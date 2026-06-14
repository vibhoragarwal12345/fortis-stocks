"""THREE forward de-leverage tracks (Part F). No taxation anywhere; no fresh
capital; fund only by selling/trimming cash holdings. Rupee basis is EXACT
quantity (workbook col F) x LIVE EOD price (yfinance) -- consistent with the
P&L and reference levels used everywhere else in the report.

Funding-model assumption (stated plainly in the PDF): the margin-LOAN balance is
not in the holdings file, so the cash a track must raise to neutralise an MTF
position is taken as that position's full current MARKET VALUE -- a conservative
UPPER BOUND (true loan <= market value). Positions that are EXITED are squared
off by their own sale (proceeds repay their margin) and need no external cash;
positions CONVERTED-TO-CASH-and-kept must be funded from cash-holding sales.
"""
from __future__ import annotations
import json, math, sys
sys.stdout.reconfigure(encoding="utf-8")
from verdicts import VERDICT

M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
H = M["holdings"]

DEFENCE = {"BDL.NS","BEL.NS","HAL.NS","HBLENGINE.NS","AVANTEL.NS","APOLLO.NS","ASTRAMICRO.NS","AVALON.NS"}
MTF = {tk for tk,d in H.items() if d.get("mtf")}

def mv(tk):   # current market value, qty x live
    d = H[tk]; return (d.get("mkt_value_live") or 0)
def px(tk):   return H[tk].get("price")
def cost(tk): return H[tk].get("avg_cost")
def qty(tk):  return H[tk].get("qty")
def gain_full(tk):
    if px(tk) and cost(tk) and qty(tk): return qty(tk)*(px(tk)-cost(tk))
    return 0.0

TOTAL_LIVE = sum(mv(tk) for tk in H)
MTF_LIVE = sum(mv(tk) for tk in MTF)
CASH_LIVE = TOTAL_LIVE - MTF_LIVE
DEF_LIVE = sum(mv(tk) for tk in DEFENCE)

# Funding priority: names whose sale improves the book anyway -> then quality core
FROTH_DISTRESS = ["AVANTEL.NS","APOLLO.NS","ASTRAMICRO.NS","AVALON.NS","HFCL.NS","RTNPOWER.NS","TATATECH.NS"]
QUALITY_CORE   = ["HBLENGINE.NS","BEL.NS","HAL.NS","BDL.NS"]
FUND_REASON = {
 "AVANTEL.NS":"frothy micro-cap defence + NET INSIDER SELLING — sell improves book",
 "APOLLO.NS":"+223% frothy small-cap defence (extreme multiple) — harvest",
 "ASTRAMICRO.NS":"+226% frothy small-cap defence — harvest",
 "AVALON.NS":"+206% frothy EMS/defence + net insider selling — harvest",
 "HFCL.NS":"richly-valued telecom-gear trade — trim",
 "RTNPOWER.NS":"sub-₹10 distressed IPP (EXIT verdict) — clean out",
 "TATATECH.NS":"still 55x after −37% (EXIT verdict) — book the loss, redeploy",
 "HBLENGINE.NS":"doubled defence winner — trim cuts the 61% concentration",
 "BEL.NS":"doubled defence winner — slice trims concentration (keep core)",
 "HAL.NS":"quality defence — last-resort trim",
 "BDL.NS":"richest large-cap multiple, top weight — last-resort trim",
}

def raise_cash(target, priority, exclude=()):
    """Walk the priority list selling full/partial until `target` is met."""
    sells, raised = [], 0.0
    for tk in priority:
        if tk in exclude or tk in MTF:
            continue
        if raised >= target - 1:
            break
        value, p, q, c = mv(tk), px(tk), qty(tk), cost(tk)
        if not (value and p and q):
            continue
        remaining = target - raised
        if value <= remaining + 1:
            shares, proceeds, kind = int(q), value, "FULL"
        else:
            shares = math.ceil(remaining / p); proceeds = shares*p; kind = "PARTIAL"
        gain = shares*(p-(c or p))
        raised += proceeds
        sells.append({"ticker":tk,"name":H[tk]["name"],"kind":kind,"shares_sold":shares,
                      "shares_total":int(q),"pct":round(shares/q*100,1),"price":round(p,2),
                      "proceeds":round(proceeds),"gain":round(gain),
                      "gain_pct":round((p/c-1)*100,1) if c else None,
                      "reason":FUND_REASON.get(tk,""),"cumulative":round(raised)})
    return sells, raised

def after_picture(sold_value_by_tk, mtf_neutralised_tks):
    """Portfolio shape after the operation. sold_value_by_tk: {tk: value_sold}."""
    rem_total = TOTAL_LIVE - sum(sold_value_by_tk.values())
    rem_def = DEF_LIVE - sum(v for tk,v in sold_value_by_tk.items() if tk in DEFENCE)
    rem_mtf = MTF_LIVE - sum(mv(tk) for tk in mtf_neutralised_tks)
    return {"rem_total":round(rem_total),"rem_def":round(rem_def),
            "rem_def_pct":round(rem_def/rem_total*100,1) if rem_total else None,
            "rem_mtf":round(rem_mtf),"rem_mtf_pct":round(rem_mtf/rem_total*100,1) if rem_total else None}

# ───────────────────────── TRACK 1 — full de-leverage ─────────────────────────
def track1():
    target = MTF_LIVE  # neutralise all 8, raise full market value (per instruction)
    sells, raised = raise_cash(target, FROTH_DISTRESS + QUALITY_CORE)
    sold = {s["ticker"]: s["proceeds"] for s in sells}
    realized = sum(s["gain"] for s in sells)
    ap = after_picture(sold, MTF)
    return {"name":"TRACK 1 — Full de-leverage (square off ALL 8 MTF)",
            "target":round(target),"raised":round(raised),"surplus":round(raised-target),
            "realized_gain":round(realized),"sells":sells,
            "mtf_neutralised":sorted(MTF),"mtf_action":"all 8 squared off / converted",
            "after":ap,
            "narrative":("Cleanest, most aggressive de-risk. Raise the full ₹%.0f-lakh MTF market value "
                "from cash sales: the frothy small-cap defence/EMS winners go first (good hygiene), then a "
                "slice of the doubled defence majors (HBL, BEL) which simultaneously cuts the ~61%% defence "
                "concentration. After: 0%% margin, no financing bleed, defence concentration down, ₹%.0f-lakh "
                "of profit booked — but you surrender further upside in the winners you sold."
                % (target/1e5, realized/1e5))}

# ───────────────────── TRACK 2 — selective hold (owner plan) ──────────────────
def track2():
    convert = {"PFC.NS":0.5, "ACC.NS":0.5, "BALAMINES.NS":1.0}      # keep, de-lever
    exit_full = ["EASEMYTRIP.NS","TRANSRAILL.NS","JWL.NS","PPLPHARMA.NS","MAHSEAMLES.NS"]
    exit_half = {"PFC.NS":0.5, "ACC.NS":0.5}
    # cash to RAISE = de-lever cost of the converted-and-kept portion only
    target = sum(mv(tk)*f for tk,f in convert.items())
    sells, raised = raise_cash(target, FROTH_DISTRESS)
    sold = {s["ticker"]: s["proceeds"] for s in sells}
    # realized P&L on the MTF EXITS (these self-fund; mostly losses booked)
    mtf_exits = []
    for tk in exit_full:
        mtf_exits.append({"ticker":tk,"name":H[tk]["name"],"value":round(mv(tk)),
                          "gain":round(gain_full(tk)),"gain_pct":round((px(tk)/cost(tk)-1)*100,1)})
    for tk,f in exit_half.items():
        mtf_exits.append({"ticker":tk,"name":H[tk]["name"]+" (half)","value":round(mv(tk)*f),
                          "gain":round(gain_full(tk)*f),"gain_pct":round((px(tk)/cost(tk)-1)*100,1)})
    exit_proceeds = sum(e["value"] for e in mtf_exits)
    exit_realized = sum(e["gain"] for e in mtf_exits)
    fund_realized = sum(s["gain"] for s in sells)
    ap = after_picture(sold, list(convert)+exit_full)  # all 8 neutralised (converted or exited)
    return {"name":"TRACK 2 — Selective hold (convert keepers, exit the rest) [owner's stated plan]",
            "target":round(target),"raised":round(raised),"surplus":round(raised-target),
            "realized_gain":round(fund_realized),"sells":sells,
            "convert":[{"ticker":tk,"name":H[tk]["name"],"frac":f,"value":round(mv(tk)*f)} for tk,f in convert.items()],
            "mtf_exits":mtf_exits,"exit_proceeds":round(exit_proceeds),"exit_realized":round(exit_realized),
            "mtf_neutralised":sorted(set(list(convert)+exit_full)),
            "after":ap,
            "narrative":("Owner's plan. Keep PFC(½), ACC(½), Balaji(full) — converted to cash; exit the rest of "
                "the MTF sleeve. The exits square off via their own sale (proceeds repay their margin), so only the "
                "₹%.0f-lakh keeper-conversion needs outside cash, easily covered by trimming the frothy winners — "
                "the quality defence core (BEL/HAL/BDL/HBL) is left untouched. The exits crystallise the leveraged "
                "LOSSES (₹%.0f-lakh) — that is the cost of having leveraged the losers."
                % (target/1e5, exit_realized/1e5))}

# ─────────────────── TRACK 3 — minimal-disturbance ───────────────────────────
def track3():
    squareoff = ["EASEMYTRIP.NS","JWL.NS","TRANSRAILL.NS","MAHSEAMLES.NS","PPLPHARMA.NS"]  # the 5 worst
    convert = ["PFC.NS","ACC.NS","BALAMINES.NS"]   # defensible -> full cash, keep
    target = sum(mv(tk) for tk in convert)          # raise to de-lever the kept core
    sells, raised = raise_cash(target, FROTH_DISTRESS)   # froth/distress ONLY — spare quality core
    sold = {s["ticker"]: s["proceeds"] for s in sells}
    mtf_exits = [{"ticker":tk,"name":H[tk]["name"],"value":round(mv(tk)),
                  "gain":round(gain_full(tk)),"gain_pct":round((px(tk)/cost(tk)-1)*100,1)} for tk in squareoff]
    exit_realized = sum(e["gain"] for e in mtf_exits)
    fund_realized = sum(s["gain"] for s in sells)
    ap = after_picture(sold, squareoff+convert)
    return {"name":"TRACK 3 — Minimal-disturbance (square off the 5 worst, keep & de-lever the defensible)",
            "target":round(target),"raised":round(raised),"surplus":round(raised-target),
            "realized_gain":round(fund_realized),"sells":sells,
            "convert":[{"ticker":tk,"name":H[tk]["name"],"frac":1.0,"value":round(mv(tk))} for tk in convert],
            "mtf_exits":mtf_exits,"exit_realized":round(exit_realized),
            "mtf_neutralised":sorted(squareoff+convert),
            "after":ap,
            "narrative":("Least disturbance to the quality core. Square off only the five deepest-loss / weakest-thesis "
                "leveraged names; keep PFC, ACC and Balaji, converted FULLY to cash. Raise the ₹%.0f-lakh needed from the "
                "frothy/distressed names ONLY — BEL/HAL/BDL/HBL are NOT touched, so the defence concentration is barely "
                "reduced (that is the trade-off: least churn, least concentration relief). Booked leveraged loss on the "
                "five exits: ₹%.0f-lakh." % (target/1e5, exit_realized/1e5))}

def main():
    out = {"basis":"qty (workbook col F) x LIVE EOD price (yfinance)",
           "total_live":round(TOTAL_LIVE),"mtf_live":round(MTF_LIVE),"cash_live":round(CASH_LIVE),
           "defence_live":round(DEF_LIVE),"defence_pct":round(DEF_LIVE/TOTAL_LIVE*100,1),
           "mtf_pct":round(MTF_LIVE/TOTAL_LIVE*100,1),
           "tracks":[track1(), track2(), track3()]}
    json.dump(out, open(r"D:\liquidation3.json","w",encoding="utf-8"), indent=2)
    print(f"Total live ₹{TOTAL_LIVE:,.0f} | MTF ₹{MTF_LIVE:,.0f} ({MTF_LIVE/TOTAL_LIVE*100:.1f}%) | "
          f"Cash ₹{CASH_LIVE:,.0f} | Defence ₹{DEF_LIVE:,.0f} ({DEF_LIVE/TOTAL_LIVE*100:.1f}%)")
    for t in out["tracks"]:
        cov = "COVERED" if t["raised"] >= t["target"]-1 else "SHORTFALL"
        print(f"\n{t['name']}")
        print(f"  target ₹{t['target']:,} | raised ₹{t['raised']:,} | {cov} surplus ₹{t['surplus']:,} | "
              f"fund-gain ₹{t['realized_gain']:,}")
        print(f"  sells: " + ", ".join(f"{s['name'].split()[0]}({s['kind'][0]})" for s in t['sells']))
        print(f"  after: defence {t['after']['rem_def_pct']}%  MTF {t['after']['rem_mtf_pct']}%  total ₹{t['after']['rem_total']:,}")
    print("\nSaved D:\\liquidation3.json")

if __name__ == "__main__":
    main()
