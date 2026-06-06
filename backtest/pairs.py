"""Statistical-arbitrage data prep for cointegrated pairs.

We load two pairs (e.g. EUR/USD and GBP/USD) at the same granularity, align
them on timestamp, fit a hedge ratio with OLS, run an Engle-Granger
cointegration test (ADF on the residuals), and produce a synthetic "spread"
series. The backtest engine treats the spread as if it were a normal price
series — strategies plug into it as usual.

Hedge ratio is **static** (fit once on the full series). For production
you'd want a rolling fit, e.g. on a 60-day window, so the relationship can
evolve. We surface the static-fit caveat in `SpreadFit.warnings`.

PnL is approximately in account currency for USD-quoted majors (both legs
USD-quoted means the spread is in USD per unit). For cross-quote pairs or
non-USD-quoted instruments, multiply by a conversion factor — see
`leg_sizes()` for the per-leg translation needed to place real orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

from data import Source, load_candles


@dataclass
class SpreadFit:
    pair_a: str
    pair_b: str
    hedge_ratio: float       # β such that A ≈ β·B + α
    intercept: float
    correlation: float
    n_bars: int

    # Engle-Granger cointegration test on the spread residuals.
    adf_statistic: float
    adf_pvalue: float
    adf_critical_5pct: float
    is_cointegrated: bool    # adf_pvalue < 0.05

    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        coint_label = "✅ cointegrated" if self.is_cointegrated else "⚠️ NOT cointegrated"
        return (
            f"{self.pair_a} ~ β={self.hedge_ratio:.4f}·{self.pair_b} + α={self.intercept:.4f}  "
            f"corr={self.correlation:.3f}\n"
            f"  ADF: stat={self.adf_statistic:.3f}  p={self.adf_pvalue:.4f}  "
            f"crit(5%)={self.adf_critical_5pct:.3f}  →  {coint_label}"
        )

    def leg_sizes(self, spread_size: float) -> tuple[float, float]:
        """Translate a spread-unit size into actual leg sizes for live execution.

        A spread = price_A - β·price_B trade of size S means:
          - leg A: SAME direction, size S
          - leg B: OPPOSITE direction, size β·S
        So long-spread = long S of A, short β·S of B. Short-spread reverses both.
        """
        return spread_size, self.hedge_ratio * spread_size


def load_pair_spread(
    pair_a: str,
    pair_b: str,
    granularity: str,
    start: datetime,
    end: datetime,
    source: Source = "dukascopy",
) -> tuple[pd.DataFrame, SpreadFit]:
    """Return synthetic OHLC bars for the spread series + the hedge fit info.

    Output bar `close` = close_a - β·close_b. Open/high/low are derived by
    applying the same hedge to corresponding A/B OHLC values, then sorted
    so high >= close >= low remains true.
    """
    a = load_candles(pair_a, granularity, start, end, source=source)
    b = load_candles(pair_b, granularity, start, end, source=source)
    if a.empty or b.empty:
        empty_fit = SpreadFit(pair_a, pair_b, 0, 0, 0, 0, 0, 1, 0, False, ["no data"])
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"]), empty_fit

    merged = pd.merge(a, b, on="ts", how="inner", suffixes=("_a", "_b"))

    closes_a = merged["close_a"].to_numpy()
    closes_b = merged["close_b"].to_numpy()
    correlation = float(np.corrcoef(closes_a, closes_b)[0, 1])
    beta, alpha = np.polyfit(closes_b, closes_a, deg=1)

    residuals = closes_a - beta * closes_b - alpha
    # ADF: H0 = unit root (non-stationary). Reject => stationary residuals => cointegrated.
    adf_stat, adf_p, _, _, crits, _ = adfuller(residuals, autolag="AIC")
    crit_5 = float(crits["5%"])
    is_coint = bool(adf_p < 0.05)

    warnings: list[str] = []
    if not is_coint:
        warnings.append(f"spread residuals fail ADF test (p={adf_p:.4f}); pair may not be cointegrated")
    if abs(correlation) < 0.7:
        warnings.append(f"low price correlation ({correlation:.2f}); β may be unstable")
    warnings.append("static hedge ratio (full-series fit) — rolling β not yet implemented")

    spread_close = closes_a - beta * closes_b
    spread_open = merged["open_a"].to_numpy() - beta * merged["open_b"].to_numpy()
    # When β > 0, spread is high when A is high and B is low; flip if β < 0.
    if beta >= 0:
        spread_high = merged["high_a"].to_numpy() - beta * merged["low_b"].to_numpy()
        spread_low = merged["low_a"].to_numpy() - beta * merged["high_b"].to_numpy()
    else:
        spread_high = merged["high_a"].to_numpy() - beta * merged["high_b"].to_numpy()
        spread_low = merged["low_a"].to_numpy() - beta * merged["low_b"].to_numpy()

    out = pd.DataFrame({
        "ts": merged["ts"],
        "open": spread_open,
        "high": np.maximum(spread_high, spread_low),
        "low": np.minimum(spread_high, spread_low),
        "close": spread_close,
        "volume": (merged["volume_a"] + merged["volume_b"]).astype(int),
    })

    fit = SpreadFit(
        pair_a=pair_a, pair_b=pair_b,
        hedge_ratio=float(beta), intercept=float(alpha),
        correlation=correlation, n_bars=len(out),
        adf_statistic=float(adf_stat),
        adf_pvalue=float(adf_p),
        adf_critical_5pct=crit_5,
        is_cointegrated=is_coint,
        warnings=warnings,
    )
    return out, fit
