"""Connectivity smoke-test for every FREE data source the report depends on.
Prints a CONNECTED / FAILED verdict per source with the reason, and writes
D:\\source_status.json so downstream scripts know what to trust vs mark
[UNVERIFIED]. Read-only public GET requests only."""
import sys, json, time, traceback
sys.stdout.reconfigure(encoding="utf-8")

status = {}

def check(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        status[name] = {"ok": True, "detail": detail, "secs": round(time.time()-t0,1)}
        print(f"[ OK ]  {name:<34} {detail}")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:200]
        status[name] = {"ok": False, "detail": msg, "secs": round(time.time()-t0,1)}
        print(f"[FAIL]  {name:<34} {msg}")

# ---- 1. yfinance ----
def _yf():
    import yfinance as yf
    d = yf.download("BEL.NS", period="5d", interval="1d", progress=False, auto_adjust=False)
    px = float(d["Close"].iloc[-1])
    return f"BEL.NS last close ₹{px:.2f}, {len(d)} rows"
check("yfinance price", _yf)

def _yf_info():
    import yfinance as yf
    info = yf.Ticker("BEL.NS").get_info() or {}
    return (f"fields={len(info)} cur={info.get('currency')} "
            f"finCur={info.get('financialCurrency')} trailingPE={info.get('trailingPE')} "
            f"P/B={info.get('priceToBook')}")
check("yfinance .info fundamentals", _yf_info)

# ---- 2. jugaad-data (NSE EOD + live) ----
def _jugaad_hist():
    from jugaad_data.nse import stock_df
    from datetime import date, timedelta
    df = stock_df(symbol="BEL", from_date=date.today()-timedelta(days=15),
                  to_date=date.today(), series="EQ")
    return f"BEL EOD rows={len(df)} lastClose={df['CLOSE'].iloc[-1] if len(df) else 'n/a'}"
check("jugaad-data EOD history", _jugaad_hist)

def _jugaad_live():
    from jugaad_data.nse import NSELive
    n = NSELive()
    q = n.stock_quote("BEL")
    p = q["priceInfo"]["lastPrice"]
    return f"BEL live lastPrice ₹{p}"
check("jugaad-data live quote", _jugaad_live)

# ---- 3. nsepython nsefetch endpoints ----
def _nse_ann():
    from nsepython import nsefetch
    r = nsefetch("https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=BEL")
    n = len(r) if isinstance(r, list) else len(r.get("data", []))
    return f"BEL announcements rows={n}"
check("nsepython announcements", _nse_ann)

def _nse_results():
    from nsepython import nsefetch
    r = nsefetch("https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=BEL&period=Quarterly")
    n = len(r) if isinstance(r, list) else len(r.get("data", []) if isinstance(r,dict) else [])
    return f"BEL financial-results rows={n}"
check("nsepython financial-results", _nse_results)

def _nse_pit():
    from nsepython import nsefetch
    r = nsefetch("https://www.nseindia.com/api/corporates-pit?index=equities&symbol=BEL")
    n = len(r.get("data", [])) if isinstance(r, dict) else (len(r) if isinstance(r,list) else 0)
    return f"BEL PIT insider rows={n}"
check("nsepython PIT insider", _nse_pit)

def _nse_sast():
    from nsepython import nsefetch
    r = nsefetch("https://www.nseindia.com/api/corporate-sast?index=equities&symbol=BEL")
    n = len(r.get("data", [])) if isinstance(r, dict) else (len(r) if isinstance(r,list) else 0)
    return f"BEL SAST rows={n}"
check("nsepython SAST", _nse_sast)

def _nse_ban():
    import requests, io
    r = requests.get("https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
                     headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    return f"ban CSV {len(r.text)} bytes"
check("NSE F&O ban CSV", _nse_ban)

# ---- 4. NseIndiaApi fallback (pip 'nse') ----
def _nseindiaapi():
    from nse import NSE
    import tempfile
    nse = NSE(download_folder=tempfile.gettempdir())
    a = nse.announcements()
    nse.exit()
    return f"NseIndiaApi announcements type={type(a).__name__} len={len(a) if hasattr(a,'__len__') else '?'}"
check("NseIndiaApi (pip nse) fallback", _nseindiaapi)

# ---- 5. Google News RSS ----
def _gnews():
    import feedparser, urllib.parse
    q = urllib.parse.quote('"Bharat Electronics" NSE share')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    f = feedparser.parse(url)
    return f"entries={len(f.entries)} first='{(f.entries[0].title[:50] if f.entries else 'none')}'"
check("Google News RSS", _gnews)

# ---- 6. VADER sentiment ----
def _vader():
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    s = SentimentIntensityAnalyzer().polarity_scores("Company bags record defence order, profit surges")
    return f"compound={s['compound']}"
check("VADER sentiment (local)", _vader)

# ---- 7. World Bank macro ----
def _wb():
    import requests
    r = requests.get("https://api.worldbank.org/v2/country/IND/indicator/FP.CPI.TOTL.ZG?format=json&per_page=3&mrnev=1", timeout=20)
    j = r.json()
    val = j[1][0] if len(j) > 1 and j[1] else None
    return f"India CPI {val['date'] if val else '?'} = {val['value'] if val else '?'}"
check("World Bank macro (CPI)", _wb)

print("\n" + "="*60)
ok = [k for k,v in status.items() if v["ok"]]
bad = [k for k,v in status.items() if not v["ok"]]
print(f"CONNECTED ({len(ok)}): {ok}")
print(f"FAILED    ({len(bad)}): {bad}")
json.dump(status, open(r"D:\source_status.json","w",encoding="utf-8"), indent=2)
print("Saved D:\\source_status.json")
