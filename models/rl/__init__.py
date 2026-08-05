# ═══════════════════════════════════════════════════════════════════════
#  RL Agent — Decision Optimization with PPO
#  Extended with FinRL-inspired StockTradingEnv and DRLAgent
# ═══════════════════════════════════════════════════════════════════════
import os
import pickle
import random
from typing import Dict, List, Optional, Tuple

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("models.rl")

try:
    import torch
    from torch import nn, optim
    from torch.distributions import Categorical

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    log.warning("PyTorch not available, using Q-learning")

# Import FinRL-inspired components
try:
    from models.rl.drl_agent import DEFAULT_PARAMS, MODELS, DRLAgent, EnsembleDRLAgent
    from models.rl.stock_trading_env import OptionsTradingEnv, StockTradingEnv

    FINRL_COMPONENTS = True
except ImportError as e:
    log.warning(f"Could not import FinRL components: {e}")
    FINRL_COMPONENTS = False
    StockTradingEnv = None
    OptionsTradingEnv = None
    DRLAgent = None
    EnsembleDRLAgent = None


class PolicyNetwork(nn.Module):
    """Policy network for RL agent"""

    def __init__(self, state_size=14, hidden_size=64, action_size=3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, action_size),
        )

    def forward(self, x):
        return self.net(x)

    def get_action(self, state):
        logits = self.forward(state)
        probs = torch.softmax(logits, dim=-1)
        dist = Categorical(probs)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        return action.item(), log_prob


class ValueNetwork(nn.Module):
    """Value network for advantage estimation"""

    def __init__(self, state_size=14, hidden_size=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze()


class RLAgent:
    """Reinforcement Learning agent for trading decisions"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.q_table: dict[str, dict[str, float]] = {}
        self.learning_rate = self.config.get("learning_rate", 0.1)
        self.discount_factor = self.config.get("discount_factor", 0.9)
        self.epsilon = self.config.get("epsilon", 0.1)

        self.actions = ["BUY", "SELL", "HOLD"]
        self.state = "NEUTRAL"
        self.episode_count = 0

        self.policy_net = None
        self.value_net = None
        self.optimizer = None

        self._setup_ppo()

    def _setup_ppo(self):
        """Setup PPO networks"""
        if not TORCH_AVAILABLE:
            return

        try:
            self.policy_net = PolicyNetwork(
                state_size=14, hidden_size=64, action_size=3
            )
            self.value_net = ValueNetwork(state_size=14, hidden_size=64)

            self.policy_optimizer = optim.Adam(self.policy_net.parameters(), lr=0.001)
            self.value_optimizer = optim.Adam(self.value_net.parameters(), lr=0.001)

            self._load_models()
            log.info("PPO agent initialized with neural networks")
        except Exception as e:
            log.warning(f"Could not initialize PPO: {e}")

    def _load_models(self):
        """Load trained models"""
        policy_path = "models/policy_net.pth"
        value_path = "models/value_net.pth"

        if os.path.exists(policy_path) and os.path.exists(value_path):
            try:
                self.policy_net.load_state_dict(torch.load(policy_path))
                self.value_net.load_state_dict(torch.load(value_path))
                self.policy_net.eval()
                self.value_net.eval()
                log.info("Loaded PPO models")
            except Exception as e:
                log.warning(f"Could not load PPO models: {e}")

    def _state_to_tensor(self, state: str, features: dict) -> torch.Tensor:
        """Convert state to feature tensor"""
        feature_vals = []

        feature_vals.append(
            0.0 if state == "PROFIT" else (1.0 if state == "LOSS" else 0.5)
        )

        rsi = features.get("rsi", 50)
        feature_vals.append(rsi / 100)

        macd = features.get("macd", {})
        if isinstance(macd, dict):
            feature_vals.append(macd.get("histogram", 0) / 10)
        else:
            feature_vals.append(0)

        for key in ["sma_9", "sma_21", "sma_50"]:
            val = features.get(key, 0)
            feature_vals.append(val / 10000 if val else 0)

        trend_map = {"UPTREND": 1.0, "DOWNTREND": -1.0, "SIDEWAYS": 0.0}
        feature_vals.append(trend_map.get(features.get("trend", "SIDEWAYS"), 0))

        feature_vals.append(features.get("volatility", 0) / 100)

        feature_vals.append(features.get("atr", 0) / 1000)

        bb = features.get("bollinger", {})
        if isinstance(bb, dict):
            upper = bb.get("upper", 0)
            lower = bb.get("lower", 0)
            middle = bb.get("middle", 1)
            if middle > 0:
                feature_vals.append((upper - lower) / middle)
            else:
                feature_vals.append(0)
        else:
            feature_vals.append(0)

        while len(feature_vals) < 14:
            feature_vals.append(0)

        return torch.FloatTensor(feature_vals[:14])

    def get_action(self, state: str, features: dict) -> str:
        """Get action based on current state"""
        if TORCH_AVAILABLE and self.policy_net is not None and self.episode_count > 0:
            return self._get_ppo_action(state, features)

        return self._get_heuristic_action(state, features)

    def _get_ppo_action(self, state: str, features: dict) -> str:
        """Get action from PPO policy"""
        try:
            state_tensor = self._state_to_tensor(state, features).unsqueeze(0)

            with torch.no_grad():
                logits = self.policy_net(state_tensor)
                probs = torch.softmax(logits, dim=-1)
                action = torch.argmax(probs).item()

            return self.actions[action]
        except Exception as e:
            log.error(f"PPO action error: {e}")
            return self._get_heuristic_action(state, features)

    def _get_heuristic_action(self, state: str, features: dict) -> str:
        """Heuristic action when no Q-values available"""
        score = 0.0

        rsi = features.get("rsi")
        if rsi:
            if rsi < 35:
                score += 0.5
            elif rsi > 65:
                score -= 0.5

        trend = features.get("trend", "SIDEWAYS")
        if trend == "UPTREND":
            score += 0.3
        elif trend == "DOWNTREND":
            score -= 0.3

        if score > 0.3:
            return "BUY"
        elif score < -0.3:
            return "SELL"
        return "HOLD"

    def get_q_action(self, state: str, features: dict) -> str:
        """Get action using Q-learning"""
        if random.random() < self.epsilon:
            return random.choice(self.actions)

        q_values = self._get_q_values(state)

        if not q_values:
            return self._get_heuristic_action(state, features)

        best_action = max(q_values, key=q_values.get)
        return best_action

    def _get_q_values(self, state: str) -> dict[str, float]:
        """Get Q-values for state"""
        return self.q_table.get(state, {})

    def update_q_value(self, state: str, action: str, reward: float, next_state: str):
        """Update Q-value using Q-learning"""
        if state not in self.q_table:
            self.q_table[state] = {a: 0.0 for a in self.actions}

        if next_state not in self.q_table:
            self.q_table[next_state] = {a: 0.0 for a in self.actions}

        current_q = self.q_table[state][action]
        max_next_q = max(self.q_table[next_state].values())

        new_q = current_q + self.learning_rate * (
            reward + self.discount_factor * max_next_q - current_q
        )
        self.q_table[state][action] = new_q

    def train(self, episodes: int, training_data: list[dict]):
        """Train the RL agent"""
        log.info(f"Training RL agent for {episodes} episodes")

        if TORCH_AVAILABLE and self.policy_net is not None:
            self._train_ppo(episodes, training_data)
        else:
            self._train_q_learning(episodes, training_data)

        self.episode_count += episodes
        log.info(f"RL training complete. Episodes: {self.episode_count}")

    def _train_q_learning(self, episodes: int, training_data: list[dict]):
        """Train using Q-learning"""
        for episode in range(episodes):
            state = "NEUTRAL"

            for data in training_data[:100]:
                action = self.get_q_action(state, data)
                reward = data.get("reward", 0)
                next_state = data.get("state", "NEUTRAL")

                self.update_q_value(state, action, reward, next_state)
                state = next_state

    def _train_ppo(self, episodes: int, training_data: list[dict]):
        """Train using PPO"""
        log.info("Training with PPO")

        for epoch in range(min(episodes, 10)):
            self.policy_net.train()

            for data in training_data:
                state = data.get("state", "NEUTRAL")
                features = data.get("features", {})
                reward = data.get("reward", 0)

                state_tensor = self._state_to_tensor(state, features).unsqueeze(0)

                logits = self.policy_net(state_tensor)
                probs = torch.softmax(logits, dim=-1)
                dist = Categorical(probs)

                action_idx = random.randint(0, 2)
                log_prob = dist.log_prob(torch.tensor(action_idx))
                value = self.value_net(state_tensor)

                advantage = reward - value.item()

                policy_loss = -log_prob * advantage
                value_loss = advantage**2

                self.policy_optimizer.zero_grad()
                (policy_loss + 0.5 * value_loss).backward()
                self.policy_optimizer.step()

        os.makedirs("models", exist_ok=True)
        torch.save(self.policy_net.state_dict(), "models/policy_net.pth")
        torch.save(self.value_net.state_dict(), "models/value_net.pth")

        self.policy_net.eval()
        log.info("PPO models saved")

    def get_policy(self) -> dict[str, str]:
        """Get learned policy"""
        if self.q_table:
            policy = {}
            for state, q_values in self.q_table.items():
                if q_values:
                    best_action = max(q_values, key=q_values.get)
                    policy[state] = best_action
            return policy
        return {"NEUTRAL": "HOLD"}

    def decay_epsilon(self):
        """Decay exploration rate"""
        self.epsilon = max(0.01, self.epsilon * 0.99)
