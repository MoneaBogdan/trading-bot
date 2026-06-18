"""Daily per-bot report.

Reads new-schema `bot=<variant>/<date>.jsonl` logs and produces a markdown
summary for a given UTC date — one report per day, written to
`reports/<YYYY-MM-DD>.md`. The report captures:

  * Effective config per bot (extracted from the boot event so we know
    EXACTLY what params were running that day, not "what's in code today").
  * Event counts (boot, skip, fire, crash) per bot.
  * Skip distribution by reason (so we see why bots didn't fire).
  * Fire details + resolved PnL (via polymarket gamma lookup) per bot.
  * A daily cumulative line so we can graph fires/PnL over time later.

Usage:
  python -m polymarket.backtest.daily_report --logs ~/trading-bot-logs --date 2026-06-17
  python -m polymarket.backtest.daily_report --logs ~/trading-bot-logs  # defaults to UTC today

The report is overwrite-safe: re-running it on the same date overwrites the
existing report with the latest data. Useful for end-of-day refresh after a
final rsync.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

GAMMA = "https://gamma-api.polymarket.com"
SIZE_USDC_ASSUMED = 5.0   # if size_usdc missing from a fire, assume this for PnL


def _ts_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _read_day(logs_dir: Path, date: str) -> dict[str, list[dict[str, Any]]]:
    """Return {variant: [event_rows...]} for the given date string."""
    out: dict[str, list[dict[str, Any]]] = {}
    for vdir in sorted(logs_dir.glob("bot=*")):
        variant = vdir.name[len("bot="):]
        path = vdir / f"{date}.jsonl"
        if not path.exists():
            continue
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if rows:
            out[variant] = rows
    return out


def _most_recent_boot(logs_dir: Path, variant: str, on_or_before: str) -> dict[str, Any] | None:
    """Find the most recent boot event for `variant` on or before `on_or_before`.
    Bots can run for days without rebooting, so today's file may have no boot
    row — fall back to earlier dates."""
    paths = sorted(logs_dir.glob(f"bot={variant}/*.jsonl"), reverse=True)
    for path in paths:
        date_str = path.stem  # "YYYY-MM-DD"
        if date_str > on_or_before:
            continue
        last_boot = None
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event") == "boot":
                    last_boot = row
        if last_boot:
            return last_boot
    return None


def _resolve_markets(condition_ids: set[str], date: str) -> dict[str, str]:
    """Look up market winners for a date window. Returns {cid: 'UP'|'DOWN'|'UNKNOWN'}."""
    if not condition_ids:
        return {}
    day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    out: dict[str, str] = {}
    client = httpx.Client(timeout=30.0)
    try:
        offset = 0
        while True:
            r = client.get(
                f"{GAMMA}/events",
                params={
                    "closed": "true",
                    "end_date_min": day.strftime("%Y-%m-%dT00:00:00Z"),
                    "end_date_max": (day + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z"),
                    "limit": 100, "offset": offset,
                    "order": "endDate", "ascending": "true",
                },
            )
            if r.status_code == 422:
                # gamma caps offset; treat as end-of-results
                break
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for e in data:
                for mk in e.get("markets", []):
                    cid = mk.get("conditionId")
                    if cid in condition_ids:
                        prices = mk.get("outcomePrices")
                        try:
                            up_p, down_p = json.loads(prices) if prices else (None, None)
                            up_p = float(up_p) if up_p is not None else None
                            down_p = float(down_p) if down_p is not None else None
                            if up_p is not None and up_p >= 0.99:
                                out[cid] = "UP"
                            elif down_p is not None and down_p >= 0.99:
                                out[cid] = "DOWN"
                            else:
                                out[cid] = "UNKNOWN"
                        except (ValueError, TypeError, json.JSONDecodeError):
                            out[cid] = "UNKNOWN"
            offset += 100
            if len(data) < 100:
                break
    finally:
        client.close()
    return out


def _config_brief(boot: dict[str, Any] | None) -> str:
    if not boot:
        return "_no boot event_"
    c = boot.get("config", {})
    asset = c.get("asset", "?")
    tf = c.get("timeframe_min", "?")
    parts = [
        f"asset={asset}",
        f"tf={tf}m",
        f"thresh={c.get('threshold_pct','?')}",
        f"sweet=[{c.get('sweet_lo','?')},{c.get('sweet_hi','?')}]",
        f"confirm={c.get('require_confirm','?')}",
        f"anchor={c.get('require_window_anchor','?')}",
        f"snipe={c.get('snipe_window_s','?')}",
        f"dry={c.get('dry_run','?')}",
    ]
    return " ".join(parts)


def _fire_pnl(fire: dict[str, Any], winner: str | None) -> tuple[float, str]:
    if winner is None or winner == "UNKNOWN":
        return 0.0, "unresolved"
    outcome = (fire.get("outcome_name") or "").upper()
    won = outcome == winner
    size = float(fire.get("size_usdc") or SIZE_USDC_ASSUMED)
    ask = float(fire.get("limit_price") or 0.0)
    if ask <= 0:
        return 0.0, "no_ask"
    filled = float(fire.get("filled_size") or (size / ask))
    pnl = (filled - size) if won else -size
    return pnl, ("WIN" if won else "LOSS")


def _market_context(date: str) -> dict[str, Any]:
    """Pull broad market context for the day from Polymarket gamma:
    UP vs DOWN resolution counts per asset/timeframe, sample size.

    This tells us what the markets DID, independent of what our bots did.
    Useful for sanity-checking ("our bot bet UP 3 times today, but BTC
    actually closed 67% UP markets — were we directionally right?").
    """
    day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    ctx: dict[str, Any] = {}
    client = httpx.Client(timeout=30.0)
    try:
        offset = 0
        events_seen = 0
        # Bucket by (asset, tf_min) → {up, down, unknown}
        buckets: dict[tuple[str, int], dict[str, int]] = defaultdict(
            lambda: {"up": 0, "down": 0, "unknown": 0})
        while True:
            r = client.get(
                f"{GAMMA}/events",
                params={
                    "closed": "true",
                    "end_date_min": day.strftime("%Y-%m-%dT00:00:00Z"),
                    "end_date_max": (day + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),
                    "limit": 100, "offset": offset,
                    "order": "endDate", "ascending": "true",
                },
            )
            if r.status_code == 422:
                break
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for e in data:
                title = e.get("title", "")
                # Match "<Asset> Up or Down - ..."
                low = title.lower()
                if " up or down " not in low:
                    continue
                asset = None
                for a in ("Bitcoin", "Ethereum", "Solana"):
                    if title.startswith(a):
                        asset = {"Bitcoin": "BTC", "Ethereum": "ETH", "Solana": "SOL"}[a]
                        break
                if not asset:
                    continue
                # TF detection from title: ranged events have "HH:MMAM-HH:MMAM"
                # (5m or 15m — compute duration), hourly events have just "HHPM ET".
                # gamma's startDate is event-creation time, not the betting window — don't use it.
                import re as _re
                _rng = _re.search(
                    r"(\d{1,2}):(\d{2})(AM|PM)-(\d{1,2}):(\d{2})(AM|PM)", title)
                if _rng:
                    _h1, _m1, _ap1, _h2, _m2, _ap2 = _rng.groups()
                    def _to_min(h, m, ap):
                        hh = int(h) % 12
                        if ap.upper() == "PM":
                            hh += 12
                        return hh * 60 + int(m)
                    _dur = (_to_min(_h2, _m2, _ap2) - _to_min(_h1, _m1, _ap1)) % (24 * 60)
                    tf = _dur if _dur in (5, 15) else _dur  # Why: keep raw duration so unknown slot cadences don't silently bucket as 5m
                else:
                    tf = 60
                events_seen += 1
                for mk in e.get("markets", []):
                    prices = mk.get("outcomePrices")
                    try:
                        up_p, down_p = json.loads(prices) if prices else (None, None)
                        up_p = float(up_p) if up_p is not None else 0
                        down_p = float(down_p) if down_p is not None else 0
                        if up_p >= 0.99:
                            buckets[(asset, tf)]["up"] += 1
                        elif down_p >= 0.99:
                            buckets[(asset, tf)]["down"] += 1
                        else:
                            buckets[(asset, tf)]["unknown"] += 1
                    except (ValueError, TypeError, json.JSONDecodeError):
                        buckets[(asset, tf)]["unknown"] += 1
                    break  # one market per event for Up/Down
            offset += 100
            if len(data) < 100:
                break
        ctx["buckets"] = dict(buckets)
        ctx["events_seen"] = events_seen
    finally:
        client.close()
    return ctx


def _news_context(date: str) -> dict[str, Any]:
    """Summarize headlines captured by news recorder for the day, if any."""
    ctx = {"total": 0, "by_source": {}, "top_titles": []}
    # Try several common locations for the news log.
    for candidate in (
        Path(os.path.expanduser("~/trading-bot-logs/news")),
        Path("logs/news"),
        Path("polymarket/logs/news"),
    ):
        path = candidate / f"{date}.jsonl"
        if not path.exists():
            continue
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        ctx["total"] = len(rows)
        ctx["by_source"] = dict(Counter(r.get("channel", "?") for r in rows).most_common(10))
        # Surface a few "trade-relevant looking" titles — content with all-caps source
        # prefixes or BREAKING markers.
        candidates = [r for r in rows if (r.get("text") or "").upper().startswith(
            ("BREAKING", "JUST IN", "DECRYPT", "BLOOMBERG", "REUTERS", "CFTC", "SEC",
             "FED", "ETF", "FORBES", "COINTELEGRAPH", "THE BLOCK", "COINDESK"))]
        ctx["top_titles"] = [
            (r.get("ts", "")[:19], r.get("channel", "?"), (r.get("text") or "")[:120])
            for r in candidates[:8]
        ]
        ctx["log_path"] = str(path)
        break
    return ctx


def _fire_cid(fire: dict[str, Any]) -> str | None:
    """Pull the condition_id from a fire row. live_trader.py logs it under
    `market_id` (despite the name, it's a 0x… condition hash). news_alpha_runner
    logs it explicitly as `condition_id`. Try both."""
    return fire.get("condition_id") or fire.get("market_id")


def _derive_direction_and_risks(
    data: dict[str, list[dict[str, Any]]],
    resolutions: dict[str, str],
    fires_by_variant: dict[str, list[dict[str, Any]]],
    daily_pnl: float,
    total_fire: int,
    total_skip: int,
    total_crash: int,
    most_recent_boots: dict[str, dict[str, Any] | None],
) -> tuple[list[str], list[str]]:
    """Compute auto-generated direction recommendations and risk flags.

    These are heuristics, not gospel — the human decides. The goal is to surface
    things worth thinking about, not to make decisions.
    """
    direction: list[str] = []
    risks: list[str] = []

    # ---- Direction ----

    if total_crash > 0:
        direction.append(f"🚨 **Investigate crashes first** — {total_crash} crash event(s) today. "
                         f"Don't change configs until the cause is understood; a crash that recurs "
                         f"after a config tweak makes the tweak hard to evaluate.")

    if total_fire == 0:
        direction.append("**No fires today across any bot.** Check: (a) are price feeds connecting "
                         "(Binance/Coinbase WS)? (b) is the WS-recorder writing orderbooks? "
                         "(c) was today a low-volatility day? If feeds look fine, this is just "
                         "the strategy's natural rarity — no action needed.")
    elif total_fire <= 2:
        direction.append(f"**Only {total_fire} fire(s) today.** Sample too small to draw conclusions "
                         "about config quality. Keep current settings and let data accumulate.")
    else:
        # Per-variant: any clearly winning or losing variant
        for variant, frs in fires_by_variant.items():
            resolved = [(f, resolutions.get(_fire_cid(f) or "")) for f in frs]
            resolved_known = [(f, w) for f, w in resolved if w in ("UP", "DOWN")]
            if not resolved_known:
                continue
            wins = sum(1 for f, w in resolved_known
                       if (f.get("outcome_name") or "").upper() == w)
            n = len(resolved_known)
            wr = wins / n
            pnl = sum(_fire_pnl(f, w)[0] for f, w in resolved_known)
            if n >= 5 and wr >= 0.70 and pnl > 0:
                direction.append(f"✅ **`{variant}` is performing well** ({wins}/{n} wins, "
                                 f"${pnl:+.2f}). If the trend holds for 3+ days, consider "
                                 f"raising `POLY_MAX_ORDER_USDC` for this variant only.")
            elif n >= 5 and wr <= 0.40:
                direction.append(f"⚠ **`{variant}` is underperforming** ({wins}/{n} wins, "
                                 f"${pnl:+.2f}). Inspect the fires — wrong direction signal? "
                                 f"Wrong sweet-band? Consider pausing this variant pending review.")

    # If PnL is positive but small, encourage patience
    if 0 < daily_pnl < 5:
        direction.append(f"PnL today (${daily_pnl:+.2f}) is small but positive — within "
                         "noise band of a single fire. Don't over-interpret.")

    # ---- Risks ----

    # Multiple boots today on the same bot → instability
    for variant, rows in data.items():
        boots = [r for r in rows if r.get("event") == "boot"]
        if len(boots) > 1:
            risks.append(f"⚠ **`{variant}` rebooted {len(boots)} times today.** Either a manual "
                         f"redeploy or unhealthy restarts. Check container logs for the cause.")

    # Unresolved fires
    pending = []
    for variant, frs in fires_by_variant.items():
        for fr in frs:
            cid = _fire_cid(fr)
            if not cid or resolutions.get(cid) in (None, "UNKNOWN"):
                pending.append((variant, fr.get("ts", "?")[:19]))
    if pending:
        risks.append(f"⏳ **{len(pending)} fire(s) not yet resolved by gamma** "
                     f"({', '.join(f'{v}@{t}' for v, t in pending[:3])}"
                     f"{'…' if len(pending) > 3 else ''}). Re-run this report after the markets "
                     f"close to capture their PnL.")

    # Anomalous skip concentration — could indicate broken feed
    for variant, rows in data.items():
        skips = [r for r in rows if r.get("event") == "skip"]
        if len(skips) < 20:
            continue
        reasons = Counter(s.get("reason", "?") for s in skips)
        top_reason, top_count = reasons.most_common(1)[0]
        if top_count / len(skips) > 0.95 and top_reason not in (
            "no_market_in_window",  # expected for hourly bots
        ):
            risks.append(f"⚠ **`{variant}` skip distribution dominated by `{top_reason}` "
                         f"({100*top_count/len(skips):.0f}%).** Either a single failure mode "
                         f"is happening over and over (broken feed?) or the band is set such "
                         f"that other gates rarely apply. Investigate.")

    # Config drift between variants of same asset (e.g., btc-5m and trader-btc-5m-wide
    # using contradictory sweet bands when they should target the same baseline)
    by_asset: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for variant in data.keys():
        boot = next((r for r in data[variant] if r.get("event") == "boot"), None)
        boot = boot or most_recent_boots.get(variant)
        if boot:
            cfg = boot.get("config", {})
            asset = cfg.get("asset")
            if asset:
                by_asset[asset].append((variant, cfg))
    for asset, items in by_asset.items():
        if len(items) <= 1:
            continue
        sweet_combos = {(it[1].get("sweet_lo"), it[1].get("sweet_hi")) for it in items}
        if len(sweet_combos) > 1:
            combos_str = ", ".join(f"{v}=[{lo},{hi}]" for v, cfg in items
                                   for lo, hi in [(cfg.get("sweet_lo"), cfg.get("sweet_hi"))])
            risks.append(f"📊 **`{asset}` bots running different sweet bands: {combos_str}.** "
                         f"This is intentional only if A/B testing — confirm.")

    # Daily cap proximity (informational)
    for variant, frs in fires_by_variant.items():
        boot = next((r for r in data[variant] if r.get("event") == "boot"), None)
        boot = boot or most_recent_boots.get(variant)
        if boot:
            cap = boot.get("config", {}).get("max_fires_per_day", 0)
            if cap and len(frs) >= 0.8 * cap:
                risks.append(f"📈 **`{variant}` near daily fire cap** ({len(frs)}/{cap}). "
                             f"If you expect more fires today, raise `NEWS_MAX_FIRES_PER_DAY` "
                             f"or `--max-daily-fires`.")

    if not direction:
        direction.append("_(no specific direction inferred — review numbers above.)_")
    if not risks:
        risks.append("_(no red flags detected.)_")

    return direction, risks


def _render(date: str, data: dict[str, list[dict[str, Any]]],
            resolutions: dict[str, str],
            most_recent_boots: dict[str, dict[str, Any] | None],
            market_ctx: dict[str, Any],
            news_ctx: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Daily report — {date}\n")
    lines.append(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.\n")
    lines.append("")

    # Header summary
    total_skip = total_fire = total_boot = total_crash = 0
    daily_pnl = 0.0
    fires_by_variant: dict[str, list[dict[str, Any]]] = {}
    for variant, rows in data.items():
        for r in rows:
            e = r.get("event")
            if e == "skip": total_skip += 1
            elif e == "fire":
                total_fire += 1
                fires_by_variant.setdefault(variant, []).append(r)
            elif e == "boot": total_boot += 1
            elif e == "bot_crashed": total_crash += 1
    for variant, frs in fires_by_variant.items():
        for fr in frs:
            cid = _fire_cid(fr)
            pnl, _verdict = _fire_pnl(fr, resolutions.get(cid) if cid else None)
            daily_pnl += pnl

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Variants reporting: **{len(data)}**")
    lines.append(f"- Total events: skips=**{total_skip}**, "
                 f"fires=**{total_fire}**, boots=**{total_boot}**, "
                 f"crashes=**{total_crash}**")
    lines.append(f"- Resolved fires PnL (sum across variants, $5/order assumed): "
                 f"**${daily_pnl:+.2f}** *(dry-run unless flagged)*")
    lines.append("")

    # ---- Direction + Risks (auto-derived, at the top so they're seen) ----
    direction, risks = _derive_direction_and_risks(
        data=data, resolutions=resolutions, fires_by_variant=fires_by_variant,
        daily_pnl=daily_pnl, total_fire=total_fire, total_skip=total_skip,
        total_crash=total_crash, most_recent_boots=most_recent_boots,
    )
    lines.append("## Direction (what to do tomorrow)")
    lines.append("")
    for d in direction:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("## Risks (what to watch)")
    lines.append("")
    for r in risks:
        lines.append(f"- {r}")
    lines.append("")

    # Per-variant block
    lines.append("## Per-bot detail\n")
    for variant in sorted(data.keys()):
        rows = data[variant]
        boots = [r for r in rows if r.get("event") == "boot"]
        skips = [r for r in rows if r.get("event") == "skip"]
        fires = [r for r in rows if r.get("event") == "fire"]
        crashes = [r for r in rows if r.get("event") == "bot_crashed"]

        lines.append(f"### `{variant}`\n")
        boot_for_config = boots[0] if boots else most_recent_boots.get(variant)
        boot_source = "today" if boots else (
            f"prior to today" if boot_for_config else "none"
        )
        lines.append(f"**Config running this day:** `{_config_brief(boot_for_config)}`"
                     f" *(from boot event {boot_source})*")
        if len(boots) > 1:
            lines.append(f"  *(⚠ {len(boots)} boots today — bot restarted; "
                         f"config above is from first boot)*")
        lines.append("")
        lines.append(f"- Events: skips=**{len(skips)}**, fires=**{len(fires)}**, "
                     f"crashes=**{len(crashes)}**")

        if skips:
            skip_counter = Counter(s.get("reason", "?") for s in skips)
            lines.append("- Skip reasons:")
            for reason, count in skip_counter.most_common():
                pct = 100 * count / len(skips)
                lines.append(f"    - `{reason}`: {count} ({pct:.0f}%)")

        if fires:
            lines.append("- Fires:")
            variant_pnl = 0.0
            for fr in fires:
                cid = _fire_cid(fr)
                winner = resolutions.get(cid) if cid else None
                pnl, verdict = _fire_pnl(fr, winner)
                if verdict not in ("no_ask", "unresolved"):
                    variant_pnl += pnl
                lines.append(
                    f"    - {fr.get('ts','?')[:19]} {fr.get('outcome_name','?')} "
                    f"@ {fr.get('limit_price','?')} → "
                    f"{verdict if verdict in ('WIN','LOSS') else verdict} "
                    f"({'**$%+.2f**' % pnl if verdict in ('WIN','LOSS') else '_pending_'})"
                )
                lines.append(f"      market: `{fr.get('market_title','?')}`")
            lines.append(f"- Variant PnL today: **${variant_pnl:+.2f}**")

        if crashes:
            lines.append(f"- ⚠ Crashes:")
            for c in crashes:
                lines.append(f"    - {c.get('ts','?')[:19]}: "
                             f"{c.get('error_type','?')}: {c.get('error','?')}")
        lines.append("")

    # ---- Market context ----
    lines.append("## Market context")
    lines.append("")
    buckets = market_ctx.get("buckets", {})
    events_seen = market_ctx.get("events_seen", 0)
    if not buckets:
        lines.append("_(no Polymarket Up/Down markets resolved today, or gamma lookup failed)_")
    else:
        lines.append(f"Polymarket Up/Down resolutions across all markets today "
                     f"(n={events_seen} events scanned):")
        lines.append("")
        lines.append("| Asset | TF | UP | DOWN | UP % |")
        lines.append("|---|---|---|---|---|")
        for (asset, tf), b in sorted(buckets.items()):
            n = b["up"] + b["down"]
            up_pct = (100 * b["up"] / n) if n else 0
            lines.append(f"| {asset} | {tf}m | {b['up']} | {b['down']} | "
                         f"{up_pct:.0f}% |")
        lines.append("")
        lines.append("> Reads the *market-implied direction* for the day — useful "
                     "for sanity-checking whether our bot's bias matched reality. "
                     "A 50/50 split is the long-run expectation; a strong skew often "
                     "means a trending day in the underlying.")
    lines.append("")

    # ---- News context ----
    lines.append("## News context")
    lines.append("")
    if news_ctx.get("total"):
        path = news_ctx.get("log_path", "")
        lines.append(f"News recorder captured **{news_ctx['total']}** headlines today "
                     f"(`{path}`).")
        if news_ctx.get("by_source"):
            lines.append("")
            lines.append("Top sources:")
            for src, n in news_ctx["by_source"].items():
                lines.append(f"- `{src}`: {n}")
        if news_ctx.get("top_titles"):
            lines.append("")
            lines.append("Notable headlines (filtered by source prefix):")
            for ts, src, text in news_ctx["top_titles"]:
                lines.append(f"- `{ts}` *{src}* — {text}")
    else:
        lines.append("_(news recorder not running or no headlines logged today)_")
    lines.append("")

    # ---- Manual notes scaffold ----
    lines.append("## Notes (manual append)")
    lines.append("")
    lines.append("_Edit this section by hand to add anything the script missed —_")
    lines.append("_macro events, exchange outages, strategy hypotheses you want_")
    lines.append("_to remember when reviewing this date later. Anything in this_")
    lines.append("_section is preserved if you regenerate the report (TODO — currently_")
    lines.append("_overwritten on re-run)._")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="~/trading-bot-logs",
                    help="dir containing bot=*/ trees")
    ap.add_argument("--date", default=None,
                    help="UTC date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--out-dir", default="reports",
                    help="where to write <date>.md")
    ap.add_argument("--push", action="store_true",
                    help="git add + commit + push the report after writing")
    args = ap.parse_args()

    logs_dir = Path(os.path.expanduser(args.logs))
    if not logs_dir.exists():
        print(f"logs dir not found: {logs_dir}", file=sys.stderr)
        return 1

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[daily_report] date={date} logs={logs_dir}")

    data = _read_day(logs_dir, date)
    if not data:
        print(f"[daily_report] no bot=*/{date}.jsonl files found")
        return 0
    print(f"[daily_report] {len(data)} variants reporting for {date}")

    # Gather resolution lookups for any fires
    cids = set()
    for rows in data.values():
        for r in rows:
            if r.get("event") == "fire":
                cid = _fire_cid(r)
                if cid:
                    cids.add(cid)
    resolutions = _resolve_markets(cids, date) if cids else {}
    print(f"[daily_report] resolved {sum(1 for v in resolutions.values() if v != 'UNKNOWN')}"
          f"/{len(cids)} fired markets via gamma")

    # For variants with no boot today, pull the most recent prior boot so we
    # always report the config that was actually running.
    most_recent_boots: dict[str, dict[str, Any] | None] = {}
    for variant in data.keys():
        has_boot_today = any(r.get("event") == "boot" for r in data[variant])
        if not has_boot_today:
            most_recent_boots[variant] = _most_recent_boot(logs_dir, variant, date)
        else:
            most_recent_boots[variant] = None

    # Market + news context (lookups go to the network — best-effort, swallow errors)
    try:
        market_ctx = _market_context(date)
        print(f"[daily_report] market context: {market_ctx.get('events_seen', 0)} events scanned")
    except Exception as e:
        print(f"[daily_report] market context failed: {type(e).__name__}: {e}")
        market_ctx = {}
    news_ctx = _news_context(date)
    if news_ctx.get("total"):
        print(f"[daily_report] news context: {news_ctx['total']} headlines from {news_ctx.get('log_path')}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date}.md"
    out_path.write_text(_render(date, data, resolutions, most_recent_boots,
                                market_ctx, news_ctx))
    print(f"[daily_report] wrote {out_path}")

    if args.push:
        import subprocess
        subprocess.run(["git", "add", str(out_path)], check=True)
        rc = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", str(out_path)]
        ).returncode
        if rc == 0:
            print(f"[daily_report] no changes to commit")
        else:
            subprocess.run(["git", "commit", "-m",
                            f"reports: daily report for {date}"], check=True)
            subprocess.run(["git", "push", "origin", "HEAD"], check=True)
            print(f"[daily_report] committed + pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
