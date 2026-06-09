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
SNIPE_WINDOW_S="${SNIPE_WINDOW_S:-300}"  # max secs-to-close at fire time. 300 = no tight snipe; anchor alone is the EV-maximizer per 2026-06-09 backtest (n=63, 78% win, +$26.85 vs n=14, 86% win, +$7 with snipe=90)
REQUIRE_WINDOW_ANCHOR="${REQUIRE_WINDOW_ANCHOR:-1}"  # 1 = require BTC-now vs window-open return to agree in sign with 60s return

trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

echo "[wrapper] starting live_trader.py — threshold=${THRESHOLD}%  cooldown=${COOLDOWN}s  sweet=[${SWEET_LO},${SWEET_HI}]"

while true; do
  DATE=$(date -u +%Y%m%d)
  STDOUT_LOG="$LOG_DIR/live_${DATE}.log"
  echo "[wrapper $(date -u +%H:%M:%S)] starting trader"
  CONFIRM_FLAG=""
  if [ "$REQUIRE_CONFIRM" = "1" ]; then CONFIRM_FLAG="--require-confirm"; fi
  ANCHOR_FLAG=""
  if [ "$REQUIRE_WINDOW_ANCHOR" = "1" ]; then ANCHOR_FLAG="--require-window-anchor"; fi
  "$PYTHON" live_trader.py \
    --threshold "$THRESHOLD" \
    --cooldown "$COOLDOWN" \
    --sweet-lo "$SWEET_LO" \
    --sweet-hi "$SWEET_HI" \
    --snipe-window-s "$SNIPE_WINDOW_S" \
    $CONFIRM_FLAG \
    $ANCHOR_FLAG \
    --log "$LOG_DIR/live_${DATE}.jsonl" \
    2>&1 | tee -a "$STDOUT_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "[wrapper $(date -u +%H:%M:%S)] trader exited $EXIT_CODE — restarting in 10s"
  sleep 10
done
