"""
Agent 4: positioning — interprets the COT data (sources/cot.py) into the
sentiment / smart-money read.

Reads per commodity:
  * speculator stance: net long/short and the 3-yr percentile
  * crowding: >=90th pct net-long = crowded_long (reversal risk on longs),
    <=10th = crowded_short (squeeze fuel) — else not extreme
  * commercial divergence: commercials are hedgers; when they lean hard
    against the specs at an extreme, the reversal signal strengthens
  * weekly flow: which way positioning moved in the latest report

This is a CONTRARIAN-AT-EXTREMES indicator: mid-range percentiles carry
little signal, and the output says so rather than manufacturing a read.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources import cot  # noqa: E402
from commodities.sources.common import ESTIMATED, UNVERIFIED, utcnow_iso  # noqa: E402

log = logging.getLogger("commodities.positioning")

CROWDED_HIGH = 90.0
CROWDED_LOW = 10.0


def _interpret(c: dict) -> dict:
    spec_net = c.get("noncommercial_net")
    pct_info = c.get("spec_net_percentile_3y") or {}
    pct = pct_info.get("value")

    stance = ("net_long" if spec_net and spec_net > 0
              else "net_short" if spec_net and spec_net < 0 else "flat")
    if pct is None:
        crowding, signal = "unknown", "percentile history unavailable — no crowding read"
    elif pct >= CROWDED_HIGH:
        crowding = "crowded_long"
        signal = ("speculative longs at a 3-yr extreme — crowded trade, elevated "
                  "reversal/washout risk if the narrative stumbles")
    elif pct <= CROWDED_LOW:
        crowding = "crowded_short"
        signal = ("speculative shorts at a 3-yr extreme — squeeze fuel if the "
                  "narrative improves")
    else:
        crowding = "not_extreme"
        signal = (f"spec positioning mid-range ({pct:.0f}th pct of 3y) — little "
                  "contrarian signal either way; do not overread")

    wow = c.get("noncommercial_net_wow_change")
    flow = ("specs added longs" if wow and wow > 0
            else "specs cut longs / added shorts" if wow and wow < 0 else "no change")

    comm_net = c.get("commercial_net")
    divergence = None
    if comm_net is not None and spec_net is not None and crowding in ("crowded_long", "crowded_short"):
        opposed = (spec_net > 0 > comm_net) or (spec_net < 0 < comm_net)
        divergence = ("commercials positioned hard against the spec extreme — "
                      "strengthens the contrarian read" if opposed else
                      "commercials NOT opposing the extreme — weaker contrarian read")

    return {
        "status": "OK",
        "market": c.get("market"),
        "report_date": c.get("report_date"),
        "data_lag_note": c.get("data_lag_note"),
        "spec_stance": stance,
        "spec_net": spec_net,
        "spec_net_percentile_3y": pct,
        "crowding": crowding,
        "weekly_flow": {"noncommercial_net_change": wow, "summary": flow},
        "commercial_net": comm_net,
        "commercial_divergence": divergence,
        "signal": signal,
        "verification": ESTIMATED + " (interpreted from verified CFTC data; source labels in cot layer)",
    }


def analyze(keys: list[str] | None = None, cot_data: dict | None = None) -> dict:
    keys = keys or list(COMMODITIES)
    if cot_data is None:
        cot_data = cot.fetch_all()
    out = {}
    for k in keys:
        c = cot_data.get("commodities", {}).get(k, {})
        if c.get("status") != "OK":
            out[k] = {"commodity": k, "computed_at": utcnow_iso(),
                      "status": c.get("status", "MISSING"),
                      "verification": UNVERIFIED,
                      "note": c.get("note", "COT data unavailable for this commodity.")}
            continue
        out[k] = {"commodity": k, "computed_at": utcnow_iso(), **_interpret(c)}
    return out


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    keys = sys.argv[1:] or ["wti", "gold", "copper"]
    print(json.dumps(analyze(keys), indent=2, default=str))
