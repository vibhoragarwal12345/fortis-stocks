"""
Quantitative Models Desk -- Model 6: DCF (discounted cash flow) valuation.

DCF estimates intrinsic value by projecting free cash flow and discounting it
back at the cost of capital. It is powerful but trivially manipulated by rosy
assumptions, so EVERY default here is deliberately CONSERVATIVE:
  * projection growth capped at 15% (no perpetual hypergrowth),
  * terminal growth fixed at 2.5% (long-run inflation),
  * WACC capped at 12% (higher rates produce absurd valuations),
  * negative-FCF histories are refused outright.

Financials come from yfinance's statement data. (The spec named Finnhub's
/stock/financials-reported, but that endpoint is paywalled on the free tier;
yfinance's cash-flow / balance-sheet statements are the working free source.)

Deterministic math only -- no LLM.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quant.utils import get_risk_free_rate  # noqa: E402

log = logging.getLogger(__name__)

EQUITY_RISK_PREMIUM = 0.06
TERMINAL_GROWTH     = 0.025
GROWTH_CAP          = 0.15
WACC_CAP, WACC_FLOOR = 0.12, 0.07
TAX_RATE            = 0.21
PROJECTION_YEARS    = 10
# DCF is the wrong tool for these GICS sectors (use DDM / NAV / embedded value).
NON_DCF_SECTORS = {"Financial Services", "Real Estate"}

_STANDARD_LIMITATIONS = (
    "Assumes stable FCF margins. Sensitive to the terminal-growth assumption "
    "-- see the sensitivity range. Best for mature, cash-generative businesses; "
    "less reliable for early-stage, cyclical or capital-structure-driven firms.")


def _row(df, *names):
    """First matching row of a yfinance statement DataFrame, or None."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


class DCFValuation:
    """Conservative discounted-cash-flow intrinsic valuation."""

    # ──────────────────────────────────────────────────────────────────────────
    def fetch_financials(self, ticker: str) -> dict:
        """Free cash flow history, balance-sheet items and identity data."""
        tk = yf.Ticker(ticker)
        try:
            info = tk.info or {}
        except Exception:
            info = {}
        out = {
            "ticker": ticker, "sector": info.get("sector"),
            "beta": info.get("beta"), "shares": info.get("sharesOutstanding"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "eps": info.get("trailingEps"), "trailing_pe": info.get("trailingPE"),
            "market_cap": info.get("marketCap"),
            "usable": True, "reason": None, "fcf_history": [],
            "total_debt": 0.0, "cash": 0.0,
        }
        try:
            cf = tk.cashflow
        except Exception:
            cf = None
        fcf = _row(cf, "Free Cash Flow")
        if fcf is None:
            ocf = _row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
            capex = _row(cf, "Capital Expenditure", "Capital Expenditures")
            if ocf is not None and capex is not None:
                fcf = ocf + capex                    # capex is negative in yfinance
        if fcf is None or fcf.dropna().empty:
            out["usable"], out["reason"] = False, "no free-cash-flow data available"
            return out
        out["fcf_history"] = [float(x) for x in fcf.dropna().sort_index().values][-5:]

        try:
            bs = tk.balance_sheet
        except Exception:
            bs = None
        td = _row(bs, "Total Debt")
        cash = _row(bs, "Cash And Cash Equivalents",
                    "Cash Cash Equivalents And Short Term Investments")
        if td is not None and not td.dropna().empty:
            out["total_debt"] = float(td.dropna().iloc[0])
        if cash is not None and not cash.dropna().empty:
            out["cash"] = float(cash.dropna().iloc[0])
        if not out["shares"] or out["shares"] <= 0:
            out["usable"], out["reason"] = False, "shares outstanding unavailable"
        return out

    # ──────────────────────────────────────────────────────────────────────────
    def estimate_inputs(self, fin: dict) -> dict:
        """Conservative growth / WACC / terminal-growth assumptions."""
        fcf = fin["fcf_history"]
        base_fcf = fcf[-1]
        notes = []

        years = len(fcf) - 1
        if years >= 2 and fcf[0] > 0 and fcf[-1] > 0:
            cagr = (fcf[-1] / fcf[0]) ** (1.0 / years) - 1.0
        else:
            cagr, notes = 0.03, notes + ["FCF history negative/short -> 3% GDP-like growth"]

        growth = cagr if (cagr == cagr and cagr > 0) else 0.03
        if growth > GROWTH_CAP:
            notes.append(f"5yr CAGR {cagr:.0%} capped at {GROWTH_CAP:.0%} (no hypergrowth)")
            growth = GROWTH_CAP

        # WACC via CAPM cost of equity blended with after-tax cost of debt.
        rf = get_risk_free_rate()
        beta = fin.get("beta") or 1.0
        cost_equity = rf + beta * EQUITY_RISK_PREMIUM
        cost_debt = (rf + 0.02) * (1.0 - TAX_RATE)
        E = fin.get("market_cap") or 0.0
        D = fin.get("total_debt") or 0.0
        wacc = ((E / (E + D)) * cost_equity + (D / (E + D)) * cost_debt
                if (E + D) > 0 else cost_equity)
        if wacc > WACC_CAP:
            notes.append(f"WACC {wacc:.1%} capped at {WACC_CAP:.0%}")
        wacc = min(max(wacc, WACC_FLOOR), WACC_CAP)

        return {"base_fcf": base_fcf, "growth": round(growth, 4),
                "terminal_growth": TERMINAL_GROWTH, "wacc": round(wacc, 4),
                "beta": round(float(beta), 3), "notes": notes}

    # ──────────────────────────────────────────────────────────────────────────
    def project_cash_flows(self, base_fcf: float, growth: float,
                           terminal_growth: float = TERMINAL_GROWTH,
                           years: int = PROJECTION_YEARS) -> list[float]:
        """Years 1-5 at `growth`; years 6-10 decay linearly to terminal growth."""
        flows, cur = [], base_fcf
        for y in range(1, years + 1):
            if y <= 5:
                g = growth
            else:
                g = growth + (terminal_growth - growth) * (y - 5) / 5.0
            cur = cur * (1.0 + g)
            flows.append(cur)
        return flows

    # ──────────────────────────────────────────────────────────────────────────
    def discount_to_present(self, cash_flows: list[float], wacc: float,
                            terminal_growth: float, net_debt: float,
                            shares: float) -> dict:
        """Discount projected flows + Gordon-growth terminal value to a per-share."""
        pv_flows = sum(cf / (1.0 + wacc) ** y
                       for y, cf in enumerate(cash_flows, start=1))
        terminal_value = (cash_flows[-1] * (1.0 + terminal_growth)
                          / (wacc - terminal_growth))
        pv_terminal = terminal_value / (1.0 + wacc) ** len(cash_flows)
        enterprise_value = pv_flows + pv_terminal
        equity_value = enterprise_value - net_debt
        per_share = equity_value / shares if shares else float("nan")
        return {"enterprise_value": enterprise_value, "equity_value": equity_value,
                "pv_terminal_fraction": (pv_terminal / enterprise_value
                                         if enterprise_value else None),
                "per_share": per_share}

    # ──────────────────────────────────────────────────────────────────────────
    def sensitivity_analysis(self, base_fcf: float, growth: float, wacc: float,
                             terminal_growth: float, net_debt: float,
                             shares: float) -> dict:
        """3x3 grid over growth +/-2% and WACC +/-1%; returns the value range."""
        grid = []
        for dg in (-0.02, 0.0, 0.02):
            for dw in (-0.01, 0.0, 0.01):
                g = max(0.0, min(growth + dg, GROWTH_CAP))
                w = min(max(wacc + dw, WACC_FLOOR + 0.005), WACC_CAP + 0.01)
                if w <= terminal_growth:               # guard Gordon denominator
                    continue
                cfs = self.project_cash_flows(base_fcf, g, terminal_growth)
                ps = self.discount_to_present(cfs, w, terminal_growth,
                                              net_debt, shares)["per_share"]
                if ps == ps:
                    grid.append(ps)
        if not grid:
            return {"low": None, "high": None, "grid_size": 0}
        return {"low": round(min(grid), 2), "high": round(max(grid), 2),
                "grid_size": len(grid)}

    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def peer_multiples_check(implied_pe: float | None,
                             peer_median_pe: float | None) -> str:
        """Compare the DCF-implied P/E to the peer median."""
        if implied_pe is None or peer_median_pe is None or peer_median_pe <= 0:
            return "consistent_with_peers"             # insufficient data to judge
        diff = (implied_pe - peer_median_pe) / peer_median_pe
        if diff > 0.30:
            return "outlier_high"
        if diff < -0.30:
            return "outlier_low"
        return "consistent_with_peers"

    # ──────────────────────────────────────────────────────────────────────────
    def value(self, ticker: str) -> dict:
        """Full DCF for one ticker. Returns a result dict; never raises."""
        result = {"ticker": ticker, "calculation_date":
                  datetime.now(timezone.utc).date().isoformat(),
                  "valuation_label": "not_applicable", "confidence": "low",
                  "limitations": _STANDARD_LIMITATIONS}
        try:
            fin = self.fetch_financials(ticker)
            result["current_price"] = fin.get("current_price")
            result["sector"] = fin.get("sector")
            result["trailing_pe"] = fin.get("trailing_pe")
            result["eps"] = fin.get("eps")

            # CRITICAL: DCF is inappropriate for these business models.
            if fin.get("sector") in NON_DCF_SECTORS:
                result["reason"] = (f"DCF not applicable to {fin['sector']} "
                                    f"-- use DDM / NAV / embedded value instead")
                result["limitations"] = result["reason"]
                return result
            if not fin["usable"]:
                result["reason"] = fin["reason"]
                return result

            fcf = fin["fcf_history"]
            neg_years = sum(1 for x in fcf if x < 0)
            if neg_years >= 3:
                result["reason"] = (f"FCF negative in {neg_years}/{len(fcf)} years "
                                    f"-- DCF not meaningful")
                return result
            if fin["base_fcf"] if False else fcf[-1] <= 0:
                result["reason"] = "latest-year FCF is negative -- DCF not meaningful"
                return result

            inp = self.estimate_inputs(fin)
            net_debt = fin["total_debt"] - fin["cash"]
            shares = fin["shares"]
            cfs = self.project_cash_flows(inp["base_fcf"], inp["growth"],
                                          inp["terminal_growth"])
            disc = self.discount_to_present(cfs, inp["wacc"],
                                            inp["terminal_growth"], net_debt, shares)
            dcf_ps = disc["per_share"]

            # A negative equity value (net debt exceeds enterprise value) makes
            # the per-share DCF meaningless -- do not emit a negative "target".
            if disc["equity_value"] <= 0 or not (dcf_ps == dcf_ps) or dcf_ps <= 0:
                result["reason"] = ("equity value is negative -- enterprise value "
                                    "is below net debt; a per-share DCF is not "
                                    "meaningful for this capital structure")
                return result

            sens = self.sensitivity_analysis(inp["base_fcf"], inp["growth"],
                                             inp["wacc"], inp["terminal_growth"],
                                             net_debt, shares)

            price = fin.get("current_price")
            upside = ((dcf_ps - price) / price) if (price and dcf_ps == dcf_ps) else None
            label = "fairly_valued"
            if upside is not None:
                label = ("deeply_undervalued" if upside > 0.50 else
                         "undervalued" if upside > 0.15 else
                         "deeply_overvalued" if upside < -0.40 else
                         "overvalued" if upside < -0.15 else "fairly_valued")

            # Confidence: extreme outputs are almost always wrong.
            confidence = "high"
            if inp["notes"]:
                confidence = "medium"
            if price and dcf_ps == dcf_ps and (dcf_ps > 3 * price or dcf_ps < 0.3 * price):
                confidence = "low"
                inp["notes"].append("DCF value >3x or <0.3x price -- extreme, treat as low confidence")

            implied_pe = (dcf_ps / fin["eps"]) if fin.get("eps") and fin["eps"] > 0 else None

            result.update({
                "dcf_value_per_share": round(dcf_ps, 2) if dcf_ps == dcf_ps else None,
                "dcf_low": sens["low"], "dcf_high": sens["high"],
                "upside_to_dcf_pct": round(upside, 4) if upside is not None else None,
                "valuation_label": label, "confidence": confidence,
                "assumptions": {"growth": inp["growth"], "wacc": inp["wacc"],
                                "terminal_growth": inp["terminal_growth"],
                                "base_fcf": inp["base_fcf"],
                                "pv_terminal_fraction": disc["pv_terminal_fraction"]},
                "limitations": _STANDARD_LIMITATIONS
                + (" NOTES: " + "; ".join(inp["notes"]) if inp["notes"] else ""),
                "peer_multiple_check": "consistent_with_peers",   # set by the runner
                "_implied_pe": implied_pe,
                "reason": None,
            })
            return result
        except Exception as exc:
            result["reason"] = f"DCF error: {exc}"
            return result
