# ═══════════════════════════════════════════════════════════════
#  Portfolio Optimization — Risk Parity & Markowitz
# ═══════════════════════════════════════════════════════════════
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("portfolio.optimization")


class PortfolioOptimizer:
    """Portfolio optimization using risk parity and Markowitz"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.max_positions = self.config.get("max_positions", 10)
        self.max_weight = self.config.get("max_weight", 0.25)

    def risk_parity(self, returns: np.ndarray, symbols: list[str]) -> dict[str, float]:
        """Risk parity allocation"""
        try:
            cov_matrix = np.cov(returns.T)

            if cov_matrix.shape[0] == 0:
                return self._equal_weight(symbols)

            n = len(symbols)
            target_vol = 0.15

            weights = np.ones(n) / n

            for _ in range(100):
                portfolio_vol = np.sqrt(weights @ cov_matrix @ weights)

                marginal_risk = cov_matrix @ weights
                risk_contrib = weights * marginal_risk / portfolio_vol

                target_risk = portfolio_vol * np.ones(n) / n

                weights = weights * (target_risk / (risk_contrib + 1e-8))
                weights = weights / weights.sum()

                weights = np.clip(weights, 0.01, self.max_weight)
                weights = weights / weights.sum()

            result = {sym: float(w) for sym, w in zip(symbols, weights)}
            log.info(f"Risk parity weights: {result}")
            return result

        except Exception as e:
            log.warning(f"Risk parity failed: {e}")
            return self._equal_weight(symbols)

    def markowitz(
        self, returns: np.ndarray, symbols: list[str], target_return: float = 0.01
    ) -> dict[str, float]:
        """Markowitz mean-variance optimization"""
        try:
            mean_returns = np.mean(returns, axis=0)
            cov_matrix = np.cov(returns.T)

            n = len(symbols)

            from scipy.optimize import minimize

            def neg_sharpe(w):
                port_ret = w @ mean_returns
                port_vol = np.sqrt(w @ cov_matrix @ w)
                return -(port_ret / (port_vol + 1e-8))

            constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
            bounds = [(0, self.max_weight) for _ in range(n)]
            initial = np.ones(n) / n

            result = minimize(
                neg_sharpe,
                initial,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )

            if result.success:
                weights = result.x
                result = {sym: float(w) for sym, w in zip(symbols, weights)}
                log.info(f"Markowitz weights: {result}")
                return result

        except Exception as e:
            log.warning(f"Markowitz failed: {e}")

        return self._equal_weight(symbols)

    def equal_weight(self, symbols: list[str]) -> dict[str, float]:
        """Equal weight allocation"""
        return self._equal_weight(symbols)

    def _equal_weight(self, symbols: list[str]) -> dict[str, float]:
        """Equal weight helper"""
        if not symbols:
            return {}

        w = 1.0 / len(symbols)
        return {sym: w for sym in symbols}

    def allocate_capital(
        self, symbols: list[str], weights: dict[str, float], total_capital: float
    ) -> dict[str, float]:
        """Convert weights to capital allocations"""
        allocations = {}

        for sym, weight in weights.items():
            capital = total_capital * weight
            allocations[sym] = capital

        return allocations

    def adjust_for_volatility(
        self, weights: dict[str, float], volatilities: dict[str, float]
    ) -> dict[str, float]:
        """Adjust weights based on volatility"""
        adjusted = {}

        for sym, weight in weights.items():
            vol = volatilities.get(sym, 0.2)
            if vol > 0:
                adjusted[sym] = weight / vol
            else:
                adjusted[sym] = weight

        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}

        return adjusted

    def rebalance(
        self,
        current_weights: dict[str, float],
        target_weights: dict[str, float],
        threshold: float = 0.05,
    ) -> dict[str, float]:
        """Determine rebalancing needed"""
        rebalance = {}

        all_symbols = set(current_weights.keys()) | set(target_weights.keys())

        for sym in all_symbols:
            current = current_weights.get(sym, 0)
            target = target_weights.get(sym, 0)

            diff = target - current

            if abs(diff) > threshold:
                rebalance[sym] = {"current": current, "target": target, "diff": diff}

        return rebalance


class CapitalManager:
    """Capital management and scaling"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.base_capital = self.config.get("base_capital", 300000)
        self.max_capital = self.config.get("max_capital", 1000000)
        self.min_capital = self.config.get("min_capital", 50000)

        self.current_capital = self.base_capital
        self.peak_capital = self.base_capital

    def get_available_capital(self, utilized: float = 0) -> float:
        """Get available capital for new trades"""
        available = self.current_capital - utilized
        return max(self.min_capital, available)

    def scale_capital(self, performance: float) -> float:
        """Scale capital based on performance"""
        if performance > 0.20:
            new_capital = self.current_capital * 1.25
        elif performance > 0.10:
            new_capital = self.current_capital * 1.10
        elif performance < -0.10:
            new_capital = self.current_capital * 0.75
        else:
            new_capital = self.current_capital

        new_capital = min(new_capital, self.max_capital)
        new_capital = max(new_capital, self.min_capital)

        if new_capital != self.current_capital:
            log.info(
                f"Capital scaled: {self.current_capital:,.0f} -> {new_capital:,.0f}"
            )
            self.current_capital = new_capital

        return new_capital

    def update_capital(self, pnl: float):
        """Update capital after PnL"""
        self.current_capital += pnl

        self.peak_capital = max(self.peak_capital, self.current_capital)

    def get_stats(self) -> dict:
        """Get capital stats"""
        return {
            "current": self.current_capital,
            "peak": self.peak_capital,
            "profit": self.current_capital - self.base_capital,
            "drawdown": (
                (self.peak_capital - self.current_capital) / self.peak_capital
                if self.peak_capital > 0
                else 0
            ),
        }


_optimizer_instance = None
_capital_manager_instance = None


def get_portfolio_optimizer() -> "PortfolioOptimizer":
    """Get singleton portfolio optimizer"""
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = PortfolioOptimizer()
    return _optimizer_instance


def get_capital_manager() -> "CapitalManager":
    """Get singleton capital manager"""
    global _capital_manager_instance
    if _capital_manager_instance is None:
        _capital_manager_instance = CapitalManager()
    return _capital_manager_instance


def risk_parity(returns: np.ndarray, symbols: list[str]) -> dict[str, float]:
    """Risk parity allocation"""
    return get_portfolio_optimizer().risk_parity(returns, symbols)


def allocate_capital(
    symbols: list[str], weights: dict[str, float], total_capital: float
) -> dict[str, float]:
    """Allocate capital to symbols"""
    return get_portfolio_optimizer().allocate_capital(symbols, weights, total_capital)
