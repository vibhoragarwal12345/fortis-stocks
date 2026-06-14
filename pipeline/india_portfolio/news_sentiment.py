"""Per-holding news flow + local sentiment + Signal-vs-Chatter separation.
Google News RSS (chatter, never treated as fact) -> VADER sentiment -> split into
VERIFIED material info (only items that map to an official, dated, attributable
source: regulatory/result/order filings) vs UNVERIFIED chatter (everything else).
Writes D:\\news_sentiment.json. RSS is opinion/flow only; nothing here is a
price-moving fact unless corroborated by the NSE filing layer."""
from __future__ import annotations
import json, sys, time, urllib.parse, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from portfolio_input import load_holdings

VADER = SentimentIntensityAnalyzer()

# Headlines that *claim* a material, filing-backed event (still must be corroborated
# by the NSE filing layer to be called VERIFIED -- here we only pre-classify intent).
MATERIAL_KWS = ["order", "contract", "bags", "wins", "awarded", "loa", "result",
                "profit", "revenue", "ebitda", "dividend", "qip", "fund rais",
                "acquisition", "merger", "stake", "sebi", "rating", "approval",
                "board", "buyback", "guidance"]
CHATTER_KWS = ["could", "may ", "should you", "multibagger", "target", "tip",
               "best stocks", "rally", "soar", "surge", "crash", "buzz", "watch",
               "expert", "analyst says", "stocks to", "rumour", "rumor"]


def classify_item(title: str) -> str:
    t = title.lower()
    if any(k in t for k in CHATTER_KWS):
        return "chatter"
    if any(k in t for k in MATERIAL_KWS):
        return "material_claim"   # corroborate against filings before trusting
    return "neutral_flow"


def fetch_news(company: str, n=12) -> list[dict]:
    q = urllib.parse.quote(f'"{company}" NSE share')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    f = feedparser.parse(url)
    out = []
    for e in f.entries[:n]:
        title = getattr(e, "title", "")
        s = VADER.polarity_scores(title)
        out.append({"title": title, "source": getattr(getattr(e, "source", None), "title", None),
                    "published": getattr(e, "published", None),
                    "compound": s["compound"], "klass": classify_item(title)})
    return out


def summarize(items: list[dict]) -> dict:
    if not items:
        return {"n": 0, "avg_compound": None, "n_material_claim": 0, "n_chatter": 0,
                "pos": 0, "neg": 0, "neu": 0, "tone": "no coverage"}
    comp = [i["compound"] for i in items]
    avg = sum(comp)/len(comp)
    pos = sum(1 for c in comp if c >= 0.25); neg = sum(1 for c in comp if c <= -0.25)
    neu = len(comp) - pos - neg
    tone = "positive" if avg >= 0.15 else "negative" if avg <= -0.15 else "mixed/neutral"
    return {"n": len(items), "avg_compound": round(avg, 3),
            "n_material_claim": sum(1 for i in items if i["klass"] == "material_claim"),
            "n_chatter": sum(1 for i in items if i["klass"] == "chatter"),
            "pos": pos, "neg": neg, "neu": neu, "tone": tone}


def main():
    holdings = load_holdings()
    out = {}
    for h in holdings:
        # use a clean company string (drop ' Ltd')
        company = h["name"].replace(" Ltd", "").strip()
        try:
            items = fetch_news(company)
        except Exception as e:
            out[h["ticker"]] = {"error": str(e)[:100]};
            print(f"  {company:<28} ERROR {e}"); time.sleep(1); continue
        s = summarize(items)
        out[h["ticker"]] = {"company": company, "summary": s,
                            "headlines": [{"title": i["title"], "compound": i["compound"],
                                           "klass": i["klass"], "source": i["source"]} for i in items[:8]]}
        print(f"  {company:<28} n={s['n']:>2} tone={s['tone']:<14} avg={s['avg_compound']} "
              f"material={s['n_material_claim']} chatter={s['n_chatter']}")
        time.sleep(1.0)
    json.dump({"generated_at_utc": __import__("datetime").datetime.utcnow().isoformat(),
               "source": "Google News RSS (flow/opinion only) + local VADER sentiment",
               "holdings": out}, open(r"D:\news_sentiment.json","w",encoding="utf-8"), indent=2)
    print("\nSaved D:\\news_sentiment.json")


if __name__ == "__main__":
    main()
