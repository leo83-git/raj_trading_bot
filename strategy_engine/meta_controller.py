# ═══════════════════════════════════════════════════════════════
#  Meta Strategy Controller (Multi-Brain Fusion)
# ═══════════════════════════════════════════════════════════════

from quant_utils.logger import get_logger

log = get_logger("meta_controller")


class MetaController:
    """
    Meta Strategy Controller - fuses signals from ML, DL, and RL models
    to generate final trading decisions
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.weights = {
            "ml": self.config.get("ml_weight", 0.4),
            "dl": self.config.get("dl_weight", 0.3),
            "rl": self.config.get("rl_weight", 0.3),
        }

        self.thresholds = {
            "buy": self.config.get("buy_threshold", 0.7),
            "sell": self.config.get("sell_threshold", 0.3),
            "min_confidence": self.config.get("min_confidence", 0.5),
        }

        self.last_decision = None

    def make_decision(
        self, ml_score: float, dl_score: float, rl_score: float
    ) -> tuple[str, float]:
        """
        Fuse decisions from all three brains

        Returns:
            (direction, confidence) - direction is BUY/SELL/HOLD, confidence is 0-1
        """
        combined_score = (
            ml_score * self.weights["ml"]
            + dl_score * self.weights["dl"]
            + rl_score * self.weights["rl"]
        )

        if combined_score > self.thresholds["buy"]:
            decision = "BUY"
        elif combined_score < self.thresholds["sell"]:
            decision = "SELL"
        else:
            decision = "HOLD"

        confidence = abs(combined_score)

        self.last_decision = {
            "decision": decision,
            "confidence": confidence,
            "ml_score": ml_score,
            "dl_score": dl_score,
            "rl_score": rl_score,
            "combined": combined_score,
        }

        log.info(
            f"Meta Decision: {decision} ({confidence:.0%}) | ML: {ml_score:.2f}, DL: {dl_score:.2f}, RL: {rl_score:.2f}"
        )

        return decision, confidence

    def get_last_decision(self) -> dict | None:
        """Get last decision details"""
        return self.last_decision

    def adjust_weights(
        self, ml_performance: float, dl_performance: float, rl_performance: float
    ):
        """Adjust model weights based on performance"""
        total = ml_performance + dl_performance + rl_performance

        if total > 0:
            self.weights["ml"] = ml_performance / total
            self.weights["dl"] = dl_performance / total
            self.weights["rl"] = rl_performance / total

            log.info(
                f"Weights adjusted: ML={self.weights['ml']:.2f}, DL={self.weights['dl']:.2f}, RL={self.weights['rl']:.2f}"
            )


class AutoStrategySwitcher:
    """Auto-switch between strategies based on market conditions"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.volatility_threshold = self.config.get("volatility_threshold", 20)

    def select_strategy(self, volatility: float, trend: str) -> str:
        """
        Select strategy based on market conditions

        Returns:
            Strategy name
        """
        if volatility > self.volatility_threshold:
            strategy = "OPTIONS_SELLING"
        elif trend == "UPTREND":
            strategy = "TREND_FOLLOWING"
        elif trend == "DOWNTREND":
            strategy = "MEAN_REVERSION"
        else:
            strategy = "SIDEWAYS"

        log.info(
            f"Strategy selected: {strategy} | Vol: {volatility:.1f}%, Trend: {trend}"
        )

        return strategy

    def get_strategy_params(self, strategy: str) -> dict:
        """Get parameters for selected strategy"""
        params = {
            "OPTIONS_SELLING": {
                "option_type": "SELL",
                "strike_offset": 2,
                "target_pct": 0.30,
                "sl_pct": 0.50,
            },
            "TREND_FOLLOWING": {
                "option_type": "BUY",
                "strike_offset": 1,
                "target_pct": 0.50,
                "sl_pct": 0.25,
            },
            "MEAN_REVERSION": {
                "option_type": "BUY",
                "strike_offset": 0,
                "target_pct": 0.40,
                "sl_pct": 0.20,
            },
            "SIDEWAYS": {
                "option_type": "SELL",
                "strike_offset": 1,
                "target_pct": 0.25,
                "sl_pct": 0.40,
            },
        }

        return params.get(strategy, params["SIDEWAYS"])


class StrategyPerformanceTracker:
    """Track and suppress poor-performing strategies"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.min_win_rate = self.config.get("min_win_rate", 0.40)
        self.lookback_trades = self.config.get("lookback_trades", 20)
        self.suppression_minutes = self.config.get("suppression_minutes", 30)

        self.strategy_trades = {}
        self.suppressed_strategies = {}

    def record_trade(self, strategy_name: str, pnl: float, won: bool):
        """Record trade result for a strategy"""
        if strategy_name not in self.strategy_trades:
            self.strategy_trades[strategy_name] = []

        self.strategy_trades[strategy_name].append(
            {"pnl": pnl, "won": won, "timestamp": __import__("datetime").datetime.now()}
        )

        if len(self.strategy_trades[strategy_name]) > self.lookback_trades:
            self.strategy_trades[strategy_name] = self.strategy_trades[strategy_name][
                -self.lookback_trades :
            ]

    def get_win_rate(self, strategy_name: str) -> float:
        """Calculate win rate for strategy"""
        if strategy_name not in self.strategy_trades:
            return 0.5

        trades = self.strategy_trades[strategy_name]
        if not trades:
            return 0.5

        wins = sum(1 for t in trades if t.get("won", False))
        return wins / len(trades)

    def is_suppressed(self, strategy_name: str) -> bool:
        """Check if strategy is currently suppressed"""
        if strategy_name not in self.suppressed_strategies:
            return False

        from datetime import datetime

        suppress_until = self.suppressed_strategies[strategy_name]

        if datetime.now() > suppress_until:
            del self.suppressed_strategies[strategy_name]
            log.info(f"Strategy {strategy_name} re-enabled after suppression period")
            return False

        return True

    def get_suppression_reason(self, strategy_name: str) -> str | None:
        """Get reason for suppression"""
        win_rate = self.get_win_rate(strategy_name)

        from datetime import datetime

        if strategy_name in self.suppressed_strategies:
            remaining = self.suppressed_strategies[strategy_name] - datetime.now()
            return f"Win rate {win_rate:.0%} < {self.min_win_rate:.0%}, suppressed for {remaining.seconds // 60}m"

        return None

    def check_and_suppress(self, strategy_name: str) -> bool:
        """
        Check strategy performance and suppress if needed
        Returns True if strategy should be suppressed
        """
        if self.is_suppressed(strategy_name):
            return True

        win_rate = self.get_win_rate(strategy_name)

        if win_rate < self.min_win_rate:
            from datetime import datetime, timedelta

            suppress_until = datetime.now() + timedelta(
                minutes=self.suppression_minutes
            )
            self.suppressed_strategies[strategy_name] = suppress_until

            log.warning(
                f"Suppressing {strategy_name}: win rate {win_rate:.0%} < {self.min_win_rate:.0%} "
                f"({self.suppression_minutes} min suppression)"
            )
            return True

        return False

    def get_all_performance(self) -> dict:
        """Get performance for all strategies"""
        performance = {}

        for strategy_name in self.strategy_trades:
            trades = self.strategy_trades[strategy_name]
            wins = sum(1 for t in trades if t.get("won", False))
            total_pnl = sum(t.get("pnl", 0) for t in trades)

            performance[strategy_name] = {
                "total_trades": len(trades),
                "wins": wins,
                "win_rate": self.get_win_rate(strategy_name),
                "total_pnl": total_pnl,
                "suppressed": self.is_suppressed(strategy_name),
            }

        return performance

    def get_active_strategies(self) -> list:
        """Get list of non-suppressed strategies"""
        active = []

        for strategy_name in self.strategy_trades:
            if not self.is_suppressed(strategy_name):
                active.append(strategy_name)

        return active if active else list(self.strategy_trades.keys())

    def get_performance(self):
        """Return performance metrics for the strategy."""
        return {
            "win_rate": self.min_win_rate,
            "lookback_trades": self.lookback_trades,
            "suppression_minutes": self.suppression_minutes,
            "total_trades": len(self.strategy_trades),
            "winning_trades": sum(
                1
                for stats in self.strategy_trades.values()
                for t in stats
                if t.get("won", False)
            ),
        }

    def get_consecutive_wins(self, strategy_name):
        """Return number of consecutive wins for strategy."""
        if strategy_name not in self.strategy_trades:
            return 0
        trades = self.strategy_trades[strategy_name]
        consecutive = 0
        for trade in reversed(trades):
            if trade.get("won", False):
                consecutive += 1
            else:
                break
        return consecutive
