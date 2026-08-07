# ═══════════════════════════════════════════════════════════════
#  Risk Management (Kill Switch + Limits)
# ═══════════════════════════════════════════════════════════════
import datetime
import threading
from typing import Any

from quant_utils.logger import get_logger
from quant_utils.notifier import alert_killswitch

log = get_logger("risk")


class CircuitBreakerTriggered(Exception):
    """Raised when global circuit breaker conditions are breached."""

    def __init__(self, message: str, current_pnl: float | None = None):
        super().__init__(message)
        self.current_pnl = current_pnl


class RiskManager:
    """Risk management with kill switch and limits"""

    def __init__(self, config: dict | None = None, data_provider: Any | None = None):
        self.config = config or {}
        self.data_provider = data_provider

        # Accept both the historical flat risk config and the application-level
        # config used by ``RajTradingBot``.
        risk_config = self.config.get("risk", self.config)

        self.paper_trading = risk_config.get("paper_trading", True)
        self.max_daily_loss = risk_config.get("max_daily_loss", 15000)
        self.max_daily_profit = risk_config.get("max_daily_profit", 50000)
        self.max_drawdown = risk_config.get("max_drawdown", 0.20)
        self.max_positions = risk_config.get("max_positions", 20)
        self.max_exposure = risk_config.get("max_exposure", 0.30)
        self.max_latency_ms = risk_config.get("max_latency_ms", 500)
        self.max_correlated_positions = risk_config.get("max_correlated_positions", 3)
        self.correlation_threshold = risk_config.get("correlation_threshold", 0.7)
        self.min_correlation_days = risk_config.get("min_correlation_days", 30)
        self.max_capital_per_trade = risk_config.get("max_capital_per_trade", 50000)

        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        self.trading_halted = False
        self.circuit_breaker_reason = ""

        self.daily_loss = 0.0
        self.daily_profit = 0.0
        self.trade_count = 0
        self._lock = threading.RLock()

        log.info("Risk manager initialized")

    def check_global_circuit_breaker(
        self, current_pnl: float, max_drawdown_limit: float
    ) -> bool:
        """
        Check whether portfolio PnL has breached the global circuit breaker.

        If current_pnl falls below the provided drawdown limit, trading is halted
        for the remainder of the runtime and a CircuitBreakerTriggered exception is raised.
        """
        if self.trading_halted:
            log.critical(
                "Global circuit breaker already active; rejecting new orders "
                f"(reason={self.circuit_breaker_reason})"
            )
            raise CircuitBreakerTriggered(
                self.circuit_breaker_reason or "Trading halted by circuit breaker",
                current_pnl=current_pnl,
            )

        if current_pnl <= max_drawdown_limit:
            self.trading_halted = True
            self.circuit_breaker_reason = (
                f"current_pnl={current_pnl:.4f} breached limit={max_drawdown_limit:.4f}"
            )
            log.critical(
                "GLOBAL CIRCUIT BREAKER TRIGGERED: "
                f"PnL {current_pnl:.4f} <= limit {max_drawdown_limit:.4f}"
            )
            self._trigger_kill_switch(
                "GLOBAL_CIRCUIT_BREAKER", self.circuit_breaker_reason
            )
            raise CircuitBreakerTriggered(
                self.circuit_breaker_reason, current_pnl=current_pnl
            )

        return True

    def calculate_position_size(
        self, capital: float, risk_per_trade: float, atr: float
    ) -> int:
        """
        Calculate a dynamic ATR-based position size.

        Formula: size = (capital * risk_per_trade) / atr
        """
        try:
            capital_value = float(capital)
            risk_value = float(risk_per_trade)
            atr_value = float(atr)
        except (TypeError, ValueError):
            log.warning(
                "Invalid inputs for position sizing; returning minimum size fallback"
            )
            return 1

        if capital_value <= 0 or risk_value <= 0:
            log.warning(
                "Non-positive capital or risk_per_trade for position sizing; "
                "returning minimum size fallback"
            )
            return 1

        if atr_value <= 0:
            log.warning(
                "ATR missing or zero for position sizing; returning minimum size fallback"
            )
            return 1

        risk_amount = capital_value * risk_value
        size = int(risk_amount / atr_value)
        return max(size, 1)

    def check_trade_allowed(self, current_daily_pnl: float) -> bool:
        """Compatibility alias for the original core risk-manager API."""
        return self.check_daily_loss_limit(current_daily_pnl)

    def check_daily_loss_limit(self, current_pnl: float) -> bool:
        """Check if daily loss limit is breached"""
        if current_pnl <= -self.max_daily_loss:
            self._trigger_kill_switch("DAILY_LOSS_LIMIT", f"Loss: ₹{current_pnl:,.0f}")
            return False
        return True

    def check_daily_profit_limit(self, current_pnl: float) -> bool:
        """Check if daily profit limit is breached"""
        if current_pnl >= self.max_daily_profit:
            self._trigger_kill_switch(
                "DAILY_PROFIT_LIMIT", f"Profit: ₹{current_pnl:,.0f}"
            )
            return False
        return True

    def check_drawdown(self, peak_capital: float, current_capital: float) -> bool:
        """Check if drawdown limit is breached"""
        if peak_capital <= 0:
            return True

        drawdown = (peak_capital - current_capital) / peak_capital

        if drawdown > self.max_drawdown:
            self._trigger_kill_switch("DRAWDOWN_LIMIT", f"Drawdown: {drawdown:.1%}")
            return False
        return True

    def check_position_limit(self, current_positions: int) -> bool:
        """Check if position limit is breached"""
        if current_positions >= self.max_positions:
            log.warning(
                f"Position limit reached: {current_positions}/{self.max_positions}"
            )
            return False
        return True

    def check_exposure_limit(self, exposure_pct: float) -> bool:
        """Check if exposure limit is breached"""
        if exposure_pct > self.max_exposure:
            log.warning(
                f"Exposure limit exceeded: {exposure_pct:.1%}/{self.max_exposure:.1%}"
            )
            return False
        return True

    def check_latency(self, latency_ms: float) -> bool:
        """Check if latency is within acceptable range"""
        if latency_ms > self.max_latency_ms:
            log.warning(f"High latency: {latency_ms}ms")
            return False
        return True

    def check_time_limit(self) -> bool:
        """Check if trading time is within allowed window"""
        # Paper trading allows trading anytime
        if self.paper_trading:
            return True

        now = datetime.datetime.now().astimezone()
        current_time = now.time()

        from datetime import time

        start_time = time(9, 15)
        end_time = time(15, 0)

        if current_time < start_time or current_time > end_time:
            log.info("Outside trading hours")
            return False
        return True

    def check_all_limits(self, pnl_data: dict) -> dict:
        """Check all risk limits"""
        checks = {
            "daily_loss": self.check_daily_loss_limit(pnl_data.get("daily_pnl", 0)),
            "daily_profit": self.check_daily_profit_limit(pnl_data.get("daily_pnl", 0)),
            "drawdown": self.check_drawdown(
                pnl_data.get("peak_capital", 0), pnl_data.get("current_capital", 0)
            ),
            "positions": self.check_position_limit(pnl_data.get("positions", 0)),
            "exposure": self.check_exposure_limit(pnl_data.get("exposure_pct", 0)),
            "time": self.check_time_limit(),
        }

        all_passed = all(checks.values())

        if not all_passed and not self.kill_switch_triggered:
            failed = [k for k, v in checks.items() if not v]
            log.warning(f"Risk checks failed: {failed}")

        return checks

    def _trigger_kill_switch(self, trigger: str, details: str):
        """Trigger kill switch"""
        if not self.kill_switch_triggered:
            self.kill_switch_triggered = True
            self.kill_switch_reason = trigger

            log.critical(f"KILL SWITCH TRIGGERED: {trigger} | {details}")
            alert_killswitch(trigger, details)

    def reset_kill_switch(self):
        """Reset kill switch"""
        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        log.info("Kill switch reset")

    def get_risk_status(self) -> dict:
        """Get current risk status"""
        return {
            "kill_switch_triggered": self.kill_switch_triggered,
            "kill_switch_reason": self.kill_switch_reason,
            "max_daily_loss": self.max_daily_loss,
            "max_daily_profit": self.max_daily_profit,
            "max_drawdown": self.max_drawdown,
            "max_positions": self.max_positions,
            "max_exposure": self.max_exposure,
            "daily_loss_today": self.daily_loss,
            "daily_profit_today": self.daily_profit,
            "trade_count": self.trade_count,
        }

    def _get_historical_closes(self, symbol: str, count: int = 60) -> list:
        """Fetch daily close prices for a symbol."""
        if not self.data_provider or not symbol:
            return []

        try:
            candles = self.data_provider.get_candles("NSE", symbol, "day", count)
            if not candles or not isinstance(candles, list):
                return []

            closes = []
            for candle in candles:
                if isinstance(candle, dict):
                    close_value = candle.get("close") or candle.get("Close")
                else:
                    close_value = getattr(candle, "close", None) or getattr(
                        candle, "Close", None
                    )
                if close_value is None:
                    continue
                try:
                    closes.append(float(close_value))
                except (TypeError, ValueError):
                    continue
            return closes
        except Exception as e:  # noqa: BLE001 - external data-provider boundary
            log.debug(f"Historical close fetch failed for {symbol}: {e}")
            return []

    def _calculate_returns(self, prices: list) -> list:
        """Calculate simple daily returns from a list of close prices."""
        returns = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            curr = prices[i]
            if prev == 0:
                continue
            returns.append((curr - prev) / prev)
        return returns

    def _pearson_correlation(self, x: list, y: list) -> float:
        """Calculate Pearson correlation coefficient between two return series."""
        if not x or not y or len(x) != len(y) or len(x) < 2:
            return 0.0

        n = len(x)
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        var_x = sum((xi - mean_x) ** 2 for xi in x)
        var_y = sum((yi - mean_y) ** 2 for yi in y)

        if var_x <= 0 or var_y <= 0:
            return 0.0

        return cov / ((var_x**0.5) * (var_y**0.5))

    def check_correlation_limits(
        self, current_positions: list, new_symbol: str
    ) -> bool:
        """Check correlation limits for a new symbol against current positions."""
        if self.kill_switch_triggered:
            return False

        if not current_positions or not new_symbol:
            return True

        with self._lock:
            try:
                new_closes = self._get_historical_closes(
                    new_symbol, self.min_correlation_days + 5
                )
                new_returns = self._calculate_returns(new_closes)

                if len(new_returns) < self.min_correlation_days:
                    log.debug(
                        f"Correlation check skipped for {new_symbol}: insufficient history "
                        f"({len(new_returns)} daily returns)"
                    )
                    return True

                correlated_count = 0
                for position in current_positions:
                    existing_symbol = position.get("symbol")
                    if not existing_symbol or existing_symbol == new_symbol:
                        continue

                    existing_closes = self._get_historical_closes(
                        existing_symbol, self.min_correlation_days + 5
                    )
                    existing_returns = self._calculate_returns(existing_closes)

                    if len(existing_returns) < self.min_correlation_days:
                        log.debug(
                            f"Correlation pair skipped {new_symbol} vs {existing_symbol}: "
                            f"insufficient history ({len(existing_returns)} daily returns)"
                        )
                        continue

                    min_len = min(len(new_returns), len(existing_returns))
                    if min_len < self.min_correlation_days:
                        continue

                    corr = self._pearson_correlation(
                        new_returns[-min_len:], existing_returns[-min_len:]
                    )
                    if corr > self.correlation_threshold:
                        correlated_count += 1
                        log.debug(
                            f"Correlation {new_symbol} vs {existing_symbol}: {corr:.2f} "
                            f"(count={correlated_count})"
                        )
                        if correlated_count >= self.max_correlated_positions:
                            log.warning(
                                f"Correlation limit exceeded for {new_symbol}: "
                                f"{correlated_count} positions above {self.correlation_threshold:.2f}"
                            )
                            return False

                return True
            except Exception as e:  # noqa: BLE001 - correlation data is optional
                log.warning(f"Correlation limit check failed for {new_symbol}: {e}")
                return True

    def can_open_trade(
        self, current_positions: list | None = None, new_symbol: str | None = None
    ) -> bool:
        """Check if new trade can be opened"""
        if self.kill_switch_triggered:
            return False

        if not self.check_time_limit():
            return False

        if current_positions is not None and new_symbol:
            return self.check_correlation_limits(current_positions, new_symbol)

        return True

    def update_daily_pnl(self, pnl: float):
        """Update daily PnL tracking"""
        if pnl < 0:
            self.daily_loss = min(self.daily_loss, pnl)
        else:
            self.daily_profit = max(self.daily_profit, pnl)

    def reset_daily(self):
        """Reset daily counters"""
        self.daily_loss = 0.0
        self.daily_profit = 0.0
        self.trade_count = 0
        log.info("Daily risk counters reset")
