#!/usr/bin/env python3
"""Download full historical candle data for *all* symbols using Zerodha
Broker, store each symbol as a CSV file and import the data into a SQLite
database.

The script respects Zerodha rate‑limits (≈0.35 s between calls) and can be
run periodically – it will only append new rows because the SQLite table
uses a primary key on ``(symbol, ts)``.
"""

import csv
import json
import os

# ---------------------------------------------------------------------------
# Ensure the project root is on ``sys.path`` when the script is executed
# directly (e.g. ``python scripts/download_all_historical_to_sqlite.py``).
# This allows imports such as ``from sources.broker import ZerodhaBroker`` to
# resolve correctly without requiring the user to modify PYTHONPATH.
# ---------------------------------------------------------------------------
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]  # ``scripts`` → project root
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Optional uvloop for faster asyncio event loop (no async code currently, but
# importing it does no harm and prepares the script for future async
# extensions).
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    pass

# ---------------------------------------------------------------------------
# Third‑party utilities (suggested enhancements)
# ---------------------------------------------------------------------------
try:
    # Rich provides prettier console output and progress bars
    from rich.console import Console
    from rich.progress import BarColumn, Progress, TimeRemainingColumn

    console = Console()
except Exception:
    # Fallback to a no‑op console if Rich is not installed
    class _DummyConsole:
        def log(self, *args, **kwargs):
            print(*args)

    console = _DummyConsole()

try:
    # Tenacity adds retry logic with exponential back‑off
    from tenacity import retry
except Exception:
    # Define a no‑op decorator when tenacity is unavailable
    def retry(*_, **__):
        def decorator(func):
            return func

        return decorator


try:
    # python‑dotenv for loading API credentials from a .env file
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# Local imports – these work when the script is executed from the project root
from quant_utils.logger import get_logger
from sources.broker import ZerodhaBroker

# Optional third‑party CLI helper
try:
    import typer
except Exception:
    # Provide a minimal fallback if typer is not installed – the script can still be run programmatically.
    class _DummyTyper:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("Typer is required for CLI usage. Install it via pip.")

    typer = _DummyTyper()

# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------
log = get_logger("historical_download")


# ---------------------------------------------------------------------------
# Helper to load Zerodha broker configuration from the project's config file.
# ---------------------------------------------------------------------------
def _load_broker_config() -> dict:
    """Load Zerodha credentials from ``config/config.yaml``.

    The function looks for the configuration file two directories up from
    this script (project root) under ``config/config.yaml``. If the file is
    present, it is parsed with ``yaml.safe_load`` and the resulting mapping
    is returned. Missing keys are tolerated – the ``ZerodhaBroker`` will handle
    absent values appropriately.
    """
    try:
        import yaml

        # The configuration lives under ``config/config.yaml``
        # relative to the project root (one level up from the ``scripts``
        # directory). ``parents[1]`` gives the project root.
        project_root = Path(__file__).resolve().parents[1]
        cfg_path = project_root / "config" / "config.yaml"
        if cfg_path.is_file():
            with open(cfg_path, "r") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:  # pragma: no cover – defensive fallback
        log.debug(f"Failed to load broker config: {exc}")
        try:
            from tqdm import tqdm

            completed = _load_progress()
            for sym in tqdm(symbols, desc="Downloading", unit="symbol"):
                if sym in completed:
                    continue
                _wait_if_paused()
                if skip_existing and (PARQUET_DIR / f"{sym}.parquet").exists():
                    log.debug(f"Skipping existing Parquet for {sym}")
                    continue
                try:
                    download_symbol(broker, sym, duck_con=duck_con)
                    _append_progress(sym)
                except Exception as exc:
                    log.error(f"Error while processing {sym}: {exc}")
        except Exception:
            # Final fallback: simple logging every 100 symbols
            completed = _load_progress()
            for idx, sym in enumerate(symbols, start=1):
                if sym in completed:
                    continue
                _wait_if_paused()
                if skip_existing and (PARQUET_DIR / f"{sym}.parquet").exists():
                    log.debug(f"Skipping existing Parquet for {sym}")
                    continue
                try:
                    download_symbol(broker, sym, duck_con=duck_con)
                    _append_progress(sym)
                except Exception as exc:
                    log.error(f"Error while processing {sym}: {exc}")
                if idx % 100 == 0:
                    log.info(f"Progress: {idx}/{total} symbols processed")


# ---------------------------------------------------------------------------
# Configuration (mirrors the enhanced script)
# ---------------------------------------------------------------------------
INTERVAL = os.getenv("INTERVAL", "day")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "2000"))
RATE_LIMIT_DELAY = float(os.getenv("RATE_LIMIT_DELAY", "0.35"))
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
CSV_DIR = DATA_ROOT / "zerodha_csv"
DB_PATH = DATA_ROOT / "zerodha_history.db"

# DuckDB / Parquet configuration – can be overridden via DUCKDB_ROOT env var.
DUCKDB_ROOT = Path(
    os.getenv(
        "DUCKDB_ROOT",
        "/media/rajasekhar/Backup/duckdb/",
    )
)
DUCKDB_ROOT.mkdir(parents=True, exist_ok=True)
DUCKDB_PATH = DUCKDB_ROOT / "historical_data.duckdb"
PARQUET_DIR = DUCKDB_ROOT / "parquet"
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

# Optional imports – duckdb and pandas are heavy but useful. Provide graceful fallbacks.
try:
    import duckdb
except Exception:
    duckdb = None
    log.warning("duckdb package not installed – DuckDB functionality will be disabled.")
try:
    import pandas as pd
except Exception:
    pd = None
    log.warning("pandas package not installed – Parquet export will be disabled.")

# Ensure CSV directory exists (compatibility with existing code).
CSV_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helper to initialise DuckDB connection
# ---------------------------------------------------------------------------
def init_duckdb():
    """Create (or reconnect to) the DuckDB database and ensure the ``candles``
    table exists.

    Returns a ``duckdb.DuckDBPyConnection`` instance. Raises ``RuntimeError`` if
    the ``duckdb`` package is not available.
    """
    if duckdb is None:
        raise RuntimeError(
            "duckdb package is not installed – cannot initialise DuckDB connection"
        )
    # Connect to the database file (creates it if missing).
    con = duckdb.connect(str(DUCKDB_PATH))
    # Ensure the target table exists with the appropriate schema.
    con.execute("""
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            ts TIMESTAMP NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume BIGINT,
            PRIMARY KEY (symbol, ts)
        )
        """)
    return con


# SQLite initialization removed – DuckDB is used exclusively.
# ---------------------------------------------------------------------------
# Pause/Resume and progress checkpoint helpers (mirrors enhanced script)
# ---------------------------------------------------------------------------


def _progress_file_path() -> Path:
    """Path to JSON file tracking completed symbols for the current ``INTERVAL``."""
    return DATA_ROOT / f"download_progress_{INTERVAL}.json"


def _load_progress() -> set:
    """Load set of symbols already downloaded from the checkpoint file."""
    p = _progress_file_path()
    if not p.is_file():
        return set()
    try:
        with open(p, "r") as f:
            data = json.load(f)
        return set(data)
    except Exception as e:
        log.debug(f"Failed to read progress file {p}: {e}")
        return set()


def _append_progress(symbol: str) -> None:
    """Append a successfully processed *symbol* to the progress file."""
    p = _progress_file_path()
    completed = _load_progress()
    completed.add(symbol)
    try:
        with open(p, "w") as f:
            json.dump(sorted(completed), f)
    except Exception as e:
        log.debug(f"Failed to write progress file {p}: {e}")


def _wait_if_paused() -> None:
    """Block execution while a ``pause.flag`` file exists in ``DATA_ROOT``."""
    pause_file = DATA_ROOT / "pause.flag"
    while pause_file.is_file():
        log.info("Download paused – waiting for pause.flag removal")
        time.sleep(5)


# SQLite persistence removed – data is stored directly in DuckDB and Parquet.


def save_to_duckdb(
    duck_con: "duckdb.DuckDBPyConnection", symbol: str, rows: list[dict]
):
    """Insert rows into DuckDB ``candles`` table.

    If pandas is available we use a DataFrame for bulk insert; otherwise we fall
    back to executemany with a simple tuple list.
    """
    if duck_con is None:
        return
    # Prepare data with an explicit ``symbol`` column.
    if pd is not None:
        # Build a DataFrame and ensure the timestamp column is named ``timestamp``
        # because the broker returns ``date``. This normalises the schema for the
        # downstream DuckDB insert.
        df = pd.DataFrame(rows)
        # Rename ``date`` to ``timestamp`` if present.
        if "date" in df.columns:
            df = df.rename(columns={"date": "timestamp"})
        df["symbol"] = symbol
        # Register temporary view and insert using an explicit column list to
        # guarantee correct ordering.
        duck_con.register("tmp_df", df)
        duck_con.execute(
            "INSERT OR REPLACE INTO candles (symbol, ts, open, high, low, close, volume) "
            "SELECT symbol, timestamp, open, high, low, close, volume FROM tmp_df"
        )
        duck_con.unregister("tmp_df")
    else:
        # Manual insertion without pandas.
        duck_con.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                ts TIMESTAMP NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                PRIMARY KEY (symbol, ts)
            )
            """)
        insert_sql = "INSERT OR REPLACE INTO candles (symbol, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)"
        data = []
        for row in rows:
            # ``row`` may contain ``date`` instead of ``timestamp``.
            ts = row.get("timestamp", row.get("date"))
            data.append(
                (
                    symbol,
                    ts,
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                )
            )
        duck_con.executemany(insert_sql, data)


def write_parquet(symbol: str, rows: list[dict]):
    """Write a Parquet file for *symbol* under ``PARQUET_DIR``.

    Requires pandas; if pandas is unavailable the function is a no‑op.
    """
    if pd is None:
        return
    df = pd.DataFrame(rows)
    df["symbol"] = symbol
    parquet_path = PARQUET_DIR / f"{symbol}.parquet"
    df.to_parquet(parquet_path, index=False)


def write_csv(symbol: str, rows: list[dict]):
    """Write a CSV file ``<symbol>_<interval>.csv`` under ``CSV_DIR``.
    The CSV header matches the column names used by the DB.
    """
    csv_path = CSV_DIR / f"{symbol}_{INTERVAL}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "open", "high", "low", "close", "volume"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "timestamp": row["timestamp"],
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                }
            )
    return csv_path


def fetch_all_symbols(broker: ZerodhaBroker) -> list[str]:
    """Return a list of tradable symbols filtered to NSE and NFO.

    The broker lazily loads the master‑contract cache; we ensure it is populated
    and then filter the instruments to those belonging to the NSE or NFO
    exchanges. For NSE we keep equity symbols (instrument_type ``EQ``). For NFO we
    keep futures and options (instrument_type ``FUT`` or ``OPT``).
    """
    # Load the master‑contract cache if it hasn't been loaded yet. The
    # ``ZerodhaBroker`` provides a private ``_load_instruments`` helper that
    # ensures the cache is populated (downloading from Zerodha if required).
    if hasattr(broker, "_load_instruments"):
        instruments = broker._load_instruments()
    else:
        # Fallback – attempt to use any existing cache attribute.
        instruments = getattr(broker, "instruments_cache", {}).values()
    filtered_symbols: list[str] = []
    for inst in instruments:
        exchange = inst.get("exchange")
        inst_type = inst.get("instrument_type")
        tradingsymbol = inst.get("tradingsymbol")
        name = inst.get("name", "").upper()
        # Basic NSE equity filter
        if exchange == "NSE" and inst_type == "EQ":
            # Exclude bonds/SDL/T-bill instruments which return no candles via Kite.
            # Exclude known bond/T‑bill patterns, mutual‑fund NAVs, ETFs and other instruments
            # that typically lack historical candles via Kite.
            if tradingsymbol.endswith(
                ("-SG", "-TB", "-N0", "-SM", "-BE", "-INAV", "-NAV")
            ):
                continue
            if any(
                keyword in name
                for keyword in [
                    "SDL",
                    "TBILL",
                    "BOND",
                    "GOVT",
                    "MF",
                    "FUND",
                    "NAV",
                    "ETF",
                    "INDEX",
                ]
            ):
                continue
            filtered_symbols.append(tradingsymbol)
        # Include futures and options from NFO.
        elif exchange == "NFO" and inst_type in ("FUT", "OPT"):
            filtered_symbols.append(tradingsymbol)
    return filtered_symbols


def download_symbol(
    broker: ZerodhaBroker,
    symbol: str,
    duck_con: "duckdb.DuckDBPyConnection" = None,
):
    """Download the complete historical series for *symbol*.

    Zerodha limits each request to ``CHUNK_SIZE`` candles. The function loops
    backwards in time until a request returns fewer than ``CHUNK_SIZE`` candles,
    which indicates that we have reached the earliest available data.
    """
    # Quick pre‑check: if the symbol does not return even a single candle, skip it.
    first_row = broker.get_historical_data(symbol, INTERVAL, 1)
    log.debug(
        f"pre-check for {symbol} (interval={INTERVAL}): got {len(first_row) if first_row else 0} candles"
    )
    if first_row:
        log.debug(f"  first candle: {first_row[0]}")
    if not first_row:
        log.info(f"No candles returned for {symbol} (pre‑check) – skipping")
        return

    all_rows: list[dict] = []
    while True:
        # ``count`` is the number of candles we ask for – the broker will honour
        # the maximum allowed by the API.
        rows = broker.get_historical_data(symbol, INTERVAL, CHUNK_SIZE)
        if not rows:
            break
        # Zerodha returns newest‑first; prepend to keep chronological order.
        all_rows = rows + all_rows
        log.debug(
            f"Fetched {len(rows)} candles for {symbol} (total so far {len(all_rows)})"
        )
        if len(rows) < CHUNK_SIZE:
            # Fewer than the maximum means we have reached the oldest data.
            break
        # Respect the rate‑limit before the next call.
        time.sleep(RATE_LIMIT_DELAY)
    if not all_rows:
        # Many symbols (e.g., newly listed equities, bonds, funds) do not have
        # historical candle data via Kite. Log at INFO level to avoid noisy
        # warnings while still providing visibility.
        log.info(f"No candles returned for {symbol}")
        return
    # Persist data – store directly in DuckDB and optionally Parquet.
    if duck_con is not None:
        try:
            # Use helper defined later.
            save_to_duckdb(duck_con, symbol, all_rows)
        except Exception as exc:
            log.error(f"DuckDB write failed for {symbol}: {exc}")
    # Write Parquet file for the symbol (optional, based on pandas availability).
    try:
        write_parquet(symbol, all_rows)
    except Exception as exc:
        log.error(f"Parquet export failed for {symbol}: {exc}")
    log.info(f"Completed {symbol}: {len(all_rows)} candles stored")


# ---------------------------------------------------------------------------
# Typer CLI – replaces the simple ``main`` function.
# ---------------------------------------------------------------------------
app = typer.Typer(
    add_completion=False,
    help="Download Zerodha historical candles to SQLite, DuckDB and Parquet.",
)


@app.command()
def run(
    interval: str = typer.Option(
        INTERVAL, "--interval", help="Zerodha interval (e.g., 5minute)"
    ),
    chunk_size: int = typer.Option(
        CHUNK_SIZE, "--chunk-size", help="Max candles per request"
    ),
    rate_limit: float = typer.Option(
        RATE_LIMIT_DELAY, "--rate-limit", help="Delay between broker calls (seconds)"
    ),
    output_dir: str = typer.Option(
        str(CSV_DIR),
        "--output-dir",
        help="Directory for CSV output (retained for compatibility)",
    ),
    db_path: str = typer.Option(str(DB_PATH), "--db-path", help="SQLite DB file path"),
    duckdb_root: str = typer.Option(
        str(DUCKDB_ROOT),
        "--duckdb-root",
        help="Root folder for DuckDB and Parquet files",
    ),
    skip_existing: bool = typer.Option(
        False, "--skip-existing", help="Skip symbols that already have a Parquet file"
    ),
):
    """Download all symbols and store them in SQLite, DuckDB and Parquet."""
    # Apply overrides to global configuration variables.
    global INTERVAL, CHUNK_SIZE, RATE_LIMIT_DELAY, CSV_DIR, DB_PATH, DUCKDB_ROOT, DUCKDB_PATH, PARQUET_DIR
    INTERVAL = interval
    CHUNK_SIZE = chunk_size
    RATE_LIMIT_DELAY = rate_limit
    CSV_DIR = Path(output_dir)
    DB_PATH = Path(db_path)
    DUCKDB_ROOT = Path(duckdb_root)
    DUCKDB_ROOT.mkdir(parents=True, exist_ok=True)
    DUCKDB_PATH = DUCKDB_ROOT / "historical_data.duckdb"
    PARQUET_DIR = DUCKDB_ROOT / "parquet"
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    broker = ZerodhaBroker(config=_load_broker_config())
    broker.connect()
    log.info(f"Broker connected. Using interval: {INTERVAL}")
    duck_con = init_duckdb()
    symbols = fetch_all_symbols(broker)
    total = len(symbols)
    log.info(f"Starting download for {total} symbols")

    # Use a progress bar to show download status. Prefer Rich if available; otherwise fall back to tqdm.
    try:
        # Rich progress bar
        progress = Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeRemainingColumn(),
        )
        completed = _load_progress()
        with progress:
            task = progress.add_task("Downloading", total=total)
            for sym in symbols:
                # Skip already processed symbols
                if sym in completed:
                    progress.update(task, advance=1)
                    continue
                # Respect pause flag
                _wait_if_paused()
                if skip_existing and (PARQUET_DIR / f"{sym}.parquet").exists():
                    log.debug(f"Skipping existing Parquet for {sym}")
                    progress.update(task, advance=1)
                    continue
                try:
                    download_symbol(broker, sym, duck_con=duck_con)
                    _append_progress(sym)
                except Exception as exc:
                    log.error(f"Error while processing {sym}: {exc}")
                progress.update(task, advance=1)
    except Exception:
        # Fallback to tqdm if Rich is not usable
        try:
            from tqdm import tqdm

            for sym in tqdm(symbols, desc="Downloading", unit="symbol"):
                if skip_existing and (PARQUET_DIR / f"{sym}.parquet").exists():
                    log.debug(f"Skipping existing Parquet for {sym}")
                    continue
                try:
                    download_symbol(broker, sym, duck_con=duck_con)
                except Exception as exc:
                    log.error(f"Error while processing {sym}: {exc}")
        except Exception:
            # Final fallback: simple logging every 50 symbols
            for idx, sym in enumerate(symbols, start=1):
                if skip_existing and (PARQUET_DIR / f"{sym}.parquet").exists():
                    log.debug(f"Skipping existing Parquet for {sym}")
                    continue
                try:
                    download_symbol(broker, sym, duck_con=duck_con)
                except Exception as exc:
                    log.error(f"Error while processing {sym}: {exc}")
                if idx % 50 == 0:
                    log.info(f"Progress: {idx}/{total} symbols processed")

    if duck_con is not None:
        duck_con.close()
    log.info(f"All symbols processed – DuckDB DB ready at {DUCKDB_PATH}")


if __name__ == "__main__":
    # When executed directly, invoke the Typer CLI.
    app()
