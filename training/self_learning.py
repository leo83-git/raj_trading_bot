# ═══════════════════════════════════════════════════════════════
#  Self-Learning System (Retrain + Adapt)
# ═══════════════════════════════════════════════════════════════
import datetime

from quant_utils.logger import get_logger

log = get_logger("self_learning")


class SelfLearningSystem:
    """Self-learning system for continuous improvement"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.retrain_interval_hours = self.config.get("retrain_interval_hours", 24)
        self.min_trades_for_retrain = self.config.get("min_trades_for_retrain", 20)
        self.performance_threshold = self.config.get("performance_threshold", 0.40)

        self.last_retrain = datetime.datetime.now()
        self.model_versions = []
        self.performance_history = []

        log.info("Self-learning system initialized")

    def should_retrain(self, trade_count: int, latest_performance: float) -> bool:
        """Determine if model should be retrained"""
        time_since_retrain = (
            datetime.datetime.now() - self.last_retrain
        ).total_seconds() / 3600

        should_retrain = False
        reasons = []

        if time_since_retrain >= self.retrain_interval_hours:
            reasons.append(f"Time: {time_since_retrain:.1f}h since last retrain")
            should_retrain = True

        if trade_count >= self.min_trades_for_retrain:
            reasons.append(f"Trades: {trade_count}")
            should_retrain = True

        if latest_performance < self.performance_threshold:
            reasons.append(
                f"Performance: {latest_performance:.1%} < {self.performance_threshold:.1%}"
            )
            should_retrain = True

        if should_retrain:
            log.info(f"Retrain needed: {' | '.join(reasons)}")

        return should_retrain

    def collect_training_data(self, trades: list[dict]) -> list[dict]:
        """Collect and prepare training data from trades"""
        training_data = []

        for trade in trades:
            features = {
                "rsi": trade.get("rsi"),
                "macd": trade.get("macd"),
                "sma_9": trade.get("sma_9"),
                "sma_21": trade.get("sma_21"),
                "trend": trade.get("trend"),
                "volatility": trade.get("volatility"),
            }

            reward = 1.0 if trade.get("pnl", 0) > 0 else -1.0
            state = "PROFIT" if trade.get("pnl", 0) > 0 else "LOSS"

            training_data.append(
                {"features": features, "reward": reward, "state": state}
            )

        log.info(f"Collected {len(training_data)} training samples")
        return training_data

    def evaluate_model_performance(self, trades: list[dict]) -> dict:
        """Evaluate model performance"""
        if not trades:
            return {"win_rate": 0, "profit_factor": 0, "avg_pnl": 0}

        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in trades if t.get("pnl", 0) <= 0)

        win_rate = wins / len(trades) if trades else 0

        total_profit = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
        total_loss = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0))

        profit_factor = total_profit / total_loss if total_loss > 0 else 0
        avg_pnl = sum(t.get("pnl", 0) for t in trades) / len(trades)

        return {
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_pnl": avg_pnl,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
        }

    def select_best_model(self, models: list[dict]) -> str:
        """Select best performing model"""
        if not models:
            return "default"

        best_model = max(models, key=lambda m: m.get("performance", 0))

        log.info(
            f"Best model selected: {best_model.get('name')} | Perf: {best_model.get('performance'):.1%}"
        )

        return best_model.get("name", "default")

    def retrain_models(self, training_data: list[dict], ml_model, rl_agent):
        """Retrain models with new data"""
        log.info(f"Retraining models with {len(training_data)} samples...")

        if ml_model:
            ml_model.train(training_data)
            log.info("ML model retrained")

        if rl_agent:
            rl_agent.train(episodes=100, training_data=training_data)
            log.info("RL agent retrained")

        self.last_retrain = datetime.datetime.now()

        self.model_versions.append(
            {"timestamp": self.last_retrain, "samples": len(training_data)}
        )

    def adapt_parameters(self, current_performance: float) -> dict:
        """Adapt system parameters based on performance"""
        adaptations = {}

        if current_performance < 0.35:
            adaptations["risk_per_trade"] = 0.005
            adaptations["max_positions"] = 3
            log.warning("Poor performance: Reducing risk parameters")
        elif current_performance > 0.55:
            adaptations["risk_per_trade"] = 0.015
            adaptations["max_positions"] = 6
            log.info("Good performance: Increasing risk parameters")

        return adaptations

    def get_learning_stats(self) -> dict:
        """Get learning system statistics"""
        return {
            "last_retrain": self.last_retrain.isoformat(),
            "model_versions": len(self.model_versions),
            "performance_history": self.performance_history[-10:],
        }
