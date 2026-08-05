# ═══════════════════════════════════════════════════════════════
#  Enhanced Risk Management
#  - Correlation matrix for diversification
#  - VIX-based systematic hedging
#  - Leverage caps
# ═══════════════════════════════════════════════════════════════
import datetime
from dataclasses import dataclass

import numpy as np

from quant_utils.logger import get_logger
from quant_utils.notifier import alert_killswitch

log = get_logger("risk")


class CorrelationMatrixBuilder:
    """Build correlation matrix from candle data"""

    def __init__(self, lookback_days: int = 20):
        self.lookback_days = lookback_days
        self.matrix = {}
        self.last_update = None

    def compute_from_candles(self, candles_by_symbol: dict[str, list[dict]]) -> dict:
        """
        Compute rolling return correlation from candle data.

        Args:
            candles_by_symbol: dict of {symbol: [candles]}

        Returns:
            correlation matrix dict
        """
        if not candles_by_symbol:
            return CORRELATION_MATRIX

        returns_by_symbol = {}

        for symbol, candles in candles_by_symbol.items():
            if not candles or len(candles) < 10:
                continue

            closes = [c.get("close", 0) for c in candles if c.get("close")]
            if len(closes) < 10:
                continue

            returns = np.diff(np.log(closes))
            if len(returns) > 0:
                returns_by_symbol[symbol] = returns[-self.lookback_days :]

        if len(returns_by_symbol) < 2:
            return CORRELATION_MATRIX

        symbols = list(returns_by_symbol.keys())
        new_matrix = {}

        for sym1 in symbols:
            new_matrix[sym1] = {}
            for sym2 in symbols:
                if sym1 == sym2:
                    new_matrix[sym1][sym2] = 1.0
                    continue

                r1 = returns_by_symbol[sym1]
                r2 = returns_by_symbol[sym2]

                min_len = min(len(r1), len(r2))
                if min_len < 5:
                    new_matrix[sym1][sym2] = 0.5
                    continue

                corr = np.corrcoef(r1[-min_len:], r2[-min_len:])[0, 1]
                new_matrix[sym1][sym2] = (
                    round(float(corr), 3) if not np.isnan(corr) else 0.5
                )

        self.matrix = new_matrix
        self.last_update = datetime.datetime.now()

        log.info(f"Correlation matrix rebuilt: {len(symbols)} symbols")

        return self.matrix

    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Get correlation between two symbols"""
        if not self.matrix:
            return CORRELATION_MATRIX.get(symbol1, {}).get(symbol2, 0.5)

        return self.matrix.get(symbol1, {}).get(symbol2, 0.5)


CORRELATION_MATRIX = {
    "NIFTY": {"BANKNIFTY": 0.85, "RELIANCE": 0.65, "HDFCBANK": 0.70, "INFY": 0.60},
    "BANKNIFTY": {"NIFTY": 0.85, "HDFCBANK": 0.80, "ICICIBANK": 0.78, "SBIN": 0.75},
    "RELIANCE": {"NIFTY": 0.65, "INFY": 0.55, "TCS": 0.50, "TITAN": 0.48},
    "HDFCBANK": {
        "BANKNIFTY": 0.80,
        "ICICIBANK": 0.82,
        "KOTAKBANK": 0.75,
        "AXISBANK": 0.72,
    },
    "INFY": {"NIFTY": 0.60, "TCS": 0.75, "WIPRO": 0.70, "RELIANCE": 0.55},
}

SECTOR_CORRELATIONS = {
    "banking": ["HDFCBANK", "ICICIBANK", "KOTAKBANK", "SBIN", "AXISBANK"],
    "it": ["INFY", "TCS", "WIPRO", "HCLTECH"],
    "energy": ["RELIANCE", "ONGC", "NTPC", "POWERGRID"],
    "auto": ["MARUTI", "M&M", "TATAMOTORS", "HEROMOTOCO"],
    "pharma": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"],
}


@dataclass
class Position:
    symbol: str
    quantity: int
    entry_price: float
    current_price: float
    category: str = "equity"
    sector: str = "mixed"
    beta: float = 1.0


@dataclass
class RiskMetrics:
    portfolio_beta: float = 1.0
    correlation_risk: float = 0.0
    sector_concentration: float = 0.0
    leverage: float = 0.0
    var_95: float = 0.0
    max_drawdown: float = 0.0


class CorrelationManager:
    """Manage position correlation for diversification"""

    def __init__(self, max_correlation: float = 0.7, max_sector_size: float = 0.30):
        self.max_correlation = max_correlation
        self.max_sector_size = max_sector_size
        self.correlation_matrix = CORRELATION_MATRIX

    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        if symbol1 == symbol2:
            return 1.0
        return self.correlation_matrix.get(symbol1, {}).get(symbol2, 0.5)

    def calculate_portfolio_correlation(self, positions: list[Position]) -> float:
        if len(positions) < 2:
            return 0.0

        total_corr = 0.0
        pairs = 0

        value_1 = positions[0].quantity * positions[0].current_price
        value_2 = (
            positions[1].quantity * positions[1].current_price
            if len(positions) > 1
            else 0
        )

        if len(positions) > 1:
            corr = self.get_correlation(positions[0].symbol, positions[1].symbol)
            total_corr = corr * 0.5 + 0.5
            pairs = 1
        else:
            return 0.0

        return total_corr / pairs if pairs > 0 else 0.0

    def check_sector_concentration(self, positions: list[Position]) -> tuple[bool, str]:
        sector_values = {}
        total_value = sum(p.quantity * p.current_price for p in positions)

        if total_value == 0:
            return True, ""

        for pos in positions:
            sector = getattr(pos, "sector", "mixed")
            value = pos.quantity * pos.current_price
            sector_values[sector] = sector_values.get(sector, 0) + value

        for sector, value in sector_values.items():
            if value / total_value > self.max_sector_size:
                return False, f"Sector {sector} at {value / total_value:.1%}"

        return True, ""

    def recommend_diversification(
        self, positions: list[Position], new_symbol: str
    ) -> dict:
        """Recommend whether to add new position"""
        current_corr = self.calculate_portfolio_correlation(positions)

        new_corrs = []
        for pos in positions:
            corr = self.get_correlation(new_symbol, pos.symbol)
            new_corrs.append(corr)

        avg_new_corr = sum(new_corrs) / len(new_corrs) if new_corrs else 0

        return {
            "add_position": avg_new_corr <= self.max_correlation,
            "current_correlation": round(current_corr, 3),
            "new_correlation": round(avg_new_corr, 3),
            "recommended_corr_max": self.max_correlation,
            "diversification_score": 1 - avg_new_corr,
        }


class LeverageManager:
    """Manage portfolio leverage"""

    def __init__(self, max_leverage: float = 3.0, warning_leverage: float = 2.5):
        self.max_leverage = max_leverage
        self.warning_leverage = warning_leverage

    def calculate_leverage(
        self, positions: list[Position], total_capital: float
    ) -> float:
        if total_capital <= 0:
            return 0.0

        total_exposure = sum(p.quantity * p.current_price for p in positions)
        return total_exposure / total_capital

    def calculate_position_leverage(
        self, position: Position, total_capital: float
    ) -> float:
        if total_capital <= 0:
            return 0.0
        return (position.quantity * position.current_price) / total_capital

    def can_add_position(
        self, positions: list[Position], new_position: Position, total_capital: float
    ) -> tuple[bool, str]:
        current_leverage = self.calculate_leverage(positions, total_capital)
        new_leverage = self.calculate_position_leverage(new_position, total_capital)

        total_leverage = current_leverage + new_leverage

        if total_leverage > self.max_leverage:
            return (
                False,
                f"Leverage {total_leverage:.2f}x exceeds cap {self.max_leverage}x",
            )

        if current_leverage > self.warning_leverage:
            log.warning(
                f"Leverage {current_leverage:.2f}x exceeds warning {self.warning_leverage}x"
            )

        return True, ""

    def get_leverage_breakdown(
        self, positions: list[Position], total_capital: float
    ) -> dict:
        leverage_by_position = []

        for pos in positions:
            lev = self.calculate_position_leverage(pos, total_capital)
            leverage_by_position.append(
                {
                    "symbol": pos.symbol,
                    "leverage": round(lev, 3),
                    "value": pos.quantity * pos.current_price,
                }
            )

        return {
            "total_leverage": round(
                self.calculate_leverage(positions, total_capital), 3
            ),
            "max_leverage": self.max_leverage,
            "leverage_remaining": round(
                max(
                    0,
                    self.max_leverage
                    - self.calculate_leverage(positions, total_capital),
                ),
                3,
            ),
            "by_position": leverage_by_position,
        }


class VIXHedgeManager:
    """Systematic risk hedging using VIX-based instruments"""

    def __init__(self, vix_symbol: str = "INDIAVIX", hedge_threshold: float = 20.0):
        self.vix_symbol = vix_symbol
        self.hedge_threshold = hedge_threshold
        self.current_vix = 15.0
        self.hedge_ratio = 0.0
        self.hedge_positions = []

    def update_vix(self, vix_value: float):
        self.current_vix = vix_value

    def calculate_hedge_ratio(
        self, portfolio_beta: float, portfolio_value: float, nifty_price: float
    ) -> float:
        if self.current_vix < self.hedge_threshold:
            return 0.0

        vix_excess = self.current_vix - self.hedge_threshold
        hedge_needed = (
            (vix_excess / 100) * portfolio_beta * (portfolio_value / nifty_price)
        )

        self.hedge_ratio = min(hedge_needed, 0.25)
        return self.hedge_ratio

    def should_hedge(self) -> bool:
        return self.current_vix > self.hedge_threshold

    def get_hedge_recommendation(self, portfolio: dict) -> dict:
        if not self.should_hedge():
            return {
                "hedge_recommended": False,
                "reason": f"VIX {self.current_vix:.1f} below threshold {self.hedge_threshold}",
                "hedge_ratio": 0,
            }

        return {
            "hedge_recommended": True,
            "reason": f"VIX {self.current_vix:.1f} elevated",
            "hedge_symbol": self.vix_symbol,
            "hedge_ratio": round(self.hedge_ratio, 3),
            "action": "SELL" if self.hedge_ratio > 0.1 else "NONE",
        }

    def estimate_protection(self, drop_pct: float, hedge_ratio: float) -> float:
        if hedge_ratio <= 0:
            return 0.0
        return min(drop_pct * hedge_ratio, drop_pct)


class RiskEngine:
    """Enhanced risk engine with correlation, leverage, and VIX hedging"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.max_leverage = self.config.get("max_leverage", 3.0)
        self.max_correlation = self.config.get("max_correlation", 0.7)
        self.max_sector_size = self.config.get("max_sector_size", 0.30)
        self.vix_hedge_threshold = self.config.get("vix_threshold", 20.0)

        self.correlation_mgr = CorrelationManager(
            max_correlation=self.max_correlation, max_sector_size=self.max_sector_size
        )
        self.leverage_mgr = LeverageManager(
            max_leverage=self.max_leverage, warning_leverage=self.max_leverage * 0.8
        )
        self.vix_hedge = VIXHedgeManager(hedge_threshold=self.vix_hedge_threshold)

        self.kill_switch_triggered = False
        self.kill_switch_reason = ""

        log.info(f"Risk engine initialized | Max leverage: {self.max_leverage}x")

    def check_position_risk(
        self,
        positions: list[Position],
        new_symbol: str,
        total_capital: float,
        current_vix: float = 15.0,
    ) -> dict:
        self.vix_hedge.update_vix(current_vix)

        portfolio_corr = self.correlation_mgr.calculate_portfolio_correlation(positions)
        sector_ok, sector_msg = self.correlation_mgr.check_sector_concentration(
            positions
        )

        diversify = self.correlation_mgr.recommend_diversification(
            positions, new_symbol
        )

        leverage_check = self.leverage_mgr.can_add_position(
            positions, Position(new_symbol, 1, 0, 0), total_capital
        )

        leverage_breakdown = self.leverage_mgr.get_leverage_breakdown(
            positions, total_capital
        )

        hedge = self.vix_hedge.get_hedge_recommendation({})

        return {
            "can_add": leverage_check[0] and sector_ok and diversify["add_position"],
            "reason": leverage_check[1] if not leverage_check[0] else sector_msg,
            "correlation_risk": round(portfolio_corr, 3),
            "sector_concentration_ok": sector_ok,
            "diversification": diversify,
            "leverage": leverage_breakdown,
            "vix_hedge": hedge,
            "risk_passed": leverage_check[0] and sector_ok,
        }

    def calculate_portfolio_metrics(
        self,
        positions: list[Position],
        total_capital: float,
        nifty_price: float = 22000,
    ) -> RiskMetrics:
        if not positions:
            return RiskMetrics()

        total_exposure = sum(p.quantity * p.current_price for p in positions)

        portfolio_beta = (
            sum(p.beta * p.quantity * p.current_price for p in positions)
            / total_exposure
            if total_exposure > 0
            else 1.0
        )

        correlation_risk = self.correlation_mgr.calculate_portfolio_correlation(
            positions
        )

        sector_values = {}
        for pos in positions:
            sector = getattr(pos, "sector", "mixed")
            val = pos.quantity * pos.current_price
            sector_values[sector] = sector_values.get(sector, 0) + val

        max_sector = (
            max(sector_values.values()) / total_exposure if total_exposure > 0 else 0
        )

        leverage = total_exposure / total_capital if total_capital > 0 else 0

        var_95 = total_exposure * 0.02 * portfolio_beta

        return RiskMetrics(
            portfolio_beta=round(portfolio_beta, 3),
            correlation_risk=round(correlation_risk, 3),
            sector_concentration=round(max_sector, 3),
            leverage=round(leverage, 3),
            var_95=round(var_95, 2),
        )

    def trigger_kill_switch(self, reason: str, details: str):
        if not self.kill_switch_triggered:
            self.kill_switch_triggered = True
            self.kill_switch_reason = reason
            log.critical(f"KILL SWITCH: {reason} | {details}")
            alert_killswitch(reason, details)

    def reset_kill_switch(self):
        self.kill_switch_triggered = False
        self.kill_switch_reason = ""
        log.info("Kill switch reset")


def create_risk_engine(config: dict = None) -> RiskEngine:
    return RiskEngine(config)


def calculate_position_size(
    capital: float, price: float, leverage: float = 1.0, risk_pct: float = 0.02
) -> int:
    max_position_value = capital * leverage * risk_pct
    return int(max_position_value / price)


def calculate_stop_loss(entry: float, risk_pct: float = 0.02) -> float:
    return entry * (1 - risk_pct)


def calculate_risk_reward(entry: float, target: float, stop_loss: float) -> float:
    risk = entry - stop_loss
    reward = target - entry
    return reward / risk if risk > 0 else 0
