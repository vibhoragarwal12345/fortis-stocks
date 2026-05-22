"""
Ranking Engine
The brain that picks today's focus list. It blends every signal category into a
single, fully transparent score and writes the top 100 tickers per run to
ranked_focus_list.

INPUTS
  validated_signals        confirmation multiplier (Tier-2 cross-validation)
  pattern_matches          historical-analog confidence
  anomaly_flags            price / volume / SEC signal strengths
  ticker_sentiment_rollup  news sentiment
  options_signals          options conviction
  ticker_debates           crowd attention

SCORING
  Each run_type weights the categories differently (see WEIGHTS below):
      weighted_sum = sum(category_score * category_weight)   # scores are 0-100
      final_score  = weighted_sum * confirmation_multiplier
  confirmation_multiplier comes from validated_signals:
      1 + confirmation_bonus + direction_bonus,  then x0.7 if contradiction_flag.
  A ticker absent from validated_signals gets multiplier 1.0.

TRANSPARENCY
  Every ranked row stores a components breakdown so an advisor can see exactly
  why a ticker ranks where it does -- each category's score, weight and
  contribution, plus the multiplier and final score.

STORAGE
  Written to ranked_focus_list (migration 001). That table predates this spec,
  so: final_score -> composite_score column, components -> signals column,
  thesis is left NULL (filled later by the debate synthesizer).

Usage:
    python pipeline/agents/ranking_engine.py [premarket|midday|close]
    python pipeline/agents/ranking_engine.py            # runs all three
"""

import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

# ── Anomaly-flag families ──────────────────────────────────────────────────────
PRICE_FLAGS  = {"gap_anomaly", "price_acceleration", "breakout_52w",
                "breakdown_52w", "ma_crossover", "range_expansion"}
VOLUME_FLAGS = {"volume_spike", "volume_dryup", "block_trade"}
SEC_FLAGS    = {"insider_cluster", "material_event", "activist_alert",
                "institutional_pivot"}

# ── Run-type weight tables ─────────────────────────────────────────────────────
# Each entry: (component_key, weight, score_source). Weights sum to 1.0 so the
# weighted sum stays on a 0-100 scale before the confirmation multiplier.
WEIGHTS = {
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

TOP_N = 100

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
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _fetch_all(query_fn, page: int = 1000) -> list[dict]:
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


# ══════════════════════════════════════════════════════════════════════════════
# Signal store -- loads every source once, exposes 0-100 scores per ticker
# ══════════════════════════════════════════════════════════════════════════════

class SignalStore:
    def __init__(self, db):
        # anomaly_flags: latest snapshot, {ticker: {flag_type: max_severity}}
        flags = _fetch_all(lambda: db.table("anomaly_flags")
                           .select("ticker,flag_type,severity,snapshot_time")
                           .order("snapshot_time", desc=True))
        latest = flags[0]["snapshot_time"] if flags else None
        self.flag_sev: dict[str, dict[str, float]] = defaultdict(dict)
        for f in flags:
            if f["snapshot_time"] != latest or not f.get("ticker"):
                continue
            sev = _num(f.get("severity")) or 0
            cur = self.flag_sev[f["ticker"]].get(f["flag_type"], 0)
            self.flag_sev[f["ticker"]][f["flag_type"]] = max(cur, sev)

        # ticker_sentiment_rollup
        self.sentiment: dict[str, float] = {}
        for r in _fetch_all(lambda: db.table("ticker_sentiment_rollup")
                            .select("ticker,weighted_sentiment,snapshot_date")
                            .order("snapshot_date", desc=True)):
            ws = _num(r.get("weighted_sentiment"))
            if r.get("ticker") and r["ticker"] not in self.sentiment and ws is not None:
                self.sentiment[r["ticker"]] = ws

        # options_signals
        self.options: dict[str, float] = {}
        for r in _fetch_all(lambda: db.table("options_signals")
                            .select("ticker,conviction_score,snapshot_time")
                            .order("snapshot_time", desc=True)):
            if r.get("ticker") and r["ticker"] not in self.options:
                self.options[r["ticker"]] = _num(r.get("conviction_score")) or 0

        # ticker_debates
        self.crowd: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("ticker_debates")
                                .select("ticker,attention_score,snapshot_date")
                                .order("snapshot_date", desc=True)):
                if r.get("ticker") and r["ticker"] not in self.crowd:
                    self.crowd[r["ticker"]] = _num(r.get("attention_score")) or 0
        except Exception as exc:
            log.warning("ticker_debates load failed: %s", exc)

        # pattern_matches
        self.pattern: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("pattern_matches")
                                .select("ticker,confidence,snapshot_date")
                                .order("snapshot_date", desc=True)):
                if r.get("ticker") and r["ticker"] not in self.pattern:
                    self.pattern[r["ticker"]] = _num(r.get("confidence")) or 0
        except Exception as exc:
            log.warning("pattern_matches load failed: %s", exc)

        # validated_signals -> confirmation multiplier
        self.multiplier: dict[str, float] = {}
        try:
            for r in _fetch_all(lambda: db.table("validated_signals")
                                .select("ticker,confirmation_bonus,direction_bonus,"
                                        "contradiction_flag,snapshot_time")
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
            log.warning("validated_signals load failed -- multiplier defaults to "
                        "1.0 (%s)", exc)

    # ── 0-100 category scores ─────────────────────────────────────────────────
    def _flag_max(self, ticker, family) -> float:
        d = self.flag_sev.get(ticker, {})
        return max((d.get(ft, 0) for ft in family), default=0.0)

    def score(self, ticker: str, source: str) -> float:
        if source == "news":
            return round(abs(self.sentiment.get(ticker, 0.0)) * 100, 2)
        if source == "volume":
            return round(self._flag_max(ticker, VOLUME_FLAGS), 2)
        if source == "price":
            return round(self._flag_max(ticker, PRICE_FLAGS), 2)
        if source == "sec":
            return round(self._flag_max(ticker, SEC_FLAGS), 2)
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


# ══════════════════════════════════════════════════════════════════════════════
# Scoring
# ══════════════════════════════════════════════════════════════════════════════

def _score_ticker(ticker: str, store: SignalStore, weights: list) -> dict:
    components: dict = {}
    weighted_sum = 0.0
    for key, weight, source in weights:
        score = store.score(ticker, source)
        contribution = round(score * weight, 3)
        weighted_sum += contribution
        components[f"{key}_score"]        = score
        components[f"{key}_weight"]       = weight
        components[f"{key}_contribution"] = contribution

    multiplier = store.multiplier.get(ticker, 1.0)
    final_score = round(weighted_sum * multiplier, 4)
    components["weighted_sum"]            = round(weighted_sum, 4)
    components["confirmation_multiplier"] = multiplier
    components["final_score"]             = final_score
    return {"ticker": ticker, "final_score": final_score, "components": components}


def rank(store: SignalStore, run_type: str) -> list[dict]:
    weights = WEIGHTS[run_type]
    scored = [_score_ticker(t, store, weights) for t in store.universe()]
    scored = [s for s in scored if s["final_score"] > 0]
    scored.sort(key=lambda s: s["final_score"], reverse=True)
    return scored[:TOP_N]


# ══════════════════════════════════════════════════════════════════════════════
# Persistence + report
# ══════════════════════════════════════════════════════════════════════════════

def _persist(db, run_date: str, run_type: str, ranked: list[dict]) -> int:
    # Replace any existing rows for this run so a re-run is idempotent.
    try:
        db.table("ranked_focus_list").delete().eq("run_date", run_date) \
          .eq("run_type", run_type).execute()
    except Exception as exc:
        log.warning("could not clear prior %s/%s rows: %s", run_date, run_type, exc)

    rows = [{
        "run_date":        run_date,
        "run_type":        run_type,
        "rank":            i,
        "ticker":          s["ticker"],
        "composite_score": s["final_score"],   # final_score -> composite_score
        "signals":         s["components"],    # components  -> signals
        "thesis":          None,               # filled later by debate synthesizer
    } for i, s in enumerate(ranked, start=1)]

    inserted = 0
    for i in range(0, len(rows), 500):
        batch = rows[i : i + 500]
        try:
            resp = db.table("ranked_focus_list").insert(batch).execute()
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("ranked_focus_list insert failed: %s", exc)
    return inserted


def _report(run_type: str, ranked: list[dict]) -> None:
    bar = "=" * 76
    print(f"\n{bar}\nRANKED FOCUS LIST -- {run_type.upper()} -- top {len(ranked)}\n{bar}")
    print(f"  {'#':<4}{'TICKER':<8}{'FINAL':>10}{'WSUM':>9}{'MULT':>7}")
    for i, s in enumerate(ranked[:15], start=1):
        c = s["components"]
        print(f"  {i:<4}{s['ticker']:<8}{s['final_score']:>10.2f}"
              f"{c['weighted_sum']:>9.2f}{c['confirmation_multiplier']:>7.2f}")

    if ranked:
        top = ranked[0]
        print(f"\n  -- #1 {top['ticker']} component breakdown "
              f"(final_score {top['final_score']:.2f}) --")
        c = top["components"]
        keys = [k[:-6] for k in c if k.endswith("_score") and k != "final_score"]
        print(f"    {'COMPONENT':<22}{'SCORE':>8}{'WEIGHT':>9}{'CONTRIB':>10}")
        for k in keys:
            print(f"    {k:<22}{c[k+'_score']:>8.1f}{c[k+'_weight']:>9.2f}"
                  f"{c[k+'_contribution']:>10.2f}")
        print(f"    {'-' * 49}")
        print(f"    {'weighted_sum':<22}{'':>8}{'':>9}{c['weighted_sum']:>10.2f}")
        print(f"    {'x confirmation_mult':<22}{'':>8}{'':>9}"
              f"{c['confirmation_multiplier']:>10.2f}")
        print(f"    {'= final_score':<22}{'':>8}{'':>9}{c['final_score']:>10.2f}")
    print(bar)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_types: list[str]) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Ranking Engine -- loading signal store...")
    store = SignalStore(db)
    log.info("Signal store: %d tickers in universe, %d with confirmation multiplier",
             len(store.universe()), len(store.multiplier))
    if not store.multiplier:
        log.warning("validated_signals empty -- multiplier defaults to 1.0 for all. "
                    "Apply migration 009 + re-run cross_validator for confirmation.")

    run_date = datetime.now(timezone.utc).date().isoformat()
    for run_type in run_types:
        ranked = rank(store, run_type)
        inserted = _persist(db, run_date, run_type, ranked)
        log.info("%s: ranked %d tickers, %d rows written to ranked_focus_list",
                 run_type, len(ranked), inserted)
        _report(run_type, ranked)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else None
    targets = [arg] if arg in WEIGHTS else list(WEIGHTS)
    if arg and arg not in WEIGHTS:
        log.warning("Unknown run_type '%s' -- running all three", arg)
    run(targets)
