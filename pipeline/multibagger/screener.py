"""
Stage 1 -- Multibagger Structural Screener
==========================================

Applies the 6 multibagger DNA traits to every ticker in mb_universe.csv:

    1. Small base                ($100M-$5B sweet spot $200M-$2B)
    2. Durable revenue growth    (3y CAGR > 25%, not decelerating)
    3. Improving unit economics  (GM stable/expanding, OM trend improving)
    4. Large addressable market  (qualitative -- flagged for Stage 3 LLM)
    5. Aligned owner-operators   (insider ownership, low insider selling)
    6. Under-discovered          (analyst coverage < 8, room to be found)

Each trait gets a 0-100 score; "passes" at >= 60. Names with 5+ traits passing
advance to scorer/research; 4 traits → "watch" bucket.

Sources (Finnhub free tier, 1 req/sec):
    /stock/metric?metric=all  -- revenue/margin growth, debt, cash ratios
    /stock/recommendation     -- analyst count proxy (buy+hold+sell)

Form 4 insider data, if present in `form4_transactions`, augments trait 5.

Writes to public.multibagger_candidates.

CLI
    python pipeline/multibagger/screener.py            # full universe
    python pipeline/multibagger/screener.py --sample 300
    python pipeline/multibagger/screener.py --tickers AAPL,NVDA
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / "data" / "mb_universe.csv"
CACHE_DIR = ROOT / "data" / "_finnhub_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_DAYS = 14   # screener runs monthly, two-week TTL is comfortable

PASS_THRESHOLD = 60   # per-trait score for a "pass"

# Sectors with structurally larger TAMs / growth runways. Used as a soft
# bias on trait 4 until the LLM analyst weighs in.
GROWTH_SECTORS = {
    "technology", "health care", "communication services",
    "consumer discretionary", "industrials",
}


# ══════════════════════════════════════════════════════════════════════════════
# Finnhub client + on-disk cache
# ══════════════════════════════════════════════════════════════════════════════

_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


def _cache_path(ticker: str, kind: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.{kind}.json"


def _cache_get(ticker: str, kind: str) -> dict | list | None:
    p = _cache_path(ticker, kind)
    if not p.exists():
        return None
    age = time.time() - p.stat().st_mtime
    if age > CACHE_TTL_DAYS * 86400:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cache_put(ticker: str, kind: str, obj) -> None:
    try:
        _cache_path(ticker, kind).write_text(json.dumps(obj))
    except Exception:
        pass


def _finnhub_get(path: str, **params):
    if not FINNHUB_API_KEY:
        return None
    url = f"https://finnhub.io/api/v1/{path}"
    params = {**params, "token": FINNHUB_API_KEY}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=_HEADERS, timeout=20)
            if r.status_code == 429:
                time.sleep(3 + attempt * 2)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("Finnhub %s failed (attempt %d): %s", path, attempt + 1, exc)
            time.sleep(1 + attempt)
    return None


def _fetch_metric(ticker: str) -> dict:
    cached = _cache_get(ticker, "metric")
    if cached is not None:
        return cached
    obj = _finnhub_get("stock/metric", symbol=ticker, metric="all") or {}
    _cache_put(ticker, "metric", obj)
    return obj


def _fetch_recommendation(ticker: str) -> list:
    cached = _cache_get(ticker, "rec")
    if cached is not None:
        return cached
    obj = _finnhub_get("stock/recommendation", symbol=ticker) or []
    _cache_put(ticker, "rec", obj)
    return obj


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _clip(v: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════════════════════════════
# Trait 1 -- Small base
# ══════════════════════════════════════════════════════════════════════════════

def trait_small_base(market_cap: float | None) -> float:
    if market_cap is None or market_cap <= 0:
        return 0.0
    mc = market_cap
    if 200e6 <= mc <= 2e9:
        # Sweet spot. Score peaks near $500M and tapers gently to $2B.
        return 100.0
    if 100e6 <= mc < 200e6:
        return 80.0
    if 2e9 < mc <= 5e9:
        return 60.0
    if 5e9 < mc <= 10e9:
        return 30.0
    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Trait 2 -- Durable revenue growth
# ══════════════════════════════════════════════════════════════════════════════

def trait_durable_growth(m: dict) -> tuple[float, dict]:
    """Pulls revenue growth signals from /stock/metric. Returns
    (score, evidence_dict)."""
    series = (m.get("series") or {}).get("annual", {}) or {}
    metric = m.get("metric") or {}

    # 3y / 5y revenue CAGR proxies (Finnhub field names drift, try several)
    cagr3 = _num(metric.get("revenueGrowth3Y"))
    cagr5 = _num(metric.get("revenueGrowth5Y"))
    ttm   = _num(metric.get("revenueGrowthTTMYoy"))
    qoq   = _num(metric.get("revenueGrowthQuarterlyYoy"))

    # Fall back to series.annual.revenue if CAGR fields missing.
    if cagr3 is None:
        rev_series = series.get("revenue") or series.get("revenuePerShare") or []
        if isinstance(rev_series, list) and len(rev_series) >= 4:
            try:
                old = float(rev_series[3].get("v"))
                new = float(rev_series[0].get("v"))
                if old > 0 and new > 0:
                    cagr3 = ((new / old) ** (1 / 3) - 1) * 100
            except Exception:
                pass

    evidence = {
        "revenue_cagr_3y": cagr3,
        "revenue_cagr_5y": cagr5,
        "revenue_growth_ttm_yoy": ttm,
        "revenue_growth_quarterly_yoy": qoq,
    }

    # Score: anchor on 3y CAGR (or 5y if 3y missing). Big bonus if
    # recent quarter is also strong.
    base = cagr3 if cagr3 is not None else cagr5
    if base is None and ttm is None:
        return 0.0, evidence

    score = 0.0
    if base is not None:
        if base >= 40:
            score = 95
        elif base >= 25:
            score = 80
        elif base >= 15:
            score = 50
        elif base >= 5:
            score = 20
        else:
            score = 0
    # Quarterly confirmation
    if ttm is not None:
        if ttm >= 30:
            score = max(score, 90)
        elif ttm >= 20:
            score += 10
        elif ttm < 0:
            score = min(score, 20)

    # Deceleration penalty: if last quarter < TTM by a lot
    if ttm is not None and qoq is not None and qoq < ttm - 10:
        score *= 0.6
        evidence["decelerating"] = True

    return _clip(score), evidence


# ══════════════════════════════════════════════════════════════════════════════
# Trait 3 -- Improving unit economics
# ══════════════════════════════════════════════════════════════════════════════

def trait_unit_economics(m: dict) -> tuple[float, dict]:
    metric = m.get("metric") or {}
    gm_ttm    = _num(metric.get("grossMarginTTM"))
    gm_5y     = _num(metric.get("grossMargin5Y"))
    om_ttm    = _num(metric.get("operatingMarginTTM"))
    om_5y     = _num(metric.get("operatingMargin5Y"))
    om_annual = _num(metric.get("operatingMarginAnnual"))

    evidence = {
        "gross_margin_ttm": gm_ttm,
        "gross_margin_5y": gm_5y,
        "operating_margin_ttm": om_ttm,
        "operating_margin_5y": om_5y,
        "operating_margin_annual": om_annual,
    }

    if gm_ttm is None and om_ttm is None:
        return 0.0, evidence

    score = 0.0
    # Gross margin level + trend
    if gm_ttm is not None:
        if gm_ttm >= 60:
            score += 40        # software-tier margins
        elif gm_ttm >= 40:
            score += 30
        elif gm_ttm >= 25:
            score += 20
        elif gm_ttm >= 10:
            score += 10
    if gm_ttm is not None and gm_5y is not None:
        delta = gm_ttm - gm_5y
        if delta >= 2:
            score += 10
        elif delta < -3:
            score -= 15
        evidence["gm_trend_bps"] = delta * 100

    # Operating margin slope (improving even if still negative is OK)
    if om_ttm is not None and om_5y is not None:
        delta = om_ttm - om_5y
        evidence["om_trend_bps"] = delta * 100
        if delta >= 2:
            score += 30
        elif delta >= 0:
            score += 15
        elif delta < -5:
            score -= 20
    elif om_ttm is not None:
        if om_ttm >= 10:
            score += 20
        elif om_ttm > -10:
            score += 5
        else:
            score -= 5

    return _clip(score), evidence


# ══════════════════════════════════════════════════════════════════════════════
# Trait 4 -- Large addressable market (soft proxy here; LLM elaborates later)
# ══════════════════════════════════════════════════════════════════════════════

def trait_large_tam(sector, market_cap: float | None) -> tuple[float, dict]:
    base = 50.0
    if not isinstance(sector, str):
        sector = "" if sector is None else str(sector)
    s = sector.strip().lower() if sector else ""
    if s in GROWTH_SECTORS:
        base += 20
    # Tiny revenue base inside a big sector = more runway (rough proxy)
    if market_cap is not None and market_cap < 500e6:
        base += 10
    return _clip(base), {"sector": sector, "growth_sector": s in GROWTH_SECTORS}


# ══════════════════════════════════════════════════════════════════════════════
# Trait 5 -- Aligned owner-operators
# ══════════════════════════════════════════════════════════════════════════════

def trait_aligned_owners(m: dict, insider_form4: dict | None) -> tuple[float, dict]:
    """Finnhub /stock/metric doesn't expose insider ownership directly on the
    free tier. We approximate via:
      - Form 4 directional buy vs sell ratio (if our form4_transactions has it)
      - If neither, baseline 50 (unknown -- penalised only for clearly bad signals)
    """
    metric = m.get("metric") or {}
    insider_pct = _num(metric.get("insiderHoldingsPercentage"))

    evidence: dict = {"insider_ownership_pct": insider_pct}
    score = 50.0
    if insider_pct is not None:
        if insider_pct >= 20:
            score = 95
        elif insider_pct >= 10:
            score = 80
        elif insider_pct >= 5:
            score = 60
        elif insider_pct >= 1:
            score = 40
        else:
            score = 25

    if insider_form4:
        buys = insider_form4.get("directional_buys") or 0
        sells = insider_form4.get("directional_sells") or 0
        evidence["form4_buys"] = buys
        evidence["form4_sells"] = sells
        if buys + sells > 0:
            ratio = buys / (buys + sells)
            # Insider buying tilts up; heavy selling tilts down.
            if ratio >= 0.7:
                score = max(score, 80)
            elif ratio >= 0.4:
                score = max(score, 60)
            elif sells > 3 and ratio < 0.2:
                score = min(score, 30)

    return _clip(score), evidence


# ══════════════════════════════════════════════════════════════════════════════
# Trait 6 -- Under-discovered
# ══════════════════════════════════════════════════════════════════════════════

def trait_under_discovered(rec: list) -> tuple[float, dict]:
    """Sum buy+hold+sell+strongBuy+strongSell of the most recent
    recommendation row. Lower analyst count = more obscure = higher score."""
    if not isinstance(rec, list) or not rec:
        return 70.0, {"analyst_count": None, "note": "no recommendation data"}
    latest = rec[0] if rec else {}
    cnt = sum(_num(latest.get(k)) or 0 for k in (
        "buy", "hold", "sell", "strongBuy", "strongSell"
    ))
    evidence = {"analyst_count": int(cnt), "rec_period": latest.get("period")}
    if cnt == 0:
        return 95.0, evidence
    if cnt <= 2:
        return 95.0, evidence
    if cnt <= 4:
        return 80.0, evidence
    if cnt <= 6:
        return 60.0, evidence
    if cnt <= 7:
        return 45.0, evidence
    return 20.0, evidence


# ══════════════════════════════════════════════════════════════════════════════
# Form 4 insider lookup (Postgres) -- batch for the run
# ══════════════════════════════════════════════════════════════════════════════

def _form4_insider_map(db) -> dict[str, dict]:
    """Pull last 180 days of directional Form 4 events, aggregated per ticker.
    Returns {ticker: {directional_buys, directional_sells}}. If the table
    is missing or empty, returns {}."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        rows = (
            db.table("form4_transactions")
              .select("ticker, transaction_code, is_directional_signal, transaction_shares")
              .gte("filing_date", cutoff)
              .execute()
              .data or []
        )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if not r.get("is_directional_signal"):
            continue
        t = (r.get("ticker") or "").upper()
        if not t:
            continue
        d = out.setdefault(t, {"directional_buys": 0, "directional_sells": 0})
        code = (r.get("transaction_code") or "").upper()
        if code == "P":
            d["directional_buys"] += 1
        elif code in ("S", "V"):
            d["directional_sells"] += 1
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker screen
# ══════════════════════════════════════════════════════════════════════════════

def screen_ticker(row: dict, insider_map: dict[str, dict]) -> dict:
    ticker = row["ticker"]
    market_cap = _num(row.get("market_value"))
    sector = row.get("sector")
    industry = row.get("industry")

    metric_blob = _fetch_metric(ticker)
    rec_blob    = _fetch_recommendation(ticker)

    t1, e1 = trait_small_base(market_cap), {"market_cap": market_cap}
    t2, e2 = trait_durable_growth(metric_blob)
    t3, e3 = trait_unit_economics(metric_blob)
    t4, e4 = trait_large_tam(sector, market_cap)
    t5, e5 = trait_aligned_owners(metric_blob, insider_map.get(ticker.upper()))
    t6, e6 = trait_under_discovered(rec_blob)

    trait_scores = {
        "small_base":           round(float(t1), 1),
        "durable_growth":       round(float(t2), 1),
        "improving_unit_econ":  round(float(t3), 1),
        "large_tam_proxy":      round(float(t4), 1),
        "aligned_owners":       round(float(t5), 1),
        "under_discovered":     round(float(t6), 1),
    }
    passes = sum(1 for v in trait_scores.values() if v >= PASS_THRESHOLD)

    # Pull out the raw metrics we want to persist for the scorer.
    metric = (metric_blob or {}).get("metric") or {}

    return {
        "ticker": ticker,
        "name": row.get("name"),
        "sector": sector,
        "industry": industry,
        "market_cap": market_cap,
        "trait_scores": trait_scores,
        "traits_passed": passes,
        "revenue_cagr_3y": e2.get("revenue_cagr_3y"),
        "revenue_growth_ttm_yoy": e2.get("revenue_growth_ttm_yoy"),
        "gross_margin_ttm": e3.get("gross_margin_ttm"),
        "gross_margin_trend": e3.get("gm_trend_bps"),
        "operating_margin_ttm": e3.get("operating_margin_ttm"),
        "operating_margin_trend": e3.get("om_trend_bps"),
        "insider_ownership_pct": e5.get("insider_ownership_pct"),
        "analyst_count": e6.get("analyst_count"),
        "institutional_ownership_pct": _num(metric.get("institutionalOwnershipPercentage")),
        "debt_to_equity": _num(metric.get("totalDebt/totalEquityQuarterly")),
        # cash runway requires cash + burn rate, not in metric; leave None
        "cash_runway_months": None,
        "evidence": {"t1": e1, "t2": e2, "t3": e3, "t4": e4, "t5": e5, "t6": e6},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def run_screen(sample: int | None = None,
               tickers: list[str] | None = None,
               throttle_ms: int = 1100) -> pd.DataFrame:
    if not UNIVERSE_PATH.exists():
        raise SystemExit(f"Run universe.py first -- {UNIVERSE_PATH} missing")
    universe = pd.read_csv(UNIVERSE_PATH)
    # Drop SPAC units / rights / warrants (tickers ending in U, W, R when 5+ chars):
    # these dominate the IPO list and never have fundamentals.
    bad_suffix = universe["ticker"].str.match(r"^[A-Z]+(U|W|R)$") & (universe["ticker"].str.len() >= 4)
    universe = universe[~bad_suffix].reset_index(drop=True)

    if tickers:
        wanted = {t.upper().strip() for t in tickers}
        universe = universe[universe["ticker"].isin(wanted)]
    elif sample:
        # Skew the sample toward the sweet spot ($200M-$2B) so a small demo
        # actually exercises trait 1 well.
        sweet = universe[
            (universe["market_value"] >= 200e6) & (universe["market_value"] <= 2e9)
        ]
        other = universe[
            (universe["market_value"] < 200e6) | (universe["market_value"] > 2e9)
        ]
        s_sweet = sweet.sample(min(int(sample * 0.7), len(sweet)), random_state=42)
        s_other = other.sample(min(sample - len(s_sweet), len(other)), random_state=42)
        universe = pd.concat([s_sweet, s_other]).reset_index(drop=True)
    log.info("Screening %d tickers", len(universe))

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    insider_map = _form4_insider_map(db)
    log.info("Form 4 insider data: %d tickers with directional signals", len(insider_map))

    today = date.today()
    out_rows: list[dict] = []
    for i, row in enumerate(universe.to_dict("records"), 1):
        try:
            res = screen_ticker(row, insider_map)
        except Exception as exc:
            log.warning("  %s screen failed: %s", row.get("ticker"), exc)
            continue
        res["screen_date"] = today.isoformat()
        out_rows.append(res)
        if i % 25 == 0:
            log.info("  …%d/%d (last: %s, passes=%d)",
                     i, len(universe), res["ticker"], res["traits_passed"])
        time.sleep(throttle_ms / 1000.0)

    df = pd.DataFrame(out_rows)
    log.info("Screen complete.")
    log.info("Trait pass distribution: %s",
             df["traits_passed"].value_counts().sort_index().to_dict())
    return df


def _clean_for_json(v):
    """NaN/inf -> None, recursive on dict/list. Postgrest/httpx serialises
    with allow_nan=False so naked NaN aborts the whole upsert."""
    import math
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, dict):
        return {k: _clean_for_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean_for_json(x) for x in v]
    return v


def persist(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    payload = []
    for r in df.to_dict("records"):
        evidence = r.pop("evidence", None)
        payload.append(_clean_for_json({
            "ticker": r["ticker"],
            "screen_date": r["screen_date"],
            "name": r.get("name"),
            "sector": r.get("sector"),
            "industry": r.get("industry"),
            "market_cap": r.get("market_cap"),
            "trait_scores": r["trait_scores"],
            "traits_passed": int(r["traits_passed"]),
            "revenue_cagr_3y": r.get("revenue_cagr_3y"),
            "revenue_growth_ttm_yoy": r.get("revenue_growth_ttm_yoy"),
            "gross_margin_ttm": r.get("gross_margin_ttm"),
            "gross_margin_trend": r.get("gross_margin_trend"),
            "operating_margin_ttm": r.get("operating_margin_ttm"),
            "operating_margin_trend": r.get("operating_margin_trend"),
            "insider_ownership_pct": r.get("insider_ownership_pct"),
            "analyst_count": int(r["analyst_count"]) if r.get("analyst_count") is not None and not (isinstance(r["analyst_count"], float) and pd.isna(r["analyst_count"])) else None,
            "institutional_ownership_pct": r.get("institutional_ownership_pct"),
            "debt_to_equity": r.get("debt_to_equity"),
            "cash_runway_months": r.get("cash_runway_months"),
            "notes": json.dumps({"evidence": _clean_for_json(evidence)}, default=str) if evidence else None,
        }))
    # Chunk to avoid 413/timeouts on big upserts.
    n = 0
    for i in range(0, len(payload), 200):
        chunk = payload[i:i+200]
        db.table("multibagger_candidates").upsert(chunk, on_conflict="tenant_id,ticker,screen_date").execute()
        n += len(chunk)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated ticker subset")
    ap.add_argument("--throttle-ms", type=int, default=1100,
                    help="Per-ticker sleep to respect Finnhub free-tier limits")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    df = run_screen(sample=args.sample, tickers=tickers, throttle_ms=args.throttle_ms)
    written = persist(df)
    log.info("Wrote %d screened rows.", written)
    top = df.sort_values("traits_passed", ascending=False).head(20)
    log.info("Top passes:\n%s",
             top[["ticker", "name", "market_cap", "traits_passed", "trait_scores"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
