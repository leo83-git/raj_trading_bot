# ═══════════════════════════════════════════════════════════════
#  Analytics & Feedback Loop — PnL, Attribution, Learning
# ═══════════════════════════════════════════════════════════════
import datetime
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("feedback.attribution")


@dataclass
class Trade:
    symbol: str
    action: str
    entry: float
    exit: float
    quantity: int
    pnl: float
    entry_time: datetime.datetime
    exit_time: datetime.datetime
    reason: str
    metadata: dict = field(default_factory=dict)


class PortfolioTracker:
    """Track positions and PnL"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.positions = {}
        self.closed_trades = []

        self.daily_pnl = 0
        self.total_pnl = 0

    def open_position(
        self,
        symbol: str,
        action: str,
        entry: float,
        quantity: int,
        reason: str = "",
        metadata: dict = None,
    ):
        """Open new position"""
        self.positions[symbol] = {
            "action": action,
            "entry": entry,
            "quantity": quantity,
            "entry_time": datetime.datetime.now(),
            "reason": reason,
            "metadata": metadata or {},
        }
        log.info(f"Position opened: {symbol} | {action} | {entry} x {quantity}")

    def close_position(
        self, symbol: str, exit_price: float, reason: str = "MANUAL"
    ) -> float:
        """Close position and record trade"""
        if symbol not in self.positions:
            log.warning(f"Position not found: {symbol}")
            return 0

        pos = self.positions[symbol]

        if pos["action"] == "BUY":
            pnl = (exit_price - pos["entry"]) * pos["quantity"]
        else:
            pnl = (pos["entry"] - exit_price) * pos["quantity"]

        trade = Trade(
            symbol=symbol,
            action=pos["action"],
            entry=pos["entry"],
            exit=exit_price,
            quantity=pos["quantity"],
            pnl=pnl,
            entry_time=pos["entry_time"],
            exit_time=datetime.datetime.now(),
            reason=reason,
            metadata=pos.get("metadata", {}),
        )

        self.closed_trades.append(trade)
        self.daily_pnl += pnl
        self.total_pnl += pnl

        log.info(f"Position closed: {symbol} | PnL: {pnl:.2f} | {reason}")

        del self.positions[symbol]

        return pnl

    def get_unrealized_pnl(self, current_prices: dict[str, float]) -> float:
        """Calculate unrealized PnL"""
        unrealized = 0

        for symbol, pos in self.positions.items():
            current = current_prices.get(symbol, pos["entry"])

            if pos["action"] == "BUY":
                pnl = (current - pos["entry"]) * pos["quantity"]
            else:
                pnl = (pos["entry"] - current) * pos["quantity"]

            unrealized += pnl

        return unrealized

    def get_positions(self) -> list[dict]:
        """Get current positions"""
        return [{"symbol": sym, **pos} for sym, pos in self.positions.items()]


class PerformanceAnalyzer:
    """Analyze trading performance"""

    def __init__(self):
        self.trades = []

    def analyze(self, trades: list[Trade]) -> dict:
        """Analyze performance metrics"""
        if not trades:
            return self._empty_metrics()

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        total_trades = len(trades)
        win_count = len(wins)
        loss_count = len(losses)

        win_rate = win_count / total_trades if total_trades > 0 else 0

        avg_win = sum(t.pnl for t in wins) / win_count if win_count > 0 else 0
        avg_loss = sum(t.pnl for t in losses) / loss_count if loss_count > 0 else 0

        total_pnl = sum(t.pnl for t in trades)

        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        return {
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "largest_win": round(max((t.pnl for t in wins), default=0), 2),
            "largest_loss": round(min((t.pnl for t in losses), default=0), 2),
        }

    def _empty_metrics(self) -> dict:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "total_pnl": 0,
            "profit_factor": 0,
            "largest_win": 0,
            "largest_loss": 0,
        }

    def analyze_by_symbol(self, trades: list[Trade]) -> dict:
        """Analyze performance by symbol"""
        symbol_pnl = {}

        for trade in trades:
            sym = trade.symbol
            if sym not in symbol_pnl:
                symbol_pnl[sym] = {"trades": 0, "pnl": 0, "wins": 0}

            symbol_pnl[sym]["trades"] += 1
            symbol_pnl[sym]["pnl"] += trade.pnl
            if trade.pnl > 0:
                symbol_pnl[sym]["wins"] += 1

        return symbol_pnl

    def analyze_by_strategy(self, trades: list[Trade]) -> dict:
        """Analyze performance by strategy"""
        strategy_pnl = {}

        for trade in trades:
            strat = trade.metadata.get("strategy", "default")
            if strat not in strategy_pnl:
                strategy_pnl[strat] = {"trades": 0, "pnl": 0, "wins": 0}

            strategy_pnl[strat]["trades"] += 1
            strategy_pnl[strat]["pnl"] += trade.pnl
            if trade.pnl > 0:
                strategy_pnl[strat]["wins"] += 1

        return strategy_pnl


class AttributionEngine:
    """Attribute PnL to factors"""

    def __init__(self):
        self.factors = ["market", "sector", "stock", "timing", "model"]

    def attribute(self, trade: Trade) -> dict:
        """Attribute trade PnL to factors"""
        pnl = trade.pnl

        market_contrib = pnl * 0.3
        sector_contrib = pnl * 0.2
        stock_contrib = pnl * 0.35
        timing_contrib = pnl * 0.1
        model_contrib = pnl * 0.05

        return {
            "total_pnl": pnl,
            "market": round(market_contrib, 2),
            "sector": round(sector_contrib, 2),
            "stock": round(stock_contrib, 2),
            "timing": round(timing_contrib, 2),
            "model": round(model_contrib, 2),
        }

    def aggregate_attribution(self, trades: list[Trade]) -> dict:
        """Aggregate attribution across trades"""
        totals = {f: 0 for f in self.factors}

        for trade in trades:
            attr = self.attribute(trade)
            for f in self.factors:
                totals[f] += attr.get(f, 0)

        return {k: round(v, 2) for k, v in totals.items()}


class FeedbackLoop:
    """Feedback loop for model improvement"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.retrain_threshold = self.config.get("retrain_threshold", 0.45)
        self.min_trades = self.config.get("min_trades", 15)
        self.consecutive_losses = self.config.get("consecutive_losses_threshold", 5)
        self.max_drawdown = self.config.get("max_drawdown_threshold", 0.10)

        self.last_retrain = datetime.datetime.now()
        self.performance_history = []
        self.loss_streak = 0

    def should_retrain(self, metrics: dict) -> bool:
        """Determine if model needs retraining"""
        win_rate = metrics.get("win_rate", 0)
        total_trades = metrics.get("total_trades", 0)
        pnl = metrics.get("pnl", 0)
        max_drawdown = metrics.get("max_drawdown", 0)

        time_since = (
            datetime.datetime.now() - self.last_retrain
        ).total_seconds() / 3600

        if win_rate < self.retrain_threshold and total_trades >= self.min_trades:
            log.info(
                f"Retrain needed: win_rate={win_rate:.1%} < {self.retrain_threshold:.1%}"
            )
            return True

        if max_drawdown > self.max_drawdown:
            log.info(
                f"Retrain needed: max_drawdown={max_drawdown:.1%} > {self.max_drawdown:.1%}"
            )
            return True

        if time_since >= 24:
            log.info(f"Retrain: {time_since:.1f}h since last retrain")
            return True

        return False

    def adapt_parameters(self, metrics: dict) -> dict:
        """Adapt trading parameters based on performance"""
        adaptations = {}

        win_rate = metrics.get("win_rate", 0.5)
        pnl = metrics.get("pnl", 0)
        avg_win = metrics.get("avg_win", 0)
        avg_loss = metrics.get("avg_loss", 0)

        if win_rate < 0.35 or pnl < -500:
            adaptations["risk_per_trade"] = 0.005
            adaptations["min_confidence"] = 0.6
            log.warning("Poor performance: Reducing risk, raising confidence threshold")
        elif win_rate > 0.55 and pnl > 500:
            adaptations["risk_per_trade"] = 0.02
            adaptations["min_confidence"] = 0.35
            log.info(
                "Strong performance: Increasing risk, lowering confidence threshold"
            )

        if avg_loss > abs(avg_win * 2):
            adaptations["stop_loss_pct"] = 0.015
            log.warning("Large losses: Tightening stop loss")

        return adaptations

    def check_consecutive_losses(self, last_pnl: float) -> bool:
        """Check for consecutive losses"""
        if last_pnl < 0:
            self.loss_streak += 1
            if self.loss_streak >= self.consecutive_losses:
                log.warning(
                    f"Loss streak detected: {self.loss_streak} consecutive losses"
                )
                self.loss_streak = 0
                return True
        else:
            self.loss_streak = 0
        return False

    def record_performance(self, metrics: dict):
        """Record performance for tracking"""
        self.performance_history.append(
            {"timestamp": datetime.datetime.now(), "metrics": metrics}
        )

        if len(self.performance_history) > 100:
            self.performance_history = self.performance_history[-100:]

    def get_stats(self) -> dict:
        """Get feedback loop stats"""
        return {
            "last_retrain": self.last_retrain.isoformat(),
            "performance_history_count": len(self.performance_history),
            "recent_performance": (
                self.performance_history[-5:] if self.performance_history else []
            ),
        }


class AnalyticsEngine:
    """Unified analytics and feedback"""

    _initialized = False

    def __init__(self, config: dict = None):
        if AnalyticsEngine._initialized:
            log.debug("AnalyticsEngine already initialized, skipping")
            return
        AnalyticsEngine._initialized = True

        self.config = config or {}

        self.tracker = PortfolioTracker(config)
        self.analyzer = PerformanceAnalyzer()
        self.attribution = AttributionEngine()
        self.feedback = FeedbackLoop(config)

        log.info("Analytics engine initialized")

    def record_trade(
        self,
        symbol: str,
        action: str,
        entry: float,
        exit_price: float,
        quantity: int,
        reason: str = "",
        metadata: dict = None,
    ):
        """Record completed trade"""
        self.tracker.open_position(symbol, action, entry, quantity, reason, metadata)
        pnl = self.tracker.close_position(symbol, exit_price, reason)

        return pnl

    def get_summary(self) -> dict:
        """Get performance summary"""
        trades = self.tracker.closed_trades
        metrics = self.analyzer.analyze(trades)

        metrics["daily_pnl"] = round(self.tracker.daily_pnl, 2)
        metrics["total_pnl"] = round(self.tracker.total_pnl, 2)
        metrics["open_positions"] = len(self.tracker.positions)

        return metrics

    def get_attribution(self) -> dict:
        """Get PnL attribution"""
        return self.attribution.aggregate_attribution(self.tracker.closed_trades)

    def check_feedback(self) -> dict:
        """Check if feedback needed"""
        metrics = self.get_summary()

        retrain = self.feedback.should_retrain(metrics)
        adaptations = self.feedback.adapt_parameters(metrics) if retrain else {}

        self.feedback.record_performance(metrics)

        return {
            "should_retrain": retrain,
            "adaptations": adaptations,
            "metrics": metrics,
        }

    def save_to_file(self, filename: str = "logs/analytics.json"):
        """Save analytics to file"""
        try:
            data = {
                "positions": self.tracker.positions,
                "closed_trades": [
                    {
                        "symbol": t.symbol,
                        "action": t.action,
                        "entry": t.entry,
                        "exit": t.exit,
                        "quantity": t.quantity,
                        "pnl": t.pnl,
                        "reason": t.reason,
                    }
                    for t in self.tracker.closed_trades
                ],
                "summary": self.get_summary(),
            }

            import os

            os.makedirs(os.path.dirname(filename), exist_ok=True)

            with open(filename, "w") as f:
                json.dump(data, f, indent=2, default=str)

            log.info(f"Analytics saved to {filename}")
        except Exception as e:
            log.error(f"Save failed: {e}")


# Singleton removed to prevent duplicate initialization
