"""
Cross Validator
The agent that prevents single-source false positives. A signal only counts as
"validated" when independent source categories confirm it -- the triangulation
principle that separates institutional research from retail screening.

For every ticker with any signal today it checks SIX independent categories:

  1. PRICE ACTION   price anomaly flags   (anomaly_flags)
  2. VOLUME         volume anomaly flags  (anomaly_flags)
  3. NEWS SENTIMENT |weighted_sentiment| > 0.4, volume >= 3
                                          (ticker_sentiment_rollup)
  4. SEC / INSIDER  SEC anomaly flags     (anomaly_flags)
  5. OPTIONS        pattern_detected != 'none'   (options_signals)
  6. CROWD          attention_score > 50 OR significant divergence
                                          (ticker_debates)

VALIDATED_STRENGTH
    confirmation_bonus = confirmation_count * 0.15   (+15% per confirming category)
    direction_bonus    = direction_alignment * 0.25  (+25% for perfect alignment)
    validated_strength = base_score * (1 + confirmation_bonus + direction_bonus)
    if signals contradict (bull in one category, bear in another):
        validated_strength *= 0.7

A stock with several weak *confirming* signals outscores one with a single
spectacular signal -- consensus across independent sources is what matters.

Usage:
    python pipeline/agents/cross_validator.py [premarket|midday|close]
"""

import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

# ── Category definitions ───────────────────────────────────────────────────────
PRICE_FLAGS  = {"gap_anomaly", "price_acceleration", "breakout_52w",
                "breakdown_52w", "ma_crossover", "range_expansion"}
VOLUME_FLAGS = {"volume_spike", "volume_dryup", "block_trade"}
SEC_FLAGS    = {"insider_cluster", "material_event", "activist_alert",
                "institutional_pivot"}

CATEGORIES = ("price", "volume", "news_sentiment", "sec_insider",
              "options", "crowd")
CATEGORY_WEIGHT = {"price": 1.0, "volume": 0.7, "news_sentiment": 1.0,
                   "sec_insider": 0.9, "options": 0.9, "crowd": 0.6}

SENTIMENT_THRESHOLD = 0.4
SENTIMENT_MIN_VOL   = 3
ATTENTION_THRESHOLD = 50

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


def _direction_majority(dirs: list[str]) -> str:
    bull = dirs.count("bull")
    bear = dirs.count("bear")
    if bull > bear:
        return "bull"
    if bear > bull:
        return "bear"
    return "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# Source loaders
# ══════════════════════════════════════════════════════════════════════════════

def _load_anomalies(db) -> dict[str, list[dict]]:
    """anomaly_flags from the most recent snapshot, grouped by ticker."""
    full = _fetch_all(lambda: db.table("anomaly_flags")
                      .select("ticker,flag_type,flag_value,severity,snapshot_time")
                      .order("snapshot_time", desc=True))
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    latest_time = full[0]["snapshot_time"] if full else None
    for r in full:
        if r["snapshot_time"] == latest_time and r.get("ticker"):
            by_ticker[r["ticker"]].append(r)
    return by_ticker


def _load_sentiment(db) -> dict[str, dict]:
    rows = _fetch_all(lambda: db.table("ticker_sentiment_rollup")
                      .select("ticker,weighted_sentiment,sentiment_volume,"
                              "snapshot_date").order("snapshot_date", desc=True))
    out: dict[str, dict] = {}
    for r in rows:                                  # newest first -> first wins
        out.setdefault(r["ticker"], r)
    return out


def _load_options(db) -> dict[str, dict]:
    rows = _fetch_all(lambda: db.table("options_signals")
                      .select("ticker,pattern_detected,conviction_score,"
                              "put_call_ratio,snapshot_time")
                      .order("snapshot_time", desc=True))
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ticker"], r)
    return out


def _load_debates(db) -> dict[str, dict]:
    try:
        rows = _fetch_all(lambda: db.table("ticker_debates")
                          .select("ticker,attention_score,sentiment_balance,"
                                  "retail_pro_divergence,snapshot_date")
                          .order("snapshot_date", desc=True))
    except Exception as exc:
        log.warning("ticker_debates load failed: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ticker"], r)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Per-category evaluation -> {present, direction, strength, detail}
# ══════════════════════════════════════════════════════════════════════════════

def _price_flag_direction(flag: dict) -> str:
    ft, fv = flag["flag_type"], _num(flag.get("flag_value")) or 0
    if ft in ("breakout_52w", "price_acceleration"):
        return "bull"
    if ft == "breakdown_52w":
        return "bear"
    if ft in ("gap_anomaly", "ma_crossover"):
        return "bull" if fv > 0 else "bear"
    return "neutral"                                # range_expansion


def _eval_categories(ticker, anomalies, sentiment, options, debates) -> dict:
    cats: dict[str, dict] = {c: {"present": False, "direction": "neutral",
                                 "strength": 0.0, "detail": None}
                             for c in CATEGORIES}
    flags = anomalies.get(ticker, [])

    # 1 + 2 + 4 -- anomaly_flags split by family.
    for cat, family in (("price", PRICE_FLAGS), ("volume", VOLUME_FLAGS),
                        ("sec_insider", SEC_FLAGS)):
        fam = [f for f in flags if f["flag_type"] in family]
        if not fam:
            continue
        sev = max((_num(f.get("severity")) or 0) for f in fam)
        if cat == "price":
            direction = _direction_majority([_price_flag_direction(f) for f in fam])
        else:                                       # volume + SEC have no direction
            direction = "neutral"
        cats[cat] = {"present": True, "direction": direction,
                     "strength": round(sev / 100, 4),
                     "detail": sorted({f["flag_type"] for f in fam})}

    # 3 -- news sentiment.
    s = sentiment.get(ticker)
    if s is not None:
        ws  = _num(s.get("weighted_sentiment"))
        vol = s.get("sentiment_volume") or 0
        if ws is not None and abs(ws) > SENTIMENT_THRESHOLD and vol >= SENTIMENT_MIN_VOL:
            cats["news_sentiment"] = {
                "present": True,
                "direction": "bull" if ws > 0 else "bear",
                "strength": round(min(abs(ws), 1.0), 4),
                "detail": {"weighted_sentiment": ws, "volume": vol}}

    # 5 -- options pattern.
    o = options.get(ticker)
    if o is not None and (o.get("pattern_detected") or "none") != "none":
        pat = o["pattern_detected"]
        direction = ("bull" if pat == "call_speculation"
                     else "bear" if pat == "put_hedging" else "neutral")
        cats["options"] = {"present": True, "direction": direction,
                            "strength": round((_num(o.get("conviction_score")) or 0)
                                              / 100, 4),
                            "detail": {"pattern": pat}}

    # 6 -- crowd.
    d = debates.get(ticker)
    if d is not None:
        attn = _num(d.get("attention_score")) or 0
        div  = (d.get("retail_pro_divergence") or "").lower()
        if attn > ATTENTION_THRESHOLD or "significant divergence" in div:
            sb = _num(d.get("sentiment_balance")) or 0
            cats["crowd"] = {
                "present": True,
                "direction": "bull" if sb > 0 else "bear" if sb < 0 else "neutral",
                "strength": round(min(attn / 100, 1.0), 4),
                "detail": {"attention_score": attn}}

    return cats


# ══════════════════════════════════════════════════════════════════════════════
# Validated-strength scoring
# ══════════════════════════════════════════════════════════════════════════════

def _score(cats: dict) -> dict:
    present = [c for c in CATEGORIES if cats[c]["present"]]
    confirmation_count = len(present)

    base_score = round(sum(cats[c]["strength"] * CATEGORY_WEIGHT[c]
                           for c in present), 4)

    bull = sum(1 for c in present if cats[c]["direction"] == "bull")
    bear = sum(1 for c in present if cats[c]["direction"] == "bear")
    direction_alignment = (round(abs(bull - bear) / confirmation_count, 4)
                           if confirmation_count else 0.0)

    confirmation_bonus = round(confirmation_count * 0.15, 4)
    direction_bonus    = round(direction_alignment * 0.25, 4)

    validated = base_score * (1.0 + confirmation_bonus + direction_bonus)
    contradiction = bull > 0 and bear > 0
    if contradiction:
        validated *= 0.7

    return {
        "base_score":          base_score,
        "confirmation_count":  confirmation_count,
        "direction_alignment": direction_alignment,
        "confirmation_bonus":  confirmation_bonus,
        "direction_bonus":     direction_bonus,
        "contradiction_flag":  contradiction,
        "validated_strength":  round(validated, 4),
        "_bull": bull, "_bear": bear,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(rows: list[dict]) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nCROSS-VALIDATED SIGNALS -- {len(rows)} tickers\n{bar}")

    by_vs = sorted(rows, key=lambda r: r["validated_strength"], reverse=True)
    by_bs = sorted(rows, key=lambda r: r["base_score"], reverse=True)

    print("\n[ TOP 20 BY VALIDATED_STRENGTH ]")
    print(f"  {'TICKER':<8}{'V.STR':>9}{'BASE':>8}{'CONF':>6}{'ALIGN':>7}"
          f"{'CONTRA':>8}  CATEGORIES")
    for r in by_vs[:20]:
        print(f"  {r['ticker']:<8}{r['validated_strength']:>9.3f}"
              f"{r['base_score']:>8.3f}{r['confirmation_count']:>6}"
              f"{r['direction_alignment']:>7.2f}{('yes' if r['contradiction_flag'] else '-'):>8}"
              f"  {', '.join(r['_present'])}")

    max_conf = max((r["confirmation_count"] for r in rows), default=0)
    top_conf = sorted([r for r in rows if r["confirmation_count"] == max_conf],
                      key=lambda r: r["validated_strength"], reverse=True)
    print(f"\n[ HIGHEST CONFIRMATION_COUNT -- {max_conf}/6 categories firing "
          f"({len(top_conf)} tickers) ]")
    for r in top_conf[:20]:
        print(f"  {r['ticker']:<8}  conf {r['confirmation_count']}  "
              f"v.str {r['validated_strength']:.3f}  -> {', '.join(r['_present'])}")

    contras = sorted([r for r in rows if r["contradiction_flag"]],
                     key=lambda r: r["validated_strength"], reverse=True)
    print(f"\n[ CONTRADICTIONS FLAGGED -- {len(contras)} tickers "
          f"(independent sources disagree) ]")
    for r in contras[:20]:
        print(f"  {r['ticker']:<8}  v.str {r['validated_strength']:.3f}  "
              f"bull={r['_bull']} bear={r['_bear']}  -> {', '.join(r['_present'])}")
    if not contras:
        print("  (none)")

    print("\n[ COMPARE -- TOP 20 VALIDATED_STRENGTH vs TOP 20 BASE_SCORE ]")
    vs_set = [r["ticker"] for r in by_vs[:20]]
    bs_set = [r["ticker"] for r in by_bs[:20]]
    print(f"  {'#':<4}{'BY VALIDATED_STRENGTH':<26}{'BY BASE_SCORE':<26}")
    for i in range(20):
        v = vs_set[i] if i < len(vs_set) else ""
        b = bs_set[i] if i < len(bs_set) else ""
        moved = "  <-- differs" if v != b else ""
        print(f"  {i+1:<4}{v:<26}{b:<26}{moved}")
    only_vs = set(vs_set) - set(bs_set)
    print(f"\n  {len(only_vs)} ticker(s) in the validated top 20 but NOT the "
          f"base-score top 20: {', '.join(sorted(only_vs)) or '(none)'}")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Cross Validator -- run_type=%s", run_type)

    anomalies = _load_anomalies(db)
    sentiment = _load_sentiment(db)
    options   = _load_options(db)
    debates   = _load_debates(db)
    log.info("Loaded: %d anomaly tickers, %d sentiment, %d options, %d debates",
             len(anomalies), len(sentiment), len(options), len(debates))

    tickers = set(anomalies) | set(sentiment) | set(options) | set(debates)
    log.info("Evaluating %d tickers with at least one signal", len(tickers))

    snapshot = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []
    for t in tickers:
        cats = _eval_categories(t, anomalies, sentiment, options, debates)
        present = [c for c in CATEGORIES if cats[c]["present"]]
        if not present:
            continue
        scored = _score(cats)
        rows.append({
            "ticker":              t,
            "snapshot_time":       snapshot,
            "run_type":            run_type,
            "base_score":          scored["base_score"],
            "confirmation_count":  scored["confirmation_count"],
            "direction_alignment": scored["direction_alignment"],
            "confirmation_bonus":  scored["confirmation_bonus"],
            "direction_bonus":     scored["direction_bonus"],
            "contradiction_flag":  scored["contradiction_flag"],
            "validated_strength":  scored["validated_strength"],
            "category_signals":    {c: cats[c] for c in CATEGORIES},
            "_present":            present,
            "_bull":               scored["_bull"],
            "_bear":               scored["_bear"],
        })

    log.info("Produced %d validated-signal rows", len(rows))
    if not rows:
        log.warning("No validated signals -- are anomaly_flags / "
                    "ticker_sentiment_rollup populated? Re-run those agents.")
        return

    # Persist (strip the report-only underscore keys).
    payload = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    inserted = 0
    for i in range(0, len(payload), 500):
        batch = payload[i : i + 500]
        try:
            resp = db.table("validated_signals").insert(batch).execute()
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("validated_signals insert failed (rows %d-%d): %s",
                        i, i + len(batch), exc)
    log.info("validated_signals: %d/%d rows inserted", inserted, len(payload))
    if inserted == 0:
        log.warning("Nothing persisted -- apply supabase/migrations/"
                    "009_validated_signals.sql, then re-run.")

    _report(rows)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
