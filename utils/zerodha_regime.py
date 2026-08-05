"""Zerodha based market regime detector.

Provides a lightweight, cache‑aware detector that uses recent candle data from
Zerodha Kite Connect to infer market direction. The detector is deliberately
simple – it fetches a configurable number of recent candles for a given
symbol, computes the percentage price change between the first and last close,
and maps that change to a direction:

* ``price_change_pct > 1``   → ``BULLISH``
* ``price_change_pct < -1``  → ``BEARISH``
* otherwise                 → ``NEUTRAL``

Confidence is derived from the magnitude of the price change (capped at 0.9).
Results are cached for ``cache_ttl`` seconds to avoid excessive API calls.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.zerodha_broker import ZerodhaBroker

try:
    from intelligence.regime.vocabulary import normalize_regime
except ImportError:
    # Fallback if regime module unavailable
    def normalize_regime(regime: str) -> str:
        return (
            regime.strip().upper()
            if regime.strip().upper() in {"BULLISH", "BEARISH", "NEUTRAL"}
            else "NEUTRAL"
        )


log = logging.getLogger(__name__)


class ZerodhaRegimeDetector:
    """Detect market regime using Zerodha candle data.

    Parameters
    ----------
    broker: ZerodhaBroker, optional
        Instance of :class:`ZerodhaBroker`. If omitted a new instance is created.
    cache_ttl: int, default 300
        Time‑to‑live for cached results (seconds).
    candle_interval: str, default "FIVE_MINUTE"
        Interval constant accepted by ``ZerodhaBroker.get_candles``.
    candle_count: int, default 5
        Number of recent candles to analyse.
    """

    def __init__(
        self,
        broker: ZerodhaBroker | None = None,
        cache_ttl: int = 300,
        candle_interval: str = "FIVE_MINUTE",
        candle_count: int = 5,
    ) -> None:
        self.broker = broker or ZerodhaBroker()
        self.cache_ttl = cache_ttl
        self.candle_interval = candle_interval
        self.candle_count = candle_count
        # Cache: symbol → (result_dict, timestamp)
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _is_cache_valid(self, ts: float) -> bool:
        return self.cache_ttl > 0 and (time.time() - ts) < self.cache_ttl

    def _map_symbol(self, symbol: str) -> str:
        """Map generic index symbols to Zerodha tradingsymbols.

        Uses ``ZerodhaBroker.INDEX_SYMBOL_MAP`` when available; otherwise returns
        the upper‑cased input.
        """
        upper = symbol.upper()
        return getattr(self.broker, "INDEX_SYMBOL_MAP", {}).get(upper, upper)

    def _fetch_candles(self, symbol: str) -> list[dict[str, Any]]:
        """Fetch the most recent candles for *symbol*.

        Returns a list of candle dictionaries as provided by
        ``ZerodhaBroker.get_candles``. If the broker call fails, an empty list is
        returned and the error is logged.
        """
        mapped = self._map_symbol(symbol)
        try:
            candles = self.broker.get_candles(
                exchange="NSE",
                symbol=mapped,
                interval=self.candle_interval,
                count=self.candle_count,
            )
            if not isinstance(candles, list):
                log.debug("Zerodha get_candles returned non‑list for %s", symbol)
                return []
            return candles
        except Exception as exc:  # pragma: no cover – defensive
            log.error("Failed to fetch candles for %s: %s", symbol, exc)
            return []

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def detect_regime(self, symbol: str) -> dict[str, Any]:
        """Detect market regime for *symbol*.

        Returns a dictionary with keys ``direction``, ``sentiment``, ``confidence``,
        ``drivers`` and ``source``.
        """
        # Cache lookup
        cached = self._cache.get(symbol)
        if cached:
            result, ts = cached
            if self._is_cache_valid(ts):
                return result

        candles = self._fetch_candles(symbol)
        if len(candles) < 2:
            result = {
                "direction": "NEUTRAL",
                "sentiment": "NEUTRAL",
                "confidence": 0.3,
                "drivers": ["insufficient candle data"],
                "source": "zerodha",
            }
            self._cache[symbol] = (result, time.time())
            log.info(
                "ZerodhaRegimeDetector insufficient candles for %s, returning NEUTRAL",
                symbol,
            )
            return result

        first = candles[0]
        last = candles[-1]
        open_price = first.get("open", first.get("close", 0))
        close_price = last.get("close", last.get("open", 0))
        price_change_pct = 0.0
        if open_price:
            price_change_pct = ((close_price - open_price) / open_price) * 100

        # Log the raw price change percentage for diagnostics
        log.debug(
            "ZerodhaRegimeDetector price_change_pct=%.4f%% for symbol=%s",
            price_change_pct,
            symbol,
        )

        # Use a lower threshold (0.5%) to classify direction, making the detector
        # more responsive in live environments where price moves may be modest.
        if price_change_pct > 0.5:
            direction = "BULLISH"
        elif price_change_pct < -0.5:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"
            log.debug(
                "ZerodhaRegimeDetector flat movement for %s, price_change=%.2f%%",
                symbol,
                price_change_pct,
            )

        # Confidence scales with magnitude of change, capped at 0.9.
        confidence = min(0.9, 0.5 + min(abs(price_change_pct) / 10, 0.4))
        if confidence < 0.5:
            log.debug(
                "ZerodhaRegimeDetector low confidence (%.2f) for %s, price_change=%.2f%%",
                confidence,
                symbol,
                price_change_pct,
            )

        drivers = [f"price_change={price_change_pct:.2f}%"]

        result = {
            "direction": direction,
            "sentiment": direction,
            "confidence": round(confidence, 3),
            "drivers": drivers,
            "source": "zerodha",
        }
        self._cache[symbol] = (result, time.time())
        return result
