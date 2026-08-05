# ═══════════════════════════════════════════════════════════════
#  Execution Router — TWAP & VWAP Execution
# ═══════════════════════════════════════════════════════════════
import time
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("execution.router")


class ExecutionRouter:
    """Smart order execution router"""

    def __init__(self, broker, config: dict | None = None):
        self.broker = broker
        self.config = config or {}

        self.max_slippage = self.config.get("max_slippage", 0.002)

    def execute_trade(self, symbol: str, decision: dict, capital: float) -> dict:
        """Execute trade with smart routing"""

        trade_type = decision.get("type", "EQUITY")

        if trade_type == "OPTIONS":
            return self.execute_options(symbol, decision, capital)
        else:
            return self.execute_equity(symbol, decision, capital)

    def execute_equity(self, symbol: str, decision: dict, capital: float) -> dict:
        """Execute equity trade"""

        action = decision.get("action", "BUY")
        entry = decision.get("entry", 0)
        quantity = int(capital / entry) if entry > 0 else 0

        quantity = quantity - (quantity % 1)

        if quantity < 1:
            return {"status": "error", "message": "Insufficient capital"}

        try:
            result = self.broker.place_order(
                symbol=symbol,
                exchange="NSE",
                token="",
                transaction_type=action,
                quantity=quantity,
                order_type="MARKET",
            )

            log.info(f"Equity trade executed: {action} {quantity} {symbol}")

            return result

        except Exception as e:
            log.error(f"Trade execution failed: {e}")
            return {"status": "error", "message": str(e)}

    def execute_options(self, symbol: str, decision: dict, capital: float) -> dict:
        """Execute options trade"""

        legs = decision.get("legs", [])

        if not legs:
            return {"status": "error", "message": "No legs"}

        results = []

        for leg in legs:
            strike = leg.get("strike", 0)
            opt_type = leg.get("opt_type", "CE")
            action = leg.get("action", "BUY")

            option_symbol = f"{symbol}{strike}{opt_type}"

            try:
                lot_size = 25
                quantity = lot_size

                result = self.broker.place_order(
                    symbol=option_symbol,
                    exchange="NFO",
                    token="",
                    transaction_type=action,
                    quantity=quantity,
                    order_type="MARKET",
                )

                results.append(
                    {
                        "symbol": option_symbol,
                        "status": result.get("status"),
                        "order_id": result.get("order_id"),
                    }
                )

                log.info(f"Options leg executed: {action} {option_symbol}")

            except Exception as e:
                log.error(f"Options leg failed: {e}")
                results.append(
                    {"symbol": option_symbol, "status": "error", "message": str(e)}
                )

        return {"status": "success", "legs": results}

    def execute_twap(
        self, symbol: str, action: str, total_quantity: int, slices: int = 10
    ) -> dict:
        """TWAP execution"""
        slice_qty = total_quantity // slices

        results = []

        for i in range(slices):
            try:
                result = self.broker.place_order(
                    symbol=symbol,
                    exchange="NSE",
                    token="",
                    transaction_type=action,
                    quantity=slice_qty,
                    order_type="LIMIT",
                    price=None,
                )

                results.append(result)
                time.sleep(1)

            except Exception as e:
                log.error(f"TWAP slice {i} failed: {e}")

        return {"status": "success", "slices": len(results)}

    def execute_vwap(
        self, symbol: str, action: str, total_quantity: int, candles: list[dict]
    ) -> dict:
        """VWAP execution"""

        if not candles:
            return self.execute_market(symbol, action, total_quantity)

        vwap = sum(c.get("close", 0) * c.get("volume", 0) for c in candles) / sum(
            c.get("volume", 0) for c in candles
        )

        slice_qty = total_quantity // len(candles)

        results = []

        for i, candle in enumerate(candles[:10]):
            price = vwap

            try:
                result = self.broker.place_order(
                    symbol=symbol,
                    exchange="NSE",
                    token="",
                    transaction_type=action,
                    quantity=slice_qty,
                    order_type="LIMIT",
                    price=price,
                )

                results.append(result)
                time.sleep(1)

            except Exception as e:
                log.error(f"VWAP slice {i} failed: {e}")

        return {"status": "success", "slices": len(results)}

    def execute_market(self, symbol: str, action: str, quantity: int) -> dict:
        """Simple market order execution"""
        try:
            result = self.broker.place_order(
                symbol=symbol,
                exchange="NSE",
                token="",
                transaction_type=action,
                quantity=quantity,
                order_type="MARKET",
            )
            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def check_slippage(self, expected_price: float, executed_price: float) -> bool:
        """Check if slippage is within acceptable range"""
        slippage = abs(executed_price - expected_price) / expected_price

        return slippage <= self.max_slippage


execution_router = ExecutionRouter(None)


def execute_trade(symbol: str, decision: dict, capital: float, broker=None) -> dict:
    """Execute trade"""
    if broker:
        execution_router.broker = broker
    return execution_router.execute_trade(symbol, decision, capital)
