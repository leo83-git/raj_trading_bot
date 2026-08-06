"""
F&O Contract Loader Module
Fetches real-time NSE F&O option contract symbols (e.g., "NIFTY26APR50000CE") using Zerodha Kite Connect API
Replaces static fno_prefilter.py with dynamic, real-time F&O contract data
"""

import os
import threading
from datetime import datetime, timedelta

import requests
import yaml

from core.database import DatabaseManager
from quant_utils.logger import get_logger
from utils.cache import get_cached_fno_contracts, set_cached_fno_contracts

log = get_logger("fno_contract_loader")


class FnoContractLoader:
    """Fetches and caches real-time NSE F&O option contract symbols using Zerodha Kite Connect API"""

    CACHE_DURATION_HOURS: int = (
        24  # Refresh daily (matches Zerodha instrument list update)
    )
    _db_write_lock = threading.Lock()

    def __init__(self):
        self.contracts: list[dict] = []
        self.last_refresh: datetime | None = None
        self.db_manager: DatabaseManager | None = None
        self._background_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        try:
            self.db_manager = DatabaseManager()
            log.info("DatabaseManager initialized for F&O contract cache")
        except Exception as exc:
            log.debug(f"DatabaseManager unavailable for F&O cache: {exc}")
        self._load_cache()
        self._start_background_refresh()

    def _load_cache(self) -> None:
        """Load cached F&O contracts from ORM storage if recent."""
        cached = get_cached_fno_contracts()
        if cached:
            cached_last_refresh = cached.get("last_refresh")
            if isinstance(cached_last_refresh, datetime) and (
                datetime.now() - cached_last_refresh
            ) <= timedelta(hours=self.CACHE_DURATION_HOURS):
                self.contracts = list(cached.get("contracts", []))
                self.last_refresh = cached_last_refresh
                if self.contracts:
                    log.info(
                        f"Loaded {len(self.contracts)} F&O contracts from in-memory cache"
                    )
                    return
        if self.db_manager:
            try:
                contracts, cache_time = self.db_manager.load_fno_contract_cache()
                if cache_time and (
                    datetime.now() - cache_time
                ) <= timedelta(hours=self.CACHE_DURATION_HOURS):
                    self.contracts = contracts
                    self.last_refresh = cache_time
                if self.contracts:
                    log.info(
                        f"Loaded {len(self.contracts)} F&O contracts from ORM cache"
                    )
                    set_cached_fno_contracts(
                        "default",
                        {"contracts": self.contracts, "last_refresh": self.last_refresh},
                    )
                    return
            except Exception as e:
                log.warning(f"Failed to load F&O ORM cache: {e}")

    def _start_background_refresh(self) -> None:
        """Start background thread to refresh F&O contract list."""
        with self._lifecycle_lock:
            if self._background_thread and self._background_thread.is_alive():
                return
            self._background_thread = threading.Thread(
                target=self._refresh_contracts_async, daemon=True
            )
            self._background_thread.start()

    def _refresh_contracts_async(self) -> None:
        """Background refresh of F&O contracts list"""
        with self._refresh_lock:
            try:
                log.info("Starting background F&O contracts refresh")
                self._refresh_contracts_sync()
            except Exception as e:
                log.error(f"Background F&O refresh failed: {e}")

    def _refresh_contracts_sync(self) -> None:
        """Fetch F&O contracts from Zerodha instrument list API synchronously"""
        try:
            if self.contracts and self.last_refresh and (
                datetime.now() - self.last_refresh
            ) < timedelta(hours=self.CACHE_DURATION_HOURS):
                log.debug("F&O contracts already fresh; skipping refresh")
                return
        except Exception:
            pass
        try:
            # Load configuration from config.yaml
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "config", "config.yaml"
            )
            with open(config_path, "r") as f:
                config: dict = yaml.safe_load(f)

            # Get Zerodha API credentials from config.yaml
            api_key: str = config.get("zerodha_api_key", "")
            api_secret: str = config.get("zerodha_api_secret", "")

            if not api_key or not api_secret:
                log.error("Zerodha API credentials not configured in config.yaml")
                return

            # Zerodha instruments endpoint (public, no auth required)
            # Can also use specific exchange endpoints: /NSE, /NFO, etc.
            url = "https://api.kite.trade/instruments"

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Parse CSV with the csv module to handle quoted names and commas
            import csv

            reader = csv.DictReader(response.text.strip().splitlines())
            fno_contracts: list[dict] = []
            for row in reader:
                instrument_token = row.get("instrument_token")
                tradingsymbol = row.get("tradingsymbol")
                expiry = row.get("expiry")
                strike = row.get("strike")
                instrument_type = row.get("instrument_type") or ""
                segment = row.get("segment") or ""
                exchange = row.get("exchange") or ""

                if not instrument_token or not tradingsymbol:
                    continue

                if instrument_type.startswith("OPT") or instrument_type.startswith(
                    "FUT"
                ):
                    option_type = None
                    if instrument_type.startswith("OPT"):
                        if tradingsymbol.endswith("PE"):
                            option_type = "PE"
                        elif tradingsymbol.endswith("CE"):
                            option_type = "CE"
                    elif instrument_type.startswith("FUT"):
                        option_type = "FUT"

                    contract_info = {
                        "symbol": tradingsymbol,
                        "instrument_token": instrument_token,
                        "exchange": exchange,
                        "segment": segment,
                        "expiry": expiry,
                        "strike": float(strike) if strike else None,
                        "option_type": option_type,
                    }
                    fno_contracts.append(contract_info)

            self.contracts = self._dedupe_contracts(fno_contracts)
            self.last_refresh = datetime.now()
            log.info(f"Refreshed {len(self.contracts)} F&O contracts from Zerodha")
            self._save_cache()
            set_cached_fno_contracts(
                "default",
                {"contracts": self.contracts, "last_refresh": self.last_refresh},
            )

        except Exception as e:
            log.error(f"Failed to refresh F&O contracts from Zerodha: {e}")
            # Keep existing contracts if refresh fails

    def refresh_async(self) -> None:
        """Explicitly trigger a background refresh."""
        self._start_background_refresh()

    def _save_cache(self) -> None:
        """Save F&O contracts to ORM cache."""
        if not self.db_manager or not self.last_refresh:
            return
        try:
            with self._db_write_lock:
                count = self.db_manager.replace_fno_contract_cache(
                    self.contracts, self.last_refresh
                )
            log.info(f"Saved {count} F&O contracts to ORM cache")
        except Exception as e:
            log.warning(f"Failed to save F&O cache to ORM storage: {e}")

    def _dedupe_contracts(self, contracts: list[dict]) -> list[dict]:
        """Return a stable list of contracts with one row per symbol."""
        deduped: dict[str, dict] = {}
        duplicate_symbols: list[str] = []
        dropped = 0
        for contract in contracts:
            symbol = str(contract.get("symbol", "")).strip()
            if not symbol:
                continue
            if symbol in deduped:
                dropped += 1
                if symbol not in duplicate_symbols:
                    duplicate_symbols.append(symbol)
            deduped[symbol] = contract
        if dropped:
            sample = ", ".join(duplicate_symbols[:5])
            suffix = f" Sample: {sample}" if sample else ""
            log.warning(
                f"Collapsed {dropped} duplicate F&O contracts from upstream feed.{suffix}"
            )
        return list(deduped.values())

    def get_fno_symbols(self) -> list[str]:
        """Get list of F&O contract symbols"""
        return [contract["symbol"] for contract in self.contracts]
