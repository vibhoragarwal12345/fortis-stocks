"""
Probe whether the Supabase project will honour our reset-password redirectTo.

Supabase's generateLink uses the same allowlist as resetPasswordForEmail. If
our requested redirectTo is on the allowlist the returned action_link's
`redirect_to` query param matches what we asked for. If it isn't, Supabase
silently substitutes the project's default Site URL (no error).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SERVICE = os.environ["SUPABASE_SERVICE_ROLE_KEY"]


def probe(redirect_to: str) -> str:
    """Return the redirect_to that Supabase actually embedded in the link."""
    svc = create_client(URL, SERVICE)
    suffix = uuid.uuid4().hex[:8]
    email = f"probe-{suffix}@isolation.test"
    user = svc.auth.admin.create_user(
        {"email": email, "password": "probe-password-1", "email_confirm": True}
    ).user
    try:
        link = svc.auth.admin.generate_link(
            {"type": "recovery", "email": email, "options": {"redirect_to": redirect_to}}
        )
        action_link = link.properties.action_link if link.properties else None
        if not action_link:
            return "(no action_link returned)"
        qs = parse_qs(urlparse(action_link).query)
        actual = qs.get("redirect_to", ["(missing)"])[0]
        return actual
    finally:
        try:
            svc.auth.admin.delete_user(user.id)
        except Exception:
            pass


def main() -> int:
    candidates = [
        "http://localhost:3000/auth/confirm?next=/account/update-password",
        "https://fortis-stocks.vercel.app/auth/confirm?next=/account/update-password",
        "http://localhost:3000/auth/confirm",
        "https://fortis-stocks.vercel.app/auth/confirm",
    ]
    bad: list[str] = []
    for req in candidates:
        actual = probe(req)
        ok = actual == req
        print(f"  {'OK  ' if ok else 'FAIL'}  requested={req}")
        if not ok:
            print(f"        actual={actual}")
            bad.append(req)
    print()
    if not bad:
        print("All redirect URLs are on the allowlist.")
        return 0
    print("MISSING from Supabase Dashboard -> Authentication -> URL Configuration -> Redirect URLs:")
    for b in bad:
        print(f"  {b}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
