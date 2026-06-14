"""Single source of truth for per-holding VERDICTS and technical ACTION LEVELS.
Both report.py and liquidation3.py import this, so a stock can never say one
thing on its page and another in a plan.

Verdicts bake in the owner's stated Part-D intentions (analysed for realism, not
rubber-stamped). Action levels are STATISTICAL/TECHNICAL reference levels derived
only from price data we have -- explicitly NOT fundamental price targets.
"""
from __future__ import annotations

# verdict string + conviction grade (A/B/C = business quality + conviction)
VERDICT = {
 # ---- MTF (leveraged) -- owner Part-D decisions baked in ----
 "PFC.NS":        ("DE-LEVERAGE → SELL ½, CONVERT ½", "B"),
 "ACC.NS":        ("DE-LEVERAGE → SELL ½, CONVERT ½", "B"),
 "BALAMINES.NS":  ("DE-LEVERAGE → CONVERT TO CASH (KEEP)", "B"),
 "PPLPHARMA.NS":  ("DE-LEVERAGE → EXIT", "C"),
 "MAHSEAMLES.NS": ("DE-LEVERAGE → EXIT", "B"),
 "TRANSRAILL.NS": ("DE-LEVERAGE → EXIT", "B"),
 "JWL.NS":        ("DE-LEVERAGE → EXIT", "C"),
 "EASEMYTRIP.NS": ("DE-LEVERAGE → EXIT", "C"),
 # ---- cash: defence cluster ----
 "BDL.NS":        ("TRIM", "B"),
 "BEL.NS":        ("RETAIN / TRIM", "A"),
 "HAL.NS":        ("RETAIN", "A"),
 "HBLENGINE.NS":  ("TRIM", "B"),
 "AVANTEL.NS":    ("TRIM / EXIT", "C"),
 "APOLLO.NS":     ("TRIM / EXIT", "C"),
 "ASTRAMICRO.NS": ("TRIM", "C"),
 "AVALON.NS":     ("TRIM", "C"),
 # ---- cash: other ----
 "TATAPOWER.NS":  ("RETAIN", "B"),
 "IREDA.NS":      ("RETAIN / TRIM", "B"),
 "OLECTRA.NS":    ("RETAIN / TRIM", "C"),
 "RTNPOWER.NS":   ("EXIT", "C"),
 "HAPPSTMNDS.NS": ("RETAIN (hold, don't book at lows)", "C"),
 "GPPL.NS":       ("RETAIN", "B"),
 "TATATECH.NS":   ("EXIT (book loss)", "C"),
 "GMMPFAUDLR.NS": ("TRIM / HOLD SMALL", "C"),
 "GTLINFRA.NS":   ("EXIT", "C"),
 "AKSHAR.NS":     ("EXIT", "C"),
 "HFCL.NS":       ("TRIM", "C"),
 "AJOONI.NS":     ("EXIT", "C"),
 "IRCON.NS":      ("RETAIN / TRIM", "B"),
 "FCONSUMER.NS":  ("EXIT", "C"),
 # ---- unweighted tail ----
 "TDPOWERSYS.NS": ("BOOK PROFIT / decide", "B"),
 "RCOM.NS":       ("EXIT", "C"),
 "KSHITIJPOL.NS": ("EXIT", "C"),
 "HDIL.NS":       ("EXIT", "C"),
 "VIKASLIFE.NS":  ("EXIT", "C"),
 "VIKASECO.NS":   ("EXIT", "C"),
 "FEL.NS":        ("EXIT", "C"),
 "RVNL.NS":       ("TRIM / decide", "B"),
}

# the owner's Part-D stated intentions (verbatim) + the data-realism verdict.
# Used to render the "your stated decision" block honestly.
PART_D = {
 "PFC.NS":        "Sell HALF, convert the other half to cash.",
 "ACC.NS":        "Sell HALF, convert the other half to cash.",
 "BALAMINES.NS":  "Convert fully to cash (de-leverage, keep).",
 "PPLPHARMA.NS":  "EXIT, targeting a sell price near avg cost (₹211).",
 "MAHSEAMLES.NS": "EXIT, sell nearest to avg cost (₹890).",
 "RTNPOWER.NS":   "EXIT at best achievable price.",
 "HAPPSTMNDS.NS": "Reconsider for loss-booking & exit.",
 "AVALON.NS":     "Reconsider for loss-booking & exit.",
 "TATATECH.NS":   "Reconsider for loss-booking & exit.",
 "AKSHAR.NS":     "Reconsider for loss-booking & exit.",
 "KSHITIJPOL.NS": "Reconsider for loss-booking & exit.",
}

SELL_WORDS = ("EXIT", "TRIM", "SELL", "BOOK PROFIT", "BOOK LOSS", "AVOID")
HOLD_WORDS = ("RETAIN", "CONVERT TO CASH", "HOLD")


def action_side(verdict: str) -> str:
    """SELL (reduce/exit) vs HOLD (keep) -- drives which action zone we show."""
    u = verdict.upper()
    # de-leverage that keeps the asset is a HOLD-side for level purposes
    if "CONVERT TO CASH" in u or u.startswith("RETAIN") or u.startswith("HAL") or "HOLD" in u:
        if "EXIT" not in u and "TRIM" not in u:
            return "HOLD"
    if any(w in u for w in ("EXIT", "SELL", "BOOK PROFIT", "BOOK LOSS", "AVOID")):
        return "SELL"
    if "TRIM" in u:
        return "TRIM"
    if "RETAIN" in u or "CONVERT TO CASH" in u:
        return "HOLD"
    return "HOLD"


def _below(price, *cands):
    vals = [c for c in cands if isinstance(c, (int, float)) and c < price * 0.999]
    return max(vals) if vals else None

def _above(price, *cands):
    vals = [c for c in cands if isinstance(c, (int, float)) and c > price * 1.001]
    return min(vals) if vals else None


def compute_levels(d: dict, verdict: str, avg_cost: float | None) -> dict:
    """Build the bull/normal/bear reference band + verdict-tied action levels,
    purely from price-derived stats already in the metrics dict."""
    px = d.get("price")
    if not px:
        return {}
    lo52, hi52 = d.get("low_52w"), d.get("high_52w")
    out = {
        "price": px, "low_52w": lo52, "high_52w": hi52, "pos_52w_pct": d.get("pos_52w_pct"),
        "dma50": d.get("dma50"), "dma200": d.get("dma200"),
        "swing_support": d.get("swing_support"), "swing_resistance": d.get("swing_resistance"),
        "boll_low": d.get("boll_low"), "boll_up": d.get("boll_up"),
        "atr_band_low": d.get("atr_band_low"), "atr_band_up": d.get("atr_band_up"),
        # reference band (1y) -- Monte-Carlo percentiles primary
        "bear": d.get("mc_p5"), "normal": d.get("mc_p50"), "bull": d.get("mc_p95"),
    }
    # nearest technical support / resistance to the current price
    support = _below(px, d.get("swing_support"), d.get("boll_low"), d.get("atr_band_low"),
                     d.get("dma50"), d.get("dma200"), lo52)
    resistance = _above(px, d.get("swing_resistance"), d.get("boll_up"), d.get("atr_band_up"), hi52)
    out["support"], out["resistance"] = support, resistance

    # ATR proxy (half the 2-ATR band width) for sizing usable zones
    atr = None
    if d.get("atr_band_up") and px:
        atr = (d["atr_band_up"] - px) / 2.0

    side = action_side(verdict)
    out["side"] = side
    if side in ("SELL", "TRIM"):
        # sell into strength: from current up to a usable resistance band.
        # take the nearest swing resistance but ensure the zone is at least ~1 ATR
        # wide (so degenerate near-touching pivots don't collapse the zone),
        # capped at the MC p95 bull case.
        atr_top = px + 1.0 * atr if atr else px * 1.05
        top = max(resistance or 0, atr_top)
        if d.get("mc_p95"):
            top = min(top, d["mc_p95"])
        out["sell_zone_low"], out["sell_zone_high"] = round(px, 2), round(max(top, px*1.02), 2)
        # hard floor: below this, stop hoping and cut regardless
        out["cut_floor"] = support or d.get("atr_band_low") or round(px*0.90, 2)
        # realism vs avg cost (for the exit-near-cost intentions)
        if avg_cost and avg_cost > px:
            need = (avg_cost/px - 1) * 100
            out["avg_cost"] = avg_cost
            out["pct_to_avg_cost"] = round(need, 1)
            reach = top  # best near-term technical reach
            out["avg_cost_reachable"] = bool(reach and avg_cost <= reach * 1.02)
    else:  # HOLD / RETAIN / CONVERT-KEEP
        base = support or d.get("dma200") or d.get("boll_low") or round(px*0.92, 2)
        out["accum_zone_low"], out["accum_zone_high"] = round(min(base, px*0.99), 2), round(px, 2)
        # invalidation: a clean break below structural support / 52w low
        out["invalidation"] = round(min(x for x in [support, d.get("boll_low"), lo52,
                                                    d.get("atr_band_low")] if x), 2) \
            if any([support, d.get("boll_low"), lo52, d.get("atr_band_low")]) else round(px*0.85, 2)
    return out


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    M = json.load(open(r"D:\portfolio_metrics.json", encoding="utf-8"))["holdings"]
    for tk in ("PPLPHARMA.NS", "MAHSEAMLES.NS", "PFC.NS", "HAPPSTMNDS.NS", "BDL.NS"):
        d = M[tk]; v = VERDICT[tk][0]
        L = compute_levels(d, v, d.get("avg_cost"))
        print(f"\n{d['name']} | {v} | side={L['side']} px=₹{L['price']}")
        print(f"   band bear/normal/bull: ₹{L['bear']} / ₹{L['normal']} / ₹{L['bull']}")
        print(f"   support {L['support']} resistance {L['resistance']}")
        if L['side'] in ('SELL','TRIM'):
            print(f"   sell zone ₹{L['sell_zone_low']}-{L['sell_zone_high']} cut floor ₹{L['cut_floor']} "
                  f"to-avg-cost {L.get('pct_to_avg_cost')}% reachable={L.get('avg_cost_reachable')}")
        else:
            print(f"   accumulate ₹{L['accum_zone_low']}-{L['accum_zone_high']} invalidation ₹{L['invalidation']}")
