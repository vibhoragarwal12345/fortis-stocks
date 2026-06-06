"""
India source #3 -- SEBI SAST / PIT insider disclosures (the Form-4 counterpart).
NSE's corporates-pit feed reports promoter/insider acquisitions & disposals.
Free, no key, via nsepython. Each record is classified BUY / SELL and
company-verified; a net-signal summary mirrors our US insider-cluster logic.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")
from nsepython import nsefetch

from ._common import bare_symbol, parse_dt, verify_company

_URL = "https://www.nseindia.com/api/corporates-pit?index=equities&symbol={}"


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return 0.0


def get_insider_trades(symbol: str, expected_name: str | None = None,
                       days: int = 365) -> list[dict]:
    """Recent SAST/PIT disclosures for `symbol`, newest first, BUY/SELL-tagged."""
    sym = bare_symbol(symbol)
    raw = nsefetch(_URL.format(sym))
    rows = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in rows:
        dt = parse_dt(r.get("date") or r.get("intimDt"))
        if dt and dt < cutoff:
            continue
        buy_q, sell_q = _num(r.get("buyQuantity")), _num(r.get("sellquantity"))
        buy_v, sell_v = _num(r.get("buyValue")), _num(r.get("sellValue"))
        before, after = _num(r.get("befAcqSharesPer")), _num(r.get("afterAcqSharesPer"))
        # PIT trading-window trades populate buy/sell qty; SAST disclosures
        # (off-market / inter-se) leave those 0 and encode direction in the
        # before->after holding %. Infer from whichever is available.
        if buy_q or buy_v:
            direction = "BUY"
        elif sell_q or sell_v:
            direction = "SELL"
        elif r.get("afterAcqSharesPer") not in (None, "") and after != before:
            direction = "BUY" if after > before else "SELL"
        else:
            direction = "OTHER"
        mode = (r.get("acqMode") or "")
        is_inter_se = "inter-se" in mode.lower()  # promoter-group reshuffle, not a market signal
        rec_name = r.get("company")
        out.append({
            "date": r.get("date") or r.get("intimDt"),
            "acquirer": r.get("acqName"),
            "person_category": r.get("personCategory"),
            "direction": direction,
            "mode": mode or None,
            "inter_se": is_inter_se,
            "shares": int(_num(r.get("secAcq"))) or None,
            "value_inr": _num(r.get("secVal")) or (buy_v or sell_v) or None,
            "holding_before_pct": _num(r.get("befAcqSharesPer")) or None,
            "holding_after_pct": _num(r.get("afterAcqSharesPer")) or None,
            "company": rec_name,
            "verified": verify_company(rec_name, expected_name),
        })
    out.sort(key=lambda x: parse_dt(x["date"]) or datetime.min, reverse=True)
    return out


def net_signal(trades: list[dict]) -> dict:
    """Aggregate to a directional signal (mirrors US insider-cluster scoring).

    Inter-se transfers (promoter-group reshuffles) are EXCLUDED from the net --
    they are not a market signal. promoter_open_market_selling_flag fires only
    on genuine non-inter-se promoter disposals (the real bearish tell).
    """
    directional = [t for t in trades if not t["inter_se"]]
    buys = [t for t in directional if t["direction"] == "BUY"]
    sells = [t for t in directional if t["direction"] == "SELL"]
    buy_val = sum(t["value_inr"] or 0 for t in buys)
    sell_val = sum(t["value_inr"] or 0 for t in sells)
    net = buy_val - sell_val
    promoter_sell = any(
        t["direction"] == "SELL" and "promoter" in (t["person_category"] or "").lower()
        for t in directional)
    return {
        "n_buys": len(buys), "n_sells": len(sells),
        "n_inter_se": sum(1 for t in trades if t["inter_se"]),
        "buy_value_inr": buy_val, "sell_value_inr": sell_val,
        "net_value_inr": net,
        "signal": ("net_buying" if net > 0 else "net_selling" if net < 0 else "neutral"),
        "promoter_open_market_selling_flag": promoter_sell,
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for sym, nm in [("GMMPFAUDLR", "GMM Pfaudler"), ("AVANTEL", "Avantel"), ("BEL", "Bharat Electronics")]:
        t = get_insider_trades(sym, nm, days=365)
        print(f"\n{sym}: {len(t)} disclosures (365d) | net={net_signal(t)}")
        for x in t[:4]:
            print(f"  [{x['date']}] {x['direction']} {x['person_category']} | {x['shares']} sh | "
                  f"hold {x['holding_before_pct']}->{x['holding_after_pct']}% | verified={x['verified']}")
