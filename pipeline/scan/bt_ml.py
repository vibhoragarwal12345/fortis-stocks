"""
SHADOW ML overfit demonstration -- RandomForest + XGBoost, STRICT walk-forward.
==============================================================================
Trains to predict whether a name is UP at 20 trading days from NO-LOOKAHEAD
price features. The point is NOT to use these models -- it is to expose the
OVERFIT GAP: in-sample (train) accuracy vs out-of-sample (held-out, FUTURE)
accuracy. Walk-forward by DATE BLOCKS (train always strictly before test; whole
dates kept together so same-date cross-correlation can't leak across the split).
Models are NOT tuned to flatter OOS. Run AFTER prices are cached.

    python pipeline/scan/bt_ml.py [--sample 1500]
"""
from __future__ import annotations

import argparse
import random
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scan import bt_engine as E   # noqa: E402
from scan import extension as ext  # noqa: E402

H = 20  # forward horizon (trading days)
FEATS = ["ret5", "ret20", "mom_12_1", "rsi", "vol60", "breakout", "pos52",
         "above20", "above50", "ext"]


def _feat_vec(sub):
    f = E._features(sub["adj"].to_numpy())
    if f is None or any(f[k] is None for k in ("ret5", "ret20", "mom_12_1", "rsi", "vol60")):
        return None
    ohlc = sub.rename(columns={"rawclose": "close"})[["open", "high", "low", "close"]]
    e = ext.compute_extension(ohlc, asof_index=-1).get("extension_score")
    return [f["ret5"], f["ret20"], f["mom_12_1"], f["rsi"], f["vol60"],
            1.0 if f["breakout"] else 0.0, f["pos52"],
            f["px"] / f["sma20"] - 1, f["px"] / f["sma50"] - 1,
            (e if e is not None else 50.0)]


def build_dataset(sample: int):
    uni = E.load_universe()
    survivors = [t for t, m in uni.items() if not m["delisted"] and m["start"] <= E.OOS_START]
    deaths = [t for t, m in uni.items() if m["delisted"] and m["end"] >= E.OOS_START]
    cached_surv = [t for t in survivors if (E.bt_prices.CACHE / f"{t}.csv").exists()]
    surv = cached_surv[:sample]
    panel = E.build_panel(surv, deaths)
    spy_raw = E.bt_prices.get("SPY", allow_tiingo=False)
    spy = E._split_adjusted(spy_raw)
    cal = [d for d in spy.index if str(d.date()) >= E.OOS_START]
    rebal = cal[::E.REBAL_STEP]
    X, y, dates = [], [], []
    for T in rebal:
        for t, df in panel.items():
            m = uni.get(t, {})
            if not (m.get("start", "9999") <= str(T.date())):
                continue
            if m.get("end") and m["end"] < str(T.date()):
                continue
            if T not in df.index:
                continue
            p = df.index.get_loc(T)
            if isinstance(p, slice) or p < 252 or p + H >= len(df):
                continue
            sub = df.iloc[: p + 1]
            adv = float((sub["rawclose"] * sub["vol"]).iloc[-20:].mean())
            if not (adv >= E.ADV_MIN):
                continue
            fv = _feat_vec(sub)
            if fv is None or any((v is None or not np.isfinite(v)) for v in fv):
                continue
            fwd = df["adj"].iloc[p + H] / df["adj"].iloc[p] - 1
            X.append(fv); y.append(1 if fwd > 0 else 0); dates.append(T)
    return np.array(X, float), np.array(y, int), np.array(dates)


def walk_forward(X, y, dates, n_folds: int = 5):
    from sklearn.ensemble import RandomForestClassifier
    from xgboost import XGBClassifier
    order = np.argsort(dates)
    X, y, dates = X[order], y[order], dates[order]
    udates = np.array(sorted(set(dates)))
    blocks = np.array_split(udates, n_folds + 1)
    models = {
        "RandomForest": lambda: RandomForestClassifier(
            n_estimators=200, max_depth=8, n_jobs=-1, random_state=0),
        "XGBoost": lambda: XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.1, n_jobs=-1,
            random_state=0, eval_metric="logloss", verbosity=0),
    }
    out = {m: {"train": [], "oos": []} for m in models}
    base = []
    for k in range(1, len(blocks)):
        train_d = set(np.concatenate(blocks[:k]))
        test_d = set(blocks[k])
        tr = np.array([d in train_d for d in dates])
        te = np.array([d in test_d for d in dates])
        if tr.sum() < 200 or te.sum() < 50:
            continue
        base.append(max(y[te].mean(), 1 - y[te].mean()))   # majority-class
        for name, mk in models.items():
            clf = mk()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                clf.fit(X[tr], y[tr])
            out[name]["train"].append((clf.predict(X[tr]) == y[tr]).mean())
            out[name]["oos"].append((clf.predict(X[te]) == y[te]).mean())
    return out, (float(np.mean(base)) if base else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=1500)
    a = ap.parse_args()
    X, y, dates = build_dataset(a.sample)
    print(f"\nML dataset: {len(y)} (ticker,date) examples, {X.shape[1] if len(X) else 0} features, "
          f"base rate up@20d = {(y.mean()*100 if len(y) else 0):.1f}%")
    print("  [examples are OVERLAPPING (weekly rebalance, 20d horizon) and cross-correlated "
          "-> effective N is far smaller than the count]")
    if len(y) < 500:
        print("too few examples to bother."); return 0
    out, base = walk_forward(X, y, dates)
    print(f"  majority-class baseline (OOS) = {base*100:.1f}%  (a model must beat THIS to add value)\n")
    print(f"{'MODEL':<14}{'train_acc':>11}{'oos_acc':>10}{'OVERFIT GAP':>16}")
    print("-" * 51)
    for m, d in out.items():
        if not d["train"]:
            print(f"{m:<14}{'--':>11}{'--':>10}{'(no folds)':>16}"); continue
        tr, oos = np.mean(d["train"]) * 100, np.mean(d["oos"]) * 100
        print(f"{m:<14}{tr:>10.1f}%{oos:>9.1f}%{(tr - oos):>11.1f} pts")
    print("\n[OVERFIT GAP = train minus out-of-sample accuracy.] If train is high and OOS sits at "
          "the majority-class baseline, the model memorized noise, not signal -- the whole point.")
    print("[caveat] models NOT tuned to flatter OOS; overlapping/correlated samples; "
          "SURVIVORSHIP-INCOMPLETE if any deaths are still uncached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
