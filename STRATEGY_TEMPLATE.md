# Strategy style guide — write new strategies so future extraction is mechanical

Until Phase C of `ARCHITECTURE.md` lands, new strategies live wherever they fit
(`polymarket/`, a sibling top-level module, etc.). To make the Phase C extraction
a copy-paste job rather than a rewrite, follow these rules from day one.

## The five rules

1. **Pure decision function.** Every strategy has one function:

   ```python
   def decide(state, event, params) -> list[Intent]: ...
   ```

   No I/O inside. No `print`, no HTTP, no `time.sleep`, no reading env. If you
   need clock time, take `event.ts`.

2. **Params built once at boot from env.** Group all tunables in a frozen
   `Params` dataclass. Build it in `main()` from `os.environ`. Pass it into
   `decide`. Never read env inside the function.

3. **No cross-strategy / no cross-venue imports inside the strategy.** A
   strategy module imports only stdlib, `src.core.*`, and its own helpers.
   Data it needs (orderbook, prices) arrives via the event. The runner
   fetches; the strategy decides.

4. **State is one mutable object.** Pass it back into each `decide` call. To
   survive restarts, log via `BotLogger` and rebuild on boot. Do not pickle,
   do not write per-strategy state files.

5. **Side effects happen in the runner, not the strategy.** `decide` returns
   intents. The runner places orders and logs `fire` / `skip`.

## The shape

See `src/strategies/_template.py` for a copy-ready skeleton. The runner pattern
looks like:

```python
params = Params(threshold_pct=float(os.environ["THRESHOLD"]), ...)
state = State()
logger = BotLogger(bot=BOT_NAME, strategy="MyStrategy")
logger.boot(**asdict(params))

async for event in stream:
    for intent in decide(state, event, params):
        result = execute(intent)
        logger.fire(...)
```

## Logging

- Use `src.core.logger.BotLogger`. One line per fire, one line per skip.
- Skip reasons come from the controlled vocabulary
  (`src/core/logger.py:KNOWN_SKIP_REASONS`). To add a reason, add it to that
  set in the same PR — extending the vocab is intentional, not accidental.
- New events go to `logs/bot=<name>/YYYY-MM-DD.jsonl`. The legacy
  `logs/live_*.jsonl` path is for the existing latency-arb bot only; new
  strategies skip it.

## What this buys us at extraction time

When Phase C runs, the work is:
- Move `Params` / `State` / `decide` into `src/strategies/<name>.py` (often
  literally a `git mv`).
- Replace the strategy-local `Intent` with `src.core.types.OrderIntent`
  (field-for-field compatible by design).
- Delete the per-strategy main()/runner — the supervisor takes over.

No logic changes. No tests to rewrite. That's the whole point of the rules
above.

## Anti-patterns (refuse these in review)

- Strategy importing from another strategy.
- Strategy reading env vars inside `decide`.
- Strategy doing HTTP / file I/O / print.
- Mutable global state in the strategy module.
- Skip events written without going through `BotLogger.skip(reason, ...)`.
- A new "skip reason" string not in `KNOWN_SKIP_REASONS`.
