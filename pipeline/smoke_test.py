"""
Smoke Tests
Import every agent / processor / quant module and verify the key entry points
exist. Cheap enough to run on every commit; catches the dumb regressions
(missing import, renamed function, syntax error) that would otherwise only
surface deep inside a long pipeline run.

Usage:
    python pipeline/smoke_test.py
"""

import importlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULES = [
    # data agents
    ("agents.market_data",         ["run", "_download_batch"]),
    ("agents.news_harvester",      []),
    ("agents.news_resolver",       ["run"]),
    ("agents.sec_watcher",         []),
    ("agents.crowd_intelligence",  []),
    ("agents.macro_agent",         ["run"]),
    ("agents.options_flow",        ["run"]),
    # processing
    ("agents.anomaly_detector",    ["run"]),
    ("agents.pattern_matcher",     ["run"]),
    ("agents.cross_validator",     ["run"]),
    ("agents.ranking_engine",      ["run", "WEIGHTS"]),
    ("agents.catalyst_agent",      ["run"]),
    ("agents.debate_synthesizer",  ["run"]),
    ("agents.critic_agent",        ["run"]),
    ("agents.conviction_grader",   ["run"]),
    ("processors.sentiment_scorer", ["score_text", "score_many", "get_backend"]),
    ("processors.fingerprint",     ["build_feature_frame", "embed", "fingerprint_text"]),
    # quant desk
    ("quant.utils",                ["fetch_atm_iv", "get_risk_free_rate",
                                    "black_scholes_call_price",
                                    "implied_volatility_from_price"]),
    ("quant.monte_carlo",          ["MonteCarloSimulator"]),
    ("quant.var_cvar",             ["RiskModel"]),
    ("quant.garch",                ["GARCHModel"]),
    ("quant.fama_french",          ["FamaFrench5Factor", "FACTORS"]),
    ("quant.dcf",                  ["DCFValuation"]),
    ("quant.run_garch",            ["run"]),
    ("quant.run_factor_dcf",       ["run"]),
]


def _import_one(modpath: str, required_attrs: list[str]):
    """Return (status, detail). status in {'OK','MISSING_ATTR','IMPORT_ERROR'}."""
    try:
        m = importlib.import_module(modpath)
    except Exception as exc:
        return "IMPORT_ERROR", f"{type(exc).__name__}: {str(exc)[:120]}"
    missing = [a for a in required_attrs if not hasattr(m, a)]
    if missing:
        return "MISSING_ATTR", f"missing {missing}"
    return "OK", ""


def main() -> int:
    bar = "=" * 78
    print(f"{bar}\nSMOKE TESTS -- {len(MODULES)} modules\n{bar}")
    start = time.time()
    results = []
    for modpath, attrs in MODULES:
        status, detail = _import_one(modpath, attrs)
        results.append((modpath, status, detail))
        flag = "  OK   " if status == "OK" else " FAIL  "
        line = f"{flag}{modpath:<32}"
        if detail:
            line += f"  {detail}"
        print(line)

    elapsed = time.time() - start
    ok = sum(1 for _, s, _ in results if s == "OK")
    print(f"\n{bar}\n{ok}/{len(results)} clean in {elapsed:.1f}s\n{bar}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
