# ═══════════════════════════════════════════════════════════════
#  Feature Engine — Technical Analysis Indicators
# ═══════════════════════════════════════════════════════════════
import math

from quant_utils.logger import get_logger

log = get_logger("features.ta")


def sma(data: list[float], period: int) -> float | None:
    """Simple Moving Average"""
    if len(data) < period:
        return None
    return sum(data[-period:]) / period


def ema(data: list[float], period: int) -> float | None:
    """Exponential Moving Average"""
    if len(data) < period:
        return None

    multiplier = 2 / (period + 1)
    ema_val = sum(data[:period]) / period

    for price in data[period:]:
        ema_val = (price - ema_val) * multiplier + ema_val

    return ema_val


def rsi(data: list[float], period: int = 14) -> float | None:
    """Relative Strength Index"""
    if len(data) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(data)):
        change = data[i] - data[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    data: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict | None:
    """MACD indicator"""
    if len(data) < slow:
        return None

    ema_fast = ema(data, fast)
    ema_slow = ema(data, slow)

    if ema_fast is None or ema_slow is None:
        return None

    macd_line = ema_fast - ema_slow
    signal_line = ema([float(macd_line)], signal) if signal else None
    histogram = macd_line - signal_line if signal_line else None

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(
    data: list[float], period: int = 20, std_dev: float = 2.0
) -> dict | None:
    """Bollinger Bands"""
    if len(data) < period:
        return None

    sma_val = sma(data, period)
    if sma_val is None:
        return None

    variance = sum((x - sma_val) ** 2 for x in data[-period:]) / period
    std = math.sqrt(variance)

    return {
        "upper": sma_val + (std_dev * std),
        "middle": sma_val,
        "lower": sma_val - (std_dev * std),
    }


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    """Average True Range"""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    return sum(true_ranges[-period:]) / period


def vwap(
    highs: list[float], lows: list[float], closes: list[float], volumes: list[float]
) -> float | None:
    """Volume Weighted Average Price"""
    if len(highs) != len(lows) != len(closes) != len(volumes):
        return None

    total_pv = sum(
        (highs[i] + lows[i] + closes[i]) / 3 * volumes[i] for i in range(len(volumes))
    )
    total_vol = sum(volumes)

    return total_pv / total_vol if total_vol > 0 else None


def pivot_points(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    pivot_type: str = "CLASSIC",
) -> dict | None:
    """Calculate pivot points"""
    if len(highs) < 2 or len(lows) < 2 or len(closes) < 2:
        return None

    high = highs[-2]
    low = lows[-2]
    close = closes[-1]

    if pivot_type == "CLASSIC":
        pp = (high + low + close) / 3
        r1 = 2 * pp - low
        r2 = pp + (high - low)
        r3 = high + 2 * (pp - low)
        s1 = 2 * pp - high
        s2 = pp - (high - low)
        s3 = low - 2 * (high - pp)
    elif pivot_type == "CAMARILLA":
        pp = (high + low + close) / 3
        r1 = close + (high - low) * 0.11
        r2 = close + (high - low) * 0.183
        r3 = close + (high - low) * 0.25
        s1 = close - (high - low) * 0.11
        s2 = close - (high - low) * 0.183
        s3 = close - (high - low) * 0.25
    else:
        pp = (high + low + close) / 3
        r1 = pp + (high - low) * 0.382
        r2 = pp + (high - low) * 0.618
        r3 = pp + (high - low)
        s1 = pp - (high - low) * 0.382
        s2 = pp - (high - low) * 0.618
        s3 = pp - (high - low)

    return {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}


def detect_trend(closes: list[float], period: int = 20) -> str:
    """Detect market trend"""
    if len(closes) < period:
        return "SIDEWAYS"

    sma_fast = sma(closes, 5)
    sma_slow = sma(closes, period)

    if sma_fast is None or sma_slow is None:
        return "SIDEWAYS"

    if sma_fast > sma_slow * 1.02:
        return "UPTREND"
    elif sma_fast < sma_slow * 0.98:
        return "DOWNTREND"
    return "SIDEWAYS"


def detect_volatility(closes: list[float], period: int = 20) -> float:
    """Calculate historical volatility"""
    if len(closes) < period:
        return 0

    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))
    ]
    if len(returns) < period:
        return 0

    recent_returns = returns[-period:]
    mean = sum(recent_returns) / len(recent_returns)
    variance = sum((r - mean) ** 2 for r in recent_returns) / len(recent_returns)

    return math.sqrt(variance * 252) * 100


def calculate_all_features(candles: list[dict]) -> dict:
    """Calculate all technical features from candles"""
    if len(candles) < 30:
        return {}

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    return {
        "sma_9": sma(closes, 9),
        "sma_21": sma(closes, 21),
        "sma_50": sma(closes, 50),
        "ema_9": ema(closes, 9),
        "ema_21": ema(closes, 21),
        "rsi": rsi(closes, 14),
        "macd": macd(closes),
        "bollinger": bollinger_bands(closes, 20, 2.0),
        "atr": atr(highs, lows, closes, 14),
        "vwap": vwap(highs, lows, closes, volumes),
        "pivot": pivot_points(highs, lows, closes),
        "trend": detect_trend(closes),
        "volatility": detect_volatility(closes),
    }
