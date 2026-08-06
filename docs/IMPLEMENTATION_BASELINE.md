# P0 Implementation Baseline

This document captures the authoritative repository state before P0 changes.
It is intentionally read-only and documents exact files, symbols, data assets,
and current baseline test results.

## Confirmed runtime symbols

- `main.RajTradingBot` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L654).
- `RajTradingBot.run` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L861).
- `RajTradingBot._run_trading_cycle` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L5020).
- `RajTradingBot._manage_positions` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L9628).
- `RajTradingBot._process_fno_pipeline_async` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L8034).
- `RajTradingBot._execute_multi_leg_strategy` is defined in [`main.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/main.py#L2529) and also as a nested compatibility helper inside `_run_trading_cycle`.
- `core.order_manager.OrderManager.place_order` is defined in [`core/order_manager.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/core/order_manager.py#L36).
- `execution.execution_engine.place_order` is defined in [`execution/execution_engine.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/execution/execution_engine.py#L26).
- `core.risk_manager.RiskManager` is defined in [`core/risk_manager.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/core/risk_manager.py#L5).
- `risk.risk_manager.RiskManager` is defined in [`risk/risk_manager.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/risk/risk_manager.py#L22).
- `screener.fno_contract_loader.FnoContractLoader` is defined in [`screener/fno_contract_loader.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/screener/fno_contract_loader.py#L20).
- `screener.dynamic_symbol_filter.DynamicSymbolFilter.get_fno_symbols` is defined in [`screener/dynamic_symbol_filter.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/screener/dynamic_symbol_filter.py#L126).

## Current call-site map

- `main.RajTradingBot` instantiates `core.order_manager.OrderManager`, `core.risk_manager.RiskManager`, and `PositionTracker`.
- `main.download_market_snapshots(system)` reads `core.database.DatabaseManager.get_instrument_cache()` and writes market snapshot rows through `DatabaseManager.replace_market_snapshot`.
- `core.zerodha_broker.ZerodhaBroker` reads and writes instrument cache rows through `DatabaseManager.get_instrument_cache()` and `DatabaseManager.replace_instrument_cache()`.
- `core.order_manager.OrderManager` persists fills through `DatabaseManager.save_trade()`.
- `screener.fno_contract_loader.FnoContractLoader` reads and writes the F&O cache through `DatabaseManager.load_fno_contract_cache()` and `DatabaseManager.replace_fno_contract_cache()`.
- `screener.dynamic_symbol_filter.DynamicSymbolFilter` uses `FnoContractLoader.get_fno_symbols()` and `screener.get_screening()`.
- `scripts/download_instruments.py` writes the `instrument_cache` table.
- `scripts/download_market_snapshot.py` reads instrument cache and writes `market_snapshot`.
- `scripts/download_all_historical_to_sqlite.py` and `scripts/download_historical_daily.py` manage DuckDB and Parquet historical candle stores.

## Downloader inventory

Current downloader or downloader-adjacent scripts:

- [`scripts/download_instruments.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_instruments.py)
- [`scripts/download_market_snapshot.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_market_snapshot.py)
- [`scripts/download_all_historical_to_sqlite.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_all_historical_to_sqlite.py)
- [`scripts/download_all_historical_enhanced.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_all_historical_enhanced.py)
- [`scripts/download_historical_daily.py`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_historical_daily.py)
- [`scripts/download_timeframes.sh`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/download_timeframes.sh)
- [`scripts/run_download_historical_daily.sh`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/run_download_historical_daily.sh)

## Local database and Parquet assets

### DuckDB / Parquet historical store

- DuckDB file: [`data/duckdb/historical_data.duckdb`](/home/rajasekhar/vibe-coding/raj_trading_bot/data/duckdb/historical_data.duckdb)
- Parquet directory: [`data/duckdb/parquet/`](/home/rajasekhar/vibe-coding/raj_trading_bot/data/duckdb/parquet/)
- Consumer modules:
  - `scripts/download_all_historical_to_sqlite.py`
  - `scripts/download_historical_daily.py`
  - `scripts/download_timeframes.sh`

Observed DuckDB baseline:

- Table: `candles`
- Schema: `symbol VARCHAR PRIMARY KEY`, `ts TIMESTAMP PRIMARY KEY`, `open FLOAT`, `high FLOAT`, `low FLOAT`, `close FLOAT`, `volume BIGINT`
- Row count: `5824`
- Date range: `2026-06-24 00:00:00` to `2026-07-29 00:00:00`
- Duplicate key count on `(symbol, ts)`: `0`

Observed Parquet baseline:

- File count: `539`
- Representative file schema: `timestamp` with `timestamp[ns, tz=+05:30]`, `open`, `high`, `low`, `close`, `volume`, `symbol`
- Representative row counts:
  - `360ONE26OCTFUT.parquet`: `1`
  - `360ONE26SEPFUT.parquet`: `17`
  - `AASTHA.parquet`: `14`
  - `WIPRO26SEPFUT.parquet`: `17`
  - `ZYDUSLIFE26SEPFUT.parquet`: `17`

### SQLite caches and operational stores

Status: all local SQLite database files that were previously present in the workspace have been deleted as part of the Postgres migration verification. The file-by-file inventory below is preserved only as historical baseline evidence.

#### `data/fno_contracts_cache.db`

- Tables: `cache_metadata`, `fno_contracts`
- Consumer modules:
  - `screener/fno_contract_loader.py`
  - `core/database.py`
- `fno_contracts` schema:
  - `symbol TEXT PRIMARY KEY`
  - `instrument_token TEXT`
  - `exchange TEXT`
  - `segment TEXT`
  - `expiry TEXT`
  - `strike REAL`
  - `option_type TEXT`
  - `last_updated TEXT`
- Row count: `992`
- Duplicate `symbol` count: `0`
- Null `symbol` count: `0`
- `instrument_token` duplicate count: `0`
- `instrument_token` null count: `0`

#### `data/quant_trading.db`

- Tables: `instrument`, `market_depth`, `market_snapshot`
- Consumer modules:
  - `main.py` market snapshot helper
  - `scripts/download_market_snapshot.py`
  - `screener/dynamic_screening_clean.py`
- `instrument` row count: `124244`
- `instrument.tradingsymbol` duplicate count: `9532`
- `market_depth` row count: `0`
- `market_snapshot` row count: `0`

#### `data/simple_market_snapshot.db`

- Tables: `market_snapshot`
- Consumer modules:
  - `core.database.DatabaseManager` fallback snapshot storage
  - `scripts/download_market_snapshot.py`
- `market_snapshot` schema:
  - `instrument_token INTEGER PRIMARY KEY`
  - `symbol TEXT`
  - `exchange TEXT`
  - `last_price REAL`
  - `bids_json TEXT`
  - `asks_json TEXT`
  - `timestamp DATETIME`
- Row count: `108396`
- Timestamp range: `2026-07-31 10:12:04` to `2026-07-31 10:12:06`
- `symbol` null count: `108396`
- `symbol` duplicate count: `108395`
- `instrument_token` duplicate count: `0`

#### `data/trading_bot.db`

- Tables: `cache_metadata`, `fno_contracts`, `instrument_cache`, `log_events`, `market_snapshot`, `positions`, `trades`
- Consumer modules:
  - `core/database.py`
  - `core/order_manager.py`
  - `core/zerodha_broker.py`
  - `screener/fno_contract_loader.py`
- `trades` row count: `5`
- `trades.timestamp` range: `2026-08-03 21:08:33.214687` to `2026-08-03 21:30:37.205462`
- `trades.symbol` duplicate count: `3`
- `log_events` row count: `5`
- `log_events.timestamp` range: `2026-08-03 21:08:33.233915` to `2026-08-03 21:30:37.222148`
- `positions` row count: `0`
- `instrument_cache`, `market_snapshot`, and `fno_contracts` are currently empty

#### `data/zerodha_instruments.db`

- Tables: `zerodha_instruments`
- Consumer modules:
  - `screener/dynamic_screening_clean.py`
  - `scripts/download_instruments.py` via upstream export paths
- Row count: `132288`
- `tradingsymbol` duplicate count: `9369`
- `instrument_token` duplicate count: `0`

#### `db/health.db`

- Tables: `health_alerts`, `health_metrics`
- Consumer modules:
  - `quant_utils.health`-style runtime monitoring paths
- `health_alerts` row count: `0`
- `health_metrics` row count: `44`
- `health_metrics.timestamp` range: `2026-06-10 16:10:14` to `2026-06-15 07:56:11`

#### `db/latency.db`

- Tables: `order_latency`
- Consumer modules:
  - execution and observability paths that log RTT metrics
- Row count: `2`
- `timestamp` range: `2026-06-10 16:10:18` to `2026-06-10 16:10:56`
- `symbol` null count: `2`

#### `db/logs.db`

- Tables: `error_404_tracker`, `invalid_api_key_tracker`, `ip_bans`, `traffic_logs`
- Consumer modules:
  - HTTP / API abuse-tracking and logging utilities
- `invalid_api_key_tracker` row count: `1`
- `traffic_logs` row count: `2`
- `traffic_logs.timestamp` range: `2026-06-10 16:10:18` to `2026-06-10 16:10:56`

#### `db/sandbox.db`

- Tables: `sandbox_config`, `sandbox_daily_pnl`, `sandbox_funds`, `sandbox_gtt`, `sandbox_gtt_legs`, `sandbox_holdings`, `sandbox_orders`, `sandbox_positions`, `sandbox_trades`
- Consumer modules:
  - sandbox and paper-trading infrastructure
- Representative row counts:
  - `sandbox_config`: `20`
  - `sandbox_funds`: `2`
  - `sandbox_holdings`: `1`
  - `sandbox_positions`: `1`
- Representative timestamp ranges:
  - `sandbox_config.updated_at`: `2026-06-05 05:43:31` to `2026-06-05 05:43:31`
  - `sandbox_funds.created_at`: `2026-06-05 05:49:06` to `2026-06-16 03:47:16`
  - `sandbox_positions.created_at`: `2026-06-05 05:58:45` to `2026-06-05 05:58:45`

#### Additional mirrored or auxiliary DBs

- The following historical SQLite artifacts were also removed during cleanup:
  - `scripts/data/fno_contracts_cache.db`
  - `scripts/data/quant_trading.db`
  - `scripts/data/trading_bot.db`
  - `scripts/data/zerodha_instruments.db`
  - `tests/data/fno_contracts_cache.db`
  - `quant_trading_system/data/zerodha_instruments.db`

## PostgreSQL usage

### Intended connection target

- Authoritative Postgres database name: `trading_bot`.
- Local `.env` declares `DATABASE_URL=postgresql+psycopg2://trading_bot:***@localhost:5433/trading_bot`.
- `core/database.py` builds the default Postgres URL from:
  - `POSTGRES_USER` default `postgres`
  - `POSTGRES_PASSWORD` default empty
  - `POSTGRES_HOST` default `localhost`
  - `POSTGRES_PORT` default `5433`
  - `POSTGRES_DB` default `trading_bot`
- `core/config.py` now defaults `POSTGRES_DB` to `trading_bot`, matching the live target.
- `scripts/setup_postgresql.py` provisions the Postgres database and writes `DATABASE_URL` back to `.env`.

### ORM tables expected in Postgres

- `trades`
- `positions`
- `log_events`
- `instrument_cache`
- `fno_contracts`
- `cache_metadata`
- `market_snapshot`

### Postgres consumers

- `core/database.DatabaseManager` is the main ORM entry point.
- `core/order_manager.OrderManager` persists trades.
- `core/execution_engine.ExecutionEngine` persists reconciled positions.
- `core/zerodha_broker.ZerodhaBroker` loads and replaces instrument cache data.
- `screener/fno_contract_loader.FnoContractLoader` loads and replaces F&O contract cache data.
- `scripts/download_instruments.py` writes `instrument_cache` to Postgres.
- `scripts/download_market_snapshot.py` writes `market_snapshot` to Postgres.
- `scripts/download_all_historical_enhanced.py` writes historical data to Postgres.

### Local reachability check

- `pg_isready -h localhost -p 5433` returned `localhost:5433 - no response`.
- `psql -h localhost -p 5433 -d trading_bot` was not usable because the local server was not responding.

### Postgres migration checklist

Use this checklist on the live `trading_bot` PostgreSQL database:

- Verify database name: `trading_bot`
- Verify ORM tables exist:
  - `trades`
  - `positions`
  - `log_events`
  - `instrument_cache`
  - `fno_contracts`
  - `cache_metadata`
  - `market_snapshot`
- Verify historical candle table exists if the historical downloader is used:
  - `candles`
- Verify `market_snapshot` schema:
  - either `instrument_token`, `symbol`, `exchange`, `last_price`, `quote_json`, `depth_json`, `timestamp`
  - or `instrument_token`, `symbol`, `exchange`, `last_price`, `bids_json`, `asks_json`, `timestamp`
- Verify `candles` schema:
  - `symbol`, `ts`, `interval`, `open`, `high`, `low`, `close`, `volume`
- Verify there are no leftover SQLite fallback dependencies in runtime code:
  - no file-backed `sqlite:///data/trading_bot.db`
  - no file-backed `data/*.db` runtime dependencies
  - in-memory SQLite fallback only if engine creation fails

### Read-only validation script

- [`scripts/validate_postgres_schema.sql`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/validate_postgres_schema.sql)
- Run with:
  - `psql "$DATABASE_URL" -f scripts/validate_postgres_schema.sql`

### Convenience wrapper

- [`scripts/validate_postgres_schema.sh`](/home/rajasekhar/vibe-coding/raj_trading_bot/scripts/validate_postgres_schema.sh)
- Run with:
  - `bash scripts/validate_postgres_schema.sh`

## Postgres validation handoff

Use these commands to validate the live `trading_bot` PostgreSQL database:

```bash
bash scripts/validate_postgres_schema.sh
psql "$DATABASE_URL" -f scripts/validate_postgres_schema.sql
```

## Baseline quality notes

- Duplicate `tradingsymbol` values exist in the instrument caches and should be treated as upstream-data artifacts, not repo corruption.
- `simple_market_snapshot.db` stores snapshot rows keyed by `instrument_token`, but the `symbol` column is entirely null in the current snapshot.
- `data/trading_bot.db` has duplicate `symbol` values in `trades`, which is expected for repeated fills of the same symbol.
- Historical DuckDB key integrity is currently clean for `(symbol, ts)`.
- Several tests and optional imports fail in the current environment because `pytest` is not installed and `vectorbt`/`numba` attempts to enable cached JIT artifacts from site-packages.
- The configured local Postgres endpoint is not reachable from this workspace at the time of inspection.

## Baseline test results

- `python3 -m py_compile $(rg --files . | rg '\\.py$')`
  - Result: passed
- `python3 -m pytest -q`
  - Result: failed because `pytest` is not installed in the current environment
- `python3 -m unittest discover -q`
  - Result: failed with pre-existing import/runtime issues:
    - missing `pytest` in several tests
    - `RuntimeError: cannot cache function 'set_seed_nb'` from `vectorbt` / `numba`
    - offline Zerodha instrument refresh attempts fail DNS resolution for `api.kite.trade`
