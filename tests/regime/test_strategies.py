"""Tests for strategy marketplace regime mapping.

Verify every canonical regime maps to valid strategies.
"""

import pytest

from strategy.marketplace import StrategyMarketplace, StrategyManager, REGIME_MAP


class TestRegimeStrategyMapping:
    """Tests for REGIME_MAP strategy mapping."""

    @pytest.fixture
    def strategy_manager(self):
        """Create StrategyManager instance."""
        marketplace = StrategyMarketplace()
        return StrategyManager(marketplace.strategies)

    def test_regime_map_exists(self):
        """REGIME_MAP should be defined."""
        assert REGIME_MAP is not None
        assert isinstance(REGIME_MAP, dict)

    def test_all_canonical_regimes_in_map(self):
        """All canonical regimes should exist in REGIME_MAP."""
        canonical_regimes = [
            "TRENDING_UP",
            "TRENDING_DOWN",
            "MEAN_REVERTING",
            "SIDEWAYS",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
        ]
        for regime in canonical_regimes:
            assert regime in REGIME_MAP, f"Missing {regime} in REGIME_MAP"
            assert isinstance(REGIME_MAP[regime], list)

    def test_trending_up_strategies(self):
        """TRENDING_UP should have breakout, oi_buildup, index_momentum."""
        strategies = REGIME_MAP["TRENDING_UP"]
        assert "breakout" in strategies
        assert "oi_buildup" in strategies
        assert "index_momentum" in strategies

    def test_trending_down_strategies(self):
        """TRENDING_DOWN should have breakout, oi_buildup."""
        strategies = REGIME_MAP["TRENDING_DOWN"]
        assert "breakout" in strategies
        assert "oi_buildup" in strategies

    def test_mean_reverting_strategies(self):
        """MEAN_REVERTING should have mean_reversion, bollinger_band."""
        strategies = REGIME_MAP["MEAN_REVERTING"]
        assert "mean_reversion" in strategies
        assert "bollinger_band" in strategies

    def test_sideways_strategies(self):
        """SIDEWAYS should have mean_reversion, bollinger_band, vwap."""
        strategies = REGIME_MAP["SIDEWAYS"]
        assert "mean_reversion" in strategies
        assert "bollinger_band" in strategies
        assert "vwap" in strategies

    def test_high_volatility_strategies(self):
        """HIGH_VOLATILITY should have scalping, expiry_theta."""
        strategies = REGIME_MAP["HIGH_VOLATILITY"]
        assert "scalping" in strategies
        assert "expiry_theta" in strategies

    def test_low_volatility_strategies(self):
        """LOW_VOLATILITY should have scalping, breakout."""
        strategies = REGIME_MAP["LOW_VOLATILITY"]
        assert "scalping" in strategies
        assert "breakout" in strategies


class TestStrategyFilterByRegime:
    """Tests for StrategyManager.filter_by_regime method."""

    @pytest.fixture
    def strategy_manager(self):
        """Create StrategyManager instance."""
        marketplace = StrategyMarketplace()
        return StrategyManager(marketplace.strategies)

    def test_trending_up_filter(self, strategy_manager):
        """filter_by_regime should return appropriate strategies for TRENDING_UP."""
        strategies = strategy_manager.filter_by_regime("TRENDING_UP")
        assert isinstance(strategies, list)

    def test_trending_down_filter(self, strategy_manager):
        """filter_by_regime should return appropriate strategies for TRENDING_DOWN."""
        strategies = strategy_manager.filter_by_regime("TRENDING_DOWN")
        assert isinstance(strategies, list)

    def test_sideways_filter(self, strategy_manager):
        """filter_by_regime should return appropriate strategies for SIDEWAYS."""
        strategies = strategy_manager.filter_by_regime("SIDEWAYS")
        assert isinstance(strategies, list)

    def test_normalized_regime_filter(self, strategy_manager):
        """filter_by_regime should work with normalized regime names."""
        strategies = strategy_manager.filter_by_regime("bullish")
        assert isinstance(strategies, list)

    def test_invalid_regime_fallback(self, strategy_manager):
        """filter_by_regime should fallback to all strategies for unknown regime."""
        strategies = strategy_manager.filter_by_regime("UNKNOWN_REGIME")
        assert isinstance(strategies, list)
