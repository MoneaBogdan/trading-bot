# Crypto Trading Strategies — 2026 Expansion Research

Source-cited research report for expanding the working Polymarket BTC Up/Down latency-arb bot. Generated 2026-06-16.

## Strategy Table (12 playbooks, ranked)

### 1. Polymarket ETH/SOL "Up or Down" hourly — same bot, new tickers — **FRESH/LIVE**
- **Trigger**: Identical to BTC logic — Binance 60s ETH (or SOL) return ≥ threshold, Coinbase cross-confirm, ask in $0.30–$0.40 band, window-open anchor. Threshold needs re-fit per asset (ETH ≈ 0.13%, SOL ≈ 0.20% — their 60s realized vol is higher).
- **Edge**: Polymarket resolves ETH/SOL hourlies from Binance USDT spot pairs ([polyesc.xyz](https://polyesc.xyz/blog/polymarket-crypto-bucket-markets)) and the same retail-tourist counterparty pool funds the trade. Hourly buckets listed at [polymarket.com/crypto/hourly](https://polymarket.com/crypto/hourly).
- **Infra**: ~zero new — add two more WS subscriptions, reuse the Polymarket CLOB client. Latency budget identical (<300 ms).
- **Capital min**: $5–$50 per fire, same as BTC.
- **Evidence**: ETH/SOL bucket books thinner than BTC's but 4–5 figures deep — fillable at small clip sizes. Polymarket weekly volume hit $1B early 2026, crypto = 20% of mix ([pewresearch.org](https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/)).
- **Failure modes**: Lower volume → more stale-book gaps; ETH/SOL momentum mean-reverts differently than BTC, don't assume win-rate carries.
- **Crowdedness**: **Fresh** — the specific 60s-momentum + ask-band combo unlikely to be saturated outside BTC.

### 2. Polymarket × Kalshi BTC-hourly direct arb — **LIVE but tightening**
- **Trigger**: For same hourly BTC strike, `cost = Polymarket("Up") + Kalshi("No, BTC ≤ strike")`. If `cost < $1 − fees − slippage` (~2 cents headroom net), fire both legs.
- **Edge**: Kalshi taker fees ~1.2% vs Polymarket Global's zero taker create persistent 1.75–2.5¢ gross-spread requirement; arbs appear when Kalshi US-hour conservative flow vs Polymarket crypto-native flow diverge ([laikalabs.ai](https://laikalabs.ai/prediction-markets/polymarket-kalshi-arbitrage-guide)).
- **Infra**: Add Kalshi API (US KYC required; fixed-point dollar strings since March 2026, [quantvps.com](https://www.quantvps.com/blog/how-to-setup-kalshi-trading-bot)). Pre-positioned USDC on Polymarket + USD on Kalshi.
- **Capital min**: $500 each side. Below that the $1.20-ish Kalshi taker eats the edge.
- **Evidence**: Open-source scanners exist ([CarlosIbCu](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot), [WSOL12](https://github.com/WSOL12/Polymarket-Kalshi-Arbitrage-Trading-Bot-BTC)); none publish PnL. Cross-platform arbs "persist for minutes, not seconds" ([financemagnates.com](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/)).
- **Failure modes**: Leg-fill risk; Kalshi slower API; resolution-source mismatch (CME CF BRR vs Binance — small wick risk).
- **Crowdedness**: **Live** — many scanners, few executors due to dual-KYC + capital-prep friction.

### 3. Polymarket maker rebates + liquidity rewards stacking — **FRESH (post-Apr-2026)**
- **Trigger**: Post symmetric limit orders inside reward-eligible spread (<3¢ off mid) on high-volume markets; rebalance every N minutes.
- **Edge**: CLOB v2 launched Apr 28 2026 with $1M rewards program; makers pay zero fees, earn 20–25% of taker fees as PUSD rebates ([crypto.news](https://crypto.news/polymarket-rolls-out-clob-v2-with-1m-liquidity-rewards-to-harden-prediction-markets/), [medium.com/mountain-movers](https://medium.com/mountain-movers/the-hidden-yield-layer-on-polymarket-how-maker-rebates-holding-rewards-and-liquidity-incentives-e2e41972dcb7)). Estimated **1–4% monthly stacked yield** on $10k.
- **Infra**: Polymarket CLOB client + cancel-replace logic; robust quote management. No new exchanges.
- **Capital min**: $5k floor — below that economics get thin.
- **Failure modes**: **Adverse selection is catastrophic** — binary markets "can gap from 55¢ to 2¢ instantly… a single event can erase weeks of spread income" ([startpolymarket.com](https://startpolymarket.com/strategies/market-making/)). Mandatory: news-kill switch + hard inventory caps.
- **Crowdedness**: **Fresh** — program <2 months old; pros onboarding, not saturated.

### 4. Polymarket sports MM — quiet windows on long-duration events — **LIVE**
- **Trigger**: Quote both sides on World Cup / NBA championship outcomes 1–4 weeks out, $1–3¢ spread, only when no game/news within 12h.
- **Edge**: Sports = 39% of Polymarket volume ([pewresearch.org](https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/)). Long-duration events stack all 3 reward lanes.
- **Infra**: Same Polymarket stack + free ESPN API for news-kill switch.
- **Capital**: $2k–$20k.
- **Crowdedness**: **Live** — sportsbook-arb funds present, spreads still wide off-event.

### 5. Hyperliquid funding-rate basis trade (cash-and-carry) — **LIVE, modest yield**
- **Trigger**: When HL perp funding APR > 12% on a coin you can spot-hedge (BTC/ETH on a CEX), short the perp on HL, long spot on Binance. Close <5%.
- **Edge**: Q2 2026 funding compressed to "high single digits" after late-2024 highs; specific-coin spikes persist ([arbitrageghost on Medium](https://arbitrageghost.medium.com/funding-rate-arbitrage-in-2026-the-complete-guide-with-real-calculations-40e6cf341e52)). Realistic net **8–15% APY** after fees on $10k. The 30%+ figures cited elsewhere are pre-fee, best-case.
- **Infra**: Hyperliquid SDK + Binance API + inventory tracker. No latency requirement.
- **Capital min**: $5k. Below that, $5–15 transfer/withdraw fees per cycle eat the carry — need deltas held 5–7+ days to break even.
- **Failure modes**: 40–60% of months see negative funding stretches bleeding 2–5% ([tv-hub.org](https://www.tv-hub.org/guide/market-neutral-strategy-crypto)); liquidation risk; HL venue risk.
- **Crowdedness**: **Crowded on BTC/ETH**, **Live on mid-caps**.

### 6. Hyperliquid HLP deposit (passive vault MM) — **LIVE, low effort**
- **Trigger**: Deposit USDC to HLP, withdraw when net APR <10% trailing-30d.
- **Edge**: HLP runs MM + backstop liquidations, produces **15–35% annualized** ([vaasblock.com](https://www.vaasblock.com/news/hyperliquid-hlp-vault-economics-perp-dex-2026/), [dextools.io](https://www.dextools.io/tutorials/what-is-hyperliquid-hlp-vault-strategy-guide-2026)). Drawdowns 5–12%.
- **Infra**: None — one deposit. Counts as "always-firing" idle yield bucket.
- **Capital min**: $100.
- **Failure modes**: Black-swan liquidation losses, HL solvency risk, lockup mechanics.
- **Crowdedness**: **Crowded** but yield real (it's literally taker flow).

### 7. Hyperliquid leaderboard "alpha-vault" copy-trade — **LIVE, picky**
- **Trigger**: Sub-vault with ≥9mo track record, Sharpe >2, low BTC correlation, AUM <$5M. Deposit a slice; re-eval monthly.
- **Edge**: Public leaderboard with live positions creates verifiable track-record edge not available on CEXes ([eco.com](https://eco.com/support/en/articles/15197987-hyperliquid-vault-strategies-2026-hlp-and-user-vaults-explained)).
- **Failure modes**: Survivorship bias; some "pro" sub-vaults wash-traded for marketing.
- **Crowdedness**: **Live**.

### 8. Polymarket geopolitical / news-driven daily markets — slow latency arb — **FRESH**
- **Trigger**: Major wire (Reuters/AP) headline materially affecting a Polymarket binary → check whether Polymarket bid has moved within ~30s. Lag >15s → take.
- **Edge**: Polymarket = global crypto-native flow, slower to digest English-language wire than political news desks. Political markets = 32% of Polymarket volume ([pewresearch.org](https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/)). Short-dated geopolitical markets paying up to $5k/day in LP rewards (Iran-deal example, Mountain Movers).
- **Infra**: NewsAPI / Reuters feed + NLP keyword router + Polymarket client.
- **Capital min**: $500.
- **Crowdedness**: **Fresh** for niche markets, **Crowded** for big-name politics.

### 9. CEX–DEX implied-funding arb (Hyperliquid vs Binance perps) — **LIVE**
- **Trigger**: BTC perp basis between HL vs Binance >30 bps annualized in funding-equivalent → short rich / long cheap.
- **Edge**: Different MM populations; HL is largest onchain perps venue ([dextools.io](https://www.dextools.io/tutorials/what-is-hyperliquid-onchain-perps-guide-2026)) but doesn't always have CEX-tight basis.
- **Capital min**: $2k.
- **Crowdedness**: **Live** — HFT funds run this; mid-cap perps still have retail edge.

### 10. Kalshi non-crypto event-edge (weather/macro/sports) — **LIVE, off-piste**
- **Trigger**: Use Octagon/Tavily-style research stack ([OctagonAI/kalshi-trading-bot-cli](https://github.com/OctagonAI/kalshi-trading-bot-cli)) to compute own probability; fire when |yours − market| >5¢.
- **Edge**: Most Kalshi flow is sports retail; weather and macro are thinner with quantifiable priors.
- **Crowdedness**: **Fresh** in macro, **Crowded** in NFL.

### 11. JIT/v4-hooks LPing — **AVOID / Fading for retail**
- By 2026 "the line between passive investor and MEV bot has blurred"; protected pools with withdrawal delays neuter retail JIT ([academy.exmon.pro](https://academy.exmon.pro/future-of-liquidity-uniswap-v4-hooks-vs-jit-mev-attacks)). Skip unless you have Flashbots-grade builder relationships.

### 12. CEX–CEX spot arb (Binance ↔ Coinbase ↔ OKX) — **AVOID / Crowded**
- Spreads mostly <5 bps on majors; transfer windows kill the trade. "Fees, slippage and transfer time are the three biggest killers" ([cryptowisser.com](https://www.cryptowisser.com/guides/arbitrage-dexs-cexs-cross-chain-bridges)). Only works pre-positioned on mid-cap listings.

## Extensions of the current Polymarket BTC bot

**Other-asset hourlies exist.** Polymarket runs hourly "Up or Down" for ETH and other majors per the crypto bucket page; resolution = Binance USDT spot ([polyesc.xyz](https://polyesc.xyz/blog/polymarket-crypto-bucket-markets), [polymarket.com](https://polymarket.com/crypto/hourly)). Crypto = 20% of Polymarket volume — ~$1.8B/wk in April 2026 ([pewresearch.org](https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/)). Plenty of depth for $5–$50 clips. **Port to ETH first** (highest non-BTC liquidity, identical resolution stack), then SOL with re-fitted return threshold.

**Kalshi port.** Kalshi runs BTC, ETH, SOL, XRP hourlies ([quantvps.com](https://www.quantvps.com/blog/how-to-setup-kalshi-trading-bot)). Same logic ports but: (a) ~1.2% taker fee shifts the ask band wider, (b) US KYC required, (c) fixed-point string format changed March 2026 — serializer needs updating.

**Adjacent Polymarket markets.** Daily crypto-close markets show same retail-tourist flow on 24h window; 60s-momentum won't translate but **ask-band + book-imbalance** half of the stack will. Test single-token tail strikes ("BTC ≥ $X by Friday") in the 24–48h pre-resolution window where spreads widen ([academy.exmon.pro](https://academy.exmon.pro/prediction-market-arbitrage-polymarket-kalshi-strategy)).

## What's actually working in 2026 — honest section

- **Funding-rate arb (CEX delta-neutral)**: alive, **8–15% net APY** on $10k+ — not 30%+. 40–60% of months go negative ([tv-hub.org](https://www.tv-hub.org/guide/market-neutral-strategy-crypto)). Below $5k, fees eat carry.
- **CEX–CEX spot arb on majors**: effectively dead for retail. Bots dominate sub-second windows; only mid-cap new listings + pre-positioned capital have edge ([cryptowisser.com](https://www.cryptowisser.com/guides/arbitrage-dexs-cexs-cross-chain-bridges)).
- **JIT LPing Uniswap v4**: fading for retail. Hooks marketplace + withdrawal delays shut out fast LPs ([academy.exmon.pro](https://academy.exmon.pro/future-of-liquidity-uniswap-v4-hooks-vs-jit-mev-attacks)).
- **Hyperliquid HLP deposit**: working at 15–35% APR ([vaasblock.com](https://www.vaasblock.com/news/hyperliquid-hlp-vault-economics-perp-dex-2026/)).
- **Polymarket maker rewards**: **Fresh** post-CLOB-v2; the 20–25% taker-fee rebate is the most underpriced retail-accessible yield right now ([crypto.news](https://crypto.news/polymarket-rolls-out-clob-v2-with-1m-liquidity-rewards-to-harden-prediction-markets/)).
- **Institutional context**: Jane Street/Susquehanna/Jump are in but hold <5% of OI; depth caps at $10–50M per contract leave retail edge intact ([medium.com/julia_innovator](https://medium.com/@julia_innovator/chapter-5-prediction-markets-in-2026-hedge-funds-are-here-but-you-cant-see-them-a5946b6477e3)).

## First 3 to build (complementing the BTC bot)

**Build #1 — ETH/SOL clone of the current bot (1–3 days).** Same time horizon, same stack, different signal because ETH/SOL 60s realized vols differ — re-fit threshold, ask-band, anchor T-N per asset. Lowest marginal effort, doubles-to-triples fire frequency. MVP: copy `recorder.py`, add two WS subs, parameter sweep over 30 days of cached Polymarket order books.

**Build #2 — Polymarket maker-rebate LPer on crypto hourlies (1–2 weeks).** Different time horizon (minutes-to-hours hold), uncorrelated to directional latency bet, same Polymarket auth stack. MVP: quote ±2¢ off mid on BTC/ETH/SOL hourlies with $1k inventory, cancel-replace every 30s, hard kill on Binance >0.3% move in 60s. Track rebate accrual separate from spread PnL.

**Build #3 — Polymarket × Kalshi BTC hourly arb (2–4 weeks).** Hours-long opportunity windows, uncorrelated to crypto direction. MVP: Kalshi sandbox API first, then $500 each side, scan overlapping hourly strikes, fire when `cost < $0.97`. Latency budget loose (minutes). Unlocks structurally different counterparty pool.

## Sources read

- [polyesc.xyz](https://polyesc.xyz/blog/polymarket-crypto-bucket-markets) — Polymarket runs hourly Up/Down for BTC, ETH, SOL, XRP; Binance USDT resolution.
- [polymarket.com/crypto/hourly](https://polymarket.com/crypto/hourly) — Multi-asset hourly buckets confirmed.
- [github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot) — Reference scanner; no published PnL.
- [laikalabs.ai](https://laikalabs.ai/prediction-markets/polymarket-kalshi-arbitrage-guide) — Needed gross spread 1.75–2.5¢; pool-divergence edge.
- [medium.com/mountain-movers](https://medium.com/mountain-movers/the-hidden-yield-layer-on-polymarket-how-maker-rebates-holding-rewards-and-liquidity-incentives-e2e41972dcb7) — 1–4% monthly on $10k stacking rewards.
- [crypto.news](https://crypto.news/polymarket-rolls-out-clob-v2-with-1m-liquidity-rewards-to-harden-prediction-markets/) — Apr 28 2026 CLOB v2 + $1M rewards.
- [arbitrageghost on Medium](https://arbitrageghost.medium.com/funding-rate-arbitrage-in-2026-the-complete-guide-with-real-calculations-40e6cf341e52) — Real net funding APY 10–30%, requires sustained deltas.
- [tv-hub.org](https://www.tv-hub.org/guide/market-neutral-strategy-crypto) — 40–60% of months go negative on funding.
- [vaasblock.com](https://www.vaasblock.com/news/hyperliquid-hlp-vault-economics-perp-dex-2026/) — HLP 15–35% APR with 5–12% drawdowns.
- [medium.com/@julia_innovator](https://medium.com/@julia_innovator/chapter-5-prediction-markets-in-2026-hedge-funds-are-here-but-you-cant-see-them-a5946b6477e3) — Institutions <5% OI; depth $10–50M/contract.
- [pewresearch.org](https://www.pewresearch.org/short-reads/2026/05/27/trading-volume-on-prediction-markets-has-soared-in-recent-months/) — Combined volume <$5B → ~$24B/mo Sep25→Apr26; crypto = 20% of Polymarket.
- [startpolymarket.com](https://startpolymarket.com/strategies/market-making/) — Adverse-selection dominates MM PnL.
- [financemagnates.com](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/) — Cross-platform arbs persist for minutes.
- [academy.exmon.pro](https://academy.exmon.pro/future-of-liquidity-uniswap-v4-hooks-vs-jit-mev-attacks) — JIT closed off to retail in Uniswap v4.
- [cryptowisser.com](https://www.cryptowisser.com/guides/arbitrage-dexs-cexs-cross-chain-bridges) — CEX–CEX spot arb hostile to retail.

## Caveats

- **Polymarket reward economics** post-CLOB-v2 (<2 months of data) — 1–4% monthly is modeled, not audited. Build small, measure on own books.
- **Funding-arb APY** 8% to 30%+ across sources; safe planning number is 8–15% net.
- **HLP returns 15–35%** is wide — actual quarterly figures from on-chain dashboards should be checked at deposit time.
- **CarlosIbCu/WSOL12 arb bots** publish no live PnL — "edge persists" claim relies on practitioner figure (~2¢ gross spread); validate with paper run before risking $500/side.
- **Sources** are mostly Medium/blog — treat single-author claims as hypotheses until own dry-run confirms.

## Follow-up research log

Add findings here as we iterate on specific strategies. Each entry: date — strategy — source — finding.

### 2026-06-16 — Live data validates the research diagnosis

After 19 hours of live dry-run, the observations matched the research's structural claims:

- **Polymarket binary pricing is discrete** — asks clustered at 0.71–0.99 (priced in) or 0.46–0.69, with **zero asks in [0.41, 0.49]**. Matches startpolymarket.com / Mountain Movers ("gap from 55¢ to 2¢ instantly"). The [0.30, 0.40] sweet band is correctly placed in the rare-mispricing zone — won't be fixed by BTC-only tuning.
- **Fire rate ceiling confirmed** — 1 fire in 19h on BTC is below the backtest's 1/5h but consistent with [financemagnates.com](https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/)'s "mispricings persist for minutes, not seconds" — long enough to capture, not frequent enough for the band to fill repeatedly.
- **Diversifying signals > tuning BTC harder.** The 3-build shortlist is the right plan:
  - **#1 ETH/SOL clones** — higher 60s realized vol → more candidates in [0.30, 0.40]
  - **#2 Maker-rebate LPer** — direct fix for "always something firing" (fires per minute, not per 5h)
  - **#3 Polymarket × Kalshi arb** — uncorrelated to BTC direction entirely
- **Open question:** 31% of signals rejected by Coinbase confirm filter — all near-threshold (binance=±0.100%, coinbase=±0.085–0.099%). Need to know Polymarket's actual resolution feed before deciding to relax this. Search in progress.

### 2026-06-16 — Polymarket BTC resolution feeds (answered)

Critical finding via targeted WebSearch:

- **Hourly "Up or Down" markets** → **Binance BTC/USDT only**. Verbatim from current market page: *"The resolution source for this market is information from Binance, specifically the BTC/USDT pair. The close 'C' and open 'O' displayed at the top of the graph for the relevant '1H' candle will be used once the data for that candle is finalized."* Single-venue, candle open vs close. [polymarket.com hourly example](https://polymarket.com/event/bitcoin-up-or-down-april-18-2026-7am-et).
- **5-minute "Up or Down" markets** → **Chainlink BTC/USD low-latency data stream** ([data.chain.link/streams/btc-usd](https://data.chain.link/streams/btc-usd)) — a multi-venue weighted aggregate including **Coinbase, Binance, Kraken, Bitstamp**. Resolution = price at window start vs window end. Confirmed: [mlq.ai](https://mlq.ai/news/polymarket-introduces-5-minute-bitcoin-price-prediction-market/), [coinmarketcap.com](https://coinmarketcap.com/academy/article/polymarket-debuts-5-minute-bitcoin-prediction-markets-with-instant-settlement).

**Implication for our bot (currently on 5-min markets):**

- **Keep the Coinbase filter tight.** Chainlink includes Coinbase, so Coinbase divergence is a real resolution-risk signal, not noise. The 31% rejection rate is doing useful work.
- **If we extend to hourly markets:** the filter could be dropped or relaxed to a directional-only sanity check (e.g., same sign + ≥0.03%) since Coinbase is not in the hourly resolution path.
- **Resolution sources can change.** Polymarket has used different feeds historically; re-check the rules section on each specific market page before deploying any new variant.

### 2026-06-16 — Polymarket market inventory (verified live)

Confirmed against the Gamma API while building the multi-variant refactor:

- **All three assets we care about (BTC, ETH, SOL) have active Up/Down markets** in both 5-min and hourly windows. 5-min markets appear ~10 ahead at any time; hourly markets appear 1 ahead.
- **Other assets observed live**: BNB, Dogecoin (likely more — XRP per [polyesc.xyz](https://polyesc.xyz/blog/polymarket-crypto-bucket-markets)). Worth a future expansion if BTC/ETH/SOL variants prove out.
- **15-min windows also exist** (e.g., `"4:15AM-4:30AM ET"`) — different liquidity profile per the original `discover_btc_markets` doc comment. Could be a third timeframe variant if needed.
- **Hourly title format is single-time** (`"Bitcoin Up or Down - June 16, 5AM ET"`), not start-end like 5-min. Required a separate regex in `gamma.py`. The `end_dt` resolves at the *end* of the named hour (i.e., `5AM ET` market ends at 6AM ET / 11AM UTC).

### 2026-06-16 — Phase 1 deployed (6 variants live in repo, server redeploy pending)

Shipped multi-variant code as commit `955976b`:

- 6 docker services (BTC/ETH/SOL × 5m/1h), 1 shared WS recorder
- Per-asset thresholds picked from research vol estimates: BTC 0.10%, ETH 0.13%, SOL 0.20% — **will need to be re-fit from actual 30-day data** once we collect it
- Coinbase confirm filter **disabled by default for hourly variants** per the resolution-feed finding above
- Legacy `live_<date>.{log,jsonl}` filenames preserved for BTC-5m so historical logs and downstream tooling keep working
- Old function aliases (`discover_btc_markets`, `stream_btc_trades`) retained — no breakage in monitor/recorder/test scripts

Open items to validate after server redeploy:
- Do all 6 variants subscribe and start logging?
- Are the ETH/SOL fire rates higher than BTC (predicted: 2–3× more candidates due to vol)?
- Does the hourly Coinbase-confirm-disabled variant fire more often than the 5-min one?
- Per-asset threshold re-fit — schedule a backtest pass once each variant has 30 days of data.

### 2026-06-16 — New angles round 2: information-edge sources

Round-2 web search avoided everything already covered. Top findings:

**Tree of Alpha news websocket** ([treeofalpha.com](https://www.treeofalpha.com/)) — the only news-latency feed with persistent 2026 edge. Aggregates 1000+ sources + 2000+ Twitter accounts; beats raw Twitter by 200ms–2s on listings, hacks, macro headlines. Used by every serious market-maker desk and Hyperliquid sniping bots. Free Telegram tier exists but throttled; websocket API ~$50–200/mo (undisclosed, DM them). The edge isn't the feed itself — it's pre-positioning Polymarket limit orders keyed to known headline triggers (Fed speakers, ETF flow reports, BLS releases) before the panic-orders that follow. Build #4 candidate: ~1 week MVP.

**Polymarket whale tracking — skip the vendors.** Polywhaler, PolySharks, polymarket-whales.xyz all repackage on-chain data with 5-minute polling delays. Polymarket positions are public Polygon ERC-1155 balances; a 100-line Python indexer beats every paid tier on latency. Real edge exists only in: (a) copying known-sharp wallets within seconds on illiquid markets, (b) fading retail copy-traders after Telegram pings. Vendor-reported PnL ("+8.4% weighted, 63.2% win rate" — PolySharks) is gross-of-fees marketing — ignore. Build #5 candidate: ~2 days; subscribe to the CTF Exchange's `OrderFilled` event filtered by a hand-curated wallet list (top 50 by realized PnL).

**DefiLlama derivatives endpoints** ([defillama.com](https://defillama.com)) — most underused free dataset in crypto. Perp OI, funding, volume by venue, no rate limits worth mentioning, Python wrapper available. Cross-reference with Polymarket BTC: when aggregated funding diverges >2σ from Polymarket's implied direction → fade signal. Build #6 candidate: ~2 days, worst case it's useful telemetry.

**Sentiment feeds are mostly dead in 2026.** January 2026: X revoked API access for "apps that pay users to post"; Kaito sunsetted Yaps, lost 157k yappers, KAITO -17% on news ([coindesk.com](https://www.coindesk.com/business/2026/01/15/kaito-to-sunset-yaps-as-x-cracks-down-on-infofi-apps-token-falls-17)). The underlying X data quality degraded across the entire sector (LunarCrush, Santiment, Glassnode social). For our use case: **skip entirely.**

**Polymarket alternatives with obscurity edge**: Azuro / SX Bet (decentralized sports — Polymarket's NBA/NFL markets often 2–4¢ wider here), Insight Prediction (long-tail political, $500 order can be top-of-book), OG.com / DraftKings Predictions (CFTC-regulated but tight spreads — no edge). PredictIt is dead; Kalshi ate that demand.

**Underused on-chain tools**: Arkham Pro $24.99/mo + Telegram alerts ([bitget.com](https://www.bitget.com/news/detail/12560605205721), single-source pricing flag) — cheap if you have specific wallets to tag. Nansen Smart Money labels are now table stakes (gone as standalone edge; need second-derivative). Parsec overpriced for retail unless trading options.

**Alpha-leak accounts to follow** (post real PnL/backtests, not shilling): Quant Journey ([quantjourney.substack.com](https://quantjourney.substack.com/)), angusslq ([angusslq.substack.com](https://angusslq.substack.com/p/heading-to-2026-with-5-quant-trading)), Quant Arb ([substack.com/@quantarb](https://substack.com/@quantarb)), SetupAlpha ([setup4alpha.substack.com](https://setup4alpha.substack.com/p/my-scientific-workflow-for-generating-alpha)).

**Ranked next-sprint priorities** from this round:
1. Tree of Alpha + headline-bucketed Polymarket pre-positioning (~1 week MVP, real edge)
2. Self-indexed whale wallet feed via Polygon node (~2 days)
3. DefiLlama derivatives funding-divergence signal (~2 days)
