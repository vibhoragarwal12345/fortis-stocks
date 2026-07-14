"""Run card — every backtest run emits a self-describing artifact.

Pattern absorbed from HKUDS/Vibe-Trading run cards: no metric leaves this
engine without its provenance (code SHA, config hash, universe source,
window) and an explicit caveats section stapled to it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports" / "backtest_v2"


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=Path(__file__).parent).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _cfg_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]


def write_run_card(config: dict, summary: dict, fidelity: dict,
                   validation: dict, factor_health: dict,
                   caveats: list[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    card = {
        "run_card_version": 1,
        "generated_at": now.isoformat(),
        "code_git_sha": _git_sha(),
        "config": config,
        "config_hash": _cfg_hash(config),
        "summary": summary,
        "fidelity": fidelity,
        "validation": validation,
        "factor_health": factor_health,
        "caveats": caveats,
    }
    json_path = REPORTS_DIR / f"run_{stamp}.json"
    json_path.write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    md_path = REPORTS_DIR / f"run_{stamp}.md"
    md_path.write_text(_markdown(card), encoding="utf-8")
    return json_path


def _markdown(card: dict) -> str:
    c, s = card["config"], card["summary"]
    lines = [
        f"# Backtest v2 Run Card — {c.get('start')} → {c.get('end')}",
        "",
        f"- Generated: {card['generated_at']}  ·  Code: `{card['code_git_sha']}`  ·  Config: `{card['config_hash']}`",
        f"- Universe: {c.get('universe_provenance')} ({c.get('universe_size')} tickers)  ·  Top-N: {c.get('top_n')}  ·  Entry: next close",
        "",
        "## Headline (replayed funnel picks vs SPY)",
        "",
        "| horizon | n | avg return | avg alpha | median alpha | beat SPY |",
        "|---|---|---|---|---|---|",
    ]
    for h in (1, 5, 20):
        d = s.get(f"h{h}d", {})
        if d.get("n"):
            lines.append(f"| {h}d | {d['n']} | {d['avg_return_pct']:+.2f}% | "
                         f"{d['avg_alpha_pct']:+.2f}% | {d['median_alpha_pct']:+.2f}% | "
                         f"{d['beat_benchmark_pct']:.0f}% |")
        else:
            lines.append(f"| {h}d | 0 | — | — | — | — (not matured) |")

    f = card["fidelity"]
    lines += ["", "## Fidelity (replay vs live picks)", ""]
    if "error" in f:
        lines.append(f"- {f['error']}")
    else:
        lines.append(f"- **{f['avg_replay_in_live_pct']}% of replayed picks were also "
                     f"surfaced by production** (avg across {f['days_compared']} days; "
                     f"v1 scored ~2% on the equivalent check)")
        lines.append(f"- Replay covered {f['avg_overlap_pct_of_live']}% of the full live "
                     f"focus list (which accumulates 2-3 scans/day)")
        lines.append(f"- {f['note']}")

    v = card["validation"]
    lines += ["", "## Statistical validation", ""]
    mc = v.get("monte_carlo", {})
    if "p_value_sharpe" in mc:
        lines.append(f"- Monte Carlo permutation: p(Sharpe) = {mc['p_value_sharpe']} "
                     f"(actual {mc.get('actual_sharpe')}, {mc.get('n_picks')} picks)")
    bs = v.get("bootstrap", {})
    if "observed_sharpe" in bs:
        lines.append(f"- Bootstrap Sharpe {bs['observed_sharpe']} "
                     f"[{bs['ci_lower']}, {bs['ci_upper']}] @95% · P(Sharpe>0) = {bs['prob_positive']}")
    wf = v.get("walk_forward", {})
    if "per_window" in wf:
        lines.append(f"- Walk-forward: {wf['positive_windows']} windows positive, "
                     f"Sharpe σ across windows = {wf['sharpe_std_across_windows']}")

    lines += ["", "## Factor health (daily Spearman IC vs 5d alpha)", "",
              "| factor | state | mean IC | IC>0 ratio | t | days |",
              "|---|---|---|---|---|---|"]
    for name, d in card["factor_health"].items():
        if d.get("state") == "INSUFFICIENT_DATA":
            lines.append(f"| {name} | INSUFFICIENT_DATA | — | — | — | {d['n_days']} |")
        else:
            lines.append(f"| {name} | **{d['state']}** | {d['mean_ic']:+.4f} | "
                         f"{d['ic_positive_ratio']:.2f} | {d['t_stat']:+.2f} | {d['n_days']} |")

    lines += ["", "## Caveats", ""]
    lines += [f"- {c}" for c in card["caveats"]]
    lines.append("")
    return "\n".join(lines)
