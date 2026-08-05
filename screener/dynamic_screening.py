"""
Dynamic real-time stock screening using Zerodha OHLCV data and local TA calculations.
"""

import datetime
import logging
import queue
import threading
import time

from core.logger import get_logger
from core.zerodha_broker import ZerodhaBroker
from screener.fno_contract_loader import FnoContractLoader

try:
    from sources.broker import ZerodhaBroker as SourcesZerodhaBroker
except ImportError:
    SourcesZerodhaBroker = ZerodhaBroker

log = get_logger("dynamic_screening")
logging.getLogger("urllib3").setLevel(logging.CRITICAL)


class DynamicSymbolScreening:
    """Dynamic symbol screening system using real-time WebSocket data"""

    def __init__(self, broker):
        self.broker = broker
        self.screened_symbols = set()
        self.last_screened = None
        self.cache_ttl = 30
        self._cache_lock = threading.Lock()

        self._websocket_data = {}
        self._websocket_lock = threading.Lock()
        # timestamp of last time we logged that websocket cache was empty
        self._last_ws_cache_empty_log = 0
        # track last websocket cache update log to avoid repeat messages
        self._last_ws_cache_update_count = 0
        self._last_ws_cache_update_log_time = 0

        self.fno_loader = FnoContractLoader()

        self._ws_thread = None
        # Keep track of the websocket client and the tokens we have subscribed to.
        self._websocket_obj = None
        self._subscribed_tokens = set()
        # Queue for passing tick updates from the listener to the cache worker.
        self._ws_queue = queue.Queue()
        self._start_cache_worker()
        self._start_websocket_listener()

    def _start_cache_worker(self):
        """Background worker that consumes tick updates from ``self._ws_queue``
        and updates ``self._websocket_data`` safely.
        """

        def cache_worker():
            while True:
                item = self._ws_queue.get()
                try:
                    if not isinstance(item, (list, tuple)) or len(item) != 2:
                        log.warning(
                            f"Invalid queue item received by cache worker: {item!r}"
                        )
                        continue

                    token, price_info = item
                    if token is None:
                        log.warning("Cache worker received None token; skipping")
                        continue

                    with self._websocket_lock:
                        if self._websocket_data is None or not isinstance(
                            self._websocket_data, dict
                        ):
                            self._websocket_data = {}
                        self._websocket_data[token] = price_info
                except Exception as e:
                    log.error(f"Cache worker error: {e}")
                finally:
                    try:
                        self._ws_queue.task_done()
                    except Exception as task_done_err:
                        log.error(f"Cache worker task_done error: {task_done_err}")

        threading.Thread(target=cache_worker, daemon=True).start()

    def _start_websocket_listener(self):
        """Start a background thread that continuously subscribes to NSE equity tick data.

        The previous implementation attempted to spawn a new thread from within the
        listener itself, which resulted in the listener never being started. This
        version defines the listener function and launches it exactly once.
        """
        if not hasattr(self.broker, "websocket") and not hasattr(
            self.broker, "_start_websocket"
        ):
            log.info(
                "Broker does not support WebSocket, background listener not started"
            )
            return

        def websocket_listener():
            connection_attempts = 0
            # Increase the number of connection attempts to improve reliability.
            # A higher limit (e.g., 20) gives the system more chances to recover from transient
            # network issues without giving up prematurely. The back‑off sleep below will
            # gradually increase to avoid hammering the server.
            max_attempts = 20
            websocket_connected = False
            websocket_subscribed = False

            while connection_attempts < max_attempts:
                try:
                    websocket_obj = getattr(self.broker, "websocket", None)
                    start_ws_method = getattr(self.broker, "_start_websocket", None)

                    if not websocket_obj and start_ws_method:
                        log.info("Starting WebSocket connection...")
                        start_ws_method()
                        websocket_obj = getattr(self.broker, "websocket", None)

                    if not websocket_obj:
                        # No WebSocket client available – attempt to start it and retry.
                        connection_attempts += 1
                        continue
                    if not websocket_connected:
                        log.info("WebSocket connected successfully")
                        websocket_connected = True
                        # Store reference to the connected websocket for later use (e.g., unsubscribe).
                        self._websocket_obj = websocket_obj
                        # Log detailed connection state for debugging.
                        try:
                            is_conn = getattr(websocket_obj, "is_connected", False)
                            subs = getattr(websocket_obj, "subscriptions", set())
                            log.debug(
                                "WebSocket client state – is_connected=%s, existing subscriptions=%d",
                                is_conn,
                                len(subs),
                            )
                        except Exception as e:
                            log.error(f"Failed to log WebSocket client state: {e}")
                    # Reset attempts after a successful connection.
                    connection_attempts = 0

                    # Load instrument metadata and prepare token list.
                    # -----------------------------------------------------------------
                    # Load all NSE equity instruments and **apply the pre‑filter** before
                    # building the subscription list. This ensures we only subscribe to
                    # tokens that actually pass the screening criteria (price trend +
                    # volume) while still respecting Zerodha's 4 000‑token hard limit.
                    # -----------------------------------------------------------------
                    instruments = self.broker._load_instruments()
                    if not instruments:
                        log.warning("No instruments loaded from broker")
                        time.sleep(10)
                        continue

                    # Build a mapping of symbol → token for quick lookup.
                    symbol_to_token = {}
                    # Include both equity (EQ) and futures (FUT, FUTIDX, FUTSTK) instruments.
                    allowed_types = {"EQ", "FUT", "FUTIDX", "FUTSTK"}
                    for inst in instruments:
                        if (
                            inst.get("exchange", "") or inst.get("Exchange", "")
                        ) != "NSE":
                            continue
                        # Some instrument metadata may omit the type field; treat missing as EQ (equity).
                        inst_type = inst.get("instrument_type", "") or inst.get(
                            "Instrument_type", ""
                        )
                        if inst_type and inst_type not in allowed_types:
                            continue
                        symbol = (
                            inst.get("tradingsymbol")
                            or inst.get("Tradingsymbol")
                            or inst.get("symbol")
                        )
                        token = inst.get(
                            "instrument_token", inst.get("Instrument_token")
                        )
                        if symbol and token:
                            symbol_to_token[symbol] = token

                    # Initially subscribe to a broad set of tokens (up to Zerodha's limit).
                    # We use the full list of NSE equity tokens, capped at 4000, to
                    # populate the WebSocket cache before applying any filtering.
                    all_tokens = list(symbol_to_token.values())
                    max_subscribe = 4000
                    if len(all_tokens) > max_subscribe:
                        instrument_tokens = all_tokens[:max_subscribe]
                    else:
                        instrument_tokens = all_tokens
                    log.debug(
                        "Prepared %d NSE equity tokens for initial subscription (pre‑filter)"
                        % len(instrument_tokens)
                    )

                    # Subscription loop – attempt a few times before falling back.
                    while connection_attempts < max_attempts:
                        try:
                            log.debug(
                                "websocket_subscribed=%s, has subscribe=%s, type=%s"
                                % (
                                    websocket_subscribed,
                                    hasattr(websocket_obj, "subscribe"),
                                    type(websocket_obj),
                                )
                            )
                            if not websocket_subscribed and hasattr(
                                websocket_obj, "subscribe"
                            ):
                                # Ensure connection is still alive.
                                # Wait for the WebSocket to become ready. The original
                                # implementation used a 10‑second timeout, which can be
                                # insufficient on slower networks or when the server
                                # takes longer to establish the connection. Increase the
                                # timeout to 30 seconds to give the client more time to
                                # receive the ``on_open`` callback and set ``is_connected``.
                                wait_start = time.time()
                                while (
                                    not getattr(websocket_obj, "is_connected", False)
                                    and time.time() - wait_start < 30
                                ):
                                    time.sleep(0.1)

                                if not getattr(websocket_obj, "is_connected", False):
                                    log.warning(
                                        "WebSocket not connected when attempting subscription"
                                    )
                                    break

                                valid_tokens = [
                                    t for t in instrument_tokens if str(t).isdigit()
                                ]
                                if not valid_tokens:
                                    log.warning(
                                        "No valid instrument tokens to subscribe via WebSocket"
                                    )
                                    break

                                # Increase batch size for subscription to reduce number of calls.
                                chunk_size = 500
                                subscribed_tokens = []
                                for i in range(0, len(valid_tokens), chunk_size):
                                    chunk = valid_tokens[i : i + chunk_size]
                                    log.debug(
                                        "Attempting WebSocket subscription for chunk %d (size %d). is_connected=%s"
                                        % (
                                            i // chunk_size + 1,
                                            len(chunk),
                                            getattr(
                                                websocket_obj, "is_connected", False
                                            ),
                                        )
                                    )
                                    # Log a sample of the chunk tokens for debugging.
                                    chunk_sample = chunk[:20]
                                    log.debug(
                                        f"Chunk {i // chunk_size + 1} token sample: {chunk_sample} (size {len(chunk)})"
                                    )
                                    websocket_obj.subscribe(chunk)
                                    # Log the total number of tokens we have asked the WebSocket to subscribe to so far.
                                    log.debug(
                                        "Total subscribed tokens after chunk %d: %d",
                                        i // chunk_size + 1,
                                        len(self._subscribed_tokens) + len(chunk),
                                    )
                                    if hasattr(websocket_obj, "set_mode") and getattr(
                                        websocket_obj, "is_connected", False
                                    ):
                                        try:
                                            websocket_obj.set_mode("full", chunk)
                                            time.sleep(0.05)
                                        except Exception as mode_err:
                                            log.error(
                                                f"Failed to set mode for WebSocket chunk {i // chunk_size + 1}: {mode_err}"
                                            )
                                    # Record the tokens we have subscribed to.
                                    self._subscribed_tokens.update(chunk)
                                    subscribed_tokens.extend(chunk)
                                    log.debug(
                                        "Subscribed to %d instruments (chunk %d) via WebSocket"
                                        % (len(chunk), i // chunk_size + 1)
                                    )

                                websocket_subscribed = True
                                # Wait for any tick data.
                                # ------------------------------------------------------------
                                # Warm‑up the WebSocket cache.
                                # Previously the code broke out of the loop after the *first* non‑empty batch,
                                # which resulted in only a small fraction of the subscribed tokens being cached
                                # (e.g., 495 out of >3000). The new logic waits (up to a configurable timeout) until a
                                # sufficient portion of the tokens have received price updates, logging progress
                                # along the way.
                                # ------------------------------------------------------------
                                start_wait = time.time()
                                # Allow a shorter warm‑up period. The original 60 second
                                # timeout could make the system appear stalled when the
                                # market data feed is slow or when many tokens are
                                # subscribed. Reduce to 15 seconds – enough for a
                                # quick cache warm‑up while keeping startup responsive.
                                max_initial_wait = 15
                                while time.time() - start_wait < max_initial_wait:
                                    prices = websocket_obj.get_prices(subscribed_tokens)
                                    # Push any received ticks into the queue and update the shared cache.
                                    for token, price_info in prices.items():
                                        if price_info is None:
                                            continue
                                        self._ws_queue.put((token, price_info))
                                        with self._websocket_lock:
                                            if (
                                                self._websocket_data is None
                                                or not isinstance(
                                                    self._websocket_data, dict
                                                )
                                            ):
                                                self._websocket_data = {}
                                            self._websocket_data[token] = price_info

                                    filled = len(self._websocket_data)
                                    total = len(subscribed_tokens)
                                    log.info(
                                        "WebSocket cache progress: %d / %d tokens filled",
                                        filled,
                                        total,
                                    )

                                    # Stop when we have data for all tokens or at least 90 % of them.
                                    if filled >= total or filled >= int(0.9 * total):
                                        break
                                    time.sleep(0.5)

                                # After the warm‑up loop, log the final cache size.
                                final_count = len(self._websocket_data)
                                log.info(
                                    "Initial WebSocket prices received for %d tokens after warm‑up",
                                    final_count,
                                )

                                # Schedule a one‑off dump of a few cache entries after a short delay.
                                def _debug_cache_snapshot():
                                    try:
                                        cache = getattr(
                                            websocket_obj, "_price_cache", None
                                        ) or getattr(websocket_obj, "price_cache", {})
                                        sample = list(cache.items())[:5]
                                        log.debug(
                                            f"Price‑cache snapshot (first 5 entries): {sample}"
                                        )
                                    except Exception as e:
                                        log.error(
                                            f"Failed to snapshot price cache: {e}"
                                        )

                                threading.Timer(5.0, _debug_cache_snapshot).start()
                                try:
                                    cache_keys = list(
                                        getattr(websocket_obj, "price_cache", {}).keys()
                                    )
                                    log.debug(
                                        f"WebSocket price cache keys after initial data: {cache_keys[:20]} (total {len(cache_keys)})"
                                    )
                                except Exception as e:
                                    log.error(f"Failed to inspect price_cache: {e}")
                                # ------------------------------------------------------------
                                # At this point we have a populated cache for the *initial*
                                # subscription (which may be up to 4000 tokens). Now run the
                                # screening to determine the final set of symbols we actually
                                # care about, and adjust the subscription accordingly.
                                # ------------------------------------------------------------
                                try:
                                    # Run the screening using the data we just received.
                                    filtered_symbols = self.get_filtered_symbols()
                                    # Map symbols back to tokens using the previously built map.
                                    filtered_tokens = [
                                        symbol_to_token[s]
                                        for s in filtered_symbols
                                        if s in symbol_to_token
                                    ]
                                    # Apply the Zerodha cap after filtering.
                                    max_subscribe = 4000
                                    if len(filtered_tokens) > max_subscribe:
                                        log.info(
                                            "Truncating filtered subscription list from %d to %d tokens to respect API limit"
                                            % (len(filtered_tokens), max_subscribe)
                                        )
                                        filtered_tokens = filtered_tokens[
                                            :max_subscribe
                                        ]

                                    # Determine tokens to add or remove.
                                    tokens_to_add = (
                                        set(filtered_tokens) - self._subscribed_tokens
                                    )
                                    tokens_to_remove = self._subscribed_tokens - set(
                                        filtered_tokens
                                    )

                                    # Unsubscribe tokens we no longer need.
                                    if tokens_to_remove and hasattr(
                                        websocket_obj, "unsubscribe"
                                    ):
                                        try:
                                            websocket_obj.unsubscribe(
                                                list(tokens_to_remove)
                                            )
                                            log.debug(
                                                "Unsubscribed %d tokens after filtering"
                                                % len(tokens_to_remove)
                                            )
                                        except Exception as unsub_err:
                                            log.error(
                                                "Failed to unsubscribe tokens: %s"
                                                % unsub_err
                                            )
                                        self._subscribed_tokens.difference_update(
                                            tokens_to_remove
                                        )

                                    # Subscribe to any new tokens required.
                                    if tokens_to_add:
                                        chunk_size = 500
                                        added_chunks = []
                                        for i in range(
                                            0, len(tokens_to_add), chunk_size
                                        ):
                                            chunk = list(tokens_to_add)[
                                                i : i + chunk_size
                                            ]
                                            # Ensure mode is set for newly added tokens.
                                            websocket_obj.subscribe(chunk)
                                            if hasattr(
                                                websocket_obj, "set_mode"
                                            ) and getattr(
                                                websocket_obj, "is_connected", False
                                            ):
                                                try:
                                                    websocket_obj.set_mode(
                                                        "full", chunk
                                                    )
                                                    time.sleep(0.05)
                                                except Exception as mode_err:
                                                    log.error(
                                                        f"Failed to set mode for WebSocket chunk after filtering: {mode_err}"
                                                    )
                                            self._subscribed_tokens.update(chunk)
                                            added_chunks.append(chunk)
                                            log.debug(
                                                "Subscribed additional %d tokens after filtering"
                                                % len(chunk)
                                            )
                                        subscribed_tokens = list(filtered_tokens)
                                except Exception as filter_err:
                                    log.error(
                                        "Error during post‑subscription filtering: %s"
                                        % filter_err
                                    )

                                # Continuous update loop – now using the (potentially) reduced set.
                                while getattr(websocket_obj, "is_connected", False):
                                    try:
                                        latest_prices = websocket_obj.get_prices(
                                            subscribed_tokens
                                        )
                                        # Push each tick onto the queue for the cache worker.
                                        for token, price_info in latest_prices.items():
                                            if price_info is not None:
                                                self._ws_queue.put((token, price_info))
                                        # Keep the original 2‑second interval to balance update frequency and load.
                                        time.sleep(2)
                                    except Exception as e:
                                        log.error(
                                            "Error updating WebSocket cache: %s" % e
                                        )
                                        break
                                break
                                # No data arrived – will retry outer loop.
                                time.sleep(0.5)
                        except Exception as e:
                            log.error(
                                "WebSocket listener error (attempt %d/%d): %s"
                                % (connection_attempts, max_attempts, e)
                            )
                        finally:
                            # Increment attempt counter and apply exponential back‑off to avoid rapid retries.
                            connection_attempts += 1
                            # Back‑off: start with 2 seconds, double each attempt up to a max of 30 seconds.
                            backoff = min(2**connection_attempts, 30)
                            log.debug(
                                f"WebSocket connection attempt {connection_attempts} failed, sleeping {backoff}s before retry"
                            )
                            time.sleep(backoff)
                    else:
                        # Critical failure: WebSocket could not be established after the allowed retries.
                        # Instead of aborting the entire screening process, we now log a warning and allow
                        # the system to fall back to the REST‑based data fetch in _fetch_ohlc_data().
                        # This prevents a hard crash and gives the screening pipeline a chance to continue
                        # using the slower but reliable REST endpoint.
                        log.warning(
                            "Max WebSocket connection attempts reached – falling back to REST data fetch."
                        )
                        # Exit the listener thread gracefully; _fetch_ohlc_data will handle the fallback.
                        break
                except Exception as outer_e:
                    log.error("WebSocket listener outer error: %s" % outer_e)
                    break

        # Launch the listener thread exactly once.
        if self._ws_thread is None or not self._ws_thread.is_alive():
            # Store a reference to the websocket client for later use (e.g., unsubscribe).
            self._websocket_obj = (
                None  # will be set inside listener when connection is ready
            )
            self._ws_thread = threading.Thread(target=websocket_listener, daemon=True)
            self._ws_thread.start()

    def _fetch_ohlc_data(self):
        """Fetch OHLCV data from WebSocket cache or fallback to REST API if needed."""
        with self._websocket_lock:
            if self._websocket_data:
                log.info(
                    "Using %d instruments from WebSocket cache"
                    % len(self._websocket_data)
                )
                return self._websocket_data.copy()

        # If the WebSocket cache is empty, wait briefly for data to arrive instead of falling back to REST.
        # This avoids rate‑limited REST calls and prefers real‑time WebSocket updates.
        wait_start = time.time()
        # Extend wait for WebSocket data to 120 seconds to improve cache fill reliability
        # Extend the wait time to give the WebSocket cache more opportunity to fill.
        # A longer window (e.g., 300 seconds) is acceptable because the listener runs
        # in a background thread and does not block the main screening flow.
        # Wait longer (up to 30 seconds) for the WebSocket cache to be populated.
        # A longer wait reduces premature fallback to REST and eliminates the
        # frequent "WebSocket cache still empty" warnings during normal start‑up.
        max_wait_seconds = 120  # extended warm‑up period for cache population
        while time.time() - wait_start < max_wait_seconds:
            with self._websocket_lock:
                if self._websocket_data:
                    log.info(
                        "Using %d instruments from WebSocket cache after waiting"
                        % len(self._websocket_data)
                    )
                    return self._websocket_data.copy()
            time.sleep(1)

        # After waiting, if still empty, attempt a quick restart of the WebSocket listener
        # before falling back to the REST‑based FNO fetch. This helps recover from transient
        # connection issues where the initial subscription succeeded but no ticks were
        # received within the wait window.
        if not self._websocket_data:
            log.warning(
                "WebSocket cache still empty after waiting; attempting listener restart."
            )
            # Force a listener restart by clearing the existing thread reference.
            # The original _start_websocket_listener() checks if a thread is already
            # alive and does nothing, which prevented a true restart. By resetting
            # self._ws_thread to None we ensure a fresh listener thread is created.
            self._ws_thread = None
            self._start_websocket_listener()
            # Give the new listener a short grace period to populate the cache.
            retry_start = time.time()
            # Allow up to 30 seconds after restart for the cache to fill
            while time.time() - retry_start < 30:
                with self._websocket_lock:
                    if self._websocket_data:
                        log.info("WebSocket cache populated after listener restart.")
                        return self._websocket_data.copy()
                time.sleep(1)

            # If still empty, fall back to a REST fetch for *all* subscribed tokens.
            # Previously this used ``_fetch_from_fno_symbols`` which only queried
            # F&O contracts, resulting in a dramatically reduced candidate set
            # (e.g., 988 symbols) and causing the dynamic filter to prune the
            # pre‑filter universe incorrectly. We now retrieve quotes for every
            # token we are subscribed to, ensuring the live‑screening data
            # matches the original 3524 candidates.
            log.warning(
                "WebSocket cache still empty after restart; falling back to REST for all subscribed tokens."
            )
            return self._fetch_quotes_for_tokens(self._subscribed_tokens)

        # If we have data (unlikely to reach here because of early return), continue.
        ohlc_data = {}
        fetched_count = 0

        # Fallback path: fetch data via REST for the tokens we are currently subscribed to.
        # Load instrument metadata once.
        instruments = self.broker._load_instruments()
        for token in self._subscribed_tokens:
            try:
                instrument = next(
                    (
                        inst
                        for inst in instruments
                        if inst.get("instrument_token") == token
                        or inst.get("Instrument_token") == token
                    ),
                    None,
                )
                if not instrument:
                    continue

                symbol = instrument.get(
                    "tradingsymbol",
                    instrument.get("Tradingsymbol", instrument.get("symbol", "")),
                )

                if not symbol:
                    continue

                if not hasattr(self.broker, "get_quote"):
                    log.warning("Broker does not support get_quote for %s" % symbol)
                    continue

                quote = self.broker.get_quote(symbol)
                if quote is None:
                    log.debug("No quote returned for %s" % symbol)
                    continue

                if hasattr(quote, "last_price"):
                    last_price = getattr(quote, "last_price", None)
                    volume = getattr(quote, "volume", 0)
                    open_price = getattr(quote, "open", last_price)
                elif isinstance(quote, dict):
                    last_price = quote.get("last_price")
                    volume = quote.get("volume", 0)
                    open_price = quote.get("open", last_price)
                else:
                    continue

                if last_price is None or float(last_price) <= 0:
                    log.debug("Invalid price for %s: %s" % (symbol, last_price))
                    continue

                ohlc_data[token] = {
                    "open": (
                        open_price
                        if open_price and float(open_price) > 0
                        else last_price
                    ),
                    "high": last_price,
                    "low": last_price,
                    "close": last_price,
                    "volume": volume or 0,
                    "_symbol": symbol,
                }
                fetched_count += 1
            except Exception as e:
                log.debug(
                    "Failed to fetch OHLCV data for instrument %s: %s" % (token, e)
                )

        if not ohlc_data:
            log.error(
                "No real market data available for any symbol after REST API fallback. "
                "Broker connection or data feed may be unavailable. Stopping screening."
            )
            raise RuntimeError(
                "No real market data available for any symbol - "
                "cannot perform screening without live data. "
                "Check broker connection and data feed status."
            )

            log.info("Fetched OHLCV data for %d symbols via REST API" % fetched_count)
        return ohlc_data

    def _fetch_from_fno_symbols(self):
        """Fetch OHLCV data using equity symbols from FNO contracts as fallback."""
        ohlc_data = {}
        try:
            fno_symbols = self.fno_loader.get_fno_symbols()
            if not fno_symbols:
                log.error(
                    "No F&O symbols available. Cannot proceed without market data."
                )
                return ohlc_data

            # Track missing quotes to avoid noisy per‑symbol debug logs.
            missing_quote_count = 0
            for fno_contract in fno_symbols:
                try:
                    symbol = fno_contract
                    if not hasattr(self.broker, "get_quote"):
                        continue

                    quote = self.broker.get_quote(symbol)
                    # If the quote is unavailable or lacks a valid last_price, count it and skip.
                    if (
                        not quote
                        or not hasattr(quote, "last_price")
                        or not quote.last_price
                    ):
                        missing_quote_count += 1
                        continue

                    open_price = getattr(quote, "open", None) or quote.last_price
                    ohlc_data[hash(symbol)] = {
                        "open": open_price,
                        "high": quote.last_price,
                        "low": quote.last_price,
                        "close": quote.last_price,
                        "volume": getattr(quote, "volume", 0),
                        "_symbol": symbol,
                    }
                except Exception as e:
                    log.debug("Failed to fetch quote for %s: %s" % (fno_contract, e))
            # Summarize missing quotes after the loop to keep logs concise.
            if missing_quote_count:
                log.info(
                    "No live quote available for %d FNO symbols" % missing_quote_count
                )
        except Exception as e:
            log.error("FNO contract loader fallback failed: %s" % e)
        return ohlc_data

    @staticmethod
    def _normalize_token(token):
        """Normalize instrument tokens to a stable string form for lookups."""
        if token is None:
            return None
        token_str = str(token).strip()
        if not token_str:
            return None
        try:
            return str(int(float(token_str)))
        except (TypeError, ValueError):
            return token_str

    def get_filtered_symbols(self):
        """Get symbols that pass real-time screening criteria"""
        start_time = time.time()
        # Allow more time for screening to complete, especially when waiting for WS data.
        # Increased from 120 seconds to 300 seconds to give the cache ample time to populate.
        timeout = 300

        with self._cache_lock:
            if (
                self.last_screened
                and (datetime.datetime.now() - self.last_screened).total_seconds()
                < self.cache_ttl
            ):
                return list(self.screened_symbols)

        # Fetch latest OHLC data from the WebSocket cache.
        current_data = self._fetch_ohlc_data()
        # If the cache is still empty, we cannot perform a fresh screening.
        # Instead of returning an empty list (which forces the main pipeline
        # to fall back to generic top‑volume symbols), preserve any previously
        # successful screening results. This avoids a sudden drop to zero
        # candidates when the WebSocket cache is temporarily unavailable.
        if not current_data:
            if self.screened_symbols:
                log.warning(
                    "WebSocket cache empty – reusing previous screened symbols (%d)"
                    % len(self.screened_symbols)
                )
                return list(self.screened_symbols)
            log.warning(
                "WebSocket cache empty and no prior screened symbols – skipping dynamic filter this cycle"
            )
            return []

        if time.time() - start_time > timeout:
            # Previously this returned an empty list, causing the screening step to produce no results.
            # Instead, we log the timeout but continue processing the data we have.
            log.warning(
                "get_filtered_symbols timed out, proceeding with partial results"
            )
            # Do not return early; allow the remaining screening logic to run.

        log.debug("Processing %d instruments for screening" % len(current_data))

        # Prepare for screening
        self.screened_symbols.clear()
        instruments = self.broker._load_instruments()

        token_to_symbol = {}
        allowed_types = {"EQ", "FUT", "FUTIDX", "FUTSTK"}
        for inst in instruments:
            exchange = inst.get("exchange", "") or inst.get("Exchange", "")
            inst_type = inst.get("instrument_type", "") or inst.get(
                "Instrument_type", ""
            )
            if exchange == "NSE" and inst_type in allowed_types:
                token = inst.get("instrument_token", inst.get("Instrument_token"))
                symbol = inst.get(
                    "tradingsymbol", inst.get("Tradingsymbol", inst.get("symbol", ""))
                )
                if not token or not symbol:
                    continue
                token_key = self._normalize_token(token)
                if token_key:
                    token_to_symbol[token_key] = str(symbol).strip()

        total_instruments = len(current_data)
        passed_criteria = 0
        low_price = 0
        zero_volume = 0

        for instrument_token, ohlc in current_data.items():
            if time.time() - start_time > timeout:
                log.warning(
                    "Screening criteria check timed out, returning partial results"
                )
                break

            if ohlc is None:
                low_price += 1
                continue

            if ohlc.get("close") == 0:
                low_price += 1
                continue

            if ohlc.get("close") < 10:
                low_price += 1
                continue

            if ohlc.get("volume") == 0:
                zero_volume += 1
                continue

            if ohlc["open"] > 0:
                price_change_pct = (
                    abs(ohlc["close"] - ohlc["open"]) / ohlc["open"] * 100
                )
                if price_change_pct < 1:
                    continue

            lookup_key = self._normalize_token(instrument_token)
            symbol_key = lookup_key
            if lookup_key and lookup_key in token_to_symbol:
                symbol_key = token_to_symbol[lookup_key]
            elif isinstance(ohlc, dict):
                symbol_key = ohlc.get("_symbol", lookup_key)
            self.screened_symbols.add(
                str(symbol_key).strip() if symbol_key is not None else str(lookup_key)
            )
            passed_criteria += 1

        self.last_screened = datetime.datetime.now()
        log.info(
            "Screening completed: %d/%d symbols passed (low price: %s, zero volume: %s)"
            % (passed_criteria, total_instruments, low_price, zero_volume)
        )

        # Prune subscriptions to keep only screened tokens.
        if self._websocket_obj:
            keep_tokens = []
            for token, sym in token_to_symbol.items():
                if sym in self.screened_symbols:
                    keep_tokens.append(token)
            self._prune_subscription(keep_tokens)

        return list(self.screened_symbols)

    def _prune_subscription(self, keep_tokens):
        """Unsubscribe from any tokens that are not in the keep list.

        This method is called after the screening step to reduce the number of
        active WebSocket subscriptions to only those symbols that passed the
        screening criteria. It respects the API limit and avoids unnecessary
        data traffic.
        """
        if not self._websocket_obj:
            log.debug("WebSocket object not available for pruning subscription")
            return

        # Determine which tokens need to be unsubscribed.
        current = set(self._subscribed_tokens)
        keep_set = set(keep_tokens)
        to_unsub = list(current - keep_set)
        if not to_unsub:
            log.debug("No tokens to unsubscribe after screening")
            return

        try:
            self._websocket_obj.unsubscribe(to_unsub)
            # Update internal tracking set.
            self._subscribed_tokens = keep_set & current
            log.info(
                "Unsubscribed from %d tokens not passing screening" % len(to_unsub)
            )
        except Exception as e:
            log.error("Failed to unsubscribe tokens: %s" % e)


def initialize_screening(broker=None):
    """Initialize the dynamic screening system."""
    if broker is None:
        return None
    return DynamicSymbolScreening(broker)
