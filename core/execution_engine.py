import logging
import time
from typing import Any

try:
    from core.database import DatabaseManager
except Exception:
    DatabaseManager = None  # type: ignore

log = logging.getLogger("execution_engine")


class ExecutionEngine:
    """
    Orchestrates the lifecycle of the trading bot:
    - Screener polling
    - Strategy evaluation
    - Order management
    - Position tracking
    """

    def __init__(
        self,
        broker,
        config,
        order_manager,
        position_tracker,
        risk_manager,
        db_manager: Any | None = None,
    ):
        self.broker = broker
        self.config = config
        self.order_manager = order_manager
        self.position_tracker = position_tracker
        self.risk_manager = risk_manager
        self.db_manager = db_manager
        self.is_running = False

        if self.db_manager is None and DatabaseManager is not None:
            try:
                self.db_manager = DatabaseManager()
                log.info("DatabaseManager initialized for execution persistence")
            except Exception as exc:
                log.debug(
                    f"DatabaseManager unavailable for execution persistence: {exc}"
                )

        if hasattr(self.broker, "set_recovery_callback"):
            try:
                self.broker.set_recovery_callback(self.handle_websocket_disconnect)
                log.info("Registered execution engine websocket recovery callback")
            except Exception as exc:
                log.warning(f"Failed to register recovery callback on broker: {exc}")

    def reconcile_state(self) -> None:
        """
        Reconcile internal position tracking with the broker's live positions.

        The broker is treated as the source of truth. Any discrepancies are logged and
        the internal tracker is updated to match broker state.
        """
        if not self.broker or not hasattr(self.broker, "get_positions"):
            log.warning(
                "Skipping reconciliation: broker does not expose get_positions()"
            )
            return

        if not self.position_tracker or not hasattr(
            self.position_tracker, "get_all_positions"
        ):
            log.warning(
                "Skipping reconciliation: position_tracker does not expose get_all_positions()"
            )
            return

        try:
            broker_positions = self.broker.get_positions() or []
        except Exception as exc:
            log.error(
                f"Failed to fetch positions from broker during reconciliation: {exc}"
            )
            return

        try:
            internal_positions = self.position_tracker.get_all_positions() or []
        except Exception as exc:
            log.error(
                f"Failed to fetch internal positions during reconciliation: {exc}"
            )
            internal_positions = []

        broker_map = self._build_position_map(broker_positions)
        internal_map = self._build_position_map(internal_positions)

        broker_symbols = set(broker_map)
        internal_symbols = set(internal_map)

        missing_in_internal = broker_symbols - internal_symbols
        missing_in_broker = internal_symbols - broker_symbols
        common_symbols = broker_symbols & internal_symbols

        for symbol in sorted(missing_in_internal):
            log.warning(
                f"Broker position missing from internal tracker: {symbol} -> {broker_map[symbol]}"
            )
            self._persist_log_event(
                "WARNING",
                f"Broker position missing from internal tracker: {symbol}",
            )

        for symbol in sorted(missing_in_broker):
            log.warning(
                f"Internal position missing from broker snapshot: {symbol} -> {internal_map[symbol]}"
            )
            self._persist_log_event(
                "WARNING",
                f"Internal position missing from broker snapshot: {symbol}",
            )

        for symbol in sorted(common_symbols):
            broker_pos = broker_map[symbol]
            internal_pos = internal_map[symbol]
            if broker_pos != internal_pos:
                log.warning(
                    f"Position mismatch for {symbol}: broker={broker_pos}, internal={internal_pos}"
                )
                self._persist_log_event("WARNING", f"Position mismatch for {symbol}")

        synced_positions = [broker_map[symbol] for symbol in sorted(broker_map)]
        self._sync_position_tracker(synced_positions)
        self._persist_positions(synced_positions)
        log.info(
            "State reconciliation complete: "
            f"broker_positions={len(broker_positions)}, internal_positions={len(internal_positions)}, "
            f"synced_positions={len(synced_positions)}"
        )
        self._persist_log_event(
            "INFO",
            "State reconciliation complete",
        )

    def _build_position_map(self, positions: Any) -> dict[str, dict[str, Any]]:
        """Normalize a list of broker/tracker positions into a symbol-keyed map."""
        normalized: dict[str, dict[str, Any]] = {}
        if not isinstance(positions, list):
            return normalized

        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = self._extract_symbol(position)
            if not symbol:
                continue
            normalized[symbol] = position
        return normalized

    def _extract_symbol(self, position: dict[str, Any]) -> str:
        """Best-effort extraction of a position symbol from common payload formats."""
        for key in ("symbol", "tradingsymbol", "trading_symbol", "instrument", "name"):
            value = position.get(key)
            if value:
                return str(value)
        return ""

    def _sync_position_tracker(self, broker_positions: list[dict[str, Any]]) -> None:
        """Update internal tracker state to mirror broker positions."""
        if not hasattr(self.position_tracker, "positions"):
            log.warning(
                "position_tracker has no mutable positions store; reconciliation limited"
            )
            return

        new_state: dict[str, dict[str, Any]] = {}
        for position in broker_positions:
            symbol = self._extract_symbol(position)
            if symbol:
                new_state[symbol] = position

        self.position_tracker.positions = new_state

    def _persist_positions(self, positions: list[dict[str, Any]]) -> None:
        """Persist broker positions when the ORM layer is available."""
        if not self.db_manager:
            return

        for position in positions:
            symbol = self._extract_symbol(position)
            if not symbol:
                continue
            try:
                self.db_manager.update_position(
                    {
                        "symbol": symbol,
                        "quantity": int(position.get("quantity", 0) or 0),
                        "avg_price": float(
                            position.get("avg_price", position.get("price", 0.0)) or 0.0
                        ),
                        "action": str(position.get("action", "HOLD")),
                    }
                )
            except Exception as exc:
                log.debug(f"Failed to persist position {symbol}: {exc}")

    def _persist_log_event(self, level: str, message: str) -> None:
        """Persist a critical lifecycle event when possible."""
        if not self.db_manager:
            return
        try:
            self.db_manager.save_log_event(level, message, source="execution_engine")
        except Exception as exc:
            log.debug(f"Failed to persist log event '{message}': {exc}")

    def handle_websocket_disconnect(self) -> None:
        """
        Recovery hook to call after a WebSocket disconnect.

        Any websocket or broker layer can invoke this method once a disconnect
        has been detected so the engine can resynchronize with the broker.
        """
        log.warning("WebSocket disconnect detected; reconciling state")
        self.reconcile_state()

    def start(self):
        """Start the execution engine loop"""
        log.info("Starting Execution Engine...")
        self.is_running = False
        self.reconcile_state()
        self.is_running = True
        self._run_loop()

    def stop(self):
        """Stop the execution engine"""
        log.info("Stopping Execution Engine...")
        self.is_running = False

    def _run_loop(self):
        """Main orchestrator loop"""
        while self.is_running:
            try:
                # 1. Update positions & PnL

                # 2. Check risk limits (Circuit breakers)

                # 3. Poll Screeners & Strategies

                # 4. Execute Orders

                time.sleep(1)  # Prevent high CPU usage
            except Exception as e:
                log.error(f"Error in execution loop: {e}")
                time.sleep(5)
