"""
SSA portfolio -- verified-data layer (UPGRADED).
================================================
Live yfinance fetch for every holding; every number here traces to that fetch.
Adds vs prior run:
  * fundamentals CURRENCY-SANITY check (financialCurrency vs price currency) --
    suspect fields flagged, never printed as a clean ratio;
  * full technical reference levels: Bollinger(20,2), ATR(14) band, swing-pivot
    nearest support/resistance, 50/200 DMA, 52w position;
  * Monte-Carlo 1y GBM bands with the drift = mean(log-ret) (embeds the -0.5*sig^2
    Ito correction); P5/P50/P95;
  * current market value = EXACT qty (file) x LIVE price (qty from workbook);
  * holdings loaded FRESH from the workbook via portfolio_input.

Output -> D:\\portfolio_metrics.json
"""
from __future__ import annotations
import json, warnings
from datetime import datetime, timezone
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, yfinance as yf

from portfolio_input import load_holdings

NIFTY = "^NSEI"


# ---------- technical helpers ----------
def rsi(s: pd.Series, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    v = (100 - 100/(1+rs)).iloc[-1]
    return None if pd.isna(v) else float(v)

def ret(s, days):
    if len(s) <= days: return None
    return float(s.iloc[-1]/s.iloc[-1-days] - 1) * 100

def maxdd(s):
    roll = s.cummax()
    return float(((s/roll) - 1).min()) * 100

def swing_levels(high, low, close, k=5, lookback=180):
    """Nearest swing support (highest pivot-low below price) and resistance
    (lowest pivot-high above price), from fractal pivots over `lookback` days."""
    h, l = high.tail(lookback).reset_index(drop=True), low.tail(lookback).reset_index(drop=True)
    px = float(close.iloc[-1])
    piv_hi, piv_lo = [], []
    for i in range(k, len(h)-k):
        if h[i] == max(h[i-k:i+k+1]): piv_hi.append(float(h[i]))
        if l[i] == min(l[i-k:i+k+1]): piv_lo.append(float(l[i]))
    res = sorted([p for p in piv_hi if p > px*1.005])
    sup = sorted([p for p in piv_lo if p < px*0.995], reverse=True)
    return (sup[0] if sup else None), (res[0] if res else None)


def metrics(tk, hist, nifty_close, info):
    out = {}
    close = hist["Close"].dropna()
    if close.empty:
        return {"error": "no price data"}
    high, low = hist["High"], hist["Low"]
    px = float(close.iloc[-1])
    out["price"] = px
    out["price_date"] = str(pd.Timestamp(close.index[-1]).date())
    out["prev_close"] = float(close.iloc[-2]) if len(close) > 1 else None
    out["day_change_pct"] = round((px/out["prev_close"]-1)*100,2) if out["prev_close"] else None
    out["dma50"] = float(close.tail(50).mean()) if len(close) >= 50 else None
    out["dma200"] = float(close.tail(200).mean()) if len(close) >= 200 else None
    out["pct_vs_dma50"] = round((px/out["dma50"]-1)*100,1) if out["dma50"] else None
    out["pct_vs_dma200"] = round((px/out["dma200"]-1)*100,1) if out["dma200"] else None
    r = rsi(close); out["rsi14"] = round(r,1) if r is not None else None
    win = close.tail(252)
    hi, lo = float(win.max()), float(win.min())
    out["high_52w"], out["low_52w"] = round(hi,2), round(lo,2)
    out["pct_from_52w_high"] = round((px/hi-1)*100,1)
    out["pct_from_52w_low"] = round((px/lo-1)*100,1)
    out["pos_52w_pct"] = round((px-lo)/(hi-lo)*100,1) if hi > lo else None  # 0=low,100=high
    for d,label in [(21,"ret_1m"),(63,"ret_3m"),(126,"ret_6m"),(252,"ret_1y")]:
        rr = ret(close,d); out[label] = round(rr,1) if rr is not None else None
    out["max_drawdown_2y"] = round(maxdd(close),1)

    # Bollinger(20,2) + ATR(14) bands
    if len(close) >= 20:
        ma20 = float(close.tail(20).mean()); sd20 = float(close.tail(20).std())
        out["boll_mid"], out["boll_up"], out["boll_low"] = round(ma20,2), round(ma20+2*sd20,2), round(ma20-2*sd20,2)
        out["boll_pctb"] = round((px-(ma20-2*sd20))/((ma20+2*sd20)-(ma20-2*sd20))*100,1) if sd20 else None
    if len(close) >= 15:
        prev = close.shift(1)
        tr = pd.concat([(high-low), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        out["atr14"] = round(atr,2)
        out["atr_band_low"], out["atr_band_up"] = round(px-2*atr,2), round(px+2*atr,2)
    # swing pivots
    sup, res = swing_levels(high, low, close)
    out["swing_support"], out["swing_resistance"] = (round(sup,2) if sup else None), (round(res,2) if res else None)

    # daily log returns / risk
    lr = np.log(close/close.shift(1)).dropna()
    out["ann_vol_pct"] = round(float(lr.std()*np.sqrt(252))*100,1) if len(lr)>20 else None
    if len(lr) > 60:
        rv = lr.values
        for cl in (95,99):
            q = np.percentile(rv, 100-cl)
            out[f"var_{cl}_1d_pct"] = round(-q*100,2)
            tail = rv[rv <= q]
            out[f"cvar_{cl}_1d_pct"] = round(-tail.mean()*100,2) if len(tail) else None
    if nifty_close is not None and len(nifty_close) > 60:
        nlr = np.log(nifty_close/nifty_close.shift(1)).dropna()
        j = pd.concat([lr, nlr], axis=1, join="inner").dropna()
        if len(j) > 60:
            cov = np.cov(j.iloc[:,0], j.iloc[:,1]); out["beta_vs_nifty"] = round(float(cov[0,1]/cov[1,1]),2)
        n6 = ret(nifty_close.dropna(),126)
        if out["ret_6m"] is not None and n6 is not None:
            out["rel_strength_6m_vs_nifty"] = round(out["ret_6m"] - n6, 1)

    # Monte-Carlo 1y GBM: drift = mean(log-ret) [embeds -0.5 sig^2], sig = std(log-ret)
    if len(lr) > 60:
        mu, sig = float(lr.mean()), float(lr.std())
        out["mc_drift_daily_logret"] = round(mu,5); out["mc_sigma_daily"] = round(sig,5)
        rng = np.random.default_rng(7)
        sims = px*np.exp(np.cumsum(rng.normal(mu,sig,(252,20000)),axis=0))[-1]
        out["mc_p5"] = round(float(np.percentile(sims,5)),2)
        out["mc_p50"] = round(float(np.percentile(sims,50)),2)
        out["mc_p95"] = round(float(np.percentile(sims,95)),2)
        out["mc_prob_below_current_pct"] = round(float((sims<px).mean())*100,1)

    # GARCH(1,1) regime
    out["garch_ann_vol_pct"] = None; out["garch_regime"] = None
    if len(lr) > 250:
        try:
            from arch import arch_model
            res = arch_model(lr.values*100, vol="Garch", p=1, q=1, mean="constant").fit(disp="off")
            cv = float(res.conditional_volatility[-1])*np.sqrt(252)
            a,b,om = res.params.get("alpha[1]",0), res.params.get("beta[1]",0), res.params.get("omega",np.nan)
            uv = float(np.sqrt(om/max(1-a-b,1e-9))*np.sqrt(252)) if (1-a-b)>0 else None
            out["garch_ann_vol_pct"] = round(cv,1)
            if uv and not np.isnan(uv):
                out["garch_regime"] = ("elevated" if cv>uv*1.15 else "calm" if cv<uv*0.85 else "normal")
        except Exception as e:
            out["garch_error"] = str(e)[:80]

    # ---------- fundamentals with CURRENCY SANITY ----------
    price_cur = info.get("currency")
    fin_cur = info.get("financialCurrency")
    mismatch = bool(price_cur and fin_cur and price_cur != fin_cur)
    out["price_currency"] = price_cur
    out["financial_currency"] = fin_cur
    out["fundamentals_currency_mismatch"] = mismatch
    def g(k):
        v = info.get(k)
        return float(v) if isinstance(v,(int,float)) and not pd.isna(v) else None
    raw = {
        "market_cap": g("marketCap"), "trailing_pe": g("trailingPE"),
        "forward_pe": g("forwardPE"), "price_to_book": g("priceToBook"),
        "ev_to_ebitda": g("enterpriseToEbitda"),
        "profit_margin_pct": round(g("profitMargins")*100,1) if g("profitMargins") is not None else None,
        "roe_pct": round(g("returnOnEquity")*100,1) if g("returnOnEquity") is not None else None,
        "debt_to_equity": g("debtToEquity"),
        "dividend_yield_pct": round(g("dividendYield"),2) if g("dividendYield") is not None else None,
    }
    # plausibility guard: implausible ratios (currency-corrupted) are flagged suspect
    suspect = {}
    def flag(name, val, lo, hi):
        if val is None: return None
        bad = mismatch or not (lo <= val <= hi)
        suspect[name] = bad
        return val
    raw["trailing_pe"] = flag("trailing_pe", raw["trailing_pe"], 0, 400)
    raw["forward_pe"]  = flag("forward_pe",  raw["forward_pe"], 0, 400)
    raw["price_to_book"] = flag("price_to_book", raw["price_to_book"], 0, 60)
    raw["ev_to_ebitda"] = flag("ev_to_ebitda", raw["ev_to_ebitda"], -50, 200)
    raw["roe_pct"] = flag("roe_pct", raw["roe_pct"], -200, 200)
    raw["debt_to_equity"] = flag("debt_to_equity", raw["debt_to_equity"], 0, 1000)
    out["fundamentals"] = raw
    out["fundamentals_suspect"] = suspect
    return out


def main():
    holdings = load_holdings()
    syms = [h["ticker"] for h in holdings]
    print(f"Fetching {len(syms)} holdings + Nifty (2y daily)...")
    data = yf.download(syms + [NIFTY], period="2y", interval="1d",
                       auto_adjust=False, progress=False, group_by="ticker")
    nifty_close = data[NIFTY]["Close"] if NIFTY in data.columns.get_level_values(0) else None

    results = {}
    for h in holdings:
        tk = h["ticker"]
        try:
            hist = data[tk].dropna(how="all")
        except Exception:
            hist = None
        info = {}
        try:
            info = yf.Ticker(tk).get_info() or {}
        except Exception:
            info = {}
        m = metrics(tk, hist, nifty_close, info) if hist is not None and not hist.empty else {"error":"no data"}
        px = m.get("price"); cost = h["avg_cost"]; qty = h["qty"]
        m["avg_cost"] = cost
        m["unrealized_pnl_pct"] = round((px/cost - 1)*100,1) if (px and cost) else None
        m["qty"] = qty
        m["file_valuation"] = h["file_valuation"]
        m["mkt_value_live"] = round(px*qty,2) if (px and qty) else None  # EXACT qty x LIVE px
        m["file_vs_live_div_pct"] = (round((m["mkt_value_live"]/h["file_valuation"]-1)*100,1)
                                     if (m["mkt_value_live"] and h["file_valuation"]) else None)
        results[tk] = {"name": h["name"], "weight": h["weight"], "theme": h["theme"],
                       "in_defence_cluster": h["in_defence_cluster"], "mtf": h["mtf"], **m}
        print(f"  {h['name']:<28} {tk:<13} px={px} P&L={m.get('unrealized_pnl_pct')}% "
              f"PE={m.get('fundamentals',{}).get('trailing_pe')} curMis={m.get('fundamentals_currency_mismatch')}")

    nctx = {}
    if nifty_close is not None:
        nc = nifty_close.dropna()
        nctx = {"nifty_price": round(float(nc.iloc[-1]),2),
                "nifty_date": str(pd.Timestamp(nc.index[-1]).date()),
                "nifty_ret_6m": round(ret(nc,126),1) if ret(nc,126) else None,
                "nifty_ret_1y": round(ret(nc,252),1) if ret(nc,252) else None}

    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "data_source": "yfinance live (NSE .NS), EOD/delayed; qty from workbook (exact)",
               "nifty_context": nctx, "holdings": results}
    json.dump(payload, open(r"D:\portfolio_metrics.json","w",encoding="utf-8"), indent=2, default=str)
    print("\nSaved D:\\portfolio_metrics.json")


if __name__ == "__main__":
    import sys; sys.stdout.reconfigure(encoding="utf-8")
    main()
