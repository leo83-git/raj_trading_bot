"""Paper-only atomic coordinator with reconciliation and compensation."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any, Protocol

from execution.journal import ExecutionJournal
from execution.models import (
    ExecutionState,
    LegExecution,
    LegState,
    StrategyExecution,
    deterministic_key,
    strategy_fingerprint,
)

log = logging.getLogger("multileg_coordinator")


class ExecutionAdapter(Protocol):
    def submit(self, leg: LegExecution, *, compensation: bool = False) -> Any: ...
    def reconcile(self, leg: LegExecution, *, compensation: bool = False) -> Any: ...
    def cancel(self, order_id: str) -> Any: ...


class OrderManagerAdapter:
    """Composition adapter preserving the public OrderManager API."""

    def __init__(self, manager: Any):
        self.manager = manager

    @staticmethod
    def _key(leg: LegExecution, compensation: bool) -> str:
        return (
            deterministic_key("comp-" + leg.idempotency_key, leg.index, "flatten")
            if compensation
            else leg.idempotency_key
        )

    def submit(self, leg: LegExecution, *, compensation: bool = False) -> Any:
        action = (
            {"BUY": "SELL", "SELL": "BUY"}[leg.action] if compensation else leg.action
        )
        key = self._key(leg, compensation)
        return self.manager.place_order(
            symbol=leg.symbol,
            quantity=leg.filled_quantity if compensation else leg.quantity,
            action=action,
            order_type="MARKET",
            price=leg.average_price or leg.expected_price,
            metadata={
                "idempotency_key": key,
                "p6_atomic": True,
                "is_exit": compensation,
            },
        )

    def reconcile(self, leg: LegExecution, *, compensation: bool = False) -> Any:
        if leg.order_id and not compensation:
            result = self.manager.get_order_status(leg.order_id)
            if result:
                return result
        return self.manager.find_order_by_idempotency_key(self._key(leg, compensation))

    def cancel(self, order_id: str) -> Any:
        return self.manager.cancel_order(order_id)


class MultiLegCoordinator:
    """Execute all legs or flatten every observed fill; PAPER mode only."""

    def __init__(
        self,
        adapter: ExecutionAdapter,
        journal: ExecutionJournal,
        mode: str = "PAPER",
        max_retries: int = 1,
    ):
        if mode.upper() != "PAPER":
            raise RuntimeError("P6 atomic execution is disabled outside PAPER mode")
        self.adapter = adapter
        self.journal = journal
        self.max_retries = max(0, max_retries)

    def execute(
        self,
        underlying: str,
        strategy_name: str,
        legs: list[dict[str, Any]],
        *,
        expected_net: str = "ANY",
        max_net_amount: float | None = None,
        client_ref: str = "",
    ) -> StrategyExecution:
        normalized_expected_net = expected_net.upper()
        normalized_max_net_amount = (
            None if max_net_amount is None else float(max_net_amount)
        )
        for leg in legs:
            original_action = leg.get("action", "BUY")
            normalized_action = str(original_action).upper()
            if normalized_action not in {"BUY", "SELL"}:
                raise ValueError(f"Unsupported leg action: {original_action!r}")
            leg["action"] = normalized_action
        strategy_id = strategy_fingerprint(
            underlying,
            strategy_name,
            legs,
            client_ref,
            normalized_expected_net,
            normalized_max_net_amount,
        )
        execution = StrategyExecution(
            strategy_id=strategy_id,
            strategy_name=strategy_name,
            underlying=underlying,
            expected_net=normalized_expected_net,
            max_net_amount=normalized_max_net_amount,
            state=ExecutionState.CREATED,
            legs=[
                LegExecution(
                    index=index,
                    symbol=str(leg["symbol"]),
                    action=str(leg.get("action", "BUY")).upper(),
                    quantity=int(leg["quantity"]),
                    expected_price=float(leg["price"]),
                    idempotency_key=deterministic_key(strategy_id, index),
                )
                for index, leg in enumerate(legs)
            ],
        )
        owned, execution, created = self.journal.claim(execution)
        if not owned:
            return execution
        try:
            if not created:
                return self._recover(execution)
            if not legs or not self._net_valid(
                self._net(execution, expected=True), execution
            ):
                execution.state = ExecutionState.FAILED
                execution.failure_reason = "projected_net_validation_failed"
                self._save(execution)
                return execution
            execution.state = ExecutionState.VALIDATED
            self._save(execution)
            return self._submit_remaining(execution)
        finally:
            self.journal.release(strategy_id)

    def recover_incomplete(self) -> list[StrategyExecution]:
        """Reconcile journaled executions after restart before any resubmission."""
        recovered = []
        for item in self.journal.incomplete():
            owned, observed, _ = self.journal.claim(item)
            if not owned:
                recovered.append(observed)
                continue
            try:
                recovered.append(self._recover(observed))
            finally:
                self.journal.release(item.strategy_id)
        return recovered

    def _recover(self, execution: StrategyExecution) -> StrategyExecution:
        execution.state = ExecutionState.RECONCILING
        self._save(execution)
        for leg in execution.legs:
            if leg.state not in {LegState.FILLED, LegState.COMPENSATED}:
                self._apply_result(leg, self._safe_reconcile(execution, leg))
        self._save(execution)
        if all(leg.state is LegState.FILLED for leg in execution.legs):
            return self._complete_or_flatten(execution)
        if any(leg.filled_quantity for leg in execution.legs):
            return self._compensate(execution, "restart_incomplete")
        return self._submit_remaining(execution)

    def _submit_remaining(self, execution: StrategyExecution) -> StrategyExecution:
        execution.state = ExecutionState.SUBMITTING
        self._save(execution)
        for leg in execution.legs:
            if leg.state is LegState.FILLED:
                continue
            result = self._safe_submit(execution, leg)
            self._apply_result(leg, result)
            self._save(execution)
            attempts = 0
            while leg.state is LegState.AMBIGUOUS and attempts <= self.max_retries:
                execution.state = ExecutionState.RECONCILING
                self._save(execution)
                self._apply_result(leg, self._safe_reconcile(execution, leg))
                self._save(execution)
                if leg.state is not LegState.AMBIGUOUS:
                    break
                attempts += 1
                if attempts <= self.max_retries:
                    execution.state = ExecutionState.SUBMITTING
                    self._save(execution)
                    self._apply_result(leg, self._safe_submit(execution, leg))
                    self._save(execution)
            if leg.state is not LegState.FILLED:
                return self._compensate(execution, f"leg_{leg.index}_{leg.state.value}")
        return self._complete_or_flatten(execution)

    def _complete_or_flatten(self, execution: StrategyExecution) -> StrategyExecution:
        if any(
            leg.state is LegState.FILLED
            and leg.filled_quantity > 0
            and leg.average_price <= 0
            for leg in execution.legs
        ):
            return self._compensate(execution, "actual_net_validation_failed")
        execution.actual_net_amount = self._net(execution)
        if not self._net_valid(execution.actual_net_amount, execution):
            return self._compensate(execution, "actual_net_validation_failed")
        execution.state = ExecutionState.COMPLETED
        self._save(execution)
        return execution

    def _compensate(
        self, execution: StrategyExecution, reason: str
    ) -> StrategyExecution:
        execution.failure_reason = reason
        execution.state = ExecutionState.CANCELLING
        self._save(execution)
        for leg in execution.legs:
            if leg.order_id and leg.state in {
                LegState.SUBMITTED,
                LegState.PARTIAL,
                LegState.AMBIGUOUS,
            }:
                self._safe_cancel(execution, leg)
                reconciled = self._safe_reconcile(execution, leg)
                self._apply_result(leg, reconciled)
        execution.state = ExecutionState.COMPENSATING
        self._save(execution)
        # An unresolved ambiguous leg may have filled at the broker. Even when
        # its observed quantity is zero, declaring the strategy flat would be
        # unsafe; retain HALTED until a later recovery can prove its state.
        failed = any(leg.state is LegState.AMBIGUOUS for leg in execution.legs)
        for leg in reversed(execution.legs):
            if leg.filled_quantity <= 0 or leg.state is LegState.COMPENSATED:
                continue
            result = self._safe_submit(execution, leg, compensation=True)
            status, qty, _, _ = self._normalize(result)
            if status != "filled" or qty < leg.filled_quantity:
                reconciled = self._safe_reconcile(execution, leg, compensation=True)
                status, qty, _, _ = self._normalize(reconciled)
            if status == "filled" and qty >= leg.filled_quantity:
                leg.state = LegState.COMPENSATED
            else:
                failed = True
            self._save(execution)
        execution.state = ExecutionState.HALTED if failed else ExecutionState.FLATTENED
        self._save(execution)
        return execution

    def _apply_result(self, leg: LegExecution, result: Any) -> None:
        status, quantity, price, order_id = self._normalize(result)
        leg.raw = result if isinstance(result, dict) else {"value": result}
        leg.order_id = order_id or leg.order_id
        leg.filled_quantity = max(leg.filled_quantity, quantity)
        if status == "filled" and leg.filled_quantity == 0:
            leg.filled_quantity = leg.quantity
        leg.average_price = price or leg.average_price
        leg.state = {
            "filled": LegState.FILLED,
            "partial": LegState.PARTIAL,
            "cancelled": LegState.CANCELLED,
            "failed": LegState.FAILED,
        }.get(status, LegState.AMBIGUOUS)

    def _safe_submit(
        self,
        execution: StrategyExecution,
        leg: LegExecution,
        *,
        compensation: bool = False,
    ) -> Any:
        try:
            return self.adapter.submit(leg, compensation=compensation)
        except Exception:  # Broker boundary must fail safely and durably.
            log.exception(
                "p6_submit_exception execution_id=%s leg=%s order_id=%s compensation=%s",
                execution.strategy_id,
                leg.index,
                leg.order_id,
                compensation,
            )
            return None

    def _safe_reconcile(
        self,
        execution: StrategyExecution,
        leg: LegExecution,
        *,
        compensation: bool = False,
    ) -> Any:
        try:
            return self.adapter.reconcile(leg, compensation=compensation)
        except Exception:  # Reconciliation must not skip compensation.
            log.exception(
                "p6_reconcile_exception execution_id=%s leg=%s order_id=%s compensation=%s",
                execution.strategy_id,
                leg.index,
                leg.order_id,
                compensation,
            )
            return None

    def _safe_cancel(self, execution: StrategyExecution, leg: LegExecution) -> Any:
        try:
            return self.adapter.cancel(str(leg.order_id))
        except Exception:  # Cancellation failure proceeds to reconciliation.
            log.exception(
                "p6_cancel_exception execution_id=%s leg=%s order_id=%s",
                execution.strategy_id,
                leg.index,
                leg.order_id,
            )
            return None

    @staticmethod
    def _normalize(result: Any) -> tuple[str, int, float, str | None]:
        if not isinstance(result, dict):
            return "ambiguous", 0, 0.0, None
        raw_status = str(
            result.get("status", result.get("order_status", "unknown"))
        ).lower()
        mapping = {
            "success": "filled",
            "complete": "filled",
            "completed": "filled",
            "rejected": "failed",
            "error": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "open": "ambiguous",
            "pending": "ambiguous",
        }
        status = mapping.get(raw_status, raw_status)
        raw_quantity = (
            result.get("filled_quantity", result.get("quantity", result.get("qty", 0)))
            or 0
        )
        raw_price = result.get("average_price", result.get("price", 0)) or 0
        try:
            numeric_quantity = float(raw_quantity)
            price = float(raw_price)
        except (TypeError, ValueError):
            return "ambiguous", 0, 0.0, None
        if (
            not numeric_quantity.is_integer()
            or numeric_quantity < 0
            or not math.isfinite(numeric_quantity)
            or not math.isfinite(price)
        ):
            return "ambiguous", 0, 0.0, None
        quantity = int(numeric_quantity)
        order_id = result.get("order_id", result.get("id"))
        if status == "filled" and quantity == 0:
            raw_requested = result.get("requested_quantity", 0) or 0
            try:
                numeric_requested = float(raw_requested)
            except (TypeError, ValueError):
                return "ambiguous", 0, 0.0, None
            if not numeric_requested.is_integer() or numeric_requested < 0:
                return "ambiguous", 0, 0.0, None
            quantity = int(numeric_requested)
        return status, quantity, price, str(order_id) if order_id else None

    @staticmethod
    def _net(execution: StrategyExecution, expected: bool = False) -> float:
        return sum(
            (1 if leg.action == "BUY" else -1)
            * (leg.expected_price if expected else leg.average_price)
            * (leg.quantity if expected else leg.filled_quantity)
            for leg in execution.legs
        )

    @staticmethod
    def _net_valid(net: float, execution: StrategyExecution) -> bool:
        expected = execution.expected_net
        if expected == "DEBIT" and net < 0:
            return False
        if expected == "CREDIT" and net > 0:
            return False
        return execution.max_net_amount is None or abs(net) <= execution.max_net_amount

    def _save(self, execution: StrategyExecution) -> None:
        execution.updated_at = datetime.now(UTC).isoformat()
        self.journal.save(execution)
        log.info(
            "p6_execution_state strategy_id=%s state=%s",
            execution.strategy_id,
            execution.state.value,
        )
