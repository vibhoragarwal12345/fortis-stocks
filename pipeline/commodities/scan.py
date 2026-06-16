"""
Part C: the commodity scan — runs the full Part B model on every registry
commodity, persists one row to Supabase (commodity_scans), and emits the
cross-link signals the stock engine's macro agent consumes.

Cadence: once per weekday (see .github/workflows/commodities_scan.yml),
deliberately NOT the stock engine's 3x cadence — the fundamental inputs are
weekly (EIA Wed, COT Fri) or monthly (WASDE, IMF/FRED metals), so re-running
intraday would re-publish identical fundamentals under fresher timestamps and
burn the Actions-minutes budget the stock scan already presses against.

Persistence is graceful: a failed insert leaves the local JSON artifacts and
exits 0 with the gap reported — a scan is never lost to a DB hiccup, and
nothing is fabricated to fill one.

Usage:
    python -m pipeline.commodities.scan               # all, LLM on, persist
    python -m pipeline.commodities.scan --no-llm      # skip narratives
    python -m pipeline.commodities.scan --local-only  # skip Supabase insert
    python -m pipeline.commodities.scan wti gold      # subset (debug)
"""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commodities import analyze, cross_link  # noqa: E402
from commodities.registry import COMMODITIES  # noqa: E402

log = logging.getLogger("commodities.scan")

DATA_DIR = Path(__file__).resolve().parent / "data" / "analysis"


def _narrative_counts(result: dict) -> tuple[int, int]:
    ok = total = 0
    for entry in result.get("commodities", {}).values():
        for horizon in ("tactical", "structural"):
            total += 1
            if (entry.get(horizon, {}).get("narrative") or {}).get("status") == "OK":
                ok += 1
    return ok, total


def _status(result: dict, ok: int, total: int) -> str:
    """complete = every commodity priced and every narrative published;
    anything less that still produced output is partial (gaps are labeled
    inside the payload, never filled)."""
    techs_ok = all(
        (e.get("layers", {}).get("technicals") or {}).get("status") in ("OK", "NO_DAILY_DATA")
        for e in result.get("commodities", {}).values()
    )
    return "complete" if techs_ok and ok == total else "partial"


def _persist(result: dict, signals: dict, status: str,
             ok: int, total: int) -> bool:
    try:
        from supabase import create_client
        from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        resp = (db.table("commodity_scans")
                  .insert({
                      "status": status,
                      "commodity_count": len(result.get("commodities", {})),
                      "narratives_ok": ok,
                      "narratives_total": total,
                      "payload": result,
                      "cross_link": signals,
                  }).execute())
        rid = resp.data[0]["id"] if resp.data else "?"
        log.info("commodity_scans row stored (id=%s)", rid)
        return True
    except Exception as exc:  # noqa: BLE001 — non-fatal by design
        log.warning("commodity_scans insert failed: %s", exc)
        return False


def _carry_forward_structural(result: dict) -> dict:
    """Tactical (daily) runs reuse the most recent run's STRUCTURAL block per
    commodity, so the long-horizon read refreshes only on the weekly full run
    while the tactical read updates daily -- and NO structural LLM call is
    spent. The 'as_of' timestamp chains back to the full run that wrote it."""
    try:
        from supabase import create_client
        from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        prior = (db.table("commodity_scans").select("scan_time,payload")
                 .neq("status", "failed").order("scan_time", desc=True)
                 .limit(1).execute().data or [])
    except Exception as exc:  # noqa: BLE001 -- non-fatal, leaves the stub
        log.warning("carry-forward: could not load prior structural reads: %s", exc)
        prior = []
    prior_comms = prior[0]["payload"].get("commodities", {}) if prior else {}
    prior_time = prior[0].get("scan_time") if prior else None
    carried = 0
    for e in result.get("commodities", {}).values():
        prev_struct = (prior_comms.get(_key_of(e)) or {}).get("structural")
        if prev_struct and (prev_struct.get("narrative") or {}).get("status") == "OK":
            e["structural"] = {**prev_struct,
                               "as_of": prev_struct.get("as_of") or prior_time}
            carried += 1
    log.info("carried forward structural reads for %d commodity(ies) (as of %s)",
             carried, prior_time)
    return result


def _key_of(entry: dict) -> str:
    """Reverse the display name back to a registry key for prior-scan lookup."""
    name = entry.get("name")
    for k, c in COMMODITIES.items():
        if c["name"] == name:
            return k
    return ""


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    args = sys.argv[1:]
    with_llm = "--no-llm" not in args
    local_only = "--local-only" in args
    keys = [a for a in args if a in COMMODITIES] or None
    # --mode tactical : daily pre-market run, short-term read only (structural
    #                   carried forward from the last weekly full run).
    # --mode full     : weekly Monday run, regenerate everything (default).
    mode = "full"
    if "--mode" in args:
        i = args.index("--mode")
        if i + 1 < len(args) and args[i + 1] in ("tactical", "full"):
            mode = args[i + 1]

    result = analyze.run(keys, with_llm=with_llm, mode=mode)
    if mode == "tactical":
        result = _carry_forward_structural(result)
    signals = cross_link.build_signals(result)
    ok, total = _narrative_counts(result)
    status = _status(result, ok, total)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "latest_analysis.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    (DATA_DIR / "latest_cross_link.json").write_text(
        json.dumps(signals, indent=2, default=str), encoding="utf-8")

    persisted = local_only or _persist(result, signals, status, ok, total)

    bar = "=" * 70
    print(f"\n{bar}\nCOMMODITY SCAN [{mode.upper()}] — {result['generated_at']}  "
          f"[{status.upper()}]\n{bar}")
    for k, e in result["commodities"].items():
        price = e.get("price") or {}
        tac = (e.get("tactical", {}).get("narrative") or {}).get("status")
        struct = (e.get("structural", {}).get("narrative") or {}).get("status")
        layers = e.get("layers", {})
        print(f"  {k:<10} {str(price.get('close')):>10}  "
              f"sd={ (layers.get('supply_demand') or {}).get('classification', '?'):<10} "
              f"curve={ (layers.get('curve') or {}).get('structure', '?'):<15} "
              f"narratives: tactical={tac} structural={struct}")
    print(f"\n  narratives published: {ok}/{total}")
    print(f"  cross-link: {cross_link.summarize(signals)}")
    print(f"  persisted to Supabase: {persisted if not local_only else 'skipped (--local-only)'}")
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
