#!/usr/bin/env bash
# Run the Polymarket observation harness locally with auto-restart and daily log rotation.
# Stop with Ctrl+C (which kills both this wrapper and the monitor).

set -uo pipefail

cd "$(dirname "$0")"
PYTHON="../backtest/.venv/bin/python"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

THRESHOLD="${THRESHOLD:-0.30}"      # 60s BTC return % to trigger a signal
COOLDOWN="${COOLDOWN:-120}"          # seconds between signals
HORIZON="${HORIZON:-300}"            # max seconds before market end to consider tradable

# Ensure children die when this script does.
trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

echo "[wrapper] starting monitor — threshold=${THRESHOLD}%  cooldown=${COOLDOWN}s"
echo "[wrapper] signals -> $LOG_DIR/signals_<date>.jsonl"
echo "[wrapper] stdout  -> $LOG_DIR/monitor_<date>.log"
echo "[wrapper] Ctrl+C to stop."
echo

while true; do
  DATE=$(date +%Y%m%d)
  SIGNAL_LOG="$LOG_DIR/signals_${DATE}.jsonl"
  STDOUT_LOG="$LOG_DIR/monitor_${DATE}.log"
  echo "[wrapper $(date +%H:%M:%S)] starting monitor (logs rotate on UTC date change)"
  "$PYTHON" monitor.py \
    --threshold "$THRESHOLD" \
    --cooldown "$COOLDOWN" \
    --log "$SIGNAL_LOG" \
    2>&1 | tee -a "$STDOUT_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "[wrapper $(date +%H:%M:%S)] monitor exited with code $EXIT_CODE — restarting in 5s"
  sleep 5
done
