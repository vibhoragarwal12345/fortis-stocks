"""
Outcome Tracker
Forward-tracks every A/B/C pick the pipeline produces, captures entry / 1d /
5d / 20d / 60d closing prices (TRADING days, not calendar days), benchmarks
against SPY, and aggregates into system_performance_rollup.

This is the empirical-proof layer: every grade we publish becomes a row, so
after 30 days we have early signal and after 90 days we have a publishable
track record. It is also the foundation for Tier-5 weight learning -- weights
cannot be retrained without outcome data.

THREE PUBLIC METHODS
  record_picks_from_run(run_date, run_type)
      Reads ranked_focus_list for that run, takes A/B/C-graded rows, fetches
      the closing price on run_date, and inserts pick_outcomes rows with
      entry_price set and still_active=TRUE.

  update_outcomes(today=None)
      For every still_active row, computes how many trading days have elapsed
      since recommended_date and fills in price_Nd / return_Nd / benchmark
      return / alpha for each milestone (1, 5, 20, 60) that has matured. Also
      updates max_drawdown_during_period and max_gain_during_period from the
      full daily series. Flips still_active=FALSE once 60d is recorded.

  compute_rollups(rollup_date=None)
      Aggregates pick_outcomes by (run_type, conviction_grade) and also for
      'all'/'all'. Writes system_performance_rollup with averages, win rates,
      alpha-win rates, an annualized Sharpe estimate, a t-statistic against
      zero, and a sample_disclaimer when sample is too small.

LOOKAHEAD-BIAS PROTECTION
  Forward returns are computed from prices STRICTLY AFTER recommended_date.
  Entry price is the close ON recommended_date. We never use a price from
  the future of the snapshot we're computing.

TRADING DAYS
  "1d / 5d / 20d / 60d" mean 1 / 5 / 20 / 60 TRADING days after
  recommended_date. We slice yfinance data by position in the date index
  (which already excludes weekends and US market holidays).

Usage:
    python pipeline/agents/outcome_tracker.py record [run_date] [run_type]
    python pipeline/agents/outcome_tracker.py update
    python pipeline/agents/outcome_tracker.py rollup
    python pipeline/agents/outcome_tracker.py backfill        # record all past runs
    python pipeline/agents/outcome_tracker.py record-recent [days]  # record last N days (default 14)
    python pipeline/agents/outcome_tracker.py all             # update + rollup
"""

import logging
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

BENCHMARK = "SPY"
MILESTONES = (1, 5, 20, 60)            # trading days after pick
MIN_SAMPLE_FOR_SIGNIFICANCE = 10        # below this, no t-test celebration
T_STAT_THRESHOLD = 1.5                  # |t| >= 1.5 to call it "significant"
PRICE_FETCH_BUFFER_DAYS = 30            # calendar buffer beyond 60 trading days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _fetch_all(query_fn, page=1000):
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def _to_date(d):
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return datetime.fromisoformat(str(d)[:10]).date()


def _trading_days_elapsed(price_index: pd.DatetimeIndex, anchor: date) -> int:
    """Number of trading days in the index strictly AFTER anchor."""
    anchor_ts = pd.Timestamp(anchor)
    return int((price_index.tz_localize(None).normalize() > anchor_ts).sum())


def _price_at(price_index: pd.DatetimeIndex, closes: pd.Series, anchor: date,
              n_after: int) -> tuple[float | None, date | None]:
    """Closing price n_after TRADING days strictly after anchor.
    Returns (price, actual_date) or (None, None) if not yet matured."""
    normalized = price_index.tz_localize(None).normalize()
    anchor_ts = pd.Timestamp(anchor)
    after = normalized[normalized > anchor_ts]
    if len(after) < n_after:
        return None, None
    target_ts = after[n_after - 1]
    # Map back to the original (possibly tz-aware) index to fetch the close.
    pos = int((normalized == target_ts).argmax())
    if not bool((normalized == target_ts).any()):
        return None, None
    return _num(closes.iloc[pos]), target_ts.date()


def _entry_price(price_index: pd.DatetimeIndex, closes: pd.Series,
                 anchor: date) -> float | None:
    """Closing price ON anchor; if the market was closed that day (weekend/holiday),
    fall back to the most recent prior trading day."""
    normalized = price_index.tz_localize(None).normalize()
    anchor_ts = pd.Timestamp(anchor)
    on_or_before = normalized[normalized <= anchor_ts]
    if len(on_or_before) == 0:
        return None
    target_ts = on_or_before[-1]
    pos = int((normalized == target_ts).argmax())
    return _num(closes.iloc[pos])


def _pct(latest, ref) -> float | None:
    a, b = _num(latest), _num(ref)
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / abs(b) * 100, 4)


def _download_history(tickers: list[str], start: date,
                      end: date) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV from yfinance for tickers in [start, end].
    Returns {ticker: DataFrame with lowercased columns}. Empty if all fail."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, start=start.isoformat(),
                          end=(end + timedelta(days=1)).isoformat(),
                          interval="1d", auto_adjust=True, progress=False,
                          group_by="ticker", threads=True)
    except Exception as exc:
        log.warning("yfinance batch download failed: %s", exc)
        return {}
    if raw is None or raw.empty:
        return {}
    result: dict[str, pd.DataFrame] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            df = (raw[t] if multi else raw).dropna(how="all")
        except KeyError:
            continue
        if df.empty:
            continue
        df = df.rename(columns=str.lower)
        if "close" in df.columns:
            result[t] = df
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 1) record_picks_from_run -- capture entry prices for a single run
# ══════════════════════════════════════════════════════════════════════════════

def record_picks_from_run(db, run_date, run_type: str) -> int:
    """Insert (or upsert) pick_outcomes rows for every A/B/C pick in a run.
    Returns number of rows inserted/upserted."""
    rd = _to_date(run_date)
    picks = (db.table("ranked_focus_list")
             .select("ticker,composite_score,conviction_grade")
             .eq("run_date", rd.isoformat()).eq("run_type", run_type)
             .in_("conviction_grade", ["A", "B", "C"]).execute().data or [])
    if not picks:
        log.info("record_picks_from_run %s/%s: no A/B/C picks", rd, run_type)
        return 0

    tickers = sorted({p["ticker"] for p in picks})
    # Fetch a small window around the pick date (rd - 7d .. rd + 7d) so we can
    # cope with weekends / holidays for entry_price.
    hist = _download_history(tickers, rd - timedelta(days=10),
                              rd + timedelta(days=10))

    # Also fetch validated_strength for nicer audit data.
    vs_map = {}
    try:
        for r in _fetch_all(lambda: db.table("validated_signals")
                            .select("ticker,validated_strength,snapshot_time")
                            .in_("ticker", tickers).eq("run_type", run_type)
                            .order("snapshot_time", desc=True)):
            t = r.get("ticker")
            if t and t not in vs_map:
                vs_map[t] = _num(r.get("validated_strength"))
    except Exception as exc:
        log.debug("validated_signals fetch failed: %s", exc)

    # Smart Money pattern at pick time (Step 6.7). Joining forward returns
    # to pattern after 60-90 days lets the backtest engine answer "which
    # patterns produced the best outperformance?".
    sm_map = {}
    try:
        for r in _fetch_all(lambda: db.table("smart_money_intel")
                            .select("ticker,pattern_detected,confidence_level,"
                                    "composite_smart_money_score,snapshot_date")
                            .in_("ticker", tickers).eq("run_type", run_type)
                            .lte("snapshot_date", rd.isoformat())
                            .order("snapshot_date", desc=True)):
            t = r.get("ticker")
            if t and t not in sm_map:
                sm_map[t] = r
    except Exception as exc:
        log.debug("smart_money_intel fetch failed (may not be migrated yet): %s", exc)

    rows = []
    skipped_no_price = 0
    for p in picks:
        t = p["ticker"]
        df = hist.get(t)
        if df is None or df.empty:
            skipped_no_price += 1
            continue
        entry = _entry_price(df.index, df["close"], rd)
        if entry is None:
            skipped_no_price += 1
            continue
        sm = sm_map.get(t) or {}
        rows.append({
            "ticker": t,
            "recommended_date": rd.isoformat(),
            "recommended_run_type": run_type,
            "conviction_grade": p["conviction_grade"],
            "composite_score": _num(p.get("composite_score")),
            "validated_strength": vs_map.get(t),
            "entry_price": entry,
            "still_active": True,
            # Picks recorded now use the corrected Form 4 transaction-code
            # insider filter -- safe for backtest integrity comparisons.
            "signal_quality_after_form4_fix": True,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "smart_money_pattern": sm.get("pattern_detected"),
            "composite_smart_money_score": _num(sm.get("composite_smart_money_score")),
            "smart_money_confidence": sm.get("confidence_level"),
        })

    if skipped_no_price:
        log.warning("record_picks_from_run %s/%s: %d picks skipped (no entry price)",
                    rd, run_type, skipped_no_price)

    if not rows:
        return 0
    inserted = 0
    for i in range(0, len(rows), 200):
        batch = rows[i : i + 200]
        try:
            resp = (db.table("pick_outcomes")
                    .upsert(batch,
                            on_conflict="ticker,recommended_date,recommended_run_type")
                    .execute())
            inserted += len(resp.data) if resp.data else len(batch)
        except Exception as exc:
            log.warning("pick_outcomes upsert failed: %s", exc)
    log.info("record_picks_from_run %s/%s: %d rows upserted",
             rd, run_type, inserted)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# 2) update_outcomes -- fill forward prices for active positions
# ══════════════════════════════════════════════════════════════════════════════

def update_outcomes(db, today=None) -> dict:
    """Fill forward prices / returns / alpha for every still_active row whose
    milestones have matured since the last update.
    Returns a counters dict."""
    today = _to_date(today) if today else datetime.now(timezone.utc).date()
    active = _fetch_all(lambda: db.table("pick_outcomes")
                        .select("id,ticker,recommended_date,entry_price,"
                                "price_1d,price_5d,price_20d,price_60d")
                        .eq("still_active", True))
    if not active:
        log.info("update_outcomes: no still_active rows")
        return {"rows_active": 0, "rows_updated": 0, "rows_completed": 0}

    # Group active rows by ticker so each yfinance pull covers all matured
    # milestones for that ticker in one go.
    by_ticker: dict[str, list[dict]] = {}
    earliest = today
    for r in active:
        t = r["ticker"]
        rd = _to_date(r["recommended_date"])
        if rd < earliest:
            earliest = rd
        by_ticker.setdefault(t, []).append({**r, "_rd": rd})

    tickers = sorted(by_ticker.keys())
    # We need prices from the earliest recommended_date through today + buffer.
    end = today + timedelta(days=2)
    log.info("update_outcomes: %d active rows across %d tickers (window %s -> %s)",
             len(active), len(tickers), earliest, end)

    # Always include SPY for benchmark.
    fetch_tickers = list({*tickers, BENCHMARK})
    # Split into chunks to keep yfinance happy.
    hist: dict[str, pd.DataFrame] = {}
    CHUNK = 200
    for i in range(0, len(fetch_tickers), CHUNK):
        chunk = fetch_tickers[i : i + CHUNK]
        hist.update(_download_history(chunk, earliest - timedelta(days=10), end))

    spy_df = hist.get(BENCHMARK)
    if spy_df is None or spy_df.empty:
        log.warning("update_outcomes: SPY history unavailable -- benchmark fields "
                    "will be NULL this pass")

    counters = {"rows_active": len(active), "rows_updated": 0, "rows_completed": 0}

    for t, rows in by_ticker.items():
        df = hist.get(t)
        if df is None or df.empty:
            log.debug("no price history for %s -- skipping", t)
            continue
        for r in rows:
            rd = r["_rd"]
            entry = _num(r.get("entry_price"))
            if entry is None:
                # Try to backfill entry_price if missing.
                entry = _entry_price(df.index, df["close"], rd)
                if entry is None:
                    continue
            spy_entry = (_entry_price(spy_df.index, spy_df["close"], rd)
                          if spy_df is not None else None)

            payload: dict = {"entry_price": entry,
                              "last_updated": datetime.now(timezone.utc).isoformat()}
            elapsed = _trading_days_elapsed(df.index, rd)

            all_matured = True
            for n in MILESTONES:
                col_price = f"price_{n}d"
                col_ret   = f"return_{n}d"
                col_bret  = f"benchmark_return_{n}d"
                col_alpha = f"alpha_{n}d"

                if r.get(col_price) is not None:
                    continue            # already filled
                if elapsed < n:
                    all_matured = False
                    continue
                price, _ = _price_at(df.index, df["close"], rd, n)
                if price is None:
                    all_matured = False
                    continue
                ret = _pct(price, entry)
                bret = None
                alpha = None
                if spy_df is not None and spy_entry is not None:
                    spy_price, _ = _price_at(spy_df.index, spy_df["close"], rd, n)
                    bret = _pct(spy_price, spy_entry)
                    if ret is not None and bret is not None:
                        alpha = round(ret - bret, 4)
                payload[col_price] = price
                payload[col_ret]   = ret
                if bret is not None:
                    payload[col_bret] = bret
                if alpha is not None:
                    payload[col_alpha] = alpha

            # Update drawdown / max gain over the holding period so far.
            held = df.loc[df.index.tz_localize(None).normalize() > pd.Timestamp(rd)]
            if not held.empty:
                closes = held["close"].dropna()
                if not closes.empty:
                    running_max = closes.cummax()
                    running_min = closes.cummin()
                    max_gain = _pct(closes.max(), entry)
                    # Worst drawdown: lowest close from entry, expressed as percent.
                    # Reported as a negative percent (e.g., -12.5).
                    max_dd = _pct(closes.min(), entry)
                    if max_gain is not None:
                        payload["max_gain_during_period"] = max_gain
                    if max_dd is not None:
                        payload["max_drawdown_during_period"] = max_dd

            # Decide whether to freeze this row.
            if (payload.get("price_60d") is not None
                or (r.get("price_60d") is not None and all_matured)):
                payload["still_active"] = False
                counters["rows_completed"] += 1

            if not payload:
                continue
            try:
                db.table("pick_outcomes").update(payload).eq("id", r["id"]).execute()
                counters["rows_updated"] += 1
            except Exception as exc:
                log.debug("update %s failed: %s", t, exc)

    log.info("update_outcomes: %d updated, %d completed (of %d active)",
             counters["rows_updated"], counters["rows_completed"],
             counters["rows_active"])
    return counters


# ══════════════════════════════════════════════════════════════════════════════
# 3) compute_rollups -- aggregate per (run_type, grade)
# ══════════════════════════════════════════════════════════════════════════════

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _win_rate(xs, threshold=0.0):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    wins = sum(1 for x in xs if x > threshold)
    return round(wins / len(xs) * 100, 2)


def _stdev(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _sharpe(alpha_20d_list):
    """Crude annualization: alpha is on a ~20 trading-day window (~21 cal days);
    there are ~12.6 such windows per year. Sharpe = (mean / stdev) * sqrt(12.6).
    Risk-free is folded into the SPY alpha already (alpha is excess vs SPY)."""
    xs = [x for x in alpha_20d_list if x is not None]
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    sd = _stdev(xs)
    if sd is None or sd == 0:
        return None
    return round((mu / sd) * math.sqrt(12.6), 3)


def _t_stat(xs):
    """One-sample t-stat against 0."""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return None
    mu = sum(xs) / n
    sd = _stdev(xs)
    if sd is None or sd == 0:
        return None
    return round(mu / (sd / math.sqrt(n)), 3)


def _best_worst(rows, key):
    matured = [r for r in rows if _num(r.get(key)) is not None]
    if not matured:
        return None, None
    best = max(matured, key=lambda r: _num(r[key]))
    worst = min(matured, key=lambda r: _num(r[key]))
    def _pack(r):
        return {
            "ticker": r["ticker"],
            "return_20d": _num(r.get("return_20d")),
            "alpha_20d": _num(r.get("alpha_20d")),
            "recommended_date": str(r.get("recommended_date")),
            "conviction_grade": r.get("conviction_grade"),
            "run_type": r.get("recommended_run_type"),
        }
    return _pack(best), _pack(worst)


def _disclaimer(matured_20d: int, t_stat: float | None,
                total_picks: int) -> str | None:
    if total_picks == 0:
        return "no picks tracked"
    if matured_20d == 0:
        return f"none of {total_picks} picks matured to 20d yet"
    if matured_20d < MIN_SAMPLE_FOR_SIGNIFICANCE:
        return (f"insufficient sample at 20d (n={matured_20d}, "
                f"need >= {MIN_SAMPLE_FOR_SIGNIFICANCE})")
    if t_stat is None:
        return "20d returns present but stdev undefined"
    if abs(t_stat) < T_STAT_THRESHOLD:
        return (f"alpha not statistically distinguishable from zero "
                f"(|t|={abs(t_stat):.2f} < {T_STAT_THRESHOLD})")
    return None


def _aggregate(rows: list[dict], rollup_date: date, run_type: str,
               grade: str) -> dict:
    extract = lambda k: [_num(r.get(k)) for r in rows]
    r1, r5, r20, r60 = (extract(f"return_{n}d") for n in MILESTONES)
    a1, a5, a20, a60 = (extract(f"alpha_{n}d") for n in MILESTONES)
    dds, gains = extract("max_drawdown_during_period"), extract("max_gain_during_period")

    best, worst = _best_worst(rows, "return_20d")
    t20 = _t_stat(a20)
    n_matured_20 = sum(1 for x in a20 if x is not None)
    disclaimer = _disclaimer(n_matured_20, t20, len(rows))

    return {
        "rollup_date":           rollup_date.isoformat(),
        "run_type":              run_type,
        "conviction_grade":      grade,
        "pick_count":            len(rows),
        "avg_return_1d":         _mean(r1),
        "avg_return_5d":         _mean(r5),
        "avg_return_20d":        _mean(r20),
        "avg_return_60d":        _mean(r60),
        "avg_alpha_1d":          _mean(a1),
        "avg_alpha_5d":          _mean(a5),
        "avg_alpha_20d":         _mean(a20),
        "avg_alpha_60d":         _mean(a60),
        "win_rate_1d":           _win_rate(r1),
        "win_rate_5d":           _win_rate(r5),
        "win_rate_20d":          _win_rate(r20),
        "win_rate_60d":          _win_rate(r60),
        "alpha_win_rate_5d":     _win_rate(a5),
        "alpha_win_rate_20d":    _win_rate(a20),
        "sharpe_ratio_estimated": _sharpe(a20),
        "t_statistic_alpha_20d": t20,
        "is_statistically_significant":
            (t20 is not None and abs(t20) >= T_STAT_THRESHOLD
             and n_matured_20 >= MIN_SAMPLE_FOR_SIGNIFICANCE),
        "max_drawdown_avg":      _mean(dds),
        "max_gain_avg":          _mean(gains),
        "best_performing_pick":  best,
        "worst_performing_pick": worst,
        "sample_disclaimer":     disclaimer,
    }


def compute_rollups(db, rollup_date=None) -> int:
    rd = _to_date(rollup_date) if rollup_date else datetime.now(timezone.utc).date()
    rows = _fetch_all(lambda: db.table("pick_outcomes")
                      .select("ticker,recommended_date,recommended_run_type,"
                              "conviction_grade,composite_score,"
                              "return_1d,return_5d,return_20d,return_60d,"
                              "alpha_1d,alpha_5d,alpha_20d,alpha_60d,"
                              "max_drawdown_during_period,max_gain_during_period"))
    if not rows:
        log.info("compute_rollups: no pick_outcomes rows")
        return 0

    aggregates = []
    # (run_type, grade) buckets + 'all' rolls.
    by_rt_grade: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_rt_grade.setdefault((r["recommended_run_type"], r["conviction_grade"]), []).append(r)

    for (rt, g), subset in by_rt_grade.items():
        aggregates.append(_aggregate(subset, rd, rt, g))

    # 'all' run_type by grade
    by_grade = {}
    for r in rows:
        by_grade.setdefault(r["conviction_grade"], []).append(r)
    for g, subset in by_grade.items():
        aggregates.append(_aggregate(subset, rd, "all", g))

    # 'all'/'all'
    aggregates.append(_aggregate(rows, rd, "all", "all"))

    written = 0
    for agg in aggregates:
        try:
            (db.table("system_performance_rollup")
               .upsert(agg, on_conflict="rollup_date,run_type,conviction_grade")
               .execute())
            written += 1
        except Exception as exc:
            log.warning("rollup upsert %s/%s failed: %s",
                        agg["run_type"], agg["conviction_grade"], exc)
    log.info("compute_rollups: %d rollup rows written for %s", written, rd)
    return written


# ══════════════════════════════════════════════════════════════════════════════
# Backfill -- record_picks_from_run() for every past run
# ══════════════════════════════════════════════════════════════════════════════

def backfill_all(db) -> int:
    runs = _fetch_all(lambda: db.table("ranked_focus_list")
                      .select("run_date,run_type")
                      .in_("conviction_grade", ["A", "B", "C"]))
    if not runs:
        log.info("backfill: no graded picks in ranked_focus_list")
        return 0
    unique = sorted({(_to_date(r["run_date"]), r["run_type"]) for r in runs})
    log.info("backfill: %d (run_date, run_type) combos with A/B/C grades", len(unique))
    total = 0
    for rd, rt in unique:
        total += record_picks_from_run(db, rd, rt)
    return total


def record_recent(db, days: int = 14) -> int:
    """Idempotently record every A/B/C pick from runs in the last `days` days.

    The lean scan (run_scan.py) does NOT record its own picks -- the recording
    step that used to live in run_full_pipeline.py was orphaned by the lean
    rewrite, which silently froze the track record. The nightly outcome job
    calls this so no graded run is ever missed again. Bounded to a recent window
    so it stays cheap and never re-opens an already-completed (>60d) row
    (record sets still_active=True; within `days` nothing has reached 60d)."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    runs = _fetch_all(lambda: db.table("ranked_focus_list")
                      .select("run_date,run_type")
                      .gte("run_date", cutoff)
                      .in_("conviction_grade", ["A", "B", "C"]))
    if not runs:
        log.info("record_recent: no graded picks in the last %d days", days)
        return 0
    unique = sorted({(_to_date(r["run_date"]), r["run_type"]) for r in runs})
    log.info("record_recent: %d (run_date, run_type) combos in last %d days",
             len(unique), days)
    total = 0
    for rd, rt in unique:
        total += record_picks_from_run(db, rd, rt)
    return total


# ══════════════════════════════════════════════════════════════════════════════
# Summary report (printed to stdout)
# ══════════════════════════════════════════════════════════════════════════════

def print_summary(db) -> None:
    rows = _fetch_all(lambda: db.table("system_performance_rollup")
                      .select("*").eq("run_type", "all"))
    if not rows:
        print("\n(no system_performance_rollup rows yet)")
        return
    latest = max(_to_date(r["rollup_date"]) for r in rows)
    latest_rows = [r for r in rows if _to_date(r["rollup_date"]) == latest]
    by_grade = {r["conviction_grade"]: r for r in latest_rows}

    bar = "=" * 80
    print(f"\n{bar}\nOUTCOME TRACKER SUMMARY -- rollup_date {latest}\n{bar}")
    print(f"\n{'GRADE':<6}{'N':>5}{'avg ret 1d':>12}{'avg alpha 1d':>14}"
          f"{'avg ret 20d':>13}{'avg alpha 20d':>15}{'sig?':>6}")
    for g in ("A", "B", "C", "all"):
        r = by_grade.get(g)
        if not r:
            continue
        def fmt(v, w=10, p=2):
            return ("--".rjust(w) if v is None else f"{float(v):>{w}.{p}f}")
        sig = "Y" if r.get("is_statistically_significant") else "n"
        print(f"{g:<6}{r['pick_count']:>5}"
              f"{fmt(r.get('avg_return_1d'), 12)}"
              f"{fmt(r.get('avg_alpha_1d'), 14)}"
              f"{fmt(r.get('avg_return_20d'), 13)}"
              f"{fmt(r.get('avg_alpha_20d'), 15)}"
              f"{sig:>6}")
        if r.get("sample_disclaimer"):
            print(f"        ! {r['sample_disclaimer']}")
    print(f"\n{bar}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def _db():
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    db = _db()

    if cmd == "record":
        if len(sys.argv) < 4:
            log.error("usage: record <run_date YYYY-MM-DD> <run_type>")
            sys.exit(2)
        record_picks_from_run(db, sys.argv[2], sys.argv[3])
    elif cmd == "update":
        update_outcomes(db)
    elif cmd == "rollup":
        compute_rollups(db)
        print_summary(db)
    elif cmd == "backfill":
        backfill_all(db)
    elif cmd == "record-recent":
        days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 14
        record_recent(db, days)
    elif cmd == "all":
        update_outcomes(db)
        compute_rollups(db)
        print_summary(db)
    else:
        log.error("unknown command '%s' -- use: record | record-recent | update | "
                  "rollup | backfill | all", cmd)
        sys.exit(2)


if __name__ == "__main__":
    main()
