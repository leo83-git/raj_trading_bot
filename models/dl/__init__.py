# ═══════════════════════════════════════════════════════════════
#  DL Models (Order Book + Vol Surface) with PyTorch
# ═══════════════════════════════════════════════════════════════
import os
import pickle
from typing import Dict, List, Optional

import numpy as np

from quant_utils.logger import get_logger

log = get_logger("models.dl")

try:
    import torch
    from torch import nn, optim
    from torch.utils.data import DataLoader, TensorDataset

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    log.warning("PyTorch not available, using heuristics")


class OrderBookLSTM(nn.Module):
    """LSTM model for order book prediction"""

    def __init__(self, input_size=20, hidden_size=64, num_layers=2, dropout=0.2):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size, hidden_size, num_layers, batch_first=True, dropout=dropout
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
            nn.Tanh(),
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        out = self.fc(lstm_out[:, -1, :])
        return out.squeeze()


class VolatilityNet(nn.Module):
    """Neural network for IV surface prediction"""

    def __init__(self, input_size=5, hidden_size=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return self.net(x).squeeze()


class DLOrderBookPredictor:
    """Deep Learning model for order book prediction"""

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or "models/ob_lstm.pth"
        self.is_trained = False
        self.model = None
        self.scaler = None

        if TORCH_AVAILABLE:
            self.model = OrderBookLSTM()
            self._load_model()

    def _load_model(self):
        """Load trained model if exists"""
        if os.path.exists(self.model_path):
            try:
                self.model.load_state_dict(torch.load(self.model_path))
                self.is_trained = True
                self.model.eval()
                log.info(f"Loaded OB model from {self.model_path}")
            except Exception as e:
                log.warning(f"Could not load OB model: {e}")

    def prepare_ob_features(self, ob: dict) -> np.ndarray:
        """Extract features from order book"""
        if not ob:
            return np.zeros(20)

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        features = []

        for i in range(5):
            bid_price = bids[i][0] if i < len(bids) else 0
            bid_vol = bids[i][1] if i < len(bids) else 0
            ask_price = asks[i][0] if i < len(asks) else 0
            ask_vol = asks[i][1] if i < len(asks) else 0

            features.extend([bid_price, bid_vol, ask_price, ask_vol])

        total_bid_vol = sum(b[1] for b in bids[:10])
        total_ask_vol = sum(a[1] for a in asks[:10])

        imbalance = (total_bid_vol - total_ask_vol) / (
            total_bid_vol + total_ask_vol + 1
        )
        features.extend([total_bid_vol, total_ask_vol, imbalance])

        if len(features) < 20:
            features.extend([0] * (20 - len(features)))

        return np.array(features[:20], dtype=np.float32)

    def predict(self, order_book_data: dict) -> float:
        """Predict price movement from order book"""
        if not self.is_trained or self.model is None:
            return self._heuristic_predict(order_book_data)

        try:
            X = self.prepare_ob_features(order_book_data)
            X_tensor = torch.FloatTensor(X).unsqueeze(0).unsqueeze(0)

            with torch.no_grad():
                score = self.model(X_tensor).item()

            return float(score)

        except Exception as e:
            log.error(f"OB prediction error: {e}")
            return self._heuristic_predict(order_book_data)

    def _heuristic_predict(self, ob: dict) -> float:
        """Heuristic order book prediction"""
        if not ob:
            return 0.0

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if not bids or not asks:
            return 0.0

        bid_vol = sum(b[1] for b in bids[:5])
        ask_vol = sum(a[1] for a in asks[:5])

        imbalance = (
            (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
        )

        return float(imbalance)

    def train(self, data: list[dict]):
        """Train DL model"""
        if not TORCH_AVAILABLE or len(data) < 100:
            log.warning("Not enough data or PyTorch unavailable")
            self.is_trained = False
            return

        log.info(f"Training OB LSTM on {len(data)} samples")

        X = []
        y = []

        for item in data:
            features = self.prepare_ob_features(item.get("ob", {}))
            label = item.get("label", 0)

            X.append(features)
            y.append(label)

        X = torch.FloatTensor(X).unsqueeze(1)
        y = torch.FloatTensor(y)

        dataset = TensorDataset(X, y)
        loader = DataLoader(dataset, batch_size=32, shuffle=True)

        self.model = OrderBookLSTM()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        criterion = nn.MSELoss()

        for epoch in range(50):
            self.model.train()
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                pred = self.model(batch_X)
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()

        os.makedirs("models", exist_ok=True)
        torch.save(self.model.state_dict(), self.model_path)

        self.model.eval()
        self.is_trained = True
        log.info(f"OB model trained and saved to {self.model_path}")


class VolatilitySurfaceModel:
    """DL model for volatility surface modeling"""

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or "models/vol_net.pth"
        self.is_trained = False
        self.model = None

        if TORCH_AVAILABLE:
            self.model = VolatilityNet()
            self._load_model()

    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                self.model.load_state_dict(torch.load(self.model_path))
                self.is_trained = True
                self.model.eval()
                log.info(f"Loaded Vol model from {self.model_path}")
            except Exception as e:
                log.warning(f"Could not load Vol model: {e}")

    def prepare_iv_features(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        moneyness: float,
        option_type: int,
    ) -> np.ndarray:
        """Prepare features for IV prediction"""
        moneyness = strike / spot if spot > 0 else 1.0
        return np.array(
            [spot, strike, time_to_expiry, moneyness, option_type], dtype=np.float32
        )

    def predict_iv(
        self, spot: float, strike: float, time_to_expiry: float, option_type: str = "CE"
    ) -> float:
        """Predict implied volatility"""
        if not self.is_trained or self.model is None:
            return self._heuristic_iv(spot, strike, time_to_expiry)

        try:
            opt_type = 1 if option_type == "CE" else 0
            X = self.prepare_iv_features(spot, strike, time_to_expiry, 0, opt_type)
            X_tensor = torch.FloatTensor(X).unsqueeze(0)

            with torch.no_grad():
                iv = self.model(X_tensor).item()

            return float(iv * 0.5)

        except Exception as e:
            log.error(f"IV prediction error: {e}")
            return self._heuristic_iv(spot, strike, time_to_expiry)

    def _heuristic_iv(self, spot: float, strike: float, time_to_expiry: float) -> float:
        """Heuristic IV calculation"""
        moneyness = strike / spot if spot > 0 else 1.0

        base_iv = 0.20

        if moneyness < 0.9:
            return base_iv * 1.2
        elif moneyness > 1.1:
            return base_iv * 1.3
        return base_iv

    def detect_skew(self, ivs: dict) -> float:
        """Detect IV skew"""
        if not ivs:
            return 0.0

        strikes = sorted(ivs.keys())
        if len(strikes) < 2:
            return 0.0

        low_iv = ivs.get(strikes[0], 0)
        high_iv = ivs.get(strikes[-1], 0)

        return float(high_iv - low_iv)

    def train(self, data: list[dict]):
        """Train volatility model"""
        if not TORCH_AVAILABLE or len(data) < 100:
            log.warning("Not enough data or PyTorch unavailable")
            return

        log.info(f"Training Vol model on {len(data)} samples")

        X = []
        y = []

        for item in data:
            spot = item.get("spot", 10000)
            strike = item.get("strike", 10000)
            tte = item.get("time_to_expiry", 0.04)
            opt_type = 1 if item.get("option_type") == "CE" else 0
            iv = item.get("iv", 0.2)

            features = self.prepare_iv_features(spot, strike, tte, 0, opt_type)
            X.append(features)
            y.append(iv)

        X = torch.FloatTensor(X)
        y = torch.FloatTensor(y)

        dataset = TensorDataset(X, y)
        loader = DataLoader(dataset, batch_size=32, shuffle=True)

        self.model = VolatilityNet()
        optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        criterion = nn.MSELoss()

        for epoch in range(50):
            self.model.train()
            for batch_X, batch_y in loader:
                optimizer.zero_grad()
                pred = self.model(batch_X)
                loss = criterion(pred, batch_y)
                loss.backward()
                optimizer.step()

        os.makedirs("models", exist_ok=True)
        torch.save(self.model.state_dict(), self.model_path)

        self.model.eval()
        self.is_trained = True
        log.info(f"Vol model trained and saved to {self.model_path}")
