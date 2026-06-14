"""
India source -- SEBI SAST substantial-acquisition disclosures (Reg 29/7/8).
NSE corporate-sast feed: 5%+ acquisitions/disposals and creeping-acquisition
crossings. Free, no key, via nsepython. Separate from the PIT trading-window
feed (insider_sast.py). Sparse by design -- only populated for names with a
recent substantial 5%+ move; an empty result is a valid 'no event', not a fault.
"""
from __future__ import annotations
import warnings
from datetime import datetime, timedelta
warnings.filterwarnings("ignore")
from nsepython import nsefetch
from ._common import bare_symbol, parse_dt, verify_company

_URL = "https://www.nseindia.com/api/corporate-sast?index=equities&symbol={}"


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return 0.0


def get_sast(symbol: str, expected_name: str | None = None, days: int = 365) -> list[dict]:
    sym = bare_symbol(symbol)
    raw = nsefetch(_URL.format(sym))
    rows = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in rows:
        dt = parse_dt(r.get("date") or r.get("dissemDt") or r.get("intimDt"))
        if dt and dt < cutoff:
            continue
        before, after = _num(r.get("befAcqSharesPer")), _num(r.get("afterAcqSharesPer"))
        direction = "ACQUIRE" if after > before else "DISPOSE" if after < before else "OTHER"
        rec_name = r.get("company") or r.get("sm_name")
        out.append({
            "date": r.get("date") or r.get("dissemDt") or r.get("intimDt"),
            "acquirer": r.get("acqName") or r.get("name"),
            "direction": direction,
            "shares": int(_num(r.get("secAcq"))) or None,
            "holding_before_pct": before or None,
            "holding_after_pct": after or None,
            "company": rec_name,
            "verified": verify_company(rec_name, expected_name),
        })
    out.sort(key=lambda x: parse_dt(x["date"]) or datetime.min, reverse=True)
    return out


def net_sast(rows: list[dict]) -> dict:
    acq = [r for r in rows if r["direction"] == "ACQUIRE"]
    dis = [r for r in rows if r["direction"] == "DISPOSE"]
    return {"n_events": len(rows), "n_acquire": len(acq), "n_dispose": len(dis),
            "signal": ("accumulation" if len(acq) > len(dis) else
                       "distribution" if len(dis) > len(acq) else "balanced" if rows else "none")}


if __name__ == "__main__":
    import sys; sys.stdout.reconfigure(encoding="utf-8")
    for sym, nm in [("AVANTEL","Avantel"), ("PPLPHARMA","Piramal Pharma"), ("BEL","Bharat Electronics")]:
        s = get_sast(sym, nm)
        print(f"{sym}: {len(s)} SAST events | {net_sast(s)}")
        for x in s[:3]:
            print(f"   [{x['date']}] {x['direction']} {x['holding_before_pct']}->{x['holding_after_pct']}% {x['acquirer']}")
