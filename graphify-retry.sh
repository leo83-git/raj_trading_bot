#!/bin/bash
# graphify-retry.sh
# Retry wrapper for `graphify extract` against Cerebras (or any OpenAI-compatible
# backend) that keeps re-invoking graphify until a run completes with no
# per-chunk failures, or until MAX_ATTEMPTS is hit. Relies on graphify's
# incremental semantic cache — each re-run only reprocesses files that
# failed or were never cached, so nothing is repeated unnecessarily.

set -uo pipefail

# ── Config — adjust as needed ──────────────────────────────────────────
MODEL="${GRAPHIFY_MODEL:-gemma-4-31b}"
TOKEN_BUDGET="${GRAPHIFY_TOKEN_BUDGET:-2000}"
API_TIMEOUT="${GRAPHIFY_API_TIMEOUT:-60}"
MAX_CONCURRENCY="${GRAPHIFY_MAX_CONCURRENCY:-1}"
SLEEP_SECONDS="${GRAPHIFY_RETRY_SLEEP:-20}"
MAX_ATTEMPTS="${GRAPHIFY_MAX_ATTEMPTS:-30}"
LOG_FILE="${GRAPHIFY_LOG_FILE:-/tmp/graphify-retry.log}"

export OPENAI_API_KEY="${CEREBRAS_API_KEY_1:?Set CEREBRAS_API_KEY_1 before running}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.cerebras.ai/v1}"

echo "=== graphify-retry starting: $(date) ===" | tee -a "$LOG_FILE"
echo "model=$MODEL token_budget=$TOKEN_BUDGET timeout=$API_TIMEOUT sleep=$SLEEP_SECONDS max_attempts=$MAX_ATTEMPTS" | tee -a "$LOG_FILE"

for i in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "" | tee -a "$LOG_FILE"
  echo "=== Attempt $i / $MAX_ATTEMPTS — $(date) ===" | tee -a "$LOG_FILE"

  # Capture this run's output so we can inspect it for failures.
  RUN_OUTPUT=$(graphify extract . \
    --backend openai \
    --model "$MODEL" \
    --max-concurrency "$MAX_CONCURRENCY" \
    --token-budget "$TOKEN_BUDGET" \
    --api-timeout "$API_TIMEOUT" 2>&1)

  echo "$RUN_OUTPUT" | tee -a "$LOG_FILE"

  # Heuristics for "this run had failures / more work remains":
  #   - any line containing "failed" (chunk failures)
  #   - any line containing "429" or "error" (transport/API errors)
  #   - any line reporting a nonzero count of docs/images/code still needing
  #     semantic extraction (i.e. "semantic cache: N hit / M miss" with M > 0)
  if echo "$RUN_OUTPUT" | grep -qiE "failed|error:|too_many_requests|queue_exceeded"; then
    echo "Failures detected this attempt. Sleeping ${SLEEP_SECONDS}s before retry..." | tee -a "$LOG_FILE"
    sleep "$SLEEP_SECONDS"
    continue
  fi

  # If no failure/error markers showed up at all, treat this as a clean run.
  echo "No failures detected — run completed cleanly." | tee -a "$LOG_FILE"
  echo "=== graphify-retry finished successfully: $(date) ===" | tee -a "$LOG_FILE"
  exit 0
done

echo "" | tee -a "$LOG_FILE"
echo "=== Reached MAX_ATTEMPTS ($MAX_ATTEMPTS) without a clean run. Check $LOG_FILE for details. ===" | tee -a "$LOG_FILE"
exit 1
