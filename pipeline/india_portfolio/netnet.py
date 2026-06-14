import json, sys
sys.stdout.reconfigure(encoding="utf-8")
L = json.load(open(r"D:\liquidation3.json", encoding="utf-8"))
print("basis:", L["basis"])
print(f"book ₹{L['total_live']:,} | MTF ₹{L['mtf_live']:,} ({L['mtf_pct']}%) | defence {L['defence_pct']}%\n")
for t in L["tracks"]:
    cov = "COVERED" if t["raised"] >= t["target"]-1 else "SHORTFALL"
    er = t.get("exit_realized")
    print(t["name"])
    print(f"  target ₹{t['target']:,} | raised ₹{t['raised']:,} | {cov} (surplus ₹{t['surplus']:,})")
    print(f"  fund-side gain booked ₹{t['realized_gain']:,}" + (f" | MTF-exit realised ₹{er:,}" if isinstance(er,int) else ""))
    print(f"  after: MTF {t['after']['rem_mtf_pct']}% | defence {t['after']['rem_def_pct']}% | residual ₹{t['after']['rem_total']:,}")
    print(f"  cash names sold: {', '.join(s['name'].split()[0]+'('+s['kind'][0]+')' for s in t['sells'])}\n")
