"""Targeted retry of jugaad-data after clearing its cache, plus a cookie-seeded
live-quote attempt. Determines whether jugaad can serve as a redundant NSE
EOD/price cross-check vs yfinance."""
import sys, traceback
sys.stdout.reconfigure(encoding="utf-8")
from datetime import date, timedelta

# 1. EOD history (cache cleared)
try:
    from jugaad_data.nse import stock_df
    df = stock_df(symbol="BEL", from_date=date.today()-timedelta(days=20),
                  to_date=date.today(), series="EQ")
    print(f"[OK] jugaad EOD: BEL rows={len(df)} lastClose={df['CLOSE'].iloc[-1]} date={df['DATE'].iloc[-1]}")
except Exception as e:
    print(f"[FAIL] jugaad EOD: {type(e).__name__}: {e}")
    traceback.print_exc()

# 2. Live quote with manual cookie seeding via nsepython's session helper
try:
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    s.get("https://www.nseindia.com", timeout=15)  # seed cookies
    s.get("https://www.nseindia.com/get-quotes/equity?symbol=BEL", timeout=15)
    r = s.get("https://www.nseindia.com/api/quote-equity?symbol=BEL",
              headers={"Referer": "https://www.nseindia.com/get-quotes/equity?symbol=BEL"}, timeout=15)
    j = r.json()
    print(f"[OK] cookie-seeded NSE quote: BEL lastPrice ₹{j['priceInfo']['lastPrice']} "
          f"(status {r.status_code})")
except Exception as e:
    print(f"[FAIL] cookie-seeded NSE quote: {type(e).__name__}: {e}")

# 3. jugaad NSELive retry
try:
    from jugaad_data.nse import NSELive
    n = NSELive()
    q = n.stock_quote("BEL")
    print(f"[OK] jugaad NSELive: BEL ₹{q['priceInfo']['lastPrice']}")
except Exception as e:
    print(f"[FAIL] jugaad NSELive: {type(e).__name__}: {e}")
