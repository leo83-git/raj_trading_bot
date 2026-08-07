"""Fail-open P7 domain-event and operational metric tracking.

The module retains the legacy :class:`Tracker` API while adding a typed,
bounded observability path.  Every public telemetry operation contains sink
failures: observing the bot must never change a trading outcome.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from time import monotonic
from typing import Any

from quant_utils.logger import correlation_scope, get_logger, log_domain_event

log = get_logger("analytics.tracker")


class EventType(str, Enum):
    """Stable event vocabulary spanning one trade's complete lifecycle."""

    SCHEDULER_CYCLE = "scheduler.cycle"
    DATA_FETCH = "data.fetch"
    DATA_QUALITY = "data.quality"
    SCREENING = "screening.result"
    SIGNAL = "signal.generated"
    RISK = "risk.decision"
    OPTIONS = "options.selected"
    EXECUTION = "execution.update"
    POSITION_OPENED = "position.opened"
    PNL = "pnl.updated"
    DRAWDOWN = "drawdown.updated"
    GREEKS = "greeks.updated"
    POSITION_EXITED = "position.exited"
    RECOVERY = "recovery.completed"
    HEALTH = "health.check"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Serializable event carrying a correlation ID across subsystem boundaries."""

    event_type: EventType
    correlation_id: str
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["event_type"] = self.event_type.value
        value["occurred_at"] = self.occurred_at.isoformat()
        value["payload"] = dict(self.payload)
        return value


def new_correlation_id(prefix: str = "trade") -> str:
    """Create a compact correlation ID suitable for logs and broker metadata."""

    return f"{prefix}-{uuid.uuid4().hex[:20]}"


class ObservabilityTracker:
    """Thread-safe bounded event tracker with non-fatal, best-effort sinks."""

    def __init__(
        self,
        *,
        max_events: int = 2_000,
        sink: Callable[[DomainEvent], None] | None = None,
    ) -> None:
        self._events: deque[DomainEvent] = deque(maxlen=max(1, max_events))
        self._metrics: Counter[str] = Counter()
        self._gauges: dict[str, float] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._sink = sink
        self._lock = threading.RLock()
        self._started = monotonic()
        self.telemetry_errors = 0

    @contextmanager
    def trace(self, correlation_id: str | None = None) -> Iterator[str]:
        correlation_id = correlation_id or new_correlation_id()
        with correlation_scope(correlation_id):
            yield correlation_id

    def emit(
        self,
        event_type: EventType,
        *,
        source: str,
        correlation_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(event_type, correlation_id, source, payload or {})
        try:
            with self._lock:
                self._events.append(event)
                self._metrics[f"events.{event_type.value}"] += 1
                self._capture_domain_metrics(event_type, event.payload)
            log_domain_event(log, event.to_dict())
            if self._sink is not None:
                try:
                    self._sink(event)
                except Exception:  # Third-party sinks are intentionally isolated.
                    with self._lock:
                        self.telemetry_errors += 1
                    log.debug("P7 event sink failed", exc_info=True)
        except Exception:  # Final intentional fail-open telemetry boundary.
            # Defensive final boundary: callers must never handle telemetry errors.
            self.telemetry_errors += 1
            log.debug("P7 event recording failed", exc_info=True)
        return event

    def _capture_domain_metrics(
        self, event_type: EventType, payload: Mapping[str, Any]
    ) -> None:
        """Extract standard counters/gauges while preserving arbitrary payloads."""
        counter_fields = {
            EventType.DATA_QUALITY: ("failures", "data_quality.failures"),
            EventType.SCREENING: ("candidate_count", "screening.candidates"),
            EventType.SIGNAL: ("signal_count", "signals.generated"),
            EventType.RISK: ("rejected", "risk.rejections"),
            EventType.RECOVERY: ("recovered_count", "recovery.items"),
        }
        if event_type in counter_fields:
            field_name, metric_name = counter_fields[event_type]
            value = payload.get(field_name, 0)
            try:
                self._metrics[metric_name] += float(value)
            except (TypeError, ValueError):
                pass
        gauge_fields = {
            EventType.PNL: (
                ("realized", "pnl.realized"),
                ("unrealized", "pnl.unrealized"),
            ),
            EventType.DRAWDOWN: (("percent", "drawdown.percent"),),
            EventType.EXECUTION: (("latency_ms", "execution.last_latency_ms"),),
            EventType.GREEKS: tuple(
                (name, f"greeks.{name}") for name in ("delta", "gamma", "theta", "vega")
            ),
        }
        for field_name, metric_name in gauge_fields.get(event_type, ()):
            try:
                self._gauges[metric_name] = float(payload[field_name])
            except (KeyError, TypeError, ValueError):
                continue

    def increment(self, metric: str, value: float = 1.0) -> None:
        try:
            with self._lock:
                self._metrics[metric] += value
        except Exception:  # Metrics must never affect trading.
            self.telemetry_errors += 1
            log.debug("P7 counter update failed", exc_info=True)

    def gauge(self, metric: str, value: float) -> None:
        try:
            with self._lock:
                self._gauges[metric] = float(value)
        except Exception:  # Metrics must never affect trading.
            self.telemetry_errors += 1
            log.debug("P7 gauge update failed", exc_info=True)

    def record_health(self, component: str, healthy: bool, *, detail: str = "") -> None:
        try:
            with self._lock:
                self._health[component] = {
                    "healthy": bool(healthy),
                    "detail": detail[:256],
                    "checked_at": dt.datetime.now(dt.UTC).isoformat(),
                }
            self.emit(
                EventType.HEALTH,
                source=component,
                correlation_id=new_correlation_id("health"),
                payload={"healthy": healthy, "detail": detail[:256]},
            )
        except Exception:  # Health telemetry is intentionally advisory.
            self.telemetry_errors += 1
            log.debug("P7 health recording failed", exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        try:
            with self._lock:
                return {
                    "uptime_seconds": round(monotonic() - self._started, 3),
                    "metrics": dict(self._metrics),
                    "gauges": dict(self._gauges),
                    "health": dict(self._health),
                    "telemetry_errors": self.telemetry_errors,
                    "events_retained": len(self._events),
                }
        except Exception:  # Reporting intentionally remains fail-open.
            self.telemetry_errors += 1
            log.debug("P7 snapshot generation failed", exc_info=True)
            return {"telemetry_errors": self.telemetry_errors}

    def recent_events(self, limit: int = 100) -> list[DomainEvent]:
        if limit <= 0:
            return []
        try:
            with self._lock:
                return list(self._events)[-limit:]
        except Exception:  # Reporting intentionally remains fail-open.
            self.telemetry_errors += 1
            log.debug("P7 recent-event read failed", exc_info=True)
            return []

    def compact_report(self) -> str:
        snap = self.snapshot()
        metrics = snap.get("metrics", {})
        gauges = snap.get("gauges", {})
        unhealthy = sorted(
            name
            for name, state in snap.get("health", {}).items()
            if not state.get("healthy", False)
        )
        return (
            f"P7 daily | cycles={metrics.get('events.scheduler.cycle', 0)} "
            f"signals={metrics.get('events.signal.generated', 0)} "
            f"orders={metrics.get('events.execution.update', 0)} "
            f"exits={metrics.get('events.position.exited', 0)} "
            f"pnl={gauges.get('pnl.realized', 0.0):.2f} "
            f"drawdown={gauges.get('drawdown.percent', 0.0):.2f}% "
            f"unhealthy={','.join(unhealthy) if unhealthy else 'none'}"
        )


class Tracker:
    """Backward-compatible PnL tracker."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.closed_trades: list[dict[str, Any]] = []
        self.pnl_by_strategy: dict[str, float] = {}
        self.pnl_by_symbol: dict[str, float] = {}
        self.daily_pnl = 0.0
        self.total_pnl = 0.0

    def record_open(
        self, symbol: str, strategy: str, entry: float, quantity: int, direction: str
    ):
        self.positions[symbol] = {
            "strategy": strategy,
            "entry": entry,
            "quantity": quantity,
            "direction": direction,
            "entry_time": dt.datetime.now(dt.UTC),
        }
        log.info("Position opened: %s | %s | Entry: %s", symbol, strategy, entry)

    def record_close(self, symbol: str, exit_price: float, reason: str = "MANUAL"):
        if symbol not in self.positions:
            log.warning("Cannot close - position not found: %s", symbol)
            return None
        pos = self.positions[symbol]
        pnl = (
            (exit_price - pos["entry"])
            if pos["direction"] == "BUY"
            else (pos["entry"] - exit_price)
        ) * pos["quantity"]
        trade = {
            "symbol": symbol,
            "strategy": pos["strategy"],
            "entry": pos["entry"],
            "exit": exit_price,
            "quantity": pos["quantity"],
            "pnl": pnl,
            "reason": reason,
            "entry_time": pos["entry_time"],
            "exit_time": dt.datetime.now(dt.UTC),
        }
        self.closed_trades.append(trade)
        self.pnl_by_strategy[pos["strategy"]] = (
            self.pnl_by_strategy.get(pos["strategy"], 0) + pnl
        )
        self.pnl_by_symbol[symbol] = self.pnl_by_symbol.get(symbol, 0) + pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl
        del self.positions[symbol]
        log.info("Position closed: %s | PnL: %.2f | Reason: %s", symbol, pnl, reason)
        return pnl

    def get_current_pnl(self, current_prices: dict[str, float]) -> float:
        return sum(
            (
                (
                    (current_prices.get(s, p["entry"]) - p["entry"])
                    if p["direction"] == "BUY"
                    else (p["entry"] - current_prices.get(s, p["entry"]))
                )
                * p["quantity"]
            )
            for s, p in self.positions.items()
        )

    def get_summary(self) -> dict[str, Any]:
        total = len(self.closed_trades)
        wins = sum(1 for trade in self.closed_trades if trade["pnl"] > 0)
        losses = total - wins
        avg_win = sum(t["pnl"] for t in self.closed_trades if t["pnl"] > 0) / max(
            1, wins
        )
        avg_loss = sum(t["pnl"] for t in self.closed_trades if t["pnl"] < 0) / max(
            1, losses
        )
        return {
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "open_positions": len(self.positions),
            "closed_trades": total,
            "win_rate": wins / total if total else 0,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss else 0,
            "pnl_by_strategy": self.pnl_by_strategy,
            "pnl_by_symbol": self.pnl_by_symbol,
        }

    def get_recent_trades(self, count: int = 10) -> list[dict]:
        return self.closed_trades[-count:]

    def save_to_file(self, filename: str = "analytics/trades.json"):
        try:
            with open(filename, "w") as handle:
                json.dump(
                    {
                        "closed_trades": self.closed_trades,
                        "summary": self.get_summary(),
                    },
                    handle,
                    indent=2,
                    default=str,
                )
        except (OSError, TypeError, ValueError) as exc:
            log.error("Failed to save trades: %s", exc)

    def reset_daily(self):
        self.daily_pnl = 0.0

    def get_tracker(self):
        return self


_tracker_instance: Tracker | None = None


def get_tracker() -> Tracker:
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = Tracker()
    return _tracker_instance


def record_trade(symbol: str, strategy: str, pnl: float):
    tracker = get_tracker()
    tracker.pnl_by_strategy[strategy] = tracker.pnl_by_strategy.get(strategy, 0) + pnl
    tracker.total_pnl += pnl
    tracker.daily_pnl += pnl
