# ═══════════════════════════════════════════════════════════════
#  Intelligence Layer — FII/DII, News, Macro
# ═══════════════════════════════════════════════════════════════
import json
from typing import Dict, List, Optional

import requests

from quant_utils.logger import get_logger

log = get_logger("intelligence")


class IntelligenceEngine:
    """Market intelligence gathering"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

        self.fii_cache = []
        self.news_cache = []
        self.macro_cache = {}
        self._last_fii_fetch = None
        self._fii_cache_timeout = 300  # 5 minutes cache

    def get_fii_dii_flows(self) -> dict:
        """Get FII/DII cash market flows with timeout protection and caching"""
        import time

        # Return cached data if available and not expired
        if self._last_fii_fetch and self.fii_cache:
            if time.time() - self._last_fii_fetch < self._fii_cache_timeout:
                log.debug("Returning cached FII/DII data")
                return self.fii_cache[-1]

        try:
            import threading

            result = [None]

            def fetch_fii_data():
                try:
                    url = "https://www.nseindia.com/api/fiialtSecStockArchive"
                    headers = {
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "application/json",
                    }
                    response = requests.get(
                        url, headers=headers, timeout=5
                    )  # Reduced timeout
                    result[0] = response
                except Exception as e:
                    log.debug(f"FII data fetch failed: {e}")
                    result[0] = None

            thread = threading.Thread(target=fetch_fii_data)
            thread.start()
            thread.join(timeout=8)  # 8 second total timeout

            if thread.is_alive():
                log.debug("FII/DII data fetch timed out")
                # Return cached data if available, even if expired
                if self.fii_cache:
                    log.debug("Returning expired cached FII/DII data due to timeout")
                    return self.fii_cache[-1]
                return None

            response = result[0]
            if response and response.status_code == 200:
                data = response.json()
                fii_buy = data.get("FIIBuyValue", 0)
                fii_sell = data.get("FIISellValue", 0)
                dii_buy = data.get("DIIBuyValue", 0)
                dii_sell = data.get("DIISellValue", 0)

                result_data = {
                    "fii_net": fii_buy - fii_sell,
                    "dii_net": dii_buy - dii_sell,
                    "fii_buy": fii_buy,
                    "fii_sell": fii_sell,
                    "dii_buy": dii_buy,
                    "dii_sell": dii_sell,
                }
                self.fii_cache.append(result_data)
                self._last_fii_fetch = time.time()
                return result_data
        except Exception as e:
            log.debug(f"FII data fetch failed: {e}")

        return {
            "fii_net": 0,
            "dii_net": 0,
            "fii_buy": 0,
            "fii_sell": 0,
            "dii_buy": 0,
            "dii_sell": 0,
        }

    def get_global_markets(self) -> dict:
        """Get global market indices"""
        indices = {}

        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC", timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                indices["SP500"] = data["chart"]["result"][0]["meta"][
                    "regularMarketPrice"
                ]
        except Exception as e:
            log.warning(f"SP500 fetch failed: {e}")

        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EIXIC", timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                indices["NASDAQ"] = data["chart"]["result"][0]["meta"][
                    "regularMarketPrice"
                ]
        except Exception as e:
            log.warning(f"NASDAQ fetch failed: {e}")

        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/SGXNIFTY", timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                indices["SGXNIFTY"] = data["chart"]["result"][0]["meta"][
                    "regularMarketPrice"
                ]
        except Exception as e:
            log.warning(f"SGXNIFTY fetch failed: {e}")

        self.macro_cache = indices
        return indices

    def get_currency(self) -> dict:
        """Get USD/INR and other currency rates"""
        currencies = {}

        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X", timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                currencies["USDINR"] = data["chart"]["result"][0]["meta"][
                    "regularMarketPrice"
                ]
        except Exception as e:
            log.warning(f"USDINR fetch failed: {e}")

        return currencies

    def get_news_sentiment(self, symbol: str) -> dict:
        """Get news sentiment for symbol (placeholder for AI)"""
        return {"sentiment": "NEUTRAL", "score": 0.0, "headlines": []}

    def get_earnings_calendar(self) -> list[dict]:
        """Get upcoming earnings"""
        return []

    def analyze_market_sentiment(self) -> str:
        """Analyze overall market sentiment"""
        fii_data = self.get_fii_dii_flows()
        global_data = self.get_global_markets()

        score = 0

        if fii_data.get("fii_net", 0) > 0:
            score += 1
        elif fii_data.get("fii_net", 0) < 0:
            score -= 1

        if fii_data.get("dii_net", 0) > 0:
            score += 1

        sp_change = global_data.get("SP500", 0)
        if sp_change > 4500:
            score += 1
        elif sp_change < 4400:
            score -= 1

        if score >= 2:
            return "BULLISH"
        elif score <= -2:
            return "BEARISH"
        return "NEUTRAL"

    def get_market_summary(self) -> dict:
        """Get comprehensive market summary for regime detection"""
        try:
            # Try to get FII/DII data with timeout protection
            fii_dii = self.get_fii_dii_flows()
            if fii_dii:
                fii_net = fii_dii.get("fii_net", 0)
                dii_net = fii_dii.get("dii_net", 0)

                # Determine sentiment based on flows
                if fii_net > 1000 and dii_net > 500:  # Strong buying
                    sentiment = "BULLISH"
                    confidence = 0.7
                elif fii_net < -1000 and dii_net < -500:  # Strong selling
                    sentiment = "BEARISH"
                    confidence = 0.7
                elif fii_net > 500 or dii_net > 200:  # Moderate buying
                    sentiment = "BULLISH"
                    confidence = 0.5
                elif fii_net < -500 or dii_net < -200:  # Moderate selling
                    sentiment = "BEARISH"
                    confidence = 0.5
                else:
                    sentiment = "NEUTRAL"
                    confidence = 0.3

                return {
                    "sentiment": [{"sentiment": sentiment, "confidence": confidence}],
                    "drivers": [f"FII: ₹{fii_net:,.0f}", f"DII: ₹{dii_net:,.0f}"],
                    "fii_dii_data": fii_dii,
                }
        except Exception as e:
            log.debug(f"Market summary generation failed: {e}")

        # Fallback empty response
        return {
            "sentiment": [{"sentiment": "NEUTRAL", "confidence": 0.0}],
            "drivers": [],
            "fii_dii_data": {"fii_net": 0, "dii_net": 0},
        }

    def get_intelligence_summary(self) -> dict:
        """Get complete intelligence summary"""
        fii = self.get_fii_dii_flows()
        global_idx = self.get_global_markets()
        currencies = self.get_currency()
        sentiment = self.analyze_market_sentiment()

        return {
            "fii_dii": fii,
            "global_indices": global_idx,
            "currencies": currencies,
            "sentiment": sentiment,
        }


# Singleton instances - only create when explicitly requested
# Remove these to prevent duplicate initialization
# intelligence_engine = IntelligenceEngine()


def get_market_intelligence() -> dict:
    """Get market intelligence summary"""
    return intelligence_engine.get_intelligence_summary()
