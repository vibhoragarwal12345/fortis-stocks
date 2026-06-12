"""
Commodity news — headlines only, for the narrative layer.

Feeds:
  * Sector RSS: OilPrice.com (energy), Mining.com (metals), Kitco (metals),
    Investing.com commodities (broad). Reuters killed its public RSS — a
    Google News RSS query on "Reuters commodities" terms stands in.
  * Per-commodity Google News RSS using the registry's news_query.

Headlines are VERIFIED as "this headline was published" — the *claims inside*
a headline are narrative, not data, and the narrative agent (Part B) must
treat them that way. GDELT is intentionally not wired in yet: it is noisy and
unstructured; revisit only if the geopolitical layer proves too thin.

Usage:  python pipeline/commodities/sources/news.py   # smoke test
"""

import json
import logging
import sys
import urllib.parse
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from commodities.registry import COMMODITIES  # noqa: E402
from commodities.sources.common import UNVERIFIED, utcnow_iso, verified  # noqa: E402

log = logging.getLogger("commodities.news")

SECTOR_FEEDS = {
    "oilprice": {"url": "https://oilprice.com/rss/main", "category": "energy"},
    "mining_com": {"url": "https://www.mining.com/feed/", "category": "metals"},
    # Kitco's public RSS endpoints are dead (verified 2026-06-12: every known
    # URL returns an empty/bozo feed) — a Google News metals query stands in.
    "metals_gnews": {"url": "https://news.google.com/rss/search?q=precious+metals+gold+silver+market&hl=en-US&gl=US&ceid=US:en",
                     "category": "metals"},
    "investing_commodities": {"url": "https://www.investing.com/rss/news_11.rss", "category": "all"},
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
MAX_HEADLINES = 10


def _parse_feed(url: str, limit: int = MAX_HEADLINES) -> list[dict] | None:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            return None
        return [{
            "title": e.get("title"),
            "link": e.get("link"),
            "published": e.get("published", e.get("updated")),
            "source": e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None,
        } for e in feed.entries[:limit]] or None
    except Exception as exc:  # noqa: BLE001
        log.warning("feed %s failed: %s", url, exc)
        return None


def fetch_sector_feeds() -> dict:
    out = {}
    for name, spec in SECTOR_FEEDS.items():
        entries = _parse_feed(spec["url"])
        out[name] = ({
            "status": "OK", "category": spec["category"], "headlines": entries,
            "verification": verified(f"RSS {spec['url']}"),
        } if entries else {"status": "FETCH_FAILED", "url": spec["url"],
                           "verification": UNVERIFIED})
    return out


def fetch_per_commodity() -> dict:
    out = {}
    for key, spec in COMMODITIES.items():
        url = GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(spec["news_query"]))
        entries = _parse_feed(url, limit=6)
        out[key] = ({
            "status": "OK", "query": spec["news_query"], "headlines": entries,
            "verification": verified("Google News RSS"),
        } if entries else {"status": "FETCH_FAILED", "query": spec["news_query"],
                           "verification": UNVERIFIED})
    return out


def fetch_all() -> dict:
    return {
        "fetched_at": utcnow_iso(),
        "headline_caveat": "Headlines verify only that the story was published; claims inside "
                           "them are narrative input, never data points.",
        "sector_feeds": fetch_sector_feeds(),
        "per_commodity": fetch_per_commodity(),
    }


def smoke() -> dict:
    data = fetch_all()
    return {
        "source": "news (sector RSS + Google News per commodity)",
        "needs_key": "none",
        "sector_ok": [k for k, v in data["sector_feeds"].items() if v["status"] == "OK"],
        "sector_failed": [k for k, v in data["sector_feeds"].items() if v["status"] != "OK"],
        "commodity_ok": [k for k, v in data["per_commodity"].items() if v["status"] == "OK"],
        "commodity_failed": [k for k, v in data["per_commodity"].items() if v["status"] != "OK"],
        "raw": data,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    print(json.dumps(smoke(), indent=2, default=str))
