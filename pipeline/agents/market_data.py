"""
Market Data Agent
Fetches daily OHLCV snapshots for every ticker in tickers.csv via yfinance,
computes gap_pct and relative_volume, and bulk-inserts into market_snapshots.

Batches 200 tickers per yfinance.download() call — typically < 5 min for 1 500 tickers.

Usage:
    python pipeline/agents/market_data.py
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL, TICKER_UNIVERSE_PATH

BATCH_SIZE       = 200   # tickers per yfinance.download() call
HISTORY_DAYS     = 35    # ~35 calendar days → ≥30 trading days
INSERT_BATCH_SIZE = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Download
# ══════════════════════════════════════════════════════════════════════════════

def _download_batch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Download HISTORY_DAYS of daily OHLCV for a list of tickers.
    Returns {ticker: DataFrame} only for tickers that returned usable data.
    """
    try:
        raw = yf.download(
            tickers,
            period=f"{HISTORY_DAYS}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        log.warning("yfinance download error: %s", exc)
        return {}

    if raw is None or raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        # Multiple tickers: columns are (ticker, field)
        for t in tickers:
            try:
                df = raw[t].dropna(how="all")
                if not df.empty:
                    result[t] = df
            except KeyError:
                pass  # delisted / unknown — silently skip
    else:
        # Single ticker: flat columns
        df = raw.dropna(how="all")
        if not df.empty:
            result[tickers[0]] = df

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Snapshot computation
# ══════════════════════════════════════════════════════════════════════════════

def _f(val) -> float | None:
    """Return float or None for a scalar that might be NaN / None."""
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _compute_snapshot(ticker: str, df: pd.DataFrame, now: datetime) -> dict | None:
    """Build a market_snapshots row from a ticker's OHLCV DataFrame."""
    if len(df) < 2:
        return None
    try:
        latest = df.iloc[-1]
        prev   = df.iloc[-2]

        price      = _f(latest.get("Close"))
        open_      = _f(latest.get("Open"))
        high       = _f(latest.get("High"))
        low        = _f(latest.get("Low"))
        prev_close = _f(prev.get("Close"))

        vol_raw = latest.get("Volume")
        volume  = int(vol_raw) if vol_raw is not None and not pd.isna(vol_raw) else None

        vol_series = df["Volume"].iloc[-30:].dropna()
        avg_vol    = float(vol_series.mean()) if not vol_series.empty else None

        if price is None:
            return None

        gap_pct = None
        if open_ is not None and prev_close and prev_close != 0:
            gap_pct = round((open_ - prev_close) / prev_close * 100, 4)

        rel_vol = None
        if volume is not None and avg_vol and avg_vol > 0:
            rel_vol = round(volume / avg_vol, 4)

        return {
            "ticker":          ticker,
            "snapshot_time":   now.isoformat(),
            "price":           round(price, 4),
            "open":            round(open_, 4)  if open_ is not None else None,
            "high":            round(high, 4)   if high  is not None else None,
            "low":             round(low, 4)    if low   is not None else None,
            "volume":          volume,
            "avg_volume_30d":  int(avg_vol) if avg_vol is not None else None,
            "gap_pct":         gap_pct,
            "relative_volume": rel_vol,
        }
    except Exception as exc:
        log.debug("Snapshot compute error [%s]: %s", ticker, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Database insert
# ══════════════════════════════════════════════════════════════════════════════

def _bulk_insert(client, rows: list[dict]) -> int:
    inserted = 0
    for i in range(0, len(rows), INSERT_BATCH_SIZE):
        batch = rows[i : i + INSERT_BATCH_SIZE]
        try:
            resp = client.table("market_snapshots").insert(batch).execute()
            inserted += len(resp.data) if resp.data else 0
        except Exception as exc:
            log.warning("Batch insert failed (rows %d–%d): %s", i, i + len(batch), exc)
    return inserted


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> None:
    tickers: list[str] = (
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).tolist()
    )
    total    = len(tickers)
    n_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    log.info("Loaded %d tickers — %d batches of up to %d", total, n_batches, BATCH_SIZE)

    now = datetime.now(timezone.utc)
    db  = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    all_rows: list[dict] = []
    skipped = 0

    for batch_num, i in enumerate(range(0, total, BATCH_SIZE), start=1):
        batch = tickers[i : i + BATCH_SIZE]
        dfs   = _download_batch(batch)

        for t in batch:
            df = dfs.get(t)
            if df is None:
                skipped += 1
                continue
            row = _compute_snapshot(t, df, now)
            if row is None:
                skipped += 1
            else:
                all_rows.append(row)

        log.info(
            "Batch %2d/%d — %d/%d tickers returned data — %d snapshots so far",
            batch_num, n_batches, len(dfs), len(batch), len(all_rows),
        )

    log.info(
        "Download complete. %d snapshots collected, %d tickers skipped (no data / delisted).",
        len(all_rows), skipped,
    )

    if not all_rows:
        log.warning("No rows to insert — exiting.")
        return

    log.info("Inserting %d rows into market_snapshots…", len(all_rows))
    inserted = _bulk_insert(db, all_rows)
    log.info("Done. %d rows inserted.", inserted)

    # ── Top 10 by relative_volume from this run ────────────────────────────
    cutoff = (now - timedelta(minutes=10)).isoformat()
    resp = (
        db.table("market_snapshots")
        .select("ticker,price,volume,avg_volume_30d,gap_pct,relative_volume")
        .gte("snapshot_time", cutoff)
        .order("relative_volume", desc=True)
        .limit(10)
        .execute()
    )
    if resp.data:
        log.info("")
        log.info("── Top 10 by relative_volume ──────────────────────────────────")
        log.info("%-8s  %-9s  %-8s  %-9s  %s", "TICKER", "PRICE", "REL_VOL", "GAP_PCT", "VOLUME")
        log.info("%-8s  %-9s  %-8s  %-9s  %s", "──────", "─────", "───────", "───────", "──────")
        for row in resp.data:
            log.info(
                "%-8s  $%-8.2f  %-8.2fx  %+.2f%%       %s",
                row["ticker"],
                row.get("price") or 0,
                row.get("relative_volume") or 0,
                row.get("gap_pct") or 0,
                f"{row['volume']:,}" if row.get("volume") else "N/A",
            )


if __name__ == "__main__":
    run()
