"""Options positioning context for the focus list — deterministic, no LLM.

Absorbed from HKUDS/Vibe-Trading's options tools (chain reader + pricing):
per shortlisted name, read the nearest monthly expiry chain via yfinance
(free) and distill three positioning numbers a human can read at a glance:

  put_call_oi_ratio   open-interest puts / calls   (>1 = defensive skew)
  put_call_vol_ratio  today's volume puts / calls
  atm_iv_pct          at-the-money implied vol, annualized %

Best-effort by design: options data on yfinance is flaky and thin names
have no chains — a miss writes nothing and never fails the scan. Results
merge into ranked_focus_list.signals (jsonb) under "options_context".

CLI
  python -m pipeline.agents.options_context --scan-id 123
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

THROTTLE_SEC = 0.4
TARGET_DTE = 30          # aim at the expiry nearest ~30 days out


def _context_for(ticker: str) -> dict | None:
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        expiries = tk.options
        if not expiries:
            return None
        today = datetime.now(timezone.utc).date()
        target = today + timedelta(days=TARGET_DTE)
        # Yahoo populates openInterest only on SOME expiries (verified
        # 2026-07-15: NVDA monthlies carried OI, an in-between weekly summed
        # to 0 with 1e-05 placeholder IVs). Walk candidates nearest the
        # target until one has real OI.
        candidates = sorted(
            expiries,
            key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date()
                               - target).days))[:4]
        calls = puts = None
        expiry = None
        call_oi = put_oi = 0.0
        # Liquidity gate on OI+volume combined: Yahoo often serves zero OI
        # intraday (OI updates overnight) while volume is rich — verified
        # 2026-07-15 on NVDA (OI 0 across expiries, volume 1.5M). Whichever
        # is populated carries the gate.
        for exp in candidates:
            chain = tk.option_chain(exp)
            c_oi = float(chain.calls["openInterest"].fillna(0).sum()) \
                if "openInterest" in chain.calls else 0.0
            p_oi = float(chain.puts["openInterest"].fillna(0).sum()) \
                if "openInterest" in chain.puts else 0.0
            c_vol = float(chain.calls["volume"].fillna(0).sum())
            p_vol = float(chain.puts["volume"].fillna(0).sum())
            if c_oi + p_oi + c_vol + p_vol >= 1000:
                calls, puts, expiry = chain.calls, chain.puts, exp
                call_oi, put_oi = c_oi, p_oi
                call_vol, put_vol = c_vol, p_vol
                break
            time.sleep(0.2)
        if expiry is None:
            return None

        # ATM IV: median of the 3 strikes nearest spot, both sides.
        spot = None
        hist = tk.history(period="1d")
        if hist is not None and not hist.empty:
            spot = float(hist["Close"].iloc[-1])
        atm_iv = None
        if spot:
            ivs = []
            for df in (calls, puts):
                if "impliedVolatility" in df and not df.empty:
                    near = df.iloc[(df["strike"] - spot).abs().argsort()[:3]]
                    ivs.extend(near["impliedVolatility"].dropna().tolist())
            # Yahoo pads illiquid strikes with placeholder IVs (1e-05, ~0.03).
            # Nothing liquid trades under 5% annualized vol — below that
            # we return None rather than publish junk.
            ivs = [v for v in ivs if 0.05 < v < 5]
            if ivs:
                ivs.sort()
                atm_iv = round(100 * ivs[len(ivs) // 2], 1)

        return {
            "expiry": expiry,
            "put_call_oi_ratio": round(put_oi / call_oi, 3) if call_oi > 0 else None,
            "put_call_vol_ratio": round(put_vol / call_vol, 3) if call_vol > 0 else None,
            "atm_iv_pct": atm_iv,
            "total_oi": int(call_oi + put_oi),
            "total_volume": int(call_vol + put_vol),
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception as exc:   # noqa: BLE001
        log.debug("options_context %s: %s", ticker, exc)
        return None


def run(scan_id: int) -> tuple[int, int]:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    rows = (db.table("ranked_focus_list")
              .select("ticker,signals")
              .eq("scan_id", scan_id)
              .order("rank").execute().data or [])
    if not rows:
        log.warning("options_context: no rows for scan_id=%d", scan_id)
        return 0, 0
    done = 0
    for r in rows:
        ctx = _context_for(r["ticker"])
        time.sleep(THROTTLE_SEC)
        if ctx is None:
            continue
        signals = r.get("signals") or {}
        if not isinstance(signals, dict):
            signals = {}
        signals["options_context"] = ctx
        try:
            (db.table("ranked_focus_list")
               .update({"signals": signals})
               .eq("scan_id", scan_id).eq("ticker", r["ticker"]).execute())
            done += 1
        except Exception as exc:   # noqa: BLE001
            log.warning("options_context %s: persist failed: %s", r["ticker"], exc)
    log.info("options_context scan_id=%d: %d/%d names enriched",
             scan_id, done, len(rows))
    return done, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-id", type=int, required=True)
    args = ap.parse_args()
    run(args.scan_id)
    return 0   # best-effort: missing options data never fails the scan


if __name__ == "__main__":
    sys.exit(main())
