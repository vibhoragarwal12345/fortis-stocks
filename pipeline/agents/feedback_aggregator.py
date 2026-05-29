"""
Step 7.5 -- feedback aggregator.

Daily rollup of dashboard_events into user_action_summary, plus the signal-
value diagnostics that will eventually feed weight_retrainer.

Three entry points:

    daily_rollup(date)
        Aggregates one day's dashboard_events into one row per active user
        in user_action_summary (UPSERT on (user_id, summary_date)).

    identify_signal_value(lookback_days=90)
        For each ranking weight category (news, options, sec, ...), reports
            click-rate    -- fraction of picks where that category was the
                             top contributor and the pick was clicked
            avg_alpha_20d -- mean 20-day alpha vs SPY on those picks
        Useful answers: which signals actually correlate with advisor
        attention and which correlate with positive outcomes.

    correlate_signals_to_outcomes(min_outcome_age_days=30)
        Spearman correlation between each category's raw score and the
        pick's forward alpha at 5d / 20d / 60d, on picks where the outcome
        is mature enough to trust.

Usage:
    python -m pipeline.agents.feedback_aggregator daily [YYYY-MM-DD]
    python -m pipeline.agents.feedback_aggregator signal-value [LOOKBACK_DAYS]
    python -m pipeline.agents.feedback_aggregator correlate [MIN_AGE_DAYS]
    python -m pipeline.agents.feedback_aggregator all
"""

from __future__ import annotations

import json
import logging
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# Make `from config import ...` work when invoked as `python -m pipeline.agents.X`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

log = logging.getLogger("feedback_aggregator")
logging.basicConfig(format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S", level=logging.INFO)


# ── Constants ─────────────────────────────────────────────────────────────
PROPOSALS_DIR = Path(__file__).resolve().parent.parent / "proposals"
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

# Engagement score formula, total 100. Each component is capped so that an
# unusually active outlier doesn't dominate the cohort.
SCORE_WEIGHTS = {
    "reports_opened":          (10, 30),   # (points each, max contribution)
    "picks_clicked":           ( 5, 25),
    "expansions_any":          ( 5, 15),
    "explicit_feedback_count": (10, 20),
    "ticker_searches":         ( 5, 10),
}

EXPANSION_EVENTS = {
    "thesis_expanded",
    "quant_expanded",
    "smart_money_expanded",
    "peer_expanded",
}


# ── helpers ───────────────────────────────────────────────────────────────
def _fetch_all(db, table: str, *, page_size: int = 1000, **eq_filters):
    """Paged select that returns every matching row."""
    rows: list[dict] = []
    start = 0
    while True:
        q = db.table(table).select("*")
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        batch = q.range(start, start + page_size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def _ticker_sector_lookup(db, tickers: Iterable[str]) -> dict[str, str]:
    """Return {ticker: sector} from the latest factor_exposure row per ticker.
    Tickers with no factor_exposure row map to 'Unknown'."""
    out: dict[str, str] = {t: "Unknown" for t in tickers}
    if not out:
        return out
    res = (
        db.table("factor_exposure")
        .select("ticker,sector,as_of")
        .in_("ticker", list(out.keys()))
        .order("as_of", desc=True)
        .execute()
    )
    seen: set[str] = set()
    for r in res.data or []:
        t = r["ticker"]
        if t in seen:
            continue
        seen.add(t)
        out[t] = r.get("sector") or "Unknown"
    return out


def _engagement_score(counts: dict[str, int]) -> float:
    """Sum the per-category contributions, each capped at its column ceiling."""
    s = 0.0
    for k, (pts, cap) in SCORE_WEIGHTS.items():
        s += min(counts.get(k, 0) * pts, cap)
    return round(s, 2)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Compute the Spearman rank correlation by hand so scipy stays optional."""
    n = len(xs)
    if n < 5:
        return None
    def _ranks(vs: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vs[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            tied_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = tied_rank
            i = j + 1
        return ranks
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    denx = math.sqrt(sum((r - mx) ** 2 for r in rx))
    deny = math.sqrt(sum((r - my) ** 2 for r in ry))
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


# ══════════════════════════════════════════════════════════════════════════
# 1. Daily rollup
# ══════════════════════════════════════════════════════════════════════════
def daily_rollup(target_date: date | None = None) -> int:
    """UPSERT one user_action_summary row per active user on target_date.
    Returns the number of rows written."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    target_date = target_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    day_end   = day_start + timedelta(days=1)

    log.info("Daily rollup for %s (UTC)", target_date.isoformat())

    events = (
        db.table("dashboard_events")
        .select("user_id,tenant_id,event_type,event_data,occurred_at")
        .gte("occurred_at", day_start.isoformat())
        .lt ("occurred_at", day_end.isoformat())
        .execute()
        .data or []
    )
    if not events:
        log.info("No events on %s -- nothing to roll up.", target_date)
        return 0

    # Bucket by user
    by_user: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_user[e["user_id"]].append(e)
    log.info("Found %d events across %d users", len(events), len(by_user))

    # Pre-load the picks referenced in the day's pick_clicked events so the
    # per-signal-type attribution can join to ranked_focus_list.signals.
    clicked_keys: set[tuple[str, str, str]] = set()  # (run_date, run_type, ticker)
    for e in events:
        if e["event_type"] != "pick_clicked":
            continue
        d = e.get("event_data") or {}
        t = d.get("ticker")
        rd = d.get("run_date")
        rt = d.get("run_type")
        if t and rd and rt:
            clicked_keys.add((rd, rt, t))
    picks_by_key: dict[tuple[str, str, str], dict] = {}
    if clicked_keys:
        # Query in chunks of 250 to keep PostgREST URLs reasonable.
        keys = list(clicked_keys)
        for i in range(0, len(keys), 250):
            tickers = list({k[2] for k in keys[i : i + 250]})
            rs = (
                db.table("ranked_focus_list")
                .select("run_date,run_type,ticker,signals")
                .in_("ticker", tickers)
                .execute()
                .data or []
            )
            for r in rs:
                key = (r["run_date"], r["run_type"], r["ticker"])
                if key in clicked_keys:
                    picks_by_key[key] = r

    # ticker -> sector for the clicked picks
    sectors = _ticker_sector_lookup(db, {k[2] for k in clicked_keys})

    rows: list[dict] = []
    for user_id, evs in by_user.items():
        counts = Counter(e["event_type"] for e in evs)
        reports_opened   = counts.get("report_opened", 0)
        picks_clicked    = counts.get("pick_clicked", 0)
        expansions_any   = sum(counts.get(t, 0) for t in EXPANSION_EVENTS)
        explicit_fb      = counts.get("pick_marked_useful", 0) + counts.get("pick_marked_unhelpful", 0)
        watchlist_chg    = counts.get("sub_added", 0) + counts.get("unsub_added", 0)
        ticker_searches  = counts.get("ticker_searched", 0)
        positions_ack    = counts.get("position_acknowledged", 0)

        # Tenant from the first event with a value
        tenant_id = next((e.get("tenant_id") for e in evs if e.get("tenant_id")), None)

        # Preferred sectors: count clicks into picks bucketed by sector
        sector_counts: Counter[str] = Counter()
        sig_contrib: dict[str, float] = defaultdict(float)
        for e in evs:
            if e["event_type"] != "pick_clicked":
                continue
            d = e.get("event_data") or {}
            t  = d.get("ticker")
            rd = d.get("run_date")
            rt = d.get("run_type")
            if not t:
                continue
            sector_counts[sectors.get(t, "Unknown")] += 1
            pick = picks_by_key.get((rd, rt, t)) if rd and rt else None
            if not pick:
                continue
            sig = pick.get("signals") or {}
            # Sum the per-key `<key>_contribution` from ranking_engine output.
            for k, v in sig.items():
                if k.endswith("_contribution") and isinstance(v, (int, float)):
                    sig_contrib[k[: -len("_contribution")]] += float(v)

        # Normalise signal contributions into a {category: share} mapping
        sig_total = sum(sig_contrib.values())
        preferred_signal_types = (
            {k: round(v / sig_total, 4) for k, v in sig_contrib.items()}
            if sig_total > 0 else {}
        )
        preferred_sectors = [
            {"sector": s, "clicks": n}
            for s, n in sector_counts.most_common(5)
        ]

        engagement = _engagement_score({
            "reports_opened":          reports_opened,
            "picks_clicked":           picks_clicked,
            "expansions_any":          expansions_any,
            "explicit_feedback_count": explicit_fb,
            "ticker_searches":         ticker_searches,
        })
        avg_picks_per_report = (
            round(picks_clicked / reports_opened, 3)
            if reports_opened > 0 else 0
        )

        rows.append({
            "user_id":                       user_id,
            "summary_date":                  target_date.isoformat(),
            "tenant_id":                     tenant_id,
            "reports_opened":                reports_opened,
            "picks_clicked":                 picks_clicked,
            "avg_picks_clicked_per_report":  avg_picks_per_report,
            "thesis_expansions":             counts.get("thesis_expanded", 0),
            "quant_expansions":              counts.get("quant_expanded", 0),
            "smart_money_expansions":        counts.get("smart_money_expanded", 0),
            "peer_expansions":               counts.get("peer_expanded", 0),
            "positions_acknowledged":        positions_ack,
            "explicit_feedback_count":       explicit_fb,
            "watchlist_changes":             watchlist_chg,
            "ticker_searches":               ticker_searches,
            "preferred_sectors":             preferred_sectors,
            "preferred_signal_types":        preferred_signal_types,
            "engagement_score":              engagement,
            "updated_at":                    datetime.now(timezone.utc).isoformat(),
        })

    # UPSERT in batches
    written = 0
    for i in range(0, len(rows), 500):
        batch = rows[i : i + 500]
        db.table("user_action_summary").upsert(
            batch, on_conflict="user_id,summary_date"
        ).execute()
        written += len(batch)
    log.info("Wrote %d user_action_summary rows for %s", written, target_date)
    return written


# ══════════════════════════════════════════════════════════════════════════
# 2. Signal value
# ══════════════════════════════════════════════════════════════════════════
def identify_signal_value(lookback_days: int = 90) -> dict:
    """For each signal category, compute click-rate and average forward alpha
    on the picks where that category was the strongest contributor. Returns
    the dict so callers (and the workflow) can persist it."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=lookback_days)).isoformat()

    log.info("identify_signal_value: lookback %d days (since %s)", lookback_days, cutoff)

    rfl_rows = (
        db.table("ranked_focus_list")
        .select("run_date,run_type,ticker,signals")
        .gte("run_date", cutoff)
        .execute()
        .data or []
    )
    if not rfl_rows:
        log.warning("No ranked_focus_list rows in window.")
        return {}

    # Group by dominant category (the contribution column with max value).
    # We only care about picks where the dominant signal is "convincingly"
    # dominant, i.e. score >= 70 on a 0-100 scale.
    by_category: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for r in rfl_rows:
        sig = r.get("signals") or {}
        scored = [(k[: -len("_score")], float(v))
                  for k, v in sig.items()
                  if k.endswith("_score") and isinstance(v, (int, float))]
        if not scored:
            continue
        top_key, top_val = max(scored, key=lambda kv: kv[1])
        if top_val >= 70:
            by_category[top_key].append((r["run_date"], r["run_type"], r["ticker"]))

    # Click rate per category: how many of those picks had ANY user click
    click_rate: dict[str, dict] = {}
    for cat, picks in by_category.items():
        if not picks:
            continue
        click_count = 0
        for run_date, run_type, ticker in picks:
            # Any pick_clicked event matching this ticker on a near date
            q = (
                db.table("dashboard_events")
                .select("*", count="exact", head=True)
                .eq("event_type", "pick_clicked")
                .contains("event_data", {"ticker": ticker})
            )
            if q.execute().count or 0:
                click_count += 1
        click_rate[cat] = {
            "picks":   len(picks),
            "clicked": click_count,
            "rate":    round(click_count / len(picks), 4),
        }

    # Forward alpha (20d) per category
    alpha: dict[str, dict] = {}
    for cat, picks in by_category.items():
        tickers = list({p[2] for p in picks})
        if not tickers:
            continue
        outcomes = (
            db.table("pick_outcomes")
            .select("ticker,recommended_date,recommended_run_type,alpha_5d,alpha_20d,alpha_60d")
            .in_("ticker", tickers)
            .gte("recommended_date", cutoff)
            .execute()
            .data or []
        )
        pick_set = set(picks)
        for_alpha = [o for o in outcomes
                     if (o["recommended_date"], o["recommended_run_type"], o["ticker"]) in pick_set]
        a20 = [o["alpha_20d"] for o in for_alpha if o.get("alpha_20d") is not None]
        a5  = [o["alpha_5d"]  for o in for_alpha if o.get("alpha_5d")  is not None]
        alpha[cat] = {
            "n":            len(for_alpha),
            "mean_alpha_5d":  round(sum(a5) / len(a5), 4) if a5 else None,
            "mean_alpha_20d": round(sum(a20) / len(a20), 4) if a20 else None,
        }

    out = {cat: {**click_rate.get(cat, {}), **alpha.get(cat, {})}
           for cat in set(click_rate) | set(alpha)}

    _print_signal_value(out)
    _persist_signal_value(out, lookback_days)
    return out


def _print_signal_value(table: dict) -> None:
    print("\n" + "=" * 78)
    print(f"{'CATEGORY':<22}{'PICKS':>7}{'CLICK_RATE':>12}"
          f"{'N_OUT':>7}{'ALPHA_5D':>10}{'ALPHA_20D':>11}")
    print("=" * 78)
    for cat, m in sorted(table.items(), key=lambda kv: -(kv[1].get("rate") or 0)):
        cr = m.get("rate")
        n  = m.get("n", 0)
        a5  = m.get("mean_alpha_5d")
        a20 = m.get("mean_alpha_20d")
        print(f"{cat:<22}"
              f"{m.get('picks', 0):>7}"
              f"{(f'{cr:>11.1%}' if cr is not None else '       n/a')}"
              f"{n:>7}"
              f"{(f'{a5:>9.2%}'  if a5  is not None else '      n/a')}"
              f"{(f'{a20:>10.2%}' if a20 is not None else '       n/a')}")
    print("=" * 78 + "\n")


def _persist_signal_value(table: dict, lookback_days: int) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    p = PROPOSALS_DIR / f"signal_value_{today}.json"
    p.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "lookback_days": lookback_days,
         "by_category": table},
        indent=2, sort_keys=True
    ))
    log.info("Wrote %s", p)


# ══════════════════════════════════════════════════════════════════════════
# 3. Correlate signals to outcomes
# ══════════════════════════════════════════════════════════════════════════
def correlate_signals_to_outcomes(min_outcome_age_days: int = 30) -> dict:
    """Spearman correlation between each category's raw score and the pick's
    forward alpha at 5d / 20d / 60d. Picks newer than min_outcome_age_days
    are skipped so we don't reward signals that haven't had time to play."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=min_outcome_age_days)).isoformat()

    log.info("correlate_signals_to_outcomes: picks older than %s", cutoff)

    outcomes = (
        db.table("pick_outcomes")
        .select("ticker,recommended_date,recommended_run_type,alpha_5d,alpha_20d,alpha_60d")
        .lte("recommended_date", cutoff)
        .execute()
        .data or []
    )
    if not outcomes:
        log.warning("No mature pick_outcomes -- nothing to correlate.")
        return {}

    # Join to ranked_focus_list via (run_date, run_type, ticker)
    keys = {(o["recommended_date"], o["recommended_run_type"], o["ticker"])
            for o in outcomes}
    tickers = list({k[2] for k in keys})
    by_key: dict[tuple, dict] = {}
    for i in range(0, len(tickers), 250):
        chunk = tickers[i : i + 250]
        rs = (
            db.table("ranked_focus_list")
            .select("run_date,run_type,ticker,signals")
            .in_("ticker", chunk)
            .execute()
            .data or []
        )
        for r in rs:
            k = (r["run_date"], r["run_type"], r["ticker"])
            if k in keys:
                by_key[k] = r

    # Pivot: per category -> (score, alpha_5d/20d/60d)
    per_cat: dict[str, list[dict]] = defaultdict(list)
    for o in outcomes:
        k = (o["recommended_date"], o["recommended_run_type"], o["ticker"])
        sig = (by_key.get(k) or {}).get("signals") or {}
        for sk, sv in sig.items():
            if not sk.endswith("_score"):
                continue
            cat = sk[: -len("_score")]
            try:
                score = float(sv)
            except (TypeError, ValueError):
                continue
            per_cat[cat].append({
                "score":    score,
                "alpha_5d":  o.get("alpha_5d"),
                "alpha_20d": o.get("alpha_20d"),
                "alpha_60d": o.get("alpha_60d"),
            })

    results: dict[str, dict] = {}
    for cat, items in per_cat.items():
        scores = [it["score"] for it in items]
        row: dict[str, object] = {"n": len(items)}
        for horizon in ("alpha_5d", "alpha_20d", "alpha_60d"):
            xs, ys = [], []
            for it in items:
                v = it.get(horizon)
                if v is None:
                    continue
                xs.append(it["score"]); ys.append(float(v))
            row[f"spearman_{horizon}"] = _spearman(xs, ys)
            row[f"n_{horizon}"]       = len(xs)
        results[cat] = row

    _print_correlations(results)
    _persist_correlations(results, min_outcome_age_days)
    return results


def _print_correlations(table: dict) -> None:
    print("\n" + "=" * 78)
    print(f"{'CATEGORY':<22}{'N':>6}{'SP_5D':>10}{'SP_20D':>10}{'SP_60D':>10}")
    print("=" * 78)
    for cat, m in sorted(table.items(),
                         key=lambda kv: -(kv[1].get("spearman_alpha_20d") or 0)):
        def _fmt(x):
            return f"{x:>9.3f}" if isinstance(x, (int, float)) else "      n/a"
        print(f"{cat:<22}"
              f"{m.get('n', 0):>6}"
              f"{_fmt(m.get('spearman_alpha_5d'))}"
              f"{_fmt(m.get('spearman_alpha_20d'))}"
              f"{_fmt(m.get('spearman_alpha_60d'))}")
    print("=" * 78 + "\n")


def _persist_correlations(table: dict, min_age: int) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    p = PROPOSALS_DIR / f"signal_correlations_{today}.json"
    p.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "min_outcome_age_days": min_age,
         "by_category": table},
        indent=2, sort_keys=True
    ))
    log.info("Wrote %s", p)


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════
def main() -> None:
    args = sys.argv[1:]
    if not args:
        log.error("usage: feedback_aggregator.py {daily|signal-value|correlate|all} [arg]")
        sys.exit(2)
    cmd = args[0]
    if cmd == "daily":
        target = date.fromisoformat(args[1]) if len(args) > 1 else None
        daily_rollup(target)
    elif cmd == "signal-value":
        lookback = int(args[1]) if len(args) > 1 else 90
        identify_signal_value(lookback)
    elif cmd == "correlate":
        min_age = int(args[1]) if len(args) > 1 else 30
        correlate_signals_to_outcomes(min_age)
    elif cmd == "all":
        daily_rollup()
        identify_signal_value()
        correlate_signals_to_outcomes()
    else:
        log.error("unknown command '%s'", cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
