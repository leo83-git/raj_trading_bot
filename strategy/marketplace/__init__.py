# Strategy Marketplace - Plugin-based Strategy System
import datetime
import importlib
import inspect
import os
from typing import Dict, List, Optional, Tuple

from quant_utils.logger import get_logger

log = get_logger("strategy.marketplace")

try:
    from tradingview_mcp.server import (
        combined_analysis as mcp_tradingview_combined_analysis,
    )
except ImportError:
    log.warning("TradingView MCP technical analysis not available")
    mcp_tradingview_combined_analysis = None

STRATEGY_HISTORY: dict[str, dict] = {}


def record_strategy_performance(strategy_name: str, pnl: float, action: str) -> None:
    """Record strategy performance for adaptive scoring"""
    if strategy_name not in STRATEGY_HISTORY:
        STRATEGY_HISTORY[strategy_name] = {
            "wins": 0,
            "losses": 0,
            "total_pnl": 0,
            "trades": 0,
        }

    stats = STRATEGY_HISTORY[strategy_name]
    stats["trades"] += 1
    stats["total_pnl"] += pnl
    if pnl > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1


def get_strategy_winrate(strategy_name: str) -> float:
    """Get historical win rate for a strategy"""
    stats = STRATEGY_HISTORY.get(strategy_name, {})
    trades = stats.get("trades", 0)
    if trades < 5:
        return 0.5
    wins = stats.get("wins", 0)
    return wins / trades


def get_strategy_avg_pnl(strategy_name: str) -> float:
    """Get average PnL per trade"""
    stats = STRATEGY_HISTORY.get(strategy_name, {})
    trades = stats.get("trades", 0)
    if trades < 5:
        return 0
    return stats.get("total_pnl", 0) / trades


class StrategyPlugin:
    """Base class for strategy plugins"""

    name: str = ""
    description: str = ""
    timeframe: str = "intraday"  # intraday, swing, scalping
    asset_types: list[str] = ["EQUITY"]  # EQUITY, OPTIONS, INDEX
    parameters: dict = {}

    def analyze(self, data: dict) -> dict | None:
        """Analyze and return signal"""
        raise NotImplementedError

    def get_signal(self, features: dict) -> dict | None:
        """Generate trading signal"""
        raise NotImplementedError

    def validate_with_tradingview(
        self, signal: dict, symbol: str, exchange: str = "NSE"
    ) -> dict:
        """
        Validate and enhance signal with TradingView technical analysis

        Args:
            signal: Trading signal dict with 'action' field (BUY/SELL)
            symbol: Stock symbol
            exchange: Exchange name (NSE, NASDAQ, NYSE, etc.)

        Returns:
            Enhanced signal dict with confidence potentially boosted if TA agrees
        """
        if signal is None:
            return None

        # Ensure signal has a confidence field (default 0.5)
        if "confidence" not in signal:
            signal["confidence"] = 0.5

        try:
            if mcp_tradingview_combined_analysis is None:
                log.debug(
                    f"TradingView TA validation skipped for {symbol} (tool unavailable)"
                )
                return signal

            # Call TradingView combined analysis for technical validation
            log.debug(f"Running TradingView TA validation for {symbol}...")
            ta_result = mcp_tradingview_combined_analysis(
                symbol=symbol, exchange=exchange, timeframe="1D"
            )

            if not ta_result or not isinstance(ta_result, dict):
                log.debug(f"Invalid TA result for {symbol}")
                return signal

            technical = (
                ta_result.get("technical", {}) if isinstance(ta_result, dict) else {}
            )
            ta_recommendation = (
                technical.get("market_sentiment", {}).get("buy_sell_signal", "")
                or ta_result.get("confluence", {}).get("recommendation", "")
            ).upper()
            signal_action = signal.get("action", "").upper()

            # Map TA recommendations to BUY/SELL
            ta_buy_signals = ["BUY", "STRONG_BUY", "BUYING"]
            ta_sell_signals = ["SELL", "STRONG_SELL", "SELLING"]

            ta_is_bullish = any(s in ta_recommendation for s in ta_buy_signals)
            ta_is_bearish = any(s in ta_recommendation for s in ta_sell_signals)

            signal_is_bullish = signal_action == "BUY"
            signal_is_bearish = signal_action == "SELL"

            # Check if TA agrees with signal
            ta_agrees = False
            if (
                signal_is_bullish
                and ta_is_bullish
                or signal_is_bearish
                and ta_is_bearish
            ):
                ta_agrees = True

            # Boost confidence if TA agrees
            if ta_agrees:
                current_confidence = signal.get("confidence", 0.5)
                boosted_confidence = min(current_confidence * 1.2, 1.0)
                signal["confidence"] = boosted_confidence
                signal["ta_validated"] = True
                signal["ta_recommendation"] = ta_recommendation

                log.info(
                    f"✓ TA agreement for {symbol}: {signal_action} | TA={ta_recommendation} | Confidence boosted: {current_confidence:.2f} → {boosted_confidence:.2f}"
                )
            else:
                signal["ta_validated"] = False
                signal["ta_recommendation"] = ta_recommendation
                log.debug(
                    f"TA disagreement for {symbol}: Signal={signal_action} | TA={ta_recommendation}"
                )

            return signal

        except Exception as e:
            log.warning(f"TradingView TA validation failed for {symbol}: {e}")
            return signal


class StrategyMarketplace:
    """Manages strategy plugins - load, enable, disable, evaluate"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.strategies: dict[str, StrategyPlugin] = {}
        self.enabled_strategies: list[str] = []
        self.strategy_path = self.config.get("strategy_path", "strategies")

        self._load_strategies()

    def _load_strategies(self):
        """Dynamically load all strategy plugins"""
        try:
            from strategy.marketplace.strategies import (
                BollingerBounceStrategy,
                BreakoutStrategy,
                IntradayMomentumStrategy,
                MACrossStrategy,
                MeanReversionStrategy,
                MultiTimeframeStrategy,
                PullbackStrategy,
                RelativeStrengthStrategy,
                ScalpingStrategy,
                SupertrendStrategy,
                SwingStrategy,
                VWAPStrategy,
            )

            strategy_classes = [
                BreakoutStrategy,
                MeanReversionStrategy,
                ScalpingStrategy,
                SwingStrategy,
                VWAPStrategy,
                IntradayMomentumStrategy,
                MultiTimeframeStrategy,
                RelativeStrengthStrategy,
                PullbackStrategy,
                SupertrendStrategy,
                MACrossStrategy,
                BollingerBounceStrategy,
            ]

            for cls in strategy_classes:
                try:
                    strategy = cls(self.config.get(cls.name, {}))
                    self.strategies[strategy.name] = strategy
                    log.info(f"Loaded strategy: {strategy.name}")
                except Exception as e:
                    log.warning(f"Error loading {cls.name}: {e}")

        except ImportError as e:
            log.warning(f"Could not load strategies module: {e}")

        self.enabled_strategies = list(self.strategies.keys())
        log.info(
            f"Strategy Marketplace initialized with {len(self.strategies)} strategies"
        )

    def get_strategies(self) -> list[str]:
        """Get list of available strategies"""
        return list(self.strategies.keys())

    def get_all_strategies(self) -> dict[str, StrategyPlugin]:
        """Get all loaded strategy objects"""
        return self.strategies

    def enable_strategy(self, name: str):
        """Enable a strategy"""
        if name in self.strategies and name not in self.enabled_strategies:
            self.enabled_strategies.append(name)
            log.info(f"Strategy enabled: {name}")

    def disable_strategy(self, name: str):
        """Disable a strategy"""
        if name in self.enabled_strategies:
            self.enabled_strategies.remove(name)
            log.info(f"Strategy disabled: {name}")

    def evaluate_all(self, symbol: str, features: dict) -> list[dict]:
        """Evaluate all enabled strategies for a symbol"""
        results = []

        for name in self.enabled_strategies:
            strategy = self.strategies.get(name)
            if not strategy:
                continue

            try:
                signal = strategy.get_signal(features)
                if signal:
                    # Validate and enhance signal with TradingView technical analysis
                    exchange = features.get("exchange", "NSE")
                    signal = strategy.validate_with_tradingview(
                        signal, symbol, exchange
                    )

                    results.append(
                        {
                            "strategy": name,
                            "signal": signal,
                            "timeframe": strategy.timeframe,
                            "asset_types": strategy.asset_types,
                        }
                    )
            except Exception as e:
                log.debug(f"Strategy {name} error for {symbol}: {e}")

        return results

    def get_best_strategy(
        self, symbol: str, features: dict, ml_score: float = 0
    ) -> dict | None:
        """Get best strategy based on ML scoring"""
        results = self.evaluate_all(symbol, features)

        if not results:
            return None

        for r in results:
            r["score"] = ml_score * 0.3  # Base score from ML
            if r["signal"].get("action") == "BUY":
                r["score"] += 0.5
            elif r["signal"].get("action") == "SELL":
                r["score"] += 0.3

            strategy_name = r.get("strategy", "")
            winrate = get_strategy_winrate(strategy_name)
            avg_pnl = get_strategy_avg_pnl(strategy_name)

            r["score"] *= 0.5 + 0.5 * winrate
            r["score"] += avg_pnl * 0.01

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[0] if results else None

    def get_performance_summary(self) -> dict:
        """Get performance summary for all strategies"""
        summary = {}
        for name, stats in STRATEGY_HISTORY.items():
            summary[name] = {
                "winrate": stats["wins"] / max(1, stats["trades"]),
                "avg_pnl": stats["total_pnl"] / max(1, stats["trades"]),
                "trades": stats["trades"],
            }
        return summary


REGIME_MAP = {
    "TRENDING_UP": ["breakout", "oi_buildup", "index_momentum"],
    "TRENDING_DOWN": ["breakout", "oi_buildup"],
    "MEAN_REVERTING": ["mean_reversion", "bollinger_band"],
    "SIDEWAYS": ["mean_reversion", "bollinger_band", "vwap"],
    "HIGH_VOLATILITY": ["scalping", "expiry_theta"],
    "LOW_VOLATILITY": ["scalping", "breakout"],
}


class StrategyManager:
    """Manages strategies and selects best based on regime and performance"""

    def __init__(self, strategies: dict[str, StrategyPlugin] = None):
        self.strategies = strategies or {}

    def filter_by_regime(self, regime: str) -> list[StrategyPlugin]:
        """Filter strategies valid for current regime"""
        from intelligence.regime.vocabulary import normalize_regime

        normalized = normalize_regime(regime)
        valid_names = REGIME_MAP.get(normalized, REGIME_MAP.get("SIDEWAYS", []))

        filtered = []
        for name in valid_names:
            if name in self.strategies:
                filtered.append(self.strategies[name])

        if not filtered:
            filtered = list(self.strategies.values())

        return filtered

    def get_signals(
        self, features: dict, regime: str, strategy_hint: str = None
    ) -> list[dict]:
        """Get signals from strategies valid for the regime or specified hint"""
        if strategy_hint and strategy_hint in self.strategies:
            valid_strategies = [self.strategies[strategy_hint]]
        else:
            valid_strategies = self.filter_by_regime(regime)

        signals = []
        for strat in valid_strategies:
            try:
                sig = strat.get_signal(features)
                if sig:
                    sig["strategy"] = strat.name
                    signals.append(sig)
            except Exception as e:
                log.debug(f"Strategy {strat.name} error: {e}")

        return signals


class StrategyPerformanceTracker:
    """Tracks strategy performance for selection"""

    def __init__(self):
        self.performance: dict[str, dict] = {}

    def get_performance(self, strategy_name: str) -> dict:
        """Get performance metrics for a strategy"""
        if strategy_name in STRATEGY_HISTORY:
            stats = STRATEGY_HISTORY[strategy_name]
            return {
                "win_rate": stats["wins"] / max(1, stats["trades"]),
                "avg_pnl": stats["total_pnl"] / max(1, stats["trades"]),
                "trades": stats["trades"],
            }
        return {"win_rate": 0.5, "avg_pnl": 0, "trades": 0}

    def get_all_performance(self) -> dict:
        """Get performance for all strategies"""
        result = {}
        for strat_name in STRATEGY_HISTORY:
            stats = STRATEGY_HISTORY[strat_name]
            win_rate = stats["wins"] / max(1, stats["trades"])
            avg_pnl = stats["total_pnl"] / max(1, stats["trades"])
            suppressed = win_rate < 0.35 or avg_pnl < -200
            result[strat_name] = {
                "win_rate": win_rate,
                "avg_pnl": avg_pnl,
                "trades": stats["trades"],
                "suppressed": suppressed,
            }
        return result

    def update(self, strategy_name: str, pnl: float):
        """Update performance after trade"""
        record_strategy_performance(strategy_name, pnl, "BUY" if pnl > 0 else "SELL")

    def record_trade(self, strategy_name: str, pnl: float, won: bool):
        """Record trade outcome"""
        self.update(strategy_name, pnl)


def _get_time_multiplier(strategy_name: str, current_time: datetime.time) -> float:
    """Get time-based score multiplier for a strategy."""
    preferred_morning = {"MOMENTUM", "BREAKOUT"}
    preferred_afternoon = {"MEAN_REVERSION", "OI_BUILDUP"}
    strat_key = strategy_name.upper() if strategy_name else ""

    morning_start = datetime.time(9, 15)
    morning_end = datetime.time(11, 0)
    midday_end = datetime.time(14, 0)
    afternoon_end = datetime.time(15, 15)

    if current_time < morning_start or current_time > afternoon_end:
        return 1.0

    if morning_start <= current_time < morning_end:
        return 1.5 if strat_key in preferred_morning else 0.7
    if morning_end <= current_time < midday_end:
        return 1.0
    if midday_end <= current_time <= afternoon_end:
        return 1.5 if strat_key in preferred_afternoon else 0.7

    return 1.0


def select_best_signal(
    signals: list[dict], tracker: StrategyPerformanceTracker
) -> dict | None:
    """Select best signal based on confidence, historical performance and time-of-day preferences."""
    if not signals:
        return None

    now = datetime.datetime.now().time()
    scored = []
    time_window_active = datetime.time(9, 15) <= now <= datetime.time(15, 15)

    for sig in signals:
        strat = sig.get("strategy", "")
        perf = tracker.get_performance(strat)

        confidence = sig.get("confidence", 0.5)
        if confidence == 0.5:
            confidence = sig.get("entry", 0) and 0.6 or 0.5

        multiplier = 1.0
        if time_window_active:
            multiplier = _get_time_multiplier(strat, now)

        score = (0.5 * confidence + 0.5 * perf.get("win_rate", 0.5)) * multiplier

        scored.append((score, sig, multiplier))

    if not scored:
        return None

    best_score, best_sig, best_multiplier = max(scored, key=lambda x: x[0])
    if time_window_active and best_multiplier != 1.0:
        log.info(
            f"Time-based strategy switch: selected {best_sig.get('strategy')} "
            f"with multiplier={best_multiplier:.1f} at {now.strftime('%H:%M')}"
        )

    return best_sig
