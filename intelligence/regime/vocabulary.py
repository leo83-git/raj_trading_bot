"""Canonical regime vocabulary and normalization utilities.

This module provides a single source of truth for regime labels used throughout
the trading system, ensuring consistent terminology and preventing duplication
of regime aliases across the codebase.

Regime Categories
-----------------
TRENDING_UP
    Market exhibits consistent upward price movement with minimal noise.

TRENDING_DOWN
    Market exhibits consistent downward price movement with minimal noise.

MEAN_REVERTING
    Market oscillates around a central value, providing opportunities for
    mean reversion strategies.

SIDEWAYS
    Market shows no clear directional bias, trading within a range.

HIGH_VOLATILITY
    Market experiences large price swings in short timeframes.

LOW_VOLATILITY
    Market exhibits stable, slow price movements.
"""

from __future__ import annotations

import logging
from typing import Final, Dict, Optional

log = logging.getLogger(__name__)

# Canonical regime labels - the single source of truth
CANONICAL_REGIMES: Final[frozenset[str]] = frozenset(
    {
        "TRENDING_UP",
        "TRENDING_DOWN",
        "MEAN_REVERTING",
        "SIDEWAYS",
        "HIGH_VOLATILITY",
        "LOW_VOLATILITY",
    }
)

# Mapping of common aliases to canonical regime labels
# Aliases are stored in lowercase for case-insensitive matching
REGIME_ALIASES: Final[Dict[str, str]] = {
    # Trending up aliases
    "bullish": "TRENDING_UP",
    "upward": "TRENDING_UP",
    "up": "TRENDING_UP",
    "bull": "TRENDING_UP",
    "rising": "TRENDING_UP",
    "growth": "TRENDING_UP",
    "bullish_bias": "TRENDING_UP",
    "uptrend": "TRENDING_UP",
    "up_trend": "TRENDING_UP",
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


def normalize_regime(regime: Optional[str]) -> str:
    """Normalize a regime string to its canonical form.

    This function handles:
    - Case-insensitive matching
    - Alias expansion to canonical labels
    - Whitespace trimming
    - Unknown label handling with debug logging

    Parameters
    ----------
    regime : str or None
        The regime string to normalize. If None, returns "SIDEWAYS" as default.

    Returns
    -------
    str
        The canonical regime label, or "SIDEWAYS" if input is None.

    Examples
    --------
    >>> normalize_regime("bullish")
    'TRENDING_UP'
    >>> normalize_regime("  BEARISH  ")
    'TRENDING_DOWN'
    >>> normalize_regime("unknown_label")
    'SIDEWAYS'
    >>> normalize_regime(None)
    'SIDEWAYS'
    """
    if regime is None:
        return "SIDEWAYS"

    # Clean and normalize the input
    normalized = regime.strip().upper()

    # Check for exact match first (optimization for already canonical labels)
    if normalized in CANONICAL_REGIMES:
        return normalized

    # Check aliases - normalize input to lowercase for lookup
    # since aliases are stored in lowercase for case-insensitive matching
    canonical = REGIME_ALIASES.get(normalized.lower())

    if canonical is not None:
        return canonical

    # Unknown regime - log for debugging and return default
    log.debug(
        "Unknown regime '%s' encountered, defaulting to SIDEWAYS. Known regimes: %s",
        regime,
        sorted(CANONICAL_REGIMES),
    )
    return "SIDEWAYS"
