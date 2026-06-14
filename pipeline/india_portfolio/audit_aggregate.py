"""Self-audit + portfolio aggregation on EXACT values (qty x live price).
Recomputes every headline number, verifies it traces to the fetched source,
and builds the value-based portfolio stats the report's cover/summary cite."""
from __future__ import annotations
import json, sys, warnings
sys.stdout.reconfigure(encoding="utf-8")
from collections import defaultdict
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yfinance as yf

J = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
H = J["holdings"]
DEFENCE = {"BDL.NS","BEL.NS","HAL.NS","HBLENGINE.NS","AVANTEL.NS","APOLLO.NS","ASTRAMICRO.NS","AVALON.NS"}

print("="*70); print("SELF-AUDIT PASS"); print("="*70)
issues = []
for tk, d in H.items():
    px, cost, pnl = d.get("price"), d.get("avg_cost"), d.get("unrealized_pnl_pct")
    if px and cost:
        recompute = round((px/cost - 1)*100, 1)
        if abs(recompute - (pnl if pnl is not None else -999)) > 0.15:
            issues.append(f"P&L mismatch {tk}: stored {pnl} vs recompute {recompute}")
    mvl, q = d.get("mkt_value_live"), d.get("qty")
    if px and q and mvl and abs(px*q - mvl) > max(1.0, 0.001*mvl):
        issues.append(f"mkt_value_live mismatch {tk}: {mvl} vs qty*px {px*q:.0f}")
    for f in ("price","rsi14","ann_vol_pct"):
        v = d.get(f)
        if v is not None and not isinstance(v,(int,float)):
            issues.append(f"{tk}.{f} non-numeric: {v!r}")

def mv(tk): return H[tk].get("mkt_value_live") or 0
total = sum(mv(tk) for tk in H)
mtf_live = sum(mv(tk) for tk in H if H[tk].get("mtf"))
cash_live = total - mtf_live
def_live = sum(mv(tk) for tk in DEFENCE)

# value-weighted P&L
def vw_pnl(keys):
    num = sum(mv(tk)*H[tk]["unrealized_pnl_pct"] for tk in keys
              if H[tk].get("unrealized_pnl_pct") is not None and mv(tk))
    den = sum(mv(tk) for tk in keys if H[tk].get("unrealized_pnl_pct") is not None and mv(tk))
    return num/den if den else None
book_pnl = vw_pnl(list(H))
mtf_pnl = vw_pnl([tk for tk in H if H[tk].get("mtf")])
cash_pnl = vw_pnl([tk for tk in H if not H[tk].get("mtf")])
mtf_underwater = sum(1 for tk in H if H[tk].get("mtf") and (H[tk].get("unrealized_pnl_pct") or 0) < 0)

# theme weights by value
theme_v = defaultdict(float)
for tk in H: theme_v[H[tk]["theme"]] += mv(tk)

quant_have = sum(1 for d in H.values() if d.get("var_95_1d_pct") is not None)
mc_have = sum(1 for d in H.values() if d.get("mc_p50") is not None)
garch_have = sum(1 for d in H.values() if d.get("garch_ann_vol_pct") is not None)
cur_mismatch = [tk for tk,d in H.items() if d.get("fundamentals_currency_mismatch")]
susp = sum(1 for d in H.values() for v in (d.get("fundamentals_suspect") or {}).values() if v)

print(f"P&L recompute issues: {len(issues)}")
for i in issues: print("  !", i)
print(f"VaR {quant_have}/38 | MonteCarlo {mc_have}/38 | GARCH {garch_have}/38")
print(f"currency-mismatch names: {cur_mismatch or 'none'} | suspect fundamental fields flagged: {susp}")
print(f"\nTotal live ₹{total:,.0f} | MTF ₹{mtf_live:,.0f} ({mtf_live/total*100:.1f}%) | "
      f"Cash ₹{cash_live:,.0f} | Defence ₹{def_live:,.0f} ({def_live/total*100:.1f}%)")
print(f"Value-weighted P&L: book {book_pnl:+.1f}% | MTF {mtf_pnl:+.1f}% | cash {cash_pnl:+.1f}%  "
      f"(MTF underwater {mtf_underwater}/8)")

# defence cluster correlation (live)
defence = [tk for tk in DEFENCE]
px = yf.download(defence, period="1y", interval="1d", auto_adjust=False, progress=False, group_by="ticker")
rets = pd.DataFrame({tk: np.log(px[tk]["Close"]/px[tk]["Close"].shift(1)) for tk in defence}).dropna()
corr = rets.corr(); iu = np.triu_indices_from(corr.values, k=1)
avg_corr = float(corr.values[iu].mean())
print(f"Defence cluster avg pairwise corr (1y): {avg_corr:.2f}")

agg = {"total_live": round(total), "mtf_live": round(mtf_live), "cash_live": round(cash_live),
       "defence_live": round(def_live), "defence_pct": round(def_live/total*100,1),
       "mtf_pct": round(mtf_live/total*100,1), "cash_pct": round(cash_live/total*100,1),
       "theme_values": {k: round(v) for k,v in theme_v.items()},
       "theme_pct": {k: round(v/total*100,1) for k,v in theme_v.items()},
       "book_pnl_pct": round(book_pnl,1), "mtf_pnl_pct": round(mtf_pnl,1), "cash_pnl_pct": round(cash_pnl,1),
       "mtf_underwater_count": mtf_underwater, "mtf_count": sum(1 for tk in H if H[tk].get("mtf")),
       "defence_cluster_avg_corr": round(avg_corr,2),
       "var_have": quant_have, "mc_have": mc_have, "garch_have": garch_have,
       "currency_mismatch": cur_mismatch, "suspect_fundamental_fields": susp,
       "audit_issues": issues}
json.dump(agg, open(r"D:\portfolio_aggregates.json","w",encoding="utf-8"), indent=2)
print("\nSaved D:\\portfolio_aggregates.json")
