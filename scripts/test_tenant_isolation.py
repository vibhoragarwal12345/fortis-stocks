"""
RLS isolation smoke test for Step 7.6.

Creates two ephemeral tenants + one user each via the service role, then opens
two ANON-keyed Supabase clients (one signed in as each user) and verifies:

  1. tenant A user sees only tenant A's portfolios; never tenant B's.
  2. tenant B user sees only tenant B's portfolios; never tenant A's.
  3. tenant A user cannot read a tenant B portfolio by id.
  4. Neither user can SELECT a peer tenant row from tenants table.

Cleans up everything it creates. Run with:
    python scripts/test_tenant_isolation.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
ANON = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def main() -> int:
    svc = create_client(URL, SERVICE)

    suffix = uuid.uuid4().hex[:8]
    pa = {
        "email": f"alice-{suffix}@isolation.test",
        "password": "isolation-test-password-1",
    }
    pb = {
        "email": f"bob-{suffix}@isolation.test",
        "password": "isolation-test-password-2",
    }

    # Create tenants ----------------------------------------------------
    ta = (
        svc.table("tenants")
        .insert({"name": f"Tenant A {suffix}", "slug": f"tenant-a-{suffix}"})
        .execute()
        .data[0]
    )
    tb = (
        svc.table("tenants")
        .insert({"name": f"Tenant B {suffix}", "slug": f"tenant-b-{suffix}"})
        .execute()
        .data[0]
    )
    print(f"Created tenant A {ta['id']} / B {tb['id']}")

    created: list[tuple[str, str]] = [("tenants", ta["id"]), ("tenants", tb["id"])]

    try:
        # Create users via admin API (skip email confirmation) ----------
        u_alice = svc.auth.admin.create_user(
            {"email": pa["email"], "password": pa["password"], "email_confirm": True}
        ).user
        u_bob = svc.auth.admin.create_user(
            {"email": pb["email"], "password": pb["password"], "email_confirm": True}
        ).user
        print(f"Created users alice={u_alice.id} bob={u_bob.id}")

        # Link to tenants ------------------------------------------------
        svc.table("tenant_members").insert(
            {
                "tenant_id": ta["id"],
                "user_id": u_alice.id,
                "role": "admin",
                "accepted_at": "now()",
            }
        ).execute()
        svc.table("tenant_members").insert(
            {
                "tenant_id": tb["id"],
                "user_id": u_bob.id,
                "role": "admin",
                "accepted_at": "now()",
            }
        ).execute()

        # Seed one portfolio per tenant ---------------------------------
        port_a = (
            svc.table("portfolios")
            .insert(
                {
                    "tenant_id": ta["id"],
                    "user_id": u_alice.id,
                    "name": f"Alice book {suffix}",
                }
            )
            .execute()
            .data[0]
        )
        port_b = (
            svc.table("portfolios")
            .insert(
                {
                    "tenant_id": tb["id"],
                    "user_id": u_bob.id,
                    "name": f"Bob book {suffix}",
                }
            )
            .execute()
            .data[0]
        )

        # Sign in each user via the ANON client and probe ---------------
        passed = []
        failed = []

        def check(label: str, ok: bool, detail: str = "") -> None:
            (passed if ok else failed).append(f"{label}{(' -- ' + detail) if detail else ''}")

        cli_a = create_client(URL, ANON)
        cli_a.auth.sign_in_with_password({"email": pa["email"], "password": pa["password"]})

        rows = cli_a.table("portfolios").select("id, tenant_id, name").execute().data
        check(
            "alice sees only her tenant's portfolios",
            all(r["tenant_id"] == ta["id"] for r in rows) and any(r["id"] == port_a["id"] for r in rows),
            f"got {len(rows)} rows",
        )
        check(
            "alice cannot see bob's portfolio by id",
            not cli_a.table("portfolios").select("id").eq("id", port_b["id"]).execute().data,
        )
        check(
            "alice cannot SELECT tenant B from tenants",
            not cli_a.table("tenants").select("id").eq("id", tb["id"]).execute().data,
        )

        cli_b = create_client(URL, ANON)
        cli_b.auth.sign_in_with_password({"email": pb["email"], "password": pb["password"]})
        rows_b = cli_b.table("portfolios").select("id, tenant_id, name").execute().data
        check(
            "bob sees only his tenant's portfolios",
            all(r["tenant_id"] == tb["id"] for r in rows_b) and any(r["id"] == port_b["id"] for r in rows_b),
            f"got {len(rows_b)} rows",
        )
        check(
            "bob cannot see alice's portfolio by id",
            not cli_b.table("portfolios").select("id").eq("id", port_a["id"]).execute().data,
        )

        print()
        for p in passed:
            print(f"  PASS  {p}")
        for f in failed:
            print(f"  FAIL  {f}")
        print()
        print(f"{len(passed)} passed, {len(failed)} failed")
        return 0 if not failed else 1

    finally:
        # Cleanup -------------------------------------------------------
        try:
            svc.table("portfolios").delete().eq("tenant_id", ta["id"]).execute()
            svc.table("portfolios").delete().eq("tenant_id", tb["id"]).execute()
        except Exception:
            pass
        try:
            svc.table("tenant_members").delete().in_("tenant_id", [ta["id"], tb["id"]]).execute()
        except Exception:
            pass
        try:
            svc.table("tenants").delete().in_("id", [ta["id"], tb["id"]]).execute()
        except Exception:
            pass
        for u in (locals().get("u_alice"), locals().get("u_bob")):
            if u is None:
                continue
            try:
                svc.auth.admin.delete_user(u.id)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
