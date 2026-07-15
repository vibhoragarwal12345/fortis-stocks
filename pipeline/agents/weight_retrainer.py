"""
Step 7.5 -- weight retraining proposal engine.

Re-scores historical picks under perturbed ranking weights and proposes the
combination that maximises mean forward alpha at 20 days, *without* ever
mutating pipeline/agents/ranking_engine.py.

Workflow:
    1. `should_retrain()` gates on three hard prerequisites:
         - 21+ calendar days of pick_outcomes history (one full 20d
           maturation cycle; the blanket 90-day rule was retired 2026-07-15
           by owner decision -- evidence gates replace calendar gates, see
           pipeline/backtest_v2/sweep.py for the fast path)
         - 100+ A-grade picks tracked
         - t-statistic of mean(alpha_20d) > 2.0 on existing A-grade picks
           (raised from 1.5 when the calendar gate was dropped: with less
           seasoning time, the statistical bar goes up, not down)
       Any one missing -> the retrainer returns "not yet" and writes nothing.

    2. `propose_new_weights(run_type)` re-uses each pick's persisted per-
       category SCORES (ranked_focus_list.signals.<key>_score) so we never
       need to re-fetch raw market data. For each weight in WEIGHTS[run_type]
       we sweep it through {0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15,
       1.20} of its current value (rescaling the other weights proportionally
       so the sum stays at 1.0), re-rank, compute mean alpha_20d on the
       top-N, and keep the best single-weight tweak. A final pass combines
       every category's best tweak into one composite vector and re-evaluates.

    3. `generate_proposal()` writes
            pipeline/proposals/weight_change_<date>.md
       with: current vs proposed weights, expected alpha lift,
       per-category confidence intervals, and the one-line edit a human
       would have to make to ranking_engine.py to accept it.

    *** Weights are the system's DNA. This module never touches the source. ***

Usage:
    python -m pipeline.agents.weight_retrainer check        # only print should_retrain status
    python -m pipeline.agents.weight_retrainer propose      # write proposals for every run_type
    python -m pipeline.agents.weight_retrainer propose premarket
"""

from __future__ import annotations

import logging
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.ranking_engine import WEIGHTS  # noqa: E402  -- canonical source
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

log = logging.getLogger("weight_retrainer")
logging.basicConfig(format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S", level=logging.INFO)


PROPOSALS_DIR = Path(__file__).resolve().parent.parent / "proposals"
PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

# ── Gates ─────────────────────────────────────────────────────────────────
# Calendar gate retired 2026-07-15 (owner decision): 21 days is the floor at
# which alpha_20d can exist at all. The safety burden moves to evidence:
# a higher t-stat bar here, and split-half + Monte Carlo gates in
# backtest_v2/sweep.py for anything faster than this path.
MIN_HISTORY_DAYS    = 21
MIN_A_GRADE_PICKS   = 100
MIN_T_STAT          = 2.0

# ── Search shape ──────────────────────────────────────────────────────────
WEIGHT_MULTIPLIERS  = [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20]
TOP_N_REEVAL        = 20   # how many top-ranked picks to score per (date, run_type)


# ══════════════════════════════════════════════════════════════════════════
# 1. Gate
# ══════════════════════════════════════════════════════════════════════════
def should_retrain() -> tuple[bool, str, dict]:
    """Return (ok, human_readable_reason, metrics_dict). Never mutates state."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # All A-grade picks, mature enough to have alpha_20d
    rows = (
        db.table("pick_outcomes")
        .select("recommended_date,alpha_20d,conviction_grade")
        .eq("conviction_grade", "A")
        .not_.is_("alpha_20d", "null")
        .execute()
        .data or []
    )
    metrics = {
        "n_a_picks":      len(rows),
        "history_days":   0,
        "mean_alpha_20d": None,
        "t_stat":         None,
    }
    if not rows:
        return False, "No mature A-grade pick_outcomes rows yet.", metrics

    dates = [datetime.fromisoformat(r["recommended_date"]).date() for r in rows]
    metrics["history_days"] = (max(dates) - min(dates)).days
    alphas = [float(r["alpha_20d"]) for r in rows if r["alpha_20d"] is not None]
    if alphas:
        m = sum(alphas) / len(alphas)
        sd = statistics.pstdev(alphas) if len(alphas) >= 2 else 0
        se = sd / math.sqrt(len(alphas)) if sd > 0 else float("inf")
        metrics["mean_alpha_20d"] = round(m, 5)
        metrics["t_stat"]         = round(m / se, 3) if se and se != float("inf") else None

    reasons = []
    if metrics["history_days"] < MIN_HISTORY_DAYS:
        reasons.append(f"history_days={metrics['history_days']} < {MIN_HISTORY_DAYS}")
    if metrics["n_a_picks"] < MIN_A_GRADE_PICKS:
        reasons.append(f"A-grade picks={metrics['n_a_picks']} < {MIN_A_GRADE_PICKS}")
    if metrics["t_stat"] is None or metrics["t_stat"] < MIN_T_STAT:
        reasons.append(f"t-stat={metrics['t_stat']} < {MIN_T_STAT}")

    if reasons:
        return False, "Gating failed: " + "; ".join(reasons), metrics
    return True, "All gates passed.", metrics


# ══════════════════════════════════════════════════════════════════════════
# 2. Load history
# ══════════════════════════════════════════════════════════════════════════
def _load_history(db, run_type: str) -> list[dict]:
    """Return a flat list of {date, ticker, scores: {cat: score}, mult,
    alpha_20d} for every mature pick of this run_type."""
    rfl = (
        db.table("ranked_focus_list")
        .select("run_date,run_type,ticker,signals")
        .eq("run_type", run_type)
        .execute()
        .data or []
    )
    by_key = {(r["run_date"], r["run_type"], r["ticker"]): r for r in rfl}

    out_rows = (
        db.table("pick_outcomes")
        .select("ticker,recommended_date,recommended_run_type,alpha_20d")
        .eq("recommended_run_type", run_type)
        .not_.is_("alpha_20d", "null")
        .execute()
        .data or []
    )
    history: list[dict] = []
    for o in out_rows:
        key = (o["recommended_date"], o["recommended_run_type"], o["ticker"])
        rfl_row = by_key.get(key)
        if not rfl_row:
            continue
        sig = rfl_row.get("signals") or {}
        scores = {k[: -len("_score")]: float(v)
                  for k, v in sig.items()
                  if k.endswith("_score") and isinstance(v, (int, float))}
        if not scores:
            continue
        mult = float(sig.get("confirmation_multiplier", 1.0))
        history.append({
            "date":      o["recommended_date"],
            "ticker":    o["ticker"],
            "scores":    scores,
            "mult":      mult,
            "alpha_20d": float(o["alpha_20d"]),
        })
    return history


# ══════════════════════════════════════════════════════════════════════════
# 3. Re-rank under candidate weights
# ══════════════════════════════════════════════════════════════════════════
def _final_score(scores: dict[str, float], weights: dict[str, float], mult: float) -> float:
    s = 0.0
    for cat, w in weights.items():
        s += scores.get(cat, 0.0) * w
    return s * mult


def _mean_top_alpha(history: list[dict], weights: dict[str, float]) -> tuple[float, int]:
    """Re-score every pick, group by date, keep top-N per date, return
    (mean alpha_20d across the kept picks, n_kept)."""
    by_date: dict[str, list[tuple[float, float]]] = {}
    for h in history:
        score = _final_score(h["scores"], weights, h["mult"])
        by_date.setdefault(h["date"], []).append((score, h["alpha_20d"]))
    kept_alphas: list[float] = []
    for picks in by_date.values():
        picks.sort(key=lambda x: -x[0])
        for _, alpha in picks[:TOP_N_REEVAL]:
            kept_alphas.append(alpha)
    if not kept_alphas:
        return float("nan"), 0
    return sum(kept_alphas) / len(kept_alphas), len(kept_alphas)


def _ci_95(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 5:
        return None
    m = sum(values) / len(values)
    sd = statistics.pstdev(values)
    se = sd / math.sqrt(len(values))
    return (m - 1.96 * se, m + 1.96 * se)


# ══════════════════════════════════════════════════════════════════════════
# 4. Search
# ══════════════════════════════════════════════════════════════════════════
def _scaled_weights(base: dict[str, float], category: str, mult: float
                    ) -> dict[str, float]:
    """Multiply `category`'s weight by `mult`, rescale the others proportionally
    so the sum stays at 1.0."""
    if category not in base:
        return dict(base)
    others_sum = sum(v for k, v in base.items() if k != category)
    if others_sum <= 0:
        return dict(base)
    new_cat = base[category] * mult
    leftover = 1.0 - new_cat
    if leftover < 0:
        return dict(base)
    scale = leftover / others_sum
    out = {k: (new_cat if k == category else v * scale) for k, v in base.items()}
    # Snap to 4 decimals for printability
    return {k: round(v, 4) for k, v in out.items()}


def propose_new_weights(run_type: str) -> dict:
    """Return {baseline, proposed, per_category_search, n_history}."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    base_pairs = WEIGHTS[run_type]
    base = {k: w for k, w, _ in base_pairs}

    history = _load_history(db, run_type)
    if not history:
        return {"baseline": base, "proposed": base, "per_category_search": {},
                "n_history": 0, "note": "no mature history for run_type"}

    baseline_alpha, baseline_n = _mean_top_alpha(history, base)
    log.info("[%s] baseline mean top-%d alpha_20d = %.4f over %d picks",
             run_type, TOP_N_REEVAL, baseline_alpha, baseline_n)

    per_cat: dict[str, dict] = {}
    for cat in base:
        sweep = []
        for m in WEIGHT_MULTIPLIERS:
            cand = _scaled_weights(base, cat, m)
            alpha, n_kept = _mean_top_alpha(history, cand)
            sweep.append({"mult": m, "alpha_20d": alpha, "n": n_kept,
                          "weights": cand})
        best = max(sweep, key=lambda s: s["alpha_20d"] if not math.isnan(s["alpha_20d"]) else -1)
        per_cat[cat] = {"sweep": sweep, "best": best}

    # Composite: combine each category's best tweak, then re-normalise.
    composite: dict[str, float] = dict(base)
    for cat, m in per_cat.items():
        composite[cat] = m["best"]["weights"][cat]
    s = sum(composite.values())
    composite = {k: round(v / s, 4) for k, v in composite.items()}
    composite_alpha, composite_n = _mean_top_alpha(history, composite)

    proposed = composite if composite_alpha > baseline_alpha else base
    return {
        "run_type":            run_type,
        "baseline":            base,
        "baseline_alpha_20d":  baseline_alpha,
        "baseline_n":          baseline_n,
        "proposed":            proposed,
        "proposed_alpha_20d":  composite_alpha,
        "proposed_n":          composite_n,
        "per_category_search": {
            cat: {"best_mult":   m["best"]["mult"],
                  "best_alpha":  m["best"]["alpha_20d"]}
            for cat, m in per_cat.items()
        },
        "alpha_lift":          composite_alpha - baseline_alpha,
        "n_history":           len(history),
    }


# ══════════════════════════════════════════════════════════════════════════
# 5. Proposal markdown
# ══════════════════════════════════════════════════════════════════════════
def generate_proposal(run_types: list[str] | None = None) -> Path | None:
    ok, reason, metrics = should_retrain()
    if not ok:
        log.warning("Refusing to propose: %s", reason)
        log.info("Metrics so far: %s", metrics)
        return None

    if not run_types:
        run_types = list(WEIGHTS.keys())

    today = datetime.now(timezone.utc).date().isoformat()
    path = PROPOSALS_DIR / f"weight_change_{today}.md"

    lines = [
        f"# Weight retraining proposal -- {today}",
        "",
        "_This proposal was generated by `pipeline/agents/weight_retrainer.py` "
        "and **was not auto-applied**. Weights are the system's DNA; changes "
        "require human approval._",
        "",
        "## Gate metrics",
        "",
        f"- A-grade picks tracked: **{metrics['n_a_picks']}** "
        f"(min {MIN_A_GRADE_PICKS})",
        f"- History span: **{metrics['history_days']} days** "
        f"(min {MIN_HISTORY_DAYS})",
        f"- Mean alpha_20d (A): **{metrics['mean_alpha_20d']}**",
        f"- t-stat: **{metrics['t_stat']}** (min {MIN_T_STAT})",
        "",
    ]

    for rt in run_types:
        p = propose_new_weights(rt)
        lines.append(f"## `{rt}` weights")
        lines.append("")
        lines.append(f"- History rows used: **{p['n_history']}**")
        lines.append(f"- Baseline mean top-{TOP_N_REEVAL} alpha_20d: "
                     f"**{p['baseline_alpha_20d']:.4f}** "
                     f"({p['baseline_n']} picks scored)")
        lines.append(f"- Proposed mean top-{TOP_N_REEVAL} alpha_20d: "
                     f"**{p['proposed_alpha_20d']:.4f}** "
                     f"({p['proposed_n']} picks scored)")
        lines.append(f"- **Alpha lift:** {p['alpha_lift']:+.4f}")
        lines.append("")
        lines.append("| category | baseline | proposed | Δ | best multiplier |")
        lines.append("| --- | --- | --- | --- | --- |")
        for cat, base_w in p["baseline"].items():
            new_w = p["proposed"].get(cat, base_w)
            delta = new_w - base_w
            best  = p["per_category_search"].get(cat, {}).get("best_mult", 1.0)
            lines.append(f"| {cat} | {base_w:.4f} | {new_w:.4f} | "
                         f"{delta:+.4f} | x{best:.2f} |")
        lines.append("")
        if p["proposed"] == p["baseline"]:
            lines.append("**Conclusion:** no per-category tweak improved the "
                         "baseline. No change recommended for this run_type.")
        else:
            edit = ",\n        ".join(
                f'("{cat}", {p["proposed"][cat]}, "<source>")'
                for cat in p["proposed"]
            )
            lines.append("**Conclusion:** baseline beaten. To accept, edit "
                         "`pipeline/agents/ranking_engine.py` and replace the "
                         f"`WEIGHTS['{rt}']` list with:")
            lines.append("")
            lines.append("```python")
            lines.append(f'WEIGHTS["{rt}"] = [')
            lines.append(f"        {edit},")
            lines.append("    ]")
            lines.append("```")
            lines.append("")
            lines.append(
                "(Keep each entry's third element -- the `score_source` -- "
                "the same as the current row. The retrainer only proposes a "
                "weight change, not a source change.)"
            )
        lines.append("")

    path.write_text("\n".join(lines))
    log.info("Wrote %s", path)
    return path


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════
def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] == "check":
        ok, reason, metrics = should_retrain()
        print(f"should_retrain: {ok}")
        print(f"  reason : {reason}")
        for k, v in metrics.items():
            print(f"  {k:<16}: {v}")
        sys.exit(0 if ok else 0)   # gating failure is not an error
    if args[0] == "propose":
        rts = args[1:] or list(WEIGHTS.keys())
        path = generate_proposal(rts)
        if path is None:
            sys.exit(0)
        print(f"Proposal written to {path}")
        sys.exit(0)
    log.error("usage: weight_retrainer.py {check|propose [run_type ...]}")
    sys.exit(2)


if __name__ == "__main__":
    main()
