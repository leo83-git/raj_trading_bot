-- Read-only PostgreSQL validation for the raj_trading_bot migration.
-- Run with:
--   psql "$DATABASE_URL" -f scripts/validate_postgres_schema.sql

\echo 'Checking database name'
SELECT current_database() AS current_database;

\echo 'Checking core tables'
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'trades',
    'positions',
    'log_events',
    'instrument_cache',
    'fno_contracts',
    'cache_metadata',
    'market_snapshot',
    'candles'
  )
ORDER BY table_name;

\echo 'Checking market_snapshot schema'
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'market_snapshot'
ORDER BY ordinal_position;

\echo 'Checking candles schema'
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'candles'
ORDER BY ordinal_position;

\echo 'Row counts'
SELECT 'trades' AS table_name, COUNT(*) AS row_count FROM trades
UNION ALL
SELECT 'positions', COUNT(*) FROM positions
UNION ALL
SELECT 'log_events', COUNT(*) FROM log_events
UNION ALL
SELECT 'instrument_cache', COUNT(*) FROM instrument_cache
UNION ALL
SELECT 'fno_contracts', COUNT(*) FROM fno_contracts
UNION ALL
SELECT 'cache_metadata', COUNT(*) FROM cache_metadata
UNION ALL
SELECT 'market_snapshot', COUNT(*) FROM market_snapshot
UNION ALL
SELECT 'candles', COUNT(*) FROM candles;

