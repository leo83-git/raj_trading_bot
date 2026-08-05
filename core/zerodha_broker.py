# ═══════════════════════════════════════════════════════════════
#  core/zerodha_broker.py — Zerodha Kite Connect Broker
# ═══════════════════════════════════════════════════════════════
"""
Zerodha Kite Connect API integration for live trading.
Implements the BrokerBase interface for Zerodha broker.
"""

import os
import time
from datetime import datetime

import yaml
from kiteconnect import KiteConnect

from core.config import ZERODHA_ACCESS_TOKEN, ZERODHA_API_KEY, ZERODHA_API_SECRET
from core.database import DatabaseManager
from core.logger import get_logger
from core.token_manager import ZerodhaTokenManager
from core.zerodha_websocket import ZerodhaWebSocket

log = get_logger("zerodha_broker")


class ZerodhaBroker:
    """Zerodha API integration layer"""

    _rate_limit_delay = 0.1
    _rate_limit_lock = None
    _recovery_debounce_seconds = 10.0

    def __init__(
        self, api_key: str = None, api_secret: str = None, access_token: str = None
    ):
        self.api_key = api_key or ZERODHA_API_KEY
        self.api_secret = api_secret or ZERODHA_API_SECRET
        self.access_token = access_token or ZERODHA_ACCESS_TOKEN
        self.kite = None
        self.websocket = None
        self._recovery_callback = None
        self._last_recovery_at = 0.0
        self.token_manager = ZerodhaTokenManager(self.api_key, self.api_secret)
        self.db_manager = None
        try:
            self.db_manager = DatabaseManager()
            log.info("DatabaseManager initialized for Zerodha instrument cache")
        except Exception as exc:
            log.debug(f"DatabaseManager unavailable for Zerodha broker cache: {exc}")
        self._initialize_kite()
        # Validate token before starting WebSocket to avoid handshake failures.
        # This validation can be bypassed by setting the environment variable
        # ``ZERODHA_SKIP_TOKEN_VALIDATION=1`` – useful in environments where the
        # cached token is known to be valid and network access to the profile
        # endpoint is blocked (e.g., proxy restrictions).
        if self.access_token:
            if os.getenv("ZERODHA_SKIP_TOKEN_VALIDATION") == "1":
                log.info(
                    "Skipping token validation per ZERODHA_SKIP_TOKEN_VALIDATION – using cached token"
                )
            else:
                try:
                    # Perform a lightweight profile call to ensure the token is accepted.
                    self.kite.profile()
                    log.info(
                        "Zerodha access token validated via profile call before WebSocket start"
                    )
                except Exception as e:
                    log.warning(
                        f"Access token validation failed before WebSocket start: {e}"
                    )
                    # Invalidate token to trigger fallback or re-authentication later.
                    self.access_token = None
        self._start_websocket()

    def set_recovery_callback(self, callback) -> None:
        """Register a callback invoked after websocket disconnects."""
        self._recovery_callback = callback

    # ---------------------------------------------------------------------
    # Token management helpers
    # ---------------------------------------------------------------------
    def update_token(self, new_token: str) -> None:
        """Update the broker's access token and propagate it to the Kite client.

        This method is called when the underlying WebSocket refreshes the token
        after an authentication failure. It ensures that subsequent REST calls
        (e.g., ``quote`` or ``instruments``) use the fresh token.
        """
        if not new_token:
            log.warning("Attempted to update Zerodha token with an empty value")
            return
        # Update internal state.
        self.access_token = new_token
        # If the Kite client has already been instantiated, update its token.
        if self.kite:
            try:
                self.kite.set_access_token(new_token)
                log.info("ZerodhaBroker access token updated successfully")
            except Exception as e:
                log.error(f"Failed to set new access token on Kite client: {e}")
        else:
            log.debug(
                "Kite client not yet initialized; token will be applied on next init"
            )

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

    def _initialize_kite(self):
        """Initialize the KiteConnect client with credentials."""
        # Try to get credentials from environment variables first (for testing)
        api_key = os.getenv("KITE_API_KEY")
        api_secret = os.getenv("KITE_API_SECRET")
        access_token = os.getenv("KITE_ACCESS_TOKEN")

        # If environment variables are not set, use config.yaml values
        if not all([api_key, api_secret]):
            # Fall back to config file for non-test environments
            config = self._load_config()
            api_key = config.get("zerodha_api_key")
            api_secret = config.get("zerodha_api_secret")
            access_token = config.get("zerodha_access_token")

        # If access token is still not available, use any access token already assigned on the instance.
        access_token = access_token or self.access_token

        # Try loading the saved token from the token manager first
        if self.token_manager.load_token():
            access_token = self.token_manager.access_token
            self.access_token = access_token
            log.info("Using access token loaded by token manager")

        self.access_token = access_token

        if not api_key or not api_secret:
            raise ValueError("Kite API key and secret are required")

        self.kite = KiteConnect(api_key=api_key)

        # If access token is provided, use it directly
        if access_token:
            self.kite.set_access_token(access_token)
            self.access_token = access_token
            self.broker = self.kite
            self.is_initialized = True
            log.info("Using access token from config or saved token")
            return

        # If no access token is available, prompt for request token only in interactive mode.
        if not access_token:
            import sys

            if not sys.stdin or not sys.stdin.isatty():
                raise RuntimeError(
                    "No Zerodha access token available and running in non-interactive mode"
                )

            print(f"Please visit this URL to login: {self.kite.login_url()}")
            request_token = input(
                "Please enter the request_token from the redirected URL: "
            ).strip()
            if not self.token_manager.request_access_token(request_token):
                raise RuntimeError("Failed to obtain a new Zerodha access token")

            self.access_token = self.token_manager.access_token
            self.kite.set_access_token(self.access_token)

        # Initialize the broker with the kite client
        self.broker = self.kite
        self.is_initialized = True

    def _start_websocket(self):
        """Start WebSocket connection for real-time market data"""
        if not self.access_token:
            log.warning(
                "Skipping Zerodha WebSocket startup because no access token is available"
            )
            return

        if not self.websocket:
            # Pass a callback so the WebSocket can inform the broker when a new token is generated.
            self.websocket = ZerodhaWebSocket(
                self.api_key,
                self.access_token,
                on_error=self._handle_websocket_error,
                on_close=self._handle_websocket_close,
                token_refresh_callback=self.update_token,
            )
            self.websocket.connect()
            log.info("WebSocket connection started for real-time market data")

    def _handle_websocket_close(self, *args, **kwargs) -> None:
        """Handle websocket close and trigger engine recovery if registered."""
        self._trigger_recovery("websocket close")

    def _handle_websocket_error(self, error) -> None:
        """Handle websocket errors that should trigger state recovery."""
        error_text = str(error).lower()
        fatal_conditions = (
            "403" in error_text
            or "authentication failed" in error_text
            or "invalid token" in error_text
            or "max reconnect" in error_text
            or "giving up" in error_text
        )

        if fatal_conditions and callable(self._recovery_callback):
            try:
                log.warning(
                    f"WebSocket error requires recovery; triggering reconciliation: {error}"
                )
                self._trigger_recovery(f"websocket error: {error}")
            except Exception as exc:
                log.error(
                    f"Recovery callback raised an exception after websocket error: {exc}"
                )

    def _trigger_recovery(self, reason: str) -> None:
        """Invoke the registered recovery callback with debounce."""
        if not callable(self._recovery_callback):
            return

        now = time.monotonic()
        elapsed = now - self._last_recovery_at
        if self._last_recovery_at and elapsed < self._recovery_debounce_seconds:
            log.warning(
                "Skipping recovery callback due to debounce: "
                f"reason={reason}, elapsed={elapsed:.2f}s, "
                f"debounce={self._recovery_debounce_seconds:.2f}s"
            )
            return

        self._last_recovery_at = now
        try:
            log.info(f"Triggering recovery callback: {reason}")
            self._recovery_callback()
        except Exception as exc:
            log.error(f"Recovery callback raised an exception after {reason}: {exc}")

    def connect(self) -> bool:
        """Initialize and verify the Zerodha Kite client and WebSocket connection."""
        try:
            if not self.kite:
                self._initialize_kite()
            if not self.kite:
                log.error("Zerodha Kite client is not initialized")
                return False

            if not self.access_token:
                log.error("Zerodha access token is not available")
                return False

            # Validate the access token by requesting the user profile only if the token is expired or expiry unknown.
            token_expired = True
            if self.token_manager.token_expiry:
                if datetime.now() < self.token_manager.token_expiry:
                    token_expired = False
                    log.info(
                        "Cached Zerodha token not expired; skipping profile validation"
                    )
            if token_expired:
                # Allow skipping this validation via env var – useful when the
                # proxy blocks the profile endpoint but the cached token is
                # known to be valid.
                if os.getenv("ZERODHA_SKIP_TOKEN_VALIDATION") == "1":
                    log.info(
                        "Skipping token validation per ZERODHA_SKIP_TOKEN_VALIDATION during connect"
                    )
                else:
                    try:
                        profile = self.kite.profile()
                        if not profile or not profile.get("user_id"):
                            log.error(
                                "Zerodha access token validation failed: invalid profile response"
                            )
                            # Continue without aborting – some environments may not return a full profile
                        else:
                            log.info("Zerodha access token validated successfully")
                    except Exception as e:
                        log.warning(
                            f"Zerodha profile validation raised an exception (ignored): {e}"
                        )

            # Ensure websocket is available for real-time streaming
            if not self.websocket or not getattr(self.websocket, "is_connected", False):
                self._start_websocket()

            # Ensure instrument cache is loaded
            try:
                self._load_instruments()
            except Exception as inst_err:
                log.warning(
                    f"Failed to load Zerodha instruments during connect: {inst_err}"
                )

            return True
        except Exception as e:
            log.error(f"Zerodha broker connect failed: {e}")
            return False

    def _load_instruments(self) -> list[dict]:
        """Load instrument dump from Zerodha or the ORM-backed cache."""
        if hasattr(self, "_cached_instruments"):
            return self._cached_instruments

        if self.db_manager:
            try:
                instruments = self.db_manager.get_instrument_cache()
                if instruments:
                    log.info(
                        f"Loaded {len(instruments)} instruments from ORM Zerodha cache"
                    )
                    self._cached_instruments = instruments
                    return instruments
            except Exception as exc:
                log.debug(f"ORM instrument cache load failed: {exc}")

        # Download fresh instrument dump, respecting the Zerodha rate limit.
        try:
            self._apply_rate_limit()
            instruments = self.kite.instruments()
            self._write_instruments_to_db(instruments)
            log.info(f"Downloaded {len(instruments)} instruments from Zerodha")
            self._cached_instruments = instruments
            return instruments
        except Exception as e:
            log.error(f"Failed to download instruments: {e}")
            raise

    def _write_instruments_to_db(self, instruments: list[dict]) -> None:
        """Persist downloaded Zerodha instruments into the ORM cache."""
        if not self.db_manager:
            return
        try:
            count = self.db_manager.replace_instrument_cache(instruments)
            log.info(f"Upserted {count} instruments into ORM cache")
        except Exception as exc:
            log.error(f"Failed to write instruments to ORM cache: {exc}")

    def _map_interval(self, interval: str) -> str:
        """Map interval to Zerodha format"""
        mapping = {
            "FIVE_MINUTE": "5minute",
            "FIFTEEN_MINUTE": "15minute",
            "THIRTY_MINUTE": "30minute",
            "SIXTY_MINUTE": "60minute",
            "DAY": "day",
        }
        return mapping.get(interval, "5minute")

    def get_candles(
        self,
        exchange: str,
        symbol: str,
        interval: str = "FIVE_MINUTE",
        count: int = 100,
    ) -> list[dict]:
        """Fetch OHLCV data from Zerodha API"""
        try:
            self._apply_rate_limit()
            z_interval = self._map_interval(interval)
            instruments = self._load_instruments()
            instrument = next(
                (
                    inst
                    for inst in instruments
                    if inst["exchange"] == exchange and inst["tradingsymbol"] == symbol
                ),
                None,
            )
            if not instrument:
                log.warning(f"Instrument not found: {exchange} {symbol}")
                return []

            data = self.kite.historical_data(
                instrument_token=instrument["instrument_token"],
                from_date=time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400)
                ),
                to_date=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                interval=z_interval,
            )

            # Return last 'count' candles
            return data[-count:] if len(data) >= count else data
        except Exception as e:
            log.error(f"Failed to fetch candles for {exchange} {symbol}: {e}")
            return []

    def get_instrument_token(self, exchange: str, symbol: str) -> int | None:
        """Get instrument token for a given exchange and symbol"""
        instruments = self._load_instruments()
        exchange_up = exchange.strip().upper() if exchange else ""
        symbol_up = symbol.strip().upper()
        instrument = next(
            (
                inst
                for inst in instruments
                if str(inst.get("exchange", "")).strip().upper() == exchange_up
                and str(inst.get("tradingsymbol", "")).strip().upper() == symbol_up
            ),
            None,
        )
        return instrument["instrument_token"] if instrument else None

    def get_multiple_quotes(self, symbols: list, exchange: str = "NSE") -> dict:
        """Batch fetch quotes for up to 500 instruments in a single REST call."""
        if not self.kite:
            return {}
        try:
            self._apply_rate_limit()
            instrument_keys = []
            for s in symbols:
                if not s:
                    continue
                sym = s.strip().upper()
                if sym in self.INDEX_SYMBOLS:
                    mapped = self.INDEX_SYMBOL_MAP.get(sym, sym)
                    instrument_keys.append(f"NSE:{mapped}")
                elif ":" in sym:
                    instrument_keys.append(sym)
                else:
                    instrument_keys.append(f"{exchange}:{sym}")
            results = {}
            for i in range(0, len(instrument_keys), 500):
                batch = instrument_keys[i : i + 500]
                data = self.kite.quote(*batch)
                for key, q in data.items():
                    sym = key.split(":", 1)[-1]
                    actual_exchange = key.split(":", 1)[0]
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
            return results
        except Exception as e:
            log.error(f"Zerodha batch quote failed: {e}")
            return {}

    # Set of known index symbols for quick membership test
    INDEX_SYMBOLS = {
        "NIFTY",
        "NIFTY 50",
        "NIFTY50",
        "BANKNIFTY",
        "NIFTY BANK",
        "FINNIFTY",
        "INDIAVIX",
        "NIFTYNEXT50",
        "MIDCPNIFTY",
    }

    # Mapping from generic index identifiers to Zerodha's tradingsymbols
    # This ensures the correct symbol is used when constructing the API key.
    INDEX_SYMBOL_MAP = {
        "NIFTY": "NIFTY 50",
        "NIFTY50": "NIFTY 50",
        "BANKNIFTY": "BANKNIFTY",
        "INDIAVIX": "INDIA VIX",
    }

    def get_quote(
        self, symbol: str, exchange: str = "NSE", timeout: float = 12.0
    ) -> dict | None:
        """Fetch real-time quote from Zerodha Kite API."""
        if not self.kite:
            log.warning("Zerodha Kite client not initialized")
            return None

        try:
            self._apply_rate_limit()
            # Strip leading zeros for NSE symbols to avoid 404 errors.
            symbol_upper = symbol.strip().lstrip("0").upper()
            # Determine if this is an index and map to Zerodha's tradingsymbol if needed
            if symbol_upper in self.INDEX_SYMBOLS:
                # Zerodha uses the regular NSE exchange for index tradingsymbols
                exchange = "NSE"
                # Use mapped tradingsymbol when available, otherwise fall back to the original
                symbol_mapped = self.INDEX_SYMBOL_MAP.get(symbol_upper, symbol_upper)
            else:
                symbol_mapped = symbol_upper
            symbol_key = f"{exchange}:{symbol_mapped}"

            data = self.kite.quote(symbol_key)
            if not data:
                return None

            quote_data = data.get(symbol_key, {})

            return {
                "symbol": symbol,
                "exchange": exchange,
                "last_price": quote_data.get("last_price", 0),
                "volume": quote_data.get("volume", 0),
                "bid": (
                    quote_data.get("depth", {}).get("buy", [{}])[0].get("price", 0)
                    if quote_data.get("depth")
                    else 0
                ),
                "ask": (
                    quote_data.get("depth", {}).get("sell", [{}])[0].get("price", 0)
                    if quote_data.get("depth")
                    else 0
                ),
            }
        except Exception as e:
            log.error(f"Failed to fetch Zerodha quote for {symbol}: {e}")
            return None

    def _save_access_token(self, token: str) -> None:
        """Save the access token to a file."""
        file_path = "data/zerodha_access_token.txt"
        with open(file_path, "w") as f:
            f.write(token)
        log.info(f"Access token saved to {file_path}")

    def _load_config(self):
        """Load configuration from config.yaml file."""
        config_path = os.path.join(os.path.dirname(__file__), "../config/config.yaml")
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        return config
