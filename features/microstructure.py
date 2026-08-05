# ═══════════════════════════════════════════════════════════════
#  Market Microstructure Analysis
# ═══════════════════════════════════════════════════════════════
from quant_utils.logger import get_logger

log = get_logger("features.microstructure")

log = get_logger("features.microstructure")


def calculate_order_flow(candles: list[dict]) -> dict:
    """Calculate order flow metrics"""
    if len(candles) < 2:
        return {}

    buy_volume = 0
    sell_volume = 0
    total_volume = 0

    for c in candles:
        vol = c.get("volume", 0)
        close = c.get("close", 0)
        open_ = c.get("open", 0)

        total_volume += vol

        if close > open_:
            buy_volume += vol
        elif close < open_:
            sell_volume += vol

    buy_pressure = buy_volume / total_volume if total_volume > 0 else 0.5

    return {
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "total_volume": total_volume,
        "buy_pressure": buy_pressure,
        "sell_pressure": 1 - buy_pressure,
    }


def calculate_volume_profile(candles: list[dict], bins: int = 20) -> dict:
    """Calculate volume profile"""
    if not candles:
        return {}

    prices = [c.get("close", 0) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price

    if price_range == 0:
        return {}

    bin_size = price_range / bins
    profile = [0] * bins

    for price, vol in zip(prices, volumes):
        bin_idx = min(int((price - min_price) / bin_size), bins - 1)
        profile[bin_idx] += vol

    max_bin = max(profile)
    poc = profile.index(max_bin) * bin_size + min_price + bin_size / 2

    return {"profile": profile, "poc": poc, "value_area": sum(profile) * 0.7}


def calculate_twap_deviation(candles: list[dict]) -> float:
    """Calculate TWAP deviation"""
    if not candles:
        return 0

    closes = [c.get("close", 0) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    total_pv = sum(c * v for c, v in zip(closes, volumes))
    total_vol = sum(volumes)

    if total_vol == 0:
        return 0

    twap = total_pv / total_vol

    deviation = abs(closes[-1] - twap) / twap * 100 if twap > 0 else 0

    return deviation


def detect_vwap_cross(candles: list[dict]) -> str:
    """Detect VWAP crossover"""
    if len(candles) < 2:
        return "NONE"

    closes = [c.get("close", 0) for c in candles]
    highs = [c.get("high", 0) for c in candles]
    lows = [c.get("low", 0) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    if len(closes) < 20 or not volumes or sum(volumes[-20:]) == 0:
        return "NONE"

    from features.ta import vwap

    vwap_val = vwap(highs, lows, closes, volumes)

    if vwap_val is None:
        return "NONE"

    current_price = closes[-1]
    prev_price = closes[-2]

    if prev_price < vwap_val and current_price >= vwap_val:
        return "CROSS_ABOVE"
    elif prev_price > vwap_val and current_price <= vwap_val:
        return "CROSS_BELOW"

    return "NONE"


def calculate_liquidity_zones(
    highs: list[float], lows: list[float], closes: list[float]
) -> dict:
    """Identify liquidity zones"""
    if len(highs) < 20:
        return {}

    highs_sorted = sorted(highs[-20:], reverse=True)[:3]
    lows_sorted = sorted(lows[-20:])[:3]

    return {"resistance_zones": highs_sorted, "support_zones": lows_sorted}


def calculate_momentum_score(candles: list[dict]) -> float:
    """Calculate momentum score (-1 to 1)"""
    if len(candles) < 20:
        return 0

    closes = [c.get("close", 0) for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    price_change = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0

    volume_change = (volumes[-1] - volumes[0]) / volumes[0] if volumes[0] > 0 else 0

    score = price_change * 10 + volume_change * 0.1

    return max(-1, min(1, score))


def analyze_orderbook_imbalance(orderbook: dict) -> dict | None:
    """Analyze orderbook for buying vs selling pressure"""
    if not orderbook:
        return None

    bid_volume = orderbook.get("bid_quantity", 0)
    ask_volume = orderbook.get("ask_quantity", 0)

    if bid_volume + ask_volume == 0:
        return {"imbalance": 0, "pressure": "neutral"}

    imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

    if imbalance > 0.3:
        pressure = "bullish"
    elif imbalance < -0.3:
        pressure = "bearish"
    else:
        pressure = "neutral"

    return {
        "imbalance": round(imbalance, 4),
        "pressure": pressure,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
    }


def calculate_spread_analysis(orderbook: dict) -> dict | None:
    """Analyze bid-ask spread and market depth"""
    if not orderbook:
        return None

    bid_price = orderbook.get("bid_price", 0)
    ask_price = orderbook.get("ask_price", 0)

    if bid_price <= 0 or ask_price <= 0:
        return None

    spread = ask_price - bid_price
    spread_pct = (spread / bid_price) * 100
    mid_price = (bid_price + ask_price) / 2

    depth = orderbook.get("bid_quantity", 0) + orderbook.get("ask_quantity", 0)

    # Classify spread
    if spread_pct < 0.1:
        spread_quality = "excellent"
    elif spread_pct < 0.5:
        spread_quality = "good"
    elif spread_pct < 1.0:
        spread_quality = "moderate"
    else:
        spread_quality = "poor"

    return {
        "spread": round(spread, 2),
        "spread_pct": round(spread_pct, 4),
        "mid_price": round(mid_price, 2),
        "depth": depth,
        "spread_quality": spread_quality,
        "is_liquid": spread_pct <= 0.5 and depth >= 1000,
    }


def detect_market_impact(orderbook: dict, trade_size: int) -> dict | None:
    """Estimate market impact of a potential trade"""
    if not orderbook or trade_size <= 0:
        return None

    bid_quantity = orderbook.get("bid_quantity", 0)
    ask_quantity = orderbook.get("ask_quantity", 0)

    # Simple market impact model
    if trade_size > ask_quantity:
        impact_pct = (
            min((trade_size - ask_quantity) / ask_quantity * 0.01, 0.05)
            if ask_quantity > 0
            else 0.05
        )
    else:
        impact_pct = 0.001  # Minimal impact for small orders

    return {
        "estimated_impact_pct": round(impact_pct, 4),
        "feasible": trade_size <= (bid_quantity + ask_quantity) * 2,
        "liquidity_score": min(bid_quantity + ask_quantity, 10000) / 10000,
    }


def analyze_market_microstructure(symbol: str, orderbook: dict = None) -> dict:
    """Comprehensive market microstructure analysis"""
    analysis = {
        "spread_analysis": None,
        "orderbook_imbalance": None,
        "market_impact": None,
        "overall_valid": False,
        "reason": "insufficient_data",
    }

    if orderbook:
        spread_analysis = calculate_spread_analysis(orderbook)
        imbalance_analysis = analyze_orderbook_imbalance(orderbook)

        analysis["spread_analysis"] = spread_analysis
        analysis["orderbook_imbalance"] = imbalance_analysis

        # Determine validity
        if spread_analysis and imbalance_analysis:
            spread_ok = spread_analysis.get("is_liquid", False)
            imbalance_neutral = imbalance_analysis.get("pressure") == "neutral"

            analysis["overall_valid"] = spread_ok
            if not spread_ok:
                analysis["reason"] = (
                    f"poor_liquidity_spread_{spread_analysis.get('spread_pct', 0):.2f}%"
                )
            else:
                analysis["reason"] = "microstructure_ok"
        else:
            analysis["reason"] = "missing_orderbook_data"

    return analysis
