"""
Quantitative Models Desk -- Model 4: Fama-French 5-factor regression.

Fama & French (2015) explain equity returns through five systematic factors:
  Mkt-RF  market excess return
  SMB     size      (small minus big)
  HML     value     (high minus low book/market)
  RMW     profitability (robust minus weak)
  CMA     investment    (conservative minus aggressive)

Regressing a stock's excess return on these five gives its factor exposures
(betas) and -- the part every active manager cares about -- alpha: the return
NOT explained by factor exposure.

Deterministic math (statsmodels OLS). Factor data is Ken French's published
daily series; no LLM is involved.
"""

import io
import logging
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
import statsmodels.api as sm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quant.models_config import TRADING_DAYS_PER_YEAR  # noqa: E402

log = logging.getLogger(__name__)

FF5_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
           "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip")
FF5_CACHE = Path(__file__).resolve().parent.parent / "data" / "ff5_factors.parquet"
CACHE_MAX_AGE = 7 * 86400          # refresh weekly
FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]


class FamaFrench5Factor:
    """Load French's daily factors, regress a stock on them, interpret the fit."""

    # ──────────────────────────────────────────────────────────────────────────
    def load_factor_data(self) -> pd.DataFrame:
        """Daily FF5 factors as decimals, indexed by date. Cached weekly."""
        if FF5_CACHE.exists() and (time.time() - FF5_CACHE.stat().st_mtime) < CACHE_MAX_AGE:
            return pd.read_parquet(FF5_CACHE)

        log.info("Downloading Fama-French 5-factor daily data...")
        resp = requests.get(FF5_URL, timeout=60,
                            headers={"User-Agent": "fortis-pipeline/1.0"})
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        text = zf.read(zf.namelist()[0]).decode("utf-8", errors="ignore")

        # The CSV carries descriptive comment lines, then a header row
        # ",Mkt-RF,SMB,HML,RMW,CMA,RF", then YYYYMMDD-dated rows, then a
        # trailing copyright line. Keep only 8-digit-dated rows.
        rows = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 7 and re.fullmatch(r"\d{8}", parts[0]):
                rows.append(parts[:7])
        if not rows:
            raise RuntimeError("FF5 parse failed -- no dated rows found")

        df = pd.DataFrame(rows, columns=["date", *FACTORS, "RF"])
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        for col in [*FACTORS, "RF"]:
            df[col] = pd.to_numeric(df[col], errors="coerce") / 100.0   # % -> decimal
        df = df.dropna().set_index("date").sort_index()

        FF5_CACHE.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(FF5_CACHE)
        log.info("FF5 factors: %d daily rows (%s .. %s)",
                 len(df), df.index[0].date(), df.index[-1].date())
        return df

    # ──────────────────────────────────────────────────────────────────────────
    def regress(self, ticker_returns: pd.Series, factor_data: pd.DataFrame) -> dict:
        """OLS of excess return on the five factors."""
        tr = pd.Series(ticker_returns).copy()
        tr.index = pd.to_datetime(tr.index).tz_localize(None)
        joined = factor_data.join(tr.rename("ret"), how="inner").dropna()
        if len(joined) < 60:
            raise ValueError(f"only {len(joined)} aligned observations (<60)")

        y = joined["ret"] - joined["RF"]                 # excess return
        X = sm.add_constant(joined[FACTORS])
        model = sm.OLS(y, X).fit()

        return {
            "alpha":         float(model.params["const"]),
            "alpha_t":       float(model.tvalues["const"]),
            "betas":         {f: float(model.params[f]) for f in FACTORS},
            "tstats":        {f: float(model.tvalues[f]) for f in FACTORS},
            "r_squared":     float(model.rsquared),
            "residual_std":  float(model.resid.std(ddof=1)),
            "n_obs":         int(len(joined)),
        }

    # ──────────────────────────────────────────────────────────────────────────
    def interpret_factors(self, reg: dict, ticker: str) -> dict:
        """Classify the factor profile into plain-language labels."""
        b = reg["betas"]
        mkt, smb, hml, rmw, cma = (b["Mkt-RF"], b["SMB"], b["HML"],
                                   b["RMW"], b["CMA"])

        market = ("aggressive" if mkt > 1.2 else
                  "defensive" if mkt < 0.8 else "market-like")
        size = ("small-cap" if smb > 0.2 else
                "large-cap" if smb < -0.2 else "mid-cap")
        value = ("value" if hml > 0.2 else
                 "growth" if hml < -0.2 else "blend")
        profitability = ("high-profitability" if rmw > 0.2 else
                         "low-profitability" if rmw < -0.2 else "neutral-profitability")
        investment = ("conservative" if cma > 0.2 else
                      "aggressive-investment" if cma < -0.2 else "neutral-investment")

        size_box = "small" if size == "small-cap" else \
                   "large" if size == "large-cap" else "mid"
        val_box = "value" if value == "value" else \
                  "growth" if value == "growth" else "blend"
        factor_box = (f"{size_box}_{val_box}"
                      if size_box != "mid" and val_box != "blend" else "mid_blend")

        factor_label = (f"{size} {value}, {market}, {profitability}").capitalize()
        return {"market": market, "size": size, "value": value,
                "profitability": profitability, "investment": investment,
                "factor_label": factor_label, "factor_box": factor_box}

    # ──────────────────────────────────────────────────────────────────────────
    def alpha_assessment(self, alpha: float, t_stat: float, n_observations: int) -> dict:
        """Annualize alpha and test its statistical significance."""
        alpha_annual = alpha * TRADING_DAYS_PER_YEAR
        significant = abs(t_stat) > 2.0
        if significant and alpha > 0:
            classification = "significant_positive"
        elif significant and alpha < 0:
            classification = "significant_negative"
        else:
            classification = "insignificant"
        return {"alpha_daily": round(alpha, 6),
                "alpha_annual": round(alpha_annual, 6),
                "alpha_t": round(t_stat, 3),
                "significance": classification,
                "n_obs": n_observations}
