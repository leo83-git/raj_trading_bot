# ═══════════════════════════════════════════════════════════════
#  Simulation Engine — Paper Trading & Backtesting
# ═══════════════════════════════════════════════════════════════
import datetime
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("simulation")


class SimulationEngine:
    """Paper trading and backtesting engine"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.capital = self.config.get("capital", 300000)
        self.initial_capital = self.capital

        self.positions = {}
        self.trades = []

    def buy(self, symbol: str, price: float, quantity: int) -> bool:
        """Simulate buy"""
        cost = price * quantity

        if cost > self.capital:
            log.warning(f"Insufficient capital for {symbol}")
            return False

        self.capital -= cost

        self.positions[symbol] = {
            "entry": price,
            "quantity": quantity,
            "direction": "BUY",
        }

        self.trades.append(
            {
                "symbol": symbol,
                "action": "BUY",
                "price": price,
                "quantity": quantity,
                "time": datetime.datetime.now(),
            }
        )

        log.info(f"[PAPER] BUY {quantity} {symbol} @ {price}")

        return True

    def sell(self, symbol: str, price: float, quantity: int | None = None) -> float:
        """Simulate sell, returns PnL"""
        if symbol not in self.positions:
            log.warning(f"No position for {symbol}")
            return 0

        pos = self.positions[symbol]
        qty = quantity if quantity else pos["quantity"]

        proceeds = price * qty
        cost = pos["entry"] * qty
        pnl = proceeds - cost

        self.capital += proceeds
        self.trades.append(
            {
                "symbol": symbol,
                "action": "SELL",
                "price": price,
                "quantity": qty,
                "pnl": pnl,
                "time": datetime.datetime.now(),
            }
        )

        if quantity is None or quantity >= pos["quantity"]:
            del self.positions[symbol]
        else:
            pos["quantity"] -= quantity

        log.info(f"[PAPER] SELL {qty} {symbol} @ {price} | PnL: {pnl:.2f}")

        return pnl

    def get_current_value(self, prices: dict[str, float]) -> float:
        """Get current portfolio value"""
        value = self.capital

        for symbol, pos in self.positions.items():
            current_price = prices.get(symbol, pos["entry"])
            value += current_price * pos["quantity"]

        return value

    def get_pnl(self) -> float:
        """Get total PnL"""
        return self.capital - self.initial_capital

    def get_stats(self) -> dict:
        """Get simulation stats"""
        total_trades = len(self.trades)

        if total_trades > 0:
            pnl_sum = sum(t.get("pnl", 0) for t in self.trades)
            wins = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
            win_rate = wins / total_trades
        else:
            pnl_sum = 0
            win_rate = 0

        return {
            "capital": self.capital,
            "initial_capital": self.initial_capital,
            "pnl": self.get_pnl(),
            "open_positions": len(self.positions),
            "total_trades": total_trades,
            "win_rate": win_rate,
        }

    def reset(self):
        """Reset simulation"""
        self.capital = self.initial_capital
        self.positions = {}
        self.trades = []
        log.info("Simulation reset")


_simulation_instance = None


def get_simulation() -> "SimulationEngine":
    """Get simulation engine singleton"""
    global _simulation_instance
    if _simulation_instance is None:
        _simulation_instance = SimulationEngine()
    return _simulation_instance
