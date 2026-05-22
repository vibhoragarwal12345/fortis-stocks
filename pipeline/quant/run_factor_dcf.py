"""
Quantitative Models Desk -- runner for Model 4 (Fama-French) and Model 6 (DCF).
Runs both on the top-ranked tickers, persists to factor_attribution /
dcf_valuations, and prints the report.

Usage:
    python pipeline/quant/run_factor_dcf.py [N]      # default N = 30
"""

import logging
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from quant.dcf import DCFValuation  # noqa: E402
from quant.fama_french import FACTORS, FamaFrench5Factor  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def _load_top_tickers(db, n: int) -> tuple[str, list[str]]:
    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        return "", []
    rd = recent[0]["run_date"]
    rows = (db.table("ranked_focus_list").select("ticker,rank")
            .eq("run_date", rd).eq("run_type", "midday")
            .order("rank").limit(n).execute().data or [])
    return rd, [r["ticker"] for r in rows]


def run(n: int = 30) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    run_date, tickers = _load_top_tickers(db, n)
    if not tickers:
        log.warning("ranked_focus_list empty -- run ranking_engine first.")
        return
    today = datetime.now(timezone.utc).date().isoformat()
    log.info("Factor + DCF desk -- %d tickers", len(tickers))

    # ── Fama-French ───────────────────────────────────────────────────────────
    ff = FamaFrench5Factor()
    factors = ff.load_factor_data()
    raw = yf.download(tickers, period="3y", interval="1d", auto_adjust=True,
                      progress=False, group_by="ticker")
    multi = isinstance(raw.columns, pd.MultiIndex)

    ff_results, dcf_results, failures = {}, {}, []
    for t in tickers:
        try:
            close = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            rets = close.pct_change().dropna()              # simple returns for FF
            reg = ff.regress(rets, factors)
            interp = ff.interpret_factors(reg, t)
            alpha = ff.alpha_assessment(reg["alpha"], reg["alpha_t"], reg["n_obs"])
            ff_results[t] = {"reg": reg, "interp": interp, "alpha": alpha}
        except Exception as exc:
            failures.append(("fama_french", t, str(exc)))
            log.warning("FF %s failed: %s", t, exc)

    # ── DCF ───────────────────────────────────────────────────────────────────
    dcf = DCFValuation()
    for t in tickers:
        r = dcf.value(t)
        dcf_results[t] = r
        if r.get("reason"):
            failures.append(("dcf", t, r["reason"]))

    # Peer-multiple check: median trailing P/E per sector across this batch.
    sector_pes = defaultdict(list)
    for t, r in dcf_results.items():
        if r.get("sector") and r.get("trailing_pe") and r["trailing_pe"] > 0:
            sector_pes[r["sector"]].append(r["trailing_pe"])
    sector_median = {s: statistics.median(v) for s, v in sector_pes.items() if len(v) >= 3}
    _downgrade = {"high": "medium", "medium": "low", "low": "low"}
    for t, r in dcf_results.items():
        if r.get("valuation_label") == "not_applicable":
            continue
        peer_med = sector_median.get(r.get("sector"))
        verdict = dcf.peer_multiples_check(r.get("_implied_pe"), peer_med)
        r["peer_multiple_check"] = verdict
        if verdict in ("outlier_high", "outlier_low"):
            r["confidence"] = _downgrade[r["confidence"]]

    # ── Persist ───────────────────────────────────────────────────────────────
    fa_rows = []
    for t, d in ff_results.items():
        b, ts = d["reg"]["betas"], d["reg"]["tstats"]
        fa_rows.append({
            "ticker": t, "calculation_date": today,
            "beta_market": round(b["Mkt-RF"], 4), "beta_size": round(b["SMB"], 4),
            "beta_value": round(b["HML"], 4), "beta_profitability": round(b["RMW"], 4),
            "beta_investment": round(b["CMA"], 4),
            "t_market": round(ts["Mkt-RF"], 3), "t_size": round(ts["SMB"], 3),
            "t_value": round(ts["HML"], 3), "t_profitability": round(ts["RMW"], 3),
            "t_investment": round(ts["CMA"], 3),
            "alpha_daily": d["alpha"]["alpha_daily"], "alpha_annual": d["alpha"]["alpha_annual"],
            "alpha_significance": d["alpha"]["significance"],
            "r_squared": round(d["reg"]["r_squared"], 4),
            "regression_window_days": d["reg"]["n_obs"],
            "factor_label": d["interp"]["factor_label"],
            "factor_box": d["interp"]["factor_box"]})
    dcf_rows = []
    for t, r in dcf_results.items():
        dcf_rows.append({
            "ticker": t, "calculation_date": today,
            "current_price": r.get("current_price"),
            "dcf_value_per_share": r.get("dcf_value_per_share"),
            "dcf_low": r.get("dcf_low"), "dcf_high": r.get("dcf_high"),
            "upside_to_dcf_pct": r.get("upside_to_dcf_pct"),
            "valuation_label": r.get("valuation_label"),
            "assumptions": r.get("assumptions"),
            "peer_multiple_check": r.get("peer_multiple_check"),
            "confidence": r.get("confidence"), "limitations": r.get("limitations")})
    for tbl, rows in (("factor_attribution", fa_rows), ("dcf_valuations", dcf_rows)):
        try:
            db.table(tbl).upsert(rows, on_conflict="ticker,calculation_date").execute()
            log.info("%s: %d rows persisted", tbl, len(rows))
        except Exception as exc:
            log.warning("%s not persisted (%s) -- apply the migration",
                        tbl, getattr(exc, "code", exc))

    _report(tickers, ff_results, dcf_results, failures)


def _report(tickers, ff, dcf, failures) -> None:
    bar = "=" * 80
    top5 = [t for t in tickers if t in ff or t in dcf][:5]
    print(f"\n{bar}\nFAMA-FRENCH 5-FACTOR  +  DCF VALUATION\n{bar}")

    print("\n[ 1. FAMA-FRENCH -- TOP 5 ]")
    for t in top5:
        d = ff.get(t)
        if not d:
            print(f"  {t}: regression failed"); continue
        r, i, a = d["reg"], d["interp"], d["alpha"]
        print(f"\n  {t}  R2={r['r_squared']:.2f}  ({r['n_obs']}d)")
        print(f"    {i['factor_label']}  [{i['factor_box']}]")
        print(f"    betas: mkt {r['betas']['Mkt-RF']:+.2f} smb {r['betas']['SMB']:+.2f} "
              f"hml {r['betas']['HML']:+.2f} rmw {r['betas']['RMW']:+.2f} "
              f"cma {r['betas']['CMA']:+.2f}")
        print(f"    alpha {a['alpha_annual']*100:+.1f}%/yr  (t={a['alpha_t']:+.2f}, "
              f"{a['significance']})")

    print(f"\n{bar}\n[ 2. DCF VALUATION -- TOP 5 ]")
    for t in top5:
        r = dcf.get(t)
        if not r:
            continue
        if r.get("valuation_label") == "not_applicable":
            print(f"\n  {t}: NOT APPLICABLE -- {r.get('reason')}")
            continue
        ps, lo, hi = r.get("dcf_value_per_share"), r.get("dcf_low"), r.get("dcf_high")
        up = r.get("upside_to_dcf_pct")
        print(f"\n  {t}  price ${r.get('current_price')}  ->  DCF ${ps}  "
              f"(range ${lo}-${hi})")
        print(f"    {r.get('valuation_label')}  upside {up*100:+.0f}%  "
              f"confidence={r.get('confidence')}  peers={r.get('peer_multiple_check')}"
              if up is not None else f"    {r.get('valuation_label')}")

    print(f"\n{bar}\n[ 3. CLEANEST SKILL SIGNALS -- significant +alpha AND R2 > 0.50 ]")
    skill = [(t, d) for t, d in ff.items()
             if d["alpha"]["significance"] == "significant_positive"
             and d["reg"]["r_squared"] > 0.50]
    if skill:
        for t, d in sorted(skill, key=lambda x: x[1]["alpha"]["alpha_annual"], reverse=True):
            print(f"  {t:<8} alpha {d['alpha']['alpha_annual']*100:+.1f}%/yr  "
                  f"R2 {d['reg']['r_squared']:.2f}  t={d['alpha']['alpha_t']:+.2f}")
    else:
        print("  (none cleared both bars)")

    print(f"\n{bar}\n[ 4. DCF DISAGREES WITH PRICE -- |upside| > 25% ]")
    disagree = [(t, r) for t, r in dcf.items()
                if r.get("upside_to_dcf_pct") is not None
                and abs(r["upside_to_dcf_pct"]) > 0.25]
    if disagree:
        for t, r in sorted(disagree, key=lambda x: x[1]["upside_to_dcf_pct"], reverse=True):
            print(f"  {t:<8}{r['upside_to_dcf_pct']*100:>+8.0f}%  {r['valuation_label']:<20}"
                  f"confidence={r['confidence']}")
    else:
        print("  (none)")

    print(f"\n{bar}\n[ 5. QUALITY-CHECK FAILURES / FLAGS ]")
    flagged = False
    for t, d in ff.items():
        r2 = d["reg"]["r_squared"]
        if r2 < 0.30:
            flagged = True
            junk = (" -- JUNKY ALPHA (R2<0.20, alpha is likely noise not skill)"
                    if r2 < 0.20 and abs(d["alpha"]["alpha_annual"]) > 0.10 else "")
            print(f"  FF   {t:<8} R2 {r2:.2f} <0.30 -- factors explain poorly{junk}")
        if d["reg"]["n_obs"] < 252:
            flagged = True
            print(f"  FF   {t:<8} only {d['reg']['n_obs']} obs (<252)")
    for t, r in dcf.items():
        if r.get("reason"):
            flagged = True
            print(f"  DCF  {t:<8} {r['reason']}")
        elif r.get("confidence") == "low":
            flagged = True
            print(f"  DCF  {t:<8} confidence=low ({r.get('peer_multiple_check')})")
    for kind, t, why in failures:
        if kind == "fama_french":
            flagged = True
            print(f"  FF   {t:<8} {why}")
    if not flagged:
        print("  (none)")
    print(bar + "\n")


if __name__ == "__main__":
    arg = 30
    if len(sys.argv) > 1:
        try:
            arg = int(sys.argv[1])
        except ValueError:
            pass
    run(arg)
