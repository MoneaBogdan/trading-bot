# News recorder

Standalone listener that captures news headlines to `logs/news/YYYY-MM-DD.jsonl`
for offline classifier development and backtesting. Does **not** trade.

## telegram_news.py — Tree of Alpha Telegram feed

### One-time setup

1. Get Telegram API credentials (free):
   - Go to https://my.telegram.org/apps → log in with your phone number
   - Create an app (any name/description) → copy `api_id` and `api_hash`

2. Install deps:
   ```bash
   pip install telethon
   ```

3. Create `.env.news` next to repo root (gitignored — already covered by `.env*`):
   ```
   TG_API_ID=1234567
   TG_API_HASH=abcdef0123456789abcdef0123456789
   TG_CHANNELS=treeofalpha
   ```

   `TG_CHANNELS` is a comma-separated list of channel usernames (without @).
   Confirm the exact handle of the public Tree of Alpha channel before first run;
   you may need to join it from your Telegram account first.

### First run (interactive — required once)

```bash
cd /path/to/trading-bot
set -a; source .env.news; set +a
python -m src.recorders.telegram_news
```

Telethon will prompt for your phone number, then an SMS code. After successful
auth it saves `tree_news_recorder.session` in the cwd. Keep that file — it's
the auth artifact (treat like a credential; gitignored via `*.session`).

### Subsequent runs (non-interactive)

Same command — it'll reuse the saved session.

### Output

Appends to `logs/news/<UTC-date>.jsonl`. Each line:

```json
{
  "ts": "2026-06-16T13:42:01.234+00:00",
  "ts_received": "2026-06-16T13:42:01.456+00:00",
  "source": "telegram",
  "channel": "treeofalpha",
  "message_id": 12345,
  "text": "BREAKING: ...",
  "raw": { ... }
}
```

`ts_received - ts` is the Telegram-delivery latency — useful for sanity-checking
how fast the feed is in practice.

### Server deployment (later)

Once you've done the interactive login locally, copy the `.session` file to
the server alongside the same `.env.news`. The recorder can then run headless
in Docker — but we're not adding it to docker-compose yet. First collect a
local corpus, build the classifier, backtest, then deploy.
