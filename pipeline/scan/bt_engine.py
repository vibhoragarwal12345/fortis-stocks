"""
SHADOW falsification backtest ENGINE (price-based strategies).
===============================================================
Walk-forward, survivorship-honest, no-lookahead. For each rebalance date T it
builds the LIVE point-in-time liquid universe (delistings included), ranks it
under each candidate strategy using ONLY data <= T, takes the top-K picks, and
forward-tracks split-adjusted returns vs SPY at 1/5/20/60 trading days. Results
break down BY REGIME (from bt_regimes.csv). Nothing here touches the live board.

PIT / leakage discipline:
  - Signals & forward returns use a SPLIT-ADJUSTED close. Splits AFTER T scale a
    trailing window uniformly, so they CANCEL in the ratio-based signals/returns
    used here -> future-split-safe. (Dividends excluded: price-return, minor for
    <=60d momentum -- flagged.)
  - The LIQUIDITY filter uses RAW close x RAW volume = actual PIT dollar volume.
  - Forward return at horizon h only counts if T+h has matured in the data.

NOT in this engine: Value/Quality/Factor-size need point-in-time fundamentals /
market cap we do not have -> reported NOT TESTABLE elsewhere. ML is separate.

    python pipeline/scan/bt_engine.py --sample 150     # bounded validation run
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scan import bt_prices            # noqa: E402
from scan import extension as ext     # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
OOS_START = "2025-01-02"
HORIZONS = (1, 5, 20, 60)
TOP_K = 15
REBAL_STEP = 5            # rebalance every 5 trading days (~weekly)
ADV_MIN = 1_000_000      # $1M+ average daily dollar volume, like the live scan


# ── data loading ──────────────────────────────────────────────────────────────

def load_universe() -> dict[str, dict]:
    out = {}
    for r in csv.DictReader((DATA / "bt_universe.csv").open(encoding="utf-8")):
        out[r["ticker"]] = {"start": r["start"], "end": r["end"], "delisted": r["delisted"] == "1"}
    return out


def load_regimes() -> dict[str, str]:
    p = DATA / "bt_regimes.csv"
    if not p.exists():
        return {}
    return {r["date"]: r["regime_name"] for r in csv.DictReader(p.open(encoding="utf-8"))}


def _split_adjusted(df: pd.DataFrame) -> pd.Series:
    """Split-adjusted close from raw close + per-day split factor (causal-safe
    for ratio signals: post-window splits scale the whole window uniformly)."""
    raw = df["close"].astype(float)
    spl = df["split"].astype(float).fillna(1.0).replace(0, 1.0)
    cum = 1.0
    factors = np.ones(len(raw))
    for i in range(len(raw) - 1, -1, -1):
        factors[i] = cum
        if spl.iloc[i] not in (1.0, 0.0):
            cum *= spl.iloc[i]
    return raw / factors


def build_panel(survivors: list[str], deaths: list[str]) -> dict[str, pd.DataFrame]:
    """Survivors via yfinance (allow_tiingo=False); deaths CACHE-ONLY so this
    never competes with the throttled background Tiingo fetch on the shared key."""
    panel = {}

    def _add(t, df):
        if df is None or len(df) < 60:
            return
        df = df.sort_index()
        out = pd.DataFrame(index=df.index)
        out["adj"] = _split_adjusted(df)
        out["rawclose"] = df["close"].astype(float)
        out["vol"] = pd.to_numeric(df["volume"], errors="coerce")
        out["high"] = df["high"].astype(float)
        out["low"] = df["low"].astype(float)
        out["open"] = df["open"].astype(float)
        out["split"] = df["split"].astype(float)
        panel[t] = out

    for t in survivors:
        _add(t, bt_prices._from_cache(t))               # cache-only (prefetched)
    for t in deaths:
        _add(t, bt_prices._from_cache(t))               # cache only; never fetch
    return panel


# ── indicators on the adjusted close, evaluated AS OF position p ────────────────

def _rsi(a: np.ndarray, n: int = 14) -> float | None:
    if len(a) < n + 1:
        return None
    d = np.diff(a)
    up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    ru = pd.Series(up).ewm(alpha=1/n, adjust=False).mean().iloc[-1]
    rd = pd.Series(dn).ewm(alpha=1/n, adjust=False).mean().iloc[-1]
    if rd == 0:
        return 100.0
    return float(100 - 100 / (1 + ru / rd))


def _features(adj: np.ndarray) -> dict | None:
    """Ratio-based features as of the last point of adj (length L)."""
    L = len(adj)
    if L < 60:
        return None
    px = adj[-1]
    sma20 = adj[-20:].mean(); sma50 = adj[-50:].mean()
    ret5 = px / adj[-6] - 1 if L >= 6 else None
    ret20 = px / adj[-21] - 1 if L >= 21 else None
    mom_12_1 = (adj[-21] / adj[-252] - 1) if L >= 252 else None   # 12-1 momentum
    prior20high = adj[-21:-1].max()
    breakout = px > prior20high
    rng = adj[-252:] if L >= 252 else adj
    pos52 = (px - rng.min()) / (rng.max() - rng.min() + 1e-9)
    vol60 = float(np.std(np.diff(adj[-61:]) / adj[-61:-1])) if L >= 61 else None
    rsi = _rsi(adj[-60:])
    return dict(px=px, sma20=sma20, sma50=sma50, ret5=ret5, ret20=ret20,
                mom_12_1=mom_12_1, breakout=breakout, pos52=pos52, vol60=vol60, rsi=rsi)


# ── strategies: score a candidate as of T (higher = more preferred) ─────────────

def _baseline_mom(f, df_slice) -> float | None:
    if f["ret20"] is None:
        return None
    s = 50 + 35 * np.tanh(f["ret20"] / 0.25)          # momentum (saturating)
    s += 20 if f["breakout"] else 0                    # breakout
    s += 30 * f["pos52"]                               # 52w proximity
    if f["rsi"] is not None:
        s += (f["rsi"] - 30) * 0.3 if f["rsi"] <= 70 else (100 - f["rsi"]) * 0.3
    return float(s)


def _meanrev(f, df_slice) -> float | None:
    # pullback-in-uptrend: in a 50d uptrend but oversold short-term
    if f["sma50"] is None or f["ret5"] is None:
        return None
    if f["px"] < f["sma50"]:
        return None                                    # not an uptrend -> skip
    score = -f["ret5"] * 100                            # more oversold = better
    if f["rsi"] is not None:
        score += max(0, 40 - f["rsi"])                 # reward low RSI
    return float(score)


def _ext_penalty(f, df_slice) -> float | None:
    base = _baseline_mom(f, df_slice)
    if base is None:
        return None
    ohlc = df_slice.rename(columns={"rawclose": "close"})[["open", "high", "low", "close"]]
    e = ext.compute_extension(ohlc, asof_index=-1)
    pen = e.get("extension_score")
    return float(base - (pen if pen is not None else 0))   # subtract-only penalty


def _mom_factor(f, df_slice) -> float | None:
    return None if f["mom_12_1"] is None else float(f["mom_12_1"])


def _lowvol(f, df_slice) -> float | None:
    return None if f["vol60"] is None else float(-f["vol60"])   # lowest vol = best


STRATEGIES = {
    "BASELINE_MOM": _baseline_mom,
    "MEAN_REVERSION": _meanrev,
    "EXTENSION_PENALTY": _ext_penalty,
    "MOMENTUM_FACTOR": _mom_factor,
    "LOWVOL_FACTOR": _lowvol,
}


# ── the walk-forward loop ───────────────────────────────────────────────────────

def run(sample: int) -> int:
    uni = load_universe()
    regimes = load_regimes()
    rng = random.Random(42)

    survivors = [t for t, m in uni.items()
                 if not m["delisted"] and m["start"] <= OOS_START]
    deaths = [t for t, m in uni.items() if m["delisted"] and m["end"] >= OOS_START]
    cached_surv = [t for t in survivors if (bt_prices.CACHE / f"{t}.csv").exists()]
    sample_survivors = cached_surv[:sample] if sample else cached_surv
    print(f"universe: {len(sample_survivors)} cached survivors + {len(deaths)} in-window "
          f"deaths (cache-only) ...", flush=True)

    panel = build_panel(sample_survivors, deaths)
    # SPY benchmark (split-adjusted close)
    spy_raw = bt_prices.get("SPY", allow_tiingo=False)
    spy = _split_adjusted(spy_raw) if spy_raw is not None else None
    if spy is None:
        print("no SPY -- aborting"); return 1
    print(f"loaded {len(panel)} tickers with usable history; SPY {len(spy)} bars", flush=True)

    # master trading calendar = SPY index within OOS
    cal = [d for d in spy.index if str(d.date()) >= OOS_START]
    rebal = cal[::REBAL_STEP]

    # results[strategy][horizon] -> list of returns ; alpha ; regime tagging
    res = {s: defaultdict(list) for s in STRATEGIES}
    res_reg = {s: defaultdict(lambda: defaultdict(list)) for s in STRATEGIES}  # [reg][h]->rets
    npicks = defaultdict(int)
    res_ext = defaultdict(list)   # [strategy] -> (ret20, ticker, date) for outlier diagnosis

    def adj_at(s: pd.Series, d) -> float | None:
        try:
            return float(s.loc[d])
        except KeyError:
            return None

    def fwd(series: pd.Series, t_pos: int, h: int) -> float | None:
        if t_pos + h >= len(series):
            return None
        return float(series.iloc[t_pos + h] / series.iloc[t_pos] - 1)

    spy_pos = {d: i for i, d in enumerate(spy.index)}

    for T in rebal:
        reg = regimes.get(str(T.date()), "?")
        # candidate liquid PIT universe at T
        cands = []
        for t, df in panel.items():
            m = uni.get(t, {})
            if not (m.get("start", "9999") <= str(T.date())):
                continue
            if m.get("end") and m["end"] < str(T.date()):
                continue                                   # already delisted by T
            if T not in df.index:
                continue
            p = df.index.get_loc(T)
            if isinstance(p, slice) or p < 60:
                continue
            sub = df.iloc[: p + 1]
            adv = float((sub["rawclose"] * sub["vol"]).iloc[-20:].mean())
            if not (adv >= ADV_MIN):
                continue
            f = _features(sub["adj"].to_numpy())
            if f is None:
                continue
            cands.append((t, p, sub, f))
        if len(cands) < TOP_K:
            continue

        if T not in spy_pos:
            continue
        sp = spy_pos[T]
        spy_fwd = {h: fwd(spy, sp, h) for h in HORIZONS}

        for sname, fn in STRATEGIES.items():
            scored = []
            for t, p, sub, f in cands:
                sc = fn(f, sub)
                if sc is not None:
                    scored.append((sc, t, p))
            scored.sort(reverse=True)
            for _, t, p in scored[:TOP_K]:
                npicks[sname] += 1
                series = panel[t]["adj"]
                for h in HORIZONS:
                    r = fwd(series, p, h)
                    if r is None:
                        continue
                    a = (r - spy_fwd[h]) if spy_fwd[h] is not None else None
                    res[sname][f"ret{h}"].append(r * 100)
                    if a is not None:
                        res[sname][f"alpha{h}"].append(a * 100)
                    if h == 20:
                        res_reg[sname][reg]["ret20"].append(r * 100)
                        res_ext[sname].append((r * 100, t, str(T.date())))

    _report(res, res_reg, npicks, res_ext)
    return 0


def _m(xs):
    xs = [x for x in xs if x is not None]
    return round(st.fmean(xs), 2) if xs else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def _win(xs):
    xs = [x for x in xs if x is not None]
    return round(100 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None


def _t(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    sd = st.pstdev(xs)
    return round(st.fmean(xs) / (sd / len(xs) ** 0.5), 2) if sd else None


def _wins(xs, cap=50.0):
    xs = [max(-cap, min(cap, x)) for x in xs if x is not None]
    return round(st.fmean(xs), 2) if xs else None


def _pctl(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def _report(res, res_reg, npicks, res_ext):
    print("\n" + "=" * 100)
    print("FALSIFICATION TABLE  (survivorship-complete: 344/404 in-window deaths included; "
          "60 unrecoverable, mostly illiquid)")
    print("MEDIAN is the honest centre; raw MEAN is outlier-driven (mean>>median == a few "
          "extreme picks -- see EXTREMES).")
    print("=" * 100)
    print(f"{'STRATEGY':<19}{'picks':>6}{'med20':>8}{'win20':>7}{'wmean20':>9}{'rawmean':>9}"
          f"{'p99':>8}{'walpha20':>10}{'n20':>6}")
    print("-" * 100)
    for s in STRATEGIES:
        r20 = res[s]['ret20']
        print(f"{s:<19}{npicks[s]:>6}{str(_med(r20)):>8}{str(_win(r20)):>7}"
              f"{str(_wins(r20)):>9}{str(_m(r20)):>9}{str(_pctl(r20, 0.99)):>8}"
              f"{str(_wins(res[s]['alpha20'])):>10}{len([x for x in r20 if x is not None]):>6}")
    print("\nBY REGIME (MEDIAN 20d return / n; TURBULENT ~8 OOS days -> unreliable):")
    regs = ["CALM", "CHOPPY", "TURBULENT"]
    print(f"{'STRATEGY':<19}" + "".join(f"{r:>20}" for r in regs))
    print("-" * 100)
    for s in STRATEGIES:
        row = f"{s:<19}"
        for r in regs:
            xs = res_reg[s][r]["ret20"]
            row += f"{(str(_med(xs)) + ' / ' + str(len(xs))):>20}"
        print(row)
    print("\nEXTREMES (largest |20d| single picks -- spot data artifacts / acquisition pops):")
    for s in STRATEGIES:
        ex = sorted(res_ext[s], key=lambda z: -abs(z[0]))[:3]
        print(f"  {s:<19} " + ", ".join(f"{t} {dt} {r:+.0f}%" for r, t, dt in ex))
    print("\n[caveats] med20 = MEDIAN 20d return (robust, the honest centre); wmean/walpha "
          "winsorize to +/-50%; rawmean is outlier-inflated -- do NOT trust it. t-stats omitted: "
          "overlapping(~75%) + cross-correlated picks make them meaningless. 60/404 deaths "
          "unrecoverable (mostly illiquid). 20d barely matured for late-2025 picks. SURVIVING != "
          "validated -- the live pick_outcomes (Jun-17+) remains the out-of-sample judge.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=150)
    return run(ap.parse_args().sample)


if __name__ == "__main__":
    sys.exit(main())
