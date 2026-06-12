"""
Shared plumbing for the commodity ingestors.

Discipline (enforced here, used everywhere):
  * Every numeric payload carries a `verification` label —
    "VERIFIED: <source>" only when the number came back from a live fetch in
    this run. "UNVERIFIED" when the source is paid/gated/failed.
    "ESTIMATED" when we computed a derived figure from verified inputs.
    Nothing is ever filled from model memory.
  * Graceful degradation: fetch helpers raise nothing to callers — they
    return None and the caller records the gap honestly.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

HTTP_TIMEOUT = 25
UA = {"User-Agent": "fortis-research-pipeline/1.0 (research use)"}

log = logging.getLogger("commodities")


def verified(source: str) -> str:
    return f"VERIFIED: {source}"


UNVERIFIED = "UNVERIFIED"
ESTIMATED = "ESTIMATED"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_json(url: str, params: dict | None = None, timeout: int = HTTP_TIMEOUT):
    """GET returning parsed JSON, or None on any failure (logged, never raised)."""
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001 — degradation by design
        log.warning("GET %s failed: %s", url, exc)
        return None


# ── FRED ───────────────────────────────────────────────────────────────────────

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


def fred_observations(series_id: str, api_key: str, limit: int = 30,
                      start: str | None = None) -> list[dict] | None:
    """Most-recent-first observations [{date, value:float}], skipping '.' gaps."""
    if not api_key:
        return None
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    if start:
        params["observation_start"] = start
    data = get_json(FRED_OBS_URL, params)
    if not data or "observations" not in data:
        return None
    out = []
    for o in data["observations"]:
        if o.get("value") in (".", "", None):
            continue
        try:
            out.append({"date": o["date"], "value": float(o["value"])})
        except (ValueError, TypeError):
            continue
    return out or None


def fred_latest(series_id: str, api_key: str) -> dict | None:
    obs = fred_observations(series_id, api_key, limit=10)
    return obs[0] if obs else None
