"""
Debate Synthesizer
Writes the institutional-grade bull/bear thesis for each top-ranked ticker.

For each of the top N tickers in today's ranked_focus_list it assembles the
full context the rest of the pipeline has gathered -- catalyst, news flow, SEC
filings, technical setup, options positioning, historical analog, crowd
consensus, validated-strength components -- and asks Groq Llama 3.3 70B
(falling back to Gemini 2.0 Flash) to write a structured thesis:

    THESIS / BULL_CASE / BEAR_CASE / PRICE_TARGET / POSITION_SIZE_GUIDANCE /
    WHAT_TO_WATCH

The parsed result is written back onto ranked_focus_list (migrations 011/050):
    thesis, bull_case, bear_case, price_target_upside, price_target_downside,
    position_size_guidance, what_to_watch, thesis_generated_at.
Honest price reference levels come from the deterministic zero-drift Monte
Carlo cone (pipeline/scan/price_bands.py -> price_reference), NOT from the LLM
PRICE_TARGET line, which is kept for legacy/report use only.

Usage:
    python pipeline/agents/debate_synthesizer.py [N]      # default N = 30
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pandas_ta as ta  # noqa: F401  -- registers df.ta accessor
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.factcheck_agent import factcheck, strip_data_refs  # noqa: E402
from config import (  # noqa: E402
    GEMINI_API_KEY, GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402
from llm import complete  # noqa: E402 -- shared provider-waterfall gateway

DEFAULT_N    = 20              # matches run_scan.DEFAULT_TOP_N (LLM budget)
RANK_SOURCE  = "midday"        # which ranked_focus_list run to read
GROQ_MODEL   = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _fetch_all(query_fn, page: int = 1000) -> list[dict]:
    rows, start = [], 0
    while True:
        chunk = query_fn().range(start, start + page - 1).execute().data or []
        rows.extend(chunk)
        if len(chunk) < page:
            return rows
        start += page


# ══════════════════════════════════════════════════════════════════════════════
# Technical setup -- computed fresh (market_snapshots' TA columns may be empty)
# ══════════════════════════════════════════════════════════════════════════════

def _technicals(tickers: list[str]) -> dict[str, dict]:
    """{ticker: technical summary dict} from 1y of yfinance data."""
    try:
        raw = yf.download(tickers, period="1y", interval="1d", auto_adjust=True,
                          progress=False, group_by="ticker")
    except Exception as exc:
        log.warning("yfinance technical download failed: %s", exc)
        return {}
    out: dict[str, dict] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            df = (raw[t] if multi else raw).dropna(how="all").rename(columns=str.lower)
            if len(df) < 60:
                continue
            close = df["close"]
            price = float(close.iloc[-1])
            prev  = float(close.iloc[-2])
            rsi = df.ta.rsi(length=14)
            macd = df.ta.macd()
            sma50  = df.ta.sma(length=50)
            sma200 = df.ta.sma(length=200)
            rsi_v = float(rsi.dropna().iloc[-1]) if rsi is not None and rsi.dropna().size else None
            macd_h = None
            if macd is not None and not macd.empty:
                hc = next((c for c in macd.columns if c.startswith("MACDh")), None)
                if hc and macd[hc].dropna().size:
                    macd_h = float(macd[hc].dropna().iloc[-1])
            s50  = float(sma50.dropna().iloc[-1])  if sma50 is not None and sma50.dropna().size else None
            s200 = float(sma200.dropna().iloc[-1]) if sma200 is not None and sma200.dropna().size else None
            out[t] = {
                "price":        round(price, 2),
                "prev_close":   round(prev, 2),
                "day_change":   round((price - prev) / prev * 100, 2) if prev else None,
                "high_52w":     round(float(df["high"].tail(252).max()), 2),
                "low_52w":      round(float(df["low"].tail(252).min()), 2),
                "rsi":          round(rsi_v, 1) if rsi_v is not None else None,
                "rsi_state":    ("oversold" if rsi_v is not None and rsi_v < 30
                                 else "overbought" if rsi_v is not None and rsi_v > 70
                                 else "neutral"),
                "macd":         "bullish" if (macd_h or 0) > 0 else "bearish",
                "sma50":        round(s50, 2) if s50 else None,
                "sma200":       round(s200, 2) if s200 else None,
                "vs_sma50":     (f"{(price/s50-1)*100:+.1f}% ({'above' if price>=s50 else 'below'})"
                                 if s50 else "n/a"),
                "vs_sma200":    (f"{(price/s200-1)*100:+.1f}% ({'above' if price>=s200 else 'below'})"
                                 if s200 else "n/a"),
                "support":      round(float(df["low"].iloc[-21:-1].min()), 2),
                "resistance":   round(float(df["high"].iloc[-21:-1].max()), 2),
            }
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Context assembly
# ══════════════════════════════════════════════════════════════════════════════

def _assemble(ticker: str, rfl: dict, tech: dict, news, sec, options,
              patterns, debates, validated, smart_money=None) -> dict:
    """Build the textual context blocks for one ticker's prompt."""
    t = tech.get(ticker, {})

    # News -- latest 5 with sentiment.
    nlist = sorted(news.get(ticker, []),
                   key=lambda n: n.get("published_at") or "", reverse=True)[:5]
    news_summary = "\n".join(
        f"  - {n.get('headline')} (sentiment {(_num(n.get('sentiment_score')) or 0):+.2f})"
        for n in nlist) or "  - No recent headlines."

    # SEC filings last 30 days.
    sfl = sec.get(ticker, [])
    sec_summary = ("; ".join(f"{f.get('form_type')} {(f.get('filing_date') or '')[:10]}"
                             for f in sfl[:6]) or "None in the last 30 days.")

    # Options positioning.
    o = options.get(ticker)
    if o:
        options_summary = (
            f"pattern={o.get('pattern_detected')}, "
            f"P/C ratio={o.get('put_call_ratio')}, "
            f"{len(o.get('unusual_call_strikes') or [])} unusual call / "
            f"{len(o.get('unusual_put_strikes') or [])} unusual put strikes, "
            f"{len(o.get('large_block_alerts') or [])} large blocks")
    else:
        options_summary = "No options-signal data."

    # Historical analog.
    p = patterns.get(ticker)
    if p:
        pattern_summary = (
            f"Similar past setups returned {(_num(p.get('avg_5d_return')) or 0):+.1f}% "
            f"over 5 days (win rate {(_num(p.get('win_rate_5d')) or 0)*100:.0f}%), "
            f"{(_num(p.get('avg_20d_return')) or 0):+.1f}% over 20 days; "
            f"analog confidence {p.get('confidence')}. {p.get('sample_analog_text') or ''}")
    else:
        pattern_summary = "No historical-analog data available."

    # Crowd consensus.
    d = debates.get(ticker)
    if d:
        crowd_summary = (f"attention {d.get('attention_score')}/100. "
                         f"Bull: {d.get('bull_case') or 'n/a'} "
                         f"Bear: {d.get('bear_case') or 'n/a'}")
    else:
        crowd_summary = "No crowd-intelligence data (social debate not yet run)."

    # Validated-strength components.
    v = validated.get(ticker)
    sig = rfl.get("signals") or {}
    if v:
        cats = [c for c, info in (v.get("category_signals") or {}).items()
                if isinstance(info, dict) and info.get("present")]
        validated_summary = (
            f"confirmation {v.get('confirmation_count')}/6 categories "
            f"({', '.join(cats) or 'n/a'}), "
            f"direction alignment {v.get('direction_alignment')}, "
            f"validated_strength {v.get('validated_strength')}"
            + (", CONTRADICTION flagged" if v.get("contradiction_flag") else ""))
    else:
        validated_summary = (f"composite rank score {rfl.get('composite_score')}, "
                             f"confirmation multiplier "
                             f"{sig.get('confirmation_multiplier', 'n/a')}")

    # Smart Money signals (Step 6.7).
    sm = (smart_money or {}).get(ticker) if smart_money else None
    if sm:
        smart_money_summary = (
            f"pattern={sm.get('pattern_detected')} "
            f"(confidence={sm.get('confidence_level')}, composite="
            f"{sm.get('composite_smart_money_score')}). "
            f"Earnings={sm.get('earnings_signal_score')}, "
            f"Insider={sm.get('insider_signal_score')} "
            f"({sm.get('insider_detected_cluster')}), "
            f"Short={sm.get('short_signal_score')} "
            f"(trend {sm.get('short_trend_direction')}), "
            f"Revisions={sm.get('revision_signal_score')} "
            f"({sm.get('revision_velocity')}).")
    else:
        smart_money_summary = "No smart-money signal computed yet."

    return {
        "ticker":            ticker,
        "price":             t.get("price", "n/a"),
        "low_52w":           t.get("low_52w", "n/a"),
        "high_52w":          t.get("high_52w", "n/a"),
        "day_change":        t.get("day_change", "n/a"),
        "catalyst":          (rfl.get("catalyst_description")
                              or "No specific catalyst recorded."),
        "rsi":               t.get("rsi", "n/a"),
        "rsi_state":         t.get("rsi_state", "n/a"),
        "macd":              t.get("macd", "n/a"),
        "vs_sma50":          t.get("vs_sma50", "n/a"),
        "vs_sma200":         t.get("vs_sma200", "n/a"),
        "support":           t.get("support", "n/a"),
        "resistance":        t.get("resistance", "n/a"),
        "news_summary":      news_summary,
        "sec_summary":       sec_summary,
        "options_summary":   options_summary,
        "pattern_summary":   pattern_summary,
        "crowd_summary":     crowd_summary,
        "validated_summary": validated_summary,
        "smart_money_summary": smart_money_summary,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Prompt
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM = ("You are a senior portfolio manager at a $5B fundamentals-driven "
           "equity fund. You write institutional-grade investment theses. Be "
           "specific. Cite numbers. Name catalysts. Avoid generic language. "
           "CLOSED CONTEXT: you may ONLY cite facts that appear in the data "
           "provided in the prompt -- never from memory. In THESIS, BULL_CASE "
           "and BEAR_CASE, every numeric or specific claim MUST be immediately "
           "followed by a [DATA REF: key] tag naming the data key it came "
           "from (the allowed keys are listed in the prompt). If the data "
           "does not support a claim, do not make it.")

# Keys of the context dict that are legal [DATA REF] targets (everything the
# prompt shows the model). factcheck_agent verifies each tagged claim points
# at one of these and, for scalar keys, that the number matches.
_DATA_KEYS = [
    "price", "low_52w", "high_52w", "day_change", "catalyst", "rsi",
    "rsi_state", "macd", "vs_sma50", "vs_sma200", "support", "resistance",
    "news_summary", "sec_summary", "options_summary", "pattern_summary",
    "crowd_summary", "validated_summary", "smart_money_summary",
]


def _build_prompt(c: dict) -> str:
    return f"""Generate a structured investment thesis for {c['ticker']}:

CURRENT PRICE: ${c['price']}
52W RANGE: ${c['low_52w']} - ${c['high_52w']}
TODAY: {c['day_change']}%

CATALYST: {c['catalyst']}

TECHNICAL SETUP:
- RSI: {c['rsi']} ({c['rsi_state']})
- MACD: {c['macd']}
- vs 50-day MA: {c['vs_sma50']}
- vs 200-day MA: {c['vs_sma200']}
- Support/Resistance: ${c['support']} support, ${c['resistance']} resistance

NEWS FLOW (last 24h):
{c['news_summary']}

RECENT SEC FILINGS (30d): {c['sec_summary']}

OPTIONS POSITIONING: {c['options_summary']}

HISTORICAL ANALOG: {c['pattern_summary']}

CROWD CONSENSUS: {c['crowd_summary']}

CROSS-VALIDATION: {c['validated_summary']}

SMART MONEY SIGNALS: {c['smart_money_summary']}

DATA KEYS for [DATA REF] tags (each prompt section above, in order):
{", ".join(_DATA_KEYS)}

OUTPUT (use exactly these headers):

THESIS:
One or two sentences: the single, specific reason to own this now -- the line a
PM would say in one breath. Synthesize from the data; introduce NO fact that is
not already below, and tag every numeric or specific claim with [DATA REF: key].

BULL_CASE:
Two specific paragraphs (3-4 sentences each) arguing the long thesis. Cite
specific numbers, dates, and catalysts. Address why now. EXPLICITLY address
whether the smart-money signals (earnings, insider, short, revisions) support
or contradict the thesis -- name the supporting signals. Every numeric or
specific claim MUST be immediately followed by [DATA REF: key] using one of
the data keys above (e.g. "RSI at 62 [DATA REF: rsi]").

BEAR_CASE:
Two specific paragraphs arguing the short thesis. What could go wrong. Name the
risk factors specifically. EXPLICITLY address whether any smart-money signal
contradicts the bull thesis (e.g. discretionary insider selling, building short
interest, downgrade cluster). Apply the same [DATA REF: key] rule to every
numeric or specific claim.

PRICE_TARGET:
Reasonable 3-month upside target with reasoning, and the downside risk level.
End this section with one line formatted EXACTLY:
TARGETS: upside=$<number> downside=$<number>

POSITION_SIZE_GUIDANCE:
Suggested portfolio weight (low conviction 0-1%, medium 1-3%, high 3-5%).
Note: this is sizing guidance, not advice.

WHAT_TO_WATCH:
Three specific data points or events that would confirm or invalidate the
thesis. Format each as its own line starting with "- "."""


# ══════════════════════════════════════════════════════════════════════════════
# LLM generation -- shared gateway (Cerebras -> Groq -> NVIDIA -> Gemini)
# ══════════════════════════════════════════════════════════════════════════════

def _generate(prompt: str) -> tuple[str | None, str]:
    return complete(prompt, system=_SYSTEM, temperature=0.5, max_tokens=1600)


# ══════════════════════════════════════════════════════════════════════════════
# Parse the structured output
# ══════════════════════════════════════════════════════════════════════════════

_HEADERS = ["THESIS", "BULL_CASE", "BEAR_CASE", "PRICE_TARGET",
            "POSITION_SIZE_GUIDANCE", "WHAT_TO_WATCH"]


def _parse_thesis(text: str) -> dict:
    sections: dict[str, str] = {}
    # Tolerate markdown headers: "## BULL_CASE", "**BULL_CASE:**", "BULL_CASE:".
    pattern = re.compile(r"^[#*\s]*(" + "|".join(_HEADERS) + r")[#*:\s]*$",
                         re.MULTILINE | re.IGNORECASE)
    marks = [(m.group(1).upper(), m.start(), m.end()) for m in pattern.finditer(text)]
    for i, (name, _, end) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        sections[name] = text[end:stop].strip()

    pt = sections.get("PRICE_TARGET", "")
    up = re.search(r"upside\s*=?\s*\$?\s*([\d,]+(?:\.\d+)?)", pt, re.IGNORECASE)
    dn = re.search(r"downside\s*=?\s*\$?\s*([\d,]+(?:\.\d+)?)", pt, re.IGNORECASE)
    def _safe_float(m):
        if not m:
            return None
        s = m.group(1).replace(",", "").strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    up_v = _safe_float(up)
    dn_v = _safe_float(dn)
    if up_v is None or dn_v is None:          # fallback: first two $ amounts
        nums = [float(x.replace(",", ""))
                for x in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", pt)]
        if len(nums) >= 2:
            up_v = up_v if up_v is not None else nums[0]
            dn_v = dn_v if dn_v is not None else nums[1]

    watch_raw = sections.get("WHAT_TO_WATCH", "")
    watch = [re.sub(r"^[\-\*\d.\)\s]+", "", ln).strip()
             for ln in watch_raw.splitlines() if ln.strip()]
    watch = [w for w in watch if len(w) > 8][:3]

    return {
        "thesis":                sections.get("THESIS") or None,
        "bull_case":             sections.get("BULL_CASE") or None,
        "bear_case":             sections.get("BEAR_CASE") or None,
        "price_target_upside":   up_v,
        "price_target_downside": dn_v,
        "position_size_guidance": sections.get("POSITION_SIZE_GUIDANCE") or None,
        "what_to_watch":         watch or None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(limit: int = DEFAULT_N, scan_id: int | None = None) -> int:
    """Returns the number of picks still missing a thesis (0 = full cover)."""
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Debate Synthesizer -- top %d, model=%s",
             limit, GROQ_MODEL if GROQ_API_KEY else GEMINI_MODEL)

    if scan_id is not None:
        # Precise lineage: exactly this scan's shortlist. (run_date-based
        # selection mixes the day's scans -- 3 share run_date + run_type.)
        ranked = (db.table("ranked_focus_list").select("*")
                  .eq("scan_id", scan_id)
                  .order("rank").limit(limit).execute().data or [])
        if not ranked:
            log.warning("no ranked_focus_list rows for scan_id=%s", scan_id)
            return 0
        run_date = ranked[0]["run_date"]
        log.info("Loaded %d picks for scan_id=%s", len(ranked), scan_id)
    else:
        recent = (db.table("ranked_focus_list").select("run_date")
                  .order("run_date", desc=True).limit(1).execute().data)
        if not recent:
            log.warning("ranked_focus_list empty -- run ranking_engine first.")
            return 0
        run_date = recent[0]["run_date"]

        ranked = (db.table("ranked_focus_list").select("*")
                  .eq("run_date", run_date).eq("run_type", RANK_SOURCE)
                  .order("rank").limit(limit).execute().data or [])
        log.info("Loaded top %d from %s/%s", len(ranked), run_date, RANK_SOURCE)
    tickers = [r["ticker"] for r in ranked]
    if not tickers:
        return 0

    # Idempotent re-runs: rows that already carry a thesis are skipped, so
    # a partial run can be completed by simply running the agent again.
    already = [r["ticker"] for r in ranked if r.get("bull_case")]
    todo = [r for r in ranked if not r.get("bull_case")]
    if already:
        log.info("%d pick(s) already have theses (%s); processing %d",
                 len(already), ", ".join(already), len(todo))
    if not todo:
        return 0

    # Shared context sources.
    since30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    since1d = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    tech = _technicals(tickers)

    def _by_ticker(rows, key="ticker"):
        d: dict[str, list] = {}
        for r in rows:
            d.setdefault(r[key], []).append(r)
        return d

    news = _by_ticker(_fetch_all(lambda: db.table("news_items")
                      .select("ticker,headline,sentiment_score,published_at")
                      .in_("ticker", tickers).gte("published_at", since1d)))
    sec = _by_ticker(_fetch_all(lambda: db.table("sec_filings")
                     .select("ticker,form_type,filing_date")
                     .in_("ticker", tickers).gte("filing_date", since30)))
    options: dict[str, dict] = {}
    for r in _fetch_all(lambda: db.table("options_signals").select("*")
                        .in_("ticker", tickers).order("snapshot_time", desc=True)):
        options.setdefault(r["ticker"], r)
    patterns = {}
    for r in _fetch_all(lambda: db.table("pattern_matches").select("*")
                        .in_("ticker", tickers).order("snapshot_date", desc=True)):
        patterns.setdefault(r["ticker"], r)
    debates = {}
    try:
        for r in _fetch_all(lambda: db.table("ticker_debates").select("*")
                            .in_("ticker", tickers).order("snapshot_date", desc=True)):
            debates.setdefault(r["ticker"], r)
    except Exception:
        pass
    validated = {}
    for r in _fetch_all(lambda: db.table("validated_signals").select("*")
                        .in_("ticker", tickers).order("snapshot_time", desc=True)):
        validated.setdefault(r["ticker"], r)
    # Smart Money intel (Step 6.7) -- latest snapshot per ticker.
    smart_money: dict[str, dict] = {}
    try:
        for r in _fetch_all(lambda: db.table("smart_money_intel")
                            .select("ticker,pattern_detected,confidence_level,"
                                    "composite_smart_money_score,"
                                    "earnings_signal_score,insider_signal_score,"
                                    "insider_detected_cluster,short_signal_score,"
                                    "short_trend_direction,revision_signal_score,"
                                    "revision_velocity,snapshot_date")
                            .in_("ticker", tickers)
                            .order("snapshot_date", desc=True)):
            smart_money.setdefault(r["ticker"], r)
    except Exception as exc:
        log.debug("smart_money_intel preload failed: %s", exc)

    # Synthesize per ticker. Failures (provider quota, transient rate
    # limits) get one retry pass after a cooldown instead of being
    # silently dropped -- partial coverage is what hid price targets from
    # the dashboard.
    results: list[dict] = []
    failed: list[dict] = []
    queue = todo
    for attempt in (1, 2):
        if attempt == 2:
            if not failed:
                break
            queue, failed = failed, []
            log.info("retry pass: %d ticker(s) after 30s cooldown",
                     len(queue))
            time.sleep(30)
        for rfl in queue:
            t = rfl["ticker"]
            ctx = _assemble(t, rfl, tech, news, sec, options, patterns,
                            debates, validated, smart_money)
            text, method = _generate(_build_prompt(ctx))
            if not text:
                log.warning("%s: no thesis generated (attempt %d)", t, attempt)
                failed.append(rfl)
                continue
            parsed = _parse_thesis(text)
            # Factcheck the narrative sections against the exact data the
            # prompt provided (closed context). UNVERIFIED = too many claims
            # without a valid [DATA REF] -- treat like a generation failure
            # so the retry pass gets another shot; an unverified thesis must
            # never reach the displayed shortlist (dossier gate enforces).
            fc_data = {k: ctx[k] for k in _DATA_KEYS if k in ctx}
            fc = factcheck("\n\n".join(
                s for s in (parsed["thesis"], parsed["bull_case"],
                            parsed["bear_case"]) if s),
                fc_data)
            if fc["quality_grade"] == "UNVERIFIED":
                log.warning("%s: thesis factcheck UNVERIFIED (%.2f, attempt %d)"
                            " -- discarding", t, fc["verification_score"], attempt)
                failed.append(rfl)
                continue
            # Strip the [DATA REF] tags for clean display; verification
            # already ran on the tagged text.
            for k in ("thesis", "bull_case", "bear_case", "position_size_guidance"):
                if parsed.get(k):
                    parsed[k] = strip_data_refs(parsed[k])
            if parsed.get("what_to_watch"):
                parsed["what_to_watch"] = [strip_data_refs(w)
                                           for w in parsed["what_to_watch"]]
            parsed["ticker"] = t
            parsed["_method"] = method
            parsed["_price"] = ctx["price"]
            parsed["_fc"] = fc
            results.append(parsed)
            log.info("%s: thesis generated via %s (factcheck %s %.2f)",
                     t, method, fc["quality_grade"], fc["verification_score"])

    # Persist.
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    for r in results:
        try:
            q = db.table("ranked_focus_list").update({
                "thesis":                r["thesis"],
                "bull_case":             r["bull_case"],
                "bear_case":             r["bear_case"],
                "price_target_upside":   r["price_target_upside"],
                "price_target_downside": r["price_target_downside"],
                "position_size_guidance": r["position_size_guidance"],
                "what_to_watch":         r["what_to_watch"],
                "thesis_generated_at":   now,
                # Dossier provenance (migration 048): which provider served
                # this dossier + its factcheck verdict. The critic may later
                # lower the score (min of thesis/critique verification).
                "dossier_provider":           r["_method"],
                "dossier_verification_score": r["_fc"]["verification_score"],
                "dossier_quality_grade":      r["_fc"]["quality_grade"],
            })
            q = (q.eq("scan_id", scan_id) if scan_id is not None
                 else q.eq("run_date", run_date))
            q.eq("ticker", r["ticker"]).execute()
            updated += 1
        except Exception as exc:
            log.debug("update %s failed: %s", r["ticker"], exc)
    log.info("ranked_focus_list: thesis written for %d/%d tickers",
             updated, len(results))
    if updated == 0 and results:
        log.warning("Nothing persisted -- apply supabase/migrations/"
                    "011_thesis_columns.sql, then re-run.")

    _report(results)

    if failed:
        log.warning("%d/%d pick(s) STILL missing theses after retry: %s",
                    len(failed), len(tickers),
                    ", ".join(r["ticker"] for r in failed))
    return len(failed)


def _report(results: list[dict]) -> None:
    bar = "=" * 80
    print(f"\n{bar}\nINVESTMENT THESES -- {len(results)} tickers\n{bar}")
    for r in results:
        print(f"\n{'#' * 80}\n## {r['ticker']}   (price ${r['_price']}, "
              f"via {r['_method']})\n{'#' * 80}")
        print(f"\nTHESIS:\n{r.get('thesis') or '(parse failed)'}")
        print(f"\nBULL CASE:\n{r['bull_case'] or '(parse failed)'}")
        print(f"\nBEAR CASE:\n{r['bear_case'] or '(parse failed)'}")
        print(f"\nPRICE TARGET:  upside ${r['price_target_upside']}  |  "
              f"downside ${r['price_target_downside']}")
        print(f"\nPOSITION SIZE GUIDANCE:\n{r['position_size_guidance'] or '(parse failed)'}")
        print("\nWHAT TO WATCH:")
        for w in (r["what_to_watch"] or ["(parse failed)"]):
            print(f"  - {w}")
    print(f"\n{bar}\n")


if __name__ == "__main__":
    from scan_target import argv_scan_id, strip_scan_id  # noqa: E402

    _scan_id = argv_scan_id()
    _args = strip_scan_id(sys.argv[1:])
    n = DEFAULT_N
    if _args:
        try:
            n = int(_args[0])
        except ValueError:
            log.warning("Invalid N '%s' -- using %d", _args[0], DEFAULT_N)
    # Non-zero exit when coverage is incomplete so the orchestrator records
    # it in the scan notes instead of the gap passing silently.
    sys.exit(1 if run(n, scan_id=_scan_id) else 0)
