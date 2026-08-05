"""Candle-based market regime detector.

This module provides a robust candle-based regime detection system with:
- Safe preprocessing (zero prices, negative prices, NaN, missing data)
- Safe log returns calculation with isfinite checks
- Normalized cumulative-return trend model
- Support for all canonical regimes

Canonical Regimes
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
from dataclasses import dataclass
from typing import Final

import numpy as np

from quant_utils.logger import get_logger

from .vocabulary import normalize_regime

log = get_logger("regime.detector")


# Minimum number of candles required for reliable regime detection
MIN_CANDLES: Final[int] = 20


@dataclass
class MarketRegime:
    """Represents a detected market regime with confidence scores.

    Attributes
    ----------
    regime : str
        The canonical regime label (TRENDING_UP, TRENDING_DOWN, etc.)
    trend_strength : float
        Normalized trend strength (0.5 = neutral, >0.5 = bullish, <0.5 = bearish)
    mean_reversion_score : float
        Score indicating mean reversion tendency (0-1)
    volatility : float
        Annualized volatility estimate
    confidence : float
        Overall confidence in the regime classification (0-1)
    """

    regime: str
    trend_strength: float
    mean_reversion_score: float
    volatility: float
    confidence: float


def _preprocess_candles(candles: list[dict], max_candles: int = 252) -> np.ndarray:
    """Preprocess candle data safely.

    Handles:
    - Zero prices
    - Negative prices
    - NaN values
    - Missing close prices
    - Insufficient candle history

    Parameters
    ----------
    candles : list[dict]
        List of candle dictionaries with 'close' key
    max_candles : int
        Maximum number of candles to use (default 252 for ~1 year)

    Returns
    -------
    np.ndarray
        Cleaned array of close prices, sanitized to be positive and finite
    """
    if not candles:
        return np.array([])

    # Extract close prices, defaulting to NaN for missing values
    closes_list = []
    for c in candles[-max_candles:]:
        close_val = c.get("close")
        if close_val is None:
            closes_list.append(np.nan)
        else:
            try:
                closes_list.append(float(close_val))
            except (TypeError, ValueError):
                closes_list.append(np.nan)
    closes = np.array(closes_list)

    # Filter out NaN values
    valid = ~np.isnan(closes)

    if np.sum(valid) < MIN_CANDLES:
        log.debug(
            "Insufficient valid candles: %d valid out of %d",
            np.sum(valid),
            len(closes),
        )
        return np.array([])

    closes = closes[valid]

    # Handle zero and negative prices - replace with small positive value
    # Using 0.0001 as minimum (0.01 cents) to allow log calculations
    closes = np.where(closes <= 0, 0.0001, closes)

    return closes


def _calculate_log_returns(closes: np.ndarray) -> np.ndarray:
    """Calculate log returns safely.

    Prevents np.log(0) errors and handles infinite results.

    Parameters
    ----------
    closes : np.ndarray
        Array of close prices (must be positive)

    Returns
    -------
    np.ndarray
        Array of finite log returns
    """
    if len(closes) < 2:
        return np.array([])

    # Calculate log returns
    log_closes = np.log(closes)
    returns = np.diff(log_closes)

    # Filter out infinite and NaN values
    finite_returns = returns[np.isfinite(returns)]

    return finite_returns


def _calculate_cumulative_return(closes: np.ndarray) -> float:
    """Calculate normalized cumulative return.

    Returns a value centered around 0.5 (neutral):
    - >0.5 = Bullish trend
    - <0.5 = Bearish trend
    - ~0.5 = Neutral

    Parameters
    ----------
    closes : np.ndarray
        Array of close prices

    Returns
    -------
    float
        Normalized cumulative return (0.5 = neutral)
    """
    if len(closes) < 2 or closes[0] <= 0:
        return 0.5

    total_return = (closes[-1] / closes[0]) - 1.0

    # Normalize to 0.5-based scale
    # Return of 0% -> 0.5 (neutral)
    # Return of +10% -> ~0.6
    # Return of -10% -> ~0.4
    # Using sigmoid-like scaling for bounded output
    normalized = 0.5 + (total_return / 2)
    return float(np.clip(normalized, 0.0, 1.0))


def _calculate_trend_strength(closes: np.ndarray, returns: np.ndarray) -> float:
    """Calculate normalized trend strength using cumulative return model.

    This replaces the original implementation with a cleaner model:
    - 0.5 = Neutral (no trend)
    - >0.5 = Bullish (upward trend)
    - <0.5 = Bearish (downward trend)

    Parameters
    ----------
    closes : np.ndarray
        Array of close prices
    returns : np.ndarray
        Array of log returns

    Returns
    -------
    float
        Normalized trend strength (0.5 = neutral)
    """
    n = len(closes)
    if n < 2 or len(returns) < 5:
        return 0.5

    # Calculate cumulative return (normalized to 0.5 base)
    cumulative_return = _calculate_cumulative_return(closes)

    # Adjust for volatility
    # High volatility should reduce confidence in trend
    if len(returns) > 0:
        volatility = np.std(returns)
        volatility_factor = np.clip(1.0 - (volatility * 5), 0.5, 1.5)
    else:
        volatility_factor = 1.0

    # Combine cumulative return with volatility adjustment
    trend_strength = cumulative_return * volatility_factor
    return float(np.clip(trend_strength, 0.0, 1.0))


def _calculate_mean_reversion(closes: np.ndarray, returns: np.ndarray) -> float:
    """Calculate mean reversion tendency score.

    Higher scores indicate stronger mean reversion behavior
    (prices oscillating around a central value).

    Parameters
    ----------
    closes : np.ndarray
        Array of close prices
    returns : np.ndarray
        Array of log returns

    Returns
    -------
    float
        Mean reversion score (0-1, higher = stronger mean reversion)
    """
    n = len(closes)
    if n < 10:
        return 0.5

    ma = np.mean(closes)
    if ma <= 0:
        return 0.5

    # Distance from mean (normalized by mean)
    distance = np.mean(np.abs(closes - ma)) / ma

    # Z-scores for zero-crossing detection
    try:
        std_closes = np.std(closes)
        if std_closes <= 0:
            return 0.5
        z_scores = (closes - ma) / std_closes
        zero_crossings = np.sum(np.diff(np.sign(z_scores)) != 0)
    except Exception:
        return 0.5

    # Combine distance and zero-crossings
    # Higher distance = prices further from mean (stronger reversion)
    # More zero-crossings = more oscillation (stronger reversion)
    mean_rev_score = min(1.0, distance * 2 + zero_crossings / n)

    return float(np.clip(mean_rev_score, 0.0, 1.0))


def _calculate_volatility(returns: np.ndarray) -> float:
    """Calculate annualized volatility.

    Parameters
    ----------
    returns : np.ndarray
        Array of log returns

    Returns
    -------
    float
        Annualized volatility (0.05 - 1.0)
    """
    if len(returns) < 5:
        return 0.15

    daily_vol = np.std(returns)

    # Annualize (assuming 252 trading days)
    annualized_vol = daily_vol * np.sqrt(252)

    # Clamp to reasonable bounds
    return float(np.clip(annualized_vol, 0.05, 1.0))


def _classify_regime(
    trend_strength: float,
    mean_reversion_score: float,
    volatility: float,
) -> str:
    """Classify regime based on calculated metrics.

    Priority order:
    1. Volatility extremes (HIGH_VOLATILITY, LOW_VOLATILITY)
    2. Trend direction (TRENDING_UP, TRENDING_DOWN)
    3. Mean reversion (MEAN_REVERTING)
    4. Default (SIDEWAYS)

    Parameters
    ----------
    trend_strength : float
        Normalized trend strength (0.5 = neutral)
    mean_reversion_score : float
        Mean reversion score (0-1)
    volatility : float
        Annualized volatility

    Returns
    -------
    str
        Canonical regime label
    """
    # First priority: Volatility extremes
    if volatility > 0.35:
        return "HIGH_VOLATILITY"
    if volatility < 0.10:
        return "LOW_VOLATILITY"

    # Second priority: Trend direction
    # Use 0.5 as neutral threshold
    # Bullish: trend_strength > 0.55
    # Bearish: trend_strength < 0.45
    if trend_strength > 0.55 and trend_strength >= mean_reversion_score:
        return "TRENDING_UP"
    if trend_strength < 0.45 and trend_strength <= mean_reversion_score:
        return "TRENDING_DOWN"

    # Third priority: Mean reversion
    if mean_reversion_score > 0.60:
        return "MEAN_REVERTING"

    # Default: Sideways market
    return "SIDEWAYS"


def _calculate_confidence(
    trend_strength: float,
    mean_reversion_score: float,
    volatility: float,
) -> float:
    """Calculate confidence score for regime classification.

    Confidence is based on:
    - Clear distinction between trend and mean reversion
    - Consistent volatility level
    - Sufficient data points

    Parameters
    ----------
    trend_strength : float
        Normalized trend strength
    mean_reversion_score : float
        Mean reversion score
    volatility : float
        Annualized volatility

    Returns
    -------
    float
        Confidence score (0-1)
    """
    # Base confidence from trend-mean reversion separation
    separation = abs(trend_strength - mean_reversion_score)
    separation_confidence = np.clip(separation * 2, 0.3, 0.9)

    # Volatility consistency factor
    # Very high or very low volatility should increase confidence
    if 0.15 < volatility < 0.25:
        volatility_confidence = 0.7  # Moderate volatility = less certain
    elif volatility < 0.10 or volatility > 0.35:
        volatility_confidence = 0.9  # Extreme volatility = more certain
    else:
        volatility_confidence = 0.8

    # Combine factors
    confidence = separation_confidence * 0.6 + volatility_confidence * 0.4
    return float(np.clip(confidence, 0.3, 0.95))


class RegimeDetector:
    """Detect market regime using candle data.

    Implements safe preprocessing and calculation methods to handle
    edge cases like zero prices, negative prices, NaN values, and
    insufficient data.

    Attributes
    ----------
    lookback : int
        Number of candles to use for analysis
    """

    def __init__(self, lookback: int = 50):
        """Initialize the regime detector.

        Parameters
        ----------
        lookback : int
            Number of candles to use for analysis (default 50)
        """
        self.lookback = lookback
        self.regime_history: list[str] = []

    def detect_regime(self, candles: list[dict]) -> MarketRegime:
        """Detect the current market regime from candle data.

        Parameters
        ----------
        candles : list[dict]
            List of candle dictionaries with 'close' key

        Returns
        -------
        MarketRegime
            The detected regime with confidence scores
        """
        # Safe preprocessing
        closes = _preprocess_candles(candles, max_candles=self.lookback)

        if len(closes) < MIN_CANDLES:
            log.debug("Insufficient valid candles, returning default SIDEWAYS")
            return MarketRegime(
                regime="SIDEWAYS",
                trend_strength=0.5,
                mean_reversion_score=0.5,
                volatility=0.15,
                confidence=0.3,
            )

        # Safe log returns calculation
        returns = _calculate_log_returns(closes)

        if len(returns) < 5:
            log.debug("Insufficient valid returns, returning default SIDEWAYS")
            return MarketRegime(
                regime="SIDEWAYS",
                trend_strength=0.5,
                mean_reversion_score=0.5,
                volatility=0.15,
                confidence=0.3,
            )

        # Calculate metrics
        trend_strength = _calculate_trend_strength(closes, returns)
        mean_reversion_score = _calculate_mean_reversion(closes, returns)
        volatility = _calculate_volatility(returns)

        # Classify regime
        regime = _classify_regime(trend_strength, mean_reversion_score, volatility)

        # Normalize regime to canonical form
        normalized_regime = normalize_regime(regime)

        # Calculate confidence
        confidence = _calculate_confidence(
            trend_strength, mean_reversion_score, volatility
        )

        regime_result = MarketRegime(
            regime=normalized_regime,
            trend_strength=trend_strength,
            mean_reversion_score=mean_reversion_score,
            volatility=volatility,
            confidence=confidence,
        )

        self.regime_history.append(normalized_regime)

        return regime_result


def detect_market_regime(candles: list[dict], lookback: int = 50) -> MarketRegime:
    """Convenience function to detect regime from candles.

    Parameters
    ----------
    candles : list[dict]
        List of candle dictionaries with 'close' key
    lookback : int
        Number of candles to use (default 50)

    Returns
    -------
    MarketRegime
        The detected regime
    """
    detector = RegimeDetector(lookback=lookback)
    return detector.detect_regime(candles)
