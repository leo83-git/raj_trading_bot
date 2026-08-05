# ══════════════════════════════════════════════════════════════════════════════
#  FinGPT-Inspired Sentiment Analysis for Trading
#  Adapted from: https://github.com/AI4Finance-Foundation/FinGPT
#  License: MIT
# ══════════════════════════════════════════════════════════════════════════════
"""
Financial sentiment analysis using LLMs or lightweight models.
Can be used for news-driven trading signals.

Supports:
- Integration with OpenAI GPT models
- Local sentiment analysis with FinGPT models
- Simple keyword-based fallback
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

try:
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from quant_utils.logger import get_logger

log = get_logger("intelligence.sentiment")


class SentimentLabel(Enum):
    """Sentiment classification labels"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


@dataclass
class SentimentResult:
    """Sentiment analysis result"""

    label: SentimentLabel
    confidence: float
    score: float  # -1 to 1 scale
    reasoning: str
    timestamp: datetime = None

    def to_dict(self) -> dict:
        return {
            "label": self.label.value,
            "confidence": self.confidence,
            "score": self.score,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class FinancialSentimentAnalyzer:
    """
    Financial sentiment analyzer using LLMs.

    Supports:
    - OpenAI GPT API
    - Local keyword-based analysis (fallback)

    Example:
        >>> analyzer = FinancialSentimentAnalyzer()
        >>> result = analyzer.analyze("AAPL results beat expectations")
        >>> print(result.label, result.score)
    """

    def __init__(
        self,
        model: str = "openai",
        api_key: str = None,
        provider: str = "openai",
    ):
        """
        Initialize sentiment analyzer.

        Args:
            model: Model to use ('gpt-4', 'gpt-3.5-turbo', 'local', 'keyword')
            api_key: API key for OpenAI
            provider: Cloud provider for inference
        """
        self.model = model
        self.provider = provider

        # Setup OpenAI client
        self.client = None
        if OPENAI_AVAILABLE and model.startswith("gpt"):
            api_key = api_key or os.environ.get("OPENAI_API_KEY")
            if api_key:
                self.client = OpenAI(api_key=api_key)
            else:
                log.warning("OpenAI API key not found, using keyword fallback")
                self.model = "keyword"

        # Keyword-based patterns for fallback
        self._setup_patterns()

    def _setup_patterns(self):
        """Setup keyword patterns for fallback sentiment"""
        self.positive_patterns = [
            r"\b(beat|gain|profit|surge|jump|rally|soar|rise|grow|expand|upgrade|bullish|outperform|exceed)s?\b",
            r"\b(high|record|peak|excellent|strong|beat|strike)\b",
            r"\+\d+\.?\d*%?",
            r"#\d+",
        ]

        self.negative_patterns = [
            r"\b(fall|drop|loss|decline|shrink|downgrade|bearish|underperform|miss|weak|fail|cut|layoff)s?\b",
            r"\b(low|weak|poor|miss|delay|cancel|cut|reduce)\b",
            r"-\d+\.?\d*%?",
        ]

        self.neutral_patterns = [
            r"\b(maintain|hold|flat|unchanged|steady|meet|inline)\b",
        ]

    def analyze(self, text: str) -> SentimentResult:
        """
        Analyze sentiment of financial text.

        Args:
            text: Financial news or announcement

        Returns:
            SentimentResult with classification
        """
        if not text or not text.strip():
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                confidence=0.0,
                score=0.0,
                reasoning="Empty text",
                timestamp=datetime.now(),
            )

        # Use keyword fallback if no API
        if self.model == "keyword" or not self.client:
            return self._keyword_analysis(text)

        # Use LLM analysis
        if self.model.startswith("gpt"):
            return self._gpt_analysis(text)

        return self._keyword_analysis(text)

    def _keyword_analysis(self, text: str) -> SentimentResult:
        """Keyword-based sentiment analysis"""
        text_lower = text.lower()

        positive_matches = sum(
            len(re.findall(p, text_lower)) for p in self.positive_patterns
        )
        negative_matches = sum(
            len(re.findall(p, text_lower)) for p in self.negative_patterns
        )

        score = (positive_matches - negative_matches) / (
            positive_matches + negative_matches + 1
        )

        if score > 0.1:
            label = SentimentLabel.POSITIVE
            confidence = min(0.9, abs(score))
            reasoning = f"Found {positive_matches} positive indicators"
        elif score < -0.1:
            label = SentimentLabel.NEGATIVE
            confidence = min(0.9, abs(score))
            reasoning = f"Found {negative_matches} negative indicators"
        else:
            label = SentimentLabel.NEUTRAL
            confidence = 0.5
            reasoning = "Mixed or neutral signals"

        return SentimentResult(
            label=label,
            confidence=confidence,
            score=score,
            reasoning=reasoning,
            timestamp=datetime.now(),
        )

    def _gpt_analysis(self, text: str) -> SentimentResult:
        """OpenAI GPT-based sentiment analysis"""
        prompt = f"""Analyze the sentiment of this financial news. 
Return ONLY a JSON object with these fields:
- label: "positive", "negative", or "neutral"  
- score: decimal between -1 (very negative) and 1 (very positive)
- confidence: decimal between 0 and 1
- reasoning: brief 1-sentence explanation

News: {text[:500]}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )

            content = response.choices[0].message.content

            # Parse JSON from response
            content = content.strip()
            content = content.removeprefix("```json")
            content = content.removeprefix("```")
            content = content.removesuffix("```")

            result = json.loads(content)

            return SentimentResult(
                label=SentimentLabel(result.get("label", "neutral")),
                confidence=result.get("confidence", 0.5),
                score=result.get("score", 0.0),
                reasoning=result.get("reasoning", ""),
                timestamp=datetime.now(),
            )
        except Exception as e:
            log.warning(f"GPT analysis failed: {e}, using keyword fallback")
            return self._keyword_analysis(text)

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Analyze multiple texts"""
        return [self.analyze(text) for text in texts]

    def get_market_sentiment(
        self,
        news_items: list[dict],
        symbol: str = None,
    ) -> tuple[SentimentResult, float]:
        """
        Get aggregate market sentiment from news items.

        Args:
            news_items: List of news dictionaries with 'headline', 'summary', 'datetime'
            symbol: Optional stock symbol

        Returns:
            (aggregate_sentiment, news_impact_score)
        """
        if not news_items:
            return (
                SentimentResult(
                    label=SentimentLabel.NEUTRAL,
                    confidence=0.0,
                    score=0.0,
                    reasoning="No news available",
                    timestamp=datetime.now(),
                ),
                0.0,
            )

        results = []
        for item in news_items:
            text = item.get("headline", "") or item.get("summary", "")
            result = self.analyze(text)
            results.append(result)

        # Aggregate scores (weighted by recency)
        weights = []
        scores = []

        for i, result in enumerate(results):
            age_hours = 1  # Default weight
            if result.timestamp:
                age_hours = max(
                    1, (datetime.now() - result.timestamp).total_seconds() / 3600
                )

            weight = 1.0 / (age_hours + 1)
            weights.append(weight)
            scores.append(result.score * weight)

        total_weight = sum(weights)
        if total_weight > 0:
            avg_score = sum(scores) / total_weight
        else:
            avg_score = 0

        # Classify aggregate
        if avg_score > 0.2:
            label = SentimentLabel.POSITIVE
            reasoning = "Bullish market sentiment"
        elif avg_score < -0.2:
            label = SentimentLabel.NEGATIVE
            reasoning = "Bearish market sentiment"
        else:
            label = SentimentLabel.NEUTRAL
            reasoning = "Mixed market sentiment"

        confidence = min(0.9, abs(avg_score) + 0.3)

        aggregate = SentimentResult(
            label=label,
            confidence=confidence,
            score=avg_score,
            reasoning=reasoning,
            timestamp=datetime.now(),
        )

        # Calculate news impact (number of recent articles)
        impact = len([r for r in results if r.confidence > 0.5])

        return aggregate, impact


class SentimentSignalGenerator:
    """
    Generate trading signals based on news sentiment.

    Combines technical signals with sentiment for enhanced decision making.
    """

    def __init__(
        self,
        sentiment_analyzer: FinancialSentimentAnalyzer = None,
        sentiment_weight: float = 0.3,
    ):
        self.sentiment_analyzer = sentiment_analyzer or FinancialSentimentAnalyzer()
        self.sentiment_weight = sentiment_weight

    def generate_signal(
        self,
        symbol: str,
        news: list[dict],
        technical_score: float,
        regime: str = "SIDEWAYS",
    ) -> dict:
        """
        Generate sentiment-enhanced trading signal.

        Args:
            symbol: Stock symbol
            news: List of recent news items
            technical_score: Technical indicator score (-1 to 1)
            market_regime: Current market regime

        Returns:
            Signal dictionary with sentiment adjustment
        """
        # Get sentiment
        sentiment, news_count = self.sentiment_analyzer.get_market_sentiment(
            news, symbol
        )

        # Adjust score based on regime
        regime_multiplier = 1.0
        if regime == "HIGH_VOL":
            regime_multiplier = 0.5  # Reduce sentiment weight in volatile markets
        elif regime == "TRENDING_UP":
            regime_multiplier = 1.2  # Increase in uptrends
        elif regime == "TRENDING_DOWN":
            regime_multiplier = 0.8  # Decrease in downtrends

        # Combine technical and sentiment
        adjusted_score = (
            technical_score * (1 - self.sentiment_weight)
            + sentiment.score * self.sentiment_weight * regime_multiplier
        )

        # Generate signal
        if adjusted_score > 0.3:
            action = "BUY"
        elif adjusted_score < -0.3:
            action = "SELL"
        else:
            action = "HOLD"

        return {
            "action": action,
            "score": adjusted_score,
            "technical_score": technical_score,
            "sentiment_score": sentiment.score,
            "sentiment_label": sentiment.label.value,
            "confidence": sentiment.confidence,
            "reasoning": f"Technical: {technical_score:.2f}, Sentiment: {sentiment.label.value} ({sentiment.reasoning})",
            "news_count": news_count,
            "regime": regime,
        }


class NewsFetcher:
    """
    Fetch financial news from various sources.

    Sources:
    - Yahoo Finance
    - Finnhub
    - Custom APIs
    """

    def __init__(
        self,
        api_key: str = None,
        source: str = "yahoo",
    ):
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        self.source = source

        if not self.api_key and source == "finnhub":
            log.warning("Finnhub API key not found, using fallback")

    def fetch_news(
        self,
        symbol: str,
        hours_lookback: int = 24,
    ) -> list[dict]:
        """
        Fetch recent news for a symbol.

        Args:
            symbol: Stock symbol
            hours_lookback: Hours of history to fetch

        Returns:
            List of news items
        """
        if self.source == "yahoo":
            return self._fetch_yahoo(symbol, hours_lookback)
        elif self.source == "finnhub":
            return self._fetch_finnhub(symbol, hours_lookback)
        else:
            return self._fetch_yahoo(symbol, hours_lookback)

    def _fetch_yahoo(self, symbol: str, hours: int) -> list[dict]:
        """Fetch from Yahoo Finance"""
        import yfinance as yf

        news = []
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.news
            if info:
                for item in info[:10]:
                    news.append(
                        {
                            "title": item.get("title", ""),
                            "summary": item.get("summary", ""),
                            "publisher": item.get("publisher", ""),
                            "link": item.get("link", ""),
                            "datetime": (
                                datetime.fromtimestamp(item.get("pubTime", 0))
                                if item.get("pubTime")
                                else datetime.now()
                            ),
                        }
                    )
        except Exception as e:
            log.warning(f"Yahoo fetch error: {e}")

        return news

    def _fetch_finnhub(self, symbol: str, hours: int) -> list[dict]:
        """Fetch from Finnhub"""
        if not self.api_key:
            return []

        import finnhub

        client = finnhub.Client(self.api_key)

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        news = []
        try:
            data = client.company_news(
                symbol,
                _from=start_time.strftime("%Y-%m-%d"),
                to=end_time.strftime("%Y-%m-%d"),
            )

            for item in data:
                news.append(
                    {
                        "title": item.get("headline", ""),
                        "summary": item.get("summary", ""),
                        "source": item.get("source", ""),
                        "url": item.get("url", ""),
                        "datetime": datetime.fromtimestamp(item.get("datetime", 0)),
                    }
                )
        except Exception as e:
            log.warning(f"Finnhub fetch error: {e}")

        return news


def create_sentiment_pipeline(
    api_key: str = None,
    use_gpt: bool = False,
    sentiment_weight: float = 0.3,
) -> tuple[SentimentSignalGenerator, NewsFetcher]:
    """
    Create complete sentiment trading pipeline.

    Returns:
        (sentiment_generator, news_fetcher)
    """
    model = "gpt-4" if use_gpt else "keyword"
    analyzer = FinancialSentimentAnalyzer(model=model, api_key=api_key)
    generator = SentimentSignalGenerator(analyzer, sentiment_weight)
    fetcher = NewsFetcher(api_key)

    return generator, fetcher
