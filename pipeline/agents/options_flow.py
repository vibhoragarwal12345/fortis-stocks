"""
Options Flow Agent
Institutional-grade options-activity scanner. For the most liquid names in the
universe it pulls option chains via yfinance, computes call/put flow metrics,
flags unusual activity and large blocks, derives IV signals, and classifies the
"smart money" pattern -- then writes one row per ticker to options_signals.

Pipeline per ticker:
  1. Pull chains for the nearest 4 weekly expirations + next monthly + next
     quarterly (yf.Ticker.option_chain).
  2. Aggregate total call / put volume and the put/call ratio.
  3. Detect unusual activity:
       unusual : contract volume > UNUSUAL_VOL_MULT x the chain's mean volume
                 AND volume > open interest (new positioning, not closing).
       block   : any single strike with volume > BLOCK_THRESHOLD contracts.
       sweep   : >= SWEEP_MIN_STRIKES strikes hit unusually hard at once.
  4. IV signals: iv_percentile (cross-sectional proxy -- see note below) and
     iv_skew (OTM put IV minus OTM call IV; positive = downside-hedge demand).
  5. Pattern: earnings_straddle / call_speculation / put_hedging / none, plus a
     0-100 conviction_score.

NOTES / LIMITATIONS
  * Liquidity ranking. The universe (tickers.csv) carries no market-cap column,
    and fetching market cap for ~1,500 names is ~1,500 slow per-ticker calls.
    Instead the top-N filter ranks by 20-day average dollar volume (price x
    volume) from one bulk download -- the truer liquidity proxy for an options
    scanner, and ~0.9-correlated with market cap. Override with the CLI limit.
  * iv_percentile. A real "current IV vs 52-week range" needs historical IV,
    which yfinance does not expose. We approximate with a CROSS-SECTIONAL
    percentile: where the ticker's ATM IV sits within the IV distribution of
    every strike scanned for it today. Treat it as a relative richness gauge.
  * yfinance / Yahoo rate-limits aggressively; failed tickers are skipped and
    the scan continues. Rows are inserted in batches so a partial run persists.

Usage:
    python pipeline/agents/options_flow.py [limit]      # default limit = 500
"""

import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from supabase import create_client

# ── Path setup ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL, TICKER_UNIVERSE_PATH  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════
# Settings
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_LIMIT      = 500     # top-N most liquid tickers to scan
RANK_BATCH_SIZE    = 200     # tickers per bulk OHLCV download (liquidity ranking)
INSERT_BATCH_SIZE  = 50      # options_signals rows per insert

UNUSUAL_VOL_MULT   = 3.0     # contract volume must exceed this x the chain mean
BLOCK_THRESHOLD    = 5000    # single-strike volume that counts as an institutional block
SWEEP_MIN_STRIKES  = 3       # unusual strikes in one chain to flag a sweep
EARNINGS_WINDOW    = 14      # days ahead an earnings date still counts as "near"
OTM_BAND           = 0.05    # +/- 5% of spot defines the OTM strike sampled for skew
ATM_BAND           = 0.03    # within 3% of spot counts as ATM

REQUEST_PAUSE      = 0.15    # polite delay between tickers (seconds)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════════════

def _num(val):
    """Coerce to float, or None for missing / NaN."""
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _is_third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


# ══════════════════════════════════════════════════════════════════════════════
# Liquidity ranking -- pick the top-N most liquid tickers
# ══════════════════════════════════════════════════════════════════════════════

def _rank_by_liquidity(tickers: list[str], limit: int) -> list[str]:
    """Return the `limit` tickers with the highest 20-day average dollar volume."""
    if len(tickers) <= limit:
        return tickers

    dollar_vol: dict[str, float] = {}
    n_batches = (len(tickers) + RANK_BATCH_SIZE - 1) // RANK_BATCH_SIZE
    log.info("Ranking %d tickers by liquidity (%d batches)...", len(tickers), n_batches)

    for bnum, i in enumerate(range(0, len(tickers), RANK_BATCH_SIZE), start=1):
        batch = tickers[i : i + RANK_BATCH_SIZE]
        try:
            raw = yf.download(batch, period="1mo", interval="1d",
                              auto_adjust=True, progress=False, group_by="ticker")
        except Exception as exc:
            log.warning("Liquidity batch %d/%d failed: %s", bnum, n_batches, exc)
            continue
        if raw is None or raw.empty:
            continue

        for t in batch:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                dv = (df["Close"] * df["Volume"]).dropna()
                if not dv.empty:
                    dollar_vol[t] = float(dv.tail(20).mean())
            except (KeyError, TypeError):
                pass
        log.info("  liquidity batch %d/%d -- %d ranked so far", bnum, n_batches,
                 len(dollar_vol))

    ranked = sorted(dollar_vol, key=dollar_vol.get, reverse=True)[:limit]
    log.info("Selected top %d tickers by 20-day average dollar volume", len(ranked))
    return ranked


# ══════════════════════════════════════════════════════════════════════════════
# Expiration selection
# ══════════════════════════════════════════════════════════════════════════════

def _pick_expirations(exps: list[str]) -> list[str]:
    """Nearest 4 weekly expirations + next monthly + next quarterly."""
    today = date.today()
    parsed = []
    for e in exps:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            parsed.append((e, d))
    parsed.sort(key=lambda x: x[1])

    chosen = [e for e, _ in parsed[:4]]                       # nearest 4 ("weeklies")
    for e, d in parsed:                                       # next monthly
        if e not in chosen and _is_third_friday(d):
            chosen.append(e)
            break
    for e, d in parsed:                                       # next quarterly
        if e not in chosen and d.month in (3, 6, 9, 12) and _is_third_friday(d):
            chosen.append(e)
            break
    return sorted(set(chosen))


# ══════════════════════════════════════════════════════════════════════════════
# Per-ticker analysis
# ══════════════════════════════════════════════════════════════════════════════

def _unusual_strikes(df: pd.DataFrame, exp: str) -> tuple[list[dict], list[dict]]:
    """Return (unusual, blocks) for one calls-or-puts chain DataFrame.

    unusual : volume > UNUSUAL_VOL_MULT x mean volume AND volume > open interest.
    blocks  : any single strike with volume > BLOCK_THRESHOLD.
    """
    if df is None or df.empty:
        return [], []
    vols = df["volume"].apply(_num)
    mean_vol = vols.dropna().mean()
    if not mean_vol or mean_vol <= 0:
        return [], []

    unusual, blocks = [], []
    for _, row in df.iterrows():
        vol = _num(row.get("volume"))
        oi  = _num(row.get("openInterest")) or 0
        if vol is None or vol <= 0:
            continue
        rec = {
            "exp":    exp,
            "strike": _num(row.get("strike")),
            "volume": int(vol),
            "oi":     int(oi),
            "iv":     round(_num(row.get("impliedVolatility")) or 0, 4),
            "vol_mult": round(vol / mean_vol, 1),
        }
        if vol > UNUSUAL_VOL_MULT * mean_vol and vol > oi:
            unusual.append(rec)
        if vol > BLOCK_THRESHOLD:
            blocks.append(rec)
    return unusual, blocks


def _atm_iv(df: pd.DataFrame, spot: float) -> float | None:
    """Mean implied volatility of strikes within ATM_BAND of spot."""
    if df is None or df.empty or not spot:
        return None
    lo, hi = spot * (1 - ATM_BAND), spot * (1 + ATM_BAND)
    band = df[(df["strike"] >= lo) & (df["strike"] <= hi)]
    ivs = band["impliedVolatility"].apply(_num).dropna() if not band.empty else []
    return round(float(pd.Series(ivs).mean()), 4) if len(ivs) else None


def _otm_iv(df: pd.DataFrame, target: float) -> float | None:
    """Implied volatility of the strike closest to `target`."""
    if df is None or df.empty or not target:
        return None
    idx = (df["strike"] - target).abs().idxmin()
    return _num(df.loc[idx, "impliedVolatility"])


def _next_earnings(tk: yf.Ticker) -> date | None:
    """Next earnings date within EARNINGS_WINDOW days, or None."""
    try:
        cal = tk.calendar
        ed = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
        elif cal is not None and "Earnings Date" in getattr(cal, "index", []):
            ed = cal.loc["Earnings Date"].tolist()
        if not ed:
            return None
        first = ed[0] if isinstance(ed, (list, tuple)) else ed
        d = first.date() if hasattr(first, "date") else first
        today = date.today()
        return d if isinstance(d, date) and today <= d <= today + timedelta(
            days=EARNINGS_WINDOW) else None
    except Exception:
        return None


def _classify_pattern(spot, calls_all, puts_all, earnings_near) -> str:
    """Smart-money pattern from the concatenated calls / puts across expirations."""
    if spot is None or calls_all.empty or puts_all.empty:
        return "none"

    atm_lo, atm_hi = spot * (1 - ATM_BAND), spot * (1 + ATM_BAND)
    atm_call = calls_all[(calls_all["strike"] >= atm_lo)
                         & (calls_all["strike"] <= atm_hi)]["volume"].apply(_num).sum()
    atm_put  = puts_all[(puts_all["strike"] >= atm_lo)
                        & (puts_all["strike"] <= atm_hi)]["volume"].apply(_num).sum()

    otm_calls = calls_all[calls_all["strike"] > spot * (1 + OTM_BAND)]
    otm_puts  = puts_all[puts_all["strike"] < spot * (1 - OTM_BAND)]
    otm_call_vol = otm_calls["volume"].apply(_num).sum()
    otm_put_vol  = otm_puts["volume"].apply(_num).sum()

    total_call = calls_all["volume"].apply(_num).sum() or 1
    total_put  = puts_all["volume"].apply(_num).sum() or 1

    # 1. Earnings straddle -- heavy ATM volume on BOTH sides into earnings.
    if (earnings_near and atm_call > 0.30 * total_call
            and atm_put > 0.30 * total_put):
        return "earnings_straddle"

    # 2. Call speculation -- OTM call volume concentrated on a specific strike.
    if otm_call_vol > 0:
        by_strike = otm_calls.groupby("strike")["volume"].apply(
            lambda s: s.apply(_num).sum())
        if not by_strike.empty and by_strike.max() > 0.25 * total_call \
                and otm_call_vol > otm_put_vol * 1.5:
            return "call_speculation"

    # 3. Put hedging -- elevated OTM put volume spread across multiple strikes.
    if otm_put_vol > 0:
        active_put_strikes = otm_puts.groupby("strike")["volume"].apply(
            lambda s: s.apply(_num).sum())
        active = (active_put_strikes > 0.05 * total_put).sum()
        if active >= 3 and otm_put_vol > total_put * 0.40:
            return "put_hedging"

    return "none"


def _conviction(unusual_calls, unusual_puts, blocks, pcr, iv_skew, sweep) -> float:
    """0-100 strength score for the day's signal on this ticker."""
    score = 0.0
    score += min((len(unusual_calls) + len(unusual_puts)) * 6, 36)
    score += min(len(blocks) * 12, 36)
    if pcr is not None and (pcr > 2.0 or pcr < 0.4):     # lopsided positioning
        score += 14
    if iv_skew is not None and abs(iv_skew) > 0.05:      # rich one-sided IV
        score += 8
    if sweep:
        score += 12
    return round(min(score, 100.0), 1)


def analyze_ticker(symbol: str) -> dict | None:
    """Full options-flow analysis for one ticker. None if no usable data."""
    try:
        tk = yf.Ticker(symbol)
        exps = list(tk.options or [])
        if not exps:
            return None
        chosen = _pick_expirations(exps)
        if not chosen:
            return None

        try:
            spot = _num(tk.fast_info.get("last_price"))
        except Exception:
            spot = None

        calls_frames, puts_frames = [], []
        unusual_calls, unusual_puts, blocks = [], [], []
        total_call_vol = total_put_vol = 0
        sweep = False
        atm_call_ivs: list[float] = []
        per_strike_ivs: list[float] = []

        for exp in chosen:
            try:
                chain = tk.option_chain(exp)
            except Exception:
                continue
            calls, puts = chain.calls, chain.puts
            if calls is not None and not calls.empty:
                calls_frames.append(calls)
                total_call_vol += int(calls["volume"].apply(_num).fillna(0).sum())
                per_strike_ivs += calls["impliedVolatility"].apply(_num).dropna().tolist()
                uc, bc = _unusual_strikes(calls, exp)
                unusual_calls += uc
                blocks += [{**b, "side": "call"} for b in bc]
                if len(uc) >= SWEEP_MIN_STRIKES:
                    sweep = True
                iv = _atm_iv(calls, spot)
                if iv is not None:
                    atm_call_ivs.append(iv)
            if puts is not None and not puts.empty:
                puts_frames.append(puts)
                total_put_vol += int(puts["volume"].apply(_num).fillna(0).sum())
                per_strike_ivs += puts["impliedVolatility"].apply(_num).dropna().tolist()
                up, bp = _unusual_strikes(puts, exp)
                unusual_puts += up
                blocks += [{**b, "side": "put"} for b in bp]
                if len(up) >= SWEEP_MIN_STRIKES:
                    sweep = True

        if not calls_frames and not puts_frames:
            return None

        calls_all = pd.concat(calls_frames) if calls_frames else pd.DataFrame()
        puts_all  = pd.concat(puts_frames) if puts_frames else pd.DataFrame()

        pcr = round(total_put_vol / total_call_vol, 3) if total_call_vol else None

        # IV skew: OTM put IV minus OTM call IV on the front expiration chain.
        iv_skew = None
        if spot and calls_frames and puts_frames:
            put_iv  = _otm_iv(puts_frames[0],  spot * (1 - OTM_BAND))
            call_iv = _otm_iv(calls_frames[0], spot * (1 + OTM_BAND))
            if put_iv is not None and call_iv is not None:
                iv_skew = round(put_iv - call_iv, 4)

        # IV percentile: cross-sectional -- ATM IV's rank within all strike IVs.
        iv_percentile = None
        if atm_call_ivs and per_strike_ivs:
            atm = sum(atm_call_ivs) / len(atm_call_ivs)
            series = pd.Series(per_strike_ivs)
            iv_percentile = round(float((series <= atm).mean()) * 100, 1)

        earnings = _next_earnings(tk)
        pattern = _classify_pattern(spot, calls_all, puts_all, earnings is not None)
        conviction = _conviction(unusual_calls, unusual_puts, blocks,
                                 pcr, iv_skew, sweep)

        unusual_calls.sort(key=lambda r: r["volume"], reverse=True)
        unusual_puts.sort(key=lambda r: r["volume"], reverse=True)
        blocks.sort(key=lambda r: r["volume"], reverse=True)

        return {
            "ticker":               symbol,
            "expiration_date":      chosen[0],
            "total_call_volume":    total_call_vol,
            "total_put_volume":     total_put_vol,
            "put_call_ratio":       pcr,
            "unusual_call_strikes": unusual_calls[:25],
            "unusual_put_strikes":  unusual_puts[:25],
            "large_block_alerts":   blocks[:25],
            "iv_percentile":        iv_percentile,
            "iv_skew":              iv_skew,
            "pattern_detected":     pattern,
            "conviction_score":     conviction,
            "_sweep":               sweep,
            "_earnings":            earnings.isoformat() if earnings else None,
        }
    except Exception as exc:
        log.debug("analyze_ticker(%s) failed: %s", symbol, exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════════════════════

def _row_for_db(res: dict, snapshot_time: str) -> dict:
    return {
        "ticker":               res["ticker"],
        "snapshot_time":        snapshot_time,
        "expiration_date":      res["expiration_date"],
        "total_call_volume":    res["total_call_volume"],
        "total_put_volume":     res["total_put_volume"],
        "put_call_ratio":       res["put_call_ratio"],
        "unusual_call_strikes": res["unusual_call_strikes"],
        "unusual_put_strikes":  res["unusual_put_strikes"],
        "large_block_alerts":   res["large_block_alerts"],
        "iv_percentile":        res["iv_percentile"],
        "iv_skew":              res["iv_skew"],
        "pattern_detected":     res["pattern_detected"],
        "conviction_score":     res["conviction_score"],
    }


def _flush(db, rows: list[dict]) -> int:
    if not db or not rows:
        return 0
    try:
        resp = db.table("options_signals").insert(rows).execute()
        return len(resp.data) if resp.data else 0
    except Exception as exc:
        log.warning("options_signals insert failed (%d rows): %s", len(rows), exc)
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# Reporting
# ══════════════════════════════════════════════════════════════════════════════

def _report(results: list[dict]) -> None:
    bar = "=" * 74
    print(f"\n{bar}\nOPTIONS FLOW SCAN -- {len(results)} tickers analyzed\n{bar}")

    def _uvol(res, key):
        return sum(r["volume"] for r in res[key])

    # Top 10 by unusual call activity --------------------------------------------
    by_call = sorted((r for r in results if r["unusual_call_strikes"]),
                     key=lambda r: _uvol(r, "unusual_call_strikes"), reverse=True)[:10]
    print("\n[ TOP 10 -- UNUSUAL CALL ACTIVITY ]")
    if by_call:
        print(f"  {'TICKER':<8}{'UNUSUAL VOL':>13}{'#STRIKES':>10}{'P/C':>8}{'CONV':>7}")
        for r in by_call:
            print(f"  {r['ticker']:<8}{_uvol(r,'unusual_call_strikes'):>13,}"
                  f"{len(r['unusual_call_strikes']):>10}"
                  f"{(r['put_call_ratio'] if r['put_call_ratio'] is not None else 0):>8.2f}"
                  f"{r['conviction_score']:>7.0f}")
    else:
        print("  (none detected)")

    # Top 10 by unusual put activity ---------------------------------------------
    by_put = sorted((r for r in results if r["unusual_put_strikes"]),
                    key=lambda r: _uvol(r, "unusual_put_strikes"), reverse=True)[:10]
    print("\n[ TOP 10 -- UNUSUAL PUT ACTIVITY ]")
    if by_put:
        print(f"  {'TICKER':<8}{'UNUSUAL VOL':>13}{'#STRIKES':>10}{'P/C':>8}{'CONV':>7}")
        for r in by_put:
            print(f"  {r['ticker']:<8}{_uvol(r,'unusual_put_strikes'):>13,}"
                  f"{len(r['unusual_put_strikes']):>10}"
                  f"{(r['put_call_ratio'] if r['put_call_ratio'] is not None else 0):>8.2f}"
                  f"{r['conviction_score']:>7.0f}")
    else:
        print("  (none detected)")

    # Large block trades ---------------------------------------------------------
    blocks = []
    for r in results:
        for b in r["large_block_alerts"]:
            blocks.append((r["ticker"], b))
    blocks.sort(key=lambda x: x[1]["volume"], reverse=True)
    print(f"\n[ LARGE BLOCK TRADES -- single strike > {BLOCK_THRESHOLD:,} contracts ]")
    if blocks:
        print(f"  {'TICKER':<8}{'SIDE':<6}{'STRIKE':>10}{'VOLUME':>11}{'OI':>11}  EXP")
        for tk, b in blocks[:25]:
            print(f"  {tk:<8}{b.get('side',''):<6}{b['strike']:>10.2f}"
                  f"{b['volume']:>11,}{b['oi']:>11,}  {b['exp']}")
        if len(blocks) > 25:
            print(f"  ... and {len(blocks) - 25} more")
    else:
        print("  (none detected)")

    # Smart-money earnings positioning -------------------------------------------
    straddles = sorted((r for r in results if r["pattern_detected"] == "earnings_straddle"),
                       key=lambda r: r["conviction_score"], reverse=True)
    print("\n[ SMART-MONEY EARNINGS POSITIONING -- earnings_straddle pattern ]")
    if straddles:
        print(f"  {'TICKER':<8}{'EARNINGS':<13}{'CALL VOL':>11}{'PUT VOL':>11}{'CONV':>7}")
        for r in straddles:
            print(f"  {r['ticker']:<8}{(r['_earnings'] or 'n/a'):<13}"
                  f"{r['total_call_volume']:>11,}{r['total_put_volume']:>11,}"
                  f"{r['conviction_score']:>7.0f}")
    else:
        print("  (none detected)")

    # Pattern summary ------------------------------------------------------------
    counts: dict[str, int] = {}
    for r in results:
        counts[r["pattern_detected"]] = counts.get(r["pattern_detected"], 0) + 1
    print("\n[ PATTERN SUMMARY ]  " + "  ".join(
        f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])))
    print(bar + "\n")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(limit: int = DEFAULT_LIMIT) -> None:
    universe = (
        pd.read_csv(TICKER_UNIVERSE_PATH)["ticker"].dropna().astype(str).tolist()
    )
    log.info("Universe: %d tickers -- scanning top %d by liquidity", len(universe), limit)

    tickers = _rank_by_liquidity(universe, limit)

    try:
        db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as exc:
        log.warning("Supabase client init failed -- running without DB: %s", exc)
        db = None

    snapshot_time = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    pending: list[dict] = []
    inserted = skipped = 0

    for n, sym in enumerate(tickers, start=1):
        res = analyze_ticker(sym)
        if res is None:
            skipped += 1
        else:
            results.append(res)
            pending.append(_row_for_db(res, snapshot_time))
            if len(pending) >= INSERT_BATCH_SIZE:
                inserted += _flush(db, pending)
                pending = []
        if n % 25 == 0 or n == len(tickers):
            log.info("Scanned %d/%d -- %d analyzed, %d skipped, %d stored",
                     n, len(tickers), len(results), skipped, inserted)
        time.sleep(REQUEST_PAUSE)

    inserted += _flush(db, pending)
    log.info("Scan complete. %d tickers analyzed, %d skipped, %d rows stored.",
             len(results), skipped, inserted)
    if db is None or inserted == 0:
        log.warning("No rows persisted -- apply supabase/migrations/"
                    "004_options_signals.sql, then re-run.")

    _report(results)


if __name__ == "__main__":
    arg = DEFAULT_LIMIT
    if len(sys.argv) > 1:
        try:
            arg = int(sys.argv[1])
        except ValueError:
            log.warning("Invalid limit '%s' -- using %d", sys.argv[1], DEFAULT_LIMIT)
    run(arg)
