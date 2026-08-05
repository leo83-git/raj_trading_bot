# ═══════════════════════════════════════════════════════════════
#  Institutional Data Provider — FII/DII Flow Analysis
# ═══════════════════════════════════════════════════════════════
from datetime import datetime

from quant_utils.logger import get_logger

log = get_logger("sources.institutional")


class InstitutionalDataProvider:
    """Provider for FII/DII institutional flow data"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.cache = {}
        self.cache_expiry = 3600  # 1 hour cache

    def get_fii_dii_flows(self, symbol: str, days: int = 30) -> dict:
        """Get FII/DII buying/selling data for a symbol"""
        cache_key = f"{symbol}_{days}"

        # Check cache
        if cache_key in self.cache:
            cached_data, timestamp = self.cache[cache_key]
            if (datetime.now() - timestamp).seconds < self.cache_expiry:
                return cached_data

        try:
            # In production, this would fetch from NSE API or external data provider
            # For now, return placeholder data based on symbol characteristics
            flows = self._calculate_placeholder_flows(symbol, days)

            # Cache the result
            self.cache[cache_key] = (flows, datetime.now())
            return flows

        except Exception as e:
            log.warning(f"Failed to get FII/DII flows for {symbol}: {e}")
            return self._get_default_flows()

    def _calculate_placeholder_flows(self, symbol: str, days: int) -> dict:
        """Calculate placeholder FII/DII flows based on symbol characteristics"""
        # This is a simplified model - in production would use real data

        # Large cap stocks typically have more institutional interest
        large_cap_symbols = [
            "RELIANCE",
            "HDFCBANK",
            "ICICIBANK",
            "INFY",
            "TCS",
            "HINDUNILVR",
        ]
        mid_cap_symbols = ["MARUTI", "SUNPHARMA", "BAJFINANCE", "ITC", "BHARTIARTL"]

        if symbol in large_cap_symbols:
            fii_interest = 0.7
            dii_interest = 0.6
        elif symbol in mid_cap_symbols:
            fii_interest = 0.5
            dii_interest = 0.4
        else:
            fii_interest = 0.3
            dii_interest = 0.3

        # Simulate some randomness but maintain consistency
        import random

        random.seed(hash(symbol) % 1000)  # Deterministic randomness per symbol

        fii_net = (random.random() - 0.5) * 1000 * fii_interest * days
        dii_net = (random.random() - 0.5) * 800 * dii_interest * days

        fii_dii_ratio = abs(fii_net) / (abs(dii_net) + 1) if abs(dii_net) > 0 else 2.0

        # Determine institutional sentiment
        if fii_net > 100 and dii_net > 50:
            sentiment = "strong_institutional_buying"
        elif fii_net < -100 and dii_net < -50:
            sentiment = "strong_institutional_selling"
        elif fii_net > 50 or dii_net > 25:
            sentiment = "moderate_institutional_buying"
        elif fii_net < -50 or dii_net < -25:
            sentiment = "moderate_institutional_selling"
        else:
            sentiment = "neutral"

        return {
            "fii_net": round(fii_net, 2),
            "dii_net": round(dii_net, 2),
            "fii_dii_ratio": round(fii_dii_ratio, 2),
            "institutional_interest": sentiment,
            "days_analyzed": days,
            "data_source": "placeholder_model",
        }

    def _get_default_flows(self) -> dict:
        """Return default/placeholder flows when data unavailable"""
        return {
            "fii_net": 0,
            "dii_net": 0,
            "fii_dii_ratio": 1.0,
            "institutional_interest": "unknown",
            "days_analyzed": 0,
            "data_source": "default",
        }

    def get_institutional_sentiment_score(self, symbol: str, days: int = 30) -> float:
        """Get institutional sentiment score (-1 to 1)"""
        flows = self.get_fii_dii_flows(symbol, days)

        # Convert sentiment to numerical score
        sentiment_map = {
            "strong_institutional_buying": 0.8,
            "moderate_institutional_buying": 0.4,
            "neutral": 0.0,
            "moderate_institutional_selling": -0.4,
            "strong_institutional_selling": -0.8,
            "unknown": 0.0,
        }

        base_score = sentiment_map.get(
            flows.get("institutional_interest", "neutral"), 0.0
        )

        # Adjust based on ratio
        ratio = flows.get("fii_dii_ratio", 1.0)
        if ratio > 1.5:
            base_score *= 1.2  # FII dominance increases significance
        elif ratio < 0.7:
            base_score *= 0.8  # DII dominance decreases significance

        return round(max(-1, min(1, base_score)), 2)

    def get_fii_net_flow(self, symbol: str, days: int = 30) -> float:
        """Get FII net flow for symbol"""
        flows = self.get_fii_dii_flows(symbol, days)
        return flows.get("fii_net", 0)

    def get_dii_net_flow(self, symbol: str, days: int = 30) -> float:
        """Get DII net flow for symbol"""
        flows = self.get_fii_dii_flows(symbol, days)
        return flows.get("dii_net", 0)


# Global instance for easy access
_institutional_provider = None


def get_institutional_provider(config: dict | None = None) -> InstitutionalDataProvider:
    """Get global institutional data provider instance"""
    global _institutional_provider
    if _institutional_provider is None:
        _institutional_provider = InstitutionalDataProvider(config)
    return _institutional_provider
