#!/usr/bin/env python3
"""Download historical candles from Zerodha and store them directly in PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sources.broker import ZerodhaBroker

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - fallback if tqdm is unavailable
    tqdm = None

DEFAULT_INTERVALS = [
    interval.strip()
    for interval in os.getenv("INTERVALS", os.getenv("INTERVAL", "day,5minute")).split(",")
    if interval.strip()
]
DEFAULT_CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "2000"))
DEFAULT_RATE_LIMIT = float(os.getenv("RATE_LIMIT_DELAY", "0.35"))


def load_environment() -> None:
    load_dotenv()


def resolve_database_url(cli_value: str | None) -> tuple[str | None, str]:
    if cli_value:
        return cli_value, "command line"
    env_file = Path(".env")
    if env_file.exists():
        from dotenv import dotenv_values

        data = dotenv_values(env_file)
        value = data.get("DATABASE_URL") or data.get("POSTGRES_URL")
        if value:
            return value, ".env file"
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL"), "environment"
    if os.getenv("POSTGRES_URL"):
        return os.getenv("POSTGRES_URL"), "environment"
    return None, "missing"


def build_engine(database_url: str):
    return create_engine(database_url, future=True)


def ensure_table(engine):
    metadata = MetaData()
    candles = Table(
        "candles",
        metadata,
        Column("symbol", String(64), primary_key=True),
        Column("ts", DateTime, primary_key=True),
        Column("interval", String(16), primary_key=True),
        Column("open", Float),
        Column("high", Float),
        Column("low", Float),
        Column("close", Float),
        Column("volume", Integer),
    )
    metadata.create_all(engine, tables=[candles])
    return candles


def fetch_symbols(broker: ZerodhaBroker) -> list[str]:
    if hasattr(broker, "_load_instruments"):
        instruments = broker._load_instruments()
    else:
        instruments = getattr(broker, "instruments_cache", {}).values()

    symbols: list[str] = []
    seen: set[str] = set()
    for inst in instruments:
        if not isinstance(inst, dict):
            continue
        exchange = inst.get("exchange")
        inst_type = inst.get("instrument_type")
        tradingsymbol = inst.get("tradingsymbol")
        if not tradingsymbol or not isinstance(tradingsymbol, str):
            continue
        if exchange == "NSE" and inst_type == "EQ":
            name = str(inst.get("name", "")).upper()
            if any(
                token in name
                for token in (
                    "BOND",
                    "SDL",
                    "TBILL",
                    "ETF",
                    "FUND",
                    "NAV",
                    "INDEX",
                )
            ):
                continue
            if tradingsymbol not in seen:
                seen.add(tradingsymbol)
                symbols.append(tradingsymbol)
        elif exchange == "NFO" and inst_type in ("FUT", "OPT"):
            if tradingsymbol not in seen:
                seen.add(tradingsymbol)
                symbols.append(tradingsymbol)
    return symbols


def normalize_rows(symbol: str, interval: str, rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for row in rows:
        ts = row.get("timestamp", row.get("date"))
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                pass
        normalized.append(
            {
                "symbol": symbol,
                "ts": ts,
                "interval": interval,
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
            }
        )
    return normalized


def store_candles(engine, table, rows: list[dict]) -> int:
    if not rows:
        return 0
    with engine.begin() as conn:
        symbol = rows[0]["symbol"]
        interval = rows[0]["interval"]
        conn.execute(
            text("DELETE FROM candles WHERE symbol = :symbol AND interval = :interval"),
            {"symbol": symbol, "interval": interval},
        )
        conn.execute(table.insert(), rows)
    return len(rows)


def load_existing_jobs(engine) -> set[tuple[str, str]]:
    """Return symbol/interval pairs already present in PostgreSQL."""
    existing: set[tuple[str, str]] = set()
    with engine.begin() as conn:
        result = conn.execute(
            text("SELECT DISTINCT symbol, interval FROM candles")
        )
        for symbol, interval in result:
            if symbol and interval:
                existing.add((str(symbol), str(interval)))
    return existing


def download_symbol(broker: ZerodhaBroker, symbol: str, interval: str, chunk_size: int, rate_limit: float) -> list[dict]:
    first = broker.get_historical_data(symbol, interval, 1)
    if not first:
        print(f"  WARNING: No historical data for {symbol} @ {interval}")
        return []
    all_rows: list[dict] = []
    while True:
        rows = broker.get_historical_data(symbol, interval, chunk_size)
        if not rows:
            break
        all_rows = rows + all_rows
        if len(rows) < chunk_size:
            break
        time.sleep(rate_limit)
    return all_rows


def main() -> int:
    load_environment()

    parser = argparse.ArgumentParser(
        description="Download Zerodha historical candles and store them in PostgreSQL"
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"))
    parser.add_argument(
        "--intervals",
        default=",".join(DEFAULT_INTERVALS),
        help="Comma-separated Zerodha intervals, e.g. day,5minute,15minute",
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT)
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()

    database_url, source = resolve_database_url(args.database_url)
    if not database_url:
        raise SystemExit("DATABASE_URL or POSTGRES_URL must be set")

    print(f"Using database URL from {source}")

    engine = build_engine(database_url)
    table = ensure_table(engine)

    # Load config to get broker credentials
    try:
        import yaml
        config_path = ROOT_DIR / "config" / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    broker_config = config.get("broker", {})
    zerodha_config = config.get("zerodha", {})

    # Try environment variables first, then config file
    api_key = (
        os.getenv("ZERODHA_API_KEY")
        or zerodha_config.get("api_key")
        or config.get("zerodha_api_key", "")
    )
    api_secret = (
        os.getenv("ZERODHA_API_SECRET")
        or zerodha_config.get("api_secret")
        or config.get("zerodha_api_secret", "")
    )
    access_token = (
        os.getenv("ZERODHA_ACCESS_TOKEN")
        or zerodha_config.get("access_token")
        or config.get("zerodha_access_token", "")
    )

    print(f"API Key present: {bool(api_key)}")
    print(f"API Secret present: {bool(api_secret)}")
    print(f"Access Token present: {bool(access_token)}")

    # Create config for ZerodhaBroker
    broker_config_dict = {
        "api_key": api_key,
        "api_secret": api_secret,
        "access_token": access_token,
    }
    broker = ZerodhaBroker(config)
    connected = broker.connect()
    
    if not connected:
        raise SystemExit("Failed to connect to Zerodha broker")
    
    print(f"Broker connected: {broker.connected}")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or fetch_symbols(broker)
    intervals = [i.strip() for i in args.intervals.split(",") if i.strip()]
    existing_jobs = load_existing_jobs(engine)

    jobs = [(symbol, interval) for interval in intervals for symbol in symbols]
    total_jobs = len(jobs)
    pending_jobs = [job for job in jobs if job not in existing_jobs]
    skipped_jobs = total_jobs - len(pending_jobs)
    total_rows = 0

    if skipped_jobs:
        print(f"Skipping {skipped_jobs} already-stored symbol/interval jobs from PostgreSQL")

    progress = tqdm(pending_jobs, desc="Downloading candles", unit="job") if tqdm else pending_jobs

    for job_index, (symbol, interval) in enumerate(progress, start=1):
        if tqdm:
            progress.set_postfix_str(f"{symbol} @ {interval}")
        rows = download_symbol(
            broker, symbol, interval, args.chunk_size, args.rate_limit
        )
        normalized = normalize_rows(symbol, interval, rows)
        total_rows += store_candles(engine, table, normalized)
        print(
            f"[{job_index}/{len(pending_jobs)}] {symbol} @ {interval}: {len(normalized)} rows"
        )
        if job_index % 25 == 0 or job_index == len(pending_jobs):
            print(
                f"Progress: {job_index}/{len(pending_jobs)} jobs complete, "
                f"{skipped_jobs} skipped, {total_rows} rows stored"
            )

    print(f"Stored {total_rows} candle rows in PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
