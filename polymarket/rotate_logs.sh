#!/usr/bin/env bash
# Rotate Polymarket logs. Intended to run inside the container (via host cron
# `docker compose exec`) or on the host pointing at the mounted logs dir.
#
# - Compresses yesterday's WS-recorder JSONL (10x reduction)
# - Prunes JSONL/log files older than $RETAIN_DAYS (default 14)
# - Trader signal JSONLs are tiny; we keep them indefinitely

set -euo pipefail
LOG_DIR="${LOG_DIR:-$(dirname "$0")/logs}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"

# Compress WS-recorder files that aren't today's
TODAY=$(date -u +%Y%m%d)
for f in "$LOG_DIR"/orderbook_ws_*.jsonl; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  date_part=${base#orderbook_ws_}; date_part=${date_part%.jsonl}
  if [ "$date_part" != "$TODAY" ]; then
    echo "gzip $f"
    gzip -f "$f"
  fi
done

# Prune anything older than RETAIN_DAYS
find "$LOG_DIR" -type f \( -name 'orderbook_ws_*.jsonl*' -o -name 'live_*.log' \) \
     -mtime "+$RETAIN_DAYS" -print -delete

echo "[rotate] done. disk used: $(du -sh "$LOG_DIR" | cut -f1)"
