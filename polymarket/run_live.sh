#!/usr/bin/env bash
# Auto-restart wrapper for the live trader.
# Default config is sweet-spot strategy with $5 orders, dry-run unless POLY_DRY_RUN=false in .env.

set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-../backtest/.venv/bin/python}"  # override in Docker via env
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

THRESHOLD="${THRESHOLD:-0.10}"
COOLDOWN="${COOLDOWN:-60}"
SWEET_LO="${SWEET_LO:-0.30}"
SWEET_HI="${SWEET_HI:-0.40}"  # tightened 2026-06-08: best-bucket [0.30,0.40] = 67% backtest + 1/1 live win; [0.40,0.45] = 0/3 live
REQUIRE_CONFIRM="${REQUIRE_CONFIRM:-1}"  # 1 = require Coinbase 60s return to agree with Binance (filters single-exchange noise; Polymarket settles vs Chainlink-aggregated price)

trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

echo "[wrapper] starting live_trader.py — threshold=${THRESHOLD}%  cooldown=${COOLDOWN}s  sweet=[${SWEET_LO},${SWEET_HI}]"

while true; do
  DATE=$(date -u +%Y%m%d)
  STDOUT_LOG="$LOG_DIR/live_${DATE}.log"
  echo "[wrapper $(date -u +%H:%M:%S)] starting trader"
  CONFIRM_FLAG=""
  if [ "$REQUIRE_CONFIRM" = "1" ]; then CONFIRM_FLAG="--require-confirm"; fi
  "$PYTHON" live_trader.py \
    --threshold "$THRESHOLD" \
    --cooldown "$COOLDOWN" \
    --sweet-lo "$SWEET_LO" \
    --sweet-hi "$SWEET_HI" \
    $CONFIRM_FLAG \
    --log "$LOG_DIR/live_${DATE}.jsonl" \
    2>&1 | tee -a "$STDOUT_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "[wrapper $(date -u +%H:%M:%S)] trader exited $EXIT_CODE — restarting in 10s"
  sleep 10
done
