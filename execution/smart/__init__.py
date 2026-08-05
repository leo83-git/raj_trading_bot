# ═══════════════════════════════════════════════════════════════
#  Execution Engine — TWAP, VWAP, Broker routing
# ═══════════════════════════════════════════════════════════════
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("execution.smart")


@dataclass
class OrderResult:
    order_id: str
    status: str
    symbol: str
    quantity: int
    price: float | None
    message: str


class TWAPExecutor:
    """Time-Weighted Average Price execution"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.slices = self.config.get("slices", 10)
        self.slice_interval = self.config.get("slice_interval", 60)

    def execute(
        self, broker, symbol: str, action: str, quantity: int, order_type: str = "LIMIT"
    ) -> OrderResult:
        """Execute TWAP order"""
        slice_qty = quantity // self.slices
        remainder = quantity % self.slices

        results = []

        for i in range(self.slices):
            qty = slice_qty + (1 if i < remainder else 0)

            if qty == 0:
                continue

            try:
                result = broker.place_order(
                    symbol=symbol,
                    transaction_type=action,
                    quantity=qty,
                    order_type=order_type,
                )
                results.append(result)
                log.info(f"TWAP slice {i + 1}/{self.slices}: {qty} {symbol}")

                if i < self.slices - 1:
                    time.sleep(self.slice_interval)

            except Exception as e:
                log.error(f"TWAP slice {i} failed: {e}")

        return OrderResult(
            order_id=f"TWAP_{int(time.time())}",
            status="success" if results else "partial",
            symbol=symbol,
            quantity=quantity,
            price=None,
            message=f"Slices: {len(results)}/{self.slices}",
        )


class VWAPExecutor:
    """Volume-Weighted Average Price execution"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.min_slices = self.config.get("min_slices", 5)

    def execute(
        self, broker, symbol: str, action: str, quantity: int, candles: list[dict]
    ) -> OrderResult:
        """Execute VWAP order"""
        if not candles:
            return self._execute_market(broker, symbol, action, quantity)

        total_volume = sum(c.get("volume", 0) for c in candles)

        if total_volume == 0:
            return self._execute_market(broker, symbol, action, quantity)

        slice_qty = quantity // len(candles)

        results = []

        for i, candle in enumerate(candles):
            volume = candle.get("volume", 0)
            if volume == 0:
                continue

            vwap_price = (
                candle.get("close", 0) * volume + candle.get("open", 0) * volume
            ) / (volume * 2)

            try:
                result = broker.place_order(
                    symbol=symbol,
                    transaction_type=action,
                    quantity=slice_qty,
                    order_type="LIMIT",
                    price=vwap_price,
                )
                results.append(result)
                log.info(f"VWAP slice {i + 1}: {slice_qty} @ {vwap_price:.2f}")

                time.sleep(2)

            except Exception as e:
                log.error(f"VWAP slice {i} failed: {e}")

        return OrderResult(
            order_id=f"VWAP_{int(time.time())}",
            status="success" if results else "partial",
            symbol=symbol,
            quantity=quantity,
            price=None,
            message=f"Slices: {len(results)}",
        )

    def _execute_market(
        self, broker, symbol: str, action: str, quantity: int
    ) -> OrderResult:
        """Fallback to market execution"""
        try:
            result = broker.place_order(
                symbol=symbol,
                transaction_type=action,
                quantity=quantity,
                order_type="MARKET",
            )
            return OrderResult(
                order_id=result.get("order_id", "MARKET"),
                status=result.get("status", "success"),
                symbol=symbol,
                quantity=quantity,
                price=None,
                message="Market execution",
            )
        except Exception as e:
            return OrderResult(
                order_id="ERROR",
                status="failed",
                symbol=symbol,
                quantity=quantity,
                price=None,
                message=str(e),
            )


class BrokerRouter:
    """Multi-broker routing with failover"""

    def __init__(self, brokers: list, config: dict = None):
        self.brokers = brokers
        self.config = config or {}

        self.primary = brokers[0] if brokers else None
        self.failover = self.config.get("failover", True)

        self.order_history = []
        self.failed_orders = []

        log.info(f"Broker router initialized with {len(brokers)} brokers")

    def execute(
        self,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str = "MARKET",
        price: float | None = None,
        use_twap: bool = False,
        use_vwap: bool = False,
        candles: list[dict] = None,
    ) -> OrderResult:
        """Execute order with routing"""

        if not self.primary:
            return OrderResult("ERROR", "failed", symbol, quantity, price, "No broker")

        if use_twap:
            executor = TWAPExecutor()
            return executor.execute(self.primary, symbol, action, quantity, order_type)

        if use_vwap and candles:
            executor = VWAPExecutor()
            return executor.execute(self.primary, symbol, action, quantity, candles)

        for broker in self.brokers:
            try:
                result = broker.place_order(
                    symbol=symbol,
                    transaction_type=action,
                    quantity=quantity,
                    order_type=order_type,
                    price=price,
                )

                if result and result.get("status") == "success":
                    self.order_history.append(
                        {
                            "broker": broker.name,
                            "symbol": symbol,
                            "quantity": quantity,
                            "status": "success",
                        }
                    )
                    return OrderResult(
                        order_id=result.get("order_id", "ORDER"),
                        status="success",
                        symbol=symbol,
                        quantity=quantity,
                        price=price,
                        message=f"Executed via {broker.name}",
                    )

            except Exception as e:
                log.error(f"Order failed on {broker.name}: {e}")
                continue

        self.failed_orders.append(
            {"symbol": symbol, "quantity": quantity, "reason": "All brokers failed"}
        )

        return OrderResult(
            "ERROR", "failed", symbol, quantity, price, "All brokers failed"
        )

    def get_stats(self) -> dict:
        """Get execution stats"""
        total = len(self.order_history)
        success = sum(1 for o in self.order_history if o.get("status") == "success")

        return {
            "total_orders": total,
            "successful": success,
            "failed": len(self.failed_orders),
            "success_rate": round(success / total, 2) if total > 0 else 0,
        }


class ExecutionEngine:
    """Main execution engine"""

    _initialized = False

    def __init__(self, brokers: list, config: dict = None):
        self.config = config or {}
        self.brokers = brokers
        self.router = BrokerRouter(brokers, config)

        log.info("Execution engine initialized")

    def execute_equity(
        self,
        symbol: str,
        action: str,
        quantity: int,
        execution_type: str = "MARKET",
        candles: list[dict] = None,
    ) -> OrderResult:
        """Execute equity order"""

        if execution_type == "TWAP":
            return self.router.execute(symbol, action, quantity, "LIMIT", use_twap=True)

        if execution_type == "VWAP" and candles:
            return self.router.execute(
                symbol, action, quantity, "LIMIT", use_vwap=True, candles=candles
            )

        return self.router.execute(symbol, action, quantity, execution_type)

    def execute_options(self, symbol: str, legs: list[dict]) -> list[OrderResult]:
        """Execute options order"""
        results = []

        for leg in legs:
            result = self.router.execute(
                symbol=leg.get("symbol", symbol),
                action=leg.get("action", "BUY"),
                quantity=leg.get("quantity", 1),
                order_type="MARKET",
            )
            results.append(result)

            time.sleep(0.5)

        return results

    def cancel_order(self, order_id: str) -> dict:
        """Cancel order"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "cancel_order"):
                    return broker.cancel_order(order_id)
            except Exception as e:
                log.error(f"Cancel failed: {e}")

        return {"status": "error", "message": "Cancel failed"}

    def get_order_status(self, order_id: str) -> dict:
        """Get order status"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_order_status"):
                    return broker.get_order_status(order_id)
            except Exception as e:
                log.error(f"Status check failed: {e}")

        return {"status": "unknown"}


execution_engine = ExecutionEngine([])
