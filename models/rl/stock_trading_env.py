# ══════════════════════════════════════════════════════════════════════════════
#  FinRL-Inspired Stock Trading Environment for RL Training
#  Adapted from: https://github.com/AI4Finance-Foundation/FinRL
#  License: MIT
# ══════════════════════════════════════════════════════════════════════════════
"""
Gym-style trading environment for reinforcement learning.
Supports multi-stock trading with technical indicators.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

from quant_utils.logger import get_logger

log = get_logger("env.stock_trading")


@dataclass
class TradingState:
    """Current state of the trading environment"""

    cash: float
    prices: np.ndarray
    holdings: np.ndarray
    indicators: np.ndarray
    turbulence: float = 0.0
    day: int = 0


class StockTradingEnv(gym.Env):
    """
    A stock trading environment for RL training.

    State space: [cash, prices, holdings, technical_indicators]
    Action space: Box(-1, 1, shape=(stock_dim,)) where:
        -1 to 0: Sell (fraction of holdings)
        0: Hold
        0 to 1: Buy (fraction of cash)

    Adapted from FinRL StockTradingEnv with simplifications for Indian markets.
    """

    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        stock_dim: int = 1,
        initial_amount: float = 300000,
        buy_cost_pct: float = 0.001,
        sell_cost_pct: float = 0.001,
        reward_scaling: float = 1e-4,
        tech_indicator_list: list[str] = None,
        turbulence_threshold: float = 1e8,
        risk_indicator_col: str = "turbulence",
        max_stocks_perTrade: int = 100,
        initial: bool = True,
        previous_state: list = None,
    ):
        super().__init__()

        self.df = df
        self.stock_dim = stock_dim
        self.initial_amount = initial_amount
        self.buy_cost_pct = buy_cost_pct
        self.sell_cost_pct = sell_cost_pct
        self.reward_scaling = reward_scaling
        self.tech_indicator_list = tech_indicator_list or [
            "rsi",
            "macd",
            "bollinger",
            "atr",
            "sma_9",
            "sma_21",
        ]
        self.turbulence_threshold = turbulence_threshold
        self.risk_indicator_col = risk_indicator_col
        self.max_stocks_per_trade = max_stocks_perTrade
        self.initial = initial
        self.previous_state = previous_state or []

        # Calculate spaces
        self.state_space = (
            1 + self.stock_dim + self.stock_dim + len(self.tech_indicator_list)
        )
        self.action_space = spaces.Box(low=-1, high=1, shape=(self.stock_dim,))
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.state_space,)
        )

        # Episode state
        self.day = 0
        self.data = None
        self.terminal = False
        self.asset_memory = [self.initial_amount]
        self.rewards_memory = []
        self.actions_memory = []
        self.state_memory = []
        self.date_memory = []
        self.trades = 0
        self.cost = 0
        self.turbulence = 0
        self.episode = 0

        # Seed for reproducibility
        self._seed()

        # Initialize
        self.state = self._initiate_state()

    def _seed(self, seed=None):
        """Set random seed"""
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

    def _get_date(self):
        """Get current date from dataframe"""
        if len(self.df) > self.day:
            return (
                str(self.df.iloc[self.day].name)
                if hasattr(self.df.iloc[self.day], "name")
                else str(self.day)
            )
        return str(self.day)

    def _initiate_state(self) -> np.ndarray:
        """Initialize state for the first time"""
        if len(self.df.tic.unique()) > 1:
            # Multiple stocks
            state = (
                [self.initial_amount]
                + self.data.close.values.tolist()
                + [0] * self.stock_dim
                + sum(
                    [
                        self.data[tech].values.tolist()
                        for tech in self.tech_indicator_list
                    ],
                    [],
                )
            )
        else:
            # Single stock
            state = (
                [self.initial_amount]
                + [self.data.close]
                + [0] * self.stock_dim
                + [self.data[tech] for tech in self.tech_indicator_list]
            )
        return np.array(state, dtype=np.float32)

    def _update_state(self) -> np.ndarray:
        """Update state after each step"""
        if len(self.df.tic.unique()) > 1:
            state = (
                [self.state[0]]  # cash
                + self.data.close.values.tolist()
                + self.state[
                    1 + self.stock_dim : 1 + 2 * self.stock_dim
                ].tolist()  # holdings
                + sum(
                    [
                        self.data[tech].values.tolist()
                        for tech in self.tech_indicator_list
                    ],
                    [],
                )
            )
        else:
            state = (
                [self.state[0]]
                + [self.data.close]
                + self.state[1 + self.stock_dim : 1 + 2 * self.stock_dim].tolist()
                + [self.data[tech] for tech in self.tech_indicator_list]
            )
        return np.array(state, dtype=np.float32)

    def _sell_stock(self, index: int, action: float) -> float:
        """Execute sell action"""
        if action == 0:
            return 0

        price = self.state[index + 1]
        holdings = self.state[index + self.stock_dim + 1]

        if price <= 0 or holdings <= 0:
            return 0

        # Calculate sell amount
        sell_num_shares = min(abs(action), holdings)
        sell_amount = price * sell_num_shares * (1 - self.sell_cost_pct)

        # Update state
        self.state[0] += sell_amount
        self.state[index + self.stock_dim + 1] -= sell_num_shares
        self.cost += price * sell_num_shares * self.sell_cost_pct
        self.trades += 1

        return sell_num_shares

    def _buy_stock(self, index: int, action: float) -> float:
        """Execute buy action"""
        if action == 0:
            return 0

        price = self.state[index + 1]
        if price <= 0:
            return 0

        # Calculate available amount considering cost
        available_amount = self.state[0] / (price * (1 + self.buy_cost_pct))
        buy_num_shares = min(action, available_amount)
        buy_amount = price * buy_num_shares * (1 + self.buy_cost_pct)

        # Update state
        self.state[0] -= buy_amount
        self.state[index + self.stock_dim + 1] += buy_num_shares
        self.cost += price * buy_num_shares * self.buy_cost_pct
        self.trades += 1

        return buy_num_shares

    def step(self, actions: np.ndarray):
        """Execute one step in the environment"""
        self.terminal = self.day >= len(self.df.index.unique()) - 1

        if self.terminal:
            # End of episode
            end_total_asset = self.state[0] + sum(
                self.state[1 : 1 + self.stock_dim]
                * self.state[1 + self.stock_dim : 1 + 2 * self.stock_dim]
            )

            df_total_value = pd.DataFrame({"account_value": self.asset_memory})
            df_total_value["date"] = self.date_memory
            df_total_value["daily_return"] = df_total_value[
                "account_value"
            ].pct_change()

            # Calculate Sharpe ratio
            if df_total_value["daily_return"].std() != 0:
                sharpe = (
                    (252**0.5)
                    * df_total_value["daily_return"].mean()
                    / df_total_value["daily_return"].std()
                )
            else:
                sharpe = 0

            total_reward = end_total_asset - self.asset_memory[0]

            if self.episode % 10 == 0:
                log.info(
                    f"Episode {self.episode}: End Asset={end_total_asset:.2f}, "
                    f"Reward={total_reward:.2f}, Sharpe={sharpe:.3f}"
                )

            return self.state, total_reward * self.reward_scaling, True, False, {}

        # Execute actions
        begin_total_asset = self.state[0] + sum(
            self.state[1 : 1 + self.stock_dim]
            * self.state[1 + self.stock_dim : 1 + 2 * self.stock_dim]
        )

        # Sort actions: sell first (negative), then buy (positive)
        argsort_actions = np.argsort(actions)
        sell_index = argsort_actions[: np.where(actions < 0)[0].shape[0]]
        buy_index = argsort_actions[::-1][: np.where(actions > 0)[0].shape[0]]

        # Execute sells
        for index in sell_index:
            actions[index] = self._sell_stock(index, actions[index]) * (-1)

        # Execute buys
        for index in buy_index:
            actions[index] = self._buy_stock(index, actions[index])

        self.actions_memory.append(actions)

        # Move to next day
        self.day += 1
        self.data = self.df.loc[self.day]

        # Update turbulence
        if self.risk_indicator_col in self.data:
            self.turbulence = (
                self.data[self.risk_indicator_col]
                if isinstance(self.data, pd.Series)
                else self.data[self.risk_indicator_col].values[0]
            )

        self.state = self._update_state()

        # Calculate end asset and reward
        end_total_asset = self.state[0] + sum(
            self.state[1 : 1 + self.stock_dim]
            * self.state[1 + self.stock_dim : 1 + 2 * self.stock_dim]
        )
        self.asset_memory.append(end_total_asset)
        self.date_memory.append(self._get_date())

        reward = end_total_asset - begin_total_asset
        self.rewards_memory.append(reward)
        self.state_memory.append(self.state.copy())

        return self.state, reward * self.reward_scaling, False, False, {}

    def reset(self, seed=None, options=None):
        """Reset environment to initial state"""
        if seed is not None:
            self._seed(seed)

        self.day = 0
        self.data = self.df.loc[self.day]
        self.terminal = False

        if self.initial:
            self.state = self._initiate_state()
            self.asset_memory = [self.initial_amount]
        else:
            if self.previous_state:
                self.state = self._initiate_state()
                self.asset_memory = [
                    self.previous_state[0]
                    + sum(
                        np.array(self.state[1 : 1 + self.stock_dim])
                        * np.array(
                            self.state[1 + self.stock_dim : 1 + 2 * self.stock_dim]
                        )
                    )
                ]
            else:
                self.state = self._initiate_state()
                self.asset_memory = [self.initial_amount]

        self.cost = 0
        self.trades = 0
        self.turbulence = 0
        self.rewards_memory = []
        self.actions_memory = []
        self.date_memory = [self._get_date()]
        self.episode += 1

        return self.state, {}

    def render(self, mode="human"):
        """Render current state"""
        return {
            "day": self.day,
            "cash": self.state[0],
            "holdings": self.state[
                1 + self.stock_dim : 1 + 2 * self.stock_dim
            ].tolist(),
            "total_asset": (
                self.asset_memory[-1] if self.asset_memory else self.initial_amount
            ),
        }


class OptionsTradingEnv(gym.Env):
    """
    Specialized environment for options trading.
    Considers ATM/OTM strikes, IV, theta decay.
    """

    def __init__(
        self,
        price_data: pd.DataFrame,
        option_chain_data: dict,
        initial_amount: float = 300000,
        max_position: int = 5,
        gamma_decay: float = 0.01,
    ):
        super().__init__()

        self.price_data = price_data
        self.option_chain_data = option_chain_data
        self.initial_amount = initial_amount
        self.max_position = max_position
        self.gamma_decay = gamma_decay

        # State: [cash, pnl, positions, gamma, theta, vega, IV_rank]
        self.state_space = 10
        # Action: [0=Hold, 1=Buy ATM Call, 2=Buy ATM Put, 3=Sell ATM Call, 4=Sell ATM Put, 5=Sell straddle]
        self.action_space = spaces.Discrete(6)

        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(self.state_space,)
        )

        self.day = 0
        self.state = None
        self.terminal = False

    def step(self, action: int):
        """Execute options trading action"""
        if self.terminal:
            return self.state, 0, True, False, {}

        # Simplified reward calculation based on option Greeks
        reward = 0
        gamma = self.option_chain_data.get("gamma", 0)
        theta = self.option_chain_data.get("theta", 0)

        # Time decay reward (theta)
        reward += theta * self.gamma_decay

        # Move to next day
        self.day += 1
        self.terminal = self.day >= len(self.price_data) - 1

        return self.state, reward, self.terminal, False, {}

    def reset(self, seed=None, options=None):
        """Reset environment"""
        self.day = 0
        self.terminal = False
        self.state = np.zeros(self.state_space)
        self.state[0] = self.initial_amount
        return self.state, {}
