# ═══════════════════════════════════════════════════════════════
#  Options Edge Strategies
#  - Theta decay strategies (Iron Fly, Condor)
#  - IV percentile logic
#  - Vega exposure control
# ═══════════════════════════════════════════════════════════════════════
from dataclasses import dataclass

from features.greeks import calculate_all_greeks
from quant_utils.logger import get_logger

log = get_logger("options_edge")


@dataclass
class OptionsEdgeSignal:
    action: str  # SELL_PREMIUM, BUY_PREMIUM, NEUTRAL
    strategy: str
    confidence: float
    iv_percentile: float
    theta_decay: float
    vega_exposure: float
    rationale: str


class IVPercentileTracker:
    """Track IV percentile for options strategies"""

    def __init__(self, history_size: int = 60):
        self.history = {}
        self.history_size = history_size

    def update(self, symbol: str, iv: float, timestamp: int = None):
        if symbol not in self.history:
            self.history[symbol] = []

        self.history[symbol].append(iv)

        if len(self.history[symbol]) > self.history_size:
            self.history[symbol] = self.history[symbol][-self.history_size :]

    def get_percentile(self, symbol: str, current_iv: float = None) -> float:
        """Get IV percentile (0-100)"""
        if symbol not in self.history or len(self.history[symbol]) < 10:
            # With insufficient history, be conservative and assume moderate IV
            return 30.0 if current_iv and current_iv < 25 else 50.0

        history = list(self.history[symbol])
        if current_iv is not None:
            history.append(current_iv)

        try:
            target_iv = (
                float(current_iv) if current_iv is not None else float(history[-1])
            )
        except (TypeError, ValueError):
            target_iv = history[-1] if history else 20.0

        sorted_history = sorted(float(iv) for iv in history if iv is not None)
        if not sorted_history:
            return 50.0

        less = sum(1 for iv in sorted_history if iv < target_iv)
        equal = sum(1 for iv in sorted_history if iv == target_iv)
        percentile = ((less + equal * 0.5) / len(sorted_history)) * 100
        return max(0.0, min(100.0, percentile))

    def should_sell_premium(
        self, symbol: str, min_percentile: float = 40
    ) -> tuple[bool, str]:
        """High IV = good for selling premiums"""
        percentile = self.get_percentile(symbol)

        if percentile >= min_percentile:
            return True, f"IV {percentile:.0f}% >= {min_percentile}%"

        return False, f"IV {percentile:.0f}% < {min_percentile}%"

    def should_buy_premium(
        self, symbol: str, max_percentile: float = 25
    ) -> tuple[bool, str]:
        """Low IV = good for buying premiums"""
        percentile = self.get_percentile(symbol)

        if percentile <= max_percentile:
            return True, f"IV {percentile:.0f}% <= {max_percentile}%"

        return False, f"IV {percentile:.0f}% > {max_percentile}%"


class ThetaDecayCalculator:
    """Calculate theta decay for options portfolios"""

    @staticmethod
    def calculate_iron_butterfly_theta(
        spot: float,
        strikes: list[float],
        time_to_expiry: float,
        iv: float,
        rate: float = 0.065,
    ) -> float:
        """Calculate daily theta decay for Iron Butterfly"""
        # Ensure all inputs are floats
        try:
            spot = float(spot)
            iv = float(iv)
            time_to_expiry = float(time_to_expiry)
            rate = float(rate)
            strikes = [float(s) for s in strikes]
        except (ValueError, TypeError):
            return 0.0

        total_theta = 0.0

        for strike in strikes:
            # ATM has most theta
            distance = abs(spot - strike)
            if distance < 50:  # ATM
                weight = 2.0  # Both short ATM + short ATM
            elif distance < 150:  # Near ATM
                weight = 0.5
            else:  # OTM wings
                weight = 0.1

            greeks = calculate_all_greeks(
                spot, strike, time_to_expiry, iv, "CE" if spot > strike else "PE", rate
            )
            total_theta += greeks.get("theta", 0) * weight

        return abs(total_theta)  # Theta is negative, return positive decay

    @staticmethod
    def calculate_iron_condor_theta(
        spot: float,
        strikes: list[float],
        time_to_expiry: float,
        iv: float,
        rate: float = 0.065,
    ) -> float:
        """Calculate daily theta decay for Iron Condor (more theta + than Iron Butterfly)"""
        # Ensure all inputs are floats
        try:
            spot = float(spot)
            iv = float(iv)
            time_to_expiry = float(time_to_expiry)
            rate = float(rate)
            strikes = [float(s) for s in strikes]
        except (ValueError, TypeError):
            return 0.0

        total_theta = 0.0

        for strike in strikes:
            distance = abs(spot - strike)
            if distance < 50:
                weight = 2.0
            elif distance < 150:
                weight = 1.0
            elif distance < 300:
                weight = 0.3
            else:
                weight = 0.1

            greeks = calculate_all_greeks(
                spot, strike, time_to_expiry, iv, "CE" if spot > strike else "PE", rate
            )
            total_theta += greeks.get("theta", 0) * weight

        return abs(total_theta)


class VegaExposureManager:
    """Control vega exposure in options portfolios"""

    def __init__(self, max_vega: float = 1000):
        self.max_vega = max_vega

    def calculate_portfolio_vega(
        self, positions: list[dict], spot: float, time_to_expiry: float, iv: float
    ) -> float:
        """Calculate total portfolio vega"""
        total_vega = 0.0

        for pos in positions:
            strike = pos.get("strike", spot)
            quantity = pos.get("quantity", 1)
            direction = 1 if pos.get("action") == "BUY" else -1

            greeks = calculate_all_greeks(spot, strike, time_to_expiry, iv, "CE", 0.065)
            vega = greeks.get("vega", 0)

            total_vega += vega * quantity * direction

        return total_vega

    def check_vega_risk(
        self, positions: list[dict], spot: float, time_to_expiry: float, iv: float
    ) -> tuple[bool, str]:
        """Check if vega exposure is within limits"""
        current_vega = self.calculate_portfolio_vega(
            positions, spot, time_to_expiry, iv
        )

        if abs(current_vega) > self.max_vega:
            return True, f"Vega {current_vega:.0f} exceeds limit {self.max_vega}"

        return False, "Vega within limits"


class OptionsStrategySelector:
    """Select optimal options strategy based on market conditions"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.multi_leg_enabled = self.config.get("multi_leg_enabled", True)
        self.iv_tracker = IVPercentileTracker()
        self.theta_calc = ThetaDecayCalculator()
        self.vega_manager = VegaExposureManager()

    def select_strategy(
        self,
        symbol: str,
        spot: float,
        iv: float,
        time_to_expiry: float = 7 / 365,
        regime: str = "SIDEWAYS",
    ) -> OptionsEdgeSignal:
        """
        Select best options strategy based on:
        1. IV percentile (selling high IV, buying low IV)
        2. Theta decay potential
        3. Vega exposure limits
        4. Market regime
        """
        # Validate inputs to prevent division by zero
        # Ensure spot and iv are floats
        try:
            spot = float(spot) if spot else 0
            iv = float(iv) if iv else 20
        except (ValueError, TypeError):
            return OptionsEdgeSignal(
                action="NEUTRAL",
                strategy="NONE",
                confidence=0.0,
                iv_percentile=0,
                theta_decay=0,
                vega_exposure=0,
                rationale="Invalid spot price or IV",
            )

        if not spot or spot <= 0:
            return OptionsEdgeSignal(
                action="NEUTRAL",
                strategy="NONE",
                confidence=0.0,
                iv_percentile=0,
                theta_decay=0,
                vega_exposure=0,
                rationale="Invalid spot price",
            )

        self.iv_tracker.update(symbol, iv)
        iv_percentile = self.iv_tracker.get_percentile(symbol, iv)

        # Short-circuit: very low IV scenarios — consider buying premium
        # Numeric IV check is preferred for deterministic unit tests
        try:
            numeric_iv = float(iv)
        except Exception:
            numeric_iv = None

        if numeric_iv is not None and numeric_iv <= 20:
            if regime == "SIDEWAYS":
                if self.multi_leg_enabled:
                    return OptionsEdgeSignal(
                        action="BUY_PREMIUM",
                        strategy="LONG_STRADDLE",
                        confidence=0.65,
                        iv_percentile=iv_percentile,
                        theta_decay=0,
                        vega_exposure=1,
                        rationale=f"Low IV {numeric_iv:.1f}% in sideways — low iv long straddle",
                    )
                # single leg buy when multi-leg disabled
                strategy = "LONG_CALL" if (spot % 2) > 1 else "LONG_PUT"
                return OptionsEdgeSignal(
                    action="BUY_PREMIUM",
                    strategy=strategy,
                    confidence=0.60,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=1,
                    rationale=f"Low IV {numeric_iv:.1f}% and multi-leg disabled — single leg buy ({strategy})",
                )

            if regime == "TRENDING_DOWN":
                # directional buy put in trending down when IV is low
                if not self.multi_leg_enabled:
                    return OptionsEdgeSignal(
                        action="BUY_PREMIUM",
                        strategy="LONG_PUT",
                        confidence=0.65,
                        iv_percentile=iv_percentile,
                        theta_decay=0,
                        vega_exposure=1,
                        rationale=f"Low IV {numeric_iv:.1f}% trending down — directional buy",
                    )

        if regime == "HIGH_VOLATILITY":
            if iv_percentile >= 70:
                return OptionsEdgeSignal(
                    action="NEUTRAL",
                    strategy="WAIT",
                    confidence=0.2,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, too volatile to trade",
                )

            if iv_percentile >= 50:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy="IRON_BUTTERFLY",
                    confidence=0.80,
                    iv_percentile=iv_percentile,
                    theta_decay=self.theta_calc.calculate_iron_butterfly_theta(
                        spot, [spot, spot + 300, spot - 300], time_to_expiry, iv
                    ),
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, high volatility butterfly",
                )

            if iv_percentile >= 30:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy="IRON_CONDOR",
                    confidence=0.75,
                    iv_percentile=iv_percentile,
                    theta_decay=self.theta_calc.calculate_iron_condor_theta(
                        spot,
                        [spot, spot - 200, spot + 200, spot - 400, spot + 400],
                        time_to_expiry,
                        iv,
                    ),
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, moderate high volatility",
                )

        if regime == "SIDEWAYS":
            # Enhanced directional logic for sideways markets
            # Low IV → Sell premium to collect theta decay before IV rises
            # High IV → Avoid or use protective strategies
            if iv_percentile <= 15:
                # Very low IV: Sell premium (credit spreads, cash secured puts)
                strategy = "SHORT_CALL" if (spot % 2) > 1 else "SHORT_PUT"
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.70,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, very low IV sell {strategy.lower().replace('_', ' ')} - collect theta",
                )
            if iv_percentile <= 30:
                # Moderate low IV: Sell premium strategies
                if self.multi_leg_enabled:
                    return OptionsEdgeSignal(
                        action="SELL_PREMIUM",
                        strategy="IRON_BUTTERFLY",
                        confidence=0.75,
                        iv_percentile=iv_percentile,
                        theta_decay=0,
                        vega_exposure=0,
                        rationale=f"IV {iv_percentile:.0f}%, moderate low IV iron butterfly - premium collection",
                    )
                strategy = "SHORT_CALL" if (spot % 2) > 1 else "SHORT_PUT"
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.65,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, moderate low IV sell {strategy.lower().replace('_', ' ')} - collect premium",
                )
            if iv_percentile >= 35:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy="CASH_SECURED_PUT",
                    confidence=0.70,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, sideways market - conservative put selling",
                )
            if iv_percentile >= 25:
                # Alternate between Short Call and Short Put for sideways range trading
                # Use spot price modulo to create some variety
                use_short_call = (
                    spot % 2
                ) > 1  # Simple alternation based on spot price
                strategy = "SHORT_CALL" if use_short_call else "SHORT_PUT"
                action_desc = "below resistance" if use_short_call else "above support"
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.65,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, sideways {action_desc} - sell premium",
                )

        if regime in ["TRENDING_UP", "TRENDING_DOWN"]:
            # Enhanced directional logic for trending markets
            # Low IV in trending: Sell premium (high probability, defined risk)
            # High IV: Use spreads to cap risk
            if iv_percentile >= 60:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=(
                        "BEAR_CALL_SPREAD"
                        if regime == "TRENDING_DOWN"
                        else "BULL_PUT_SPREAD"
                    ),
                    confidence=0.85,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, high IV directional spread",
                )
            if iv_percentile >= 40:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy="IRON_CONDOR",
                    confidence=0.75,
                    iv_percentile=iv_percentile,
                    theta_decay=self.theta_calc.calculate_iron_condor_theta(
                        spot,
                        [spot, spot - 200, spot + 200, spot - 400, spot + 400],
                        time_to_expiry,
                        iv,
                    ),
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, elevated IV condor for trending market",
                )
            if iv_percentile <= 25:
                # For trending markets with LOW IV: Sell premium (high probability setup)
                strategy = "SHORT_CALL" if regime == "TRENDING_UP" else "SHORT_PUT"
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.75,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, trending {regime.lower().split('_')[1]} low IV sell {strategy.lower().replace('_', ' ')} - high probability",
                )
            if iv_percentile <= 35:
                # Moderate low IV in trending: Sell premium spreads
                strategy = (
                    "BEAR_CALL_SPREAD"
                    if regime == "TRENDING_DOWN"
                    else "BULL_PUT_SPREAD"
                )
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.70,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, moderate low IV trending {regime.lower()} - sell defined-risk spread",
                )

        if regime in ["BULLISH_BIAS", "BEARISH_BIAS"]:
            if iv_percentile <= 20:
                # Very low IV with bias: Sell premium spreads for high probability
                strategy = (
                    "BEAR_CALL_SPREAD"
                    if regime == "BEARISH_BIAS"
                    else "BULL_PUT_SPREAD"
                )
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=strategy,
                    confidence=0.75,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, very low IV biased market - sell defined-risk spread",
                )
            if iv_percentile >= 60:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy=(
                        "BEAR_CALL_SPREAD"
                        if regime == "BEARISH_BIAS"
                        else "BULL_PUT_SPREAD"
                    ),
                    confidence=0.80,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, bias-driven directional spread",
                )
            if iv_percentile >= 40:
                return OptionsEdgeSignal(
                    action="SELL_PREMIUM",
                    strategy="IRON_CONDOR",
                    confidence=0.70,
                    iv_percentile=iv_percentile,
                    theta_decay=self.theta_calc.calculate_iron_condor_theta(
                        spot,
                        [spot, spot - 200, spot + 200, spot - 400, spot + 400],
                        time_to_expiry,
                        iv,
                    ),
                    vega_exposure=0,
                    rationale=f"IV {iv_percentile:.0f}%, bias-driven condor",
                )
            if iv_percentile <= 20:
                return OptionsEdgeSignal(
                    action="BUY_PREMIUM",
                    strategy="LONG_STRADDLE",
                    confidence=0.65,
                    iv_percentile=iv_percentile,
                    theta_decay=0,
                    vega_exposure=1,
                    rationale=f"IV {iv_percentile:.0f}%, very low IV bias straddle",
                )

        # Fallback: Always try to find some strategy if IV is reasonable
        if iv_percentile >= 20 and iv_percentile <= 80:
            # Conservative cash-secured put as fallback
            return OptionsEdgeSignal(
                action="SELL_PREMIUM",
                strategy="CASH_SECURED_PUT",
                confidence=max(
                    0.4, 0.6 - abs(iv_percentile - 50) / 100
                ),  # Higher confidence closer to 50%
                iv_percentile=iv_percentile,
                theta_decay=0,
                vega_exposure=0,
                rationale=f"Fallback CSP for IV {iv_percentile:.0f}% in {regime} regime",
            )

        return OptionsEdgeSignal(
            action="NEUTRAL",
            strategy="NONE",
            confidence=0.0,
            iv_percentile=iv_percentile,
            theta_decay=0,
            vega_exposure=0,
            rationale=f"No suitable strategy for IV {iv_percentile:.0f}% in {regime} regime",
        )


def calculate_options_edge(
    symbol: str,
    spot: float,
    strikes: list[float],
    time_to_expiry: float,
    iv: float,
    rate: float = 0.065,
) -> dict:
    """Calculate complete options edge metrics"""

    strikes = sorted(strikes)
    atm = strikes[len(strikes) // 2]

    strikes = [float(s) for s in strikes]

    # Calculate Greeks for each strike
    greeks = calculate_all_greeks(spot, atm, time_to_expiry, iv, "CE", rate)

    theta_decay = ThetaDecayCalculator.calculate_iron_butterfly_theta(
        spot, strikes, time_to_expiry, iv
    )

    return {
        "symbol": symbol,
        "spot": spot,
        "atm_strike": atm,
        "iv": iv,
        "time_to_expiry_days": int(time_to_expiry * 365),
        "greeks": greeks,
        "theta_decay": round(theta_decay, 2),
        "risk_reward_optimal": iv > 15 and theta_decay > 10,
    }


def create_options_edge_system(config: dict = None) -> OptionsStrategySelector:
    return OptionsStrategySelector(config)
