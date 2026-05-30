"""
Quantitative Models Desk -- Model 2: VaR & CVaR risk model.

Deterministic math (numpy / scipy). Parametric VaR uses a Student's t fit, not
Normal -- the Normal assumption systematically understates equity tail risk.
CVaR captures the expected loss *beyond* VaR, which VaR alone misses.

VaR / CVaR are returned as POSITIVE loss magnitudes (e.g. 0.031 == a 3.1%
loss). Callers persisting to the DB negate them per the table's sign convention.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quant.models_config import (  # noqa: E402
    STRESS_SCENARIOS, TRADING_DAYS_PER_YEAR, VAR_FLOOR,
)
from quant.utils import fetch_price_history, safe_log_returns  # noqa: E402

log = logging.getLogger(__name__)


class RiskModel:
    """Historical & parametric VaR, CVaR, portfolio risk and stress tests."""

    def __init__(self, risk_free_rate: float = 0.04):
        self.rf = risk_free_rate

    # ── single-series VaR / CVaR ──────────────────────────────────────────────
    def historical_var(self, returns: pd.Series, confidence: float = 0.95,
                        horizon_days: int = 1) -> float:
        """Historical-simulation VaR: -(1-confidence) percentile, scaled by sqrt(h)."""
        r = pd.Series(returns).dropna().to_numpy()
        if len(r) < 2:
            return 0.0
        var_1d = -float(np.percentile(r, (1.0 - confidence) * 100.0))
        return max(var_1d, 0.0) * np.sqrt(horizon_days)

    def parametric_var(self, returns: pd.Series, confidence: float = 0.95,
                       horizon_days: int = 1) -> float:
        """Variance-covariance VaR using a Student's t (df from kurtosis)."""
        r = pd.Series(returns).dropna().to_numpy()
        if len(r) < 2:
            return 0.0
        mu, sigma = r.mean(), r.std(ddof=1)
        excess_kurt = stats.kurtosis(r, fisher=True)
        # t-distribution kurtosis = 6/(df-4)  ->  df = 6/excess_kurt + 4
        df = (6.0 / excess_kurt + 4.0) if excess_kurt > 0.1 else 30.0
        df = float(min(max(df, 4.5), 60.0))
        # standardized t quantile (unit variance) so sigma scales it correctly
        std_t_q = stats.t.ppf(1.0 - confidence, df) * np.sqrt((df - 2.0) / df)
        var_1d = -(mu + sigma * std_t_q)
        return max(var_1d, 0.0) * np.sqrt(horizon_days)

    def cvar(self, returns: pd.Series, confidence: float = 0.95) -> float:
        """Conditional VaR -- mean loss given the loss exceeds VaR."""
        r = pd.Series(returns).dropna().to_numpy()
        if len(r) < 2:
            return 0.0
        threshold = np.percentile(r, (1.0 - confidence) * 100.0)
        tail = r[r <= threshold]
        if tail.size == 0:
            return self.historical_var(returns, confidence)
        return max(-float(tail.mean()), 0.0)

    # ── descriptive risk metrics ──────────────────────────────────────────────
    @staticmethod
    def annual_volatility(returns: pd.Series) -> float:
        return float(pd.Series(returns).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))

    @staticmethod
    def max_drawdown(prices: pd.Series) -> float:
        p = pd.Series(prices).dropna()
        if len(p) < 2:
            return 0.0
        return float((p / p.cummax() - 1.0).min())

    def sharpe_ratio(self, returns: pd.Series) -> float:
        r = pd.Series(returns).dropna()
        vol = self.annual_volatility(r)
        if vol == 0:
            return 0.0
        ann_ret = float(r.mean()) * TRADING_DAYS_PER_YEAR
        return (ann_ret - self.rf) / vol

    def sortino_ratio(self, returns: pd.Series) -> float:
        r = pd.Series(returns).dropna()
        downside = r[r < 0]
        dd = float(downside.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)) \
            if len(downside) > 1 else 0.0
        if dd == 0:
            return 0.0
        ann_ret = float(r.mean()) * TRADING_DAYS_PER_YEAR
        return (ann_ret - self.rf) / dd

    @staticmethod
    def beta(stock_returns: pd.Series, market_returns: pd.Series) -> float:
        joined = pd.concat([pd.Series(stock_returns), pd.Series(market_returns)],
                           axis=1, join="inner").dropna()
        if len(joined) < 30:
            return float("nan")
        reg = stats.linregress(joined.iloc[:, 1], joined.iloc[:, 0])
        return float(reg.slope)

    # Portfolio-level VaR / stress test were removed alongside portfolios.
    # The lean platform is per-ticker only.

    def floored_var(self, var_value: float) -> tuple[float, bool]:
        """Apply VAR_FLOOR to a single-name daily VaR. Returns (value, floored?)."""
        if var_value < VAR_FLOOR:
            return VAR_FLOOR, True
        return var_value, False

    @staticmethod
    def count_var_exceedances(returns: pd.Series, var_95_1d: float) -> int:
        """Days in the series where the realized loss exceeded the 1-day VaR_95."""
        r = pd.Series(returns).dropna().to_numpy()
        return int((r < -abs(var_95_1d)).sum())
