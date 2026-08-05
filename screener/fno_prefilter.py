"""
F&O Pre-Filter Module
Handles pre-filtering of F&O eligible stocks for efficient scanning
"""

import json
import os
import threading
from datetime import datetime, timedelta

try:
    from nselib import capital_market

    NSE_AVAILABLE = True
except ImportError:
    NSE_AVAILABLE = False

from quant_utils.logger import get_logger

log = get_logger("fno_prefilter")


class FnoPreFilter:
    """Pre-filters NSE stocks to F&O eligible universe"""

    CACHE_FILE = "data/fno_stocks_cache.json"
    CACHE_DURATION_HOURS = 6  # Refresh every 6 hours for more dynamic universe

    def __init__(self, data_provider=None):
        self.data_provider = data_provider
        self.fno_stocks = []
        self.last_refresh = None
        os.makedirs("data", exist_ok=True)
        self._load_cache()

    def _load_cache(self):
        """Load cached F&O stocks if recent"""
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, "r") as f:
                    data = json.load(f)
                    self.fno_stocks = data.get("stocks", [])
                    cache_time = datetime.fromisoformat(
                        data.get("timestamp", "2000-01-01")
                    )
                    if datetime.now() - cache_time < timedelta(
                        hours=self.CACHE_DURATION_HOURS
                    ):
                        self.last_refresh = cache_time
                        log.info(f"Loaded {len(self.fno_stocks)} F&O stocks from cache")
                        return
            except Exception as e:
                log.warning(f"Failed to load F&O cache: {e}")

        # Start background refresh if no valid cache
        self._start_background_refresh()

    def _start_background_refresh(self):
        """Start background thread to refresh F&O list"""
        thread = threading.Thread(target=self._refresh_fno_list_async, daemon=True)
        thread.start()

    def _refresh_fno_list_async(self):
        """Background refresh of F&O stocks list"""
        try:
            log.info("Starting background F&O list refresh")
            self._refresh_fno_list_sync()
        except Exception as e:
            log.error(f"Background F&O refresh failed: {e}")

    def _refresh_fno_list_sync(self):
        """Fetch current F&O eligible stocks from NSE synchronously"""
        if self.data_provider and self._refresh_fno_list_from_data_provider():
            self._save_cache()
            return

        if not NSE_AVAILABLE:
            log.warning("nselib not available, using fallback F&O list")
            self.fno_stocks = self._get_fallback_fno_list()
            self._save_cache()
            return

        try:
            # Get F&O stocks from nselib
            fno_data = capital_market.fno_equity_list()

            if fno_data is not None and "symbol" in fno_data.columns:
                self.fno_stocks = fno_data["symbol"].dropna().unique().tolist()
                # Filter out indices and keep only equity stocks
                self.fno_stocks = [
                    s
                    for s in self.fno_stocks
                    if not s.endswith("NIFTY")
                    and not s.endswith("BANKNIFTY")
                    and len(s) > 2
                ]
                log.info(f"Refreshed {len(self.fno_stocks)} F&O stocks from NSE")
            else:
                log.warning("Invalid F&O data from NSE, using fallback")
                self.fno_stocks = self._get_fallback_fno_list()

            self.last_refresh = datetime.now()
            self._save_cache()

        except Exception as e:
            log.error(f"Failed to refresh F&O list: {e}")
            self.fno_stocks = self._get_fallback_fno_list()
            self._save_cache()

    def _get_fallback_fno_list(self) -> list[str]:
        """Fallback list of major F&O stocks"""
        return [
            "RELIANCE",
            "HDFCBANK",
            "ICICIBANK",
            "KOTAKBANK",
            "AXISBANK",
            "LT",
            "HINDUNILVR",
            "MARUTI",
            "SUNPHARMA",
            "TITAN",
            "BAJFINANCE",
            "DIVISLAB",
            "CIPLA",
            "DRREDDY",
            "HEROMOTOCO",
            "TATACONSUM",
            "BPCL",
            "COALINDIA",
            "NTPC",
            "POWERGRID",
            "ONGC",
            "TCS",
            "INFY",
            "ADANIPOWER",
            "M&M",
            "ASIANPAINT",
            "HDFCLIFE",
            "BRITANNIA",
            "NESTLEIND",
            "TECHM",
            "WIPRO",
            "HCLTECH",
            "ULTRACEMCO",
            "GRASIM",
            "ADANIENT",
            "SBILIFE",
        ]

    def _refresh_fno_list_from_data_provider(self) -> bool:
        """Try to build F&O stock universe from data provider instruments."""
        if self.data_provider is None:
            return False

        try:
            instruments = self.data_provider.get_instrument_master("NFO")
            if not instruments:
                return False

            # Derive a list of unique underlying symbols from instrument master data
            symbols = set()
            for item in instruments:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol") or item.get("name")
                if isinstance(symbol, str) and symbol.strip():
                    symbols.add(symbol.strip().upper())

            if symbols:
                self.fno_stocks = sorted(symbols)
                log.info(
                    f"Refreshed {len(self.fno_stocks)} F&O stocks from data provider"
                )
                return True

        except Exception as e:
            log.warning(f"Data provider F&O refresh failed: {e}")

        return False

    def _save_cache(self):
        """Save current F&O list to cache"""
        try:
            data = {"stocks": self.fno_stocks, "timestamp": datetime.now().isoformat()}
            with open(self.CACHE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"Failed to save F&O cache: {e}")

    def get_fno_stocks(self) -> list[str]:
        """Get current F&O eligible stocks"""
        if not self.fno_stocks:
            # Synchronous refresh if no data available
            log.warning("No F&O stocks available, performing sync refresh")
            try:
                self._refresh_fno_list_sync()
            except Exception as e:
                log.error(f"Sync refresh failed: {e}")
                self.fno_stocks = self._get_fallback_fno_list()

        return self.fno_stocks.copy()

    def is_fno_eligible(self, symbol: str) -> bool:
        """Check if symbol is F&O eligible"""
        stocks = self.get_fno_stocks()
        upper_symbol = symbol.upper()
        for s in stocks:
            if s.upper() == upper_symbol:
                return True
        return False
