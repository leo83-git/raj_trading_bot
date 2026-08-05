import logging
import time
from typing import Any

try:
    from core.database import DatabaseManager
except Exception:
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
    ):
        self.broker = broker
        self.mode = mode
        self.simulation = simulation_engine
        self.db_manager = db_manager

        if self.db_manager is None and DatabaseManager is not None:
            try:
                self.db_manager = DatabaseManager()
                log.info("DatabaseManager initialized for order persistence")
            except Exception as exc:
                log.debug(f"DatabaseManager unavailable for order persistence: {exc}")
        
    def place_order(self, symbol: str, quantity: int, action: str, order_type: str = "MARKET", price: float = 0.0, trigger_price: float = 0.0, product: str = "MIS", exchange: str = "NSE", metadata: dict = None) -> dict:
        """Place an order through the broker or simulation engine."""
        log.info(f"Placing {action} order for {quantity} {symbol} at {price if order_type != 'MARKET' else 'MARKET'} (Mode: {self.mode})")
        
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
                exchange=exchange
            )
            self._persist_trade(symbol, quantity, action, price, order_id)
            return order_id
        except Exception as e:
            log.error(f"Failed to place order: {e}")
            return None

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
                "INFO", f"Trade persisted: {action} {quantity} {symbol}", source="order_manager"
            )
        except Exception as exc:
            log.debug(f"Failed to persist trade for {symbol}: {exc}")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order."""
        log.info(f"Canceling order {order_id}")
        try:
            return self.broker.cancel_order(order_id)
        except Exception as e:
            log.error(f"Failed to cancel order: {e}")
            return False

    def place_orders_concurrently(self, orders: list) -> list:
        """
        Execute multiple orders concurrently.
        Each order should be a dictionary containing kwargs for place_order.
        """
        import concurrent.futures
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(orders) or 1) as executor:
            future_to_order = {
                executor.submit(self.place_order, **order): order
                for order in orders
            }
            for future in concurrent.futures.as_completed(future_to_order):
                order = future_to_order[future]
                try:
                    result = future.result()
                    results.append({"order": order, "result": result, "status": "success"})
                except Exception as e:
                    log.error(f"Concurrent order failed for {order.get('symbol')}: {e}")
                    results.append({"order": order, "result": None, "status": "failed", "error": str(e)})
        return results

    def modify_order(self, order_id: str, new_price: float, new_qty: int = None) -> bool:
        """Modify an existing order. Requires broker support for modify."""
        log.info(f"Modifying order {order_id} to price {new_price}")
        try:
            if hasattr(self.broker, 'modify_order'):
                return self.broker.modify_order(order_id, price=new_price, qty=new_qty)
            else:
                log.warning("Broker does not support modify_order natively")
                return False
        except Exception as e:
            log.error(f"Failed to modify order: {e}")
            return False
