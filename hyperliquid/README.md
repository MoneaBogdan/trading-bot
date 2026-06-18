# Hyperliquid Funding-Arb Bot

Status: **phase 1 scaffold (2026-06-18)** — monitor-only with dry-run execution stubs. No live capital.

Companion strategy to the Polymarket Up/Down latency-arb bot. See `../RESEARCH_DAYTRADING_2026.md` for the research that informed this build.

## Strategy

Two flavors, one monitor:

1. **HL cash-and-carry** — long spot on HL, short HL perp (or reverse). Portfolio margin cross-margins both legs → most capital-efficient on majors.
2. **Cross-venue perp basis** — long perp on the cheaper-funding venue (Binance/Bybit), short perp on the richer (HL or vice versa). Capital duplicated across venues; opens alts not on HL spot.

Initial universe: **BTC, ETH, SOL only**. Alts deferred until JELLY-style oracle-override risk is understood in practice (see research file).

Edge sources:
- HL funding rates routinely diverge from Binance/Bybit by 3–5 bps per 8h on majors → 3–12% APR unlevered
- Break-even spread with maker fills ~1.3 bps per 8h; slippage at small clip dominates fees

## Phase 1 — monitor + paper PnL

`funding_monitor.py`:
- Polls HL `/info`, Binance `/fapi/v1/premiumIndex`, Bybit `/v5/market/tickers` every N seconds
- Per asset, picks the **widest** HL-vs-CEX spread across {Binance, Bybit}
- Writes a JSONL event per poll to `logs/funding_YYYY-MM-DD.jsonl`
- When |spread| ≥ `HL_OPPORTUNITY_BPS_8H`, logs an `opportunity` event with gross + costs + **net** paper-PnL

Funding-rate semantics caveat: HL `funding` is forward-looking (next hour, × 8); Binance `lastFundingRate` and Bybit `fundingRate` are most-recent SETTLED 8h rates. Treat as sticky-state proxies for next-period funding, not as fillable quotes.

Cost model (env-tunable): 12 bps round-trip fees + 2× 3 bps slippage per leg, booked against a single 8h cycle (conservative — actual cost amortizes over hold length).

### Known gap — HL spot-vs-perp carry

The richest majors play per the research is **on-HL cash-and-carry** (HL spot vs HL perp) because portfolio margin cross-margins the two legs. We're NOT polling this yet because HL spot on majors is wrapped (uBTC, uETH) with thinner liquidity than perps. Defer to phase 1.5; if cross-venue perp monitor shows low opportunity count after ≥7 days, switch focus.

Run:
```bash
cd hyperliquid
cp .env.example .env  # no secrets needed for phase 1, all reads are public
pip install -r requirements.txt
python funding_monitor.py
```

## Phase 2 (planned)

- Add `executor.py` with EIP-712 signing using `hyperliquid-python-sdk`. Stays in dry-run via `HL_DRY_RUN=true`.
- Add `strategy.py` with a pure `decide(state) -> Intent | None` function (same pattern as `polymarket/`).
- Wire pre-trade gates: per-asset position cap, daily-loss cap, oracle-deviation kill switch.

## Phase 3 (gated on paper-PnL evidence)

- Fund wallets, flip `HL_DRY_RUN=false` one asset at a time.
- Add a `verify_fills.py` that reconciles intended vs realized PnL.

## Go-live gates

Same discipline as the Polymarket bot:

- N≥7 days of paper-PnL beating break-even APR (after modeled 12 bps slippage)
- Zero unhandled exceptions in monitor logs
- Manual review of every "would-have-fired" event for the first 100 opportunities
- Kill-switch tested: a stale-quote event (>5s WS gap) must block fires

## References

- Research: `../RESEARCH_DAYTRADING_2026.md`
- HL API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- HL Python SDK: https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- HL funding comparison: https://app.hyperliquid.xyz/fundingComparison
