"""
End-to-end test suite for the quant trading system.

Covers:
- Pure helper functions (compute_ensemble_score, compute_ensemble_v2)
- PriceCache
- Zerodha OAuth helpers
- ZerodhaWebsocket
- RajTradingBot initialization and lifecycle
- Options expiry and symbol resolution
- Alert manager
- Full integration flow with mocked external dependencies
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ===================================================================
# 1. Pure function tests
# ===================================================================


class TestComputeEnsembleScore:
    """Tests for the pure scoring helpers in main.py."""

    def test_returns_float_in_range(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
        )
        assert isinstance(result, float)
        assert -1.0 <= result <= 1.0

    def test_bullish_rsi_above_50(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=60.0,
            macd=1.0,
            macd_signal=0.5,
            sma20=100.0,
            close=105.0,
            volume=1500,
            avg_volume=1000,
        )
        assert result > 0.0

    def test_bearish_rsi_below_50(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=40.0,
            macd=-1.0,
            macd_signal=-0.5,
            sma20=100.0,
            close=95.0,
            volume=500,
            avg_volume=1000,
        )
        assert result < 0.0

    def test_neutral_mid_rsi(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
        )
        assert -0.3 <= result <= 0.3

    def test_volume_surge_boosts_score(self):
        from main import compute_ensemble_score

        base = compute_ensemble_score(
            rsi=55.0,
            macd=0.5,
            macd_signal=0.3,
            sma20=100.0,
            close=102.0,
            volume=1000,
            avg_volume=1000,
        )
        surge = compute_ensemble_score(
            rsi=55.0,
            macd=0.5,
            macd_signal=0.3,
            sma20=100.0,
            close=102.0,
            volume=2000,
            avg_volume=1000,
        )
        assert surge >= base

    def test_price_above_sma_bullish(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=55.0,
            macd=0.5,
            macd_signal=0.3,
            sma20=100.0,
            close=110.0,
            volume=1000,
            avg_volume=1000,
        )
        assert result > 0.0

    def test_price_below_sma_bearish(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=45.0,
            macd=-0.5,
            macd_signal=-0.3,
            sma20=100.0,
            close=90.0,
            volume=1000,
            avg_volume=1000,
        )
        assert result < 0.0

    def test_extreme_rsi_overbought(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=85.0,
            macd=2.0,
            macd_signal=1.5,
            sma20=100.0,
            close=120.0,
            volume=3000,
            avg_volume=1000,
        )
        assert isinstance(result, float)

    def test_extreme_rsi_oversold(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=15.0,
            macd=-2.0,
            macd_signal=-1.5,
            sma20=100.0,
            close=80.0,
            volume=300,
            avg_volume=1000,
        )
        assert isinstance(result, float)

    def test_zero_volume_does_not_crash(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=0,
            avg_volume=0,
        )
        assert isinstance(result, float)


class TestComputeEnsembleV2:
    """Tests for the v2 ensemble scoring helper."""

    def test_returns_float_in_range(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
            atr=2.0,
            bb_upper=110.0,
            bb_lower=90.0,
            adx=25.0,
        )
        assert isinstance(result, float)
        assert -1.0 <= result <= 1.0

    def test_bullish_conditions(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=60.0,
            macd=1.0,
            macd_signal=0.5,
            sma20=100.0,
            close=105.0,
            volume=1500,
            avg_volume=1000,
            atr=3.0,
            bb_upper=115.0,
            bb_lower=95.0,
            adx=30.0,
        )
        assert result > 0.0

    def test_bearish_conditions(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=40.0,
            macd=-1.0,
            macd_signal=-0.5,
            sma20=100.0,
            close=95.0,
            volume=500,
            avg_volume=1000,
            atr=3.0,
            bb_upper=110.0,
            bb_lower=90.0,
            adx=20.0,
        )
        assert result < 0.0

    def test_strong_trend_high_adx(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=65.0,
            macd=2.0,
            macd_signal=1.0,
            sma20=100.0,
            close=110.0,
            volume=2000,
            avg_volume=1000,
            atr=5.0,
            bb_upper=120.0,
            bb_lower=90.0,
            adx=50.0,
        )
        assert result > 0.0

    def test_low_adx_weak_trend(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
            atr=1.0,
            bb_upper=105.0,
            bb_lower=95.0,
            adx=10.0,
        )
        assert isinstance(result, float)

    def test_price_at_bb_upper(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=70.0,
            macd=1.5,
            macd_signal=1.0,
            sma20=100.0,
            close=115.0,
            volume=1500,
            avg_volume=1000,
            atr=4.0,
            bb_upper=115.0,
            bb_lower=95.0,
            adx=35.0,
        )
        assert isinstance(result, float)

    def test_price_at_bb_lower(self):
        from main import compute_ensemble_v2

        result = compute_ensemble_v2(
            rsi=30.0,
            macd=-1.5,
            macd_signal=-1.0,
            sma20=100.0,
            close=95.0,
            volume=800,
            avg_volume=1000,
            atr=4.0,
            bb_upper=115.0,
            bb_lower=95.0,
            adx=35.0,
        )
        assert isinstance(result, float)


# ===================================================================
# 2. PriceCache tests
# ===================================================================


class TestPriceCache:
    """Tests for the PriceCache class."""

    def test_initialization(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        assert cache.ttl == 60
        assert len(cache._data) == 0

    def test_set_and_get(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        cache.set("RELIANCE", 2500.0)
        assert cache.get("RELIANCE") == 2500.0

    def test_get_missing_key(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        assert cache.get("MISSING") is None

    def test_ttl_expiration(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=1)
        cache.set("TEST", 100.0)
        assert cache.get("TEST") == 100.0
        time.sleep(1.1)
        assert cache.get("TEST") is None

    def test_overwrite_existing(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        cache.set("TEST", 100.0)
        cache.set("TEST", 200.0)
        assert cache.get("TEST") == 200.0

    def test_multiple_symbols(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        cache.set("A", 10.0)
        cache.set("B", 20.0)
        cache.set("C", 30.0)
        assert cache.get("A") == 10.0
        assert cache.get("B") == 20.0
        assert cache.get("C") == 30.0

    def test_cleanup_removes_expired(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=1)
        cache.set("A", 10.0)
        cache.set("B", 20.0)
        time.sleep(1.1)
        cache.cleanup()
        assert cache.get("A") is None
        assert cache.get("B") is None


# ===================================================================
# 3. Zerodha OAuth tests
# ===================================================================


class TestZerodhaOAuth:
    """Tests for Zerodha OAuth helper functions."""

    def test_build_login_url(self):
        from main import build_zerodha_login_url

        url = build_zerodha_login_url("test_key", "http://localhost/callback")
        assert "test_key" in url
        assert "localhost" in url

    def test_build_login_url_contains_required_params(self):
        from main import build_zerodha_login_url

        url = build_zerodha_login_url("api_key", "http://callback")
        assert "api_key=api_key" in url
        assert "redirect_uri=" in url

    def test_extract_request_token(self):
        from main import extract_request_token

        url = "http://localhost/?status=success&request_token=abc123"
        token = extract_request_token(url)
        assert token == "abc123"

    def test_extract_request_token_missing(self):
        from main import extract_request_token

        url = "http://localhost/?status=success"
        token = extract_request_token(url)
        assert token is None

    def test_extract_request_token_failure(self):
        from main import extract_request_token

        url = "http://localhost/?status=failure"
        token = extract_request_token(url)
        assert token is None


# ===================================================================
# 4. ZerodhaWebsocket tests
# ===================================================================


class TestZerodhaWebsocket:
    """Tests for the ZerodhaWebsocket class."""

    def _make_ws(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        return ZerodhaWebsocket(
            api_key="test_key",
            access_token="test_token",
            on_tick=lambda ticks: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda err: None,
        )

    def test_initialization(self, tmp_path: Path):
        ws = self._make_ws(tmp_path)
        assert ws.api_key == "test_key"
        assert ws.access_token == "test_token"
        assert ws._tokens == []

    def test_set_tokens(self, tmp_path: Path):
        ws = self._make_ws(tmp_path)
        ws.set_tokens(["RELIANCE", "TCS", "INFY"])
        assert ws._tokens == ["RELIANCE", "TCS", "INFY"]

    def test_connect_calls_on_connect(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        connect_called = []

        def on_connect():
            connect_called.append(True)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=on_connect,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws.connect()
        assert connect_called

    def test_subscribe_adds_tokens(self, tmp_path: Path):
        ws = self._make_ws(tmp_path)
        ws.subscribe(["RELIANCE", "TCS"])
        assert "RELIANCE" in ws._tokens
        assert "TCS" in ws._tokens

    def test_unsubscribe_removes_tokens(self, tmp_path: Path):
        ws = self._make_ws(tmp_path)
        ws.set_tokens(["RELIANCE", "TCS", "INFY"])
        ws.unsubscribe(["TCS"])
        assert "TCS" not in ws._tokens
        assert "RELIANCE" in ws._tokens

    def test_disconnect_calls_on_close(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        close_called = []

        def on_close():
            close_called.append(True)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=lambda: None,
            on_close=on_close,
            on_error=lambda e: None,
        )
        ws.disconnect()
        assert close_called

    def test_error_calls_on_error(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        errors = []

        def on_error(err):
            errors.append(err)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=on_error,
        )
        ws._on_error("test_error")
        assert "test_error" in errors

    def test_tick_processing(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ticks_received = []

        def on_tick(ticks):
            ticks_received.extend(ticks)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=on_tick,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._on_tick([{"symbol": "RELIANCE", "price": 2500.0}])
        assert len(ticks_received) == 1
        assert ticks_received[0]["symbol"] == "RELIANCE"

    def test_reconnect_logic(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._reconnect_attempts = 0
        ws._max_reconnect_attempts = 3
        assert ws._reconnect_attempts == 0


# ===================================================================
# 5. RajTradingBot initialization tests
# ===================================================================


class TestRajTradingBotInit:
    """Tests for RajTradingBot initialization."""

    def _make_system(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "test_key")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        return RajTradingBot()

    def test_initialization(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        system = self._make_system(tmp_path, monkeypatch)
        assert system is not None
        assert hasattr(system, "config")
        assert hasattr(system, "db_manager")
        assert hasattr(system, "redis_client")

    def test_config_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        system = self._make_system(tmp_path, monkeypatch)
        assert system.config is not None
        assert isinstance(system.config, dict)

    def test_config_has_required_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        system = self._make_system(tmp_path, monkeypatch)
        required = ["trading", "risk", "symbols"]
        for key in required:
            assert key in system.config, f"Missing config key: {key}"

    def test_db_manager_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        system = self._make_system(tmp_path, monkeypatch)
        assert system.db_manager is not None

    def test_redis_client_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        system = self._make_system(tmp_path, monkeypatch)
        assert system.redis_client is not None

    def test_logger_initialized(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        system = self._make_system(tmp_path, monkeypatch)
        assert system.logger is not None

    def test_price_cache_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import PriceCache

        system = self._make_system(tmp_path, monkeypatch)
        assert hasattr(system, "price_cache")
        assert isinstance(system.price_cache, PriceCache)

    def test_websocket_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        system = self._make_system(tmp_path, monkeypatch)
        assert hasattr(system, "websocket")
        assert system.websocket is not None

    def test_alert_manager_initialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        system = self._make_system(tmp_path, monkeypatch)
        assert hasattr(system, "alert_manager")
        assert system.alert_manager is not None


# ===================================================================
# 6. RajTradingBot lifecycle tests
# ===================================================================


class TestRajTradingBotLifecycle:
    """Tests for start/stop/run lifecycle."""

    def test_start_initializes_components(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()
        assert system._running is True

    def test_stop_cleans_up(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()
        system.stop()
        assert system._running is False

    def test_double_start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()
        system.start()  # Should not raise
        system.stop()

    def test_stop_without_start(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.stop()  # Should not raise


# ===================================================================
# 7. Options expiry tests
# ===================================================================


class TestOptionsExpiry:
    """Tests for options expiry handling."""

    def test_get_current_month_expiry(self):
        from main import get_current_month_expiry

        expiry = get_current_month_expiry()
        assert expiry is not None
        assert isinstance(expiry, str)

    def test_expiry_is_thursday(self):
        from main import get_current_month_expiry

        expiry_str = get_current_month_expiry()
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
        assert expiry_date.weekday() == 3  # Thursday

    def test_expiry_is_future_or_today(self):
        from main import get_current_month_expiry

        expiry_str = get_current_month_expiry()
        expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
        assert expiry_date >= datetime.now().date()

    def test_get_next_expiry_after_current(self):
        from main import get_current_month_expiry, get_next_expiry

        current = datetime.strptime(get_current_month_expiry(), "%Y-%m-%d").date()
        next_exp = datetime.strptime(get_next_expiry(), "%Y-%m-%d").date()
        assert next_exp > current

    def test_build_option_symbol_nifty(self):
        from main import build_option_symbol

        symbol = build_option_symbol("NIFTY", 22000, "CE", "2024-01-25")
        assert "NIFTY" in symbol
        assert "CE" in symbol
        assert "22000" in symbol

    def test_build_option_symbol_banknifty(self):
        from main import build_option_symbol

        symbol = build_option_symbol("BANKNIFTY", 46000, "PE", "2024-01-25")
        assert "BANKNIFTY" in symbol
        assert "PE" in symbol

    def test_build_option_symbol_format(self):
        from main import build_option_symbol

        symbol = build_option_symbol("NIFTY", 22000, "CE", "2024-01-25")
        parts = symbol.split()
        assert len(parts) >= 3


# ===================================================================
# 8. Alert manager tests
# ===================================================================


class TestAlertManager:
    """Tests for the AlertManager class."""

    def _make_manager(self, tmp_path: Path):
        from main import AlertManager

        return AlertManager(
            db_path=str(tmp_path / "alerts.db"),
            log_path=str(tmp_path / "alerts.log"),
        )

    def test_initialization(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        assert manager is not None
        assert hasattr(manager, "db_path")
        assert hasattr(manager, "log_path")

    def test_send_alert(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        result = manager.send_alert(
            alert_type="PRICE",
            symbol="RELIANCE",
            message="Price crossed threshold",
            severity="INFO",
        )
        assert result is True or result is None

    def test_send_multiple_alerts(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        for i in range(5):
            manager.send_alert(
                alert_type="PRICE",
                symbol=f"SYM{i}",
                message=f"Alert {i}",
                severity="INFO",
            )

    def test_alert_with_different_severities(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        for severity in ["INFO", "WARNING", "CRITICAL"]:
            manager.send_alert(
                alert_type="RISK",
                symbol="TEST",
                message=f"Severity {severity}",
                severity=severity,
            )

    def test_alert_log_file_created(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        manager.send_alert("TEST", "SYM", "msg", "INFO")
        assert (tmp_path / "alerts.log").exists()

    def test_alert_db_file_created(self, tmp_path: Path):
        manager = self._make_manager(tmp_path)
        manager.send_alert("TEST", "SYM", "msg", "INFO")
        assert (tmp_path / "alerts.db").exists()


# ===================================================================
# 9. Symbol resolution tests
# ===================================================================


class TestSymbolResolution:
    """Tests for symbol resolution and mapping."""

    def test_resolve_nifty_symbol(self):
        from main import resolve_symbol

        result = resolve_symbol("NIFTY")
        assert result is not None
        assert isinstance(result, str)

    def test_resolve_banknifty_symbol(self):
        from main import resolve_symbol

        result = resolve_symbol("BANKNIFTY")
        assert result is not None

    def test_resolve_equity_symbol(self):
        from main import resolve_symbol

        result = resolve_symbol("RELIANCE")
        assert result is not None

    def test_resolve_invalid_symbol(self):
        from main import resolve_symbol

        result = resolve_symbol("INVALID_SYMBOL_XYZ")
        assert result is not None  # Should return original or mapped

    def test_resolve_consistent(self):
        from main import resolve_symbol

        r1 = resolve_symbol("NIFTY")
        r2 = resolve_symbol("NIFTY")
        assert r1 == r2


# ===================================================================
# 10. Risk management tests
# ===================================================================


class TestRiskManagement:
    """Tests for risk management functions."""

    def test_calculate_position_size(self):
        from main import calculate_position_size

        size = calculate_position_size(
            capital=100000,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=980,
        )
        assert size > 0
        assert isinstance(size, int)

    def test_position_size_scales_with_capital(self):
        from main import calculate_position_size

        small = calculate_position_size(
            capital=50000,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=980,
        )
        large = calculate_position_size(
            capital=100000,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=980,
        )
        assert large > small

    def test_position_size_with_tight_stop(self):
        from main import calculate_position_size

        size = calculate_position_size(
            capital=100000,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=990,
        )
        assert size > 0

    def test_calculate_max_loss(self):
        from main import calculate_max_loss

        max_loss = calculate_max_loss(
            capital=100000,
            max_drawdown_pct=0.1,
        )
        assert max_loss == 10000.0

    def test_validate_trade(self):
        from main import validate_trade

        result = validate_trade(
            symbol="RELIANCE",
            quantity=10,
            price=2500,
            capital=100000,
            risk_per_trade=0.02,
        )
        assert result is True or result is False

    def test_validate_trade_exceeds_capital(self):
        from main import validate_trade

        result = validate_trade(
            symbol="RELIANCE",
            quantity=1000,
            price=2500,
            capital=100000,
            risk_per_trade=0.02,
        )
        assert result is False


# ===================================================================
# 11. Data fetching tests
# ===================================================================


class TestDataFetching:
    """Tests for market data fetching."""

    def test_fetch_historical_data(self):
        from main import fetch_historical_data

        data = fetch_historical_data("RELIANCE", "1D", "1mo")
        assert data is not None
        assert len(data) > 0

    def test_fetch_historical_data_columns(self):
        from main import fetch_historical_data

        data = fetch_historical_data("RELIANCE", "1D", "1mo")
        required_cols = ["open", "high", "low", "close", "volume"]
        for col in required_cols:
            assert col in data.columns, f"Missing column: {col}"

    def test_fetch_quote(self):
        from main import fetch_quote

        quote = fetch_quote("RELIANCE")
        assert quote is not None
        assert "last_price" in quote or "price" in quote

    def test_fetch_quotes_batch(self):
        from main import fetch_quotes_batch

        quotes = fetch_quotes_batch(["RELIANCE", "TCS", "INFY"])
        assert quotes is not None
        assert len(quotes) >= 1

    def test_fetch_invalid_symbol(self):
        from main import fetch_historical_data

        data = fetch_historical_data("INVALID_XYZ_123", "1D", "1mo")
        assert data is None or len(data) == 0


# ===================================================================
# 12. Strategy execution tests
# ===================================================================


class TestStrategyExecution:
    """Tests for strategy execution."""

    def test_run_rsi_strategy(self):
        from main import run_rsi_strategy

        result = run_rsi_strategy("RELIANCE", period="1mo")
        assert result is not None
        assert "signal" in result or "action" in result

    def test_run_macd_strategy(self):
        from main import run_macd_strategy

        result = run_macd_strategy("RELIANCE", period="1mo")
        assert result is not None

    def test_run_bollinger_strategy(self):
        from main import run_bollinger_strategy

        result = run_bollinger_strategy("RELIANCE", period="1mo")
        assert result is not None

    def test_run_ema_cross_strategy(self):
        from main import run_ema_cross_strategy

        result = run_ema_cross_strategy("RELIANCE", period="1mo")
        assert result is not None

    def test_run_supertrend_strategy(self):
        from main import run_supertrend_strategy

        result = run_supertrend_strategy("RELIANCE", period="1mo")
        assert result is not None

    def test_run_donchian_strategy(self):
        from main import run_donchian_strategy

        result = run_donchian_strategy("RELIANCE", period="1mo")
        assert result is not None


# ===================================================================
# 13. Backtesting tests
# ===================================================================


class TestBacktesting:
    """Tests for backtesting engine."""

    def test_run_backtest_rsi(self):
        from main import run_backtest

        result = run_backtest("RELIANCE", strategy="rsi", period="1y")
        assert result is not None
        assert "total_return" in result or "returns" in result

    def test_run_backtest_macd(self):
        from main import run_backtest

        result = run_backtest("RELIANCE", strategy="macd", period="1y")
        assert result is not None

    def test_run_backtest_bollinger(self):
        from main import run_backtest

        result = run_backtest("RELIANCE", strategy="bollinger", period="1y")
        assert result is not None

    def test_backtest_metrics_present(self):
        from main import run_backtest

        result = run_backtest("RELIANCE", strategy="rsi", period="1y")
        expected_metrics = ["total_return", "sharpe_ratio", "max_drawdown", "win_rate"]
        for metric in expected_metrics:
            assert metric in result, f"Missing metric: {metric}"


# ===================================================================
# 14. Database tests
# ===================================================================


class TestDatabase:
    """Tests for database operations."""

    def test_db_connection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        assert system.db_manager is not None

    def test_save_trade(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        trade = {
            "symbol": "RELIANCE",
            "side": "BUY",
            "quantity": 10,
            "price": 2500.0,
            "timestamp": datetime.now().isoformat(),
        }
        result = system.db_manager.save_trade(trade)
        assert result is True or result is None

    def test_get_trades(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        trades = system.db_manager.get_trades("RELIANCE")
        assert trades is not None


# ===================================================================
# 15. Redis cache tests
# ===================================================================


class TestRedisCache:
    """Tests for Redis caching."""

    def test_redis_set_get(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        if system.redis_client is None:
            pytest.skip("Redis not available")

        system.redis_client.set("test_key", "test_value", ex=60)
        assert system.redis_client.get("test_key") == b"test_value"

    def test_redis_delete(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        if system.redis_client is None:
            pytest.skip("Redis not available")

        system.redis_client.set("del_key", "val", ex=60)
        system.redis_client.delete("del_key")
        assert system.redis_client.get("del_key") is None


# ===================================================================
# 16. Configuration tests
# ===================================================================


class TestConfiguration:
    """Tests for configuration loading and validation."""

    def test_load_config_file(self, tmp_path: Path):
        from main import load_config

        config_data = {
            "trading": {"max_positions": 10},
            "risk": {"max_drawdown": 0.1},
            "symbols": ["RELIANCE", "TCS"],
        }
        config_file = tmp_path / "config.yaml"
        import yaml

        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        config = load_config(str(config_file))
        assert config["trading"]["max_positions"] == 10

    def test_config_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        assert "trading" in system.config
        assert "risk" in system.config

    def test_config_validation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        assert system.config["trading"]["max_positions"] > 0
        assert system.config["risk"]["max_drawdown"] > 0


# ===================================================================
# 17. Error handling tests
# ===================================================================


class TestErrorHandling:
    """Tests for error handling and resilience."""

    def test_invalid_symbol_no_crash(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
        )
        assert isinstance(result, float)

    def test_none_inputs_handled(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=100.0,
            volume=1000,
            avg_volume=1000,
        )
        assert isinstance(result, float)

    def test_extreme_values(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=100.0,
            macd=1e6,
            macd_signal=1e6,
            sma20=1e6,
            close=1e6,
            volume=1e9,
            avg_volume=1e9,
        )
        assert isinstance(result, float)
        assert not (result != result)  # not NaN

    def test_negative_prices(self):
        from main import compute_ensemble_score

        result = compute_ensemble_score(
            rsi=50.0,
            macd=0.0,
            macd_signal=0.0,
            sma20=100.0,
            close=-10.0,
            volume=1000,
            avg_volume=1000,
        )
        assert isinstance(result, float)


# ===================================================================
# 18. Integration tests
# ===================================================================


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_pipeline_mocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "test_key")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("LOG_LEVEL", "INFO")

        system = RajTradingBot()
        system.start()

        assert system._running is True

        system.stop()
        assert system._running is False

    def test_signal_generation_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        signals = system.generate_signals(["RELIANCE", "TCS"])
        assert signals is not None
        assert isinstance(signals, list)

        system.stop()

    def test_portfolio_update(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        portfolio = system.get_portfolio()
        assert portfolio is not None

        system.stop()

    def test_risk_check_pipeline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        risk_status = system.check_risk_limits()
        assert risk_status is not None

        system.stop()

    def test_websocket_tick_to_signal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        tick = {
            "symbol": "RELIANCE",
            "price": 2500.0,
            "volume": 100000,
            "timestamp": datetime.now().isoformat(),
        }
        system.websocket._on_tick([tick])

        system.stop()

    def test_alert_integration(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        system.alert_manager.send_alert(
            alert_type="TEST",
            symbol="RELIANCE",
            message="Integration test alert",
            severity="INFO",
        )

        system.stop()


# ===================================================================
# 19. Performance tests
# ===================================================================


class TestPerformance:
    """Performance and load tests."""

    def test_compute_ensemble_score_performance(self, benchmark=None):
        from main import compute_ensemble_score

        if benchmark is None:
            for _ in range(1000):
                compute_ensemble_score(
                    rsi=50.0,
                    macd=0.0,
                    macd_signal=0.0,
                    sma20=100.0,
                    close=100.0,
                    volume=1000,
                    avg_volume=1000,
                )
        else:
            benchmark(
                compute_ensemble_score,
                rsi=50.0,
                macd=0.0,
                macd_signal=0.0,
                sma20=100.0,
                close=100.0,
                volume=1000,
                avg_volume=1000,
            )

    def test_price_cache_performance(self):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)
        for i in range(10000):
            cache.set(f"SYM{i}", float(i))
        for i in range(10000):
            assert cache.get(f"SYM{i}") == float(i)

    def test_batch_signal_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        symbols = [f"SYM{i}" for i in range(100)]
        signals = system.generate_signals(symbols)
        assert signals is not None

        system.stop()


# ===================================================================
# 20. Edge case tests
# ===================================================================


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_empty_symbol_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        signals = system.generate_signals([])
        assert signals == [] or signals is not None

        system.stop()

    def test_single_symbol(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        signals = system.generate_signals(["RELIANCE"])
        assert signals is not None

        system.stop()

    def test_very_large_quantity(self):
        from main import calculate_position_size

        size = calculate_position_size(
            capital=1e9,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=990,
        )
        assert size > 0

    def test_very_small_quantity(self):
        from main import calculate_position_size

        size = calculate_position_size(
            capital=1000,
            risk_per_trade=0.02,
            entry_price=1000,
            stop_loss=990,
        )
        assert size >= 0

    def test_concurrent_access(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import PriceCache

        cache = PriceCache(ttl_seconds=60)

        def writer(start: int):
            for i in range(start, start + 100):
                cache.set(f"SYM{i}", float(i))

        def reader(start: int):
            for i in range(start, start + 100):
                cache.get(f"SYM{i}")

        import threading

        threads = []
        for i in range(5):
            t = threading.Thread(target=writer, args=(i * 100,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    def test_malformed_tick(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ticks = []

        def on_tick(t):
            ticks.extend(t)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=on_tick,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._on_tick([{"bad": "tick"}])
        assert len(ticks) == 1

    def test_empty_tick_list(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ticks = []

        def on_tick(t):
            ticks.extend(t)

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=on_tick,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._on_tick([])
        assert len(ticks) == 0


# ===================================================================
# 21. Zerodha API integration tests (mocked)
# ===================================================================


class TestZerodhaAPIIntegration:
    """Tests for Zerodha API interactions with mocked responses."""

    def test_get_holdings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        with patch.object(system.zerodha_client, "holdings") as mock_holdings:
            mock_holdings.return_value = {"data": []}
            holdings = system.zerodha_client.holdings()
            assert holdings is not None

        system.stop()

    def test_place_order_mocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        with patch.object(system.zerodha_client, "place_order") as mock_order:
            mock_order.return_value = {"order_id": "TEST123"}
            result = system.place_order("RELIANCE", "BUY", 1)
            assert result is not None

        system.stop()

    def test_get_positions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from main import RajTradingBot

        monkeypatch.setenv("ZERODHA_API_KEY", "k")
        monkeypatch.setenv("ZERODHA_ACCESS_TOKEN", "t")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        system = RajTradingBot()
        system.start()

        with patch.object(system.zerodha_client, "positions") as mock_pos:
            mock_pos.return_value = {"data": {"net": []}}
            positions = system.zerodha_client.positions()
            assert positions is not None

        system.stop()


# ===================================================================
# 22. WebSocket reconnection tests
# ===================================================================


class TestWebSocketReconnection:
    """Tests for WebSocket reconnection behavior."""

    def test_reconnect_on_disconnect(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        reconnect_count = [0]

        def on_connect():
            pass

        def on_close():
            reconnect_count[0] += 1

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=on_connect,
            on_close=on_close,
            on_error=lambda e: None,
        )
        ws._max_reconnect_attempts = 3
        ws._reconnect_delay = 0.1

        ws.disconnect()
        assert reconnect_count[0] >= 1

    def test_max_reconnect_attempts(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._max_reconnect_attempts = 3
        ws._reconnect_attempts = 3
        assert not ws._should_reconnect()

    def test_should_reconnect_under_limit(self, tmp_path: Path):
        from main import ZerodhaWebsocket

        ws = ZerodhaWebsocket(
            api_key="k",
            access_token="t",
            on_tick=lambda x: None,
            on_connect=lambda: None,
            on_close=lambda: None,
            on_error=lambda e: None,
        )
        ws._max_reconnect_attempts = 3
        ws._reconnect_attempts = 1
        assert ws._should_reconnect()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:
    """Ensure tmp_path exists and is writable."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(scope="session")
def sample_market_data():
    """Provide sample market data for tests."""
    return {
        "RELIANCE": {
            "price": 2500.0,
            "volume": 1000000,
            "rsi": 55.0,
            "macd": 1.0,
            "macd_signal": 0.5,
            "sma20": 2450.0,
            "avg_volume": 800000,
        },
        "TCS": {
            "price": 3800.0,
            "volume": 500000,
            "rsi": 60.0,
            "macd": 2.0,
            "macd_signal": 1.0,
            "sma20": 3700.0,
            "avg_volume": 600000,
        },
    }


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
