# ═══════════════════════════════════════════════════════════════
#  Trade Management Engine
#  - Trailing stop-loss
#  - Partial profit booking
#  - Time-based exit
# ═══════════════════════════════════════════════════════════════════════
import datetime
from dataclasses import dataclass

from quant_utils.logger import get_logger

log = get_logger("trade_management")


@dataclass
class TradeExit:
    action: str  # FULL_EXIT, PARTIAL_EXIT, TRAIL_SL, TIME_EXIT
    reason: str
    quantity: int
    price: float
    pnl: float


class TradeManager:
    """Manages open positions with dynamic exit rules"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.trailing_atr_multiplier = self.config.get("trailing_atr", 1.0)
        self.profit_target_partial = self.config.get(
            "partial_profit_pct", 0.50
        )  # Book 50% at target
        self.time_exit_hours = self.config.get(
            "time_exit_hours", 3
        )  # Exit if held > 3 hours
        self.max_hold_hours = self.config.get("max_hold_hours", 6)

        self.open_trades = {}

    def register_trade(
        self,
        trade_id: str,
        entry_price: float,
        quantity: int,
        direction: str,
        entry_time: datetime.datetime,
        atr: float = None,
        target: float = None,
        sl: float = None,
    ):
        """Register a new trade with management parameters"""
        self.open_trades[trade_id] = {
            "entry_price": entry_price,
            "quantity": quantity,
            "direction": direction,
            "entry_time": entry_time,
            "atr": atr or entry_price * 0.02,
            "original_sl": sl,
            "original_target": target,
            "partial_booked": False,
            "highest_price": entry_price if direction == "BUY" else entry_price,
            "lowest_price": entry_price if direction == "SELL" else entry_price,
            "trail_count": 0,
        }
        log.info(
            f"Trade registered: {trade_id} entry={entry_price} target={target} sl={sl}"
        )

    def check_exits(
        self, trade_id: str, current_price: float, current_time: datetime.datetime
    ) -> TradeExit | None:
        """
        Check if any exit conditions are met.

        Returns TradeExit if should exit, None otherwise.
        """
        if trade_id not in self.open_trades:
            return None

        trade = self.open_trades[trade_id]
        entry_price = trade["entry_price"]
        quantity = trade["quantity"]
        direction = trade["direction"]
        atr = trade["atr"]

        # 1. Check trailing stop-loss
        trail_exit = self._check_trailing_sl(trade, current_price)
        if trail_exit:
            return trail_exit

        # 2. Check partial profit booking (first target hit)
        partial_exit = self._check_partial_profit(
            trade, current_price, entry_price, quantity, direction
        )
        if partial_exit:
            return partial_exit

        # 3. Check time-based exit
        time_exit = self._check_time_exit(
            trade, current_time, entry_price, quantity, direction
        )
        if time_exit:
            return time_exit

        # 4. Update highest/lowest price for trailing
        self._update_price_tracker(trade, current_price)

        return None

    def _check_trailing_sl(self, trade: dict, current_price: float) -> TradeExit | None:
        """Trailing stop: move SL when price moves 0.5× ATR in profit"""
        direction = trade["direction"]
        entry_price = trade["entry_price"]
        atr = trade["atr"]
        original_sl = trade["original_sl"]
        quantity = trade["quantity"]

        # Calculate profit threshold
        profit_threshold = atr * 0.5

        if direction == "BUY":
            profit = current_price - entry_price

            if profit > profit_threshold:
                # Trail SL: entry + 0.5× ATR
                new_sl = entry_price + (atr * self.trailing_atr_multiplier)

                if new_sl > original_sl:
                    trade["trail_count"] += 1
                    pnl = (new_sl - entry_price) * quantity

                    log.info(
                        f"TRAIL_SL {trade.get('trade_id', 'N/A')}: price={current_price:.2f} new_sl={new_sl:.2f}"
                    )

                    return TradeExit(
                        action="TRAIL_SL",
                        reason=f"Trailing SL triggered (count: {trade['trail_count']})",
                        quantity=quantity,
                        price=new_sl,
                        pnl=pnl,
                    )
        else:  # SELL
            profit = entry_price - current_price

            if profit > profit_threshold:
                new_sl = entry_price - (atr * self.trailing_atr_multiplier)

                if new_sl < original_sl:
                    trade["trail_count"] += 1
                    pnl = (entry_price - new_sl) * quantity

                    return TradeExit(
                        action="TRAIL_SL",
                        reason=f"Trailing SL triggered (count: {trade['trail_count']})",
                        quantity=quantity,
                        price=new_sl,
                        pnl=pnl,
                    )

        return None

    def _check_partial_profit(
        self,
        trade: dict,
        current_price: float,
        entry_price: float,
        quantity: int,
        direction: str,
    ) -> TradeExit | None:
        """Book partial profits when first target is hit"""
        if trade.get("partial_booked"):
            return None

        target = trade.get("original_target")
        if not target:
            return None

        target_hit = False

        if (
            direction == "BUY"
            and current_price >= target
            or direction == "SELL"
            and current_price <= target
        ):
            target_hit = True

        if target_hit:
            # Book 50% of position
            exit_qty = quantity // 2
            if exit_qty < 1:
                exit_qty = quantity

            pnl = self._calculate_pnl(entry_price, current_price, exit_qty, direction)
            trade["partial_booked"] = True

            log.info(
                f"PARTIAL_EXIT: booked {exit_qty} at {current_price:.2f} PnL={pnl:.2f}"
            )

            return TradeExit(
                action="PARTIAL_EXIT",
                reason=f"First target hit {target:.2f}",
                quantity=exit_qty,
                price=current_price,
                pnl=pnl,
            )

        return None

    def _check_time_exit(
        self,
        trade: dict,
        current_time: datetime.datetime,
        entry_price: float,
        quantity: int,
        direction: str,
    ) -> TradeExit | None:
        """Time-based exit after max hold time"""
        entry_time = trade["entry_time"]

        hold_duration = (current_time - entry_time).total_seconds() / 3600  # hours

        # First partial exit at time_exit_hours
        time_threshold = self.time_exit_hours

        if hold_duration >= time_threshold and not trade.get("time_exit_triggered"):
            pnl = self._calculate_pnl(
                entry_price, entry_price, quantity, direction
            )  # Estimate with no price change

            trade["time_exit_triggered"] = True

            log.info(f"TIME_EXIT: held {hold_duration:.1f}h, exiting")

            return TradeExit(
                action="TIME_EXIT",
                reason=f"Held {hold_duration:.1f}h > {time_threshold}h",
                quantity=quantity,
                price=entry_price,
                pnl=pnl,
            )

        # Hard exit at max hold time
        if hold_duration >= self.max_hold_hours:
            pnl = self._calculate_pnl(entry_price, entry_price, quantity, direction)

            log.info(f"MAX_HOLD_EXIT: held {hold_duration:.1f}h, forced exit")

            return TradeExit(
                action="FULL_EXIT",
                reason=f"Max hold time {self.max_hold_hours}h exceeded",
                quantity=quantity,
                price=entry_price,
                pnl=pnl,
            )

        return None

    def _update_price_tracker(self, trade: dict, current_price: float):
        """Track highest/lowest price for trailing SL"""
        direction = trade["direction"]

        if direction == "BUY":
            trade["highest_price"] = max(trade["highest_price"], current_price)
        else:
            trade["lowest_price"] = min(trade["lowest_price"], current_price)

    def _calculate_pnl(
        self, entry: float, exit: float, qty: int, direction: str
    ) -> float:
        if direction == "BUY":
            return (exit - entry) * qty
        else:
            return (entry - exit) * qty

    def close_trade(self, trade_id: str):
        """Remove trade from management"""
        if trade_id in self.open_trades:
            del self.open_trades[trade_id]
            log.debug(f"Trade removed from management: {trade_id}")

    def get_trade_status(self, trade_id: str) -> dict | None:
        """Get current trade status"""
        if trade_id not in self.open_trades:
            return None

        trade = self.open_trades[trade_id]
        return {
            "entry_price": trade["entry_price"],
            "quantity": trade["quantity"],
            "partial_booked": trade.get("partial_booked", False),
            "trail_count": trade.get("trail_count", 0),
            "highest_price": trade.get("highest_price"),
            "lowest_price": trade.get("lowest_price"),
        }


def create_trade_manager(config: dict = None) -> TradeManager:
    return TradeManager(config)
