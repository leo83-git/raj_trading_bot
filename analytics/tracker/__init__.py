# ═══════════════════════════════════════════════════════════════
#  Analytics Tracker — PnL & Performance
# ═══════════════════════════════════════════════════════════════
import datetime
import json
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("analytics.tracker")


class Tracker:
    """PnL and performance tracking"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.positions = {}
        self.closed_trades = []

        self.pnl_by_strategy = {}
        self.pnl_by_symbol = {}

        self.daily_pnl = 0.0
        self.total_pnl = 0.0

    def record_open(
        self, symbol: str, strategy: str, entry: float, quantity: int, direction: str
    ):
        """Record open position"""
        self.positions[symbol] = {
            "strategy": strategy,
            "entry": entry,
            "quantity": quantity,
            "direction": direction,
            "entry_time": datetime.datetime.now(),
        }

        log.info(f"Position opened: {symbol} | {strategy} | Entry: {entry}")

    def record_close(self, symbol: str, exit_price: float, reason: str = "MANUAL"):
        """Record closed position"""
        if symbol not in self.positions:
            log.warning(f"Cannot close - position not found: {symbol}")
            return

        pos = self.positions[symbol]

        if pos["direction"] == "BUY":
            pnl = (exit_price - pos["entry"]) * pos["quantity"]
        else:
            pnl = (pos["entry"] - exit_price) * pos["quantity"]

        trade = {
            "symbol": symbol,
            "strategy": pos["strategy"],
            "entry": pos["entry"],
            "exit": exit_price,
            "quantity": pos["quantity"],
            "pnl": pnl,
            "reason": reason,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.datetime.now(),
        }

        self.closed_trades.append(trade)

        strategy = pos["strategy"]
        if strategy not in self.pnl_by_strategy:
            self.pnl_by_strategy[strategy] = 0
        self.pnl_by_strategy[strategy] += pnl

        if symbol not in self.pnl_by_symbol:
            self.pnl_by_symbol[symbol] = 0
        self.pnl_by_symbol[symbol] += pnl

        self.daily_pnl += pnl
        self.total_pnl += pnl

        del self.positions[symbol]

        log.info(f"Position closed: {symbol} | PnL: {pnl:.2f} | Reason: {reason}")

        return pnl

    def get_current_pnl(self, current_prices: dict[str, float]) -> float:
        """Calculate unrealized PnL"""
        unrealized = 0

        for symbol, pos in self.positions.items():
            current = current_prices.get(symbol, pos["entry"])

            if pos["direction"] == "BUY":
                pnl = (current - pos["entry"]) * pos["quantity"]
            else:
                pnl = (pos["entry"] - current) * pos["quantity"]

            unrealized += pnl

        return unrealized

    def get_summary(self) -> dict:
        """Get performance summary"""
        total_trades = len(self.closed_trades)

        if total_trades > 0:
            wins = sum(1 for t in self.closed_trades if t["pnl"] > 0)
            win_rate = wins / total_trades

            avg_win = sum(t["pnl"] for t in self.closed_trades if t["pnl"] > 0) / max(
                1, wins
            )
            avg_loss = sum(t["pnl"] for t in self.closed_trades if t["pnl"] < 0) / max(
                1, total_trades - wins
            )

            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        else:
            win_rate = 0
            avg_win = 0
            avg_loss = 0
            profit_factor = 0

        return {
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "open_positions": len(self.positions),
            "closed_trades": total_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "pnl_by_strategy": self.pnl_by_strategy,
            "pnl_by_symbol": self.pnl_by_symbol,
        }

    def get_recent_trades(self, count: int = 10) -> list[dict]:
        """Get recent closed trades"""
        return self.closed_trades[-count:]

    def save_to_file(self, filename: str = "analytics/trades.json"):
        """Save trades to file"""
        try:
            with open(filename, "w") as f:
                json.dump(
                    {
                        "closed_trades": self.closed_trades,
                        "summary": self.get_summary(),
                    },
                    f,
                    indent=2,
                    default=str,
                )

            log.info(f"Trades saved to {filename}")
        except Exception as e:
            log.error(f"Failed to save trades: {e}")

    def reset_daily(self):
        """Reset daily PnL"""
        self.daily_pnl = 0.0

    def get_tracker(self):
        """Get tracker instance"""
        return self


_tracker_instance = None


def get_tracker() -> Tracker:
    """Get singleton tracker instance"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = Tracker()
    return _tracker_instance


def record_trade(symbol: str, strategy: str, pnl: float):
    """Record trade for tracking"""
    tracker = get_tracker()

    if strategy not in tracker.pnl_by_strategy:
        tracker.pnl_by_strategy[strategy] = 0
    tracker.pnl_by_strategy[strategy] += pnl

    tracker.total_pnl += pnl
    tracker.daily_pnl += pnl
