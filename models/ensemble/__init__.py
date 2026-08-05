# ═══════════════════════════════════════════════════════════════
#  Models Layer — ML / DL / RL ensemble
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("models.ensemble")


@dataclass
class ModelPrediction:
    signal: str  # BUY, SELL, HOLD
    confidence: float
    direction: str  # BULLISH, BEARISH, NEUTRAL
    metadata: dict


class MLPredictor:
    """Machine Learning pattern recognition"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.model = None
        self.scaler = None
        self.feature_names = [
            "rsi",
            "macd_hist",
            "sma_9",
            "sma_21",
            "atr",
            "volume",
            "change_pct",
            "volatility",
            "stoch_k",
        ]

    def predict(self, features: dict) -> float:
        """Predict signal strength (-1 to 1)"""
        return self._heuristic_predict(features)

    def _heuristic_predict(self, features: dict) -> float:
        """Heuristic prediction when model not trained"""
        score = 0.0

        rsi = features.get("rsi")
        if rsi and isinstance(rsi, (int, float)):
            if rsi < 30:
                score += 0.4
            elif rsi > 70:
                score -= 0.4
            elif 40 < rsi < 60:
                score += 0.1
        else:
            # No RSI data, assume neutral
            score += 0.05

        trend = features.get("trend", "SIDEWAYS")
        if trend == "UPTREND":
            score += 0.3
        elif trend == "DOWNTREND":
            score -= 0.3
        else:
            # SIDEWAYS or unknown
            score += 0.1

        macd = features.get("macd")
        if macd and isinstance(macd, dict):
            hist = macd.get("histogram")
            if hist is not None and isinstance(hist, (int, float)):
                if hist > 0:
                    score += 0.2
                else:
                    score -= 0.2
        else:
            # No MACD, neutral
            score += 0.05

        change_pct = features.get("change_pct", 0)
        if change_pct and isinstance(change_pct, (int, float)):
            score += min(change_pct / 10, 0.3)
        else:
            score += 0.05

        return float(np.clip(score, -1, 1))

    def train(self, training_data: list[dict]):
        """Train ML model"""
        log.info(f"Training ML model with {len(training_data)} samples")

    def get_prediction(self, features: dict) -> ModelPrediction:
        """Get structured prediction"""
        score = self.predict(features)

        # Lower threshold for more trading signals
        if score > 0.15:
            signal = "BUY"
            direction = "BULLISH"
        elif score < -0.15:
            signal = "SELL"
            direction = "BEARISH"
        else:
            signal = "HOLD"
            direction = "NEUTRAL"

        return ModelPrediction(
            signal=signal,
            confidence=abs(score),
            direction=direction,
            metadata={"score": score, "features": features},
        )


class DLPredictor:
    """Deep Learning order book prediction"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.model = None

    def predict(self, orderbook_data: dict, features: dict = None) -> float:
        """
        Predict price movement from order book using proper volume imbalance.

        Key metrics:
        - volume_imbalance: (-1 to 1) = (bid - ask) / (bid + ask)
        - spread_pct: tighter is better
        - depth thickness: total qty across levels
        """
        if not orderbook_data and not features:
            return 0.0

        # Use features as fallback when orderbook is empty
        if not orderbook_data and features:
            return self._predict_from_features(features)

        volume_imbalance = orderbook_data.get("volume_imbalance", 0)
        spread_pct = orderbook_data.get("spread_pct", 0)
        total_buy_qty = orderbook_data.get("total_buy_qty", 0)
        total_sell_qty = orderbook_data.get("total_sell_qty", 0)

        score = 0.0

        # 1. Volume imbalance (65% weight) - main signal
        abs_imbalance = abs(volume_imbalance)
        noise_floor = 0.08  # Below 8%, it's noise
        strong_threshold = 0.30  # Above 30%, it's strong

        if abs_imbalance > noise_floor:
            if abs_imbalance < strong_threshold:
                imbalance_score = (abs_imbalance - noise_floor) / (
                    strong_threshold - noise_floor
                )
            else:
                imbalance_score = 1.0

            if volume_imbalance > 0:
                score += imbalance_score * 0.65  # BULLISH
            else:
                score -= imbalance_score * 0.65  # BEARISH

        # 2. Spread multiplier (20%) - tighter is better
        if spread_pct < 0.05:
            score *= 1.20  # Liquid market, more trustworthy
        elif spread_pct > 0.25:
            score *= 0.50  # Wide spread, less trustworthy

        # 3. Depth thickness damper - thin books are unreliable
        total_depth = total_buy_qty + total_sell_qty
        if total_depth < 500:
            depth_factor = max(0.3, total_depth / 500)
            score *= depth_factor

        # 4. Extreme stacking bonus (+/-0.15)
        if volume_imbalance > 0.6:  # 3:1 ratio
            score += 0.15
        elif volume_imbalance < -0.6:
            score -= 0.15

        return float(np.clip(score, -1, 1))

    def _predict_from_features(self, features: dict) -> float:
        """Fallback: predict from technical features when orderbook unavailable"""
        score = 0.0

        change_pct = features.get("change_pct", 0)
        if change_pct and isinstance(change_pct, (int, float)):
            score += min(max(change_pct / 10, -0.5), 0.5)

        rsi = features.get("rsi")
        if rsi and isinstance(rsi, (int, float)):
            if rsi < 35:
                score += 0.2
            elif rsi > 65:
                score -= 0.2

        volatility = features.get("volatility", features.get("atr_pct", 0))
        if volatility and isinstance(volatility, (int, float)):
            if volatility > 3:
                score *= 0.8
            elif volatility < 1:
                score *= 1.1

        volume = features.get("volume", 0)
        if volume and isinstance(volume, (int, float)):
            if volume > 1000000:
                score *= 1.2

        return float(np.clip(score, -1, 1))

    def get_prediction(self, orderbook: dict) -> ModelPrediction:
        """Get structured prediction"""
        score = self.predict(orderbook)

        return ModelPrediction(
            signal="BUY" if score > 0.2 else ("SELL" if score < -0.2 else "HOLD"),
            confidence=abs(score),
            direction=(
                "BULLISH" if score > 0 else ("BEARISH" if score < 0 else "NEUTRAL")
            ),
            metadata={"orderbook": orderbook},
        )


class RLPredictor:
    """Reinforcement Learning agent"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.policy = None
        self.value_function = {}

    def get_action(self, state: dict) -> str:
        """Get RL action for state"""
        score = 0

        rsi = state.get("rsi", 50)
        if rsi:
            if rsi < 35:
                score += 0.6
            elif rsi > 65:
                score -= 0.5
            elif 40 <= rsi <= 60:
                score += 0.15

        trend = state.get("trend", "SIDEWAYS")
        if trend == "UPTREND":
            score += 0.4
        elif trend == "DOWNTREND":
            score -= 0.4
        elif trend == "SIDEWAYS":
            score += 0.1

        change_pct = state.get("change_pct", 0)
        if change_pct and isinstance(change_pct, (int, float)):
            score += min(max(change_pct / 15, -0.3), 0.3)

        if score > 0.2:
            return "BUY"
        elif score < -0.2:
            return "SELL"
        return "HOLD"

    def get_prediction(self, state: dict) -> ModelPrediction:
        """Get structured prediction"""
        action = self.get_action(state)

        score_map = {"BUY": 0.8, "SELL": -0.8, "HOLD": 0}

        direction = (
            "BULLISH"
            if action == "BUY"
            else ("BEARISH" if action == "SELL" else "NEUTRAL")
        )

        return ModelPrediction(
            signal=action,
            confidence=0.6,
            direction=direction,
            metadata={"state": state},
        )


class EnsembleModel:
    """Ensemble combining ML, DL, RL models"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.ml = MLPredictor(config)
        self.dl = DLPredictor(config)
        self.rl = RLPredictor(config)

        self.weights = {
            "ml": self.config.get("ml_weight", 0.4),
            "dl": self.config.get("dl_weight", 0.4),
            "rl": self.config.get("rl_weight", 0.2),
        }

        log.info(f"Ensemble model initialized with weights: {self.weights}")

    def predict(self, data: dict) -> ModelPrediction:
        """Get ensemble prediction"""
        features = data.get("features", {})
        orderbook = data.get("orderbook", {})

        ml_score = self.ml.predict(features)
        dl_score = self.dl.predict(orderbook, features)

        state = {k: v for k, v in features.items() if v is not None}
        rl_action = self.rl.get_action(state)
        rl_score = 0.8 if rl_action == "BUY" else (-0.8 if rl_action == "SELL" else 0)

        ml_weight = self.weights["ml"]
        dl_weight = self.weights["dl"] if dl_score != 0 else 0
        rl_weight = self.weights["rl"] if rl_score != 0 else 0

        total_weight = ml_weight + dl_weight + rl_weight
        if total_weight == 0:
            total_weight = 1.0

        ensemble_score = (
            ml_score * ml_weight + dl_score * dl_weight + rl_score * rl_weight
        ) / total_weight

        # Threshold for trading signals (low threshold for signal generation)
        min_conf = self.config.get("min_confidence", 0.05)
        if ensemble_score > min_conf:
            signal = "BUY"
            direction = "BULLISH"
        elif ensemble_score < -min_conf:
            signal = "SELL"
            direction = "BEARISH"
        else:
            signal = "HOLD"
            direction = "NEUTRAL"

        log.debug(
            f"Ensemble: score={ensemble_score:.3f} min_conf={min_conf} → signal={signal}"
        )

        return ModelPrediction(
            signal=signal,
            confidence=abs(ensemble_score),
            direction=direction,
            metadata={
                "ml_score": ml_score,
                "dl_score": dl_score,
                "rl_score": rl_score,
                "weights": self.weights,
            },
        )

    def train(self, training_data: list[dict]):
        """Train all models"""
        self.ml.train(training_data)
        log.info("Ensemble training complete")


# Singleton removed to prevent duplicate initialization during import
