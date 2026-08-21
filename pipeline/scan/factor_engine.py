"""
Factor Engine (v2) -- the redesigned stock-selection core.
==========================================================

WHY THIS EXISTS
---------------
The legacy selector (layer1_fast_scan -> layer2_rank) ranks purely on
price/volume *technicals*: 20-day return, relative volume, breakout, RSI,
proximity to the 52-week high. In plain terms it rewards stocks that have
ALREADY spiked -- and extreme short-term spikes mean-revert, especially in
low-quality names. The live evidence is unambiguous: negative alpha at every
horizon, a 32% win rate, and catastrophic single-name blow-ups (PSIG -89%,
WLFC -71%, MPLT -67%) that the momentum/volume signals actively STEERED INTO.
Within our own shortlist the momentum signal carried a *reversed* information
coefficient. We were buying tops.

This engine inverts the premise. It does three things the old one never did:

  1. GATE on quality + liquidity BEFORE ranking. A name is ineligible unless it
     is a real, tradable, non-junk business. This is what removes the lottery
     tickets at the source -- fundamentals *decide* eligibility, they no longer
     merely annotate.

  2. Rank on a QUALITY-TILTED MULTI-FACTOR score: profitability/quality (the
     heaviest weight, our tail-risk shield), *slow* 12-1 momentum (skipping the
     most recent month to dodge short-term reversal), low volatility, and
     quality-interacted value. Combined as sector-neutral z-scores.

  3. Apply fixed-sign INTERACTIONS: value and momentum only "count" inside
     high-quality names (quality gates everything). No fitted weights -- the
     structure is pre-registered so there is nothing to overfit on a small
     sample.

This module is deliberately self-contained and dependency-light so it can be
run and validated in isolation before it is wired into run_scan. Every factor
here is computable from data we already pull (yfinance prices + Finnhub
/stock/metric). Point-in-time correctness for the historical backtest is a
separate concern handled by the as-of store; this module computes the CURRENT
cross-section for live scanning and for spot-checking behaviour.

USAGE
    from pipeline.scan.factor_engine import rank_universe
    df = rank_universe(["AAPL", "MSFT", ...])          # ranked, gated
    python -m pipeline.scan.factor_engine DEMO         # runs the built-in demo
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import FINNHUB_API_KEY  # noqa: E402

BENCHMARK = "SPY"

# ── Eligibility gate thresholds (the junk filter) ─────────────────────────────
MIN_PRICE      = 5.0            # penny-stock / reverse-split distress floor
MIN_ADV_USD    = 5_000_000.0    # median 20d dollar volume -- tradable + exitable
MIN_MKT_CAP    = 300_000_000.0  # below this, blow-up frequency explodes
MAX_DEBT_EQ    = 4.0            # over-levered -> excluded outright
MIN_HISTORY_D  = 200            # need ~1y for 12-1 momentum + vol/beta

# ── Composite weights (pre-registered, NOT fitted) ────────────────────────────
W_QUALITY  = 0.35
W_MOMENTUM = 0.25
W_LOWVOL   = 0.15
W_VALUE    = 0.15   # only expressed inside high-quality names (interaction)
# (event factors -- PEAD/revisions -- are a later layer; not in this core.)

_CACHE = Path(__file__).resolve().parent.parent / "data" / "_finnhub_cache"
_CACHE.mkdir(parents=True, exist_ok=True)
_HEADERS = {"User-Agent": "cefa-factor-engine/2.0"}


# ══════════════════════════════════════════════════════════════════════════════
#  Fundamentals (Finnhub /stock/metric) -- disk-cached
# ══════════════════════════════════════════════════════════════════════════════

def _finnhub(path: str, **params):
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
        except requests.RequestException:
            time.sleep(1 + attempt)
    return None


def _cached(ticker: str, kind: str, fetch):
    fp = _CACHE / f"{ticker}_{kind}.json"
    if fp.exists() and (time.time() - fp.stat().st_mtime) < 14 * 86400:
        try:
            return json.loads(fp.read_text())
        except Exception:
            pass
    obj = fetch()
    if obj is not None:
        fp.write_text(json.dumps(obj))
    return obj


def fetch_fundamentals(ticker: str) -> dict:
    """Return the quality/value fields we score on, from Finnhub. Missing keys
    come back as None; the caller treats a name with no fundamentals as
    ineligible (fundamentals must GATE, so 'unknown' == 'excluded')."""
    metric = (_cached(ticker, "metric",
                      lambda: _finnhub("stock/metric", symbol=ticker, metric="all"))
              or {}).get("metric", {})
    profile = _cached(ticker, "profile",
                      lambda: _finnhub("stock/profile2", symbol=ticker)) or {}

    def g(*names):
        for n in names:
            v = metric.get(n)
            if v is not None:
                try:
                    f = float(v)
                    if not np.isnan(f):
                        return f
                except (TypeError, ValueError):
                    pass
        return None

    out = {
        "sector":        profile.get("finnhubIndustry"),
        "mkt_cap_usd":   (g("marketCapitalization") or 0) * 1e6,  # Finnhub reports in $M
        "gross_margin":  g("grossMarginTTM", "grossMarginAnnual"),
        "oper_margin":   g("operatingMarginTTM", "operatingMarginAnnual"),
        "net_margin":    g("netProfitMarginTTM", "netMarginAnnual"),
        "roe":           g("roeTTM", "roeRfy"),
        "roa":           g("roaTTM", "roaRfy"),
        "debt_equity":   g("totalDebt/totalEquityQuarterly", "totalDebt/totalEquityAnnual"),
        "current_ratio": g("currentRatioQuarterly", "currentRatioAnnual"),
        "rev_growth":    g("revenueGrowthTTMYoy", "revenueGrowth3Y"),
        "pe":            g("peTTM", "peBasicExclExtraTTM"),
        "pfcf_share":    g("pfcfShareTTM", "pfcfShareAnnual"),
        "ps":            g("psTTM", "psAnnual"),
        "beta":          g("beta"),
        "avg_vol_10d_m": g("10DayAverageTradingVolume", "3MonthAverageTradingVolume"),
    }
    # Fallback: when Finnhub's free tier misses a name (it silently drops some
    # large caps), backfill the gating fields from yfinance so a data gap can't
    # masquerade as "unprofitable / no fundamentals" and wrongly exclude a name.
    if out["gross_margin"] is None or out["mkt_cap_usd"] == 0:
        info = _cached(ticker, "yfinfo", lambda: _yf_info(ticker)) or {}
        def pct(x):   return x * 100 if x is not None else None
        out["sector"]       = out["sector"]       or info.get("sector")
        out["mkt_cap_usd"]  = out["mkt_cap_usd"]  or (info.get("marketCap") or 0)
        out["gross_margin"] = out["gross_margin"] if out["gross_margin"] is not None else pct(info.get("grossMargins"))
        out["oper_margin"]  = out["oper_margin"]  if out["oper_margin"]  is not None else pct(info.get("operatingMargins"))
        out["roe"]          = out["roe"]          if out["roe"]          is not None else pct(info.get("returnOnEquity"))
        # yfinance debtToEquity is a percentage (145.0 == 1.45x); Finnhub is a ratio.
        if out["debt_equity"] is None and info.get("debtToEquity") is not None:
            out["debt_equity"] = info["debtToEquity"] / 100.0
        out["pe"]           = out["pe"]           or info.get("trailingPE")
    return out


def _yf_info(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info or {}
        return {k: info.get(k) for k in
                ("sector", "marketCap", "grossMargins", "operatingMargins",
                 "returnOnEquity", "debtToEquity", "trailingPE")}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  Price-derived factors (yfinance) -- near-free, full-universe capable
# ══════════════════════════════════════════════════════════════════════════════

def _download(tickers: list[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    all_t = sorted(set(tickers) | {BENCHMARK})
    CH = 120
    for i in range(0, len(all_t), CH):
        batch = all_t[i:i + CH]
        df = yf.download(batch, period="15mo", auto_adjust=True,
                         group_by="ticker", progress=False, threads=True)
        for t in batch:
            try:
                sub = (df[t] if len(batch) > 1 else df).dropna(subset=["Close"])
                if len(sub):
                    frames[t] = sub
            except Exception:
                pass
    return frames


def price_factors(df: pd.DataFrame, spy: pd.DataFrame | None) -> dict:
    """12-1 momentum (skip last ~21 sessions), 6m momentum, 60d realized vol,
    beta vs SPY, median 20d dollar volume, last price, 52w position."""
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float) if "Volume" in df else None
    n = len(close)
    if n < MIN_HISTORY_D:
        return {}

    px = float(close.iloc[-1])
    # median 20-day dollar volume
    adv = None
    if vol is not None and n >= 20:
        adv = float((close * vol).iloc[-20:].median())

    # 12-1 momentum: return from ~252 sessions ago to ~21 sessions ago (skip 1mo)
    look_start = close.iloc[max(0, n - 252)]
    look_end   = close.iloc[n - 22] if n > 22 else close.iloc[-1]
    mom_12_1 = (float(look_end) / float(look_start) - 1.0) if look_start else None

    # 6-month momentum, also skipping the last month
    if n > 21 + 126:
        m6 = float(close.iloc[n - 22]) / float(close.iloc[n - 22 - 126]) - 1.0
    else:
        m6 = None

    rets = close.pct_change().dropna()
    vol60 = float(rets.iloc[-60:].std() * np.sqrt(252)) if len(rets) >= 60 else None

    beta = None
    if spy is not None:
        sp = spy["Close"].astype(float).pct_change().dropna()
        j = pd.concat([rets, sp], axis=1, join="inner").dropna()
        if len(j) >= 120:
            x = j.iloc[-120:, 1].values
            y = j.iloc[-120:, 0].values
            vx = np.var(x)
            beta = float(np.cov(x, y)[0, 1] / vx) if vx > 0 else None

    hi52 = float(close.iloc[-252:].max()) if n >= 252 else float(close.max())
    pct_from_high = (px / hi52 - 1.0) * 100 if hi52 else None

    return {
        "price": px, "adv_usd": adv, "mom_12_1": mom_12_1, "mom_6m": m6,
        "vol_60d": vol60, "beta": beta, "pct_from_52w_high": pct_from_high,
        "history_days": n,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Scoring
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Candidate:
    ticker: str
    price: float | None = None
    adv_usd: float | None = None
    mkt_cap_usd: float | None = None
    sector: str | None = None
    factors: dict = field(default_factory=dict)   # raw factor values
    eligible: bool = False
    reject: str | None = None
    composite: float | None = None


def _zscore(vals: dict[str, float]) -> dict[str, float]:
    keys = [k for k, v in vals.items() if v is not None]
    arr = np.array([vals[k] for k in keys], dtype=float)
    if len(arr) < 2 or np.std(arr) == 0:
        return {k: 0.0 for k in keys}
    # winsorize at 1st/99th pct then z-score
    lo, hi = np.percentile(arr, 1), np.percentile(arr, 99)
    arr = np.clip(arr, lo, hi)
    mu, sd = arr.mean(), arr.std()
    return {k: float((v - mu) / sd) for k, v in zip(keys, arr)}


def _sector_neutral_z(cands: list[Candidate], getter) -> dict[str, float]:
    """Z-score `getter(c)` WITHIN each sector so a name is judged against its
    peers, not across the whole market. Falls back to a global z when a sector
    has too few names."""
    out: dict[str, float] = {}
    by_sector: dict[str, list[Candidate]] = {}
    for c in cands:
        by_sector.setdefault(c.sector or "UNKNOWN", []).append(c)
    # sectors with <4 names get pooled into a global bucket
    small = [c for s, cs in by_sector.items() if len(cs) < 4 for c in cs]
    for sector, cs in by_sector.items():
        group = cs if len(cs) >= 4 else small
        vals = {c.ticker: getter(c) for c in cs}
        # z within the (peer or pooled) group
        ref = {c.ticker: getter(c) for c in group}
        z = _zscore(ref)
        for c in cs:
            out[c.ticker] = z.get(c.ticker, 0.0)
    return out


def _quality_raw(f: dict) -> float | None:
    """Composite quality = profitability + safety, averaged over available
    sub-signals (each already directional so higher == better)."""
    parts = []
    if f.get("gross_margin")  is not None: parts.append(f["gross_margin"])
    if f.get("oper_margin")   is not None: parts.append(f["oper_margin"])
    if f.get("roe")           is not None: parts.append(min(f["roe"], 80))   # cap absurd ROE
    if f.get("roa")           is not None: parts.append(f["roa"] * 2)
    if f.get("debt_equity")   is not None: parts.append(-f["debt_equity"] * 10)  # less debt = better
    if f.get("current_ratio") is not None: parts.append(f["current_ratio"] * 5)
    return float(np.mean(parts)) if parts else None


def _value_raw(f: dict) -> float | None:
    yields = []
    if f.get("pe") and f["pe"] > 0:          yields.append(1.0 / f["pe"])          # earnings yield
    if f.get("pfcf_share") and f["pfcf_share"] > 0: yields.append(1.0 / f["pfcf_share"])  # fcf yield proxy
    return float(np.mean(yields)) if yields else None


def _apply_gate(c: Candidate) -> None:
    f = c.factors
    if c.price is None or c.price < MIN_PRICE:
        c.reject = f"price ${c.price or 0:.2f} < ${MIN_PRICE:.0f}"; return
    if c.adv_usd is None or c.adv_usd < MIN_ADV_USD:
        c.reject = f"ADV ${(c.adv_usd or 0)/1e6:.1f}M < ${MIN_ADV_USD/1e6:.0f}M"; return
    if c.mkt_cap_usd is None or c.mkt_cap_usd < MIN_MKT_CAP:
        c.reject = f"mktcap ${(c.mkt_cap_usd or 0)/1e6:.0f}M < ${MIN_MKT_CAP/1e6:.0f}M"; return
    if f.get("history_days", 0) < MIN_HISTORY_D:
        c.reject = "insufficient price history"; return
    gm = f.get("gross_margin")
    if gm is None:
        c.reject = "no fundamentals (excluded -- fundamentals must gate)"; return
    if gm <= 0:
        c.reject = f"gross margin {gm:.1f}% <= 0 (unprofitable at the gross line)"; return
    de = f.get("debt_equity")
    if de is not None and de > MAX_DEBT_EQ:
        c.reject = f"debt/equity {de:.1f} > {MAX_DEBT_EQ:.0f} (over-levered)"; return
    c.eligible = True


def rank_universe(tickers: list[str]) -> pd.DataFrame:
    """The full pipeline: fetch -> gate -> sector-neutral multi-factor score ->
    fixed-sign interactions -> rank. Returns a DataFrame ordered best-first,
    plus the rejected names with their reason (transparency)."""
    tickers = sorted(set(t.upper().strip() for t in tickers if t))
    hist = _download(tickers)
    spy = hist.get(BENCHMARK)

    cands: list[Candidate] = []
    for t in tickers:
        if t == BENCHMARK:
            continue
        c = Candidate(t)
        df = hist.get(t)
        if df is None:
            c.reject = "no price data"; cands.append(c); continue
        pf = price_factors(df, spy)
        fund = fetch_fundamentals(t)
        c.factors = {**pf, **fund}
        c.price = pf.get("price")
        c.adv_usd = pf.get("adv_usd")
        c.mkt_cap_usd = fund.get("mkt_cap_usd")
        c.sector = fund.get("sector")
        _apply_gate(c)
        cands.append(c)

    eligible = [c for c in cands if c.eligible]

    # sector-neutral z-scores for each factor family
    zq = _sector_neutral_z(eligible, lambda c: _quality_raw(c.factors))
    zm = _sector_neutral_z(eligible, lambda c: c.factors.get("mom_12_1"))
    zlv = _sector_neutral_z(eligible, lambda c: (-c.factors["vol_60d"]
                                                 if c.factors.get("vol_60d") is not None else None))
    zv = _sector_neutral_z(eligible, lambda c: _value_raw(c.factors))

    for c in eligible:
        q = zq.get(c.ticker, 0.0)
        m = zm.get(c.ticker, 0.0)
        lv = zlv.get(c.ticker, 0.0)
        v = zv.get(c.ticker, 0.0)
        # FIXED-SIGN INTERACTION: value & momentum only count in >=median quality.
        # (quality gates everything -- avoids value-traps and junk-momentum.)
        q_ok = q >= 0
        m_eff = m if q_ok else 0.5 * m          # discount momentum in low-quality names
        v_eff = v if q_ok else 0.0              # ignore "cheap" junk entirely
        c.composite = (W_QUALITY * q + W_MOMENTUM * m_eff
                       + W_LOWVOL * lv + W_VALUE * v_eff)

    eligible.sort(key=lambda c: c.composite, reverse=True)

    rows = []
    for rank, c in enumerate(eligible, 1):
        f = c.factors
        rows.append({
            "rank": rank, "ticker": c.ticker, "sector": (c.sector or "?")[:20],
            "composite": round(c.composite, 3),
            "price": round(c.price, 2),
            "mktcap_$M": round((c.mkt_cap_usd or 0) / 1e6),
            "adv_$M": round((c.adv_usd or 0) / 1e6, 1),
            "gross_margin": _r(f.get("gross_margin")),
            "roe": _r(f.get("roe")),
            "debt_eq": _r(f.get("debt_equity")),
            "mom_12_1_%": _r((f.get("mom_12_1") or 0) * 100),
            "vol_60d_%": _r((f.get("vol_60d") or 0) * 100),
            "pe": _r(f.get("pe")),
        })
    ranked = pd.DataFrame(rows)

    rej = pd.DataFrame([{"ticker": c.ticker, "reject_reason": c.reject}
                        for c in cands if not c.eligible])
    rank_universe.last_rejected = rej   # attach for inspection
    return ranked


def _r(v):
    return None if v is None else round(float(v), 1)


# ══════════════════════════════════════════════════════════════════════════════
#  Demo
# ══════════════════════════════════════════════════════════════════════════════

# A deliberately mixed watchlist: quality compounders, cyclicals, AND several of
# the exact names the OLD spike-chasing selector picked right before they
# collapsed (WLFC, MPLT, HYLN, KPTI). A working engine should rank the durable
# businesses up and gate/penalise the blow-ups.
_DEMO = [
    # quality large/mid caps
    "AAPL", "MSFT", "GOOGL", "V", "MA", "UNH", "COST", "LLY", "NVDA", "ADBE",
    "TXN", "HD", "PG", "JNJ", "CAT",
    # cheaper / value-ish
    "CVX", "PFE", "CMCSA", "F", "T", "VZ", "GM",
    # smaller quality
    "MELI", "MNST", "POOL", "IDXX",
    # the old selector's blow-ups / hype names
    "WLFC", "MPLT", "HYLN", "KPTI", "PSIG",
]


def _demo():
    print("Running factor engine on a mixed watchlist "
          f"({len(_DEMO)} names incl. the old selector's blow-ups)...\n")
    ranked = rank_universe(_DEMO)
    pd.set_option("display.max_columns", None, "display.width", 200)
    print("=" * 100)
    print("RANKED (eligible names, best first) -- quality-tilted, sector-neutral")
    print("=" * 100)
    print(ranked.to_string(index=False))
    print("\n" + "=" * 100)
    print("GATED OUT (fundamentals/liquidity refused them BEFORE ranking)")
    print("=" * 100)
    rej = getattr(rank_universe, "last_rejected", None)
    if rej is not None and len(rej):
        print(rej.to_string(index=False))
    print("\nRead: the blow-up names should be gated out or ranked low; durable, "
          "profitable businesses should rise. Selection is now driven by what a "
          "company IS, not by how much it just spiked.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].upper() == "DEMO":
        _demo()
    else:
        args = [a for a in sys.argv[1:] if a]
        if not args:
            print("usage: python -m pipeline.scan.factor_engine DEMO | <TICKER...>")
            sys.exit(2)
        out = rank_universe(args)
        print(out.to_string(index=False))
        rej = getattr(rank_universe, "last_rejected", None)
        if rej is not None and len(rej):
            print("\nGated out:\n", rej.to_string(index=False))
