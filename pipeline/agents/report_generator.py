"""
Report Generator
Builds the daily advisory report (HTML + Markdown) from the ranked focus list
and the crowd-intelligence debates, then stores it in the reports table.

Sections:
  1. Top Focus List          - ranked_focus_list for today's run.
  2. Today's Debates         - the 3 most actively contested stocks.
  3. Peer & Industry Verdict - for each A-grade pick: peer comparison table,
                               industry stance badge, differentiation thesis,
                               relative bull/bear, watchlist peer callout,
                               and a "Stronger peer detected" banner when
                               the A-pick is not best-in-industry. Reads
                               peer_industry_analysis (migration 027).

Usage:
    python pipeline/agents/report_generator.py [premarket|midday|close]
"""

import html as _html
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL
from agents.factcheck_agent import strip_data_refs  # noqa: E402

# ── Settings ───────────────────────────────────────────────────────────────────
VALID_RUN_TYPES = ("premarket", "midday", "close")
FOCUS_LIMIT     = 15      # focus-list rows shown in the report
DEBATE_COUNT    = 3       # number of debates in "Today's Debates"
MIN_ATTENTION   = 30      # attention floor for a stock to count as "debated"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════════

def _load_focus(db, today: str, run_type: str) -> list[dict]:
    resp = (
        db.table("ranked_focus_list")
        .select("rank,ticker,composite_score,thesis")
        .eq("run_date", today)
        .eq("run_type", run_type)
        .order("rank")
        .limit(FOCUS_LIMIT)
        .execute()
    )
    return resp.data or []


def _load_debates(db, today: str, run_type: str) -> list[dict]:
    try:
        resp = (
            db.table("ticker_debates").select("*")
            .eq("snapshot_date", today).eq("run_type", run_type).execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("ticker_debates query failed: %s", exc)
        return []


def _pick_debates(debates: list[dict]) -> list[dict]:
    """High attention AND sentiment_balance closest to zero = most contested."""
    eligible = [d for d in debates if float(d.get("attention_score") or 0) > MIN_ATTENTION]
    eligible.sort(key=lambda d: abs(float(d.get("sentiment_balance") or 0)))
    return eligible[:DEBATE_COUNT]


def _load_smart_money(db, today, run_type) -> dict:
    """Latest smart_money_intel row per ticker for today's run.
    Returns {ticker: row}. Step 6.7 integration."""
    try:
        rows = (db.table("smart_money_intel").select("*")
                .eq("snapshot_date", today).eq("run_type", run_type)
                .execute().data or [])
    except Exception as exc:
        log.debug("smart_money_intel load failed: %s", exc)
        return {}
    return {r["ticker"]: r for r in rows}


def _smart_money_section_markdown(focus, sm_by_ticker) -> list[str]:
    """Build the 'Smart Money Verdict' section. Full for A-grade picks,
    abbreviated for B/C grades."""
    if not focus or not sm_by_ticker:
        return []
    pick_grade = {r["ticker"]: r.get("conviction_grade") for r in focus}
    a_tickers = [t for t in pick_grade if pick_grade[t] == "A" and t in sm_by_ticker]
    bc_tickers = [t for t in pick_grade
                   if pick_grade[t] in ("B", "C") and t in sm_by_ticker]
    if not (a_tickers or bc_tickers):
        return []

    def _cell(score, strength):
        try:
            s = float(score or 0)
            st = float(strength or 0)
        except (TypeError, ValueError):
            return "neutral"
        if s > 30 and st > 50: return "POS"
        if s < -30 and st > 50: return "NEG"
        return "·"

    lines = ["", "## Smart Money Verdict", "",
             "_Triangulating earnings call, insider activity, short interest, "
             "and analyst revisions. LOW-confidence rows are informational._",
             ""]

    for t in a_tickers:
        sm = sm_by_ticker[t]
        grid = (f"E:{_cell(sm.get('earnings_signal_score'), sm.get('earnings_signal_strength'))} "
                f"| I:{_cell(sm.get('insider_signal_score'), sm.get('insider_signal_strength'))} "
                f"| S:{_cell(sm.get('short_signal_score'), sm.get('short_signal_strength'))} "
                f"| R:{_cell(sm.get('revision_signal_score'), sm.get('revision_signal_strength'))}")
        lines += [
            f"### {t}  --  {sm.get('pattern_detected') or 'n/a'}  "
            f"(composite {sm.get('composite_smart_money_score')}, "
            f"{sm.get('confidence_level') or '?'} confidence)",
            "",
            f"**Signal grid:** `{grid}`",
            "",
            f"- **Earnings**  score {sm.get('earnings_signal_score')} "
            f"(strength {sm.get('earnings_signal_strength')}) -- "
            f"verdict {sm.get('earnings_verdict') or 'n/a'}",
            f"- **Insider**   score {sm.get('insider_signal_score')} "
            f"(strength {sm.get('insider_signal_strength')}) -- "
            f"{sm.get('insider_detected_cluster') or 'n/a'}; "
            f"discretionary {sm.get('insider_discretionary_count')} / "
            f"10b5-1 {sm.get('insider_10b5_1_count')}",
            f"- **Short**     score {sm.get('short_signal_score')} "
            f"(strength {sm.get('short_signal_strength')}) -- "
            f"{sm.get('short_pct_of_float')}% float, "
            f"DTC {sm.get('days_to_cover')}, trend "
            f"{sm.get('short_trend_direction')}",
            f"- **Revisions** score {sm.get('revision_signal_score')} "
            f"(strength {sm.get('revision_signal_strength')}) -- "
            f"{sm.get('revision_velocity')}, net "
            f"{sm.get('net_revision_count')}",
            "",
        ]
        note = sm.get("intel_note")
        if note and sm.get("quality_grade") != "UNVERIFIED":
            lines += [strip_data_refs(note), ""]
        elif note:
            lines += ["_Intel note generated but unverified -- shown for review only._",
                       "", strip_data_refs(note), ""]

    if bc_tickers:
        lines += ["### Other graded picks (abbreviated)", "",
                  "| Ticker | Grade | Pattern | Composite | Confidence |",
                  "|---|---|---|---|---|"]
        for t in bc_tickers:
            sm = sm_by_ticker[t]
            lines.append(f"| {t} | {pick_grade.get(t)} | "
                         f"{sm.get('pattern_detected') or 'n/a'} | "
                         f"{sm.get('composite_smart_money_score')} | "
                         f"{sm.get('confidence_level') or '?'} |")
        lines.append("")
    return lines


def _load_portfolio_review(db, today, run_type) -> list[dict]:
    """Step 6.8 -- per-portfolio position signals + rebalance suggestion.
    Returns a list of dicts, one per portfolio, in the form:
        {portfolio_id, portfolio_name, signals, rebalance, urgent_count}.
    The "Existing Portfolio Review" section is built from this."""
    try:
        portfolios = (db.table("portfolios").select("id,name,user_id")
                      .execute().data or [])
    except Exception as exc:
        log.debug("portfolios load failed: %s", exc)
        return []
    out = []
    for p in portfolios:
        try:
            sigs = (db.table("position_signals").select("*")
                    .eq("portfolio_id", p["id"])
                    .eq("snapshot_date", today).eq("run_type", run_type)
                    .order("signal_strength", desc=True).execute().data or [])
        except Exception:
            sigs = []
        try:
            rb_rows = (db.table("portfolio_rebalance_suggestions").select("*")
                       .eq("portfolio_id", p["id"])
                       .eq("snapshot_date", today).eq("run_type", run_type)
                       .limit(1).execute().data or [])
        except Exception:
            rb_rows = []
        if not sigs and not rb_rows:
            continue
        out.append({
            "portfolio_id":   p["id"],
            "portfolio_name": p["name"],
            "signals":        sigs,
            "rebalance":      rb_rows[0] if rb_rows else None,
            "urgent_count":   sum(1 for s in sigs
                                  if s.get("requires_immediate_attention")),
        })
    return out


def _portfolio_review_markdown(reviews: list[dict]) -> list[str]:
    """Build the section that goes at the TOP of every report. Protect
    existing capital before exploring new ideas (positioning matters)."""
    if not reviews:
        return []
    lines = ["", "## Existing Portfolio Review", "",
             "_Position signals across tracked portfolios. **For advisor "
             "review -- not a recommendation.**_", ""]
    for rv in reviews:
        rb = rv.get("rebalance") or {}
        sigs = rv["signals"]
        urgent = [s for s in sigs if s.get("requires_immediate_attention")]
        total_value = rb.get("current_total_value")
        lines.append(f"### {rv['portfolio_name']}  "
                     f"({len(sigs)} positions" +
                     (f", ${float(total_value):,.0f}" if total_value else "") +
                     (f", **{len(urgent)} requiring review**" if urgent else "") +
                     ")")
        lines.append("")
        # Portfolio-level reasoning
        if rb.get("reasoning"):
            lines.append(f"> {rb['reasoning']}")
            lines.append("")
        # Top-3 priorities
        priorities = rb.get("top_3_priorities") or []
        if priorities:
            lines.append("**Top priorities for review:**")
            for i, p in enumerate(priorities, start=1):
                action = (p.get("action") or "review").replace("_", " ")
                ticker = p.get("ticker") or "—"
                reasoning = (p.get("reasoning") or "").strip()
                lines.append(f"{i}. **{action.title()} {ticker}** — {reasoning}")
            lines.append("")
        # HIGH-severity individual position signals
        if urgent:
            lines.append("**Position alerts requiring review:**")
            lines.append("")
            lines.append("| Ticker | Signal | Strength | Action | Size | Notes |")
            lines.append("|---|---|---|---|---|---|")
            for s in urgent:
                stype = (s.get("signal_type") or "").replace("_", " ")
                action = (s.get("recommended_action") or "").replace("_", " ")
                size = s.get("recommended_size_change_pct")
                size_str = f"{size:+.0f}%" if size else "—"
                notes = (s.get("reasoning") or "")[:140].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {s['ticker']} | {stype} | "
                              f"{s.get('signal_strength')} | {action} | "
                              f"{size_str} | {notes} |")
            lines.append("")
        # Concentration alerts
        concs = (rb.get("concentration_alerts") or [])
        if concs:
            lines.append("**Concentration alerts:**")
            for c in concs:
                lines.append(f"- {c.get('severity', 'MEDIUM')}: "
                              f"{c.get('description')}")
            lines.append("")
    return lines


def _load_peer_verdicts(db, today, run_type) -> list[dict]:
    """One peer_industry_analysis row per A-grade pick for today's run."""
    try:
        rows = (db.table("peer_industry_analysis").select("*")
                .eq("snapshot_date", today).eq("run_type", run_type)
                .execute().data or [])
        return rows
    except Exception as exc:
        log.warning("peer_industry_analysis load failed: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Peer & Industry Verdict -- shared formatting helpers
# ══════════════════════════════════════════════════════════════════════════════

# Metrics shown in the compact report table -- chosen for advisor-readability.
REPORT_METRIC_COLS = [
    ("pe",               "P/E",     "ratio"),
    ("ev_ebitda",        "EV/EBITDA","ratio"),
    ("ps",               "P/S",     "ratio"),
    ("operating_margin", "Op Mgn",  "pct"),
    ("roe",              "ROE",     "pct"),
    ("revenue_growth",   "Rev YoY", "pct"),
    ("eps_growth",       "EPS YoY", "pct"),
    ("debt_equity",      "D/E",     "ratio"),
]


def _fmt_metric(v, kind):
    if v is None:
        return "n/a"
    if isinstance(v, str):
        return v
    if kind == "pct":
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _stance_color(stance):
    return {
        "overweight":     ("#1a7a3e", "#eafaf0", "#b8e6c8"),  # green
        "market_weight":  ("#7a5a1a", "#fbf5e6", "#e8d9a8"),  # amber
        "underweight":    ("#b3262f", "#fdeced", "#f0bcc0"),  # red
    }.get(stance, ("#444", "#f0f0f0", "#ddd"))


# ══════════════════════════════════════════════════════════════════════════════
# Markdown
# ══════════════════════════════════════════════════════════════════════════════

def _build_peer_section_markdown(verdicts: list[dict]) -> list[str]:
    if not verdicts:
        return []
    lines = ["", "## Peer & Industry Verdict", "",
             "_For each A-grade pick: how it compares to its analyst-cited "
             "peer set and where its industry sits in the cycle._", ""]
    for v in verdicts:
        target = v["target_ticker"]
        stance = (v.get("industry_stance") or "market_weight")
        rank   = v.get("within_industry_rank")
        nuni   = (v.get("peer_count") or 0) + 1
        best   = v.get("best_in_industry_ticker")
        invest = v.get("investability_score") or 0
        cycle  = v.get("industry_cycle_position") or "unknown"
        is_best = bool(v.get("best_in_industry_is_target"))
        verify = v.get("verification_score") or 0
        quality = v.get("quality_grade") or "UNVERIFIED"

        lines += [f"### {target}  &mdash;  Industry rank {rank}/{nuni}", ""]
        if not is_best and best:
            lines += [f"> **Stronger peer detected:** {best} ranks higher on "
                      f"the peer composite. Consider as an alternative.", ""]
        lines += [
            f"**Industry stance:** `{stance.upper()}`  &middot; "
            f"investability {float(invest):.0f}/100  &middot; cycle `{cycle}`  "
            f"&middot; factcheck `{quality}` ({float(verify):.2f})",
            "",
            f"_{strip_data_refs(v.get('industry_stance_rationale') or '')}_",
            "",
        ]

        # Peer comparison table.
        pc = v.get("peer_comparison") or {}
        by_ticker = (pc.get("by_ticker") or {})
        overall = (pc.get("overall_score") or {})
        ranked = pc.get("ranked") or [target]
        if by_ticker:
            header = ["Ticker", "Score"] + [c[1] for c in REPORT_METRIC_COLS]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "|".join(["---"] * len(header)) + "|")
            for t in ranked:
                row_metrics = by_ticker.get(t, {})
                row_score = overall.get(t)
                marker = "**" if t == target else ""
                row = [f"{marker}{t}{marker}",
                       f"{float(row_score):.1f}" if row_score is not None else "n/a"]
                for k, _, kind in REPORT_METRIC_COLS:
                    row.append(_fmt_metric(row_metrics.get(k), kind))
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")

        diff = strip_data_refs(v.get("differentiation_thesis") or "")
        if diff:
            lines += [f"**Differentiation thesis.** {diff}", ""]

        rb = strip_data_refs(v.get("relative_bull_case") or "")
        rbe = strip_data_refs(v.get("relative_bear_case") or "")
        if rb or rbe:
            lines += ["**Relative bull (vs peers):** " + (rb or "n/a"), "",
                       "**Relative bear (vs peers):** " + (rbe or "n/a"), ""]

        wp = v.get("watchlist_peer")
        wp_reason = strip_data_refs(v.get("watchlist_peer_reasoning") or "")
        if wp:
            lines += [f"**Watchlist peer:** **{wp}** &mdash; {wp_reason}", ""]
    return lines


def _build_peer_section_html(verdicts: list[dict]) -> list[str]:
    if not verdicts:
        return []
    out = ["<h2>Peer &amp; Industry Verdict</h2>",
           "<p class='meta'>For each A-grade pick: how it compares to its "
           "analyst-cited peer set and where its industry sits in the cycle.</p>"]
    for v in verdicts:
        target  = v["target_ticker"]
        stance  = (v.get("industry_stance") or "market_weight")
        rank    = v.get("within_industry_rank")
        nuni    = (v.get("peer_count") or 0) + 1
        best    = v.get("best_in_industry_ticker")
        is_best = bool(v.get("best_in_industry_is_target"))
        invest  = v.get("investability_score") or 0
        cycle   = v.get("industry_cycle_position") or "unknown"
        verify  = v.get("verification_score") or 0
        quality = v.get("quality_grade") or "UNVERIFIED"
        fg, bg, bd = _stance_color(stance)

        out.append(f"<div class='peer-block'>")
        out.append(
            f"<div class='peer-head'>"
            f"<b>{_esc(target)}</b> &nbsp; "
            f"<span class='meta'>Industry rank {rank}/{nuni}</span>"
            f"</div>"
        )
        if not is_best and best:
            out.append(
                f"<div class='banner-warn'>"
                f"&#9888;&#xfe0f; <b>Stronger peer detected:</b> "
                f"{_esc(best)} ranks higher on the peer composite. "
                f"Consider as an alternative.</div>"
            )
        out.append(
            f"<div class='peer-meta'>"
            f"<span class='stance' style='background:{bg};border:1px solid {bd};color:{fg}'>"
            f"{_esc(stance.upper())}</span> "
            f"&middot; investability {float(invest):.0f}/100 "
            f"&middot; cycle <code>{_esc(cycle)}</code> "
            f"&middot; factcheck <code>{_esc(quality)} ({float(verify):.2f})</code>"
            f"</div>"
        )
        out.append(f"<p class='extra'><em>{_esc(strip_data_refs(v.get('industry_stance_rationale') or ''))}</em></p>")

        # Compact peer comparison grid.
        pc = v.get("peer_comparison") or {}
        by_ticker = pc.get("by_ticker") or {}
        overall   = pc.get("overall_score") or {}
        ranked    = pc.get("ranked") or [target]
        if by_ticker:
            out.append("<table><tr><th>Ticker</th><th>Score</th>")
            for _, label, _ in REPORT_METRIC_COLS:
                out.append(f"<th>{_esc(label)}</th>")
            out.append("</tr>")
            for t in ranked:
                row_metrics = by_ticker.get(t) or {}
                row_score = overall.get(t)
                cls = " class='target'" if t == target else ""
                out.append(f"<tr{cls}>")
                marker = "*" if t == target else ""
                out.append(f"<td><b>{_esc(t)}</b>{marker}</td>")
                out.append(f"<td>{float(row_score):.1f}</td>" if row_score is not None
                            else "<td>n/a</td>")
                for k, _, kind in REPORT_METRIC_COLS:
                    out.append(f"<td>{_esc(_fmt_metric(row_metrics.get(k), kind))}</td>")
                out.append("</tr>")
            out.append("</table>")

        diff = strip_data_refs(v.get("differentiation_thesis") or "")
        if diff:
            out.append(f"<p class='extra'><b>Differentiation thesis.</b> {_esc(diff)}</p>")

        rb = strip_data_refs(v.get("relative_bull_case") or "")
        rbe = strip_data_refs(v.get("relative_bear_case") or "")
        if rb or rbe:
            out.append("<div class='debate'>")
            out.append(f"<div class='bull'><h4>Relative bull (vs peers)</h4>{_esc(rb)}</div>")
            out.append(f"<div class='bear'><h4>Relative bear (vs peers)</h4>{_esc(rbe)}</div>")
            out.append("</div>")

        wp = v.get("watchlist_peer")
        wp_reason = strip_data_refs(v.get("watchlist_peer_reasoning") or "")
        if wp:
            out.append(
                f"<p class='extra'><b>Watchlist peer:</b> "
                f"<b>{_esc(wp)}</b> &mdash; {_esc(wp_reason)}</p>"
            )
        out.append("</div>")
    return out


def _build_markdown(today, run_type, focus, debates, peer_verdicts,
                     smart_money=None, portfolio_reviews=None) -> str:
    # Step 6.8 -- urgent review count drives the email subject upgrade.
    reviews = portfolio_reviews or []
    total_urgent = sum(rv.get("urgent_count", 0) for rv in reviews)
    title_prefix = "[URGENT REVIEW] " if total_urgent > 0 else ""
    lines = [
        f"# {title_prefix}Fortis Stock Intelligence -- {run_type.title()} Report",
        f"_{today}_",
    ]
    if total_urgent:
        lines += [
            "",
            f"> **{total_urgent} position(s) across tracked portfolios "
            "require advisor review** -- see the Existing Portfolio Review "
            "section below before exploring new ideas.",
        ]
    # Step 6.8 -- protect existing capital first; new ideas come after.
    lines.extend(_portfolio_review_markdown(reviews))
    lines += [
        "",
        "## Top Focus List",
        "",
    ]
    if focus:
        lines += ["| Rank | Ticker | Score | Thesis |", "|---|---|---|---|"]
        for r in focus:
            thesis = (r.get("thesis") or "").replace("|", "\\|")
            lines.append(
                f"| {r['rank']} | {r['ticker']} | {r['composite_score']:.1f} | {thesis} |"
            )
    else:
        lines.append("_No ranked focus list available for this run._")

    lines += ["", "## Today's Debates", "",
              "The most actively contested stocks -- high crowd attention with "
              "bull and bear camps near parity.", ""]
    if debates:
        for d in debates:
            attn = float(d.get("attention_score") or 0)
            sb   = float(d.get("sentiment_balance") or 0)
            lines += [
                f"### {d['ticker']}  (attention {attn:.0f}/100, sentiment balance {sb:+.0f})",
                "",
                f"**Bull case:** {d.get('bull_case') or 'n/a'}",
                "",
                f"**Bear case:** {d.get('bear_case') or 'n/a'}",
                "",
                f"**Consensus tilt:** {d.get('consensus_tilt') or 'n/a'}",
                "",
                f"**Retail vs pro:** {d.get('retail_pro_divergence') or 'n/a'}",
                "",
            ]
    else:
        lines.append("_No debates cleared the attention threshold today._")

    lines.extend(_build_peer_section_markdown(peer_verdicts))
    lines.extend(_smart_money_section_markdown(focus, smart_money or {}))

    lines += ["", "---", "_Generated by the Fortis pipeline. Not investment advice._"]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# HTML
# ══════════════════════════════════════════════════════════════════════════════

def _esc(text) -> str:
    return _html.escape(str(text or ""))


def _build_html(today, run_type, focus, debates, peer_verdicts) -> str:
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>Fortis Stock Intelligence - {_esc(run_type.title())} Report</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;"
        "max-width:880px;margin:0 auto;padding:32px;line-height:1.5}",
        "h1{font-size:22px;margin-bottom:2px}h2{font-size:17px;margin-top:32px;"
        "border-bottom:2px solid #eee;padding-bottom:6px}",
        ".date{color:#888;font-size:13px}",
        "table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}",
        "th,td{border:1px solid #e3e3e3;padding:6px 9px;text-align:left;vertical-align:top}",
        "th{background:#f6f6f6}",
        ".debate{display:flex;gap:14px;margin:14px 0}",
        ".bull,.bear{flex:1;padding:11px 13px;border-radius:7px;font-size:13px}",
        ".bull{background:#eafaf0;border:1px solid #b8e6c8}",
        ".bear{background:#fdeced;border:1px solid #f0bcc0}",
        ".bull h4{color:#1a7a3e;margin:0 0 5px}.bear h4{color:#b3262f;margin:0 0 5px}",
        ".dh{font-size:15px;font-weight:600;margin-top:18px}",
        ".meta{color:#888;font-size:12px;margin-bottom:2px}",
        ".extra{font-size:12px;color:#444;margin-top:6px}",
        ".foot{color:#999;font-size:11px;margin-top:34px}",
        ".peer-block{margin:22px 0;padding:14px 16px;background:#fafafa;border-radius:8px}",
        ".peer-head{font-size:15px;margin-bottom:4px}",
        ".peer-meta{font-size:12px;color:#555;margin-bottom:6px}",
        ".stance{font-size:11px;font-weight:600;padding:2px 8px;border-radius:11px}",
        ".banner-warn{background:#fff7d6;border:1px solid #ecd277;color:#7a5a1a;"
            "padding:8px 10px;border-radius:6px;font-size:13px;margin:6px 0 10px}",
        "table tr.target td{background:#eef6ff;font-weight:600}",
        "</style></head><body>",
        f"<h1>Fortis Stock Intelligence &mdash; {_esc(run_type.title())} Report</h1>",
        f"<div class='date'>{_esc(today)}</div>",
        "<h2>Top Focus List</h2>",
    ]

    if focus:
        parts.append("<table><tr><th>Rank</th><th>Ticker</th><th>Score</th>"
                      "<th>Thesis</th></tr>")
        for r in focus:
            parts.append(
                f"<tr><td>{r['rank']}</td><td><b>{_esc(r['ticker'])}</b></td>"
                f"<td>{r['composite_score']:.1f}</td><td>{_esc(r.get('thesis'))}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p><em>No ranked focus list available for this run.</em></p>")

    parts.append("<h2>Today's Debates</h2>")
    parts.append("<p class='meta'>The most actively contested stocks &mdash; high "
                 "crowd attention with bull and bear camps near parity.</p>")

    if debates:
        for d in debates:
            attn = float(d.get("attention_score") or 0)
            sb   = float(d.get("sentiment_balance") or 0)
            parts.append(
                f"<div class='dh'>{_esc(d['ticker'])}</div>"
                f"<div class='meta'>attention {attn:.0f}/100 &middot; "
                f"sentiment balance {sb:+.0f}</div>"
                "<div class='debate'>"
                f"<div class='bull'><h4>Bull case</h4>{_esc(d.get('bull_case'))}</div>"
                f"<div class='bear'><h4>Bear case</h4>{_esc(d.get('bear_case'))}</div>"
                "</div>"
                f"<div class='extra'><b>Consensus tilt:</b> {_esc(d.get('consensus_tilt'))}<br>"
                f"<b>Retail vs pro:</b> {_esc(d.get('retail_pro_divergence'))}</div>"
            )
    else:
        parts.append("<p><em>No debates cleared the attention threshold today.</em></p>")

    parts.extend(_build_peer_section_html(peer_verdicts))

    parts.append("<div class='foot'>Generated by the Fortis pipeline. "
                 "Not investment advice.</div>")
    parts.append("</body></html>")
    return "".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in VALID_RUN_TYPES:
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"

    today = datetime.now(timezone.utc).date().isoformat()
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    focus = _load_focus(db, today, run_type)
    log.info("Loaded %d focus-list rows for %s/%s", len(focus), today, run_type)
    if not focus:
        log.warning("No ranked_focus_list rows -- run ranking_engine.py first.")

    all_debates = _load_debates(db, today, run_type)
    debates = _pick_debates(all_debates)
    log.info("Selected %d debates (of %d) for Today's Debates section",
             len(debates), len(all_debates))

    peer_verdicts = _load_peer_verdicts(db, today, run_type)
    log.info("Loaded %d peer/industry verdict(s) for the Peer section",
             len(peer_verdicts))

    smart_money = _load_smart_money(db, today, run_type)
    log.info("Loaded %d smart_money_intel row(s)", len(smart_money))

    # Step 6.8 -- Existing Portfolio Review section at top of report
    portfolio_reviews = _load_portfolio_review(db, today, run_type)
    total_urgent = sum(rv.get("urgent_count", 0) for rv in portfolio_reviews)
    log.info("Loaded %d portfolio review(s); %d urgent signal(s)",
             len(portfolio_reviews), total_urgent)

    markdown = _build_markdown(today, run_type, focus, debates, peer_verdicts,
                                smart_money, portfolio_reviews)
    html_doc = _build_html(today, run_type, focus, debates, peer_verdicts)

    row = {
        "report_type":      run_type,
        "report_date":      today,
        "content_html":     html_doc,
        "content_markdown": markdown,
        "delivered_email":  False,
    }
    try:
        resp = db.table("reports").insert(row).execute()
        rid = resp.data[0]["id"] if resp.data else "?"
        log.info("Report stored in reports table (id=%s)", rid)
    except Exception as exc:
        log.error("reports insert failed: %s", exc)

    log.info("")
    log.info("== Report preview (markdown) ==")
    for line in markdown.splitlines():
        log.info(line)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
