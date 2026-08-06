#!/usr/bin/env python3
"""Download Zerodha instruments CSV and store it in PostgreSQL.

This script is intentionally small:
- fetch the public Zerodha instruments CSV
- connect directly to PostgreSQL via DATABASE_URL
- replace the cached instrument rows

It does not initialize the trading bot, scheduler, or other application layers.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv
from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from utils.dataset_manifests import DatasetManifest, load_manifest, manifest_path_for, summarize_rows, write_manifest


INSTRUMENTS_URL = "https://api.kite.trade/instruments"


def load_environment() -> None:
    """Load variables from .env if present."""
    load_dotenv()


def resolve_database_url(cli_value: str | None) -> tuple[str | None, str]:
    """Return the database URL and the source it came from."""
    if cli_value:
        return cli_value, "command line"

    dotenv_path = Path(".env")
    if dotenv_path.exists():
        env_file = dotenv_values(dotenv_path)
        env_file_value = env_file.get("DATABASE_URL") or env_file.get("POSTGRES_URL")
        if env_file_value:
            return env_file_value, ".env file"

    env_value = os.getenv("DATABASE_URL")
    if env_value:
        return env_value, "environment"

    postgresql_url = os.getenv("POSTGRES_URL")
    if postgresql_url:
        return postgresql_url, "environment"

    return None, "missing"


def build_engine(database_url: str):
    return create_engine(database_url, future=True)


def ensure_table(engine):
    metadata = MetaData()
    instruments = Table(
        "instrument_cache",
        metadata,
        Column("instrument_token", String(32), primary_key=True),
        Column("exchange", String(16)),
        Column("tradingsymbol", String(64)),
        Column("name", String(128)),
        Column("expiry", String(32)),
        Column("strike", Float),
        Column("tick_size", Float),
        Column("lot_size", Integer),
        Column("instrument_type", String(32)),
        Column("segment", String(32)),
    )
    metadata.create_all(engine, tables=[instruments])
    return instruments


def fetch_instruments_csv(url: str) -> list[dict[str, str]]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    reader = csv.DictReader(response.text.splitlines())
    rows: list[dict[str, str]] = []
    for row in reader:
        if row.get("instrument_token") and row.get("tradingsymbol"):
            rows.append(row)
    return rows


def normalize_row(row: dict[str, str]) -> dict[str, object | None]:
    strike = row.get("strike")
    tick_size = row.get("tick_size")
    lot_size = row.get("lot_size")
    return {
        "instrument_token": str(row.get("instrument_token", "")),
        "exchange": row.get("exchange"),
        "tradingsymbol": row.get("tradingsymbol"),
        "name": row.get("name"),
        "expiry": row.get("expiry"),
        "strike": float(strike) if strike else None,
        "tick_size": float(tick_size) if tick_size else None,
        "lot_size": int(lot_size) if lot_size else None,
        "instrument_type": row.get("instrument_type"),
        "segment": row.get("segment"),
    }


def store_instruments(engine, table, rows: list[dict[str, str]]) -> int:
    records = [normalize_row(row) for row in rows]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM instrument_cache"))
        if records:
            conn.execute(table.insert(), records)
    return len(records)


def main() -> int:
    load_environment()

    parser = argparse.ArgumentParser(
        description="Fetch Zerodha instruments CSV and store it in PostgreSQL"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"),
        help="PostgreSQL SQLAlchemy URL, for example postgresql+psycopg2://user:pass@host:5432/db",
    )
    parser.add_argument(
        "--url",
        default=INSTRUMENTS_URL,
        help="Instrument CSV URL",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional file path to write the raw CSV rows as JSON-like text for debugging",
    )
    args = parser.parse_args()

    database_url, source = resolve_database_url(args.database_url)

    if not database_url:
        raise SystemExit("DATABASE_URL or POSTGRES_URL must be set")

    print(f"Using database URL from {source}")

    engine = build_engine(database_url)
    table = ensure_table(engine)
    rows = fetch_instruments_csv(args.url)
    count = store_instruments(engine, table, rows)
    manifest = DatasetManifest(
        dataset="instrument_cache",
        source=args.url,
        status="completed",
        summary=summarize_rows(
            [
                {
                    "symbol": row.get("tradingsymbol"),
                    "ts": row.get("expiry"),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": None,
                    "volume": None,
                }
                for row in rows
            ]
        ),
    )
    write_manifest(manifest_path_for(Path(database_url.split("///")[-1]).with_suffix(".manifest")), manifest)

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(rows, indent=2, default=str), encoding="utf-8"
        )

    print(f"Stored {count} instrument rows in PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
