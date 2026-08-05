# ═══════════════════════════════════════════════════════════════
#  Technical Indicators
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("features.indicators")


@dataclass
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def calculate_rsi(candles: list[dict], period: int = 14) -> float | None:
    """Calculate RSI (Relative Strength Index)"""
    if len(candles) < period + 1:
        return None

    closes = [c.get("close", 0) for c in candles]
    deltas = np.diff(closes)

    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return round(rsi, 2)


def calculate_sma(candles: list[dict], period: int) -> float | None:
    """Simple Moving Average"""
    if len(candles) < period:
        return None

    closes = [c.get("close", 0) for c in candles[-period:]]
    return round(sum(closes) / period, 2)


def calculate_ema(candles: list[dict], period: int) -> float | None:
    """Exponential Moving Average"""
    if len(candles) < period:
        return None

    closes = [c.get("close", 0) for c in candles]
    multiplier = 2 / (period + 1)

    ema = closes[0]
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 2)


def calculate_macd(
    candles: list[dict], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict:
    """Calculate MACD (Moving Average Convergence Divergence)"""
    if len(candles) < slow:
        return {"macd": 0, "signal": 0, "histogram": 0}

    closes = [c.get("close", 0) for c in candles]

    ema_fast = calculate_ema(candles, fast)
    ema_slow = calculate_ema(candles, slow)

    if ema_fast is None or ema_slow is None:
        return {"macd": 0, "signal": 0, "histogram": 0}

    macd_line = ema_fast - ema_slow

    macd_values = []
    temp_candles = []
    for c in candles:
        temp_candles.append(c)
        if len(temp_candles) >= slow:
            ef = calculate_ema(temp_candles, fast)
            es = calculate_ema(temp_candles, slow)
            if ef and es:
                macd_values.append(ef - es)

    if len(macd_values) >= signal:
        signal_line = sum(macd_values[-signal:]) / signal
        histogram = macd_line - signal_line
    else:
        signal_line = 0
        histogram = 0

    return {
        "macd": round(macd_line, 4),
        "signal": round(signal_line, 4),
        "histogram": round(histogram, 4),
    }


def calculate_bollinger_bands(
    candles: list[dict], period: int = 20, std_dev: float = 2.0
) -> dict:
    """Calculate Bollinger Bands"""
    if len(candles) < period:
        return {"upper": 0, "middle": 0, "lower": 0}

    closes = [c.get("close", 0) for c in candles[-period:]]
    middle = sum(closes) / period

    variance = sum((x - middle) ** 2 for x in closes) / period
    std = variance**0.5

    return {
        "upper": round(middle + std_dev * std, 2),
        "middle": round(middle, 2),
        "lower": round(middle - std_dev * std, 2),
    }


def calculate_atr(candles: list[dict], period: int = 14) -> float | None:
    """Average True Range"""
    if len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].get("high", 0)
        low = candles[i].get("low", 0)
        prev_close = candles[i - 1].get("close", 0)

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    return round(sum(true_ranges[-period:]) / period, 2)


def calculate_adx(candles: list[dict], period: int = 14) -> float | None:
    """Average Directional Index (ADX)"""
    if len(candles) < period + 1:
        return None

    highs = [c.get("high", 0) for c in candles]
    lows = [c.get("low", 0) for c in candles]
    closes = [c.get("close", 0) for c in candles]

    # Calculate True Range, +DM, and -DM
    tr_values = []
    plus_dm_values = []
    minus_dm_values = []

    for i in range(1, len(candles)):
        # True Range
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)

        # Directional Movement
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0

        plus_dm_values.append(plus_dm)
        minus_dm_values.append(minus_dm)

    if len(tr_values) < period:
        return None

    # Calculate smoothed averages
    tr_smooth = sum(tr_values[-period:]) / period
    plus_dm_smooth = sum(plus_dm_values[-period:]) / period
    minus_dm_smooth = sum(minus_dm_values[-period:]) / period

    # Calculate DI values
    plus_di = (plus_dm_smooth / tr_smooth) * 100 if tr_smooth > 0 else 0
    minus_di = (minus_dm_smooth / tr_smooth) * 100 if tr_smooth > 0 else 0

    # Calculate DX and ADX
    dx = (
        abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        if (plus_di + minus_di) > 0
        else 0
    )

    return round(dx, 2)


def calculate_beta(
    stock_returns: list[float], market_returns: list[float]
) -> float | None:
    """Calculate beta coefficient vs market"""
    if len(stock_returns) != len(market_returns) or len(stock_returns) < 2:
        return None

    try:
        # Calculate covariance and variance
        covariance = np.cov(stock_returns, market_returns)[0, 1]
        market_variance = np.var(market_returns)

        if market_variance == 0:
            return 1.0

        beta = covariance / market_variance
        return round(beta, 4)
    except Exception:
        return None


def calculate_daily_range_pct(candles: list[dict]) -> float | None:
    """Calculate daily range as percentage"""
    if not candles:
        return None

    high = candles[-1].get("high", 0)
    low = candles[-1].get("low", 0)

    if low <= 0:
        return None

    range_pct = ((high - low) / low) * 100
    return round(range_pct, 2)


def calculate_gap_analysis(candles: list[dict]) -> dict | None:
    """Analyze gap between previous close and current open"""
    if len(candles) < 2:
        return None

    prev_close = candles[-2].get("close", 0)
    current_open = candles[-1].get("open", 0)

    if prev_close <= 0:
        return None

    gap_pct = ((current_open - prev_close) / prev_close) * 100
    gap_type = "GAP_UP" if gap_pct > 1 else "GAP_DOWN" if gap_pct < -1 else "NO_GAP"

    return {
        "gap_pct": round(gap_pct, 2),
        "gap_type": gap_type,
        "is_large_gap": abs(gap_pct) > 3,
    }


def calculate_stochastic(candles: list[dict], period: int = 14) -> dict:
    """Stochastic Oscillator"""
    if len(candles) < period:
        return {"k": 50, "d": 50}

    recent = candles[-period:]
    highs = [c.get("high", 0) for c in recent]
    lows = [c.get("low", 0) for c in recent]
    close = candles[-1].get("close", 0)

    highest = max(highs)
    lowest = min(lows)

    if highest == lowest:
        return {"k": 50, "d": 50}

    k = ((close - lowest) / (highest - lowest)) * 100

    return {"k": round(k, 2), "d": 50}


def calculate_obv(candles: list[dict]) -> float | None:
    """On-Balance Volume"""
    if len(candles) < 2:
        return None

    obv = 0
    for i in range(1, len(candles)):
        current_close = candles[i].get("close", 0)
        prev_close = candles[i - 1].get("close", 0)
        volume = candles[i].get("volume", 0)

        if current_close > prev_close:
            obv += volume
        elif current_close < prev_close:
            obv -= volume

    return round(obv, 0)


def calculate_vwap(candles: list[dict]) -> float | None:
    """Volume Weighted Average Price"""
    if not candles:
        return None

    total_pv = sum(c.get("close", 0) * c.get("volume", 0) for c in candles)
    total_vol = sum(c.get("volume", 0) for c in candles)

    if total_vol == 0:
        return None

    return round(total_pv / total_vol, 2)


def detect_trend(
    candles: list[dict], short_period: int = 9, long_period: int = 21
) -> str:
    """Detect price trend"""
    if len(candles) < long_period:
        return "SIDEWAYS"

    sma_short = calculate_sma(candles, short_period)
    sma_long = calculate_sma(candles, long_period)

    if sma_short is None or sma_long is None:
        return "SIDEWAYS"

    if sma_short > sma_long * 1.02:
        return "UPTREND"
    elif sma_short < sma_long * 0.98:
        return "DOWNTREND"

    return "SIDEWAYS"


def calculate_volatility(candles: list[dict], period: int = 20) -> float | None:
    """Calculate historical volatility"""
    if len(candles) < period:
        return None

    closes = [c.get("close", 0) for c in candles[-period:]]
    returns = np.diff(np.log(closes))

    return round(np.std(returns) * np.sqrt(252) * 100, 2)


def calculate_avg_volume(candles: list[dict], period: int = 20) -> float | None:
    """Calculate average volume from last period candles"""
    if len(candles) < period:
        return None

    volumes = [c.get("volume", 0) for c in candles[-period:]]
    if not volumes or all(v == 0 for v in volumes):
        return None

    avg_volume = np.mean(volumes)
    return round(avg_volume, 2)


def calculate_relative_volume(candles: list[dict], period: int = 20) -> float | None:
    """Calculate relative volume (current volume / average volume over period)"""
    if len(candles) < period + 1:
        return None

    current_volume = candles[-1].get("volume", 0)
    if current_volume == 0:
        return None

    volumes = [
        c.get("volume", 0) for c in candles[-period - 1 : -1]
    ]  # Last 'period' candles before current
    if not volumes or all(v == 0 for v in volumes):
        return None

    avg_volume = np.mean(volumes)
    if avg_volume == 0:
        return None

    return round(current_volume / avg_volume, 2)


def calculate_all_indicators(candles: list[dict]) -> dict:
    """Calculate all technical indicators"""
    indicators = {
        "rsi": calculate_rsi(candles),
        "sma_9": calculate_sma(candles, 9),
        "sma_21": calculate_sma(candles, 21),
        "sma_50": calculate_sma(candles, 50),
        "ema_9": calculate_ema(candles, 9),
        "ema_21": calculate_ema(candles, 21),
        "macd": calculate_macd(candles),
        "bollinger": calculate_bollinger_bands(candles),
        "atr": calculate_atr(candles),
        "adx": calculate_adx(candles),
        "stochastic": calculate_stochastic(candles),
        "obv": calculate_obv(candles),
        "vwap": calculate_vwap(candles),
        "trend": detect_trend(candles),
        "volatility": calculate_volatility(candles),
        "avg_volume": calculate_avg_volume(candles),
        "relative_volume": calculate_relative_volume(candles),
        "supertrend": calculate_supertrend(candles),
        "ichimoku": calculate_ichimoku(candles),
        "support_resistance": calculate_support_resistance(candles),
        "daily_range_pct": calculate_daily_range_pct(candles),
        "gap_analysis": calculate_gap_analysis(candles),
    }

    # Calculate beta if we have market data (NIFTY returns)
    if len(candles) > 1:
        stock_returns = []
        for i in range(1, len(candles)):
            prev_close = candles[i - 1].get("close", 0)
            curr_close = candles[i].get("close", 0)
            if prev_close > 0:
                ret = (curr_close - prev_close) / prev_close
                stock_returns.append(ret)

        # For beta calculation, we'd need market returns
        # For now, we'll skip this as it requires market data integration
        indicators["beta"] = None  # Placeholder

    return indicators


def calculate_supertrend(
    candles: list[dict], period: int = 10, multiplier: float = 3.0
) -> dict | None:
    """Supertrend indicator - trend following"""
    if len(candles) < period:
        return None

    tr_list = []
    for i in range(1, len(candles)):
        high = candles[i].get("high", 0)
        low = candles[i].get("low", 0)
        prev_close = candles[i - 1].get("close", 0)

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    atr = sum(tr_list[-period:]) / period

    hl_avg = (candles[-1].get("high", 0) + candles[-1].get("low", 0)) / 2

    upper_band = hl_avg + (multiplier * atr)
    lower_band = hl_avg - (multiplier * atr)

    close = candles[-1].get("close", 0)
    direction = "UPTREND" if close > lower_band else "DOWNTREND"

    return {
        "value": round(lower_band if direction == "UPTREND" else upper_band, 2),
        "direction": direction,
        "atr": round(atr, 2),
    }


def calculate_ichimoku(candles: list[dict]) -> dict:
    """Ichimoku Cloud - multi-timeframe trend indicator"""
    if len(candles) < 52:
        return {
            "tenkan": None,
            "kijun": None,
            "senkou_a": None,
            "senkou_b": None,
            "cloud": "UNKNOWN",
        }

    def highest(candles, period, idx, key="high"):
        start = max(0, idx - period)
        return max(c.get(key, 0) for c in candles[start:idx])

    def lowest(candles, period, idx, key="low"):
        start = max(0, idx - period)
        return min(c.get(key, 0) for c in candles[start:idx])

    idx = len(candles)

    tenkan = (highest(candles, 9, idx) + lowest(candles, 9, idx)) / 2
    kijun = (highest(candles, 26, idx) + lowest(candles, 26, idx)) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (highest(candles, 52, idx) + lowest(candles, 52, idx)) / 2

    close = candles[-1].get("close", 0)

    if close > senkou_a and close > senkou_b:
        cloud = "BULLISH"
    elif close < senkou_a and close < senkou_b:
        cloud = "BEARISH"
    else:
        cloud = "NEUTRAL"

    return {
        "tenkan": round(tenkan, 2),
        "kijun": round(kijun, 2),
        "senkou_a": round(senkou_a, 2),
        "senkou_b": round(senkou_b, 2),
        "cloud": cloud,
    }


def calculate_support_resistance(candles: list[dict], lookback: int = 20) -> dict:
    """Find support and resistance levels"""
    if len(candles) < lookback:
        return {"support": None, "resistance": None, "pivot": None}

    highs = [c.get("high", 0) for c in candles[-lookback:]]
    lows = [c.get("low", 0) for c in candles[-lookback:]]

    resistance = max(highs)
    support = min(lows)

    pivot = (resistance + support + candles[-1].get("close", 0)) / 3

    return {
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "pivot": round(pivot, 2),
    }


def calculate_golden_cross_death_cross(
    candles: list[dict], short: int = 50, long: int = 200
) -> dict | None:
    """Golden Cross / Death Cross detection"""
    if len(candles) < long + 1:
        return None

    sma_short_current = calculate_sma(candles, short)
    sma_long_current = calculate_sma(candles, long)

    sma_short_prev = calculate_sma(candles[:-1], short)
    sma_long_prev = calculate_sma(candles[:-1], long)

    if sma_short_current is None or sma_long_current is None:
        return None

    if sma_short_prev is not None and sma_long_prev is not None:
        if sma_short_prev < sma_long_prev and sma_short_current > sma_long_current:
            return {"signal": "GOLDEN_CROSS", "action": "BUY"}
        elif sma_short_prev > sma_long_prev and sma_short_current < sma_long_current:
            return {"signal": "DEATH_CROSS", "action": "SELL"}

    return {"signal": "NONE", "action": "HOLD"}


def calculate_volume_profile(candles: list[dict], bins: int = 10) -> dict | None:
    """Volume Profile - identify high volume price zones"""
    if len(candles) < bins:
        return None

    highs = [c.get("high", 0) for c in candles]
    lows = [c.get("low", 0) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    price_range = max(highs) - min(lows)
    if price_range == 0:
        return None

    bin_size = price_range / bins

    profile = {}
    for i, (high, low, vol) in enumerate(zip(highs, lows, volumes)):
        bin_idx = int((high - min(lows)) / bin_size)
        profile[bin_idx] = profile.get(bin_idx, 0) + vol

    max_bin = max(profile, key=profile.get)
    poc_price = min(lows) + (max_bin * bin_size) + (bin_size / 2)

    return {"poc": round(poc_price, 2), "profile": profile}


# ═══════════════════════════════════════════════════════════════
#  ADVANCED ML FEATURES
# ═══════════════════════════════════════════════════════════════


def calculate_interaction_terms(candles: list[dict], period: int = 14) -> dict:
    """
    Calculate interaction terms (feature engineering for ML)
    - RSI × Price Momentum
    - Volume × Volatility
    - RSI × MA Distance
    """
    if len(candles) < period + 10:
        return {}

    closes = np.array([c.get("close", 0) for c in candles])
    volumes = np.array([c.get("volume", 0) for c in candles])
    highs = np.array([c.get("high", 0) for c in candles])
    lows = np.array([c.get("low", 0) for c in candles])

    rsi = calculate_rsi(candles, period) or 50
    sma = calculate_sma(candles, period) or closes[-1]

    returns = np.diff(np.log(closes))
    volatility = (
        np.std(returns[-period:]) * np.sqrt(252) if len(returns) >= period else 0
    )

    price_momentum = (closes[-1] / closes[-period] - 1) if period < len(closes) else 0
    ma_distance = (closes[-1] - sma) / sma if sma else 0

    avg_volume = np.mean(volumes[-period:])
    volume_scaled = avg_volume / 1e6

    return {
        "rsi_momentum": round(rsi * price_momentum, 4),
        "volume_volatility": round(volume_scaled * volatility, 4),
        "rsi_ma_distance": round(rsi * ma_distance, 4),
        "price_momentum": round(price_momentum, 4),
        "ma_distance": round(ma_distance, 4),
        "raw_score": round(
            rsi * 0.6 + (100 - volatility * 100) * 0.2 + ma_distance * 100 * 0.2, 2
        ),
    }


def calculate_time_series_decomposition(candles: list[dict], period: int = 20) -> dict:
    """
    Time-series decomposition into trend, seasonal, residual
    Using simple moving average as trend proxy
    """
    if len(candles) < period * 2:
        return {}

    closes = np.array([c.get("close", 0) for c in candles[-period * 2 :]])

    trend = calculate_sma(candles, period)
    if trend is None:
        return {}

    detrended = closes[-period:] - trend

    seasonal = np.array([np.sin(2 * np.pi * i / period) for i in range(period)])
    seasonal_strength = (
        np.corrcoef(detrended, seasonal)[0, 1] if len(detrended) > 1 else 0
    )

    residual_std = np.std(detrended)
    trend_strength = (
        abs(trend - closes[-period - 1]) / residual_std if residual_std > 0 else 0
    )

    return {
        "trend_component": round(trend, 2),
        "seasonal_strength": round(seasonal_strength, 4),
        "trend_strength": round(min(trend_strength, 1), 4),
        "residual_std": round(residual_std, 4),
        "decomposition_score": round(min(seasonal_strength, 1), 2),
    }


def calculate_volatility_regime(candles: list[dict], period: int = 20) -> dict:
    """
    Classify volatility regime: LOW, ELEVATED, HIGH, EXTREME
    Based on historical ATR percentiles
    """
    if len(candles) < period + 5:
        return {"regime": "UNKNOWN", "score": 0.5}

    closes = np.array([c.get("close", 0) for c in candles])
    highs = np.array([c.get("high", 0) for c in candles])
    lows = np.array([c.get("low", 0) for c in candles])

    true_range = highs - lows

    recent_atr = np.mean(true_range[-period:])
    historical_atr = np.mean(true_range)

    atr_percentile = np.sum(true_range[-period:] < recent_atr) / period

    percentile_score = np.percentile(true_range[-period * 3 :], [33, 66, 90])

    if recent_atr >= percentile_score[2] * 0.9:
        regime = "EXTREME"
        score = 0.9
    elif recent_atr >= percentile_score[1]:
        regime = "HIGH"
        score = 0.7
    elif recent_atr >= percentile_score[0]:
        regime = "ELEVATED"
        score = 0.5
    elif recent_atr >= historical_atr * 0.8:
        regime = "NORMAL"
        score = 0.3
    else:
        regime = "LOW"
        score = 0.2

    atr_ratio = recent_atr / historical_atr if historical_atr > 0 else 1

    return {
        "regime": regime,
        "score": round(score, 2),
        "atr_ratio": round(atr_ratio, 2),
        "recent_atr": round(recent_atr, 2),
        "historical_atr": round(historical_atr, 2),
        "percentile": round(atr_percentile, 2),
    }


def calculate_advanced_features(candles: list[dict], period: int = 20) -> dict:
    """
    Combine all advanced features into single dict for ML model
    """
    interaction = calculate_interaction_terms(candles, period)
    decomposition = calculate_time_series_decomposition(candles, period)
    volatility = calculate_volatility_regime(candles, period)

    closes = np.array([c.get("close", 0) for c in candles])
    returns = np.diff(np.log(closes))

    momentum_5d = np.sum(returns[-5:]) if len(returns) >= 5 else 0
    momentum_10d = np.sum(returns[-10:]) if len(returns) >= 10 else 0
    momentum_20d = np.sum(returns[-20:]) if len(returns) >= 20 else 0

    return {
        "interaction_features": interaction,
        "decomposition": decomposition,
        "volatility_regime": volatility,
        "momentum_5d": round(momentum_5d, 4),
        "momentum_10d": round(momentum_10d, 4),
        "momentum_20d": round(momentum_20d, 4),
        "returns_mean": (
            round(np.mean(returns[-period:]), 4) if len(returns) >= period else 0
        ),
        "returns_std": (
            round(np.std(returns[-period:]), 4) if len(returns) >= period else 0
        ),
        "skewness": (
            round(
                float(
                    np.mean((returns[-period:] - np.mean(returns[-period:])) ** 3)
                    / (np.std(returns[-period:]) ** 3 + 1e-10)
                ),
                2,
            )
            if len(returns) >= period
            else 0
        ),
    }
