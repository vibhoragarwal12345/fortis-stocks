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

    # ── portfolio ─────────────────────────────────────────────────────────────
    def portfolio_var(self, holdings: dict, lookback_days: int = 504,
                      returns_by_ticker: dict | None = None) -> dict:
        """Portfolio VaR/CVaR plus each position's component (marginal) VaR."""
        tickers = list(holdings)
        rets = {}
        for t in tickers:
            try:
                series = (returns_by_ticker.get(t) if returns_by_ticker
                          else safe_log_returns(fetch_price_history(t, lookback_days)))
                if series is not None and len(series) > 30:
                    rets[t] = pd.Series(series)
            except Exception as exc:
                log.warning("portfolio_var: %s skipped (%s)", t, exc)
        if not rets:
            return {}

        mat = pd.DataFrame(rets).dropna()
        used = list(mat.columns)
        w = np.array([holdings[t] for t in used], dtype=float)
        w = w / w.sum()                                  # renormalize to used set

        port_ret = mat.to_numpy() @ w
        port_series = pd.Series(port_ret, index=mat.index)

        cov = mat.cov().to_numpy()
        port_var_pct = float(w @ cov @ w)
        sigma_p = np.sqrt(port_var_pct)
        hist95 = self.historical_var(port_series, 0.95)
        # component VaR: contributions sum to the portfolio VaR
        marginal = {}
        if sigma_p > 0:
            contrib = w * (cov @ w) / port_var_pct       # fractions, sum to 1
            for t, frac in zip(used, contrib):
                marginal[t] = round(float(frac * hist95), 6)

        return {
            "tickers_used":            used,
            "weights_used":            {t: round(float(x), 4) for t, x in zip(used, w)},
            "portfolio_var_95":        round(hist95, 6),
            "portfolio_var_99":        round(self.historical_var(port_series, 0.99), 6),
            "portfolio_var_95_param":  round(self.parametric_var(port_series, 0.95), 6),
            "portfolio_cvar_95":       round(self.cvar(port_series, 0.95), 6),
            "annual_volatility":       round(self.annual_volatility(port_series), 6),
            "per_position_marginal_var": marginal,
            "diversification_ratio":   self._diversification_ratio(mat, w),
        }

    @staticmethod
    def _diversification_ratio(returns_matrix: pd.DataFrame, weights: np.ndarray) -> float:
        """Weighted-average single-name vol / portfolio vol. 1 = no benefit."""
        stds = returns_matrix.std(ddof=1).to_numpy()
        cov = returns_matrix.cov().to_numpy()
        port_vol = np.sqrt(weights @ cov @ weights)
        if port_vol == 0:
            return 1.0
        return round(float((weights @ stds) / port_vol), 4)

    def stress_test(self, holdings: dict,
                    scenarios: list | None = None) -> dict:
        """Replay exact historical crisis windows against the current holdings.

        Tickers without data in a window are dropped and weights renormalized
        across the survivors; the survivor count is reported per scenario.
        """
        scenarios = scenarios or list(STRESS_SCENARIOS)
        out: dict = {}
        for name in scenarios:
            if name not in STRESS_SCENARIOS:
                continue
            start, end = STRESS_SCENARIOS[name]
            drawdowns, weights, used = [], [], []
            for t, wt in holdings.items():
                try:
                    df = yf.download(t, start=start, end=end, interval="1d",
                                     auto_adjust=True, progress=False)
                    close = df["Close"].dropna()
                    if isinstance(close, pd.DataFrame):
                        close = close.iloc[:, 0]
                    if len(close) < 5:
                        continue
                    drawdowns.append(self.max_drawdown(close))
                    weights.append(wt)
                    used.append(t)
                except Exception:
                    continue
            if used:
                w = np.array(weights) / sum(weights)
                est = float(np.array(drawdowns) @ w)
                out[name] = {"estimated_drawdown_pct": round(est * 100, 2),
                             "tickers_covered": len(used),
                             "tickers_total": len(holdings)}
            else:
                out[name] = {"estimated_drawdown_pct": None,
                             "tickers_covered": 0, "tickers_total": len(holdings)}
        return out

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
