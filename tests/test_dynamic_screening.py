"""Focused tests for :class:`DynamicSymbolScreening` fallback behavior.

The real implementation relies on a live Zerodha broker and a WebSocket
connection.  For unit testing we provide a lightweight mock broker that
exposes only the methods used by ``DynamicSymbolScreening``.  The tests verify
that when the WebSocket cache remains empty the class correctly falls back to
the FNO‑based REST fetch and that ``get_filtered_symbols`` returns an empty list
without timing out.
"""

import pytest

from screener.dynamic_screening import DynamicSymbolScreening


class DummyQuote:
    """Simple object mimicking the quote returned by the broker."""

    def __init__(self, last_price=0.0, open_price=None, volume=0):
        self.last_price = last_price
        self.open = open_price
        self.volume = volume


class MockBroker:
    """Minimal broker stub required by ``DynamicSymbolScreening``.

    * ``_load_instruments`` – returns an empty list so no tokens are subscribed.
    * ``get_quote`` – returns a ``DummyQuote`` for any symbol.
    * ``websocket`` – attribute is omitted to force the class to use the REST
      fallback path.
    """

    def _load_instruments(self):
        return []

    def get_quote(self, symbol):
        # Return a quote with a non‑zero price to exercise the fallback logic.
        return DummyQuote(last_price=100.0, open_price=95.0, volume=10)


@pytest.fixture
def screening(monkeypatch):
    """Create a ``DynamicSymbolScreening`` instance with the background listener
    disabled.  The real implementation spawns a thread that attempts to
    connect to Zerodha, which fails in the isolated test environment.
    """
    # Replace the listener starter with a no‑op to avoid network activity.
    monkeypatch.setattr(
        DynamicSymbolScreening, "_start_websocket_listener", lambda self: None
    )
    broker = MockBroker()
    return DynamicSymbolScreening(broker)


def test_fetch_from_fno_symbols_uses_broker(screening, monkeypatch):
    """When the WebSocket cache is empty, ``_fetch_from_fno_symbols`` should
    retrieve data via the broker's ``get_quote`` method.
    """
    # Patch the FNO loader to return a deterministic list of symbols.
    monkeypatch.setattr(
        screening.fno_loader, "get_fno_symbols", lambda: ["TEST1", "TEST2"]
    )
    data = screening._fetch_from_fno_symbols()
    # Two entries should be present, keyed by the hash of the symbol.
    assert len(data) == 2
    for symbol in ["TEST1", "TEST2"]:
        # Verify that the stored dict contains expected fields.
        entry = data[hash(symbol)]
        assert entry["high"] == entry["low"] == entry["close"] == 100.0
        assert entry["open"] == 95.0
        assert entry["volume"] == 10
        assert entry["_symbol"] == symbol


def test_websocket_cache_worker_populates_websocket_data(screening):
    """Queued WebSocket ticks should be consumed and stored in the internal cache."""
    tick = {
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "volume": 50,
        "_symbol": "TEST_SYMBOL",
    }

    screening._ws_queue.put((123456, tick))
    screening._ws_queue.join()

    assert 123456 in screening._websocket_data
    assert screening._websocket_data[123456]["close"] == 1.05

    fetched = screening._fetch_ohlc_data()
    assert fetched[123456]["close"] == 1.05
    assert fetched[123456]["_symbol"] == "TEST_SYMBOL"


def test_cache_worker_handles_invalid_queue_items(screening):
    """Invalid queue items should not break the cache worker."""
    invalid_item = "invalid-entry"
    screening._ws_queue.put(invalid_item)

    tick = {
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 100,
        "_symbol": "TEST_SYMBOL",
    }
    screening._ws_queue.put((654321, tick))
    screening._ws_queue.join()

    assert 654321 in screening._websocket_data
    assert screening._websocket_data[654321]["close"] == 11.0


def test_get_filtered_symbols_returns_empty_when_no_data(screening, monkeypatch):
    """If the WebSocket cache never fills, ``get_filtered_symbols`` should
    return an empty list after the internal timeout without raising.
    """
    # Force the internal OHLC fetch to return an empty dict instantly.
    monkeypatch.setattr(screening, "_fetch_ohlc_data", dict)
    result = screening.get_filtered_symbols()
    assert result == []
