"""
Quantitative Models Desk -- Model 5: Black-Litterman portfolio optimization.

Markowitz mean-variance optimization is famously unstable -- tiny changes in
expected returns swing the weights wildly. Black-Litterman (Black & Litterman,
Goldman Sachs, 1990) fixes this by:
  1. starting from MARKET EQUILIBRIUM (returns implied by market-cap weights),
  2. letting the manager express VIEWS with explicit confidence,
  3. blending the two in a Bayesian posterior,
  4. optimizing the posterior -> stable, intuitive weights.

Here the views are the system's A/B-grade picks; the confidence is the
conviction grade. All maths is deterministic (numpy / scipy / cvxpy /
scikit-learn Ledoit-Wolf). The LLM only narrates the computed result.

The Bayesian posterior is implemented directly in numpy (the canonical
closed form) rather than via riskfolio-lib, for transparency and control;
the constrained optimization uses cvxpy.
"""

import logging
import sys
from pathlib import Path

import cvxpy as cp
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROQ_API_KEY  # noqa: E402
from quant.models_config import TRADING_DAYS_PER_YEAR, VAR_LOOKBACK_DAYS  # noqa: E402
from quant.utils import safe_log_returns  # noqa: E402

log = logging.getLogger(__name__)

RISK_AVERSION = 2.5        # lambda -- equilibrium risk-aversion
TAU = 0.025               # equilibrium dominates unless a view is confident
MAX_POSITION = 0.05       # long-only, 5% single-name cap (relaxed if universe small)
MAX_SECTOR = 0.30
MAX_TURNOVER = 0.25       # one-way turnover budget
TXN_COST_BPS = 5          # round-trip transaction cost assumption
GROQ_MODEL = "llama-3.3-70b-versatile"


class BlackLittermanOptimizer:
    """Equilibrium returns -> Bayesian views -> constrained optimal weights."""

    def __init__(self):
        self.tickers: list[str] = []
        self.sigma: np.ndarray | None = None       # annualized covariance
        self.w_mkt: np.ndarray | None = None
        self.pi: np.ndarray | None = None

    # ──────────────────────────────────────────────────────────────────────────
    def compute_market_equilibrium(self, universe_tickers: list[str],
                                   market_caps: dict, price_data: dict) -> np.ndarray:
        """Implied equilibrium returns: pi = lambda * Sigma * w_market.

        Covariance uses Ledoit-Wolf shrinkage -- a raw sample covariance over
        ~25 names is too noisy to invert reliably.
        """
        rets = {}
        for t in universe_tickers:
            p = price_data.get(t)
            if p is not None and len(p) > 60:
                rets[t] = safe_log_returns(p.tail(VAR_LOOKBACK_DAYS))
        mat = pd.DataFrame(rets).dropna()
        if mat.shape[1] < 2:
            raise ValueError("need >=2 tickers with usable return history")
        self.tickers = list(mat.columns)

        # Ledoit-Wolf shrinkage covariance (daily -> annualized).
        lw = LedoitWolf().fit(mat.to_numpy())
        self.sigma = lw.covariance_ * TRADING_DAYS_PER_YEAR

        caps = np.array([max(market_caps.get(t, 0.0), 0.0) for t in self.tickers])
        if caps.sum() <= 0:                         # no cap data -> equal weight
            caps = np.ones(len(self.tickers))
        self.w_mkt = caps / caps.sum()
        self.pi = RISK_AVERSION * self.sigma @ self.w_mkt
        log.info("Equilibrium: %d assets, market caps cover %d, "
                 "annualized vol range %.0f%%-%.0f%%", len(self.tickers),
                 int((caps > 0).sum()), np.sqrt(np.diag(self.sigma)).min() * 100,
                 np.sqrt(np.diag(self.sigma)).max() * 100)
        return self.pi

    # ──────────────────────────────────────────────────────────────────────────
    def specify_views(self, picks_with_grades: list[dict]) -> dict:
        """Build the view matrix P, view returns Q and uncertainty Omega.

        A-grade picks get a full-magnitude absolute view (the asset will earn
        its equilibrium return plus an uplift); B-grade picks a half-magnitude
        view; C/D picks contribute no view. View magnitude is data-driven
        (DCF upside, pattern-analog return, validated strength); Omega encodes
        confidence -- higher conviction -> smaller Omega -> the view bites more.
        """
        idx = {t: i for i, t in enumerate(self.tickers)}
        rows, q, conf, applied = [], [], [], []
        for p in picks_with_grades:
            t = p.get("ticker")
            grade = (p.get("conviction_grade") or "C").upper()
            if t not in idx or grade not in ("A", "B"):
                continue
            # View magnitude (annualized excess), data-driven, conservative.
            mag = 0.0
            dcf_up = p.get("dcf_upside")
            if dcf_up is not None and 0 < dcf_up <= 0.50:
                mag = max(mag, min(dcf_up, 0.20))    # cap absurd DCF upside
            pat = p.get("pattern_5d_return")
            if pat is not None:
                mag = max(mag, min(abs(pat) / 100.0 * 4, 0.15))  # 5d -> ~rough annual
            if mag == 0.0:
                mag = 0.06                           # default modest view
            scale = 1.0 if grade == "A" else 0.5
            view_ret = self.pi[idx[t]] + mag * scale

            row = np.zeros(len(self.tickers))
            row[idx[t]] = 1.0
            rows.append(row)
            q.append(view_ret)
            # Confidence: A-grade -> tight Omega, B-grade -> looser.
            conf.append(0.65 if grade == "A" else 0.35)
            applied.append({"ticker": t, "grade": grade,
                            "view_return": round(float(view_ret), 4),
                            "magnitude": round(mag * scale, 4),
                            "confidence": conf[-1]})
        if not rows:
            return {"P": None, "Q": None, "Omega": None, "applied": []}

        P = np.array(rows)
        Q = np.array(q)
        # Omega: diag of P(tau Sigma)P', divided by confidence -> tighter Omega.
        base = np.diag(P @ (TAU * self.sigma) @ P.T)
        Omega = np.diag(base / np.array(conf))
        return {"P": P, "Q": Q, "Omega": Omega, "applied": applied}

    # ──────────────────────────────────────────────────────────────────────────
    def bl_posterior(self, pi: np.ndarray, sigma: np.ndarray,
                     views: dict) -> np.ndarray:
        """Black-Litterman posterior expected returns (closed form)."""
        if views.get("P") is None:
            return pi.copy()                         # no views -> equilibrium
        P, Q, Omega = views["P"], views["Q"], views["Omega"]
        tau_sigma_inv = np.linalg.inv(TAU * sigma)
        omega_inv = np.linalg.inv(Omega)
        a = tau_sigma_inv + P.T @ omega_inv @ P
        b = tau_sigma_inv @ pi + P.T @ omega_inv @ Q
        return np.linalg.solve(a, b)

    # ──────────────────────────────────────────────────────────────────────────
    def optimize(self, posterior_returns: np.ndarray, sigma: np.ndarray,
                 current_weights: np.ndarray,
                 sectors: dict | None = None) -> dict:
        """Constrained mean-variance optimization of the posterior."""
        n = len(self.tickers)
        # The 5% cap is infeasible if the universe is too small to sum to 1.0;
        # relax it just enough to stay feasible, and flag that it was relaxed.
        max_w = MAX_POSITION
        relaxed = False
        if n * MAX_POSITION < 1.0:
            max_w = min(0.25, 1.2 / n)
            relaxed = True

        w = cp.Variable(n)
        objective = cp.Maximize(posterior_returns @ w
                                - (RISK_AVERSION / 2) * cp.quad_form(w, cp.psd_wrap(sigma)))
        cons = [cp.sum(w) == 1, w >= 0, w <= max_w,
                cp.sum(cp.abs(w - current_weights)) <= 2 * MAX_TURNOVER]
        if sectors:
            for sec in set(sectors.values()):
                mask = np.array([1.0 if sectors.get(t) == sec else 0.0
                                 for t in self.tickers])
                if mask.sum() > 0:
                    cons.append(mask @ w <= MAX_SECTOR)
        prob = cp.Problem(objective, cons)
        try:
            prob.solve()
        except Exception as exc:
            return {"weights": None, "quality": "infeasible", "reason": str(exc)}

        if prob.status not in ("optimal", "optimal_inaccurate") or w.value is None:
            return {"weights": None, "quality": "infeasible",
                    "reason": f"solver status {prob.status}"}
        weights = np.clip(np.array(w.value), 0, None)
        weights = weights / weights.sum()
        quality = ("feasible_but_constrained" if relaxed
                   or weights.max() > 0.20 else "optimal_solution")
        return {"weights": weights, "quality": quality,
                "max_weight_used": max_w, "relaxed": relaxed}

    # ──────────────────────────────────────────────────────────────────────────
    def compare_portfolios(self, current_weights: np.ndarray,
                           optimal_weights: np.ndarray, posterior_returns: np.ndarray,
                           sigma: np.ndarray, rf: float = 0.04) -> dict:
        """Trades, costs and the expected return / risk / Sharpe deltas."""
        def _stats(w):
            ret = float(posterior_returns @ w)
            vol = float(np.sqrt(w @ sigma @ w))
            sharpe = (ret - rf) / vol if vol > 0 else 0.0
            return ret, vol, sharpe

        cur_ret, cur_vol, cur_sharpe = _stats(current_weights)
        opt_ret, opt_vol, opt_sharpe = _stats(optimal_weights)

        trades = []
        for i, t in enumerate(self.tickers):
            delta = optimal_weights[i] - current_weights[i]
            if abs(delta) > 0.005:                   # ignore <0.5% noise
                trades.append({"ticker": t,
                               "current": round(float(current_weights[i]), 4),
                               "target": round(float(optimal_weights[i]), 4),
                               "delta": round(float(delta), 4),
                               "action": "buy" if delta > 0 else "trim"})
        turnover = float(np.abs(optimal_weights - current_weights).sum()) / 2
        txn_cost = turnover * 2 * (TXN_COST_BPS / 10000.0)   # round-trip
        net_benefit = (opt_ret - cur_ret) - txn_cost

        return {
            "trades": sorted(trades, key=lambda x: abs(x["delta"]), reverse=True),
            "expected_return_current": round(cur_ret, 4),
            "expected_return_optimal": round(opt_ret, 4),
            "expected_volatility_current": round(cur_vol, 4),
            "expected_volatility_optimal": round(opt_vol, 4),
            "sharpe_current": round(cur_sharpe, 3),
            "sharpe_optimal": round(opt_sharpe, 3),
            "turnover": round(turnover, 4),
            "transaction_cost_estimate": round(txn_cost, 5),
            "net_benefit": round(net_benefit, 5),
            "recommend_trade": net_benefit > 0,
        }

    # ──────────────────────────────────────────────────────────────────────────
    def explain_recommendations(self, comparison: dict, views_applied: list) -> str:
        """Plain-English narration of the COMPUTED optimization result (Groq)."""
        c = comparison
        top = c["trades"][:3]
        trade_txt = "; ".join(f"{t['action']} {t['ticker']} "
                              f"{t['current']*100:.1f}%->{t['target']*100:.1f}%"
                              for t in top) or "no material trades"
        if GROQ_API_KEY:
            try:
                from groq import Groq
                system = ("You explain a pre-computed portfolio optimization in "
                          "plain English. Restate ONLY the numbers given -- never "
                          "invent or recompute. Two or three sentences.")
                user = (f"Black-Litterman result: top trades = {trade_txt}. "
                        f"Sharpe {c['sharpe_current']} -> {c['sharpe_optimal']}; "
                        f"expected return {c['expected_return_current']*100:.1f}% -> "
                        f"{c['expected_return_optimal']*100:.1f}%; volatility "
                        f"{c['expected_volatility_current']*100:.1f}% -> "
                        f"{c['expected_volatility_optimal']*100:.1f}%; turnover "
                        f"{c['turnover']*100:.0f}%; transaction cost "
                        f"{c['transaction_cost_estimate']*100:.2f}%; net benefit "
                        f"{c['net_benefit']*100:.2f}%; "
                        f"{len(views_applied)} conviction views applied. "
                        f"Write the interpretation.")
                resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.3, max_tokens=200)
                txt = (resp.choices[0].message.content or "").strip()
                if txt:
                    return txt
            except Exception as exc:
                log.warning("Groq explain failed: %s", exc)

        verdict = ("recommends trading" if c["recommend_trade"]
                   else "recommends NO trade -- costs exceed the benefit")
        return (f"Black-Litterman {verdict}. Suggested: {trade_txt}. Sharpe moves "
                f"{c['sharpe_current']} -> {c['sharpe_optimal']} for "
                f"{c['turnover']*100:.0f}% turnover at "
                f"{c['transaction_cost_estimate']*100:.2f}% cost.")
