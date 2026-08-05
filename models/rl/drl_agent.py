# ══════════════════════════════════════════════════════════════════════════════
#  FinRL-Inspired DRL Agent Wrapper
#  Uses Stable Baselines 3 for PPO/A2C/DDPG/SAC/TD3 training
#  Adapted from: https://github.com/AI4Finance-Foundation/FinRL
#  License: MIT
# ══════════════════════════════════════════════════════════════════════════════
"""
DRL Agent implementations using Stable Baselines 3.
Supports multiple algorithms: PPO, A2C, DDPG, SAC, TD3.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    from stable_baselines3 import A2C, DDPG, PPO, SAC, TD3
    from stable_baselines3.common.callbacks import BaseCallback, CallbackList
    from stable_baselines3.common.noise import (
        NormalActionNoise,
        OrnsteinUhlenbeckActionNoise,
    )

    SB3_AVAILABLE = True
except ImportError:
    SB3_AVAILABLE = False


from quant_utils.logger import get_logger

log = get_logger("models.drl_agent")

# Available algorithms
MODELS = {
    "a2c": A2C,
    "ddpg": DDPG,
    "td3": TD3,
    "sac": SAC,
    "ppo": PPO,
}

# Default hyperparameters (similar to FinRL)
DEFAULT_PARAMS = {
    "ppo": {
        "n_steps": 2048,
        "batch_size": 64,
        "n_epochs": 10,
        "learning_rate": 3e-4,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "clip_range": 0.2,
        "ent_coef": 0.01,
    },
    "a2c": {
        "n_steps": 2048,
        "batch_size": 64,
        "learning_rate": 7e-4,
        "gamma": 0.99,
        "gae_lambda": 0.95,
        "ent_coef": 0.01,
    },
    "ddpg": {
        "batch_size": 64,
        "buffer_size": 100000,
        "learning_rate": 1e-3,
        "gamma": 0.99,
        "tau": 0.005,
    },
    "td3": {
        "batch_size": 64,
        "buffer_size": 100000,
        "learning_rate": 1e-3,
        "gamma": 0.99,
        "tau": 0.005,
        "policy_delay": 2,
    },
    "sac": {
        "batch_size": 64,
        "buffer_size": 100000,
        "learning_rate": 3e-4,
        "gamma": 0.99,
        "tau": 0.005,
        "ent_coef": "auto",
    },
}


@dataclass
class TrainingMetrics:
    """Training metrics container"""

    episode_reward: float
    episode_length: int
    total_steps: int
    loss: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None


class TensorboardCallback(BaseCallback):
    """
    Custom callback for logging training metrics to TensorBoard.
    Similar to FinRL's implementation.
    """

    def __init__(self, verbose: int = 0):
        super().__init__(verbose)
        self.episode_rewards = []
        self.episode_lengths = []

    def _on_step(self) -> bool:
        try:
            # Log reward
            reward = self.locals.get("rewards", [0])[0]
            self.logger.record("train/reward", reward)
        except Exception:
            try:
                reward = self.locals.get("reward", [0])[0]
                self.logger.record("train/reward", reward)
            except Exception:
                pass
        return True

    def _on_rollout_end(self) -> None:
        try:
            rollout_buffer_rewards = self.locals["rollout_buffer"].rewards.flatten()
            self.logger.record("train/reward_min", float(min(rollout_buffer_rewards)))
            self.logger.record(
                "train/reward_mean", float(np.mean(rollout_buffer_rewards))
            )
            self.logger.record("train/reward_max", float(max(rollout_buffer_rewards)))
        except Exception:
            pass


class EarlyStoppingCallback(BaseCallback):
    """
    Early stopping based on Sharpe ratio improvement.
    """

    def __init__(
        self,
        check_freq: int = 10000,
        sharpe_window: int = 100,
        threshold: float = 1.5,
        verbose: int = 1,
    ):
        super().__init__(verbose)
        self.check_freq = check_freq
        self.sharpe_window = sharpe_window
        self.threshold = threshold
        self.best_sharpe = -np.inf
        self.wait_steps = 0

    def _on_step(self) -> bool:
        if self.n_calls % self.check_freq == 0:
            current_sharpe = self._calculate_sharpe()
            if current_sharpe > self.best_sharpe:
                self.best_sharpe = current_sharpe
                self.wait_steps = 0
                if self.verbose > 0:
                    log.info(f"New best Sharpe: {current_sharpe:.3f}")
            else:
                self.wait_steps += self.check_freq
                if self.wait_steps > 5 * self.check_freq:
                    if self.verbose > 0:
                        log.info("Early stopping: Sharpe not improving")
                    return False
        return True

    def _calculate_sharpe(self) -> float:
        try:
            episode_rewards = self.training_env.get_attr("episode_returns")
            if episode_rewards and len(episode_rewards) > self.sharpe_window:
                returns = episode_rewards[-self.sharpe_window :]
                return np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252)
        except Exception:
            pass
        return -np.inf


class DRLAgent:
    """
    Unified DRL Agent supporting multiple algorithms from Stable Baselines 3.

    Adapted from FinRL's DRLAgent with improvements for Indian markets.

    Example:
        >>> from models.rl.stock_trading_env import StockTradingEnv
        >>> env = StockTradingEnv(df, stock_dim=1, initial_amount=300000)
        >>> agent = DRLAgent(env)
        >>> model = agent.get_model("ppo", model_kwargs={"n_steps": 2048})
        >>> agent.train_model(model, total_timesteps=50000)
        >>> actions, _ = model.predict(state, deterministic=True)
    """

    def __init__(
        self,
        env,
        policy: str = "MlpPolicy",
        policy_kwargs: dict = None,
        model_kwargs: dict = None,
        verbose: int = 1,
        seed: int = None,
        tensorboard_log: str = None,
    ):
        self.env = env
        self.policy = policy
        self.policy_kwargs = policy_kwargs or {}
        self.model_kwargs = model_kwargs or {}
        self.verbose = verbose
        self.seed = seed
        self.tensorboard_log = tensorboard_log

        self.model = None
        self.algorithm = None

        if not SB3_AVAILABLE:
            log.warning(
                "Stable Baselines 3 not available. Install with: pip install stable-baselines3"
            )

    def get_model(
        self,
        model_name: str,
        policy: str = None,
        policy_kwargs: dict = None,
        model_kwargs: dict = None,
        verbose: int = None,
        seed: int = None,
        tensorboard_log: str = None,
    ):
        """
        Get a DRL model instance.

        Args:
            model_name: One of 'ppo', 'a2c', 'ddpg', 'td3', 'sac'
            policy: Policy class (default: 'MlpPolicy')
            policy_kwargs: Additional policy arguments
            model_kwargs: Algorithm-specific hyperparameters
            verbose: Verbosity level
            seed: Random seed
            tensorboard_log: Path for TensorBoard logs

        Returns:
            Initialized model instance
        """
        if not SB3_AVAILABLE:
            raise ImportError("Stable Baselines 3 not installed")

        if model_name not in MODELS:
            raise ValueError(
                f"Model '{model_name}' not found. Available: {list(MODELS.keys())}"
            )

        # Use defaults if not specified
        policy = policy or self.policy
        policy_kwargs = policy_kwargs or self.policy_kwargs
        model_kwargs = model_kwargs or self.model_kwargs
        model_kwargs = {**DEFAULT_PARAMS.get(model_name, {}), **model_kwargs}
        verbose = verbose if verbose is not None else self.verbose
        seed = seed if seed is not None else self.seed
        tensorboard_log = tensorboard_log or self.tensorboard_log

        # Handle action noise for off-policy algorithms
        if "action_noise" in model_kwargs:
            n_actions = self.env.action_space.shape[-1]
            noise_type = model_kwargs.pop("action_noise")
            if noise_type == "normal":
                noise = NormalActionNoise(
                    mean=np.zeros(n_actions), sigma=0.1 * np.ones(n_actions)
                )
            elif noise_type == "ou":
                noise = OrnsteinUhlenbeckActionNoise(
                    mean=np.zeros(n_actions), sigma=0.1 * np.ones(n_actions)
                )
            model_kwargs["action_noise"] = noise

        if verbose > 0:
            log.info(f"Creating {model_name.upper()} model with kwargs: {model_kwargs}")

        self.algorithm = model_name.upper()

        return MODELS[model_name](
            policy=policy,
            env=self.env,
            tensorboard_log=tensorboard_log,
            verbose=verbose,
            policy_kwargs=policy_kwargs,
            seed=seed,
            **model_kwargs,
        )

    @staticmethod
    def train_model(
        model,
        tb_log_name: str = None,
        total_timesteps: int = 50000,
        callbacks: list[BaseCallback] = None,
        progress_bar: bool = True,
    ) -> object:
        """
        Train a DRL model.

        Args:
            model: Initialized model instance
            tb_log_name: TensorBoard log name
            total_timesteps: Number of training steps
            callbacks: List of callbacks
            progress_bar: Show progress bar

        Returns:
            Trained model
        """
        callback_list = []

        # Add tensorboard callback
        callback_list.append(TensorboardCallback())

        # Add custom callbacks
        if callbacks:
            callback_list.extend(callbacks)

        final_callbacks = CallbackList(callback_list) if callback_list else None

        log.info(f"Training {model.__class__.__name__} for {total_timesteps} steps...")

        model = model.learn(
            total_timesteps=total_timesteps,
            tb_log_name=tb_log_name,
            callback=final_callbacks,
            progress_bar=progress_bar,
        )

        log.info("Training complete!")
        return model

    def predict(self, state: np.ndarray, deterministic: bool = True) -> tuple:
        """
        Get action prediction from trained model.

        Args:
            state: Current state observation
            deterministic: Use deterministic policy (no exploration)

        Returns:
            (action, state_value) tuple
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train_model first.")

        return self.model.predict(state, deterministic=deterministic)

    def save(self, path: str):
        """Save model to disk"""
        if self.model:
            self.model.save(path)
            log.info(f"Model saved to {path}")

    def load(self, path: str, env=None):
        """Load model from disk"""
        if not SB3_AVAILABLE:
            raise ImportError("Stable Baselines 3 not installed")

        if env is None:
            env = self.env

        algo = Path(path).stem.split("_")[0].lower()
        if algo in ["a2c", "ddpg", "td3", "sac", "ppo"]:
            self.model = MODELS[algo].load(path, env=env)
            self.algorithm = algo.upper()
            log.info(f"Model loaded from {path}")
        else:
            raise ValueError(f"Cannot determine algorithm from path: {path}")


class EnsembleDRLAgent:
    """
    Ensemble of multiple DRL agents for robust trading.

    Combines PPO, A2C, SAC for better performance.
    Uses voting or averaging for final decisions.
    """

    def __init__(
        self,
        env,
        algorithms: list[str] = None,
        weights: dict[str, float] = None,
    ):
        self.env = env
        self.algorithms = algorithms or ["ppo", "a2c", "sac"]
        self.weights = weights or {
            a: 1.0 / len(self.algorithms) for a in self.algorithms
        }

        self.agents: dict[str, DRLAgent] = {}
        self._initialize_agents()

    def _initialize_agents(self):
        """Initialize all agents"""
        for algo in self.algorithms:
            self.agents[algo] = DRLAgent(
                self.env, model_kwargs=DEFAULT_PARAMS.get(algo, {})
            )
            log.info(f"Initialized {algo.upper()} agent")

    def train_all(
        self,
        total_timesteps_per_agent: int = 50000,
        save_dir: str = "models/ensemble",
    ):
        """Train all agents in the ensemble"""
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        for algo in self.algorithms:
            log.info(f"Training {algo.upper()} agent...")
            model = self.agents[algo].get_model(algo)
            model = DRLAgent.train_model(
                model,
                tb_log_name=f"{algo}_run",
                total_timesteps=total_timesteps_per_agent,
            )
            self.agents[algo].model = model
            self.agents[algo].save(f"{save_dir}/{algo}_model")

        log.info("All ensemble agents trained!")

    def predict_ensemble(self, state: np.ndarray) -> tuple:
        """
        Get ensemble prediction using weighted voting.

        Args:
            state: Current state observation

        Returns:
            (action, confidence) tuple
        """
        actions = []
        values = []

        for algo in self.algorithms:
            agent = self.agents[algo]
            if agent.model:
                action, value = agent.predict(state, deterministic=True)
                actions.append(action)
                values.append(value * self.weights.get(algo, 0))

        if not actions:
            return None, 0

        # Weighted average for continuous actions
        if isinstance(actions[0], np.ndarray):
            ensemble_action = np.average(
                actions, weights=list(self.weights.values()), axis=0
            )
            confidence = np.mean(values) / (np.std(values) + 1e-8)
        else:
            # Discrete actions - majority voting
            from collections import Counter

            counts = Counter(actions)
            ensemble_action = counts.most_common(1)[0][0]
            confidence = counts.most_common(1)[0][1] / len(actions)

        return ensemble_action, confidence
