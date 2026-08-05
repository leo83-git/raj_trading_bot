"""Unit tests for :class:`ZerodhaWebSocket` message handling.

The real WebSocket connection requires network access and valid credentials,
which are unavailable in the CI environment.  These tests therefore focus on
the pure‑Python logic inside ``_on_message`` – parsing binary packets and error
messages – by calling the method directly with fabricated data.
"""

import json
import struct

import pytest

from core.zerodha_websocket import ZerodhaWebSocket


@pytest.fixture
def ws():
    """Create a ``ZerodhaWebSocket`` instance with a dummy logger.

    The ``api_key`` and ``access_token`` values are irrelevant for the unit
    tests because we never open a real connection.
    """
    return ZerodhaWebSocket(api_key="dummy", access_token="dummy")


def test_on_message_heartbeat(ws, caplog):
    """A 1‑byte heartbeat should be ignored without raising an exception."""
    ws._on_message(ws, b"\x00")
    # No error logs should be emitted for a heartbeat.
    assert not any(record.levelname == "ERROR" for record in caplog.records)


def test_on_message_error_json(ws, caplog):
    """When the server sends an error JSON, it should be logged as error."""
    error_msg = json.dumps({"type": "error", "message": "invalid token"})
    ws._on_message(ws, error_msg)
    # Verify that an error log entry was created.
    assert any(
        "WebSocket error message" in record.getMessage() for record in caplog.records
    )


def test_on_message_ltp_packet(ws):
    """Parse a minimal 8‑byte LTP packet and store the price in the cache.

    The packet layout used by Zerodha is:
        token (2 bytes) | reserved (1) | price (4 bytes float) | timestamp (1)
    For the test we use token ``1`` and price ``123.45``.
    """
    token = 1
    price = 123.45
    # Construct packet according to the layout described above.
    packet = struct.pack(">H B f B", token, 0, price, 0)
    ws._on_message(ws, packet)
    # The cache should contain the token as both int and str keys.
    assert ws.price_cache[token]["close"] == price
    assert ws.price_cache[str(token)]["close"] == price
