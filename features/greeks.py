# ═══════════════════════════════════════════════════════════════
#  Feature Engine — Greeks Engine (Options Pricing)
#  Uses proper Black-Scholes-Merton model for accurate Greeks
# ═══════════════════════════════════════════════════════════════════════
import math
from dataclasses import dataclass

from quant_utils.logger import get_logger

log = get_logger("features.greeks")

QUANTLIB_AVAILABLE = False


def _normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function (Abramowitz & Stegun approximation)"""
    if x < 0:
        return 1 - _normal_cdf(-x)

    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)

    return 0.5 * (1.0 + sign * y)


def _normal_pdf(x: float) -> float:
    """Standard normal probability density function"""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


@dataclass
class OptionGreeks:
    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float = 0
    vanna: float = 0
    charm: float = 0
    speed: float = 0
    zomma: float = 0
    volga: float = 0


class BlackScholesModel:
    """Black-Scholes-Merton option pricing model with accurate Greeks"""

    def __init__(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        risk_free_rate: float = 0.065,
        dividend_yield: float = 0.0,
    ):
        self.spot = spot
        self.strike = strike
        self.time_to_expiry = time_to_expiry
        self.volatility = volatility
        self.risk_free_rate = risk_free_rate
        self.dividend_yield = dividend_yield

        self._calculate_d1_d2()

    def _calculate_d1_d2(self):
        """Calculate d1 and d2 parameters"""
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            self.d1 = 0
            self.d2 = 0
            return

        v_sqrt_t = self.volatility * math.sqrt(self.time_to_expiry)

        self.d1 = (
            math.log(self.spot / self.strike)
            + (self.risk_free_rate - self.dividend_yield + 0.5 * self.volatility**2)
            * self.time_to_expiry
        ) / v_sqrt_t

        self.d2 = self.d1 - v_sqrt_t

    def price(self, opt_type: str = "CE") -> float:
        """Calculate option price using Black-Scholes"""
        if self.time_to_expiry <= 0:
            if opt_type == "CE":
                return max(0, self.spot - self.strike)
            else:
                return max(0, self.strike - self.spot)

        if self.volatility <= 0:
            intrinsic = (
                max(0, self.spot - self.strike)
                if opt_type == "CE"
                else max(0, self.strike - self.spot)
            )
            return intrinsic

        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)
        nd1 = _normal_cdf(self.d1)
        nd2 = _normal_cdf(self.d2)
        nmd1 = _normal_cdf(-self.d1)
        nmd2 = _normal_cdf(-self.d2)

        if opt_type == "CE":
            price = (
                self.spot * math.exp(-self.dividend_yield * self.time_to_expiry) * nd1
                - self.strike * discount * nd2
            )
        else:
            price = (
                self.strike * discount * nmd2
                - self.spot
                * math.exp(-self.dividend_yield * self.time_to_expiry)
                * nmd1
            )

        return max(0, price)

    def delta(self, opt_type: str = "CE") -> float:
        """Calculate delta (∂V/∂S)"""
        if self.time_to_expiry <= 0:
            if opt_type == "CE":
                return 1.0 if self.spot > self.strike else 0.0
            else:
                return -1.0 if self.spot < self.strike else 0.0

        if self.volatility <= 0:
            return 0

        nd1 = _normal_cdf(self.d1)

        if opt_type == "CE":
            return math.exp(-self.dividend_yield * self.time_to_expiry) * nd1
        else:
            return -math.exp(-self.dividend_yield * self.time_to_expiry) * _normal_cdf(
                -self.d1
            )

    def gamma(self) -> float:
        """Calculate gamma (∂²V/∂S²)"""
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            return 0

        return (
            math.exp(-self.dividend_yield * self.time_to_expiry) * _normal_pdf(self.d1)
        ) / (self.spot * self.volatility * math.sqrt(self.time_to_expiry))

    def theta(self, opt_type: str = "CE") -> float:
        """Calculate theta (∂V/∂t) in per-day terms"""
        if self.time_to_expiry <= 0:
            return 0

        v_sqrt_t = self.volatility * math.sqrt(self.time_to_expiry)

        term1 = -(
            self.spot
            * _normal_pdf(self.d1)
            * self.volatility
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        ) / (2 * math.sqrt(self.time_to_expiry))

        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)

        if opt_type == "CE":
            term2 = -self.risk_free_rate * self.strike * discount * _normal_cdf(self.d2)
            term3 = (
                self.dividend_yield
                * self.spot
                * math.exp(-self.dividend_yield * self.time_to_expiry)
                * _normal_cdf(self.d1)
            )
        else:
            term2 = self.risk_free_rate * self.strike * discount * _normal_cdf(-self.d2)
            term3 = (
                self.dividend_yield
                * self.spot
                * math.exp(-self.dividend_yield * self.time_to_expiry)
                * _normal_cdf(-self.d1)
            )

        theta_per_year = term1 + term2 + term3
        theta_per_day = theta_per_year / 365

        return theta_per_day

    def vega(self) -> float:
        """Calculate vega (∂V/∂σ) - sensitivity to 1% change in volatility"""
        if self.time_to_expiry <= 0:
            return 0

        v_sqrt_t = math.sqrt(self.time_to_expiry)
        vega_raw = (
            self.spot
            * v_sqrt_t
            * _normal_pdf(self.d1)
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        )

        return vega_raw / 100

    def rho(self, opt_type: str = "CE") -> float:
        """Calculate rho (∂V/∂r) - sensitivity to 1% change in rate"""
        if self.time_to_expiry <= 0:
            return 0

        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)

        if opt_type == "CE":
            rho = self.strike * self.time_to_expiry * discount * _normal_cdf(self.d2)
        else:
            rho = -self.strike * self.time_to_expiry * discount * _normal_cdf(-self.d2)

        return rho / 100

    def vanna(self) -> float:
        """Calculate vanna (∂²V/∂S∂σ)"""
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            return 0

        v_sqrt_t = math.sqrt(self.time_to_expiry)
        return (
            -_normal_pdf(self.d1)
            * (self.d2 / self.volatility)
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        )

    def charm(self, opt_type: str = "CE") -> float:
        """Calculate charm (∂²V/∂S∂t)"""
        if self.time_to_expiry <= 0:
            return 0

        nd1 = _normal_cdf(self.d1)

        if opt_type == "CE":
            charm = -math.exp(-self.dividend_yield * self.time_to_expiry) * (
                self.dividend_yield * nd1
                - _normal_pdf(self.d1) * (self.d2 / (2 * self.time_to_expiry))
            )
        else:
            charm = math.exp(-self.dividend_yield * self.time_to_expiry) * (
                self.dividend_yield * _normal_cdf(-self.d1)
                + _normal_pdf(self.d1) * (self.d2 / (2 * self.time_to_expiry))
            )

        return charm / 365

    def speed(self) -> float:
        """Calculate speed (∂³V/∂S³)"""
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            return 0

        v_sqrt_t = self.volatility * math.sqrt(self.time_to_expiry)
        return -_normal_pdf(self.d1) * (self.d1 + self.d2) / (self.spot**2 * v_sqrt_t)

    def get_all_greeks(self, opt_type: str = "CE") -> OptionGreeks:
        """Calculate all Greeks and price"""
        return OptionGreeks(
            price=self.price(opt_type),
            delta=self.delta(opt_type),
            gamma=self.gamma(),
            theta=self.theta(opt_type),
            vega=self.vega(),
            rho=self.rho(opt_type),
            vanna=self.vanna(),
            charm=self.charm(opt_type),
            speed=self.speed(),
            volga=self.vega(),
        )


def calculate_delta(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    opt_type: str = "CE",
    risk_free_rate: float = 0.065,
) -> float:
    """Calculate accurate delta using Black-Scholes"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.delta(opt_type)


def calculate_gamma(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
) -> float:
    """Calculate accurate gamma using Black-Scholes"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.gamma()


def calculate_theta(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    opt_type: str = "CE",
    risk_free_rate: float = 0.065,
) -> float:
    """Calculate accurate theta using Black-Scholes"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.theta(opt_type)


def calculate_vega(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
) -> float:
    """Calculate accurate vega using Black-Scholes"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.vega()


def black_scholes_price(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> float:
    """Calculate accurate Black-Scholes option price"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.price(opt_type)


def calculate_all_greeks(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    opt_type: str = "CE",
    risk_free_rate: float = 0.065,
) -> dict:
    """Calculate all Greeks using accurate Black-Scholes model"""
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    greeks = bs.get_all_greeks(opt_type)

    return {
        "price": round(greeks.price, 2),
        "delta": round(greeks.delta, 4),
        "gamma": round(greeks.gamma, 6),
        "theta": round(greeks.theta, 4),
        "vega": round(greeks.vega, 4),
        "rho": round(greeks.rho, 4),
        "vanna": round(greeks.vanna, 4),
        "charm": round(greeks.charm, 4),
    }


class DeltaHedger:
    """Dynamic delta hedging"""

    def __init__(self, target_delta: float = 0, rebalance_threshold: float = 0.05):
        self.target_delta = target_delta
        self.rebalance_threshold = rebalance_threshold

    def should_rebalance(self, current_delta: float) -> bool:
        """Check if delta rebalancing is needed"""
        delta_deviation = abs(current_delta - self.target_delta)
        return delta_deviation > self.rebalance_threshold

    def calculate_hedge(
        self, current_delta: float, spot: float, lot_size: int = 50
    ) -> dict:
        """Calculate hedge quantities needed"""
        hedge_delta = self.target_delta - current_delta

        contracts_needed = hedge_delta * lot_size
        contracts_rounded = round(contracts_needed)

        return {
            "action": "BUY" if contracts_rounded > 0 else "SELL",
            "contracts": abs(contracts_rounded),
            "hedge_delta": hedge_delta,
            "current_delta": current_delta,
            "target_delta": self.target_delta,
            "rebalance_needed": self.should_rebalance(current_delta),
        }


def calculate_portfolio_greeks(positions: list) -> dict:
    """Calculate portfolio-level Greeks from positions"""
    total_delta = 0
    total_gamma = 0
    total_vega = 0
    total_theta = 0

    for pos in positions:
        direction = 1 if pos.get("action") == "BUY" else -1
        quantity = pos.get("quantity", 0)

        greeks = pos.get("greeks", {})

        total_delta += greeks.get("delta", 0) * quantity * direction
        total_gamma += greeks.get("gamma", 0) * quantity * direction
        total_vega += greeks.get("vega", 0) * quantity * direction
        total_theta += greeks.get("theta", 0) * quantity * direction

    return {
        "delta": round(total_delta, 4),
        "gamma": round(total_gamma, 6),
        "vega": round(total_vega, 4),
        "theta": round(total_theta, 4),
        "is_neutral": abs(total_delta) < 0.01,
    }


def optimize_hedge_ratios(
    portfolio: list, target_gamma: float = 0, target_theta: float = None
) -> dict:
    """Optimize hedge ratios for target Greeks"""
    current = calculate_portfolio_greeks(portfolio)

    recommendations = []

    if abs(current["gamma"]) > target_gamma and target_gamma == 0:
        recommendations.append(
            {
                "action": "BUY" if current["gamma"] < 0 else "SELL",
                "type": "gamma_hedge",
                "reason": f"Gamma={current['gamma']:.6f} exceeds target",
            }
        )

    if target_theta and current["theta"] < target_theta:
        recommendations.append(
            {
                "action": "SELL",
                "type": "theta_boost",
                "reason": f"Theta={current['theta']:.4f} below target {target_theta}",
            }
        )

    return {
        "current": current,
        "target_gamma": target_gamma,
        "target_theta": target_theta,
        "recommendations": recommendations,
        "hedging_advised": len(recommendations) > 0,
    }


def hedge_delta(
    current_delta: float, target_delta: float = 0, lot_size: int = 50
) -> dict:
    """Calculate delta hedge required"""
    hedger = DeltaHedger(target_delta)
    return hedger.calculate_hedge(current_delta, 0, lot_size)
