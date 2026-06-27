"""
SHADOW backtest -- does the freshness tag / extension predict the trap?
=======================================================================
Runs the local extension.py advisory against the CLEAN pick_outcomes
(recommended_date >= CUTOFF) and aggregates by tag. LOCAL/SHADOW only:
reads prod pick_outcomes read-only, changes nothing, lives outside the repo.

CAVEAT: as of late June only 1d (some 5d) forward returns have matured; the
20d give-back horizon lands ~mid-July. The ONE thing fully measurable now is
the open-vs-close timing cost (needs only the next open). Re-run at 20d.

    python pipeline/scan/setup_backtest.py            # cutoff 2026-06-17
    python pipeline/scan/setup_backtest.py 2026-06-17
"""

import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402
from scan.extension import classify_breakout, compute_extension, _gap_pct  # noqa: E402

CUTOFF = sys.argv[1] if len(sys.argv) > 1 else "2026-06-17"


def _to_yahoo(t: str) -> str:
    return t.replace("/", "-").replace(".", "-")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def main() -> int:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = []
    start = 0
    while True:
        chunk = (db.table("pick_outcomes")
                 .select("ticker,recommended_date,conviction_grade,composite_score,"
                         "entry_price,price_1d,price_5d,price_20d,"
                         "return_1d,return_5d,return_20d,"
                         "max_gain_during_period,max_drawdown_during_period")
                 .gte("recommended_date", CUTOFF)
                 .range(start, start + 999).execute().data or [])
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        start += 1000
    if not rows:
        print(f"No clean picks since {CUTOFF}."); return 0

    dates = sorted({r["recommended_date"] for r in rows})
    tickers = sorted({r["ticker"] for r in rows})
    print(f"Clean picks since {CUTOFF}: {len(rows)} rows, {len(tickers)} tickers, "
          f"dates {dates[0]}..{dates[-1]}")

    # Batch-download history once, covering 175d before the earliest pick.
    import yfinance as yf
    lo = pd.to_datetime(dates[0]).date() - timedelta(days=175)
    hi = pd.to_datetime(dates[-1]).date() + timedelta(days=12)
    yahoo = {t: _to_yahoo(t) for t in tickers}
    print(f"Downloading {len(tickers)} tickers {lo}..{hi} ...")
    data = yf.download(tickers=" ".join(yahoo.values()), start=lo.isoformat(),
                       end=hi.isoformat(), interval="1d", group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    multi = isinstance(data.columns, pd.MultiIndex)

    def _df_for(orig):
        yt = yahoo[orig]
        try:
            sub = data[yt] if multi else data
        except KeyError:
            return None
        df = sub.rename(columns=str.lower)
        keep = [c for c in ("open", "high", "low", "close") if c in df.columns]
        df = df[keep].dropna()
        return df if not df.empty else None

    by_tag = defaultdict(lambda: defaultdict(list))
    evaluated = skipped = 0
    for r in rows:
        df = _df_for(r["ticker"])
        if df is None:
            skipped += 1; continue
        rd = pd.to_datetime(r["recommended_date"]).date()
        idx = [pd.Timestamp(x).date() for x in df.index]
        asof_pos = max((i for i, dt in enumerate(idx) if dt <= rd), default=None)
        if asof_pos is None or asof_pos < 24:
            skipped += 1; continue
        sub = df.iloc[: asof_pos + 1]
        ext = compute_extension(df, asof_index=asof_pos)
        gap = _gap_pct(sub)
        tag = classify_breakout(ext, gap)
        close_asof = float(sub["close"].iloc[-1])
        nxt = df.iloc[asof_pos + 1:]
        next_open = float(nxt["open"].iloc[0]) if len(nxt) else None
        timing = ((next_open / close_asof - 1) * 100) if next_open else None
        # realized return from the close we track vs the open a real buyer pays
        ret1d_close = r.get("return_1d")
        ret1d_open = (((r["price_1d"] / next_open) - 1) * 100
                      if r.get("price_1d") and next_open else None)

        d = by_tag[tag]
        d["ext"].append(ext.get("extension_score"))
        d["gap"].append(gap)
        d["timing"].append(timing)
        d["ret1d_close"].append(ret1d_close)
        d["ret1d_open"].append(ret1d_open)
        d["ret5d"].append(r.get("return_5d"))
        d["ret20d"].append(r.get("return_20d"))
        evaluated += 1

    print(f"evaluated={evaluated}  skipped(no price/short history)={skipped}\n")

    order = ["SETUP", "FRESH_PIVOT", "MID_MOVE", "GAP_CHASE", "STALE", "EXTENDED", "NEUTRAL", "UNKNOWN"]
    hdr = (f"{'TAG':<12}{'n':>4}{'avg_ext':>8}{'avg_gap':>8}{'timing':>8}"
           f"{'ret1d_cl':>9}{'ret1d_op':>9}{'drag':>7}{'m5d':>5}{'avg5d':>7}{'m20d':>5}")
    print(hdr); print("-" * len(hdr))
    allrows = [t for t in order if t in by_tag] + [t for t in by_tag if t not in order]
    for tag in allrows:
        d = by_tag[tag]
        n = len(d["ext"])
        rc, ro = _mean(d["ret1d_close"]), _mean(d["ret1d_open"])
        drag = round(ro - rc, 2) if (rc is not None and ro is not None) else None
        m5 = sum(1 for x in d["ret5d"] if x is not None)
        m20 = sum(1 for x in d["ret20d"] if x is not None)
        def f(v, w=8): return ("--".rjust(w) if v is None else f"{v:>{w}.2f}")
        print(f"{tag:<12}{n:>4}{f(_mean(d['ext']))}{f(_mean(d['gap']))}{f(_mean(d['timing']))}"
              f"{f(rc,9)}{f(ro,9)}{f(drag,7)}{m5:>5}{f(_mean(d['ret5d']),7)}{m20:>5}")

    # overall
    alld = defaultdict(list)
    for d in by_tag.values():
        for k, v in d.items():
            alld[k].extend(v)
    rc, ro = _mean(alld["ret1d_close"]), _mean(alld["ret1d_open"])
    print("-" * len(hdr))
    print(f"{'ALL':<12}{len(alld['ext']):>4}{(_mean(alld['ext']) or 0):>8.2f}"
          f"{(_mean(alld['gap']) or 0):>8.2f}{(_mean(alld['timing']) or 0):>8.2f}"
          f"{(rc or 0):>9.2f}{(ro or 0):>9.2f}{((ro - rc) if (rc and ro) else 0):>7.2f}"
          f"{sum(1 for x in alld['ret5d'] if x is not None):>5}"
          f"{(_mean(alld['ret5d']) or 0):>7.2f}"
          f"{sum(1 for x in alld['ret20d'] if x is not None):>5}")

    print("\nKEY: timing = next-open vs tracked close (what a pre-market buyer overpays).")
    print("     ret1d_cl = 1d return from the close we track; ret1d_op = from the next open.")
    print("     drag = ret1d_op - ret1d_cl (the realized cost of the timing lag).")
    print("     m5d/m20d = how many matured to 5d/20d. 20d (the give-back) ~mid-July.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
