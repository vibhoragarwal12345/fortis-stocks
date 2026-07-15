"""Financial rigor — exact-arithmetic verification of numbers we publish.

Adapted from HKUDS/Vibe-Trading's financial_rigor_tool (MIT): pure-stdlib
verification routines under a shared 28-digit Decimal context, so results
carry no IEEE-754 drift and are reproducible. Fetch data elsewhere; verify
here. Wired into:
  - factcheck_agent.factcheck()      → audit_context() on the dossier's
                                       closed-context data
  - multibagger/scorer.apply_caps()  → cross_validate() on market-cap
                                       sources (NASDAQ screener vs Finnhub)
  - scan/layer1_fast_scan            → benford() over universe volumes as a
                                       feed-integrity tripwire (volumes span
                                       orders of magnitude; prices don't
                                       qualify for Benford and are not used)
"""

from __future__ import annotations

import math
from decimal import Decimal, getcontext, InvalidOperation
from typing import Any, Iterable

getcontext().prec = 28


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v))
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def verify_market_cap(price: Any, shares: Any, reported_cap: Any) -> dict:
    """price × shares vs reported cap; verdict at 1% / 5% deviation."""
    p, s, cap = _dec(price), _dec(shares), _dec(reported_cap)
    if p is None or s is None or cap is None or cap == 0:
        return {"verdict": "insufficient_data"}
    derived = p * s
    dev = abs(derived - cap) / abs(cap)
    verdict = "ok" if dev <= Decimal("0.01") else (
        "minor_deviation" if dev <= Decimal("0.05") else "MISMATCH")
    return {"verdict": verdict, "derived": float(derived),
            "reported": float(cap), "deviation_pct": round(float(dev * 100), 2)}


def verify_valuation(price: Any = None, eps: Any = None, book_per_share: Any = None,
                     sales_per_share: Any = None, fcf_per_share: Any = None,
                     dividend_per_share: Any = None) -> dict:
    """Derive PE / PB / PS / FCF-yield / dividend-yield from raw per-share
    inputs with exact arithmetic. Compare against any reported multiple
    yourself — this returns the arithmetic truth."""
    p = _dec(price)
    out: dict[str, float | None] = {}
    if p is None or p <= 0:
        return {"verdict": "insufficient_data"}
    pairs = {"pe": _dec(eps), "pb": _dec(book_per_share), "ps": _dec(sales_per_share)}
    for name, denom in pairs.items():
        out[name] = float(p / denom) if denom and denom != 0 else None
    fcf = _dec(fcf_per_share)
    out["fcf_yield_pct"] = float(fcf / p * 100) if fcf is not None else None
    div = _dec(dividend_per_share)
    out["dividend_yield_pct"] = float(div / p * 100) if div is not None else None
    return {"verdict": "ok", **{k: (round(v, 4) if v is not None else None)
                                for k, v in out.items()}}


def cross_validate(values: dict[str, Any], tolerance_pct: float = 2.0) -> dict:
    """One quantity from several sources: flag deviations beyond tolerance
    against the median consensus."""
    decs = {k: _dec(v) for k, v in values.items()}
    decs = {k: v for k, v in decs.items() if v is not None}
    if len(decs) < 2:
        return {"verdict": "insufficient_data", "n_sources": len(decs)}
    ordered = sorted(decs.values())
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    if median == 0:
        return {"verdict": "insufficient_data", "n_sources": n}
    outliers = {k: round(float(abs(v - median) / abs(median) * 100), 2)
                for k, v in decs.items()
                if abs(v - median) / abs(median) * 100 > Decimal(str(tolerance_pct))}
    return {"verdict": "ok" if not outliers else "DIVERGENT",
            "median": float(median), "n_sources": n,
            "outliers_pct_dev": outliers, "tolerance_pct": tolerance_pct}


def benford(values: Iterable[Any], min_n: int = 50) -> dict:
    """Benford's-law first-digit conformity on naturally-scaled magnitudes
    (volumes, revenues, line items — NOT prices/ratios/percentages).
    Returns MAD-based conformity per Nigrini's thresholds."""
    digits = []
    for v in values:
        d = _dec(v)
        if d is None or d == 0:
            continue
        s = f"{abs(d):.6E}"          # first significant digit via sci notation
        first = s[0]
        if first.isdigit() and first != "0":
            digits.append(int(first))
    n = len(digits)
    if n < min_n:
        return {"verdict": "insufficient_data", "n": n}
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
    observed = {d: digits.count(d) / n for d in range(1, 10)}
    mad = sum(abs(observed[d] - expected[d]) for d in range(1, 10)) / 9
    verdict = ("close_conformity" if mad < 0.006 else
               "acceptable_conformity" if mad < 0.012 else
               "marginal_conformity" if mad < 0.015 else
               "NONCONFORMITY")
    return {"verdict": verdict, "mad": round(mad, 5), "n": n,
            "observed_pct": {d: round(observed[d] * 100, 1) for d in range(1, 10)}}


def three_scenario(eps_ttm: Any, growth_bull_pct: Any, growth_base_pct: Any,
                   growth_bear_pct: Any, pe_bull: Any, pe_base: Any,
                   pe_bear: Any, years: int = 1) -> dict:
    """Bull/base/bear reference levels from EPS-growth + terminal-PE
    assumptions. Exact arithmetic; assumptions in, arithmetic out —
    labeling as 'statistical scenario, not a forecast' is the caller's job."""
    eps = _dec(eps_ttm)
    if eps is None:
        return {"verdict": "insufficient_data"}
    out = {}
    for name, g, pe in (("bull", growth_bull_pct, pe_bull),
                        ("base", growth_base_pct, pe_base),
                        ("bear", growth_bear_pct, pe_bear)):
        gd, ped = _dec(g), _dec(pe)
        if gd is None or ped is None:
            out[name] = None
            continue
        out[name] = round(float(eps * (1 + gd / 100) ** years * ped), 2)
    return {"verdict": "ok", **out}


# ── Context auditor for factcheck_agent ───────────────────────────────────

_KEY_HINTS = {
    "price": ("price", "current_price", "last_price"),
    "shares": ("shares_outstanding", "share_count", "shares"),
    "market_cap": ("market_cap", "marketcap", "market_capitalization"),
    "eps": ("eps_ttm", "eps", "earnings_per_share"),
    "pe": ("pe_ratio", "pe_ttm", "pe"),
}


def _find(flat: dict[str, Any], hints: tuple[str, ...]) -> Any:
    for k, v in flat.items():
        kl = k.lower().rsplit(".", 1)[-1]
        if kl in hints:
            return v
    return None


def audit_context(flat_data: dict[str, Any]) -> list[dict]:
    """Cross-check derivable relationships inside a dossier's closed-context
    data dict (flattened). Returns a list of rigor flags (empty = clean).
    Only checks relationships whose inputs are all present — silent
    otherwise, so it can run on every dossier without noise."""
    flags: list[dict] = []
    price = _find(flat_data, _KEY_HINTS["price"])
    shares = _find(flat_data, _KEY_HINTS["shares"])
    cap = _find(flat_data, _KEY_HINTS["market_cap"])
    eps = _find(flat_data, _KEY_HINTS["eps"])
    pe = _find(flat_data, _KEY_HINTS["pe"])

    if price is not None and shares is not None and cap is not None:
        r = verify_market_cap(price, shares, cap)
        if r.get("verdict") == "MISMATCH":
            flags.append({"check": "market_cap", **r})
    if price is not None and eps is not None and pe is not None:
        pd_, ed, ped = _dec(price), _dec(eps), _dec(pe)
        if pd_ and ed and ped and ed != 0:
            derived = pd_ / ed
            if ped != 0 and abs(derived - ped) / abs(ped) > Decimal("0.05"):
                flags.append({"check": "pe_ratio",
                              "derived": round(float(derived), 2),
                              "reported": float(ped), "verdict": "MISMATCH"})
    return flags
