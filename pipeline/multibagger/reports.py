"""
Multibagger Report Generators
=============================

    weekly_emerging_opportunities()
        Generates the "Emerging Opportunities" report -- the new candidates
        and watchlist movers from the latest weekly discovery run. Markdown
        output; HTML can be rendered later via the same Jinja template family
        used by report_composer. Writes to public.reports with report_type
        'weekly_emerging' so it slots into the same dashboard plumbing.

(The quarterly thesis-review report was removed June 2026 -- the weekly
discovery run + the weekly watchlist refresh cover thesis status.)

CLI
    python pipeline/multibagger/reports.py --weekly
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

RISK_BANNER = (
    "_Emerging candidates are speculative, long-horizon, high-risk positions. "
    "Most will not become multibaggers. Position sizing should reflect the "
    "asymmetric risk: small bets, long horizons, expect many failures and a "
    "few large winners. For licensed advisor review — not investment advice._"
)


def _money(v):
    if not v or v <= 0:
        return "—"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.0f}M"
    return f"${v:.0f}"


def _pct(v):
    return "—" if v is None else f"{'+' if v > 0 else ''}{v:.1f}%"


def _persist_report(db, report_type: str, markdown: str, html: str | None = None) -> int | None:
    try:
        row = {
            "report_type": report_type,
            "report_date": date.today().isoformat(),
            "content_markdown": markdown,
            "content_html": html,
            "delivered_email": False,
        }
        out = db.table("reports").insert(row).execute()
        return out.data[0]["id"] if out.data else None
    except Exception as exc:
        log.warning("reports persist failed (%s) -- writing to file instead",
                    exc)
        outdir = Path(__file__).resolve().parent / "data" / "_reports"
        outdir.mkdir(parents=True, exist_ok=True)
        outp = outdir / f"{date.today().isoformat()}_{report_type}.md"
        outp.write_text(markdown, encoding="utf-8")
        log.info("wrote %s", outp)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Weekly -- Emerging Opportunities
# ══════════════════════════════════════════════════════════════════════════════

def weekly_emerging_opportunities() -> str:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    # Latest theses per ticker, newest first
    theses = (
        db.table("multibagger_theses")
          .select("*")
          .order("generated_at", desc=True)
          .limit(50)
          .execute()
          .data or []
    )
    latest_by_ticker: dict[str, dict] = {}
    for t in theses:
        latest_by_ticker.setdefault(t["ticker"], t)

    wl_rows = (
        db.table("emerging_watchlist")
          .select("ticker, status, conviction_tier, multibagger_score, return_since_added, "
                  "current_market_cap, current_price")
          .in_("status", ["active", "thesis_intact", "thesis_weakening"])
          .execute()
          .data or []
    )
    wl_by_ticker = {r["ticker"]: r for r in wl_rows}

    today = date.today().isoformat()
    md = [
        f"# Emerging Opportunities — {today}",
        "",
        RISK_BANNER,
        "",
        "## Philosophy",
        "",
        "This report surfaces small/micro-cap candidates that match the",
        "structural DNA of historical multibaggers: small base, durable",
        "growth, improving unit economics, aligned owner-operators, and a",
        "long discovery runway. It is **structured speculation** -- not",
        "high-conviction prediction. Most of these names will fail. The",
        "winners can return many multiples; the losers can only return -100%.",
        "",
        f"## New & active candidates ({len(latest_by_ticker)})",
        "",
    ]

    for tier in ("tier_1_high_conviction", "tier_2_promising", "tier_3_speculative"):
        tier_names = [
            t for t in latest_by_ticker.values()
            if t.get("conviction_tier") == tier
        ]
        if not tier_names:
            continue
        nice = {
            "tier_1_high_conviction": "Tier 1 — Higher conviction",
            "tier_2_promising":       "Tier 2 — Promising",
            "tier_3_speculative":     "Tier 3 — Speculative",
        }[tier]
        md.append(f"### {nice}")
        md.append("")
        for t in tier_names:
            ticker = t["ticker"]
            wl = wl_by_ticker.get(ticker)
            md.append(f"#### {ticker}  ·  score {float(t.get('multibagger_score') or 0):.1f}  "
                      f"·  risk: {t.get('risk_rating') or '—'}")
            md.append("")
            if t.get("business_model"):
                md.append(f"**Business model.** {t['business_model']}")
                md.append("")
            if t.get("the_10x_path"):
                md.append(f"**The 10x path.** {t['the_10x_path']}")
                md.append("")
            if t.get("what_kills_it"):
                md.append(f"**What kills it.** {t['what_kills_it']}")
                md.append("")
            if t.get("moat_assessment"):
                md.append(f"**Moat.** {t['moat_assessment']}")
                md.append("")
            kms = t.get("key_metrics_to_track") or []
            if isinstance(kms, list) and kms:
                md.append(f"**Key metrics to track:** {', '.join(str(x) for x in kms)}")
                md.append("")
            if wl:
                md.append(f"_On watchlist: {wl['status']} · "
                          f"return since added {_pct(wl.get('return_since_added'))} · "
                          f"market cap {_money(wl.get('current_market_cap'))}_")
                md.append("")
            md.append("---")
            md.append("")

    md.append("## Movers since last week")
    md.append("")
    movers = sorted(wl_rows, key=lambda r: r.get("return_since_added") or 0, reverse=True)[:10]
    if movers:
        md.append("| Ticker | Tier | Score | Status | Return | Market cap |")
        md.append("|---|---|---|---|---|---|")
        for r in movers:
            md.append(
                f"| {r['ticker']} | {r.get('conviction_tier','—')} | "
                f"{float(r.get('multibagger_score') or 0):.1f} | "
                f"{r['status']} | {_pct(r.get('return_since_added'))} | "
                f"{_money(r.get('current_market_cap'))} |"
            )
    else:
        md.append("_No movers yet — watchlist is fresh._")

    md.append("")
    md.append("")
    md.append(RISK_BANNER)

    md_text = "\n".join(md)
    _persist_report(db, "weekly_emerging", md_text)
    return md_text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true",
                    help="(default) generate the weekly Emerging Opportunities report")
    ap.parse_args()
    log.info("Generating weekly Emerging Opportunities report…")
    weekly_emerging_opportunities()
    log.info("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
