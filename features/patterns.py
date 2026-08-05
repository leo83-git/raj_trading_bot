# ═══════════════════════════════════════════════════════════════
#  Pattern Recognition — Advanced Candlestick Patterns
# ═══════════════════════════════════════════════════════════════

from quant_utils.logger import get_logger

log = get_logger("features.patterns")


class CandlestickPatterns:
    """Advanced candlestick pattern recognition"""

    def detect_hammer(
        self, open_price: float, high: float, low: float, close: float
    ) -> bool:
        """Detect hammer candlestick pattern"""
        if high <= low:
            return False

        body = abs(close - open_price)
        total_range = high - low

        if total_range == 0:
            return False

        body_ratio = body / total_range

        # Hammer: small body, long lower wick
        if body_ratio <= 0.3:
            upper_wick = high - max(open_price, close)
            lower_wick = min(open_price, close) - low

            # Lower wick should be at least 2x the body
            return lower_wick > body * 2 and lower_wick > upper_wick

        return False

    def detect_shooting_star(
        self, open_price: float, high: float, low: float, close: float
    ) -> bool:
        """Detect shooting star candlestick pattern"""
        if high <= low:
            return False

        body = abs(close - open_price)
        total_range = high - low

        if total_range == 0:
            return False

        body_ratio = body / total_range

        # Shooting star: small body, long upper wick
        if body_ratio <= 0.3:
            upper_wick = high - max(open_price, close)
            lower_wick = min(open_price, close) - low

            # Upper wick should be at least 2x the body
            return upper_wick > body * 2 and upper_wick > lower_wick

        return False

    def detect_engulfing(self, candles: list[dict], index: int) -> str | None:
        """Detect bullish/bearish engulfing pattern"""
        if index < 1 or index >= len(candles):
            return None

        current = candles[index]
        previous = candles[index - 1]

        curr_open = current.get("open", 0)
        curr_close = current.get("close", 0)
        prev_open = previous.get("open", 0)
        prev_close = previous.get("close", 0)

        # Bullish engulfing
        if (
            prev_close < prev_open  # Previous bearish
            and curr_close > curr_open  # Current bullish
            and curr_close > prev_open  # Current close > previous open
            and curr_open < prev_close
        ):  # Current open < previous close
            return "bullish_engulfing"

        # Bearish engulfing
        if (
            prev_close > prev_open  # Previous bullish
            and curr_close < curr_open  # Current bearish
            and curr_close < prev_open  # Current close < previous open
            and curr_open > prev_close
        ):  # Current open > previous close
            return "bearish_engulfing"

        return None

    def detect_doji(
        self, open_price: float, high: float, low: float, close: float
    ) -> bool:
        """Detect doji pattern (indecision)"""
        if high <= low:
            return False

        body = abs(close - open_price)
        total_range = high - low

        if total_range == 0:
            return False

        body_ratio = body / total_range

        # Doji: very small body (< 5% of total range)
        return body_ratio < 0.05

    def detect_morning_star(self, candles: list[dict], index: int) -> bool:
        """Detect morning star reversal pattern"""
        if index < 2 or index >= len(candles):
            return False

        # Three candles: bearish, small body, bullish
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]

        # First candle: bearish
        if not (first.get("close", 0) < first.get("open", 0)):
            return False

        # Second candle: small body (doji or spinning top)
        second_body = abs(second.get("close", 0) - second.get("open", 0))
        second_range = second.get("high", 0) - second.get("low", 0)
        if second_range == 0 or second_body / second_range > 0.3:
            return False

        # Third candle: bullish, closes above midpoint of first candle
        if not (third.get("close", 0) > third.get("open", 0)):
            return False

        first_midpoint = (first.get("open", 0) + first.get("close", 0)) / 2
        return third.get("close", 0) > first_midpoint

    def detect_evening_star(self, candles: list[dict], index: int) -> bool:
        """Detect evening star reversal pattern"""
        if index < 2 or index >= len(candles):
            return False

        # Three candles: bullish, small body, bearish
        first = candles[index - 2]
        second = candles[index - 1]
        third = candles[index]

        # First candle: bullish
        if not (first.get("close", 0) > first.get("open", 0)):
            return False

        # Second candle: small body
        second_body = abs(second.get("close", 0) - second.get("open", 0))
        second_range = second.get("high", 0) - second.get("low", 0)
        if second_range == 0 or second_body / second_range > 0.3:
            return False

        # Third candle: bearish, closes below midpoint of first candle
        if not (third.get("close", 0) < third.get("open", 0)):
            return False

        first_midpoint = (first.get("open", 0) + first.get("close", 0)) / 2
        return third.get("close", 0) < first_midpoint

    def analyze_single_candle(
        self, open_price: float, high: float, low: float, close: float
    ) -> dict:
        """Analyze a single candle for patterns"""
        patterns = {
            "hammer": self.detect_hammer(open_price, high, low, close),
            "shooting_star": self.detect_shooting_star(open_price, high, low, close),
            "doji": self.detect_doji(open_price, high, low, close),
        }

        # Determine overall pattern strength
        pattern_count = sum(patterns.values())
        strength = (
            "strong"
            if pattern_count >= 2
            else "moderate" if pattern_count == 1 else "weak"
        )

        return {
            "patterns": patterns,
            "strength": strength,
            "direction": (
                "bullish"
                if patterns["hammer"]
                else "bearish" if patterns["shooting_star"] else "neutral"
            ),
        }

    def analyze_candle_sequence(self, candles: list[dict]) -> dict:
        """Analyze a sequence of candles for multi-candle patterns"""
        if len(candles) < 3:
            return {"patterns": [], "strength": "insufficient_data"}

        patterns_found = []

        # Check each position for patterns
        for i in range(len(candles)):
            # Engulfing patterns
            engulfing = self.detect_engulfing(candles, i)
            if engulfing:
                patterns_found.append(
                    {"pattern": engulfing, "index": i, "strength": "strong"}
                )

            # Morning star
            if i >= 2 and self.detect_morning_star(candles, i):
                patterns_found.append(
                    {"pattern": "morning_star", "index": i, "strength": "strong"}
                )

            # Evening star
            if i >= 2 and self.detect_evening_star(candles, i):
                patterns_found.append(
                    {"pattern": "evening_star", "index": i, "strength": "strong"}
                )

        # Determine overall analysis
        bullish_patterns = sum(
            1
            for p in patterns_found
            if "bullish" in p["pattern"] or p["pattern"] == "morning_star"
        )
        bearish_patterns = sum(
            1
            for p in patterns_found
            if "bearish" in p["pattern"] or p["pattern"] == "evening_star"
        )

        if bullish_patterns > bearish_patterns:
            direction = "bullish"
        elif bearish_patterns > bullish_patterns:
            direction = "bearish"
        else:
            direction = "neutral"

        strength = (
            "strong"
            if len(patterns_found) >= 2
            else "moderate" if len(patterns_found) == 1 else "weak"
        )

        return {
            "patterns": patterns_found,
            "direction": direction,
            "strength": strength,
            "bullish_signals": bullish_patterns,
            "bearish_signals": bearish_patterns,
        }


# Global instance for easy access
_pattern_recognizer = None


def get_pattern_recognizer() -> CandlestickPatterns:
    """Get global pattern recognizer instance"""
    global _pattern_recognizer
    if _pattern_recognizer is None:
        _pattern_recognizer = CandlestickPatterns()
    return _pattern_recognizer
