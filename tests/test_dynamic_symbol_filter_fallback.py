"""Unit tests for the fallback behavior of :class:`DynamicSymbolFilter`.

The production implementation injects a minimal set of core indices
(`"NIFTY"` and `"BANKNIFTY"`) when the real‑time screening service cannot be
initialised (e.g., missing Zerodha credentials). These tests verify that the
fallback is correctly applied for both ``get_intraday_symbols`` and
``get_filtered_symbols``.
"""

import unittest
from unittest.mock import patch

# Import the class under test. The module creates a global instance at import
# time, but we instantiate a fresh object for isolation.
from screener.dynamic_symbol_filter import DynamicSymbolFilter


class TestDynamicSymbolFilterFallback(unittest.TestCase):
    """Ensure fallback symbols are returned when screening is unavailable."""

    @patch("screener.dynamic_symbol_filter.get_screening", return_value=None)
    def test_get_intraday_symbols_fallback(self, mock_screening):
        """When ``get_screening`` returns ``None`` the intraday list contains core indices."""
        dsf = DynamicSymbolFilter()
        intraday = dsf.get_intraday_symbols()
        # The fallback should always include the two core indices.
        self.assertIsInstance(intraday, list)
        self.assertIn("NIFTY", intraday)
        self.assertIn("BANKNIFTY", intraday)
        # No F&O symbols should be present in the intraday list.
        # (The fallback only adds core indices, which are allowed.)
        self.assertTrue(all(isinstance(sym, str) for sym in intraday))

    @patch("screener.dynamic_symbol_filter.get_screening", return_value=None)
    def test_get_filtered_symbols_fallback(self, mock_screening):
        """The combined ``get_filtered_symbols`` also falls back to core indices."""
        dsf = DynamicSymbolFilter()
        all_symbols = dsf.get_filtered_symbols()
        self.assertIsInstance(all_symbols, list)
        # Core indices must be present.
        self.assertIn("NIFTY", all_symbols)
        self.assertIn("BANKNIFTY", all_symbols)
        # The list should contain no ``None`` entries.
        self.assertNotIn(None, all_symbols)


if __name__ == "__main__":
    unittest.main()
