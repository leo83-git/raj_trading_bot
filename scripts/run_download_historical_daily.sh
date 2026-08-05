#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"

if [[ -d "$REPO_ROOT/.venv" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
fi

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/historical_download_$(date +%Y%m%d_%H%M%S).log"

DEFAULT_INTERVALS="${INTERVALS:-day,5minute}"
INTERVALS_TO_USE="${1:-$DEFAULT_INTERVALS}"

"$PYTHON_BIN" "$REPO_ROOT/scripts/download_all_historical_enhanced.py" \
  --intervals "$INTERVALS_TO_USE" \
  2>&1 | tee -a "$LOG_FILE"
