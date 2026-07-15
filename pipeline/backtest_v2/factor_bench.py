"""Alpha-factor bench — Vibe-Trading's zoo idea, run natively on our panels.

Factor definitions absorbed from the HKUDS/Vibe-Trading Alpha Zoo families
(Qlib Alpha158 transform grid + named academic factors), re-implemented as
vectorized pandas over our wide PIT matrices rather than vendoring their
execution framework. Definitions are public (Qlib docs / original papers);
implementations here are ours.

Method (mirrors their alive/reversed/dead bench):
  - factor value at day D uses bars <= D only (rolling ops guarantee this)
  - target: 5d forward SPY-relative alpha, entered at the NEXT close
  - daily cross-sectional Spearman IC over the full liquid universe
  - a factor survives only if HEALTHY (|mean IC| >= 0.02, sign-consistent,
    |t| >= 2) with the SAME sign in BOTH windows

    python -m pipeline.backtest_v2.factor_bench
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .run_card import REPORTS_DIR, _git_sha

log = logging.getLogger("backtest_v2.factor_bench")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")

CACHE = Path(__file__).resolve().parent.parent / "data" / "backtest_v2_cache"
WINDOWS = {
    "may_jun": ("panel_2026-05-19_2026-06-17_3347.parquet", "2026-05-19", "2026-06-17"),
    "jun_jul": ("panel_2026-06-18_2026-07-15_3347.parquet", "2026-06-18", "2026-07-15"),
}
BENCHMARK = "SPY"
HORIZON = 5
MIN_NAMES_PER_DAY = 300      # cross-section must be broad to count
IC_HEALTHY, T_HEALTHY, POS_HEALTHY = 0.02, 2.0, 0.55


def load_wide(parquet: Path) -> dict[str, pd.DataFrame]:
    flat = pd.read_parquet(parquet)
    out = {}
    for col in ("Open", "High", "Low", "Close", "Volume"):
        out[col.lower()] = flat.pivot_table(index="Date", columns="ticker",
                                            values=col).sort_index()
    return out


# ── factor library (each returns dates × tickers DataFrame) ───────────────

def factor_library(m: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    c, o, h, l, v = m["close"], m["open"], m["high"], m["low"], m["volume"]
    ret = c.pct_change()
    f: dict[str, pd.DataFrame] = {}

    for w in (5, 10, 20, 60):
        # Qlib158 families (per-window grid)
        f[f"roc{w}"] = c.pct_change(w)                                   # momentum/ROC
        f[f"ma{w}"] = c.rolling(w).mean() / c                            # MA ratio
        f[f"std{w}"] = ret.rolling(w).std()                              # volatility
        f[f"max{w}"] = c.rolling(w).max() / c
        f[f"min{w}"] = c.rolling(w).min() / c
        f[f"rsv{w}"] = (c - l.rolling(w).min()) / (
            h.rolling(w).max() - l.rolling(w).min() + 1e-12)             # range position
        f[f"vma{w}"] = v.rolling(w).mean() / (v + 1e-12)                 # volume MA ratio
        f[f"vstd{w}"] = v.pct_change().rolling(w).std()                  # volume vol
        f[f"corr{w}"] = c.rolling(w).corr(np.log1p(v))                   # price-volume corr
        f[f"cntp{w}"] = (ret > 0).rolling(w).mean()                      # up-day share
        f[f"sump{w}"] = ret.clip(lower=0).rolling(w).sum() / (
            ret.abs().rolling(w).sum() + 1e-12)                          # gain share (RSI-like)
        f[f"wvma{w}"] = (ret.abs() * v).rolling(w).mean() / (
            v.rolling(w).mean() + 1e-12)                                 # vol-weighted move

    # K-bar shape (Qlib158)
    f["kmid"] = (c - o) / o
    f["klen"] = (h - l) / o
    f["ksft"] = (2 * c - h - l) / o

    # Academic
    f["strev"] = ret.rolling(5).sum()                    # Jegadeesh short-term reversal input
    f["high52w"] = c / c.rolling(252, min_periods=60).max()   # George-Hwang
    f["illiq"] = (ret.abs() / (np.log1p(c * v) + 1e-12)).rolling(20).mean()  # Amihud
    f["retskew"] = ret.rolling(20).skew()                # Harvey-Siddique
    f["maxret"] = ret.rolling(20).max()                  # Bali lottery-demand MAX
    return f


def forward_alpha(m: dict[str, pd.DataFrame], horizon: int = HORIZON) -> pd.DataFrame:
    c = m["close"]
    entry = c.shift(-1)                    # next close after signal day
    exit_ = c.shift(-1 - horizon)
    fwd = exit_ / entry - 1
    spy = fwd[BENCHMARK]
    return (fwd.sub(spy, axis=0)) * 100    # SPY-relative, %


HORIZON_CURVE = (1, 5, 10, 20)   # IC measured at each; gates key off HORIZON=5


def bench_window(name: str) -> pd.DataFrame:
    fname, start, end = WINDOWS[name]
    m = load_wide(CACHE / fname)
    fas = {h: forward_alpha(m, h) for h in HORIZON_CURVE}
    lib = factor_library(m)
    days = [d for d in m["close"].index
            if start <= d.strftime("%Y-%m-%d") <= end]
    rows = []
    for fac_name, fac in lib.items():
        row: dict = {"factor": fac_name}
        for h in HORIZON_CURVE:
            fa = fas[h]
            ics = []
            for d in days:
                if d not in fac.index or d not in fa.index:
                    continue
                x, y = fac.loc[d], fa.loc[d]
                ok = x.notna() & y.notna()
                if ok.sum() < MIN_NAMES_PER_DAY:
                    continue
                ic = x[ok].rank().corr(y[ok].rank())
                if np.isfinite(ic):
                    ics.append(ic)
            if len(ics) < 5:
                continue
            arr = np.array(ics)
            t = arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)) + 1e-12)
            if h == HORIZON:      # gate horizon keeps the original column names
                row.update({f"ic_{name}": round(float(arr.mean()), 4),
                            f"t_{name}": round(float(t), 2),
                            f"pos_{name}": round(float((arr > 0).mean()), 2),
                            f"days_{name}": len(arr)})
            row[f"ic{h}d_{name}"] = round(float(arr.mean()), 4)
        if f"ic_{name}" in row:
            rows.append(row)
    return pd.DataFrame(rows).set_index("factor")


def classify(df: pd.DataFrame) -> pd.DataFrame:
    def state(r) -> str:
        ic1, ic2 = r["ic_may_jun"], r["ic_jun_jul"]
        t1, t2 = r["t_may_jun"], r["t_jun_jul"]
        if np.sign(ic1) != np.sign(ic2):
            return "INCONSISTENT"
        if min(abs(ic1), abs(ic2)) >= IC_HEALTHY and min(abs(t1), abs(t2)) >= T_HEALTHY:
            return "HEALTHY"
        if min(abs(t1), abs(t2)) >= 1.0:
            return "WARNING"
        return "DEAD"
    df = df.copy()
    df["state"] = df.apply(state, axis=1)
    df["ic_joint"] = (df["ic_may_jun"] + df["ic_jun_jul"]) / 2
    return df.sort_values("ic_joint", key=abs, ascending=False)


def main() -> int:
    b1 = bench_window("may_jun")
    b2 = bench_window("jun_jul")
    df = classify(b1.join(b2, how="inner"))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    out = REPORTS_DIR / f"factor_bench_{stamp}.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_git_sha": _git_sha(),
        "method": "daily cross-sectional Spearman IC vs 5d fwd SPY-relative alpha, "
                  "full liquid universe, two independent windows",
        "gates": {"ic": IC_HEALTHY, "t": T_HEALTHY, "same_sign_both_windows": True},
        "table": json.loads(df.reset_index().to_json(orient="records")),
    }, indent=2), encoding="utf-8")

    healthy = df[df["state"] == "HEALTHY"]
    pd.set_option("display.width", 160)
    print("\n=== HEALTHY in BOTH windows (sign-consistent, |IC|>=0.02, |t|>=2) ===")
    print(healthy.to_string())
    print("\n=== Top 15 by |joint IC| (any state) ===")
    print(df.head(15).to_string())
    print(f"\nfull table -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
