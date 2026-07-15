"""Research hypothesis registry — the experiment logbook.

Lean re-implementation of HKUDS/Vibe-Trading's hypothesis registry (theirs:
agent/src/hypotheses/registry.py, MIT): local JSON, deterministic, no LLM.
Every strategy experiment gets a durable record — thesis, status, evidence
artifacts — so decisions stay auditable after the chat that made them is
gone. The registry file is COMMITTED (it's the lab notebook, not cache).

Statuses: proposed → testing → adopted | rejected | monitoring
(monitoring = wired into production at weight 0, measured nightly).

    python -m pipeline.backtest_v2.hypotheses list
    python -m pipeline.backtest_v2.hypotheses add "title" --thesis "…"
    python -m pipeline.backtest_v2.hypotheses set-status <id> adopted --note "…"
    python -m pipeline.backtest_v2.hypotheses link <id> pipeline/reports/backtest_v2/sweep_x.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "hypotheses.json"
STATUSES = ("proposed", "testing", "adopted", "rejected", "monitoring")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> list[dict]:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return []


def _save(items: list[dict]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")


def _new_id(title: str) -> str:
    return "hyp-" + hashlib.sha256(f"{title}{_now()}".encode()).hexdigest()[:8]


def add(title: str, thesis: str, status: str = "proposed") -> dict:
    items = _load()
    h = {"id": _new_id(title), "title": title, "thesis": thesis,
         "status": status, "created_at": _now(), "updated_at": _now(),
         "evidence": [], "history": [{"at": _now(), "status": status, "note": "created"}]}
    items.append(h)
    _save(items)
    return h


def set_status(hyp_id: str, status: str, note: str = "") -> dict:
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {STATUSES}")
    items = _load()
    for h in items:
        if h["id"] == hyp_id:
            h["status"] = status
            h["updated_at"] = _now()
            h["history"].append({"at": _now(), "status": status, "note": note})
            _save(items)
            return h
    raise SystemExit(f"no hypothesis {hyp_id}")


def link(hyp_id: str, artifact: str, note: str = "") -> dict:
    items = _load()
    for h in items:
        if h["id"] == hyp_id:
            h["evidence"].append({"at": _now(), "artifact": artifact, "note": note})
            h["updated_at"] = _now()
            _save(items)
            return h
    raise SystemExit(f"no hypothesis {hyp_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_add = sub.add_parser("add")
    p_add.add_argument("title")
    p_add.add_argument("--thesis", required=True)
    p_add.add_argument("--status", default="proposed", choices=STATUSES)
    p_st = sub.add_parser("set-status")
    p_st.add_argument("id")
    p_st.add_argument("status", choices=STATUSES)
    p_st.add_argument("--note", default="")
    p_ln = sub.add_parser("link")
    p_ln.add_argument("id")
    p_ln.add_argument("artifact")
    p_ln.add_argument("--note", default="")
    a = ap.parse_args()

    if a.cmd == "list":
        for h in _load():
            ev = f" [{len(h['evidence'])} artifacts]" if h["evidence"] else ""
            print(f"{h['id']}  {h['status']:<10}  {h['title']}{ev}")
    elif a.cmd == "add":
        h = add(a.title, a.thesis, a.status)
        print(f"created {h['id']}")
    elif a.cmd == "set-status":
        h = set_status(a.id, a.status, a.note)
        print(f"{h['id']} -> {h['status']}")
    elif a.cmd == "link":
        h = link(a.id, a.artifact, a.note)
        print(f"{h['id']} evidence: {len(h['evidence'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
