"""Typed, fail-closed validation for single-order trade entry intents."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

log = logging.getLogger("trade_validator")


@dataclass(frozen=True)
class PortfolioGreeks:
    """Projected absolute portfolio Greeks after accepting an entry."""

    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0


@dataclass(frozen=True)
class TradeIntent:
    """Complete context required to decide whether one order may be submitted."""

    symbol: str
    action: str
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    quote_timestamp: datetime
    session_open: bool
    is_exit: bool = False
    order_type: str = "MARKET"
    limit_price: float | None = None
    lot_size: int = 1
    tick_size: float = 0.05
    bid_price: float = 0.0
    ask_price: float = 0.0
    available_quantity: int = 0
    expected_slippage_pct: float = 0.0
    required_margin: float = 0.0
    available_margin: float = 0.0
    projected_exposure_pct: float = 0.0
    implied_volatility: float = 0.0
    drawdown_pct: float = 0.0
    max_correlation: float = 0.0
    strategy_max_loss: float = 0.0
    portfolio_greeks: PortfolioGreeks = field(default_factory=PortfolioGreeks)
    circuit_breaker_active: bool = False
    kill_switch_active: bool = False


@dataclass(frozen=True)
class ValidationResult:
    """Structured result returned by :class:`TradeValidator`."""

    is_valid: bool
    status: Literal["approved", "adjusted", "rejected", "halted"]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    checks: Mapping[str, bool] = field(default_factory=dict)


class TradeValidator:
    """Validate a single entry intent against execution and portfolio limits."""

    DEFAULTS: Mapping[str, Any] = {
        "max_data_age_seconds": 30.0,
        "max_spread_pct": 0.02,
        "max_slippage_pct": 0.01,
        "max_exposure": 0.30,
        "min_iv": 0.0,
        "max_iv": 200.0,
        "max_drawdown": 0.20,
        "correlation_threshold": 0.70,
        "max_strategy_loss": 100_000.0,
        "max_abs_delta": 10_000.0,
        "max_abs_gamma": 10_000.0,
        "max_abs_theta": 100_000.0,
        "max_abs_vega": 100_000.0,
    }

    def __init__(self, config: Mapping[str, Any] | None = None):
        root = dict(config or {})
        risk = root.get("risk", root)
        validation = root.get("trade_validation", {})
        self.config = {**self.DEFAULTS, **risk, **validation}

    def validate(
        self, intent: TradeIntent, *, now: datetime | None = None
    ) -> ValidationResult:
        """Return all failed checks; exits are always allowed for risk reduction."""
        if intent.is_exit:
            return ValidationResult(
                True, status="approved", checks={"exit_allowed": True}
            )

        current = now or datetime.now(UTC)
        quote_time = intent.quote_timestamp
        if quote_time.tzinfo is None:
            quote_time = quote_time.replace(tzinfo=UTC)
        age = (current.astimezone(UTC) - quote_time.astimezone(UTC)).total_seconds()
        side = intent.action.upper()
        finite_prices = all(
            math.isfinite(value) and value > 0
            for value in (intent.entry_price, intent.stop_loss, intent.target)
        )
        direction_ok = finite_prices and (
            (side == "BUY" and intent.stop_loss < intent.entry_price < intent.target)
            or (
                side == "SELL" and intent.target < intent.entry_price < intent.stop_loss
            )
        )
        price_for_tick = (
            intent.limit_price if intent.order_type.upper() == "LIMIT" else None
        )
        tick_ok = intent.tick_size > 0 and (
            price_for_tick is None
            or abs(
                (price_for_tick / intent.tick_size)
                - round(price_for_tick / intent.tick_size)
            )
            < 1e-7
        )
        quantity_ok = (
            intent.quantity > 0
            and intent.lot_size > 0
            and intent.quantity % intent.lot_size == 0
        )
        spread_pct = (
            (intent.ask_price - intent.bid_price)
            / ((intent.ask_price + intent.bid_price) / 2)
            if intent.bid_price > 0 and intent.ask_price >= intent.bid_price
            else math.inf
        )
        greeks = intent.portfolio_greeks
        checks = {
            "data_freshness": 0 <= age <= float(self.config["max_data_age_seconds"]),
            "session": intent.session_open,
            "prices": finite_prices and side in {"BUY", "SELL"},
            "stop_target_direction": direction_ok,
            "quantity_and_lot": quantity_ok,
            "tick_size": tick_ok,
            "liquidity": intent.available_quantity >= intent.quantity,
            "spread": spread_pct <= float(self.config["max_spread_pct"]),
            "slippage": 0
            <= intent.expected_slippage_pct
            <= float(self.config["max_slippage_pct"]),
            "margin": intent.required_margin >= 0
            and intent.available_margin >= intent.required_margin,
            "exposure": 0
            <= intent.projected_exposure_pct
            <= float(self.config["max_exposure"]),
            "implied_volatility": float(self.config["min_iv"])
            <= intent.implied_volatility
            <= float(self.config["max_iv"]),
            "drawdown": 0 <= intent.drawdown_pct <= float(self.config["max_drawdown"]),
            "correlation": abs(intent.max_correlation)
            <= float(self.config["correlation_threshold"]),
            "strategy_max_loss": 0
            <= intent.strategy_max_loss
            <= float(self.config["max_strategy_loss"]),
            "portfolio_delta": abs(greeks.delta) <= float(self.config["max_abs_delta"]),
            "portfolio_gamma": abs(greeks.gamma) <= float(self.config["max_abs_gamma"]),
            "portfolio_theta": abs(greeks.theta) <= float(self.config["max_abs_theta"]),
            "portfolio_vega": abs(greeks.vega) <= float(self.config["max_abs_vega"]),
            "circuit_breaker": not intent.circuit_breaker_active,
            "kill_switch": not intent.kill_switch_active,
        }
        errors = tuple(name for name, passed in checks.items() if not passed)
        halted = not checks["circuit_breaker"] or not checks["kill_switch"]
        result = ValidationResult(
            not errors,
            status="approved" if not errors else "halted" if halted else "rejected",
            errors=errors,
            checks=checks,
        )
        if errors:
            log.warning(
                "trade_entry_rejected symbol=%s action=%s checks=%s",
                intent.symbol,
                side,
                errors,
            )
        else:
            log.info(
                "trade_entry_validated symbol=%s action=%s quantity=%s",
                intent.symbol,
                side,
                intent.quantity,
            )
        return result
