#!/usr/bin/env bash
# Auto-restart wrapper for the live trader.
# Defaults: BTC 5-min markets, sweet-spot strategy, $5 orders, dry-run unless POLY_DRY_RUN=false.
# Override ASSET and TIMEFRAME_MIN to run other variants (ETH/SOL × 5/60).

set -uo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-../backtest/.venv/bin/python}"  # override in Docker via env
LOG_DIR="logs"
mkdir -p "$LOG_DIR"

ASSET="${ASSET:-BTC}"
TIMEFRAME_MIN="${TIMEFRAME_MIN:-5}"
# Optional suffix to distinguish two variants with the same asset+timeframe
# (e.g. VARIANT_SUFFIX=wide → eth-5m-wide). Empty = legacy behavior.
VARIANT_SUFFIX="${VARIANT_SUFFIX:-}"

THRESHOLD="${THRESHOLD:-0.10}"
COOLDOWN="${COOLDOWN:-60}"
SWEET_LO="${SWEET_LO:-0.30}"
SWEET_HI="${SWEET_HI:-0.40}"  # tightened 2026-06-08: best-bucket [0.30,0.40] = 67% backtest + 1/1 live win
# Coinbase confirm: only meaningful on 5-min markets (Chainlink-aggregate resolution).
# Hourly markets resolve from Binance only — set REQUIRE_CONFIRM=0 there.
REQUIRE_CONFIRM="${REQUIRE_CONFIRM:-1}"
SNIPE_WINDOW_S="${SNIPE_WINDOW_S:-300}"  # 300 = no tight snipe; anchor alone is EV-max per 2026-06-09 backtest
REQUIRE_WINDOW_ANCHOR="${REQUIRE_WINDOW_ANCHOR:-1}"

# Variant tag used in log filenames. Special-case BTC 5m to keep the legacy
# `live_<date>.jsonl` filename and preserve backward compatibility with old logs.
VARIANT="$(echo "$ASSET" | tr '[:upper:]' '[:lower:]')-${TIMEFRAME_MIN}m"
if [ -n "$VARIANT_SUFFIX" ]; then
  VARIANT="${VARIANT}-${VARIANT_SUFFIX}"
fi
if [ "$VARIANT" = "btc-5m" ]; then
  FILE_TAG=""        # legacy: logs/live_YYYYMMDD.{log,jsonl}
else
  FILE_TAG="${VARIANT}_"  # new variants: logs/live_<variant>_YYYYMMDD.{log,jsonl}
fi

trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM

echo "[wrapper] starting live_trader.py — asset=$ASSET  timeframe=${TIMEFRAME_MIN}m  variant=$VARIANT"
echo "[wrapper] threshold=${THRESHOLD}%  cooldown=${COOLDOWN}s  sweet=[${SWEET_LO},${SWEET_HI}]  confirm=$REQUIRE_CONFIRM  anchor=$REQUIRE_WINDOW_ANCHOR"

while true; do
  DATE=$(date -u +%Y%m%d)
  STDOUT_LOG="$LOG_DIR/live_${FILE_TAG}${DATE}.log"
  JSONL_LOG="$LOG_DIR/live_${FILE_TAG}${DATE}.jsonl"
  echo "[wrapper $(date -u +%H:%M:%S)] starting trader → $STDOUT_LOG"
  CONFIRM_FLAG=""
  if [ "$REQUIRE_CONFIRM" = "1" ]; then CONFIRM_FLAG="--require-confirm"; fi
  ANCHOR_FLAG=""
  if [ "$REQUIRE_WINDOW_ANCHOR" = "1" ]; then ANCHOR_FLAG="--require-window-anchor"; fi
  "$PYTHON" live_trader.py \
    --asset "$ASSET" \
    --timeframe-min "$TIMEFRAME_MIN" \
    --threshold "$THRESHOLD" \
    --cooldown "$COOLDOWN" \
    --sweet-lo "$SWEET_LO" \
    --sweet-hi "$SWEET_HI" \
    --snipe-window-s "$SNIPE_WINDOW_S" \
    --variant-suffix "$VARIANT_SUFFIX" \
    $CONFIRM_FLAG \
    $ANCHOR_FLAG \
    --log "$JSONL_LOG" \
    2>&1 | tee -a "$STDOUT_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "[wrapper $(date -u +%H:%M:%S)] trader exited $EXIT_CODE — restarting in 10s"
  sleep 10
done
