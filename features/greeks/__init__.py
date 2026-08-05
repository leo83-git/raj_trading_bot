# ═══════════════════════════════════════════════════════════════════════
#  Options Greeks — Black-Scholes & Portfolio Greeks
# ═══════════════════════════════════════════════════════════════════════
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("features.greeks")


def _normal_cdf(x: float) -> float:
    """Standard normal CDF (Abramowitz & Stegun approximation)"""
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


class BlackScholesModel:
    """Black-Scholes-Merton option pricing with accurate Greeks"""

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
        self._d1 = 0.0
        self._d2 = 0.0
        self._calculate_d1_d2()

    def _calculate_d1_d2(self):
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            self._d1 = 0
            self._d2 = 0
            return

        v_sqrt_t = self.volatility * math.sqrt(self.time_to_expiry)

        self._d1 = (
            math.log(self.spot / self.strike)
            + (self.risk_free_rate - self.dividend_yield + 0.5 * self.volatility**2)
            * self.time_to_expiry
        ) / v_sqrt_t
        self._d2 = self._d1 - v_sqrt_t

    def price(self, opt_type: str = "CE") -> float:
        if self.time_to_expiry <= 0:
            if opt_type == "CE":
                return max(0, self.spot - self.strike)
            return max(0, self.strike - self.spot)

        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)
        nd1 = _normal_cdf(self._d1)
        nd2 = _normal_cdf(self._d2)

        if opt_type == "CE":
            return (
                self.spot * math.exp(-self.dividend_yield * self.time_to_expiry) * nd1
                - self.strike * discount * nd2
            )
        return self.strike * discount * _normal_cdf(-self._d2) - self.spot * math.exp(
            -self.dividend_yield * self.time_to_expiry
        ) * _normal_cdf(-self._d1)

    def delta(self, opt_type: str = "CE") -> float:
        if self.time_to_expiry <= 0:
            if opt_type == "CE":
                return 1.0 if self.spot > self.strike else 0.0
            return -1.0 if self.spot < self.strike else 0.0

        nd1 = _normal_cdf(self._d1)

        if opt_type == "CE":
            return math.exp(-self.dividend_yield * self.time_to_expiry) * nd1
        return -math.exp(-self.dividend_yield * self.time_to_expiry) * _normal_cdf(
            -self._d1
        )

    def gamma(self) -> float:
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            return 0
        return (
            math.exp(-self.dividend_yield * self.time_to_expiry) * _normal_pdf(self._d1)
        ) / (self.spot * self.volatility * math.sqrt(self.time_to_expiry))

    def theta(self, opt_type: str = "CE") -> float:
        if self.time_to_expiry <= 0:
            return 0

        v_sqrt_t = self.volatility * math.sqrt(self.time_to_expiry)
        term1 = -(
            self.spot
            * _normal_pdf(self._d1)
            * self.volatility
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        ) / (2 * math.sqrt(self.time_to_expiry))

        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)

        if opt_type == "CE":
            term2 = (
                -self.risk_free_rate * self.strike * discount * _normal_cdf(self._d2)
            )
            term3 = (
                self.dividend_yield
                * self.spot
                * math.exp(-self.dividend_yield * self.time_to_expiry)
                * _normal_cdf(self._d1)
            )
        else:
            term2 = (
                self.risk_free_rate * self.strike * discount * _normal_cdf(-self._d2)
            )
            term3 = (
                self.dividend_yield
                * self.spot
                * math.exp(-self.dividend_yield * self.time_to_expiry)
                * _normal_cdf(-self._d1)
            )

        return (term1 + term2 + term3) / 365

    def vega(self) -> float:
        if self.time_to_expiry <= 0:
            return 0
        v_sqrt_t = math.sqrt(self.time_to_expiry)
        return (
            self.spot
            * v_sqrt_t
            * _normal_pdf(self._d1)
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        ) / 100

    def rho(self, opt_type: str = "CE") -> float:
        if self.time_to_expiry <= 0:
            return 0
        discount = math.exp(-self.risk_free_rate * self.time_to_expiry)
        if opt_type == "CE":
            return (
                self.strike
                * self.time_to_expiry
                * discount
                * _normal_cdf(self._d2)
                / 100
            )
        return (
            -self.strike * self.time_to_expiry * discount * _normal_cdf(-self._d2) / 100
        )

    def vanna(self) -> float:
        if self.time_to_expiry <= 0 or self.volatility <= 0:
            return 0
        v_sqrt_t = math.sqrt(self.time_to_expiry)
        return (
            -_normal_pdf(self._d1)
            * (self._d2 / self.volatility)
            * math.exp(-self.dividend_yield * self.time_to_expiry)
        )

    def charm(self, opt_type: str = "CE") -> float:
        if self.time_to_expiry <= 0:
            return 0

        nd1 = _normal_cdf(self._d1)

        if opt_type == "CE":
            charm = -math.exp(-self.dividend_yield * self.time_to_expiry) * (
                self.dividend_yield * nd1
                - _normal_pdf(self._d1) * (self._d2 / (2 * self.time_to_expiry))
            )
        else:
            charm = math.exp(-self.dividend_yield * self.time_to_expiry) * (
                self.dividend_yield * _normal_cdf(-self._d1)
                + _normal_pdf(self._d1) * (self._d2 / (2 * self.time_to_expiry))
            )

        return charm / 365

    def get_all_greeks(self, opt_type: str = "CE") -> OptionGreeks:
        return OptionGreeks(
            price=self.price(opt_type),
            delta=self.delta(opt_type),
            gamma=self.gamma(),
            theta=self.theta(opt_type),
            vega=self.vega(),
            rho=self.rho(opt_type),
            vanna=self.vanna(),
            charm=self.charm(opt_type),
        )


def black_scholes_price(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.price(opt_type)


def calculate_delta(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.delta(opt_type)


def calculate_gamma(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.gamma()


def calculate_vega(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.vega()


def calculate_theta(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.theta(opt_type)


def calculate_rho(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> float:
    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return bs.rho(opt_type)


def calculate_all_greeks(
    price: float,
    strike: float,
    time_to_expiry: float,
    volatility: float,
    risk_free_rate: float = 0.065,
    opt_type: str = "CE",
) -> dict:
    # Ensure all inputs are floats
    try:
        price = float(price)
        strike = float(strike)
        time_to_expiry = float(time_to_expiry)
        volatility = float(volatility)
        risk_free_rate = float(risk_free_rate)
    except (ValueError, TypeError):
        return {
            "price": 0,
            "delta": 0,
            "gamma": 0,
            "theta": 0,
            "vega": 0,
            "rho": 0,
            "vanna": 0,
        }

    bs = BlackScholesModel(price, strike, time_to_expiry, volatility, risk_free_rate)
    return {
        "price": round(bs.price(opt_type), 2),
        "delta": round(bs.delta(opt_type), 4),
        "gamma": round(bs.gamma(), 6),
        "theta": round(bs.theta(opt_type), 4),
        "vega": round(bs.vega(), 4),
        "rho": round(bs.rho(opt_type), 4),
        "vanna": round(bs.vanna(), 4),
    }


def calculate_portfolio_greeks(positions: list[dict]) -> dict:
    total_delta = 0.0
    total_gamma = 0.0
    total_vega = 0.0
    total_theta = 0.0

    for pos in positions:
        quantity = pos.get("quantity", 0)
        direction = 1 if pos.get("action", "").upper() in ["BUY", "LONG"] else -1

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
        "is_delta_neutral": abs(total_delta) < 0.01,
    }


def hedge_delta(
    current_delta: float, target_delta: float = 0, lot_size: int = 50
) -> dict:
    hedge_needed = target_delta - current_delta

    contracts = round(hedge_needed * lot_size)

    return {
        "hedge_delta": round(hedge_needed, 4),
        "action": "BUY" if contracts > 0 else "SELL",
        "contracts": abs(contracts),
        "rebalance_needed": abs(hedge_needed) > 0.01,
    }


def hedge_gamma(
    positions: list[dict],
    target_gamma: float = 0,
    spot: float = 22000,
    strikes: list[int] = None,
) -> dict:
    """Calculate gamma hedge recommendations"""
    portfolio_greeks = calculate_portfolio_greeks(positions)
    current_gamma = portfolio_greeks.get("gamma", 0)

    if abs(current_gamma) > target_gamma:
        hedge_action = "SELL" if current_gamma > 0 else "BUY"
        return {
            "action": hedge_action,
            "reason": f"Gamma={current_gamma:.6f} exceeds target {target_gamma}",
            "current_gamma": current_gamma,
            "recommended": True,
        }

    return {"recommended": False, "current_gamma": current_gamma}


def optimize_hedge_ratios(
    positions: list[dict],
    target_delta: float = 0,
    target_gamma: float = 0,
    target_theta: float = 0,
) -> dict:
    """Optimize hedge ratios for target Greeks"""
    portfolio = calculate_portfolio_greeks(positions)

    recommendations = []

    if abs(portfolio.get("delta", 0)) > abs(target_delta):
        hedge = hedge_delta(portfolio.get("delta", 0), target_delta)
        if hedge.get("rebalance_needed"):
            recommendations.append(
                {
                    "type": "delta",
                    "action": hedge["action"],
                    "contracts": hedge["contracts"],
                }
            )

    if abs(portfolio.get("gamma", 0)) > target_gamma:
        gamma_hedge = hedge_gamma(positions, target_gamma)
        if gamma_hedge.get("recommended"):
            recommendations.append(
                {
                    "type": "gamma",
                    "action": gamma_hedge["action"],
                    "reason": gamma_hedge["reason"],
                }
            )

    return {
        "current": portfolio,
        "targets": {
            "delta": target_delta,
            "gamma": target_gamma,
            "theta": target_theta,
        },
        "recommendations": recommendations,
        "hedging_advised": len(recommendations) > 0,
    }
