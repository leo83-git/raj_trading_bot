"""Tests for utils/zerodha_regime module.

Tests mock scenarios for ZerodhaRegimeDetector:
- Insufficient candles
- Flat market
- Bullish market
- Bearish market
"""

import pytest
from unittest.mock import MagicMock, patch

from utils.zerodha_regime import ZerodhaRegimeDetector


class TestZerodhaRegimeDetector:
    """Tests for ZerodhaRegimeDetector class."""

    def test_insufficient_candles(self):
        """Returns NEUTRAL with low confidence when insufficient candles."""
        broker = MagicMock()
        broker.get_candles.return_value = []

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "NEUTRAL"
        assert result["sentiment"] == "NEUTRAL"
        assert result["confidence"] == 0.3
        assert "insufficient candle data" in result["drivers"]

    def test_flat_market(self):
        """Returns NEUTRAL when price change is within threshold."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 100.1},
            {"open": 100.1, "close": 100.2},
            {"open": 100.2, "close": 100.15},
            {"open": 100.15, "close": 100.25},
            {"open": 100.25, "close": 100.3},
        ]

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "NEUTRAL"
        assert result["direction"] == "NEUTRAL"
        assert "price_change=" in result["drivers"][0]

    def test_bullish_market(self):
        """Returns BULLISH when price change > 0.5%."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 100.5},
            {"open": 100.5, "close": 101.0},
            {"open": 101.0, "close": 101.5},
            {"open": 101.5, "close": 102.0},
            {"open": 102.0, "close": 102.5},
        ]

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "BULLISH"
        assert result["sentiment"] == "BULLISH"
        assert result["confidence"] > 0.5
        assert "price_change=" in result["drivers"][0]

    def test_bearish_market(self):
        """Returns BEARISH when price change < -0.5%."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 99.5},
            {"open": 99.5, "close": 99.0},
            {"open": 99.0, "close": 98.5},
            {"open": 98.5, "close": 98.0},
            {"open": 98.0, "close": 97.5},
        ]

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "BEARISH"
        assert result["sentiment"] == "BEARISH"
        assert result["confidence"] > 0.5

    def test_caching(self):
        """Results are cached for cache_ttl seconds."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 101.0},
        ] * 5

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=300)
        result1 = detector.detect_regime("NIFTY")
        result2 = detector.detect_regime("NIFTY")

        assert result1["direction"] == result2["direction"]
        broker.get_candles.assert_called_once()

    def test_symbol_mapping(self):
        """Maps generic symbols to Zerodha tradingsymbols."""
        broker = MagicMock()
        broker.INDEX_SYMBOL_MAP = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 100.5},
        ] * 5

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        detector.detect_regime("NIFTY")

        # Should use mapped symbol
        broker.get_candles.assert_called_once()
        call_args = broker.get_candles.call_args
        assert call_args[1]["symbol"] == "NIFTY 50"


class TestZerodhaRegimeNormalization:
    """Tests for normalization of Zerodha regime results."""

    def test_bullish_normalized(self):
        """BULLISH should be normalized."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 102.0},
        ] * 5

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "BULLISH"

    def test_bearish_normalized(self):
        """BEARISH should be normalized."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 98.0},
        ] * 5

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "BEARISH"

    def test_neutral_normalized(self):
        """NEUTRAL should be normalized."""
        broker = MagicMock()
        broker.get_candles.return_value = [
            {"open": 100.0, "close": 100.1},
        ] * 5

        detector = ZerodhaRegimeDetector(broker=broker, cache_ttl=0)
        result = detector.detect_regime("NIFTY")

        assert result["direction"] == "NEUTRAL"
