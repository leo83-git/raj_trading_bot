#!/usr/bin/env python3
"""Demo script to verify Zerodha WebSocket real‑time ticks.
It initializes the dynamic screening system (which creates a broker,
opens the WebSocket, subscribes to a batch of NSE equity tokens, and
updates an internal cache). After a short wait it prints the number of
instruments for which price data has been received and shows a few
sample entries.
"""

import os
import sys
import time

# Ensure the project root is on the import path so that ``screener`` can be imported
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import the helper that lazily creates the screening instance
from screener import get_screening


def main():
    screening = get_screening()
    if screening is None:
        sys.stderr.write(
            "Dynamic screening could not be initialized – check Zerodha credentials and token.\n"
        )
        sys.exit(1)

    # Give the WebSocket a moment to receive initial ticks
    # Wait longer to allow the background WebSocket listener to receive initial ticks.
    # The listener waits up to 90 seconds for the first price snapshot, so we give it
    # a comfortable margin.
    # Increase wait time to give the WebSocket listener more time to receive ticks.
    # The listener may need up to ~90 s for the first price snapshot, so we wait a
    # comfortable 60 seconds here.
    wait_seconds = 60
    # Simple wait message using only ASCII characters to avoid encoding issues
    # Use plain string concatenation to avoid hidden Unicode issues
    print("Waiting " + str(wait_seconds) + "s for initial market data ...")
    time.sleep(wait_seconds)

    # Trigger screening to apply filtering and prune subscriptions.
    screened = screening.get_filtered_symbols()
    print(f"Screened symbols count: {len(screened)}")

    # Access the internal WebSocket cache (thread‑safe via the lock)
    with screening._websocket_lock:
        ws_data = screening._websocket_data.copy()

    # Use plain concatenation for compatibility
    print("Received data for " + str(len(ws_data)) + " instruments.")
    # Show a few sample entries
    for i, (token, ohlc) in enumerate(ws_data.items()):
        if i >= 5:
            break
        print("Token " + str(token) + ": " + str(ohlc))


if __name__ == "__main__":
    main()
