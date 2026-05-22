"""
Anomaly Detector
Pure statistical flag generation. After the data agents have populated the
database, this scans every ticker and emits anomaly_flags rows -- one per
(ticker, anomaly type) -- each with a 0-100 severity score.

Anomaly families and their data source:

  PRICE / VOLUME  (market_snapshots)
    gap_anomaly          |gap_pct| > 3
    price_acceleration   big up move on rising volume
    volume_spike         relative_volume > 2.0
    volume_dryup         relative_volume < 0.5
    range_expansion      day range > 2x 20-day ATR        (needs migration 005)
    breakout_52w         closed above the 52-week high    (needs migration 005)
    breakdown_52w        closed below the 52-week low     (needs migration 005)
    ma_crossover         50-SMA crossed 200-SMA           (needs migration 005)

  OPTIONS  (options_signals)
    unusual_calls / unusual_puts / iv_spike / iv_crush

  SEC / INSIDER  (sec_filings)
    insider_cluster      3+ Form 4 filings in the last 5 days
    material_event       an 8-K filed today
    activist_alert       a 13D filed today (activist stake crossed)
    institutional_pivot  a 13F filed today

  SOCIAL  (ticker_debates)
    attention_spike / consensus_flip / divergence_alert

NOTES
  * "price up" anomalies. market_snapshots stores gap_pct but not a full-day
    return, so gap_pct is used as the up-move proxy for price_acceleration.
  * block_trade is intentionally NOT emitted -- it needs intraday 5-minute
    bars, which no pipeline table currently ingests.
  * range_expansion / breakout_52w / breakdown_52w / ma_crossover read the
    technical jsonb columns added by migration 005. If those columns are
    absent (005 not applied / market_data not re-run) they are skipped.
  * insider_cluster cannot read trade direction (sec_filings has no such
    field); it flags on filing count and reports direction as "unknown".

Usage:
    python pipeline/agents/anomaly_detector.py [premarket|midday|close]
"""

import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

# ── Thresholds ─────────────────────────────────────────────────────────────────
GAP_THRESHOLD        = 3.0      # |gap_pct| for gap_anomaly
ACCEL_GAP            = 5.0      # gap_pct for price_acceleration
ACCEL_MIN_RELVOL     = 1.0      # rising-volume confirmation for acceleration
VOLUME_SPIKE         = 2.0
VOLUME_DRYUP         = 0.5
RANGE_ATR_MULT       = 2.0      # day range vs ATR
IV_SPIKE_PCTL        = 90.0
IV_CRUSH_PCTL        = 10.0
INSIDER_MIN_FILINGS  = 3
INSIDER_WINDOW_DAYS  = 5
TODAY_WINDOW_HOURS   = 36       # "today" for 8-K / 13D / 13F (clock-skew tolerant)
ATTN_SPIKE_TODAY     = 70.0
ATTN_SPIKE_YESTERDAY = 30.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _cap(x) -> float:
    """Clamp a severity into [0, 100]."""
    try:
        return round(max(0.0, min(100.0, float(x))), 1)
    except (TypeError, ValueError):
        return 0.0


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _fetch_all(query_fn, page: int = 1000) -> list[dict]:
    """Page through a Supabase select (PostgREST caps a response at ~1000 rows)."""
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


def _latest_by_ticker(rows: list[dict], time_key: str) -> dict[str, dict]:
    """Keep only the most recent row per ticker."""
    best: dict[str, dict] = {}
    for r in rows:
        t = r.get("ticker")
        if not t:
            continue
        ts = _parse_dt(r.get(time_key))
        cur = best.get(t)
        if cur is None or (ts and (_parse_dt(cur.get(time_key)) or datetime.min
                                   .replace(tzinfo=timezone.utc)) < ts):
            best[t] = r
    return best


# ══════════════════════════════════════════════════════════════════════════════
# Anomaly evaluators -- each yields flag dicts {flag_type, flag_value, severity, context}
# ══════════════════════════════════════════════════════════════════════════════

def _price_volume_flags(ms: dict):
    gap    = _num(ms.get("gap_pct"))
    relvol = _num(ms.get("relative_volume"))

    if gap is not None and abs(gap) > GAP_THRESHOLD:
        yield {"flag_type": "gap_anomaly", "flag_value": round(gap, 4),
               "severity": _cap(abs(gap) * 10),
               "context": {"gap_pct": gap, "price": ms.get("price")}}

    if (gap is not None and gap > ACCEL_GAP
            and relvol is not None and relvol > ACCEL_MIN_RELVOL):
        yield {"flag_type": "price_acceleration", "flag_value": round(gap, 4),
               "severity": _cap(abs(gap) * 10),
               "context": {"gap_pct": gap, "relative_volume": relvol,
                           "note": "gap_pct used as up-move proxy"}}

    if relvol is not None and relvol > VOLUME_SPIKE:
        yield {"flag_type": "volume_spike", "flag_value": round(relvol, 4),
               "severity": _cap((relvol - 1) * 30),
               "context": {"relative_volume": relvol, "volume": ms.get("volume"),
                           "avg_volume_30d": ms.get("avg_volume_30d")}}

    if relvol is not None and 0 < relvol < VOLUME_DRYUP:
        yield {"flag_type": "volume_dryup", "flag_value": round(relvol, 4),
               "severity": _cap(40 + (VOLUME_DRYUP - relvol) * 100),
               "context": {"relative_volume": relvol, "volume": ms.get("volume")}}

    # ── Technical anomalies -- only if migration 005's jsonb columns are present.
    price = _num(ms.get("price"))
    pl = ms.get("price_levels") or {}
    if isinstance(pl, dict) and pl:
        from_high = _num(pl.get("pct_from_52w_high"))
        from_low  = _num(pl.get("pct_from_52w_low"))
        if from_high is not None and from_high >= 0:
            yield {"flag_type": "breakout_52w", "flag_value": round(from_high, 4),
                   "severity": _cap(60 + from_high * 8),
                   "context": {"price": price, "high_52w": pl.get("high_52w")}}
        if from_low is not None and from_low <= 0:
            yield {"flag_type": "breakdown_52w", "flag_value": round(from_low, 4),
                   "severity": _cap(60 + abs(from_low) * 8),
                   "context": {"price": price, "low_52w": pl.get("low_52w")}}

    ma = ms.get("moving_averages") or {}
    if isinstance(ma, dict) and ma:
        if ma.get("golden_cross"):
            yield {"flag_type": "ma_crossover", "flag_value": 1,
                   "severity": 75.0,
                   "context": {"direction": "golden_cross",
                               "sma_50": (ma.get("sma") or {}).get("sma_50"),
                               "sma_200": (ma.get("sma") or {}).get("sma_200")}}
        elif ma.get("death_cross"):
            yield {"flag_type": "ma_crossover", "flag_value": -1,
                   "severity": 75.0,
                   "context": {"direction": "death_cross",
                               "sma_50": (ma.get("sma") or {}).get("sma_50"),
                               "sma_200": (ma.get("sma") or {}).get("sma_200")}}

    vol = ms.get("volatility") or {}
    high, low = _num(ms.get("high")), _num(ms.get("low"))
    atr = _num(vol.get("atr")) if isinstance(vol, dict) else None
    if atr and atr > 0 and high is not None and low is not None:
        day_range = high - low
        if day_range > RANGE_ATR_MULT * atr:
            ratio = day_range / atr
            yield {"flag_type": "range_expansion", "flag_value": round(ratio, 3),
                   "severity": _cap((ratio - 1) * 25),
                   "context": {"day_range": round(day_range, 4), "atr": atr,
                               "range_atr_ratio": round(ratio, 2)}}


def _options_flags(opt: dict):
    calls = opt.get("unusual_call_strikes") or []
    puts  = opt.get("unusual_put_strikes") or []
    ivp   = _num(opt.get("iv_percentile"))

    if calls:
        yield {"flag_type": "unusual_calls", "flag_value": len(calls),
               "severity": _cap(20 + len(calls) * 8),
               "context": {"strike_count": len(calls),
                           "put_call_ratio": opt.get("put_call_ratio"),
                           "sample": calls[:3]}}
    if puts:
        yield {"flag_type": "unusual_puts", "flag_value": len(puts),
               "severity": _cap(20 + len(puts) * 8),
               "context": {"strike_count": len(puts),
                           "put_call_ratio": opt.get("put_call_ratio"),
                           "sample": puts[:3]}}
    if ivp is not None and ivp > IV_SPIKE_PCTL:
        yield {"flag_type": "iv_spike", "flag_value": round(ivp, 2),
               "severity": _cap(50 + (ivp - IV_SPIKE_PCTL) * 5),
               "context": {"iv_percentile": ivp, "iv_skew": opt.get("iv_skew")}}
    if ivp is not None and ivp < IV_CRUSH_PCTL:
        yield {"flag_type": "iv_crush", "flag_value": round(ivp, 2),
               "severity": _cap(50 + (IV_CRUSH_PCTL - ivp) * 5),
               "context": {"iv_percentile": ivp, "iv_skew": opt.get("iv_skew")}}


_FORM4   = {"4", "4/A"}
_FORM_8K = {"8-K", "8-K/A"}
_FORM_13D = {"13D", "SC 13D", "SC 13D/A", "13D/A"}
_FORM_13F = {"13F-HR", "13F-HR/A"}


def _sec_flags(filings: list[dict], now: datetime):
    today_cut   = now - timedelta(hours=TODAY_WINDOW_HOURS)
    insider_cut = now - timedelta(days=INSIDER_WINDOW_DAYS)

    dated = [(f, _parse_dt(f.get("filing_date"))) for f in filings]
    dated = [(f, d) for f, d in dated if d is not None]

    form4_recent = [(f, d) for f, d in dated
                    if (f.get("form_type") in _FORM4) and d >= insider_cut]
    if len(form4_recent) >= INSIDER_MIN_FILINGS:
        n = len(form4_recent)
        yield {"flag_type": "insider_cluster", "flag_value": n,
               "severity": _cap(n * 22),
               "context": {"form4_count": n,
                           "window_days": INSIDER_WINDOW_DAYS,
                           "direction": "unknown (sec_filings has no trade side)",
                           "dates": sorted(d.date().isoformat()
                                           for _, d in form4_recent)}}

    eightk = [(f, d) for f, d in dated
              if f.get("form_type") in _FORM_8K and d >= today_cut]
    if eightk:
        yield {"flag_type": "material_event", "flag_value": len(eightk),
               "severity": _cap(40 + len(eightk) * 15),
               "context": {"filing_count": len(eightk),
                           "urls": [f.get("filing_url") for f, _ in eightk[:3]]}}

    thirteend = [(f, d) for f, d in dated
                 if f.get("form_type") in _FORM_13D and d >= today_cut]
    if thirteend:
        yield {"flag_type": "activist_alert", "flag_value": len(thirteend),
               "severity": 90.0,
               "context": {"filing_count": len(thirteend),
                           "urls": [f.get("filing_url") for f, _ in thirteend[:3]]}}

    thirteenf = [(f, d) for f, d in dated
                 if f.get("form_type") in _FORM_13F and d >= today_cut]
    if thirteenf:
        yield {"flag_type": "institutional_pivot", "flag_value": len(thirteenf),
               "severity": 40.0,
               "context": {"filing_count": len(thirteenf),
                           "note": "13F filed today; >10% position change "
                                   "not computable -- holdings not stored"}}


def _social_flags(debates: list[dict]):
    """Today-vs-yesterday social anomalies from ticker_debates."""
    if len(debates) < 1:
        return
    by_date = sorted(debates, key=lambda d: d.get("snapshot_date") or "")
    today = by_date[-1]
    yest = by_date[-2] if len(by_date) >= 2 else None

    attn_today = _num(today.get("attention_score"))
    if attn_today is not None and attn_today > ATTN_SPIKE_TODAY:
        attn_yest = _num(yest.get("attention_score")) if yest else None
        if attn_yest is None or attn_yest < ATTN_SPIKE_YESTERDAY:
            yield {"flag_type": "attention_spike", "flag_value": round(attn_today, 2),
                   "severity": _cap(attn_today),
                   "context": {"attention_today": attn_today,
                               "attention_yesterday": attn_yest}}

    sb_today = _num(today.get("sentiment_balance"))
    sb_yest  = _num(yest.get("sentiment_balance")) if yest else None
    if (sb_today is not None and sb_yest is not None
            and sb_today != 0 and sb_yest != 0
            and (sb_today > 0) != (sb_yest > 0)):
        yield {"flag_type": "consensus_flip", "flag_value": round(sb_today, 2),
               "severity": _cap(40 + abs(sb_today - sb_yest) / 2),
               "context": {"sentiment_balance_today": sb_today,
                           "sentiment_balance_yesterday": sb_yest}}

    div = (today.get("retail_pro_divergence") or "").lower()
    if "significant divergence" in div:
        yield {"flag_type": "divergence_alert", "flag_value": None,
               "severity": 60.0,
               "context": {"retail_pro_divergence": today.get("retail_pro_divergence")}}


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(flags: list[dict]) -> None:
    bar = "=" * 72
    print(f"\n{bar}\nANOMALY DETECTION -- {len(flags)} flags raised\n{bar}")

    by_ticker_count = Counter(f["ticker"] for f in flags)
    by_ticker_sev: dict[str, float] = defaultdict(float)
    for f in flags:
        by_ticker_sev[f["ticker"]] += f["severity"]

    print("\n[ TOP 20 BY ANOMALY COUNT ]")
    print(f"  {'TICKER':<8}{'COUNT':>7}{'TOT SEV':>10}  FLAG TYPES")
    for tk, cnt in by_ticker_count.most_common(20):
        types = sorted({f["flag_type"] for f in flags if f["ticker"] == tk})
        print(f"  {tk:<8}{cnt:>7}{by_ticker_sev[tk]:>10.0f}  {', '.join(types)}")

    print("\n[ TOP 20 BY TOTAL SEVERITY ]")
    print(f"  {'TICKER':<8}{'TOT SEV':>10}{'COUNT':>7}")
    for tk, sev in sorted(by_ticker_sev.items(), key=lambda x: x[1],
                          reverse=True)[:20]:
        print(f"  {tk:<8}{sev:>10.0f}{by_ticker_count[tk]:>7}")

    print("\n[ ANOMALY TYPE DISTRIBUTION ]")
    dist = Counter(f["flag_type"] for f in flags)
    avg_sev = {t: sum(f["severity"] for f in flags if f["flag_type"] == t) / n
               for t, n in dist.items()}
    print(f"  {'FLAG TYPE':<22}{'COUNT':>8}{'AVG SEV':>10}")
    for t, n in dist.most_common():
        print(f"  {t:<22}{n:>8}{avg_sev[t]:>10.1f}")
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    now = datetime.now(timezone.utc)
    log.info("Anomaly Detector -- run_type=%s", run_type)

    # ── Load sources ──────────────────────────────────────────────────────────
    market = _latest_by_ticker(
        _fetch_all(lambda: db.table("market_snapshots").select("*")
                   .order("snapshot_time", desc=True)), "snapshot_time")
    log.info("market_snapshots: %d tickers", len(market))
    has_technicals = any(m.get("price_levels") for m in market.values())
    if not has_technicals:
        log.warning("market_snapshots has no technical columns -- "
                    "range_expansion / breakout_52w / breakdown_52w / "
                    "ma_crossover skipped (apply migration 005, re-run market_data)")

    options = _latest_by_ticker(
        _fetch_all(lambda: db.table("options_signals").select("*")
                   .order("snapshot_time", desc=True)), "snapshot_time")
    log.info("options_signals: %d tickers", len(options))

    filings_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for f in _fetch_all(lambda: db.table("sec_filings")
                        .select("ticker,form_type,filing_date,filing_url,description")):
        if f.get("ticker"):
            filings_by_ticker[f["ticker"]].append(f)
    log.info("sec_filings: %d tickers", len(filings_by_ticker))

    debates_by_ticker: dict[str, list[dict]] = defaultdict(list)
    try:
        for d in _fetch_all(lambda: db.table("ticker_debates").select(
                "ticker,snapshot_date,attention_score,sentiment_balance,"
                "retail_pro_divergence")):
            if d.get("ticker"):
                debates_by_ticker[d["ticker"]].append(d)
    except Exception as exc:
        log.warning("ticker_debates: skipped (%s)", exc)
    log.info("ticker_debates: %d tickers", len(debates_by_ticker))

    # ── Evaluate ──────────────────────────────────────────────────────────────
    tickers = (set(market) | set(options)
               | set(filings_by_ticker) | set(debates_by_ticker))
    log.info("Evaluating %d distinct tickers...", len(tickers))

    flags: list[dict] = []
    snapshot_iso = now.isoformat()
    for t in tickers:
        produced = []
        if t in market:
            produced += list(_price_volume_flags(market[t]))
        if t in options:
            produced += list(_options_flags(options[t]))
        if t in filings_by_ticker:
            produced += list(_sec_flags(filings_by_ticker[t], now))
        if t in debates_by_ticker:
            produced += list(_social_flags(debates_by_ticker[t]))
        for fl in produced:
            flags.append({
                "ticker":        t,
                "snapshot_time": snapshot_iso,
                "run_type":      run_type,
                "flag_type":     fl["flag_type"],
                "flag_value":    fl["flag_value"],
                "severity":      fl["severity"],
                "context":       fl["context"],
            })

    log.info("Raised %d anomaly flags across %d tickers",
             len(flags), len({f['ticker'] for f in flags}))

    # ── Persist ───────────────────────────────────────────────────────────────
    if flags:
        inserted = 0
        for i in range(0, len(flags), 500):
            batch = flags[i : i + 500]
            try:
                resp = db.table("anomaly_flags").insert(batch).execute()
                inserted += len(resp.data) if resp.data else 0
            except Exception as exc:
                log.warning("anomaly_flags insert failed (rows %d-%d): %s",
                            i, i + len(batch), exc)
        log.info("anomaly_flags: %d/%d rows inserted", inserted, len(flags))
        if inserted == 0:
            log.warning("Nothing persisted -- apply supabase/migrations/"
                        "007_anomaly_flags.sql, then re-run.")

    _report(flags)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
