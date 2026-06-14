#!/usr/bin/env bash
# Auto-restart wrapper for the Polymarket WS orderbook recorder.
# Mirrors run_live.sh: tee stdout to a per-UTC-day file so reconnect/error
# messages persist alongside the .jsonl data file.

set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-../backtest/.venv/bin/python}"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

while true; do
  DATE=$(date -u +%Y%m%d)
  STDOUT_LOG="$LOG_DIR/orderbook_ws_${DATE}.log"
  echo "[wrapper $(date -u +%H:%M:%S)] starting ws-recorder"
  "$PYTHON" orderbook_recorder_ws.py 2>&1 | tee -a "$STDOUT_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "[wrapper $(date -u +%H:%M:%S)] ws-recorder exited $EXIT_CODE — restarting in 10s"
  sleep 10
done
