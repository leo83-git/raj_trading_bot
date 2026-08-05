"""
Screener package initialization file.
This file initializes the DynamicSymbolScreening with Zerodha credentials from config.
"""

from pathlib import Path

from core.config import ZERODHA_API_KEY, ZERODHA_API_SECRET
from core.logger import get_logger
from core.zerodha_broker import ZerodhaBroker as CoreZerodhaBroker

# Extract Zerodha credentials from shared config
api_key = ZERODHA_API_KEY
api_secret = ZERODHA_API_SECRET

TOKEN_FILE = Path(__file__).parent.parent / "data" / "zerodha_token.json"

# Lazily create broker and screening instances when needed
broker = None
screening = None

log = get_logger("screener")


def get_screening():
    """Return or initialize the DynamicSymbolScreening instance."""
    global broker, screening
    if screening is not None:
        return screening

    if not api_key or not api_secret:
        log.warning(
            "Zerodha API credentials not configured; skipping dynamic screening"
        )
        return None

    try:
        from core.token_manager import ZerodhaTokenManager

        token_manager = ZerodhaTokenManager(api_key, api_secret)
        if not token_manager.load_token():
            log.warning("No valid Zerodha token available; skipping dynamic screening")
            return None

        broker = CoreZerodhaBroker(
            api_key=api_key,
            api_secret=api_secret,
            access_token=token_manager.access_token,
        )
        # Connect the broker to initialize instruments cache
        if not broker.connect():
            log.warning("Zerodha broker connection failed; skipping dynamic screening")
            return None
        # Import lazily to avoid side‑effects (e.g., starting background threads) during module import.
        from screener.dynamic_screening import initialize_screening

        screening = initialize_screening(broker)
    except Exception:
        # Log the exception with full traceback for debugging
        log.exception("Failed to initialize ZerodhaBroker")
        screening = None
        return screening

    # Return the initialized screening instance (or None if initialization failed)
    return screening


log.info("Screener module loaded")
