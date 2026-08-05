# Breakout Strategy Plugin
import datetime

from strategy.enhanced_risk_management import (
    EnhancedRiskCalculator,
    enhance_signal_with_sr_levels,
)
from strategy.marketplace import StrategyPlugin


class BreakoutStrategy(StrategyPlugin):
    """Breakout trading strategy - for intraday trading"""

    name = "breakout"
    description = "Trade breakouts above resistance with volume confirmation"
    timeframe = "intraday"
    asset_types = ["EQUITY", "INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.lookback = self.config.get("lookback_period", 20)
        self.volume_multiplier = self.config.get("volume_multiplier", 1.5)

    def analyze(self, data: dict) -> dict | None:
        return self.get_signal(data)

    def get_signal(self, features: dict) -> dict | None:
        """Generate breakout signal"""
        high = features.get("high", [])
        low = features.get("low", [])
        volume = features.get("volume", [])
        close = features.get("close", [])
        candles = features.get("candles", [])  # Full candle data for S/R calculation

        if not high or len(high) < self.lookback:
            return None

        # Calculate resistance (highest high in lookback)
        resistance = max(high[-self.lookback : -1])
        current_price = close[-1] if close else 0

        # Check if breakout
        if current_price > resistance:
            # Check volume
            avg_volume = sum(volume[-self.lookback : -1]) / len(
                volume[-self.lookback : -1]
            )
            current_volume = volume[-1] if volume else 0

            if current_volume > avg_volume * self.volume_multiplier:
                signal = {
                    "action": "BUY",
                    "entry": current_price,
                    "stop_loss": min(low[-5:]) if low else current_price * 0.98,
                    "target": current_price * 1.02,
                    "reason": f"Breakout above {resistance}",
                }

                # Enhance with support/resistance levels if candle data available
                if candles and len(candles) >= 20:
                    signal = enhance_signal_with_sr_levels(signal, candles)

                return signal

        # Check breakdown
        support = min(low[-self.lookback : -1])
        if current_price < support:
            avg_volume = sum(volume[-self.lookback : -1]) / len(
                volume[-self.lookback : -1]
            )
            current_volume = volume[-1] if volume else 0

            if current_volume > avg_volume * self.volume_multiplier:
                signal = {
                    "action": "SELL",
                    "entry": current_price,
                    "stop_loss": max(high[-5:]) if high else current_price * 1.02,
                    "target": current_price * 0.98,
                    "reason": f"Breakdown below {support}",
                }

                # Enhance with support/resistance levels if candle data available
                if candles and len(candles) >= 20:
                    signal = enhance_signal_with_sr_levels(signal, candles)

                return signal

        return None


class MeanReversionStrategy(StrategyPlugin):
    """Mean reversion strategy - for sideways markets with improved risk management"""

    name = "mean_reversion"
    description = (
        "Trade reversions to moving average with support/resistance-based stops"
    )
    timeframe = "intraday"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.ma_period = self.config.get("ma_period", 20)
        self.deviation_threshold = self.config.get("deviation_threshold", 0.02)
        self.use_atr = self.config.get("use_atr", True)

    def get_signal(self, features: dict) -> dict | None:
        """Generate mean reversion signal with better stops"""
        close = features.get("close", [])
        sma = features.get("sma_20") or features.get("sma_50")
        candles = features.get("candles", [])

        if not close or not sma:
            return None

        current_price = close[-1]

        # Calculate deviation from MA
        deviation = (current_price - sma) / sma

        if deviation > self.deviation_threshold:
            # Price too high - expect reversal down
            signal = {
                "action": "SELL",
                "entry": current_price,
                "stop_loss": current_price * 1.02,  # Above entry
                "target": sma,  # Target is the MA
                "reason": f"Overbought - deviation {deviation * 100:.1f}%",
            }

            # Improve stop loss using ATR if available
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    # Stop above entry by 1.5 ATR
                    signal["stop_loss"] = current_price + (1.5 * atr)

            return signal

        elif deviation < -self.deviation_threshold:
            # Price too low - expect reversal up
            signal = {
                "action": "BUY",
                "entry": current_price,
                "stop_loss": current_price * 0.98,  # Below entry
                "target": sma,  # Target is the MA
                "reason": f"Oversold - deviation {deviation * 100:.1f}%",
            }

            # Improve stop loss using ATR if available
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    # Stop below entry by 1.5 ATR
                    signal["stop_loss"] = current_price - (1.5 * atr)

            return signal

        return None


class ScalpingStrategy(StrategyPlugin):
    """Scalping strategy - quick trades with small targets but improved risk management"""

    name = "scalping"
    description = "Quick trades with ATR-based stops instead of fixed percentages"
    timeframe = "scalping"
    asset_types = ["EQUITY", "INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.profit_target = self.config.get(
            "profit_target", 0.005
        )  # 0.5% (increased from 0.2%)
        self.stop_loss = self.config.get(
            "stop_loss", 0.0025
        )  # 0.25% (increased from 0.1%)
        self.use_atr = self.config.get("use_atr", True)  # Use ATR-based stops
        self.atr_multiplier = self.config.get("atr_multiplier", 1.0)  # 1x ATR for stops

    def get_signal(self, features: dict) -> dict | None:
        """Generate scalping signal with improved risk management"""
        close = features.get("close", [])
        candles = features.get("candles", [])

        if not close or len(close) < 10:
            return None

        # Look for small trends
        recent = close[-5:]
        if len(recent) < 3:
            return None

        # Simple momentum check
        if recent[-1] > recent[-2] > recent[-3]:
            current = close[-1]

            # Determine stop loss: use ATR if available, otherwise percentage
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    stop_loss = current - (atr * self.atr_multiplier)
                else:
                    stop_loss = current * (1 - self.stop_loss)
            else:
                stop_loss = current * (1 - self.stop_loss)

            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": stop_loss,
                "target": current * (1 + self.profit_target),
                "reason": "Scalping - upward momentum with ATR-based stop",
            }

        elif recent[-1] < recent[-2] < recent[-3]:
            current = close[-1]

            # Determine stop loss: use ATR if available, otherwise percentage
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    stop_loss = current + (atr * self.atr_multiplier)
                else:
                    stop_loss = current * (1 + self.stop_loss)
            else:
                stop_loss = current * (1 + self.stop_loss)

            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": stop_loss,
                "target": current * (1 - self.profit_target),
                "reason": "Scalping - downward momentum with ATR-based stop",
            }

        return None


class SwingStrategy(StrategyPlugin):
    """Swing trading strategy - hold for days with support/resistance stops"""

    name = "swing"
    description = "Hold positions for 2-5 days with market structure-based stops"
    timeframe = "swing"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.rsi_oversold = self.config.get("rsi_oversold", 30)
        self.rsi_overbought = self.config.get("rsi_overbought", 70)
        self.lookback = self.config.get("lookback", 20)

    def get_signal(self, features: dict) -> dict | None:
        """Generate swing trading signal with market structure"""
        rsi = features.get("rsi")
        trend = features.get("trend", "SIDEWAYS")
        close = features.get("close", [])
        high = features.get("high", [])
        low = features.get("low", [])

        if not rsi or not close:
            return None

        current_price = close[-1]

        # Calculate support and resistance
        lookback_range = min(self.lookback, len(high))
        recent_high = max(high[-lookback_range:]) if high else current_price
        recent_low = min(low[-lookback_range:]) if low else current_price

        if rsi < self.rsi_oversold and trend != "DOWNTREND":
            return {
                "action": "BUY",
                "entry": current_price,
                "stop_loss": recent_low,  # Use recent support as stop
                "target": recent_high,  # Use recent resistance as target
                "reason": f"Swing - RSI oversold ({rsi:.0f}), buying at support",
            }
        elif rsi > self.rsi_overbought and trend != "UPTREND":
            return {
                "action": "SELL",
                "entry": current_price,
                "stop_loss": recent_high,  # Use recent resistance as stop
                "target": recent_low,  # Use recent support as target
                "reason": f"Swing - RSI overbought ({rsi:.0f}), selling at resistance",
            }

        return None


class VWAPStrategy(StrategyPlugin):
    """VWAP Strategy - intraday traded based on VWAP with improved stops"""

    name = "vwap"
    description = "Trade based on VWAP levels with ATR-based stops"
    timeframe = "intraday"
    asset_types = ["EQUITY", "INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.vwap_tolerance = self.config.get("vwap_tolerance", 0.005)
        self.use_atr = self.config.get("use_atr", True)

    def get_signal(self, features: dict) -> dict | None:
        """Generate VWAP-based signal with better stops"""
        vwap = features.get("vwap")
        close = features.get("close", [])
        candles = features.get("candles", [])

        if not vwap or not close:
            return None

        current = close[-1]
        deviation = (current - vwap) / vwap if vwap else 0

        if deviation > self.vwap_tolerance:
            signal = {
                "action": "SELL",
                "entry": current,
                "stop_loss": current * 1.01,
                "target": vwap,
                "reason": f"Above VWAP by {deviation * 100:.1f}%",
            }

            # Improve with ATR if available
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    signal["stop_loss"] = current + (1.5 * atr)

            return signal

        elif deviation < -self.vwap_tolerance:
            signal = {
                "action": "BUY",
                "entry": current,
                "stop_loss": current * 0.99,
                "target": vwap,
                "reason": f"Below VWAP by {abs(deviation) * 100:.1f}%",
            }

            # Improve with ATR if available
            if self.use_atr and candles and len(candles) >= 14:
                highs = [c.get("high", 0) for c in candles]
                lows = [c.get("low", 0) for c in candles]
                atr = EnhancedRiskCalculator.calculate_atr(
                    highs, lows, close, period=14
                )
                if atr:
                    signal["stop_loss"] = current - (1.5 * atr)

            return signal

        return None


class IntradayMomentumStrategy(StrategyPlugin):
    """Intraday Momentum - Opening Range Breakout"""

    name = "intraday_momentum"
    description = "Opening range breakout with volume"
    timeframe = "intraday"
    asset_types = ["EQUITY", "INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.orb_period = self.config.get("orb_period", 15)
        self.volume_filter = self.config.get("volume_filter", 1.5)

    def get_signal(self, features: dict) -> dict | None:
        """Opening Range Breakout signal"""
        open_price = features.get("open", 0)
        high = features.get("high", [])
        low = features.get("low", [])
        close = features.get("close", [])
        volume = features.get("volume", [])

        if not high or len(high) < self.orb_period:
            return None

        or_high = max(high[: self.orb_period])
        or_low = min(low[: self.orb_period])

        current = close[-1]

        if current > or_high:
            avg_vol = sum(volume[-self.orb_period :]) / self.orb_period if volume else 0
            current_vol = volume[-1] if volume else 0

            if current_vol > avg_vol * self.volume_filter:
                return {
                    "action": "BUY",
                    "entry": current,
                    "stop_loss": or_low,
                    "target": current + (or_high - or_low),
                    "reason": f"ORB Breakout above {or_high}",
                }

        elif current < or_low:
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": or_high,
                "target": current - (or_high - or_low),
                "reason": f"ORB Breakdown below {or_low}",
            }

        return None


class MultiTimeframeStrategy(StrategyPlugin):
    """Multi-Timeframe Strategy - trend on HTF, entry on LTF"""

    name = "multitimeframe"
    description = "Higher timeframe trend, lower timeframe entry"
    timeframe = "intraday"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}

    def get_signal(self, features: dict) -> dict | None:
        """Multi-timeframe signal"""
        trend = features.get("trend", "SIDEWAYS")
        rsi = features.get("rsi", 50)
        close = features.get("close", [])
        supertrend = features.get("supertrend", {})

        if not close:
            return None

        current = close[-1]
        st_direction = supertrend.get("direction", "") if supertrend else ""

        if trend == "UPTREND" and rsi < 40 and st_direction == "UPTREND":
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": current * 0.98,
                "target": current * 1.03,
                "reason": "HTF Uptrend + ST bullish",
            }

        elif trend == "DOWNTREND" and rsi > 60 and st_direction == "DOWNTREND":
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": current * 1.02,
                "target": current * 0.97,
                "reason": "HTF downtrend + ST bearish",
            }

        return None


class RelativeStrengthStrategy(StrategyPlugin):
    """Relative Strength vs NIFTY"""

    name = "relative_strength"
    description = "Relative strength vs benchmark"
    timeframe = "swing"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.lookback = self.config.get("lookback", 20)
        self.strength_threshold = self.config.get("strength_threshold", 0.05)

    def get_signal(self, features: dict) -> dict | None:
        """Relative strength signal"""
        stock_return = features.get("return", 0)
        index_return = features.get("index_return", 0)
        close = features.get("close", [])

        if not close:
            return None

        relative_strength = stock_return - index_return
        current = close[-1]

        if relative_strength > self.strength_threshold:
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": current * 0.97,
                "target": current * 1.04,
                "reason": f"Outperforming by {relative_strength * 100:.1f}%",
            }
        elif relative_strength < -self.strength_threshold:
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": current * 1.03,
                "target": current * 0.96,
                "reason": f"Underperforming by {abs(relative_strength) * 100:.1f}%",
            }

        return None


class PullbackStrategy(StrategyPlugin):
    """Pullback Strategy - trend + retracement entry"""

    name = "pullback"
    description = "Enter on pullbacks in trending market"
    timeframe = "swing"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.pullback_threshold = self.config.get("pullback_threshold", 0.03)

    def get_signal(self, features: dict) -> dict | None:
        """Pullback entry signal"""
        trend = features.get("trend", "SIDEWAYS")
        sma_20 = features.get("sma_20") or features.get("sma_21")
        close = features.get("close", [])
        rsi = features.get("rsi", 50)

        if not close or not sma_20:
            return None

        current = close[-1]
        deviation = (sma_20 - current) / sma_20

        if trend == "UPTREND" and deviation > self.pullback_threshold and rsi < 45:
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": current * 0.97,
                "target": current * 1.04,
                "reason": f"Pullback to SMA20 ({deviation * 100:.1f}%)",
            }

        elif trend == "DOWNTREND" and deviation < -self.pullback_threshold and rsi > 55:
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": current * 1.03,
                "target": current * 0.96,
                "reason": "Rally to SMA20",
            }

        return None


class SupertrendStrategy(StrategyPlugin):
    """Supertrend Strategy - robust trend following"""

    name = "supertrend"
    description = "Supertrend-based trend following"
    timeframe = "intraday"
    asset_types = ["EQUITY", "INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}

    def get_signal(self, features: dict) -> dict | None:
        """Supertrend signal"""
        supertrend = features.get("supertrend", {})

        if not supertrend:
            return None

        direction = supertrend.get("direction", "")
        value = supertrend.get("value", 0)
        close = features.get("close", [])

        if not close or not value:
            return None

        current = close[-1]

        if direction == "UPTREND":
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": value,
                "target": current * 1.03,
                "reason": f"Supertrend UP @ {value}",
            }

        elif direction == "DOWNTREND":
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": value,
                "target": current * 0.97,
                "reason": f"Supertrend DOWN @ {value}",
            }

        return None


class MACrossStrategy(StrategyPlugin):
    """Moving Average Crossover (Golden/Death Cross)"""

    name = "ma_crossover"
    description = "SMA50/SMA200 crossover"
    timeframe = "swing"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}

    def get_signal(self, features: dict) -> dict | None:
        """MA crossover signal"""
        sma_50 = features.get("sma_50")
        sma_200 = features.get("sma_200")
        close = features.get("close", [])

        if not sma_50 or not sma_200 or not close:
            return None

        current = close[-1]

        if sma_50 > sma_200 * 1.02:
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": sma_50 * 0.95,
                "target": current * 1.05,
                "reason": "Golden Cross (SMA50 > SMA200)",
            }

        elif sma_50 < sma_200 * 0.98:
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": sma_50 * 1.05,
                "target": current * 0.95,
                "reason": "Death Cross (SMA50 < SMA200)",
            }

        return None


class BollingerBounceStrategy(StrategyPlugin):
    """Bollinger Bands Bounce - mean reversion at bands"""

    name = "bollinger_bounce"
    description = "Trade from Bollinger Bands"
    timeframe = "intraday"
    asset_types = ["EQUITY"]

    def __init__(self, config: dict = None):
        self.config = config or {}

    def get_signal(self, features: dict) -> dict | None:
        """Bollinger bounce signal"""
        bollinger = features.get("bollinger", {})
        close = features.get("close", [])

        if not bollinger or not close:
            return None

        upper = bollinger.get("upper", 0)
        lower = bollinger.get("lower", 0)
        middle = bollinger.get("middle", 0)

        if not upper or not lower:
            return None

        current = close[-1]

        if current < lower:
            return {
                "action": "BUY",
                "entry": current,
                "stop_loss": lower,
                "target": middle,
                "reason": "Bounce from lower BB",
            }

        elif current > upper:
            return {
                "action": "SELL",
                "entry": current,
                "stop_loss": upper,
                "target": middle,
                "reason": "Reversal from upper BB",
            }

        return None


class OIBuildupStrategy(StrategyPlugin):
    """OI Buildup Strategy - Track price vs OI relationship for trend confirmation"""

    name = "oi_buildup"
    description = "Bullish when price and OI both increase, indicating strong momentum"
    timeframe = "intraday"
    asset_types = ["INDEX", "OPTIONS"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.oi_change_threshold = self.config.get("oi_change_threshold", 0.05)

    def analyze(self, data: dict) -> dict | None:
        return self.get_signal(data)

    def get_signal(self, features: dict) -> dict | None:
        price = features.get("close", [])
        oi = features.get("oi", [])

        if not price or len(price) < 2 or not oi or len(oi) < 2:
            return None

        price_change = (price[-1] - price[-2]) / price[-2]
        oi_change = (oi[-1] - oi[-2]) / oi[-2] if oi[-2] != 0 else 0

        if price_change > 0 and oi_change > self.oi_change_threshold:
            return {
                "action": "BUY",
                "entry": price[-1],
                "stop_loss": price[-1] * 0.98,
                "target": price[-1] * 1.03,
                "reason": f"OI Buildup Bullish: price +{price_change:.1%}, OI +{oi_change:.1%}",
            }
        elif price_change < 0 and oi_change > self.oi_change_threshold:
            return {
                "action": "SELL",
                "entry": price[-1],
                "stop_loss": price[-1] * 1.02,
                "target": price[-1] * 0.97,
                "reason": f"OI Buildup Bearish: price {price_change:.1%}, OI +{oi_change:.1%}",
            }

        return None


class PCRReversalStrategy(StrategyPlugin):
    """PCR Reversal Strategy - Use Put-Call Ratio for reversals"""

    name = "pcr_reversal"
    description = "Market may reverse up when PCR > 1.3 (excessive puts)"
    timeframe = "intraday"
    asset_types = ["INDEX", "OPTIONS"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.pcr_threshold = self.config.get("pcr_threshold", 1.3)
        self.pcr_upper = self.config.get("pcr_upper", 1.5)

    def analyze(self, data: dict) -> dict | None:
        return self.get_signal(data)

    def get_signal(self, features: dict) -> dict | None:
        pcr = features.get("pcr", features.get("put_call_ratio", 0))
        price = features.get("close", [])

        if pcr == 0 or not price:
            return None

        current_price = price[-1] if price else 0

        if pcr > self.pcr_threshold:
            return {
                "action": "BUY",
                "entry": current_price,
                "stop_loss": current_price * 0.98,
                "target": current_price * 1.04,
                "reason": f"PCR Reversal: PCR={pcr:.2f} > {self.pcr_threshold} (oversold)",
            }
        elif pcr < 0.7:
            return {
                "action": "SELL",
                "entry": current_price,
                "stop_loss": current_price * 1.02,
                "target": current_price * 0.96,
                "reason": f"PCR Reversal: PCR={pcr:.2f} < 0.7 (overbought)",
            }

        return None


class ExpiryThetaStrategy(StrategyPlugin):
    """Expiry Theta Strategy - Sell options on Thursday to capture theta decay"""

    name = "expiry_theta"
    description = "Sell options on Thursday expiry to capture theta decay"
    timeframe = "intraday"
    asset_types = ["OPTIONS"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.days_to_expiry = self.config.get("days_to_expiry_threshold", 1)

    def analyze(self, data: dict) -> dict | None:
        return self.get_signal(data)

    def get_signal(self, features: dict) -> dict | None:
        now = datetime.datetime.now()

        if now.weekday() != 3:
            return None

        price = features.get("close", [])
        iv = features.get("iv", features.get("volatility", 20))

        if not price:
            return None

        current_price = price[-1] if isinstance(price, list) else price

        if iv and iv > 15:
            return {
                "action": "SELL",
                "entry": current_price,
                "stop_loss": current_price * 1.05,
                "target": current_price * 0.90,
                "reason": f"Theta Decay: IV={iv:.1f}%, Thursday expiry",
            }

        return None


class IndexMomentumStrategy(StrategyPlugin):
    """Index Momentum Strategy - First 15-min breakout for NIFTY/BANKNIFTY"""

    name = "index_momentum"
    description = (
        "Trade first 15-min breakout on NIFTY/BANKNIFTY for strong intraday moves"
    )
    timeframe = "intraday"
    asset_types = ["INDEX"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.lookback = self.config.get("lookback", 5)
        self.momentum_threshold = self.config.get("momentum_threshold", 0.005)

    def analyze(self, data: dict) -> dict | None:
        return self.get_signal(data)

    def get_signal(self, features: dict) -> dict | None:
        now = datetime.datetime.now()

        if now.hour > 9 or (now.hour == 9 and now.minute > 30):
            return None

        high = features.get("high", [])
        low = features.get("low", [])
        close = features.get("close", [])
        open_price = features.get("open", [])

        if not high or len(high) < 3 or not close:
            return None

        current_price = close[-1]
        day_open = open_price[-1] if open_price else current_price

        range_high = max(high[-self.lookback :])
        range_low = min(low[-self.lookback :])

        if current_price > range_high * (1 + self.momentum_threshold):
            return {
                "action": "BUY",
                "entry": current_price,
                "stop_loss": range_low,
                "target": current_price * 1.02,
                "reason": f"Index Momentum Breakout: {current_price} > {range_high}",
            }
        elif current_price < range_low * (1 - self.momentum_threshold):
            return {
                "action": "SELL",
                "entry": current_price,
                "stop_loss": range_high,
                "target": current_price * 0.98,
                "reason": f"Index Momentum Breakdown: {current_price} < {range_low}",
            }

        return None
