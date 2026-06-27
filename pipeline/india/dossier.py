"""
India Equity Dossier Generator
==============================

Ticker-in → full styled PDF dossier-out, for NSE/BSE names. This is the
India counterpart to the US daily-scan research page, built on the data sources
that actually work for Indian equities today:

    • Price action & technicals  — yfinance .NS history (returns, vs-Nifty,
                                     RSI, DMAs, volatility, 52w range, drawdown)
    • Fundamentals               — india_fundamentals adapter (valuation,
                                     quality, growth, margins, leverage, analysts)
    • Price reference band       — scan.price_bands zero-drift Monte-Carlo cone
    • Your position              — cost / value / P&L joined from the holdings xlsx
    • LLM thesis                 — bull / bear / valuation / what-to-watch / verdict,
                                     grounded STRICTLY in the computed data above

Honest scope: the catalyst / insider / filings / news sections of the US funnel
read SEC/Finnhub/NSE-news tables that are not yet wired for India. Those blocks
are disclosed as "pending India feed" rather than faked.

This module only computes the dossier payload + renders HTML. PDF conversion and
the run loop live in render.py / __main__ here.

CLI
    python -m pipeline.india.dossier --tickers RELIANCE.NS,SBIN.NS
    python -m pipeline.india.dossier --portfolio   # core holdings from the xlsx
"""

from __future__ import annotations

import json
import re
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE))

from data import india_fundamentals as ind          # noqa: E402
from india import feeds                              # noqa: E402
from scan.price_bands import _band                   # noqa: E402

try:
    from llm import complete as llm_complete          # noqa: E402
except Exception:                                     # noqa: BLE001
    llm_complete = None

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("india.dossier")

NIFTY = "^NSEI"


# ══════════════════════════════════════════════════════════════════════════════
# Core portfolio map (canonical xlsx name → NSE Yahoo symbol)
# ══════════════════════════════════════════════════════════════════════════════

# Every listed equity in the book → its NSE Yahoo symbol. Symbols that fail to
# resolve at run time are auto-demoted to the appendix (see main()), so a flaky
# Yahoo response never produces an empty dossier.
CORE_MAP = {
    "Reliance Industries Ltd":                  "RELIANCE.NS",
    "State Bank of India":                      "SBIN.NS",
    "BSE Ltd":                                  "BSE.NS",
    "ICICI Bank Ltd":                           "ICICIBANK.NS",
    "ICICI Prudential Asset Management Co Ltd":  "ICICIAMC.NS",
    "Larsen & Toubro Ltd":                      "LT.NS",
    "Bharat Electronics Ltd":                   "BEL.NS",
    "JSW Steel Ltd":                            "JSWSTEEL.NS",
    "Computer Age Management Services Ltd":      "CAMS.NS",
    "Waaree Renewable Technologies Ltd":         "WAAREERTL.NS",
    "HDFC Bank Ltd":                            "HDFCBANK.NS",
    "Mahindra & Mahindra Ltd":                  "M&M.NS",
    "Vedanta Ltd":                              "VEDL.NS",
    "Tata Motors Ltd":                          "TATAMOTORS.NS",
    "Adani Green Energy Ltd":                   "ADANIGREEN.NS",
    "Samvardhana Motherson International Ltd":   "MOTHERSON.NS",
    "Bajaj Auto Ltd":                           "BAJAJ-AUTO.NS",
    "JBM Auto Ltd":                             "JBMA.NS",
    "Tata Power Company Ltd":                   "TATAPOWER.NS",
    "Amara Raja Energy & Mobility Ltd":          "ARE&M.NS",
    "HBL Engineering Ltd":                      "HBLENGINE.NS",
}

# Positions that cannot carry a market dossier — included in the report appendix
# with their position and a status, so no holding is left out.
NOTE_ONLY = {
    "Vedanta Aluminium Metal Ltd":      "Vedanta demerger entity, not separately listed / illiquid. The +768% mark is a thin notional.",
    "Tata Motors Passenger Vehicles Ltd": "Tata Motors demerger (passenger-vehicle) entity, awaiting separate listing / illiquid.",
    "Vedanta Power Ltd":                "Vedanta demerger entity, illiquid. Marked down ~52%.",
    "Vedanta Oil and Gas Ltd":          "Vedanta demerger entity, illiquid. Marked down ~78%.",
    "Vedanta Iron & Steel Ltd":         "Vedanta demerger entity, illiquid. Marked down ~42%.",
    "SBI PSU Fund - Growth":            "Mutual fund (NAV-based), not an exchange-listed equity.",
    "Reliance Communications Ltd":      "Under NCLT insolvency, near-zero value (Rs 9). Recommend write-off.",
    "Balasore Alloys Ltd":              "Delisted / suspended, value Rs 0. Recommend write-off.",
}

HOLDINGS_XLSX = Path.home() / "Downloads" / "SBA porfolio Holings as on 23-6-26.xlsx"


# ══════════════════════════════════════════════════════════════════════════════
# Data payload
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Dossier:
    ticker: str
    name: str = ""
    sector: str = ""
    snapshot: dict = field(default_factory=dict)
    technicals: dict = field(default_factory=dict)
    fundamentals: dict = field(default_factory=dict)
    band: dict | None = None
    spark: list = field(default_factory=list)
    analysts: dict = field(default_factory=dict)
    position: dict | None = None
    news: list = field(default_factory=list)
    events: dict = field(default_factory=dict)
    ownership: dict = field(default_factory=dict)
    thesis: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    generated_at: str = ""


def _pct(a, b):
    if a is None or b in (None, 0) or pd.isna(a) or pd.isna(b):
        return None
    return (a / b - 1.0) * 100.0


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    val = 100 - 100 / (1 + rs.iloc[-1])
    return None if pd.isna(val) else round(float(val), 1)


def _technicals(close: pd.Series, nifty: pd.Series | None) -> dict:
    close = close.dropna()
    if len(close) < 30:
        return {}
    last = float(close.iloc[-1])

    def ret(n):
        return round(_pct(last, float(close.iloc[-n - 1])), 1) if len(close) > n else None

    win = close.tail(252)
    hi, lo = float(win.max()), float(win.min())
    logret = np.log(close / close.shift(1)).dropna()
    vol = round(float(logret.tail(252).std() * np.sqrt(252) * 100), 1) if len(logret) > 20 else None
    dma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    dma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    roll_max = win.cummax()
    max_dd = round(float(((win - roll_max) / roll_max).min() * 100), 1) if len(win) else None

    rel6m = None
    if nifty is not None and len(nifty.dropna()) > 126:
        n = nifty.dropna()
        s6 = ret(126)
        n6 = _pct(float(n.iloc[-1]), float(n.iloc[-127]))
        if s6 is not None and n6 is not None:
            rel6m = round(s6 - n6, 1)

    return {
        "price": round(last, 2),
        "ret_1m": ret(21), "ret_3m": ret(63), "ret_6m": ret(126), "ret_1y": ret(252),
        "vs_nifty_6m": rel6m,
        "vol_ann_pct": vol,
        "rsi14": _rsi(close),
        "high_52w": round(hi, 2), "low_52w": round(lo, 2),
        "from_high_pct": round(_pct(last, hi), 1),
        "dma50": round(dma50, 2) if dma50 else None,
        "dma200": round(dma200, 2) if dma200 else None,
        "above_dma200": (dma200 is not None and last > dma200),
        "golden_cross": (dma50 is not None and dma200 is not None and dma50 > dma200),
        "max_drawdown_1y_pct": max_dd,
    }


def _fundamentals(ticker: str, info: dict) -> tuple[dict, dict, dict, str]:
    """Returns (snapshot, fundamentals, analysts, coverage_str)."""
    blob = ind.metric_blob(ticker)
    m = blob.get("metric", {})
    rec = ind.recommendation(ticker)
    latest = rec[0] if rec else {}
    analyst_n = sum(int(latest.get(k, 0) or 0)
                    for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))

    snapshot = {
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "dividend_yield": info.get("dividendYield"),
        "roe": (info.get("returnOnEquity") * 100) if info.get("returnOnEquity") is not None else None,
    }
    fundamentals = {
        "revenue_cagr_3y": m.get("revenueGrowth3Y"),
        "revenue_growth_ttm": m.get("revenueGrowthTTMYoy"),
        "revenue_growth_qtr": m.get("revenueGrowthQuarterlyYoy"),
        "gross_margin_ttm": m.get("grossMarginTTM"),
        "gross_margin_5y": m.get("grossMargin5Y"),
        "operating_margin_ttm": m.get("operatingMarginTTM"),
        "operating_margin_5y": m.get("operatingMargin5Y"),
        "debt_to_equity": m.get("totalDebt/totalEquityQuarterly"),
        "coverage": blob.get("_coverage"),
    }
    analysts = {
        "count": analyst_n,
        "recommendation": info.get("recommendationKey"),
        "mean": info.get("recommendationMean"),
        "target_mean": info.get("targetMeanPrice"),
    }
    return snapshot, fundamentals, analysts, f"{analyst_n} analysts"


# ══════════════════════════════════════════════════════════════════════════════
# LLM thesis — grounded strictly in the computed payload
# ══════════════════════════════════════════════════════════════════════════════

_THESIS_SYSTEM = (
    "You are an experienced buy-side equity analyst writing a sober, "
    "evidence-bound note for a private investor who will share it with market "
    "professionals. Use ONLY the JSON data provided. Do not invent catalysts, "
    "management quotes, order books, or numbers that are not in the data. The "
    "bear case must be as rigorous as the bull case. "
    "Write plain, specific, professional prose. Do NOT use hype ('multibagger', "
    "'to the moon', 'guaranteed', 'rocket') and do NOT use AI-tell filler words: "
    "no 'delve', 'moreover', 'furthermore', 'notably', 'it is worth noting', "
    "'robust', 'leverage' as a verb, 'navigate', 'landscape', 'underscore', "
    "'testament', 'pivotal', 'in conclusion', 'overall'. Never use em-dashes; "
    "use a period, comma, or colon instead. Vary sentence length. If the data is "
    "thin, say so plainly. Return strict JSON only."
)

_THESIS_SCHEMA = (
    '{"snapshot_read": str (2-3 sentences on what the data says today), '
    '"bull": [str, str, str] (each a specific, data-anchored point), '
    '"bear": [str, str, str], '
    '"valuation_view": str (is it cheap/fair/rich on the multiples vs its growth/quality), '
    '"catalysts": [str, str] (near-term events or recent news from the data that could move it; '
    '"" entries if none in the data), '
    '"what_to_watch": [str, str, str], '
    '"verdict": one of ["Accumulate","Hold","Trim","Avoid"], '
    '"verdict_rationale": str (2-3 sentences; reference their cost basis if a position is given), '
    '"position_action": str (1 sentence specific to their holding, or "" if no position)}'
)


def _loads_lenient(text: str) -> dict:
    """Parse JSON that may arrive fenced (```json ... ```) or with stray prose
    around the object. Falls back to the outermost {...} span."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        return json.loads(t, strict=False)   # strict=False tolerates literal newlines in strings
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(t[i:j + 1], strict=False)
        raise


# Deterministic de-AI pass on generated prose: em-dashes and filler words are the
# tells the model still slips in occasionally despite the system prompt. Finance
# nouns like "leverage" are deliberately NOT touched (debt leverage is legitimate).
_WORD_SUB = {
    "robust": "solid", "elevate": "lift", "delve": "examine", "moreover": "also",
    "furthermore": "also", "navigate": "manage", "underscore": "highlight",
    "pivotal": "key", "testament": "sign", "seamless": "smooth",
}


def _match_case(src: str, repl: str) -> str:
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _sanitize_prose(s):
    if not isinstance(s, str):
        return s
    s = (s.replace(" — ", ", ").replace(" – ", ", ")
           .replace("—", ", ").replace("–", ", "))
    for w, r in _WORD_SUB.items():
        s = re.sub(rf"(?i)\b{re.escape(w)}\b",
                   lambda m, _r=r: _match_case(m.group(0), _r), s)
    s = re.sub(r"\s{2,}", " ", s).replace(" ,", ",").replace(" .", ".").strip()
    return s


def _sanitize_thesis(out: dict) -> dict:
    for k, v in out.items():
        if isinstance(v, str):
            out[k] = _sanitize_prose(v)
        elif isinstance(v, list):
            out[k] = [_sanitize_prose(x) for x in v]
    return out


def _thesis(d: Dossier) -> dict:
    if llm_complete is None:
        return {"verdict": "Hold", "verdict_rationale": "LLM gateway unavailable; data-only dossier.",
                "snapshot_read": "", "bull": [], "bear": [], "what_to_watch": [],
                "valuation_view": "", "position_action": ""}
    payload = {
        "ticker": d.ticker, "name": d.name, "sector": d.sector,
        "snapshot": d.snapshot, "technicals": d.technicals,
        "fundamentals": d.fundamentals, "analysts": d.analysts,
        "price_band_21d": d.band, "your_position": d.position,
        "recent_news": [n.get("title") for n in (d.news or [])[:6]],
        "corporate_events": d.events,
        "ownership": d.ownership,
    }
    prompt = (
        "Write the dossier thesis as JSON matching this schema:\n"
        + _THESIS_SCHEMA
        + "\n\nDATA:\n" + json.dumps(payload, default=str)
    )
    last_exc = None
    for attempt in range(3):
        text = None
        try:
            text, provider = llm_complete(prompt, system=_THESIS_SYSTEM,
                                          temperature=0.2, max_tokens=3000, json_mode=True)
            if not text:
                raise RuntimeError("empty LLM response")
            out = _sanitize_thesis(_loads_lenient(text))
            out["_provider"] = provider
            return out
        except Exception as exc:  # noqa: BLE001 — truncated/malformed JSON, rate-limit, etc.
            last_exc = exc
            log.info("thesis attempt %d for %s failed (%s); retrying", attempt + 1, d.ticker, exc)
    log.warning("thesis LLM failed for %s after retries: %s", d.ticker, last_exc)
    return {"verdict": "Hold",
            "verdict_rationale": "Thesis generation was unavailable on this run; see the data tables above.",
            "snapshot_read": "", "bull": [], "bear": [], "what_to_watch": [],
            "valuation_view": "", "position_action": ""}


# ══════════════════════════════════════════════════════════════════════════════
# Position context from the holdings workbook
# ══════════════════════════════════════════════════════════════════════════════

def load_positions(xlsx: Path = HOLDINGS_XLSX) -> dict[str, dict]:
    """{canonical_name: position dict} keyed by CORE_MAP names (best-effort match)."""
    if not xlsx.exists():
        return {}
    raw = pd.ExcelFile(xlsx).parse("Portfolio Tracker", header=None)
    hdr = [str(x).strip() for x in raw.iloc[5].tolist()]
    df = raw.iloc[6:].copy()
    df.columns = hdr

    def num(x):
        if pd.isna(x):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        try:
            return float(str(x).replace(",", "").strip())
        except ValueError:
            return None

    out: dict[str, dict] = {}
    total_cur = 0.0
    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("ISIN")) or pd.isna(r.get("Invested Value")):
            continue
        name = str(r.get("Script Name")).strip()
        cur = num(r.get("Current Value")) or 0
        total_cur += cur
        rows.append((name, r))
    for canon in CORE_MAP:
        for name, r in rows:
            if name.startswith(canon[:18]) or canon.startswith(name[:18]):
                inv = num(r.get("Invested Value"))
                cur = num(r.get("Current Value"))
                out[canon] = {
                    "qty": num(r.get("Quantity")),
                    "avg_cost": num(r.get("Avg Unit Cost")),
                    "invested": inv,
                    "current_value": cur,
                    "current_price": num(r.get("Current Price")),
                    "unrealized_pnl": num(r.get("Unrealized P&L")),
                    "return_pct": round(_pct(cur, inv), 1) if inv else None,
                    "weight_in_book_pct": round(cur / total_cur * 100, 2) if total_cur else None,
                }
                break
    return out


_GOLD_KW = ("SGB", "GOLD", "SILVER", "GOVT_OFIND", "CENTRAL GOVERNMENT LOAN")


def portfolio_summary(xlsx: Path = HOLDINGS_XLSX) -> dict:
    """Whole-book summary for the report cover: totals, gold/equity split,
    sleeves, top holdings, cleanup candidates."""
    raw = pd.ExcelFile(xlsx).parse("Portfolio Tracker", header=None)
    # header block (client name / holding date) lives in rows 1-4, col 0/1 & 3/5
    meta = {}
    for i in range(1, 5):
        for kc, vc in ((0, 1), (3, 5)):
            k = raw.iat[i, kc]
            if isinstance(k, str) and k.strip():
                meta[k.strip()] = raw.iat[i, vc]
    hdr = [str(x).strip() for x in raw.iloc[5].tolist()]
    df = raw.iloc[6:].copy()
    df.columns = hdr

    def num(x):
        if pd.isna(x):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        try:
            return float(str(x).replace(",", "").strip())
        except ValueError:
            return None

    rows = []
    for _, r in df.iterrows():
        if pd.isna(r.get("ISIN")) or pd.isna(r.get("Invested Value")):
            continue
        name = str(r.get("Script Name")).strip()
        rows.append({
            "name": name, "sector": str(r.get("Sector")).strip(),
            "invested": num(r.get("Invested Value")) or 0,
            "current": num(r.get("Current Value")) or 0,
            "is_gold": any(k in name.upper() for k in _GOLD_KW),
        })
    total_cur = sum(x["current"] for x in rows)
    total_inv = sum(x["invested"] for x in rows)
    for x in rows:
        x["weight"] = x["current"] / total_cur * 100 if total_cur else 0
        x["ret"] = (x["current"] / x["invested"] - 1) * 100 if x["invested"] else None
    gold = [x for x in rows if x["is_gold"]]
    equity = [x for x in rows if not x["is_gold"]]
    gold_cur = sum(x["current"] for x in gold)
    eq_cur = sum(x["current"] for x in equity)
    gold_gain = sum(x["current"] - x["invested"] for x in gold)
    total_gain = total_cur - total_inv
    cleanup = [x for x in equity if x["current"] < 2000 or (x["ret"] is not None and x["ret"] < -40)]

    return {
        "client": str(meta.get("Client Name", "")).strip(),
        "client_code": str(meta.get("Client Code", "")).strip(),
        "holding_date": str(meta.get("Holding Date", "")).strip(),
        "total_current": total_cur, "total_invested": total_inv,
        "total_gain": total_gain, "total_ret_pct": (total_cur / total_inv - 1) * 100 if total_inv else None,
        "gold_current": gold_cur, "gold_weight": gold_cur / total_cur * 100 if total_cur else 0,
        "equity_current": eq_cur, "equity_weight": eq_cur / total_cur * 100 if total_cur else 0,
        "gold_gain_share": gold_gain / total_gain * 100 if total_gain else 0,
        "n_holdings": len(rows), "n_equity": len(equity),
        "gold": sorted(gold, key=lambda x: -x["current"]),
        "holdings": sorted(rows, key=lambda x: -x["current"]),
        "cleanup": sorted(cleanup, key=lambda x: (x["ret"] if x["ret"] is not None else 0)),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Build one dossier
# ══════════════════════════════════════════════════════════════════════════════

def build(ticker: str, position: dict | None = None) -> Dossier:
    log.info("Building dossier: %s", ticker)
    sym = ind.to_yahoo(ticker)
    tk = yf.Ticker(sym)
    try:
        info = tk.info or {}
    except Exception:  # noqa: BLE001
        info = {}
    try:
        hist = tk.history(period="2y", auto_adjust=True)["Close"]
    except Exception as exc:  # noqa: BLE001
        log.warning("history failed for %s: %s", sym, exc)
        hist = pd.Series(dtype=float)
    try:
        nifty = yf.Ticker(NIFTY).history(period="2y", auto_adjust=True)["Close"]
    except Exception:  # noqa: BLE001
        nifty = None

    d = Dossier(ticker=ticker, generated_at=datetime.now(timezone.utc).strftime("%d %b %Y"))
    d.name = info.get("longName") or info.get("shortName") or ticker
    d.sector = info.get("sector") or info.get("industry") or ""
    d.technicals = _technicals(hist, nifty)
    d.snapshot, d.fundamentals, d.analysts, _ = _fundamentals(ticker, info)
    d.snapshot["high_52w"] = d.technicals.get("high_52w")
    d.snapshot["low_52w"] = d.technicals.get("low_52w")
    d.band = _band(hist) if len(hist.dropna()) >= 60 else None
    # ~1-year price series, downsampled to ~90 points for the sparkline
    closes = hist.dropna()
    if len(closes) > 10:
        last = closes.tail(252)
        step = max(1, len(last) // 90)
        d.spark = [round(float(v), 2) for v in last.iloc[::step].tolist()]
    d.position = position
    try:
        fb = feeds.fetch(ticker, company=d.name)
        d.news, d.events, d.ownership = fb.get("news", []), fb.get("events", {}), fb.get("ownership", {})
    except Exception as exc:  # noqa: BLE001
        log.warning("feeds failed for %s: %s", ticker, exc)
    d.flags = [
        "News via Yahoo + Google News (India); corporate events via earnings/dividend calendar; "
        "ownership/insider via Yahoo holders. Full NSE/BSE statutory announcement and SEBI PIT "
        "text are not exposed for free programmatic access, so those are summarised, not reproduced.",
    ]
    d.thesis = _thesis(d)
    return d


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    import argparse
    from india import render

    ap = argparse.ArgumentParser(description="India equity dossier → PDF")
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated NSE symbols, e.g. RELIANCE.NS,SBIN.NS")
    ap.add_argument("--portfolio", action="store_true",
                    help="Use the core holdings from the SBA workbook")
    ap.add_argument("--outdir", type=str,
                    default=str(Path.home() / "Downloads" / "dossiers"))
    ap.add_argument("--split", action="store_true",
                    help="Also write a separate PDF per ticker")
    ap.add_argument("--no-cover", action="store_true",
                    help="Skip the portfolio cover/overview (dossiers only)")
    args = ap.parse_args()

    positions = load_positions()
    rev = {v: k for k, v in CORE_MAP.items()}      # symbol → canonical name
    outdir = Path(args.outdir)
    out = outdir / "Portfolio_Review.pdf"

    # ── ad-hoc ticker mode: just those names, no portfolio framing ──────────
    if args.tickers and not args.portfolio:
        dossiers = []
        for sym in [t.strip() for t in args.tickers.split(",") if t.strip()]:
            try:
                d = build(sym, position=positions.get(rev.get(sym, "")))
                dossiers.append(d)
                if args.split:
                    render.to_pdf(render.document([d]),
                                  outdir / f"{sym.replace('.', '_').replace('&', 'and')}_dossier.pdf")
            except Exception as exc:  # noqa: BLE001
                log.warning("build failed for %s: %s", sym, exc)
        render.to_pdf(render.document(dossiers), out)
        log.info("✓ Report → %s (%d dossiers)", out, len(dossiers))
        return 0

    # ── full portfolio mode: every holding, nothing left out ────────────────
    summary = portfolio_summary()
    holdings_by_name = {h["name"]: h for h in summary["holdings"]}
    dossiers, verdict_by_name, notes = [], {}, []

    for canon, sym in CORE_MAP.items():
        try:
            d = build(sym, position=positions.get(canon))
        except Exception as exc:  # noqa: BLE001
            log.warning("build failed for %s (%s): %s", canon, sym, exc)
            d = None
        if d is None or not (d.technicals or {}).get("price"):
            notes.append({"name": canon, "holding": holdings_by_name.get(canon),
                          "status": "Market data unavailable from Yahoo at run time; not dossiered this run."})
            log.info("  demoted %s → appendix (no data)", sym)
            continue
        dossiers.append(d)
        verdict_by_name[canon] = (d.thesis.get("verdict", ""), d.thesis.get("verdict_rationale", ""))
        log.info("  built %s (verdict %s)", sym, d.thesis.get("verdict"))
        if args.split:
            render.to_pdf(render.document([d]),
                          outdir / f"{sym.replace('.', '_').replace('&', 'and')}_dossier.pdf")

    for canon, status in NOTE_ONLY.items():
        notes.append({"name": canon, "holding": holdings_by_name.get(canon), "status": status})

    html = render.report(summary, dossiers, verdict_by_name, notes)
    render.to_pdf(html, out)
    log.info("✓ Portfolio report → %s (%d dossiers, %d appendix notes)",
             out, len(dossiers), len(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
