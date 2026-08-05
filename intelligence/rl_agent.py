# ═══════════════════════════════════════════════════════════════
#  RL Trading Agent — Self-Learning from Trading Outcomes
#  Inspired by FinRL patterns, adapted for existing simulation
# ═══════════════════════════════════════════════════════════════
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torch.distributions import Categorical

from quant_utils.logger import get_logger

log = get_logger("intelligence.rl_agent")


# ─── Neural Network Policy ────────────────────────────────────────────────
class PolicyNetwork(nn.Module):
    """Actor-Critic network for PGO (Policy Gradient Optimization)"""

    def __init__(self, state_dim: int, action_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.fc1 = nn.Linear(state_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, hidden_dim)
        self.fc3 = nn.Linear(hidden_dim, hidden_dim)

        # Policy head (actor)
        self.policy_head = nn.Linear(hidden_dim, action_dim)

        # Value head (critic)
        self.value_head = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))

        # Action probabilities
        action_probs = F.softmax(self.policy_head(x), dim=-1)

        # State value
        state_value = self.value_head(x)

        return action_probs, state_value


@dataclass
class TradingExperience:
    """Single trading step memory"""

    state: list[float]
    action_idx: int
    reward: float
    next_state: list[float]
    done: bool
    info: dict = field(default_factory=dict)


class RLTradingMemory:
    """Circular buffer for trading experiences"""

    def __init__(self, capacity: int = 10000):
        self.buffer = deque(maxlen=capacity)

    def push(self, exp: TradingExperience):
        self.buffer.append(exp)

    def sample(self, batch_size: int) -> list[TradingExperience]:
        return random.sample(self.buffer, min(batch_size, len(self.buffer)))

    def __len__(self):
        return len(self.buffer)

    def clear(self):
        self.buffer.clear()


# ─── PPO Agent ────────────────────────────────────────────────────────────
class PPOAgent:
    """
    Proximal Policy Optimization agent for trading.

    Learns from trade outcomes (PnL) as reward signal.
    State includes: price features, position info, technical indicators
    Actions: BUY/SELL/HOLD per eligible stock
    """

    def __init__(self, state_dim: int, action_dim: int, config: dict = None):
        self.config = config or {}
        self.state_dim = state_dim
        self.action_dim = action_dim

        # Hyperparameters
        self.lr = self.config.get("lr", 3e-4)
        self.gamma = self.config.get("gamma", 0.99)  # Discount factor
        self.eps_clip = self.config.get("eps_clip", 0.2)  # PPO clip
        self.K_epochs = self.config.get("K_epochs", 10)  # Update epochs
        self.batch_size = self.config.get("batch_size", 64)

        # Networks
        self.policy = PolicyNetwork(state_dim, action_dim)
        self.optimizer = torch.optim.Adam(self.policy.parameters(), lr=self.lr)

        # Memory
        self.memory = RLTradingMemory(capacity=10000)

        # Training state
        self.training_step = 0
        self.model_path = Path(self.config.get("model_path", "models/rl_agent.pth"))

        log.info(
            f"PPO Agent initialized: state_dim={state_dim}, action_dim={action_dim}"
        )

    def select_action(
        self, state: list[float], deterministic: bool = False
    ) -> tuple[int, float]:
        """
        Select action based on current state.

        Args:
            state: Feature vector [price_indicators, position_info, ...]
            deterministic: If True, take greedy action; else sample from policy

        Returns:
            action_idx: Integer action index
            logprob: Log probability of selected action
        """
        state_tensor = torch.FloatTensor(state).unsqueeze(0)

        with torch.no_grad():
            action_probs, _ = self.policy(state_tensor)

            if deterministic:
                action_idx = torch.argmax(action_probs).item()
            else:
                dist = Categorical(action_probs)
                action_idx = dist.sample().item()

            # Calculate log probability
            logprob = torch.log(action_probs[0, action_idx]).item()

        return action_idx, logprob

    def store_transition(self, exp: TradingExperience):
        """Store experience in memory buffer"""
        self.memory.push(exp)

    def update(self) -> dict[str, float]:
        """
        PPO policy update using collected experiences.

        Returns:
            Dict with training metrics (loss, KL divergence, etc.)
        """
        if len(self.memory) < self.batch_size:
            return {"loss": 0.0, "updates": 0}

        # Sample batch
        batch = self.memory.sample(self.batch_size)

        # Convert to tensors
        states = torch.FloatTensor([exp.state for exp in batch])
        actions = torch.LongTensor([exp.action_idx for exp in batch])
        rewards = torch.FloatTensor([exp.reward for exp in batch])
        next_states = torch.FloatTensor([exp.next_state for exp in batch])
        dones = torch.BoolTensor([exp.done for exp in batch])

        # Compute advantages using Generalized Advantage Estimation (GAE)
        with torch.no_grad():
            _, values = self.policy(states)
            _, next_values = self.policy(next_states)

            # TD(λ) advantage
            advantages = (
                rewards + self.gamma * next_values * (~dones) - values.squeeze()
            )
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        # PPO update for K epochs
        total_loss = 0
        for _ in range(self.K_epochs):
            # Get current policy
            action_probs, values = self.policy(states)
            values = values.squeeze()

            # Old log probs (stored during collection would be better, but recalc for simplicity)
            old_logprobs = torch.log(
                action_probs.gather(1, actions.unsqueeze(1))
            ).squeeze()

            # Ratio of new/old policy
            ratios = torch.exp(old_logprobs - old_logprobs.detach())  # Simplified

            # Surrogate loss
            surr1 = ratios * advantages
            surr2 = (
                torch.clamp(ratios, 1 - self.eps_clip, 1 + self.eps_clip) * advantages
            )

            # Actor loss
            actor_loss = -torch.min(surr1, surr2).mean()

            # Critic loss (value function)
            returns = rewards + self.gamma * next_values * (~dones)
            critic_loss = F.mse_loss(values, returns)

            # Total loss
            loss = (
                actor_loss
                + 0.5 * critic_loss
                - 0.01
                * (action_probs * torch.log(action_probs + 1e-8)).sum(dim=1).mean()
            )

            # Optimize
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.policy.parameters(), 0.5)
            self.optimizer.step()

            total_loss += loss.item()

        avg_loss = total_loss / self.K_epochs
        self.training_step += 1

        # Clear memory after update (on-policy)
        self.memory.clear()

        return {
            "loss": avg_loss,
            "updates": self.training_step,
            "buffer_size": len(self.memory),
        }

    def save(self, path: str = None):
        """Save model checkpoint"""
        save_path = Path(path) if path else self.model_path
        save_path.parent.mkdir(parents=True, exist_ok=True)

        torch.save(
            {
                "policy_state_dict": self.policy.state_dict(),
                "optimizer_state_dict": self.optimizer.state_dict(),
                "training_step": self.training_step,
                "config": self.config,
            },
            save_path,
        )
        log.info(f"Model saved to {save_path}")

    def load(self, path: str = None):
        """Load model checkpoint"""
        load_path = Path(path) if path else self.model_path
        if load_path.exists():
            checkpoint = torch.load(load_path, map_location="cpu")
            self.policy.load_state_dict(checkpoint["policy_state_dict"])
            self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
            self.training_step = checkpoint.get("training_step", 0)
            log.info(f"Model loaded from {load_path}")
        else:
            log.warning(f"No checkpoint found at {load_path}")

    def get_action_space(self) -> int:
        """Get number of possible actions"""
        return self.action_dim

    def get_state_dim(self) -> int:
        """Get state vector dimension"""
        return self.state_dim


# ─── Feature Extractor for Trading State ─────────────────────────────────
class StateBuilder:
    """Construct state vector from market data + position info"""

    def __init__(self, feature_cols: list[str], position_cols: int = 5):
        self.feature_cols = feature_cols
        self.position_cols = position_cols  # [qty, entry_price, current_pnl, age_minutes, days_to_expiry]

    def build(
        self, market_features: dict[str, float], position_info: dict | None = None
    ) -> list[float]:
        """
        Build state vector.

        Args:
            market_features: Technical indicators + price data
            position_info: Current position details if any

        Returns:
            Normalized state vector
        """
        # Market features
        state = []
        for col in self.feature_cols:
            val = market_features.get(col, 0)
            # Normalize if we know typical ranges
            if col in ["rsi"]:
                val = val / 100.0
            elif col in ["macd", "volume"]:
                val = np.log1p(abs(val)) * np.sign(val)  # Log-scale
            state.append(float(val))

        # Position context (if holding)
        if position_info:
            state.extend(
                [
                    float(position_info.get("quantity", 0) > 0),  # Has position flag
                    float(
                        position_info.get("unrealized_pnl", 0) / 100000
                    ),  # Normalized PnL
                    float(
                        position_info.get("hold_minutes", 0) / 360
                    ),  # Normalized age (max 6 hours)
                ]
            )
        else:
            state.extend([0.0, 0.0, 0.0])

        return state

    @property
    def state_dim(self) -> int:
        return len(self.feature_cols) + 3


# ─── Reward Shapers ───────────────────────────────────────────────────────
class RewardShaper:
    """Configure reward function for RL training"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.pnl_weight = self.config.get("pnl_weight", 1.0)
        self.sharpe_weight = self.config.get("sharpe_weight", 0.1)
        self.drawdown_penalty = self.config.get("drawdown_penalty", 0.5)

    def compute_reward(
        self, pnl: float, capital: float, max_drawdown: float, trade_count: int
    ) -> float:
        """
        Compute composite reward.

        Components:
        - PnL change (primary)
        - Sharpe ratio proxy (risk-adjusted)
        - Drawdown penalty
        - Exploration bonus for new trades
        """
        # Normalized PnL
        norm_pnl = pnl / (capital + 1e-8)

        # Sharpe-like (mean / std of recent trades)
        if trade_count > 10:
            sharpe = self._estimate_sharpe()
        else:
            sharpe = 0.0

        # Drawdown penalty
        dd_penalty = -abs(max_drawdown) * self.drawdown_penalty

        reward = self.pnl_weight * norm_pnl + self.sharpe_weight * sharpe + dd_penalty

        # Clip to [-1, 1] for stability
        reward = np.clip(reward, -1.0, 1.0)

        return float(reward)

    def _estimate_sharpe(self) -> float:
        """Rolling Sharpe estimation (simplified)"""
        # Implementation would track trade returns
        return 0.0


# ─── Training Pipeline ────────────────────────────────────────────────────
class RLTrainer:
    """Orchestrates offline RL training on historical data"""

    def __init__(
        self,
        simulation_engine,
        agent: PPOAgent,
        state_builder: StateBuilder,
        reward_shaper: RewardShaper,
    ):
        self.sim = simulation_engine
        self.agent = agent
        self.state_builder = state_builder
        self.reward_shaper = reward_shaper

    def collect_episode(self, data: list[dict]) -> list[TradingExperience]:
        """
        Run one episode on historical data and collect experiences.

        Args:
            data: List of market data points (with indicators)

        Returns:
            List of TradingExperience
        """
        experiences = []
        self.sim.reset_all()

        for i, data_point in enumerate(data):
            symbol = data_point.get("symbol", "NIFTY")
            market_features = {
                k: data_point.get(k, 0) for k in self.state_builder.feature_cols
            }

            # Current positions
            positions = self.sim.get_positions()
            pos_info = positions[0] if positions else None

            # Build state
            state = self.state_builder.build(market_features, pos_info)

            # Select action (deterministic during data collection)
            action_idx, logprob = self.agent.select_action(state, deterministic=True)

            # Map action to trade decision
            # 0=HOLD, 1=BUY, 2=SELL, 3=INCREASE, 4=DECREASE
            action = self._map_action(action_idx)

            # Execute in simulation
            if action == "BUY" and not pos_info:
                # Buy underlying (simplified for training)
                price = data_point.get("close", 0)
                qty = self._calculate_position_size(price)
                result = self.sim.buy(symbol, price, qty, {"strategy": "rl_agent"})
            elif action == "SELL" and pos_info:
                # Close position
                price = data_point.get("close", 0)
                qty = pos_info["quantity"]
                result = self.sim.sell(symbol, price, qty, {"strategy": "rl_agent"})
            # HOLD → do nothing

            # Compute reward from PnL change
            new_positions = self.sim.get_positions()
            new_pnl = sum(p.get("unrealized_pnl", 0) for p in new_positions)
            reward = self.reward_shaper.compute_reward(
                pnl=new_pnl,
                capital=self.sim.capital,
                max_drawdown=self.sim.initial_capital - self.sim.capital,
                trade_count=self.sim.total_trades,
            )

            # Next state
            next_market_features = data_point  # Simplified; would use next timestep
            next_pos_info = new_positions[0] if new_positions else None
            next_state = self.state_builder.build(next_market_features, next_pos_info)

            done = i == len(data) - 1

            exp = TradingExperience(
                state=state,
                action_idx=action_idx,
                reward=reward,
                next_state=next_state,
                done=done,
            )
            experiences.append(exp)

        return experiences

    def _map_action(self, action_idx: int) -> str:
        mapping = {0: "HOLD", 1: "BUY", 2: "SELL", 3: "INCREASE", 4: "DECREASE"}
        return mapping.get(action_idx, "HOLD")

    def _calculate_position_size(self, price: float) -> int:
        """Kell position size based on capital"""
        risk_per_trade = self.sim.capital * 0.02
        return int(risk_per_trade / (price * 0.01))  # 1% SL


# ─── Simple DQN Alternative ───────────────────────────────────────────────
class DQNAgent:
    """Deep Q-Network for discrete action spaces (lightweight alternative)"""

    def __init__(self, state_dim: int, action_dim: int, config: dict = None):
        self.state_dim = state_dim
        self.action_dim = action_dim

        # Q-network
        self.q_network = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU(),
            nn.Linear(128, action_dim),
        )

        self.target_network = nn.Sequential(
            nn.Linear(state_dim, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU(),
            nn.Linear(128, action_dim),
        )
        self.target_network.load_state_dict(self.q_network.state_dict())

        self.optimizer = torch.optim.Adam(self.q_network.parameters(), lr=1e-3)
        self.gamma = 0.99
        self.epsilon = 1.0
        self.epsilon_decay = 0.995
        self.epsilon_min = 0.01

        self.memory = RLTradingMemory(10000)
        self.update_freq = 50
        self.train_step = 0

    def select_action(self, state: list[float]) -> int:
        """Epsilon-greedy action selection"""
        if random.random() < self.epsilon:
            return random.randint(0, self.action_dim - 1)

        state_tensor = torch.FloatTensor(state).unsqueeze(0)
        with torch.no_grad():
            q_values = self.q_network(state_tensor)
            return torch.argmax(q_values).item()

    def store(self, exp: TradingExperience):
        self.memory.push(exp)

    def update(self) -> float:
        if len(self.memory) < self.batch_size:
            return 0.0

        batch = random.sample(self.memory, self.batch_size)

        states = torch.FloatTensor([e.state for e in batch])
        actions = torch.LongTensor([e.action_idx for e in batch])
        rewards = torch.FloatTensor([e.reward for e in batch])
        next_states = torch.FloatTensor([e.next_state for e in batch])
        dones = torch.BoolTensor([e.done for e in batch])

        # Current Q-values
        current_q = self.q_network(states).gather(1, actions.unsqueeze(1))

        # Target Q-values
        with torch.no_grad():
            next_q = self.target_network(next_states).max(1)[0]
            target_q = rewards + self.gamma * next_q * (~dones)

        # Loss
        loss = F.mse_loss(current_q.squeeze(), target_q)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        # Decay epsilon
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        # Periodically update target network
        self.train_step += 1
        if self.train_step % self.update_freq == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())

        return loss.item()
