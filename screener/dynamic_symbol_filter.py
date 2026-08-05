"""
Dynamic symbol filter that integrates real-time screening with the trading system.
This module provides a unified interface for the trading system to get dynamically screened symbols from the full NSE master contract, replacing the static F&O filter.
"""

from quant_utils.logger import get_logger
from screener import get_screening
from screener.fno_contract_loader import FnoContractLoader

log = get_logger("screener.dynamic_symbol_filter")


class DynamicSymbolFilter:
    """Wrapper for dynamic screening that provides the same interface as the old SymbolFilter."""

    def __init__(self):
        self.fno_loader = FnoContractLoader()

    def get_filtered_symbols(self) -> list:
        """Get dynamically screened symbols using real-time market data analysis.

        Returns:
            List of symbols that pass real-time screening criteria (equities + F&O options)
        """
        # Lazily initialize the screening instance and handle missing Zerodha credentials gracefully
        screening_instance = get_screening()
        equity_symbols = []
        if screening_instance is not None:
            try:
                equity_symbols = screening_instance.get_filtered_symbols()
            except Exception as e:
                log.warning(
                    f"Dynamic screening failed, falling back to static F&O list only: {e}"
                )

        # -----------------------------------------------------------------
        # Fallback for intraday symbols
        # -----------------------------------------------------------------
        # In some environments the real‑time screening may return an empty
        # list (e.g., when Zerodha credentials are missing).  The original
        # implementation would then merge only the F&O symbols, causing the
        # intraday pipeline to become empty.  To keep the intraday side alive
        # we provide a minimal default set of core indices when the equity
        # list is empty.
        if not equity_symbols:
            log.info(
                "Equity screening returned no symbols – injecting core intraday symbols as fallback"
            )
            equity_symbols = ["NIFTY", "BANKNIFTY"]

        # Ensure we always work with a list of strings for symbol normalization.
        if isinstance(equity_symbols, (set, tuple)):
            equity_symbols = list(equity_symbols)
        if not isinstance(equity_symbols, list):
            equity_symbols = [equity_symbols]
        equity_symbols = [
            str(sym).strip()
            for sym in equity_symbols
            if sym is not None and str(sym).strip()
        ]

        # Get F&O option contract symbols
        fno_symbols = self.fno_loader.get_fno_symbols()
        if isinstance(fno_symbols, (set, tuple)):
            fno_symbols = list(fno_symbols)
        if not isinstance(fno_symbols, list):
            fno_symbols = [fno_symbols]
        fno_symbols = [
            str(sym).strip()
            for sym in fno_symbols
            if sym is not None and str(sym).strip()
        ]

        # Merge and deduplicate
        all_symbols = sorted(set(equity_symbols + fno_symbols))

        return all_symbols

    # ---------------------------------------------------------------------
    # Separate symbol getters – used by the main pipeline to keep intraday
    # and F&O symbols independent while still sharing the same underlying
    # screening infrastructure.
    # ---------------------------------------------------------------------
    def get_intraday_symbols(self) -> list:
        """Return only the equity (intraday) symbols from the dynamic screen.

        The original implementation called ``screening_instance.get_filtered_symbols``
        and then returned the raw list, which includes both equity and F&O symbols.
        When the dynamic screening cannot be initialised (e.g., missing Zerodha
        credentials or token), ``screening_instance`` is ``None`` and the method
        returned an empty list, triggering the *"NO INTRADAY STOCKS to process"*
        warning.

        This revised version mirrors the fallback behaviour used in
        ``get_filtered_symbols``: if the screening instance is unavailable we
        provide a minimal core‑indices list (``["NIFTY", "BANKNIFTY"]``). When a
        screening instance is available we still need to strip out the F&O
        symbols that are added by ``DynamicSymbolScreening``. We achieve this by
        retrieving the full filtered list and then removing any symbols that are
        present in the F&O loader.
        """
        screening_instance = get_screening()
        equity_symbols = []
        if screening_instance is not None:
            try:
                # Get the full list (equities + F&O) and then filter out the F&O symbols.
                full_symbols = screening_instance.get_filtered_symbols()
                fno_symbols = set(self.fno_loader.get_fno_symbols())
                equity_symbols = [s for s in full_symbols if s not in fno_symbols]
            except Exception as e:
                log.warning(f"Dynamic screening failed for intraday: {e}")
        # Fallback when the screening instance could not be created.
        if not equity_symbols:
            log.info(
                "Intraday screening unavailable – injecting core symbols as fallback"
            )
            equity_symbols = ["NIFTY", "BANKNIFTY"]

        # Normalise to a list of clean strings.
        if isinstance(equity_symbols, (set, tuple)):
            equity_symbols = list(equity_symbols)
        if not isinstance(equity_symbols, list):
            equity_symbols = [equity_symbols]
        return [str(sym).strip() for sym in equity_symbols if sym]

    def get_fno_symbols(self) -> list:
        """Return only the F&O symbols (option contracts) from the loader.

        This mirrors the fallback behaviour that the original wrapper used when
        the dynamic screening failed.
        """
        fno_symbols = self.fno_loader.get_fno_symbols()
        if isinstance(fno_symbols, (set, tuple)):
            fno_symbols = list(fno_symbols)
        if not isinstance(fno_symbols, list):
            fno_symbols = [fno_symbols]
        return [str(sym).strip() for sym in fno_symbols if sym]


# Create a global instance for easy access
dynamic_symbol_filter = DynamicSymbolFilter()
