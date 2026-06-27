"""
SHADOW market-regime classifier -- Markov regime-switching (statsmodels).
=========================================================================
CONDITIONING ROLE ONLY. It LABELS the overall-market environment (SPY) per
date into K states by return/volatility character. It does NOT pick stocks and
does NOT forecast direction.

NO-LOOKAHEAD DESIGN:
  - Parameters are fit IN-SAMPLE on SPY history through TRAIN_END (<= 2024).
  - OUT-OF-SAMPLE labels (2025+) are FILTERED (causal) marginal probabilities
    under those FIXED params -- the label for date T uses only returns up to T
    and never re-estimates on future data. No future price leaks into a label.
  - Regimes are aligned across the fit by variance (state 0 = calmest) so the
    names are stable, not an artifact of statsmodels' arbitrary state order.

Markov caveat (printed): the model is itself fit to history and can overfit
regime boundaries. We report per-regime sample size, persistence (run length)
and the transition matrix so you can judge whether the regimes are sensible or
flickering artifacts.

    python pipeline/scan/markov_regime.py          # K=3
    python pipeline/scan/markov_regime.py 2         # K=2
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

CACHE = Path(__file__).resolve().parent / "data" / "_bt_cache"
OUT = Path(__file__).resolve().parent / "data" / "bt_regimes.csv"
TRAIN_END = "2024-12-31"      # in-sample parameter fit boundary
OOS_START = "2025-01-01"      # out-of-sample (the strategy backtest window)
K = int(sys.argv[1]) if len(sys.argv) > 1 else 3
ANN = 252 ** 0.5


def _spy_returns() -> pd.Series:
    p = CACHE / "SPY_close.csv"
    if p.exists():
        s = pd.read_csv(p, parse_dates=["date"]).set_index("date")["close"]
    else:
        import yfinance as yf
        raw = yf.download("SPY", start="2015-01-01", auto_adjust=True, progress=False)
        s = raw["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        s.name = "close"
        CACHE.mkdir(parents=True, exist_ok=True)
        s.rename_axis("date").reset_index().to_csv(p, index=False)
    ret = np.log(s).diff().dropna() * 100.0   # daily log return, in %
    ret.name = "ret"
    return ret


def _runs(labels: pd.Series) -> float:
    """Average run length (persistence). 1.0 == flips every day (artifact)."""
    if labels.empty:
        return 0.0
    changes = (labels.values[1:] != labels.values[:-1]).sum()
    return round(len(labels) / (changes + 1), 1)


def main() -> int:
    ret = _spy_returns()
    train = ret[ret.index <= TRAIN_END]
    print(f"SPY daily returns: {len(ret)} obs {ret.index[0].date()}..{ret.index[-1].date()}")
    print(f"IN-SAMPLE (param fit) <= {TRAIN_END}: {len(train)} obs | "
          f"OUT-OF-SAMPLE >= {OOS_START}: {(ret.index >= OOS_START).sum()} obs | K={K}\n")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mod_tr = MarkovRegression(train, k_regimes=K, trend="c", switching_variance=True)
        res_tr = mod_tr.fit(maxiter=200)
        # apply the FIXED in-sample params to the FULL series -> causal filtered probs
        mod_full = MarkovRegression(ret, k_regimes=K, trend="c", switching_variance=True)
        res_full = mod_full.smooth(res_tr.params)

    # align states by fitted variance: 0 = calmest, K-1 = most turbulent
    sig2 = np.array([res_tr.params[f"sigma2[{i}]"] for i in range(K)])
    mu = np.array([res_tr.params[f"const[{i}]"] for i in range(K)])
    perm = list(np.argsort(sig2))                      # raw-state order -> rank
    filt = res_full.filtered_marginal_probabilities    # DataFrame (T x K), causal
    filt = filt.reindex(columns=range(K))
    ranked = filt.iloc[:, perm].to_numpy()
    labels = pd.Series(np.argmax(ranked, axis=1), index=ret.index, name="regime")

    names = (["CALM", "TURBULENT"] if K == 2 else
             ["CALM", "CHOPPY", "TURBULENT"] if K == 3 else
             [f"R{i}" for i in range(K)])
    name_of = {i: names[i] for i in range(K)}

    # transition matrix (in-sample params), reordered to ranked states
    P = res_tr.regime_transition  # shape (K, K) or (K, K, 1)
    P = np.asarray(P)
    if P.ndim == 3:
        P = P[:, :, 0]
    P = P[np.ix_(perm, perm)]

    print("REGIME PARAMS (in-sample fit, ranked calm->turbulent):")
    for rank, raw in enumerate(perm):
        print(f"  {name_of[rank]:<10} daily_mean={mu[raw]:+.3f}%  "
              f"daily_vol={sig2[raw]**0.5:.3f}%  (ann_vol~{sig2[raw]**0.5*ANN:.1f}%)  "
              f"self-persist={P[rank, rank]:.2f}")

    def _report(window_label, mask):
        lab = labels[mask]
        r = ret[mask]
        print(f"\n{window_label}  ({len(lab)} days, avg run length {_runs(lab)}d):")
        print(f"  {'REGIME':<10}{'days':>6}{'%days':>7}{'ann_ret':>9}{'ann_vol':>9}{'win%':>7}")
        for rank in range(K):
            m = lab == rank
            d = int(m.sum())
            if d == 0:
                print(f"  {name_of[rank]:<10}{d:>6}{0:>7.0f}{'--':>9}{'--':>9}{'--':>7}")
                continue
            rr = r[m.values]
            ann_ret = rr.mean() * 252
            ann_vol = rr.std() * ANN
            win = (rr > 0).mean() * 100
            flag = "  << thin sample, unreliable" if d < 20 else ""
            print(f"  {name_of[rank]:<10}{d:>6}{100*d/len(lab):>7.1f}"
                  f"{ann_ret:>9.1f}{ann_vol:>9.1f}{win:>7.1f}{flag}")

    _report("FULL SERIES (incl. in-sample)", ret.index >= ret.index[0])
    _report("OUT-OF-SAMPLE >= 2025 (the conditioning window)", ret.index >= OOS_START)

    print("\nTRANSITION MATRIX (in-sample, rows=from):")
    print("           " + "".join(f"{name_of[j]:>11}" for j in range(K)))
    for i in range(K):
        print(f"  {name_of[i]:<9}" + "".join(f"{P[i, j]:>11.2f}" for j in range(K)))

    # persist OOS labels for the strategy-conditioning step
    oos = labels[labels.index >= OOS_START]
    out = pd.DataFrame({"date": oos.index.date, "regime": oos.values,
                        "regime_name": [name_of[r] for r in oos.values]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)

    print("\nCAVEATS:")
    print(f"  - Params fit IN-SAMPLE (<= {TRAIN_END}); 2025+ labels are causal/filtered, no lookahead.")
    print("  - Regimes ranked by variance (stable naming). Check: do ann_vol values separate cleanly,")
    print("    are run lengths multi-day (persistent, not flickering), is the transition diagonal high?")
    print("  - Conditions/explains only -- it does NOT predict direction or pick stocks.")
    print(f"\nwrote OOS regime labels -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
