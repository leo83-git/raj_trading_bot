# ═══════════════════════════════════════════════════════════════
#  Data Provider — Aggregates market data from multiple brokers
# ═══════════════════════════════════════════════════════════════
import asyncio
import concurrent.futures
import inspect
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from quant_utils.logger import get_logger
from utils.cache import (
    get_cached_fno_contracts,
    get_cached_option_chain,
    get_cached_quote,
    set_cached_fno_contracts,
    set_cached_option_chain,
    set_cached_quote,
)

from ..indian_stock_api import NSEIndiaData

log = get_logger("sources.data_provider")


class CircuitBreaker:
    """Small failure gate for noisy fallback providers."""

    def __init__(self, max_failures: int = 3, reset_after_seconds: float = 60.0) -> None:
        self.max_failures = max_failures
        self.reset_after_seconds = reset_after_seconds
        self.failures = 0
        self.last_failure_time = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.failures < self.max_failures:
                return True
            return (time.time() - self.last_failure_time) >= self.reset_after_seconds

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.last_failure_time = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.last_failure_time = time.time()


class DataProvider:
    """Unified market data from multiple broker sources"""

    def __init__(self, brokers: list[Any], config: dict = None):
        self.brokers = brokers
        self.config = config or {}
        self.cache = {}
        self.cache_ttl = self.config.get("cache_ttl", 60)
        # Realistic default timeouts optimized for NSE
        self.historical_timeout = self.config.get("historical_timeout", 10)
        self.intraday_timeout = self.config.get("intraday_timeout", 12)
        self.quote_timeout = self.config.get(
            "quote_timeout", 12
        )  # Increased default for reliability under load
        # Retry configuration - increased for reliability under NSE rate limiting
        self.max_retries = self.config.get("max_retries", 5)
        self.broker_max_concurrency = self.config.get("broker_max_concurrency", 3)
        self.candle_rate_limit_delay = self.config.get("candle_rate_limit_delay", 0.35)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(4, self.broker_max_concurrency * 2)
        )
        self._broker_semaphore = threading.Semaphore(self.broker_max_concurrency)
        self._candle_rate_lock = threading.Lock()
        self._last_candle_request_time = 0.0

        self.symbols_cache = {}
        self.instrument_master_cache = {"data": [], "fetched_date": None}
        self.fallback_api = NSEIndiaData()
        self._fallback_breaker = CircuitBreaker(max_failures=3, reset_after_seconds=60.0)
        # VIX cache to prevent repeated rate-limited requests
        self._vix_cache = None
        self._vix_cache_time = None

        log.info(f"Data provider initialized with {len(brokers)} brokers")
        log.info(
            f"Data provider broker concurrency limited to {self.broker_max_concurrency} active requests"
        )
        log.info(
            f"Data provider quote timeout set to {self.quote_timeout}s (for NSE operations)"
        )
        log.info(
            f"Data provider max_retries set to {self.max_retries}, received config: {self.config}"
        )

    def _is_valid_quote(self, quote: Any) -> bool:
        """Check if quote object (Quote or dict) has valid last_price"""
        if quote is None:
            return False
        # Handle both Quote objects and dictionaries
        if isinstance(quote, dict):
            price = quote.get("last_price", 0)
            return price and float(price) > 0
        else:
            # Quote object - check attribute
            price = getattr(quote, "last_price", None)
            return price is not None and float(price) > 0

    def _is_valid_option_chain(self, chain: Any) -> bool:
        if not chain:
            return False
        if isinstance(chain, dict):
            data = chain.get("data")
            if isinstance(data, list) and data:
                return True
            if isinstance(data, dict):
                records = data.get("records") or data.get("filtered") or data
                if isinstance(records, dict):
                    options = records.get("data")
                    if isinstance(options, list) and options:
                        return True
                    filtered = records.get("filtered", {})
                    if isinstance(filtered, dict):
                        options = filtered.get("data")
                        if isinstance(options, list) and options:
                            return True
            source = chain.get("source")
            if source in ("sensibull", "opstra"):
                log.debug(
                    f"Option chain from {source} accepted without strict validation"
                )
                return True
            if source:
                return True
        elif isinstance(chain, list) and chain:
            return True
        log.debug(
            f"Invalid option chain rejected: type={type(chain).__name__}, keys={list(chain.keys()) if isinstance(chain, dict) else 'N/A'}"
        )
        return False

    def _safe_broker_call(self, func, *args, timeout: float = 10.0, **kwargs):
        """Run broker calls in a thread and enforce a timeout while limiting active broker requests."""
        if not hasattr(self, "_executor"):
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max(4, self.broker_max_concurrency * 2)
            )
        if not hasattr(self, "_broker_semaphore"):
            self._broker_semaphore = threading.Semaphore(self.broker_max_concurrency)

        def safe_call():
            with self._broker_semaphore:
                try:
                    log.debug(
                        f"Executing broker call {getattr(func, '__name__', repr(func))} args={args} kwargs_keys={list(kwargs.keys())}"
                    )
                except Exception:
                    pass
                result = func(*args, **kwargs)
                try:
                    log.debug(
                        f"Broker call {getattr(func, '__name__', repr(func))} completed, returned_type={type(result).__name__}"
                    )
                except Exception:
                    pass
                return result

        future = self._executor.submit(safe_call)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            log.debug(
                f"Broker call timed out after {timeout}s: {getattr(func, '__name__', repr(func))}({args}, {kwargs})"
            )
            try:
                future.cancel()
            except Exception:
                pass
            raise
        except Exception as exc:
            log.debug(f"Broker call failed: {exc}")
        return None

    def _get_broker_quote(
        self, broker: Any, symbol: str, exchange: str = "NSE"
    ) -> Any | None:
        """Call broker.get_quote while forwarding exchange when supported."""
        try:
            sig = inspect.signature(broker.get_quote)
            params = sig.parameters
            if "exchange" in params and "timeout" in params:
                return broker.get_quote(symbol, exchange, timeout=self.quote_timeout)
            if "exchange" in params:
                return broker.get_quote(symbol, exchange)
            if "timeout" in params:
                return broker.get_quote(symbol, timeout=self.quote_timeout)
            return broker.get_quote(symbol)
        except TypeError:
            try:
                return broker.get_quote(symbol, timeout=self.quote_timeout)
            except Exception as exc:
                log.debug(
                    f"Broker {getattr(broker, 'name', 'unknown')} get_quote(timeout) failed: {exc}"
                )
        except Exception as exc:
            log.debug(
                f"Broker {getattr(broker, 'name', 'unknown')} get_quote failed: {exc}"
            )
        return None

    def get_quote(self, symbol: str, exchange: str = "NSE") -> dict | None:
        """Get quote with retry logic, exponential backoff and caching."""
        cache_key = f"{exchange}:{symbol}"

        # --------------------------------------------------------------
        # 1️⃣  Futures‑symbol early‑exit – ignore FUT symbols entirely.
        # --------------------------------------------------------------
        if self._is_futures_symbol(symbol):
            # Futures symbols are not supported for NFO‑OPT processing; downgrade log level to debug to reduce noise.
            log.debug(f"Ignoring futures symbol {symbol} – NFO‑OPT only")
            # Returning ``None`` signals that no quote is available for this
            # symbol. Callers typically guard with ``if quote and isinstance(...):``
            # so ``None`` will be treated as “no data”.
            return None

        # Check optimized cache first
        cached = get_cached_quote(cache_key)
        if cached:
            return cached

        # Check local cache
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if (datetime.now() - cached["timestamp"]).seconds < self.cache_ttl:
                set_cached_quote(cache_key, cached["data"])  # Sync to optimized cache
                return cached["data"]

        # Try each broker with retries and exponential backoff
        for broker in self.brokers:
            attempt = 0
            while attempt < self.max_retries:
                try:
                    # Increase timeout slightly on retries
                    timeout = (
                        self.quote_timeout
                        if attempt == 0
                        else min(self.quote_timeout + attempt * 5, 20)
                    )
                    quote = self._safe_broker_call(
                        self._get_broker_quote,
                        broker,
                        symbol,
                        exchange,
                        timeout=timeout,
                    )

                    if not self._is_valid_quote(quote):
                        log.debug(
                            f"[Attempt {attempt + 1}] Broker {broker.name} returned invalid quote for {symbol}: {quote}"
                        )
                        attempt += 1
                        if attempt < self.max_retries:
                            # Use longer backoff for server errors (2s, 4s, 8s, 16s)
                            time.sleep(min(2 ** (attempt + 1), 16))
                        continue

                    # Convert Quote object to dict for caching
                    if isinstance(quote, dict):
                        quote_dict = quote
                    else:
                        quote_dict = {
                            "last_price": getattr(quote, "last_price", 0),
                            "volume": getattr(quote, "volume", 0),
                            "bid": getattr(quote, "bid", 0),
                            "ask": getattr(quote, "ask", 0),
                        }

                    data_obj = {
                        "symbol": symbol,
                        "exchange": exchange,
                        **quote_dict,
                        "timestamp": datetime.now(),
                        "source": getattr(broker, "name", "broker"),
                        "provenance": "broker",
                        "freshness_seconds": 0.0,
                    }

                    self.cache[cache_key] = {
                        "data": data_obj,
                        "timestamp": datetime.now(),
                    }
                    set_cached_quote(cache_key, data_obj)
                    return data_obj

                except concurrent.futures.TimeoutError:
                    log.debug(
                        f"[Attempt {attempt + 1}] Timeout fetching {symbol} from {broker.name} (timeout={timeout}s)"
                    )
                    attempt += 1
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt + 1), 16))
                except Exception as e:
                    log.debug(
                        f"[Attempt {attempt + 1}] Error fetching {symbol} from {broker.name}: {e}"
                    )
                    attempt += 1
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt + 1), 16))

        # Early exit for expired options to avoid fallback API spam
        if self._is_expired_option_symbol(symbol):
            log.info(f"Skipping fallback for expired option {symbol}")
            return {
                "symbol": symbol,
                "exchange": exchange,
                "last_price": 0,
                "volume": 0,
                "bid": 0,
                "ask": 0,
                "timestamp": datetime.now(),
            }

        # Skip fallback for futures symbols which are not supported by the
        # generic fallback API (NSEIndiaData). This prevents repeated 404
        # errors and reduces log noise.
        if self._is_futures_symbol(symbol):
            log.info(f"Skipping fallback for futures symbol {symbol}")
            return {
                "symbol": symbol,
                "exchange": exchange,
                "last_price": 0,
                "volume": 0,
                "bid": 0,
                "ask": 0,
                "timestamp": datetime.now(),
            }

        # -----------------------------------------------------------------
        # Fast‑fail for known problematic symbols (e.g., BANKEX option contracts)
        # -----------------------------------------------------------------
        # Certain symbols such as BANKEX option contracts consistently return
        # 404 from the fallback API. To avoid unnecessary network latency and
        # log noise, we short‑circuit the fallback for these cases.
        if symbol.upper().startswith("BANKEX") and (
            "CE" in symbol.upper() or "PE" in symbol.upper()
        ):
            log.debug(f"Skipping fallback API for known failing symbol {symbol}")
            return {
                "symbol": symbol,
                "exchange": exchange,
                "last_price": 0,
                "volume": 0,
                "bid": 0,
                "ask": 0,
                "timestamp": datetime.now(),
            }

        # Fallback to Indian stock API + yfinance if broker quotes fail
        try:
            if not self._fallback_breaker.allow():
                log.debug(f"Fallback breaker open for {symbol}; returning cached/no data")
                return None
            log.warning(f"All brokers failed for {symbol}, trying fallback API")
            fallback = self.fallback_api.get_quote(symbol)
            if fallback:
                data_obj = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "last_price": fallback.get("last_price", 0),
                    "volume": fallback.get("volume", 0),
                    "bid": fallback.get("bid", 0),
                    "ask": fallback.get("ask", 0),
                    "timestamp": datetime.now(),
                    "source": "fallback_api",
                    "provenance": "nseindia+yfinance",
                    "freshness_seconds": 0.0,
                }
                self.cache[cache_key] = {"data": data_obj, "timestamp": datetime.now()}
                set_cached_quote(cache_key, data_obj)
                self._fallback_breaker.record_success()
                return data_obj
            self._fallback_breaker.record_failure()
        except Exception as e:
            log.debug(f"Fallback quote fetch failed for {symbol}: {e}")
            self._fallback_breaker.record_failure()

        return None

    # ---------------------------------------------------------------------
    # Market‑data batch API used by ``download_market_snapshots``
    # ---------------------------------------------------------------------
    def get_market_data(self, tokens: list[int]) -> dict[int, dict]:
        """Return a mapping of ``instrument_token`` → price information.

        The original code expected a ``DataProvider.get_market_data`` method that
        accepts a list of instrument tokens and returns a dict where each key is
        the token and the value is a dict containing at least ``"last_price"``.

        For the purposes of the test suite we provide a lightweight
        implementation that:

        1. Looks up the symbol/exchange for each token using the first broker's
           ``_load_instruments`` cache (if available).
        2. Calls :meth:`get_quote` for the resolved symbol/exchange.
        3. Constructs a minimal payload ``{"last_price": <price>}``.

        If a token cannot be resolved or a quote is unavailable, the token is
        omitted from the result – the caller already tolerates missing entries.
        """
        result: dict[int, dict] = {}
        if not tokens:
            return result

        # Use the first broker that can provide an instrument list.
        broker = None
        for b in self.brokers:
            if hasattr(b, "_load_instruments"):
                broker = b
                break
        if broker is None:
            log.debug(
                "DataProvider.get_market_data called but no broker with _load_instruments available"
            )
            return result

        # Build a token → instrument mapping for quick lookup.
        try:
            instruments = broker._load_instruments()
        except Exception as exc:
            log.debug(f"Failed to load instruments for market data batch: {exc}")
            instruments = []
        token_map = {
            inst.get("instrument_token"): inst
            for inst in instruments
            if inst.get("instrument_token")
        }

        for token in tokens:
            inst = token_map.get(token)
            if not inst:
                continue
            symbol = inst.get("tradingsymbol")
            exchange = inst.get("exchange", "NSE")
            quote = self.get_quote(symbol, exchange)
            if quote and isinstance(quote, dict):
                ltp = quote.get("last_price")
                if ltp is not None:
                    result[token] = {"last_price": ltp}
        return result

    async def get_quote_async(self, symbol: str, exchange: str = "NSE") -> dict | None:
        """Async wrapper for get_quote to keep compatibility with async callers."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_quote, symbol, exchange)

    def get_index_price(self, symbol: str) -> float | None:
        """Get index price"""
        quote = self.get_quote(symbol)
        return quote.get("last_price") if quote else None

    def _is_expired_option_symbol(self, symbol: str) -> bool:
        """Detect if an option symbol has expired (today or earlier)."""
        import re
        from datetime import date, datetime

        symbol = symbol.upper()
        # Match format like NIFTY26MAY2623700PE
        match = re.match(r"^([A-Z]+?)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$", symbol)
        if not match:
            return False

        expiry_token = match.group(2)
        try:
            day = int(expiry_token[:2])
            month_str = expiry_token[2:5]
            year = 2000 + int(expiry_token[5:])
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
            month = month_map.get(month_str)
            if month:
                expiry_date = datetime(year, month, day).date()
                return expiry_date <= date.today()
        except Exception:
            pass
        return False

    def _is_futures_symbol(self, symbol: str) -> bool:
        """Detect simple futures symbols.

        Returns ``True`` for symbols that end with ``FUT`` (e.g., ``BANKEX26JULFUT``).
        These symbols are not supported by the generic fallback API, so they
        are handled specially to avoid unnecessary 404 errors.
        """
        return symbol.upper().endswith("FUT")

    def _apply_candle_rate_limit(self) -> None:
        """Throttle historical candle requests to respect broker rate limits."""
        # Avoid holding the lock while sleeping — compute the required wait
        while True:
            with self._candle_rate_lock:
                elapsed = time.time() - self._last_candle_request_time
                if elapsed >= self.candle_rate_limit_delay:
                    # It's our turn: reserve the timestamp and proceed
                    self._last_candle_request_time = time.time()
                    return
                sleep_time = self.candle_rate_limit_delay - elapsed
            # Sleep without holding the lock so other threads can check/reserve
            log.debug(f"Enforcing candle rate limit, sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

    def get_candles(
        self, exchange: str, symbol: str, interval: str, count: int
    ) -> list[dict]:
        """Get historical candles"""
        cache_key = f"{exchange}:{symbol}:{interval}:{count}"

        if cache_key in self.symbols_cache:
            return self.symbols_cache[cache_key]

        interval_key = interval.lower().replace("minute", "m").replace("min", "m")
        timeout = (
            self.intraday_timeout
            if any(
                tok in interval_key
                for tok in ["m", "5m", "15m", "30m", "1m", "2m", "3m"]
            )
            else self.historical_timeout
        )

        for broker in self.brokers:
            attempt = 0
            while attempt < self.max_retries:
                self._apply_candle_rate_limit()
                current_timeout = (
                    timeout if attempt == 0 else min(timeout + attempt * 5, 30)
                )
                try:
                    candles = self._safe_broker_call(
                        broker.get_historical_data,
                        symbol,
                        interval,
                        count,
                        timeout=current_timeout,
                    )
                    if candles:
                        self.symbols_cache[cache_key] = candles
                        return candles
                    log.debug(
                        f"[Attempt {attempt + 1}] Broker {getattr(broker, 'name', 'unknown')} returned no candles for {symbol}/{interval}/{count}"
                    )
                    break
                except concurrent.futures.TimeoutError:
                    attempt += 1
                    log.debug(
                        f"[Attempt {attempt}] Timeout fetching candles for {symbol} from {getattr(broker, 'name', 'unknown')} (timeout={current_timeout}s)"
                    )
                    if attempt < self.max_retries:
                        time.sleep(min(2**attempt, 10))
                    continue
                except Exception as e:
                    attempt += 1
                    log.debug(
                        f"[Attempt {attempt}] Candles fetch failed for {symbol} from {getattr(broker, 'name', 'unknown')}: {e}"
                    )
                    if attempt < self.max_retries:
                        time.sleep(min(2**attempt, 10))
                    continue

        # Fallback to Indian stock API / yfinance for historical candles
        try:
            fallback = self.fallback_api.get_historical(
                symbol, days=max(10, count // 8 + 1)
            )
            if fallback:
                self.symbols_cache[cache_key] = fallback
                return fallback
        except Exception as e:
            log.debug(f"Fallback candles fetch failed for {symbol}: {e}")

        return []

    def get_live_ohlc(self, symbol: str) -> dict | None:
        """Get live OHLC data"""
        quote = self.get_quote(symbol)
        if quote:
            return {
                "open": quote.get("last_price"),
                "high": quote.get("last_price") * 1.01,
                "low": quote.get("last_price") * 0.99,
                "close": quote.get("last_price"),
                "volume": quote.get("volume", 0),
            }
        return None

    def get_multiple_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """Get quotes for multiple symbols in parallel.

        Uses an asyncio-based gather to run `get_quote` in threads with a per-symbol timeout.
        Falls back to a thread pool approach when an event loop is already running.
        Prefers broker-native batch endpoints when available.
        """
        quotes: dict[str, dict | None] = {}
        if not symbols:
            return quotes

        for broker in self.brokers:
            if hasattr(broker, "get_multiple_quotes"):
                try:
                    batch_timeout = max(120.0, len(symbols) * 0.005)
                    log.debug(
                        f"Trying broker-native batch quote for {len(symbols)} symbols (timeout={batch_timeout:.0f}s)"
                    )
                    batch = self._safe_broker_call(
                        broker.get_multiple_quotes, symbols, timeout=batch_timeout
                    )
                    log.debug(
                        f"Broker batch quote returned type={type(batch).__name__}, len={len(batch) if isinstance(batch, dict) else 'N/A'}"
                    )
                    if isinstance(batch, dict) and batch:
                        quotes.update(batch)
                        return quotes
                    log.debug(
                        "Broker batch quote returned no data, falling back to individual quote fetches"
                    )
                except Exception as e:
                    log.debug(
                        f"Broker {getattr(broker, 'name', 'unknown')} get_multiple_quotes failed: {e}"
                    )

        timeout_per = min(self.quote_timeout + self.max_retries * 5 + 5, 30)

        async def _gather():
            tasks = []
            for sym in symbols:
                tasks.append(
                    asyncio.create_task(
                        asyncio.wait_for(
                            asyncio.to_thread(self.get_quote, sym), timeout=timeout_per
                        )
                    )
                )
            return await asyncio.gather(*tasks, return_exceptions=True)

        try:
            try:
                loop = asyncio.get_running_loop()
                running_loop = True
            except RuntimeError:
                running_loop = False

            if not running_loop:
                results = asyncio.run(_gather())
                for sym, res in zip(symbols, results):
                    if isinstance(res, Exception) or res is None:
                        continue
                    quotes[sym] = res
                return quotes
            else:
                # Running inside an event loop - use a bounded thread pool with per-future timeouts
                max_workers = min(self.broker_max_concurrency * 2, max(4, len(symbols)))
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers
                ) as executor:
                    future_to_symbol = {
                        executor.submit(self.get_quote, sym): sym for sym in symbols
                    }
                    for future in concurrent.futures.as_completed(future_to_symbol):
                        sym = future_to_symbol[future]
                        try:
                            res = future.result(timeout=timeout_per)
                            if res:
                                quotes[sym] = res
                        except Exception:
                            continue
                return quotes
        except Exception as e:
            log.debug(f"Parallel quote fetch failed: {e}")
            # Fallback to sequential fetch
            for sym in symbols:
                q = self.get_quote(sym)
                if q:
                    quotes[sym] = q
            return quotes

    def get_market_depth(self, symbol: str) -> dict | None:
        """Get market depth (order book)"""
        quote = self.get_quote(symbol)
        if quote:
            return {
                "bid": [{"price": quote.get("bid"), "quantity": 100}],
                "ask": [{"price": quote.get("ask"), "quantity": 100}],
            }
        return None

    def get_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        """Get options chain data with caching"""
        cache_key = f"{symbol}:{expiry or 'latest'}"

        # Check optimized cache first
        cached = get_cached_option_chain(cache_key)
        if cached:
            return cached

        for broker in self.brokers:
            try:
                if hasattr(broker, "get_option_chain"):
                    chain = self._safe_broker_call(
                        broker.get_option_chain, symbol, expiry, timeout=35
                    )
                    if self._is_valid_option_chain(chain):
                        set_cached_option_chain(cache_key, chain)
                        return chain
                    log.debug(
                        f"Broker {broker.name} returned invalid option chain for {symbol} expiry={expiry}"
                    )
            except Exception as e:
                log.debug(f"Option chain fetch failed from {broker.name}: {e}")
                continue
        return None

    def get_stock_option_chain(self, symbol: str, expiry: str = None) -> dict | None:
        """Get stock options chain data (for F&O stocks)"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_stock_option_chain"):
                    chain = self._safe_broker_call(
                        broker.get_stock_option_chain, symbol, timeout=35
                    )
                    if self._is_valid_option_chain(chain):
                        return chain
                    log.debug(
                        f"Broker {broker.name} returned invalid stock option chain for {symbol}"
                    )
            except Exception as e:
                log.debug(f"Stock option chain fetch failed from {broker.name}: {e}")
                continue
        return None

    def get_oi_pcr_data(self, symbol: str) -> dict | None:
        """Get OI and PCR data for indices"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_oi_pcr_data"):
                    data = self._safe_broker_call(
                        broker.get_oi_pcr_data, symbol, timeout=10
                    )
                    if data:
                        return data
            except Exception as e:
                log.debug(f"OI/PCR fetch failed from {broker.name}: {e}")
                continue
        return None

    def get_option_premium_scrape(
        self, symbol: str, strike: int, opt_type: str
    ) -> float | None:
        """Scrape option premium from NSE website"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_option_premium_scrape"):
                    premium = broker.get_option_premium_scrape(symbol, strike, opt_type)
                    if premium and premium > 0:
                        return premium
            except Exception as e:
                log.debug(f"Premium scrape failed from {broker.name}: {e}")
                continue
        return None

    def get_underlying_price(self, symbol: str) -> float | None:
        """Get underlying price from NSE"""
        for broker in self.brokers:
            try:
                if hasattr(broker, "get_underlying_price"):
                    price = broker.get_underlying_price(symbol)
                    if price and price > 0:
                        return price
            except Exception as e:
                log.debug(f"Underlying price failed from {broker.name}: {e}")
                continue
        return None

    def get_instrument_master(
        self, exchange: str = None, force_refresh: bool = False
    ) -> list[dict]:
        """Get instrument master data with daily refresh semantics."""
        today = datetime.now().date()
        if not force_refresh and self.instrument_master_cache["fetched_date"] == today:
            return self.instrument_master_cache["data"]

        for broker in self.brokers:
            try:
                if hasattr(broker, "get_instruments"):
                    instruments = self._safe_broker_call(
                        broker.get_instruments, exchange
                    )
                    if isinstance(instruments, list) and instruments:
                        self.instrument_master_cache = {
                            "data": instruments,
                            "fetched_date": today,
                        }
                        return instruments
            except Exception as e:
                log.debug(f"Instrument master fetch failed from {broker.name}: {e}")
                continue

        log.warning("No instrument master data available from configured brokers")
        return []

    def get_positions(self) -> list[dict]:
        """Get positions from all brokers"""
        positions = []
        for broker in self.brokers:
            try:
                broker_positions = broker.get_positions()
                positions.extend(broker_positions)
            except Exception as e:
                log.debug(f"Position fetch failed: {e}")
        return positions

    def clear_cache(self):
        """Clear data cache"""
        self.cache.clear()
        self.symbols_cache.clear()
        self.instrument_master_cache = {"data": [], "fetched_date": None}
        log.info("Data cache cleared")


class StreamingDataProvider(DataProvider):
    """Real-time streaming data provider"""

    def __init__(self, brokers: list[Any], config: dict = None):
        super().__init__(brokers, config)
        self.subscribers = {}
        self.last_update = {}

    def subscribe(self, symbol: str, callback: callable):
        """Subscribe to real-time updates"""
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
        self.subscribers[symbol].append(callback)

    def unsubscribe(self, symbol: str, callback: callable):
        """Unsubscribe from updates"""
        if symbol in self.subscribers:
            self.subscribers[symbol] = [
                cb for cb in self.subscribers[symbol] if cb != callback
            ]

    def notify_subscribers(self, symbol: str, data: dict):
        """Notify all subscribers of new data"""
        if symbol in self.subscribers:
            for callback in self.subscribers[symbol]:
                try:
                    callback(data)
                except Exception as e:
                    log.error(f"Subscriber callback failed: {e}")

    # Async methods for optimized fetching
    async def get_quote_async(self, symbol: str, exchange: str = "NSE") -> dict | None:
        """Async version of get_quote with caching."""
        cache_key = f"{exchange}:{symbol}"
        cached = get_cached_quote(cache_key)
        if cached:
            return cached

        # Fallback to sync for now, but can be made fully async later
        result = self.get_quote(symbol, exchange)
        if result:
            set_cached_quote(cache_key, result)
        return result

    async def get_option_chain_async(
        self, symbol: str, expiry: str = None
    ) -> dict | None:
        """Async version of get_option_chain with caching."""
        cache_key = f"{symbol}:{expiry or 'latest'}"
        cached = get_cached_option_chain(cache_key)
        if cached:
            return cached

        # Fallback to sync for now
        result = self.get_option_chain(symbol, expiry)
        if result:
            set_cached_option_chain(cache_key, result)
        return result

    async def fetch_multiple_quotes(
        self, symbols: list[str], exchange: str = "NSE"
    ) -> dict[str, dict]:
        """Fetch multiple quotes concurrently."""
        tasks = [self.get_quote_async(symbol, exchange) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            symbol: result
            for symbol, result in zip(symbols, results)
            if not isinstance(result, Exception) and result
        }

    async def fetch_multiple_option_chains(
        self, symbols: list[str], expiry: str = None
    ) -> dict[str, dict]:
        """Fetch multiple option chains concurrently."""
        tasks = [self.get_option_chain_async(symbol, expiry) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            symbol: result
            for symbol, result in zip(symbols, results)
            if not isinstance(result, Exception) and result
        }


# Module-level instances are created on-demand with proper brokers - not here with empty lists
# Use main.py's self.data_provider and self.streaming_provider instead
