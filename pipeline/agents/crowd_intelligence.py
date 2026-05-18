"""
Crowd Intelligence Agent
Aggregates multi-source retail + professional signals for the most-discussed
tickers, then synthesizes a two-sided bull/bear debate for each one that draws
meaningful attention.

Runs in 3 sequential stages:

  STAGE 1 - COLLECT   Parallel async fetches across 5 live sources.
  STAGE 2 - ANALYZE   Normalize every source into one row per ticker; score
                      text with FinBERT; compute attention_score,
                      sentiment_balance and a retail-vs-pro divergence.
  STAGE 3 - DEBATE    For tickers with attention_score > 30, synthesize a
                      structured bull/bear debate (Groq LLM if GROQ_API_KEY is
                      set, otherwise a deterministic data-driven template).

Sources actually used (see the build notes -- 3 of the originally requested
8 were dead and were replaced with working equivalents):
  1. ApeWisdom            - retail mention rank + 24h mention change
  2. StockTwits streams   - retail self-tagged Bullish/Bearish (best-effort)
  3. Finnhub recommend.   - professional analyst consensus (free tier)
  4. Hacker News (Algolia)- tech / media attention
  5. news_items table     - news article volume from our own DB

Writes ticker_debates (new) and keeps social_mentions updated.

Usage:
    python pipeline/agents/crowd_intelligence.py
"""

import asyncio
import json
import logging
import math
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pandas as pd
from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    FINNHUB_API_KEY,
    GEMINI_API_KEY,
    GROQ_API_KEY,
    SUPABASE_SERVICE_KEY,
    SUPABASE_URL,
    TICKER_UNIVERSE_PATH,
)
from processors.sentiment_scorer import get_backend, score_text

# ── Settings ───────────────────────────────────────────────────────────────────
FOCUS_SIZE          = 100      # tickers carried through StockTwits / Finnhub / HN
APEWISDOM_PAGES     = 10       # 10 pages x 100 = top 1000 mention-ranked tickers
STOCKTWITS_DELAY    = 1.1      # seconds between StockTwits calls (politeness)
FINNHUB_DELAY       = 1.2      # seconds between Finnhub calls (free tier 60/min)
HN_CONCURRENCY      = 5
ATTENTION_THRESHOLD = 30       # attention_score above which a debate is generated
NEWS_WINDOW_HOURS   = 48
TIMEOUT             = aiohttp.ClientTimeout(total=20)
GROQ_MODEL          = "llama-3.3-70b-versatile"
GEMINI_MODEL        = "gemini-2.0-flash"
VALID_RUN_TYPES     = ("premarket", "midday", "close")
USER_AGENT          = (
    "FortisStockScreener/1.0 (crowd-intelligence research; research@fortis.com)"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Tracks which sources failed entirely, for the final debug report.
_SOURCE_NOTES: dict[str, str] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 - COLLECT
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_json(session: aiohttp.ClientSession, url: str, **kwargs):
    async with session.get(url, **kwargs) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


async def _collect_apewisdom(session) -> list[dict]:
    """Pages 1-10 of ApeWisdom -> up to 1000 mention-ranked tickers."""
    async def one(page: int):
        try:
            d = await _fetch_json(
                session,
                f"https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}",
            )
            return d.get("results", []) or []
        except Exception as exc:
            log.warning("ApeWisdom page %d failed: %s", page, exc)
            return []

    pages = await asyncio.gather(*[one(p) for p in range(1, APEWISDOM_PAGES + 1)])
    out: list[dict] = []
    for chunk in pages:
        for r in chunk:
            mentions = int(r.get("mentions") or 0)
            prev = int(r.get("mentions_24h_ago") or 0)
            if prev > 0:
                change = (mentions - prev) / prev * 100.0
            elif mentions > 0:
                change = 500.0          # new from zero -> treat as a max spike
            else:
                change = 0.0
            out.append({
                "ticker":   str(r.get("ticker") or "").upper().strip(),
                "name":     (r.get("name") or "").strip(),
                "rank":     int(r.get("rank") or 99999),
                "mentions": mentions,
                "upvotes":  int(r.get("upvotes") or 0),
                "change_pct": round(change, 2),
            })
    log.info("ApeWisdom: %d ranked tickers collected", len(out))
    if not out:
        _SOURCE_NOTES["ApeWisdom"] = "returned no data (all 10 pages failed)"
    return out


async def _collect_stocktwits(session, tickers: list[str]) -> dict:
    """Per-ticker StockTwits stream -> bull/bear counts + message text.
    Best-effort: StockTwits rate-limits aggressively, so any failure for a
    ticker just drops that ticker rather than crashing the source."""
    data: dict[str, dict] = {}
    for i, t in enumerate(tickers):
        if i:
            await asyncio.sleep(STOCKTWITS_DELAY)
        try:
            d = await _fetch_json(
                session, f"https://api.stocktwits.com/api/2/streams/symbol/{t}.json"
            )
            msgs = d.get("messages", []) or []
            bull = bear = 0
            bodies: list[str] = []
            for m in msgs:
                body = m.get("body") or ""
                if body:
                    bodies.append(body)
                ent = m.get("entities") or {}
                sent = ent.get("sentiment") or {}
                basic = (sent.get("basic") or "").lower() if isinstance(sent, dict) else ""
                if basic == "bullish":
                    bull += 1
                elif basic == "bearish":
                    bear += 1
            denom = bull + bear
            data[t] = {
                "bull": bull, "bear": bear, "total": len(msgs),
                "bull_ratio": round((bull - bear) / denom, 4) if denom else 0.0,
                "messages": bodies,
            }
        except Exception as exc:
            log.debug("StockTwits [%s] skipped: %s", t, exc)
    log.info("StockTwits: %d/%d tickers returned data", len(data), len(tickers))
    if not data:
        _SOURCE_NOTES["StockTwits"] = (
            "0 tickers returned data -- endpoint likely IP rate-limited this run"
        )
    return data


async def _collect_finnhub(session, tickers: list[str]) -> dict:
    """Per-ticker Finnhub analyst recommendation trends (the professional signal)."""
    data: dict[str, dict] = {}
    if not FINNHUB_API_KEY:
        _SOURCE_NOTES["Finnhub"] = "no FINNHUB_API_KEY configured"
        log.warning("Finnhub: no API key -- skipping analyst recommendations")
        return data
    for i, t in enumerate(tickers):
        if i:
            await asyncio.sleep(FINNHUB_DELAY)
        try:
            d = await _fetch_json(
                session,
                f"https://finnhub.io/api/v1/stock/recommendation"
                f"?symbol={t}&token={FINNHUB_API_KEY}",
            )
            if not isinstance(d, list) or not d:
                continue
            latest = d[0]   # API returns periods newest-first
            sb = int(latest.get("strongBuy") or 0)
            b  = int(latest.get("buy") or 0)
            h  = int(latest.get("hold") or 0)
            s  = int(latest.get("sell") or 0)
            ss = int(latest.get("strongSell") or 0)
            total = sb + b + h + s + ss
            # -1 (all strong sell) .. +1 (all strong buy)
            score = ((2 * sb + b - s - 2 * ss) / (2 * total)) if total else 0.0
            data[t] = {
                "strongBuy": sb, "buy": b, "hold": h, "sell": s, "strongSell": ss,
                "period": latest.get("period"), "total": total,
                "analyst_score": round(score, 4),
            }
        except Exception as exc:
            log.debug("Finnhub [%s] skipped: %s", t, exc)
    log.info("Finnhub: %d/%d tickers returned recommendations", len(data), len(tickers))
    if not data:
        _SOURCE_NOTES.setdefault("Finnhub", "0 tickers returned recommendation data")
    return data


async def _collect_hackernews(session, tickers: list[str], names: dict) -> dict:
    """Per-ticker Hacker News story search (last 24h) via the Algolia API.
    Searched by company name rather than ticker symbol to cut false positives;
    non-tech names simply return ~0 hits, so no explicit sector filter is used."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp())
    sem = asyncio.Semaphore(HN_CONCURRENCY)
    data: dict[str, dict] = {}

    async def one(t: str):
        query = names.get(t) or t
        async with sem:
            try:
                d = await _fetch_json(
                    session,
                    "https://hn.algolia.com/api/v1/search",
                    params={
                        "query": query,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{cutoff}",
                    },
                )
                hits = d.get("hits", []) or []
                if hits:
                    data[t] = {
                        "story_count":    len(hits),
                        "total_points":   sum(int(h.get("points") or 0) for h in hits),
                        "total_comments": sum(int(h.get("num_comments") or 0) for h in hits),
                    }
            except Exception as exc:
                log.debug("HN [%s] skipped: %s", t, exc)

    await asyncio.gather(*[one(t) for t in tickers])
    log.info("Hacker News: %d/%d tickers had recent stories", len(data), len(tickers))
    return data


def _collect_news_volume(db, tickers: list[str]) -> dict:
    """Recent news-article volume + headlines per ticker, from our own DB."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=NEWS_WINDOW_HOURS)).isoformat()
    data: dict[str, dict] = {}
    for t in tickers:
        try:
            resp = (
                db.table("news_items")
                .select("headline", count="exact")
                .eq("ticker", t)
                .gte("published_at", cutoff)
                .limit(30)
                .execute()
            )
            rows = resp.data or []
            data[t] = {
                "count": int(resp.count or 0),
                "headlines": [r.get("headline", "") for r in rows if r.get("headline")],
            }
        except Exception as exc:
            log.debug("news_items [%s] skipped: %s", t, exc)
    log.info("news_items: volume collected for %d/%d tickers", len(data), len(tickers))
    return data


def _unwrap(result, label: str) -> dict:
    if isinstance(result, BaseException):
        log.warning("%s collector crashed: %s", label, result)
        _SOURCE_NOTES[label] = f"collector crashed: {result}"
        return {}
    return result


async def _collect_all(db, universe: set[str], portfolio: list[str]):
    """COLLECT stage. Returns (focus, ape_map, names, st, fh, hn, news)."""
    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    ) as session:
        ape = await _collect_apewisdom(session)

        # Focus universe: top FOCUS_SIZE ApeWisdom tickers that are in our
        # S&P 1500 universe, plus any portfolio holdings.
        ape_in_universe = sorted(
            (r for r in ape if r["ticker"] in universe), key=lambda r: r["rank"]
        )
        focus, seen = [], set()
        for r in ape_in_universe:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                focus.append(r["ticker"])
            if len(focus) >= FOCUS_SIZE:
                break
        n_ape = len(focus)
        for t in portfolio:
            if t in universe and t not in seen:
                seen.add(t)
                focus.append(t)
        log.info("Focus universe: %d tickers (%d from ApeWisdom, %d portfolio additions)",
                 len(focus), n_ape, len(focus) - n_ape)

        names = {r["ticker"]: r["name"] for r in ape}

        st, fh, hn = await asyncio.gather(
            _collect_stocktwits(session, focus),
            _collect_finnhub(session, focus),
            _collect_hackernews(session, focus, names),
            return_exceptions=True,
        )

    st = _unwrap(st, "StockTwits")
    fh = _unwrap(fh, "Finnhub")
    hn = _unwrap(hn, "HackerNews")
    news = _collect_news_volume(db, focus)
    ape_map = {r["ticker"]: r for r in ape}
    return focus, ape_map, names, st, fh, hn, news


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 - ANALYZE
# ══════════════════════════════════════════════════════════════════════════════

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _analyze(focus, ape_map, st, fh, hn, news) -> list[dict]:
    max_mentions  = max([ape_map.get(t, {}).get("mentions", 0) for t in focus] + [1])
    max_hn_points = max([hn.get(t, {}).get("total_points", 0) for t in focus] + [1])

    rows: list[dict] = []
    for t in focus:
        a = ape_map.get(t, {})
        s = st.get(t, {})
        f = fh.get(t, {})
        h = hn.get(t, {})
        n = news.get(t, {})

        # ── FinBERT on text-heavy sources ──────────────────────────────────
        st_msgs = s.get("messages", []) or []
        news_heads = n.get("headlines", []) or []
        st_finbert   = score_text(" ".join(st_msgs)[:4000]) if st_msgs else 0.0
        news_finbert = score_text(" ".join(news_heads)[:4000]) if news_heads else 0.0

        # ── attention_score (0..100): weighted blend of 5 attention signals ─
        change  = max(min(a.get("change_pct", 0.0), 500.0), -100.0)
        c_change = max(change, 0.0) / 500.0
        c_volume = math.log1p(a.get("mentions", 0)) / math.log1p(max_mentions)
        c_st     = min(s.get("total", 0) / 30.0, 1.0)
        c_hn     = math.log1p(h.get("total_points", 0)) / math.log1p(max_hn_points)
        c_news   = min(n.get("count", 0) / 20.0, 1.0)
        attention = 100.0 * (
            0.30 * c_change + 0.25 * c_volume + 0.15 * c_st
            + 0.15 * c_hn + 0.15 * c_news
        )

        # ── sentiment_balance (-100..+100) ─────────────────────────────────
        st_ratio = s.get("bull_ratio", 0.0)
        analyst  = f.get("analyst_score", 0.0)
        sentiment = 100.0 * (
            0.35 * st_ratio + 0.20 * st_finbert
            + 0.30 * analyst + 0.15 * news_finbert
        )

        # ── retail vs professional divergence (-100..+100) ─────────────────
        retail = _mean([st_ratio, st_finbert])
        pro    = _mean([analyst, news_finbert])
        divergence = (retail - pro) * 50.0

        raw_signals = {
            "apewisdom": {
                "rank": a.get("rank"), "mentions": a.get("mentions", 0),
                "upvotes": a.get("upvotes", 0), "change_pct": a.get("change_pct", 0.0),
            },
            "stocktwits": {
                "bull": s.get("bull", 0), "bear": s.get("bear", 0),
                "total": s.get("total", 0), "bull_ratio": st_ratio,
                "finbert": st_finbert,
            },
            "finnhub": {
                "strongBuy": f.get("strongBuy", 0), "buy": f.get("buy", 0),
                "hold": f.get("hold", 0), "sell": f.get("sell", 0),
                "strongSell": f.get("strongSell", 0),
                "analyst_score": analyst, "period": f.get("period"),
            },
            "hackernews": {
                "story_count": h.get("story_count", 0),
                "total_points": h.get("total_points", 0),
                "total_comments": h.get("total_comments", 0),
            },
            "news": {"count": n.get("count", 0), "finbert": news_finbert},
            "scores": {
                "attention": round(attention, 2),
                "sentiment_balance": round(sentiment, 2),
                "divergence": round(divergence, 2),
            },
        }

        rows.append({
            "ticker": t,
            "name": a.get("name", ""),
            "apewisdom_rank":        a.get("rank"),
            "apewisdom_mentions":    a.get("mentions", 0),
            "apewisdom_change_pct":  a.get("change_pct", 0.0),
            "stocktwits_bull_count": s.get("bull", 0),
            "stocktwits_bear_count": s.get("bear", 0),
            "stocktwits_total":      s.get("total", 0),
            "stocktwits_bull_ratio": st_ratio,
            "stocktwits_finbert":    st_finbert,
            "stocktwits_messages":   st_msgs,
            "finnhub_strongbuy":     f.get("strongBuy", 0),
            "finnhub_buy":           f.get("buy", 0),
            "finnhub_hold":          f.get("hold", 0),
            "finnhub_sell":          f.get("sell", 0),
            "finnhub_strongsell":    f.get("strongSell", 0),
            "finnhub_analyst_score": analyst,
            "hackernews_story_count":  h.get("story_count", 0),
            "hackernews_total_points": h.get("total_points", 0),
            "news_count":            n.get("count", 0),
            "news_headlines":        news_heads,
            "news_finbert":          news_finbert,
            "attention_score":   round(attention, 2),
            "sentiment_balance": round(sentiment, 2),
            "divergence":        round(divergence, 2),
            "raw_signals":       raw_signals,
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 - DEBATE
# ══════════════════════════════════════════════════════════════════════════════

def _build_debate_prompt(row: dict) -> str:
    t = row["ticker"]
    top_msgs = [m for m in row["stocktwits_messages"][:3]]
    top_news = row["news_headlines"][:3]
    data = {
        "apewisdom_mentions":     row["apewisdom_mentions"],
        "apewisdom_change_pct":   row["apewisdom_change_pct"],
        "stocktwits_bull_count":  row["stocktwits_bull_count"],
        "stocktwits_bear_count":  row["stocktwits_bear_count"],
        "stocktwits_bull_ratio":  row["stocktwits_bull_ratio"],
        "stocktwits_finbert":     row["stocktwits_finbert"],
        "analyst_strongbuy":      row["finnhub_strongbuy"],
        "analyst_buy":            row["finnhub_buy"],
        "analyst_hold":           row["finnhub_hold"],
        "analyst_sell":           row["finnhub_sell"],
        "analyst_strongsell":     row["finnhub_strongsell"],
        "analyst_score":          row["finnhub_analyst_score"],
        "hackernews_stories":     row["hackernews_story_count"],
        "news_article_count":     row["news_count"],
        "news_finbert":           row["news_finbert"],
        "attention_score":        row["attention_score"],
        "sentiment_balance":      row["sentiment_balance"],
        "divergence":             row["divergence"],
        "top_stocktwits_messages": top_msgs,
        "top_news_headlines":      top_news,
    }
    return (
        f"Given the following crowd signal data for "
        f"{t}, write a strict structured output with these exact sections:\n\n"
        "BULL_CASE: A 2-sentence argument representing what bullish "
        "posters/articles are saying. Be specific -- cite the actual narratives "
        "appearing in the source text, not generic optimism.\n\n"
        "BEAR_CASE: A 2-sentence argument representing what bearish "
        "posters/articles are saying. Same rule -- specific narratives, not "
        "generic skepticism.\n\n"
        "CONSENSUS_TILT: One sentence on which side is currently louder, by how "
        "much, and whether the gap is widening or narrowing.\n\n"
        "RETAIL_VS_PRO_DIVERGENCE: One sentence on whether retail sources "
        "(ApeWisdom, StockTwits) agree or disagree with professional sources "
        "(analyst recommendations, Hacker News, news). Disagreement here is "
        "itself a signal -- flag it.\n\n"
        f"DATA:\n{json.dumps(data, indent=2)}"
    )


_SECTION_RE = {
    "bull":  re.compile(r"BULL_CASE\s*:?\s*(.+?)(?=BEAR_CASE|CONSENSUS_TILT|RETAIL_VS_PRO|$)", re.S | re.I),
    "bear":  re.compile(r"BEAR_CASE\s*:?\s*(.+?)(?=CONSENSUS_TILT|RETAIL_VS_PRO|$)", re.S | re.I),
    "tilt":  re.compile(r"CONSENSUS_TILT\s*:?\s*(.+?)(?=RETAIL_VS_PRO|$)", re.S | re.I),
    "div":   re.compile(r"RETAIL_VS_PRO_DIVERGENCE\s*:?\s*(.+?)$", re.S | re.I),
}


def _parse_debate(text: str) -> dict | None:
    out = {}
    for key, rx in _SECTION_RE.items():
        m = rx.search(text or "")
        out[key] = m.group(1).strip() if m else ""
    if not (out["bull"] and out["bear"]):
        return None
    return {
        "bull_case": out["bull"],
        "bear_case": out["bear"],
        "consensus_tilt": out["tilt"],
        "retail_pro_divergence_text": out["div"],
    }


def _template_debate(row: dict) -> dict:
    """Deterministic, data-driven debate synthesis (used when Groq is absent)."""
    t   = row["ticker"]
    sb  = row["sentiment_balance"]
    div = row["divergence"]
    buys  = row["finnhub_strongbuy"] + row["finnhub_buy"]
    sells = row["finnhub_sell"] + row["finnhub_strongsell"]
    news_n = row["news_count"]

    bull = (
        f"Retail attention on {t} is elevated -- {row['apewisdom_mentions']} "
        f"ApeWisdom mentions ({row['apewisdom_change_pct']:+.0f}% vs 24h ago) and "
        f"{row['stocktwits_bull_count']} self-tagged-bullish StockTwits posts. "
        f"Wall Street currently carries {buys} buy/strong-buy analyst ratings, "
        f"with the crowd treating the activity as an upside setup."
    )
    bear = (
        f"Skeptics point to {row['stocktwits_bear_count']} bearish StockTwits "
        f"posts and {sells} analyst sell ratings on {t}; "
        + ("recent news flow is light, so the move looks sentiment-driven rather "
           "than backed by fundamentals."
           if news_n < 3 else
           f"{news_n} news items in the last 48h keep headline risk firmly in play.")
    )
    if sb > 20:
        tilt = (f"Bulls are clearly louder on {t} (sentiment balance {sb:+.0f}) "
                f"and the gap currently favors the upside.")
    elif sb < -20:
        tilt = (f"Bears are clearly louder on {t} (sentiment balance {sb:+.0f}) "
                f"and the gap currently favors the downside.")
    else:
        tilt = (f"{t} is a genuine standoff (sentiment balance {sb:+.0f}) -- "
                f"neither side has clear control, which is the most interesting case.")
    if abs(div) < 20:
        dvg = (f"Retail and professional sources broadly agree on {t} "
               f"(divergence {div:+.0f}).")
    elif div > 0:
        dvg = (f"Retail is more bullish than the professionals on {t} "
               f"(divergence {div:+.0f}) -- a crowd-vs-analyst gap that often "
               f"precedes volatility.")
    else:
        dvg = (f"Professionals are more bullish than retail on {t} "
               f"(divergence {div:+.0f}) -- analysts see value the crowd is fading.")
    return {
        "bull_case": bull, "bear_case": bear,
        "consensus_tilt": tilt, "retail_pro_divergence_text": dvg,
    }


_DEBATE_SYSTEM = "You are a senior equity research analyst at a wealth management firm."


def _try_groq(row: dict) -> dict | None:
    """Attempt a Groq-synthesized debate. Returns parsed dict or None on failure."""
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _DEBATE_SYSTEM},
                {"role": "user", "content": _build_debate_prompt(row)},
            ],
            temperature=0.4,
            max_tokens=600,
        )
        return _parse_debate(resp.choices[0].message.content)
    except Exception as exc:
        log.warning("Groq debate failed for %s: %s", row["ticker"], exc)
        return None


def _try_gemini(row: dict) -> dict | None:
    """Attempt a Gemini-synthesized debate. Returns parsed dict or None on failure."""
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=_DEBATE_SYSTEM)
        resp = model.generate_content(_build_debate_prompt(row))
        return _parse_debate(resp.text)
    except Exception as exc:
        log.warning("Gemini debate failed for %s: %s", row["ticker"], exc)
        return None


def _synthesize_debate(row: dict) -> tuple[dict, str]:
    """Groq -> Gemini -> deterministic template. Returns (debate_fields, method)."""
    parsed = _try_groq(row)
    if parsed:
        return parsed, "groq"
    parsed = _try_gemini(row)
    if parsed:
        return parsed, "gemini"
    return _template_debate(row), "template"


# ══════════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════════

def _persist(db, today: str, run_type: str,
             all_rows: list[dict], debate_rows: list[dict]) -> None:
    # ticker_debates -- only the tickers that cleared the attention threshold
    td_rows = [{
        "ticker": r["ticker"],
        "snapshot_date": today,
        "run_type": run_type,
        "attention_score": r["attention_score"],
        "sentiment_balance": r["sentiment_balance"],
        "bull_case": r["bull_case"],
        "bear_case": r["bear_case"],
        "consensus_tilt": r["consensus_tilt"],
        "retail_pro_divergence": r["retail_pro_divergence_text"],
        "raw_signals": r["raw_signals"],
    } for r in debate_rows]
    if td_rows:
        try:
            db.table("ticker_debates").upsert(
                td_rows, on_conflict="ticker,snapshot_date,run_type"
            ).execute()
            log.info("ticker_debates: %d rows upserted", len(td_rows))
        except Exception as exc:
            log.error("ticker_debates upsert failed: %s", exc)

    # social_mentions -- kept updated for backward compatibility
    sm_rows = [{
        "ticker": r["ticker"],
        "source": "crowd_intelligence",
        "mention_count": int(r["apewisdom_mentions"]),
        "sentiment_score": round(r["sentiment_balance"] / 100.0, 4),
        "snapshot_date": today,
    } for r in all_rows]
    if sm_rows:
        try:
            db.table("social_mentions").upsert(
                sm_rows, on_conflict="ticker,source,snapshot_date"
            ).execute()
            log.info("social_mentions: %d rows upserted", len(sm_rows))
        except Exception as exc:
            log.error("social_mentions upsert failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(all_rows: list[dict], debate_rows: list[dict]) -> None:
    by_attention = sorted(all_rows, key=lambda r: r["attention_score"], reverse=True)

    log.info("")
    log.info("== Top 10 tickers by attention_score ==")
    log.info("%-8s  %-8s  %-12s  %s", "TICKER", "ATTN", "SENTIMENT", "DIVERGENCE")
    for r in by_attention[:10]:
        log.info("%-8s  %-8.1f  %-+12.1f  %+.1f",
                 r["ticker"], r["attention_score"],
                 r["sentiment_balance"], r["divergence"])

    active = sorted(debate_rows, key=lambda r: abs(r["sentiment_balance"]))
    log.info("")
    log.info("== Top 5 most active debates (attention>%d, sentiment_balance near 0) ==",
             ATTENTION_THRESHOLD)
    for r in active[:5]:
        log.info("%-8s  attn=%.1f  sentiment_balance=%+.1f",
                 r["ticker"], r["attention_score"], r["sentiment_balance"])

    diverge = sorted(debate_rows, key=lambda r: abs(r["divergence"]), reverse=True)
    log.info("")
    log.info("== Top 5 retail-vs-pro divergence (attention>%d) ==", ATTENTION_THRESHOLD)
    for r in diverge[:5]:
        log.info("%-8s  divergence=%+.1f  attn=%.1f",
                 r["ticker"], r["divergence"], r["attention_score"])

    # Full debate for the most-debated ticker: highest attention among the
    # genuine standoffs (|sentiment_balance| < 25), else highest attention.
    standoffs = [r for r in debate_rows if abs(r["sentiment_balance"]) < 25]
    pool = standoffs or debate_rows
    if pool:
        top = max(pool, key=lambda r: r["attention_score"])
        log.info("")
        log.info("== Full debate -- most-debated ticker: %s ==", top["ticker"])
        log.info("attention_score=%.1f  sentiment_balance=%+.1f  divergence=%+.1f",
                 top["attention_score"], top["sentiment_balance"], top["divergence"])
        log.info("BULL_CASE: %s", top["bull_case"])
        log.info("BEAR_CASE: %s", top["bear_case"])
        log.info("CONSENSUS_TILT: %s", top["consensus_tilt"])
        log.info("RETAIL_VS_PRO_DIVERGENCE: %s", top["retail_pro_divergence_text"])

    log.info("")
    if _SOURCE_NOTES:
        log.info("== Sources with issues this run ==")
        for src, note in _SOURCE_NOTES.items():
            log.info("  %s: %s", src, note)
    else:
        log.info("== All sources returned data ==")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(run_type: str = "midday") -> None:
    if run_type not in VALID_RUN_TYPES:
        log.warning("Unknown run_type '%s' -- defaulting to 'midday'", run_type)
        run_type = "midday"
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    log.info("Crowd intelligence run: run_type=%s", run_type)
    universe = set(
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).str.upper()
    )
    log.info("Loaded %d tickers in S&P 1500 universe", len(universe))
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    portfolio: list[str] = []
    try:
        resp = db.table("portfolio_holdings").select("ticker").execute()
        portfolio = sorted({
            (r.get("ticker") or "").upper() for r in (resp.data or []) if r.get("ticker")
        })
    except Exception as exc:
        log.warning("portfolio_holdings query failed: %s", exc)

    log.info("=" * 70)
    log.info("STAGE 1 -- COLLECT")
    focus, ape_map, names, st, fh, hn, news = asyncio.run(
        _collect_all(db, universe, portfolio)
    )

    log.info("=" * 70)
    log.info("STAGE 2 -- ANALYZE  (sentiment backend: %s)", get_backend())
    all_rows = _analyze(focus, ape_map, st, fh, hn, news)

    log.info("=" * 70)
    log.info("STAGE 3 -- DEBATE")
    debate_rows = [r for r in all_rows if r["attention_score"] > ATTENTION_THRESHOLD]
    llms = [n for n, k in (("Groq", GROQ_API_KEY), ("Gemini", GEMINI_API_KEY)) if k]
    chain = " -> ".join(llms + ["template"]) if llms else \
        "deterministic template only (no GROQ_API_KEY / GEMINI_API_KEY set)"
    log.info("%d/%d tickers cleared attention threshold %d -- synthesis chain: %s",
             len(debate_rows), len(all_rows), ATTENTION_THRESHOLD, chain)
    methods: Counter = Counter()
    for r in debate_rows:
        debate, method = _synthesize_debate(r)
        r.update(debate)
        methods[method] += 1
    if debate_rows:
        log.info("Debate synthesis methods: %s",
                 ", ".join(f"{k}={v}" for k, v in methods.items()))

    today = datetime.now(timezone.utc).date().isoformat()
    _persist(db, today, run_type, all_rows, debate_rows)

    log.info("=" * 70)
    _report(all_rows, debate_rows)


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "midday"
    run(arg)
