"""Tests for intelligence/regime/vocabulary module."""

import pytest

from intelligence.regime.vocabulary import (
    CANONICAL_REGIMES,
    REGIME_ALIASES,
    normalize_regime,
)


class TestCanonicalRegimes:
    """Tests for CANONICAL_REGIMES constant."""

    def test_has_all_required_regimes(self):
        """Canonical regimes must include all required types."""
        required = {
            "TRENDING_UP",
            "TRENDING_DOWN",
            "MEAN_REVERTING",
            "SIDEWAYS",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
        }
        assert required.issubset(CANONICAL_REGIMES)

    def test_is_frozenset(self):
        """CANONICAL_REGIMES must be an immutable frozenset."""
        assert isinstance(CANONICAL_REGIMES, frozenset)


class TestRegimeAliases:
    """Tests for REGIME_ALIASES mapping."""

    def test_bullish_alias_maps_to_trending_up(self):
        """'bullish' should map to 'TRENDING_UP'."""
        assert REGIME_ALIASES.get("bullish") == "TRENDING_UP"

    def test_bearish_alias_maps_to_trending_down(self):
        """'bearish' should map to 'TRENDING_DOWN'."""
        assert REGIME_ALIASES.get("bearish") == "TRENDING_DOWN"

    def test_aliases_are_stored_lowercase(self):
        """Aliases should be stored in lowercase for case-insensitive matching."""
        assert "bullish" in REGIME_ALIASES
        assert "bearish" in REGIME_ALIASES


class TestNormalizeRegime:
    """Tests for normalize_regime function."""

    def test_canonical_regime_returns_unchanged(self):
        """Canonical regimes should return themselves."""
        assert normalize_regime("TRENDING_UP") == "TRENDING_UP"
        assert normalize_regime("TRENDING_DOWN") == "TRENDING_DOWN"
        assert normalize_regime("SIDEWAYS") == "SIDEWAYS"

    def test_alias_conversion(self):
        """Aliases should be converted to canonical labels."""
        assert normalize_regime("bullish") == "TRENDING_UP"
        assert normalize_regime("bearish") == "TRENDING_DOWN"
        assert normalize_regime("mean_reversion") == "MEAN_REVERTING"

    def test_unknown_label_returns_default(self):
        """Unknown regime labels should default to SIDEWAYS."""
        result = normalize_regime("unknown_regime")
        assert result == "SIDEWAYS"

    def test_none_input_returns_default(self):
        """None input should default to SIDEWAYS."""
        assert normalize_regime(None) == "SIDEWAYS"

    def test_lowercase_input_normalized(self):
        """Lowercase input should be normalized."""
        assert normalize_regime("trending_up") == "TRENDING_UP"

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace should be trimmed."""
        assert normalize_regime("  SIDEWAYS  ") == "SIDEWAYS"
        assert normalize_regime("   BULLISH   ") == "TRENDING_UP"

    def test_case_insensitive(self):
        """Input should be case-insensitive."""
        assert normalize_regime("bullish") == "TRENDING_UP"
        assert normalize_regime("BULLISH") == "TRENDING_UP"
        assert normalize_regime("BuLlIsH") == "TRENDING_UP"

    def test_all_aliases_work(self):
        """Test all defined aliases work correctly."""
        aliases_to_expected = {
            # Trending up aliases
            "bullish": "TRENDING_UP",
            "upward": "TRENDING_UP",
            "up": "TRENDING_UP",
            "bull": "TRENDING_UP",
            "rising": "TRENDING_UP",
            "growth": "TRENDING_UP",
            # Trending down aliases
            "bearish": "TRENDING_DOWN",
            "downward": "TRENDING_DOWN",
            "down": "TRENDING_DOWN",
            "bear": "TRENDING_DOWN",
            "falling": "TRENDING_DOWN",
            "decline": "TRENDING_DOWN",
            # Mean reverting aliases
            "mean_reversion": "MEAN_REVERTING",
            "reversion": "MEAN_REVERTING",
            "oscillating": "MEAN_REVERTING",
            "range": "MEAN_REVERTING",
            # Sideways aliases
            "rangebound": "SIDEWAYS",
            "ranging": "SIDEWAYS",
            "flat": "SIDEWAYS",
            "neutral": "SIDEWAYS",
            # Volatility aliases
            "volatile": "HIGH_VOLATILITY",
            "high_vol": "HIGH_VOLATILITY",
            "quiet": "LOW_VOLATILITY",
            "low_vol": "LOW_VOLATILITY",
            "calm": "LOW_VOLATILITY",
            "stable": "LOW_VOLATILITY",
        }
        for alias, expected in aliases_to_expected.items():
            assert normalize_regime(alias) == expected, (
                f"Alias '{alias}' should map to '{expected}'"
            )
