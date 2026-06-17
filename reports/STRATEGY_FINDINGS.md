# Strategy findings — running log

A curated log of evidence-based findings about the Polymarket latency-arb
strategy. Each entry includes the data window, sample size, and the action
taken. Auto-generated daily reports live under `reports/<YYYY-MM-DD>.md`;
this file is the human-curated summary of what we *learned* from them.

---

## 2026-06-17 — Sweet-band edge is concentrated in [0.60, 0.75]

**Data window:** 2026-06-16 20:17 UTC → 2026-06-17 18:57 UTC (~22.5h)
**Sample:** 282 `ask_outside_sweet_band` skip rows across all 6 variants,
209 unique markets, 206 resolved via Polymarket gamma.

**Method:** counterfactual backtest of widened sweet bands. For each rejected
signal, look up the eventual market resolution and compute the PnL we would
have realized if the sweet-band ceiling were higher.
(`polymarket/backtest/sweet_band_counterfactual.py`)

**Result (per-bucket marginal PnL, $5/order, dry-run assumption):**

| Ask bucket | n | Win % | Total PnL | Avg PnL/fire |
|---|---|---|---|---|
| [0.30, 0.55) | 20 | ~40% | -$1 | noise |
| [0.55, 0.60) | 22 | 41% | **-$32** | **-$1.44** |
| **[0.60, 0.65)** | 24 | **71%** | **+$17** | **+$0.70** |
| **[0.65, 0.70)** | 34 | **82%** | **+$39** | **+$1.15** |
| **[0.70, 0.75)** | 21 | **76%** | **+$5** | **+$0.26** |
| [0.75, 0.80) | 22 | 77% | -$0.5 | -$0.02 |
| [0.80, 0.85) | 25 | 72% | -$15 | -$0.61 |
| [0.85, 0.90) | 28 | 89% | +$2.4 | +$0.09 |
| [0.90, 1.01) | 86 | 92% | -$17 | -$0.20 |
| **TOTAL** | 282 | 77% | -$1.48 | breakeven |

**Direction accuracy across all rejected signals: 77%** (218/282).
*(Compare yesterday's 44-sample preliminary read of 95% — that was small-sample noise.)*

**Why the edge lives in [0.60, 0.75]:**
- Below 0.60 the market's implied probability is below 50% but our signal
  doesn't beat that — we win ~40%, market pays for ~50% chance, no edge.
- In [0.60, 0.75] the market implies 60-75% and we win 70-82% — clean edge.
- Above 0.75 the market is mostly right (~75-90% win rate) but the payout
  per share shrinks faster than the win rate climbs, so PnL goes thin or
  negative.
- Above 0.85 even 91% win rate isn't enough — paying $0.92 to win $0.08 net
  means one loss wipes out 12 wins.

**Action taken:**
1. **Apply [0.60, 0.75] to all 6 main variants** (5m + 60m).
2. **Refocus `eth-5m-wide`** from [0.10, 0.70] to **[0.75, 0.90]** to gather
   more data on the tail bucket (current sample n=53 with mixed signals).
3. **Hourly variants:** sweet-band tweak likely irrelevant since their main
   blocker is `no_market_in_window` (95%+ of their skips). Apply the band
   change anyway for consistency; revisit hourly bots' structural issue
   separately (see below).
4. **Daily report generator added** (`polymarket/backtest/daily_report.py`)
   so we can track per-bot stats + config-on-the-day in `reports/<date>.md`.

**Caveats / unknowns:**
- Per-bucket samples (n=21-34 in the edge zone) are still small. The pattern
  is consistent but a week of data would firm up the conclusion.
- All PnL is on the **rejected** population. The unrejected ones (signals
  that DID fire under the original [0.30, 0.40] band) are a separate, much
  smaller sample (2 fires lifetime, both wins, +$16.71 dry-run combined).
- The backtest assumes full FOK fill at the recorded ask. Real fills at
  high asks (0.80+) may not fill fully due to shallow top-of-book.

**Live fire log (cumulative):**

| Date | Bot | Direction | Ask | Outcome | Theoretical PnL |
|---|---|---|---|---|---|
| 2026-06-15 | btc-5m | DOWN @ 0.39 | DOWN | WIN | +$7.82 |
| 2026-06-17 | eth-5m | UP @ 0.36 | UP | WIN | +$8.89 |
| **Total** | | 2/2 | | | **+$16.71** (dry-run) |

---

## 2026-06-16 — Hourly variants are structurally idle

**Data window:** 2026-06-16 20:17 → 2026-06-17 18:57 UTC (~22.5h)
**Sample:** 698 skips across btc-60m / eth-60m / sol-60m.

**Result:** 95-100% of hourly skips are `no_market_in_window` (662/698 across
the three bots). This is structural: hourly Polymarket markets only resolve
at the top of the hour, so most price-signal fires mid-hour have no live
hourly market with a window short enough to bet on.

**Action:** none for now — hourly bots stay running but produce essentially
no fires. Future work (deferred): schedule the hourly bots to wake up only
in the last ~10 min of each hour, or accept they're idle infrastructure.
The cost of running them dormant is minimal (no fires = no PnL impact).

---

## 2026-06-08 — Tighter sweet band, anchor-only EV-max (LEGACY)

(Pre-Phase B. Listed for history; superseded by 2026-06-17 finding above.)

Original config tested: `[SWEET_LO=0.30, SWEET_HI=0.45]` + window-anchor gate
+ snipe-window. Anchor-only no-snipe config (snipe=300, anchor required) was
chosen as EV-max per backtest n=63, 78% win, +$26.85. This is what shipped
to production until 2026-06-17.

The 2026-06-17 finding above invalidates the [0.30, 0.40] band specifically —
not because that band was wrong, but because the much higher [0.60, 0.75]
band turned out to capture an even stronger edge. Both bands MAY have edge;
the new band has higher EV.
