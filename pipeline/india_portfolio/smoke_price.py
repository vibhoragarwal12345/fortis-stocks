import sys, os
sys.stdout.reconfigure(encoding="utf-8")
import yfinance as yf
import pandas as pd
from datetime import date, timedelta

# yfinance: last few closes with dates (correct single-ticker extraction)
d = yf.download("BEL.NS", period="1mo", interval="1d", progress=False, auto_adjust=False)
close = d["Close"]
if hasattr(close, "columns"):  # single-ticker frame
    close = close.iloc[:, 0]
print("yfinance BEL.NS last 5 closes:")
for dt, v in close.dropna().tail(5).items():
    print(f"   {pd.Timestamp(dt).date()}  ₹{float(v):.2f}")

# jugaad EOD same window
os.environ["J_CACHE_DIR"] = r"D:\jugaad_cache"
os.makedirs(os.path.join(r"D:\jugaad_cache", "nsehistory-stock"), exist_ok=True)
from jugaad_data.nse import stock_df
jd = stock_df(symbol="BEL", from_date=date.today()-timedelta(days=30), to_date=date.today(), series="EQ")
print("\njugaad BEL last 5 EOD:")
for _, row in jd.tail(5).iterrows():
    print(f"   {row['DATE'].date() if hasattr(row['DATE'],'date') else row['DATE']}  ₹{row['CLOSE']:.2f}")
print(f"\nyfinance latest date: {pd.Timestamp(close.dropna().index[-1]).date()}  | jugaad latest: {jd['DATE'].iloc[-1]}")
