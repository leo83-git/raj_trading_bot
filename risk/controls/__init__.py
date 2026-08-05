# ═══════════════════════════════════════════════════════════════
#  Risk Manager — Exposure, drawdown, kill switch
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("risk.controls")


@dataclass
class RiskCheck:
    passed: bool
    reason: str
    details: dict


class ExposureControl:
    """Position and exposure limits"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.max_positions = self.config.get("max_positions", 20)
        self.max_exposure = self.config.get("max_exposure", 0.3)
        self.max_single_position = self.config.get("max_single_position", 0.15)

    def check(
        self, positions: list[dict], new_position_value: float, total_capital: float
    ) -> RiskCheck:
        """Check exposure limits"""
        current_exposure = sum(p.get("value", 0) for p in positions)

        new_total = current_exposure + new_position_value

        if len(positions) >= self.max_positions:
            return RiskCheck(
                False, "Max positions reached", {"positions": len(positions)}
            )

        if new_total / total_capital > self.max_exposure:
            return RiskCheck(
                False,
                "Max exposure exceeded",
                {
                    "current": current_exposure,
                    "new": new_position_value,
                    "limit": total_capital * self.max_exposure,
                },
            )

        if new_position_value / total_capital > self.max_single_position:
            return RiskCheck(
                False,
                "Single position too large",
                {
                    "value": new_position_value,
                    "limit": total_capital * self.max_single_position,
                },
            )

        return RiskCheck(True, "OK", {"exposure": new_total / total_capital})


class DrawdownControl:
    """Drawdown monitoring and protection"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.max_drawdown = self.config.get("max_drawdown", 0.20)
        self.max_daily_loss = self.config.get("max_daily_loss", 15000)
        self.max_daily_profit = self.config.get("max_daily_profit", 50000)

        self.peak_capital = 0
        self.daily_pnl = 0

    def update(self, current_capital: float):
        """Update peak and check drawdown"""
        self.peak_capital = max(self.peak_capital, current_capital)

    def check(self, current_capital: float) -> RiskCheck:
        """Check drawdown limits"""
        if self.peak_capital == 0:
            return RiskCheck(True, "OK", {})

        drawdown = (self.peak_capital - current_capital) / self.peak_capital

        if drawdown >= self.max_drawdown:
            return RiskCheck(
                False,
                "MAX_DRAWDOWN",
                {
                    "drawdown": round(drawdown, 4),
                    "peak": self.peak_capital,
                    "current": current_capital,
                },
            )

        if drawdown >= self.max_drawdown * 0.7:
            return RiskCheck(
                True, "WARNING: High drawdown", {"drawdown": round(drawdown, 4)}
            )

        return RiskCheck(True, "OK", {"drawdown": round(drawdown, 4)})

    def check_daily(self, pnl: float) -> RiskCheck:
        """Check daily PnL limits"""
        self.daily_pnl = pnl

        if pnl <= -self.max_daily_loss:
            return RiskCheck(False, "MAX_DAILY_LOSS", {"pnl": pnl})

        if pnl >= self.max_daily_profit:
            return RiskCheck(False, "MAX_DAILY_PROFIT", {"pnl": pnl})

        return RiskCheck(True, "OK", {"daily_pnl": pnl})

    def reset_daily(self):
        """Reset daily PnL"""
        self.daily_pnl = 0


class KillSwitch:
    """Emergency stop mechanism"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.consecutive_losses = self.config.get("consecutive_losses", 5)
        self.max_consecutive_losses = self.config.get("max_consecutive_losses", 5)

        self.loss_count = 0
        self.triggered = False

    def record_trade(self, pnl: float):
        """Record trade result"""
        if pnl < 0:
            self.loss_count += 1
        else:
            self.loss_count = 0

        if self.loss_count >= self.max_consecutive_losses:
            self.triggered = True

    def check(self) -> RiskCheck:
        """Check if kill switch triggered"""
        if self.triggered:
            return RiskCheck(
                False, "KILL_SWITCH", {"consecutive_losses": self.loss_count}
            )

        if self.loss_count >= self.consecutive_losses:
            return RiskCheck(True, f"Warning: {self.loss_count} consecutive losses", {})

        return RiskCheck(True, "OK", {"losses": self.loss_count})

    def reset(self):
        """Reset kill switch"""
        self.loss_count = 0
        self.triggered = False


class RiskManager:
    """Unified risk management"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.paper_trading = self.config.get("paper_trading", True)

        self.exposure = ExposureControl(config)
        self.drawdown = DrawdownControl(config)
        self.kill_switch = KillSwitch(config)

        self.enabled = not self.paper_trading

        log.info(f"Risk manager initialized (enabled: {self.enabled})")

        self.paper_trading = self.config.get("paper_trading", True)

        self.exposure = ExposureControl(config)
        self.drawdown = DrawdownControl(config)
        self.kill_switch = KillSwitch(config)

        self.enabled = not self.paper_trading

        log.info(f"Risk manager initialized (enabled: {self.enabled})")

    def can_open_trade(self) -> bool:
        """Check if new trade allowed"""
        if not self.enabled:
            return True

        check = self.kill_switch.check()
        return check.passed

    def validate_trade(
        self, position_value: float, positions: list[dict], capital: float
    ) -> RiskCheck:
        """Validate new trade"""
        if not self.enabled:
            return RiskCheck(True, "OK", {})

        exp_check = self.exposure.check(positions, position_value, capital)
        if not exp_check.passed:
            return exp_check

        dd_check = self.drawdown.check(capital)
        if not dd_check.passed:
            return dd_check

        ks_check = self.kill_switch.check()
        if not ks_check.passed:
            return ks_check

        return RiskCheck(True, "OK", {})

    def record_pnl(self, pnl: float):
        """Record trade PnL"""
        self.kill_switch.record_trade(pnl)
        self.drawdown.check_daily(pnl)

    def update_capital(self, capital: float):
        """Update capital for drawdown tracking"""
        self.drawdown.update(capital)

    def get_status(self) -> dict:
        """Get risk status"""
        return {
            "enabled": self.enabled,
            "positions": "OK",
            "drawdown": "OK",
            "kill_switch": not self.kill_switch.triggered,
            "consecutive_losses": self.kill_switch.loss_count,
        }


# Singleton removed to prevent duplicate initialization
