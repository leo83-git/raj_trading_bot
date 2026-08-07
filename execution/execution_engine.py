# ═══════════════════════════════════════════════════════════════
#  Execution Engine (Multi-Broker Routing)
# ═══════════════════════════════════════════════════════════════
import time

from quant_utils.logger import get_logger

log = get_logger("execution")


class ExecutionEngine:
    """Multi-broker execution with failover"""

    def __init__(self, brokers: list, config: dict | None = None):
        self.brokers = brokers
        self.config = config or {}

        self.primary_broker = brokers[0] if brokers else None
        self.failover_enabled = self.config.get("failover_enabled", True)

        self.order_history = []
        self.failed_orders = []

        log.info(f"Execution engine initialized with {len(brokers)} brokers")

    def place_order(
        self,
        symbol: str,
        exchange: str,
        token: str,
        transaction_type: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> dict:
        """Place order with automatic failover"""
        if not self.primary_broker:
            return {"status": "error", "message": "No brokers available"}

        for broker in self.brokers:
            try:
                result = broker.place_order(
                    symbol=symbol,
                    exchange=exchange,
                    token=token,
                    transaction_type=transaction_type,
                    quantity=quantity,
                    order_type=order_type,
                    price=price,
                )

                if result and result.get("status") in ("success", True):
                    log.info(
                        f"Order placed: {broker.name} | {transaction_type} {quantity} {symbol}"
                    )
                    self.order_history.append(
                        {
                            "broker": broker.name,
                            "symbol": symbol,
                            "quantity": quantity,
                            "status": "success",
                        }
                    )
                    return result
                else:
                    log.warning(f"Order failed on {broker.name}: {result}")

            except Exception as e:
                log.error(f"Error on {broker.name}: {e}")
                continue

        log.error(f"All brokers failed for {symbol}")
        self.failed_orders.append(
            {"symbol": symbol, "quantity": quantity, "reason": "All brokers failed"}
        )

        return {"status": "error", "message": "All brokers failed"}

    def place_multi_leg_order(
        self, legs: list[dict], producttype: str = "INTRADAY"
    ) -> list[dict]:
        """Place multi-leg order"""
        if not self.primary_broker:
            return [{"status": "error", "message": "No brokers available"}]

        if hasattr(self.primary_broker, "place_multi_leg_order"):
            return self.primary_broker.place_multi_leg_order(legs, producttype)

        results = []
        for leg in legs:
            result = self.place_order(
                symbol=leg.get("symbol"),
                exchange=leg.get("exchange"),
                token=leg.get("token"),
                transaction_type=leg.get("transaction_type"),
                quantity=leg.get("quantity"),
                order_type=leg.get("order_type", "MARKET"),
                price=leg.get("price"),
            )
            results.append(result)
            time.sleep(0.2)

        return results

    def cancel_order(self, order_id: str) -> dict:
        """Cancel order"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "cancel_order"):
                    result = broker.cancel_order(order_id)
                    if result:
                        return result
            except Exception as e:
                log.error(f"Cancel error on {broker.name}: {e}")
                continue

        return {"status": "error", "message": "Cancel failed"}

    def get_order_status(self, order_id: str) -> dict:
        """Get order status"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_order_status"):
                    result = broker.get_order_status(order_id)
                    if result:
                        return result
            except Exception as e:
                log.error(f"Status check error on {broker.name}: {e}")
                continue

        return {"status": "unknown"}

    def reconcile_order(self, order_id: str) -> dict:
        """Compatibility adapter: reconciliation is always required before retry."""
        return self.get_order_status(order_id)

    def get_execution_stats(self) -> dict:
        """Get execution statistics"""
        total = len(self.order_history)
        successful = sum(1 for o in self.order_history if o.get("status") == "success")

        return {
            "total_orders": total,
            "successful_orders": successful,
            "failed_orders": len(self.failed_orders),
            "success_rate": successful / total if total > 0 else 0,
        }


class SmartOrderPlacement:
    """Smart order placement strategies"""

    @staticmethod
    def split_order(total_qty: int, slice_count: int = 3) -> list[int]:
        """Split large order into slices"""
        base = total_qty // slice_count
        remainder = total_qty % slice_count

        slices = [base] * slice_count
        for i in range(remainder):
            slices[i] += 1

        return slices

    @staticmethod
    def calculate_slice_interval(total_qty: int, time_window: int = 300) -> float:
        """Calculate time interval between slices"""
        return time_window / total_qty

    @staticmethod
    def get_optimal_order_type(volatility: float, liquidity: float) -> str:
        """Determine optimal order type"""
        if volatility > 0.03 or liquidity < 100000:
            return "LIMIT"
        return "MARKET"
