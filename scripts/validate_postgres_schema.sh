#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${DATABASE_URL:-}" && -f "${ROOT_DIR}/.env" ]]; then
  # Load only simple KEY=VALUE pairs from .env without executing it.
  while IFS='=' read -r key value; do
    case "$key" in
      DATABASE_URL)
        export DATABASE_URL="${value}"
        break
        ;;
    esac
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ROOT_DIR}/.env" || true)
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set"
  exit 1
fi

echo "Validating PostgreSQL schema for raj_trading_bot"
echo "Database URL: ${DATABASE_URL}"
echo

psql "$DATABASE_URL" -f "${ROOT_DIR}/scripts/validate_postgres_schema.sql"
