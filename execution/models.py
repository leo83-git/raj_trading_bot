"""Typed models shared by the paper-only atomic execution workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ExecutionState(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    SUBMITTING = "submitting"
    RECONCILING = "reconciling"
    CANCELLING = "cancelling"
    COMPENSATING = "compensating"
    COMPLETED = "completed"
    FLATTENED = "flattened"
    FAILED = "failed"
    HALTED = "halted"


TERMINAL_STATES = {
    ExecutionState.COMPLETED,
    ExecutionState.FLATTENED,
    ExecutionState.FAILED,
    ExecutionState.HALTED,
}


class LegState(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
    COMPENSATED = "compensated"


def deterministic_key(strategy_id: str, leg_index: int, purpose: str = "open") -> str:
    """Return a stable, broker-safe idempotency key for a strategy leg."""
    raw = f"p6|{strategy_id}|{leg_index}|{purpose}".encode()
    return f"p6-{hashlib.sha256(raw).hexdigest()[:32]}"


@dataclass
class LegExecution:
    index: int
    symbol: str
    action: str
    quantity: int
    expected_price: float
    idempotency_key: str
    state: LegState = LegState.PENDING
    order_id: str | None = None
    filled_quantity: int = 0
    average_price: float = 0.0
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LegExecution:
        value = dict(value)
        value["state"] = LegState(value["state"])
        return cls(**value)


@dataclass
class StrategyExecution:
    strategy_id: str
    strategy_name: str
    underlying: str
    expected_net: str
    max_net_amount: float | None
    state: ExecutionState
    legs: list[LegExecution]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    actual_net_amount: float = 0.0
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        for leg in result["legs"]:
            leg["state"] = leg["state"].value
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StrategyExecution:
        value = dict(value)
        value["state"] = ExecutionState(value["state"])
        value["legs"] = [LegExecution.from_dict(leg) for leg in value["legs"]]
        return cls(**value)


def strategy_fingerprint(
    underlying: str,
    strategy: str,
    legs: list[dict[str, Any]],
    client_ref: str = "",
    expected_net: str = "ANY",
    max_net_amount: float | None = None,
) -> str:
    """Build a deterministic strategy identity from canonical business inputs."""
    material = {
        "underlying": underlying,
        "strategy": strategy,
        "client_ref": client_ref,
        "expected_net": expected_net.upper(),
        "max_net_amount": max_net_amount,
        "legs": [
            {
                "symbol": leg.get("symbol", ""),
                "action": str(leg.get("action", "BUY")).upper(),
                "quantity": int(leg.get("quantity", 0) or 0),
                "price": float(leg.get("price", 0) or 0),
                "strike": float(leg.get("strike", 0) or 0),
                "opt_type": leg.get("opt_type", leg.get("option_type", "")),
            }
            for leg in legs
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class ExitReason(str, Enum):
    EMERGENCY_HALT = "emergency_halt"
    SQUARE_OFF = "square_off"
    EXPIRY = "expiry"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    PROFIT_LADDER = "profit_ladder"
    TARGET = "target"
    HOLD = "hold"


# Safety/time constraints precede price-derived decisions. Within price-derived
# decisions, loss containment precedes profit taking.
EXIT_PRECEDENCE = (
    ExitReason.EMERGENCY_HALT,
    ExitReason.SQUARE_OFF,
    ExitReason.EXPIRY,
    ExitReason.STOP_LOSS,
    ExitReason.TRAILING_STOP,
    ExitReason.PROFIT_LADDER,
    ExitReason.TARGET,
    ExitReason.HOLD,
)


@dataclass(frozen=True)
class ExitPolicyInput:
    action: str
    current_price: float
    stop_loss: float = 0.0
    target: float = 0.0
    trailing_stop: float = 0.0
    profit_ladder_price: float = 0.0
    emergency_halt: bool = False
    square_off_due: bool = False
    expired: bool = False


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    should_exit: bool


def evaluate_exit_policy(value: ExitPolicyInput) -> ExitDecision:
    """Pure exit decision; callers are responsible for market-data lookup."""
    buy = value.action.upper() == "BUY"
    checks = {
        ExitReason.EMERGENCY_HALT: value.emergency_halt,
        ExitReason.SQUARE_OFF: value.square_off_due,
        ExitReason.EXPIRY: value.expired,
        ExitReason.STOP_LOSS: value.stop_loss > 0
        and (
            value.current_price <= value.stop_loss
            if buy
            else value.current_price >= value.stop_loss
        ),
        ExitReason.TRAILING_STOP: value.trailing_stop > 0
        and (
            value.current_price <= value.trailing_stop
            if buy
            else value.current_price >= value.trailing_stop
        ),
        ExitReason.PROFIT_LADDER: value.profit_ladder_price > 0
        and (
            value.current_price >= value.profit_ladder_price
            if buy
            else value.current_price <= value.profit_ladder_price
        ),
        ExitReason.TARGET: value.target > 0
        and (
            value.current_price >= value.target
            if buy
            else value.current_price <= value.target
        ),
        ExitReason.HOLD: True,
    }
    reason = next(item for item in EXIT_PRECEDENCE if checks[item])
    return ExitDecision(reason=reason, should_exit=reason is not ExitReason.HOLD)
