"""
Enhanced Risk Management Module
Calculates stop-loss and targets using support/resistance levels instead of fixed percentages.
This addresses the high stop-loss hit rate by anchoring levels to market structure.
"""

import numpy as np

from quant_utils.logger import get_logger

try:
    import talib

    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False

log = get_logger("strategy.enhanced_risk")


class EnhancedRiskCalculator:
    """Calculate dynamic stop-loss and targets based on support/resistance"""

    # Configuration constants
    MIN_RISK_REWARD_RATIO = 1.5  # Minimum 1.5:1 reward to risk
    PIVOT_LOOKBACK = 20  # Lookback period for pivot points
    ATR_PERIOD = 14  # ATR period for volatility

    @staticmethod
    def calculate_atr(
        highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> float | None:
        """Calculate Average True Range for volatility"""
        if len(highs) < period:
            return None
        try:
            if HAS_TALIB:
                return talib.ATR(
                    np.array(highs), np.array(lows), np.array(closes), timeperiod=period
                )[-1]
        except:
            pass

        # Fallback: manual ATR calculation
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period if len(trs) >= period else None

    @staticmethod
    def calculate_pivot_points(
        high: float, low: float, close: float, pivot_type: str = "CLASSIC"
    ) -> dict[str, float]:
        """Calculate pivot points with multiple resistance/support levels"""
        if pivot_type == "CLASSIC":
            pp = (high + low + close) / 3
            r1 = 2 * pp - low
            r2 = pp + (high - low)
            r3 = high + 2 * (pp - low)
            s1 = 2 * pp - high
            s2 = pp - (high - low)
            s3 = low - 2 * (high - pp)

        elif pivot_type == "FIBONACCI":
            pp = (high + low + close) / 3
            r1 = pp + (high - low) * 0.382
            r2 = pp + (high - low) * 0.618
            r3 = pp + (high - low)
            s1 = pp - (high - low) * 0.382
            s2 = pp - (high - low) * 0.618
            s3 = pp - (high - low)

        else:  # CAMARILLA
            pp = (high + low + close) / 3
            r1 = close + (high - low) * 0.11
            r2 = close + (high - low) * 0.183
            r3 = close + (high - low) * 0.25
            s1 = close - (high - low) * 0.11
            s2 = close - (high - low) * 0.183
            s3 = close - (high - low) * 0.25

        return {"pp": pp, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3}

    @staticmethod
    def find_support_resistance(
        candles: list[dict], lookback: int = 20
    ) -> tuple[float, float]:
        """Find nearest support and resistance from historical data"""
        if len(candles) < lookback:
            return None, None

        highs = [c.get("high", 0) for c in candles[-lookback:]]
        lows = [c.get("low", 0) for c in candles[-lookback:]]

        resistance = max(highs)
        support = min(lows)

        return support, resistance

    @staticmethod
    def calculate_sl_tp_for_breakout(
        entry: float,
        signal_type: str,  # "BUY" or "SELL"
        candles: list[dict],
        current_price: float,
        use_atr: bool = True,
    ) -> tuple[float, float]:
        """
        Calculate SL and TP for breakout trades using support/resistance

        Args:
            entry: Entry price
            signal_type: "BUY" or "SELL"
            candles: Historical candle data
            current_price: Current market price
            use_atr: Use ATR for dynamic stops

        Returns:
            Tuple of (stop_loss, target)
        """
        if len(candles) < 20:
            return None, None

        highs = [c.get("high", 0) for c in candles]
        lows = [c.get("low", 0) for c in candles]
        closes = [c.get("close", 0) for c in candles]

        # Get pivot points from last day
        high_prev = highs[-2] if len(highs) >= 2 else highs[-1]
        low_prev = lows[-2] if len(lows) >= 2 else lows[-1]
        close_prev = closes[-1]

        pivots = EnhancedRiskCalculator.calculate_pivot_points(
            high_prev, low_prev, close_prev, pivot_type="FIBONACCI"
        )

        # Get ATR for volatility adjustment
        atr = (
            EnhancedRiskCalculator.calculate_atr(highs, lows, closes, period=14)
            if use_atr
            else None
        )

        if signal_type.upper() == "BUY":
            # For long trades:
            # Stop: Below nearest support or ATR-adjusted support
            # Target: At resistance or 1.5x risk

            support = min(lows[-20:])
            resistance = max(highs[-20:])

            # Stop loss: Either recent support or ATR below entry
            if atr:
                stop_loss = entry - atr
                stop_loss = max(stop_loss, support)  # Don't go below support
            else:
                stop_loss = support

            # Target: At resistance or R1 level, whichever is further
            target = max(resistance, pivots.get("r1", resistance))

            # Ensure minimum R:R ratio
            risk = entry - stop_loss
            reward = target - entry
            if reward < risk * 1.5:  # If reward < 1.5x risk
                target = entry + (risk * 1.5)

        else:  # SELL
            # For short trades:
            # Stop: Above nearest resistance or ATR-adjusted resistance
            # Target: At support or 1.5x risk

            support = min(lows[-20:])
            resistance = max(highs[-20:])

            # Stop loss: Either recent resistance or ATR above entry
            if atr:
                stop_loss = entry + atr
                stop_loss = min(stop_loss, resistance)  # Don't go above resistance
            else:
                stop_loss = resistance

            # Target: At support or S1 level
            target = min(support, pivots.get("s1", support))

            # Ensure minimum R:R ratio
            risk = stop_loss - entry
            reward = entry - target
            if reward < risk * 1.5:
                target = entry - (risk * 1.5)

        return stop_loss, target

    @staticmethod
    def calculate_sl_tp_for_mean_reversion(
        entry: float,
        signal_type: str,
        target_ma: float,
        candles: list[dict],
        use_atr: bool = True,
    ) -> tuple[float, float]:
        """
        Calculate SL and TP for mean reversion trades

        Args:
            entry: Entry price
            signal_type: "BUY" or "SELL"
            target_ma: Target moving average level
            candles: Historical candle data
            use_atr: Use ATR for stops

        Returns:
            Tuple of (stop_loss, target)
        """
        if len(candles) < 14:
            return None, None

        highs = [c.get("high", 0) for c in candles]
        lows = [c.get("low", 0) for c in candles]
        closes = [c.get("close", 0) for c in candles]

        atr = (
            EnhancedRiskCalculator.calculate_atr(highs, lows, closes, period=14)
            if use_atr
            else None
        )

        if signal_type.upper() == "BUY":
            # Buying at support, target is MA
            target = target_ma

            # Stop: 2 ATR below entry or 5-period low
            if atr:
                stop_loss = entry - (2 * atr)
            else:
                stop_loss = min(lows[-5:])

            # Ensure minimum R:R ratio
            risk = entry - stop_loss
            reward = target - entry
            if reward < risk * 1.5 and reward > 0:
                target = entry + (risk * 1.5)

        else:  # SELL
            # Selling at resistance, target is MA
            target = target_ma

            # Stop: 2 ATR above entry or 5-period high
            if atr:
                stop_loss = entry + (2 * atr)
            else:
                stop_loss = max(highs[-5:])

            # Ensure minimum R:R ratio
            risk = stop_loss - entry
            reward = entry - target
            if reward < risk * 1.5 and reward > 0:
                target = entry - (risk * 1.5)

        return stop_loss, target

    @staticmethod
    def adjust_sl_for_volatility(
        sl: float, entry: float, atr: float, signal_type: str = "BUY"
    ) -> float:
        """Adjust stop loss for high volatility environments"""
        distance = abs(entry - sl)

        # If SL is too tight (less than 1 ATR), expand it
        if distance < atr:
            if signal_type.upper() == "BUY":
                sl = entry - atr
            else:
                sl = entry + atr
            log.warning(f"Expanded SL to {sl:.2f} (1 ATR from entry) due to volatility")

        return sl


def enhance_signal_with_sr_levels(signal: dict, candles: list[dict]) -> dict:
    """
    Wrapper function to enhance any trading signal with support/resistance based SL/TP

    Args:
        signal: Original signal dict with action, entry, etc.
        candles: Historical candle data

    Returns:
        Enhanced signal with improved stop_loss and target
    """
    if not signal or not candles or len(candles) < 20:
        return signal

    entry = signal.get("entry")
    action = signal.get("action", "").upper()
    current_price = candles[-1].get("close", 0)

    if not entry or action not in ["BUY", "SELL"]:
        return signal

    try:
        # Calculate SL and TP using support/resistance
        sl, tp = EnhancedRiskCalculator.calculate_sl_tp_for_breakout(
            entry=entry,
            signal_type=action,
            candles=candles,
            current_price=current_price,
            use_atr=True,
        )

        if sl and tp:
            # Calculate risk reward ratio
            if action == "BUY":
                risk = entry - sl
                reward = tp - entry
            else:
                risk = sl - entry
                reward = entry - tp

            rr_ratio = reward / risk if risk > 0 else 0

            # Log the enhancement
            original_sl = signal.get("stop_loss", 0)
            original_tp = signal.get("target", 0)

            log.info(f"Enhanced {action} signal {signal.get('symbol', '')}:")
            log.info(
                f"  SL: {original_sl:.2f} → {sl:.2f} (R:R ratio: {rr_ratio:.2f}:1)"
            )
            log.info(f"  TP: {original_tp:.2f} → {tp:.2f}")

            # Update signal
            signal["stop_loss"] = sl
            signal["target"] = tp
            signal["risk_reward_ratio"] = rr_ratio
            signal["sl_based_on"] = "support_resistance_atr"

    except Exception as e:
        log.error(f"Error enhancing signal: {e}")

    return signal
