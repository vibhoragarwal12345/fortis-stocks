"""
India Fundamentals Adapter
==========================

Free, no-key fundamentals for NSE/BSE tickers, shaped to be a **drop-in for the
Finnhub free-tier endpoints** the US pipeline already consumes:

    Finnhub /stock/metric?metric=all   →  metric_blob(ticker)
    Finnhub /stock/recommendation      →  recommendation(ticker)
    (yfinance ownership/cash helper)   →  yf_stats(ticker)

So an existing consumer that reads `m["metric"]["grossMarginTTM"]` or
`m["series"]["annual"]["revenue"]` keeps working unchanged — it just gets fed
Indian data computed from yfinance instead of a Finnhub HTTP response.

Why yfinance
------------
Yahoo serves full Indian financial statements for `.NS` / `.BO` tickers
(5y income statement + balance sheet + cash flow, quarterly, key ratios,
analyst recommendation counts). For the liquid tradable universe this matches
the depth Finnhub gives on the US side, at zero cost. See the probe results in
the build notes.

The honest gap
--------------
yfinance is an unofficial Yahoo scrape: coverage thins for micro-caps, history
is ~5y not 10y, and same-day results can lag. Those holes are filled — not by
money — via a free fallback chain (`_fallback_metric`): Screener.in / NSE-BSE
XBRL. That chain is scaffolded here and returns None until built (phase 2); the
yfinance provider already covers the universe the scanner trades.

Units / conventions (must match Finnhub so the traits read correctly)
---------------------------------------------------------------------
* Margins and growth are expressed in **percent** (Finnhub convention:
  `grossMarginTTM == 60` means 60%). yfinance gives ratios, so we ×100.
* `totalDebt/totalEquityQuarterly` is a **ratio** (Finnhub convention), not a
  percent. yfinance `info["debtToEquity"]` is a percent, so we ÷100 there.
* `series.annual.revenue` is a list of `{"period", "v"}` **newest-first**, the
  order `trait_durable_growth` assumes (`[0]` newest, `[3]` three years back).

Currency note
-------------
yfinance reports Indian statements in INR. Everything this module emits is a
**ratio or growth rate** (currency-neutral), so no FX conversion is needed here.
Absolute INR figures (market cap sizing) belong to the universe builder, not the
fundamentals adapter.

CLI
    python pipeline/data/india_fundamentals.py RELIANCE.NS TCS.NS BEL.NS
    python pipeline/data/india_fundamentals.py --no-cache RELIANCE.NS
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "_india_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_DAYS = 7   # fundamentals move slowly; the scan refreshes weekly

# yfinance uses .NS/.BO; EODHD/exchange notation uses .NSE/.BSE. Accept both.
_INDIAN_SUFFIXES = (".NS", ".BO", ".NSE", ".BSE")
_YAHOO_REMAP = {".NSE": ".NS", ".BSE": ".BO"}


# ══════════════════════════════════════════════════════════════════════════════
# Ticker helpers
# ══════════════════════════════════════════════════════════════════════════════

def is_indian(ticker: str) -> bool:
    """True for NSE/BSE tickers (.NS/.BO and the .NSE/.BSE exchange variants)."""
    return isinstance(ticker, str) and ticker.upper().endswith(_INDIAN_SUFFIXES)


def to_yahoo(ticker: str) -> str:
    """Normalise an Indian symbol to the suffix yfinance expects (.NS/.BO)."""
    up = ticker.upper()
    for src, dst in _YAHOO_REMAP.items():
        if up.endswith(src):
            return up[: -len(src)] + dst
    return up


# ══════════════════════════════════════════════════════════════════════════════
# Disk cache (mirrors the screener's _finnhub_cache pattern)
# ══════════════════════════════════════════════════════════════════════════════

def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{to_yahoo(ticker)}.bundle.json"


def _cache_get(ticker: str) -> dict | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > CACHE_TTL_DAYS * 86400:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _cache_put(ticker: str, bundle: dict) -> None:
    try:
        _cache_path(ticker).write_text(json.dumps(bundle))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Numeric helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _series(df, *names) -> list[float | None]:
    """A statement row as a newest-first list (yfinance orders columns desc).
    Tries several row labels; returns [] when none are present."""
    if df is None or getattr(df, "empty", True):
        return []
    for nm in names:
        if nm in df.index:
            return [_num(v) for v in df.loc[nm].tolist()]
    return []


def _cagr_pct(series: list[float | None], span: int) -> float | None:
    """Percent CAGR from series[span] (old) to series[0] (new), newest-first."""
    if len(series) <= span:
        return None
    new, old = series[0], series[span]
    if new is None or old is None or old <= 0 or new <= 0:
        return None
    return ((new / old) ** (1.0 / span) - 1.0) * 100.0


def _yoy_pct(series: list[float | None], lag: int = 1) -> float | None:
    """Percent change series[0] vs series[lag] (newest-first)."""
    if len(series) <= lag:
        return None
    new, old = series[0], series[lag]
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def _mean_margin(numer: list[float | None], denom: list[float | None]) -> float | None:
    pairs = [
        n / d * 100.0
        for n, d in zip(numer, denom)
        if n is not None and d is not None and d > 0
    ]
    return round(statistics.mean(pairs), 4) if pairs else None


# ══════════════════════════════════════════════════════════════════════════════
# yfinance provider — computes the Finnhub-shaped blobs
# ══════════════════════════════════════════════════════════════════════════════

# Keys we consider "core". Coverage = how many of these the provider filled.
# Used to decide when the fallback chain is worth invoking.
_CORE_METRIC_KEYS = (
    "revenueGrowth3Y", "revenueGrowthTTMYoy",
    "grossMarginTTM", "operatingMarginTTM",
    "totalDebt/totalEquityQuarterly",
)


def _build_from_yfinance(ticker: str) -> dict:
    """Pull every frame once from a single yf.Ticker and compute the three
    Finnhub-compatible payloads. Network-bound; cached by the caller."""
    ysym = to_yahoo(ticker)
    tk = yf.Ticker(ysym)

    info = {}
    try:
        info = tk.info or {}
    except Exception as exc:  # noqa: BLE001 — yfinance raises many types
        log.debug("info failed for %s: %s", ysym, exc)

    def _frame(attr):
        try:
            return getattr(tk, attr)
        except Exception as exc:  # noqa: BLE001
            log.debug("%s failed for %s: %s", attr, ysym, exc)
            return None

    ist = _frame("income_stmt")
    qf = _frame("quarterly_income_stmt")
    bs = _frame("balance_sheet")

    rev = _series(ist, "Total Revenue", "Operating Revenue")
    rev_q = _series(qf, "Total Revenue", "Operating Revenue")
    gp = _series(ist, "Gross Profit")
    oi = _series(ist, "Operating Income")
    debt = _series(bs, "Total Debt")
    equity = _series(
        bs, "Stockholders Equity", "Common Stock Equity",
        "Total Equity Gross Minority Interest",
    )

    # ── revenue growth (percent) ──────────────────────────────────────────────
    info_rg = _num(info.get("revenueGrowth"))
    metric: dict = {
        "revenueGrowth3Y": _round(_cagr_pct(rev, 3)),
        "revenueGrowth5Y": _round(_cagr_pct(rev, 4)),   # 5 annual points → 4y span
        "revenueGrowthTTMYoy": _round(info_rg * 100.0 if info_rg is not None
                                      else _yoy_pct(rev, 1)),
        "revenueGrowthQuarterlyYoy": _round(_yoy_pct(rev_q, 4)),  # same qtr last yr
    }

    # ── margins (percent) ─────────────────────────────────────────────────────
    gm_info = _num(info.get("grossMargins"))
    om_info = _num(info.get("operatingMargins"))
    gm_ttm = (gm_info * 100.0 if gm_info is not None
              else (gp[0] / rev[0] * 100.0 if gp and rev and gp[0] and rev[0] else None))
    om_ttm = (om_info * 100.0 if om_info is not None
              else (oi[0] / rev[0] * 100.0 if oi and rev and oi[0] and rev[0] else None))
    metric.update({
        "grossMarginTTM": _round(gm_ttm),
        "grossMargin5Y": _mean_margin(gp, rev),
        "operatingMarginTTM": _round(om_ttm),
        "operatingMargin5Y": _mean_margin(oi, rev),
        "operatingMarginAnnual": _round(
            oi[0] / rev[0] * 100.0 if oi and rev and oi[0] and rev[0] else None),
    })

    # ── leverage (RATIO, not percent — Finnhub convention) ────────────────────
    d2e = None
    if debt and equity and equity[0] and equity[0] > 0 and debt[0] is not None:
        d2e = debt[0] / equity[0]
    elif _num(info.get("debtToEquity")) is not None:
        d2e = _num(info.get("debtToEquity")) / 100.0     # yfinance reports a percent
    metric["totalDebt/totalEquityQuarterly"] = _round(d2e)

    # ── series.annual.revenue fallback (newest-first {period, v}) ─────────────
    cols = []
    if ist is not None and not getattr(ist, "empty", True):
        cols = [str(getattr(c, "date", lambda: c)()) for c in ist.columns]
    revenue_series = [
        {"period": p, "v": v} for p, v in zip(cols, rev) if v is not None
    ]

    coverage = sum(1 for k in _CORE_METRIC_KEYS if metric.get(k) is not None)

    metric_blob = {
        "metric": metric,
        "series": {"annual": {"revenue": revenue_series}},
        "metricType": "all",
        "symbol": ysym,
        "_source": "yfinance",
        "_coverage": f"{coverage}/{len(_CORE_METRIC_KEYS)}",
    }

    # ── recommendation (Finnhub /stock/recommendation shape) ──────────────────
    recommendation = _build_recommendation(tk, info)

    # ── ownership / cash (same shape screener._fetch_yf_stats produced) ───────
    stats = {
        "held_pct_insiders": _num(info.get("heldPercentInsiders")),
        "held_pct_institutions": _num(info.get("heldPercentInstitutions")),
        "total_cash": _num(info.get("totalCash")),
        "free_cashflow": _num(info.get("freeCashflow")),
        "operating_cashflow": _num(info.get("operatingCashflow")),
        "debt_to_equity": _num(info.get("debtToEquity")),
        "market_cap": _num(info.get("marketCap")),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "name": info.get("longName") or info.get("shortName"),
    }

    return {"metric": metric_blob, "recommendation": recommendation, "stats": stats}


def _build_recommendation(tk, info: dict) -> list:
    """yfinance .recommendations already carries Finnhub's
    strongBuy/buy/hold/sell/strongSell/period columns. Fall back to a single
    synthetic row from numberOfAnalystOpinions when the table is absent."""
    try:
        rs = tk.recommendations
        if rs is not None and not rs.empty:
            recs = rs.to_dict("records")
            # Ensure the keys the consumer sums are present as plain ints.
            out = []
            for r in recs:
                out.append({
                    "period": r.get("period"),
                    "strongBuy": int(_num(r.get("strongBuy")) or 0),
                    "buy": int(_num(r.get("buy")) or 0),
                    "hold": int(_num(r.get("hold")) or 0),
                    "sell": int(_num(r.get("sell")) or 0),
                    "strongSell": int(_num(r.get("strongSell")) or 0),
                })
            if out:
                return out
    except Exception as exc:  # noqa: BLE001
        log.debug("recommendations failed: %s", exc)

    n = _num(info.get("numberOfAnalystOpinions"))
    if n:
        return [{"period": "0m", "strongBuy": 0, "buy": 0,
                 "hold": int(n), "sell": 0, "strongSell": 0}]
    return []


def _round(v, ndigits: int = 4):
    return None if v is None else round(float(v), ndigits)


# ══════════════════════════════════════════════════════════════════════════════
# Free fallback chain (phase 2) — Screener.in / NSE-BSE XBRL
# ══════════════════════════════════════════════════════════════════════════════

def _fallback_metric(ticker: str, partial: dict) -> dict | None:
    """Fill gaps yfinance leaves (micro-caps, deep history, same-day results)
    from free Indian sources. Not yet built — returns None so the yfinance
    result stands. Wire Screener.in / NSE XBRL here without touching consumers.

    `partial` is the yfinance metric blob, so a future implementation can patch
    only the missing keys rather than refetch everything.
    """
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Public API — what consumers (and the screener wiring) call
# ══════════════════════════════════════════════════════════════════════════════

def bundle(ticker: str, use_cache: bool = True) -> dict:
    """Cached {metric, recommendation, stats} for an Indian ticker."""
    if use_cache:
        cached = _cache_get(ticker)
        if cached is not None:
            return cached
    data = _build_from_yfinance(ticker)

    # Optional free gap-fill (no-op until phase 2 is built).
    patch = _fallback_metric(ticker, data["metric"])
    if patch:
        data["metric"]["metric"].update(patch.get("metric", {}))
        data["metric"]["_source"] += "+fallback"

    # Only cache a result that actually carries fundamentals, so a transient
    # yfinance failure isn't pinned for the whole TTL.
    if any(v is not None for v in data["metric"]["metric"].values()):
        _cache_put(ticker, data)
    return data


def metric_blob(ticker: str, use_cache: bool = True) -> dict:
    """Finnhub /stock/metric?metric=all-compatible blob."""
    return bundle(ticker, use_cache)["metric"]


def recommendation(ticker: str, use_cache: bool = True) -> list:
    """Finnhub /stock/recommendation-compatible list."""
    return bundle(ticker, use_cache)["recommendation"]


def yf_stats(ticker: str, use_cache: bool = True) -> dict:
    """Ownership/cash stats in the shape screener._fetch_yf_stats produced."""
    return bundle(ticker, use_cache)["stats"]


__all__ = [
    "is_indian", "to_yahoo", "bundle",
    "metric_blob", "recommendation", "yf_stats",
]


# ══════════════════════════════════════════════════════════════════════════════
# CLI / self-test
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="India fundamentals adapter self-test")
    ap.add_argument("tickers", nargs="*", default=["RELIANCE.NS", "TCS.NS", "BEL.NS"])
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for t in args.tickers:
        if not is_indian(t):
            print(f"\n{t}: not an Indian ticker (need .NS/.BO/.NSE/.BSE) — skipped")
            continue
        b = bundle(t, use_cache=not args.no_cache)
        m = b["metric"]
        print(f"\n================ {t}  ({b['stats'].get('name')})")
        print(f"  source={m['_source']}  coverage={m['_coverage']}")
        for k, v in m["metric"].items():
            print(f"    {k:32s} = {v}")
        rec = b["recommendation"]
        latest = rec[0] if rec else {}
        analysts = sum(int(latest.get(k, 0)) for k in
                       ("strongBuy", "buy", "hold", "sell", "strongSell"))
        print(f"    analyst_count (latest period)    = {analysts}")
        print(f"    revenue series points            = {len(m['series']['annual']['revenue'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
