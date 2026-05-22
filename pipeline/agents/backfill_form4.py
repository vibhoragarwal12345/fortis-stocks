"""
Form 4 transaction backfill  (Form 4 transaction-code data-integrity fix).

Re-parses Form 4 filings for the ticker universe and populates the
form4_transactions table (migration 037), where every row carries its SEC
transaction code and an is_directional_signal flag. anomaly_detector,
risk_flagger and catalyst_agent read that flag so that grants, vestings and
gifts no longer masquerade as insider sentiment.

Idempotent: rows upsert on (accession, txn_index), so re-running is safe and
only fills gaps.

Usage:
    python pipeline/agents/backfill_form4.py                 # whole universe, 90 days
    python pipeline/agents/backfill_form4.py --days 180
    python pipeline/agents/backfill_form4.py --tickers AAPL,MSFT,NVDA
    python pipeline/agents/backfill_form4.py --limit 25      # first 25 tickers (smoke test)
"""

from __future__ import annotations

import csv
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from agents.smart_money_intel import (  # noqa: E402
    _fetch_form4_filings, _parse_form4_xml, _resolve_cik,
)

ROOT = Path(__file__).resolve().parent.parent          # pipeline/
TICKER_UNIVERSE = ROOT / "data" / "tickers.csv"

DEFAULT_LOOKBACK_DAYS = 90
MAX_FILINGS_PER_TICKER = 150        # safety cap for very active issuers
EDGAR_SLEEP = 0.12                  # ~8 req/s -- under SEC's 10/s limit
DB_BATCH = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_form4")


def _load_universe() -> list[str]:
    if not TICKER_UNIVERSE.is_file():
        log.error("ticker universe not found: %s", TICKER_UNIVERSE)
        return []
    out: list[str] = []
    with TICKER_UNIVERSE.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            t = row[0].strip().upper()
            if t and t != "TICKER":
                out.append(t)
    return out


def _valid_date(s) -> str | None:
    s = (s or "")[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return None


def _txn_rows(ticker: str, cik: str, accession: str,
              txns: list[dict]) -> list[dict]:
    rows = []
    for t in txns:
        rows.append({
            "ticker":                ticker,
            "cik":                   cik,
            "accession":             accession,
            "txn_index":             t.get("txn_index", 0),
            "insider":               t.get("insider"),
            "role":                  t.get("role"),
            "transaction_code":      t.get("tx_code"),
            "transaction_subcode":   None,
            "is_acquisition":        t.get("is_acquisition"),
            "is_10b5_1":             bool(t.get("is_10b5_1")),
            "price_per_share":       t.get("price"),
            "shares":                t.get("shares"),
            "transaction_value":     t.get("value"),
            "transaction_date":      _valid_date(t.get("date")),
            "is_directional_signal": bool(t.get("is_directional_signal")),
            "footnote":              (t.get("footnote") or "")[:4000] or None,
        })
    return rows


def _flush(db, buffer: list[dict]) -> int:
    """Upsert a batch; returns rows written."""
    written = 0
    for i in range(0, len(buffer), DB_BATCH):
        chunk = buffer[i:i + DB_BATCH]
        try:
            db.table("form4_transactions").upsert(
                chunk, on_conflict="accession,txn_index").execute()
            written += len(chunk)
        except Exception as exc:
            log.warning("upsert batch failed (%d rows): %s", len(chunk), exc)
    return written


def backfill(tickers: list[str], days: int) -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    total_filings = total_txns = total_directional = written = 0
    buffer: list[dict] = []
    t0 = time.time()

    for n, ticker in enumerate(tickers, start=1):
        cik = _resolve_cik(ticker)
        if not cik:
            log.debug("%s: CIK lookup failed -- skipping", ticker)
            continue
        try:
            filings = _fetch_form4_filings(cik, days)[:MAX_FILINGS_PER_TICKER]
        except Exception as exc:
            log.debug("%s: Form 4 index failed: %s", ticker, exc)
            continue

        tkr_txns = 0
        for f in filings:
            try:
                txns = _parse_form4_xml(cik, f["accession"])
            except Exception as exc:
                log.debug("%s/%s parse failed: %s", ticker, f["accession"], exc)
                continue
            time.sleep(EDGAR_SLEEP)
            if not txns:
                continue
            rows = _txn_rows(ticker, cik, f["accession"], txns)
            buffer.extend(rows)
            tkr_txns += len(rows)
            total_directional += sum(1 for r in rows if r["is_directional_signal"])

        total_filings += len(filings)
        total_txns += tkr_txns
        if len(buffer) >= DB_BATCH:
            written += _flush(db, buffer)
            buffer = []
        if n % 50 == 0:
            log.info("[%d/%d] %s -- %d filings, %d txns so far (%.0fs)",
                     n, len(tickers), ticker, total_filings, total_txns,
                     time.time() - t0)

    if buffer:
        written += _flush(db, buffer)

    log.info("=" * 64)
    log.info("BACKFILL COMPLETE -- %d tickers, %d filings, %d transactions",
             len(tickers), total_filings, total_txns)
    log.info("  directional signals (P / S non-10b5-1 / V): %d", total_directional)
    log.info("  rows upserted to form4_transactions: %d", written)
    log.info("  elapsed: %.0f min", (time.time() - t0) / 60)


def main() -> None:
    args = sys.argv[1:]
    days = DEFAULT_LOOKBACK_DAYS
    limit = None
    explicit: list[str] | None = None
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--tickers" and i + 1 < len(args):
            explicit = [t.strip().upper() for t in args[i + 1].split(",") if t.strip()]
            i += 2
        else:
            print(__doc__)
            sys.exit(2)

    tickers = explicit if explicit else _load_universe()
    if limit:
        tickers = tickers[:limit]
    if not tickers:
        log.error("no tickers to process")
        sys.exit(1)
    log.info("Form 4 backfill -- %d tickers, %d-day lookback", len(tickers), days)
    backfill(tickers, days)


if __name__ == "__main__":
    main()
