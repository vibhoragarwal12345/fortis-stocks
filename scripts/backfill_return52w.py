"""One-shot: backfill return_52w_pct for the latest scan's advanced rows.

Mirrors layer1_fast_scan's math (trailing min(252, len) window of daily
closes; return vs the window's first close). Future scans compute this
natively; this only patches the scan currently on screen.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

import psycopg  # noqa: E402
import yfinance as yf  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"]


def to_yahoo(t: str) -> str:
    return t.replace("/", "-").replace(".", "-")


def main() -> int:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT latest_scan_id FROM scan_state WHERE id = 1"
            )
            row = cur.fetchone()
            if not row or not row[0]:
                print("no latest scan")
                return 1
            scan_id = row[0]
            cur.execute(
                "SELECT ticker, price FROM scan_results "
                "WHERE scan_id = %s AND advanced = true",
                (scan_id,),
            )
            targets = cur.fetchall()
            print(f"scan {scan_id}: {len(targets)} advanced rows")

            updated = 0
            for ticker, price in targets:
                if price is None:
                    continue
                try:
                    hist = yf.Ticker(to_yahoo(ticker)).history(
                        period="1y", interval="1d", auto_adjust=True
                    )
                    close = hist["Close"].dropna()
                    if len(close) < 60:
                        print(f"  {ticker}: only {len(close)} closes, skip")
                        continue
                    window = close.iloc[-min(252, len(close)):]
                    base = float(window.iloc[0])
                    if base <= 0:
                        continue
                    ret = round((float(price) / base - 1) * 100, 3)
                    cur.execute(
                        "UPDATE scan_results SET return_52w_pct = %s "
                        "WHERE scan_id = %s AND ticker = %s",
                        (ret, scan_id, ticker),
                    )
                    updated += 1
                    print(f"  {ticker}: {ret:+.1f}%")
                except Exception as e:  # noqa: BLE001 - best-effort backfill
                    print(f"  {ticker}: FAILED {e}")
            print(f"updated {updated}/{len(targets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
