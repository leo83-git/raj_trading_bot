import logging
from datetime import UTC, datetime
from typing import Any

from core.trade_validator import PortfolioGreeks, TradeIntent, TradeValidator

try:
    from core.database import DatabaseManager
except Exception:  # noqa: BLE001 - database support is optional at this boundary
    DatabaseManager = None  # type: ignore

log = logging.getLogger("order_manager")


class OrderManager:
    """
    Manages order execution, smart routing, and tracking.
    Acts as a layer above the raw broker to provide advanced execution features.
    """

    def __init__(
        self,
        broker,
        mode: str = "PAPER",
        simulation_engine=None,
        db_manager: Any | None = None,
        trade_validator: TradeValidator | None = None,
    ):
        self.broker = broker
        self.mode = mode
        self.simulation = simulation_engine
        self.db_manager = db_manager
        self.trade_validator = trade_validator

        if self.db_manager is None and DatabaseManager is not None:
            try:
                self.db_manager = DatabaseManager()
                log.info("DatabaseManager initialized for order persistence")
            except Exception as exc:  # noqa: BLE001 - optional database boundary
                log.debug(f"DatabaseManager unavailable for order persistence: {exc}")

    def place_order(
        self,
        symbol: str,
        quantity: int,
        action: str,
        order_type: str = "MARKET",
        price: float = 0.0,
        trigger_price: float = 0.0,
        product: str = "MIS",
        exchange: str = "NSE",
        metadata: dict | None = None,
    ) -> dict:
        """Place an order through the broker or simulation engine."""
        metadata = metadata or {}
        if self.trade_validator is not None:
            intent = self._build_trade_intent(
                symbol, quantity, action, order_type, price, metadata
            )
            try:
                validation = self.trade_validator.validate(intent)
            except Exception:  # Validation must fail closed before broker submission.
                log.exception(
                    "trade_validation_exception symbol=%s action=%s", symbol, action
                )
                return {
                    "status": "rejected",
                    "reason": "trade_validation_exception",
                    "errors": ["validator_exception"],
                }
            if not validation.is_valid:
                return {
                    "status": "rejected",
                    "reason": (
                        "trade_validation_halted"
                        if validation.status == "halted"
                        else "trade_validation_failed"
                    ),
                    "errors": list(validation.errors),
                }
        log.info(
            f"Placing {action} order for {quantity} {symbol} at {price if order_type != 'MARKET' else 'MARKET'} (Mode: {self.mode})"
        )

        if order_type == "SMART_LIMIT":
            # Convert a SMART_LIMIT request into a normal LIMIT order with a buffer
            # to ensure execution while protecting against extreme slippage.
            slippage_buffer = 0.005  # 0.5% buffer
            if action == "BUY":
                price = price * (1 + slippage_buffer)
            else:
                price = price * (1 - slippage_buffer)
            order_type = "LIMIT"
            log.info(f"SMART_LIMIT adjusted price to {price:.2f} for {symbol}")

        if self.mode == "PAPER" and self.simulation:
            if action == "SELL":
                result = self.simulation.sell_to_open(
                    symbol, price, quantity, metadata or {}
                )
            else:
                result = self.simulation.buy(symbol, price, quantity, metadata or {})
            self._persist_trade(symbol, quantity, action, price, result)
            return result

        try:
            order_id = self.broker.place_order(
                symbol=symbol,
                qty=quantity,
                action=action,
                order_type=order_type,
                price=price,
                trigger_price=trigger_price,
                product=product,
                exchange=exchange,
            )
            self._persist_trade(symbol, quantity, action, price, order_id)
            return order_id
        except Exception as e:  # noqa: BLE001 - broker boundary must isolate failures
            log.error(f"Failed to place order: {e}")
            return None

    def _build_trade_intent(
        self,
        symbol: str,
        quantity: int,
        action: str,
        order_type: str,
        price: float,
        metadata: dict[str, Any],
    ) -> TradeIntent:
        """Translate the stable order API plus metadata into a typed intent."""
        entry = float(metadata.get("entry", price) or price)
        greeks = metadata.get("portfolio_greeks", {}) or {}
        timestamp = metadata.get("quote_timestamp", datetime.now(UTC))
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        return TradeIntent(
            symbol=symbol,
            action=action,
            quantity=quantity,
            entry_price=entry,
            stop_loss=float(metadata.get("stop_loss", 0) or 0),
            target=float(metadata.get("target", 0) or 0),
            quote_timestamp=timestamp,
            session_open=bool(metadata.get("session_open", False)),
            is_exit=bool(metadata.get("is_exit", False)),
            order_type=order_type,
            limit_price=price if order_type.upper() == "LIMIT" else None,
            lot_size=int(metadata.get("lot_size", 1) or 1),
            tick_size=float(metadata.get("tick_size", 0.05) or 0.05),
            bid_price=float(metadata.get("bid_price", entry) or 0),
            ask_price=float(metadata.get("ask_price", entry) or 0),
            available_quantity=int(metadata.get("available_quantity", quantity) or 0),
            expected_slippage_pct=float(metadata.get("expected_slippage_pct", 0) or 0),
            required_margin=float(
                metadata.get("required_margin", entry * quantity) or 0
            ),
            available_margin=float(
                metadata.get("available_margin", entry * quantity) or 0
            ),
            projected_exposure_pct=float(
                metadata.get("projected_exposure_pct", 0) or 0
            ),
            implied_volatility=float(metadata.get("implied_volatility", 0) or 0),
            drawdown_pct=float(metadata.get("drawdown_pct", 0) or 0),
            max_correlation=float(metadata.get("max_correlation", 0) or 0),
            strategy_max_loss=float(
                metadata.get(
                    "strategy_max_loss",
                    abs(entry - float(metadata.get("stop_loss", entry) or entry))
                    * quantity,
                )
                or 0
            ),
            portfolio_greeks=PortfolioGreeks(
                **{
                    key: float(greeks.get(key, 0) or 0)
                    for key in ("delta", "gamma", "theta", "vega")
                }
            ),
            circuit_breaker_active=bool(metadata.get("circuit_breaker_active", False)),
            kill_switch_active=bool(metadata.get("kill_switch_active", False)),
        )

    def _persist_trade(
        self,
        symbol: str,
        quantity: int,
        action: str,
        price: float,
        result: Any,
    ) -> None:
        """Persist a successful trade/fill to the database when available."""
        if not self.db_manager:
            return

        trade_data = {
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "action": action,
            "pnl": 0.0,
        }

        if isinstance(result, dict):
            trade_data["pnl"] = float(result.get("pnl", 0.0) or 0.0)
            trade_data["price"] = float(result.get("price", price) or price)
            trade_data["quantity"] = int(result.get("quantity", quantity) or quantity)

        try:
            self.db_manager.save_trade(trade_data)
            self.db_manager.save_log_event(
                "INFO",
                f"Trade persisted: {action} {quantity} {symbol}",
                source="order_manager",
            )
        except Exception as exc:  # noqa: BLE001 - persistence must not fail an order
            log.debug(f"Failed to persist trade for {symbol}: {exc}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        log.info(f"Canceling order {order_id}")
        try:
            return self.broker.cancel_order(order_id)
        except Exception as e:  # noqa: BLE001 - broker boundary must isolate failures
            log.error(f"Failed to cancel order: {e}")
            return False

    def place_orders_concurrently(self, orders: list) -> list:
        """
        Execute multiple orders concurrently.
        Each order should be a dictionary containing kwargs for place_order.
        """
        import concurrent.futures

        results = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(orders) or 1
        ) as executor:
            future_to_order = {
                executor.submit(self.place_order, **order): order for order in orders
            }
            for future in concurrent.futures.as_completed(future_to_order):
                order = future_to_order[future]
                try:
                    result = future.result()
                    results.append(
                        {"order": order, "result": result, "status": "success"}
                    )
                except Exception as e:  # noqa: BLE001 - isolate each order future
                    log.error(f"Concurrent order failed for {order.get('symbol')}: {e}")
                    results.append(
                        {
                            "order": order,
                            "result": None,
                            "status": "failed",
                            "error": str(e),
                        }
                    )
        return results

    def modify_order(
        self, order_id: str, new_price: float, new_qty: int | None = None
    ) -> bool:
        """Modify an existing order. Requires broker support for modify."""
        log.info(f"Modifying order {order_id} to price {new_price}")
        try:
            if hasattr(self.broker, "modify_order"):
                return self.broker.modify_order(order_id, price=new_price, qty=new_qty)
            else:
                log.warning("Broker does not support modify_order natively")
                return False
        except Exception as e:  # noqa: BLE001 - broker boundary must isolate failures
            log.error(f"Failed to modify order: {e}")
            return False
