"""
Peer Definition
For each A-grade pick, construct a defensible peer set using five independent
methods and then rank candidates by a composite score. Top 8 by score (plus
the target itself) become the official peer set.

Five methods:
  M1  GICS peers          target's sector/industry via yfinance .info; pull
                          same-sector candidates from factor_exposure +
                          ticker_master as the candidate pool.
  M2  FMP analyst peers   GET /api/v3/stock_peers?symbol=...  -- the gold
                          standard. Analyst-curated, high weight.
  M3  Market-cap filter   keep only candidates with cap in [0.3x .. 3.0x] of
                          the target's cap. Drops nonsensical comparisons.
  M4  Correlation peers   252-day daily-return correlation; the highest 10
                          are "trading peers" -- they move together.
  M5  Business model      Groq closed-context: given the target's business
                          summary and the candidates' descriptions, pick
                          the 5-10 with the most similar business model.
                          STRICT validation: any ticker the LLM returns
                          that wasn't in the input candidate list is
                          dropped as hallucinated.

Composite scoring:
  +30  in FMP analyst list                       (M2)
  +20  in GICS sub-industry AND market-cap match (M1 ∩ M3)
  +15  in correlation top-10                     (M4)
  +25  in LLM business-model list                (M5)
  +10  appears in 3+ methods (consensus bonus)

Writes to peer_sets (migration 026).

Usage:
    python pipeline/agents/peer_definition.py [run_type]   # default midday
"""

import csv
import json
import logging
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    FMP_API_KEY, GROQ_API_KEY, SUPABASE_SERVICE_KEY, SUPABASE_URL,
)
from supabase import create_client  # noqa: E402

HTTP_TIMEOUT       = 20
GROQ_MODEL         = "openai/gpt-oss-120b"
TICKER_MASTER_CSV  = Path(__file__).resolve().parent.parent / "data" / "ticker_master.csv"

# Candidate-pool caps -- bound runtime.
MAX_POOL              = 40
CORRELATION_LOOKBACK  = 252
CORR_TOP_N            = 10
MARKETCAP_LO          = 0.3
MARKETCAP_HI          = 3.0
LLM_PICKS_MIN         = 5
LLM_PICKS_MAX         = 10
FINAL_PEER_COUNT      = 8

YF_INFO_SLEEP         = 0.10
FMP_THROTTLE          = 0.3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

class _InfoCache:
    """Cache yfinance .info per ticker for the run."""
    def __init__(self):
        self._d: dict[str, dict] = {}

    def get(self, ticker: str) -> dict:
        if ticker in self._d:
            return self._d[ticker]
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception as exc:
            log.debug(".info failed for %s: %s", ticker, exc)
            info = {}
        self._d[ticker] = info
        time.sleep(YF_INFO_SLEEP)
        return info


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Method loaders
# ══════════════════════════════════════════════════════════════════════════════

def _fmp_peers(ticker: str) -> list[str]:
    """FMP analyst-cited peers. Tries the modern /stable endpoint first, then
    falls back to /api/v4 (the legacy /api/v3/stock_peers route returns 403
    on current accounts). The two endpoints have slightly different shapes:
        /stable/stock-peers      -> [{symbol, companyName, sector, ...}]
        /api/v4/stock-peers      -> [{symbol, peersList: [...]}]
        /api/v3/stock_peers      -> [{symbol, peersList: [...]}]  (legacy)
    """
    if not FMP_API_KEY:
        return []
    urls = [
        ("https://financialmodelingprep.com/stable/stock-peers", "stable"),
        ("https://financialmodelingprep.com/api/v4/stock-peers", "v4"),
        ("https://financialmodelingprep.com/api/v3/stock_peers", "v3"),
    ]
    for url, label in urls:
        try:
            resp = requests.get(url, params={"symbol": ticker, "apikey": FMP_API_KEY},
                                timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                log.debug("FMP %s HTTP %s for %s", label, resp.status_code, ticker)
                continue
            data = resp.json()
            if not data:
                continue
            # /stable returns a flat list of {symbol, companyName, ...}.
            if isinstance(data, list) and data and isinstance(data[0], dict) \
                    and "symbol" in data[0] and "peersList" not in data[0]:
                peers = [str(d.get("symbol") or "").upper() for d in data
                         if d.get("symbol") and str(d.get("symbol")).upper() != ticker.upper()]
                if peers:
                    return peers
            # /v3 and /v4 return [{peersList: [...]}]
            if isinstance(data, list) and data:
                row = data[0]
                peers = row.get("peersList") or []
                if peers:
                    return [str(p).upper() for p in peers if p
                            and str(p).upper() != ticker.upper()]
        except Exception as exc:
            log.debug("FMP %s failed for %s: %s", label, ticker, exc)
        finally:
            time.sleep(FMP_THROTTLE)
    log.warning("FMP peers: no endpoint returned data for %s", ticker)
    return []


def _load_ticker_master() -> list[str]:
    if not TICKER_MASTER_CSV.exists():
        return []
    out = []
    with open(TICKER_MASTER_CSV, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            t = (row.get("ticker") or "").strip().upper()
            if t and t.isalpha() and len(t) <= 5:
                out.append(t)
    return out


def _load_factor_exposure(db, sector: str) -> list[str]:
    """Recent factor_exposure entries for tickers in `sector`. Empty list if
    factor_exposure is empty or sector is None."""
    if not sector:
        return []
    try:
        rows = (db.table("factor_exposure")
                .select("ticker")
                .eq("sector", sector)
                .order("snapshot_date", desc=True)
                .limit(500).execute().data or [])
        return list({r["ticker"] for r in rows})
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Composite scoring
# ══════════════════════════════════════════════════════════════════════════════

def _score_candidate(t, in_fmp, in_gics, marketcap_ok, in_correlation, in_llm):
    methods = []
    score = 0
    if in_fmp:
        score += 30; methods.append("fmp")
    if in_gics and marketcap_ok:
        score += 20; methods.append("gics")
    if in_correlation:
        score += 15; methods.append("correlation")
    if in_llm:
        score += 25; methods.append("llm")
    if len(methods) >= 3:
        score += 10
    return score, methods


# ══════════════════════════════════════════════════════════════════════════════
# Correlation method
# ══════════════════════════════════════════════════════════════════════════════

def _correlation_top(target: str, candidates: list[str]) -> list[tuple[str, float]]:
    """Returns [(ticker, rho)] for top-CORR_TOP_N candidates by 252-day daily-
    return correlation against target. Failures degrade gracefully -- if the
    batch fetch fails entirely, return []."""
    universe = list({target, *candidates})
    if len(universe) < 2:
        return []
    try:
        raw = yf.download(universe, period="1y", interval="1d",
                          auto_adjust=True, progress=False,
                          group_by="ticker", threads=False)
    except Exception as exc:
        log.warning("correlation download failed: %s", exc)
        return []

    multi = isinstance(raw.columns, pd.MultiIndex)
    closes = {}
    for t in universe:
        try:
            s = (raw[t]["Close"] if multi else raw["Close"]).dropna()
            if len(s) >= 60:
                closes[t] = s
        except (KeyError, TypeError):
            pass
    if target not in closes:
        return []
    rets = pd.DataFrame({t: closes[t].pct_change().dropna() for t in closes})
    rets = rets.tail(CORRELATION_LOOKBACK).dropna(how="all")
    if target not in rets.columns or len(rets) < 60:
        return []

    correlations: list[tuple[str, float]] = []
    for t in rets.columns:
        if t == target:
            continue
        rho = float(rets[target].corr(rets[t]))
        if rho == rho:    # skip NaN
            correlations.append((t, rho))
    correlations.sort(key=lambda x: -x[1])
    return correlations[:CORR_TOP_N]


# ══════════════════════════════════════════════════════════════════════════════
# LLM business-model method
# ══════════════════════════════════════════════════════════════════════════════

# Must ask for an OBJECT, not a bare array: the call below uses Groq's
# response_format={"type": "json_object"}, and gpt-oss-120b enforces it
# strictly -- returning a top-level array makes Groq reject the completion with
# HTTP 400 json_validate_failed, which the except-handler turns into "no peers"
# silently. The parser below still accepts either shape.
_LLM_SYSTEM = (
    "You are an equity analyst. Given a target company's business description "
    "and a list of candidate peers with their business descriptions, identify "
    "which candidates have the most similar business model. Respond with a "
    'JSON OBJECT of the form {"peers": ["TICK", ...]} containing ticker '
    "symbols only. Do NOT introduce new tickers. Pick "
    f"between {LLM_PICKS_MIN} and {LLM_PICKS_MAX} candidates."
)


def _llm_business_peers(target: str, target_summary: str,
                        candidates: list[tuple[str, str]]) -> list[str]:
    """candidates is [(ticker, business_summary), ...]. Returns the LLM-picked
    subset, dropping any ticker that wasn't in the input."""
    if not GROQ_API_KEY or not candidates:
        return []
    candidate_tickers = {t for t, _ in candidates}
    cand_block = "\n".join(f"- {t}: {(s or '')[:240]}" for t, s in candidates)
    user_prompt = (
        f"Target: {target}\n"
        f"Target business summary: {(target_summary or '')[:600]}\n\n"
        f"Candidates:\n{cand_block}\n\n"
        f"Return JSON array of ticker symbols ONLY chosen from the candidate "
        f"list. Pick {LLM_PICKS_MIN}-{LLM_PICKS_MAX} tickers."
    )
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": _LLM_SYSTEM},
                      {"role": "user",   "content": user_prompt}],
            # 512 (not 200): gpt-oss is a reasoning model and spends tokens
            # thinking before emitting the JSON. At 200 Groq returns HTTP 400
            # "Failed to validate JSON" because the object never closes, and
            # the except-handler below would silently return no peers.
            temperature=0.0, max_tokens=512,
            response_format={"type": "json_object"})
        raw = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("LLM business peers failed for %s: %s", target, exc)
        return []

    # Accept either a JSON array OR an object with a single array-valued key.
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v; break
        else:
            return []
    if not isinstance(parsed, list):
        return []
    out = []
    rejected = []
    for x in parsed:
        t = str(x).upper().strip()
        if t in candidate_tickers and t not in out:
            out.append(t)
        else:
            rejected.append(t)
    if rejected:
        log.info("    LLM hallucinated %d tickers, dropped: %s",
                 len(rejected), rejected[:5])
    return out[:LLM_PICKS_MAX]


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════

def _validate_peer_set(target: str, peers: list[str], info_cache: _InfoCache
                       ) -> tuple[list[str], list[str], str, str]:
    """Returns (valid_peers, drop_reasons, quality_grade, notes)."""
    valid, drops = [], []
    for t in peers:
        info = info_cache.get(t)
        cap = _num(info.get("marketCap"))
        if cap is None or cap <= 0:
            drops.append(f"{t}: no market cap"); continue
        # 252d price-history check.
        try:
            hist = yf.Ticker(t).history(period="1y", interval="1d", auto_adjust=False)
            if len(hist) < 200:
                drops.append(f"{t}: only {len(hist)} days history"); continue
        except Exception as exc:
            drops.append(f"{t}: history fetch failed ({exc})"); continue
        valid.append(t)
    n = len(valid)
    if n >= 7:
        grade = "high"
    elif n >= 5:
        grade = "medium"
    else:
        grade = "low"
    notes = ""
    if n < 5:
        notes = f"insufficient_peers ({n} validated; dropped {len(drops)})"
    return valid, drops, grade, notes


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker pipeline
# ══════════════════════════════════════════════════════════════════════════════

def build_peer_set(target: str, db, info_cache: _InfoCache,
                    sector_factor_lookup: dict, ticker_master_set: set
                    ) -> dict:
    log.info("=== Peer set for %s ===", target)
    target_info = info_cache.get(target)
    target_sector = target_info.get("sector")
    target_industry = target_info.get("industry")
    target_cap = _num(target_info.get("marketCap")) or 0.0
    target_summary = target_info.get("longBusinessSummary") or target_info.get("description") or ""

    log.info("  target: sector=%s  industry=%s  cap=%.1fB",
             target_sector, target_industry, target_cap / 1e9 if target_cap else 0)

    # ── Method 2: FMP analyst peers ───────────────────────────────────────────
    fmp_peers = _fmp_peers(target)
    log.info("  M2 FMP: %d analyst peer(s): %s", len(fmp_peers), fmp_peers[:8])

    # ── Build candidate pool ──────────────────────────────────────────────────
    # Seed pool from FMP + factor_exposure same-sector + ranked_focus_list
    # same-sector. The ranked_focus_list fallback is what saves us when
    # factor_exposure is sparse for a sector (e.g. only 2 healthcare names).
    pool: list[str] = []
    seen = {target}
    for t in fmp_peers:
        if t not in seen:
            pool.append(t); seen.add(t)
    for t in sector_factor_lookup.get(target_sector, []):
        if t not in seen:
            pool.append(t); seen.add(t)
        if len(pool) >= MAX_POOL:
            break
    # Augment from ranked_focus_list tickers that share the target's sector.
    if len(pool) < 15 and target_sector:
        try:
            rfl_rows = (db.table("ranked_focus_list")
                        .select("ticker").order("run_date", desc=True)
                        .limit(300).execute().data or [])
            seen_rfl = {r["ticker"] for r in rfl_rows} - seen
            for t in list(seen_rfl)[:40]:
                if len(pool) >= MAX_POOL:
                    break
                info = info_cache.get(t)
                if info.get("sector") == target_sector:
                    pool.append(t); seen.add(t)
        except Exception as exc:
            log.debug("ranked_focus_list augment failed: %s", exc)
    log.info("  candidate pool: %d (FMP=%d + factor_exposure + RFL augment)",
             len(pool), len(fmp_peers))
    if len(pool) < 5:
        log.warning("  small candidate pool -- peer set quality will suffer")

    # ── Method 1 (GICS same sub-industry) + Method 3 (market-cap filter) ─────
    gics_in: set[str] = set()
    marketcap_ok: set[str] = set()
    for t in pool:
        info = info_cache.get(t)
        if info.get("industry") and target_industry and \
                info.get("industry") == target_industry:
            gics_in.add(t)
        # market-cap filter -- compare to target's cap.
        cap = _num(info.get("marketCap"))
        if cap and target_cap and \
                MARKETCAP_LO * target_cap <= cap <= MARKETCAP_HI * target_cap:
            marketcap_ok.add(t)
    log.info("  M1 GICS (same industry): %d", len(gics_in))
    log.info("  M3 market-cap [%.1fx..%.1fx]: %d",
             MARKETCAP_LO, MARKETCAP_HI, len(marketcap_ok))

    # ── Method 4: correlation top-10 ─────────────────────────────────────────
    corr_pool = list(pool)[:30]   # cap to bound yfinance batch
    corr_top = _correlation_top(target, corr_pool)
    corr_in = {t for t, _ in corr_top}
    log.info("  M4 correlation top-%d: %s",
             CORR_TOP_N, [(t, round(r, 2)) for t, r in corr_top[:6]])

    # ── Method 5: LLM business-model peers ───────────────────────────────────
    cand_for_llm = []
    for t in pool[:30]:
        s = info_cache.get(t).get("longBusinessSummary") \
            or info_cache.get(t).get("description") or ""
        if s:
            cand_for_llm.append((t, s))
    llm_picks = _llm_business_peers(target, target_summary, cand_for_llm)
    llm_in = set(llm_picks)
    log.info("  M5 LLM business model: %d", len(llm_in))

    # ── Composite score ──────────────────────────────────────────────────────
    scored = []
    for t in pool:
        s, methods = _score_candidate(
            t,
            in_fmp=(t in fmp_peers),
            in_gics=(t in gics_in),
            marketcap_ok=(t in marketcap_ok),
            in_correlation=(t in corr_in),
            in_llm=(t in llm_in),
        )
        if s == 0:
            continue
        scored.append({"ticker": t, "score": s, "methods_matched": methods})
    scored.sort(key=lambda x: -x["score"])
    final_peers_raw = [s["ticker"] for s in scored[:FINAL_PEER_COUNT]]

    # ── Validation ───────────────────────────────────────────────────────────
    validated, drops, grade, notes = _validate_peer_set(
        target, final_peers_raw, info_cache)
    if drops:
        log.info("  validation dropped: %s", drops[:5])
    log.info("  final peers: %d (quality=%s)", len(validated), grade)

    final_scored = [s for s in scored if s["ticker"] in validated]
    return {
        "target_ticker":     target,
        "peers":             final_scored,
        "peer_count":        len(final_scored),
        "gics_industry":     target_industry,
        "gics_sub_industry": None,
        "peer_set_quality":  grade,
        "validation_notes":  notes,
        "scoring_breakdown": {
            "candidate_pool":   pool,
            "fmp_peers":        fmp_peers,
            "gics_industry_in": sorted(gics_in),
            "marketcap_ok":     sorted(marketcap_ok),
            "correlation_top":  [{"ticker": t, "rho": round(r, 3)} for t, r in corr_top],
            "llm_business":     llm_picks,
            "validation_drops": drops,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Persistence + report
# ══════════════════════════════════════════════════════════════════════════════

def _persist(db, snapshot_date, run_type, result):
    payload = {
        "target_ticker":     result["target_ticker"],
        "snapshot_date":     snapshot_date,
        "run_type":          run_type,
        "peers":             result["peers"],
        "peer_count":        result["peer_count"],
        "gics_industry":     result["gics_industry"],
        "gics_sub_industry": result["gics_sub_industry"],
        "peer_set_quality":  result["peer_set_quality"],
        "validation_notes":  result["validation_notes"],
        "scoring_breakdown": result["scoring_breakdown"],
    }
    try:
        resp = db.table("peer_sets").upsert(
            [payload], on_conflict="target_ticker,snapshot_date,run_type"
        ).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as exc:
        log.warning("peer_sets persistence failed (%s) -- apply migration 026",
                    getattr(exc, "code", exc))
        return None


def _report(results):
    bar = "=" * 78
    print(f"\n{bar}\nPEER DEFINITION  --  {len(results)} A-pick(s)\n{bar}")
    for r in results:
        print(f"\n[ {r['target_ticker']}  --  industry: {r['gics_industry']}  "
              f"--  quality: {r['peer_set_quality'].upper()} ]")
        if r["validation_notes"]:
            print(f"  WARNING: {r['validation_notes']}")
        if not r["peers"]:
            print("  (no peers passed scoring + validation)")
            continue
        print(f"  {'RANK':<5}{'TICKER':<8}{'SCORE':>6}    METHODS MATCHED")
        for i, p in enumerate(r["peers"], 1):
            methods_s = "+".join(p["methods_matched"])
            print(f"  {i:<5}{p['ticker']:<8}{p['score']:>6}    {methods_s}")
    print(f"\n{bar}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in ("premarket", "midday", "close"):
        run_type = "midday"
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Peer Definition -- run_type=%s", run_type)

    recent = (db.table("ranked_focus_list").select("run_date")
              .order("run_date", desc=True).limit(1).execute().data)
    if not recent:
        log.warning("ranked_focus_list empty -- run conviction_grader first.")
        return
    run_date = recent[0]["run_date"]
    apicks = (db.table("ranked_focus_list")
              .select("ticker,rank,composite_score,conviction_grade,"
                      "conviction_score_adjusted,grade_reasoning")
              .eq("run_date", run_date).eq("run_type", run_type)
              .eq("conviction_grade", "A").order("rank").execute().data or [])
    if not apicks:
        log.warning("No A-grade picks on %s/%s.", run_date, run_type)
        return
    log.info("Found %d A-pick(s): %s", len(apicks),
             [p["ticker"] for p in apicks])

    # Pre-build sector -> tickers lookup from factor_exposure (shared cache).
    sector_lookup: dict[str, list[str]] = {}
    info_cache = _InfoCache()
    ticker_master_set = set(_load_ticker_master())
    log.info("ticker_master loaded: %d tickers", len(ticker_master_set))

    # For each A-pick, look up its sector once and cache same-sector tickers.
    for p in apicks:
        info = info_cache.get(p["ticker"])
        sector = info.get("sector")
        if sector and sector not in sector_lookup:
            sector_lookup[sector] = _load_factor_exposure(db, sector)
            log.info("factor_exposure sector=%s: %d ticker(s)",
                     sector, len(sector_lookup[sector]))

    results = []
    for p in apicks:
        try:
            r = build_peer_set(p["ticker"], db, info_cache, sector_lookup,
                                ticker_master_set)
            r["peer_set_id"] = _persist(db, run_date, run_type, r)
            results.append(r)
        except Exception as exc:
            log.exception("peer set failed for %s: %s", p["ticker"], exc)

    _report(results)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
