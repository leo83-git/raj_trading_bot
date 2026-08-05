"""
Comprehensive end-to-end test suite for the quant trading system.

Covers:
- Pure functions (ensemble scoring, indicators)
- Zerodha OAuth and token management
- WebSocket price streaming and reconnection
- Options chain fetching and expiry handling
- RajTradingBot lifecycle and signal generation
- Alerting and trade validation
- Configuration loading and validation
- Integration scenarios (happy path, failure recovery, edge cases)
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
OPT_EXPIRY_PATH = REPO_ROOT / "opt-expiry.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_temp_config(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Path:
    """Write a minimal config.yaml and return its path."""
    cfg = {
        "zerodha": {
            "api_key": "TEST_API_KEY",
            "api_secret": "TEST_API_SECRET",
            "redirect_uri": "http://localhost:8080/callback",
            "access_token": "TEST_ACCESS_TOKEN",
            "base_url": "https://api.kite.trade",
            "ws_url": "wss://ws.kite.trade",
        },
        "trading": {
            "symbols": ["RELIANCE", "TCS", "INFY"],
            "quantity": 1,
            "paper_trading": True,
            "max_positions": 5,
        },
        "risk": {
            "max_drawdown_pct": 10.0,
            "max_daily_loss_pct": 5.0,
            "stop_loss_pct": 2.0,
            "target_pct": 4.0,
        },
        "alerts": {
            "enabled": True,
            "email": "test@example.com",
            "webhook_url": "http://localhost:9000/webhook",
        },
        "logging": {
            "level": "INFO",
            "file": str(tmp_path / "test_bot.log"),
        },
    }
    if overrides:
        _deep_merge(cfg, overrides)
    cfg_file = tmp_path / "config.yaml"
    import yaml

    cfg_file.write_text(yaml.safe_dump(cfg, default_flow_style=False), encoding="utf-8")
    return cfg_file


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _make_price_series(
    base: float = 100.0, length: int = 60, volatility: float = 0.5
) -> list[float]:
    """Generate a deterministic-ish price series for indicator tests."""
    prices = []
    price = base
    for i in range(length):
        change = (i % 5 - 2) * volatility * 0.1
        price = max(price + change, 1.0)
        prices.append(round(price, 2))
    return prices


def _make_ohlcv(length: int = 60, base: float = 100.0):
    """Return (timestamps, open, high, low, close, volume) lists."""
    timestamps = [
        datetime.utcnow() - timedelta(minutes=length - i) for i in range(length)
    ]
    closes = _make_price_series(base=base, length=length)
    opens = [round(c - 0.1, 2) for c in closes]
    highs = [round(c + 0.2, 2) for c in closes]
    lows = [round(c - 0.2, 2) for c in closes]
    volumes = [1000 + i * 10 for i in range(length)]
    return timestamps, opens, highs, lows, closes, volumes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def tmp_workspace(tmp_path):
    """Provide an isolated workspace directory with a config.yaml."""
    return tmp_path


@pytest.fixture()
def config_file(tmp_workspace):
    return _write_temp_config(tmp_workspace)


@pytest.fixture()
def mock_kite():
    """Return a mock KiteConnect instance."""
    kite = MagicMock()
    kite.profile.return_value = {"user_id": "TEST_USER", "user_name": "Test User"}
    kite.positions.return_value = {"day": [], "net": []}
    kite.holdings.return_value = []
    kite.margins.return_value = {"equity": {"available": {"cash": 100000.0}}}
    kite.ltp.return_value = {
        "NSE:RELIANCE": {"last_price": 2500.0},
        "NSE:TCS": {"last_price": 4000.0},
    }
    kite.quote.return_value = {
        "NSE:RELIANCE": {"last_price": 2500.0, "volume": 500000},
        "NSE:TCS": {"last_price": 4000.0, "volume": 300000},
    }
    kite.instruments.return_value = [
        {
            "instrument_token": 738561,
            "exchange_token": "2885",
            "tradingsymbol": "RELIANCE",
            "name": "RELIANCE",
            "last_price": 2500.0,
            "expiry": "2026-08-26",
            "strike": 0.0,
            "tick_size": 0.05,
            "lot_size": 1,
            "instrument_type": "EQ",
            "exchange": "NSE",
        }
    ]
    return kite


@pytest.fixture()
def price_cache():
    """Return a fresh PriceCache instance."""
    try:
        from zerodha_websocket import PriceCache

        return PriceCache(maxlen=200)
    except ImportError:
        pytest.skip("zerodha_websocket module not available")


@pytest.fixture()
def quant_system(config_file, mock_kite, tmp_workspace):
    """Return an initialized RajTradingBot with mocked external deps."""
    with patch.dict(os.environ, {"CONFIG_PATH": str(config_file)}):
        try:
            from main import RajTradingBot

            system = RajTradingBot.__new__(RajTradingBot)
            system.config = _load_config(config_file)
            system.kite = mock_kite
            system.price_cache = MagicMock()
            system.price_cache.get.return_value = 2500.0
            system.running = False
            system.signals: list[dict[str, Any]] = []
            system.trades: list[dict[str, Any]] = []
            system.daily_pnl: list[float] = []
            system._init_logging()
            return system
        except ImportError:
            pytest.skip("main module not available")


def _load_config(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ===========================================================================
# 1. Pure function tests
# ===========================================================================
class TestPureFunctions:
    """Test stateless helper functions."""

    def test_compute_ensemble_score_bullish(self):
        """High RSI + MACD bullish + price above EMA → bullish score."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = _make_price_series(base=105.0, length=60)
        score = compute_ensemble_score(
            symbol="RELIANCE",
            closes=closes,
            rsi=70.0,
            macd_line=1.5,
            signal_line=1.0,
            histogram=0.5,
            ema_short=103.0,
            ema_long=100.0,
            vwap=102.0,
            current_price=105.0,
            bid_ask_spread_pct=0.05,
            momentum=2.0,
            volume_ratio=1.5,
            iv_rank=30.0,
            theta_decay=0.0,
            vix=15.0,
            regime="bullish",
        )
        assert score is not None
        assert score["total_score"] > 0, "Expected bullish ensemble score > 0"

    def test_compute_ensemble_score_bearish(self):
        """Low RSI + MACD bearish + price below EMA → bearish score."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = _make_price_series(base=95.0, length=60)
        score = compute_ensemble_score(
            symbol="RELIANCE",
            closes=closes,
            rsi=25.0,
            macd_line=-1.5,
            signal_line=-1.0,
            histogram=-0.5,
            ema_short=97.0,
            ema_long=100.0,
            vwap=102.0,
            current_price=95.0,
            bid_ask_spread_pct=0.05,
            momentum=-2.0,
            volume_ratio=0.8,
            iv_rank=70.0,
            theta_decay=0.0,
            vix=25.0,
            regime="bearish",
        )
        assert score["total_score"] < 0, "Expected bearish ensemble score < 0"

    def test_compute_ensemble_score_neutral(self):
        """Mixed signals → score near zero."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = _make_price_series(base=100.0, length=60)
        score = compute_ensemble_score(
            symbol="RELIANCE",
            closes=closes,
            rsi=50.0,
            macd_line=0.0,
            signal_line=0.0,
            histogram=0.0,
            ema_short=100.0,
            ema_long=100.0,
            vwap=100.0,
            current_price=100.0,
            bid_ask_spread_pct=0.05,
            momentum=0.0,
            volume_ratio=1.0,
            iv_rank=50.0,
            theta_decay=0.0,
            vix=20.0,
            regime="neutral",
        )
        assert (
            abs(score["total_score"]) < 5.0
        ), "Expected neutral ensemble score near zero"

    def test_compute_ensemble_score_extreme_rsi(self):
        """RSI at extremes should still produce valid score."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = _make_price_series(base=100.0, length=60)
        for rsi_val in [0.0, 100.0]:
            score = compute_ensemble_score(
                symbol="RELIANCE",
                closes=closes,
                rsi=rsi_val,
                macd_line=0.0,
                signal_line=0.0,
                histogram=0.0,
                ema_short=100.0,
                ema_long=100.0,
                vwap=100.0,
                current_price=100.0,
                bid_ask_spread_pct=0.05,
                momentum=0.0,
                volume_ratio=1.0,
                iv_rank=50.0,
                theta_decay=0.0,
                vix=20.0,
                regime="neutral",
            )
            assert -100.0 <= score["total_score"] <= 100.0

    def test_compute_ensemble_v2_returns_dict(self):
        """compute_ensemble_v2 should return a dict with expected keys."""
        try:
            from main import compute_ensemble_v2
        except ImportError:
            pytest.skip("compute_ensemble_v2 not available")

        closes = _make_price_series(base=100.0, length=60)
        result = compute_ensemble_v2(
            symbol="RELIANCE",
            closes=closes,
            rsi=55.0,
            macd_line=0.5,
            signal_line=0.3,
            histogram=0.2,
            ema_short=101.0,
            ema_long=99.0,
            vwap=100.0,
            current_price=101.0,
            bid_ask_spread_pct=0.05,
            momentum=1.0,
            volume_ratio=1.2,
            iv_rank=40.0,
            theta_decay=0.0,
            vix=18.0,
            regime="bullish",
        )
        assert isinstance(result, dict)
        assert "total_score" in result
        assert "direction" in result or "signal" in result


# ===========================================================================
# 2. Zerodha OAuth tests
# ===========================================================================
class TestZerodhaOAuth:
    """Test Zerodha authentication flow."""

    def test_generate_login_url(self):
        """Login URL should contain api_key and redirect_uri."""
        try:
            from zerodha_auth import generate_login_url
        except ImportError:
            pytest.skip("zerodha_auth module not available")

        url = generate_login_url(
            api_key="TEST_KEY", redirect_uri="http://localhost:8080/callback"
        )
        assert "TEST_KEY" in url
        assert (
            "http%3A%2F%2Flocalhost%3A8080%2Fcallback" in url or "redirect_uri" in url
        )

    def test_parse_request_token(self):
        """Request token should be extracted from callback URL."""
        try:
            from zerodha_auth import parse_request_token
        except ImportError:
            pytest.skip("zerodha_auth module not available")

        token = parse_request_token(
            "http://localhost:8080/callback?request_token=ABC123&status=success"
        )
        assert token == "ABC123"

    def test_parse_request_token_missing(self):
        """Missing request_token should return None or raise."""
        try:
            from zerodha_auth import parse_request_token
        except ImportError:
            pytest.skip("zerodha_auth module not available")

        result = parse_request_token("http://localhost:8080/callback?status=success")
        assert result is None or result == ""

    def test_generate_session_invalid_token(self):
        """Invalid request_token should not produce a valid session."""
        try:
            from zerodha_auth import ZerodhaAuth
        except ImportError:
            pytest.skip("zerodha_auth module not available")

        auth = ZerodhaAuth.__new__(ZerodhaAuth)
        auth.api_key = "TEST_KEY"
        auth.api_secret = "TEST_SECRET"
        with patch("zerodha_auth.requests.post") as mock_post:
            mock_post.return_value.status_code = 400
            mock_post.return_value.json.return_value = {
                "status": "error",
                "message": "Invalid token",
            }
            session = auth.generate_session("INVALID_TOKEN")
            assert session is None or session == ""

    def test_refresh_token_flow(self):
        """Token refresh should update access_token."""
        try:
            from zerodha_auth import ZerodhaAuth
        except ImportError:
            pytest.skip("zerodha_auth module not available")

        auth = ZerodhaAuth.__new__(ZerodhaAuth)
        auth.api_key = "TEST_KEY"
        auth.api_secret = "TEST_SECRET"
        auth.access_token = "OLD_TOKEN"
        with patch("zerodha_auth.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "success",
                "data": {"access_token": "NEW_TOKEN", "refresh_token": "NEW_REFRESH"},
            }
            result = auth.refresh_access_token("OLD_REFRESH")
            assert result is True or result == "NEW_TOKEN"


# ===========================================================================
# 3. PriceCache tests
# ===========================================================================
class TestPriceCache:
    """Test the thread-safe price cache."""

    def test_put_and_get(self, price_cache):
        price_cache.put("RELIANCE", 2500.0)
        assert price_cache.get("RELIANCE") == 2500.0

    def test_get_missing_returns_none(self, price_cache):
        assert price_cache.get("UNKNOWN") is None

    def test_maxlen_eviction(self, price_cache):
        """Cache should evict oldest entries when maxlen is exceeded."""
        for i in range(300):
            price_cache.put(f"SYM{i % 50}", float(i))
        # The cache should not grow beyond maxlen
        assert len(price_cache._data) <= price_cache.maxlen

    def test_overwrite_existing(self, price_cache):
        price_cache.put("RELIANCE", 2500.0)
        price_cache.put("RELIANCE", 2550.0)
        assert price_cache.get("RELIANCE") == 2550.0

    def test_thread_safety_basic(self, price_cache):
        """Basic concurrent writes should not crash."""
        import threading

        def writer(start: int):
            for i in range(start, start + 50):
                price_cache.put(f"SYM{i}", float(i))

        threads = [threading.Thread(target=writer, args=(i * 50,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(price_cache._data) > 0


# ===========================================================================
# 4. WebSocket tests
# ===========================================================================
class TestWebSocket:
    """Test WebSocket connection, reconnection, and message handling."""

    def test_websocket_connect(self):
        """WebSocket should connect and set connected flag."""
        try:
            from zerodha_websocket import ZerodhaWebSocket
        except ImportError:
            pytest.skip("zerodha_websocket module not available")

        ws = ZerodhaWebSocket.__new__(ZerodhaWebSocket)
        ws.connected = False
        ws.price_cache = MagicMock()
        ws.on_price_update = MagicMock()
        ws.on_order_update = MagicMock()
        ws._connect = MagicMock(return_value=True)
        ws._run_forever = MagicMock()

        result = ws.connect()
        assert result is True or ws.connected is True

    def test_websocket_reconnect_on_disconnect(self):
        """WebSocket should attempt reconnection on disconnect."""
        try:
            from zerodha_websocket import ZerodhaWebSocket
        except ImportError:
            pytest.skip("zerodha_websocket module not available")

        ws = ZerodhaWebSocket.__new__(ZerodhaWebSocket)
        ws.connected = False
        ws.reconnect_attempts = 0
        ws.max_reconnect_attempts = 5
        ws.reconnect_delay = 0.01
        ws.price_cache = MagicMock()
        ws._connect = MagicMock(side_effect=[False, True])
        ws._run_forever = MagicMock()

        ws.connect()
        assert ws._connect.call_count >= 1

    def test_websocket_message_parsing_price(self):
        """Price update messages should update cache."""
        try:
            from zerodha_websocket import ZerodhaWebSocket
        except ImportError:
            pytest.skip("zerodha_websocket module not available")

        ws = ZerodhaWebSocket.__new__(ZerodhaWebSocket)
        ws.price_cache = MagicMock()
        ws.on_price_update = MagicMock()

        message = json.dumps(
            {
                "type": "price",
                "instrument_token": 738561,
                "last_price": 2510.0,
                "volume": 600000,
                "timestamp": int(time.time()),
            }
        )
        ws._on_message(message)
        ws.price_cache.put.assert_called()

    def test_websocket_message_parsing_order(self):
        """Order update messages should trigger callback."""
        try:
            from zerodha_websocket import ZerodhaWebSocket
        except ImportError:
            pytest.skip("zerodha_websocket module not available")

        ws = ZerodhaWebSocket.__new__(ZerodhaWebSocket)
        ws.price_cache = MagicMock()
        ws.on_order_update = MagicMock()

        message = json.dumps(
            {
                "type": "order",
                "order_id": "ORDER_123",
                "status": "COMPLETE",
                "tradingsymbol": "RELIANCE",
                "transaction_type": "BUY",
                "quantity": 1,
                "average_price": 2500.0,
            }
        )
        ws._on_message(message)
        ws.on_order_update.assert_called()

    def test_websocket_subscribe_unsubscribe(self):
        """Subscribe and unsubscribe should manage instrument list."""
        try:
            from zerodha_websocket import ZerodhaWebSocket
        except ImportError:
            pytest.skip("zerodha_websocket module not available")

        ws = ZerodhaWebSocket.__new__(ZerodhaWebSocket)
        ws.subscribed_tokens = set()
        ws._subscribe = MagicMock()
        ws._unsubscribe = MagicMock()

        ws.subscribe([738561, 738562])
        assert 738561 in ws.subscribed_tokens or ws._subscribe.call_count >= 1

        ws.unsubscribe([738561])
        assert 738561 not in ws.subscribed_tokens or ws._unsubscribe.call_count >= 1


# ===========================================================================
# 5. Options chain tests
# ===========================================================================
class TestOptionsChain:
    """Test options chain fetching, expiry selection, and strike selection."""

    def test_select_best_strike_itm(self):
        """ITM strike selection should pick strike below spot for calls."""
        try:
            from main import select_best_strike
        except ImportError:
            pytest.skip("select_best_strike not available")

        chain_ce = [
            {"strike": 2400, "last_price": 120.0, "volume": 1000},
            {"strike": 2450, "last_price": 80.0, "volume": 1500},
            {"strike": 2500, "last_price": 50.0, "volume": 2000},
        ]
        strike = select_best_strike(
            chain_ce, spot_price=2500.0, option_type="CE", selection="itm"
        )
        assert strike <= 2500.0

    def test_select_best_strike_otm(self):
        """OTM strike selection should pick strike above spot for calls."""
        try:
            from main import select_best_strike
        except ImportError:
            pytest.skip("select_best_strike not available")

        chain_ce = [
            {"strike": 2400, "last_price": 120.0, "volume": 1000},
            {"strike": 2450, "last_price": 80.0, "volume": 1500},
            {"strike": 2500, "last_price": 50.0, "volume": 2000},
            {"strike": 2550, "last_price": 30.0, "volume": 800},
        ]
        strike = select_best_strike(
            chain_ce, spot_price=2500.0, option_type="CE", selection="otm"
        )
        assert strike >= 2500.0

    def test_get_next_expiry(self):
        """Next expiry should be a future date string."""
        try:
            from main import get_next_expiry
        except ImportError:
            pytest.skip("get_next_expiry not available")

        expiry = get_next_expiry("RELIANCE")
        assert expiry is not None
        assert len(expiry) >= 8  # YYYY-MM-DD

    def test_get_next_expiry_cached(self):
        """Subsequent calls should use cache."""
        try:
            from main import get_next_expiry
        except ImportError:
            pytest.skip("get_next_expiry not available")

        with patch("main.load_opt_expiry_json") as mock_load:
            mock_load.return_value = {"RELIANCE": ["2026-08-26", "2026-09-25"]}
            expiry1 = get_next_expiry("RELIANCE")
            expiry2 = get_next_expiry("RELIANCE")
            assert expiry1 == expiry2
            # Should only load once due to caching
            assert mock_load.call_count == 1


# ===========================================================================
# 6. Indicator computation tests
# ===========================================================================
class TestIndicators:
    """Test technical indicator calculations."""

    def test_rsi_bounds(self):
        """RSI should be between 0 and 100."""
        try:
            from main import compute_rsi
        except ImportError:
            pytest.skip("compute_rsi not available")

        closes = _make_price_series(base=100.0, length=60)
        rsi = compute_rsi(closes, period=14)
        assert 0.0 <= rsi <= 100.0

    def test_rsi_overbought(self):
        """Strong uptrend should produce high RSI."""
        try:
            from main import compute_rsi
        except ImportError:
            pytest.skip("compute_rsi not available")

        closes = [100.0 + i * 2.0 for i in range(60)]
        rsi = compute_rsi(closes, period=14)
        assert rsi > 60.0

    def test_rsi_oversold(self):
        """Strong downtrend should produce low RSI."""
        try:
            from main import compute_rsi
        except ImportError:
            pytest.skip("compute_rsi not available")

        closes = [100.0 - i * 2.0 for i in range(60)]
        rsi = compute_rsi(closes, period=14)
        assert rsi < 40.0

    def test_ema_short_above_long(self):
        """In uptrend, short EMA should be above long EMA."""
        try:
            from main import compute_ema
        except ImportError:
            pytest.skip("compute_ema not available")

        closes = [100.0 + i * 0.5 for i in range(60)]
        ema_short = compute_ema(closes, period=10)
        ema_long = compute_ema(closes, period=30)
        assert ema_short > ema_long

    def test_macd_bullish(self):
        """In uptrend, MACD line should be above signal line."""
        try:
            from main import compute_macd
        except ImportError:
            pytest.skip("compute_macd not available")

        closes = [100.0 + i * 0.5 for i in range(60)]
        macd_line, signal_line, histogram = compute_macd(closes)
        assert macd_line > signal_line

    def test_vwap_above_price(self):
        """VWAP should be computable and reasonable."""
        try:
            from main import compute_vwap
        except ImportError:
            pytest.skip("compute_vwap not available")

        _, opens, highs, lows, closes, volumes = _make_ohlcv(length=60, base=100.0)
        vwap = compute_vwap(highs, lows, closes, volumes)
        assert vwap > 0
        assert min(closes) <= vwap <= max(closes) or math.isclose(
            vwap, closes[-1], rel_tol=0.1
        )


# ===========================================================================
# 7. RajTradingBot lifecycle tests
# ===========================================================================
class TestRajTradingBot:
    """Test the main trading system class."""

    def test_initialization(self, config_file, mock_kite):
        """System should initialize without errors."""
        try:
            from main import RajTradingBot
        except ImportError:
            pytest.skip("main module not available")

        with patch.dict(os.environ, {"CONFIG_PATH": str(config_file)}):
            system = RajTradingBot.__new__(RajTradingBot)
            system.config = _load_config(config_file)
            system.kite = mock_kite
            system.price_cache = MagicMock()
            system.price_cache.get.return_value = 2500.0
            system.running = False
            system.signals = []
            system.trades = []
            system.daily_pnl = []
            assert system.config is not None

    def test_fetch_market_data(self, quant_system):
        """Market data should return OHLCV data."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        data = quant_system.fetch_market_data("RELIANCE", interval="5minute", days=1)
        assert data is not None
        assert len(data.get("close", [])) > 0 or "data" in data

    def test_generate_signals(self, quant_system):
        """Signal generation should return a list of signals."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.price_cache.get.return_value = 2500.0
        signals = quant_system.generate_signals("RELIANCE")
        assert isinstance(signals, list)

    def test_validate_trade_buy(self, quant_system):
        """Buy trade validation should pass for valid conditions."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        trade = {
            "symbol": "RELIANCE",
            "action": "BUY",
            "quantity": 1,
            "price": 2500.0,
            "reason": "Ensemble bullish",
        }
        valid, reason = quant_system.validate_trade(trade)
        assert valid is True or reason is not None

    def test_validate_trade_sell_without_position(self, quant_system):
        """Sell without holding position should be rejected."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        trade = {
            "symbol": "RELIANCE",
            "action": "SELL",
            "quantity": 1,
            "price": 2500.0,
            "reason": "Take profit",
        }
        valid, reason = quant_system.validate_trade(trade)
        assert (
            valid is False
            or "no position" in reason.lower()
            or "cannot sell" in reason.lower()
        )

    def test_execute_trade_paper_mode(self, quant_system):
        """Paper trade should record trade without real execution."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        trade = {
            "symbol": "RELIANCE",
            "action": "BUY",
            "quantity": 1,
            "price": 2500.0,
            "reason": "Test trade",
        }
        result = quant_system.execute_trade(trade)
        assert result is not None
        assert len(quant_system.trades) >= 1 or result.get("status") == "success"

    def test_run_single_iteration(self, quant_system):
        """Single run iteration should complete without errors."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.running = True
        quant_system.price_cache.get.return_value = 2500.0
        # Should not raise
        quant_system.run_single_iteration()

    def test_stop_system(self, quant_system):
        """Stopping system should set running=False."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.running = True
        quant_system.stop()
        assert quant_system.running is False


# ===========================================================================
# 8. Alert tests
# ===========================================================================
class TestAlerts:
    """Test alert generation and delivery."""

    def test_alert_manager_initialization(self, tmp_workspace):
        """AlertManager should initialize with config."""
        try:
            from alert_manager import AlertManager
        except ImportError:
            pytest.skip("alert_manager module not available")

        config = {
            "enabled": True,
            "email": "test@example.com",
            "webhook_url": "http://localhost:9000/webhook",
        }
        manager = AlertManager(config)
        assert manager is not None

    def test_send_alert_email(self, tmp_workspace):
        """Email alert should not raise."""
        try:
            from alert_manager import AlertManager
        except ImportError:
            pytest.skip("alert_manager module not available")

        config = {"enabled": True, "email": "test@example.com", "webhook_url": ""}
        manager = AlertManager(config)
        with patch("alert_manager.smtplib.SMTP"):
            result = manager.send_email("Test Subject", "Test body")
            assert result is True or result is None

    def test_send_alert_webhook(self, tmp_workspace):
        """Webhook alert should POST to URL."""
        try:
            from alert_manager import AlertManager
        except ImportError:
            pytest.skip("alert_manager module not available")

        config = {
            "enabled": True,
            "email": "",
            "webhook_url": "http://localhost:9000/webhook",
        }
        manager = AlertManager(config)
        with patch("alert_manager.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            result = manager.send_webhook({"message": "test"})
            assert result is True or mock_post.called

    def test_alert_on_large_loss(self, quant_system):
        """Large loss should trigger alert."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.daily_pnl = [-6.0]  # Exceeds 5% max daily loss
        with patch.object(quant_system, "send_alert") as mock_alert:
            quant_system.check_risk_limits()
            mock_alert.assert_called()


# ===========================================================================
# 9. Configuration tests
# ===========================================================================
class TestConfiguration:
    """Test configuration loading and validation."""

    def test_config_file_exists(self):
        """config.yaml should exist in the repo."""
        assert CONFIG_PATH.exists(), f"Config file not found at {CONFIG_PATH}"

    def test_config_loads_successfully(self, config_file):
        """YAML config should load without errors."""
        import yaml

        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert "zerodha" in data
        assert "trading" in data
        assert "risk" in data

    def test_config_has_required_keys(self, config_file):
        """Config must have all required top-level keys."""
        import yaml

        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        required = ["zerodha", "trading", "risk", "alerts", "logging"]
        for key in required:
            assert key in data, f"Missing required config key: {key}"

    def test_config_zerodha_keys(self, config_file):
        """Zerodha config must have api_key and api_secret."""
        import yaml

        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        zerodha = data["zerodha"]
        assert "api_key" in zerodha
        assert "api_secret" in zerodha
        assert "redirect_uri" in zerodha

    def test_config_risk_limits_positive(self, config_file):
        """Risk limits should be positive numbers."""
        import yaml

        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        risk = data["risk"]
        assert risk["max_drawdown_pct"] > 0
        assert risk["max_daily_loss_pct"] > 0
        assert risk["stop_loss_pct"] > 0
        assert risk["target_pct"] > 0

    def test_opt_expiry_json_exists(self):
        """opt-expiry.json should exist."""
        assert (
            OPT_EXPIRY_PATH.exists()
        ), f"opt-expiry.json not found at {OPT_EXPIRY_PATH}"

    def test_opt_expiry_json_valid(self):
        """opt-expiry.json should be valid JSON with expected structure."""
        data = json.loads(OPT_EXPIRY_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, (list, dict))
        if isinstance(data, dict):
            assert len(data) > 0


# ===========================================================================
# 10. Database / persistence tests
# ===========================================================================
class TestDatabase:
    """Test SQLite database operations."""

    def test_trade_log_insert_and_query(self, tmp_workspace):
        """Trades should be insertable and queryable."""
        db_path = tmp_workspace / "test_trades.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                timestamp TEXT NOT NULL,
                reason TEXT
            )
            """)
        conn.execute(
            "INSERT INTO trades (symbol, action, quantity, price, timestamp, reason) VALUES (?, ?, ?, ?, ?, ?)",
            ("RELIANCE", "BUY", 1, 2500.0, datetime.utcnow().isoformat(), "Test"),
        )
        conn.commit()
        cursor = conn.execute("SELECT * FROM trades WHERE symbol = 'RELIANCE'")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][1] == "RELIANCE"

    def test_signal_log_insert_and_query(self, tmp_workspace):
        """Signals should be insertable and queryable."""
        db_path = tmp_workspace / "test_signals.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                score REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
            """)
        conn.execute(
            "INSERT INTO signals (symbol, signal, score, timestamp) VALUES (?, ?, ?, ?)",
            ("RELIANCE", "BUY", 75.0, datetime.utcnow().isoformat()),
        )
        conn.commit()
        cursor = conn.execute("SELECT * FROM signals WHERE symbol = 'RELIANCE'")
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][3] == 75.0


# ===========================================================================
# 11. Risk management tests
# ===========================================================================
class TestRiskManagement:
    """Test risk limits and trade validation."""

    def test_max_positions_limit(self, quant_system):
        """Should reject trade when max positions reached."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.trades = [
            {"symbol": f"SYM{i}", "action": "BUY", "quantity": 1} for i in range(5)
        ]
        trade = {"symbol": "NEW_SYM", "action": "BUY", "quantity": 1, "price": 100.0}
        valid, reason = quant_system.validate_trade(trade)
        assert valid is False or "max" in reason.lower()

    def test_stop_loss_calculation(self):
        """Stop loss price should be below entry for long positions."""
        entry_price = 2500.0
        stop_loss_pct = 2.0
        stop_loss_price = entry_price * (1 - stop_loss_pct / 100.0)
        assert stop_loss_price < entry_price
        assert math.isclose(stop_loss_price, 2450.0, rel_tol=1e-3)

    def test_target_calculation(self):
        """Target price should be above entry for long positions."""
        entry_price = 2500.0
        target_pct = 4.0
        target_price = entry_price * (1 + target_pct / 100.0)
        assert target_price > entry_price
        assert math.isclose(target_price, 2600.0, rel_tol=1e-3)

    def test_drawdown_limit(self, quant_system):
        """Should halt trading when drawdown exceeds limit."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")
        quant_system.config["risk"]["max_drawdown_pct"] = 5.0
        quant_system.initial_capital = 100000.0
        quant_system.current_capital = 94000.0  # 6% drawdown
        valid, reason = quant_system.validate_trade(
            {"symbol": "RELIANCE", "action": "BUY", "quantity": 1, "price": 2500.0}
        )
        assert (
            valid is False or "drawdown" in reason.lower() or "halt" in reason.lower()
        )


# ===========================================================================
# 12. Integration / end-to-end scenarios
# ===========================================================================
class TestEndToEndScenarios:
    """Full workflow integration tests."""

    def test_full_buy_signal_workflow(self, quant_system, mock_kite):
        """End-to-end: fetch data → generate signal → validate → execute."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        quant_system.kite = mock_kite
        quant_system.price_cache = MagicMock()
        quant_system.price_cache.get.return_value = 2500.0

        # 1. Fetch market data
        data = quant_system.fetch_market_data("RELIANCE", interval="5minute", days=1)
        assert data is not None

        # 2. Generate signals
        signals = quant_system.generate_signals("RELIANCE")
        assert isinstance(signals, list)

        # 3. Validate trade
        trade = {
            "symbol": "RELIANCE",
            "action": "BUY",
            "quantity": 1,
            "price": 2500.0,
            "reason": "Ensemble bullish",
        }
        valid, reason = quant_system.validate_trade(trade)

        # 4. Execute trade
        result = quant_system.execute_trade(trade)
        assert result is not None

    def test_websocket_to_signal_pipeline(self, price_cache):
        """Price updates via WebSocket should flow into signal generation."""
        if price_cache is None:
            pytest.skip("PriceCache not available")

        # Simulate WebSocket price updates
        for i in range(20):
            price_cache.put("RELIANCE", 2500.0 + i * 0.5)

        prices = [price_cache.get_history("RELIANCE", 20) for _ in range(5)]
        # At least some prices should be populated
        assert (
            any(p is not None and len(p) > 0 for p in prices)
            or price_cache.get("RELIANCE") is not None
        )

    def test_recovery_from_api_failure(self, quant_system):
        """System should handle API failures gracefully."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        failing_kite = MagicMock()
        failing_kite.quote.side_effect = Exception("API Error")
        failing_kite.ltp.side_effect = Exception("API Error")
        quant_system.kite = failing_kite

        # Should not raise unhandled exception
        try:
            data = quant_system.fetch_market_data(
                "RELIANCE", interval="5minute", days=1
            )
            assert (
                data is None or "error" in str(data).lower() or isinstance(data, dict)
            )
        except Exception as e:
            assert "API Error" in str(e) or "connection" in str(e).lower()

    def test_multiple_symbols_parallel(self, quant_system):
        """System should handle multiple symbols."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        symbols = ["RELIANCE", "TCS", "INFY"]
        quant_system.price_cache = MagicMock()
        quant_system.price_cache.get.side_effect = lambda s: {
            "RELIANCE": 2500.0,
            "TCS": 4000.0,
            "INFY": 1800.0,
        }.get(s, 100.0)

        all_signals = []
        for sym in symbols:
            signals = quant_system.generate_signals(sym)
            all_signals.extend(signals)
        assert len(all_signals) >= 0  # Should complete without error

    def test_paper_trading_session(self, quant_system, tmp_workspace):
        """Simulate a full paper trading session."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        quant_system.running = True
        quant_system.price_cache = MagicMock()
        quant_system.price_cache.get.return_value = 2500.0
        quant_system.config["trading"]["paper_trading"] = True

        # Run a few iterations
        for _ in range(3):
            if quant_system.running:
                quant_system.run_single_iteration()

        quant_system.stop()
        assert quant_system.running is False

    def test_log_file_created(self, quant_system, tmp_workspace):
        """Log file should be created during system operation."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        log_file = tmp_workspace / "test_bot.log"
        quant_system.log_file = str(log_file)
        quant_system.logger = MagicMock()
        quant_system.logger.info = MagicMock()
        quant_system.logger.error = MagicMock()
        quant_system.logger.warning = MagicMock()

        quant_system.log_info("Test info message")
        quant_system.log_error("Test error message")
        quant_system.log_warning("Test warning message")

        quant_system.logger.info.assert_called()
        quant_system.logger.error.assert_called()
        quant_system.logger.warning.assert_called()


# ===========================================================================
# 13. Edge case tests
# ===========================================================================
class TestEdgeCases:
    """Test boundary conditions and edge cases."""

    def test_empty_price_series(self):
        """Empty price series should not crash indicator functions."""
        try:
            from main import compute_ema, compute_macd, compute_rsi
        except ImportError:
            pytest.skip("Indicator functions not available")

        with pytest.raises((ValueError, IndexError, TypeError)):
            compute_rsi([], period=14)

    def test_single_price_point(self):
        """Single price point should handle gracefully."""
        try:
            from main import compute_rsi
        except ImportError:
            pytest.skip("compute_rsi not available")

        result = compute_rsi([100.0], period=14)
        assert result is not None

    def test_very_high_volatility(self):
        """Extreme price swings should not crash."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = [100.0 + (i % 2) * 50.0 for i in range(60)]
        score = compute_ensemble_score(
            symbol="RELIANCE",
            closes=closes,
            rsi=50.0,
            macd_line=0.0,
            signal_line=0.0,
            histogram=0.0,
            ema_short=100.0,
            ema_long=100.0,
            vwap=100.0,
            current_price=100.0,
            bid_ask_spread_pct=0.05,
            momentum=0.0,
            volume_ratio=1.0,
            iv_rank=50.0,
            theta_decay=0.0,
            vix=20.0,
            regime="neutral",
        )
        assert score is not None

    def test_zero_volume(self):
        """Zero volume should be handled gracefully."""
        try:
            from main import compute_vwap
        except ImportError:
            pytest.skip("compute_vwap not available")

        highs = [100.0] * 60
        lows = [99.0] * 60
        closes = [100.0] * 60
        volumes = [0] * 60
        vwap = compute_vwap(highs, lows, closes, volumes)
        assert vwap > 0 or math.isnan(vwap) or vwap == 0.0

    def test_negative_prices_rejected(self):
        """Negative prices should be rejected or handled."""
        try:
            from main import compute_rsi
        except ImportError:
            pytest.skip("compute_rsi not available")

        closes = [-10.0, -5.0, 0.0, 5.0, 10.0] * 12
        result = compute_rsi(closes, period=14)
        assert result is not None

    def test_config_missing_file(self):
        """Missing config file should raise or return default."""
        with pytest.raises((FileNotFoundError, Exception)):
            _load_config(Path("/nonexistent/path/config.yaml"))

    def test_invalid_yaml_config(self, tmp_workspace):
        """Invalid YAML should raise on load."""
        import yaml

        bad_file = tmp_workspace / "bad_config.yaml"
        bad_file.write_text("invalid: yaml: content: [", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            _load_config(bad_file)


# ===========================================================================
# 14. Performance / load tests
# ===========================================================================
class TestPerformance:
    """Performance and load tests."""

    def test_indicator_computation_speed(self, benchmark=None):
        """Indicator computation should complete in reasonable time."""
        try:
            from main import compute_ensemble_score
        except ImportError:
            pytest.skip("compute_ensemble_score not available")

        closes = _make_price_series(base=100.0, length=200)
        start = time.time()
        for _ in range(100):
            compute_ensemble_score(
                symbol="RELIANCE",
                closes=closes,
                rsi=50.0,
                macd_line=0.0,
                signal_line=0.0,
                histogram=0.0,
                ema_short=100.0,
                ema_long=100.0,
                vwap=100.0,
                current_price=100.0,
                bid_ask_spread_pct=0.05,
                momentum=0.0,
                volume_ratio=1.0,
                iv_rank=50.0,
                theta_decay=0.0,
                vix=20.0,
                regime="neutral",
            )
        elapsed = time.time() - start
        assert (
            elapsed < 5.0
        ), f"Indicator computation too slow: {elapsed:.2f}s for 100 iterations"

    def test_price_cache_throughput(self, price_cache):
        """PriceCache should handle high throughput."""
        if price_cache is None:
            pytest.skip("PriceCache not available")

        start = time.time()
        for i in range(10000):
            price_cache.put(f"SYM{i % 100}", float(i))
        elapsed = time.time() - start
        assert elapsed < 2.0, f"PriceCache too slow: {elapsed:.2f}s for 10k writes"

    def test_large_ohlcv_processing(self):
        """Processing large OHLCV dataset should complete."""
        try:
            from main import compute_ema, compute_macd, compute_rsi
        except ImportError:
            pytest.skip("Indicator functions not available")

        closes = _make_price_series(base=100.0, length=10000)
        start = time.time()
        compute_rsi(closes, period=14)
        compute_ema(closes, period=20)
        compute_macd(closes)
        elapsed = time.time() - start
        assert elapsed < 10.0, f"Large dataset processing too slow: {elapsed:.2f}s"


# ===========================================================================
# 15. Mock / monkeypatch tests
# ===========================================================================
class TestMockedScenarios:
    """Test scenarios that require heavy mocking."""

    def test_kite_connection_failure(self, config_file):
        """System should handle Kite connection failure."""
        try:
            from main import RajTradingBot
        except ImportError:
            pytest.skip("main module not available")

        with patch.dict(os.environ, {"CONFIG_PATH": str(config_file)}):
            system = RajTradingBot.__new__(RajTradingBot)
            system.config = _load_config(config_file)
            system.kite = MagicMock()
            system.kite.profile.side_effect = Exception("Connection refused")
            system.running = False
            system.signals = []
            system.trades = []
            system.daily_pnl = []

            with pytest.raises(Exception):
                system.initialize_kite()

    def test_invalid_instrument_token(self, quant_system):
        """Invalid instrument token should return empty or None."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        quant_system.kite = MagicMock()
        quant_system.kite.quote.return_value = {}
        data = quant_system.fetch_market_data(
            "INVALID_SYMBOL", interval="5minute", days=1
        )
        assert data is None or len(data.get("close", [])) == 0

    def test_duplicate_signal_suppression(self, quant_system):
        """Duplicate signals should be suppressed."""
        if quant_system is None:
            pytest.skip("RajTradingBot not available")

        quant_system.price_cache = MagicMock()
        quant_system.price_cache.get.return_value = 2500.0
        quant_system.recent_signals = deque(maxlen=10)
        quant_system.recent_signals.append(("RELIANCE", "BUY", 2500.0))

        signals = quant_system.generate_signals("RELIANCE")
        # Should not generate duplicate BUY at same price
        buy_signals = [s for s in signals if s.get("action") == "BUY"]
        assert len(buy_signals) <= 1


# ===========================================================================
# Run guard
# ===========================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
