"""
India Feeds — news, corporate events, and insider/ownership for NSE/BSE names.

Fills the dossier sections the US funnel gets from SEC EDGAR / Finnhub / news
harvesters, using the free per-ticker sources that actually work for India:

    news()              — yfinance .news (Yahoo aggregate) + Google News RSS
                          (per-company, India edition) as supplement/fallback
    corporate_events()  — next earnings date, ex-dividend, EPS/revenue estimate,
                          recent dividends & splits  (yfinance calendar/actions)
    ownership()         — promoter (insider) %, institutional %, institution
                          count, and any insider transactions  (yfinance holders)

Honest sourcing note for the dossier footer: NSE/BSE/SEBI publish the full
statutory announcement + PIT insider text, but their endpoints block free
programmatic access. So "Corporate events" here is the reliable earnings /
dividend / actions calendar plus filing-driven headlines, and "Ownership" is the
promoter / institutional holding picture (the signal that matters most in India)
plus whatever insider transactions Yahoo exposes. Everything degrades to empty
gracefully and is disclosed, never faked.

Cached per ticker (1-day TTL — news is time-sensitive).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import feedparser
import yfinance as yf

from data import india_fundamentals as ind   # to_yahoo()

log = logging.getLogger("india.feeds")

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "_india_feeds_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SEC = 86_400   # 1 day

_GNEWS = ("https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")


# ── cache ─────────────────────────────────────────────────────────────────────

def _cache(ticker: str) -> Path:
    return CACHE_DIR / f"{ind.to_yahoo(ticker)}.feeds.json"


def _cache_get(ticker: str):
    p = _cache(ticker)
    if p.exists() and time.time() - p.stat().st_mtime < CACHE_TTL_SEC:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(ticker: str, obj) -> None:
    try:
        _cache(ticker).write_text(json.dumps(obj, default=str), encoding="utf-8")
    except Exception:
        pass


# ── news ──────────────────────────────────────────────────────────────────────

def _ts(v):
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.utcfromtimestamp(v).strftime("%d %b %Y")
        s = str(v)
        return s[:10]
    except Exception:
        return str(v)[:10]


def _yf_news(tk) -> list[dict]:
    out = []
    try:
        raw = tk.news or []
    except Exception as exc:  # noqa: BLE001
        log.debug("yf news failed: %s", exc)
        return out
    for n in raw:
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = c.get("title") or n.get("title")
        if not title:
            continue
        prov = c.get("provider")
        publisher = (prov.get("displayName") if isinstance(prov, dict) else None) or n.get("publisher")
        when = _ts(c.get("pubDate") or n.get("providerPublishTime"))
        cu = c.get("canonicalUrl") or c.get("clickThroughUrl")
        link = (cu.get("url") if isinstance(cu, dict) else None) or n.get("link")
        out.append({"title": title.strip(), "publisher": publisher or "—",
                    "when": when, "link": link, "src": "Yahoo"})
    return out


def _gnews(company: str, limit: int = 6) -> list[dict]:
    out = []
    try:
        q = urllib.parse.quote(f'"{company}" when:45d')
        feed = feedparser.parse(_GNEWS.format(q=q))
        for e in feed.entries[:limit]:
            src = ""
            if isinstance(getattr(e, "source", None), dict):
                src = e.source.get("title", "")
            elif hasattr(e, "source"):
                src = getattr(e.source, "title", "")
            out.append({
                "title": (e.get("title") or "").strip(),
                "publisher": src or "Google News",
                "when": _ts(e.get("published")),
                "link": e.get("link"),
                "src": "GoogleNews",
            })
    except Exception as exc:  # noqa: BLE001
        log.debug("gnews failed for %s: %s", company, exc)
    return out


def _dedup_news(items: list[dict], limit: int) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it.get("title") or "")[:60].lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


# ── corporate events ──────────────────────────────────────────────────────────

def _events(tk) -> dict:
    ev: dict = {}
    try:
        cal = tk.calendar or {}
        ed = cal.get("Earnings Date")
        if isinstance(ed, list) and ed:
            ev["next_earnings"] = str(ed[0])
        elif ed:
            ev["next_earnings"] = str(ed)
        ev["ex_dividend"] = str(cal.get("Ex-Dividend Date")) if cal.get("Ex-Dividend Date") else None
        if cal.get("Earnings Average") is not None:
            ev["eps_estimate"] = cal.get("Earnings Average")
        if cal.get("Revenue Average") is not None:
            ev["revenue_estimate"] = cal.get("Revenue Average")
    except Exception as exc:  # noqa: BLE001
        log.debug("calendar failed: %s", exc)
    # recent dividends / splits in the last ~15 months
    try:
        div = tk.dividends
        if div is not None and len(div):
            recent = div.tail(3)
            ev["recent_dividends"] = [
                {"date": str(d.date()), "amount": round(float(v), 2)}
                for d, v in recent.items()
            ]
    except Exception:  # noqa: BLE001
        pass
    try:
        sp = tk.splits
        if sp is not None and len(sp):
            last = sp.tail(1)
            for d, v in last.items():
                ev["last_split"] = {"date": str(d.date()), "ratio": float(v)}
    except Exception:  # noqa: BLE001
        pass
    return ev


# ── ownership / insider ───────────────────────────────────────────────────────

def _ownership(tk) -> dict:
    own: dict = {}
    try:
        mh = tk.major_holders
        if mh is not None and not getattr(mh, "empty", True):
            d = mh.to_dict().get("Value", {})
            own["promoter_insider_pct"] = _pct100(d.get("insidersPercentHeld"))
            own["institutions_pct"] = _pct100(d.get("institutionsPercentHeld"))
            own["institutions_count"] = int(d["institutionsCount"]) if d.get("institutionsCount") else None
    except Exception as exc:  # noqa: BLE001
        log.debug("major_holders failed: %s", exc)
    try:
        it = tk.insider_transactions
        if it is not None and not getattr(it, "empty", True):
            own["insider_txn_count"] = int(len(it))
            # net shares if a Shares + Transaction column exist
            cols = {c.lower(): c for c in it.columns}
            if "text" in cols:
                buys = it[cols["text"]].astype(str).str.contains("Buy", case=False).sum()
                sells = it[cols["text"]].astype(str).str.contains("Sale|Sell", case=False).sum()
                own["insider_buys"] = int(buys)
                own["insider_sells"] = int(sells)
    except Exception as exc:  # noqa: BLE001
        log.debug("insider_transactions failed: %s", exc)
    return own


def _pct100(x):
    try:
        return round(float(x) * 100, 1) if x is not None else None
    except (TypeError, ValueError):
        return None


# ── public API ────────────────────────────────────────────────────────────────

def fetch(ticker: str, company: str | None = None, use_cache: bool = True) -> dict:
    """{news:[...], events:{...}, ownership:{...}} for an NSE/BSE ticker."""
    if use_cache:
        c = _cache_get(ticker)
        if c is not None:
            return c
    sym = ind.to_yahoo(ticker)
    tk = yf.Ticker(sym)
    news = _yf_news(tk)
    if len(news) < 4 and company:
        news = news + _gnews(company)
    bundle = {
        "news": _dedup_news(news, 6),
        "events": _events(tk),
        "ownership": _ownership(tk),
        "fetched_at": datetime.utcnow().strftime("%d %b %Y"),
    }
    if bundle["news"] or bundle["events"] or bundle["ownership"]:
        _cache_put(ticker, bundle)
    return bundle


__all__ = ["fetch"]
