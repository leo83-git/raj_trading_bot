import logging

from execution.models import ExitDecision, ExitPolicyInput, evaluate_exit_policy

log = logging.getLogger("position_tracker")


class PositionTracker:
    """
    Tracks open positions and calculates PnL.
    Abstracts position retrieval between LIVE broker and PAPER simulation.
    """

    def __init__(self, broker=None, mode: str = "PAPER", simulation_engine=None):
        self.positions = {}
        self.broker = broker
        self.mode = mode
        self.simulation = simulation_engine

    def add_position(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        action: str,
        metadata: dict | None = None,
    ):
        """Add or update a position (used mostly for internal tracking if needed)"""
        if symbol not in self.positions:
            self.positions[symbol] = {
                "symbol": symbol,
                "quantity": 0,
                "entry_price": 0.0,
                "action": action,
                "metadata": metadata or {},
            }

        pos = self.positions[symbol]

        # Simple averaging for now (can be expanded)
        total_cost = (pos["quantity"] * pos["entry_price"]) + (quantity * entry_price)
        pos["quantity"] += quantity
        if pos["quantity"] > 0:
            pos["entry_price"] = total_cost / pos["quantity"]
        else:
            pos["entry_price"] = 0.0

        log.info(
            f"Position updated for {symbol}: qty {pos['quantity']} at {pos['entry_price']:.2f}"
        )

    def remove_position(self, symbol: str):
        """Remove a closed position"""
        if symbol in self.positions:
            del self.positions[symbol]
            log.info(f"Position closed for {symbol}")

    def get_position(self, symbol: str):
        """Get details of a specific position"""
        all_pos = self.get_all_positions()
        for p in all_pos:
            if p.get("symbol") == symbol:
                return p
        return None

    def get_all_positions(self) -> list:
        """Get all open positions from either Simulation or Broker"""
        if self.mode == "PAPER" and self.simulation:
            return self.simulation.get_positions()

        if (
            self.mode == "LIVE"
            and self.broker
            and hasattr(self.broker, "get_positions")
        ):
            try:
                broker_positions = self.broker.get_positions()
                # Ensure we return a consistent format
                return broker_positions if isinstance(broker_positions, list) else []
            except Exception as e:
                log.error(f"Failed to fetch live positions from broker: {e}")
                return []

        return []

    def evaluate_exit(
        self, position: dict, current_price: float, **overrides
    ) -> ExitDecision:
        """Evaluate exit policy after a caller has resolved current market data."""
        metadata = position.get("metadata", {}) or {}
        policy_input = ExitPolicyInput(
            action=str(position.get("action", "BUY")),
            current_price=float(current_price),
            stop_loss=float(
                overrides.get("stop_loss", metadata.get("stop_loss", 0)) or 0
            ),
            target=float(overrides.get("target", metadata.get("target", 0)) or 0),
            trailing_stop=float(
                overrides.get("trailing_stop", metadata.get("trailing_stop", 0)) or 0
            ),
            profit_ladder_price=float(overrides.get("profit_ladder_price", 0) or 0),
            emergency_halt=bool(overrides.get("emergency_halt", False)),
            square_off_due=bool(overrides.get("square_off_due", False)),
            expired=bool(overrides.get("expired", False)),
        )
        return evaluate_exit_policy(policy_input)
