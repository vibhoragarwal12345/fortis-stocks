"""
Quantitative Models Desk -- Model 3: GARCH(1,1) volatility forecasting.

Realized volatility is backward-looking. GARCH(1,1) forecasts FORWARD
volatility by capturing volatility clustering -- high-vol days follow high-vol
days. Implemented with the `arch` package (Kevin Sheppard's canonical library).

All numbers are deterministic. The LLM only rephrases computed results in
explain(), and is forbidden from inventing numbers.

Returns are fitted in PERCENT units (x100) -- the scale `arch` is designed for;
volatilities are converted back to decimals on the way out.
"""

import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROQ_API_KEY  # noqa: E402
from quant.models_config import TRADING_DAYS_PER_YEAR  # noqa: E402

log = logging.getLogger(__name__)
GROQ_MODEL = "openai/gpt-oss-120b"

ANNUALIZE = np.sqrt(TRADING_DAYS_PER_YEAR)
MIN_RELIABLE_DAYS = 504
PERSISTENCE_UNSTABLE = 0.99
ARCH_LM_NEEDED_P = 0.10        # p > this -> no clustering -> GARCH not needed
BACKTEST_WINDOW = 30
BACKTEST_MAX_REL_ERROR = 0.50


class GARCHModel:
    """Fit GARCH(1,1), forecast conditional volatility, classify regime."""

    def __init__(self):
        self.res = None
        self.scale = 100.0                  # returns fitted in percent
        self.persistence: float | None = None
        self.unconditional_vol_annual: float | None = None   # decimal
        self.aic: float | None = None
        self.bic: float | None = None
        self.arch_lm_p: float | None = None
        self.n_obs: int = 0
        self.forecast_clamped: bool = False
        self._uncond_daily: float | None = None
        self._sample_daily_vol: float | None = None   # robust clamp anchor
        self._forecast_cache: dict | None = None

    # ──────────────────────────────────────────────────────────────────────────
    def fit(self, returns: pd.Series, p: int = 1, q: int = 1) -> dict:
        """Fit GARCH(p,q) with Student's t innovations."""
        from arch import arch_model
        from statsmodels.stats.diagnostic import het_arch

        r = pd.Series(returns).dropna()
        if len(r) < 60:
            raise ValueError(f"need >=60 returns for GARCH, got {len(r)}")
        self.n_obs = len(r)
        # Robust scale anchor for the forecast clamp -- the plain sample stdev,
        # which (unlike the model's unconditional vol) cannot be degenerate.
        self._sample_daily_vol = float(r.std(ddof=1))
        scaled = r * self.scale

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            am = arch_model(scaled, vol="GARCH", p=p, q=q,
                            dist="studentst", mean="Constant")
            self.res = am.fit(disp="off", options={"maxiter": 1000})

        params = self.res.params
        alpha = sum(v for k, v in params.items() if k.startswith("alpha"))
        beta = sum(v for k, v in params.items() if k.startswith("beta"))
        self.persistence = float(alpha + beta)
        omega = float(params.get("omega", 0.0))

        # Unconditional variance = omega / (1 - persistence)  [percent^2]
        if self.persistence < 1.0:
            uncond_var_pct = omega / (1.0 - self.persistence)
        else:
            uncond_var_pct = float(np.var(scaled))
        self._uncond_daily = np.sqrt(uncond_var_pct) / self.scale
        self.unconditional_vol_annual = float(self._uncond_daily * ANNUALIZE)
        self.aic, self.bic = float(self.res.aic), float(self.res.bic)

        # Engle's ARCH-LM on the demeaned returns: do ARCH effects exist at all?
        try:
            lm = het_arch(r - r.mean(), nlags=min(10, len(r) // 5))
            self.arch_lm_p = float(lm[1])
        except Exception:
            self.arch_lm_p = None

        return {
            "alpha": round(float(alpha), 6),
            "beta": round(float(beta), 6),
            "persistence": round(self.persistence, 6),
            "unconditional_vol_annual": round(self.unconditional_vol_annual, 6),
            "aic": round(self.aic, 2), "bic": round(self.bic, 2),
            "arch_lm_p": self.arch_lm_p, "n_obs": self.n_obs,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Daily forecast vol is clamped to this band, as a multiple of the SAMPLE
    # volatility of the fitted returns. The sample stdev is used (not the
    # model's unconditional vol) because a near-unit-root fit makes the
    # unconditional vol itself degenerate -- anchoring the clamp to it would
    # leave the ceiling so high it never bites.
    FORECAST_CLAMP = (0.20, 4.0)

    def forecast(self, horizon_days: int = 22) -> dict:
        """Forecast conditional volatility over `horizon_days`.

        Each day's forecast is clamped to FORECAST_CLAMP x unconditional vol;
        `forecast_clamped` records whether the explosive-forecast guard fired.
        All horizon aggregates are derived from the clamped series so they stay
        mutually consistent.
        """
        if self.res is None:
            raise RuntimeError("fit() must run before forecast()")
        fc = self.res.forecast(horizon=horizon_days, reindex=False)
        var_pct = np.asarray(fc.variance.iloc[-1].values, dtype=float)  # percent^2
        daily_vol = np.sqrt(var_pct) / self.scale                       # decimal daily

        lo = self.FORECAST_CLAMP[0] * self._sample_daily_vol
        hi = self.FORECAST_CLAMP[1] * self._sample_daily_vol
        clamped = np.clip(daily_vol, lo, hi)
        self.forecast_clamped = bool(np.any(np.abs(clamped - daily_vol) > 1e-12))
        daily_vol = clamped
        daily_vol_annual = daily_vol * ANNUALIZE

        def _ann(n):
            n = min(n, len(daily_vol))
            return float(np.sqrt(np.mean(daily_vol[:n] ** 2)) * ANNUALIZE)

        cumulative = float(np.sqrt(np.sum(daily_vol ** 2)))             # horizon total
        self._forecast_cache = {
            "daily_vol_annual":    [round(float(v), 6) for v in daily_vol_annual],
            "forecast_vol_1d":     round(_ann(1), 6),
            "forecast_vol_5d":     round(_ann(5), 6),
            "forecast_vol_22d":    round(_ann(horizon_days), 6),
            "cumulative_horizon_vol": round(cumulative, 6),
            "forecast_clamped":    self.forecast_clamped,
        }
        return self._forecast_cache

    # ──────────────────────────────────────────────────────────────────────────
    def current_regime(self, returns: pd.Series | None = None) -> dict:
        """Classify the current volatility regime vs the long-term average."""
        if self.res is None:
            raise RuntimeError("fit() must run before current_regime()")
        current_daily = float(self.res.conditional_volatility[-1]) / self.scale
        ratio = (current_daily / self._uncond_daily) if self._uncond_daily else 1.0
        if ratio < 0.5:
            regime = "low"
        elif ratio <= 1.5:
            regime = "normal"
        elif ratio <= 2.5:
            regime = "elevated"
        else:
            regime = "crisis"
        return {"regime": regime, "ratio": round(ratio, 3),
                "current_vol_annual": round(current_daily * ANNUALIZE, 6),
                "unconditional_vol_annual": round(self.unconditional_vol_annual, 6)}

    # ──────────────────────────────────────────────────────────────────────────
    def compare_to_iv(self, ticker: str) -> dict:
        """Compare the GARCH 22-day forecast to ATM implied volatility.

        IV is computed by Black-Scholes inversion of real bid/ask option prices
        (quant.utils.fetch_atm_iv) -- never read from yfinance's broken
        `impliedVolatility` field. iv_vs_garch_spread = IV - GARCH (positive =>
        options expensive). Returns the comparison plus the full IV metadata
        for the audit trail; a 'failed'-quality IV yields spread = None.
        """
        from quant.utils import fetch_atm_iv

        if self._forecast_cache is None:
            self.forecast(22)
        garch_vol = self._forecast_cache["forecast_vol_22d"]

        iv_meta = fetch_atm_iv(ticker)
        iv = iv_meta.get("iv")
        if (iv_meta.get("quality") == "failed" or iv is None
                or (isinstance(iv, float) and np.isnan(iv))):
            return {"iv": None, "garch_vol": garch_vol, "spread": None,
                    "verdict": "no_iv_data", "iv_meta": iv_meta}

        spread = float(iv) - garch_vol
        if spread > 0.03:
            verdict = "options_overpriced"        # IV >> forecast -> sell premium
        elif spread < -0.03:
            verdict = "options_underpriced"       # IV << forecast -> buy options
        else:
            verdict = "fairly_priced"
        return {"iv": round(float(iv), 6), "garch_vol": garch_vol,
                "spread": round(spread, 6), "verdict": verdict,
                "iv_meta": iv_meta}

    # ──────────────────────────────────────────────────────────────────────────
    def backtest(self, returns: pd.Series, p: int = 1, q: int = 1) -> dict:
        """Out-of-sample check: fit on all-but-last-30 days, forecast 30 days,
        compare the average forecast vol to the realized vol of that window."""
        r = pd.Series(returns).dropna()
        if len(r) < MIN_RELIABLE_DAYS:
            return {"mae_rel": None, "ok": None}
        train, test = r.iloc[:-BACKTEST_WINDOW], r.iloc[-BACKTEST_WINDOW:]
        try:
            oos = GARCHModel()
            oos.fit(train, p, q)
            fc = oos.forecast(BACKTEST_WINDOW)
            forecast_vol = float(np.mean(fc["daily_vol_annual"]))
            realized_vol = float(test.std(ddof=1) * ANNUALIZE)
            if realized_vol <= 0:
                return {"mae_rel": None, "ok": None}
            # Floor the denominator at 10% annualized vol: a freakishly calm or
            # stale test window gives a near-zero realized vol that would
            # otherwise explode the relative error (the TPH 1490% artifact).
            denom = max(realized_vol, 0.10)
            mae_rel = abs(forecast_vol - realized_vol) / denom
            return {"mae_rel": round(mae_rel, 4),
                    "forecast_vol": round(forecast_vol, 6),
                    "realized_vol": round(realized_vol, 6),
                    "ok": mae_rel <= BACKTEST_MAX_REL_ERROR}
        except Exception as exc:
            log.warning("GARCH backtest failed: %s", exc)
            return {"mae_rel": None, "ok": None}

    # ──────────────────────────────────────────────────────────────────────────
    def assess_quality(self, n_returns: int, backtest_ok,
                       forecast_clamped: bool = False) -> str:
        """'reliable' | 'use_with_caution' | 'unreliable' per the quality checks."""
        if self.persistence is not None and self.persistence > PERSISTENCE_UNSTABLE:
            return "unreliable"            # near-unit-root: unstable fit
        if backtest_ok is False:
            return "unreliable"
        if n_returns < MIN_RELIABLE_DAYS:
            return "use_with_caution"
        if self.arch_lm_p is not None and self.arch_lm_p > ARCH_LM_NEEDED_P:
            return "use_with_caution"      # no clustering -> realized vol is fine
        if forecast_clamped:
            return "use_with_caution"      # explosive-forecast guard fired
        return "reliable"

    # ──────────────────────────────────────────────────────────────────────────
    def explain(self, forecast_results: dict, ticker: str) -> str:
        """Plain-English interpretation of the COMPUTED GARCH numbers (Groq)."""
        f = forecast_results
        vol22 = f["forecast_vol_22d"] * 100
        regime = f["regime"]
        iv = f.get("iv")
        spread = f.get("spread")

        if GROQ_API_KEY:
            try:
                from groq import Groq
                system = ("You explain pre-computed volatility model results in "
                          "plain English. You may ONLY restate the numbers "
                          "provided -- never invent, compute or estimate a "
                          "number. Write one or two sentences.")
                iv_txt = (f"implied volatility is {iv*100:.1f}% (spread "
                          f"{spread*100:+.1f}%, options {f.get('verdict')})"
                          if iv is not None else "no implied-volatility data")
                user = (f"{ticker}: GARCH 30-day forecast volatility = "
                        f"{vol22:.1f}% annualized; volatility regime = {regime}; "
                        f"{iv_txt}. Write the interpretation.")
                resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    # 512 (not 150): reasoning models spend tokens thinking
                    # first, so a tight cap truncates mid-sentence.
                    temperature=0.2, max_tokens=512)
                txt = (resp.choices[0].message.content or "").strip()
                if txt:
                    return txt
            except Exception as exc:
                log.warning("Groq explain failed for %s: %s", ticker, exc)

        base = (f"GARCH forecasts 30-day volatility of {vol22:.1f}% annualized "
                f"for {ticker}, indicating a {regime} volatility regime.")
        if iv is not None:
            cmp = ("higher than" if spread < 0 else "lower than"
                   if spread > 0 else "in line with")
            note = {"options_overpriced": "options screen expensive",
                    "options_underpriced": "options screen cheap",
                    "fairly_priced": "options screen fairly priced"
                    }.get(f.get("verdict"), "")
            base += (f" The forecast is {cmp} implied volatility of "
                     f"{iv*100:.1f}% -- {note}.")
        return base
