# ═══════════════════════════════════════════════════════════════
#  Portfolio Optimizer — Risk parity, allocation, sizing
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("portfolio.optimizer")


@dataclass
class Allocation:
    symbol: str
    weight: float
    capital: float
    quantity: int
    entry_price: float


class RiskParity:
    """Risk parity portfolio allocation"""

    def __init__(self):
        self.lookback = 60

    def allocate(
        self, symbols: list[str], returns: dict[str, list[float]], total_capital: float
    ) -> list[Allocation]:
        """Risk parity allocation"""
        if not symbols or not returns:
            return self._equal_weight(symbols, total_capital)

        try:
            returns_matrix = np.array([returns.get(sym, [0]) for sym in symbols])
            if returns_matrix.shape[1] == 0:
                return self._equal_weight(symbols, total_capital)

            cov_matrix = np.cov(returns_matrix)

            n = len(symbols)
            weights = np.ones(n) / n

            for _ in range(100):
                port_vol = np.sqrt(weights @ cov_matrix @ weights)
                marginal_risk = cov_matrix @ weights
                risk_contrib = weights * marginal_risk / (port_vol + 1e-8)
                target_risk = port_vol * np.ones(n) / n
                weights = weights * (target_risk / (risk_contrib + 1e-8))
                weights = np.clip(weights, 0.02, 0.4)
                weights = weights / weights.sum()

            allocations = []
            for sym, w in zip(symbols, weights):
                capital = total_capital * w
                allocations.append(
                    Allocation(
                        symbol=sym,
                        weight=round(w, 4),
                        capital=round(capital, 2),
                        quantity=0,
                        entry_price=0,
                    )
                )

            log.info(f"Risk parity: {[(a.symbol, a.weight) for a in allocations]}")
            return allocations

        except Exception as e:
            log.warning(f"Risk parity failed: {e}")
            return self._equal_weight(symbols, total_capital)

    def _equal_weight(
        self, symbols: list[str], total_capital: float
    ) -> list[Allocation]:
        if not symbols:
            return []
        w = 1.0 / len(symbols)
        return [Allocation(sym, w, total_capital * w, 0, 0) for sym in symbols]


class CapitalAllocator:
    """Capital allocation and position sizing"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.base_capital = self.config.get("base_capital", 300000)
        self.max_capital_per_trade = self.config.get("max_capital_per_trade", 50000)
        self.risk_per_trade = self.config.get("risk_per_trade", 0.01)

        self.current_capital = self.base_capital
        self.peak_capital = self.base_capital

    def calculate_position_size(
        self, entry: float, stop_loss: float, capital: float = None
    ) -> int:
        """Calculate position size based on risk"""
        if capital is None:
            capital = self.current_capital

        risk_amount = capital * self.risk_per_trade
        risk_per_share = abs(entry - stop_loss)

        if risk_per_share <= 0:
            return 1

        size = int(risk_amount / risk_per_share)
        size = min(size, int(self.max_capital_per_trade / entry))

        return max(1, size)

    def get_available_capital(self, utilized: float = 0) -> float:
        """Get available capital"""
        available = self.current_capital - utilized
        return max(10000, available)

    def scale_capital(self, performance: float):
        """Scale capital based on performance"""
        if performance > 0.20:
            self.current_capital *= 1.25
        elif performance > 0.10:
            self.current_capital *= 1.10
        elif performance < -0.10:
            self.current_capital *= 0.75

        self.current_capital = min(self.current_capital, 2000000)
        self.current_capital = max(self.current_capital, 50000)

        self.peak_capital = max(self.peak_capital, self.current_capital)

    def update_capital(self, pnl: float):
        """Update capital after trade"""
        self.current_capital += pnl
        self.peak_capital = max(self.peak_capital, self.current_capital)

    def get_stats(self) -> dict:
        """Get capital stats"""
        return {
            "current": round(self.current_capital, 2),
            "peak": round(self.peak_capital, 2),
            "profit": round(self.current_capital - self.base_capital, 2),
            "drawdown": (
                round((self.peak_capital - self.current_capital) / self.peak_capital, 4)
                if self.peak_capital > 0
                else 0
            ),
        }


class PortfolioOptimizer:
    """Main portfolio optimizer"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.risk_parity = RiskParity()
        self.capital_allocator = CapitalAllocator(config)

        log.info("Portfolio optimizer initialized")

        self.risk_parity = RiskParity()
        self.capital_allocator = CapitalAllocator(config)

        log.info("Portfolio optimizer initialized")

    def optimize(
        self,
        signals: list[dict],
        returns: dict[str, list[float]],
        total_capital: float = None,
    ) -> list[dict]:
        """Optimize portfolio allocation"""
        if total_capital is None:
            total_capital = self.capital_allocator.current_capital

        symbols = [s.get("symbol") for s in signals if s.get("symbol")]

        allocations = self.risk_parity.allocate(symbols, returns, total_capital)

        result = []
        for alloc in allocations:
            signal = next((s for s in signals if s.get("symbol") == alloc.symbol), None)
            if signal:
                result.append(
                    {
                        "symbol": alloc.symbol,
                        "weight": alloc.weight,
                        "capital": alloc.capital,
                        "action": signal.get("action", "BUY"),
                        "entry": signal.get("entry", 0),
                        "stop_loss": signal.get("stop_loss", 0),
                        "target": signal.get("target", 0),
                    }
                )

        return result

    def get_capital_stats(self) -> dict:
        """Get capital stats"""
        return self.capital_allocator.get_stats()


# Singleton instances removed to prevent duplicate initialization
