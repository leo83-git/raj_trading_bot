# ═══════════════════════════════════════════════════════════════
#  Hedging Engine (Greeks + Drawdown Hedging)
# ═══════════════════════════════════════════════════════════════

from quant_utils.logger import get_logger

log = get_logger("hedging")


class HedgingEngine:
    """Hedging engine for delta, gamma, vega, and drawdown hedges"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.delta_threshold = self.config.get("delta_threshold", 0.1)
        self.gamma_threshold = self.config.get("gamma_threshold", 0.05)
        self.vega_threshold = self.config.get("vega_threshold", 0.1)
        self.drawdown_threshold = self.config.get("drawdown_threshold", 0.15)

        self.hedge_history = []

    def check_delta_hedge(
        self, portfolio_greeks: dict, spot_position: float = 0
    ) -> dict:
        """Check if delta hedge is needed"""
        portfolio_delta = portfolio_greeks.get("delta", 0)

        hedge_action = "NONE"
        hedge_contracts = 0

        if abs(portfolio_delta) > self.delta_threshold:
            if portfolio_delta > 0:
                hedge_action = "SELL"
                hedge_contracts = int(portfolio_delta)
            else:
                hedge_action = "BUY"
                hedge_contracts = int(abs(portfolio_delta))

        result = {
            "action": hedge_action,
            "contracts": hedge_contracts,
            "current_delta": portfolio_delta,
            "threshold": self.delta_threshold,
            "hedge_needed": hedge_action != "NONE",
        }

        if hedge_action != "NONE":
            log.info(
                f"Delta hedge: {hedge_action} {hedge_contracts} contracts | Delta: {portfolio_delta:.2f}"
            )
            self.hedge_history.append(result)

        return result

    def check_gamma_hedge(self, portfolio_greeks: dict) -> dict:
        """Check if gamma hedge is needed"""
        portfolio_gamma = portfolio_greeks.get("gamma", 0)

        hedge_action = "NONE"
        hedge_contracts = 0

        if abs(portfolio_gamma) > self.gamma_threshold:
            hedge_action = "BUY" if portfolio_gamma < 0 else "SELL"
            hedge_contracts = int(abs(portfolio_gamma) * 100)

        result = {
            "action": hedge_action,
            "contracts": hedge_contracts,
            "current_gamma": portfolio_gamma,
            "hedge_needed": hedge_action != "NONE",
        }

        if hedge_action != "NONE":
            log.info(
                f"Gamma hedge: {hedge_action} {hedge_contracts} | Gamma: {portfolio_gamma:.4f}"
            )

        return result

    def check_drawdown_hedge(self, peak_capital: float, current_capital: float) -> dict:
        """Check if drawdown hedging is needed"""
        if peak_capital <= 0:
            return {"hedge_needed": False}

        drawdown = (peak_capital - current_capital) / peak_capital

        action = "NONE"
        reduction_factor = 1.0

        if drawdown > self.drawdown_threshold * 2:
            action = "STOP_TRADING"
            reduction_factor = 0
        elif drawdown > self.drawdown_threshold:
            action = "REDUCE_EXPOSURE"
            reduction_factor = 0.5

        result = {
            "action": action,
            "drawdown": drawdown,
            "reduction_factor": reduction_factor,
            "hedge_needed": action != "NONE",
        }

        if action != "NONE":
            log.warning(
                f"Drawdown hedge triggered: {action} | Drawdown: {drawdown:.1%}"
            )
            self.hedge_history.append(result)

        return result

    def calculate_hedge_orders(
        self, portfolio_greeks: dict, available_funds: float
    ) -> list[dict]:
        """Calculate hedge orders needed"""
        orders = []

        delta_hedge = self.check_delta_hedge(portfolio_greeks)
        if delta_hedge["hedge_needed"] and available_funds > 0:
            orders.append(
                {
                    "type": "DELTA_HEDGE",
                    "action": delta_hedge["action"],
                    "contracts": delta_hedge["contracts"],
                }
            )

        gamma_hedge = self.check_gamma_hedge(portfolio_greeks)
        if gamma_hedge["hedge_needed"] and available_funds > 0:
            orders.append(
                {
                    "type": "GAMMA_HEDGE",
                    "action": gamma_hedge["action"],
                    "contracts": gamma_hedge["contracts"],
                }
            )

        return orders

    def get_hedge_summary(self) -> dict:
        """Get hedge history summary"""
        return {
            "total_hedges": len(self.hedge_history),
            "recent_hedges": self.hedge_history[-5:],
        }
