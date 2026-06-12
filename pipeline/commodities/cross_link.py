"""
Part C cross-link: the ONLY interface between the commodity pipeline and the
stock engine. Three deterministic signals, computed from already-verified
analysis layers — no LLM, no new fetches:

    copper trend -> industrial / growth read
    gold   trend -> risk-on / risk-off read
    oil    trend -> inflation-impulse read

Design rules (keep it an interface, not a tangle):
  * build_signals() is PURE: analyze.run() output in, signals dict out.
  * The stock engine consumes ONLY latest_signals(), which reads the most
    recent commodity_scans row from Supabase. It never imports commodity
    agents, and the commodity side never imports stock agents.
  * Signals are STRUCTURAL-horizon by construction (200/50 DMA trend state,
    not day moves) because their consumer is regime classification, not
    trade timing. Every signal carries evidence + verification labels and a
    staleness flag; a missing/stale scan degrades to None, never to a guess.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from commodities.sources.common import ESTIMATED, utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.cross_link")

# A scan older than this is flagged stale (commodity scans run each weekday;
# 5 days tolerates a long weekend + one missed run).
STALE_AFTER_DAYS = 5

_SIGNAL_SPEC = {
    "industrial_growth": {
        "commodity": "copper",
        "why": "copper demand is industrial production — its trend is a global growth proxy",
        "up_read": "growth_tailwind",
        "down_read": "growth_headwind",
    },
    "risk_appetite": {
        "commodity": "gold",
        "why": "a sustained gold bid is hedge/haven demand — a risk-off tilt",
        "up_read": "risk_off_tilt",
        "down_read": "risk_on_tilt",
    },
    "inflation_impulse": {
        "commodity": "wti",
        "why": "crude feeds headline inflation with a short lag",
        "up_read": "inflation_pressure_rising",
        "down_read": "inflation_pressure_easing",
    },
}


def _trend_state(tech: dict) -> tuple[str | None, dict]:
    """uptrend / downtrend / mixed from the verified technicals layer."""
    trend = tech.get("trend") or {}
    votes = [trend.get("above_sma50"), trend.get("above_sma200"),
             trend.get("golden_cross_state")]
    known = [v for v in votes if v is not None]
    evidence = {
        "above_50dma": trend.get("above_sma50"),
        "above_200dma": trend.get("above_sma200"),
        "golden_cross": trend.get("golden_cross_state"),
        "price_close": (tech.get("price") or {}).get("close"),
        "price_asof": (tech.get("price") or {}).get("asof"),
    }
    if len(known) < 2:
        return None, evidence
    ups = sum(1 for v in known if v)
    if ups == len(known):
        return "uptrend", evidence
    if ups == 0:
        return "downtrend", evidence
    return "mixed", evidence


def build_signals(analysis: dict) -> dict:
    """PURE: distil an analyze.run() result into the three stock-engine
    signals. Commodities missing from the run produce status=UNAVAILABLE."""
    out: dict = {
        "generated_at": utcnow_iso(),
        "source": "commodities pipeline scan",
        "horizon_note": ("STRUCTURAL trend-state signals for regime context — "
                         "not tactical, carries no timing information"),
        "signals": {},
    }
    commodities = analysis.get("commodities", {})
    for name, spec in _SIGNAL_SPEC.items():
        key = spec["commodity"]
        entry = commodities.get(key)
        tech = (entry or {}).get("layers", {}).get("technicals", {})
        if not entry or tech.get("status") != "OK":
            out["signals"][name] = {
                "commodity": key, "status": "UNAVAILABLE",
                "note": "commodity missing from scan or technicals fetch failed — "
                        "no signal emitted, not guessed",
            }
            continue
        state, evidence = _trend_state(tech)
        read = {"uptrend": spec["up_read"], "downtrend": spec["down_read"],
                "mixed": "mixed", None: "insufficient_data"}[state]
        curve = (entry.get("layers", {}).get("curve") or {}).get("structure")
        if curve:
            evidence["curve_structure"] = curve
        out["signals"][name] = {
            "commodity": key,
            "status": "OK" if state else "INSUFFICIENT_DATA",
            "trend_state": state,
            "read": read,
            "why": spec["why"],
            "evidence": evidence,
            "verification": ESTIMATED + " (trend state from verified yfinance history)",
        }
    return out


def summarize(signals: dict) -> str:
    """One human-readable line for logs / the macro narrative digest."""
    bits = []
    for name, s in (signals.get("signals") or {}).items():
        if s.get("status") == "OK":
            bits.append(f"{name}={s['read']} ({s['commodity']} {s['trend_state']})")
        else:
            bits.append(f"{name}=unavailable")
    return "; ".join(bits) if bits else "no commodity signals"


def latest_signals() -> dict | None:
    """The stock engine's entry point: the most recent scan's cross-link
    snapshot from Supabase, stamped with staleness. None when there is no
    usable scan — the macro agent must treat that as 'no commodity layer',
    never as neutral evidence."""
    try:
        from supabase import create_client
        from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        resp = (db.table("commodity_scans")
                  .select("scan_time, cross_link")
                  .neq("status", "failed")
                  .order("scan_time", desc=True)
                  .limit(1).execute())
        if not resp.data or not resp.data[0].get("cross_link"):
            return None
        row = resp.data[0]
        signals = row["cross_link"]
        scan_dt = datetime.fromisoformat(row["scan_time"].replace("Z", "+00:00"))
        signals["scan_time"] = row["scan_time"]
        signals["stale"] = (datetime.now(timezone.utc) - scan_dt
                            > timedelta(days=STALE_AFTER_DAYS))
        return signals
    except Exception as exc:  # noqa: BLE001 — graceful degradation, never fatal
        log.warning("commodity cross-link unavailable: %s", exc)
        return None
