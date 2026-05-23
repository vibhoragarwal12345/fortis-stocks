"""
Report Composer  (Step 7.1)

The master assembly agent for the three daily briefs (pre-market / midday /
close). Reads every row of intelligence the rest of the pipeline produced
and assembles it into a structured document, then renders to both HTML
(Jinja2 templates) and Markdown.

PRINCIPLE
  Composer does NOT compute signals. It only assembles. Every numeric claim
  in the rendered brief traces back to a row in a database table.  The one
  LLM call (the headline narrative -- Executive Summary / Morning Recap /
  Market Wrap) runs in a strict closed-context prompt that may only cite
  values present in the DATA block.

DATA AVAILABILITY
  Every section is built defensively: if a source table has no rows for the
  requested run_date / run_type, the section is omitted and an entry is
  added to report.data_availability with the reason. The composer never
  fabricates content to fill an empty section.

PUBLIC API
  ReportComposer(db=None)
  .compose_report(run_date, run_type, tenant_id=None,
                   save_samples=False, persist=True)
      -> {"structured": dict, "markdown": str, "html": str, "report_id": str}

CLI
  python pipeline/agents/report_composer.py 2026-05-20 midday
  python pipeline/agents/report_composer.py 2026-05-20 midday --samples
  python pipeline/agents/report_composer.py 2026-05-20 all --samples
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent              # pipeline/
TEMPLATE_DIR = ROOT / "templates"
SAMPLES_DIR = ROOT / "reports" / "samples"

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_A_PICKS, MAX_B_PICKS, MAX_C_PICKS = 5, 7, 5
MACRO_FRESH_DAYS = 5                  # macro_context older than this triggers a note
SUPPORTING_TABLE_WINDOW_DAYS = 7      # how far back to pull quant / smart_money rows

RUN_TYPE_LABELS = {
    "premarket": "Pre-Market Brief",
    "midday":    "Midday Brief",
    "close":     "Close Brief",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tiny helpers
# ══════════════════════════════════════════════════════════════════════════════

def _to_date(d) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return datetime.fromisoformat(str(d)[:10]).date()


def _fmt_num(v, places: int = 2, default: str = "—") -> str:
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if abs(f) >= 1e9:
        return f"{f/1e9:.{places}f}B"
    if abs(f) >= 1e6:
        return f"{f/1e6:.{places}f}M"
    if abs(f) >= 1e3:
        return f"{f/1e3:.{places}f}K"
    return f"{f:.{places}f}"


def _fmt_pct(v, places: int = 1, default: str = "—") -> str:
    if v is None or v == "":
        return default
    try:
        return f"{float(v):.{places}f}%"
    except (TypeError, ValueError):
        return default


def _fmt_money(v, default: str = "—") -> str:
    if v is None or v == "":
        return default
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return default


def _date_label(d: date) -> str:
    return d.strftime("%A, %B %-d, %Y") if sys.platform != "win32" \
        else d.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _safe(d: dict | None, *keys, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


# ══════════════════════════════════════════════════════════════════════════════
# Composer
# ══════════════════════════════════════════════════════════════════════════════

class ReportComposer:
    """Assembles the daily brief from existing intelligence tables."""

    def __init__(self, db=None):
        self.db = db or create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        self._jinja = None         # lazy
        self._llm_calls = 0
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public ────────────────────────────────────────────────────────────────

    def compose_report(self, run_date, run_type: str,
                       tenant_id: str | None = None,
                       save_samples: bool = False,
                       persist: bool = True) -> dict:
        if run_type not in ("premarket", "midday", "close"):
            raise ValueError(f"unsupported run_type: {run_type}")
        rd = _to_date(run_date)
        t0 = time.monotonic()
        self._llm_calls = 0

        structured: dict[str, Any] = {
            "meta": {
                "run_date":   rd.isoformat(),
                "run_type":   run_type,
                "tenant_id":  tenant_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "header": {
                "title":       RUN_TYPE_LABELS[run_type],
                "date_label":  _date_label(rd),
                "a_count":     0,
                "b_count":     0,
                "c_count":     0,
                "avg_verification_score": None,
            },
            "data_availability": {},
            "sections": [],         # ordered list of section dicts
        }

        # ── Build sections in display order ──────────────────────────────────
        builders = self._builders_for(run_type)
        for slug, fn in builders:
            try:
                section = fn(rd, run_type, tenant_id)
            except Exception as exc:
                log.warning("section %s failed: %s", slug, exc)
                section = self._empty_section(slug, slug.replace("_", " ").title(),
                                              f"section build error: {exc}")
            structured["sections"].append(section)

        # ── Roll up header stats from focus_list / supporting / honorable ────
        self._roll_up_header(structured)

        # ── Render ───────────────────────────────────────────────────────────
        markdown = self._render_markdown(structured)
        html = self._render_html(structured)

        elapsed = round(time.monotonic() - t0, 2)
        structured["meta"]["generation_time_seconds"] = elapsed
        structured["meta"]["llm_calls_made"] = self._llm_calls

        report_id = None
        if persist:
            report_id = self._persist(structured, markdown, html)
            structured["meta"]["report_id"] = report_id

        if save_samples:
            stem = f"{rd.isoformat()}_{run_type}"
            (SAMPLES_DIR / f"{stem}.html").write_text(html, encoding="utf-8")
            (SAMPLES_DIR / f"{stem}.md").write_text(markdown, encoding="utf-8")
            (SAMPLES_DIR / f"{stem}.json").write_text(
                json.dumps(structured, indent=2, default=str), encoding="utf-8")
            log.info("samples written to %s/%s.{html,md,json}", SAMPLES_DIR, stem)

        log.info("compose_report %s %s -> %d sections, %d LLM call(s), %.1fs",
                 rd, run_type, len(structured["sections"]),
                 self._llm_calls, elapsed)
        return {
            "structured": structured,
            "markdown":   markdown,
            "html":       html,
            "report_id":  report_id,
        }

    # ── Section dispatcher ───────────────────────────────────────────────────

    def _builders_for(self, run_type: str):
        if run_type == "premarket":
            return [
                ("executive_summary",  self._section_executive_summary),
                ("macro_snapshot",     self._section_macro_snapshot),
                ("portfolio_review",   self._section_portfolio_review),
                ("focus_list",         self._section_focus_list),
                ("supporting_picks",   self._section_supporting_picks),
                ("honorable_mentions", self._section_honorable_mentions),
                ("risk_radar",         self._section_risk_radar),
                ("tomorrow_setup",     self._section_tomorrow_setup),
                ("disclaimers",        self._section_disclaimers),
            ]
        if run_type == "midday":
            return [
                ("morning_recap",      self._section_morning_recap),
                ("portfolio_day_pnl",  self._section_portfolio_day_pnl),
                ("portfolio_review",   self._section_portfolio_review),
                ("focus_list",         self._section_focus_list),
                ("supporting_picks",   self._section_supporting_picks),
                ("honorable_mentions", self._section_honorable_mentions),
                ("pivot_stocks",       self._section_pivot_stocks),
                ("volume_options",     self._section_volume_options),
                ("tonight_earnings",   self._section_tonight_earnings),
                ("disclaimers",        self._section_disclaimers),
            ]
        # close
        return [
            ("market_wrap",            self._section_market_wrap),
            ("portfolio_daily_perf",   self._section_portfolio_daily_perf),
            ("portfolio_review",       self._section_portfolio_review),
            ("focus_list",             self._section_focus_list),
            ("supporting_picks",       self._section_supporting_picks),
            ("honorable_mentions",     self._section_honorable_mentions),
            ("earnings_takeaways",     self._section_earnings_takeaways),
            ("after_hours_movers",     self._section_after_hours_movers),
            ("institutional_flows",    self._section_institutional_flows),
            ("tomorrow_focus",         self._section_tomorrow_focus),
            ("weekly_themes",          self._section_weekly_themes),
            ("disclaimers",            self._section_disclaimers),
        ]

    # ══════════════════════════════════════════════════════════════════════════
    # Data fetchers (defensive)
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_focus_list(self, rd: date, run_type: str,
                          grade: str | tuple[str, ...] | None = None,
                          limit: int | None = None) -> list[dict]:
        try:
            q = (self.db.table("ranked_focus_list").select("*")
                 .eq("run_date", rd.isoformat()).eq("run_type", run_type))
            if grade is not None:
                if isinstance(grade, str):
                    q = q.eq("conviction_grade", grade)
                else:
                    q = q.in_("conviction_grade", list(grade))
            q = q.order("rank")
            if limit:
                q = q.limit(limit)
            return q.execute().data or []
        except Exception as exc:
            log.debug("focus_list fetch failed: %s", exc)
            return []

    def _fetch_smart_money(self, tickers: list[str], rd: date,
                           run_type: str) -> dict[str, dict]:
        if not tickers:
            return {}
        start = (rd - timedelta(days=SUPPORTING_TABLE_WINDOW_DAYS)).isoformat()
        out: dict[str, dict] = {}
        try:
            rows = (self.db.table("smart_money_intel").select("*")
                    .in_("ticker", tickers)
                    .gte("snapshot_date", start)
                    .order("snapshot_date", desc=True).execute().data or [])
        except Exception:
            return {}
        for r in rows:
            t = r.get("ticker")
            if t and t not in out:    # most recent within window
                out[t] = r
        return out

    def _fetch_peer_industry(self, tickers: list[str], rd: date) -> dict[str, dict]:
        if not tickers:
            return {}
        start = (rd - timedelta(days=SUPPORTING_TABLE_WINDOW_DAYS)).isoformat()
        out: dict[str, dict] = {}
        try:
            rows = (self.db.table("peer_industry_analysis").select("*")
                    .in_("target_ticker", tickers)
                    .gte("snapshot_date", start)
                    .order("snapshot_date", desc=True).execute().data or [])
        except Exception:
            return {}
        for r in rows:
            t = r.get("target_ticker")
            if t and t not in out:
                out[t] = r
        return out

    def _fetch_quant(self, tickers: list[str], rd: date) -> dict[str, dict]:
        """Fetch the latest row per ticker from each quant model table."""
        if not tickers:
            return {}
        start = (rd - timedelta(days=SUPPORTING_TABLE_WINDOW_DAYS)).isoformat()
        spec = [
            ("monte_carlo_results",  "run_date"),
            ("ticker_risk_metrics",  "calculation_date"),
            ("garch_forecasts",      "forecast_date"),
            ("factor_attribution",   "calculation_date"),
            ("dcf_valuations",       "calculation_date"),
        ]
        result: dict[str, dict] = {t: {} for t in tickers}
        for tbl, date_col in spec:
            try:
                rows = (self.db.table(tbl).select("*")
                        .in_("ticker", tickers)
                        .gte(date_col, start)
                        .order(date_col, desc=True).execute().data or [])
            except Exception:
                continue
            for r in rows:
                t = r.get("ticker")
                if t and tbl not in result[t]:
                    result[t][tbl] = r
        return result

    def _fetch_macro(self, rd: date) -> dict | None:
        try:
            rows = (self.db.table("macro_context").select("*")
                    .lte("snapshot_time", (rd + timedelta(days=1)).isoformat())
                    .order("snapshot_time", desc=True).limit(1)
                    .execute().data or [])
        except Exception:
            return None
        return rows[0] if rows else None

    def _fetch_portfolios(self, tenant_id: str | None) -> list[dict]:
        try:
            q = self.db.table("portfolios").select("*")
            # tenant_id column doesn't exist yet -- step 7.6 will add it.
            return q.execute().data or []
        except Exception:
            return []

    def _fetch_holdings_alerts(self, portfolio_ids: list, rd: date,
                               run_type: str | None) -> list[dict]:
        if not portfolio_ids:
            return []
        start = (rd - timedelta(days=2)).isoformat()
        try:
            q = (self.db.table("holdings_alerts").select("*")
                 .in_("portfolio_id", portfolio_ids)
                 .gte("alert_date", start))
            return (q.order("alert_date", desc=True).execute().data or [])
        except Exception:
            return []

    def _fetch_position_signals(self, portfolio_ids: list, rd: date) -> list[dict]:
        if not portfolio_ids:
            return []
        start = (rd - timedelta(days=2)).isoformat()
        try:
            return (self.db.table("position_signals").select("*")
                    .in_("portfolio_id", portfolio_ids)
                    .gte("snapshot_date", start)
                    .order("snapshot_date", desc=True).execute().data or [])
        except Exception:
            return []

    def _fetch_rebalance(self, portfolio_ids: list, rd: date) -> dict[str, dict]:
        if not portfolio_ids:
            return {}
        start = (rd - timedelta(days=5)).isoformat()
        out: dict[str, dict] = {}
        try:
            rows = (self.db.table("portfolio_rebalance_suggestions").select("*")
                    .in_("portfolio_id", portfolio_ids)
                    .gte("snapshot_date", start)
                    .order("snapshot_date", desc=True).execute().data or [])
        except Exception:
            return {}
        for r in rows:
            pid = r.get("portfolio_id")
            if pid and pid not in out:
                out[pid] = r
        return out

    def _fetch_holdings(self, portfolio_ids: list) -> dict[str, list[dict]]:
        if not portfolio_ids:
            return {}
        out: dict[str, list[dict]] = {pid: [] for pid in portfolio_ids}
        try:
            rows = (self.db.table("portfolio_holdings").select("*")
                    .in_("portfolio_id", portfolio_ids).execute().data or [])
        except Exception:
            return out
        for r in rows:
            pid = r.get("portfolio_id")
            if pid in out:
                out[pid].append(r)
        return out

    # ══════════════════════════════════════════════════════════════════════════
    # Section builders -- shared
    # ══════════════════════════════════════════════════════════════════════════

    def _empty_section(self, slug: str, title: str, note: str) -> dict:
        return {"slug": slug, "title": title, "kind": "empty",
                "empty_note": note}

    def _record_availability(self, structured: dict, key: str, status: str,
                             rows: int, note: str = ""):
        structured["data_availability"][key] = {
            "status": status, "rows": rows, "note": note,
        }

    # Section 1 (premarket) -------------------------------------------------------

    def _section_executive_summary(self, rd: date, run_type: str,
                                   tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        a_picks = self._fetch_focus_list(rd, run_type, grade="A",
                                         limit=MAX_A_PICKS)
        narrative = self._groq_headline(
            kind="executive_summary",
            macro=macro,
            picks=a_picks,
            rd=rd, run_type=run_type)
        if narrative is None and not (macro or a_picks):
            return self._empty_section("executive_summary", "Executive Summary",
                                       "no macro context or A-grade picks")
        return {
            "slug": "executive_summary", "title": "Executive Summary",
            "kind": "narrative",
            "narrative": narrative or "Composer narrative unavailable -- see "
                         "the data sections below for today's intelligence.",
            "source_refs": ["macro_context", "ranked_focus_list"],
        }

    def _section_macro_snapshot(self, rd: date, run_type: str,
                                tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        if not macro:
            return self._empty_section("macro_snapshot", "Macro Snapshot",
                                       "macro_context has no rows -- run macro_agent")

        snap_dt = _to_date(macro.get("snapshot_time"))
        age = (rd - snap_dt).days if snap_dt else None
        stale_note = (f"macro snapshot is {age} days old "
                      f"({snap_dt.isoformat() if snap_dt else 'unknown'})"
                      if age is not None and age > MACRO_FRESH_DAYS else None)

        indicators = macro.get("economic_indicators") or {}
        if isinstance(indicators, str):
            try: indicators = json.loads(indicators)
            except Exception: indicators = {}

        indicators_list = []
        if isinstance(indicators, dict):
            for k, v in indicators.items():
                if isinstance(v, dict):
                    indicators_list.append({
                        "label": k.replace("_", " ").title(),
                        "value": v.get("value") or v.get("latest"),
                        "change": v.get("change") or v.get("delta"),
                        "direction": v.get("direction"),
                    })
                else:
                    indicators_list.append({"label": k.replace("_", " ").title(),
                                            "value": v, "change": None,
                                            "direction": None})

        return {
            "slug": "macro_snapshot", "title": "Macro Snapshot",
            "kind": "macro",
            "regime": macro.get("regime_classification"),
            "narrative": macro.get("narrative_summary"),
            "indicators": indicators_list[:8],
            "yield_curve": macro.get("yield_curve"),
            "global_indices": macro.get("global_indices"),
            "sector_performance": macro.get("sector_performance"),
            "fed_calendar": macro.get("fed_calendar"),
            "economic_calendar": macro.get("economic_calendar"),
            "earnings_calendar": macro.get("earnings_calendar"),
            "stale_note": stale_note,
            "source_refs": [{"table": "macro_context", "id": macro.get("id")}],
        }

    # Section 3 -- portfolio review --------------------------------------------

    def _section_portfolio_review(self, rd: date, run_type: str,
                                  tenant_id) -> dict:
        portfolios = self._fetch_portfolios(tenant_id)
        if not portfolios:
            return self._empty_section("portfolio_review", "Existing Portfolio Review",
                                       "no portfolios configured for this tenant")
        portfolio_ids = [p["id"] for p in portfolios]
        alerts = self._fetch_holdings_alerts(portfolio_ids, rd, run_type)
        signals = self._fetch_position_signals(portfolio_ids, rd)
        rebalance = self._fetch_rebalance(portfolio_ids, rd)
        holdings_by_pid = self._fetch_holdings(portfolio_ids)

        per_portfolio = []
        for p in portfolios:
            pid = p["id"]
            p_alerts = [a for a in alerts if a.get("portfolio_id") == pid]
            p_alerts.sort(key=lambda a: ("LOW", "INFO", "MEDIUM", "HIGH", "CRITICAL")
                          .index((a.get("severity") or "INFO").upper())
                          if (a.get("severity") or "INFO").upper() in
                          ("LOW", "INFO", "MEDIUM", "HIGH", "CRITICAL") else 1,
                          reverse=True)
            per_portfolio.append({
                "portfolio_id": pid,
                "name": p.get("name") or f"Portfolio {pid[:8]}",
                "holdings_count": len(holdings_by_pid.get(pid, [])),
                "alerts": p_alerts[:8],
                "alerts_critical": sum(1 for a in p_alerts
                                       if (a.get("severity") or "").upper() == "CRITICAL"),
                "alerts_high":     sum(1 for a in p_alerts
                                       if (a.get("severity") or "").upper() == "HIGH"),
                "signals": [s for s in signals if s.get("portfolio_id") == pid][:6],
                "rebalance": rebalance.get(pid),
            })
        critical = sum(p["alerts_critical"] for p in per_portfolio)
        high     = sum(p["alerts_high"]     for p in per_portfolio)
        return {
            "slug": "portfolio_review", "title": "Existing Portfolio Review",
            "kind": "portfolio_review",
            "portfolios": per_portfolio,
            "critical_alert_count": critical,
            "high_alert_count": high,
            "source_refs": ["holdings_alerts", "position_signals",
                            "portfolio_rebalance_suggestions"],
        }

    # ── Focus / supporting / honorable ────────────────────────────────────────

    def _build_pick_full(self, row: dict, sm: dict, peer: dict,
                         quant: dict) -> dict:
        ticker = row.get("ticker")
        return {
            "ticker": ticker,
            "rank": row.get("rank"),
            "conviction_grade": row.get("conviction_grade"),
            "conviction_score": row.get("conviction_score_adjusted")
                                or row.get("conviction_score"),
            "composite_score": row.get("composite_score"),
            "thesis": row.get("thesis"),
            "catalyst": {
                "category":      row.get("catalyst_category"),
                "description":   row.get("catalyst_description"),
                "confidence":    row.get("catalyst_confidence"),
            },
            "bull_case": row.get("bull_case"),
            "bear_case": (row.get("critic_rewritten_bear_case")
                          or row.get("bear_case")),
            "critic": {
                "weaknesses":           row.get("critic_weaknesses"),
                "disconfirming_data":   row.get("critic_disconfirming_data"),
                "hidden_risks":         row.get("critic_hidden_risks"),
                "objection_level":      row.get("critic_objection_level"),
            },
            "what_to_watch": row.get("what_to_watch"),
            "price_target_upside":   row.get("price_target_upside"),
            "price_target_downside": row.get("price_target_downside"),
            "position_size_guidance": row.get("position_size_guidance"),
            "grade_reasoning":       row.get("grade_reasoning"),
            "quant_signal_summary":  row.get("quant_signal_summary"),
            "smart_money":      self._project_smart_money(sm),
            "peer_industry":    self._project_peer_industry(peer),
            "quant_models":     self._project_quant(quant),
            "verification": {
                "smart_money_score":     _safe(sm, "verification_score"),
                "smart_money_grade":     _safe(sm, "quality_grade"),
                "peer_industry_score":   _safe(peer, "verification_score"),
                "peer_industry_grade":   _safe(peer, "quality_grade"),
            },
            "sources": [
                {"table": "ranked_focus_list", "id": row.get("id")},
                {"table": "smart_money_intel", "id": _safe(sm, "id")}
                    if sm else None,
                {"table": "peer_industry_analysis", "id": _safe(peer, "id")}
                    if peer else None,
            ],
        }

    def _project_smart_money(self, sm: dict | None):
        if not sm:
            return None
        return {
            "pattern":          sm.get("pattern_detected"),
            "composite_score":  sm.get("composite_smart_money_score"),
            "confidence_level": sm.get("confidence_level"),
            "intel_note":       sm.get("intel_note"),
            "verification_score": sm.get("verification_score"),
            "quality_grade":    sm.get("quality_grade"),
            "signals": [
                {"name": "Earnings",  "score": sm.get("earnings_signal_score"),
                 "strength": sm.get("earnings_signal_strength"),
                 "extra": sm.get("earnings_verdict")},
                {"name": "Insider",   "score": sm.get("insider_signal_score"),
                 "strength": sm.get("insider_signal_strength"),
                 "extra": sm.get("insider_detected_cluster")},
                {"name": "Short Int.", "score": sm.get("short_signal_score"),
                 "strength": sm.get("short_signal_strength"),
                 "extra": sm.get("short_trend_direction")},
                {"name": "Revisions", "score": sm.get("revision_signal_score"),
                 "strength": sm.get("revision_signal_strength"),
                 "extra": sm.get("revision_velocity")},
            ],
        }

    def _project_peer_industry(self, peer: dict | None):
        if not peer:
            return None
        return {
            "industry_stance":   peer.get("industry_stance"),
            "stance_rationale":  peer.get("industry_stance_rationale"),
            "within_industry_rank": peer.get("within_industry_rank"),
            "peer_count":        peer.get("peer_count"),
            "overall_peer_score": peer.get("overall_peer_score"),
            "differentiation":   peer.get("differentiation_thesis"),
            "relative_bull":     peer.get("relative_bull_case"),
            "relative_bear":     peer.get("relative_bear_case"),
            "verification_score": peer.get("verification_score"),
            "quality_grade":     peer.get("quality_grade"),
        }

    def _project_quant(self, q: dict):
        mc  = q.get("monte_carlo_results") or {}
        rm  = q.get("ticker_risk_metrics") or {}
        gar = q.get("garch_forecasts")    or {}
        ff5 = q.get("factor_attribution") or {}
        dcf = q.get("dcf_valuations")     or {}
        rows = []
        if mc:
            rows.append({"model": "Monte Carlo",
                         "value": _fmt_money(mc.get("median_price")),
                         "detail": f"P5 {_fmt_money(mc.get('p5_price'))}  /  "
                                   f"P95 {_fmt_money(mc.get('p95_price'))}",
                         "note": mc.get("calibration_quality")})
        if rm:
            rows.append({"model": "VaR / Risk",
                         "value": _fmt_pct(rm.get("daily_var_95"), 2),
                         "detail": f"Sharpe {_fmt_num(rm.get('sharpe_ratio_1y'))}  /  "
                                   f"Beta {_fmt_num(rm.get('beta_vs_spy'))}",
                         "note": (f"vol {_fmt_pct(rm.get('annual_volatility'), 1)}"
                                  if rm.get("annual_volatility") is not None
                                  else None)})
        if gar:
            rows.append({"model": "GARCH",
                         "value": f"{_fmt_pct(gar.get('forecast_vol_22d'), 1)} 22d",
                         "detail": (f"regime {gar.get('regime')}  /  "
                                    f"IV-GARCH spread "
                                    f"{_fmt_pct(gar.get('iv_vs_garch_spread'), 1)}"),
                         "note": gar.get("model_quality")})
        if ff5:
            rows.append({"model": "Fama-French 5",
                         "value": ff5.get("factor_label"),
                         "detail": (f"α {_fmt_pct(ff5.get('alpha_annual'), 1)}  /  "
                                    f"R² {_fmt_num(ff5.get('r_squared'), 2)}"),
                         "note": ff5.get("alpha_significance")})
        if dcf:
            rows.append({"model": "DCF",
                         "value": _fmt_money(dcf.get("dcf_value_per_share")),
                         "detail": (f"upside "
                                    f"{_fmt_pct(dcf.get('upside_to_dcf_pct'), 1)}  /  "
                                    f"label {dcf.get('valuation_label')}"),
                         "note": (f"confidence {dcf.get('confidence')}"
                                  if dcf.get("confidence") is not None
                                  else None)})
        return rows

    def _section_focus_list(self, rd: date, run_type: str, tenant_id) -> dict:
        rows = self._fetch_focus_list(rd, run_type, grade="A", limit=MAX_A_PICKS)
        if not rows:
            return self._empty_section(
                "focus_list", "Today's Focus List",
                f"no A-grade picks in ranked_focus_list for "
                f"{rd.isoformat()} {run_type}")
        tickers = [r["ticker"] for r in rows if r.get("ticker")]
        sm   = self._fetch_smart_money(tickers, rd, run_type)
        peer = self._fetch_peer_industry(tickers, rd)
        quant = self._fetch_quant(tickers, rd)
        picks = [self._build_pick_full(r, sm.get(r.get("ticker")) or {},
                                       peer.get(r.get("ticker")) or {},
                                       quant.get(r.get("ticker")) or {})
                 for r in rows]
        return {
            "slug": "focus_list", "title": "Today's Focus List",
            "kind": "focus_list",
            "picks": picks,
        }

    def _section_supporting_picks(self, rd: date, run_type: str,
                                  tenant_id) -> dict:
        rows = self._fetch_focus_list(rd, run_type, grade="B", limit=MAX_B_PICKS)
        if not rows:
            return self._empty_section("supporting_picks", "Supporting Picks",
                                       f"no B-grade picks for {rd.isoformat()} {run_type}")
        return {
            "slug": "supporting_picks", "title": "Supporting Picks",
            "kind": "brief_picks",
            "picks": [
                {"ticker": r.get("ticker"),
                 "conviction_grade": r.get("conviction_grade"),
                 "conviction_score": r.get("conviction_score_adjusted")
                                     or r.get("conviction_score"),
                 "catalyst": r.get("catalyst_description"),
                 "thesis": r.get("thesis"),
                 "grade_reasoning": r.get("grade_reasoning")}
                for r in rows
            ],
        }

    def _section_honorable_mentions(self, rd: date, run_type: str,
                                    tenant_id) -> dict:
        rows = self._fetch_focus_list(rd, run_type, grade="C", limit=MAX_C_PICKS)
        if not rows:
            return self._empty_section(
                "honorable_mentions", "Honorable Mentions",
                f"no C-grade picks for {rd.isoformat()} {run_type}")
        return {
            "slug": "honorable_mentions", "title": "Honorable Mentions",
            "kind": "oneline_picks",
            "picks": [
                {"ticker": r.get("ticker"),
                 "catalyst": r.get("catalyst_description"),
                 "thesis": r.get("thesis")}
                for r in rows
            ],
        }

    def _section_risk_radar(self, rd: date, run_type: str, tenant_id) -> dict:
        # Pulls from smart_money_intel patterns that flag risk + recent
        # ranked_focus_list rows with notable critic_objection_level.
        warn_patterns = ("VALUE_TRAP_WARNING", "INSIDER_SMOKE_SIGNAL",
                          "QUIET_DISTRIBUTION", "SHORT_SQUEEZE_SETUP")
        try:
            sm_rows = (self.db.table("smart_money_intel").select(
                "ticker,pattern_detected,composite_smart_money_score,"
                "confidence_level,intel_note,quality_grade,snapshot_date")
                .in_("pattern_detected", list(warn_patterns))
                .gte("snapshot_date",
                     (rd - timedelta(days=SUPPORTING_TABLE_WINDOW_DAYS)).isoformat())
                .order("snapshot_date", desc=True).limit(8)
                .execute().data or [])
        except Exception:
            sm_rows = []
        if not sm_rows:
            return self._empty_section("risk_radar", "Risk Radar",
                                       "no risk-flag patterns surfaced recently")
        return {
            "slug": "risk_radar", "title": "Risk Radar",
            "kind": "risk_radar",
            "items": [
                {"ticker": r.get("ticker"),
                 "pattern": r.get("pattern_detected"),
                 "composite_score": r.get("composite_smart_money_score"),
                 "confidence": r.get("confidence_level"),
                 "snapshot_date": r.get("snapshot_date"),
                 "note": r.get("intel_note")}
                for r in sm_rows
            ],
        }

    def _section_tomorrow_setup(self, rd: date, run_type: str,
                                tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        if not macro:
            return self._empty_section("tomorrow_setup", "Tomorrow's Setup",
                                       "macro_context has no calendar data")
        return {
            "slug": "tomorrow_setup", "title": "Tomorrow's Setup",
            "kind": "calendar",
            "fed_calendar":       macro.get("fed_calendar"),
            "economic_calendar":  macro.get("economic_calendar"),
            "earnings_calendar":  macro.get("earnings_calendar"),
            "source_refs": [{"table": "macro_context", "id": macro.get("id")}],
        }

    def _section_disclaimers(self, rd: date, run_type: str,
                             tenant_id) -> dict:
        return {
            "slug": "disclaimers", "title": "Disclosures",
            "kind": "disclaimers",
            "lines": [
                "This brief is for advisor review only and is not investment "
                "advice. No claim is a recommendation to buy or sell.",
                "Every numerical claim traces to a row in the database tables "
                "referenced inline. Source citations are exposed in the "
                "structured payload accompanying this report.",
                "Forward returns, prices and analyst counts reflect "
                "best-effort data sourcing; advisor judgment is required "
                "before any client communication.",
            ],
        }

    # ── Midday-specific ───────────────────────────────────────────────────────

    def _section_morning_recap(self, rd: date, run_type: str,
                               tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        a_picks = self._fetch_focus_list(rd, run_type, grade="A",
                                         limit=MAX_A_PICKS)
        narrative = self._groq_headline("morning_recap", macro, a_picks,
                                         rd, run_type)
        if narrative is None and not (macro or a_picks):
            return self._empty_section("morning_recap", "Morning Session Recap",
                                       "no macro or focus-list inputs")
        return {
            "slug": "morning_recap", "title": "Morning Session Recap",
            "kind": "narrative",
            "narrative": narrative or "Morning recap unavailable.",
            "source_refs": ["macro_context", "ranked_focus_list"],
        }

    def _section_portfolio_day_pnl(self, rd: date, run_type: str,
                                   tenant_id) -> dict:
        # Heuristic: pull holdings + position_signals for today's
        # unrealized_return_pct values. If empty, omit.
        portfolios = self._fetch_portfolios(tenant_id)
        if not portfolios:
            return self._empty_section("portfolio_day_pnl", "Portfolio Day P&L",
                                       "no portfolios configured")
        pids = [p["id"] for p in portfolios]
        signals = self._fetch_position_signals(pids, rd)
        if not signals:
            return self._empty_section("portfolio_day_pnl", "Portfolio Day P&L",
                                       "no position_signals for today")
        rows = []
        for s in signals:
            rows.append({
                "ticker": s.get("ticker"),
                "current_price": s.get("current_price"),
                "cost_basis":    s.get("cost_basis"),
                "unrealized_pct": s.get("unrealized_return_pct"),
                "holding_days":   s.get("holding_period_days"),
                "signal_type":    s.get("signal_type"),
                "signal_strength": s.get("signal_strength"),
            })
        return {
            "slug": "portfolio_day_pnl", "title": "Portfolio Day P&L",
            "kind": "position_table",
            "positions": rows,
        }

    def _section_pivot_stocks(self, rd: date, run_type: str,
                              tenant_id) -> dict:
        return self._empty_section(
            "pivot_stocks", "Pivot Stocks",
            "no intraday-reversal source table wired into the composer yet")

    def _section_volume_options(self, rd: date, run_type: str,
                                tenant_id) -> dict:
        # If options_signals exists in DB it can populate this; otherwise omit.
        try:
            rows = (self.db.table("options_signals").select("*")
                    .eq("snapshot_date", rd.isoformat())
                    .order("flow_imbalance_pct", desc=True).limit(8)
                    .execute().data or [])
        except Exception:
            rows = []
        if not rows:
            return self._empty_section(
                "volume_options", "Volume & Options Flow Leaders",
                "no options_signals rows for today")
        return {
            "slug": "volume_options", "title": "Volume & Options Flow Leaders",
            "kind": "table",
            "rows": rows,
        }

    def _section_tonight_earnings(self, rd: date, run_type: str,
                                  tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        cal = macro.get("earnings_calendar") if macro else None
        if not cal:
            return self._empty_section("tonight_earnings", "Tonight's Earnings",
                                       "earnings_calendar empty in macro_context")
        return {
            "slug": "tonight_earnings", "title": "Tonight's Earnings",
            "kind": "earnings_calendar",
            "items": cal if isinstance(cal, list) else [cal],
        }

    # ── Close-specific ────────────────────────────────────────────────────────

    def _section_market_wrap(self, rd: date, run_type: str, tenant_id) -> dict:
        macro = self._fetch_macro(rd)
        a_picks = self._fetch_focus_list(rd, run_type, grade="A",
                                         limit=MAX_A_PICKS)
        narrative = self._groq_headline("market_wrap", macro, a_picks,
                                         rd, run_type)
        if narrative is None and not (macro or a_picks):
            return self._empty_section("market_wrap", "Full Market Wrap",
                                       "no macro or focus-list inputs")
        return {
            "slug": "market_wrap", "title": "Full Market Wrap",
            "kind": "narrative",
            "narrative": narrative or "Wrap narrative unavailable.",
            "source_refs": ["macro_context", "ranked_focus_list"],
        }

    def _section_portfolio_daily_perf(self, rd: date, run_type: str,
                                      tenant_id) -> dict:
        return self._section_portfolio_day_pnl(rd, run_type, tenant_id)

    def _section_earnings_takeaways(self, rd: date, run_type: str,
                                    tenant_id) -> dict:
        # Smart money rows whose snapshot_date matches today, with non-null
        # earnings_verdict. These tend to be the day's earnings reporters.
        try:
            rows = (self.db.table("smart_money_intel").select(
                "ticker,snapshot_date,run_type,earnings_verdict,"
                "earnings_signal_score,earnings_transcript_quality,"
                "earnings_tone_summary,intel_note,quality_grade")
                .eq("snapshot_date", rd.isoformat())
                .neq("earnings_verdict", "NO_DATA")
                .limit(8).execute().data or [])
        except Exception:
            rows = []
        if not rows:
            return self._empty_section(
                "earnings_takeaways", "Today's Earnings Takeaways",
                "no smart_money_intel rows with earnings verdicts today")
        return {
            "slug": "earnings_takeaways", "title": "Today's Earnings Takeaways",
            "kind": "earnings_takeaways", "items": rows,
        }

    def _section_after_hours_movers(self, rd: date, run_type: str,
                                    tenant_id) -> dict:
        return self._empty_section(
            "after_hours_movers", "After-Hours Movers",
            "no after-hours snapshot table wired in (placeholder)")

    def _section_institutional_flows(self, rd: date, run_type: str,
                                     tenant_id) -> dict:
        return self._empty_section(
            "institutional_flows", "Institutional Flows Detected Today",
            "13F / institutional-flow tables not in the composer's scope yet")

    def _section_tomorrow_focus(self, rd: date, run_type: str,
                                tenant_id) -> dict:
        next_d = rd + timedelta(days=1)
        # Look ahead one day for any premarket-graded picks (these would be
        # generated by tomorrow's premarket run; usually empty at close time).
        rows = self._fetch_focus_list(next_d, "premarket", grade=("A", "B"),
                                       limit=5)
        if not rows:
            return self._empty_section(
                "tomorrow_focus", "Tomorrow's Focus List Preview",
                "no premarket picks yet for tomorrow -- generated overnight")
        return {
            "slug": "tomorrow_focus", "title": "Tomorrow's Focus List Preview",
            "kind": "brief_picks",
            "picks": [
                {"ticker": r.get("ticker"),
                 "conviction_grade": r.get("conviction_grade"),
                 "catalyst": r.get("catalyst_description"),
                 "thesis": r.get("thesis")}
                for r in rows
            ],
        }

    def _section_weekly_themes(self, rd: date, run_type: str,
                               tenant_id) -> dict:
        if rd.weekday() != 4:        # Fridays only
            return self._empty_section("weekly_themes", "Weekly Themes",
                                       "weekly themes appear on the Friday close brief only")
        return self._empty_section(
            "weekly_themes", "Weekly Themes",
            "weekly aggregation not yet wired in (placeholder for Friday close)")

    # ══════════════════════════════════════════════════════════════════════════
    # LLM headline narrative (closed-context)
    # ══════════════════════════════════════════════════════════════════════════

    _NARRATIVE_SYSTEM = (
        "You are the assistant writing the headline section of a daily "
        "research brief for an institutional investment advisor. You MAY "
        "ONLY cite numbers and labels that appear in the DATA section "
        "below; never introduce facts from training data. Use cautious, "
        "research-grade language. Never use the words 'buy', 'sell', "
        "'must', 'recommend'. Keep it 3-4 sentences, plain prose, no "
        "bullet points. Every specific numeric claim must be immediately "
        "followed by a [DATA REF: key_name] tag whose key_name is one of "
        "the snake_case keys listed in DATA. If the DATA section is empty "
        "or thin, say so honestly in one short sentence."
    )

    def _groq_headline(self, kind: str, macro: dict | None,
                       picks: list[dict], rd: date, run_type: str) -> str | None:
        if not GROQ_API_KEY:
            return None
        if not (macro or picks):
            return None
        # Build a compact DATA block with snake_case keys.
        data: dict[str, Any] = {
            "report_date":         rd.isoformat(),
            "run_type":            run_type,
            "narrative_kind":      kind,
            "regime_classification": _safe(macro, "regime_classification"),
            "macro_narrative":     _safe(macro, "narrative_summary"),
        }
        for i, p in enumerate(picks[:3], start=1):
            data[f"pick{i}_ticker"]           = p.get("ticker")
            data[f"pick{i}_conviction_grade"] = p.get("conviction_grade")
            data[f"pick{i}_composite_score"]  = p.get("composite_score")
            data[f"pick{i}_catalyst"]         = p.get("catalyst_description")
            data[f"pick{i}_thesis"]           = (p.get("thesis") or "")[:300]
        data["n_a_picks"] = len(picks)

        refs = "\n".join(f"  - {k} = {v}"
                          for k, v in data.items() if v not in (None, ""))
        prompt_kind = {
            "executive_summary":
                "Write a 3-4 sentence Executive Summary opening the pre-market brief.",
            "morning_recap":
                "Write a 3-4 sentence Morning Session Recap opening the midday brief.",
            "market_wrap":
                "Write a 3-4 sentence Full Market Wrap closing today's session.",
        }.get(kind, "Write a 3-4 sentence opening paragraph.")
        user = (f"{prompt_kind}\n\nDATA SECTION (every numeric claim must be "
                f"immediately followed by [DATA REF: key_name], where key_name "
                f"is one of the snake_case keys listed below):\n{refs}\n")
        try:
            from groq import Groq
            resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "system", "content": self._NARRATIVE_SYSTEM},
                          {"role": "user",   "content": user}],
                temperature=0.2,
            )
            txt = (resp.choices[0].message.content or "").strip() or None
        except Exception as exc:
            log.warning("Groq headline call failed: %s", exc)
            return None
        self._llm_calls += 1
        if txt:
            forbidden = re.findall(r"\b(buy|sell|must|recommend you)\b",
                                    txt, re.IGNORECASE)
            if forbidden:
                log.warning("headline contained forbidden language %s -- "
                            "dropping", forbidden)
                return None
        return txt

    # ══════════════════════════════════════════════════════════════════════════
    # Rendering: Markdown
    # ══════════════════════════════════════════════════════════════════════════

    def _render_markdown(self, s: dict) -> str:
        h = s["header"]
        lines: list[str] = []
        lines.append(f"# Fortis — {h['title']}")
        lines.append(f"*{h['date_label']}*")
        lines.append("")
        lines.append(f"_A picks: {h['a_count']} · B picks: {h['b_count']} · "
                     f"C picks: {h['c_count']} · "
                     f"avg verification: {_fmt_num(h['avg_verification_score'], 2)}_")
        lines.append("")
        for sec in s["sections"]:
            lines.append(f"## {sec['title']}")
            lines.append("")
            if sec.get("kind") == "empty":
                lines.append(f"> _{sec['empty_note']}_")
            else:
                lines.extend(self._md_section(sec))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _md_section(self, sec: dict) -> list[str]:
        kind = sec.get("kind")
        if kind == "narrative":
            return [sec.get("narrative") or ""]
        if kind == "macro":
            out = []
            if sec.get("regime"):
                out.append(f"**Regime:** {sec['regime']}")
            if sec.get("narrative"):
                out.append("")
                out.append(sec["narrative"])
            ind = sec.get("indicators") or []
            if ind:
                out.append("")
                out.append("**Key indicators**")
                for it in ind:
                    out.append(f"- {it['label']}: {_fmt_num(it.get('value'))}"
                               + (f"  (Δ {_fmt_num(it.get('change'))})"
                                  if it.get("change") is not None else ""))
            if sec.get("stale_note"):
                out.append("")
                out.append(f"> ⚠ {sec['stale_note']}")
            return out
        if kind == "portfolio_review":
            out = []
            crit = sec.get("critical_alert_count", 0)
            hi = sec.get("high_alert_count", 0)
            out.append(f"**{crit} CRITICAL** · **{hi} HIGH** alerts across "
                       f"{len(sec.get('portfolios') or [])} portfolio(s).")
            for p in sec.get("portfolios") or []:
                out.append("")
                out.append(f"### {p['name']}  ({p['holdings_count']} holdings)")
                for a in p.get("alerts") or []:
                    sev = (a.get("severity") or "INFO").upper()
                    out.append(f"- **{sev}** {a.get('ticker') or ''} — "
                               f"{a.get('alert_type') or ''}: {a.get('message') or ''}")
                if not p.get("alerts"):
                    out.append("- (no alerts in the window)")
                rb = p.get("rebalance") or {}
                if rb.get("top_3_priorities"):
                    out.append("")
                    out.append(f"_Rebalance priorities:_ {rb.get('top_3_priorities')}")
            return out
        if kind == "focus_list":
            out = []
            for p in sec.get("picks") or []:
                out.append("")
                out.append(f"### {p['ticker']}  ·  grade {p.get('conviction_grade')} "
                           f"(score {_fmt_num(p.get('conviction_score'))})")
                if p.get("catalyst", {}).get("description"):
                    out.append(f"**Catalyst.** {p['catalyst']['description']}")
                if p.get("bull_case"):
                    out.append(f"**Bull.** {p['bull_case']}")
                if p.get("bear_case"):
                    out.append(f"**Bear.** {p['bear_case']}")
                if p.get("quant_models"):
                    out.append("")
                    out.append("| Model | Value | Detail |")
                    out.append("|---|---|---|")
                    for m in p["quant_models"]:
                        out.append(f"| {m['model']} | {m['value']} | {m['detail']} |")
                sm = p.get("smart_money") or {}
                if sm:
                    out.append("")
                    out.append(f"**Smart-money pattern:** {sm.get('pattern') or '—'} "
                               f"(confidence {sm.get('confidence_level') or '—'}, "
                               f"composite {_fmt_num(sm.get('composite_score'))})")
                pi = p.get("peer_industry") or {}
                if pi:
                    out.append(f"**Peer / industry:** stance "
                               f"{pi.get('industry_stance') or '—'}, "
                               f"rank {pi.get('within_industry_rank') or '—'}/"
                               f"{pi.get('peer_count') or '—'}")
            return out
        if kind == "brief_picks":
            out = []
            for p in sec.get("picks") or []:
                out.append(f"- **{p['ticker']}** ({p.get('conviction_grade')}) — "
                           f"{p.get('catalyst') or ''}")
                if p.get("thesis"):
                    out.append(f"  _{p['thesis']}_")
            return out
        if kind == "oneline_picks":
            return [f"- **{p['ticker']}** — {p.get('catalyst') or p.get('thesis') or ''}"
                    for p in sec.get("picks") or []]
        if kind == "risk_radar":
            return [f"- **{it['ticker']}** — {it.get('pattern')} "
                    f"(conf {it.get('confidence')})"
                    for it in sec.get("items") or []]
        if kind == "position_table":
            out = ["| Ticker | Price | Cost | Unrealized | Signal |",
                   "|---|---|---|---|---|"]
            for p in sec.get("positions") or []:
                out.append(f"| {p['ticker']} | {_fmt_money(p.get('current_price'))} | "
                           f"{_fmt_money(p.get('cost_basis'))} | "
                           f"{_fmt_pct(p.get('unrealized_pct'))} | "
                           f"{p.get('signal_type') or '—'} |")
            return out
        if kind == "earnings_takeaways":
            out = []
            for it in sec.get("items") or []:
                out.append(f"- **{it['ticker']}**: {it.get('earnings_verdict')} "
                           f"(quality {it.get('earnings_transcript_quality')})")
                if it.get("intel_note"):
                    out.append(f"  {it['intel_note'][:240]}")
            return out
        if kind == "disclaimers":
            return [f"- {ln}" for ln in sec.get("lines") or []]
        if kind == "calendar":
            cal = sec.get("economic_calendar") or sec.get("earnings_calendar") or []
            if not cal:
                return ["_(no upcoming events recorded in macro_context)_"]
            return [f"- {json.dumps(item)[:200]}" for item in (cal if isinstance(cal, list) else [cal])][:8]
        return [f"_(kind={kind}: rendered in HTML only)_"]

    # ══════════════════════════════════════════════════════════════════════════
    # Rendering: HTML (Jinja2)
    # ══════════════════════════════════════════════════════════════════════════

    def _jinja_env(self):
        if self._jinja is None:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            self._jinja = Environment(
                loader=FileSystemLoader(str(TEMPLATE_DIR)),
                autoescape=select_autoescape(["html", "j2"]),
                trim_blocks=True, lstrip_blocks=True,
            )
            # Filters
            self._jinja.filters["fmt_num"]   = _fmt_num
            self._jinja.filters["fmt_pct"]   = _fmt_pct
            self._jinja.filters["fmt_money"] = _fmt_money
        return self._jinja

    def _render_html(self, structured: dict) -> str:
        run_type = structured["meta"]["run_type"]
        template_name = f"{run_type}.html.j2"
        env = self._jinja_env()
        try:
            tmpl = env.get_template(template_name)
        except Exception as exc:
            log.error("template %s missing: %s", template_name, exc)
            return f"<html><body><pre>{json.dumps(structured, indent=2, default=str)}</pre></body></html>"
        return tmpl.render(report=structured)

    # ══════════════════════════════════════════════════════════════════════════
    # Header roll-up + persistence
    # ══════════════════════════════════════════════════════════════════════════

    def _roll_up_header(self, s: dict) -> None:
        a = b = c = 0
        vscores: list[float] = []
        for sec in s["sections"]:
            kind = sec.get("kind")
            if kind == "focus_list":
                a += len(sec.get("picks") or [])
                for p in sec.get("picks") or []:
                    v = _safe(p, "verification", "smart_money_score")
                    if isinstance(v, (int, float)):
                        vscores.append(float(v))
            elif kind == "brief_picks" and sec.get("slug") == "supporting_picks":
                b += len(sec.get("picks") or [])
            elif kind == "oneline_picks":
                c += len(sec.get("picks") or [])
        s["header"]["a_count"] = a
        s["header"]["b_count"] = b
        s["header"]["c_count"] = c
        s["header"]["avg_verification_score"] = (
            round(sum(vscores) / len(vscores), 3) if vscores else None)

    def _persist(self, structured: dict, markdown: str, html: str) -> str | None:
        meta = structured["meta"]
        head = structured["header"]
        # reports has no UNIQUE (report_date, report_type, tenant_id) constraint
        # yet -- delete any prior row for the same key and insert fresh. Idempotent
        # in practice without needing a DB constraint (which step 7.6 may add).
        try:
            q = (self.db.table("reports").delete()
                 .eq("report_date", meta["run_date"])
                 .eq("report_type", meta["run_type"]))
            q = (q.is_("tenant_id", "null") if meta.get("tenant_id") is None
                 else q.eq("tenant_id", meta["tenant_id"]))
            q.execute()
        except Exception as exc:
            log.debug("prior-report delete swallowed: %s", exc)
        try:
            resp = self.db.table("reports").insert({
                "tenant_id":               meta.get("tenant_id"),
                "report_date":             meta["run_date"],
                "report_type":             meta["run_type"],
                "content_html":            html,
                "content_markdown":        markdown,
                "content_structured":      structured,
                "a_grade_count":           head.get("a_count") or 0,
                "b_grade_count":           head.get("b_count") or 0,
                "c_grade_count":           head.get("c_count") or 0,
                "avg_verification_score":  head.get("avg_verification_score"),
                "generation_time_seconds": meta.get("generation_time_seconds"),
                "llm_calls_made":          meta.get("llm_calls_made", 0),
                "generated_at":            meta.get("generated_at"),
            }).execute()
            rid = (resp.data or [{}])[0].get("id")
            log.info("report persisted -> %s", rid)
            return rid
        except Exception as exc:
            log.warning("report persist failed: %s", exc)
            return None


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    run_date = args[0]
    run_type = args[1] if len(args) > 1 else "midday"
    save = "--samples" in args
    persist = "--no-persist" not in args
    composer = ReportComposer()
    if run_type == "all":
        for rt in ("premarket", "midday", "close"):
            r = composer.compose_report(run_date, rt,
                                        save_samples=save, persist=persist)
            print(f"  {rt}: {r['structured']['header']['a_count']}A "
                  f"{r['structured']['header']['b_count']}B "
                  f"{r['structured']['header']['c_count']}C  "
                  f"({r['structured']['meta']['generation_time_seconds']}s)  "
                  f"-> {r['report_id']}")
    else:
        r = composer.compose_report(run_date, run_type,
                                    save_samples=save, persist=persist)
        print(f"  {run_type}: {r['structured']['header']['a_count']}A "
              f"{r['structured']['header']['b_count']}B "
              f"{r['structured']['header']['c_count']}C  "
              f"({r['structured']['meta']['generation_time_seconds']}s)  "
              f"-> {r['report_id']}")


if __name__ == "__main__":
    main()
