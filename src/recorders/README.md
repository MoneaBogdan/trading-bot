# News recorder

Standalone listener that captures news headlines to `logs/news/YYYY-MM-DD.jsonl`
for offline classifier development and backtesting. Does **not** trade.

## news_recorder.py

Dispatches on `NEWS_SOURCE` env var:
- `treeofalpha_rest` (default) — polls https://news.treeofalpha.com/api/news
  Free, no auth. Latency ~2-3 min. **Recommended.**
- `telegram` — Telethon listener. Requires interactive one-time login + a
  Telegram channel you've already joined. ⚠ Telegram has many Tree-of-Alpha
  impersonator channels — verify the channel's content before trusting it.

### Tree of Alpha REST (default — zero setup)

```bash
cd /path/to/trading-bot
pip install httpx anthropic        # telethon NOT needed for REST mode
set -a; source .env.news; set +a   # NEWS_SOURCE defaults to treeofalpha_rest
python -m src.recorders.news_recorder
```

You should see `[recorder] primed with N known ids; entering live loop`, then
periodic `+M new headlines` lines as items come in. Headlines land in
`logs/news/<UTC-date>.jsonl`.

### Telegram (paid-WS alternative, when you have a verified channel)

Setup:
1. Get `TG_API_ID` / `TG_API_HASH` from https://my.telegram.org/apps.
2. Join the target channel from your Telegram account first.
3. Set in `.env.news`:
   ```
   NEWS_SOURCE=telegram
   TG_API_ID=...
   TG_API_HASH=...
   TG_CHANNELS=<verified_channel_handle>
   ```
4. `pip install telethon`
5. One-time interactive login:
   ```bash
   set -a; source .env.news; set +a
   python -m src.recorders.news_recorder
   ```
   Enter phone number + a code that arrives in your Telegram app.
   A `<TG_SESSION_NAME>.session` file is created — treat like a credential.

Subsequent runs are non-interactive (session file persists).

## Output schema

Every row in `logs/news/<date>.jsonl`:

```json
{
  "ts": "2026-06-17T13:42:01.234+00:00",          // source-reported time
  "ts_received": "2026-06-17T13:42:03.456+00:00", // when our process saw it
  "source": "treeofalpha_rest",                    // or "telegram"
  "channel": "DECRYPT",                            // sourceName / channel handle
  "message_id": "1781720616033BCUFAIAIPtBaFSA",
  "text": "BARRONS: Coinbase Unveils Free AI Investment Advisor...",
  "raw": { ... }                                   // full original payload
}
```

`ts_received - ts` is the end-to-end delivery latency — useful for sanity-checking.
