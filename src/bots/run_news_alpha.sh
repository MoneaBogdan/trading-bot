#!/usr/bin/env bash
# Run the news_alpha bot. Loads .env.news from repo root, runs forever.
set -euo pipefail

cd "$(dirname "$0")/../.."

if [ ! -f .env.news ]; then
  echo "missing .env.news (copy from .env.news.example and fill it in)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env.news
set +a

# Ensure logs/news exists for the recorder side-channel (runner uses logs/bot=news-alpha/).
mkdir -p logs/news "logs/bot=news-alpha"

exec python -m src.bots.news_alpha_runner

