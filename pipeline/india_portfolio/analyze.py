"""
Indian portfolio analysis -- verified-data layer.
================================================
Fetches LIVE data via yfinance for every holding and computes the metrics our
pipeline runs, adapted to Indian (.NS) equities. Every number written here is
sourced from a live fetch; nothing comes from model memory. Output -> JSON.

Sources connected today:  yfinance (price, history, basic fundamentals).
NOT connected (flagged in the report): NSE/BSE filings, SEBI/SAST insider,
F&O ban / SLB short data, Screener/Trendlyne fundamentals, RBI/MOSPI macro,
concall transcripts.
"""
from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import yfinance as yf

# ── Holdings: (ticker, name, weight%, avg_cost INR, theme, weighted) ──────────
H = [
    ("BDL.NS", "Bharat Dynamics", 14.8, 1034, "Defense", True),
    ("BEL.NS", "Bharat Electronics", 14.2, 179, "Defense", True),
    ("HAL.NS", "Hindustan Aeronautics", 12.7, 2187, "Defense", True),
    ("HBLENGINE.NS", "HBL Engineering", 9.4, 361, "Defense", True),
    ("TATAPOWER.NS", "Tata Power", 8.7, 281, "Power/Utilities", True),
    ("AVANTEL.NS", "Avantel", 6.5, 123, "Defense", True),
    ("PFC.NS", "Power Finance Corp", 4.8, 514, "Financials/PSU", True),
    ("TRANSRAILL.NS", "Transrail Lighting", 4.7, 748, "Capex/Infra", True),
    ("JWL.NS", "Jupiter Wagons", 4.3, 403, "Capex/Infra", True),
    ("EASEMYTRIP.NS", "Easy Trip Planners", 3.8, 16, "Travel/Consumer", True),
    ("ACC.NS", "ACC", 2.8, 1948, "Cement/Materials", True),
    ("IREDA.NS", "IREDA", 2.1, 64, "Financials/PSU", True),
    ("APOLLO.NS", "Apollo Micro Systems", 1.7, 129, "Defense", True),
    ("BALAMINES.NS", "Balaji Amines", 1.3, 2129, "Chemicals", True),
    ("PPLPHARMA.NS", "Piramal Pharma", 1.3, 211, "Pharma", True),
    ("MAHSEAMLES.NS", "Maharashtra Seamless", 1.3, 890, "Capex/Infra", True),
    ("OLECTRA.NS", "Olectra Greentech", 1.0, 664, "EV/Capital Goods", True),
    ("RTNPOWER.NS", "RattanIndia Power", 0.8, 6, "Power/Distressed", True),
    ("ASTRAMICRO.NS", "Astra Microwave", 0.7, 445.56, "Defense", True),
    ("HAPPSTMNDS.NS", "Happiest Minds", 0.7, 986, "IT", True),
    ("GPPL.NS", "Gujarat Pipavav Port", 0.6, 190, "Capex/Infra", True),
    ("TATATECH.NS", "Tata Technologies", 0.5, 1177, "IT", True),
    ("AVALON.NS", "Avalon Technologies", 0.4, 536, "Defense", True),
    ("GMMPFAUDLR.NS", "GMM Pfaudler [MTF]", 0.4, 1298, "Chemicals/CapGoods", True),
    ("GTLINFRA.NS", "GTL Infrastructure", 0.1, 1, "Telecom/Distressed", True),
    ("AKSHAR.NS", "Akshar Spintex", 0.1, 7, "Textiles/Distressed", True),
    ("HFCL.NS", "HFCL", 0.1, 116, "Telecom", True),
    ("AJOONI.NS", "Ajooni Biotech", 0.1, 9, "Distressed", True),
    ("IRCON.NS", "Ircon International", 0.1, 244, "Capex/Infra", True),
    ("FCONSUMER.NS", "Future Consumer", 0.1, 1.5, "Distressed", True),
    ("TDPOWERSYS.NS", "TD Power Systems", None, 518, "Capex/Infra", False),
    ("RCOM.NS", "Reliance Communications", None, 2.11, "Telecom/Distressed", False),
    ("KSHITIJPOL.NS", "Kshitij Polyline", None, 55, "Distressed", False),
    ("HDIL.NS", "Housing Dev & Infra", None, 5, "RealEstate/Distressed", False),
    ("VIKASLIFE.NS", "Vikas Lifecare", None, 4.5, "Distressed", False),
    ("VIKASECO.NS", "Vikas Ecotech", None, 4.3, "Distressed", False),
    ("FEL.NS", "Future Enterprises", None, 1.7, "Distressed", False),
    ("RVNL.NS", "Rail Vikas Nigam", None, 391, "Capex/Infra", False),
]
DEFENSE_CLUSTER = {"BDL.NS","BEL.NS","HAL.NS","HBLENGINE.NS","AVANTEL.NS",
                   "APOLLO.NS","ASTRAMICRO.NS","AVALON.NS"}
NIFTY = "^NSEI"

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

def metrics(tk, close, nifty_close, info):
    out = {}
    close = close.dropna()
    if close.empty:
        return {"error": "no price data"}
    px = float(close.iloc[-1])
    out["price"] = px
    out["prev_close"] = float(close.iloc[-2]) if len(close) > 1 else None
    out["day_change_pct"] = (round((px/out["prev_close"]-1)*100,2)
                             if out["prev_close"] else None)
    out["dma50"] = float(close.tail(50).mean()) if len(close) >= 50 else None
    out["dma200"] = float(close.tail(200).mean()) if len(close) >= 200 else None
    out["pct_vs_dma50"] = round((px/out["dma50"]-1)*100,1) if out["dma50"] else None
    out["pct_vs_dma200"] = round((px/out["dma200"]-1)*100,1) if out["dma200"] else None
    out["rsi14"] = round(rsi(close),1) if rsi(close) is not None else None
    win = close.tail(252)
    hi, lo = float(win.max()), float(win.min())
    out["high_52w"], out["low_52w"] = hi, lo
    out["pct_from_52w_high"] = round((px/hi-1)*100,1)
    out["pct_from_52w_low"] = round((px/lo-1)*100,1)
    for d,label in [(21,"ret_1m"),(63,"ret_3m"),(126,"ret_6m"),(252,"ret_1y")]:
        out[label] = round(ret(close,d),1) if ret(close,d) is not None else None
    out["max_drawdown_2y"] = round(maxdd(close),1)
    # daily log returns
    lr = np.log(close/close.shift(1)).dropna()
    out["ann_vol_pct"] = round(float(lr.std()*np.sqrt(252))*100,1) if len(lr)>20 else None
    # VaR / CVaR (1-day, historical)
    if len(lr) > 60:
        r = lr.values
        for cl in (95,99):
            q = np.percentile(r, 100-cl)
            out[f"var_{cl}_1d_pct"] = round(-q*100,2)
            tail = r[r <= q]
            out[f"cvar_{cl}_1d_pct"] = round(-tail.mean()*100,2) if len(tail) else None
    # beta + relative strength vs Nifty
    if nifty_close is not None and len(nifty_close) > 60:
        nlr = np.log(nifty_close/nifty_close.shift(1)).dropna()
        j = pd.concat([lr, nlr], axis=1, join="inner").dropna()
        if len(j) > 60:
            cov = np.cov(j.iloc[:,0], j.iloc[:,1])
            out["beta_vs_nifty"] = round(float(cov[0,1]/cov[1,1]),2)
        n6 = ret(nifty_close.dropna(),126)
        if out["ret_6m"] is not None and n6 is not None:
            out["rel_strength_6m_vs_nifty"] = round(out["ret_6m"] - n6, 1)
    # Monte Carlo (GBM, 1y, 10k paths)
    if len(lr) > 60:
        mu, sig = float(lr.mean()), float(lr.std())
        rng = np.random.default_rng(7)
        sims = px*np.exp(np.cumsum(rng.normal(mu,sig,(252,10000)),axis=0))[-1]
        out["mc_p5"] = round(float(np.percentile(sims,5)),2)
        out["mc_p50"] = round(float(np.percentile(sims,50)),2)
        out["mc_p95"] = round(float(np.percentile(sims,95)),2)
        out["mc_prob_below_current_pct"] = round(float((sims<px).mean())*100,1)
    # GARCH(1,1) conditional vol regime
    out["garch_ann_vol_pct"] = None; out["garch_regime"] = None
    if len(lr) > 250:
        try:
            from arch import arch_model
            am = arch_model(lr.values*100, vol="Garch", p=1, q=1, mean="constant")
            res = am.fit(disp="off")
            cv = float(res.conditional_volatility[-1])*np.sqrt(252)
            uv = float(np.sqrt(res.params.get("omega",np.nan) /
                       (1-res.params.get("alpha[1]",0)-res.params.get("beta[1]",0)))*np.sqrt(252)) if True else None
            out["garch_ann_vol_pct"] = round(cv,1)
            if uv and not np.isnan(uv):
                out["garch_regime"] = ("elevated" if cv > uv*1.15 else
                                       "calm" if cv < uv*0.85 else "normal")
        except Exception as e:
            out["garch_error"] = str(e)[:80]
    # Valuation (yfinance .info, may be partial)
    def g(k):
        v = info.get(k)
        return float(v) if isinstance(v,(int,float)) and not pd.isna(v) else None
    out["market_cap"] = g("marketCap")
    out["trailing_pe"] = g("trailingPE")
    out["forward_pe"] = g("forwardPE")
    out["price_to_book"] = g("priceToBook")
    out["ev_to_ebitda"] = g("enterpriseToEbitda")
    out["profit_margin_pct"] = round(g("profitMargins")*100,1) if g("profitMargins") is not None else None
    out["roe_pct"] = round(g("returnOnEquity")*100,1) if g("returnOnEquity") is not None else None
    out["debt_to_equity"] = g("debtToEquity")
    out["dividend_yield_pct"] = round(g("dividendYield"),2) if g("dividendYield") is not None else None
    return out

def main():
    syms = [h[0] for h in H]
    print(f"Fetching {len(syms)} holdings + Nifty 50 ...")
    data = yf.download(syms + [NIFTY], period="2y", interval="1d",
                       auto_adjust=False, progress=False, group_by="ticker")
    nifty_close = data[NIFTY]["Close"] if NIFTY in data.columns.get_level_values(0) else None

    results = {}
    for tk, name, wt, cost, theme, weighted in H:
        try:
            close = data[tk]["Close"]
        except Exception:
            close = None
        info = {}
        try:
            info = yf.Ticker(tk).get_info() or {}
        except Exception:
            info = {}
        m = metrics(tk, close, nifty_close, info) if close is not None else {"error":"no data"}
        # core P&L vs avg cost
        px = m.get("price")
        m["avg_cost"] = cost
        m["unrealized_pnl_pct"] = round((px/cost - 1)*100,1) if px else None
        results[tk] = {"name": name, "weight": wt, "theme": theme,
                       "weighted": weighted, "in_defense_cluster": tk in DEFENSE_CLUSTER,
                       **m}
        pnl = m.get("unrealized_pnl_pct")
        print(f"  {name:<26} {tk:<14} px={px}  P&L={pnl}%  PE={m.get('trailing_pe')}")

    # nifty 6m context
    nctx = {}
    if nifty_close is not None:
        nc = nifty_close.dropna()
        nctx = {"nifty_price": float(nc.iloc[-1]),
                "nifty_ret_6m": round(ret(nc,126),1) if ret(nc,126) else None,
                "nifty_ret_1y": round(ret(nc,252),1) if ret(nc,252) else None}

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_source": "yfinance live (NSE .NS); prices are EOD/delayed, not real-time tick",
        "nifty_context": nctx,
        "holdings": results,
    }
    with open(r"D:\portfolio_metrics.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print("\nSaved D:\\portfolio_metrics.json")

if __name__ == "__main__":
    main()
