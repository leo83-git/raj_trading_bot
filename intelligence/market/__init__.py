# ═══════════════════════════════════════════════════════════════
#  Intelligence Layer — Market sentiment & macro indicators
# ═══════════════════════════════════════════════════════════════
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from quant_utils.logger import get_logger

log = get_logger("intelligence.market")
# Enable detailed debug logging for this module
# Reduce verbosity: use INFO level for this module.
log.setLevel(logging.INFO)

# Simple in‑memory cache for NSE market sentiment (TTL 5 minutes)
_sentiment_cache: dict[str, Any] = {}
_sentiment_cache_timestamp: dict[str, float] = {}

try:
    from tradingview_mcp.server import (
        combined_analysis as mcp_tradingview_combined_analysis,
    )
    from tradingview_mcp.server import financial_news as mcp_tradingview_financial_news
    from tradingview_mcp.server import (
        market_sentiment as mcp_tradingview_market_sentiment,
    )
    from tradingview_mcp.server import (
        market_snapshot as mcp_tradingview_market_snapshot,
    )
    from tradingview_mcp.server import yahoo_price as mcp_tradingview_yahoo_price
except ImportError:
    log.warning(
        "MCP TradingView tools not available, TradingView intelligence will be disabled"
    )
    mcp_tradingview_market_sentiment = None
    mcp_tradingview_combined_analysis = None
    mcp_tradingview_financial_news = None
    mcp_tradingview_market_snapshot = None
    mcp_tradingview_yahoo_price = None


@dataclass
class MarketSignal:
    sentiment: list[dict[str, Any]]
    confidence: float
    drivers: list[str]
    fii_flow: float
    dii_flow: float
    global_market: str
    news_sentiment: float


class BaseIntelligence:
    def get_market_sentiment(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_combined_analysis(
        self, symbol: str, exchange: str, timeframe: str
    ) -> dict[str, Any]:
        raise NotImplementedError


def _normalize_tv_timeframe(timeframe: str | None, default: str = "1D") -> str:
    """Normalize external timeframe values to TradingView-supported format."""
    if not timeframe:
        return default

    normalized = str(timeframe).strip().lower()
    mapping = {
        "5m": "5m",
        "15m": "15m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
        "day": "1D",
        "daily": "1D",
        "1w": "1W",
        "week": "1W",
        "weekly": "1W",
        "1m": "1M",
        "month": "1M",
        "monthly": "1M",
    }
    return mapping.get(normalized, default)


class TradingViewIntelligence(BaseIntelligence):
    def get_market_sentiment(self, symbol: str) -> dict[str, Any]:
        try:
            if mcp_tradingview_market_sentiment is None:
                raise ImportError("MCP TradingView not available")

            # Get market sentiment from TradingView
            result = mcp_tradingview_market_sentiment(
                symbol=symbol, category="all", limit=20
            )
            sentiment = result.get("sentiment", "NEUTRAL")
            confidence = result.get("confidence", 0.5)
            tv_news = result.get("news", [])
            if not isinstance(tv_news, list) or not tv_news:
                tv_news = []

            # Get financial news with sentiment
            merged_news = list(tv_news)
            news_sentiment_scores = []

            try:
                if mcp_tradingview_financial_news is not None:
                    news_result = mcp_tradingview_financial_news(
                        symbol=symbol, category="stocks", limit=5
                    )
                    if news_result and isinstance(news_result, (list, dict)):
                        news_items = (
                            news_result
                            if isinstance(news_result, list)
                            else news_result.get("news", [])
                        )
                        if news_items:
                            merged_news.extend(news_items)
                            # Extract sentiment scores from news items
                            for item in news_items:
                                if isinstance(item, dict) and "sentiment" in item:
                                    try:
                                        score = float(item["sentiment"])
                                        news_sentiment_scores.append(score)
                                    except (ValueError, TypeError):
                                        pass
            except Exception as e:
                log.warning(f"Financial news retrieval failed for {symbol}: {e}")

            # Calculate average news sentiment
            avg_news_sentiment = 0.0
            if news_sentiment_scores:
                avg_news_sentiment = sum(news_sentiment_scores) / len(
                    news_sentiment_scores
                )

            return {
                "source": "tradingview",
                "sentiment": sentiment,
                "confidence": confidence,
                "news": merged_news,
                "news_sentiment": round(avg_news_sentiment, 2),
            }
        except Exception as e:
            log.error(f"TradingView market sentiment failed for {symbol}: {e}")
            return {
                "source": "tradingview",
                "sentiment": "NEUTRAL",
                "confidence": 0.0,
                "news": [],
                "news_sentiment": 0.0,
            }

    def get_combined_analysis(
        self, symbol: str, exchange: str, timeframe: str
    ) -> dict[str, Any]:
        try:
            if mcp_tradingview_combined_analysis is None:
                raise ImportError("MCP TradingView not available")
            timeframe = _normalize_tv_timeframe(timeframe, default="1D")
            result = mcp_tradingview_combined_analysis(
                symbol=symbol, exchange=exchange, timeframe=timeframe
            )
            sentiment = result.get("sentiment", "NEUTRAL")
            confidence = result.get("confidence", 0.5)
            tv_news = result.get("news", [])
            if not isinstance(tv_news, list) or not tv_news:
                tv_news = []

            # Get financial news with sentiment
            merged_news = list(tv_news)
            news_sentiment_scores = []

            try:
                if mcp_tradingview_financial_news is not None:
                    news_result = mcp_tradingview_financial_news(
                        symbol=symbol, category="stocks", limit=5
                    )
                    if news_result and isinstance(news_result, (list, dict)):
                        news_items = (
                            news_result
                            if isinstance(news_result, list)
                            else news_result.get("news", [])
                        )
                        if news_items:
                            merged_news.extend(news_items)
                            # Extract sentiment scores from news items
                            for item in news_items:
                                if isinstance(item, dict) and "sentiment" in item:
                                    try:
                                        score = float(item["sentiment"])
                                        news_sentiment_scores.append(score)
                                    except (ValueError, TypeError):
                                        pass
            except Exception as e:
                log.warning(f"Financial news retrieval failed for {symbol}: {e}")

            # Calculate average news sentiment
            avg_news_sentiment = 0.0
            if news_sentiment_scores:
                avg_news_sentiment = sum(news_sentiment_scores) / len(
                    news_sentiment_scores
                )

            return {
                "source": "tradingview",
                "sentiment": sentiment,
                "confidence": confidence,
                "news": merged_news,
                "news_sentiment": round(avg_news_sentiment, 2),
            }
        except Exception as e:
            log.error(f"TradingView combined analysis failed for {symbol}: {e}")
            return {
                "source": "tradingview",
                "sentiment": "NEUTRAL",
                "confidence": 0.0,
                "news": [],
                "news_sentiment": 0.0,
            }


class FII_DII_Analyzer:
    """FII/DII flow analysis"""

    def __init__(self):
        self.fii_history = []
        self.dii_history = []

    def get_flow(self) -> dict:
        """Get current FII/DII flows"""
        fii_net = random.uniform(-500, 500)
        dii_net = random.uniform(-200, 200)

        self.fii_history.append(fii_net)
        self.dii_history.append(dii_net)

        fii_ma = sum(self.fii_history[-5:]) / min(5, len(self.fii_history))
        dii_ma = sum(self.dii_history[-5:]) / min(5, len(self.dii_history))

        return {
            "fii_net": round(fii_net, 2),
            "dii_net": round(dii_net, 2),
            "fii_ma5": round(fii_ma, 2),
            "dii_ma5": round(dii_ma, 2),
            "fii_sentiment": "BUY" if fii_ma > 0 else "SELL",
            "dii_sentiment": "BUY" if dii_ma > 0 else "SELL",
        }

    def get_sentiment(self, flows: dict) -> str:
        """Determine sentiment from flows"""
        fii = flows.get("fii_ma5", 0)
        dii = flows.get("dii_ma5", 0)

        if fii > 200 and dii > 100:
            return "STRONG_BULLISH"
        elif fii > 0 or dii > 0:
            return "BULLISH"
        elif fii < -200 or dii < -100:
            return "BEARISH"
        else:
            return "NEUTRAL"


class InsiderTradingAnalyzer:
    """Insider trading & bulk deals analysis"""

    def __init__(self):
        self.bulk_deals = []

    def get_bulk_deals(self, symbol: str = None) -> list[dict]:
        """Get recent bulk deals"""
        deals = [
            {
                "symbol": "RELIANCE",
                "type": "BUY",
                "quantity": 500000,
                "price": 2850,
                "date": "2024-01-15",
            },
            {
                "symbol": "TCS",
                "type": "SELL",
                "quantity": 100000,
                "price": 4100,
                "date": "2024-01-14",
            },
            {
                "symbol": "HDFCBANK",
                "type": "BUY",
                "quantity": 250000,
                "price": 1680,
                "date": "2024-01-13",
            },
        ]

        if symbol:
            return [d for d in deals if d["symbol"] == symbol]
        return deals

    def get_sentiment(self, deals: list[dict]) -> str:
        """Determine sentiment from bulk deals"""
        buy_volume = sum(d["quantity"] for d in deals if d["type"] == "BUY")
        sell_volume = sum(d["quantity"] for d in deals if d["type"] == "SELL")

        if buy_volume > sell_volume * 1.5:
            return "BULLISH"
        elif sell_volume > buy_volume * 1.5:
            return "BEARISH"
        return "NEUTRAL"


class EarningsAnalyzer:
    """Earnings calendar and surprise analysis"""

    def __init__(self):
        self.earnings_calendar = {
            "Q3_2024": [
                {
                    "symbol": "RELIANCE",
                    "date": "2024-01-24",
                    "expected": 2500,
                    "surprise": 5.2,
                },
                {
                    "symbol": "TCS",
                    "date": "2024-01-10",
                    "expected": 120,
                    "surprise": 2.1,
                },
                {
                    "symbol": "INFY",
                    "date": "2024-01-11",
                    "expected": 19.5,
                    "surprise": -1.2,
                },
            ]
        }

    def get_upcoming(self, days: int = 7) -> list[dict]:
        """Get upcoming earnings"""
        return self.earnings_calendar.get("Q3_2024", [])[:5]

    def get_sentiment(self, upcoming: list[dict]) -> str:
        """Determine sentiment from earnings"""
        positive_surprises = sum(1 for e in upcoming if e.get("surprise", 0) > 0)
        negative_surprises = sum(1 for e in upcoming if e.get("surprise", 0) < 0)

        if positive_surprises > negative_surprises * 2:
            return "BULLISH"
        elif negative_surprises > positive_surprises * 2:
            return "BEARISH"
        return "NEUTRAL"


class NewsAnalyzer:
    """News sentiment analysis"""

    def __init__(self):
        self.news_cache = []

    def fetch_news(self, symbol: str = None) -> list[dict]:
        """Fetch recent news"""
        news = [
            {
                "headline": "RBI keeps repo rate unchanged",
                "sentiment": 0.2,
                "source": "Economic Times",
            },
            {
                "headline": "IT sector sees strong Q3 results",
                "sentiment": 0.6,
                "source": "Business Standard",
            },
            {
                "headline": "Global markets mixed amid China concerns",
                "sentiment": -0.1,
                "source": "Reuters",
            },
        ]

        if symbol:
            return [n for n in news if symbol in n["headline"].upper()]
        return news

    def get_sentiment(self, news: list[dict]) -> float:
        """Get overall news sentiment score (-1 to 1)"""
        if not news:
            return 0

        return sum(n.get("sentiment", 0) for n in news) / len(news)


class GlobalMarkets:
    """Global market indicators"""

    def __init__(self):
        self.indices = {}
        self._price_cache = {}
        self._cache_timestamp = {}
        self._api_call_count = 0
        self._last_snapshot_time = None

    def get_indices(self) -> dict:
        """Get global indices with market_snapshot fallback to mock"""
        try:
            if mcp_tradingview_market_snapshot is None:
                log.debug("market_snapshot not available, using mock data")
                return self._get_mock_indices()

            # Call market_snapshot and map results
            snapshot = mcp_tradingview_market_snapshot()
            log.info("Retrieved market snapshot from TradingView")
            self._last_snapshot_time = datetime.now()

            # Map snapshot data to indices format
            indices = {}

            # Extract major indices from snapshot
            if isinstance(snapshot, dict):
                # Map common index names
                index_mapping = {
                    "SPX": "^GSPC",  # S&P 500
                    "NDX": "^IXIC",  # NASDAQ
                    "DJI": "^DJI",  # Dow Jones
                    "FTSE": "^FTSE",  # FTSE 100
                    "N225": "^N225",  # Nikkei 225
                    "HSI": "^HSI",  # Hang Seng
                }

                for key, tv_symbol in index_mapping.items():
                    if tv_symbol in snapshot:
                        data = snapshot[tv_symbol]
                        price = data.get("price", 0)
                        change = data.get("change", 0)

                        # Determine sentiment based on change
                        if change > 0.5:
                            sentiment = "BULLISH"
                        elif change < -0.5:
                            sentiment = "BEARISH"
                        else:
                            sentiment = "NEUTRAL"

                        indices[key] = {
                            "price": price,
                            "change": change,
                            "sentiment": sentiment,
                        }

                # Use mock data for missing indices
                if indices:
                    mock = self._get_mock_indices()
                    for key in mock:
                        if key not in indices:
                            indices[key] = mock[key]
                    return indices

            log.warning("Invalid snapshot format, using mock data")
            return self._get_mock_indices()

        except Exception as e:
            log.error(
                f"Error retrieving market snapshot: {e}, falling back to mock data"
            )
            return self._get_mock_indices()

    def _get_mock_indices(self) -> dict:
        """Mock global indices (fallback)"""
        return {
            "SPX": {"price": 4780, "change": 0.5, "sentiment": "BULLISH"},
            "NDX": {"price": 16800, "change": 0.8, "sentiment": "BULLISH"},
            "DJI": {"price": 37800, "change": 0.2, "sentiment": "NEUTRAL"},
            "FTSE": {"price": 7650, "change": -0.3, "sentiment": "BEARISH"},
            "N225": {"price": 36000, "change": -0.5, "sentiment": "BEARISH"},
            "HSI": {"price": 16000, "change": -0.8, "sentiment": "BEARISH"},
        }

    def get_price(self, symbol: str) -> dict[str, Any]:
        """Get current price and change for a symbol via Yahoo Finance"""
        try:
            # Check cache (prevent API overload with 5-minute TTL)
            cache_key = symbol.upper()
            now = datetime.now()

            if cache_key in self._price_cache:
                cache_time = self._cache_timestamp.get(cache_key)
                if (
                    cache_time and (now - cache_time).total_seconds() < 300
                ):  # 5 min cache
                    log.debug(f"Using cached price for {symbol}")
                    return self._price_cache[cache_key]

            if mcp_tradingview_yahoo_price is None:
                log.warning(f"yahoo_price not available, cannot fetch {symbol}")
                return {
                    "symbol": symbol,
                    "price": 0.0,
                    "change": 0.0,
                    "source": "unavailable",
                }

            # Rate limit: max 10 API calls per minute
            if self._api_call_count >= 10:
                log.warning(
                    f"API call rate limit approaching, using cached data for {symbol}"
                )
                return self._price_cache.get(
                    cache_key,
                    {
                        "symbol": symbol,
                        "price": 0.0,
                        "change": 0.0,
                        "source": "rate_limited",
                    },
                )

            self._api_call_count += 1

            # Call yahoo_price API
            log.debug(f"Fetching price for {symbol} from Yahoo Finance")
            result = mcp_tradingview_yahoo_price(symbol=symbol)

            if result and isinstance(result, dict):
                price_data = {
                    "symbol": symbol,
                    "price": result.get("price", 0.0),
                    "change": result.get("change", 0.0),
                    "change_percent": result.get("change_percent", 0.0),
                    "source": "yahoo_finance",
                }

                # Cache the result
                self._price_cache[cache_key] = price_data
                self._cache_timestamp[cache_key] = now

                log.info(f"Retrieved price for {symbol}: ${result.get('price', 0):.2f}")
                return price_data

            log.warning(f"Invalid response format for {symbol}")
            return {
                "symbol": symbol,
                "price": 0.0,
                "change": 0.0,
                "source": "invalid_response",
            }

        except Exception as e:
            log.error(f"Error fetching price for {symbol}: {e}")
            return {
                "symbol": symbol,
                "price": 0.0,
                "change": 0.0,
                "source": "error",
                "error": str(e),
            }

    def reset_api_rate_limit(self):
        """Reset API call counter (should be called periodically, e.g., every minute)"""
        self._api_call_count = 0
        log.debug("API rate limit counter reset")

    def get_sentiment(self, indices: dict) -> str:
        """Get global sentiment"""
        bullish = sum(1 for i in indices.values() if i.get("sentiment") == "BULLISH")
        bearish = sum(1 for i in indices.values() if i.get("sentiment") == "BEARISH")

        if bullish > bearish:
            return "BULLISH"
        elif bearish > bullish:
            return "BEARISH"
        return "NEUTRAL"


class IntelligenceEngine:
    def __init__(self, config: dict = None, nse_enrichment=None):
        self.config = config or {}
        self.nse_enrichment = nse_enrichment

        # Use real NSE data if available, otherwise fall back to mock analyzers
        if self.nse_enrichment and self.config.get("use_real_data", True):
            self.fii_dii = None
            self.insider = None
            self.earnings = None
            self.news = None
            self.global_markets = None
            log.info("Intelligence engine using real NSE enrichment data")
        else:
            self.fii_dii = FII_DII_Analyzer()
            self.insider = InsiderTradingAnalyzer()
            self.earnings = EarningsAnalyzer()
            self.news = NewsAnalyzer()
            self.global_markets = GlobalMarkets()
            log.info("Intelligence engine using mock data")

        self.tradingview = TradingViewIntelligence()
        # Zerodha based regime detector – primary source when real NSE data is not used
        try:
            from utils.zerodha_regime import ZerodhaRegimeDetector

            self.zerodha_regime = ZerodhaRegimeDetector()
            log.info("ZerodhaRegimeDetector initialized for market direction")
            # Debug the instance to ensure it was created successfully
            log.debug(f"ZerodhaRegimeDetector instance: {self.zerodha_regime}")
        except Exception as e:
            log.error(f"Failed to initialize ZerodhaRegimeDetector: {e}")
            self.zerodha_regime = None

    def _get_nse_market_sentiment(self) -> dict[str, Any]:
        """Get market sentiment from NSE data (FII/DII flows, market breadth) as fallback with caching"""
        # Simple TTL cache (5 minutes) to avoid repeated NSE calls
        cache_key = "nse_market_sentiment"
        now = time.time()
        ttl = 300  # seconds
        if cache_key in _sentiment_cache and (
            now - _sentiment_cache_timestamp.get(cache_key, 0) < ttl
        ):
            log.debug("Returning cached NSE market sentiment")
            return _sentiment_cache[cache_key]

        try:
            if not self.nse_enrichment or not self.config.get("use_real_data", True):
                return None

            # Get market data from NSE
            fii_dii = self.nse_enrichment.get_fii_dii_flow()
            breadth = self.nse_enrichment.get_market_breadth()

            fii_net = fii_dii.get("fii_net", 0) if fii_dii else 0
            dii_net = fii_dii.get("dii_net", 0) if fii_dii else 0
            advances = breadth.get("advances", 0) if breadth else 0
            declines = breadth.get("declines", 0) if breadth else 0

            # Determine sentiment from combined signals
            direction = "NEUTRAL"
            confidence = 0.5
            drivers = []

            # FII flow signal
            if fii_net > 100:
                direction = "BULLISH"
                drivers.append(f"FII Buying ₹{fii_net:.0f} Cr")
            elif fii_net < -100:
                direction = "BEARISH"
                drivers.append(f"FII Selling ₹{abs(fii_net):.0f} Cr")

            # Market breadth signal
            if advances > 0 and declines > 0:
                breadth_ratio = advances / max(declines, 1)
                if breadth_ratio > 1.2 and direction != "BEARISH":
                    direction = "BULLISH" if direction == "NEUTRAL" else direction
                    drivers.append(f"Breadth: {advances}/{declines}")
                elif breadth_ratio < 0.8 and direction != "BULLISH":
                    direction = "BEARISH" if direction == "NEUTRAL" else direction
                    drivers.append(f"Breadth: {advances}/{declines}")

            # Calculate confidence based on signals
            signal_count = len(drivers)
            if signal_count > 0:
                confidence = min(0.85, 0.5 + signal_count * 0.15)

            log.info(
                f"NSE market sentiment: {direction} | Confidence: {confidence:.2f} | Drivers: {drivers}"
            )

            result = {
                "direction": direction,
                "sentiment": direction,
                "confidence": confidence,
                "fii_net": fii_net,
                "dii_net": dii_net,
                "advances": advances,
                "declines": declines,
                "source": "nse_enrichment",
            }
            # Store in cache
            _sentiment_cache[cache_key] = result
            _sentiment_cache_timestamp[cache_key] = now
            return result
        except Exception as e:
            log.debug(f"NSE market sentiment failed: {e}")
            return None

    # NOTE: The live market direction logic was previously defined as a top‑level function,
    # which meant `IntelligenceEngine` instances did not expose it. It has been moved
    # into the class so callers like `qts.intelligence.get_live_market_direction()` work.
    def get_live_market_direction(self) -> dict[str, Any]:
        """Get live market direction, preferring Zerodha data when available.

        The method first attempts to obtain a regime from the Zerodha detector
        (symbol ``NIFTY``). If a non‑neutral direction is returned, it is used
        as the final result. Otherwise the original TradingView‑based logic is
        executed as a fallback.
        """
        log.debug("Entering get_live_market_direction")
        # -----------------------------------------------------------------
        # 1️⃣ Try Zerodha regime detector (primary source)
        # -----------------------------------------------------------------
        if getattr(self, "zerodha_regime", None):
            try:
                zr = self.zerodha_regime.detect_regime("NIFTY")
                if zr.get("direction") and zr["direction"] != "NEUTRAL":
                    log.info(
                        "Zerodha regime detector provided direction: %s",
                        zr["direction"],
                    )
                    # Align keys with existing return format
                    return {
                        "direction": zr.get("direction"),
                        "sentiment": zr.get("sentiment"),
                        "confidence": zr.get("confidence", 0.5),
                        "drivers": zr.get("drivers", []),
                        "source": zr.get("source", "zerodha"),
                    }
            except Exception as e:
                log.error(f"Zerodha regime detection failed: {e}")

        # -----------------------------------------------------------------
        # 2️⃣ Fallback to existing TradingView / mock logic (unchanged)
        # -----------------------------------------------------------------
        def _normalize_direction(value: Any) -> str:
            text = str(value or "").upper()
            if any(
                token in text
                for token in ["BULLISH", "BULL", "BUY", "UP", "POSITIVE", "LONG"]
            ):
                return "BULLISH"
            if any(
                token in text
                for token in ["BEARISH", "BEAR", "SELL", "DOWN", "NEGATIVE", "SHORT"]
            ):
                return "BEARISH"
            return "NEUTRAL"

        def _extract_sentiment(payload: Any) -> dict[str, Any]:
            direction = "NEUTRAL"
            score = 0.0
            label = None
            posts = 0
            details: dict[str, Any] = {}

            if isinstance(payload, dict):
                if "sentiment" in payload and isinstance(payload["sentiment"], dict):
                    nested = payload["sentiment"]
                    for key in ["sentiment_label", "label", "analysis", "signal"]:
                        if nested.get(key):
                            label = nested[key]
                            break
                    for key in [
                        "sentiment_score",
                        "score",
                        "confidence",
                        "strength",
                        "value",
                    ]:
                        if key in nested and nested[key] is not None:
                            try:
                                score = float(nested[key])
                            except (ValueError, TypeError):
                                pass
                            break
                for key in ["sentiment_label", "label", "analysis", "signal"]:
                    if label is None and key in payload and payload[key]:
                        label = payload[key]
                for key in [
                    "sentiment_score",
                    "score",
                    "confidence",
                    "strength",
                    "value",
                ]:
                    if score == 0.0 and key in payload and payload[key] is not None:
                        try:
                            score = float(payload[key])
                        except (ValueError, TypeError):
                            pass
                        break
                posts = (
                    payload.get("posts_analyzed")
                    or payload.get("total_posts")
                    or payload.get("bullish_count")
                    or payload.get("bearish_count")
                    or 0
                )
                if isinstance(
                    payload.get("bullish_count"), (int, float)
                ) and isinstance(payload.get("bearish_count"), (int, float)):
                    details["bullish_count"] = payload.get("bullish_count")
                    details["bearish_count"] = payload.get("bearish_count")

            if label is None and isinstance(payload, dict):
                label = (
                    payload.get("direction")
                    or payload.get("sentiment")
                    or payload.get("analysis")
                    or payload.get("signal")
                )

            direction = _normalize_direction(label)
            if direction == "NEUTRAL" and isinstance(payload, dict):
                bullish_count = payload.get("bullish_count") or payload.get("bullish")
                bearish_count = payload.get("bearish_count") or payload.get("bearish")
                if (
                    isinstance(bullish_count, (int, float))
                    and isinstance(bearish_count, (int, float))
                    and bullish_count != bearish_count
                ):
                    direction = (
                        "BULLISH" if bullish_count > bearish_count else "BEARISH"
                    )
                elif isinstance(payload.get("technical_analysis"), dict):
                    ta = payload["technical_analysis"]
                    ta_text = str(
                        ta.get("signal")
                        or ta.get("recommendation")
                        or ta.get("summary")
                        or ""
                    )
                    direction = _normalize_direction(ta_text)
                    if direction == "NEUTRAL" and "score" in ta:
                        try:
                            score = float(ta.get("score", score))
                        except (ValueError, TypeError):
                            pass
            confidence = min(0.95, max(0.4, 0.5 + abs(score)))
            if isinstance(posts, (int, float)) and posts > 0:
                confidence = max(confidence, min(0.9, 0.4 + posts / 100))
            details.update(
                {
                    "sentiment_label": (
                        str(label).upper() if label is not None else "NEUTRAL"
                    ),
                    "sentiment_score": score,
                    "posts_analyzed": posts,
                }
            )
            return {"direction": direction, "confidence": confidence, **details}

        def _best_response(response: dict[str, Any], source: str) -> dict[str, Any]:
            return {
                "direction": response.get("direction", "NEUTRAL"),
                "sentiment": response.get("direction", "NEUTRAL"),
                "confidence": float(response.get("confidence", 0.5) or 0.5),
                "sentiment_label": response.get("sentiment_label"),
                "sentiment_score": response.get("sentiment_score"),
                "posts_analyzed": response.get("posts_analyzed", 0),
                "source": source,
            }

        try:
            # 1. Broad market sentiment terms
            if mcp_tradingview_market_sentiment:
                for term in ["NIFTY", "India", "NSE", "Indian Market", "Sensex"]:
                    try:
                        analysis = mcp_tradingview_market_sentiment(
                            symbol=term, category="stocks", limit=30
                        )
                        if (
                            isinstance(analysis, dict)
                            and analysis.get("posts_analyzed", 0) > 0
                        ):
                            resp = _extract_sentiment(analysis)
                            log.info(
                                f"MCP Market sentiment for {term}: {resp['direction']} | Label: {resp.get('sentiment_label')} | Score: {resp.get('sentiment_score'):.3f} | Confidence: {resp['confidence']:.2f} | Posts: {resp.get('posts_analyzed')}"
                            )
                            if (
                                resp["direction"] != "NEUTRAL"
                                or resp["confidence"] > 0.6
                            ):
                                return _best_response(resp, f"mcp_sentiment:{term}")
                    except Exception as e:
                        log.debug(f"MCP market sentiment for {term} failed: {e}")
                        continue

            # 2. US market correlation
            if mcp_tradingview_combined_analysis:
                for sym in ["SPY", "QQQ", "IWM"]:
                    try:
                        us = mcp_tradingview_combined_analysis(
                            symbol=sym, exchange="NASDAQ", timeframe="1D"
                        )
                        if isinstance(us, dict):
                            resp = _extract_sentiment(us)
                            log.debug(
                                f"MCP US market analysis for {sym}: {resp['direction']} confidence={resp['confidence']:.2f}"
                            )
                            if (
                                resp["direction"] != "NEUTRAL"
                                and resp["confidence"] > 0.6
                            ):
                                return _best_response(resp, f"mcp_us_leading:{sym}")
                    except Exception as e:
                        log.debug(f"MCP US analysis for {sym} failed: {e}")
                        continue

            # 3. Direct NIFTY sentiment fallback
            if mcp_tradingview_market_sentiment:
                try:
                    analysis = mcp_tradingview_market_sentiment(
                        symbol="NIFTY", category="all", limit=20
                    )
                    if isinstance(analysis, dict):
                        resp = _extract_sentiment(analysis)
                        posts = resp.get("posts_analyzed", 0)
                        if posts > 0:
                            log.info(
                                f"MCP Market sentiment fallback: {resp['direction']} | Label: {resp.get('sentiment_label')} | Score: {resp.get('sentiment_score'):.3f} | Confidence: {resp['confidence']:.2f} | Posts: {posts}"
                            )
                            return _best_response(resp, "mcp_sentiment")
                        log.debug(
                            "MCP market sentiment fallback skipped (0 posts), trying snapshot/US markets"
                        )
                except Exception as e:
                    log.debug(f"MCP market sentiment fallback failed: {e}")

            # 4. Market snapshot fallback
            if mcp_tradingview_market_snapshot:
                try:
                    snapshot = mcp_tradingview_market_snapshot()
                    indices = {}
                    if isinstance(snapshot, dict):
                        indices = snapshot.get("indices", {})
                    elif isinstance(snapshot, list) and snapshot:
                        for item in snapshot:
                            if isinstance(item, dict) and "indices" in item:
                                indices = item.get("indices", {})
                                break
                    nifty = (
                        indices.get("NIFTY", {}) if isinstance(indices, dict) else {}
                    )
                    change_pct = nifty.get("change_percent", 0)
                    direction = (
                        "BULLISH"
                        if change_pct > 0.2
                        else "BEARISH" if change_pct < -0.2 else "NEUTRAL"
                    )
                    confidence = min(0.8, 0.5 + abs(change_pct) / 2)
                    log.info(
                        f"MCP Market snapshot: {direction} ({change_pct:.2f}%) | Confidence: {confidence:.2f}"
                    )
                    return {
                        "direction": direction,
                        "sentiment": direction,
                        "confidence": confidence,
                        "change_percent": change_pct,
                        "source": "mcp_market_snapshot",
                    }
                except Exception as e:
                    log.debug(f"MCP market snapshot failed: {e}")

            # 5. NSE enrichment fallback
            nse_sent = self._get_nse_market_sentiment()
            if nse_sent:
                log.info(
                    f"NSE market sentiment fallback: {nse_sent['direction']} | Confidence: {nse_sent['confidence']:.2f}"
                )
                return nse_sent

            return {
                "direction": "NEUTRAL",
                "sentiment": "NEUTRAL",
                "confidence": 0.5,
                "source": "fallback",
            }
        except Exception as e:
            log.warning(f"Live market direction fetch failed: {e}")
            return {
                "direction": "NEUTRAL",
                "sentiment": "NEUTRAL",
                "confidence": 0.5,
                "source": "error",
            }

    def get_market_summary(self) -> MarketSignal:
        """Get comprehensive market signal, using real NSE data when available"""
        use_real = self.nse_enrichment is not None and self.config.get(
            "use_real_data", True
        )

        if use_real:
            return self._get_real_market_summary()
        else:
            return self._get_mock_market_summary()

    def _get_real_market_summary(self) -> MarketSignal:
        """Get market signal from real NSE enrichment data"""
        # 1. FII/DII flow
        fii_dii_data = self.nse_enrichment.get_fii_dii_flow()
        fii_net = fii_dii_data.get("fii_net", 0)
        dii_net = fii_dii_data.get("dii_net", 0)

        # 2. Market breadth
        breadth = self.nse_enrichment.get_market_breadth()
        advances = breadth.get("advances", 0)
        declines = breadth.get("declines", 0)

        # 3. India VIX for volatility regime
        vix_df = self.nse_enrichment.get_india_vix("5D")
        vix_sentiment = "NEUTRAL"
        if vix_df is not None and not vix_df.empty:
            latest_vix = vix_df["CLOSE_INDEX_VAL"].iloc[-1]
            if latest_vix > 20:
                vix_sentiment = "FEAR"
            elif latest_vix < 12:
                vix_sentiment = "CALM"

        # 4. Determine overall sentiment
        sentiment = "NEUTRAL"
        confidence = 0.5
        sentiment_scores = []

        if fii_net > 100:
            sentiment_scores.append(("BULLISH", min(0.8, 0.4 + fii_net / 2500)))
        elif fii_net < -100:
            sentiment_scores.append(("BEARISH", min(0.8, 0.4 + abs(fii_net) / 2500)))

        if advances > declines * 1.1:
            sentiment_scores.append(("BULLISH", 0.55))
        elif declines > advances * 1.1:
            sentiment_scores.append(("BEARISH", 0.55))

        if vix_sentiment == "FEAR":
            sentiment_scores.append(("BEARISH", 0.6))
        elif vix_sentiment == "CALM":
            sentiment_scores.append(("BULLISH", 0.35))

        drivers = []
        if fii_net > 100:
            drivers.append(f"FII Buying ₹{fii_net:.0f} Cr")
        elif fii_net < -100:
            drivers.append(f"FII Selling ₹{abs(fii_net):.0f} Cr")
        if dii_net > 50:
            drivers.append(f"DII Support ₹{dii_net:.0f} Cr")
        if advances > declines * 1.5:
            drivers.append(f"Market Breadth +{advances - declines}")
        elif declines > advances * 1.5:
            drivers.append(f"Market Breadth -{declines - advances}")
        if vix_sentiment == "FEAR":
            drivers.append("High Volatility (VIX > 20)")
        elif vix_sentiment == "CALM":
            drivers.append("Low Volatility (VIX < 12)")

        tradingview_symbol = self.config.get("tradingview_symbol", "NIFTY")
        tradingview_data = self.tradingview.get_market_sentiment(tradingview_symbol)
        news_sentiment = tradingview_data.get("news_sentiment", 0.0)

        tv_sentiment = str(tradingview_data.get("sentiment", "NEUTRAL")).upper()
        if any(token in tv_sentiment for token in ["BULL", "BUY", "UP"]):
            sentiment_scores.append(("BULLISH", 0.8))
            drivers.append(
                f"TradingView market view: {tradingview_data.get('sentiment')}"
            )
        elif any(token in tv_sentiment for token in ["BEAR", "SELL", "DOWN"]):
            sentiment_scores.append(("BEARISH", 0.8))
            drivers.append(
                f"TradingView market view: {tradingview_data.get('sentiment')}"
            )
        elif abs(news_sentiment) > 0.1:
            sentiment_label = "BULLISH" if news_sentiment > 0.1 else "BEARISH"
            sentiment_scores.append((sentiment_label, min(0.6, abs(news_sentiment))))
            drivers.append(f"News sentiment: {news_sentiment:.2f}")
        else:
            log.debug(
                f"TradingView market view for {tradingview_symbol} is neutral; skipping driver entry"
            )

        score_map = {"BULLISH": 0.0, "BEARISH": 0.0}
        for label, score in sentiment_scores:
            score_map[label] += score

        if score_map["BULLISH"] > score_map["BEARISH"]:
            sentiment = "BULLISH"
            confidence = min(
                0.95, 0.45 + (score_map["BULLISH"] - score_map["BEARISH"]) / 2
            )
        elif score_map["BEARISH"] > score_map["BULLISH"]:
            sentiment = "BEARISH"
            confidence = min(
                0.95, 0.45 + (score_map["BEARISH"] - score_map["BULLISH"]) / 2
            )
        else:
            if sentiment_scores:
                confidence = min(
                    0.8, 0.45 + sum(score for _, score in sentiment_scores) / 4
                )
            sentiment = "NEUTRAL"

        return MarketSignal(
            sentiment=[
                {"source": "overall", "sentiment": sentiment, "confidence": confidence},
                tradingview_data,
            ],
            confidence=round(confidence, 2),
            drivers=drivers[:6],
            fii_flow=fii_net,
            dii_flow=dii_net,
            global_market=tradingview_data.get("sentiment", "NEUTRAL"),
            news_sentiment=news_sentiment,
        )

    def _get_mock_market_summary(self) -> MarketSignal:
        """Enhanced mock-based summary with more realistic sentiment detection"""
        flows = self.fii_dii.get_flow()
        deals = self.insider.get_bulk_deals()
        upcoming = self.earnings.get_upcoming()
        news_items = self.news.fetch_news()
        indices = self.global_markets.get_indices()

        sentiments = [
            self.fii_dii.get_sentiment(flows),
            self.insider.get_sentiment(deals),
            self.earnings.get_sentiment(upcoming),
            str(self.news.get_sentiment(news_items)),
            self.global_markets.get_sentiment(indices),
        ]

        bull_count = sum(1 for s in sentiments if isinstance(s, str) and "BULLISH" in s)
        bear_count = sum(1 for s in sentiments if isinstance(s, str) and "BEARISH" in s)

        # Calculate weighted confidence based on agreement
        total_signals = len(sentiments)
        agreement_ratio = (
            max(bull_count, bear_count) / total_signals if total_signals > 0 else 0
        )

        if bull_count > bear_count:
            sentiment = "BULLISH"
            confidence = min(0.9, 0.4 + agreement_ratio * 0.5)
        elif bear_count > bull_count:
            sentiment = "BEARISH"
            confidence = min(0.9, 0.4 + agreement_ratio * 0.5)
        else:
            # When equal, check for strong neutral signals or slight bias
            sentiment = "NEUTRAL"
            confidence = 0.5
            # If we have mixed signals but some strength, adjust confidence
            if bull_count + bear_count >= total_signals * 0.6:
                confidence = 0.55

        overall_sentiment = {
            "source": "overall",
            "sentiment": sentiment,
            "confidence": confidence,
        }
        tradingview_symbol = self.config.get("tradingview_symbol", "AAPL")
        tradingview_data = self.tradingview.get_market_sentiment(tradingview_symbol)

        news_sentiment = tradingview_data.get(
            "news_sentiment",
            float(self.news.get_sentiment(news_items)) if news_items else 0,
        )

        return MarketSignal(
            sentiment=[overall_sentiment, tradingview_data],
            confidence=round(confidence, 2),
            drivers=self._get_top_drivers(flows, indices),
            fii_flow=flows.get("fii_net", 0) if flows else 0,
            dii_flow=flows.get("dii_net", 0) if flows else 0,
            global_market=(
                self.global_markets.get_sentiment(indices) if indices else "NEUTRAL"
            ),
            news_sentiment=news_sentiment,
        )

    def _get_top_drivers(self, flows: dict, indices: dict) -> list[str]:
        """Get top market drivers"""
        drivers = []

        if flows.get("fii_ma5", 0) > 100:
            drivers.append("FII Buying")
        elif flows.get("fii_ma5", 0) < -100:
            drivers.append("FII Selling")

        if flows.get("dii_ma5", 0) > 50:
            drivers.append("DII Support")

        global_sent = self.global_markets.get_sentiment(indices)
        if global_sent == "BULLISH":
            drivers.append("Global Rally")
        elif global_sent == "BEARISH":
            drivers.append("Global Weakness")

        return drivers[:5]

    def get_sentiment(self) -> str:
        """Quick sentiment check"""
        sentiments = self.get_market_summary().sentiment
        return sentiments[0]["sentiment"] if sentiments else "NEUTRAL"

    def get_fii_dii_flow(self) -> dict:
        """Get FII/DII flows (real if available, else mock)"""
        if self.nse_enrichment and self.config.get("use_real_data", True):
            return self.nse_enrichment.get_fii_dii_flow()
        elif self.fii_dii:
            return self.fii_dii.get_flow()
        return {"fii_net": 0.0, "dii_net": 0.0}

    def get_global_indices(self) -> dict:
        """Get global market data (not implemented in real mode yet)"""
        if not self.nse_enrichment or not self.config.get("use_real_data", True):
            return self.global_markets.get_indices()
        return {}


# Singleton removed to prevent duplicate initialization
