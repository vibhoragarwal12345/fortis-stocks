"""
Load a sample portfolio into Supabase so portfolio_fit.py has something real
to score against.

  * Creates (or finds) a demo auth user 'demo@fortis.local' -- the
    portfolios.user_id FK to auth.users is NOT NULL, so we need a real auth
    row, not a synthetic UUID.
  * Creates the 'Fortis Demo Portfolio' if it doesn't already exist.
  * Inserts holdings from sample_portfolio.csv (idempotent: skips existing
    (portfolio_id, ticker) rows).

Usage:
    python pipeline/data/load_sample_portfolio.py
"""

import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SUPABASE_SERVICE_KEY, SUPABASE_URL  # noqa: E402
from supabase import create_client  # noqa: E402

DEMO_EMAIL = "demo@fortis.local"
DEMO_PASSWORD = "DemoPortfolio-2026!"     # the demo user can be deleted any time
PORTFOLIO_NAME = "Fortis Demo Portfolio"
CSV_PATH = Path(__file__).resolve().parent / "sample_portfolio.csv"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


def _ensure_demo_user(db) -> str:
    """Return user_id, creating the demo auth user if it doesn't exist."""
    try:
        existing = db.auth.admin.list_users()
        users = existing if isinstance(existing, list) else getattr(existing, "users", [])
        for u in users:
            email = getattr(u, "email", None) or u.get("email")
            if email == DEMO_EMAIL:
                uid = getattr(u, "id", None) or u.get("id")
                log.info("Demo user already exists: %s", uid)
                return str(uid)
    except Exception as exc:
        log.warning("list_users failed: %s -- attempting create anyway", exc)

    try:
        resp = db.auth.admin.create_user({
            "email": DEMO_EMAIL,
            "password": DEMO_PASSWORD,
            "email_confirm": True,
        })
        user = getattr(resp, "user", None) or resp.get("user")
        uid = getattr(user, "id", None) or user.get("id")
        log.info("Created demo user: %s", uid)
        return str(uid)
    except Exception as exc:
        # Most often this is "email already exists"; retry list_users.
        log.warning("create_user failed: %s -- retrying list_users", exc)
        existing = db.auth.admin.list_users()
        users = existing if isinstance(existing, list) else getattr(existing, "users", [])
        for u in users:
            email = getattr(u, "email", None) or u.get("email")
            if email == DEMO_EMAIL:
                return str(getattr(u, "id", None) or u.get("id"))
        raise RuntimeError("Cannot create or find demo user")


def _ensure_portfolio(db, user_id: str) -> str:
    existing = (db.table("portfolios").select("id")
                .eq("user_id", user_id).eq("name", PORTFOLIO_NAME)
                .execute().data or [])
    if existing:
        log.info("Portfolio exists: %s", existing[0]["id"])
        return existing[0]["id"]
    resp = (db.table("portfolios").insert({"user_id": user_id, "name": PORTFOLIO_NAME})
            .execute())
    pid = resp.data[0]["id"]
    log.info("Created portfolio: %s", pid)
    return pid


def _load_holdings(db, portfolio_id: str) -> int:
    with open(CSV_PATH, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    log.info("CSV: %d holdings to load", len(rows))

    existing = {r["ticker"] for r in (db.table("portfolio_holdings")
                .select("ticker").eq("portfolio_id", portfolio_id)
                .execute().data or [])}
    to_insert = [{"portfolio_id": portfolio_id, "ticker": r["ticker"],
                  "shares": float(r["shares"]), "cost_basis": float(r["cost_basis"])}
                 for r in rows if r["ticker"] not in existing]
    if not to_insert:
        log.info("All %d holdings already loaded -- nothing to insert", len(rows))
        return 0
    db.table("portfolio_holdings").insert(to_insert).execute()
    log.info("Inserted %d new holdings (skipped %d already present)",
             len(to_insert), len(rows) - len(to_insert))
    return len(to_insert)


def run() -> None:
    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    user_id = _ensure_demo_user(db)
    portfolio_id = _ensure_portfolio(db, user_id)
    _load_holdings(db, portfolio_id)

    holdings = (db.table("portfolio_holdings").select("ticker,shares,cost_basis")
                .eq("portfolio_id", portfolio_id).execute().data or [])
    print(f"\nPortfolio: {PORTFOLIO_NAME}  ({portfolio_id})")
    print(f"  user_id: {user_id}")
    print(f"  holdings: {len(holdings)}")
    for h in holdings[:5]:
        print(f"    {h['ticker']:<8}{h['shares']:>8.0f} shares @ "
              f"cost ${h['cost_basis']:.2f}")
    if len(holdings) > 5:
        print(f"    ... and {len(holdings)-5} more")
    print()


if __name__ == "__main__":
    run()
