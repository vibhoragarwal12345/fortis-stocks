"""Enrich portfolio_metrics.json with the live NSE data layer (#2/#3/#4):
announcements (catalysts), SAST/PIT insider net-signal, F&O ban membership.
Each call is company-verified; failures degrade to 'unavailable' (never faked)."""
from __future__ import annotations
import json, sys, time, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.stdout.reconfigure(encoding="utf-8")

from pipeline.india import nse_announcements as ann
from pipeline.india import insider_sast as ins
from pipeline.india import fno_ban as ban

J = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
H = J["holdings"]

print("Fetching F&O ban list ...")
banlist = ban.get_fno_ban_list()
print("  trade_date", banlist["trade_date"], "| in ban:", banlist["symbols"])

for tk, d in H.items():
    sym = tk.split(".")[0]
    name = d["name"]
    rec = {"announcements": None, "insider": None, "in_fno_ban": None,
           "nse_status": "ok"}
    # announcements (90d)
    try:
        a = ann.get_announcements(sym, name, days=90)
        verified = [x for x in a if x["verified"]]
        rec["announcements"] = {
            "count_90d": len(verified),
            "by_category": ann.summarize(verified),
            "recent": [{"date": x["date"], "category": x["category"],
                        "text": (x["text"] or x["nse_desc"] or "")[:160]}
                       for x in verified[:6]],
            "verified_company": all(x["verified"] for x in a) if a else True,
        }
    except Exception as e:
        rec["announcements"] = {"error": str(e)[:100]}; rec["nse_status"] = "partial"
    time.sleep(0.7)
    # insider SAST/PIT (365d)
    try:
        t = ins.get_insider_trades(sym, name, days=365)
        rec["insider"] = {
            "count_365d": len(t),
            "net": ins.net_signal(t),
            "recent": [{"date": x["date"], "direction": x["direction"],
                        "person": x["person_category"], "inter_se": x["inter_se"],
                        "shares": x["shares"], "value_inr": x["value_inr"],
                        "hold_before": x["holding_before_pct"], "hold_after": x["holding_after_pct"]}
                       for x in t[:6]],
        }
    except Exception as e:
        rec["insider"] = {"error": str(e)[:100]}; rec["nse_status"] = "partial"
    # F&O ban membership
    rec["in_fno_ban"] = ban.is_in_fno_ban(sym, banlist)
    d["nse"] = rec
    ac = rec["announcements"].get("count_90d") if isinstance(rec["announcements"], dict) else "?"
    ic = rec["insider"].get("count_365d") if isinstance(rec["insider"], dict) else "?"
    print(f"  {name:<26} ann90d={ac} insider365d={ic} ban={rec['in_fno_ban']}")
    time.sleep(0.7)

J["nse_ban_trade_date"] = banlist["trade_date"]
J["nse_enriched_at_utc"] = __import__("datetime").datetime.utcnow().isoformat()
json.dump(J, open(r"D:\portfolio_metrics.json", "w", encoding="utf-8"), indent=2, default=str)
print("\nSaved enriched D:\\portfolio_metrics.json")
