# ═══════════════════════════════════════════════════════════════
#  Broker Interfaces — Unified API for Zerodha / Angel One + NSE Live
# ═══════════════════════════════════════════════════════════════
import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Optional imports for local DuckDB/Parquet storage; if unavailable, fallback to API.
try:
    import duckdb
except Exception:  # pragma: no cover
    duckdb = None
try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None
import logging

from quant_utils.logger import get_logger

log = get_logger("sources.broker")
# Use INFO level to avoid excessive debug output in normal operation.
log.setLevel(logging.INFO)


@dataclass
class Order:
    symbol: str
    exchange: str
    transaction_type: str
    quantity: int
    order_type: str = "MARKET"
    price: float | None = None
    product_type: str = "INTRADAY"


@dataclass
class Position:
    symbol: str
    quantity: int
    average_price: float
    current_price: float
    pnl: float
    direction: str


@dataclass
class Quote:
    symbol: str
    last_price: float
    volume: int
    bid: float
    ask: float
    bid_quantity: int = 0
    ask_quantity: int = 0
    timestamp: Any = None


class SymbolTracker:
    """Track unknown symbols, their fetch failures, and retry attempts"""

    def __init__(self):
        self.failed_symbols = (
            {}
        )  # symbol -> {attempt_count, last_error, last_attempt_time, corrected_symbols}
        self.successful_corrections = {}  # original -> corrected_symbol
        self.retry_queue = []  # List of symbols to retry
        import threading

        self._lock = threading.Lock()

    def track_failure(
        self,
        symbol: str,
        error_reason: str,
        corrected_formats: list = None,
        retry: bool = True,
    ):
        """Track a failed symbol fetch attempt"""
        with self._lock:
            if symbol not in self.failed_symbols:
                self.failed_symbols[symbol] = {
                    "attempt_count": 0,
                    "last_error": error_reason,
                    "errors": [],
                    "corrected_formats": corrected_formats or [],
                    "last_attempt_time": 0,
                }

            entry = self.failed_symbols[symbol]
            entry["attempt_count"] += 1
            entry["last_error"] = error_reason
            entry["errors"].append(error_reason)

            import time

            entry["last_attempt_time"] = time.time()

            # Add to retry queue if not already there
            if retry and symbol not in self.retry_queue:
                self.retry_queue.append(symbol)

            log.debug(
                f"Symbol tracking: {symbol} failed ({entry['attempt_count']}x) - {error_reason}"
            )

    def track_success(self, symbol: str, corrected_symbol: str = None):
        """Track successful fetch after correction"""
        with self._lock:
            if corrected_symbol:
                self.successful_corrections[symbol] = corrected_symbol
                log.info(f"Symbol correction tracked: {symbol} → {corrected_symbol}")
            if symbol in self.failed_symbols:
                del self.failed_symbols[symbol]
            if symbol in self.retry_queue:
                self.retry_queue.remove(symbol)

    def get_retry_candidates(self, max_attempts: int = 3) -> list:
        """Get symbols to retry (haven't exceeded max attempts)"""
        with self._lock:
            return [
                s
                for s, info in self.failed_symbols.items()
                if info["attempt_count"] < max_attempts
            ]

    def get_report(self) -> dict:
        """Generate report of all symbol fetch issues"""
        with self._lock:
            return {
                "total_failed": len(self.failed_symbols),
                "total_retry_queue": len(self.retry_queue),
                "corrected_symbols": self.successful_corrections.copy(),
                "failed_symbols": {
                    k: {
                        "attempt_count": v["attempt_count"],
                        "last_error": v["last_error"],
                        "total_errors": len(v["errors"]),
                        "corrected_formats": v["corrected_formats"],
                    }
                    for k, v in self.failed_symbols.items()
                },
            }


class BrokerInterface(ABC):
    """Abstract broker interface"""

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def place_order(self, order: Order) -> dict:
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote | None:
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        pass

    @abstractmethod
    def get_order_history(self) -> list[dict]:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict:
        pass

    @abstractmethod
    def get_historical_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        pass

    @abstractmethod
    def get_lot_size(self, symbol: str) -> int:
        pass


class NSELiveBroker(BrokerInterface):
    """NSE Live data broker using jugaad-data + NseIndiaApi + yfinance fallback"""

    def __init__(self, config: dict = None, data_provider=None):
        """Initialise the NSELiveBroker.

        * ``config`` – optional broker configuration dictionary. If ``None`` or empty,
          the method will attempt to load the default configuration from
          ``config/config.yaml`` located at the project root. This ensures that
          credentials for Zerodha (API key, secret, and saved access token) are
          available without the caller having to pass them explicitly.
        * ``data_provider`` – optional external data provider (e.g., ``NSEEnrichmentProvider``).
        """
        # Load configuration from file when not supplied explicitly
        if not config:
            try:
                # ``yaml`` and ``os`` are already imported at module level, so we can
                # use them directly without re‑importing. Re‑importing inside the
                # function creates a local binding that may be undefined if the
                # import fails, leading to an ``UnboundLocalError`` when ``os`` is
                # later referenced for loading the Zerodha token. Using the global
                # imports avoids that issue.
                cfg_path = os.path.abspath(
                    os.path.join(os.path.dirname(__file__), "../../config/config.yaml")
                )
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as f:
                        self.config = yaml.safe_load(f) or {}
                else:
                    log.warning(
                        f"Configuration file not found at {cfg_path}; using empty config."
                    )
                    self.config = {}
            except Exception as e:
                log.debug(f"Failed to load config.yaml: {e}")
                self.config = {}
        else:
            self.config = config

        # -----------------------------------------------------------------
        # Load and validate Zerodha access token.
        # -----------------------------------------------------------------
        # The original implementation simply read the token from
        # ``nse_cookies_requests.json`` without any validation.  This could lead
        # to using an expired or malformed token, causing WebSocket handshake
        # failures.  We now reuse the robust validation logic from ``main.py``
        # by leveraging ``ZerodhaTokenManager`` which performs a profile check
        # against Zerodha.
        #
        # Steps:
        #   1. Resolve API key/secret from the broker configuration (mirrors the
        #      resolution in ``_run_zerodha_oauth``).
        #   2. Instantiate ``ZerodhaTokenManager`` with those credentials.
        #   3. Call ``load_token()`` – it loads the token from the standard
        #      ``data/zerodha_token.json`` file and validates it via the
        #      Zerodha profile endpoint.
        #   4. If loading succeeds, store the token in ``self.config`` for the
        #      rest of the broker to use and log that the cached token is being
        #      reused.
        #   5. If loading fails, fall back to the legacy ``nse_cookies_requests``
        #      file (maintaining backward compatibility) and log a warning.
        # -----------------------------------------------------------------
        if not self.config.get("zerodha_access_token"):
            # Resolve API credentials – same logic as in ``main._run_zerodha_oauth``.
            broker_settings = self.config.get("broker", {})
            zerodha_cfg = broker_settings.get("zerodha", {})
            api_key = (
                zerodha_cfg.get("api_key")
                or broker_settings.get("zerodha_api_key")
                or broker_settings.get("api_key")
                or self.config.get("zerodha_api_key")
                or self.config.get("api_key")
            )
            api_secret = (
                zerodha_cfg.get("api_secret")
                or broker_settings.get("zerodha_api_secret")
                or broker_settings.get("api_secret")
                or self.config.get("zerodha_api_secret")
                or self.config.get("api_secret")
            )

            token_loaded = False
            if api_key and api_secret:
                try:
                    from core.token_manager import ZerodhaTokenManager

                    token_manager = ZerodhaTokenManager(api_key, api_secret)
                    if token_manager.load_token():
                        self.config["zerodha_access_token"] = token_manager.access_token
                        log.info(
                            "Reusing cached Zerodha access token via ZerodhaTokenManager"
                        )
                        token_loaded = True
                except Exception as e:
                    log.debug(f"ZerodhaTokenManager load failed: {e}")

            # Fallback to legacy cookie file if token manager did not load a token.
            # NOTE: The legacy ``nse_cookies_requests.json`` contains NSE‑specific cookies and
            # is **not** a valid source for a Zerodha access token. Loading it can cause
            # the broker to use an incorrect token, leading to repeated 400 errors.
            # We therefore guard against loading any token from a file whose name
            # includes ``nse_``. If such a file is encountered, we log the decision and
            # skip the fallback.
            if not token_loaded:
                try:
                    token_path = os.path.abspath(
                        os.path.join(
                            os.path.dirname(__file__), "../../nse_cookies_requests.json"
                        )
                    )
                    # Guard: ignore fallback if the file name suggests it is an NSE cookie file.
                    if "nse_" in os.path.basename(token_path).lower():
                        log.info(
                            "Skipping fallback token load from NSE cookies file (%s) – not a valid Zerodha token.",
                            token_path,
                        )
                    elif os.path.exists(token_path):
                        with open(token_path, "r") as tf:
                            data = json.load(tf)
                        token = data.get("nsit")
                        if token:
                            self.config["zerodha_access_token"] = token
                            log.info(
                                "Loaded Zerodha access token from legacy fallback file %s",
                                token_path,
                            )
                        else:
                            log.warning(
                                "'nsit' key not found in %s; Zerodha token not loaded.",
                                token_path,
                            )
                    else:
                        log.debug(
                            "Fallback token file %s not found; proceeding without Zerodha access token.",
                            token_path,
                        )
                except Exception as e:
                    log.error(
                        f"Failed to load Zerodha access token from fallback file: {e}"
                    )
        else:
            # ``self.config`` already contains a token – nothing to do.
            pass
        self.connected = False
        self.orders = []
        self.positions = {}
        # Initialize source status dictionary with default enabled state for all supported sources
        self._source_status = {
            "scrape": {"enabled": True, "fail_count": 0, "disabled_until": 0},
            "nse_api": {"enabled": True, "fail_count": 0, "disabled_until": 0},
            "yfinance": {"enabled": True, "fail_count": 0, "disabled_until": 0},
            "jugaad": {"enabled": True, "fail_count": 0, "disabled_until": 0},
            "alternative": {"enabled": True, "fail_count": 0, "disabled_until": 0},
            "mcp": {"enabled": True, "fail_count": 0, "disabled_until": 0},
        }
        self.source_health_check_enabled = self.config.get(
            "source_health_check_enabled", True
        )
        self.source_disable_duration = self.config.get("source_disable_duration", 300)
        self.source_failure_threshold = self.config.get("source_failure_threshold", 5)
        self._jugaad = None
        self._jugaad_available = None
        self._nse = None
        self._rate_limit_delay = self.config.get("rate_limit_delay", 0.0)
        self._last_request_time = 0.0
        self._request_count = 0
        self._rate_limit_lock = None
        # Optional external data provider (e.g., NSEEnrichmentProvider)
        self.data_provider = data_provider
        # Symbol tracker for handling unknown symbols
        self.symbol_tracker = SymbolTracker()
        # Load extended stock symbols from cache if configured
        self._load_extended_stock_symbols()
        # Human‑readable identifier for this broker implementation
        self.name = "NSELiveBroker"

    # Popular NSE stock symbols (maintained list)
    STOCK_SYMBOLS = [
        "RELIANCE",
        "TCS",
        "INFY",
        "HDFCBANK",
        "ICICIBANK",
        "ITC",
        "SBIN",
        "BHARTIARTL",
        "HINDUNILVR",
        "IOC",
        "LT",
        "ASIANPAINT",
        "MARUTI",
        "BAJFINANCE",
        "TITAN",
        "NESTLE",
        "AXISBANK",
        "KOTAKBANK",
        "SUNPHARMA",
        "ADANIENT",
        "POWERGRID",
        "NTPC",
        "ONGC",
        "COALINDIA",
        "HINDALCO",
        "AXISBANK",
        "BAJAJFINSV",
        "ULTRACEMCO",
        "DRREDDY",
        "CIPLA",
        "WIPRO",
        "TECHM",
        "M&M",
        "MARICO",
        "TMCV",
        "TMPV",
        "ADANIPORTS",
        "GRASIM",
        "JSWSTEEL",
        "TATASTEEL",
        "UPL",
        "SHREECEM",
        "DIVISLAB",
        "BPCL",
        "HCLTECH",
        "WFI",
        "KUMARI",
        "INDUSINDBK",
        "RBLBANK",
        "IDEA",
        "VODAFONE",
        "BHEL",
        "GAIL",
        "CENTURYTEX",
        "PNB",
    ]

    # NSE Index symbols - maps to yfinance tickers for fallback
    INDEX_SYMBOLS = {
        # Major Indices - include all NSE indices, mark problematic ones for no yfinance
        "NIFTY": "^NSEI",
        "NIFTY 50": "^NSEI",
        "NIFTY50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "NIFTY BANK": "^NSEBANK",
        "BANKNIFTY.NS": "^NSEBANK",
        "NIFTYNEXT50": None,  # No Yahoo Finance symbol, use NSE only
        "INDIAVIX": None,  # No Yahoo Finance fallback, use NSE/Zerodha only
        # Sector Indices (working)
        "NIFTYIT": "^CNXIT",
        "NIFTY IT": "^CNXIT",
        "NIFTYAUTO": "^CNXAUTO",
        "NIFTY AUTO": "^CNXAUTO",
        "NIFTYFMCG": "^CNXFMCG",
        "NIFTY FMCG": "^CNXFMCG",
    }

    # Rate limiting for NSE API (3 requests per second)
    _rate_limit_delay = 0  # ~3 req/sec with buffer
    _last_request_time = 0.0
    _request_count = 0
    _rate_limit_lock = None  # initialized on first use for thread-safe rate limiting

    def get_quote(self, symbol: str) -> Quote | None:
        """Fetch a quote for a given symbol.
        Primary handling for INDIAVIX using Zerodha before any other sources.
        """
        start_time = time.time()
        symbol_upper = symbol.upper()
        # Mapping of internal index symbols to Zerodha tradingsymbols for primary fetch
        _zerodha_index_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "BANKNIFTY",
            "INDIAVIX": "INDIA VIX",
        }
        log.info(f"get_quote called with symbol_upper={symbol_upper!r}")
        # Primary handling for known indices via Zerodha before any other source
        _zerodha_index_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "BANKNIFTY",
            "INDIAVIX": "INDIA VIX",
        }
        # Attempt Zerodha for indices if instrument exists in cache
        if symbol_upper in _zerodha_index_map:
            try:
                if not hasattr(self, "_zerodha_broker"):
                    self._zerodha_broker = ZerodhaBroker(self.config)
                    self._zerodha_broker.connect()
                instrument = self._zerodha_broker.get_instrument(
                    _zerodha_index_map[symbol_upper]
                )
                if instrument:
                    log.debug(
                        f"Zerodha instrument found for {symbol_upper}: {instrument}"
                    )
                    zerodha_quote = self._zerodha_broker.get_quote(
                        _zerodha_index_map[symbol_upper]
                    )
                    if zerodha_quote:
                        elapsed = time.time() - start_time
                        log.debug(
                            f"Zerodha quote for {symbol} obtained in {elapsed:.2f}s"
                        )
                        return zerodha_quote
                else:
                    log.debug(
                        f"Zerodha cache missing instrument for {symbol_upper}; skipping Zerodha fetch"
                    )
            except Exception as e:
                log.debug(f"Zerodha fetch failed for {symbol}: {e}")
        # Primary handling for known indices via Zerodha before any other source
        # Mapping of our internal index symbols to Zerodha tradingsymbols
        _zerodha_index_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "BANKNIFTY",
            "INDIAVIX": "INDIA VIX",
        }
        # Debug entry point for Zerodha index guard
        log.debug("Entering Zerodha index guard")
        # Attempt Zerodha for indices only if the instrument exists in the Zerodha master contract cache.
        # This prevents noisy "Zerodha returned empty data" logs when the cache lacks entries for
        # symbols such as "NIFTY 50" or "INDIA VIX".
        if symbol_upper in _zerodha_index_map:
            try:
                if not hasattr(self, "_zerodha_broker"):
                    self._zerodha_broker = ZerodhaBroker(self.config)
                    self._zerodha_broker.connect()
                # Verify the instrument is present in the cache before requesting a quote.
                instrument = self._zerodha_broker.get_instrument(
                    _zerodha_index_map[symbol_upper]
                )
                if instrument:
                    log.debug(
                        f"Zerodha instrument found for {symbol_upper}: {instrument}"
                    )
                    zerodha_quote = self._zerodha_broker.get_quote(
                        _zerodha_index_map[symbol_upper]
                    )
                    log.debug(f"Zerodha quote fetched: {zerodha_quote}")
                    if zerodha_quote:
                        elapsed = time.time() - start_time
                        log.debug(
                            f"Zerodha quote for {symbol} obtained in {elapsed:.2f}s"
                        )
                        return zerodha_quote
                else:
                    log.debug(
                        f"Zerodha cache missing instrument for {symbol_upper}; skipping Zerodha fetch"
                    )
            except Exception as e:
                log.debug(f"Zerodha fetch failed for {symbol}: {e}")
        # Index handling
        if symbol_upper in self.INDEX_SYMBOLS:
            quote = self._get_nse_scrape_quote(symbol)
            if quote and quote.last_price is not None and quote.last_price > 0:
                log.debug(f"NSE scrape index quote for {symbol}: {quote.last_price}")
                elapsed = time.time() - start_time
                log.debug(f"get_quote({symbol}) completed in {elapsed:.2f}s")
                # For indices that have a Zerodha mapping, defer returning so Zerodha can be tried.
                return quote
            # Try NSE API as secondary fallback for indices
            quote = self._get_nse_api_quote(symbol)
            if quote and quote.last_price is not None and quote.last_price > 0:
                elapsed = time.time() - start_time
                log.debug(f"get_quote({symbol}) completed in {elapsed:.2f}s")
                return quote
            # yfinance fallback for indices with Yahoo symbols
            yahoo_symbol = self.INDEX_SYMBOLS.get(symbol_upper)
            if yahoo_symbol:
                try:
                    import yfinance as yf

                    yf_ticker = yf.Ticker(yahoo_symbol)
                    data = yf_ticker.history(period="1d")
                    if not data.empty:
                        last_price = data["Close"].iloc[-1]
                        log.debug(f"yfinance index quote for {symbol}: {last_price}")
                        elapsed = time.time() - start_time
                        log.debug(f"get_quote({symbol}) completed in {elapsed:.2f}s")
                        return Quote(
                            symbol=symbol, last_price=last_price, volume=0, bid=0, ask=0
                        )
                except Exception as e:
                    log.debug(f"yfinance index fetch failed for {symbol}: {e}")
        # Stock handling – use data provider if available for regular stocks
        if self.data_provider:
            try:
                quote = self.data_provider.get_quote(symbol)
                if quote:
                    elapsed = time.time() - start_time
                    log.debug(
                        f"Data provider quote for {symbol} obtained in {elapsed:.2f}s"
                    )
                    return quote
            except Exception as e:
                log.debug(f"Data provider failed for {symbol}: {e}")
        # Fallback to internal mechanisms for stocks (omitted for brevity)
        # If all methods fail, attempt a generic Zerodha fallback for any symbol (including futures)
        try:
            if not hasattr(self, "_zerodha_broker"):
                self._zerodha_broker = ZerodhaBroker(self.config)
                self._zerodha_broker.connect()
            # Directly request a quote for the given symbol; Zerodha supports futures symbols as well.
            zerodha_quote = self._zerodha_broker.get_quote(symbol)
            if zerodha_quote:
                elapsed = time.time() - start_time
                log.debug(
                    f"Zerodha generic fallback quote for {symbol} obtained in {elapsed:.2f}s"
                )
                return zerodha_quote
        except Exception as e:
            log.debug(f"Zerodha generic fallback failed for {symbol}: {e}")
        # If all methods fail, return None
        return None

    def _is_yfinance_delisted_error(self, error: Exception) -> bool:
        if not error:
            return False
        message = str(error).lower()
        return "possibly delisted" in message or "no price data found" in message

    def _to_yf_ticker(self, symbol: str) -> str:
        if not symbol:
            return ""
        symbol_up = symbol.strip().upper()
        if symbol_up in self.INDEX_SYMBOLS:
            return self.INDEX_SYMBOLS[symbol_up]
        if symbol_up.endswith(".NS") or symbol_up.endswith(".BO"):
            # Normalize casing and preserve existing NSE/BSE suffixes
            base, suffix = symbol_up.rsplit(".", 1)
            return f"{base}.{suffix}"
        return f"{symbol_up}.NS"

    def _normalize_nse_symbol(self, symbol: str) -> str:
        if not symbol:
            return ""
        symbol_up = symbol.strip().upper()
        # Remove exchange suffixes
        if symbol_up.endswith(".NS") or symbol_up.endswith(".BO"):
            symbol_up = symbol_up.rsplit(".", 1)[0]
        # Remove or handle special characters that might indicate delisted/invalid symbols
        # Symbols like "$ETERNAL.NS" are malformed and should be logged
        if "$" in symbol_up or symbol_up.startswith("~"):
            log.warning(
                f"Symbol contains special characters (delisted?): {symbol} -> normalized: {symbol_up}"
            )
        return symbol_up

    def _load_extended_stock_symbols(self):
        """Load extended stock symbols from fno_stocks_cache.json"""
        try:
            import json
            import os

            # Path from sources/broker/__init__.py to data/fno_stocks_cache.json
            cache_file = os.path.join(
                os.path.dirname(__file__), "../../data/fno_stocks_cache.json"
            )
            cache_file = os.path.abspath(cache_file)
            if os.path.exists(cache_file):
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "stocks" in data:
                        # Convert to set for faster lookups, then back to list
                        extended_stocks = set(self.STOCK_SYMBOLS) | set(data["stocks"])
                        self.STOCK_SYMBOLS = list(extended_stocks)
                        log.info(
                            f"Loaded {len(extended_stocks)} extended stock symbols from cache"
                        )
            else:
                log.debug(f"Cache file not found at {cache_file}")
        except Exception as e:
            log.debug(f"Could not load extended stock symbols: {e}")

    def _init_jugaad(self):
        """Lazy init jugaad-data"""
        if self._jugaad_available is None:
            try:
                from jugaad_data.nse import NSELive

                self._jugaad = NSELive()
                self._jugaad_available = True
            except Exception:
                self._jugaad_available = False

    def _apply_rate_limit(self):
        """Apply rate limiting for NSE API (3 requests per second) - thread safe"""
        import threading
        import time

        if NSELiveBroker._rate_limit_lock is None:
            NSELiveBroker._rate_limit_lock = threading.Lock()
        with NSELiveBroker._rate_limit_lock:
            current_time = time.time()
            elapsed = current_time - NSELiveBroker._last_request_time

            if elapsed < NSELiveBroker._rate_limit_delay:
                sleep_time = NSELiveBroker._rate_limit_delay - elapsed
                time.sleep(sleep_time)

            NSELiveBroker._last_request_time = time.time()
            NSELiveBroker._request_count += 1

    def _is_source_enabled(self, source: str) -> bool:
        import time

        status = self._source_status.get(source)
        if not status:
            return False
        if status["enabled"]:
            return True
        if time.time() >= status["disabled_until"]:
            status["enabled"] = True
            status["fail_count"] = 0
            log.info(f"Source {source} re-enabled after cooldown")
            return True
        return False

    def _disable_source(self, source: str, reason: str) -> None:
        import time
        from datetime import datetime

        status = self._source_status.get(source)
        if not status:
            return
        if not status["enabled"]:
            return
        status["enabled"] = False
        status["disabled_until"] = time.time() + self.source_disable_duration
        log.warning(
            f"Source {source} disabled until {datetime.fromtimestamp(status['disabled_until']).isoformat()} "
            f"due to repeated failures: {reason}"
        )

    def _record_source_failure(self, source: str, reason: str) -> None:
        status = self._source_status.get(source)
        if not status:
            return
        status["fail_count"] += 1
        log.debug(f"Source {source} failure #{status['fail_count']}: {reason}")
        if status["fail_count"] > self.source_failure_threshold:
            self._disable_source(source, reason)

    def _record_source_success(self, source: str) -> None:
        import time

        status = self._source_status.get(source)
        if not status:
            return
        status["fail_count"] = 0
        if not status["enabled"] and time.time() >= status["disabled_until"]:
            status["enabled"] = True
            log.info(f"Source {source} re-enabled after successful probe")

    def _verify_source(self, source: str, probe_fn) -> bool:
        if not self._is_source_enabled(source):
            log.debug(f"Skipping health check for disabled source {source}")
            return False
        try:
            result = probe_fn()
            if result:
                self._record_source_success(source)
                log.debug(f"Source {source} health check passed")
                return True
        except Exception as exc:
            self._record_source_failure(source, f"health check failed: {exc}")
        return False

    def _perform_source_health_checks(self):
        if not self.source_health_check_enabled:
            return

        if self._jugaad_available:
            self._verify_source("jugaad", lambda: self._jugaad.stock_quote("RELIANCE"))

        if self._nse and hasattr(self._nse, "quote"):
            self._verify_source("nse_api", lambda: self._nse.quote("RELIANCE"))

        self._verify_source("scrape", lambda: self._get_nse_scrape_quote("RELIANCE"))
        self._verify_source(
            "alternative", lambda: self._get_alternative_quote("RELIANCE")
        )
        self._verify_source("yfinance", lambda: self._get_yfinance_quote("RELIANCE"))
        self._log_source_status()

    def _source_status_report(self) -> str:
        parts = []
        for source, status in self._source_status.items():
            if status["enabled"]:
                parts.append(f"{source}=enabled")
            else:
                parts.append(f"{source}=disabled until {status['disabled_until']}")
        return ", ".join(parts)

    def _log_source_status(self):
        report = self._source_status_report()
        log.info(f"NSELiveBroker source status: {report}")

    def connect(self) -> bool:
        # NSELiveBroker is used to fetch live data from NSE.

        self._init_jugaad()
        if self._jugaad_available:
            self.connected = True
            log.info("NSE Live broker connected (jugaad-data fallback)")
            if self.source_health_check_enabled:
                self._perform_source_health_checks()
            return True

        try:
            from nse import NSE

            self._nse = NSE(download_folder="", server=False)
            self.connected = True
            log.info("NSE Live broker connected (NseIndiaApi)")
            if self.source_health_check_enabled:
                self._perform_source_health_checks()
            return True
        except ImportError:
            log.warning("NseIndiaApi not available, trying yfinance")

        self.connected = True
        log.info("NSE Live broker connected (yfinance fallback only)")
        if self.source_health_check_enabled:
            self._perform_source_health_checks()
        return True

    def disconnect(self) -> None:
        self.connected = False
        log.info("NSE Live broker disconnected")

    def place_order(self, order: Order) -> dict:
        order_id = f"NSELIVE_{len(self.orders) + 1}"

        self.orders.append(
            {
                "order_id": order_id,
                "symbol": order.symbol,
                "quantity": order.quantity,
                "price": order.price or 0,
                "status": "success",
            }
        )

        if order.symbol not in self.positions:
            self.positions[order.symbol] = {"qty": 0, "avg_price": 0}

        pos = self.positions[order.symbol]
        if order.transaction_type == "BUY":
            pos["qty"] += order.quantity
        else:
            pos["qty"] -= order.quantity

        log.info(f"[PAPER] {order.transaction_type} {order.quantity} {order.symbol}")
        return {"status": "success", "order_id": order_id}

    def _suggest_symbol_corrections(self, symbol: str) -> list:
        """Suggest alternative symbol formats to try"""
        corrections = []
        symbol_upper = symbol.upper().strip()

        # Check if already has .NS or .BO suffix
        if symbol_upper.endswith(".NS") or symbol_upper.endswith(".BO"):
            base = symbol_upper.rsplit(".", 1)[0]
        else:
            base = symbol_upper

        # Try various formats
        corrections.append(f"{base}.NS")  # NSE listing suffix
        corrections.append(f"{base}.BO")  # Bombay Stock Exchange

        # If symbol has special chars, try removing them
        if any(c in symbol for c in ["-", "_", " "]):
            cleaned = symbol_upper.replace("-", "").replace("_", "").replace(" ", "")
            corrections.append(cleaned)
            corrections.append(f"{cleaned}.NS")

        # Try removing &M or similar suffixes
        if "&" in symbol:
            corrected = symbol_upper.replace("&", "N")
            corrections.append(corrected)
            corrections.append(f"{corrected}.NS")

        # Known alias corrections (common shortened tickers -> NSE symbol)
        alias_map = {
            "NESTLE": "NESTLEIND",
        }
        if symbol_upper in alias_map:
            corrections.insert(0, alias_map[symbol_upper])
            corrections.insert(1, f"{alias_map[symbol_upper]}.NS")

        return list(dict.fromkeys(corrections))  # Remove duplicates

    def _try_symbol_correction(self, symbol: str) -> Quote | None:
        """Try alternative symbol formats to fetch price"""
        corrections = self._suggest_symbol_corrections(symbol)
        original_symbol = symbol

        log.debug(
            f"Trying {len(corrections)} symbol corrections for {symbol}: {corrections}"
        )

        for corrected_symbol in corrections:
            if corrected_symbol == symbol:
                continue  # Skip original

            try:
                # Try jugaad-data with corrected symbol
                self._init_jugaad()
                if self._jugaad_available and self._jugaad:
                    self._apply_rate_limit()
                    quote = self._jugaad.stock_quote(corrected_symbol)
                    if quote and isinstance(quote, dict):
                        price_info = quote.get("priceInfo", {})
                        last_price = price_info.get("lastPrice")
                        if last_price and float(last_price) > 0:
                            log.info(
                                f"Symbol correction successful: {original_symbol} → {corrected_symbol} (price: {last_price})"
                            )
                            self.symbol_tracker.track_success(
                                original_symbol, corrected_symbol
                            )
                            return Quote(
                                symbol=original_symbol,
                                last_price=float(last_price),
                                volume=int(
                                    quote.get("preOpenMarket", {}).get(
                                        "totalTradedVolume", 0
                                    )
                                ),
                                bid=float(
                                    price_info.get("intraDayHighLow", {}).get(
                                        "min", last_price
                                    )
                                ),
                                ask=float(
                                    price_info.get("intraDayHighLow", {}).get(
                                        "max", last_price
                                    )
                                ),
                            )
                    log.debug(
                        f"Symbol correction {corrected_symbol}: no valid price data"
                    )
            except Exception as e:
                log.debug(f"Symbol correction {corrected_symbol} failed: {e}")

        return None

    def get_quote(self, symbol: str) -> Quote | None:
        if not self.connected:
            # Lazy-connect on first use instead of failing immediately
            try:
                self.connect()
            except Exception as e:
                log.debug(f"Lazy connect failed: {e}")

        symbol_upper = symbol.upper()

        # Early detection for expired weekly/monthly options to avoid hammering data sources on expiry day
        # (prevents "added to retry queue" spam and 404s from yfinance/NSE for delisted contracts)
        if self._is_expired_option_symbol(symbol):
            log.info(
                f"Skipping quote fetch for expired option {symbol} (no live market)"
            )
            return Quote(symbol=symbol, last_price=0, volume=0, bid=0, ask=0)

        # Use robust parser to detect option-like symbols (handles expiry tokens and suffixes)
        parsed_opt = self._parse_option_symbol(symbol_upper)
        is_option_symbol = parsed_opt is not None

        # Quote path: use NSE scrape / API / alternative sources only.
        # Check if it's a known stock symbol - use NSE sources only (no yfinance fallback)
        if symbol_upper in self.INDEX_SYMBOLS:
            quote = self._get_nse_scrape_quote(symbol)
            if quote and quote.last_price is not None and quote.last_price > 0:
                log.debug(f"NSE scrape index quote for {symbol}: {quote.last_price}")
                # Return the NSE scrape quote; Zerodha fallback will be attempted later if needed
                return quote
            # Try NSE API as secondary fallback for indices
            quote = self._get_nse_api_quote(symbol)
            if quote and quote.last_price is not None and quote.last_price > 0:
                return quote
            # Only try yfinance for indices that have yahoo symbols
            yahoo_symbol = self.INDEX_SYMBOLS.get(symbol_upper)
            if yahoo_symbol:
                return self._get_yfinance_quote(symbol)
            # No Yahoo symbol available – no further sources for this index.
            log.debug(f"No price source available for index {symbol}")
            return None

        # Check if it's an options symbol (e.g., ADANIENT2100CE, NIFTY24500PE)
        # Parse full option symbol (handles formats like NIFTY26MAY2623700PE or NMDC30JUN2674PE.NS)
        parsed = self._parse_option_symbol(symbol_upper)
        is_option = parsed is not None

        if is_option:
            # For options, extract from option chain
            option_quote = self._get_option_quote_from_chain(symbol)
            if option_quote:
                log.info(f"Option quote for {symbol}: {option_quote.last_price}")
                return option_quote
            # Option chain failed - try NSE website scraping as fallback
            log.debug(f"Option chain failed for {symbol}, trying NSE website scrape")
            scraped_quote = self._get_nse_scrape_quote(symbol)
            if (
                scraped_quote
                and scraped_quote.last_price is not None
                and scraped_quote.last_price > 0
            ):
                log.info(
                    f"Option quote from NSE scrape for {symbol}: {scraped_quote.last_price}"
                )
                return scraped_quote
            # All methods failed
            # Option quote missing; downgrade to debug to avoid log flooding.
            log.debug(f"Option quote not found for {symbol} (tried chain and scrape)")
            # Try calculating intrinsic value as last resort
            import re

            match = re.match(r"^([A-Z]+)(\d+)(CE|PE)$", symbol_upper)
            if match:
                underlying = match.group(1)
                strike = int(match.group(2))
                option_type = match.group(3)
                underlying_quote = self.get_quote(underlying)
                if underlying_quote:
                    spot = underlying_quote.last_price
                    intrinsic = (
                        max(0, strike - spot)
                        if option_type == "PE"
                        else max(0, spot - strike)
                    )
                    log.info(
                        f"Intrinsic value for {symbol}: {intrinsic} (underlying {underlying}: {spot})"
                    )
                    return Quote(symbol=symbol, last_price=intrinsic)
            return None

        # Check if it's a known stock symbol - use NSE sources first, fallback to yfinance on rate limit
        is_known_stock = symbol_upper in self.STOCK_SYMBOLS
        if is_known_stock:
            quote = self._get_nse_api_quote(symbol)
            if (
                quote
                and quote.last_price is not None
                and quote.last_price > 0
                and quote.last_price != 100
            ):
                log.info(f"NSE API quote for {symbol}: {quote.last_price}")
                return quote
            else:
                quote = self._get_nse_api_quote(symbol)
                if (
                    quote
                    and quote.last_price is not None
                    and quote.last_price > 0
                    and quote.last_price != 100
                ):
                    log.info(f"NSE API quote for {symbol}: {quote.last_price}")
                    return quote
                log.debug(
                    f"NSE API failed for known stock {symbol}, trying alternative NSE sources"
                )
                self.symbol_tracker.track_failure(
                    symbol, "NSE API returned no valid price for known symbol"
                )
                scraped = self._get_nse_scrape_quote(symbol)
                if (
                    scraped
                    and scraped.last_price is not None
                    and scraped.last_price > 0
                ):
                    log.info(
                        f"NSE scrape fallback for known stock {symbol}: {scraped.last_price}"
                    )
                    return scraped
                log.debug(
                    f"NSE sources failed for known stock {symbol}, trying yfinance as fallback"
                )
                quote = self._get_yfinance_quote(symbol)
                if quote and quote.last_price is not None and quote.last_price > 0:
                    log.info(
                        f"yfinance fallback for known stock {symbol}: {quote.last_price}"
                    )
                    return quote
                log.warning(
                    f"Failed to fetch quote for known stock {symbol} (NSE API, scrape, and yfinance failed)"
                )
                return None

        # Unknown symbol - try API first
        log.debug(f"Unknown symbol {symbol}, trying NSE API")
        quote = self._get_nse_api_quote(symbol)
        if quote and quote.last_price is not None and quote.last_price > 0:
            log.info(f"NSE API quote for unknown symbol {symbol}: {quote.last_price}")
            return quote

        # API failed for unknown symbol - try symbol format corrections
        log.debug(f"NSE API failed for {symbol}, trying symbol format corrections")
        corrected_quote = self._try_symbol_correction(symbol)
        if corrected_quote:
            return corrected_quote

        # Corrections failed - try NSE scraping
        log.debug(f"Corrections failed for {symbol}, trying NSE scraping")
        scraped_quote = self._get_nse_scrape_quote(symbol)
        if (
            scraped_quote
            and scraped_quote.last_price is not None
            and scraped_quote.last_price > 0
        ):
            log.info(f"NSE scrape quote for {symbol}: {scraped_quote.last_price}")
            return scraped_quote

        # NSE scraping failed - try yfinance as last resort (only for unknown symbols)
        quote = self._get_yfinance_quote(symbol)
        if quote:
            log.info(f"yfinance fallback for {symbol}: {quote.last_price}")
            self.symbol_tracker.track_success(
                symbol, symbol
            )  # Mark as successful via yfinance
            return quote

        if symbol.upper() in self._yfinance_delisted_symbols:
            log.warning(
                f"Symbol {symbol} marked delisted by yfinance; skipping retry queue"
            )
            self.symbol_tracker.track_failure(
                symbol, "Delisted symbol detected by yfinance", retry=False
            )
            return None

        # All methods failed - track for later retry
        self.symbol_tracker.track_failure(
            symbol,
            "All methods failed (API, corrections, scrape, yfinance)",
            self._suggest_symbol_corrections(symbol),
        )
        log.warning(f"Failed to fetch quote for {symbol} - added to retry queue")
        return None

    def retry_failed_symbols(self, max_symbols: int = 10) -> dict:
        """Retry fetching quotes for previously failed symbols"""
        retry_candidates = self.symbol_tracker.get_retry_candidates(max_attempts=3)
        if not retry_candidates:
            log.debug("No symbols to retry")
            return {"retried": 0, "successful": 0, "still_failed": 0}

        # Limit retry attempts
        symbols_to_retry = retry_candidates[:max_symbols]
        results = {"retried": len(symbols_to_retry), "successful": 0, "still_failed": 0}

        log.info(f"Retrying {len(symbols_to_retry)} failed symbols...")

        for symbol in symbols_to_retry:
            try:
                quote = self.get_quote(symbol)
                if quote and quote.last_price > 0:
                    results["successful"] += 1
                    log.info(f"Retry successful for {symbol}: {quote.last_price}")
                else:
                    results["still_failed"] += 1
            except Exception as e:
                log.debug(f"Retry failed for {symbol}: {e}")
                results["still_failed"] += 1

        return results

    def get_symbol_tracking_report(self) -> dict:
        """Get detailed report of all symbol fetch issues"""
        return self.symbol_tracker.get_report()

    def print_symbol_tracking_report(self):
        """Print human-readable report of symbol tracking"""
        report = self.get_symbol_tracking_report()

        if report["total_failed"] == 0:
            log.info("No symbol tracking issues - all symbols fetching successfully")
            return

        log.warning("=== SYMBOL TRACKING REPORT ===")
        log.warning(
            f"Total failed: {report['total_failed']} | Retry queue: {report['total_retry_queue']}"
        )

        if report["corrected_symbols"]:
            log.info(f"Successful corrections: {len(report['corrected_symbols'])}")
            for orig, corrected in report["corrected_symbols"].items():
                log.info(f"  ✓ {orig} → {corrected}")

        if report["failed_symbols"]:
            log.warning(f"Failed symbols ({len(report['failed_symbols'])}):")
            for symbol, info in sorted(report["failed_symbols"].items()):
                attempts = info["attempt_count"]
                last_error = (
                    info["last_error"][:60] if info["last_error"] else "unknown"
                )
                log.warning(f"  ✗ {symbol} ({attempts} attempts): {last_error}...")
                if info["corrected_formats"]:
                    log.debug(f"    Suggested formats: {info['corrected_formats'][:3]}")

    def _get_option_quote_from_chain(self, symbol: str) -> Quote | None:
        """Extract option premium from option chain"""
        parsed = self._parse_option_symbol(symbol)
        if not parsed:
            return None

        underlying, expiry_date, strike, option_type = parsed
        chain = self.get_option_chain(underlying, expiry_date)
        return self._extract_option_quote_from_chain(symbol, strike, option_type, chain)

    def _parse_option_symbol(
        self, symbol: str
    ) -> tuple[str, str | None, str, str] | None:
        import re

        symbol = symbol.upper()
        # Strip common exchange suffixes (e.g. .NS, .BO) so parsing works for symbols
        # that include the suffix (NMDC30JUN2674PE.NS)
        if symbol.endswith(".NS") or symbol.endswith(".BO"):
            try:
                symbol = symbol.rsplit(".", 1)[0]
            except Exception:
                pass

        # Try NSE-style symbol with explicit expiry, e.g. NIFTY23JUL24000CE
        match = re.match(r"^([A-Z]+?)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$", symbol)
        if match:
            underlying = match.group(1)
            expiry_token = match.group(2)
            strike = match.group(3)
            option_type = match.group(4)
            expiry_date = self._normalize_option_expiry(expiry_token)
            return underlying, expiry_date, strike, option_type

        match = re.match(r"^([A-Z]+?)(\d+)(CE|PE)$", symbol)
        if match:
            return match.group(1), None, match.group(2), match.group(3)

        return None

    def _normalize_option_expiry(self, token: str) -> str | None:
        import datetime

        token = token.upper()
        month_map = {
            "JAN": 1,
            "FEB": 2,
            "MAR": 3,
            "APR": 4,
            "MAY": 5,
            "JUN": 6,
            "JUL": 7,
            "AUG": 8,
            "SEP": 9,
            "OCT": 10,
            "NOV": 11,
            "DEC": 12,
        }

        if (
            len(token) == 7
            and token[:2].isdigit()
            and token[2:5].isalpha()
            and token[5:].isdigit()
        ):
            day = int(token[:2])
            month = month_map.get(token[2:5])
            year = 2000 + int(token[5:])
            if month:
                try:
                    return datetime.date(year, month, day).isoformat()
                except ValueError:
                    return None

        return None

    def _is_expired_option_symbol(self, symbol: str) -> bool:
        """Return True if symbol is a weekly/monthly option whose expiry has passed (today or earlier).
        Prevents repeated 404s and retry queue spam for delisted contracts on expiry day.
        """
        import re
        from datetime import date, datetime

        symbol = symbol.upper()
        # Full weekly format e.g. NIFTY26MAY2623700PE
        match = re.match(r"^([A-Z]+?)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$", symbol)
        if not match:
            return False

        expiry_token = match.group(2)
        try:
            expiry_date = self._normalize_option_expiry(expiry_token)
            if expiry_date:
                exp_dt = datetime.fromisoformat(expiry_date).date()
                return exp_dt <= date.today()
        except Exception:
            pass
        return False

    def _extract_option_quote_from_chain(
        self,
        symbol: str,
        strike: str,
        option_type: str,
        chain: dict | None,
    ) -> Quote | None:
        if not chain:
            return None

        try:
            data = chain.get("data", {})
            records = data.get("records", {})
            options_list = records.get("data", [])

            log.debug(
                f"Extracting quote from option chain for {symbol}: strike={strike} type={option_type} rows={len(options_list)}"
            )

            # First pass: try exact strike match
            for opt in options_list:
                opt_strike = opt.get("strikePrice")
                if opt_strike is None:
                    opt_strike = opt.get("Strike_Price")
                if opt_strike is None:
                    opt_strike = opt.get("strike")
                if opt_strike is None:
                    continue

                try:
                    # Allow small absolute tolerance for strike matching (float formatting differences)
                    if abs(float(opt_strike) - float(strike)) > 0.5:
                        continue
                except (TypeError, ValueError):
                    continue

                quote = self._get_quote_from_option_data(opt, option_type, symbol)
                if quote:
                    return quote

            # Second pass: find closest strike if exact match failed
            # Try candidate strikes ordered by proximity to target; accept first valid premium
            target_strike = float(strike)
            candidates = []
            for opt in options_list:
                opt_strike = (
                    opt.get("strikePrice")
                    or opt.get("Strike_Price")
                    or opt.get("strike")
                )
                if opt_strike is None:
                    continue
                try:
                    opt_val = float(opt_strike)
                    candidates.append((abs(opt_val - target_strike), opt_val, opt))
                except (TypeError, ValueError):
                    continue

            # Sort by distance and try each until we find a valid quote
            candidates.sort(key=lambda x: x[0])
            for diff, opt_val, opt in candidates:
                log.debug(
                    f"Trying candidate strike {opt_val} (delta={diff}) for {symbol}"
                )
                quote = self._get_quote_from_option_data(opt, option_type, symbol)
                if quote:
                    log.debug(f"Selected strike {opt_val} for {symbol} (delta={diff})")
                    return quote

            if options_list:
                strikes = sorted(
                    {
                        float(
                            opt.get("strikePrice")
                            or opt.get("Strike_Price")
                            or opt.get("strike")
                            or 0
                        )
                        for opt in options_list
                        if opt.get("strikePrice") is not None
                        or opt.get("Strike_Price") is not None
                        or opt.get("strike") is not None
                    }
                )
                log.debug(
                    f"No matching strike found for {symbol}. Available strikes sample: {strikes[:5]}"
                )

        except Exception as e:
            log.debug(f"Error extracting option quote for {symbol}: {e}")

        return None

    def _get_quote_from_option_data(
        self, opt: dict, option_type: str, symbol: str
    ) -> Quote | None:
        """Extract quote from option data dict"""

        # Support both flattened keys (CE_lastPrice) and nested dicts {"CE": {...}}
        def _extract_from_nested(key_root: str):
            nested = opt.get(key_root)
            if isinstance(nested, dict):
                # common keys inside nested dicts
                return (
                    next(
                        (
                            nested.get(key)
                            for key in ["lastPrice", "last_price", "ltp", "last"]
                            if nested.get(key) is not None
                        ),
                        None,
                    ),
                    next(
                        (
                            nested.get(key)
                            for key in ["bidprice", "bidPrice", "bid"]
                            if nested.get(key) is not None
                        ),
                        None,
                    ),
                    next(
                        (
                            nested.get(key)
                            for key in ["askPrice", "askprice", "ask"]
                            if nested.get(key) is not None
                        ),
                        None,
                    ),
                    next(
                        (
                            nested.get(key)
                            for key in ["volume", "totalTradedVolume"]
                            if nested.get(key) is not None
                        ),
                        None,
                    ),
                )
            return (None, None, None, 0)

        def _coalesce(*values):
            for value in values:
                if value is not None:
                    return value
            return None

        if option_type == "CE":
            # flattened keys
            last_price = _coalesce(
                opt.get("CE_lastPrice"),
                opt.get("CE_last_price"),
                opt.get("CE_ltp"),
                opt.get("CALLS_LTP"),
                opt.get("CALLS_ltp"),
            )
            bid_price = _coalesce(
                opt.get("CE_bidprice"),
                opt.get("CE_bidPrice"),
                opt.get("CE_bid"),
                opt.get("CALLS_Bid_Price"),
                opt.get("CALLS_Bidprice"),
                opt.get("CALLS_Bid"),
            )
            ask_price = _coalesce(
                opt.get("CE_askPrice"),
                opt.get("CE_askprice"),
                opt.get("CE_ask"),
                opt.get("CALLS_Ask_Price"),
                opt.get("CALLS_Askprice"),
                opt.get("CALLS_Ask"),
            )
            volume = _coalesce(opt.get("CE_totalTradedVolume"), opt.get("CALLS_Volume"))
            # nested dict fallback
            if last_price is None:
                lp, bp, ap, vol = _extract_from_nested("CE")
                last_price = _coalesce(last_price, lp)
                bid_price = _coalesce(bid_price, bp)
                ask_price = _coalesce(ask_price, ap)
                volume = _coalesce(volume, vol)
        else:
            last_price = _coalesce(
                opt.get("PE_lastPrice"),
                opt.get("PE_last_price"),
                opt.get("PE_ltp"),
                opt.get("PUTS_LTP"),
                opt.get("PUTS_ltp"),
            )
            bid_price = _coalesce(
                opt.get("PE_bidprice"),
                opt.get("PE_bidPrice"),
                opt.get("PE_bid"),
                opt.get("PUTS_Bid_Price"),
                opt.get("PUTS_Bidprice"),
                opt.get("PUTS_Bid"),
            )
            ask_price = _coalesce(
                opt.get("PE_askPrice"),
                opt.get("PE_askprice"),
                opt.get("PE_ask"),
                opt.get("PUTS_Ask_Price"),
                opt.get("PUTS_Askprice"),
                opt.get("PUTS_Ask"),
            )
            volume = _coalesce(opt.get("PE_totalTradedVolume"), opt.get("PUTS_Volume"))
            if last_price is None:
                lp, bp, ap, vol = _extract_from_nested("PE")
                last_price = _coalesce(last_price, lp)
                bid_price = _coalesce(bid_price, bp)
                ask_price = _coalesce(ask_price, ap)
                volume = _coalesce(volume, vol)

        if last_price is None:
            log.debug(
                f"No price fields found for {symbol} {option_type} row: bid={bid_price} ask={ask_price} volume={volume}"
            )
            return None

        import math

        try:
            last_price_value = float(last_price)
        except (TypeError, ValueError):
            return None

        if math.isnan(last_price_value) or last_price_value <= 0:
            last_price_value = None
            for fallback_price in (ask_price, bid_price):
                try:
                    candidate = float(fallback_price)
                    if candidate > 0 and not math.isnan(candidate):
                        last_price_value = candidate
                        break
                except (TypeError, ValueError):
                    continue

            if last_price_value is None:
                log.debug(
                    f"No valid option premium for {symbol} {option_type}: last_price={last_price} bid={bid_price} ask={ask_price}"
                )
                return None

        bid_value = 0.0
        ask_value = 0.0
        try:
            if bid_price is not None:
                bid_value = float(bid_price)
            if ask_price is not None:
                ask_value = float(ask_price)
        except (TypeError, ValueError):
            pass

        if math.isnan(bid_value):
            bid_value = 0.0
        if math.isnan(ask_value):
            ask_value = 0.0

        try:
            volume_value = int(volume) if volume is not None else 0
        except (TypeError, ValueError):
            volume_value = 0

        return Quote(
            symbol=symbol,
            last_price=last_price_value,
            volume=volume_value,
            bid=bid_value,
            ask=ask_value,
        )

    def _get_option_quote_from_mcp(
        self,
        symbol: str,
        underlying: str,
        strike: str,
        option_type: str,
        expiry_date: str | None = None,
    ) -> Quote | None:
        return None

    def _extract_quote_from_nested_mcp_data(
        self, symbol: str, strike: str, option_type: str, data: list
    ) -> Quote | None:
        """Extract quote from nested MCP data format (TradingView style)"""

        def _get_row_strike(r):
            for k in ("strikePrice", "strike", "Strike_Price", "StrikePrice"):
                v = r.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        try:
                            return float(str(v).replace(",", ""))
                        except Exception:
                            continue
            return None

        def _get_side_price(r, opt_type):
            prefix = f"{opt_type}_"
            keys = [
                f"{prefix}lastPrice",
                f"{prefix}last_price",
                f"{prefix}ltp",
                f"{opt_type}LTP",
                f"{opt_type}_LTP",
            ]
            for k in keys:
                if k in r and r.get(k) is not None:
                    return r.get(k)
            # generic fallback
            for k in ("lastPrice", "last_price", "ltp", "LTP"):
                if k in r and r.get(k) is not None:
                    return r.get(k)
            return None

        target_strike = None
        try:
            target_strike = float(strike)
        except Exception:
            try:
                target_strike = float(str(strike).replace(",", ""))
            except Exception:
                target_strike = None

        for row in data:
            opt_strike = _get_row_strike(row)
            if opt_strike is None or target_strike is None:
                continue
            # Compare as floats to handle decimal differences
            if abs(opt_strike - target_strike) > 0.5:
                continue

            last_price = _get_side_price(row, option_type)
            bid_price = None
            ask_price = None
            # try bid/ask variants
            for bkey in (
                f"{option_type}_bidprice",
                f"{option_type}bid",
                f"{option_type}_bid",
                "bidprice",
                "bidPrice",
                "bid",
            ):
                if bkey in row and row.get(bkey) is not None:
                    bid_price = row.get(bkey)
                    break
            for akey in (
                f"{option_type}_askPrice",
                f"{option_type}ask",
                "askPrice",
                "ask",
            ):
                if akey in row and row.get(akey) is not None:
                    ask_price = row.get(akey)
                    break
            volume = (
                row.get(f"{option_type}_totalTradedVolume")
                or row.get("totalTradedVolume")
                or 0
            )

            if last_price is None:
                continue

            import math

            try:
                last_price_value = float(last_price)
            except (TypeError, ValueError):
                try:
                    last_price_value = float(str(last_price).replace(",", ""))
                except Exception:
                    continue

            if math.isnan(last_price_value) or last_price_value <= 0:
                last_price_value = None
                for fallback_price in (ask_price, bid_price):
                    try:
                        candidate = float(fallback_price)
                        if candidate > 0 and not math.isnan(candidate):
                            last_price_value = candidate
                            break
                    except (TypeError, ValueError):
                        continue
                if last_price_value is None:
                    continue

            bid_value = 0.0
            ask_value = 0.0
            try:
                if bid_price is not None and bid_price != 0:
                    bid_value = float(bid_price)
                if ask_price is not None and ask_price != 0:
                    ask_value = float(ask_price)
            except (TypeError, ValueError):
                pass

            if math.isnan(bid_value):
                bid_value = 0.0
            if math.isnan(ask_value):
                ask_value = 0.0

            try:
                volume_value = int(volume or 0)
            except (TypeError, ValueError):
                volume_value = 0

            return Quote(
                symbol=symbol,
                last_price=last_price_value,
                volume=volume_value,
                bid=bid_value,
                ask=ask_value,
            )

        return None

    def _extract_quote_from_flat_nsekit_data(
        self, symbol: str, strike: str, option_type: str, data: list
    ) -> Quote | None:
        """Extract quote from flat NSEKit data format"""

        def _row_strike(r):
            for k in ("Strike_Price", "StrikePrice", "strikePrice", "strike"):
                v = r.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        try:
                            return float(str(v).replace(",", ""))
                        except Exception:
                            continue
            return None

        def _row_side_fields(r, opt_type):
            # common variants
            if opt_type == "CE":
                return (
                    r.get("CALLS_LTP")
                    or r.get("CE_lastPrice")
                    or r.get("CE_last")
                    or r.get("CE_ltp"),
                    r.get("CALLS_Bid_Price") or r.get("CE_bidprice") or r.get("CE_bid"),
                    r.get("CALLS_Ask_Price") or r.get("CE_askPrice") or r.get("CE_ask"),
                    r.get("CALLS_Volume")
                    or r.get("CE_totalTradedVolume")
                    or r.get("CE_volume")
                    or 0,
                )
            else:
                return (
                    r.get("PUTS_LTP")
                    or r.get("PE_lastPrice")
                    or r.get("PE_last")
                    or r.get("PE_ltp"),
                    r.get("PUTS_Bid_Price") or r.get("PE_bidprice") or r.get("PE_bid"),
                    r.get("PUTS_Ask_Price") or r.get("PE_askPrice") or r.get("PE_ask"),
                    r.get("PUTS_Volume")
                    or r.get("PE_totalTradedVolume")
                    or r.get("PE_volume")
                    or 0,
                )

        target_strike = None
        try:
            target_strike = float(strike)
        except Exception:
            try:
                target_strike = float(str(strike).replace(",", ""))
            except Exception:
                target_strike = None

        for row in data:
            opt_strike = _row_strike(row)
            if opt_strike is None or target_strike is None:
                continue
            # allow some tolerance for integer/float representation differences
            if abs(opt_strike - target_strike) > 0.5:
                continue

            last_price, bid_price, ask_price, volume = _row_side_fields(
                row, option_type
            )

            if last_price is None:
                continue

            import math

            try:
                last_price_value = float(last_price)
            except (TypeError, ValueError):
                try:
                    last_price_value = float(str(last_price).replace(",", ""))
                except Exception:
                    continue

            if math.isnan(last_price_value) or last_price_value <= 0:
                last_price_value = None
                for fallback_price in (ask_price, bid_price):
                    try:
                        candidate = float(fallback_price)
                        if candidate > 0 and not math.isnan(candidate):
                            last_price_value = candidate
                            break
                    except (TypeError, ValueError):
                        continue
                if last_price_value is None:
                    continue

            bid_value = 0.0
            ask_value = 0.0
            try:
                if bid_price is not None and bid_price != 0:
                    bid_value = float(bid_price)
                if ask_price is not None and ask_price != 0:
                    ask_value = float(ask_price)
            except (TypeError, ValueError):
                pass

            if math.isnan(bid_value):
                bid_value = 0.0
            if math.isnan(ask_value):
                ask_value = 0.0

            try:
                volume_value = int(volume or 0)
            except (TypeError, ValueError):
                volume_value = 0

            return Quote(
                symbol=symbol,
                last_price=last_price_value,
                volume=volume_value,
                bid=bid_value,
                ask=ask_value,
            )

        return None

        log.warning(
            f"No matching MCP option quote found for {symbol} on expiry {expiry_date or 'latest'}"
        )
        return None

    _nse_session = None
    _nse_session_created = 0.0
    _nse_session_ttl = 1500  # seconds before NSE cookies/session refresh
    _option_chain_last_fetch = {}
    _option_chain_cache = {}
    _option_chain_rate_limit = 60  # seconds between fetches for same symbol
    _nse_api = None

    def _option_chain_cache_key(self, symbol: str, expiry: str = None) -> str:
        key = symbol.upper()
        if expiry:
            key = f"{key}:{expiry}"
        return key

    def _get_cached_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        return NSELiveBroker._option_chain_cache.get(
            self._option_chain_cache_key(symbol, expiry)
        )

    def _set_cached_option_chain(self, symbol: str, expiry: str, chain: dict) -> None:
        NSELiveBroker._option_chain_cache[
            self._option_chain_cache_key(symbol, expiry)
        ] = chain

    def get_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        """Get options chain using NSEEnrichmentProvider with safe fallback."""

        if expiry is None:
            expiry = self._get_current_weekly_expiry()
            log.debug(f"Using current weekly expiry {expiry} for {symbol}")

        expiry = self._normalize_api_expiry(expiry)
        cache_key = self._option_chain_cache_key(symbol, expiry)
        now = datetime.now().timestamp()
        last_fetch = self._option_chain_last_fetch.get(cache_key, 0)

        if now - last_fetch < self._option_chain_rate_limit:
            log.debug(f"Rate limiting option chain fetch for {symbol} expiry={expiry}")
            cached_chain = self._get_cached_option_chain(symbol, expiry)
            if cached_chain:
                return cached_chain

        result = self._fetch_option_chain_nse_enrichment(symbol, expiry)
        if result:
            NSELiveBroker._option_chain_last_fetch[cache_key] = now
            self._set_cached_option_chain(symbol, expiry, result)
            log.info(f"Option chain for {symbol} fetched via NSEEnrichmentProvider")
            return result

        fallback_chain = self._fetch_fallback_option_chain(symbol)
        if fallback_chain and self._chain_has_valid_records(fallback_chain):
            NSELiveBroker._option_chain_last_fetch[cache_key] = now
            self._set_cached_option_chain(symbol, expiry, fallback_chain)
            log.info(f"Option chain for {symbol} fetched via safe fallback path")
            return fallback_chain

        return None

    def _get_current_weekly_expiry(self) -> str:
        """Get current weekly expiry date in DDMMMYY format for provider APIs."""
        from datetime import datetime

        now = datetime.now()
        current_weekday = now.weekday()
        days_to_thursday = (3 - current_weekday) % 7
        if days_to_thursday == 0:
            days_to_thursday = 7

        next_thursday = now + timedelta(days=days_to_thursday)
        # Return API-friendly DDMMMYY (e.g., 30JUN26)
        return next_thursday.strftime("%d%b%y").upper()

    def _normalize_api_expiry(self, expiry: str) -> str:
        """Normalize expiry strings to DDMMMYY (e.g., 30JUN26)."""
        if not expiry:
            return expiry
        cleaned = expiry.strip().upper()
        cleaned = cleaned.replace("/", "-").replace(" ", "")
        # DDMMMYY / DD-MMM-YYYY / DDMMMYYYY
        if (
            len(cleaned) == 7
            and cleaned[:2].isdigit()
            and cleaned[-3:].isalpha()
            and cleaned[2:5].isalpha()
        ):
            return cleaned[:2] + cleaned[2:5].upper() + cleaned[5:].upper()
        if len(cleaned) == 9 and cleaned[:2].isdigit() and cleaned[5:8].isalpha():
            day = cleaned[:2]
            mon = cleaned[5:8].upper()
            year = cleaned[-2:]
            return day + mon + year
        if len(cleaned) == 11 and cleaned[:2].isdigit() and cleaned[5:8].isalpha():
            day = cleaned[:2]
            mon = cleaned[5:8].upper()
            year = cleaned[-2:]
            return day + mon + year
        try:
            dt = datetime.strptime(cleaned, "%d%b%y")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%d%b%Y")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%Y-%m-%d")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%Y%m%d")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        return expiry

    def _create_nse_session(self) -> "requests.Session":
        import time

        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Referer": "https://www.nseindia.com/",
            }
        )
        retry = Retry(
            total=3, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.trust_env = False

        try:
            session.get("https://www.nseindia.com/", timeout=15)
        except Exception:
            pass

        NSELiveBroker._nse_session = session
        NSELiveBroker._nse_session_created = time.time()
        return session

    def _get_nse_session(self) -> "requests.Session":
        import time

        if NSELiveBroker._nse_session is None:
            return self._create_nse_session()

        now = time.time()
        if now - NSELiveBroker._nse_session_created > NSELiveBroker._nse_session_ttl:
            return self._create_nse_session()

        return NSELiveBroker._nse_session

    def _reset_nse_session(self) -> "requests.Session":
        try:
            if NSELiveBroker._nse_session is not None:
                NSELiveBroker._nse_session.close()
        except Exception:
            pass

        NSELiveBroker._nse_session = None
        return self._create_nse_session()

    _depth_session_last_fetch = {}
    _depth_session_rate_limit = 1500  # 25 minutes - NSE cookie expiration

    def get_orderbook(self, symbol: str) -> dict | None:
        """
        Get 5-level orderbook from NSE public API (no auth needed).

        Returns dict with:
        - bid_ask_spread: float
        - volume_imbalance: float (-1 to 1)
        - buy_levels: list of {price, quantity}
        - sell_levels: list of {price, quantity}
        """
        import json
        import time

        import requests

        now = time.time()
        last_fetch = NSELiveBroker._depth_session_last_fetch.get(symbol, 0)

        if now - last_fetch >= NSELiveBroker._depth_session_rate_limit:
            self._reset_nse_session()
            NSELiveBroker._depth_session_last_fetch = {}

        base = "https://nseindia.com"

        try:
            if NSELiveBroker._depth_session_last_fetch is None:
                NSELiveBroker._depth_session_last_fetch = {}

            session = self._get_nse_session()

            url = f"{base}/api/quote-equity?symbol={symbol}"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": base,
                "X-Requested-With": "XMLHttpRequest",
            }

            resp = session.get(url, headers=headers, timeout=5)

            if resp.status_code != 200:
                return self._estimate_orderbook_from_quote(symbol)

            data = resp.json()
            market_depth = (
                data.get("marketDepthandTrade") or data.get("marketDepth") or {}
            )

            if not market_depth:
                return self._estimate_orderbook_from_quote(symbol)

            buy_levels = []
            sell_levels = []

            buy_arr = market_depth.get("buy", [])
            sell_arr = market_depth.get("sell", [])

            for i in range(min(5, len(buy_arr))):
                buy_levels.append(
                    {
                        "price": buy_arr[i].get("price", 0),
                        "quantity": buy_arr[i].get("quantity", 0),
                        "orders": buy_arr[i].get("orders", 0),
                    }
                )

            for i in range(min(5, len(sell_arr))):
                sell_levels.append(
                    {
                        "price": sell_arr[i].get("price", 0),
                        "quantity": sell_arr[i].get("quantity", 0),
                        "orders": sell_arr[i].get("orders", 0),
                    }
                )

            total_bid_qty = sum(l["quantity"] for l in buy_levels)
            total_ask_qty = sum(l["quantity"] for l in sell_levels)

            total_bid = sum(l["quantity"] for l in buy_levels)
            total_ask = sum(l["quantity"] for l in sell_levels)
            volume_imbalance = (total_bid - total_ask) / (total_bid + total_ask + 1e-10)

            spread = (
                (sell_levels[0]["price"] - buy_levels[0]["price"])
                if buy_levels and sell_levels
                else 0
            )
            mid = (sell_levels[0]["price"] + buy_levels[0]["price"]) / 2
            spread_pct = (spread / mid * 100) if mid > 0 else 0

            NSELiveBroker._depth_session_last_fetch[symbol] = now

            return {
                "bid_ask_spread": round(spread, 2),
                "spread_pct": round(spread_pct, 4),
                "volume_imbalance": round(volume_imbalance, 4),
                "total_buy_qty": total_bid_qty,
                "total_sell_qty": total_ask_qty,
                "buy_levels": buy_levels,
                "sell_levels": sell_levels,
                "timestamp": now,
            }

        except Exception as e:
            log.debug(f"Orderbook fetch failed for {symbol}: {e}")
            return self._estimate_orderbook_from_quote(symbol)

    def _estimate_orderbook_from_quote(self, symbol: str) -> dict:
        """Fallback: estimate orderbook from quote's bid/ask when API unavailable"""
        quote = self.get_quote(symbol)
        if not quote:
            return {}

        bid = quote.bid or 0
        ask = quote.ask or 0
        bid_qty = quote.bid_quantity or 0
        ask_qty = quote.ask_quantity or 0

        spread = ask - bid
        mid = (ask + bid) / 2
        spread_pct = (spread / mid * 100) if mid > 0 else 0
        volume_imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty + 1e-10)

        return {
            "bid_ask_spread": round(spread, 2),
            "spread_pct": round(spread_pct, 4),
            "volume_imbalance": round(volume_imbalance, 4),
            "total_buy_qty": bid_qty,
            "total_sell_qty": ask_qty,
            "buy_levels": [{"price": bid, "quantity": bid_qty, "orders": 1}],
            "sell_levels": [{"price": ask, "quantity": ask_qty, "orders": 1}],
            "estimated": True,
        }

    # Reusable event loop for NSEKit MCP to avoid creation overhead
    _mcp_event_loop = None
    _mcp_loop_lock = None
    _mcp_loop_thread = None

    # Reusable event loop + aiohttp helpers for non-blocking HTTP fetches
    _http_event_loop = None
    _http_loop_thread = None
    _http_loop_lock = None
    _aiohttp_available = None

    @classmethod
    def _get_mcp_event_loop(cls):
        """Get or create a reusable event loop for NSEKit MCP."""
        if cls._mcp_loop_lock is None:
            import threading

            cls._mcp_loop_lock = threading.Lock()

        with cls._mcp_loop_lock:
            if cls._mcp_event_loop is None or cls._mcp_event_loop.is_closed():
                import asyncio
                import threading

                def _run_loop(loop):
                    try:
                        asyncio.set_event_loop(loop)
                        loop.run_forever()
                    except Exception:
                        pass

                try:
                    cls._mcp_event_loop = asyncio.new_event_loop()
                    cls._mcp_loop_thread = threading.Thread(
                        target=_run_loop, args=(cls._mcp_event_loop,), daemon=True
                    )
                    cls._mcp_loop_thread.start()
                except Exception as e:
                    log.debug(f"Failed to create reusable event loop: {e}")
                    return None
            return cls._mcp_event_loop

    @classmethod
    def _ensure_http_loop(cls):
        """Ensure a background asyncio event loop exists for aiohttp calls."""
        if cls._http_loop_lock is None:
            import threading

            cls._http_loop_lock = threading.Lock()

        with cls._http_loop_lock:
            if cls._http_event_loop is None or cls._http_event_loop.is_closed():
                import asyncio
                import threading

                def _run_loop(loop):
                    try:
                        asyncio.set_event_loop(loop)
                        loop.run_forever()
                    except Exception:
                        pass

                loop = asyncio.new_event_loop()
                t = threading.Thread(target=_run_loop, args=(loop,), daemon=True)
                t.start()
                cls._http_event_loop = loop
                cls._http_loop_thread = t
            return cls._http_event_loop

    def _run_async_coroutine(self, coro, timeout: float = 5.0):
        """Run a coroutine in the dedicated HTTP event loop and return result."""
        loop = self.__class__._ensure_http_loop()
        try:
            fut = __import__("asyncio").run_coroutine_threadsafe(coro, loop)
            return fut.result(timeout=timeout)
        except Exception as e:
            log.debug(f"Async HTTP fetch failed or timed out: {e}")
            return None

    async def _aio_fetch_text(
        self, url: str, timeout: float = 5.0, headers: dict = None
    ):
        try:
            import aiohttp
        except Exception:
            return None

        try:
            timeout_conf = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession() as session, session.get(
                url, timeout=timeout_conf, headers=headers
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except Exception:
            return None

    def _fetch_text_async(self, url: str, timeout: float = 5.0, headers: dict = None):
        """Synchronous wrapper to fetch URL text using aiohttp in background loop."""
        try:
            return self._run_async_coroutine(
                self._aio_fetch_text(url, timeout, headers), timeout=timeout + 1
            )
        except Exception:
            return None

    def _get_nsekit_mcp_quote(self, symbol: str) -> Quote | None:
        return None

    def _fetch_option_chain_nsekit_mcp(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        return None

        if not self._is_source_enabled("mcp"):
            status = self._source_status.get("mcp", {})
            disabled_ts = status.get("disabled_until")
            if disabled_ts:
                from datetime import datetime

                ts_str = datetime.fromtimestamp(disabled_ts).isoformat()
            else:
                ts_str = str(disabled_ts)
            log.debug(
                f"Skipping NSEKit MCP option chain for {symbol}: MCP disabled until {ts_str}"
            )

        log.info(
            f"Attempting NSEKit MCP option chain fetch for {symbol} expiry={expiry or 'latest'}"
        )
        self._mcp_lock.acquire()
        try:
            import asyncio

            from nsekit_mcp.server import fno_live_option_chain

            lookup_symbol = symbol.upper()
            if lookup_symbol in ["NIFTY", "NIFTY50"]:
                lookup_symbol = "NIFTY"
            elif lookup_symbol in {"NIFTY BANK", "BANKNIFTY"}:
                lookup_symbol = "BANKNIFTY"

            async def fetch_chain(expiry_value):
                return await asyncio.wait_for(
                    fno_live_option_chain(symbol=lookup_symbol, expiry=expiry_value),
                    timeout=15,
                )

            chain_data = None
            for expiry_value in [expiry, None]:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    chain_data = loop.run_until_complete(fetch_chain(expiry_value))
                    loop.close()
                    if chain_data:
                        break
                except Exception as e:
                    log.debug(
                        f"NSEKit MCP option chain fetch failed for {symbol} expiry={expiry_value}: {e}"
                    )
                    continue

            if not chain_data:
                self._record_source_failure("mcp", f"option chain no data for {symbol}")
                return None

            raw_chain = []
            if isinstance(chain_data, list):
                raw_chain = chain_data
            elif isinstance(chain_data, dict):
                if isinstance(chain_data.get("data"), list):
                    raw_chain = chain_data["data"]
                elif isinstance(chain_data.get("records", {}).get("data"), list):
                    raw_chain = chain_data["records"]["data"]

            if not raw_chain:
                log.debug(
                    f"NSEKit MCP option chain returned empty raw_chain for {symbol}"
                )
                return None

            # Convert NSEKit MCP format to NSE API format
            records_list = []
            expiry_dates = set()

            for item in raw_chain:
                if not isinstance(item, dict):
                    continue

                strike = item.get(
                    "Strike_Price", item.get("StrikePrice", item.get("strikePrice", 0))
                )
                expiry_date = item.get(
                    "Expiry_Date", item.get("ExpiryDate", item.get("expiryDate", ""))
                )
                if strike is None or expiry_date is None:
                    log.debug(
                        f"MCP option row missing strike/expiry for {symbol}: {item}"
                    )
                    continue

                expiry_dates.add(expiry_date)

                record = {
                    "strikePrice": float(strike),
                    "expiryDate": expiry_date,
                    "CE": {
                        "lastPrice": (
                            float(item.get("CALLS_LTP", item.get("CallLTP", 0)))
                            if item.get("CALLS_LTP", item.get("CallLTP", 0))
                            else 0
                        ),
                        "bidprice": (
                            float(
                                item.get("CALLS_Bid_Price", item.get("CallBidPrice", 0))
                            )
                            if item.get("CALLS_Bid_Price", item.get("CallBidPrice", 0))
                            else 0
                        ),
                        "askPrice": (
                            float(
                                item.get("CALLS_Ask_Price", item.get("CallAskPrice", 0))
                            )
                            if item.get("CALLS_Ask_Price", item.get("CallAskPrice", 0))
                            else 0
                        ),
                        "volume": (
                            int(item.get("CALLS_Volume", item.get("CallVolume", 0)))
                            if item.get("CALLS_Volume", item.get("CallVolume", 0))
                            else 0
                        ),
                        "openInterest": (
                            int(item.get("CALLS_OI", item.get("CallOI", 0)))
                            if item.get("CALLS_OI", item.get("CallOI", 0))
                            else 0
                        ),
                    },
                    "PE": {
                        "lastPrice": (
                            float(item.get("PUTS_LTP", item.get("PutLTP", 0)))
                            if item.get("PUTS_LTP", item.get("PutLTP", 0))
                            else 0
                        ),
                        "bidprice": (
                            float(
                                item.get("PUTS_Bid_Price", item.get("PutBidPrice", 0))
                            )
                            if item.get("PUTS_Bid_Price", item.get("PutBidPrice", 0))
                            else 0
                        ),
                        "askPrice": (
                            float(
                                item.get("PUTS_Ask_Price", item.get("PutAskPrice", 0))
                            )
                            if item.get("PUTS_Ask_Price", item.get("PutAskPrice", 0))
                            else 0
                        ),
                        "volume": (
                            int(item.get("PUTS_Volume", item.get("PutVolume", 0)))
                            if item.get("PUTS_Volume", item.get("PutVolume", 0))
                            else 0
                        ),
                        "openInterest": (
                            int(item.get("PUTS_OI", item.get("PutOI", 0)))
                            if item.get("PUTS_OI", item.get("PutOI", 0))
                            else 0
                        ),
                    },
                }
                records_list.append(record)

            # Format as NSE API response
            result = {
                "data": {
                    "records": {
                        "data": records_list,
                        "expiryDates": sorted(list(expiry_dates)),
                    }
                },
                "symbol": symbol,
                "is_index": True,
                "source": "nsekit_mcp",
            }

            log.debug(
                f"NSEKit MCP fetched option chain for {symbol} with {len(records_list)} strikes, expiries={sorted(list(expiry_dates))}"
            )
            return result

        except Exception as e:
            log.debug(f"NSEKit MCP option chain failed for {symbol}: {e}")
            self._record_source_failure("mcp", f"option chain failed: {e}")
        finally:
            self._mcp_lock.release()

        return None

    def _fetch_option_chain_nsekit(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        """Fetch option chain using NseKit library (primary source)"""
        try:
            from NseKit.NseKit import Nse

            nse = Nse()

            lookup_symbol = symbol.upper()
            if lookup_symbol == "NIFTY50":
                lookup_symbol = "NIFTY"
            elif lookup_symbol == "NIFTY BANK":
                lookup_symbol = "BANKNIFTY"

            log.debug(
                f"Attempting NseKit option chain fetch for {symbol} (lookup={lookup_symbol})"
            )
            df = nse.fno_live_option_chain(lookup_symbol)

            if df is None or df.empty:
                log.debug(f"NseKit returned empty option chain for {symbol}")
                return None

            # Filter by expiry if specified
            if expiry:
                df = df[df["Expiry_Date"] == expiry]
                if df.empty:
                    log.debug(
                        f"NseKit: No data for expiry {expiry}, available expiries: {df['Expiry_Date'].unique()}"
                    )
                    return None

            # Convert DataFrame to NSE API format for compatibility
            records_list = []
            for _, row in df.iterrows():
                record = {
                    "strikePrice": row["Strike_Price"],
                    "CE_lastPrice": row["CALLS_LTP"],
                    "CE_bidprice": row["CALLS_Bid_Price"],
                    "CE_askPrice": row["CALLS_Ask_Price"],
                    "CE_totalTradedVolume": (
                        int(row["CALLS_Volume"]) if row["CALLS_Volume"] else 0
                    ),
                    "PE_lastPrice": row["PUTS_LTP"],
                    "PE_bidprice": row["PUTS_Bid_Price"],
                    "PE_askPrice": row["PUTS_Ask_Price"],
                    "PE_totalTradedVolume": (
                        int(row["PUTS_Volume"]) if row["PUTS_Volume"] else 0
                    ),
                }
                records_list.append(record)

            # Extract and sort expiry dates
            expiry_dates = []
            if "Expiry_Date" in df.columns:
                unique_expiries = df["Expiry_Date"].dropna().unique()
                # Sort expiry dates chronologically
                expiry_dates = sorted([str(date) for date in unique_expiries])

            # Format as NSE API response for compatibility
            result = {
                "data": {
                    "records": {"data": records_list, "expiryDates": expiry_dates}
                },
                "symbol": symbol,
                "is_index": True,
                "source": "nsekit",
            }

            log.info(
                f"NseKit: Fetched option chain for {symbol} with {len(records_list)} strikes"
            )
            return result

        except ImportError:
            log.warning("NseKit not available, skipping NseKit option chain fetch")
            return None
        except Exception as e:
            log.debug(f"NseKit option chain failed for {symbol}: {e}")
            return None

    def _fetch_option_chain_nseapi(self, symbol: str) -> dict | None:
        """Fetch option chain using NSEIndiaApi library with short timeout"""
        import concurrent.futures

        if symbol.upper() in {"NIFTYNEXT50", "FINNIFTY"}:
            log.debug(
                f"Skipping NSEIndiaApi option chain for {symbol} (no options available)"
            )
            return None

        def _nseapi_call():
            nse_api = NSELiveBroker._nse_api
            if nse_api is None:
                from nse import NSE

                nse_api = NSE(download_folder="", server=False)
                NSELiveBroker._nse_api = nse_api
            return nse_api.optionChain(symbol)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_nseapi_call)
                result = future.result(timeout=6)

            if (
                result
                and isinstance(result, dict)
                and ("records" in result or "filtered" in result)
            ):
                log.debug(f"Fetched option chain for {symbol} via NSEIndiaApi")
                return {
                    "data": result,
                    "symbol": symbol,
                    "is_index": True,
                    "source": "nseindiaapi",
                }
        except concurrent.futures.TimeoutError:
            log.debug(f"NSEIndiaApi timeout for {symbol} after 6s")
        except Exception as e:
            log.debug(f"NSEIndiaApi option chain failed for {symbol}: {e}")
        return None

    def _fetch_fallback_option_chain(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        """Fallback chain fetch when primary path returns None."""
        try:
            fallback = self._fetch_option_chain_nseapi(symbol)
            if fallback:
                return fallback
        except Exception:
            pass
        try:
            fallback = self._fetch_option_chain_direct(symbol)
            if fallback:
                return fallback
        except Exception:
            pass
        try:
            fallback = self._fetch_option_chain_scrape(symbol)
            if fallback:
                return fallback
        except Exception:
            pass
        try:
            fallback = self._fetch_option_chain_third_party(symbol)
            if fallback:
                return fallback
        except Exception:
            pass
        return None

    @staticmethod
    def _chain_has_valid_records(chain: dict) -> bool:
        if not isinstance(chain, dict):
            return False
        data = chain.get("data")
        if isinstance(data, list) and data:
            return True
        if isinstance(data, dict):
            for key in ("records", "filtered"):
                container = data.get(key)
                if isinstance(container, dict):
                    options = container.get("data")
                    if isinstance(options, list) and options:
                        return True
        return False

    def _fetch_option_chain_nse_enrichment(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        """Fetch option chain using NSEEnrichmentProvider as fallback"""
        try:
            from sources.nse_enrichment import nse_enrichment

            result = nse_enrichment.get_option_chain(symbol, expiry)
            if result and result.get("data", {}).get("records", {}).get("data"):
                log.debug(
                    f"NSEEnrichmentProvider fetched option chain for {symbol} with {len(result['data']['records']['data'])} records"
                )
                return result
        except Exception as e:
            log.warning(f"NSEEnrichmentProvider option chain failed for {symbol}: {e}")
        return None

    def _fetch_option_chain_direct(self, symbol: str) -> dict | None:
        """Fetch option chain via direct NSE API (fallback)"""
        import time

        import requests

        # Only fetch options for indices that actually have options
        # Expanded mapping to include additional index symbols that have option chains.
        index_symbols = {
            "NIFTY": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "FINNIFTY": "NIFTY FIN SERVICE",
            "SENSEX": "SENSEX",
            "BANKEX": "BANKEX",
            "NIFTY50": "NIFTY 50",
        }
        has_options = symbol.upper() in index_symbols
        is_index = has_options

        # Skip option chain fetch for indices without options
        if symbol.upper() in {"NIFTYNEXT50", "FINNIFTY"}:
            log.debug(f"Skipping option chain for {symbol} (no options available)")
            return None

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                session = self._get_nse_session()

                if attempt == 0:
                    session.get("https://www.nseindia.com/", timeout=10)
                    time.sleep(0.8)

                if is_index:
                    underlying = index_symbols.get(symbol.upper(), symbol.upper())
                    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={underlying.replace(' ', '%20')}"
                else:
                    url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol.upper()}"

                headers = {
                    "Referer": (
                        "https://www.nseindia.com/option-chain"
                        if is_index
                        else "https://www.nseindia.com/option-chain/equities"
                    ),
                    "Host": "www.nseindia.com",
                }

                response = session.get(url, headers=headers, timeout=12)

                if response.status_code == 200:
                    data = response.json()
                    records = data.get("records", {})
                    options_data = records.get("data", []) if records else []

                    log.debug(
                        f"Fetched option chain for {symbol}: {len(options_data)} strikes (index={is_index})"
                    )
                    return {
                        "data": data,
                        "symbol": symbol,
                        "is_index": is_index,
                        "source": "direct",
                    }
                elif response.status_code == 403:
                    log.warning(f"NSE blocked request for {symbol}, rotating session")
                    self._reset_nse_session()
                    time.sleep(2)
                elif response.status_code == 429:
                    log.warning(f"NSE rate limited for {symbol}, waiting 8s")
                    time.sleep(8)
                elif response.status_code == 404:
                    log.debug(
                        f"Option chain API returned 404 for {symbol}, symbol may not have F&O listing"
                    )
                    return None
                else:
                    log.debug(
                        f"Option chain API returned {response.status_code} for {symbol}"
                    )
            except requests.exceptions.Timeout:
                log.debug(
                    f"Option chain timeout for {symbol}, attempt {attempt + 1}/{max_attempts}"
                )
                time.sleep(1)
            except requests.exceptions.ConnectionError:
                log.debug(f"Connection error for {symbol}, resetting session")
                self._reset_nse_session()
                time.sleep(1)
            except Exception as e:
                log.debug(
                    f"Option chain attempt {attempt + 1} failed for {symbol}: {e}"
                )

            if attempt < max_attempts - 1:
                time.sleep(1 + attempt * 2)

        log.debug(f"Direct NSE API failed for {symbol}, trying scrape method")
        return self._fetch_option_chain_scrape(symbol)

    def _fetch_option_chain_scrape(self, symbol: str) -> dict | None:
        """Scrape option chain from NSE or alternative sources"""
        import time

        import requests

        # Expanded mapping for scraping fallback to include additional indices.
        index_symbols = {
            "NIFTY": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "FINNIFTY": "NIFTY FIN SERVICE",
            "SENSEX": "SENSEX",
            "BANKEX": "BANKEX",
            "NIFTY50": "NIFTY 50",
        }
        is_index = symbol.upper() in index_symbols

        try:
            # Try NSE live market page (scrape)
            if is_index:
                underlying = index_symbols.get(symbol.upper(), symbol.upper()).replace(
                    " ", "%20"
                )
                url = f"https://www.nseindia.com/live_market/dumpProxy/optionChainDetailsByUnderlying?formAction=viewDetails&underlying={underlying}&instrumentType=IDX&strike=&date="
            else:
                url = f"https://www.nseindia.com/live_market/dumpProxy/optionChainDetailsByUnderlying?formAction=viewDetails&underlying={symbol.upper()}&instrumentType=OPTSTK&strike=&date="

            session = self._get_nse_session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/",
            }

            response = session.get(url, headers=headers, timeout=12)

            if response.status_code == 200:
                try:
                    data = response.json()
                    if data:
                        log.info(f"Scraped option chain for {symbol}: data received")
                        return {
                            "data": data,
                            "symbol": symbol,
                            "is_index": is_index,
                            "source": "scrape",
                        }
                except Exception:
                    pass
        except requests.exceptions.Timeout:
            log.debug(f"NSE scrape timeout for {symbol}")
        except Exception as e:
            log.debug(f"NSE scrape failed for {symbol}: {e}")

        log.debug(f"All scraping attempts failed for option chain {symbol}")
        return None

    def _fetch_option_chain_third_party(self, symbol: str) -> dict | None:
        """Fetch stock option chain from third-party providers like Sensibull and Opstra."""
        import requests

        try:
            sensibull_url = (
                f"https://api.sensibull.com/v1/option_chain?symbol={symbol.upper()}"
            )
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                }
            )
            response = session.get(sensibull_url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                log.info(f"Fetched option chain from Sensibull for {symbol}")
                return {
                    "data": data,
                    "symbol": symbol,
                    "is_index": False,
                    "source": "sensibull",
                }
        except Exception as e:
            log.debug(f"Sensibull API failed for {symbol}: {e}")

        try:
            opstra_url = (
                f"https://opstra.definedge.com/api/optionchain/{symbol.upper()}"
            )
            session = requests.Session()
            session.headers.update(
                {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            response = session.get(opstra_url, timeout=15)
            if response.status_code == 200:
                data = response.json()
                log.info(f"Fetched option chain from Opstra for {symbol}")
                return {
                    "data": data,
                    "symbol": symbol,
                    "is_index": False,
                    "source": "opstra",
                }
        except Exception as e:
            log.debug(f"Opstra API failed for {symbol}: {e}")

        return None

    def get_stock_option_chain(self, symbol: str) -> dict | None:
        """Get stock options chain (for F&O stocks) using safe fallback path."""
        try:
            session = self._get_nse_session()
            url = f"https://www.nseindia.com/live_market/dumpProxy/optionChainDetailsByUnderlying?formAction=viewDetails&underlying={symbol.upper()}&instrumentType=OPTSTK&strike=&date="
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/",
            }
            response = session.get(url, headers=headers, timeout=20)
            if response.status_code == 200:
                data = response.json()
                chain = {
                    "data": data,
                    "symbol": symbol,
                    "is_index": False,
                    "source": "nse_stock_scrape",
                }
                if data and self._chain_has_valid_records(chain):
                    log.info(f"Option chain for {symbol} fetched via stock scrape")
                    return chain
                log.debug(
                    f"Stock option chain for {symbol} returned empty or invalid data"
                )
        except Exception as e:
            log.debug(f"Stock option chain scrape failed for {symbol}: {e}")
        return None

    def get_oi_pcr_data(self, symbol: str) -> dict | None:
        """Get Open Interest and PCR data for indices/options"""
        import time

        cache_key = f"oi_pcr:{symbol}"
        now = time.time()

        if hasattr(self, "_oi_pcr_cache") and cache_key in self._oi_pcr_cache:
            cached = self._oi_pcr_cache[cache_key]
            if now - cached.get("timestamp", 0) < 60:
                return cached.get("data")

        result = self._fetch_oi_pcr_nse(symbol)

        if not result:
            result = self._fetch_oi_pcr_sensibull(symbol)

        if result and hasattr(self, "_oi_pcr_cache"):
            self._oi_pcr_cache[cache_key] = {"data": result, "timestamp": now}

        return result

    def _fetch_oi_pcr_nse(self, symbol: str) -> dict | None:
        """Fetch OI/PCR from NSE website"""
        import requests
        from bs4 import BeautifulSoup

        index_symbols = {
            "NIFTY": "NIFTY%2050",
            "BANKNIFTY": "NIFTY%20BANK",
            "FINNIFTY": "NIFTY%20FIN_SERVICE",
        }

        try:
            url = f"https://www.nseindia.com/archives/nsccl/mwpl/nsccl_mwpl_{symbol.upper().replace('%20', '')}.xml"
            session = self._get_nse_session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.nseindia.com/",
                }
            )

            response = session.get(url, timeout=15)

            if response.status_code == 200:
                log.info(f"Fetched OI/PCR from NSE for {symbol}")
                return self._parse_oi_xml(response.text, symbol)
        except Exception as e:
            log.debug(f"NSE OI fetch failed: {e}")

        return None

    def _fetch_oi_pcr_sensibull(self, symbol: str) -> dict | None:
        """Fetch OI/PCR from Sensibull"""
        import requests

        try:
            url = f"https://api.sensibull.com/v1/option_chain?symbol={symbol.upper()}&expiry=latest"
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0"})

            response = session.get(url, timeout=15)

            if response.status_code == 200:
                data = response.json()
                return self._parse_sensibull_oi(data)
        except Exception as e:
            log.debug(f"Sensibull OI fetch failed: {e}")

        return None

    def _parse_oi_xml(self, xml_text: str, symbol: str) -> dict:
        """Parse OI data from XML"""
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(xml_text)

            total_ce_oi = 0
            total_pe_oi = 0

            for record in root.findall(".//record"):
                oc = record.findtext("openInterest", "0")
                if oc:
                    total_ce_oi += float(oc)

            pcr = total_pe_oi / max(1, total_ce_oi)

            return {
                "symbol": symbol,
                "total_call_oi": total_ce_oi,
                "total_put_oi": total_pe_oi,
                "pcr": pcr,
                "source": "nse_xml",
            }
        except Exception as e:
            log.debug(f"OI XML parse error: {e}")
            return {"symbol": symbol, "pcr": 1.0, "source": "default"}

    def _parse_sensibull_oi(self, data: dict) -> dict:
        """Parse OI/PCR from Sensibull response"""
        try:
            total_call_oi = 0
            total_put_oi = 0

            for strike in data.get("option_chain", []):
                if strike.get("ce", {}).get("open_interest"):
                    total_call_oi += strike["ce"]["open_interest"]
                if strike.get("pe", {}).get("open_interest"):
                    total_put_oi += strike["pe"]["open_interest"]

            pcr = total_put_oi / max(1, total_call_oi)

            return {
                "symbol": data.get("underlying", "UNKNOWN"),
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "pcr": pcr,
                "source": "sensibull",
            }
        except Exception as e:
            log.debug(f"Sensibull OI parse error: {e}")
            return {"pcr": 1.0, "source": "default"}

    def get_option_premium_scrape(
        self, symbol: str, strike: int, opt_type: str
    ) -> float | None:
        """Scrape option premium directly from NSE website"""
        import json
        import time

        import requests

        # Only try scraping for indices that actually have options
        index_symbols = {
            "NIFTY": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "FINNIFTY": "NIFTY FIN SERVICE",
        }
        no_options_indices = {"NIFTYNEXT50"}

        if symbol.upper() in no_options_indices:
            log.debug(
                f"Skipping option premium scrape for {symbol} (no options available)"
            )
            return None

        is_index = symbol.upper() in index_symbols

        inst_type = "IDX" if is_index else "OPTSTK"
        underlying = index_symbols.get(symbol.upper(), symbol.upper())

        try:
            url = f"https://www.nseindia.com/live_market/dumpProxy/optionChainDetailsByUnderlying?formAction=viewDetails&underlying={underlying.replace(' ', '%20')}&instrumentType={inst_type}&strike=&date="

            session = self._get_nse_session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.nseindia.com/",
                    "X-Requested-With": "XMLHttpRequest",
                }
            )

            response = session.get(url, timeout=25)

            if response.status_code == 200:
                try:
                    data = response.json()
                    records = data.get("records", {})
                    options = records.get("data", []) if records else []

                    for opt in options:
                        strike_price = opt.get("strikePrice")
                        if strike_price == strike:
                            if opt_type.upper() == "CE":
                                ce_data = opt.get("CE", {})
                                premium = (
                                    ce_data.get("lastPrice") or ce_data.get("LTP") or 0
                                )
                            else:
                                pe_data = opt.get("PE", {})
                                premium = (
                                    pe_data.get("lastPrice") or pe_data.get("LTP") or 0
                                )

                            if premium and premium > 0:
                                log.info(
                                    f"Scraped premium for {symbol}{strike}{opt_type}: ₹{premium}"
                                )
                                return float(premium)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            log.debug(f"NSE premium scrape failed for {symbol}{strike}{opt_type}: {e}")

        return None

    def get_underlying_price(self, symbol: str) -> float | None:
        """Get underlying price from NSE"""
        import time

        import requests

        try:
            index_symbols = {
                "NIFTY": "NIFTY 50",
                "BANKNIFTY": "NIFTY BANK",
                "FINNIFTY": "NIFTY FIN SERVICE",
            }
            is_index = symbol.upper() in index_symbols

            url = (
                f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol.upper()}"
                if is_index
                else f"https://www.nseindia.com/api/quote-equity?symbol={symbol.upper()}"
            )

            session = self._get_nse_session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Referer": "https://www.nseindia.com/",
                }
            )

            response = session.get(url, timeout=15)

            if response.status_code == 200:
                data = response.json()
                if is_index:
                    return data.get("underlyingValue")
                else:
                    return data.get("priceInfo", {}).get("lastPrice")
        except Exception as e:
            log.debug(f"Underlying price fetch failed for {symbol}: {e}")

        return None

    def _get_nse_api_quote(self, symbol: str) -> Quote | None:
        """Get quote from jugaad-data (primary) or NseIndiaApi (fallback)"""
        stock_symbol = self._normalize_nse_symbol(symbol)

        # Try jugaad-data first (most reliable for live data)
        if self._is_source_enabled("jugaad"):
            self._init_jugaad()
            if self._jugaad_available and self._jugaad:
                # Apply rate limiting before each jugaad call
                self._apply_rate_limit()
                try:
                    quote = self._jugaad.stock_quote(stock_symbol)
                    if quote and isinstance(quote, dict):
                        price_info = quote.get("priceInfo", {})
                        last_price = price_info.get("lastPrice")
                        if last_price and float(last_price) > 0:
                            self._record_source_success("jugaad")
                            return Quote(
                                symbol=symbol,
                                last_price=float(last_price),
                                volume=int(
                                    quote.get("preOpenMarket", {}).get(
                                        "totalTradedVolume", 0
                                    )
                                ),
                                bid=float(
                                    price_info.get("intraDayHighLow", {}).get(
                                        "min", last_price
                                    )
                                ),
                                ask=float(
                                    price_info.get("intraDayHighLow", {}).get(
                                        "max", last_price
                                    )
                                ),
                            )
                        self._record_source_failure(
                            "jugaad", "invalid jugaad quote response"
                        )
                except Exception as e:
                    self._record_source_failure("jugaad", f"quote error: {e}")
                    log.debug(f"jugaad-data quote error for {symbol}: {e}")
        else:
            log.debug(f"Skipping jugaad-data quote for {symbol}: source disabled")

        # Try NseIndiaApi for stocks
        if (
            self._nse
            and hasattr(self._nse, "quote")
            and self._is_source_enabled("nse_api")
        ):
            # Apply rate limiting before NseIndiaApi call
            self._apply_rate_limit()
            try:
                data = self._nse.quote(stock_symbol)
                if data and isinstance(data, dict) and "priceInfo" in data:
                    price_info = data.get("priceInfo", {})
                    last_price = price_info.get("lastPrice")
                    if last_price and float(last_price) > 0:
                        self._record_source_success("nse_api")
                        return Quote(
                            symbol=symbol,
                            last_price=float(last_price),
                            volume=int(
                                data.get("preOpenMarket", {}).get(
                                    "totalTradedVolume", 0
                                )
                            ),
                            bid=float(
                                price_info.get("intraDayHighLow", {}).get(
                                    "min", last_price
                                )
                            ),
                            ask=float(
                                price_info.get("intraDayHighLow", {}).get(
                                    "max", last_price
                                )
                            ),
                        )
                    self._record_source_failure(
                        "nse_api", "invalid NseIndiaApi quote response"
                    )
            except Exception as e:
                # Handle specific HTTP errors
                if "404" in str(e) or "Not Found" in str(e):
                    log.debug(f"Symbol {symbol} not found on NSE (404)")
                    return None
                self._record_source_failure("nse_api", f"quote error: {e}")
                log.debug(f"NseIndiaApi stock quote error for {symbol}: {e}")
        elif not self._is_source_enabled("nse_api"):
            log.debug(f"Skipping NseIndiaApi quote for {symbol}: source disabled")

        # Try index quotes via NseIndiaApi
        index_name_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "NIFTY BANK",
            "INDIAVIX": "INDIA VIX",
        }

        nse_symbol = index_name_map.get(symbol, symbol)

        if nse_symbol != symbol:
            return self._get_nse_index_quote(nse_symbol)

        return None

    def _get_nse_index_quote(self, symbol: str) -> Quote | None:
        """Get index quote using historical data"""
        try:
            import datetime
            from datetime import date, timedelta

            index_name_map = {
                "NIFTY": "NIFTY 50",
                "NIFTY50": "NIFTY 50",
                "BANKNIFTY": "NIFTY BANK",
                "INDIAVIX": "INDIA VIX",
            }

            nse_index = index_name_map.get(symbol, symbol)

            if hasattr(self._nse, "fetch_historical_index_data"):
                yesterday = date.today() - timedelta(days=1)
                h = self._nse.fetch_historical_index_data(nse_index, yesterday)
                if h and len(h) > 0:
                    latest = h[-1]
                    close = latest.get("EOD_CLOSE_INDEX_VAL")
                    if close and float(close) > 0:
                        return Quote(
                            symbol=symbol,
                            last_price=float(close),
                            volume=int(latest.get("HIT_TRADED_QTY", 0)),
                            bid=float(latest.get("EOD_LOW_INDEX_VAL", close)),
                            ask=float(latest.get("EOD_HIGH_INDEX_VAL", close)),
                        )
        except Exception as e:
            log.debug(f"NSE index historical error for {symbol}: {e}")

        return None

    def _get_yfinance_quote(self, symbol: str) -> Quote | None:
        if not self._is_source_enabled("yfinance"):
            log.debug(f"YFinance source disabled for {symbol}")
            return None

        # For NSE symbols, prefer NSE sources; avoid yfinance 404s for plain Indian tickers
        symbol_upper = symbol.upper()
        is_plain_nse = symbol_upper in self.STOCK_SYMBOLS or (
            not symbol_upper.endswith(".NS") and not symbol_upper.endswith(".BO")
        )
        is_index = symbol_upper in self.INDEX_SYMBOLS

        # If it's an index, attempt NSE scrape first, then fall back to yfinance using the mapped Yahoo symbol.
        if is_index:
            nse_quote = self._get_nse_scrape_quote(symbol)
            if (
                nse_quote
                and nse_quote.last_price is not None
                and nse_quote.last_price > 0
            ):
                return nse_quote
            # Use Yahoo Finance mapping for index fallback
            yahoo_symbol = self.INDEX_SYMBOLS.get(symbol_upper)
            if yahoo_symbol:
                # Directly fetch via yfinance for Yahoo ticker to avoid recursive NSE logic
                try:
                    import yfinance as yf

                    ticker = yf.Ticker(yahoo_symbol)
                    info = ticker.info
                    if (
                        not info
                        or "regularMarketPrice" not in info
                        or info["regularMarketPrice"] is None
                    ):
                        return None
                    return Quote(
                        symbol=symbol,
                        last_price=info["regularMarketPrice"],
                        volume=info.get("volume", 0),
                        bid=info.get("bid", 0),
                        ask=info.get("ask", 0),
                    )
                except Exception:
                    return None
            return None

        # For plain NSE stock symbols, try NSE scrape first; if unavailable, skip yfinance to avoid 404s.
        if is_plain_nse:
            nse_quote = self._get_nse_scrape_quote(symbol)
            if nse_quote:
                return nse_quote
            return None

        # For any other symbols (e.g., Yahoo-specific tickers), use yfinance directly.
        try:
            # Use yfinance to get quote
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            info = ticker.info

            # Check if we have valid data
            if (
                not info
                or "regularMarketPrice" not in info
                or info["regularMarketPrice"] is None
            ):
                return None

            # Get current price
            last_price = info["regularMarketPrice"]

            # Get volume if available
            volume = info.get("volume", 0)

            # Get bid/ask if available
            bid = info.get("bid", 0)
            ask = info.get("ask", 0)

            # Create and return Quote object
            return Quote(
                symbol=symbol, last_price=last_price, volume=volume, bid=bid, ask=ask
            )

        except Exception as e:
            log.debug(f"YFinance fetch failed for {symbol}: {e}")
            return None

        # If yfinance failed, try NSE scrape as final fallback
        return self._get_nse_scrape_quote(symbol)

    def _get_nse_scrape_quote(self, symbol: str) -> Quote | None:
        """Get quote by scraping NSE India website"""
        if not self._is_source_enabled("scrape"):
            status = self._source_status.get("scrape", {})
            disabled_ts = status.get("disabled_until")
            if disabled_ts:
                from datetime import datetime

                ts_str = datetime.fromtimestamp(disabled_ts).isoformat()
            else:
                ts_str = str(disabled_ts)
            log.debug(
                f"Skipping NSE scrape quote for {symbol}: source disabled until {ts_str}"
            )
            return None

        import re
        import time

        import requests

        nse_index_map = {
            # Major Indices (working)
            "NIFTY": ("NIFTY 50", r"NIFTY\s*50[^0-9]*([0-9,]+\.?\d*)"),
            "NIFTY50": ("NIFTY 50", r"NIFTY\s*50[^0-9]*([0-9,]+\.?\d*)"),
            "BANKNIFTY": ("NIFTY BANK", r"NIFTY\s*BANK[^0-9]*([0-9,]+\.?\d*)"),
            "INDIAVIX": ("INDIA VIX", r"INDIA\s*VIX[^0-9]*([0-9,]+\.?\d*)"),
        }

        # Check if it's an index we know
        symbol_upper = self._normalize_nse_symbol(symbol)

        # If it's not in our index map, try stock scrape
        if symbol_upper not in nse_index_map:
            return self._get_nse_stock_scrape(symbol_upper)

        index_name, pattern = nse_index_map[symbol_upper]

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }

            # Get the indices page using non-blocking aiohttp helper
            url = "https://www.nseindia.com/market-data/live-market-indices"
            soup_text = self._fetch_text_async(url, timeout=5, headers=headers)
            if not soup_text:
                return None

            # Try to find the specific index value
            match = re.search(pattern, soup_text, re.IGNORECASE)

            if match:
                price_str = match.group(1).replace(",", "")
                if price_str and float(price_str) > 0:
                    log.info(f"NSE scrape quote for {symbol}: {price_str}")
                    return Quote(
                        symbol=symbol,
                        last_price=float(price_str),
                        volume=0,
                        bid=float(price_str),
                        ask=float(price_str),
                    )

        except Exception as e:
            self._record_source_failure("scrape", f"index scrape failed: {e}")
            log.debug(f"NSE scrape error for {symbol}: {e}")

        # Try stock scrape as fallback
        return self._get_nse_stock_scrape(symbol)

    def _get_nse_stock_scrape(self, symbol: str) -> Quote | None:
        """Get stock quote by scraping NSE India website"""
        if not self._is_source_enabled("scrape"):
            status = self._source_status.get("scrape", {})
            disabled_ts = status.get("disabled_until")
            if disabled_ts:
                from datetime import datetime

                ts_str = datetime.fromtimestamp(disabled_ts).isoformat()
            else:
                ts_str = str(disabled_ts)
            log.debug(
                f"Skipping NSE stock scrape for {symbol}: source disabled until {ts_str}"
            )
            return None

        import time

        import requests

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.nseindia.com/",
            }

            symbol_upper = self._normalize_nse_symbol(symbol)
            # Try to get quote using aiohttp helper and parse JSON
            url = f"https://www.nseindia.com/api/quote-quote?symbol={symbol_upper}&section=corporate"
            text = self._fetch_text_async(url, timeout=5, headers=headers)
            if not text:
                return None

            try:
                import json

                data = json.loads(text)
            except Exception:
                data = None

            if not data or not isinstance(data, dict):
                return None

            price_info = data.get("priceInfo", {})
            last_price = price_info.get("lastPrice")

            if last_price and float(last_price) > 0:
                log.info(f"NSE stock scrape for {symbol}: {last_price}")
                return Quote(
                    symbol=symbol,
                    last_price=float(last_price),
                    volume=int(price_info.get("totalTradedVolume", 0)),
                    bid=float(
                        price_info.get("intraDayHighLow", {}).get("min", last_price)
                    ),
                    ask=float(
                        price_info.get("intraDayHighLow", {}).get("max", last_price)
                    ),
                )

        except Exception as e:
            self._record_source_failure("scrape", f"stock scrape failed: {e}")
            log.debug(f"NSE stock scrape error for {symbol}: {e}")

        # Try alternative: Alpha Vantage or other free APIs
        return self._get_alternative_quote(symbol)

    def _get_alternative_quote(self, symbol: str) -> Quote | None:
        """Try alternative data sources for NSE stocks"""
        if not self._is_source_enabled("alternative"):
            status = self._source_status.get("alternative", {})
            disabled_ts = status.get("disabled_until")
            if disabled_ts:
                from datetime import datetime

                ts_str = datetime.fromtimestamp(disabled_ts).isoformat()
            else:
                ts_str = str(disabled_ts)
            log.debug(
                f"Skipping alternative quote sources for {symbol}: source disabled until {ts_str}"
            )
            return None

        import requests

        # Try Financial Modeling Prep
        try:
            # Free tier API - limited requests
            url = f"https://financialmodelingprep.com/api/v3/quote/NSE:{symbol}"
            text = self._fetch_text_async(url, timeout=4)
            if text:
                try:
                    import json

                    data = json.loads(text)
                except Exception:
                    data = None
                if data and len(data) > 0:
                    stock = data[0]
                    price = stock.get("price")
                    if price and float(price) > 0:
                        log.info(f"FMP quote for {symbol}: {price}")
                        return Quote(
                            symbol=symbol,
                            last_price=float(price),
                            volume=int(stock.get("volume", 0)),
                            bid=float(stock.get("bid", 0)),
                            ask=float(stock.get("ask", 0)),
                        )
        except Exception as e:
            self._record_source_failure("alternative", f"FMP quote error: {e}")
            log.debug(f"FMP quote error for {symbol}: {e}")

        # Try Twelve Data as final fallback
        try:
            # Free tier - very limited
            url = f"https://api.twelvedata.com/time_series?symbol=NSE:{symbol}&interval=1day&apikey=demo"
            text = self._fetch_text_async(url, timeout=4)
            if text:
                try:
                    import json

                    data = json.loads(text)
                except Exception:
                    data = {}
                if data.get("values") and len(data["values"]) > 0:
                    latest = data["values"][0]
                    price = latest.get("close")
                    if price and float(price) > 0:
                        log.info(f"TwelveData quote for {symbol}: {price}")
                        return Quote(
                            symbol=symbol,
                            last_price=float(price),
                            volume=0,
                            bid=0,
                            ask=0,
                        )
        except Exception as e:
            self._record_source_failure("alternative", f"TwelveData quote error: {e}")
            log.debug(f"TwelveData quote error for {symbol}: {e}")

        return None

    def get_positions(self) -> list[Position]:
        return [
            Position(sym, data["qty"], data["avg_price"], 100.0, 0, "FLAT")
            for sym, data in self.positions.items()
            if data["qty"] != 0
        ]

    def get_order_history(self) -> list[dict]:
        return self.orders

    def cancel_order(self, order_id: str) -> dict:
        return {"status": "success"}

    def get_historical_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        """Fetch historical OHLCV candles for *symbol*.

        The original implementation attempted to use a custom NSE API and a
        yfinance fallback, but ``self._nse`` is never initialised in this
        repository, resulting in an empty list for every request.  To provide a
        reliable data source we now use the Zerodha KiteConnect ``historical_data``
        endpoint, which is always available after ``connect()`` succeeds.

        The method:
        1. Ensures the broker is connected.
        2. Looks up the instrument token from the cached master‑contract.
        3. Requests a wide date range (up to one year) and then trims the
           result to the most recent ``count`` candles.
        4. Normalises the fields to match the expected dictionary format.
        """
        # Ensure we have a live connection – ``connect`` will also populate the
        # master‑contract cache if it is not already ready.
        if not self.connected:
            self.connect()

        # Attempt to load recent candles from local storage before making API call.
        local_candles = self._load_local_historical(symbol, count)
        if local_candles:
            return local_candles

        try:
            instrument = self.get_instrument(symbol)
            if not instrument:
                log.debug(f"Instrument not found for symbol {symbol}")
                return []
            token = instrument.get("instrument_token")
            if not token:
                log.debug(f"No token for symbol {symbol}")
                return []

            from datetime import datetime, timedelta

            # Request a limited date range to respect Kite's maximum of 100 days
            # for most granular intervals (e.g., 5minute). Requesting a full
            # year triggers "interval exceeds max limit" errors. We request the
            # most recent 100 days, which comfortably covers the typical
            # ``count`` values used by the downloader (the script fetches data
            # in chunks and then trims to the required number of candles).
            to_date = datetime.now()
            # Use a 30‑day window for granular intervals (e.g., 5minute). This
            # stays safely under Kite's 100‑day limit while providing enough data
            # for the downloader, which trims the result to the required ``count``.
            from_date = to_date - timedelta(days=30)

            raw = self.kite.historical_data(token, from_date, to_date, interval)
            if not raw:
                return []

            # Convert Kite's response to the unified format used elsewhere.
            candles = []
            for rec in raw:
                candles.append(
                    {
                        "timestamp": rec.get("date"),
                        "open": float(rec.get("open", 0)),
                        "high": float(rec.get("high", 0)),
                        "low": float(rec.get("low", 0)),
                        "close": float(rec.get("close", 0)),
                        "volume": int(rec.get("volume", 0)),
                    }
                )

            # Return the most recent ``count`` entries (Kite returns oldest‑first).
            return candles[-count:]
        except Exception as e:
            # Handle token expiration by attempting a reconnect and retry once.
            err_msg = str(e).lower()
            if "invalid token" in err_msg:
                log.warning(f"Zerodha token invalid for {symbol}, attempting reconnect")
                # Re‑establish connection which also refreshes the master‑contract cache.
                self.connect()
                try:
                    # Retry the request once after reconnect.
                    instrument = self.get_instrument(symbol)
                    if not instrument:
                        log.debug(
                            f"Instrument not found for symbol {symbol} after reconnect"
                        )
                        return []
                    token = instrument.get("instrument_token")
                    if not token:
                        log.debug(f"No token for symbol {symbol} after reconnect")
                        return []
                    from datetime import datetime, timedelta

                    to_date = datetime.now()
                    from_date = to_date - timedelta(days=30)
                    raw = self.kite.historical_data(token, from_date, to_date, interval)
                    if not raw:
                        return []
                    candles = []
                    for rec in raw:
                        candles.append(
                            {
                                "timestamp": rec.get("date"),
                                "open": float(rec.get("open", 0)),
                                "high": float(rec.get("high", 0)),
                                "low": float(rec.get("low", 0)),
                                "close": float(rec.get("close", 0)),
                                "volume": int(rec.get("volume", 0)),
                            }
                        )
                    return candles[-count:]
                except Exception as retry_e:
                    log.debug(
                        f"Retry after token refresh failed for {symbol}: {retry_e}"
                    )
            # Fallback: log and return empty list.
            log.debug(f"Historical data error for {symbol}: {e}")
            return []

    def _get_nse_historical_data(self, symbol: str, count: int) -> list[dict]:
        """Get historical data from NSE API"""
        try:
            if not self._nse:
                return []

            from datetime import date, timedelta

            end_date = date.today()
            start_date = end_date - timedelta(days=count)

            data = self._nse.fetch_equity_historical_data(symbol, start_date, end_date)

            if not data or len(data) == 0:
                return []

            candles = []
            for record in data:
                candles.append(
                    {
                        "timestamp": record.get("mtimestamp", ""),
                        "open": float(record.get("chOpeningPrice", 0)),
                        "high": float(record.get("chTradeHighPrice", 0)),
                        "low": float(record.get("chTradeLowPrice", 0)),
                        "close": float(record.get("chClosingPrice", 0)),
                        "volume": int(record.get("chTotTradedQty", 0)),
                    }
                )

            return candles

        except Exception as e:
            log.debug(f"NSE historical error for {symbol}: {e}")
            return []

    def _get_yfinance_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        """Get historical data from yfinance as fallback"""
        try:
            import pandas as pd
            import yfinance as yf

            def _safe_float(value: Any) -> float:
                try:
                    return float(value)
                except Exception:
                    return 0.0

            def _safe_int(value: Any) -> int:
                try:
                    return int(value)
                except Exception:
                    return 0

            interval_map = {
                "1minute": "1m",
                "5minute": "5m",
                "15minute": "15m",
                "30minute": "30m",
                "60minute": "1h",
                "day": "1d",
            }

            yf_interval = interval_map.get(interval.lower(), "5m")

            index_symbols = {
                "NIFTY": "^NSEI",
                "NIFTY50": "^NSEI",
                "BANKNIFTY": "^NSEBANK",
                "NIFTY BANK": "^NSEBANK",
            }

            yf_symbol = index_symbols.get(symbol.upper())
            if not yf_symbol:
                yf_symbol = self._to_yf_ticker(symbol)

            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(
                period=f"{count}d", interval=yf_interval, raise_errors=False
            )

            if df is None or getattr(df, "empty", True):
                if yf_interval in ("1m", "5m", "15m", "30m", "1h"):
                    df = ticker.history(
                        period=f"{max(count, 5)}d", interval="1d", raise_errors=False
                    )
            if df is None or getattr(df, "empty", True):
                return []

            candles = []
            for idx, row in df.iterrows():
                candles.append(
                    {
                        "timestamp": str(idx),
                        "open": _safe_float(
                            row.get("Open")
                            if hasattr(row, "get")
                            else row["Open"] if "Open" in row else 0
                        ),
                        "high": _safe_float(
                            row.get("High")
                            if hasattr(row, "get")
                            else row["High"] if "High" in row else 0
                        ),
                        "low": _safe_float(
                            row.get("Low")
                            if hasattr(row, "get")
                            else row["Low"] if "Low" in row else 0
                        ),
                        "close": _safe_float(
                            row.get("Close")
                            if hasattr(row, "get")
                            else row["Close"] if "Close" in row else 0
                        ),
                        "volume": _safe_int(
                            row.get("Volume")
                            if hasattr(row, "get")
                            else row["Volume"] if "Volume" in row else 0
                        ),
                    }
                )

            return candles[-count:] if len(candles) > count else candles

        except Exception as e:
            log.debug(f"yfinance error for {symbol}: {e}")
            return []

    def _generate_dummy_candles(self, count: int) -> list[dict]:
        import random

        base = 100
        candles = []
        for i in range(count):
            candles.append(
                {
                    "timestamp": f"2024-01-{i + 1:02d}",
                    "open": base + random.uniform(-2, 2),
                    "high": base + random.uniform(0, 3),
                    "low": base + random.uniform(-3, 0),
                    "close": base + random.uniform(-2, 2),
                    "volume": random.randint(100000, 1000000),
                }
            )
        return candles

    def get_lot_size(self, symbol: str) -> int:
        """Get lot size for a symbol from cached JSON file"""
        cache_file = (
            "/home/rajasekhar/vibe-coding/raj_trading_bot/config/lot_sizes.json"
        )

        # Check if cache file exists and is not too old
        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < 24 * 3600:
                with open(cache_file, "r") as f:
                    lot_sizes = json.load(f)
                    return lot_sizes.get(symbol.upper(), 25)

        # Default lot sizes for major indices
        default_sizes = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "NIFTY50": 65,
            "SENSEX": 20,
            "BANKEX": 30,
            "SENSEX50": 75,
        }

        # Return default size for indices
        if symbol.upper() in default_sizes:
            return default_sizes[symbol.upper()]

        # Default fallback for stocks
        return 25


class ZerodhaBroker(BrokerInterface):
    """Zerodha Kite broker implementation with master contract caching"""

    # Master contract cache TTL (1 hour)
    CACHE_TTL = 3600
    _rate_limit_delay = 0
    _rate_limit_lock = None
    _last_request_time = 0.0
    TOKEN_FILE = Path(__file__).parent.parent.parent / "data" / "zerodha_token.json"

    def __init__(self, config: dict):
        self.config = config
        # Human‑readable identifier used by the system for logging and introspection
        # Correct name for this broker implementation
        self.name = "ZerodhaBroker"
        self.connected = False
        self.kite = None

        # Master contract cache
        self.instruments_cache = {}  # {symbol: instrument_data}
        self.instruments_by_token = {}  # {token: instrument_data}
        self.cache_timestamp = None
        self.cache_ready = False

        # Load saved access token if not in config
        if not self.config.get("access_token"):
            self._load_saved_token()

        # Initialise the underlying core Zerodha broker and expose its WebSocket.
        # This ensures that ``system.broker.websocket`` is available immediately
        # after the wrapper is constructed, preventing the fallback warning.
        self._ensure_core_broker()

    # ---------------------------------------------------------------------
    # Helper to expose the underlying core Zerodha broker (which provides
    # the WebSocket instance and token lookup) when the wrapper creates it.
    # The core broker lives in ``core.zerodha_broker`` and
    # defines ``self.websocket`` and ``get_instrument_token``. The wrapper
    # historically accessed the core via a private attribute ``_zerodha_broker``
    # in a few code paths (e.g., quote fetching). For market‑snapshot logic we
    # need two additional capabilities:
    #   1. Make the WebSocket reachable as ``self.websocket`` so callers can
    #      use ``broker.websocket`` directly.
    #   2. Provide ``get_instrument_token`` that forwards to the core broker.
    # ---------------------------------------------------------------------

    def _ensure_core_broker(self):
        """Instantiate the core Zerodha broker if not already present.

        The core broker provides the live WebSocket connection and token
        utilities. This method is idempotent and sets ``self.websocket`` to the
        core broker's ``websocket`` attribute for external access.
        """
        if hasattr(self, "_zerodha_broker") and self._zerodha_broker:
            # Core broker already created – expose its websocket.
            self.websocket = getattr(self._zerodha_broker, "websocket", None)
            return
        # Lazily import and create the core broker.
        try:
            from core.zerodha_broker import ZerodhaBroker as CoreZerodhaBroker

            # Retrieve any stored access token from the wrapper config or from
            # a temporary attribute set by ``RajTradingBot._check_zerodha_daily_token``.
            # ``CoreZerodhaBroker`` accepts ``access_token`` as the third argument.
            access_token = self.config.get("zerodha_access_token") or getattr(
                self, "_zerodha_access_token", None
            )
            self._zerodha_broker = CoreZerodhaBroker(
                self.config.get("zerodha_api_key"),
                self.config.get("zerodha_api_secret"),
                access_token,
            )
            # Propagate the token to the environment for the core broker's
            # internal modules (which read ``ZERODHA_ACCESS_TOKEN``). This
            # ensures consistency between the wrapper config and the core
            # implementation, preventing handshake failures due to a missing
            # environment variable.
            if access_token:
                os.environ["ZERODHA_ACCESS_TOKEN"] = access_token
            # Connect the core broker – this establishes the WebSocket.
            self._zerodha_broker.connect()
            # Expose the websocket for external callers.
            self.websocket = getattr(self._zerodha_broker, "websocket", None)
        except Exception as e:
            log.error(f"Failed to initialise core Zerodha broker: {e}")

    # ---------------------------------------------------------------------
    # Public API required by ``download_market_snapshots`` and other callers.
    # ---------------------------------------------------------------------
    def get_instrument_token(self, exchange: str, symbol: str) -> str | None:
        """Return the instrument token for a given symbol.

        Delegates to the core Zerodha broker if available; otherwise falls back
        to the wrapper's cached master‑contract data.
        """
        # Ensure the core broker (and its websocket) is initialised.
        self._ensure_core_broker()
        exchange = exchange.strip().upper() if exchange else ""
        symbol = symbol.strip()
        if hasattr(self, "_zerodha_broker") and self._zerodha_broker:
            token = self._zerodha_broker.get_instrument_token(exchange, symbol)
            if token:
                return token
        # Fallback: look up token from the wrapper's own cache.
        instrument = self.get_instrument(symbol, exchange)
        if instrument:
            return instrument.get("instrument_token")
        return None

    def _load_local_historical(self, symbol: str, count: int) -> list[dict]:
        """Load recent candles for *symbol* from local DuckDB or Parquet storage.

        Returns a list of candle dictionaries matching the API format. If no
        local data is found, returns an empty list.
        """
        # Determine DuckDB root – same env var used by the downloader scripts.
        duckdb_root = Path(
            os.getenv(
                "DUCKDB_ROOT",
                "/media/rajasekhar/Backup/duckdb/",
            )
        )
        duckdb_path = duckdb_root / "historical_data.duckdb"
        # Attempt DuckDB query
        if duckdb is not None and duckdb_path.is_file():
            try:
                con = duckdb.connect(str(duckdb_path))
                df = con.execute(
                    "SELECT ts, open, high, low, close, volume FROM candles WHERE symbol = ? ORDER BY ts DESC LIMIT ?",
                    (symbol, count),
                ).fetchdf()
                con.close()
                if not df.empty:
                    df = df.rename(columns={"ts": "time"})
                    return df.to_dict(orient="records")
            except Exception as e:
                log.debug(f"DuckDB load failed for {symbol}: {e}")
        # Fallback to Parquet files
        parquet_dir = duckdb_root / "parquet"
        parquet_path = parquet_dir / f"{symbol}.parquet"
        if pd is not None and parquet_path.is_file():
            try:
                df = pd.read_parquet(parquet_path)
                if not df.empty:
                    df = df.sort_values(by="timestamp", ascending=False).head(count)
                    df = df.rename(columns={"timestamp": "time"})
                    return df.to_dict(orient="records")
            except Exception as e:
                log.debug(f"Parquet load failed for {symbol}: {e}")
        return []

    def _apply_rate_limit(self):
        """Rate limit Zerodha REST API calls to avoid 429 errors."""
        if ZerodhaBroker._rate_limit_lock is None:
            import threading

            ZerodhaBroker._rate_limit_lock = threading.Lock()
        with ZerodhaBroker._rate_limit_lock:
            elapsed = time.time() - getattr(ZerodhaBroker, "_last_request_time", 0.0)
            if elapsed < ZerodhaBroker._rate_limit_delay:
                time.sleep(ZerodhaBroker._rate_limit_delay - elapsed)
            ZerodhaBroker._last_request_time = time.time()

    def _load_saved_token(self) -> bool:
        """Load saved access token from token file"""
        try:
            import json

            if self.TOKEN_FILE.exists():
                with open(self.TOKEN_FILE, "r") as f:
                    data = json.load(f)
                    token = data.get("access_token", "")
                    expiry_str = data.get("expiry", "")
                    if token and expiry_str:
                        from datetime import datetime

                        expiry = datetime.fromisoformat(expiry_str)
                        if datetime.now() < expiry:
                            if self._validate_access_token(token):
                                self.config["access_token"] = token
                                log.info(
                                    f"Loaded saved Zerodha access token from {self.TOKEN_FILE}"
                                )
                                return True
                            log.warning(
                                f"Saved Zerodha token in {self.TOKEN_FILE} is invalid or expired."
                            )
        except Exception as e:
            log.debug(f"Could not load saved token: {e}")
        return False

    def _validate_access_token(self, access_token: str) -> bool:
        """Validate a Zerodha access token by making a simple profile API call."""
        if not access_token or not isinstance(access_token, str):
            return False
        try:
            from kiteconnect import KiteConnect

            api_key = (
                self.config.get("api_key", "")
                or self.config.get("ZERODHA_API_KEY", "")
                or self.config.get("zerodha_api_key", "")
            )
            if not api_key:
                # Missing API key is not fatal for the overall system – it merely
                # means Zerodha cannot be used. Downgrade to a warning to avoid
                # noisy error logs and allow graceful fallback to other brokers.
                log.warning("Zerodha API key missing while validating access token")
                return False
            kite = KiteConnect(api_key)
            kite.set_access_token(access_token)
            profile = kite.profile()
            valid = bool(profile and profile.get("user_id"))
            if not valid:
                log.warning("Zerodha token validation returned empty profile")
            return valid
        except Exception as e:
            log.warning(f"Zerodha token validation failed: {e}")
            return False

    def connect(self) -> bool:
        """Establish connection to Zerodha and always validate the token.

        Validation is performed via ``kite.profile()`` regardless of any cached
        expiry information. This ensures that an expired or otherwise invalid
        token is never silently accepted.
        """
        try:
            from kiteconnect import KiteConnect

            # Resolve API credentials from configuration (same logic used
            # elsewhere in the codebase).
            api_key = (
                self.config.get("api_key", "")
                or self.config.get("ZERODHA_API_KEY", "")
                or self.config.get("zerodha_api_key", "")
            )
            access_token = (
                self.config.get("access_token", "")
                or self.config.get("ZERODHA_ACCESS_TOKEN", "")
                or self.config.get("zerodha_access_token", "")
            )
            if not api_key or not access_token:
                # Missing credentials should not abort the entire startup –
                # other brokers (e.g., nselive) can be used.
                log.warning("Zerodha API key or access token missing in config")
                return False

            # Always validate the token using the profile endpoint.
            if not self._validate_access_token(access_token):
                log.error("Zerodha access token is invalid or expired")
                self.connected = False
                return False

            # -----------------------------------------------------------------
            # Persist the validated token for future runs.
            # -----------------------------------------------------------------
            try:
                from core.token_manager import ZerodhaTokenManager

                # Resolve the secret (mirrors the logic used elsewhere).
                api_secret = (
                    self.config.get("api_secret", "")
                    or self.config.get("ZERODHA_API_SECRET", "")
                    or self.config.get("zerodha_api_secret", "")
                )
                token_mgr = ZerodhaTokenManager(api_key, api_secret)
                token_mgr.access_token = access_token
                # If the token manager already knows an expiry, keep it; otherwise
                # assume a safe default of 24 hours.
                if not token_mgr.token_expiry:
                    from datetime import datetime, timedelta

                    token_mgr.token_expiry = datetime.now() + timedelta(days=1)
                # Write the token (including expiry) to the standard JSON file.
                token_path = token_mgr.TOKEN_FILE
                token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(token_path, "w") as f:
                    json.dump(
                        {
                            "access_token": token_mgr.access_token,
                            "expiry": (
                                token_mgr.token_expiry.isoformat()
                                if token_mgr.token_expiry
                                else None
                            ),
                        },
                        f,
                        indent=4,
                    )
                log.info(f"Validated Zerodha token persisted to {token_path}")
            except Exception as e:
                # Failure to persist should not block the connection – log and continue.
                log.debug(f"Failed to persist validated Zerodha token: {e}")

            # Initialise the Kite client after successful validation.
            self.kite = KiteConnect(api_key)
            self.kite.set_access_token(access_token)
            self.connected = True
            log.info("Zerodha broker connected")

            # Download master contract at startup
            try:
                self._download_master_contract()
                log.info(
                    f"✅ Zerodha master contract cached: {len(self.instruments_cache)} instruments"
                )
                self.cache_ready = True
            except Exception as e:
                log.warning(f"Failed to download Zerodha master contract: {e}")
                log.warning(
                    "Continuing without master contract cache (may impact performance)"
                )
                self.cache_ready = False

            return True
        except Exception as e:
            log.error(f"Zerodha connection failed: {e}")
            return False

    def _download_master_contract(self):
        """Download all instruments from Zerodha and cache locally"""
        from pathlib import Path
        try:
            from core.database import DatabaseManager
        except Exception:
            DatabaseManager = None  # type: ignore

        db_manager = None
        if DatabaseManager is not None:
            try:
                db_manager = DatabaseManager()
                cached_instruments = db_manager.get_instrument_cache()
                if cached_instruments:
                    self.instruments_cache = {}
                    self.instruments_by_token = {}
                    for row in cached_instruments:
                        inst = {
                            "instrument_token": row.get("instrument_token"),
                            "exchange": row.get("exchange"),
                            "tradingsymbol": row.get("tradingsymbol"),
                            "name": row.get("name"),
                            "expiry": row.get("expiry"),
                            "strike": row.get("strike"),
                            "tick_size": row.get("tick_size"),
                            "lot_size": row.get("lot_size"),
                            "instrument_type": row.get("instrument_type"),
                            "segment": row.get("segment"),
                        }
                        key = (
                            f"{inst['exchange']}:{inst['tradingsymbol']}"
                            if inst["exchange"]
                            else inst["tradingsymbol"]
                        )
                        self.instruments_cache[key] = inst
                        self.instruments_cache[inst["tradingsymbol"]] = inst
                        if inst["instrument_token"]:
                            self.instruments_by_token[str(inst["instrument_token"])] = (
                                inst
                            )
                    self.cache_timestamp = time.time()
                    log.info(
                        f"Loaded {len(self.instruments_cache)} instruments from ORM cache (cached < 24h, using it)"
                    )
                    return
            except Exception as e:
                log.debug(f"Could not load from ORM cache: {e}")

        if not self.kite:
            raise ValueError("Kite client not initialized")

        log.debug("Downloading Zerodha master contract...")

        all_instruments = self.kite.instruments()

        # Build indices for O(1) lookups
        self.instruments_cache = {}
        self.instruments_by_token = {}

        for instrument in all_instruments:
            # Index by trading symbol
            symbol = instrument.get("tradingsymbol", "")
            exchange = instrument.get("exchange", "")
            token = instrument.get("instrument_token", "")

            if symbol:
                # Store with exchange prefix for clarity
                key = f"{exchange}:{symbol}" if exchange else symbol
                self.instruments_cache[key] = instrument

                # Also store by symbol alone for convenience
                self.instruments_cache[symbol] = instrument

            # Index by token
            if token:
                self.instruments_by_token[str(token)] = instrument

        self.cache_timestamp = time.time()
        log.debug(
            f"Master contract cache built with {len(self.instruments_cache)} entries"
        )
        if db_manager is not None:
            try:
                db_manager.replace_instrument_cache(all_instruments)
            except Exception as e:
                log.debug(f"Failed to persist master contract cache to ORM: {e}")

    def _load_instruments(self) -> list[dict]:
        if not self.cache_ready:
            try:
                self._download_master_contract()
                self.cache_ready = True
            except Exception as e:
                log.error(f"Failed to load Zerodha master contract: {e}")
                return []
        return list(self.instruments_cache.values())

    def get_instruments(self, exchange: str = None) -> list[dict]:
        """Return the full instrument master from Zerodha cache.

        The DataProvider expects a ``get_instruments`` method on each broker.
        ZerodhaBroker previously only exposed ``_load_instruments`` which was
        internal, causing the provider to fall back to other brokers (e.g.
        ``nselive``). This wrapper makes the master‑contract data available to
        the system.

        Args:
            exchange: Optional exchange filter (e.g. ``"NSE"`` or ``"NFO"``).
                If provided, only instruments matching the exchange are
                returned.

        Returns:
            List of instrument dictionaries.
        """
        instruments = self._load_instruments()
        if exchange:
            # Filter by the ``exchange`` field present in the instrument dict.
            return [inst for inst in instruments if inst.get("exchange") == exchange]
        return instruments

    def _refresh_cache_if_needed(self):
        """Refresh cache if TTL has expired (optional daily refresh)"""
        if not self.cache_timestamp:
            return

        time_since_refresh = time.time() - self.cache_timestamp
        if time_since_refresh > self.CACHE_TTL:
            log.debug(
                f"Master contract cache TTL expired ({time_since_refresh}s > {self.CACHE_TTL}s), refreshing..."
            )
            try:
                self._download_master_contract()
                log.info("✅ Master contract cache refreshed")
            except Exception as e:
                log.warning(f"Failed to refresh master contract cache: {e}")

    def get_instrument(self, symbol: str, exchange: str = None) -> dict | None:
        """
        Get instrument data from cache.

        Args:
            symbol: Trading symbol
            exchange: Exchange (e.g., 'NSE', 'NFO', 'MCX')

        Returns: Instrument dict or None
        """
        if not self.cache_ready:
            return None

        # Try with exchange prefix first
        if exchange:
            key = f"{exchange}:{symbol}"
            if key in self.instruments_cache:
                return self.instruments_cache[key]

        # Try without exchange prefix
        if symbol in self.instruments_cache:
            return self.instruments_cache[symbol]

        return None

    def get_instrument_by_token(self, token: str) -> dict | None:
        """Get instrument data by token from cache"""
        if not self.cache_ready:
            return None

        return self.instruments_by_token.get(str(token))

    def disconnect(self) -> None:
        self.connected = False
        log.info("Zerodha broker disconnected")

    def place_order(self, order: Order) -> dict:
        if not self.connected:
            return {"status": "error", "message": "Not connected"}

        try:
            result = self.kite.place_order(
                variety="regular",
                exchange=order.exchange,
                tradingsymbol=order.symbol,
                transaction_type=order.transaction_type,
                quantity=order.quantity,
                product=order.product_type,
                order_type=order.order_type,
            )
            return {"status": "success", "order_id": result}
        except Exception as e:
            log.error(f"Zerodha order failed: {e}")
            return {"status": "error", "message": str(e)}

    INDEX_SYMBOLS = {
        "NIFTY",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "BANKEX",
        "SENSEX50",
        "INDIAVIX",
    }

    def _get_kite_exchange(self, symbol: str, fallback: str = "NSE") -> str:
        """Return the appropriate Kite exchange prefix for a given symbol.

        For index symbols Zerodha expects the regular ``NSE`` exchange prefix
        (e.g. ``NSE:NIFTY 50``). The previous implementation returned
        ``NSE_INDEX`` which does not correspond to a valid exchange in the Kite
        API and resulted in empty quote responses for indices such as
        ``NIFTY`` and ``INDIAVIX``. This method now correctly returns ``NSE``
        for known index symbols while preserving the original fallback for
        non‑index symbols.
        """
        s = (symbol or "").strip().upper()
        if s in self.INDEX_SYMBOLS:
            return "NSE"
        return fallback

    def get_quote(self, symbol: str) -> Quote | None:
        """Fetch a real‑time quote from Zerodha.

        The original implementation expected the caller to provide a fully
        qualified ``exchange:symbol`` string. For index symbols the caller
        often passes the plain index name (e.g. ``"NIFTY"``). Zerodha requires the
        tradingsymbol used in its master contract (e.g. ``"NIFTY 50"``) and the
        exchange prefix ``"NSE"``. To make the broker more robust we now map
        known index identifiers to their Zerodha tradingsymbols before
        constructing the request key.
        """
        if not self.connected or not self.kite:
            log.debug(f"ZerodhaBroker not connected for symbol {symbol}")
            return None

        # Mapping of internal index identifiers to Zerodha tradingsymbols
        index_map = {
            "NIFTY": "NIFTY 50",
            "NIFTY50": "NIFTY 50",
            "BANKNIFTY": "BANKNIFTY",
            "INDIAVIX": "INDIA VIX",
        }

        try:
            self._apply_rate_limit()
            # Normalise the symbol and apply index mapping if applicable
            symbol_up = symbol.strip().upper()
            if symbol_up in index_map:
                # Use the Zerodha tradingsymbol for the index
                symbol_mapped = index_map[symbol_up]
                exchange_prefix = "NSE"
            else:
                symbol_mapped = symbol_up
                # Preserve any explicit exchange prefix supplied by the caller
                if ":" in symbol_mapped:
                    exchange_prefix = symbol_mapped.split(":", 1)[0].upper()
                else:
                    instrument = self.get_instrument(symbol_mapped)
                    if instrument and instrument.get("exchange"):
                        exchange_prefix = instrument.get("exchange").upper()
                    else:
                        exchange_prefix = self._get_kite_exchange(symbol_mapped)

            # Build the fully qualified key for the Kite API
            if ":" in symbol_mapped:
                symbol_key = symbol_mapped
            else:
                symbol_key = f"{exchange_prefix}:{symbol_mapped}"

            log.debug(f"Fetching Zerodha quote for {symbol_key}")
            data = self.kite.quote(symbol_key)

            if not data:
                log.debug(f"Zerodha returned empty data for {symbol_key}")
                return None

            # Response is a dict like {"NSE:SYMBOL": {...}}
            quote_data = data.get(symbol_key, data)

            if not quote_data.get("last_price"):
                log.debug(
                    f"No last_price in Zerodha response for {symbol_key}: {quote_data}"
                )
                return None

            return Quote(
                symbol=symbol,
                last_price=quote_data.get("last_price", 0),
                volume=quote_data.get("volume", 0),
                bid=(
                    quote_data.get("depth", {}).get("buy", [{}])[0].get("price", 0)
                    if quote_data.get("depth")
                    else 0
                ),
                ask=(
                    quote_data.get("depth", {}).get("sell", [{}])[0].get("price", 0)
                    if quote_data.get("depth")
                    else 0
                ),
            )
        except Exception as e:
            log.debug(f"Zerodha quote failed for {symbol}: {e}")
            return None

    def get_multiple_quotes(self, symbols: list, exchange: str = "NSE") -> dict:
        """Batch fetch quotes for up to 500 instruments per Zerodha API limit."""
        if not self.connected or not self.kite:
            log.debug(
                f"ZerodhaBroker not connected for batch quote ({len(symbols)} symbols)"
            )
            return None
        try:
            batch_count = (len(symbols) + 499) // 500
            log.info(
                f"ZerodhaBroker.get_multiple_quotes: fetching {len(symbols)} symbols in {batch_count} batches of 500"
            )
            self._apply_rate_limit()
            instrument_keys = []
            exchange_map = {}
            for s in symbols:
                if not s:
                    continue
                symbol = s.strip()
                if ":" in symbol:
                    key = symbol.upper()
                    prefix = key.split(":", 1)[0]
                else:
                    symbol_up = symbol.upper()
                    # Prefer the cached master contract to resolve the correct
                    # exchange for option and non-NSE instruments.
                    instrument = self.get_instrument(symbol_up)
                    if instrument and instrument.get("exchange"):
                        prefix = instrument.get("exchange").upper()
                    else:
                        prefix = self._get_kite_exchange(symbol_up)
                    if symbol_up in self.INDEX_SYMBOLS:
                        symbol_up = self.INDEX_SYMBOL_MAP.get(symbol_up, symbol_up)
                        prefix = "NSE"
                    key = f"{prefix}:{symbol_up}"
                instrument_keys.append(key)
                exchange_map[key] = prefix
            results = {}
            for i in range(0, len(instrument_keys), 500):
                batch = instrument_keys[i : i + 500]
                log.debug(
                    f"  Batch {i // 500 + 1}/{batch_count}: fetching {len(batch)} symbols"
                )
                data = self.kite.quote(*batch)
                batch_results = len(data) if data else 0
                log.debug(
                    f"  Batch {i // 500 + 1}/{batch_count}: got {batch_results} results"
                )
                if i == 0 and data:
                    sample_key = next(iter(data))
                    sample = data[sample_key]
                    log.debug(
                        f"  Sample response for {sample_key}: last_price={sample.get('last_price')}, volume={sample.get('volume')}, keys={list(sample.keys())[:8]}"
                    )
                for key, q in data.items():
                    sym = key.split(":", 1)[-1]
                    actual_exchange = exchange_map.get(key, exchange)
                    results[sym] = {
                        "symbol": sym,
                        "exchange": actual_exchange,
                        "last_price": q.get("last_price", 0),
                        "volume": q.get("volume", 0),
                        "bid": (
                            q.get("depth", {}).get("buy", [{}])[0].get("price", 0)
                            if q.get("depth")
                            else 0
                        ),
                        "ask": (
                            q.get("depth", {}).get("sell", [{}])[0].get("price", 0)
                            if q.get("depth")
                            else 0
                        ),
                    }
            log.info(
                f"ZerodhaBroker.get_multiple_quotes: returning {len(results)} symbols"
            )
            return results
        except Exception as e:
            log.warning(f"Zerodha batch quote failed: {e}")
            return None

    def get_orderbook(self, symbol: str) -> dict | None:
        """Get market depth (orderbook) from Zerodha quote response."""
        if not self.connected or not self.kite:
            log.debug(f"ZerodhaBroker not connected for orderbook {symbol}")
            return None

        try:
            self._apply_rate_limit()
            if ":" in symbol:
                symbol_key = symbol
            else:
                exchange_prefix = self._get_kite_exchange(symbol)
                symbol_key = f"{exchange_prefix}:{symbol}"
            data = self.kite.quote(symbol_key)
            if not data:
                return None

            quote_data = data.get(symbol_key, data)
            depth = quote_data.get("depth", {})
            buy_levels = depth.get("buy", []) or []
            sell_levels = depth.get("sell", []) or []

            buy_out = [
                {
                    "price": b.get("price", 0),
                    "quantity": b.get("quantity", 0),
                    "orders": b.get("orders", 0),
                }
                for b in buy_levels[:5]
            ]
            sell_out = [
                {
                    "price": s.get("price", 0),
                    "quantity": s.get("quantity", 0),
                    "orders": s.get("orders", 0),
                }
                for s in sell_levels[:5]
            ]

            total_bid_qty = sum(b.get("quantity", 0) for b in buy_out)
            total_ask_qty = sum(s.get("quantity", 0) for s in sell_out)

            bid = buy_out[0]["price"] if buy_out else 0
            ask = sell_out[0]["price"] if sell_out else 0
            spread = ask - bid if bid and ask else 0
            mid = (ask + bid) / 2 if (ask + bid) > 0 else 0
            spread_pct = (spread / mid * 100) if mid > 0 else 0

            return {
                "bids": buy_out,
                "asks": sell_out,
                "buy_levels": buy_out,
                "sell_levels": sell_out,
                "total_buy_qty": total_bid_qty,
                "total_sell_qty": total_ask_qty,
                "bid_ask_spread": round(spread, 2),
                "spread_pct": round(spread_pct, 4),
                "volume_imbalance": (total_bid_qty - total_ask_qty)
                / (total_bid_qty + total_ask_qty + 1e-10),
            }
        except Exception as e:
            log.warning(f"Zerodha orderbook failed for {symbol}: {e}")
            return None

    def get_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        """Build option chain from Zerodha master contract cache + live quotes."""
        if not self.cache_ready:
            log.debug(
                f"ZerodhaBroker: master contract not ready for option chain {symbol}"
            )
            return None

        target = symbol.upper()
        INDEX_SYMBOLS = {
            "NIFTY",
            "BANKNIFTY",
            "FINNIFTY",
            "MIDCPNIFTY",
            "SENSEX",
            "BANKEX",
            "SENSEX50",
            "INDIAVIX",
            "NIFTYNEXT50",
        }

        nfo_instruments = []
        seen_ids = set()
        for inst in self.instruments_cache.values():
            if inst.get("exchange") != "NFO":
                continue
            itype = inst.get("instrument_type", "")
            allowed_types = ("CE", "PE", "FUT")
            if target in INDEX_SYMBOLS:
                allowed_types = ("CE", "PE", "FUT", "FUTIDX", "OPTIDX")
            if itype not in allowed_types:
                continue
            name = inst.get("name", "")
            if name != target:
                continue
            inst_id = id(inst)
            if inst_id in seen_ids:
                continue
            seen_ids.add(inst_id)
            nfo_instruments.append(inst)

        if not nfo_instruments:
            return None

        from collections import defaultdict

        by_expiry = defaultdict(list)
        for inst in nfo_instruments:
            exp = inst.get("expiry", "")
            if exp:
                by_expiry[exp].append(inst)

        expiry_dates = sorted(by_expiry.keys())
        if not expiry_dates:
            return None

        selected_expiry = expiry
        if selected_expiry:
            selected_expiry = self._normalize_api_expiry(selected_expiry)
            for exp in expiry_dates:
                if (
                    exp == selected_expiry
                    or self._normalize_api_expiry(exp) == selected_expiry
                ):
                    selected_expiry = exp
                    break
            else:
                selected_expiry = expiry_dates[0]
        else:
            selected_expiry = expiry_dates[0]

        instruments = by_expiry[selected_expiry]
        strikes = defaultdict(lambda: {"CE": {}, "PE": {}})
        option_symbols = []

        for inst in instruments:
            itype = inst.get("instrument_type", "")
            strike = inst.get("strike", 0)
            tsym = inst.get("tradingsymbol", "")
            lot = inst.get("lot_size", 0)
            if itype in ("CE", "PE", "OPTIDX") and strike and tsym:
                strikes[strike][itype] = {
                    "tradingSymbol": tsym,
                    "symbol": tsym,
                    "strike": strike,
                    "optionType": "CE" if "CE" in itype.upper() else "PE",
                    "lotSize": lot,
                }
                option_symbols.append(f"NFO:{tsym}")
            elif itype in ("FUT", "FUTIDX"):
                strikes["FUT"][itype] = {
                    "tradingSymbol": tsym,
                    "symbol": tsym,
                    "lotSize": lot,
                    "strike": 0,
                    "optionType": "FUT",
                }
                option_symbols.append(f"NFO:{tsym}")

        # Enrich with live prices via batch quote
        prices = {}
        if option_symbols and self.connected and self.kite:
            try:
                self._apply_rate_limit()
                for i in range(0, len(option_symbols), 500):
                    batch = option_symbols[i : i + 500]
                    data = self.kite.quote(*batch)
                    if data:
                        for key, q in data.items():
                            sym = key.split(":", 1)[-1]
                            prices[sym] = {
                                "lastPrice": q.get("last_price", 0),
                                "ltp": q.get("last_price", 0),
                                "price": q.get("last_price", 0),
                                "oi": q.get("oi", 0),
                                "volume": q.get("volume", 0),
                            }
            except Exception as e:
                log.debug(f"ZerodhaBroker option chain price fetch failed: {e}")

        records_data = []

        def sort_key(item):
            k = item[0]
            if k == "FUT":
                return (0, 0)
            return (1, k if isinstance(k, (int, float)) else 0)

        for strike, sides in sorted(strikes.items(), key=sort_key):
            if strike == "FUT":
                fut = sides.get("FUT", {})
                if fut:
                    sym = fut.get("tradingSymbol", "")
                    records_data.append(
                        {
                            **fut,
                            "strikePrice": 0,
                            "expiryDate": selected_expiry,
                        }
                    )
                continue
            rec = {
                "strikePrice": strike,
                "expiryDate": selected_expiry,
            }
            for side_key in ("CE", "PE"):
                if sides.get(side_key):
                    side = dict(sides[side_key])
                    sym = side.get("tradingSymbol", "")
                    if sym and sym in prices:
                        side.update(prices[sym])
                    rec[side_key] = side
            if "CE" in rec or "PE" in rec:
                records_data.append(rec)

        # Convert expiry dates to NSE-friendly format
        nse_expiry_dates = []
        for exp in expiry_dates:
            nse_expiry_dates.append(self._format_z_expiry(exp))

        result = {
            "data": {
                "records": {
                    "data": records_data,
                    "expiryDates": nse_expiry_dates,
                }
            },
            "symbol": target,
            "is_index": target in INDEX_SYMBOLS,
            "source": "zerodha_cache",
        }
        return result

    def get_stock_option_chain(self, symbol: str) -> dict | None:
        """Get stock options chain data. Uses the master contract + batch quotes."""
        return self.get_option_chain(symbol)

    def _format_z_expiry(self, expiry: str) -> str:
        """Convert Zerodha expiry (YYYY-MM-DD or YYYYMMDD) to NSE-friendly DD-Mon-YYYY."""
        if not expiry:
            return expiry
        cleaned = expiry.strip()
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%b-%Y"):
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.strftime("%d-%b-%Y")
            except ValueError:
                continue
        return cleaned

    def _normalize_api_expiry(self, expiry: str) -> str:
        """Normalize expiry strings to DDMMMYY (e.g., 30JUN26)."""
        if not expiry:
            return expiry
        cleaned = expiry.strip().upper()
        cleaned = cleaned.replace("/", "-").replace(" ", "")
        if (
            len(cleaned) == 7
            and cleaned[:2].isdigit()
            and cleaned[-3:].isalpha()
            and cleaned[2:5].isalpha()
        ):
            return cleaned[:2] + cleaned[2:5].upper() + cleaned[5:].upper()
        if len(cleaned) == 9 and cleaned[:2].isdigit() and cleaned[5:8].isalpha():
            day = cleaned[:2]
            mon = cleaned[5:8].upper()
            year = cleaned[-2:]
            return day + mon + year
        try:
            dt = datetime.strptime(cleaned, "%d%b%y")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%d%b%Y")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%Y-%m-%d")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        try:
            dt = datetime.strptime(cleaned, "%Y%m%d")
            return dt.strftime("%d%b%y").upper()
        except Exception:
            pass
        return expiry

    def get_positions(self) -> list[Position]:
        return []

    def get_order_history(self) -> list[dict]:
        return []

    def cancel_order(self, order_id: str) -> dict:
        return {"status": "error", "message": "Not implemented"}

    def get_historical_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        """Fetch historical data for *symbol* using a safe 30‑day window.

        The previous implementation incorrectly used ``timedelta(days=count)``
        which could exceed Kite's 100‑day limit for granular intervals (e.g.,
        ``5minute``). This caused ``interval exceeds max limit`` errors for many
        symbols. The method now mirrors the earlier implementation: it
        applies rate‑limiting, retrieves the instrument token, and requests data
        for the most recent 30 days, then trims the result to the requested
        ``count`` of candles.
        """
        if not self.connected or not self.kite:
            return []
        try:
            self._apply_rate_limit()
            from datetime import datetime, timedelta

            instrument = self.get_instrument(symbol)
            if not instrument:
                log.warning(f"Instrument not found for historical data: {symbol}")
                return []
            to_date = datetime.now()
            # Use a 30‑day window (safe under the 100‑day limit).
            from_date = to_date - timedelta(days=30)
            raw = self.kite.historical_data(
                instrument.get("instrument_token"),
                from_date,
                to_date,
                interval,
            )
            if not raw:
                return []
            candles = []
            for rec in raw:
                candles.append(
                    {
                        "timestamp": rec.get("date"),
                        "open": float(rec.get("open", 0)),
                        "high": float(rec.get("high", 0)),
                        "low": float(rec.get("low", 0)),
                        "close": float(rec.get("close", 0)),
                        "volume": int(rec.get("volume", 0)),
                    }
                )
            # Return the most recent ``count`` candles.
            return candles[-count:]
        except Exception as e:
            log.debug(f"Historical data error for {symbol}: {e}")
            return []
        except Exception as e:
            log.error(f"Zerodha historical failed for {symbol}: {e}")
            return []

    def get_lot_size(self, symbol: str) -> int:
        """Get lot size from master contract cache or fallback to JSON file"""
        # Try cache first (O(1) lookup)
        if self.cache_ready:
            instrument = self.get_instrument(symbol)
            if instrument:
                lot_size = instrument.get("lot_size")
                if lot_size:
                    log.debug(f"Lot size for {symbol}: {lot_size} (from cache)")
                    return int(lot_size)

        # Fallback to JSON file
        import json
        import os

        cache_file = os.path.join(
            os.path.dirname(__file__), "..", "data", "lot_sizes.json"
        )

        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < 24 * 3600:
                with open(cache_file, "r") as f:
                    lot_sizes = json.load(f)
                    return lot_sizes.get(symbol.upper(), 25)

        # Fallback defaults
        default_sizes = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "NIFTY50": 65,
            "SENSEX": 20,
            "BANKEX": 30,
            "SENSEX50": 75,
        }

        # Return default size for indices
        if symbol.upper() in default_sizes:
            return default_sizes[symbol.upper()]

        # Default fallback for stocks
        return 25


class AngelOneBroker(BrokerInterface):
    """Angel One SmartAPI broker implementation"""

    def __init__(self, config: dict):
        self.config = config
        self.name = "AngelOne"
        self.connected = False
        self.api = None

    def connect(self) -> bool:
        try:
            try:
                from smartapi import SmartConnect
            except ModuleNotFoundError:
                from SmartApi import SmartConnect

            self.api = SmartConnect(self.config.get("api_key"))
            data = self.api.generateSession(
                self.config.get("client_id"),
                self.config.get("password"),
                self.config.get("totp"),
            )
            if data.get("status"):
                self.connected = True
                log.info("Angel One broker connected")
                return True
        except Exception as e:
            log.error(f"Angel One connection failed: {e}")
            return False

    def disconnect(self) -> None:
        self.connected = False
        log.info("Angel One broker disconnected")

    def place_order(self, order: Order) -> dict:
        if not self.connected:
            return {"status": "error", "message": "Not connected"}

        try:
            params = {
                "variety": "NORMAL",
                "exchange": order.exchange,
                "symbol": order.symbol,
                "transactiontype": order.transaction_type,
                "quantity": order.quantity,
                "producttype": order.product_type,
                "ordertype": order.order_type,
            }
            if order.price:
                params["price"] = order.price

            result = self.api.placeOrder(params)
            return {"status": "success", "order_id": result}
        except Exception as e:
            log.error(f"Angel One order failed: {e}")
            return {"status": "error", "message": str(e)}

    def get_quote(self, symbol: str) -> Quote | None:
        if not self.connected:
            return None

        try:
            data = self.api.ltpData("NSE", symbol)
            if data.get("status"):
                return Quote(
                    symbol=symbol,
                    last_price=data.get("data", {}).get("lastprice", 0),
                    volume=0,
                    bid=0,
                    ask=0,
                )
        except Exception as e:
            log.error(f"Angel One quote failed: {e}")
        return None

    def get_positions(self) -> list[Position]:
        return []

    def get_order_history(self) -> list[dict]:
        return []

    def cancel_order(self, order_id: str) -> dict:
        return {"status": "error", "message": "Not implemented"}

    def get_historical_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        return []

    def get_lot_size(self, symbol: str) -> int:
        import json
        import os

        cache_file = os.path.join(
            os.path.dirname(__file__), "..", "data", "lot_sizes.json"
        )

        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < 24 * 3600:
                with open(cache_file, "r") as f:
                    lot_sizes = json.load(f)
                    return lot_sizes.get(symbol.upper(), 25)

        default_sizes = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "NIFTY50": 65,
            "SENSEX": 20,
            "BANKEX": 30,
            "SENSEX50": 75,
        }

        # Return default size for indices
        if symbol.upper() in default_sizes:
            return default_sizes[symbol.upper()]

        # Default fallback for stocks
        return 25


class PaperBroker(BrokerInterface):
    """Paper trading broker - simulates real trading"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.name = "PaperBroker"
        self.connected = True
        self.orders = []
        self.positions = {}

    def connect(self) -> bool:
        log.info("Paper broker initialized")
        return True

    def disconnect(self) -> None:
        pass

    def place_order(self, order: Order) -> dict:
        order_id = f"PAPER_{len(self.orders) + 1}"

        self.orders.append(
            {
                "order_id": order_id,
                "symbol": order.symbol,
                "quantity": order.quantity,
                "price": order.price or 0,
                "status": "success",
            }
        )

        if order.symbol not in self.positions:
            self.positions[order.symbol] = {"qty": 0, "avg_price": 0}

        pos = self.positions[order.symbol]
        if order.transaction_type == "BUY":
            pos["qty"] += order.quantity
        else:
            pos["qty"] -= order.quantity

        return {"status": "success", "order_id": order_id}

    def get_quote(self, symbol: str) -> Quote | None:
        return Quote(symbol=symbol, last_price=100.0, volume=1000, bid=99.5, ask=100.5)

    def get_positions(self) -> list[Position]:
        return [
            Position(sym, data["qty"], data["avg_price"], 100.0, 0, "FLAT")
            for sym, data in self.positions.items()
            if data["qty"] != 0
        ]

    def get_order_history(self) -> list[dict]:
        return self.orders

    def cancel_order(self, order_id: str) -> dict:
        return {"status": "success"}

    def get_historical_data(self, symbol: str, interval: str, count: int) -> list[dict]:
        import random

        base = 100
        data = []
        for i in range(count):
            data.append(
                {
                    "timestamp": f"2024-01-{i + 1:02d}",
                    "open": base + random.uniform(-2, 2),
                    "high": base + random.uniform(0, 3),
                    "low": base + random.uniform(-3, 0),
                    "close": base + random.uniform(-2, 2),
                    "volume": random.randint(1000, 10000),
                }
            )
        return data

    def get_lot_size(self, symbol: str) -> int:
        import json
        import os

        cache_file = os.path.join(
            os.path.dirname(__file__), "..", "data", "lot_sizes.json"
        )

        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < 24 * 3600:
                with open(cache_file, "r") as f:
                    lot_sizes = json.load(f)
                    return lot_sizes.get(symbol.upper(), 25)

        default_sizes = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "NIFTY50": 65,
            "SENSEX": 20,
            "BANKEX": 30,
            "SENSEX50": 75,
        }

        # Return default size for indices
        if symbol.upper() in default_sizes:
            return default_sizes[symbol.upper()]

        # Default fallback for stocks
        return 25


def create_broker(broker_type: str, config: dict) -> BrokerInterface:
    """Factory function to create broker instances"""
    broker_type_lower = broker_type.lower()

    brokers = {
        "zerodha": ZerodhaBroker,
        "angelone": AngelOneBroker,
        "paper": PaperBroker,
        "nselive": NSELiveBroker,
    }

    broker_class = brokers.get(broker_type_lower, NSELiveBroker)
    return broker_class(config)


def create_multi_broker(brokers_config: dict) -> list[BrokerInterface]:
    """Create multiple broker instances"""
    brokers = []
    for broker_type, config in brokers_config.items():
        broker = create_broker(broker_type, config)
        if broker.connect():
            brokers.append(broker)

    if not brokers:
        log.warning("No live brokers connected, falling back to paper")
        brokers.append(PaperBroker({"mode": "paper"}))

    return brokers
