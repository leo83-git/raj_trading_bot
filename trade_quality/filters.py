# ═══════════════════════════════════════════════════════════════
#  Trade Quality Filter & Strategy Validation
#  - Validates strategy edge before execution
#  - Maps regimes to strategies
#  - Trade quality scoring
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("trade_quality")


@dataclass
class EdgeMetrics:
    win_rate: float
    expectancy: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    avg_win: float
    avg_loss: float
    profit_factor: float


class StrategyEdgeValidator:
    """Validates strategies have proven statistical edge"""

    MIN_EXPECTANCY = 0.0
    MIN_SHARPE = 1.0
    MAX_DRAWDOWN = 0.20

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.trade_history = {}

    def record_trade(self, strategy: str, pnl: float, won: bool):
        if strategy not in self.trade_history:
            self.trade_history[strategy] = []

        self.trade_history[strategy].append(
            {"pnl": pnl, "won": won, "timestamp": __import__("datetime").datetime.now()}
        )

    def calculate_metrics(self, strategy: str) -> EdgeMetrics | None:
        if strategy not in self.trade_history:
            return None

        trades = self.trade_history[strategy]
        if len(trades) < 10:
            return None

        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_rate = len(wins) / len(pnls) if pnls else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1

        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        returns = np.array(pnls)
        mean_return = np.mean(returns)
        std_return = np.std(returns)

        sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0

        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - running_max) / (running_max + 1e-10)
        max_drawdown = abs(np.min(drawdowns)) if len(drawdowns) > 0 else 0

        profit_factor = (
            sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0
        )

        return EdgeMetrics(
            win_rate=win_rate,
            expectancy=expectancy,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            total_trades=len(trades),
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
        )

    def is_edge_valid(self, strategy: str) -> tuple[bool, str]:
        metrics = self.calculate_metrics(strategy)

        if not metrics:
            return True, "Insufficient data"

        if metrics.expectancy <= self.MIN_EXPECTANCY:
            return (
                False,
                f"Expectancy {metrics.expectancy:.2f} <= {self.MIN_EXPECTANCY}",
            )

        if metrics.sharpe_ratio < self.MIN_SHARPE:
            return False, f"Sharpe {metrics.sharpe_ratio:.2f} < {self.MIN_SHARPE}"

        if metrics.max_drawdown > self.MAX_DRAWDOWN:
            return False, f"Max DD {metrics.max_drawdown:.0%} > {self.MAX_DRAWDOWN:.0%}"

        return True, "Valid"


REGIME_STRATEGY_MAP = {
    "TRENDING_UP": ["MOMENTUM", "BREAKOUT", "MA_CROSSOVER"],
    "TRENDING_DOWN": ["MEAN_REVERSION", "RSI_REVERSAL"],
    "SIDEWAYS": ["IRON_BUTTERFLY", "IRON_CONDOR", "RANGE_TRADING"],
    "HIGH_VOLATILITY": ["STRADDLE", "GAMMA_SCALPING", "STRANGLE"],
    "LOW_VOLATILITY": ["IRON_CONDOR", "SHORT_CALL", "SHORT_PUT"],
    "VOLATILE_SIDEWAYS": ["STRADDLE_BUY", "IRON_FLY"],
}


class RegimeStrategyMapper:
    """Maps market regimes to optimal strategies"""

    def __init__(self, regime_detector=None):
        self.regime_detector = regime_detector
        self.strategy_map = REGIME_STRATEGY_MAP

    def get_strategies(self, regime: str) -> list[str]:
        return self.strategy_map.get(regime, ["COMBINED", "MA_CROSSOVER"])

    def should_switch(self, current_strategy: str, new_regime: str) -> bool:
        valid = self.get_strategies(new_regime)
        return current_strategy not in valid


class TradeQualityFilter:
    """Filters low-quality trade setups"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.min_iv_percentile = self.config.get("min_iv_percentile", 20)
        self.require_volume_spike = self.config.get("require_volume_spike", True)
        self.require_htf_confirm = self.config.get("require_htf_confirm", True)

    def filter(self, signal: dict, market_data: dict, regime: str) -> tuple[bool, str]:
        """Returns (pass, reason)"""

        if signal.get("action") == "HOLD":
            return False, "Signal is HOLD"

        features = signal.get("features", {})

        iv = features.get("iv", 0)
        if iv and iv < self.min_iv_percentile:
            return False, f"IV {iv} < {self.min_iv_percentile}"

        if self.require_volume_spike:
            volume = features.get("volume", 0)
            avg_volume = features.get("volume_avg", 0)
            if avg_volume > 0 and volume < avg_volume * 1.2:
                return False, f"Volume {volume} < 1.2x avg"

        if self.require_htf_confirm:
            trend = features.get("trend", "SIDEWAYS")
            direction = signal.get("action")

            if direction == "BUY" and trend == "DOWNTREND":
                return False, "HTF downtrend, signal BUY"
            if direction == "SELL" and trend == "UPTREND":
                return False, "HTF uptrend, signal SELL"

        if (
            regime == "HIGH_VOLATILITY"
            and features.get("atr", 0) > features.get("close", 0) * 0.03
        ):
            return False, "Excessive volatility"

        return True, "QUALITY A+"

    def score_setup(self, signal: dict, market_data: dict) -> float:
        """Score from 0-100 (A+ = 100)"""
        score = 100

        features = signal.get("features", {})

        confidence = signal.get("confidence", 0.5)
        score *= confidence

        iv = features.get("iv", 50)
        if iv > 50:
            score *= 1.1
        elif iv < 20:
            score *= 0.9

        volume = features.get("volume", 0)
        avg_volume = features.get("volume_avg", 1)
        if avg_volume > 0:
            vol_ratio = volume / avg_volume
            if vol_ratio > 2:
                score *= 1.2
            elif vol_ratio < 1:
                score *= 0.8

        return min(score, 100)


class DynamicCapitalAllocator:
    """Dynamic capital allocation based on strategy performance"""

    def __init__(self, base_capital: float = 300000):
        self.base_capital = base_capital
        self.strategy_sharpe = {}

    def update_performance(self, strategy: str, sharpe: float):
        self.strategy_sharpe[strategy] = sharpe

    def allocate(self, strategy: str) -> float:
        if not self.strategy_sharpe:
            return self.base_capital

        total_sharpe = sum(self.strategy_sharpe.values())
        if total_sharpe == 0:
            return self.base_capital

        sharpe = self.strategy_sharpe.get(strategy, 0)
        weight = (
            sharpe / total_sharpe if total_sharpe > 0 else 1 / len(self.strategy_sharpe)
        )

        allocated = self.base_capital * weight * 2

        return min(allocated, self.base_capital)

    def get_position_size(self, strategy: str, price: float, atr: float) -> int:
        capital = self.allocate(strategy)

        risk_pct = 0.02
        risk_amount = capital * risk_pct
        stop_distance = atr * 1.5

        if stop_distance > 0:
            quantity = int(risk_amount / stop_distance)
        else:
            quantity = int(capital * 0.1 / price)

        return max(quantity, 1)


class IVPercentileFilter:
    """Options IV percentile filter for IV-based strategies"""

    def __init__(self):
        self.iv_history = {}

    def update_iv(self, symbol: str, iv: float):
        if symbol not in self.iv_history:
            self.iv_history[symbol] = []
        self.iv_history[symbol].append(iv)
        if len(self.iv_history[symbol]) > 60:
            self.iv_history[symbol] = self.iv_history[symbol][-60:]

    def get_percentile(self, symbol: str) -> float:
        if symbol not in self.iv_history:
            return 50.0

        history = self.iv_history[symbol]
        if len(history) < 10:
            return 50.0

        current = history[-1] if history else 50
        below_count = sum(1 for v in history if v < current)

        return (below_count / len(history)) * 100

    def should_sell_premium(
        self, symbol: str, min_iv_percentile: float = 40
    ) -> tuple[bool, str]:
        percentile = self.get_percentile(symbol)

        if percentile >= min_iv_percentile:
            return True, f"IV {percentile:.0f}% >= {min_iv_percentile}%"
        return False, f"IV {percentile:.0f}% < {min_iv_percentile}%"

    def should_buy_premium(
        self, symbol: str, max_iv_percentile: float = 25
    ) -> tuple[bool, str]:
        percentile = self.get_percentile(symbol)

        if percentile <= max_iv_percentile:
            return True, f"IV {percentile:.0f}% <= {max_iv_percentile}%"
        return False, f"IV {percentile:.0f}% > {max_iv_percentile}%"


def create_trade_quality_system(config: dict = None) -> dict:
    return {
        "edge_validator": StrategyEdgeValidator(config),
        "regime_mapper": RegimeStrategyMapper(),
        "quality_filter": TradeQualityFilter(config),
        "allocator": DynamicCapitalAllocator(
            config.get("base_capital", 300000) if config else 300000
        ),
        "iv_filter": IVPercentileFilter(),
    }
