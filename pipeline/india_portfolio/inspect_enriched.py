import json, sys
sys.stdout.reconfigure(encoding="utf-8")
M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
H = M["holdings"]
print(f"enriched_at: {M.get('nse_enriched_at_utc')}  ban_date: {M.get('nse_ban_trade_date')}")
print(f"{'NAME':<28}{'insider':<10}{'b/s/inter':<12}{'sast':<14}{'ord':<4}{'ban':<5}{'results'}")
for tk,d in H.items():
    nse=d.get("nse") or {}
    ins=nse.get("insider") or {}; net=ins.get("net") or {}
    sast=nse.get("sast") or {}; sn=sast.get("net") or {}
    sig=net.get("signal","?") if net else (ins.get("error","-")[:8] if "error" in ins else "none")
    bs=f"{net.get('n_buys','-')}/{net.get('n_sells','-')}/{net.get('n_inter_se','-')}" if net else "-"
    ow=len(nse.get("order_wins") or [])
    print(f"{d['name']:<28}{sig:<10}{bs:<12}{sn.get('signal','-'):<14}{ow:<4}{str(nse.get('in_fno_ban')):<5}{nse.get('results_filed')}")
print("\n--- ORDER WINS (quantified) ---")
for tk,d in H.items():
    for o in (d.get("nse") or {}).get("order_wins") or []:
        if o.get("quantified"):
            print(f"  {d['name']:<22} {o['date']} amt={o['amount']} client={o['client']}")
print("\n--- promoter_open_market_selling_flag TRUE ---")
for tk,d in H.items():
    net=((d.get("nse") or {}).get("insider") or {}).get("net") or {}
    if net.get("promoter_open_market_selling_flag"):
        print(f"  {d['name']:<24} buys={net.get('n_buys')} sells={net.get('n_sells')} inter_se={net.get('n_inter_se')}")
