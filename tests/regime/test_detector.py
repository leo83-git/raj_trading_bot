"""Tests for intelligence/regime/detector module."""

import numpy as np
import pytest

from intelligence.regime.detector import (
    MarketRegime,
    RegimeDetector,
    _calculate_cumulative_return,
    _calculate_log_returns,
    _calculate_mean_reversion,
    _calculate_trend_strength,
    _calculate_volatility,
    _classify_regime,
    _preprocess_candles,
    detect_market_regime,
)


class TestPreprocessCandles:
    """Tests for _preprocess_candles function."""

    def test_normal_candles(self):
        """Normal candles should be returned as-is."""
        candles = [{"close": 100.0 + i} for i in range(25)]
        result = _preprocess_candles(candles)
        assert len(result) == 25
        assert result[0] == 100.0

    def test_empty_candles(self):
        """Empty candles list should return empty array."""
        result = _preprocess_candles([])
        assert len(result) == 0

    def test_insufficient_candles(self):
        """Less than 20 candles should return empty array."""
        candles = [{"close": 100.0 + i} for i in range(15)]
        result = _preprocess_candles(candles)
        assert len(result) == 0


class TestLogReturns:
    """Tests for _calculate_log_returns function."""

    def test_normal_returns(self):
        """Normal prices should produce finite log returns."""
        closes = np.array([100.0, 101.0, 102.0, 103.0])
        returns = _calculate_log_returns(closes)
        assert len(returns) == 3
        assert all(np.isfinite(returns))

    def test_single_price(self):
        """Single price should return empty array."""
        closes = np.array([100.0])
        returns = _calculate_log_returns(closes)
        assert len(returns) == 0


class TestCumulativeReturn:
    """Tests for _calculate_cumulative_return function."""

    def test_neutral_return(self):
        """No change should return 0.5."""
        closes = np.array([100.0, 100.0])
        result = _calculate_cumulative_return(closes)
        assert result == 0.5

    def test_positive_return(self):
        """Positive return should return > 0.5."""
        closes = np.array([100.0, 110.0])
        result = _calculate_cumulative_return(closes)
        assert result > 0.5

    def test_negative_return(self):
        """Negative return should return < 0.5."""
        closes = np.array([100.0, 90.0])
        result = _calculate_cumulative_return(closes)
        assert result < 0.5


class TestTrendStrength:
    """Tests for _calculate_trend_strength function."""

    def test_strong_upward_trend(self):
        """Strong upward trend should have high strength."""
        closes = np.array([100.0] + [100.0 * (1.005**i) for i in range(1, 50)])
        returns = _calculate_log_returns(closes)
        result = _calculate_trend_strength(closes, returns)
        assert result > 0.55

    def test_strong_downward_trend(self):
        """Strong downward trend should have low strength."""
        closes = np.array([100.0] + [100.0 * (0.995**i) for i in range(1, 50)])
        returns = _calculate_log_returns(closes)
        result = _calculate_trend_strength(closes, returns)
        assert result < 0.45

    def test_insufficient_data(self):
        """Insufficient data should return 0.5."""
        closes = np.array([100.0])
        returns = np.array([])
        result = _calculate_trend_strength(closes, returns)
        assert result == 0.5


class TestMeanReversion:
    """Tests for _calculate_mean_reversion function."""

    def test_insufficient_data(self):
        """Insufficient data should return 0.5."""
        closes = np.array([100.0])
        returns = np.array([])
        result = _calculate_mean_reversion(closes, returns)
        assert result == 0.5


class TestVolatility:
    """Tests for _calculate_volatility function."""

    def test_high_volatility(self):
        """High volatility should return high value."""
        closes = np.array([100.0])
        for i in range(100):
            closes = np.append(
                closes, closes[-1] * (1 + np.random.uniform(-0.05, 0.05))
            )
        returns = _calculate_log_returns(closes)
        result = _calculate_volatility(returns)
        assert result > 0.2

    def test_insufficient_data(self):
        """Insufficient data should return 0.15."""
        returns = np.array([0.001])
        result = _calculate_volatility(returns)
        assert result == 0.15


class TestClassifyRegime:
    """Tests for _classify_regime function."""

    def test_trending_up(self):
        """High trend should be TRENDING_UP."""
        result = _classify_regime(
            trend_strength=0.7, mean_reversion_score=0.3, volatility=0.15
        )
        assert result == "TRENDING_UP"

    def test_trending_down(self):
        """Low trend should be TRENDING_DOWN."""
        result = _classify_regime(
            trend_strength=0.3, mean_reversion_score=0.4, volatility=0.15
        )
        assert result == "TRENDING_DOWN"

    def test_mean_reverting(self):
        """High mean reversion should be MEAN_REVERTING."""
        result = _classify_regime(
            trend_strength=0.5, mean_reversion_score=0.7, volatility=0.15
        )
        assert result == "MEAN_REVERTING"

    def test_high_volatility(self):
        """High volatility should be HIGH_VOLATILITY."""
        result = _classify_regime(
            trend_strength=0.5, mean_reversion_score=0.5, volatility=0.4
        )
        assert result == "HIGH_VOLATILITY"

    def test_low_volatility(self):
        """Low volatility should be LOW_VOLATILITY."""
        result = _classify_regime(
            trend_strength=0.5, mean_reversion_score=0.5, volatility=0.05
        )
        assert result == "LOW_VOLATILITY"


class TestDetectorIntegration:
    """Integration tests for RegimeDetector class."""

    def test_trending_up_regime(self):
        """Strong upward trend should be detected."""
        candles = [{"close": 100.0 * (1.005**i)} for i in range(100)]
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert result.trend_strength > 0.5

    def test_trending_down_regime(self):
        """Strong downward trend should be detected."""
        candles = [{"close": 100.0 * (0.995**i)} for i in range(100)]
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert result.trend_strength < 0.5

    def test_high_volatility_regime(self):
        """High volatility should be detected as HIGH_VOLATILITY."""
        candles = [
            {"close": 100.0 * (1 + np.random.uniform(-0.08, 0.08))} for i in range(100)
        ]
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert result.regime == "HIGH_VOLATILITY"

    def test_low_volatility_regime(self):
        """Low volatility should be detected as LOW_VOLATILITY."""
        candles = [
            {"close": 100.0 * (1 + np.random.uniform(-0.001, 0.001))}
            for i in range(100)
        ]
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert result.regime == "LOW_VOLATILITY"

    def test_insufficient_candles(self):
        """Insufficient candles should return default SIDEWAYS."""
        candles = [{"close": 100.0 + i} for i in range(15)]
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert result.regime == "SIDEWAYS"

    def test_handle_zero_prices(self):
        """Detector should handle candles with zero prices."""
        candles = [{"close": 0}, {"close": 100.0}]
        for i in range(100):
            candles.append({"close": 100.0 + i * 0.5})
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert isinstance(result, MarketRegime)

    def test_handle_negative_prices(self):
        """Detector should handle candles with negative prices."""
        candles = [{"close": -5}, {"close": 100.0}]
        for i in range(100):
            candles.append({"close": 100.0 + i * 0.5})
        detector = RegimeDetector()
        result = detector.detect_regime(candles)
        assert isinstance(result, MarketRegime)


class TestDetectMarketRegime:
    """Tests for detect_market_regime convenience function."""

    def test_convenience_function(self):
        """Function should return valid MarketRegime."""
        candles = [{"close": 100.0 * (1.005**i)} for i in range(50)]
        result = detect_market_regime(candles)
        assert isinstance(result, MarketRegime)
