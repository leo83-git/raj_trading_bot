"""
Symbol Filtering Module
Integrates F&O pre-filtering to reduce symbol universe from 130,944 to manageable number
"""

from quant_utils.logger import get_logger
from screener.fno_prefilter import FnoPreFilter

log = get_logger("symbol_filter")


class SymbolFilter:
    """Filters symbols using F&O pre-filtering"""

    def __init__(self):
        self.fno_filter = FnoPreFilter()

    def get_filtered_symbols(self) -> list[str]:
        """Get filtered list of F&O eligible symbols"""
        try:
            # Ensure cache is loaded
            if not self.fno_filter.fno_stocks:
                log.info("Loading F&O symbol filter...")

            filtered_symbols = self.fno_filter.fno_stocks
            log.info(f"Filtered symbol universe: {len(filtered_symbols)} symbols")

            # Log first 10 symbols as example
            if filtered_symbols:
                log.info(f"Sample symbols: {filtered_symbols[:10]}")

            return filtered_symbols

        except Exception as e:
            log.error(f"Failed to get filtered symbols: {e}")
            # Return fallback list if filtering fails
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


# Create global instance for easy access
symbol_filter = SymbolFilter()
