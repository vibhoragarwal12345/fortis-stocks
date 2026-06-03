"""
Catalyst Agent
Identifies the single specific event driving each ranked ticker's signal today.
This is what turns the focus list from a screener output into an analyst note:
every ticker gets a concrete "why" -- an earnings date, an 8-K, a 52-week
breakout, an options sweep.

For every distinct ticker in today's ranked_focus_list it checks eight catalyst
categories in PRIORITY ORDER and keeps the highest-priority match:

  1 earnings              Finnhub earnings calendar (last 24h / next 5 days)
  2 sec_event             8-K filed in the last 48h            (sec_filings)
  3 analyst_action        recommendation-trend shift           (Finnhub)
  4 technical_event       52w breakout / MA cross / breakdown  (anomaly_flags)
  5 news_driven           a cluster of recent headlines        (news_items)
  6 options_driven        unusual options activity             (options_signals)
  7 insider_institutional Form 4 cluster / 13F change          (anomaly_flags)
  8 social_momentum       crowd attention, no other catalyst   (ticker_debates)

The description is written by Groq when GROQ_API_KEY is set, otherwise by a
deterministic per-category template.

Results are written back onto ranked_focus_list (migration 010):
  catalyst_category, catalyst_description, catalyst_confidence,
  catalyst_supporting_data.

LIMITATIONS
  * sec_filings.description holds only the EDGAR filer title -- no 8-K Item
    codes -- so SEC catalysts say "filed an 8-K", not "Item 5.02". Item-level
    detail would need sec_watcher to fetch each filing's index.
  * Finnhub's free recommendation endpoint gives monthly aggregate buy/hold/
    sell counts, not individual analyst actions, so analyst catalysts describe
    an aggregate shift, not "Goldman upgraded this morning".

Usage:
    python pipeline/agents/catalyst_agent.py
"""

import logging
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    FINNHUB_API_KEY, GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402
from llm import complete  # noqa: E402 -- shared provider-waterfall gateway

HTTP_TIMEOUT       = 20
GROQ_MODEL         = "llama-3.3-70b-versatile"
SEC_WINDOW_HOURS   = 48
NEWS_WINDOW_HOURS  = 24
NEWS_CLUSTER_MIN   = 3        # headlines in the window to count as a news cluster
REC_THROTTLE       = 1.1     # seconds between Finnhub recommendation calls
REC_MAX_LOOKUPS    = 80      # cap on per-ticker recommendation calls (runtime guard)
UPDATE_WORKERS     = 8

PRICE_FLAGS  = {"gap_anomaly", "price_acceleration", "breakout_52w",
                "breakdown_52w", "ma_crossover", "range_expansion"}
INSIDER_FLAGS = {"insider_cluster", "institutional_pivot"}

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


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
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
# Source loaders
# ══════════════════════════════════════════════════════════════════════════════

def _load_earnings() -> dict[str, dict]:
    """Finnhub earnings calendar -- last 2 days through next 5 days."""
    if not FINNHUB_API_KEY:
        return {}
    today = datetime.now(timezone.utc).date()
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": (today - timedelta(days=2)).isoformat(),
                    "to":   (today + timedelta(days=5)).isoformat(),
                    "token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        out = {}
        for it in resp.json().get("earningsCalendar", []) or []:
            sym = (it.get("symbol") or "").upper()
            if sym:
                out[sym] = it
        log.info("earnings calendar: %d names", len(out))
        return out
    except Exception as exc:
        log.warning("earnings calendar failed: %s", exc)
        return {}


def _load_sec(db) -> dict[str, list[dict]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SEC_WINDOW_HOURS)).isoformat()
    rows = _fetch_all(lambda: db.table("sec_filings")
                      .select("ticker,form_type,filing_date,filing_url")
                      .gte("filing_date", cutoff))
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ticker"):
            out[r["ticker"]].append(r)
    return out


def _load_anomalies(db) -> dict[str, dict]:
    """Latest-snapshot anomaly flags: {ticker: {flag_type: flag row}}."""
    flags = _fetch_all(lambda: db.table("anomaly_flags")
                       .select("ticker,flag_type,flag_value,severity,context,"
                               "snapshot_time").order("snapshot_time", desc=True))
    latest = flags[0]["snapshot_time"] if flags else None
    out: dict[str, dict] = defaultdict(dict)
    for f in flags:
        if f["snapshot_time"] == latest and f.get("ticker"):
            cur = out[f["ticker"]].get(f["flag_type"])
            if cur is None or (_num(f.get("severity")) or 0) > (_num(cur.get("severity")) or 0):
                out[f["ticker"]][f["flag_type"]] = f
    return out


def _load_news(db) -> dict[str, list[dict]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=NEWS_WINDOW_HOURS)).isoformat()
    rows = _fetch_all(lambda: db.table("news_items")
                      .select("ticker,headline,source,published_at,sentiment_score")
                      .gte("published_at", cutoff))
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("ticker"):
            out[r["ticker"]].append(r)
    return out


def _load_options(db) -> dict[str, dict]:
    rows = _fetch_all(lambda: db.table("options_signals")
                      .select("ticker,unusual_call_strikes,unusual_put_strikes,"
                              "large_block_alerts,put_call_ratio,snapshot_time")
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
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["ticker"], r)
    return out


def _recommendation(ticker: str) -> dict | None:
    """Latest-vs-prior Finnhub recommendation-trend shift, or None."""
    if not FINNHUB_API_KEY:
        return None
    try:
        resp = requests.get("https://finnhub.io/api/v1/stock/recommendation",
                             params={"symbol": ticker, "token": FINNHUB_API_KEY},
                             timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json() or []
        if len(data) < 2:
            return None
        cur, prev = data[0], data[1]
        cur_bull  = (cur.get("strongBuy", 0) + cur.get("buy", 0))
        prev_bull = (prev.get("strongBuy", 0) + prev.get("buy", 0))
        shift = cur_bull - prev_bull
        if abs(shift) < 2:
            return None
        return {"shift": shift, "current": cur, "previous": prev}
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 8-K item reference (used only if an item code is ever present)
# ══════════════════════════════════════════════════════════════════════════════

EIGHTK_ITEMS = {
    "1.01": "material definitive agreement", "2.01": "completion of acquisition",
    "2.02": "results of operations", "5.02": "executive departure/appointment",
    "7.01": "regulation FD disclosure",  "8.01": "other events",
}


# ══════════════════════════════════════════════════════════════════════════════
# Catalyst detection -- priority order
# ══════════════════════════════════════════════════════════════════════════════

def detect_catalyst(ticker: str, src: dict) -> dict:
    """Return {category, confidence, data} for the highest-priority catalyst."""
    now = datetime.now(timezone.utc)

    # P1 -- EARNINGS
    e = src["earnings"].get(ticker)
    if e:
        d = _parse_dt(e.get("date"))
        reported = d is not None and d.date() <= now.date() and e.get("epsActual") is not None
        return {"category": "earnings", "confidence": 0.92,
                "data": {"date": e.get("date"), "hour": e.get("hour"),
                         "eps_estimate": e.get("epsEstimate"),
                         "eps_actual": e.get("epsActual"),
                         "revenue_estimate": e.get("revenueEstimate"),
                         "reported": reported}}

    # P2 -- SEC EVENT (8-K)
    eightk = [f for f in src["sec"].get(ticker, []) if f["form_type"] in ("8-K", "8-K/A")]
    if eightk:
        eightk.sort(key=lambda f: f.get("filing_date") or "", reverse=True)
        return {"category": "sec_event", "confidence": 0.82,
                "data": {"filing_date": eightk[0].get("filing_date"),
                         "count": len(eightk),
                         "filing_url": eightk[0].get("filing_url"),
                         "item_detail": None}}     # item codes not in the ingestor

    # P3 -- ANALYST ACTION
    rec = src["recommendations"].get(ticker)
    if rec:
        return {"category": "analyst_action", "confidence": 0.6,
                "data": {"shift": rec["shift"], "current": rec["current"]}}

    # P4 -- TECHNICAL EVENT
    flags = src["anomalies"].get(ticker, {})
    price_flags = {ft: f for ft, f in flags.items() if ft in PRICE_FLAGS}
    if price_flags:
        # Prefer the most narratively meaningful flag.
        for pref in ("breakout_52w", "breakdown_52w", "ma_crossover",
                     "price_acceleration", "gap_anomaly", "range_expansion"):
            if pref in price_flags:
                f = price_flags[pref]
                conf = 0.75 if pref in ("breakout_52w", "ma_crossover") else 0.65
                return {"category": "technical_event", "confidence": conf,
                        "data": {"flag_type": pref,
                                 "flag_value": f.get("flag_value"),
                                 "severity": f.get("severity"),
                                 "context": f.get("context")}}

    # P5 -- NEWS-DRIVEN
    news = src["news"].get(ticker, [])
    if len(news) >= NEWS_CLUSTER_MIN:
        news.sort(key=lambda n: n.get("published_at") or "", reverse=True)
        return {"category": "news_driven", "confidence": 0.55,
                "data": {"headline_count": len(news),
                         "headlines": [n.get("headline") for n in news[:5]]}}

    # P6 -- OPTIONS-DRIVEN
    o = src["options"].get(ticker)
    if o and ((o.get("unusual_call_strikes") or []) or
              (o.get("unusual_put_strikes") or []) or
              (o.get("large_block_alerts") or [])):
        calls = o.get("unusual_call_strikes") or []
        puts  = o.get("unusual_put_strikes") or []
        blocks = o.get("large_block_alerts") or []
        return {"category": "options_driven", "confidence": 0.6,
                "data": {"unusual_call_count": len(calls),
                         "unusual_put_count": len(puts),
                         "block_count": len(blocks),
                         "put_call_ratio": o.get("put_call_ratio"),
                         "top_block": blocks[0] if blocks else None}}

    # P7 -- INSIDER / INSTITUTIONAL
    # Only a DIRECTIONAL insider cluster (real P/S/V open-market trades) is a
    # catalyst -- a wave of grants or GSU vestings is not. The non-directional
    # fallback flag (form4_transactions not backfilled) is skipped here.
    ins = {ft: f for ft, f in flags.items() if ft in INSIDER_FLAGS}
    if ins:
        ic = ins.get("insider_cluster")
        ic_directional = bool(ic) and (
            (ic.get("context") or {}).get("source") == "form4_transactions")
        if ic is not None and ic_directional:
            return {"category": "insider_institutional", "confidence": 0.68,
                    "data": {"flag_type": "insider_cluster",
                             "flag_value": ic.get("flag_value"),
                             "context": ic.get("context")}}
        if "institutional_pivot" in ins:
            ip = ins["institutional_pivot"]
            return {"category": "insider_institutional", "confidence": 0.68,
                    "data": {"flag_type": "institutional_pivot",
                             "flag_value": ip.get("flag_value"),
                             "context": ip.get("context")}}
        # insider_cluster present but non-directional -> not a catalyst.

    # P8 -- SOCIAL / MOMENTUM
    d = src["debates"].get(ticker)
    if d and (_num(d.get("attention_score")) or 0) > 50:
        return {"category": "social_momentum", "confidence": 0.3,
                "data": {"attention_score": d.get("attention_score")}}

    return {"category": "none", "confidence": 0.1, "data": {}}


# ══════════════════════════════════════════════════════════════════════════════
# Description -- Groq when available, otherwise a per-category template
# ══════════════════════════════════════════════════════════════════════════════

_GROQ_SYSTEM = ("You are an equity analyst. Identify the single specific "
                "catalyst driving today's signal for this stock. Be concrete "
                "and specific -- name the event, the date, the relevant "
                "numbers. One or two sentences.")


def _template_description(ticker: str, cat: dict) -> str:
    c, d = cat["category"], cat["data"]

    if c == "earnings":
        eps_e = d.get("eps_estimate")
        rev_e = d.get("revenue_estimate")
        eps_s = f"consensus EPS ${eps_e}" if eps_e is not None else "no EPS estimate"
        rev_s = (f", revenue ${rev_e/1e9:.1f}B" if isinstance(rev_e, (int, float))
                 and rev_e else "")
        if d.get("reported"):
            act = d.get("eps_actual")
            return (f"{ticker} reported earnings on {d.get('date')}: EPS ${act} "
                    f"vs {eps_s}{rev_s}.")
        when = {"bmo": "before the open", "amc": "after the close"}.get(
            d.get("hour"), "")
        return (f"{ticker} reports earnings {d.get('date')} {when}; "
                f"{eps_s}{rev_s}.")

    if c == "sec_event":
        n = d.get("count", 1)
        extra = f" ({n} filings)" if n > 1 else ""
        return (f"{ticker} filed an 8-K with the SEC on "
                f"{(d.get('filing_date') or '')[:10]}{extra} -- a material "
                f"corporate event.")

    if c == "analyst_action":
        shift = d.get("shift", 0)
        cur = d.get("current", {})
        direction = "more bullish" if shift > 0 else "more bearish"
        return (f"Analyst coverage of {ticker} turned {direction} this month "
                f"(net {shift:+d} buy ratings; now {cur.get('strongBuy',0)} "
                f"strong-buy / {cur.get('buy',0)} buy / {cur.get('hold',0)} "
                f"hold / {cur.get('sell',0)} sell).")

    if c == "technical_event":
        ft = d.get("flag_type")
        ctx = d.get("context") or {}
        labels = {
            "breakout_52w":  f"{ticker} broke above its 52-week high",
            "breakdown_52w": f"{ticker} broke below its 52-week low",
            "ma_crossover":  (f"{ticker} posted a "
                              f"{(ctx.get('direction') or 'moving-average')} signal"),
            "price_acceleration": f"{ticker} is accelerating higher",
            "gap_anomaly":   f"{ticker} gapped {(_num(d.get('flag_value')) or 0):+.1f}%",
            "range_expansion": f"{ticker} saw a sharp expansion in daily range",
        }
        return (labels.get(ft, f"{ticker} triggered a technical signal")
                + f" (severity {d.get('severity')}/100).")

    if c == "news_driven":
        n = d.get("headline_count", 0)
        sample = (d.get("headlines") or [""])[0]
        return (f"{ticker} drew {n} headlines in the last 24h -- concentrated "
                f"news flow, e.g. \"{sample[:90]}\".")

    if c == "options_driven":
        calls, puts = d.get("unusual_call_count", 0), d.get("unusual_put_count", 0)
        blk = d.get("top_block")
        blk_s = ""
        if blk:
            blk_s = (f" Largest block: {blk.get('volume'):,} {blk.get('side','')} "
                     f"contracts at the ${blk.get('strike')} strike.")
        return (f"Unusual options activity in {ticker}: {calls} call and {puts} "
                f"put strikes flagged with volume above open interest.{blk_s}")

    if c == "insider_institutional":
        ctx = d.get("context") or {}
        if d.get("flag_type") == "insider_cluster":
            n = ctx.get("directional_txn_count", d.get("flag_value"))
            direction = ctx.get("direction", "directional")
            return (f"{ticker} saw a cluster of directional insider activity "
                    f"({n} open-market / discretionary Form 4 transactions in "
                    f"the last 5 days, net {direction}).")
        return (f"{ticker} had a 13F institutional-holdings filing today -- a "
                f"possible shift in institutional positioning.")

    if c == "social_momentum":
        return (f"{ticker} is showing elevated crowd attention "
                f"(score {d.get('attention_score')}) with no clear fundamental "
                f"catalyst -- a momentum trade.")

    return (f"No single specific catalyst identified for {ticker}; it ranks on "
            f"aggregate signal strength rather than one discrete event.")


def _llm_description(ticker: str, cat: dict) -> str | None:
    prompt = (f"Stock: {ticker}\nCatalyst category: {cat['category']}\n"
              f"Supporting data: {cat['data']}\n\n"
              f"Write the catalyst description.")
    text, _ = complete(prompt, system=_GROQ_SYSTEM, temperature=0.3, max_tokens=120)
    return text


def describe(ticker: str, cat: dict) -> str:
    return _llm_description(ticker, cat) or _template_description(ticker, cat)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Catalyst Agent -- backend: %s", "groq" if GROQ_API_KEY else "template")

    # Latest ranked_focus_list run.
    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list is empty -- run ranking_engine first.")
        return
    run_date = recent[0]["run_date"]
    rows = _fetch_all(lambda: db.table("ranked_focus_list")
                      .select("id,ticker,run_type,rank").eq("run_date", run_date))
    tickers = sorted({r["ticker"] for r in rows})
    log.info("ranked_focus_list %s -- %d rows, %d distinct tickers",
             run_date, len(rows), len(tickers))

    # Load every shared source once.
    src = {
        "earnings":  _load_earnings(),
        "sec":       _load_sec(db),
        "anomalies": _load_anomalies(db),
        "news":      _load_news(db),
        "options":   _load_options(db),
        "debates":   _load_debates(db),
        "recommendations": {},
    }

    # P3 needs a per-ticker Finnhub call -- only for tickers not already caught
    # by P1/P2, throttled and capped to bound runtime.
    need_rec = [t for t in tickers
                if t not in src["earnings"] and t not in src["sec"]]
    log.info("Fetching analyst recommendations for up to %d/%d tickers...",
             min(len(need_rec), REC_MAX_LOOKUPS), len(need_rec))
    for t in need_rec[:REC_MAX_LOOKUPS]:
        rec = _recommendation(t)
        if rec:
            src["recommendations"][t] = rec
        time.sleep(REC_THROTTLE)

    # Smart Money intel notes (Step 6.7) -- for earnings-recent tickers, the
    # intel_note replaces the generic catalyst description when present. We
    # pre-load the latest snapshot per ticker so the per-ticker loop is cheap.
    sm_by_ticker: dict[str, dict] = {}
    try:
        sm_rows = _fetch_all(lambda: db.table("smart_money_intel")
                              .select("ticker,intel_note,quality_grade,"
                                      "earnings_last_report_date,pattern_detected,"
                                      "snapshot_date")
                              .in_("ticker", tickers)
                              .order("snapshot_date", desc=True))
        for r in sm_rows:
            sm_by_ticker.setdefault(r["ticker"], r)
    except Exception as exc:
        log.debug("smart_money_intel preload failed: %s", exc)

    # Detect + describe per distinct ticker.
    catalysts: dict[str, dict] = {}
    today = datetime.now(timezone.utc).date()
    for t in tickers:
        cat = detect_catalyst(t, src)
        cat["description"] = describe(t, cat)
        # Step 6.7 override: earnings catalyst + Smart Money intel note exists
        # and was VERIFIED/PARTIALLY_VERIFIED + earnings within last 7 days.
        sm = sm_by_ticker.get(t)
        if sm and sm.get("intel_note") and (sm.get("quality_grade") in
                                              ("VERIFIED", "PARTIALLY_VERIFIED")):
            cat_is_earnings = "earnings" in (cat.get("category") or "").lower()
            erd = sm.get("earnings_last_report_date")
            recent = False
            if erd:
                try:
                    recent = (today - datetime.fromisoformat(str(erd)[:10]).date()).days <= 7
                except Exception:
                    recent = False
            if cat_is_earnings or recent:
                cat["description"] = sm["intel_note"]
                cat.setdefault("data", {})["smart_money_pattern"] = sm.get("pattern_detected")
        catalysts[t] = cat
    by_cat: dict[str, int] = defaultdict(int)
    for c in catalysts.values():
        by_cat[c["category"]] += 1
    log.info("Catalyst categories: %s",
             ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items(),
                                                     key=lambda x: -x[1])))

    # Write back to every ranked_focus_list row for the ticker (all run_types).
    def _update(ticker):
        cat = catalysts[ticker]
        try:
            db.table("ranked_focus_list").update({
                "catalyst_category":        cat["category"],
                "catalyst_description":     cat["description"],
                "catalyst_confidence":      cat["confidence"],
                "catalyst_supporting_data": cat["data"],
            }).eq("run_date", run_date).eq("ticker", ticker).execute()
            return True
        except Exception as exc:
            log.debug("update %s failed: %s", ticker, exc)
            return False

    updated = 0
    with ThreadPoolExecutor(max_workers=UPDATE_WORKERS) as pool:
        for ok in pool.map(_update, tickers):
            updated += 1 if ok else 0
    log.info("ranked_focus_list: catalyst columns updated for %d/%d tickers",
             updated, len(tickers))
    if updated == 0:
        log.warning("No rows updated -- apply supabase/migrations/"
                    "010_catalyst_columns.sql, then re-run.")

    _report(catalysts, by_cat)


def _report(catalysts: dict[str, dict], by_cat: dict[str, int]) -> None:
    bar = "=" * 78
    print(f"\n{bar}\nCATALYST IDENTIFICATION -- {len(catalysts)} tickers\n{bar}")

    print("\n[ CATALYST CATEGORY DISTRIBUTION ]")
    for k, v in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {k:<24}{v:>4}")

    # 5 examples -- one from each of the highest-priority categories present.
    priority = ["earnings", "sec_event", "analyst_action", "technical_event",
                "news_driven", "options_driven", "insider_institutional",
                "social_momentum"]
    print("\n[ 5 EXAMPLE CATALYST IDENTIFICATIONS ]")
    shown = 0
    for cname in priority:
        ex = next((t for t, c in catalysts.items() if c["category"] == cname), None)
        if ex is None:
            continue
        c = catalysts[ex]
        print(f"\n  {ex}  --  {c['category']}  (confidence {c['confidence']:.2f})")
        print(f"    {c['description']}")
        shown += 1
        if shown >= 5:
            break
    print(f"\n{bar}\n")


if __name__ == "__main__":
    run()
