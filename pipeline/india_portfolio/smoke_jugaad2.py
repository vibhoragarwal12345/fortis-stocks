import sys, os
sys.stdout.reconfigure(encoding="utf-8")
from datetime import date, timedelta

# Pre-create cache dir to defeat jugaad's check-then-act makedirs race
CACHE = r"D:\jugaad_cache"
os.environ["J_CACHE_DIR"] = CACHE
os.makedirs(os.path.join(CACHE, "nsehistory-stock"), exist_ok=True)

try:
    from jugaad_data.nse import stock_df
    df = stock_df(symbol="BEL", from_date=date.today()-timedelta(days=25),
                  to_date=date.today(), series="EQ")
    print(f"[OK] jugaad EOD: BEL rows={len(df)} lastClose={df['CLOSE'].iloc[-1]} date={df['DATE'].iloc[-1]}")
except Exception as e:
    import traceback; print(f"[FAIL] jugaad EOD: {type(e).__name__}: {e}")

# redundant LIVE price via nsepython nsefetch (proven cookie handling)
try:
    from nsepython import nsefetch
    j = nsefetch("https://www.nseindia.com/api/quote-equity?symbol=BEL")
    pi = j.get("priceInfo", {})
    print(f"[OK] nsefetch quote-equity: BEL last ₹{pi.get('lastPrice')} "
          f"prevClose ₹{pi.get('previousClose')} 52wH {pi.get('weekHighLow',{}).get('max')}")
except Exception as e:
    print(f"[FAIL] nsefetch quote-equity: {type(e).__name__}: {e}")
