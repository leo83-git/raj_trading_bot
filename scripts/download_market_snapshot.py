#!/usr/bin/env python3
"""Fetch full market data via Zerodha WebSocket and store it in PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, inspect, text

try:
    from kiteconnect import KiteConnect
except Exception:  # pragma: no cover - optional dependency in tests
    KiteConnect = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.database import DatabaseManager
from core.zerodha_websocket import ZerodhaWebSocket
from core.token_manager import ZerodhaTokenManager
from utils.dataset_manifests import DatasetManifest, load_manifest, manifest_path_for, summarize_rows, write_manifest

MAX_TOKENS_PER_CONNECTION = 2500
MAX_SIMULTANEOUS_CONNECTIONS = 3
MAX_REST_QUOTE_BATCH_SIZE = 100
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "INDIAVIX"]


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


def load_zerodha_credentials_from_config() -> tuple[str | None, str | None]:
    api_key = (
        os.getenv("ZERODHA_API_KEY")
        or os.getenv("KITE_API_KEY")
        or os.getenv("API_KEY")
    )
    access_token = (
        os.getenv("ZERODHA_ACCESS_TOKEN")
        or os.getenv("KITE_ACCESS_TOKEN")
        or os.getenv("ACCESS_TOKEN")
    )

    if api_key and access_token:
        return api_key, access_token

    try:
        from core.config import ZERODHA_API_KEY as config_api_key
        from core.config import ZERODHA_ACCESS_TOKEN as config_access_token

        if config_api_key and not api_key:
            api_key = config_api_key
        if config_access_token and not access_token:
            access_token = config_access_token
    except Exception:
        pass

    if not access_token:
        try:
            token_manager = ZerodhaTokenManager(api_key or "", "")
            if token_manager.load_token():
                access_token = token_manager.access_token
        except Exception:
            pass

    return api_key, access_token


def resolve_credentials() -> tuple[str | None, str | None]:
    api_key, access_token = load_zerodha_credentials_from_config()
    if api_key and access_token:
        return api_key, access_token

    return None, None


def ensure_table(engine):
    metadata = MetaData()
    snapshot = Table(
        "market_snapshot",
        metadata,
        Column("instrument_token", Integer, primary_key=True),
        Column("symbol", String(64)),
        Column("exchange", String(16)),
        Column("last_price", Float),
        Column("bids_json", Text),
        Column("asks_json", Text),
        Column("timestamp", DateTime),
    )
    metadata.create_all(engine, tables=[snapshot])

    def _refresh_columns() -> set[str]:
        return {
            column["name"]
            for column in inspect(engine).get_columns("market_snapshot")
        }

    existing_columns = _refresh_columns()
    dialect = engine.dialect.name
    if dialect == "sqlite" and (
        "quote_json" in existing_columns or "depth_json" in existing_columns
    ):
        with engine.begin() as conn:
            legacy_rows = list(
                conn.execute(
                    text(
                        """
                        SELECT instrument_token, symbol, exchange, last_price, quote_json, depth_json, timestamp
                        FROM market_snapshot
                        """
                    )
                )
            )
            conn.execute(text("DROP TABLE market_snapshot"))
            conn.execute(
                text(
                    """
                    CREATE TABLE market_snapshot (
                        instrument_token INTEGER PRIMARY KEY,
                        symbol VARCHAR(64),
                        exchange VARCHAR(16),
                        last_price FLOAT,
                        bids_json TEXT,
                        asks_json TEXT,
                        timestamp DATETIME
                    )
                    """
                )
            )
            for row in legacy_rows:
                depth_payload = {}
                if row[5]:
                    try:
                        depth_payload = json.loads(row[5])
                    except Exception:
                        depth_payload = {}
                conn.execute(
                    text(
                        """
                        INSERT INTO market_snapshot (
                            instrument_token, symbol, exchange, last_price, bids_json, asks_json, timestamp
                        ) VALUES (
                            :instrument_token, :symbol, :exchange, :last_price, :bids_json, :asks_json, :timestamp
                        )
                        """
                        ),
                        {
                            "instrument_token": row[0],
                            "symbol": row[1],
                            "exchange": row[2],
                            "last_price": float(row[3]) if row[3] is not None else None,
                            "bids_json": json.dumps(depth_payload.get("buy") or [], default=str),
                            "asks_json": json.dumps(depth_payload.get("sell") or [], default=str),
                            "timestamp": row[6],
                        },
                    )
        existing_columns = _refresh_columns()
    if "bids_json" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE market_snapshot ADD COLUMN bids_json TEXT"))
        existing_columns = _refresh_columns()
    if "asks_json" not in existing_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE market_snapshot ADD COLUMN asks_json TEXT"))
        existing_columns = _refresh_columns()
    return snapshot


def load_instruments() -> list[dict]:
    load_environment()

    api_key, access_token = resolve_credentials()
    if api_key and access_token and KiteConnect is not None:
        try:
            client = KiteConnect(api_key=api_key)
            client.set_access_token(access_token)
            instruments: list[dict] = []
            for exchange in ("NSE", "NFO"):
                try:
                    instruments.extend(client.instruments(exchange))
                except Exception:
                    continue
            filtered = [
                row
                for row in instruments
                if row.get("instrument_token")
                and row.get("tradingsymbol")
                and str(row.get("exchange", "")).upper() in {"NSE", "NFO"}
                and (
                    str(row.get("segment") or "").upper() in {"EQ", "INDICES"}
                    or str(row.get("instrument_type") or "").upper() in {"FUT", "OPT"}
                    or str(row.get("segment") or "").upper().startswith("NFO")
                )
            ]
            if filtered:
                return filtered
        except Exception:
            pass

    try:
        manager = DatabaseManager()
        instruments = manager.get_instrument_cache()
        filtered = [
            row
            for row in instruments
            if row.get("instrument_token")
            and row.get("tradingsymbol")
            and str(row.get("exchange", "")).upper() in {"NSE", "BSE"}
            and str(row.get("segment") or "").upper() in {"EQ", "NFO", "INDICES"}
        ]
        if filtered:
            return filtered
    except Exception:
        pass

    instruments_file = ROOT_DIR / "data" / "zerodha_instruments.json"
    if not instruments_file.exists():
        return []

    try:
        with instruments_file.open() as handle:
            data = json.load(handle)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    selected: list[dict] = []
    seen_tokens: set[int] = set()
    for row in data:
        if not row.get("instrument_token") or not row.get("tradingsymbol"):
            continue
        exchange = str(row.get("exchange", "")).upper()
        segment = str(row.get("segment") or "").upper()
        if exchange not in {"NSE", "NFO"}:
            continue
        if segment not in {"EQ", "NFO", "NFO-FUT", "NFO-OPT", "INDICES"}:
            continue
        token = int(row["instrument_token"])
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        selected.append(row)

    return selected


def chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def resolve_batch_size(requested_size: int) -> tuple[int, bool]:
    effective_size = max(1, requested_size)
    if effective_size > MAX_TOKENS_PER_CONNECTION:
        print(
            f"Warning: requested batch size {effective_size} exceeds the recommended maximum of {MAX_TOKENS_PER_CONNECTION}; using {MAX_TOKENS_PER_CONNECTION}."
        )
        return MAX_TOKENS_PER_CONNECTION, True
    return effective_size, False


def render_progress_bar(current: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[--] 0/0"
    filled = max(1, int(round(width * current / total))) if current else 0
    bar = "#" * filled + "-" * (width - filled)
    percent = int(round(100 * current / total)) if total else 100
    return f"[{bar}] {current}/{total} ({percent}%)"


def wait_for_connection(ws: ZerodhaWebSocket, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        socket_ready = getattr(ws, "ws", None) is not None
        connection_ready = getattr(ws, "is_connected", False)
        if socket_ready and connection_ready:
            return True
        time.sleep(0.1)
    return False


def build_quote_key(meta: dict) -> str | None:
    symbol = (meta.get("tradingsymbol") or "").strip()
    exchange = (meta.get("exchange") or "").strip().upper()
    if not symbol:
        return None
    if exchange in {"NSE", "BSE"}:
        return f"{exchange}:{symbol}"
    return symbol


def extract_quote_payload(quote_payload: dict, token: int, meta: dict) -> dict | None:
    if not isinstance(quote_payload, dict):
        return None

    for candidate_key in [str(token), build_quote_key(meta)]:
        if not candidate_key:
            continue
        entry = quote_payload.get(candidate_key)
        if isinstance(entry, dict):
            return entry

    for candidate_key in [str(token), build_quote_key(meta)]:
        if not candidate_key:
            continue
        if candidate_key in quote_payload:
            return quote_payload[candidate_key]

    return None


def fetch_rest_rows(token_meta: dict[int, dict], api_key: str, access_token: str) -> list[dict]:
    if KiteConnect is None:
        return []

    client = KiteConnect(api_key=api_key)
    client.set_access_token(access_token)

    quote_keys = []
    for token in token_meta:
        quote_key = build_quote_key(token_meta[token])
        if quote_key:
            quote_keys.append(quote_key)

    if not quote_keys:
        return []

    rows: list[dict] = []
    for start in range(0, len(token_meta), MAX_REST_QUOTE_BATCH_SIZE):
        batch_tokens = list(token_meta.keys())[start : start + MAX_REST_QUOTE_BATCH_SIZE]
        try:
            quote_payload = client.quote(*[str(token) for token in batch_tokens])
        except Exception:
            continue

        if not isinstance(quote_payload, dict):
            continue
        for token in batch_tokens:
            meta = token_meta[token]
            entry = extract_quote_payload(quote_payload, token, meta)
            if not isinstance(entry, dict):
                continue
            if not entry.get("last_price") and not entry.get("close"):
                continue
            rows.append(
                    {
                        "instrument_token": token,
                        "symbol": meta.get("tradingsymbol"),
                        "exchange": meta.get("exchange"),
                        "last_price": entry.get("last_price") or entry.get("close"),
                        "bids_json": json.dumps((entry.get("depth") or {}).get("buy") or [], default=str),
                        "asks_json": json.dumps((entry.get("depth") or {}).get("sell") or [], default=str),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )
    return rows


def collect_chunk(
    chunk: list[dict],
    api_key: str,
    access_token: str,
    wait_seconds: float,
    prefer_rest: bool = False,
) -> list[dict]:
    tokens = []
    token_meta: dict[int, dict] = {}
    for row in chunk:
        token = int(row["instrument_token"])
        tokens.append(token)
        token_meta[token] = row

    ws = ZerodhaWebSocket(api_key, access_token)
    try:
        rows: list[dict] = []
        if not prefer_rest:
            ws.connect()
            if wait_for_connection(ws, timeout_seconds=max(5.0, wait_seconds)):
                ws.set_mode("full", tokens)
                ws.subscribe(tokens)
                time.sleep(wait_seconds)
            else:
                print("WebSocket did not become ready for this batch; using REST fallback")

            for token in tokens:
                entry = ws.price_cache.get(token) or ws.price_cache.get(str(token))
                if not isinstance(entry, dict):
                    continue
                meta = token_meta[token]
                depth = entry.get("depth") or {}
                rows.append(
                    {
                        "instrument_token": token,
                        "symbol": meta.get("tradingsymbol"),
                        "exchange": meta.get("exchange"),
                        "last_price": entry.get("close"),
                        "bids_json": json.dumps(depth.get("buy") or [], default=str),
                        "asks_json": json.dumps(depth.get("sell") or [], default=str),
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                )

        if not rows or prefer_rest:
            fallback_rows = fetch_rest_rows(token_meta, api_key, access_token)
            if fallback_rows:
                if prefer_rest:
                    print(f"Using REST fallback for {len(fallback_rows)} instruments in this batch")
                else:
                    print(f"WebSocket cache was empty; used REST quote fallback for {len(fallback_rows)} instruments")
            return fallback_rows

        return rows
    finally:
        try:
            ws.disconnect()
        except Exception:
            pass


def store_snapshot(engine, table, rows: list[dict]) -> int:
    if not rows:
        return 0

    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns("market_snapshot")}

    if {"bids_json", "asks_json"}.issubset(existing_columns):
        upsert_sql = text(
            """
            INSERT INTO market_snapshot (
                instrument_token, symbol, exchange, last_price, bids_json, asks_json, timestamp
            ) VALUES (
                :instrument_token, :symbol, :exchange, :last_price, :bids_json, :asks_json, :timestamp
            )
            ON CONFLICT (instrument_token) DO UPDATE SET
                symbol = EXCLUDED.symbol,
                exchange = EXCLUDED.exchange,
                last_price = EXCLUDED.last_price,
                bids_json = EXCLUDED.bids_json,
                asks_json = EXCLUDED.asks_json,
                timestamp = EXCLUDED.timestamp
            """
        )
        with engine.begin() as conn:
            for row in rows:
                payload = dict(row)
                payload.setdefault("bids_json", json.dumps([], default=str))
                payload.setdefault("asks_json", json.dumps([], default=str))
                conn.execute(upsert_sql, payload)
        return len(rows)

    raise RuntimeError(
        "market_snapshot table does not contain a supported schema for snapshot storage"
    )


def main() -> int:
    load_environment()

    parser = argparse.ArgumentParser(
        description="Fetch Zerodha market snapshot via WebSocket and store it in PostgreSQL"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"),
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols to snapshot; blank uses the NSE EQ/NFO and index universe",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_TOKENS_PER_CONNECTION,
        help="Tokens per websocket connection (max 2500 recommended)",
    )
    parser.add_argument(
        "--connections",
        type=int,
        default=MAX_SIMULTANEOUS_CONNECTIONS,
        help="Maximum simultaneous websocket connections",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=12.0,
        help="Seconds to wait after subscribing before harvesting cache",
    )
    args = parser.parse_args()

    database_url, source = resolve_database_url(args.database_url)
    if not database_url:
        raise SystemExit("DATABASE_URL or POSTGRES_URL must be set")

    print(f"Using database URL from {source}")

    engine = build_engine(database_url)
    table = ensure_table(engine)

    all_instruments = load_instruments()
    if args.symbols.strip():
        wanted = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}
        instruments = [
            row for row in all_instruments if str(row.get("tradingsymbol", "")).upper() in wanted
        ]
    else:
        instruments = all_instruments

    if not instruments:
        raise SystemExit("No instruments found in PostgreSQL instrument cache")

    api_key, access_token = resolve_credentials()
    if not api_key or not access_token:
        raise SystemExit(
            "Unable to resolve Zerodha credentials; set ZERODHA_API_KEY/ZERODHA_ACCESS_TOKEN or KITE_API_KEY/KITE_ACCESS_TOKEN"
        )

    effective_batch_size, warned = resolve_batch_size(args.batch_size)
    if warned:
        print(f"Using batch size {effective_batch_size} for websocket subscriptions")

    batches = chunked(instruments, effective_batch_size)
    total_batches = len(batches)
    total_symbols = len(instruments)
    total_rows = 0

    print(
        f"Preparing {total_batches} websocket batches for {total_symbols} symbols; each batch contains up to {effective_batch_size} symbols"
    )

    with ThreadPoolExecutor(max_workers=max(1, min(args.connections, MAX_SIMULTANEOUS_CONNECTIONS))) as executor:
        future_to_index = {
            executor.submit(collect_chunk, batch, api_key, access_token, args.wait_seconds, idx > 0): idx + 1
            for idx, batch in enumerate(batches)
        }
        completed_batches = 0
        for future in as_completed(future_to_index):
            batch_index = future_to_index[future]
            rows = future.result()
            total_rows += store_snapshot(engine, table, rows)
            completed_batches += 1
            print(
                f"[{batch_index}/{total_batches}] stored {len(rows)} snapshot rows from {len(batches[batch_index - 1])} symbols | {render_progress_bar(completed_batches, total_batches)}"
            )

    print(f"Stored {total_rows} market snapshot rows in PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
