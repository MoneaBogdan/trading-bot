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

