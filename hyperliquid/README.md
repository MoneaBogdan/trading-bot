# Hyperliquid Funding-Arb Bot

Status: **phase 1 scaffold (2026-06-18)** — monitor-only with dry-run execution stubs. No live capital.

Companion strategy to the Polymarket Up/Down latency-arb bot. See `../RESEARCH_DAYTRADING_2026.md` for the research that informed this build.

## Strategy

Two flavors, one monitor:

1. **HL cash-and-carry** — long spot on HL, short HL perp (or reverse). Portfolio margin cross-margins both legs → most capital-efficient on majors.
2. **Cross-venue perp basis** — long perp on the cheaper-funding venue, short perp on the richer. Monitored venues: Binance, Bybit, Drift, Paradex (HL is always one side; the other is whichever of the four shows the widest spread). Capital duplicated across venues; opens alts not on HL spot.

Initial universe: **BTC, ETH, SOL only**. Alts deferred until JELLY-style oracle-override risk is understood in practice (see research file).

Edge sources:
- HL funding rates routinely diverge from Binance/Bybit by 3–5 bps per 8h on majors → 3–12% APR unlevered
- Break-even spread with maker fills ~1.3 bps per 8h; slippage at small clip dominates fees

## Phase 1 — monitor + paper PnL

`funding_monitor.py` polls five venues every N seconds (all public, no auth):
- HL `/info` (perp meta + asset contexts)
- Binance `/fapi/v1/premiumIndex`
- Bybit `/v5/market/tickers?category=linear`
- Drift `https://data.api.drift.trade/rateHistory?marketIndex={0,1,2}` (0=SOL, 1=BTC, 2=ETH)
- Paradex `https://api.prod.paradex.trade/v1/markets/summary?market={BTC,ETH,SOL}-USD-PERP`

Per asset, `best_cross()` picks the **widest** HL-vs-other spread across {Binance, Bybit, Drift, Paradex}; the winning venue is recorded as `best_cex` in the snapshot event (may now be `drift` or `paradex`). The `boot` event declares `venues: ["hyperliquid", "binance", "bybit", "drift", "paradex"]` with per-venue market symbol mappings.

Writes a JSONL event per poll to `logs/funding_YYYY-MM-DD.jsonl`. When |spread| ≥ `HL_OPPORTUNITY_BPS_8H`, logs an `opportunity` event with gross + costs + **net** paper-PnL.

Drift egress note: the Drift endpoint may 403 from some egresses (CloudFront geo-restriction). Defensive parsing silently zeros Drift values on any failure so HL/Binance/Bybit/Paradex polling is never blocked.

Funding-rate semantics caveat:
- HL `funding` is forward-looking (next hour, × 8); Binance `lastFundingRate` and Bybit `fundingRate` are most-recent SETTLED 8h rates. Treat as sticky-state proxies for next-period funding, not as fillable quotes.
- Drift: raw fields are scaled integers. Conversion: `funding_per_hour = (fundingRate / 1e9) / (oraclePriceTwap / 1e6)`, then `funding_bps_8h = funding_per_hour * 8 * 10_000`, and `mark = oraclePriceTwap / 1e6`. Documented inline in `funding_monitor.py`.
- Paradex: `funding_rate` field is treated as an hourly decimal → `*8*10_000` for bps/8h. **Unit unverified.** If observed values look ~8× too large vs Binance/Bybit on the same asset, the rate is already an 8h-settled value and the `*8` should be dropped. Treat any Paradex-driven opportunity as suspect until this is confirmed against a known reference snapshot.

Cost model (env-tunable): 12 bps round-trip fees + 6 bps slippage (2× 3 bps per leg) = 18 bps booked against a single 8h cycle. Intentionally conservative: a position held N cycles amortizes the 18 bps over N. A 7 bps/8h spread held 30 days (~90 cycles) is ~$60 net on $1k notional (~73% APR). Read `net_pnl_8h_usdc` with this in mind — single-cycle net can be negative on a trade that's profitable over its real hold horizon.

### Local-test results 2026-06-18

First cross-DEX opportunity events fired on local run:
- ETH spread −6.9 bps/8h (Paradex vs HL)
- SOL spread −7.4 bps/8h (Paradex vs HL)
- BTC spread −4.1 bps/8h — below `HL_OPPORTUNITY_BPS_8H` (5 bps default), snapshot only, no opportunity event

Paradex-unit caveat above applies: re-verify before sizing on these.

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
- Paradex `funding_rate` unit confirmed (hourly vs 8h-settled) before any Paradex-side sizing
- Drift egress reachable from the production host (no 403) before any Drift-side sizing

## References

- Research: `../RESEARCH_DAYTRADING_2026.md`
- HL API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- HL Python SDK: https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- HL funding comparison: https://app.hyperliquid.xyz/fundingComparison
