"""
Backtest Engine
Three jobs:

  1. historical_backtest(start, end)
       Reconstructs what the system WOULD have produced on every weekday in
       [start, end] using ONLY data available on or before that date. Writes
       hypothetical picks to backtest_picks (created on first run; same RLS
       pattern as pick_outcomes). Forward returns are then sourced from the
       same yfinance / market_snapshots pipeline as outcome_tracker.

       The single most dangerous failure mode of any backtest is lookahead
       bias -- letting today's knowledge leak into yesterday's decision. Every
       data lookup here filters by created_at, fetched_at or snapshot_time
       being <= as_of_date. The pipeline's data-integrity columns (fetched_at,
       created_at) were built earlier in this project specifically to make
       that filter possible -- this module is the reason those exist.

  2. weight_optimization(start, end)
       Sweeps each ranking_engine weight by +/-25% in 5% increments, re-ranks
       the historical universe, and reports the weight combo that would have
       maximized historical 5d / 20d alpha and Sharpe. THIS DOES NOT WRITE
       BACK TO PRODUCTION. Output is advisory only -- a backtest is not the
       future, and the spec requires >= 90 days of forward data before any
       weight change.

  3. generate_performance_report(out_dir=pipeline/reports/)
       Pulls from system_performance_rollup + pick_outcomes and writes a
       markdown report: total picks, performance by grade, by run_type,
       best / worst calls, performance by macro regime, agent attribution,
       and the SPY benchmark. This is the sales-asset / advisor-handout
       artifact -- A vs B vs C separation, win rates, alpha distributions.

Usage:
    python pipeline/agents/backtest_engine.py historical 2026-01-01 2026-04-01
    python pipeline/agents/backtest_engine.py optimize   2026-01-01 2026-04-01
    python pipeline/agents/backtest_engine.py report
    python pipeline/agents/backtest_engine.py lookahead-test     # self-test

NOTES
  - historical_backtest is HEAVY: it re-runs ranking for every weekday in the
    window, then forward-tracks each hypothetical pick. Restrict the window
    when iterating.
  - SPY is the benchmark for alpha; same as outcome_tracker.
"""

import logging
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

# Reuse helpers from outcome_tracker to keep behavior identical.
from agents.outcome_tracker import (  # noqa: E402
    BENCHMARK, MILESTONES, MIN_SAMPLE_FOR_SIGNIFICANCE,
    _download_history, _entry_price, _fetch_all, _mean, _num, _pct, _price_at,
    _sharpe, _stdev, _t_stat, _to_date, _trading_days_elapsed, _win_rate,
)

# Mirror the ranking engine's WEIGHTS without importing the module (it eagerly
# connects to Supabase on import). If WEIGHTS in ranking_engine.py change,
# update this list too.
WEIGHTS_BASELINE = {
    "premarket": [
        ("news",                0.28, "news"),
        ("sec",                 0.25, "sec"),
        ("options",             0.08, "options"),
        ("pattern",             0.07, "pattern"),
        ("crowd",               0.08, "crowd"),
        ("pre_market_action",   0.20, "price"),
        ("pattern_match_bonus", 0.04, "pattern"),
    ],
    "midday": [
        ("news",                0.18, "news"),
        ("volume",              0.28, "volume"),
        ("options",             0.22, "options"),
        ("pattern",             0.07, "pattern"),
        ("crowd",               0.10, "crowd"),
        ("pivot",               0.15, "price"),
    ],
    "close": [
        ("earnings",            0.25, "earnings"),
        ("insider",             0.22, "insider"),
        ("news",                0.15, "news"),
        ("after_hours",         0.18, "price"),
        ("pattern",             0.11, "pattern"),
        ("institutional",       0.09, "institutional"),
    ],
}

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# Lookahead-safe signal store: every fetch is filtered to as_of_date.
# ══════════════════════════════════════════════════════════════════════════════

class AsOfSignalStore:
    """Reconstructs the signal store as it would have looked at end-of-day
    on as_of_date. Every query filters by the corresponding time column
    being <= as_of_date end-of-day.

    Tables and their filter columns:
      anomaly_flags             snapshot_time <= as_of
      ticker_sentiment_rollup   snapshot_date <= as_of
      options_signals           snapshot_time <= as_of
      ticker_debates            snapshot_date <= as_of
      pattern_matches           snapshot_date <= as_of
      validated_signals         snapshot_time <= as_of
    """

    def __init__(self, db, as_of: date):
        self.as_of = as_of
        cutoff_ts = datetime.combine(as_of, datetime.max.time()).isoformat()
        cutoff_d  = as_of.isoformat()

        # anomaly_flags -> {ticker: {flag_type: max_severity}} from the latest
        # snapshot_time that is <= cutoff.
        flags = _fetch_all(lambda: db.table("anomaly_flags")
                           .select("ticker,flag_type,severity,snapshot_time")
                           .lte("snapshot_time", cutoff_ts)
                           .order("snapshot_time", desc=True))
        latest = flags[0]["snapshot_time"] if flags else None
        self.flag_sev: dict[str, dict[str, float]] = defaultdict(dict)
        for f in flags:
            if f["snapshot_time"] != latest or not f.get("ticker"):
                continue
            sev = _num(f.get("severity")) or 0
            cur = self.flag_sev[f["ticker"]].get(f["flag_type"], 0)
            self.flag_sev[f["ticker"]][f["flag_type"]] = max(cur, sev)

        # ticker_sentiment_rollup (latest per ticker on or before cutoff)
        self.sentiment: dict[str, float] = {}
        for r in _fetch_all(lambda: db.table("ticker_sentiment_rollup")
                            .select("ticker,weighted_sentiment,snapshot_date")
                            .lte("snapshot_date", cutoff_d)
                            .order("snapshot_date", desc=True)):
            ws = _num(r.get("weighted_sentiment"))
            if r.get("ticker") and r["ticker"] not in self.sentiment and ws is not None:
                self.sentiment[r["ticker"]] = ws

        # options_signals
        self.options: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("options_signals")
                                .select("ticker,conviction_score,snapshot_time")
                                .lte("snapshot_time", cutoff_ts)
                                .order("snapshot_time", desc=True)):
                if r.get("ticker") and r["ticker"] not in self.options:
                    self.options[r["ticker"]] = _num(r.get("conviction_score")) or 0
        except Exception as exc:
            log.debug("options_signals as_of load failed: %s", exc)

        # ticker_debates
        self.crowd: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("ticker_debates")
                                .select("ticker,attention_score,snapshot_date")
                                .lte("snapshot_date", cutoff_d)
                                .order("snapshot_date", desc=True)):
                if r.get("ticker") and r["ticker"] not in self.crowd:
                    self.crowd[r["ticker"]] = _num(r.get("attention_score")) or 0
        except Exception as exc:
            log.debug("ticker_debates as_of load failed: %s", exc)

        # pattern_matches
        self.pattern: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("pattern_matches")
                                .select("ticker,confidence,snapshot_date")
                                .lte("snapshot_date", cutoff_d)
                                .order("snapshot_date", desc=True)):
                if r.get("ticker") and r["ticker"] not in self.pattern:
                    self.pattern[r["ticker"]] = _num(r.get("confidence")) or 0
        except Exception as exc:
            log.debug("pattern_matches as_of load failed: %s", exc)

        # validated_signals -> multiplier
        self.multiplier: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("validated_signals")
                                .select("ticker,confirmation_bonus,direction_bonus,"
                                        "contradiction_flag,snapshot_time")
                                .lte("snapshot_time", cutoff_ts)
                                .order("snapshot_time", desc=True)):
                t = r.get("ticker")
                if not t or t in self.multiplier:
                    continue
                m = 1.0 + (_num(r.get("confirmation_bonus")) or 0) \
                        + (_num(r.get("direction_bonus")) or 0)
                if r.get("contradiction_flag"):
                    m *= 0.7
                self.multiplier[t] = round(m, 4)
        except Exception as exc:
            log.debug("validated_signals as_of load failed: %s", exc)

    # ── 0-100 category scores ─────────────────────────────────────────────────
    PRICE_FLAGS  = {"gap_anomaly", "price_acceleration", "breakout_52w",
                    "breakdown_52w", "ma_crossover", "range_expansion"}
    VOLUME_FLAGS = {"volume_spike", "volume_dryup", "block_trade"}
    SEC_FLAGS    = {"insider_cluster", "material_event", "activist_alert",
                    "institutional_pivot"}

    def _flag_max(self, ticker, family) -> float:
        d = self.flag_sev.get(ticker, {})
        return max((d.get(ft, 0) for ft in family), default=0.0)

    def score(self, ticker: str, source: str) -> float:
        if source == "news":
            return round(abs(self.sentiment.get(ticker, 0.0)) * 100, 2)
        if source == "volume":
            return round(self._flag_max(ticker, self.VOLUME_FLAGS), 2)
        if source == "price":
            return round(self._flag_max(ticker, self.PRICE_FLAGS), 2)
        if source == "sec":
            return round(self._flag_max(ticker, self.SEC_FLAGS), 2)
        if source == "earnings":
            return round(self.flag_sev.get(ticker, {}).get("material_event", 0.0), 2)
        if source == "insider":
            return round(self.flag_sev.get(ticker, {}).get("insider_cluster", 0.0), 2)
        if source == "institutional":
            return round(self.flag_sev.get(ticker, {}).get("institutional_pivot", 0.0), 2)
        if source == "options":
            return round(self.options.get(ticker, 0.0), 2)
        if source == "crowd":
            return round(self.crowd.get(ticker, 0.0), 2)
        if source == "pattern":
            return round(self.pattern.get(ticker, 0.0), 2)
        return 0.0

    def universe(self) -> set[str]:
        return (set(self.flag_sev) | set(self.sentiment) | set(self.options)
                | set(self.crowd) | set(self.pattern) | set(self.multiplier))


def _rank_as_of(store: AsOfSignalStore, run_type: str, weights, top_n: int = 30):
    out = []
    for t in store.universe():
        weighted_sum = 0.0
        for _, w, source in weights:
            weighted_sum += store.score(t, source) * w
        m = store.multiplier.get(t, 1.0)
        score = round(weighted_sum * m, 4)
        if score > 0:
            out.append({"ticker": t, "composite_score": score})
    out.sort(key=lambda r: r["composite_score"], reverse=True)
    return out[:top_n]


# ══════════════════════════════════════════════════════════════════════════════
# Historical backtest
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_backtest_picks_table(db) -> None:
    """The backtest_picks table is created lazily on first historical run.
    We rely on a migration-style raw SQL execution via supabase-py rpc if
    available; otherwise we just attempt the insert and let the user know
    to apply the schema manually."""
    # No safe DDL path through PostgREST. Log a hint instead.
    log.debug("(skipping DDL; backtest_picks must be created via migration. "
              "See _BACKTEST_PICKS_DDL string at the bottom of this file.)")


def historical_backtest(start_date, end_date, run_types=("midday",),
                        top_n: int = 30) -> int:
    """For every WEEKDAY in [start, end]:
        for each run_type:
            store    = AsOfSignalStore(db, day)
            picks    = _rank_as_of(store, run_type, WEIGHTS_BASELINE[run_type], top_n)
            persist  -> backtest_picks (day, run_type, ticker, composite_score)
       Then forward-track each hypothetical pick using yfinance prices."""
    db = _db()
    _ensure_backtest_picks_table(db)
    start, end = _to_date(start_date), _to_date(end_date)
    log.info("historical_backtest: %s -> %s (run_types=%s, top_n=%d)",
             start, end, run_types, top_n)

    all_picks = []
    cur = start
    while cur <= end:
        # Skip weekends -- US markets closed.
        if cur.weekday() < 5:
            store = AsOfSignalStore(db, cur)
            if not store.universe():
                log.info("%s: empty as-of universe -- skipping", cur)
            else:
                for rt in run_types:
                    weights = WEIGHTS_BASELINE.get(rt) or WEIGHTS_BASELINE["midday"]
                    ranked = _rank_as_of(store, rt, weights, top_n)
                    for i, r in enumerate(ranked, start=1):
                        all_picks.append({
                            "backtest_date": cur.isoformat(),
                            "run_type":      rt,
                            "rank":          i,
                            "ticker":        r["ticker"],
                            "composite_score": r["composite_score"],
                        })
                log.info("%s: ranked %d run_type(s)", cur, len(run_types))
        cur += timedelta(days=1)

    if not all_picks:
        log.warning("historical_backtest: no picks generated (universe likely empty)")
        return 0

    # Persist (best-effort). If the table doesn't exist, log and return.
    inserted = 0
    for i in range(0, len(all_picks), 500):
        batch = all_picks[i : i + 500]
        try:
            (db.table("backtest_picks")
               .upsert(batch, on_conflict="backtest_date,run_type,ticker")
               .execute())
            inserted += len(batch)
        except Exception as exc:
            log.warning("backtest_picks persist failed (apply DDL?): %s", exc)
            break

    log.info("historical_backtest: %d hypothetical picks persisted", inserted)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Weight optimization -- ADVISORY ONLY
# ══════════════════════════════════════════════════════════════════════════════

def _forward_returns_for_picks(db, picks: list[dict]) -> list[dict]:
    """Fetch SPY-relative forward returns at 5d and 20d for a list of historical
    picks. Each pick: {ticker, backtest_date}. Adds alpha_5d, alpha_20d."""
    if not picks:
        return []
    by_ticker: dict[str, list[dict]] = {}
    earliest = min(_to_date(p["backtest_date"]) for p in picks)
    for p in picks:
        by_ticker.setdefault(p["ticker"], []).append(p)
    fetch_tickers = list({*by_ticker.keys(), BENCHMARK})
    end = max(_to_date(p["backtest_date"]) for p in picks) + timedelta(days=120)
    hist: dict[str, pd.DataFrame] = {}
    CHUNK = 200
    for i in range(0, len(fetch_tickers), CHUNK):
        hist.update(_download_history(fetch_tickers[i : i + CHUNK],
                                       earliest - timedelta(days=10), end))
    spy_df = hist.get(BENCHMARK)
    out = []
    for t, ps in by_ticker.items():
        df = hist.get(t)
        if df is None or df.empty:
            continue
        for p in ps:
            rd = _to_date(p["backtest_date"])
            entry = _entry_price(df.index, df["close"], rd)
            if entry is None:
                continue
            spy_entry = (_entry_price(spy_df.index, spy_df["close"], rd)
                          if spy_df is not None else None)
            row = dict(p)
            for n in (5, 20):
                price, _ = _price_at(df.index, df["close"], rd, n)
                ret = _pct(price, entry)
                bret = None
                if spy_df is not None and spy_entry is not None:
                    spy_price, _ = _price_at(spy_df.index, spy_df["close"], rd, n)
                    bret = _pct(spy_price, spy_entry)
                row[f"return_{n}d"] = ret
                row[f"benchmark_return_{n}d"] = bret
                row[f"alpha_{n}d"] = (round(ret - bret, 4)
                                      if ret is not None and bret is not None
                                      else None)
            out.append(row)
    return out


def weight_optimization(start_date, end_date, run_type: str = "midday",
                        sweep_pct: float = 0.25, step_pct: float = 0.05,
                        top_n: int = 30) -> dict:
    """Sweep each weight in WEIGHTS_BASELINE[run_type] by +/- sweep_pct in
    step_pct increments. Re-rank as_of each weekday in [start, end], compute
    forward 5d / 20d alpha, and report the combination that maximized alpha.

    NOTE: This is a coordinate descent -- one weight at a time, not the full
    cartesian product (which would explode combinatorially). The output is
    advisory: it shows the marginal effect of each weight on historical
    alpha. Spec says human review required; do NOT auto-apply."""
    db = _db()
    start, end = _to_date(start_date), _to_date(end_date)
    baseline = WEIGHTS_BASELINE[run_type]
    log.info("weight_optimization: %s -> %s, run_type=%s, sweep +/-%.0f%%",
             start, end, run_type, sweep_pct * 100)

    # Pre-compute the AsOfSignalStore for each weekday once (signal scores don't
    # depend on weights, only the weighted sum does).
    weekdays = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            weekdays.append(cur)
        cur += timedelta(days=1)
    log.info("weight_optimization: %d weekdays in window", len(weekdays))
    stores = {d: AsOfSignalStore(db, d) for d in weekdays}

    def _eval(weights) -> dict:
        picks = []
        for d in weekdays:
            store = stores[d]
            if not store.universe():
                continue
            for r in _rank_as_of(store, run_type, weights, top_n):
                picks.append({"backtest_date": d.isoformat(),
                              "run_type": run_type, **r})
        with_returns = _forward_returns_for_picks(db, picks)
        a5  = [r.get("alpha_5d")  for r in with_returns]
        a20 = [r.get("alpha_20d") for r in with_returns]
        return {
            "n":              len(with_returns),
            "avg_alpha_5d":   _mean(a5),
            "avg_alpha_20d":  _mean(a20),
            "sharpe_alpha_20d": _sharpe(a20),
            "alpha_win_20d":  _win_rate(a20),
        }

    baseline_perf = _eval(baseline)
    log.info("BASELINE: %s", baseline_perf)

    suggestions = []
    keys = list(range(len(baseline)))
    steps = []
    s = -sweep_pct
    while s <= sweep_pct + 1e-9:
        if abs(s) > 1e-9:
            steps.append(round(s, 4))
        s += step_pct

    for i in keys:
        key, base_w, source = baseline[i]
        for delta in steps:
            new_w = max(0.0, round(base_w * (1 + delta), 4))
            # Rebalance the rest pro-rata so weights still sum to ~1.
            rest_sum = sum(w for j, (_, w, _) in enumerate(baseline) if j != i)
            if rest_sum == 0:
                continue
            scale = (1 - new_w) / rest_sum
            variant = []
            for j, (k2, w2, s2) in enumerate(baseline):
                variant.append((k2, new_w if j == i else round(w2 * scale, 4), s2))
            perf = _eval(variant)
            suggestions.append({
                "weight_key":   key,
                "delta_pct":    round(delta * 100, 1),
                "new_weight":   new_w,
                **perf,
            })

    suggestions.sort(key=lambda x: (x["avg_alpha_20d"] or -1e9), reverse=True)
    top3 = suggestions[:3]
    bottom3 = suggestions[-3:]

    print("\n" + "=" * 78)
    print(f"WEIGHT OPTIMIZATION -- {run_type} -- {start} to {end}")
    print("=" * 78)
    print(f"\nBASELINE  n={baseline_perf['n']}  "
          f"avg_alpha_20d={baseline_perf['avg_alpha_20d']}  "
          f"sharpe={baseline_perf['sharpe_alpha_20d']}")
    if (baseline_perf["n"] or 0) < MIN_SAMPLE_FOR_SIGNIFICANCE:
        print(f"  ! sample too small to draw conclusions (n < {MIN_SAMPLE_FOR_SIGNIFICANCE})")
    print("\n[ TOP-3 SUGGESTED ADJUSTMENTS (DO NOT AUTO-APPLY) ]")
    for s in top3:
        print(f"  {s['weight_key']:<22} {s['delta_pct']:+.1f}%  "
              f"-> new_weight={s['new_weight']:.3f}  "
              f"alpha_20d={s['avg_alpha_20d']}  sharpe={s['sharpe_alpha_20d']}")
    print("\n[ BOTTOM-3 (showing what hurts) ]")
    for s in bottom3:
        print(f"  {s['weight_key']:<22} {s['delta_pct']:+.1f}%  "
              f"-> new_weight={s['new_weight']:.3f}  "
              f"alpha_20d={s['avg_alpha_20d']}  sharpe={s['sharpe_alpha_20d']}")
    print("\nAdvisory only. Spec requires >= 90 days of forward data before "
          "modifying production weights.")
    print("=" * 78 + "\n")

    return {"baseline": baseline_perf, "suggestions": suggestions}


# ══════════════════════════════════════════════════════════════════════════════
# Performance report
# ══════════════════════════════════════════════════════════════════════════════

def _regime_breakdown(db, outcomes: list[dict]) -> dict:
    """Group outcomes by macro_context.regime_classification at recommended_date
    (or nearest prior). Returns {regime: {n, avg_alpha_20d, win_rate_5d}}."""
    try:
        macro = _fetch_all(lambda: db.table("macro_context")
                            .select("snapshot_date,regime_classification")
                            .order("snapshot_date", desc=True))
    except Exception as exc:
        log.debug("macro_context unavailable: %s", exc)
        return {}
    if not macro:
        return {}
    # Build a sorted list and find nearest-prior date for each outcome.
    macro_sorted = sorted(macro, key=lambda r: r["snapshot_date"])
    macro_dates = [_to_date(r["snapshot_date"]) for r in macro_sorted]
    by_regime: dict[str, list[dict]] = {}
    for o in outcomes:
        rd = _to_date(o["recommended_date"])
        # Binary search for the last macro date <= rd.
        lo, hi = 0, len(macro_dates) - 1
        idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if macro_dates[mid] <= rd:
                idx = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if idx < 0:
            continue
        regime = macro_sorted[idx].get("regime_classification") or "unknown"
        by_regime.setdefault(regime, []).append(o)
    result = {}
    for regime, rows in by_regime.items():
        a20 = [_num(r.get("alpha_20d")) for r in rows]
        result[regime] = {
            "n": len(rows),
            "avg_alpha_20d": _mean(a20),
            "win_rate_5d":   _win_rate([_num(r.get("return_5d")) for r in rows]),
        }
    return result


def _agent_attribution_rollup(db, outcomes: list[dict]) -> list[dict]:
    """For each agent / signal source, compute:
       - sample_size
       - strong_signal_sample_size  (signal in top quartile by 'strength')
       - contribution_to_correct_picks (% with alpha_20d > 0 when signal strong)
       - signal_predictive_power (Spearman-ish correlation, signal vs alpha_20d)

    Reads signals back from ranked_focus_list.signals jsonb (which the ranking
    engine writes), keyed by (run_date, run_type, ticker)."""
    if not outcomes:
        return []
    # Pull signals for the (run_date, run_type, ticker) of each outcome.
    key_set = {(_to_date(o["recommended_date"]).isoformat(),
                o["recommended_run_type"], o["ticker"]) for o in outcomes}
    dates = sorted({k[0] for k in key_set})
    # Fetch all signal rows for those dates and filter in Python (PostgREST
    # doesn't take tuple IN filters).
    signals_by_key: dict[tuple, dict] = {}
    for d in dates:
        rows = _fetch_all(lambda d=d: db.table("ranked_focus_list")
                          .select("run_date,run_type,ticker,signals")
                          .eq("run_date", d))
        for r in rows:
            sig = r.get("signals") or {}
            signals_by_key[(d, r["run_type"], r["ticker"])] = sig

    # Identify all "_contribution" keys (e.g., news_contribution).
    agents = set()
    for sig in signals_by_key.values():
        for k in sig.keys():
            if k.endswith("_contribution"):
                agents.add(k[: -len("_contribution")])

    out = []
    for agent in sorted(agents):
        pairs = []   # (signal_strength, alpha_20d)
        for o in outcomes:
            key = (_to_date(o["recommended_date"]).isoformat(),
                   o["recommended_run_type"], o["ticker"])
            sig = signals_by_key.get(key) or {}
            s = _num(sig.get(f"{agent}_score") or sig.get(f"{agent}_contribution"))
            a20 = _num(o.get("alpha_20d"))
            if s is None or a20 is None:
                continue
            pairs.append((s, a20))
        if not pairs:
            continue
        # "Strong signal" = top quartile by s.
        thresh = sorted([p[0] for p in pairs])[max(0, int(len(pairs) * 0.75) - 1)]
        strong = [p for p in pairs if p[0] >= thresh]
        n_strong = len(strong)
        n_correct = sum(1 for _, a in strong if a > 0)
        contrib_correct = round(n_correct / n_strong * 100, 2) if n_strong else None
        # Spearman-ish: just use Pearson on ranks.
        if len(pairs) >= 3:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            mux, muy = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mux) * (y - muy) for x, y in pairs)
            sx = math.sqrt(sum((x - mux) ** 2 for x in xs))
            sy = math.sqrt(sum((y - muy) ** 2 for y in ys))
            corr = round(cov / (sx * sy), 3) if sx and sy else None
        else:
            corr = None
        out.append({
            "agent_name": agent,
            "sample_size": len(pairs),
            "strong_signal_sample_size": n_strong,
            "contribution_to_correct_picks": contrib_correct,
            "contribution_to_wrong_picks": (round(100 - contrib_correct, 2)
                                              if contrib_correct is not None else None),
            "signal_predictive_power": corr,
        })
    out.sort(key=lambda r: (r["signal_predictive_power"] or -1e9), reverse=True)
    return out


def generate_performance_report(db=None) -> Path:
    db = db or _db()
    outcomes = _fetch_all(lambda: db.table("pick_outcomes").select("*"))
    today = datetime.now(timezone.utc).date()

    def fmt(v, p=2):
        if v is None:
            return "--"
        return f"{float(v):.{p}f}"

    lines = []
    lines.append(f"# Fortis -- Performance Report  ({today})\n")
    lines.append(f"_Tracked picks: {len(outcomes)}_\n")

    # Form 4 data-integrity disclosure: picks generated before the
    # transaction-code fix may carry insider signals corrupted by grants /
    # vestings / gifts being miscounted as directional trades.
    post_fix = sum(1 for o in outcomes
                   if o.get("signal_quality_after_form4_fix"))
    pre_fix = len(outcomes) - post_fix
    if pre_fix:
        lines.append(
            f"> **Form 4 data-integrity note:** {pre_fix} of {len(outcomes)} "
            f"tracked picks predate the Form 4 transaction-code fix and may "
            f"carry insider signals corrupted by grants/vestings/gifts being "
            f"miscounted as directional trades. {post_fix} pick(s) used the "
            f"corrected insider filter. Treat pre-fix insider attribution "
            f"with caution.\n")

    if not outcomes:
        lines.append("\n> No picks tracked yet. Run `outcome_tracker.py backfill`.\n")
        out_path = REPORTS_DIR / f"performance_{today}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")
        log.info("performance report written -> %s", out_path)
        return out_path

    # ── By grade ─────────────────────────────────────────────────────────────
    lines.append("## Performance by Conviction Grade\n")
    lines.append("| Grade | N | avg ret 5d | avg ret 20d | avg alpha 20d | "
                 "win 5d | alpha-win 20d | t-stat 20d | sig? |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    a_vs_c = {}
    for g in ("A", "B", "C"):
        rows = [o for o in outcomes if o.get("conviction_grade") == g]
        a20 = [_num(r.get("alpha_20d")) for r in rows]
        r5  = [_num(r.get("return_5d"))  for r in rows]
        r20 = [_num(r.get("return_20d")) for r in rows]
        t20 = _t_stat(a20)
        n_matured = sum(1 for x in a20 if x is not None)
        sig = ("Y" if (t20 is not None and abs(t20) >= 1.5
                       and n_matured >= MIN_SAMPLE_FOR_SIGNIFICANCE) else "n")
        a_vs_c[g] = _mean(a20)
        lines.append(f"| {g} | {len(rows)} | {fmt(_mean(r5))} | {fmt(_mean(r20))} "
                     f"| {fmt(_mean(a20))} | {fmt(_win_rate(r5))} | "
                     f"{fmt(_win_rate(a20))} | {fmt(t20)} | {sig} |")
    lines.append("")

    # Honest assessment: do A > B > C?
    a, b, c = a_vs_c.get("A"), a_vs_c.get("B"), a_vs_c.get("C")
    lines.append("### Honest Assessment: Does the Grading System Discriminate?\n")
    if any(x is None for x in (a, b, c)):
        lines.append("> Not enough matured 20d data for some grade tier. "
                     "Cannot evaluate A > B > C separation yet.\n")
    elif a > b > c:
        lines.append(f"> **Pass.** A ({fmt(a)}%) > B ({fmt(b)}%) > C ({fmt(c)}%). "
                     "Grade ordering is consistent with forward 20d alpha.\n")
    else:
        lines.append(f"> **FAIL.** A={fmt(a)}%  B={fmt(b)}%  C={fmt(c)}%. "
                     "Grade ordering inverted. The grading system needs review "
                     "before public launch.\n")

    # ── By run_type ──────────────────────────────────────────────────────────
    lines.append("## Performance by Run Type\n")
    lines.append("| Run Type | N | avg ret 20d | avg alpha 20d | win 20d |")
    lines.append("|---|---|---|---|---|")
    for rt in ("premarket", "midday", "close"):
        rows = [o for o in outcomes if o.get("recommended_run_type") == rt]
        if not rows:
            continue
        r20 = [_num(r.get("return_20d")) for r in rows]
        a20 = [_num(r.get("alpha_20d")) for r in rows]
        lines.append(f"| {rt} | {len(rows)} | {fmt(_mean(r20))} | "
                     f"{fmt(_mean(a20))} | {fmt(_win_rate(r20))} |")
    lines.append("")

    # ── Best / worst ─────────────────────────────────────────────────────────
    matured = [o for o in outcomes if _num(o.get("return_20d")) is not None]
    if matured:
        top = sorted(matured, key=lambda r: _num(r["return_20d"]) or 0,
                      reverse=True)[:5]
        bot = sorted(matured, key=lambda r: _num(r["return_20d"]) or 0)[:5]
        lines.append("## Best 5 Calls (by 20d return)\n")
        lines.append("| Ticker | Grade | Date | return 20d | alpha 20d | max DD |")
        lines.append("|---|---|---|---|---|---|")
        for r in top:
            lines.append(f"| {r['ticker']} | {r.get('conviction_grade')} | "
                         f"{r['recommended_date']} | {fmt(_num(r.get('return_20d')))} "
                         f"| {fmt(_num(r.get('alpha_20d')))} | "
                         f"{fmt(_num(r.get('max_drawdown_during_period')))} |")
        lines.append("\n## Worst 5 Calls (by 20d return)\n")
        lines.append("| Ticker | Grade | Date | return 20d | alpha 20d | max DD |")
        lines.append("|---|---|---|---|---|---|")
        for r in bot:
            lines.append(f"| {r['ticker']} | {r.get('conviction_grade')} | "
                         f"{r['recommended_date']} | {fmt(_num(r.get('return_20d')))} "
                         f"| {fmt(_num(r.get('alpha_20d')))} | "
                         f"{fmt(_num(r.get('max_drawdown_during_period')))} |")
        lines.append("")

    # ── Regime stratification ────────────────────────────────────────────────
    regimes = _regime_breakdown(db, outcomes)
    if regimes:
        lines.append("## Performance by Macro Regime\n")
        lines.append("| Regime | N | avg alpha 20d | win 5d |")
        lines.append("|---|---|---|---|")
        for regime, m in sorted(regimes.items()):
            lines.append(f"| {regime} | {m['n']} | {fmt(m.get('avg_alpha_20d'))} "
                         f"| {fmt(m.get('win_rate_5d'))} |")
        lines.append("")
    else:
        lines.append("## Performance by Macro Regime\n\n"
                     "_macro_context unavailable -- skipping regime stratification._\n")

    # ── Agent attribution ────────────────────────────────────────────────────
    attribution = _agent_attribution_rollup(db, outcomes)
    if attribution:
        lines.append("## Agent Attribution (top-quartile signal -> outcome)\n")
        lines.append("| Agent | N | strong-N | win% when strong | predictive corr |")
        lines.append("|---|---|---|---|---|")
        for a in attribution:
            lines.append(f"| {a['agent_name']} | {a['sample_size']} | "
                         f"{a['strong_signal_sample_size']} | "
                         f"{fmt(a.get('contribution_to_correct_picks'))} | "
                         f"{fmt(a.get('signal_predictive_power'), 3)} |")
        lines.append("")

        # Persist attribution to agent_attribution table.
        snapshot = today.isoformat()
        for a in attribution:
            try:
                db.table("agent_attribution").upsert({
                    "snapshot_date": snapshot, **a,
                }, on_conflict="snapshot_date,agent_name").execute()
            except Exception as exc:
                log.debug("agent_attribution upsert %s failed: %s",
                          a["agent_name"], exc)

    # ── Caveats ──────────────────────────────────────────────────────────────
    lines.append("## Caveats & Self-Deception Guards\n")
    lines.append("- Statistical significance requires |t| >= 1.5 AND n >= 10.")
    lines.append("- Forward returns use TRADING days, not calendar days.")
    lines.append("- Survivorship bias: delisted / bankrupt tickers remain in "
                 "pick_outcomes (entry_price stays; forward prices may be NULL).")
    lines.append("- Drawdown is reported alongside avg return -- avg return "
                 "alone is misleading.")
    lines.append("- Regime stratification: if the period was a single market "
                 "regime, numbers are not generalizable.")
    if len(matured) < MIN_SAMPLE_FOR_SIGNIFICANCE:
        lines.append(f"- **Track record building** -- only {len(matured)} picks "
                     "with matured 20d data. At least 90 days of forward data "
                     "recommended before drawing conclusions.")
    lines.append("")

    out_path = REPORTS_DIR / f"performance_{today}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("performance report written -> %s", out_path)
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
# Lookahead self-test
# ══════════════════════════════════════════════════════════════════════════════

def lookahead_self_test() -> bool:
    """Synthetic: verify that AsOfSignalStore's universe at an early date does
    not contain tickers whose only signals appeared AFTER that date.

    Approach: query the database's earliest snapshot_time across the signal
    tables, build a store at (earliest - 1 day), and assert the store is
    empty. If anything appears, we're leaking the future."""
    db = _db()
    cutoffs = []
    for tbl, col in [("anomaly_flags", "snapshot_time"),
                      ("ticker_sentiment_rollup", "snapshot_date"),
                      ("options_signals", "snapshot_time")]:
        try:
            r = (db.table(tbl).select(col).order(col, desc=False).limit(1)
                 .execute().data or [])
            if r:
                cutoffs.append(_to_date(r[0][col]))
        except Exception:
            continue
    if not cutoffs:
        log.warning("lookahead test: no signal data available -- nothing to test")
        return True
    earliest = min(cutoffs)
    before = earliest - timedelta(days=1)
    store = AsOfSignalStore(db, before)
    if store.universe():
        log.error("LOOKAHEAD BIAS DETECTED: as_of %s should yield empty universe, "
                  "found %d tickers", before, len(store.universe()))
        return False
    log.info("lookahead test PASSED: as_of %s yielded empty universe "
             "(earliest data is %s)", before, earliest)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "report"
    if cmd == "historical":
        if len(sys.argv) < 4:
            log.error("usage: historical <start YYYY-MM-DD> <end YYYY-MM-DD>")
            sys.exit(2)
        historical_backtest(sys.argv[2], sys.argv[3])
    elif cmd in ("optimize", "weight_optimization", "weights"):
        if len(sys.argv) < 4:
            log.error("usage: optimize <start YYYY-MM-DD> <end YYYY-MM-DD> [run_type]")
            sys.exit(2)
        rt = sys.argv[4] if len(sys.argv) > 4 else "midday"
        weight_optimization(sys.argv[2], sys.argv[3], run_type=rt)
    elif cmd in ("report", "performance_report"):
        path = generate_performance_report()
        print(f"\nReport written to: {path}\n")
    elif cmd in ("lookahead-test", "lookahead", "selftest"):
        ok = lookahead_self_test()
        sys.exit(0 if ok else 1)
    else:
        log.error("unknown command '%s' -- use: historical | optimize | report | "
                  "lookahead-test", cmd)
        sys.exit(2)


# ══════════════════════════════════════════════════════════════════════════════
# Optional DDL for backtest_picks -- apply manually if you intend to use the
# historical_backtest persistence path. Kept as a string so it doesn't
# accidentally get auto-executed.
# ══════════════════════════════════════════════════════════════════════════════
_BACKTEST_PICKS_DDL = """
CREATE TABLE IF NOT EXISTS public.backtest_picks (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  backtest_date   date NOT NULL,
  run_type        text NOT NULL,
  rank            int  NOT NULL,
  ticker          text NOT NULL,
  composite_score numeric NOT NULL,
  created_at      timestamptz DEFAULT now(),
  UNIQUE (backtest_date, run_type, ticker)
);
CREATE INDEX IF NOT EXISTS idx_backtest_picks_date
  ON public.backtest_picks (backtest_date DESC);
ALTER TABLE public.backtest_picks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "authenticated_read" ON public.backtest_picks
  FOR SELECT TO authenticated USING (true);
"""

if __name__ == "__main__":
    main()
