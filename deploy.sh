#!/usr/bin/env bash
# Pull the latest from GitHub and restart the bot if anything changed.
# Idempotent — runs in seconds when there's nothing new, only rebuilds
# the image when the Dockerfile or polymarket/requirements.txt changed.
#
# Schedule via cron or systemd-timer — see README "Auto-deploy".

set -euo pipefail
cd "$(dirname "$0")"

REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

OLD=$(git rev-parse HEAD)
git fetch --quiet "$REMOTE" "$BRANCH"
NEW=$(git rev-parse "$REMOTE/$BRANCH")

if [ "$OLD" = "$NEW" ]; then
  exit 0
fi

echo "[$(date -u +%FT%TZ)] [deploy] $OLD → $NEW"
git pull --ff-only --quiet "$REMOTE" "$BRANCH"

# Code is baked into the image via `COPY . /app`, so any commit needs a rebuild.
# Docker layer caching makes this fast (~10s) when requirements.txt hasn't changed.
docker compose up -d --build --force-recreate

echo "[deploy] done"
