#!/usr/bin/env python3
"""Daily historical data downloader.

This script is intended to be run after market close (e.g., via a cron job)
to fetch the latest historical candle data for all tradable symbols and
store it in DuckDB/Parquet using the existing `download_all_historical_to_sqlite`
logic.

It simply invokes the Typer CLI entry point defined in that module with the
default options. Adjust the options or schedule as needed.
"""

import os
import sys
from pathlib import Path

# Ensure the project root is on the Python path so that imports resolve.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ---------------------------------------------------------------------------
# Configure a writable default location for DuckDB/Parquet files.
# The original script expects an external drive at ``/media/rajasekhar/Backup``
# which may not be present or writable on every system. We fall back to a
# ``data/duckdb`` directory inside the project root. Users can still override
# the location by setting the ``DUCKDB_ROOT`` environment variable before
# invoking the script.
# ---------------------------------------------------------------------------
default_duckdb_root = ROOT_DIR / "data" / "duckdb"
os.environ.setdefault("DUCKDB_ROOT", str(default_duckdb_root))

# Import the Typer command function from the existing script.
try:
    from scripts.download_all_historical_to_sqlite import run as download_run
except Exception as e:
    raise RuntimeError(
        "Failed to import the historical download function. Ensure the project "
        "structure is unchanged and dependencies are installed."
    ) from e


def main() -> None:
    """Execute the historical download with default parameters.

    The underlying Typer command respects environment variables and can be
    overridden via command‑line arguments if this wrapper is extended.
    """
    # Call the Typer command programmatically with explicit defaults.
    # Import the constants defined in the target module after the import.
    from scripts.download_all_historical_to_sqlite import (
        CHUNK_SIZE,
        CSV_DIR,
        DB_PATH,
        DUCKDB_ROOT,
        INTERVAL,
        RATE_LIMIT_DELAY,
    )

    # Invoke the command function directly, passing concrete values instead of Typer Option objects.
    download_run(
        interval=INTERVAL,
        chunk_size=CHUNK_SIZE,
        rate_limit=RATE_LIMIT_DELAY,
        output_dir=str(CSV_DIR),
        db_path=str(DB_PATH),
        duckdb_root=str(DUCKDB_ROOT),
        skip_existing=False,
    )


if __name__ == "__main__":
    main()
