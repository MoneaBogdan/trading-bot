# Day-Trading Discovery — Crypto Perps / Futures / FX with Python+Docker Automation Lens

Generated 2026-06-18 from a deep-research workflow (killed before final synthesis — coverage uneven, flagged inline). Scope: identify (a) automatable day-trading strategies and (b) prop-firm paths for a Python/Docker dev already running a Polymarket BTC/ETH/SOL latency-arb bot. NOT in scope: US equities, 0DTE options.

## 1. Verified practitioners — thin source coverage

The research surfaced **verification platforms over vetted humans** — use these as the filter, not YouTube/Twitch reputations:

- **Hyperliquid leaderboard** (`app.hyperliquid.xyz/leaderboard`) — on-chain perps PnL, 266k+ wallets, filterable by consistency. Visible names: *The White Whale* (~$50M/30d July 2025, single window — could be variance), *Machi Big Brother* (92% WR / 76 trades — small sample), *James Wynn* (documented blow-up, cautionary).
- **HyperTracker** (`hypertracker.io`) — third-party analytics over Hyperliquid; separates one-shot lottery from repeatability.
- **Kinfo** — broker-integrated, hardest to fake; thinner coverage than Myfxbook.
- **Myfxbook Systems** — broker-attested FX/futures equity curves; cherry-picking + demo accounts still leak.
- **PropFirmMatch payout leaderboard** — real withdrawal screenshots across Topstep/Apex/Tradeify/FundedFutures.

**Named grifters to avoid** (treat content as marketing only):
- **TJR Trades** — accused of fabricating PnL
- **Tori Trades** — $250k drawdown scandal + predatory prop-firm affiliate funnel
- **Dannystrades / Traders Evolve** — caught sim-trading on a "live" stream Feb 2025
- **ICT / Michael Huddleston** — failed his own 2016 $10k→$1M, blew his 2024 Robbins Cup, zero verified students. *Mechanics* (order blocks, FVG, liquidity sweeps) are salvageable; strip them out of the cult layer.

## 2. Communities

Nothing institutional-grade surfaced. Best signal:

- **r/algotrading** — least-bad public hub for Python/Docker bot builders
- **Hyperliquid + GMX Discords** — participants have real on-chain capital; good for funding-arb chatter
- **Delphi Digital** (paid) — research-grade crypto perps; paywall filters shillers
- **Filthy Rich Futures** (~10k members) — one of the few futures servers with real risk-mgmt discussion
- Skip generic Whop / Disboard servers — signals/screenshot noise

## 3. Strategy archetypes — automation feasibility (1 easy → 5 hard)

| Archetype | Crypto perps | ES/NQ | FX | Notes |
|---|---|---|---|---|
| Funding-rate / basis arb | **1** | — | — | Pure rate math, no TA — closest to your Polymarket stack |
| ORB + VWAP + volume filter | 2 | **2** | 3 | Deterministic state machine; open Pine refs to port |
| Liquidity-sweep reversal | 2 | 2 | 2 | Same primitive across asset classes; clean spec |
| ICT confirmation (OB + FVG + sweep) | 3 | 3 | 3 | Codifiable once stripped from cult layer |
| Order-flow / footprint / DOM | 4 | 4 | 5 | Needs L2 + labelled training; futures data cheapest |
| News / macro-event reaction | 4 | 4 | 4 | Latency-sensitive AND throttled by most prop firms |
| Tick scalping / latency arb | 5 | 5 | 5 | Explicitly banned at The5ers / FundingPips / MyFundedFX |

Realistic Sharpe for retail post-cost: **0.7–1.5**. Any backtest claiming Sharpe >2.5 with <10% DD is overfit.

## 4. Prop-firm landscape 2026 (post-MFF shakeout)

80–100 firms collapsed Feb 2024–late 2025 (fee-Ponzi structures). MFF itself was *dismissed with prejudice* May 2025 — not all "scam" headlines were fraud. Pass-rate reality: 5–10% eval pass industry-wide; ~7% of passers ever withdraw. Topstep: 16.8% combine pass, 33.3% funded payout (2025). Apex: $598M distributed since 2022, $15.4M/mo avg.

**EA permission matrix:**

| Firm | EAs | Payout | Gotcha |
|---|---|---|---|
| **FTMO** | Yes, no pre-approval | up to 90%, 14d | Unique-strategy rule; news blackout |
| **Topstep** | Yes via TopstepX API | 100% on first $10k/mo | VPS/remote-server execution banned on some programs |
| **Apex** | Yes (current accounts) | 100% on first $25k/mo | Legacy accounts ban automation; 30% daily consistency cap |
| **MyFundedFutures** | Yes (un-banned Jul 2025) | 90% | No HFT |
| **FundingPips** | Yes MT5 | — | 10-lot/day cap; 3-min news window |
| **The5ers** | Yes | — | Bans tick scalp / latency arb / HFT |
| **FundedNext** | Yes | 24h payout guarantee | News unrestricted; no hidden lot caps |
| **Atlas / Alpine / BrightFunded** | Yes | — | **No consistency rule** — friendly to lumpy PnL |
| **FunderPro** | Self-coded only | — | Rented EAs banned |
| **Earn2Trade** | **No** | — | Skip |

**Bot-killer rules to encode pre-trade:** consistency caps (25–50%), per-instrument lot maxes, news blackouts (±2–5 min), martingale/HFT pattern detectors, sub-100ms fill flags, divergent dashboard-vs-rulebook drawdown semantics.

## 5. First 3 to build (given existing Polymarket stack)

1. **Hyperliquid funding-rate / cross-venue basis bot.** Pure math, on-chain, no prop rules in scope. Reuses event-driven plumbing. Hyperliquid leaderboard gives free verification of own curve. Lowest decay risk. **← starting here**
2. **ORB + VWAP + volume filter on MES/MNQ → Topstep (TopstepX API) or MyFundedFutures.** Deterministic state machine. Topstep if colocation possible; MFFu if Docker-on-VPS. Adds funded capital without a CFD broker.
3. **Liquidity-sweep reversal scanner across crypto perps + FX majors → FTMO or Atlas Funded.** One feature spec reused across symbols (prior swing wick + close-back-inside + structure confirm). Pick Atlas if PnL will be lumpy — no consistency rule.

**Skip first:** order-flow/footprint (data + labelling cost), pure news bots (rule landmines + latency arms race), undiluted ICT.

---

## Follow-up: Hyperliquid funding-arb deep-dive (2026-06-18)

Concentrated search on the three gaps above. Findings below shape the build.

### The actual edge

- **Funding accrual:** hourly on HL = 1/8 of the 8h rate, capped at 4%/hr; intra-hour open+close pays zero ([HL funding docs](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding), [perp.wiki](https://perp.wiki/learn/hyperliquid-funding-rates-guide))
- **Realistic net APRs** (post fees + slippage, 2026): **3–12% on BTC/ETH/SOL**, **20–60%+ on long-tail perps** (HYPE, XPL, new HIP listings) ([neuralarb 2026-04](https://www.neuralarb.com/2026/04/24/hyperliquid-vs-cexs-perp-arbitrage-after-fees-funding-slippage/))
- **HL vs Binance/Bybit spreads:** routinely +0.03–0.05% per 8h on active pairs ([HL funding comparison](https://app.hyperliquid.xyz/fundingComparison)). BitMEX reports HL-short / BitMEX-long delivered **~15.6% APR on SOL, 15.7% on AVAX H1 2025 unlevered** — 25–30%+ at 2–3× ([BitMEX blog](https://www.bitmex.com/blog/harvest-funding-payments-on-hyperliquid))
- **Break-even spread** with maker fills: ~1.3 bps per 8h; slippage (~12 bps at $500k clip) dominates fees
- **Capital efficiency:** **on-HL cash-and-carry > cross-venue perp basis** for BTC/ETH/SOL because HL [portfolio margin](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-margin) cross-margins spot+perp. Cross-venue requires duplicated collateral but opens long-tail names not on HL spot. **Net play: HL-internal carry on majors, cross-venue for alts.**
- **Practitioner attribution thin** — leaderboards sort by directional PnL not market-neutral carry; HLP vault is the largest visible neutral operator. No named retail arb whale publicly documented.

### Hyperliquid API quirks

- **Auth:** EIP-712 typed-data, nonce = ms timestamp, monotonic per wallet ([API docs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api))
- **Rate limits:** 100 req / 10s per wallet (~10 orders/s); 1000 WS subs/IP ([limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits))
- **WS:** no keepalive guarantee — handle disconnects; reconnect ack replays missed data
- **Fees:** 0.015% maker / 0.045% taker base, HYPE-staking discounts
- **Order types:** market, limit, **ALO (post-only)**, IOC, reduce-only, stop, TWAP, trigger
- **Funding exit:** exit before top-of-hour to skip the accrual
- **L1/MEV:** order book + matching on HL's L1; no public mempool, no traditional sandwich on limits. **Oracle override is the real systemic risk** (see JELLY below)
- **Python SDK:** [hyperliquid-dex/hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) (official, good). CCXT supports HL too. **Gotcha: nonce collisions on multi-process bots** — multi-container deployments need wallet-per-container

### Practitioners & repos

Active 2025–2026 references:
- [rustjesty/hyperliquid-drift-arbitrage-bot](https://github.com/rustjesty/hyperliquid-drift-arbitrage-bot) — Drift↔HL funding diff, Python 3.12, market-neutral
- [Jackhuang166/hyberliquid-arbitrage](https://github.com/Jackhuang166/hyberliquid-arbitrage) — Bybit↔HL alerter, Rust
- [chainstacklabs/hyperliquid-trading-bot](https://github.com/chainstacklabs/hyperliquid-trading-bot) — reference scripts

### The cautionary tale: JELLY (March 2025)

HLP inherited a forced short on JELLYJELLY; attacker pumped spot 250%; validators **overrode the oracle** to settle at $0.0095, delisted the token. HLP took ~$13.5M unrealized hit, TVL dropped $200M in a day ([OAK Research](https://oakresearch.io/en/analyses/investigations/hyperliquid-jelly-attack-context-vulnerability-team-solution), [Talos](https://www.talos.com/insights/jellys-last-jam)).

**Build implication: avoid low-cap HIP listings as a perp leg. Oracle override means even a delta-neutral position can be force-closed at a non-market price. Restrict initial universe to BTC/ETH/SOL/AVAX/HYPE-tier names with deep cross-venue spot.**

---

## Build plan (decided 2026-06-18)

- **Code location:** new top-level `hyperliquid/` dir, mirrors `polymarket/`
- **Phase 1 scope:** unified monitor (HL cash-and-carry + cross-venue perp basis), dry-run execution scaffolding (EIP-712 sign code present but `HL_DRY_RUN=true` blocks send), JSONL paper-PnL log
- **Initial universe:** BTC, ETH, SOL only — defer alts until JELLY-style oracle risk is understood in practice
- **Go-live gates:** N days of paper-PnL > break-even APR after modeled slippage; manual review of every "would-have-fired" event

---

## Round 2 follow-up: 10-agent concentrated sweep (2026-06-18)

Ten parallel agents went deeper on the open gaps. Key findings:

### ⚠ Funding-arb edge has compressed — re-baseline the HL bot

The "3-12% net APR on BTC/ETH/SOL" claim is upper-band only. Post-Oct-10-2025 ADL cascade (HL force-closed $10B+, first ADL in two years, several delta-neutral funds went accidentally naked-long when shorts ADL'd while spot collateral simultaneously liquidated), realistic baseline is **3-6% net** on majors — roughly in line with sUSDe (~3.7% Q1 2026) and CME basis (~5%). Major absorbers: Ethena USDe (~$5.5-6B supply Q2 2026) and CME basis HFs ([BitMEX Q3 2025](https://www.bitmex.com/blog/2025q3-derivatives-report), [CoinDesk Oct 11 2025](https://www.coindesk.com/markets/2025/10/11/largest-ever-crypto-liquidation-event-wipes-out-6-300-wallets-on-hyperliquid), [Stablecoin Insider Q1 2026](https://stablecoininsider.org/ethena-usde-q1-2026-report/), [NeuralArb Apr 2026](https://www.neuralarb.com/2026/04/24/hyperliquid-vs-cexs-perp-arbitrage-after-fees-funding-slippage/)).

**Where the asymmetric edge actually lives in mid-2026:**
1. **HL HIP-1 long-tail pre-CEX listings** — funding hits the 4%/hr cap on thin books, 20-60%+ APR ranges. But this is exactly where JELLY-style oracle-override risk concentrates → fundamental tension with our "BTC/ETH/SOL only" constraint
2. **Cross-DEX divergence** (HL vs Drift / Lighter / Aster) — peaks >20% APR during divergences, persistent in 2026 ([Bitsgap 2026](https://bitsgap.com/blog/same-position-four-different-bills-how-funding-rates-differ-across-perp-dexs-in-2026))
3. **Vol/news regimes** — ADL risk asymmetric, expensive when wrong

**Implication:** let the monitor run; if 7 days of paper-PnL on majors averages <3% net APR, pivot to either (a) cross-DEX perp basis (Lighter is the highest-divergence next venue) or (b) accept HIP-1 long-tails with explicit oracle-deviation kill switch.

### DEX perp expansion path (when ready)

Rank-ordered next venues for delta-neutral funding arb beyond HL:

| Venue | TVL | Typical HL spread | Python ergonomics | Notes |
|---|---|---|---|---|
| **Lighter** (own zk-rollup) | ~$487M | ±5-15 bps/8h | Thin (community wrappers only) | Wide funding band, best signal-to-noise |
| **Drift** (Solana) | ~$600M+ | ±3-10 bps/8h, wider on alts | Best — official [DriftPy](https://drift-labs.github.io/driftpy/) | Sol RPC latency is the gotcha |
| **Aster** | ~$1.2B | ±5-20 bps/8h (incentive-distorted) | [Binance-style API](https://docs.asterdex.com/) | Token-flow distortions complicate signal |
| **dYdX v4** | ~$1B | ±2-5 bps/8h | Mature SDK | Tight spreads on majors, integrate only for depth |
| **Vertex** | smaller | ±5-15 bps/8h | Official [Python SDK](https://vertex-protocol.github.io/vertex-python-sdk/) | Ink/Move migration risk; defer |
| ~~Aevo~~ | $15.7M | — | — | Dead liquidity, skip |
| ~~GMX v2 / Jupiter~~ | — | borrow-fee model | — | Not funding-arb venues |

### Futures path #2 — Topstep + project-x-py is real

The "Python on futures props requires C# / NinjaScript" assumption is wrong in 2026:

- **TopstepX API** GA April 2026 — REST + SignalR WS, JWT auth, $29/mo ($14.50 if active trader). `project-x-py` ([PyPI](https://pypi.org/project/project-x-py/)) is a mature async Python SDK. **No CME ILA surcharge** unlike Tradovate ($290-500/mo).
- **Gotcha:** TopstepX terms require local execution — "no VPS/cloud" technically against ToS. Home server qualifies; cloud-only deployment is a rule break.
- **ORB+VWAP modal config** (TradingView/NT8 scripts on MNQ/MES, 2025-26): 15-min OR (9:30-9:45 ET), entry window closes 11:00 ET, 5-min close confirmation outside OR, session VWAP anchored 9:30 ET, volume >1.5× 20-bar SMA, TP 120-300 ticks, SL 60-150 ticks ([MNQ ORB TV script](https://www.tradingview.com/script/khcR5SPp-MNQ-ORB-Strategy-VWAP-Bias/)).
- **Eval killers:** Topstep's 50% consistency rule (best day ≤ 50% of total PnL) blocks one-shot algos. MFFu's ±2-min news blackout on every scheduled release is the silent killer.
- **Realistic build:** 2-3 weeks on Topstep+project-x-py for ORB/VWAP. MFFu adds ~1 week for news scheduling. NinjaScript is a dead end for a Python+Docker shop.

### FX path #3 — Atlas + TradeLocker beats FTMO + MT5

- **Atlas Funded 2-Step has no consistency rule at any stage** ([Atlas](https://www.atlasfunded.com/post/prop-firms-with-no-consistency-rules)) — friendly to lumpy sweep-PnL.
- **TradeLocker has a documented REST/WS API** — Python-native, skip MT5 entirely. Cleanest path for a Python+Docker shop.
- **FTMO** allows EAs only on MT4/MT5/cTrader. cTrader Open API > MT5+MetaApi > headless MT5 Windows VPS. **DXtrade is dead** for FX automation — FTMO killed REST API access 27 Apr 2024 ([FTMO 2024-04-25 update](https://ftmo.com/en/blog/trading-updates/trading-update-25-apr-2024/)).
- **MetaApi.cloud** has Trustpilot reliability flags (multi-day outages reported) — don't make it the sole path to a funded account.
- **EURUSD London kill-zone** (07:00-11:00 GMT) is the only sweep-reversal setup with credible numbers: prior swing taken on wick + close back inside + rejection bar, ~60-70% WR @ 1:2R per published backtests (treat selection-biased — expect 50-55% after slippage and ≥2-min FTMO hold floor).
- **Realistic build:** closer to 2 months than 2 weeks. The 6 weeks go into Forex Factory scraper reliability, per-firm rule encoding, realistic tick-replay backtest, MT5/Windows babysitting (if not using TradeLocker), and one full Challenge cycle to flush unanticipated rule violations.

### Skip order-flow automation (cost-to-edge ratio is poor)

- **Data cost:** Databento CME at $199/mo for MBP-10 + MBO is the cleanest Python-native path. Rithmic is cheaper if you tolerate FIX/R-API.
- **Codable features:** OFI, CVD, delta-divergence, basic absorption are deterministic. Exhaustion + iceberg-detection bleed into judgment.
- **Public evidence:** OFI lineage well-validated (Cont/Kukanov OFI explains 65-87% of short-term mid-price variance, multiple 2024-25 replications). **Zero 2025-26 papers** validate Bookmap-style footprint patterns (absorption/exhaustion visuals) as ML features with OOS edge on MES/MNQ net of fees.
- **If still tempted:** Databento + NautilusTrader + code only OFI + CVD-divergence as a *filter* on an existing strategy. Budget 2 weeks max. Funding arb has near-zero data cost and a mechanical (not statistical) edge mechanism.

### NautilusTrader is the single framework worth standardizing on

For any v2 of the bots:

- **NautilusTrader** (Rust core, Py API, v1.227 May 2026, ~17k stars) is the only OSS framework that genuinely earns "production-grade" in 2026
- Multi-asset adapters: Binance/Bybit/Coinbase/OKX spot+perp, dYdX, IB, Databento (CME futures), new Uniswap/Pancake/Aerodrome DeFi adapter (Jun 2026)
- Event-driven, **deterministic sim==live semantics** (sim and live run the same code path)
- One codebase could handle ORB+VWAP on CL futures AND funding arb on ETH-PERP

Data combo: **NautilusTrader + Databento (CME MBO PAYG, ~$300-800 for 6mo ES+CL+NQ) + Tardis.dev (crypto perps, ~$3.5-4k for 6mo full L2+funding)**. Binance free dumps work if you only need aggTrades + L1 snapshots.

### Verified practitioners (still thin overall)

- **Crypto perps:** HLP vault (cleanest auditable on-chain delta-neutral benchmark at [stats.hyperliquid.xyz](https://stats.hyperliquid.xyz/)), Growi HF vault (multi-month track record since July 2024, HFT MM — not retail-codifiable). **No public X account passes the "wallet + 6 months positive Sharpe" bar** — be skeptical of HL "callers."
- **ES/NQ futures:** Topstep Big Board top-10 funded P&L is the public leaderboard ([Topstep payout policy](https://help.topstep.com/en/articles/8284233-topstep-payout-policy)) — rotates monthly, mostly discretion + ORB.
- **FX:** Myfxbook "Systems" verified-track + verified-privilege filter, Kinfo broker-attested. Sort by **max DD, not gain** — most top systems are grid/martingale.
- **Codifiable reference:** [Rob Carver / pysystemtrade](https://github.com/pst-group/pysystemtrade) — systematic futures, methodology + code public, modest but real Sharpe.

### Communities (best signal-to-noise)

- **Crypto bot peer review:** [Freqtrade Discord](https://github.com/freqtrade/freqtrade) is the highest-signal free option. Strategy code, hyperopt, exchange quirks.
- **Engine/architecture critique:** [NautilusTrader Discussions](https://github.com/nautechsystems/nautilus_trader/discussions) — maintainers answer directly.
- **Free daily firehose:** [Quantocracy](https://quantocracy.com/).
- **Paid (code-density):** [Quantitativo](https://www.quantitativo.com/) (~$15/mo).
- **Skip:** every "algo trading" Discord on disboard — almost all are signal/copy-trade/prop-firm funnels.

### Newsletters worth paying for

1. **Quantitativo** — weekly backtested strategies with full Python code
2. **Concretum Group** — vol-risk-premium + trend-following, peer-reviewed-style papers
3. **Angus SLQ** — 5 live quant systems, monthly equity curves, Quantiacs Q22 winner 2025

---

## Revised priority order (post round-2)

1. ✅ **HL funding monitor** — running. **Decision threshold:** if 7 days paper-PnL averages <3% net APR on majors, pivot to cross-DEX (add Lighter / Drift) BEFORE building the executor.
2. **TopstepX + project-x-py ORB/VWAP on MNQ** — 2-3 week build, real Python path, no NinjaScript. Better-defined than the FX sweep play.
3. **NautilusTrader migration** — when HL bot needs phase 2 executor, write it on Nautilus instead of bespoke. Sets up v2 of Polymarket bot to share the same engine.
4. ~~FX sweep-reversal~~ — defer until #2 ships. 2-month build is too speculative vs the Topstep path's deterministic mechanics.
5. ~~Order-flow~~ — skip unless #1 fails and we have nothing better.

---

## Appendix: Deep-research v2 (2026-06-18)

Second-pass fan-out across web sources, adversarially verified. The headline: the Polymarket BTC/ETH/SOL short-binary latency-arb class is still the best-evidenced retail edge in mid-2026, but the window is closing, and Hyperliquid hourly funding remains the only structurally durable play. Everything else is either refuted, paywalled, or unevidenced.

### Verified strategies (still working mid-2026)

- **Polymarket BTC/ETH/SOL short-binary latency arb.** Comparator wallet `0x8dxd` turned $313 into ~$437k in one month at 98% win rate over 6,615 trades. Window compression is real: ~12.3s of stale-price arb in 2024 → ~2.7s in 2026. ~73% of profits now go to sub-100ms bots, and Polymarket has deployed dynamic taker fees targeting this class. Edge is alive but durability beyond 6–12 months is uncertain — treat it as a fast-decaying opportunity, not a moat.
- **Hyperliquid hourly funding vs CEX 8h.** Structural and durable. 0.01%/8h fixed interest floor (~11.6% APR base) plus 4%/hr cap on the variable component. Unchanged since v1; still the right phase-2 build target.
- **Polymarket maker rebates.** 25% on Sports / Politics / Finance / Weather, 20% on Crypto, paid daily in pUSD, $1 minimum payout, no volume floor. Open to any wallet, no application.
- **Loris Tools.** Normalizes funding across 14 perp DEXes but gates everything behind signup — falls outside the no-account constraint, so we built our own Drift + Paradex pollers instead.

### Refuted claims (do NOT pursue)

- "Kalshi↔Polymarket 1–5% typical spreads" — verified 0-for-3 against sources.
- "Kalshi fee tiers 7% / 5% / 3% / 1%" — verified 0-for-3.
- "$271k single-bot 30-day exploit" (predik.io) — verified 0-for-3, likely fabricated.
- "Hyperliquid 0.015% maker / 0.045% taker tiers" (eco.com) — verified 0-for-3. Pull the live fee schedule before sizing anything funding-related.

### Adjacent plays — research gaps, not graveyards

No public PnL evidence surfaced, but the structural setup is plausible. Worth a paper test, not a commit:

- Polymarket sports / politics / weather binaries — no latency-arb evidence found in v2.
- Kalshi↔Polymarket arb — four OSS repos exist, none publish PnL. ImMike explicitly calls the opportunity "rare and fleeting."
- Prediction-market vs sportsbook implied-prob arb.

### Fade list (well-documented but uneconomic for us)

- **HyperEVM HYPE/USDT0 ↔ Hyperliquid HYPE/USDC arb.** The two-brother $5M case from Q1 2026 is well-documented but now crowded post-publication: requires 100+ wallets, sub-2s execution, ~$1.2M gas budget. Capital-out-of-reach, not edge-dead.
- Generic CEX grid bots, BTC/ETH stat-arb pairs, vanilla Binance MM.

### Concrete next steps (v2 top-3)

1. **Polymarket maker-rebate quoter on Sports / Politics.** New build. 25% rebate, daily payout, no volume floor — fits the no-account constraint and runs on existing CLOB infrastructure.
2. **Cross-DEX funding extension to the HL monitor.** Loris was paywalled, so v2 shipped Drift + Paradex pollers instead. Paradex live-test on 2026-06-18 surfaced first opportunity events: −6.9 bps ETH and −7.4 bps SOL, both above the 5 bps fire threshold. Net PnL is negative under 1-cycle cost amortization but works out to ~73% APR if held 30 days. **Paradex funding-rate unit is UNVERIFIED — confirm before sizing.**
3. **Scale the Polymarket bot + add 15-min markets.** Mirrors `0x8dxd`'s universe. Deployed today (2026-06-18) as three new dry-run variants — see `polymarket/DEPLOY_NOTES.md`.

### Open questions v2 did NOT answer

- Widest persistent funding spread among Drift / Lighter / Paradex / Aster vs HL on a 30-day window.
- Whether Polymarket sports / politics shows the same price-lag the BTC/ETH/SOL bot exploits.
- Post-dynamic-fee maker-rebate net yield with $1k–$10k inventory on thin politics markets.
