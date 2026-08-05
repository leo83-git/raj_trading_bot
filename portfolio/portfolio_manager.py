# ═══════════════════════════════════════════════════════════════
#  Portfolio Manager (Multi-Position Management)
# ═══════════════════════════════════════════════════════════════
import datetime

from quant_utils.logger import get_logger

log = get_logger("portfolio")


class Position:
    def __init__(
        self,
        symbol: str,
        option_symbol: str,
        opt_type: str,
        strike: float,
        entry_price: float,
        quantity: int,
        lots: int,
        sl: float,
        target: float,
        direction: str,
        strategy: str,
        confidence: float,
        spot_at_entry: float,
        atr_at_entry: float = None,
        atr_multiplier_sl: float = 1.5,
        atr_multiplier_target: float = 2.5,
    ):
        self.id = f"{symbol}_{int(datetime.datetime.now().timestamp())}"
        self.symbol = symbol
        self.option_symbol = option_symbol
        self.opt_type = opt_type
        self.strike = strike
        self.entry_price = entry_price
        self.quantity = quantity
        self.lots = lots
        self.sl = sl
        self.target = target
        self.direction = direction
        self.strategy = strategy
        self.confidence = confidence
        self.spot_at_entry = spot_at_entry
        self.entry_time = datetime.datetime.now()
        self.exit_price: float | None = 0.0
        self.exit_time: datetime.datetime | None = None
        self.exit_reason: str = ""
        self.pnl = 0.0
        self.status = "OPEN"
        self.greeks = {}
        self.atr_at_entry = atr_at_entry
        self.atr_multiplier_sl = atr_multiplier_sl
        self.atr_multiplier_target = atr_multiplier_target

    @classmethod
    def create_with_atr(
        cls,
        symbol: str,
        option_symbol: str,
        opt_type: str,
        strike: float,
        entry_price: float,
        quantity: int,
        lots: int,
        direction: str,
        strategy: str,
        confidence: float,
        spot_at_entry: float,
        atr_at_entry: float,
        atr_multiplier_sl: float = 1.5,
        atr_multiplier_target: float = 2.5,
    ) -> "Position":
        """Create position with ATR-based stops and targets"""
        if direction == "BULLISH":
            sl = entry_price - (atr_at_entry * atr_multiplier_sl)
            target = entry_price + (atr_at_entry * atr_multiplier_target)
        else:
            sl = entry_price + (atr_at_entry * atr_multiplier_sl)
            target = entry_price - (atr_at_entry * atr_multiplier_target)

        return cls(
            symbol=symbol,
            option_symbol=option_symbol,
            opt_type=opt_type,
            strike=strike,
            entry_price=entry_price,
            quantity=quantity,
            lots=lots,
            sl=sl,
            target=target,
            direction=direction,
            strategy=strategy,
            confidence=confidence,
            spot_at_entry=spot_at_entry,
            atr_at_entry=atr_at_entry,
            atr_multiplier_sl=atr_multiplier_sl,
            atr_multiplier_target=atr_multiplier_target,
        )

    def get_dynamic_levels(self, current_atr: float) -> dict:
        """Get dynamic ATR-based levels"""
        if current_atr is None:
            current_atr = self.atr_at_entry or 0

        if self.direction == "BULLISH":
            sl = self.entry_price - (current_atr * self.atr_multiplier_sl)
            target = self.entry_price + (current_atr * self.atr_multiplier_target)
        else:
            sl = self.entry_price + (current_atr * self.atr_multiplier_sl)
            target = self.entry_price - (current_atr * self.atr_multiplier_target)

        return {
            "stop_loss": round(sl, 2),
            "target": round(target, 2),
            "risk_reward": round(
                self.atr_multiplier_target / self.atr_multiplier_sl, 2
            ),
            "atr_distance": current_atr,
        }

    def update_with_atr(self, current_atr: float):
        """Update SL/Target based on current ATR"""
        levels = self.get_dynamic_levels(current_atr)
        self.sl = levels["stop_loss"]
        self.target = levels["target"]
        log.debug(f"Updated levels: SL={self.sl}, Target={self.target}")

    def apply_trailing_stop(
        self, current_price: float, atr: float, trailing_atr: float = 1.0
    ):
        """Apply trailing stop: move SL up when in profit"""
        profit_distance = abs(current_price - self.entry_price)

        if profit_distance < atr * 0.5:
            return False

        if self.direction == "BULLISH":
            new_sl = current_price - (atr * trailing_atr)
            if new_sl > self.sl:
                self.sl = new_sl
                log.info(f"Trailed SL UP to {self.sl:.2f}")
                return True
        else:
            new_sl = current_price + (atr * trailing_atr)
            if new_sl < self.sl:
                self.sl = new_sl
                log.info(f"Trailed SL DOWN to {self.sl:.2f}")
                return True

        return False

    def current_pnl(self, ltp: float) -> float:
        return (ltp - self.entry_price) * self.quantity

    def is_sl_hit(self, ltp: float) -> bool:
        if self.direction == "BULLISH":
            return ltp <= self.sl
        return ltp >= self.sl

    def is_target_hit(self, ltp: float) -> bool:
        if self.direction == "BULLISH":
            return ltp >= self.target
        return ltp <= self.target


class PortfolioManager:
    """Manages multi-position portfolio"""

    def __init__(self, broker, config: dict | None = None):
        self.broker = broker
        self.config = config or {}
        self.positions = []
        self.position_history = []
        self.daily_pnl = 0.0
        self.win_count = 0
        self.loss_count = 0

        log.info("Portfolio manager initialized")

    def add_position(self, position: Position):
        """Add new position to portfolio"""
        self.positions.append(position)
        log.info(f"Position added: {position.option_symbol}")

    def close_position(self, position: Position, exit_price: float, reason: str):
        """Close position and update PnL (accounting for broker fees)"""
        position.exit_price = exit_price
        position.exit_time = datetime.datetime.now()
        position.exit_reason = reason
        position.status = "CLOSED"

        # Calculate gross P&L before fees
        gross_pnl = position.current_pnl(exit_price)

        # Deduct broker fees: ₹20 entry + ₹20 exit = ₹40 total per trade
        broker_fees = 40
        position.pnl = gross_pnl - broker_fees  # Net P&L after fees

        self.daily_pnl += position.pnl

        if position.pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

        self.positions.remove(position)
        self.position_history.append(position)

        log.info(
            f"Position closed: {position.option_symbol} | Gross P&L: ₹{gross_pnl:.2f} | "
            f"Broker Fees: -₹{broker_fees:.2f} | Net P&L: ₹{position.pnl:.2f} | Reason: {reason}"
        )

    def get_portfolio_greeks(self) -> dict:
        """Get portfolio-level Greeks"""
        total_delta = 0
        total_gamma = 0
        total_vega = 0
        total_theta = 0

        for pos in self.positions:
            total_delta += pos.greeks.get("delta", 0) * pos.quantity
            total_gamma += pos.greeks.get("gamma", 0) * pos.quantity
            total_vega += pos.greeks.get("vega", 0) * pos.quantity
            total_theta += pos.greeks.get("theta", 0) * pos.quantity

        return {
            "delta": total_delta,
            "gamma": total_gamma,
            "vega": total_vega,
            "theta": total_theta,
        }

    def get_exposure(self) -> dict:
        """Get portfolio exposure"""
        long_exposure = sum(
            p.entry_price * p.quantity
            for p in self.positions
            if p.direction == "BULLISH"
        )
        short_exposure = sum(
            p.entry_price * p.quantity
            for p in self.positions
            if p.direction == "BEARISH"
        )

        return {
            "long_exposure": long_exposure,
            "short_exposure": short_exposure,
            "net_exposure": long_exposure - short_exposure,
            "total_exposure": long_exposure + short_exposure,
            "positions_count": len(self.positions),
        }

    def get_pnl_summary(self) -> dict:
        """Get PnL summary"""
        total_trades = len(self.position_history)
        win_rate = self.win_count / total_trades if total_trades > 0 else 0

        total_pnl = sum(p.pnl for p in self.position_history)

        return {
            "daily_pnl": self.daily_pnl,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": win_rate,
        }

    def monitor_positions(self):
        """Monitor all positions"""
        for position in self.positions[:]:
            try:
                ltp = self.broker.get_ltp("NFO", position.option_symbol)
                if not ltp:
                    continue

                if position.is_sl_hit(ltp):
                    self.close_position(position, ltp, "SL_HIT")
                elif position.is_target_hit(ltp):
                    self.close_position(position, ltp, "TARGET_HIT")

            except Exception as e:
                log.error(f"Error monitoring {position.option_symbol}: {e}")

    def close_all(self, reason: str = "MANUAL"):
        """Close all positions"""
        for position in self.positions[:]:
            try:
                ltp = (
                    self.broker.get_ltp("NFO", position.option_symbol)
                    or position.entry_price
                )
                self.close_position(position, ltp, reason)
            except Exception as e:
                log.error(f"Error closing {position.option_symbol}: {e}")
