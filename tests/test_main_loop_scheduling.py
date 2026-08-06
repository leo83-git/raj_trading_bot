from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import main as main_module
from main import RajTradingBot


def _make_config(*, scheduler_enabled: bool, scan_interval: int = 5, manage_interval: int = 2):
    return {
        "mode": "PAPER",
        "broker": {
            "primary": "zerodha",
            "zerodha": {"api_key": "k", "api_secret": "s"},
        },
        "intraday": {
            "min_screener_score": 0.01,
            "min_volume": 2000,
            "min_price": 5,
        },
        "fno": {"min_screener_score": 0.0, "min_volume": 10000, "min_price": 10},
        "watchlist": {"enabled": False},
        "price_cache": {"enabled": False},
        "scheduler": {
            "enabled": scheduler_enabled,
            "scan_interval_seconds": scan_interval,
            "manage_interval_seconds": manage_interval,
            "loop_sleep_seconds": 1,
        },
    }


def _patch_trading_day(monkeypatch):
    class FixedDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 8, 6, 10, 0, 0, tzinfo=tz)

    monkeypatch.setattr(main_module.datetime, "datetime", FixedDateTime)


def test_scheduler_repeats_scan_and_manage_independently(monkeypatch):
    clock = {"value": 0.0}
    scan_submit_times = []
    manage_call_times = []

    class FakeFuture:
        def __init__(self, complete_at: float):
            self.complete_at = complete_at

        def done(self):
            return clock["value"] >= self.complete_at

        def result(self):
            return None

    class FakeExecutor:
        def shutdown(self, wait=False, cancel_futures=False):
            return None

        def submit(self, fn, *args, **kwargs):
            scan_submit_times.append(clock["value"])
            return FakeFuture(clock["value"] + 4.0)

    def fake_monotonic():
        return clock["value"]

    def fake_sleep(seconds):
        clock["value"] += seconds
        if clock["value"] >= 7.0:
            qts._running = False

    _patch_trading_day(monkeypatch)
    monkeypatch.setattr(main_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)

    with patch.object(RajTradingBot, "_setup_broker"), patch.object(
        RajTradingBot, "_setup_layers"
    ):
        qts = RajTradingBot(_make_config(scheduler_enabled=True))

    qts._scan_executor.shutdown(wait=False, cancel_futures=True)
    qts._scan_executor = FakeExecutor()

    def record_manage_positions():
        manage_call_times.append(clock["value"])

    monkeypatch.setattr(qts, "_manage_positions", record_manage_positions)
    monkeypatch.setattr(qts, "_check_zerodha_daily_token", lambda: None)
    monkeypatch.setattr(qts, "_is_trading_holiday", lambda: False)
    monkeypatch.setattr(qts, "_close_all_positions", lambda: None)
    monkeypatch.setattr(qts, "generate_daily_report", lambda: None)
    monkeypatch.setattr(qts, "auto_train_after_market", lambda: None)
    monkeypatch.setattr(qts, "simulation", None, raising=False)

    qts.run()

    assert scan_submit_times == [0.0, 5.0]
    assert manage_call_times == [0.0, 2.0, 4.0, 6.0]


def test_legacy_loop_remains_available_when_scheduler_disabled(monkeypatch):
    _patch_trading_day(monkeypatch)
    monkeypatch.setattr(main_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 0.0)

    with patch.object(RajTradingBot, "_setup_broker"), patch.object(
        RajTradingBot, "_setup_layers"
    ):
        qts = RajTradingBot(_make_config(scheduler_enabled=False))

    qts._running = False
    cycle_calls = []

    def record_cycle():
        cycle_calls.append("cycle")

    monkeypatch.setattr(qts, "_run_trading_cycle", record_cycle)
    monkeypatch.setattr(qts, "_check_zerodha_daily_token", lambda: None)
    monkeypatch.setattr(qts, "_is_trading_holiday", lambda: False)

    qts.run()

    assert cycle_calls == ["cycle"]
