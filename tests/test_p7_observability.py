"""P7 unit, integration, failure, restart, regression, and soak-style tests."""

from __future__ import annotations

import logging

from alerts.alert_manager import AlertManager, AlertSeverity
from analytics.performance import DailyOperationalMetrics, compact_daily_report
from analytics.tracker import EventType, ObservabilityTracker, new_correlation_id
from core.database import DatabaseManager, RuntimeCheckpoint
from quant_utils.logger import LinuxStructuredFormatter, correlation_scope


def test_typed_events_cover_fetch_to_exit_with_one_correlation_id():
    tracker = ObservabilityTracker(max_events=32)
    correlation_id = new_correlation_id("trade")
    path = [
        EventType.SCHEDULER_CYCLE,
        EventType.DATA_FETCH,
        EventType.DATA_QUALITY,
        EventType.SCREENING,
        EventType.SIGNAL,
        EventType.RISK,
        EventType.OPTIONS,
        EventType.EXECUTION,
        EventType.POSITION_OPENED,
        EventType.PNL,
        EventType.DRAWDOWN,
        EventType.GREEKS,
        EventType.POSITION_EXITED,
    ]

    payloads = {
        EventType.DATA_QUALITY: {"failures": 1},
        EventType.SCREENING: {"candidate_count": 12},
        EventType.SIGNAL: {"signal_count": 2},
        EventType.RISK: {"rejected": 1},
        EventType.EXECUTION: {"latency_ms": 14.5},
        EventType.PNL: {"realized": 120.0, "unrealized": 15.0},
        EventType.DRAWDOWN: {"percent": 1.2},
        EventType.GREEKS: {"delta": 0.3, "gamma": 0.1, "theta": -2, "vega": 4},
    }
    for event_type in path:
        tracker.emit(
            event_type,
            source="integration",
            correlation_id=correlation_id,
            payload={"symbol": "NIFTY", **payloads.get(event_type, {})},
        )

    events = tracker.recent_events()
    assert [event.event_type for event in events] == path
    assert {event.correlation_id for event in events} == {correlation_id}
    assert tracker.snapshot()["metrics"]["events.position.exited"] == 1
    assert tracker.snapshot()["gauges"]["pnl.realized"] == 120.0
    assert tracker.snapshot()["gauges"]["greeks.delta"] == 0.3


def test_broken_telemetry_sink_never_raises_or_loses_local_metrics():
    def broken_sink(_event):
        raise RuntimeError("collector unavailable")

    tracker = ObservabilityTracker(sink=broken_sink)
    event = tracker.emit(
        EventType.EXECUTION,
        source="execution",
        correlation_id="trade-safe",
    )

    assert event.correlation_id == "trade-safe"
    assert tracker.snapshot()["telemetry_errors"] == 1
    assert len(tracker.recent_events()) == 1


def test_long_run_event_buffer_is_bounded():
    tracker = ObservabilityTracker(max_events=25)
    for index in range(10_000):
        tracker.emit(
            EventType.SCHEDULER_CYCLE,
            source="soak",
            correlation_id=f"cycle-{index}",
        )
    assert len(tracker.recent_events(10_000)) == 25
    assert tracker.snapshot()["metrics"]["events.scheduler.cycle"] == 10_000


def test_recent_event_non_positive_limit_and_execution_latency_gauge():
    tracker = ObservabilityTracker()
    for latency in (12.5, 7.0):
        tracker.emit(
            EventType.EXECUTION,
            source="execution",
            correlation_id="trade-latency",
            payload={"latency_ms": latency},
        )
    assert tracker.recent_events(0) == []
    assert tracker.recent_events(-1) == []
    snapshot = tracker.snapshot()
    assert snapshot["gauges"]["execution.last_latency_ms"] == 7.0
    assert "execution.last_latency_ms" not in snapshot["metrics"]


def test_alerts_filter_deduplicate_and_allow_repeated_critical():
    sent: list[str] = []
    manager = AlertManager(
        {
            "telegram_enabled": False,
            "minimum_severity": "WARNING",
            "dedup_window_seconds": 60,
        }
    )

    def sender(message: str) -> bool:
        sent.append(message)
        return True

    assert not manager.send_alert(AlertSeverity.INFO, "noise", "ignored", sender=sender)
    assert manager.send_alert(AlertSeverity.WARNING, "feed", "stale", sender=sender)
    assert not manager.send_alert(AlertSeverity.WARNING, "feed", "stale", sender=sender)
    assert manager.send_alert(AlertSeverity.CRITICAL, "halt", "flatten", sender=sender)
    assert manager.send_alert(AlertSeverity.CRITICAL, "halt", "flatten", sender=sender)
    assert len(sent) == 3


def test_alert_configuration_and_delivery_fail_open(monkeypatch):
    manager = AlertManager(
        {
            "telegram_enabled": True,
            "minimum_severity": "not-a-severity",
            "alert_history_maxlen": 2,
        }
    )
    assert manager.minimum_severity is AlertSeverity.INFO

    def broken_transport(*args, **kwargs):
        raise ValueError("mock formatting failure")

    monkeypatch.setattr("alerts.alert_manager.requests.post", broken_transport)
    assert manager.send_message("message") is False
    for index in range(3):
        manager.alert_history.append({"index": index})
    assert manager.get_alert_stats()["recent"] == [{"index": 1}, {"index": 2}]


def test_health_checks_are_independent_and_non_throwing():
    manager = AlertManager({"telegram_enabled": False})
    manager.send_alert = lambda *args, **kwargs: False
    result = manager.run_health_checks(
        {
            "broker": lambda: True,
            "database": lambda: False,
            "collector": lambda: (_ for _ in ()).throw(RuntimeError("down")),
        }
    )
    assert result == {"broker": True, "database": False, "collector": False}


def test_restart_checkpoint_round_trip_and_clear(tmp_path):
    database = DatabaseManager(database_url=f"sqlite:///{tmp_path / 'p7.db'}")
    payload = {
        "correlation_id": "trade-restart",
        "open_strategy_ids": ["strategy-1"],
        "last_reconciled_order_id": "order-7",
    }
    assert database.save_runtime_checkpoint("execution", payload) is not None
    assert database.load_runtime_checkpoint("execution") == payload
    assert database.delete_runtime_checkpoint("execution")
    assert database.load_runtime_checkpoint("execution") is None


def test_corrupt_restart_checkpoint_fails_closed_to_none(tmp_path):
    database = DatabaseManager(database_url=f"sqlite:///{tmp_path / 'p7-corrupt.db'}")
    with database.get_session() as session:
        session.add(RuntimeCheckpoint(key="execution", payload_json="not-json"))
    assert database.load_runtime_checkpoint("execution") is None


def test_unserializable_restart_checkpoint_is_non_fatal(tmp_path):
    database = DatabaseManager(database_url=f"sqlite:///{tmp_path / 'p7-recursive.db'}")
    recursive: list = []
    recursive.append(recursive)
    assert database.save_runtime_checkpoint("execution", {"value": recursive}) is None


def test_daily_report_contains_pnl_drawdown_greeks_and_recovery():
    report = compact_daily_report(
        DailyOperationalMetrics(
            realized_pnl=125.5,
            drawdown_percent=1.25,
            delta=0.4,
            gamma=0.02,
            theta=-4.2,
            vega=3.1,
            scheduler_cycles=9,
            recoveries=1,
        )
    )
    assert "PnL=125.50" in report
    assert "DD=1.25%" in report
    assert "Greeks" in report
    assert "recovery=1" in report


def test_structured_logger_includes_context_correlation_id():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    with correlation_scope("trade-logger"):
        encoded = LinuxStructuredFormatter().format(record)
    assert '"correlation_id": "trade-logger"' in encoded


def test_legacy_tracker_api_remains_available():
    from analytics.tracker import Tracker

    tracker = Tracker()
    tracker.record_open("NIFTY", "legacy", 100.0, 2, "BUY")
    assert tracker.record_close("NIFTY", 105.0) == 10.0
    assert tracker.get_summary()["closed_trades"] == 1


def test_main_p7_hook_is_feature_flagged_and_fail_open():
    from main import RajTradingBot

    bot = RajTradingBot.__new__(RajTradingBot)
    bot.p7_observability_enabled = False
    bot.observability = None
    assert bot._observe_p7(EventType.DATA_FETCH, "data")

    class BrokenTracker:
        def emit(self, *args, **kwargs):
            raise RuntimeError("telemetry unavailable")

    bot.p7_observability_enabled = True
    bot.observability = BrokenTracker()
    assert bot._observe_p7(EventType.EXECUTION, "execution")


def test_trade_alert_runs_before_broken_telemetry_payload():
    from main import RajTradingBot

    class StrategyTracker:
        def record_trade(self, *args):
            return None

    bot = RajTradingBot.__new__(RajTradingBot)
    bot.strategy_tracker = StrategyTracker()
    call_sequence: list[str] = []

    def record_alert(*args):
        call_sequence.append("alert")

    def broken_observer(*args, **kwargs):
        call_sequence.append("telemetry")
        raise RuntimeError("telemetry failed")

    bot._send_trade_alert = record_alert
    bot._observe_p7 = broken_observer

    bot._record_trade_outcome("NIFTY", "BUY", 100.0, 1, "25", won=True)
    assert call_sequence == ["alert", "telemetry"]
