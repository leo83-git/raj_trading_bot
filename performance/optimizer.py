# ═══════════════════════════════════════════════════════════════
#  Performance Optimization — Caching & Persistence
# ═══════════════════════════════════════════════════════════════
import json
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from quant_utils.logger import get_logger

log = get_logger("performance")


class LRUCache:
    """Thread-safe LRU cache with TTL support"""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache = OrderedDict()
        self.timestamps = {}
        self.lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None

            ts = self.timestamps.get(key, 0)
            if time.time() - ts > self.ttl_seconds:
                del self.cache[key]
                del self.timestamps[key]
                self.misses += 1
                return None

            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]

    def set(self, key: str, value: Any):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            elif len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)

            self.cache[key] = value
            self.timestamps[key] = time.time()

    def invalidate(self, key: str):
        with self.lock:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)

    def clear(self):
        with self.lock:
            self.cache.clear()
            self.timestamps.clear()
            self.hits = 0
            self.misses = 0

    def get_stats(self) -> dict:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0
        return {
            "size": len(self.cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 3),
        }


class IndicatorCache:
    """Cache for technical indicators with incremental updates"""

    def __init__(self, max_size: int = 500, ttl_seconds: int = 60):
        self.price_cache = LRUCache(max_size, ttl_seconds)
        self.indicator_cache = LRUCache(max_size, ttl_seconds)
        self.last_candle_time = {}

    def get_cached_candles(self, symbol: str, timeframe: str) -> list[dict] | None:
        key = f"{symbol}:{timeframe}"
        return self.price_cache.get(key)

    def cache_candles(self, symbol: str, timeframe: str, candles: list[dict]):
        key = f"{symbol}:{timeframe}"
        self.price_cache.set(key, candles)

    def get_indicators(self, symbol: str, timeframe: str) -> dict | None:
        key = f"{symbol}:{timeframe}"
        return self.indicator_cache.get(key)

    def cache_indicators(self, symbol: str, timeframe: str, indicators: dict):
        key = f"{symbol}:{timeframe}"
        self.indicator_cache.set(key, indicators)

    def update_incremental(self, symbol: str, timeframe: str, new_candle: dict):
        key = f"{symbol}:{timeframe}"
        candles = self.price_cache.get(key)

        if candles:
            candles = list(candles)
            candles.append(new_candle)
            if len(candles) > 200:
                candles = candles[-200:]
            self.price_cache.set(key, candles)

            self.invalidate_indicators(symbol, timeframe)
        else:
            self.cache_candles(symbol, timeframe, [new_candle])

    def invalidate_indicators(self, symbol: str, timeframe: str):
        key = f"{symbol}:{timeframe}"
        self.indicator_cache.invalidate(key)

    def get_stats(self) -> dict:
        return {
            "price_cache": self.price_cache.get_stats(),
            "indicator_cache": self.indicator_cache.get_stats(),
        }


class TradeHistoryDB:
    """File-based trade history persistence (JSON)"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.trades_file = os.path.join(data_dir, "trades.json")
        self.signals_file = os.path.join(data_dir, "signals.json")
        self.performance_file = os.path.join(data_dir, "model_performance.json")
        self._ensure_data_dir()
        self._lock = threading.Lock()

    def _ensure_data_dir(self):
        os.makedirs(self.data_dir, exist_ok=True)

    def _read_json(self, filepath: str) -> list[dict]:
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    def _write_json(self, filepath: str, data: list[dict]):
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def insert_trade(self, trade: dict):
        with self._lock:
            trades = self._read_json(self.trades_file)
            trade["id"] = len(trades) + 1
            trade["timestamp"] = trade.get("timestamp", datetime.now().isoformat())
            trades.append(trade)
            self._write_json(self.trades_file, trades[-1000:])

    def get_trades(self, symbol: str = None, days: int = 30) -> list[dict]:
        trades = self._read_json(self.trades_file)

        if symbol:
            trades = [t for t in trades if t.get("symbol") == symbol]

        if days:
            cutoff = datetime.now() - timedelta(days=days)
            trades = [
                t
                for t in trades
                if datetime.fromisoformat(t.get("timestamp", "2024-01-01")) >= cutoff
            ]

        return sorted(trades, key=lambda x: x.get("timestamp", ""), reverse=True)[:1000]

    def get_performance_summary(self, days: int = 30) -> dict:
        trades = self.get_trades(days=days)

        if not trades:
            return {"no_trades": True}

        winning = [t for t in trades if t.get("pnl", 0) > 0]
        losing = [t for t in trades if t.get("pnl", 0) <= 0]

        return {
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(trades) if trades else 0,
            "total_pnl": sum(t.get("pnl", 0) for t in trades),
            "avg_win": (
                sum(t.get("pnl", 0) for t in winning) / len(winning) if winning else 0
            ),
            "avg_loss": (
                abs(sum(t.get("pnl", 0) for t in losing) / len(losing)) if losing else 0
            ),
        }

    def insert_signal(self, signal: dict):
        with self._lock:
            signals = self._read_json(self.signals_file)
            signal["timestamp"] = signal.get("timestamp", datetime.now().isoformat())
            signals.append(signal)
            self._write_json(self.signals_file, signals[-1000:])

    def update_model_performance(self, model_name: str, performance: dict):
        with self._lock:
            perfs = self._read_json(self.performance_file)
            performance["timestamp"] = datetime.now().isoformat()
            performance["model_name"] = model_name
            perfs.append(performance)
            self._write_json(self.performance_file, perfs[-100:])

    def close(self):
        pass


class AdaptiveModelWeights:
    """Adaptive model weights based on recent performance"""

    def __init__(self, initial_weights: dict = None):
        self.initial_weights = initial_weights or {"ml": 0.4, "dl": 0.3, "rl": 0.3}
        self.current_weights = self.initial_weights.copy()
        self.performance_history = {}
        self.lookback_trades = 50
        self.adjustment_factor = 0.15

    def update_performance(self, model_name: str, trade_result: dict):
        if model_name not in self.performance_history:
            self.performance_history[model_name] = []

        history = self.performance_history[model_name]
        history.append(trade_result.get("pnl", 0))

        if len(history) > self.lookback_trades:
            history = history[-self.lookback_trades :]
            self.performance_history[model_name] = history

    def recalculate_weights(self) -> dict:
        model_performances = {}

        for model, history in self.performance_history.items():
            if not history:
                continue

            wins = sum(1 for p in history if p > 0)
            win_rate = wins / len(history) if history else 0
            avg_pnl = sum(history) / len(history) if history else 0

            score = win_rate * 0.6 + (avg_pnl / 1000) * 0.4
            model_performances[model] = max(0.1, score)

        if not model_performances:
            return self.current_weights

        total_score = sum(model_performances.values())

        for model in self.current_weights:
            base = self.initial_weights.get(model, 0.33)
            perf = model_performances.get(model, base)

            new_weight = (
                base * (1 - self.adjustment_factor)
                + (perf / total_score) * self.adjustment_factor
            )

            self.current_weights[model] = new_weight

        return self.current_weights

    def get_weights(self) -> dict:
        return self.current_weights.copy()

    def reset(self):
        self.current_weights = self.initial_weights.copy()
        self.performance_history = {}


class PerformanceOptimizer:
    """Orchestrate all performance optimizations"""

    def __init__(self, config: dict = None):
        self.config = config or {}

        self.indicator_cache = IndicatorCache(
            max_size=self.config.get("cache_size", 500),
            ttl_seconds=self.config.get("cache_ttl", 60),
        )

        self.trade_db = TradeHistoryDB(data_dir=self.config.get("data_dir", "data"))

        self.model_weights = AdaptiveModelWeights()

    def get_cached_indicators(
        self, symbol: str, timeframe: str = "5minute"
    ) -> dict | None:
        return self.indicator_cache.get_indicators(symbol, timeframe)

    def cache_indicators(self, symbol: str, timeframe: str, indicators: dict):
        self.indicator_cache.cache_indicators(symbol, timeframe, indicators)

    def update_indicators_incremental(
        self, symbol: str, timeframe: str, new_candle: dict
    ):
        self.indicator_cache.update_incremental(symbol, timeframe, new_candle)

    def record_trade(self, trade: dict):
        self.trade_db.insert_trade(trade)

        for model in ["ml", "dl", "rl"]:
            trade_result = {"pnl": trade.get("pnl", 0)}
            self.model_weights.update_performance(model, trade_result)

    def get_optimized_weights(self) -> dict:
        return self.model_weights.recalculate_weights()

    def get_performance_summary(self, days: int = 30) -> dict:
        return self.trade_db.get_performance_summary(days)

    def get_cache_stats(self) -> dict:
        return self.indicator_cache.get_stats()


def create_optimizer(config: dict = None) -> PerformanceOptimizer:
    return PerformanceOptimizer(config)
