"""Minimal WebSocket manager for fetching live price snapshots.

The production system already contains a sophisticated WebSocket listener in
``screener.dynamic_screening.DynamicSymbolScreening``. For the purpose of the
option‑chain fallback we only need a lightweight helper that can retrieve the
latest price for a given symbol if the broker's WebSocket client is available.

If the broker does not expose a WebSocket or the subscription fails, the
function gracefully returns ``None`` so that the caller can continue with the
next fallback stage.
"""

import logging
import threading
import time

from core.zerodha_broker import ZerodhaBroker

log = logging.getLogger(__name__)

# Global broker instance – reused across calls to avoid repeated logins.
_broker: ZerodhaBroker | None = None
# Cache of symbol → latest price (float). Updated by the background listener.
_price_cache: dict = {}
# Lock protecting the cache.
_cache_lock = threading.Lock()
# Flag indicating whether the background listener thread has been started.
_listener_started = False


def _ensure_broker() -> ZerodhaBroker:
    """Instantiate a global :class:`ZerodhaBroker` if not already present."""
    global _broker
    if _broker is None:
        _broker = ZerodhaBroker()
    return _broker


def _start_listener():
    """Ensure the broker WebSocket client is running and able to subscribe.

    The helper does not rely on an internal tick queue. Instead it uses the
    broker's WebSocket client cache API when available.
    """
    global _listener_started
    if _listener_started:
        return
    broker = _ensure_broker()
    if not hasattr(broker, "websocket") and not hasattr(broker, "_start_websocket"):
        log.info("Broker does not support WebSocket – websocket manager disabled")
        _listener_started = True
        return

    if not getattr(broker, "websocket", None):
        start_ws = getattr(broker, "_start_websocket", None)
        if callable(start_ws):
            start_ws()

    ws = getattr(broker, "websocket", None)
    if not ws:
        log.warning("WebSocket client not available after start attempt")
        _listener_started = True
        return

    # If the broker websocket implementation exposes a connection state,
    # wait briefly for it to become ready before attempting subscriptions.
    if hasattr(ws, "is_connected") and not ws.is_connected:
        timeout = time.time() + 10
        while time.time() < timeout and not ws.is_connected:
            time.sleep(0.25)

    _listener_started = True


def _resolve_token(symbol: str, broker: ZerodhaBroker) -> int | None:
    """Resolve *symbol* to its Zerodha instrument token.

    This helper centralises the token‑lookup logic used by the public helper
    functions. It returns ``None`` if the symbol cannot be found.
    """
    instruments = broker._load_instruments()
    for inst in instruments:
        exchange = inst.get("exchange", "") or inst.get("Exchange", "")
        if exchange not in {"NSE", "NFO"}:
            continue
        s = inst.get("tradingsymbol") or inst.get("Tradingsymbol") or inst.get("symbol")
        if s == symbol.upper():
            return inst.get("instrument_token") or inst.get("Instrument_token")
    return None


def get_latest_price(symbol: str) -> float | None:
    """Return the most recent price for *symbol* from the websocket cache.

    If the price is not yet available, ``None`` is returned and the caller can
    proceed to the next fallback.
    """
    _start_listener()
    broker = _ensure_broker()
    ws = getattr(broker, "websocket", None)
    token = _resolve_token(symbol, broker)
    if token is None:
        log.debug(f"Token not found for symbol {symbol} – cannot fetch websocket price")
        return None

    # Prefer the websocket client's own cache if available before subscribing.
    price = None
    if ws is not None:
        if hasattr(ws, "get_price"):
            try:
                price = ws.get_price(token)
            except Exception as exc:
                log.debug(f"WebSocket get_price failed for token {token}: {exc}")
        elif hasattr(ws, "price_cache"):
            price_entry = ws.price_cache.get(token) or ws.price_cache.get(str(token))
            if isinstance(price_entry, dict):
                price = price_entry.get("close") or price_entry.get("last_price")
            else:
                price = price_entry

    if price is None:
        with _cache_lock:
            price = _price_cache.get(token)

    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = None

    if price is not None:
        log.debug(f"WebSocket price cache hit for {symbol}: {price}")
        with _cache_lock:
            _price_cache[token] = price
        return price

    log.debug(
        f"WebSocket price cache miss for {symbol}; attempting subscribe for token {token}"
    )
    # Only subscribe if we do not already have a live subscription for this token.
    if ws is not None and hasattr(ws, "subscribe"):
        already_subscribed = getattr(ws, "subscriptions", None)
        if not (isinstance(already_subscribed, set) and token in already_subscribed):
            try:
                ws.subscribe([token])
            except Exception as exc:
                log.debug(
                    f"Failed to subscribe WebSocket token {token} for {symbol}: {exc}"
                )
            else:
                log.debug(f"WebSocket subscribe attempted for {symbol} token {token}")

    # Re-check the websocket cache after subscribing.
    if ws is not None:
        if hasattr(ws, "get_price"):
            try:
                price = ws.get_price(token)
            except Exception as exc:
                log.debug(
                    f"WebSocket get_price failed after subscribe for token {token}: {exc}"
                )
        elif hasattr(ws, "price_cache"):
            price_entry = ws.price_cache.get(token) or ws.price_cache.get(str(token))
            if isinstance(price_entry, dict):
                price = price_entry.get("close") or price_entry.get("last_price")
            else:
                price = price_entry

    if price is None:
        with _cache_lock:
            price = _price_cache.get(token)

    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = None

    if price is not None:
        log.debug(f"WebSocket price cache hit for {symbol}: {price}")
        with _cache_lock:
            _price_cache[token] = price
    else:
        log.debug(f"WebSocket price cache miss for {symbol} after subscribe")
    return price


# ---------------------------------------------------------------------
# Helper utilities for cache inspection and explicit listener control
# ---------------------------------------------------------------------


def is_price_cached(symbol: str) -> bool:
    """Return ``True`` if a price for *symbol* is present in the cache.

    This function does **not** start the background listener – it merely
    checks the existing ``_price_cache``. It is useful for the fallback
    hierarchy where we want to prefer a cached price over initiating a new
    subscription.
    """
    broker = _ensure_broker()
    token = _resolve_token(symbol, broker)
    if token is None:
        return False
    with _cache_lock:
        return token in _price_cache


def get_cached_price(symbol: str) -> float | None:
    """Retrieve a cached price for *symbol* without triggering a subscription.

    Returns ``None`` if the price is not cached or the token cannot be
    resolved.
    """
    broker = _ensure_broker()
    token = _resolve_token(symbol, broker)
    if token is None:
        return None
    with _cache_lock:
        return _price_cache.get(token)


def start_listener_if_needed():
    """Public wrapper to ensure the background listener is running.

    The internal ``_start_listener`` is idempotent, but exposing a clearly
    named function makes the intent explicit for callers (e.g., the fallback
    logic in ``main.py``).
    """
    _start_listener()
