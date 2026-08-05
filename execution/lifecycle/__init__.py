# Trade Lifecycle Manager - Handles open positions
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("lifecycle.manager")


@dataclass
class OpenTrade:
    """Open position tracking"""

    symbol: str
    action: str
    entry: float
    quantity: int
    entry_time: datetime
    stop_loss: float
    target: float
    current_price: float = 0
    pnl: float = 0
    trailing_stop: float = None
    highest_pnl: float = 0
    metadata: dict = field(default_factory=dict)


class TradeLifecycleManager:
    """Manages open positions - trailing stops, SL, target updates"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.open_trades: dict[str, OpenTrade] = {}

        self.trailing_enabled = self.config.get("trailing_enabled", True)
        self.trailing_distance = self.config.get("trailing_distance", 0.015)  # 1.5%
        self.trailing_activation = self.config.get(
            "trailing_activation", 0.02
        )  # 2% profit

        log.info("Trade Lifecycle Manager initialized")

    def open_trade(self, signal: dict) -> bool:
        """Open a new position"""
        symbol = signal.get("symbol")
        if not symbol:
            return False

        if symbol in self.open_trades:
            log.warning(f"Position already exists for {symbol}")
            return False

        trade = OpenTrade(
            symbol=symbol,
            action=signal.get("action", "BUY"),
            entry=signal.get("entry", 0),
            quantity=signal.get("quantity", 1),
            entry_time=datetime.now(),
            stop_loss=signal.get("stop_loss", 0),
            target=signal.get("target", 0),
            metadata=signal.get("metadata", {}),
        )

        self.open_trades[symbol] = trade
        log.info(
            f"Opened position: {symbol} {trade.action} @ {trade.entry} qty={trade.quantity}"
        )

        return True

    def close_trade(self, symbol: str, reason: str = "signal") -> dict | None:
        """Close an existing position"""
        if symbol not in self.open_trades:
            log.warning(f"No position to close for {symbol}")
            return None

        trade = self.open_trades[symbol]
        exit_price = trade.current_price if trade.current_price > 0 else trade.entry

        pnl = (
            (exit_price - trade.entry) * trade.quantity
            if trade.action == "BUY"
            else (trade.entry - exit_price) * trade.quantity
        )

        result = {
            "symbol": symbol,
            "action": trade.action,
            "entry": trade.entry,
            "exit": exit_price,
            "quantity": trade.quantity,
            "pnl": pnl,
            "reason": reason,
            "holding_time": (datetime.now() - trade.entry_time).total_seconds() / 60,
        }

        del self.open_trades[symbol]
        log.info(f"Closed position: {symbol} | PnL: {pnl:.2f} | Reason: {reason}")

        return result

    def update_prices(self, market_prices: dict[str, float]):
        """Update current prices for all open positions"""
        for symbol, trade in self.open_trades.items():
            current_price = market_prices.get(symbol)
            if not current_price:
                continue

            trade.current_price = current_price

            # Calculate PnL
            if trade.action == "BUY":
                trade.pnl = (current_price - trade.entry) * trade.quantity
            else:
                trade.pnl = (trade.entry - current_price) * trade.quantity

            # Track highest PnL for trailing stop
            trade.highest_pnl = max(trade.highest_pnl, trade.pnl)

    def check_exits(self, market_prices: dict[str, float]) -> list[dict]:
        """Check and execute exit conditions - SL, target, trailing"""
        exits = []

        for symbol in list(self.open_trades.keys()):
            trade = self.open_trades[symbol]
            current_price = market_prices.get(symbol)

            if not current_price:
                continue

            trade.current_price = current_price

            # Update PnL
            if trade.action == "BUY":
                trade.pnl = (current_price - trade.entry) * trade.quantity
            else:
                trade.pnl = (trade.entry - current_price) * trade.quantity

            exit_reason = None

            # Check trailing stop activation
            profit_pct = abs(trade.pnl) / (trade.entry * trade.quantity)

            if self.trailing_enabled and profit_pct >= self.trailing_activation:
                # Update trailing stop
                if trade.action == "BUY":
                    new_trailing = current_price * (1 - self.trailing_distance)
                    if not trade.trailing_stop or new_trailing > trade.trailing_stop:
                        trade.trailing_stop = new_trailing

                    # Check trailing stop hit
                    if current_price <= trade.trailing_stop:
                        exit_reason = "TRAILING_STOP"

                else:  # SELL
                    new_trailing = current_price * (1 + self.trailing_distance)
                    if not trade.trailing_stop or new_trailing < trade.trailing_stop:
                        trade.trailing_stop = new_trailing

                    if current_price >= trade.trailing_stop:
                        exit_reason = "TRAILING_STOP"

            # Check regular stop loss
            if not exit_reason:
                if (
                    trade.action == "BUY"
                    and current_price <= trade.stop_loss
                    or trade.action == "SELL"
                    and current_price >= trade.stop_loss
                ):
                    exit_reason = "STOP_LOSS"

            # Check target
            if not exit_reason:
                if (
                    trade.action == "BUY"
                    and current_price >= trade.target
                    or trade.action == "SELL"
                    and current_price <= trade.target
                ):
                    exit_reason = "TARGET_HIT"

            if exit_reason:
                result = self.close_trade(symbol, exit_reason)
                if result:
                    exits.append(result)

        return exits

    def get_open_positions(self) -> list[dict]:
        """Get all open positions"""
        positions = []
        for symbol, trade in self.open_trades.items():
            positions.append(
                {
                    "symbol": symbol,
                    "action": trade.action,
                    "entry": trade.entry,
                    "current": trade.current_price,
                    "quantity": trade.quantity,
                    "pnl": round(trade.pnl, 2),
                    "sl": trade.stop_loss,
                    "target": trade.target,
                    "trailing_stop": trade.trailing_stop,
                    "holding_time": (datetime.now() - trade.entry_time).total_seconds()
                    / 60,
                }
            )
        return positions

    def get_positions_count(self) -> int:
        """Get number of open positions"""
        return len(self.open_trades)

    def has_position(self, symbol: str) -> bool:
        """Check if position exists"""
        return symbol in self.open_trades
