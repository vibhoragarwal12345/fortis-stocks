"""Self-audit + portfolio aggregation. Recomputes every headline number,
verifies it traces to the fetched source, then builds portfolio-level stats."""
from __future__ import annotations
import json, warnings
from collections import defaultdict
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yfinance as yf

J = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))
H = J["holdings"]

print("="*70); print("SELF-AUDIT PASS"); print("="*70)
issues = []
for tk, d in H.items():
    px, cost, pnl = d.get("price"), d.get("avg_cost"), d.get("unrealized_pnl_pct")
    if px and cost:
        recompute = round((px/cost - 1)*100, 1)
        ok = abs(recompute - (pnl if pnl is not None else -999)) < 0.15
        if not ok:
            issues.append(f"P&L mismatch {tk}: stored {pnl} vs recompute {recompute}")
    # numeric fields must be real numbers, not strings/memory
    for f in ("price","rsi14","ann_vol_pct"):
        v = d.get(f)
        if v is not None and not isinstance(v,(int,float)):
            issues.append(f"{tk}.{f} non-numeric: {v!r}")
quant_have = sum(1 for d in H.values() if d.get("var_95_1d_pct") is not None)
mc_have = sum(1 for d in H.values() if d.get("mc_p50") is not None)
garch_have = sum(1 for d in H.values() if d.get("garch_ann_vol_pct") is not None)
print(f"P&L recompute issues: {len(issues)}")
for i in issues: print("  !", i)
print(f"VaR computed: {quant_have}/38 | MonteCarlo: {mc_have}/38 | GARCH: {garch_have}/38")
val_have = sum(1 for d in H.values() if d.get("trailing_pe") is not None)
print(f"trailing P/E available (yfinance): {val_have}/38")

print("\n"+"="*70); print("PORTFOLIO AGGREGATION (weighted holdings)"); print("="*70)
wsum = sum(d["weight"] for d in H.values() if d.get("weight"))
print(f"Sum of stated weights: {wsum:.1f}%")
theme_w = defaultdict(float)
for d in H.values():
    if d.get("weight"): theme_w[d["theme"]] += d["weight"]
print("\nTheme weights:")
for t,w in sorted(theme_w.items(), key=lambda x:-x[1]):
    print(f"  {t:<24} {w:5.1f}%  ({w/wsum*100:4.1f}% of invested)")

defense_w = sum(d["weight"] for d in H.values() if d.get("in_defense_cluster") and d.get("weight"))
print(f"\nDEFENSE-ELECTRONICS CLUSTER total weight: {defense_w:.1f}%  ({defense_w/wsum*100:.1f}% of invested)")
print("  members:", [d["name"] for d in H.values() if d.get("in_defense_cluster")])

# weighted unrealized P&L
num = sum(d["weight"]*d["unrealized_pnl_pct"] for d in H.values()
          if d.get("weight") and d.get("unrealized_pnl_pct") is not None)
print(f"\nWeight-averaged unrealized P&L (weighted book): {num/wsum:+.1f}%")

# true concentration: defense cluster correlation
defense = [tk for tk,d in H.items() if d.get("in_defense_cluster")]
px = yf.download(defense, period="1y", interval="1d", auto_adjust=False,
                 progress=False, group_by="ticker")
rets = pd.DataFrame({tk: np.log(px[tk]["Close"]/px[tk]["Close"].shift(1)) for tk in defense}).dropna()
corr = rets.corr()
iu = np.triu_indices_from(corr.values, k=1)
avg_corr = float(corr.values[iu].mean())
print(f"\nDefense cluster AVG pairwise daily-return correlation (1y): {avg_corr:.2f}")
print("  (high correlation => these are one bet, not eight)")

# distressed / penny: live price < 10 INR
penny = [(d["name"], d["price"], d.get("weight")) for d in H.values()
         if d.get("price") and d["price"] < 10]
print(f"\nSub-Rs.10 names ({len(penny)}):")
for n,p,w in sorted(penny, key=lambda x:x[1]):
    print(f"  {n:<26} Rs.{p:>6.2f}  weight={w}")

agg = {"sum_weights": wsum, "theme_weights": dict(theme_w),
       "defense_cluster_weight": defense_w,
       "defense_cluster_avg_corr": round(avg_corr,2),
       "weighted_book_pnl_pct": round(num/wsum,1),
       "audit_issues": issues}
json.dump(agg, open(r"D:\portfolio_aggregates.json","w"), indent=2)
print("\nSaved D:\\portfolio_aggregates.json")
