import logging

log = logging.getLogger("risk_manager")

class RiskManager:
    """
    Enforces daily loss limits, maximum drawdowns, and calculates dynamic position sizing.
    """
    def __init__(self, config: dict):
        self.config = config
        self.max_daily_loss = config.get("risk", {}).get("max_daily_loss", 5000)
        self.max_capital_per_trade = config.get("risk", {}).get("max_capital_per_trade", 50000)
        
    def check_trade_allowed(self, current_daily_pnl: float) -> bool:
        """Check if trading should be halted due to loss limits"""
        if current_daily_pnl <= -self.max_daily_loss:
            log.warning(f"CIRCUIT BREAKER: Daily loss limit reached ({current_daily_pnl} <= {-self.max_daily_loss})")
            return False
        return True
        
    def calculate_position_size(self, capital: float, atr: float, stop_loss_pct: float) -> int:
        """
        Calculate position size based on volatility (ATR).
        (Stub for Phase 3 implementation)
        """
        # Phase 3 logic will go here
        return 1

