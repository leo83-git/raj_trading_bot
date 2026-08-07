# ═══════════════════════════════════════════════════════════════
#  Screener Engine — Stock screening with multiple criteria
# ═══════════════════════════════════════════════════════════════
from dataclasses import dataclass, field
from quant_utils.logger import get_logger
from screener.engine.p3_components import (
    INDEX_SYMBOLS,
    CandidateRanker,
    FnoFeatureScorer,
)

log = get_logger("screener.engine")

BASIC_FNO_SYMBOLS = INDEX_SYMBOLS.union(
    {
        "RELIANCE",
        "HDFCBANK",
        "ICICIBANK",
        "TCS",
        "INFY",
        "SBIN",
        "KOTAKBANK",
        "AXISBANK",
        "LT",
        "HINDUNILVR",
        "MARUTI",
        "SUNPHARMA",
        "BAJFINANCE",
        "ITC",
        "BHARTIARTL",
    }
)


@dataclass
class StockData:
    symbol: str
    price: float
    volume: int
    change_pct: float
    rsi: float | None = None
    trend: str = "SIDEWAYS"
    sector: str = "GENERAL"
    market_cap: float = 0
    features: dict = field(default_factory=dict)


@dataclass
class ScreenerResult:
    symbol: str
    score: float
    rank: int
    reasons: list[str]
    metadata: dict = field(default_factory=dict)


class SectorStrength:
    """Sector strength analysis"""

    SECTORS = {
        "IT": ["INFY", "TCS", "WIPRO", "HCLTECH"],
        "BANK": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK"],
        "AUTO": ["MARUTI", "TATAMOTORS", "M&M"],
        "PHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA"],
        "CONSUME": ["HINDUNILVR", "ITC", "NESTLE"],
        "ENERGY": ["RELIANCE", "ONGC", "NTPC"],
        "METAL": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    }

    def __init__(self):
        self.sector_performance = {}

    def analyze(self, stocks: list[StockData]) -> dict[str, float]:
        """Calculate sector strength scores"""
        sector_scores = {}

        for sector, symbols in self.SECTORS.items():
            sector_stocks = [s for s in stocks if s.symbol in symbols]

            if not sector_stocks:
                continue

            avg_change = sum(s.change_pct for s in sector_stocks) / len(sector_stocks)
            avg_volume = sum(s.volume for s in sector_stocks) / len(sector_stocks)

            score = avg_change * 0.7 + (avg_volume / 1000000) * 0.3
            sector_scores[sector] = round(score, 2)

        self.sector_performance = sector_scores
        return sector_scores

    def get_top_sectors(self, count: int = 3) -> list[str]:
        """Get top performing sectors"""
        sorted_sectors = sorted(
            self.sector_performance.items(), key=lambda x: x[1], reverse=True
        )
        return [s[0] for s in sorted_sectors[:count]]

    def get_sector_for_symbol(self, symbol: str) -> str | None:
        """Get sector for a symbol"""
        for sector, symbols in self.SECTORS.items():
            if symbol in symbols:
                return sector
        return None


class RelativeStrength:
    """Relative strength vs Nifty"""

    def __init__(self):
        self.nifty_change = 0

    def set_index_change(self, change: float):
        """Set Nifty change for comparison"""
        self.nifty_change = change

    def calculate(self, stock: StockData) -> float:
        """Calculate relative strength score"""
        stock_change = stock.change_pct

        if self.nifty_change == 0:
            rs = 1.0
        else:
            rs = (
                (stock_change - self.nifty_change) / abs(self.nifty_change)
                if abs(self.nifty_change) > 0.1
                else 0
            )

        return round(rs, 2)

    def rank_stocks(self, stocks: list[StockData]) -> list[StockData]:
        """Rank stocks by relative strength"""
        return sorted(stocks, key=lambda s: self.calculate(s), reverse=True)


class BreakoutDetector:
    """Breakout detection (price + volume)"""

    def __init__(self):
        self.lookback = 20

    def detect(self, stock: StockData) -> dict | None:
        """Detect if stock is in breakout"""
        features = stock.features
        if not features:
            return None

        current_price = stock.price
        vwap = features.get("vwap")
        ema_20 = features.get("ema_21")  # Using ema_21 as closest to ema_20
        relative_volume = features.get("relative_volume", 0)
        avg_volume = features.get("avg_volume", 0)

        # Check breakout conditions
        vwap_breakout = vwap and current_price > vwap
        ema_breakout = ema_20 and current_price > ema_20
        volume_confirmation = relative_volume and relative_volume > 1.0

        # Determine breakout type and confidence
        breakout_type = None
        confidence = 0.0

        if vwap_breakout and ema_breakout and volume_confirmation:
            breakout_type = "STRONG_BREAKOUT"
            # High confidence for triple confirmation
            vwap_strength = ((current_price - vwap) / vwap) * 100 if vwap else 0
            ema_strength = ((current_price - ema_20) / ema_20) * 100 if ema_20 else 0
            volume_strength = (
                min(relative_volume - 1.0, 2.0) / 2.0
            )  # Cap at 2.0x volume
            confidence = min(
                (vwap_strength * 0.3 + ema_strength * 0.3 + volume_strength * 0.4), 1.0
            )

        elif vwap_breakout and volume_confirmation:
            breakout_type = "VWAP_BREAKOUT"
            # Medium confidence for VWAP + volume
            vwap_strength = ((current_price - vwap) / vwap) * 100 if vwap else 0
            volume_strength = min(relative_volume - 1.0, 2.0) / 2.0
            confidence = min((vwap_strength * 0.6 + volume_strength * 0.4), 1.0)

        elif ema_breakout and volume_confirmation:
            breakout_type = "EMA_BREAKOUT"
            # Medium confidence for EMA + volume
            ema_strength = ((current_price - ema_20) / ema_20) * 100 if ema_20 else 0
            volume_strength = min(relative_volume - 1.0, 2.0) / 2.0
            confidence = min((ema_strength * 0.6 + volume_strength * 0.4), 1.0)

        if breakout_type:
            return {
                "type": breakout_type,
                "confidence": round(confidence, 2),
                "vwap_breakout": vwap_breakout,
                "ema_breakout": ema_breakout,
                "volume_confirmation": volume_confirmation,
                "price_vs_vwap": ((current_price - vwap) / vwap * 100) if vwap else 0,
                "price_vs_ema": (
                    ((current_price - ema_20) / ema_20 * 100) if ema_20 else 0
                ),
                "relative_volume": relative_volume,
            }

        return None


class OIAnalyzer:
    """Options OI buildup analysis"""

    def __init__(self):
        self.oi_data = {}

    def analyze(self, symbol: str) -> dict:
        """Analyze OI buildup for symbol"""
        return {
            "pcr": 1.2 + (hash(symbol) % 10) / 10,
            "call_oi": 1000000 + (hash(symbol) % 500000),
            "put_oi": 1200000 + (hash(symbol) % 500000),
            "change_call_oi": (hash(symbol) % 20) - 10,
            "change_put_oi": (hash(symbol) % 20) - 10,
            "max_pain": 23000 + (hash(symbol) % 500),
            "sentiment": "BULLISH" if (hash(symbol) % 3) > 1 else "BEARISH",
        }

    def get_oi_sentiment(self, oi_data: dict) -> str:
        """Get sentiment from OI data"""
        pcr = oi_data.get("pcr", 1)

        if pcr > 1.3:
            return "BEARISH"  # High put OI = bearish
        elif pcr < 0.8:
            return "BULLISH"  # Low put OI = bullish
        return "NEUTRAL"


class ScreenerEngine:
    """Unified screener combining all criteria"""

    def __init__(self, config: dict = None, data_provider=None):
        self.config = config or {}

        self.sector_strength = SectorStrength()
        self.relative_strength = RelativeStrength()
        self.breakout = BreakoutDetector()
        self.oi_analyzer = OIAnalyzer()
        self.p3_scorer = FnoFeatureScorer(
            self.config.get("root_config", self.config)
        )
        self.p3_ranker = CandidateRanker()

        # Data provider for lazy loading (optional)
        self.data_provider = data_provider

        self.min_volume = self.config.get("min_volume", 0)
        self.min_price = self.config.get("min_price", 0)
        self.max_price = self.config.get(
            "max_price", 5000
        )  # Add max_price for intraday filtering
        self.max_stocks = self.config.get("max_stocks", None)

        # Intraday-specific momentum filters
        self.min_rel_volume = self.config.get("min_rel_volume", 0.8)
        self.rsi_min = self.config.get("rsi_min", 40)
        self.rsi_max = self.config.get("rsi_max", 75)
        self.vwap_required = self.config.get("vwap_required", False)
        self.ema_alignment = self.config.get("ema_alignment", False)
        self.max_overbought_rsi = self.config.get("max_overbought_rsi", 80)
        self.volume_penalty_threshold = self.config.get("volume_penalty_threshold", 0.7)

        log.info("Screener engine initialized")

    def _lazy_load_stock_data(self, stock: StockData) -> StockData:
        """Lazily load price and technical data for a stock if not already present"""
        if not self.data_provider:
            return stock

        symbol = stock.symbol
        if not symbol:
            return stock

        # Check if we already have valid price data
        if stock.price and stock.price > 0:
            return stock

        try:
            # Fetch quote data
            quote = self.data_provider.get_quote(symbol)
            if quote:
                # Some data sources/brokers use 'ltp' instead of 'last_price'
                last_price = quote.get("last_price") or quote.get("ltp") or 0
                if last_price and float(last_price) > 0:
                    stock.price = float(last_price)
                # Update other fields if available
                volume = quote.get("volume")
                if volume:
                    stock.volume = int(volume) or 0
                else:
                    stock.volume = stock.volume or 0

            # Ensure we have valid numeric values
            stock.price = stock.price or 0
            stock.volume = stock.volume or 0

        except Exception as e:
            log.debug(f"Failed to lazy load data for {symbol}: {e}")
            # Ensure we have safe defaults
            stock.price = stock.price or 0
            stock.volume = stock.volume or 0

        return stock

    def _passes_basic_fno_filters(self, symbol: str) -> bool:
        """Basic F&O eligibility filters that don't require price data"""
        return bool(symbol) and symbol.upper() in BASIC_FNO_SYMBOLS

    def _liquidity_filter(self, stocks: list[StockData]) -> list[StockData]:
        """Apply liquidity screening to stock list"""
        filtered_stocks = []
        for stock in stocks:
            if stock.symbol in ["NIFTY", "BANKNIFTY"]:
                filtered_stocks.append(stock)
                continue
            volume = stock.volume or 0
            # For basic screening without technical data, use current volume as proxy
            avg_volume = stock.features.get("avg_volume") or volume or 0
            close = stock.price or 0
            relative_volume = (
                (volume / avg_volume) if avg_volume > 0 else 1.0
            )  # Default to 1.0 if no avg_volume

            if volume < self.min_volume:
                continue
            if close < self.min_price:
                continue
            # Relax relative volume check if avg_volume is not available (basic mode)
            if avg_volume > 0 and relative_volume < self.min_rel_volume:
                continue
            filtered_stocks.append(stock)
        return filtered_stocks

    def _trend_filter(self, stocks: list[StockData]) -> list[StockData]:
        """Apply trend filters to stock list"""
        filtered_stocks = []
        for stock in stocks:
            if stock.symbol in ["NIFTY", "BANKNIFTY"]:
                filtered_stocks.append(stock)
                continue
            trend = stock.trend or "SIDEWAYS"
            ema_9 = stock.features.get("ema_9")
            ema_20 = stock.features.get("ema_20") or stock.features.get("ema_21")
            sma_50 = stock.features.get("sma_50")
            vwap = stock.features.get("vwap")

            is_uptrend = trend == "UPTREND"
            has_bullish_ema = bool(ema_9 and ema_20 and ema_9 > ema_20)
            has_price_above_sma50 = bool(sma_50 and stock.price > sma_50)
            has_vwap_support = bool(vwap and stock.price > vwap)

            if self.vwap_required and not has_vwap_support:
                continue
            if self.ema_alignment and not has_bullish_ema:
                continue
            # In sideways markets or when technical data is missing, be more permissive
            # Allow stocks that have at least one bullish indicator OR are in uptrend/sideways
            if trend == "DOWNTREND":
                continue  # Still filter out clear downtrends
            # If we have technical data, use it; otherwise allow based on trend
            if (
                ema_9 is not None
                or ema_20 is not None
                or sma_50 is not None
                or vwap is not None
            ):
                # We have some technical data, apply normal filters
                if not (
                    is_uptrend
                    or has_bullish_ema
                    or has_price_above_sma50
                    or trend == "SIDEWAYS"
                ):
                    continue
            # If no technical data available, allow based on trend only
            elif not (is_uptrend or trend == "SIDEWAYS"):
                continue

            filtered_stocks.append(stock)
        return filtered_stocks

    def _screen_momentum(self, stocks: list[StockData]) -> list[dict]:
        """Screen stocks by momentum using multi-stage filters"""
        filtered_stocks = self._liquidity_filter(stocks)
        filtered_stocks = self._trend_filter(filtered_stocks)
        results = []
        for stock in filtered_stocks:
            score = self._momentum_score(stock)
            reasons = self._get_reasons(stock)
            result = ScreenerResult(
                symbol=stock.symbol,
                score=round(score, 2),
                rank=0,
                reasons=reasons,
                metadata={
                    "price": stock.price,
                    "change_pct": stock.change_pct,
                    "volume": stock.volume,
                    "rsi": stock.rsi,
                    "trend": stock.trend,
                    "sector": stock.sector,
                    "category": stock.features.get("category") or "intraday",
                    "features": stock.features,
                },
            )
            results.append(result)
        results.sort(key=lambda x: x.score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1
        return [self._result_to_dict(r) for r in results]

    def screen(self, stocks_data: list[dict], method: str = "ai_ranking") -> list[dict]:
        """Screen stocks based on method"""
        stocks = [self._dict_to_stock(d) for d in stocks_data]

        self.sector_strength.analyze(stocks)

        nifty = next((s for s in stocks if s.symbol == "NIFTY"), None)
        if nifty:
            self.relative_strength.set_index_change(nifty.change_pct)

        if method == "momentum":
            results = self._screen_momentum(stocks)
            log.info(f"Screened {len(results)} stocks using {method}")
            return results

        elif method == "fno":
            results = self._screen_fno(stocks)
            log.info(f"Screened {len(results)} stocks using {method}")
            return results

        elif method == "intraday_comprehensive":
            # Use the comprehensive intraday screening from the enhanced engine
            # Convert back to dict format for compatibility
            dict_stocks = stocks_data
            try:
                from analytics.performance import get_performance_analytics
                from features.indicators import calculate_all_indicators
                from features.microstructure import \
                    analyze_market_microstructure
                from features.patterns import get_pattern_recognizer
                from sources.institutional_data import \
                    get_institutional_provider

                # Initialize components
                institutional_provider = get_institutional_provider()
                performance_analytics = get_performance_analytics()
                pattern_recognizer = get_pattern_recognizer()

                # Apply comprehensive filtering
                filtered = self._intraday_liquidity_filter(dict_stocks)
                filtered = self._intraday_microstructure_filter(filtered)
                filtered = self._intraday_gap_filter(filtered)
                filtered = self._intraday_technical_filter(filtered)
                filtered = self._intraday_institutional_filter(
                    filtered, institutional_provider
                )
                filtered = self._intraday_performance_filter(
                    filtered, performance_analytics
                )
                filtered = self._intraday_pattern_filter(filtered, pattern_recognizer)

                # Calculate scores
                for stock in filtered:
                    score = self._calculate_intraday_score(stock)
                    stock["intraday_score"] = score
                    stock["screener_score"] = score

                results = sorted(
                    filtered, key=lambda x: x.get("intraday_score", 0), reverse=True
                )
                log.info(f"Screened {len(results)} stocks using intraday_comprehensive")
                return results

            except ImportError as e:
                log.warning(
                    f"Advanced components not available, falling back to momentum: {e}"
                )
                results = self._screen_momentum(stocks)
                log.info(f"Screened {len(results)} stocks using momentum (fallback)")
                return results

        results = []

        for stock in stocks:
            if stock.symbol in ["NIFTY", "BANKNIFTY"]:
                pass
            else:
                # Handle None values safely
                price = stock.price if stock.price is not None else 0
                volume = stock.volume if stock.volume is not None else 0

                if price < self.min_price:
                    continue
                if volume < self.min_volume:
                    continue

            score = self._calculate_score(stock, method)
            reasons = self._get_reasons(stock)

            result = ScreenerResult(
                symbol=stock.symbol,
                score=round(score, 2),
                rank=0,
                reasons=reasons,
                metadata={
                    "price": stock.price,
                    "change_pct": stock.change_pct,
                    "volume": stock.volume,
                    "rsi": stock.rsi,
                    "trend": stock.trend,
                    "sector": stock.sector,
                    "category": stock.features.get("category") or "intraday",
                    "features": stock.features,
                },
            )

            results.append(result)

        results.sort(key=lambda x: x.score, reverse=True)

        for i, r in enumerate(results):
            r.rank = i + 1

        log.info(f"Screened {len(results)} stocks using {method}")

        return [self._result_to_dict(r) for r in results]

    def _dict_to_stock(self, data: dict) -> StockData:
        """Convert dict to StockData"""
        # Ensure price and volume are never None
        price = data.get("close", data.get("price", 0))
        price = price if price is not None and isinstance(price, (int, float)) else 0

        volume = data.get("volume", 0)
        volume = (
            volume if volume is not None and isinstance(volume, (int, float)) else 0
        )

        change_pct = data.get("change_pct", 0)
        change_pct = (
            change_pct
            if change_pct is not None and isinstance(change_pct, (int, float))
            else 0
        )

        return StockData(
            symbol=data.get("symbol", ""),
            price=price,
            volume=volume,
            change_pct=change_pct,
            rsi=data.get("rsi"),
            trend=data.get("trend", "SIDEWAYS"),
            features=data,
        )

    def _result_to_dict(self, result: ScreenerResult) -> dict:
        """Convert result to dict"""
        return {
            "symbol": result.symbol,
            "score": result.score,
            "screener_score": result.score,
            "rank": result.rank,
            "reasons": result.reasons,
            **result.metadata,
        }

    def _stock_to_dict(self, stock: StockData) -> dict:
        """Convert a StockData object into a dict result"""
        return {
            "symbol": stock.symbol,
            "close": stock.price,  # Add close for main.py practical filtering
            "price": stock.price,
            "volume": stock.volume,
            "change_pct": stock.change_pct,
            "rsi": stock.rsi,
            "trend": stock.trend,
            "sector": stock.sector,
            "features": stock.features,
        }

    def _screen_fno(self, stocks: list[StockData]) -> list[dict]:
        """Screen stocks using comprehensive F&O pipeline and enhanced AI scoring"""
        log.info(f"Running F&O pipeline on {len(stocks)} stocks")

        # Lazy load data only for stocks that pass initial filters
        # First pass: basic symbol-level filtering (no price data needed)
        initial_candidates = []
        for stock in stocks:
            try:
                category = (
                    stock.features.get("category")
                    if isinstance(stock.features, dict)
                    else None
                )
                if category == "fno" or self._passes_basic_fno_filters(stock.symbol):
                    # Lazy load price and technical data
                    loaded_stock = self._lazy_load_stock_data(stock)
                    # Accept stocks even if price loading failed (will have default values)
                    initial_candidates.append(loaded_stock)
            except Exception as e:
                log.debug(f"Error processing F&O candidate {stock.symbol}: {e}")
                continue

        log.info(
            f"F&O candidates after basic filtering: {len(initial_candidates)} stocks"
        )

        # Apply enhanced AI scoring for F&O
        results = []
        for stock in initial_candidates:
            scored = self.p3_scorer.score(
                {**self._stock_to_dict(stock), "category": "fno"}
            )
            scored["features"] = stock.features
            scored["price"] = stock.price
            scored["volume"] = stock.volume
            scored["change_pct"] = stock.change_pct
            scored["screener_score"] = scored["score"]
            results.append(scored)

        return self.p3_ranker.rank(results)

    def _calculate_enhanced_fno_score(self, stock: StockData) -> float:
        """Calculate enhanced F&O score based on multiple factors"""
        return round(
            self.p3_scorer.score({**self._stock_to_dict(stock), "category": "fno"})[
                "score"
            ],
            2,
        )

    def _calculate_score(self, stock: StockData, method: str) -> float:
        """Calculate stock score based on method"""
        if method == "ai_ranking":
            return self._ai_score(stock)
        elif method == "momentum":
            return self._momentum_score(stock)
        elif method == "value":
            return self._value_score(stock)
        else:
            return self._ai_score(stock)

    def _ai_score(self, stock: StockData) -> float:
        """AI-based scoring"""
        score = 0

        if stock.rsi:
            if stock.rsi < 30:
                score += 20
            elif stock.rsi > 70:
                score -= 10
            elif 40 < stock.rsi < 60:
                score += 10

        score += stock.change_pct * 2

        if stock.trend == "UPTREND":
            score += 15
        elif stock.trend == "DOWNTREND":
            score -= 15

        breakout = self.breakout.detect(stock)
        if breakout:
            if breakout["type"] == "STRONG_BREAKOUT":
                score += 25
            else:
                score += 10

        rs = self.relative_strength.calculate(stock)
        score += rs * 10

        sector = self.sector_strength.get_sector_for_symbol(stock.symbol)
        if sector:
            sector_score = self.sector_strength.sector_performance.get(sector, 0)
            score += sector_score * 0.5

        oi_data = self.oi_analyzer.analyze(stock.symbol)
        oi_sent = self.oi_analyzer.get_oi_sentiment(oi_data)
        if oi_sent == "BULLISH":
            score += 10
        elif oi_sent == "BEARISH":
            score -= 10

        return score

    def _momentum_score(self, stock: StockData) -> float:
        """Momentum-based scoring"""
        score = stock.change_pct * 3

        if stock.rsi is not None:
            if stock.rsi < self.rsi_min:
                score -= (self.rsi_min - stock.rsi) * 0.5
            elif stock.rsi > self.rsi_max:
                score -= (stock.rsi - self.rsi_max) * 0.75
            else:
                score += (stock.rsi - 50) / 2

        # Handle missing technical data gracefully
        relative_volume = stock.features.get("relative_volume")
        if relative_volume is not None:
            if relative_volume < self.volume_penalty_threshold:
                score -= (self.volume_penalty_threshold - relative_volume) * 20
            else:
                score += max(0.0, (relative_volume - self.min_rel_volume)) * 10
        else:
            # If no relative volume data, give slight boost for having volume
            if stock.volume and stock.volume > 0:
                score += 2

        breakout = self.breakout.detect(stock)
        if breakout and breakout.get("type") == "STRONG_BREAKOUT":
            score += 15
        elif breakout:
            score += 7

        return score

    def _value_score(self, stock: StockData) -> float:
        """Value-based scoring"""
        score = 50

        return score

    def _get_reasons(self, stock: StockData) -> list[str]:
        """Get reasons for screening"""
        reasons = []

        if stock.rsi is not None:
            if stock.rsi < 30:
                reasons.append("Oversold (RSI)")
            elif stock.rsi > 70:
                reasons.append("Overbought (RSI)")
            if stock.rsi < self.rsi_min:
                reasons.append(f"RSI below intraday threshold ({self.rsi_min})")
            elif stock.rsi > self.max_overbought_rsi:
                reasons.append(f"RSI above overbought cap ({self.max_overbought_rsi})")
            elif self.rsi_min <= stock.rsi <= self.rsi_max:
                reasons.append("RSI in bullish intraday zone")

        if stock.trend == "UPTREND":
            reasons.append("Uptrend")
        elif stock.trend == "DOWNTREND":
            reasons.append("Downtrend")

        vwap = stock.features.get("vwap")
        ema_9 = stock.features.get("ema_9")
        ema_20 = stock.features.get("ema_20") or stock.features.get("ema_21")
        relative_volume = stock.features.get("relative_volume")

        if vwap and stock.price > vwap:
            reasons.append("Price above VWAP")
        if ema_9 and ema_20 and ema_9 > ema_20:
            reasons.append("Bullish EMA alignment")
        if relative_volume is not None and relative_volume >= self.min_rel_volume:
            reasons.append("Volume confirms momentum")
        elif (
            relative_volume is not None
            and relative_volume > 0
            and relative_volume < self.volume_penalty_threshold
        ):
            reasons.append("Volume below intraday threshold")
        elif stock.volume and stock.volume > 0:
            reasons.append(
                "Has trading volume"
            )  # Basic volume check when no relative volume

        breakout = self.breakout.detect(stock)
        if breakout:
            reasons.append(breakout["type"])

        rs = self.relative_strength.calculate(stock)
        if rs > 0.5:
            reasons.append("Outperforming")
        elif rs < -0.5:
            reasons.append("Underperforming")

        sector = self.sector_strength.get_sector_for_symbol(stock.symbol)
        if sector and sector in self.sector_strength.get_top_sectors():
            reasons.append(f"Strong sector: {sector}")

        return reasons[:5]

    def get_sector_summary(self) -> dict:
        """Get sector analysis summary"""
        return {
            "sectors": self.sector_strength.sector_performance,
            "top_sectors": self.sector_strength.get_top_sectors(),
        }

    # Comprehensive Intraday Screening Methods
    def _intraday_liquidity_filter(self, stocks: list[dict]) -> list[dict]:
        """Enhanced liquidity filter for intraday"""
        filtered_stocks = []
        min_volume = self.config.get("intraday_criteria", {}).get("min_volume", 500000)

        for stock in stocks:
            volume = stock.get("volume") or 0
            avg_volume = stock.get("avg_volume") or 0
            close = stock.get("close") or 0

            if (
                volume >= min_volume
                and avg_volume >= min_volume * 0.5
                and close >= self.min_price
                and close <= self.max_price
            ):
                filtered_stocks.append(stock)

        return filtered_stocks

    def _intraday_microstructure_filter(self, stocks: list[dict]) -> list[dict]:
        """Market microstructure filter"""
        try:
            from features.microstructure import analyze_market_microstructure

            filtered_stocks = []

            for stock in stocks:
                symbol = stock.get("symbol", "")
                # For now, skip orderbook analysis as broker integration needed
                stock["microstructure_analysis"] = {
                    "overall_valid": True,
                    "reason": "orderbook_not_available",
                }
                filtered_stocks.append(stock)

            return filtered_stocks
        except ImportError:
            return stocks

    def _intraday_gap_filter(self, stocks: list[dict]) -> list[dict]:
        """Gap analysis filter"""
        filtered_stocks = []
        max_gap_pct = self.config.get("intraday_criteria", {}).get("max_gap_pct", 3.0)

        for stock in stocks:
            gap_analysis = stock.get("gap_analysis")
            if gap_analysis:
                gap_pct = gap_analysis.get("gap_pct", 0)
                if abs(gap_pct) <= max_gap_pct:
                    filtered_stocks.append(stock)
            else:
                filtered_stocks.append(stock)  # Include if no gap data

        return filtered_stocks

    def _intraday_technical_filter(self, stocks: list[dict]) -> list[dict]:
        """Advanced technical filter"""
        filtered_stocks = []
        config = self.config.get("intraday_criteria", {})

        for stock in stocks:
            # RSI check
            rsi = stock.get("rsi")
            rsi_range = config.get("rsi_range", [40, 70])
            if rsi is not None and not (rsi_range[0] <= rsi <= rsi_range[1]):
                continue

            # ADX check
            adx = stock.get("adx")
            adx_min = config.get("adx_min", 20)
            if adx is not None and adx < adx_min:
                continue

            # ATR check
            atr = stock.get("atr")
            atr_min = config.get("atr_min", 5)
            atr_max = config.get("atr_max", 200)
            if atr is not None and not (atr_min <= atr <= atr_max):
                continue

            filtered_stocks.append(stock)

        return filtered_stocks

    def _intraday_institutional_filter(
        self, stocks: list[dict], institutional_provider
    ) -> list[dict]:
        """Institutional sentiment filter"""
        if not institutional_provider:
            return stocks

        filtered_stocks = []
        for stock in stocks:
            symbol = stock.get("symbol", "")
            sentiment_score = institutional_provider.get_institutional_sentiment_score(
                symbol
            )
            stock["institutional_sentiment"] = sentiment_score

            if sentiment_score > -0.6:  # Allow moderately negative sentiment
                filtered_stocks.append(stock)

        return filtered_stocks

    def _intraday_performance_filter(
        self, stocks: list[dict], performance_analytics
    ) -> list[dict]:
        """Performance-based filter"""
        if not performance_analytics:
            return stocks

        filtered_stocks = []
        config = self.config.get("intraday_criteria", {})

        min_sharpe = config.get("min_sharpe_ratio", 1.0)
        min_win_rate = config.get("min_win_rate", 50)
        min_profit_factor = config.get("min_profit_factor", 1.2)

        for stock in stocks:
            # Simplified performance estimation based on technical strength
            technical_score = self._calculate_intraday_technical_score(stock)
            estimated_sharpe = technical_score * 2
            estimated_win_rate = 50 + technical_score * 20
            estimated_profit_factor = 1.0 + technical_score * 0.5

            if (
                estimated_sharpe >= min_sharpe
                and estimated_win_rate >= min_win_rate
                and estimated_profit_factor >= min_profit_factor
            ):
                stock["estimated_performance"] = {
                    "sharpe_ratio": estimated_sharpe,
                    "win_rate": estimated_win_rate,
                    "profit_factor": estimated_profit_factor,
                }
                filtered_stocks.append(stock)

        return filtered_stocks

    def _intraday_pattern_filter(
        self, stocks: list[dict], pattern_recognizer
    ) -> list[dict]:
        """Pattern recognition filter"""
        if not pattern_recognizer:
            return stocks

        filtered_stocks = []
        for stock in stocks:
            candles = stock.get("candles", [])
            if not candles:
                filtered_stocks.append(stock)
                continue

            if len(candles) >= 3:
                pattern_analysis = pattern_recognizer.analyze_candle_sequence(
                    candles[-3:]
                )
                stock["pattern_analysis"] = pattern_analysis

                direction = pattern_analysis.get("direction", "neutral")
                if direction in ["bullish", "neutral"]:
                    filtered_stocks.append(stock)
            else:
                filtered_stocks.append(stock)

        return filtered_stocks

    def _calculate_intraday_score(self, stock: dict) -> float:
        """Calculate comprehensive intraday suitability score"""
        score = 0

        # Technical factors (40% weight)
        technical_score = self._calculate_intraday_technical_score(stock) * 0.4
        score += technical_score

        # Liquidity factors (20% weight)
        volume = stock.get("volume") or 0
        relative_volume = stock.get("relative_volume")
        relative_volume = 1 if relative_volume is None else relative_volume

        if volume >= 1000000:
            liquidity_score = 0.15
        elif volume >= 500000:
            liquidity_score = 0.1
        else:
            liquidity_score = 0.05

        liquidity_score += min(relative_volume / 3, 0.05)
        score += liquidity_score

        # Institutional factors (15% weight)
        institutional_sentiment = stock.get("institutional_sentiment", 0)
        institutional_score = (institutional_sentiment + 1) * 0.075
        score += institutional_score

        # Pattern factors (15% weight)
        pattern_analysis = stock.get("pattern_analysis", {})
        pattern_strength = pattern_analysis.get("strength", "weak")

        pattern_score = 0
        if pattern_strength == "strong":
            pattern_score += 0.1
        elif pattern_strength == "moderate":
            pattern_score += 0.05

        if pattern_analysis.get("direction") == "bullish":
            pattern_score += 0.05

        score += pattern_score

        # Microstructure factors (10% weight)
        ms_analysis = stock.get("microstructure_analysis", {})
        if ms_analysis.get("overall_valid", True):
            score += 0.05
        else:
            score += 0.01

        return round(score, 4)

    def _calculate_intraday_technical_score(self, stock: dict) -> float:
        """Calculate technical strength score"""
        score = 0
        factors = 0

        # RSI (40-60 is ideal)
        rsi = stock.get("rsi")
        rsi = 50 if rsi is None else rsi
        if rsi:
            rsi_score = 1 - abs(rsi - 50) / 50
            score += rsi_score
            factors += 1

        # Trend strength
        adx = stock.get("adx")
        adx = 25 if adx is None else adx
        if adx:
            adx_score = min(adx / 40, 1)
            score += adx_score
            factors += 1

        # Volume confirmation
        relative_volume = stock.get("relative_volume")
        relative_volume = 1 if relative_volume is None else relative_volume
        if relative_volume:
            vol_score = min(relative_volume / 2, 1)
            score += vol_score
            factors += 1

        return score / factors if factors > 0 else 0.5


# Singleton removed to prevent duplicate initialization
