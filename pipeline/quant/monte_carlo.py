"""
Quantitative Models Desk -- Model 1: Monte Carlo price simulator.

Deterministic math (numpy / scipy). The distribution is fitted to the data:
a Jarque-Bera test decides Normal vs Student's t -- and for almost every stock
the answer is t, because the Normal assumption understates tail risk.

The ONLY LLM touch-point is explain(): it rephrases the already-computed
numbers in plain English and is forbidden from introducing new ones.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROQ_API_KEY  # noqa: E402
from quant.models_config import MC_HORIZONS, MONTE_CARLO_SIMS  # noqa: E402
from quant.utils import safe_log_returns  # noqa: E402

log = logging.getLogger(__name__)
GROQ_MODEL = "llama-3.3-70b-versatile"


class MonteCarloSimulator:
    """Calibrate a return distribution, simulate price paths, summarize, explain."""

    def __init__(self):
        self.distribution_type: str | None = None
        self.params: dict = {}
        self.calibration_quality: float | None = None
        self.jarque_bera_p: float | None = None
        self.n_obs: int = 0

    # ──────────────────────────────────────────────────────────────────────────
    def calibrate(self, prices: pd.Series) -> dict:
        """Fit Normal or Student's t to the log returns of `prices`.

        calibration_quality = 1 - KS statistic (1.0 = perfect fit).
        """
        rets = safe_log_returns(prices)
        if len(rets) < 30:
            raise ValueError(f"need >=30 returns to calibrate, got {len(rets)}")
        self.n_obs = len(rets)
        arr = rets.to_numpy()

        # Jarque-Bera: p > 0.05 -> cannot reject normality.
        jb_stat, jb_p = stats.jarque_bera(arr)
        self.jarque_bera_p = float(jb_p)

        if jb_p > 0.05:
            self.distribution_type = "normal"
            self.params = {"mu": float(arr.mean()),
                           "sigma": float(arr.std(ddof=1))}
            ks_d, _ = stats.kstest(arr, "norm",
                                   args=(self.params["mu"], self.params["sigma"]))
        else:
            # Heavy tails -> Student's t (the usual case for equities).
            df, loc, scale = stats.t.fit(arr)
            self.distribution_type = "student_t"
            self.params = {"df": float(df), "loc": float(loc),
                           "scale": float(scale)}
            ks_d, _ = stats.kstest(arr, "t", args=(df, loc, scale))

        self.calibration_quality = round(float(max(0.0, min(1.0, 1.0 - ks_d))), 4)
        return {"distribution_type": self.distribution_type,
                "params": self.params,
                "calibration_quality": self.calibration_quality,
                "jarque_bera_p": self.jarque_bera_p,
                "n_obs": self.n_obs}

    # ──────────────────────────────────────────────────────────────────────────
    def simulate(self, starting_price: float, days_ahead: int,
                 n_simulations: int = MONTE_CARLO_SIMS) -> np.ndarray:
        """Simulate price paths under the calibrated distribution.

        Daily log returns are drawn from the fitted distribution and compounded
        geometrically: S(t+1) = S(t) * exp(r). Returns a (n_simulations,
        days_ahead + 1) array; column 0 is `starting_price`, column -1 holds
        the final prices.
        """
        if self.distribution_type is None:
            raise RuntimeError("calibrate() must run before simulate()")
        if self.distribution_type == "normal":
            draws = np.random.normal(self.params["mu"], self.params["sigma"],
                                     size=(n_simulations, days_ahead))
        else:
            draws = stats.t.rvs(self.params["df"], loc=self.params["loc"],
                                scale=self.params["scale"],
                                size=(n_simulations, days_ahead))
        log_paths = np.cumsum(draws, axis=1)
        prices = starting_price * np.exp(log_paths)
        start_col = np.full((n_simulations, 1), starting_price)
        return np.hstack([start_col, prices])

    # ──────────────────────────────────────────────────────────────────────────
    def simulate_with_garch_vol(self, starting_price: float, days_ahead: int,
                                garch_forecast, n_simulations: int = MONTE_CARLO_SIMS
                                ) -> np.ndarray:
        """Forward-looking simulation: time-varying daily volatility from a GARCH
        forecast replaces the single static sigma -- the simulation now reflects
        forecast volatility clustering rather than a backward-looking constant.

        `garch_forecast` is an array of daily decimal volatilities; the
        calibrated distribution still supplies the drift and (for student_t)
        the tail shape. If the forecast is shorter than days_ahead the last
        value is held flat.
        """
        if self.distribution_type is None:
            raise RuntimeError("calibrate() must run before simulate_with_garch_vol()")
        fc = np.asarray(garch_forecast, dtype=float).ravel()
        if fc.size == 0:
            raise ValueError("garch_forecast is empty")
        if fc.size < days_ahead:
            fc = np.concatenate([fc, np.full(days_ahead - fc.size, fc[-1])])
        sigma_t = fc[:days_ahead]                      # daily decimal vol per step

        if self.distribution_type == "normal":
            mu = self.params["mu"]
            z = np.random.standard_normal((n_simulations, days_ahead))
        else:
            df = self.params["df"]
            mu = self.params["loc"]
            # standardized Student-t (unit variance) so sigma_t scales it cleanly
            z = stats.t.rvs(df, size=(n_simulations, days_ahead)) \
                * np.sqrt((df - 2.0) / df)
        draws = mu + z * sigma_t                       # sigma_t broadcasts per day
        log_paths = np.cumsum(draws, axis=1)
        prices = starting_price * np.exp(log_paths)
        start_col = np.full((n_simulations, 1), starting_price)
        return np.hstack([start_col, prices])

    # ──────────────────────────────────────────────────────────────────────────
    def summary(self, paths: np.ndarray) -> dict:
        """Distil simulated paths into the result dict."""
        start = float(paths[0, 0])
        final = paths[:, -1]
        path_min = paths.min(axis=1)
        p5, p25, p50, p75, p95 = np.percentile(final, [5, 25, 50, 75, 95])
        return {
            "mean_price":            round(float(final.mean()), 4),
            "median_price":          round(float(p50), 4),
            "p5_price":              round(float(p5), 4),
            "p25_price":             round(float(p25), 4),
            "p75_price":             round(float(p75), 4),
            "p95_price":             round(float(p95), 4),
            "prob_up":               round(float((final > start).mean()), 4),
            "prob_loss_10pct":       round(float((final < start * 0.9).mean()), 4),
            "prob_gain_10pct":       round(float((final > start * 1.1).mean()), 4),
            "expected_max_drawdown": round(float(
                np.percentile(path_min / start - 1.0, 5)), 4),
            "calibration_metric":    self.calibration_quality,
        }

    # ──────────────────────────────────────────────────────────────────────────
    def backtest_coverage(self, prices: pd.Series, horizon: int = 30) -> float | None:
        """Empirical coverage of the model's 90% CI for `horizon`-day cumulative
        returns over the available history. Should land in 0.85-0.95."""
        rets = safe_log_returns(prices)
        realized = rets.rolling(horizon).sum().dropna().to_numpy()
        if len(realized) < 30:
            return None
        if self.distribution_type == "normal":
            mu, sigma = self.params["mu"], self.params["sigma"]
            lo = mu * horizon - 1.645 * sigma * np.sqrt(horizon)
            hi = mu * horizon + 1.645 * sigma * np.sqrt(horizon)
        else:
            sim = stats.t.rvs(self.params["df"], loc=self.params["loc"],
                              scale=self.params["scale"], size=(20000, horizon))
            cum = sim.sum(axis=1)
            lo, hi = np.percentile(cum, [5, 95])
        return round(float(((realized >= lo) & (realized <= hi)).mean()), 4)

    # ──────────────────────────────────────────────────────────────────────────
    def explain(self, summary_dict: dict, ticker: str, current_price: float) -> str:
        """Plain-English interpretation of the COMPUTED numbers (Groq).

        The LLM may only restate the numbers it is given; it never computes.
        Falls back to a deterministic template when Groq is unavailable.
        """
        s = summary_dict
        if GROQ_API_KEY:
            try:
                from groq import Groq
                system = ("You explain pre-computed quantitative results in plain "
                          "English. You may ONLY restate the numbers provided. "
                          "You must NEVER introduce, compute, round differently, "
                          "or estimate any number that is not explicitly given. "
                          "Write exactly two sentences.")
                user = (f"Ticker {ticker}, current price ${current_price:.2f}. "
                        f"Monte Carlo computed: probability of being above the "
                        f"current price = {s['prob_up']:.0%}; median simulated "
                        f"price = ${s['median_price']:.2f}; probability of a >10% "
                        f"drop = {s['prob_loss_10pct']:.0%}; expected max drawdown "
                        f"(5th percentile) = {s['expected_max_drawdown']:.1%}. "
                        f"Write the two-sentence interpretation.")
                resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.2, max_tokens=160)
                txt = (resp.choices[0].message.content or "").strip()
                if txt:
                    return txt
            except Exception as exc:
                log.warning("Groq explain failed for %s: %s", ticker, exc)

        return (f"Monte Carlo simulation suggests {ticker} has a "
                f"{s['prob_up']:.0%} probability of being above the current "
                f"price, with a median simulated target of ${s['median_price']:.2f}. "
                f"Tail risk is notable -- a {s['prob_loss_10pct']:.0%} chance of a "
                f">10% drop and an expected max drawdown near "
                f"{s['expected_max_drawdown']:.1%}.")
