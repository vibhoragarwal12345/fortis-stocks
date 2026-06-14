"""Enrich portfolio_metrics.json with the live NSE data layer:
 - corporate announcements (90d) + Reg-30 ORDER-WIN extraction (amount/client
   pulled from filing text via regex; never invented -> 'disclosed but unquantified'),
 - SEBI PIT insider net BUY/SELL signal (derived, not hardcoded),
 - SEBI SAST substantial-acquisition signal,
 - F&O ban membership (crowding proxy),
 - financial-results filing presence.
Each call is company-verified; failures degrade to 'unavailable' (never faked).
Rate-limited per NSE anti-bot etiquette."""
from __future__ import annotations
import json, re, sys, time, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.stdout.reconfigure(encoding="utf-8")

from pipeline.india import nse_announcements as ann
from pipeline.india import insider_sast as ins
from pipeline.india import sast as sastmod
from pipeline.india import fno_ban as ban
from nsepython import nsefetch

# ---- Reg-30 order-win amount/client extraction (verbatim from filing text) ----
_AMT = re.compile(
    r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\b|lakh|lacs?|mn|million|bn|billion)",
    re.I)
_CLIENT = re.compile(r"\bfrom\s+([A-Z][A-Za-z0-9&.,\- ]{4,55}?)(?:\s+(?:for|towards|worth|valued|amounting|to set|of)\b|[.,;])", re.I)

def extract_order(rec: dict) -> dict:
    blob = f"{rec.get('nse_desc') or ''}. {rec.get('text') or ''}"
    amt = _AMT.search(blob)
    amount_txt = None
    if amt:
        unit = amt.group(2).lower()
        amount_txt = f"Rs {amt.group(1)} {('crore' if unit in ('crore','cr') else unit)}"
    cl = _CLIENT.search(blob)
    client = cl.group(1).strip() if cl else None
    return {"date": rec["date"], "amount": amount_txt, "client": client,
            "quantified": amount_txt is not None,
            "subject": (rec.get("nse_desc") or "")[:90],
            "snippet": (rec.get("text") or rec.get("nse_desc") or "")[:200].replace("\n"," ").strip()}

def has_recent_results(sym: str) -> bool:
    try:
        r = nsefetch(f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly")
        rows = r if isinstance(r, list) else r.get("data", [])
        return len(rows) > 0
    except Exception:
        return None


def main():
    J = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
    H = J["holdings"]
    print("Fetching F&O ban list ...")
    try:
        banlist = ban.get_fno_ban_list()
    except Exception as e:
        banlist = {"trade_date": None, "symbols": [], "error": str(e)[:80]}
    print("  trade_date", banlist.get("trade_date"), "| in ban:", banlist.get("symbols"))

    for tk, d in H.items():
        sym = tk.split(".")[0]; name = d["name"]
        rec = {"announcements": None, "order_wins": [], "insider": None,
               "sast": None, "in_fno_ban": None, "results_filed": None, "nse_status": "ok"}
        # announcements + order wins
        try:
            a = ann.get_announcements(sym, name, days=90)
            verified = [x for x in a if x["verified"]]
            rec["announcements"] = {
                "count_90d": len(verified), "by_category": ann.summarize(verified),
                "recent": [{"date": x["date"], "category": x["category"],
                            "text": (x["nse_desc"] or x["text"] or "")[:150]} for x in verified[:6]]}
            rec["order_wins"] = [extract_order(x) for x in verified if x["category"] == "order_win"][:5]
        except Exception as e:
            rec["announcements"] = {"error": str(e)[:100]}; rec["nse_status"] = "partial"
        time.sleep(0.6)
        # PIT insider
        try:
            t = ins.get_insider_trades(sym, name, days=365)
            rec["insider"] = {"count_365d": len(t), "net": ins.net_signal(t),
                "recent": [{"date": x["date"], "direction": x["direction"],
                            "person": x["person_category"], "inter_se": x["inter_se"],
                            "shares": x["shares"], "value_inr": x["value_inr"],
                            "hold_before": x["holding_before_pct"], "hold_after": x["holding_after_pct"]}
                           for x in t[:6]]}
        except Exception as e:
            rec["insider"] = {"error": str(e)[:100]}; rec["nse_status"] = "partial"
        time.sleep(0.6)
        # SAST
        try:
            s = sastmod.get_sast(sym, name, days=365)
            rec["sast"] = {"net": sastmod.net_sast(s),
                           "recent": [{"date": x["date"], "direction": x["direction"],
                                       "hold_before": x["holding_before_pct"],
                                       "hold_after": x["holding_after_pct"], "acquirer": x["acquirer"]}
                                      for x in s[:4]]}
        except Exception as e:
            rec["sast"] = {"error": str(e)[:100]}
        time.sleep(0.6)
        rec["in_fno_ban"] = ban.is_in_fno_ban(sym, banlist)
        rec["results_filed"] = has_recent_results(sym)
        d["nse"] = rec
        ac = rec["announcements"].get("count_90d") if isinstance(rec["announcements"], dict) else "?"
        ic = rec["insider"].get("count_365d") if isinstance(rec["insider"], dict) else "?"
        nsig = rec["insider"]["net"]["signal"] if isinstance(rec["insider"], dict) and "net" in rec["insider"] else "?"
        print(f"  {name:<28} ann90d={ac} ord={len(rec['order_wins'])} insider={ic}/{nsig} "
              f"sast={rec['sast'].get('net',{}).get('n_events') if isinstance(rec['sast'],dict) else '?'} ban={rec['in_fno_ban']}")
        time.sleep(0.5)

    J["nse_ban_trade_date"] = banlist.get("trade_date")
    J["nse_enriched_at_utc"] = __import__("datetime").datetime.utcnow().isoformat()
    json.dump(J, open(r"D:\portfolio_metrics.json","w",encoding="utf-8"), indent=2, default=str)
    print("\nSaved enriched D:\\portfolio_metrics.json")


if __name__ == "__main__":
    main()
