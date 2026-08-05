# ═══════════════════════════════════════════════════════════════
#  ML Models — Pattern Recognition with XGBoost
# ═══════════════════════════════════════════════════════════════
import os
import pickle
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("models.ml")

try:
    import xgboost as xgb
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    log.warning("scikit-learn not available, using heuristics")


class MLPatternRecognizer:
    """ML-based pattern recognition for trading signals"""

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or "models/ml_model.pkl"
        self.scaler_path = "models/ml_scaler.pkl"

        self.is_trained = False
        self.model = None
        self.scaler = None

        self._load_model()

    def _load_model(self):
        """Load trained model if exists"""
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "rb") as f:
                    self.model = pickle.load(f)
                with open(self.scaler_path, "rb") as f:
                    self.scaler = pickle.load(f)
                self.is_trained = True
                log.info(f"Loaded ML model from {self.model_path}")
            except Exception as e:
                log.warning(f"Could not load model: {e}")

    def prepare_features(self, features: dict) -> np.ndarray:
        """Convert features dict to model input array"""
        feature_names = [
            "rsi",
            "macd",
            "macd_signal",
            "macd_histogram",
            "sma_9",
            "sma_21",
            "sma_50",
            "ema_9",
            "ema_21",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "atr",
            "volatility",
        ]

        values = []
        for name in feature_names:
            val = features.get(name)
            if val is None:
                values.append(0.0)
            elif isinstance(val, dict):
                if name == "macd":
                    values.append(val.get("macd", 0))
                elif name == "macd_signal":
                    values.append(val.get("signal", 0))
                elif name == "macd_histogram":
                    values.append(val.get("histogram", 0))
                elif name == "bb_upper":
                    values.append(val.get("upper", 0))
                elif name == "bb_middle":
                    values.append(val.get("middle", 0))
                elif name == "bb_lower":
                    values.append(val.get("lower", 0))
                else:
                    values.append(0.0)
            else:
                values.append(float(val))

        return np.array(values).reshape(1, -1)

    def predict(self, features: dict) -> float:
        """Predict trading signal from features"""
        if not self.is_trained or self.model is None:
            return self._heuristic_predict(features)

        try:
            X = self.prepare_features(features)
            X = self.scaler.transform(X)

            prob = self.model.predict_proba(X)[0]

            # prob[0] = SELL (original -1), prob[1] = HOLD (original 0), prob[2] = BUY (original 1)
            # Convert back to -1 to 1 scale
            score = prob[2] - prob[0]  # BUY probability - SELL probability

            return float(np.clip(score, -1, 1))

        except Exception as e:
            log.error(f"ML prediction error: {e}")
            return self._heuristic_predict(features)

    def _heuristic_predict(self, features: dict) -> float:
        """Heuristic prediction when model not trained"""
        score = 0.0

        rsi = features.get("rsi")
        if rsi:
            if rsi < 30:
                score += 0.4
            elif rsi > 70:
                score -= 0.4
            elif 40 < rsi < 60:
                score += 0.1

        trend = features.get("trend", "SIDEWAYS")
        if trend == "UPTREND":
            score += 0.3
        elif trend == "DOWNTREND":
            score -= 0.3

        macd = features.get("macd")
        if macd and isinstance(macd, dict):
            histogram = macd.get("histogram")
            if histogram is not None and histogram > 0:
                score += 0.2
            else:
                score -= 0.2

        return float(np.clip(score, -1, 1))

    def train(self, historical_data: list[dict]):
        """Train the ML model"""
        if not SKLEARN_AVAILABLE:
            log.warning("sklearn not available, skipping training")
            return

        log.info(f"Training ML model on {len(historical_data)} samples")

        X = []
        y = []

        for data in historical_data:
            features = self.prepare_features(data.get("features", {})).flatten()
            label = data.get("label", 0)

            # Convert labels: -1 -> 0 (SELL), 0 -> 1 (HOLD), 1 -> 2 (BUY)
            label = label + 1

            X.append(features)
            y.append(label)

        X = np.array(X)
        y = np.array(y)

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            objective="multi:softprob",
            num_class=3,
            random_state=42,
        )

        self.model.fit(X_scaled, y)

        os.makedirs("models", exist_ok=True)
        with open(self.model_path, "wb") as f:
            pickle.dump(self.model, f)
        with open(self.scaler_path, "wb") as f:
            pickle.dump(self.scaler, f)

        self.is_trained = True
        log.info(f"ML model trained and saved to {self.model_path}")

    def get_confidence(self, features: dict) -> float:
        """Get prediction confidence"""
        score = self.predict(features)
        return abs(score)

    def get_feature_importance(self) -> dict[str, float]:
        """Get feature importance scores"""
        if not self.is_trained or self.model is None:
            return {}

        feature_names = [
            "rsi",
            "macd",
            "macd_signal",
            "macd_histogram",
            "sma_9",
            "sma_21",
            "sma_50",
            "ema_9",
            "ema_21",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "atr",
            "volatility",
        ]

        try:
            importance = self.model.feature_importances_
            return {name: float(imp) for name, imp in zip(feature_names, importance)}
        except:
            return {}

    def get_prediction_label(self, features: dict) -> str:
        """Get prediction as label string"""
        if not self.is_trained or self.model is None:
            score = self._heuristic_predict(features)
            if score > 0.15:
                return "BUY"
            elif score < -0.15:
                return "SELL"
            return "HOLD"

        try:
            X = self.prepare_features(features)
            X = self.scaler.transform(X)
            pred = self.model.predict(X)[0]
            # Convert back: 0 -> SELL, 1 -> HOLD, 2 -> BUY
            label_map = {0: "SELL", 1: "HOLD", 2: "BUY"}
            return label_map.get(pred, "HOLD")
        except:
            return "HOLD"
