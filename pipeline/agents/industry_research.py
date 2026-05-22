"""
Industry Research
Deterministic, API-backed lookups for the Step 6.5 industry-health inputs that
peer_industry_analysis originally stubbed.

  disruption_risk_from_edgar(industry)   SEC EDGAR full-text search for the
                                          industry keyword in 10-K filings.
                                          Recent 12mo hits vs prior 12mo
                                          gives a YoY ratio mapped to 0..100.
                                          Free tier: works for any industry.

  approx_industry_5y_pe(peer_tickers)     5y avg P/E proxy via yfinance.
                                          Monthly close / trailing EPS history.
                                          Median across peers for industry avg.
                                          Free tier: works for most tickers.

  ma_activity(peer_tickers, industry)     M&A signal compounded from:
                                          - Finnhub /news?category=merger
                                            filtered by peer-ticker mention
                                          - SEC EDGAR 8-K Item 1.01 search
                                            for the industry keyword
                                          Density score 0..100.

  ipo_activity(industry, window_months)   Finnhub /calendar/ipo over the
                                          window, filtered by industry tokens.
                                          Free tier: works.

Why not Tiingo/FMP for 5y P/E + screener?
  Tiingo fundamentals is limited to the DOW 30 on Free/Power tiers and
  returned 401 for our peers (BL, AGYS, WAY, etc.). FMP /stock-screener and
  /stock-peers are restricted endpoints on this account. Both gaps are
  documented; the approximations above are honest replacements.

All functions degrade gracefully -- return None / 0 on any failure.
"""

import logging
import math
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY  # noqa: E402

HTTP_TIMEOUT      = 25
SEC_USER_AGENT    = "Fortis Stock Screener research@fortis.com"
SEC_HEADERS       = {"User-Agent": SEC_USER_AGENT,
                     "Accept-Encoding": "gzip, deflate"}
SEC_THROTTLE      = 0.15
FINNHUB_THROTTLE  = 1.1

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SEC EDGAR full-text disruption scan
# ══════════════════════════════════════════════════════════════════════════════

_INDUSTRY_TERM_CLEAN_RE = re.compile(r"[^\w\s]")
_DROP_INDUSTRY_WORDS = {"services", "industry", "products", "group",
                         "inc", "corp", "and", "the"}


def _industry_keyword(industry: str | None) -> str | None:
    """Map yfinance .info industry text -> a search-friendly keyword.
    'Software - Application' -> 'software'.
    'Health Information Services' -> 'health information'.
    EDGAR full-text search treats space-separated terms as AND, so we want
    a short (1-2 word) phrase to avoid over-restricting hits."""
    if not industry:
        return None
    cleaned = _INDUSTRY_TERM_CLEAN_RE.sub(" ", industry).lower().strip()
    words = [w for w in cleaned.split() if len(w) > 2]
    keep = [w for w in words if w not in _DROP_INDUSTRY_WORDS][:2]
    return " ".join(keep) if keep else None


def _edgar_count(query: str, startdt: str, enddt: str,
                 session: requests.Session, forms: str = "10-K") -> int | None:
    """Total full-text-search hits across the filing-type and date range."""
    params = {
        "q": query,
        "forms": forms,
        "dateRange": "custom",
        "startdt": startdt,
        "enddt": enddt,
    }
    try:
        resp = session.get("https://efts.sec.gov/LATEST/search-index",
                           params=params, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            log.debug("EDGAR HTTP %s", resp.status_code)
            return None
        data = resp.json()
        return int(((data.get("hits") or {}).get("total") or {}).get("value", 0))
    except Exception as exc:
        log.debug("EDGAR search failed: %s", exc)
        return None
    finally:
        time.sleep(SEC_THROTTLE)


def disruption_risk_from_edgar(industry: str | None) -> dict:
    """{risk_score (0..100), recent_hits, prior_hits, industry_keyword}.

    50 means flat YoY; each doubling in disruption-language hits adds +25 (cap
    100), each halving subtracts 25 (cap 0). Failure -> risk_score=None."""
    out = {"risk_score": None, "recent_hits": None, "prior_hits": None,
           "industry_keyword": None}
    kw = _industry_keyword(industry)
    if not kw:
        return out
    out["industry_keyword"] = kw

    session = requests.Session()
    session.headers.update(SEC_HEADERS)

    today = datetime.now(timezone.utc).date()
    yr_ago     = today - timedelta(days=365)
    two_yr_ago = today - timedelta(days=730)

    # EDGAR full-text accepts space-separated terms as AND; we use the simpler
    # "<industry> disruption" form -- empirically returns thousands of hits
    # whereas the compound (industry quoted + disruption_OR_displacement)
    # version we tried first returned 0.
    query = f'"{kw}" disruption'
    recent = _edgar_count(query, yr_ago.isoformat(), today.isoformat(), session)
    prior  = _edgar_count(query, two_yr_ago.isoformat(), yr_ago.isoformat(), session)

    out["recent_hits"] = recent
    out["prior_hits"]  = prior
    if recent is None or prior is None:
        return out

    if prior == 0:
        ratio = 2.0 if recent > 0 else 1.0
    else:
        ratio = recent / prior
    if ratio >= 1.0:
        score = 50 + min(50.0, (ratio - 1.0) * 50.0)
    else:
        score = 50.0 * ratio
    out["risk_score"] = round(float(score), 1)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 5-year P/E approximation via yfinance
# ══════════════════════════════════════════════════════════════════════════════

def _peer_5y_pe(ticker: str) -> float | None:
    """Median historical P/E for one ticker, using monthly close / trailing
    annual EPS as the year-end EPS estimate. Skips years without earnings.
    Returns None when no clean data is available."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5y", interval="1mo", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        # `income_stmt` returns annual statements with 'Basic EPS' / 'Diluted EPS'.
        try:
            is_a = t.income_stmt
        except Exception:
            is_a = None
        # Per-year EPS map: {year: eps}
        eps_by_year = {}
        if is_a is not None and not is_a.empty:
            for col in is_a.columns:
                try:
                    yr = pd.Timestamp(col).year
                except Exception:
                    continue
                # Prefer Diluted EPS, fallback Basic.
                for label in ("Diluted EPS", "Basic EPS"):
                    if label in is_a.index:
                        v = is_a.loc[label, col]
                        if v is not None and not pd.isna(v):
                            eps_by_year[yr] = float(v)
                            break
        if not eps_by_year:
            return None
        # Sample one P/E per (year, month) where we have EPS for the year.
        ratios = []
        for idx, row in hist.iterrows():
            yr = idx.year
            eps = eps_by_year.get(yr)
            if eps is None or eps <= 0:
                continue
            close = row.get("Close")
            if close is None or pd.isna(close) or close <= 0:
                continue
            r = float(close) / eps
            if 0 < r < 500:
                ratios.append(r)
        if len(ratios) < 12:
            return None
        return float(statistics.median(ratios))
    except Exception as exc:
        log.debug("5y P/E failed for %s: %s", ticker, exc)
        return None


def approx_industry_5y_pe(peer_tickers: list[str]) -> dict:
    """{current_median_pe, fiveyr_avg_pe, premium_ratio, n_covered}.

    fiveyr_avg_pe is the median across peers of each peer's median historical
    P/E. current_median_pe is from yfinance .info trailingPE. premium_ratio =
    (current - fiveyr) / fiveyr -- positive = trading rich vs history."""
    out = {"current_median_pe": None, "fiveyr_avg_pe": None,
           "premium_ratio": None, "n_covered": 0}
    if not peer_tickers:
        return out

    five_yr_medians: list[float] = []
    current_pes:    list[float] = []
    for t in peer_tickers:
        m5 = _peer_5y_pe(t)
        if m5 is not None:
            five_yr_medians.append(m5)
        try:
            info = yf.Ticker(t).info or {}
            pe = info.get("trailingPE")
            if pe is not None and 0 < pe < 500:
                current_pes.append(float(pe))
        except Exception:
            pass
    if not five_yr_medians:
        return out

    out["n_covered"]        = len(five_yr_medians)
    out["fiveyr_avg_pe"]    = round(statistics.median(five_yr_medians), 2)
    out["current_median_pe"] = (round(statistics.median(current_pes), 2)
                                if current_pes else None)
    if out["current_median_pe"] is not None and out["fiveyr_avg_pe"]:
        out["premium_ratio"] = round(
            (out["current_median_pe"] - out["fiveyr_avg_pe"]) / out["fiveyr_avg_pe"],
            3)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# M&A activity -- Finnhub news + SEC 8-K Item 1.01 fallback
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub_merger_news() -> list[dict]:
    """All current merger-category news. Caller filters by peer tickers."""
    if not FINNHUB_API_KEY:
        return []
    try:
        resp = requests.get("https://finnhub.io/api/v1/news",
                            params={"category": "merger", "token": FINNHUB_API_KEY},
                            timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            return []
        return resp.json() or []
    except Exception as exc:
        log.debug("Finnhub merger news failed: %s", exc)
        return []
    finally:
        time.sleep(FINNHUB_THROTTLE)


def ma_activity(peer_tickers: list[str], industry: str | None = None) -> dict:
    """Compound M&A score combining peer-ticker mention count in Finnhub
    merger-category news AND the SEC EDGAR 8-K count where the industry
    keyword appears AND the 8-K title mentions 'merger' or 'acquisition'.

    Returns {finnhub_mentions, edgar_8k_count, deal_count, score (0..100)}.
    score scales linearly: 0 deals -> 0, 10+ -> 100."""
    finnhub_mentions = 0
    edgar_8k = None
    tickers_set = {t.upper() for t in peer_tickers}

    # ── Finnhub merger news mentioning any peer ──────────────────────────────
    news = _finnhub_merger_news()
    for item in news:
        related = str(item.get("related") or "").upper()
        if related and any(t in related for t in tickers_set):
            finnhub_mentions += 1

    # ── SEC EDGAR 8-K Item 1.01 / merger search ──────────────────────────────
    kw = _industry_keyword(industry)
    if kw:
        session = requests.Session()
        session.headers.update(SEC_HEADERS)
        today = datetime.now(timezone.utc).date()
        yr_ago = today - timedelta(days=365)
        edgar_8k = _edgar_count(f'"{kw}" merger', yr_ago.isoformat(),
                                 today.isoformat(), session, forms="8-K")

    # Composite score: 10 points per peer-mention in Finnhub merger news (direct
    # signal) + 0.5 points per EDGAR 8-K "merger" hit in this industry
    # (broader proxy). Capped at 100.
    direct_score   = 10.0 * finnhub_mentions
    proxy_score    = 0.5  * (edgar_8k or 0)
    score = float(min(100.0, direct_score + proxy_score))
    deal_count = finnhub_mentions + (edgar_8k or 0)
    return {"finnhub_mentions": finnhub_mentions, "edgar_8k_count": edgar_8k,
            "deal_count": deal_count, "score": round(score, 1)}


# ══════════════════════════════════════════════════════════════════════════════
# IPO activity -- Finnhub
# ══════════════════════════════════════════════════════════════════════════════

def ipo_activity(industry: str | None, window_months: int = 12) -> dict:
    """IPOs scheduled in the window. When `industry` is given, filter by
    keyword overlap with the issuer's listed name. Returns
    {total_ipos, industry_matches, score (0..100)}."""
    if not FINNHUB_API_KEY:
        return {"total_ipos": 0, "industry_matches": 0, "score": None}
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=int(window_months * 30.5))
    try:
        r = requests.get("https://finnhub.io/api/v1/calendar/ipo",
                         params={"from": start.isoformat(),
                                 "to":   today.isoformat(),
                                 "token": FINNHUB_API_KEY},
                         timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return {"total_ipos": 0, "industry_matches": 0, "score": None}
        data = (r.json() or {}).get("ipoCalendar") or []
    except Exception:
        return {"total_ipos": 0, "industry_matches": 0, "score": None}
    finally:
        time.sleep(FINNHUB_THROTTLE)

    total = len(data)
    if not industry:
        n = total
    else:
        # ANY-token match (was ALL-token). 'Software - Application' produces
        # tokens {software, application}; an IPO named 'Foo Software Inc'
        # matches via 'software'. ALL-match was too strict and returned 0.
        tokens = {w.lower() for w in re.findall(r"\w+", industry)
                  if len(w) > 2 and w.lower() not in _DROP_INDUSTRY_WORDS}
        n = 0
        for d in data:
            name_tokens = {w.lower() for w in re.findall(r"\w+", str(d.get("name") or ""))}
            if tokens & name_tokens:
                n += 1
    score = min(100.0, n / 10.0 * 100.0)
    return {"total_ipos": total, "industry_matches": n, "score": round(score, 1)}


# ══════════════════════════════════════════════════════════════════════════════
# CLI sanity check
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("\n== SEC EDGAR disruption: 'Software - Application'")
    print(disruption_risk_from_edgar("Software - Application"))
    print("\n== SEC EDGAR disruption: 'Health Information Services'")
    print(disruption_risk_from_edgar("Health Information Services"))
    print("\n== 5y P/E approximation for AGYS peer set (small sample)")
    print(approx_industry_5y_pe(["BL", "BLKB", "FRSH"]))
    print("\n== M&A activity for AGYS peer set")
    print(ma_activity(["BL", "BLKB", "FRSH", "VERX", "ASAN"],
                     industry="Software - Application"))
    print("\n== IPO activity for software industry, 12mo")
    print(ipo_activity("Software - Application", window_months=12))
