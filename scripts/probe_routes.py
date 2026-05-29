"""
Signs in as vibhora030@gmail.com and hits every protected route, capturing
HTTP status, byte size, and any visible error markers in the HTML.

Uses the same `sb-{ref}-auth-token` cookie format @supabase/ssr writes so
the Next server's createServerClient picks up the session.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

SUPABASE_URL = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
ANON_KEY     = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
PROJECT_REF  = urlparse(SUPABASE_URL).hostname.split(".")[0]

EMAIL    = "vibhora030@gmail.com"
PASSWORD = "abcd4321"
APP_BASE = "http://localhost:3000"


def sign_in() -> dict:
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        json={"email": EMAIL, "password": PASSWORD},
        headers={
            "apikey": ANON_KEY,
            "Content-Type": "application/json",
        },
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def cookie_value(session: dict) -> str:
    # @supabase/ssr v0.5+ writes the session as `base64-` + b64(json) when
    # under the (small) length limit, otherwise chunks. Our payload is small
    # so the single-cookie form works.
    payload = {
        "access_token":  session["access_token"],
        "token_type":    "bearer",
        "expires_in":    session.get("expires_in", 3600),
        "expires_at":    session.get("expires_at", int(time.time()) + 3600),
        "refresh_token": session["refresh_token"],
        "user":          session.get("user"),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    b64 = base64.b64encode(raw).decode().rstrip("=")
    return "base64-" + b64


def probe(routes: list[str], cookies: dict[str, str]) -> None:
    print(f"{'status':<6} {'size':>8}  errs  route")
    for path in routes:
        try:
            r = requests.get(APP_BASE + path, cookies=cookies, allow_redirects=False, timeout=120)
        except requests.RequestException as exc:
            print(f"{'EXC':<6} {'-':>8}  -    {path}  ({exc})")
            continue
        body = r.text or ""
        size = len(body)
        # Next renders server errors with a "next-error-h1" element + Application error markers.
        markers = []
        for pat, label in [
            (r"Application error",      "app-error"),
            (r"Server Error",           "500"),
            (r"This page could not be found", "404"),
            (r"Unhandled Runtime Error", "runtime"),
            (r"500: INTERNAL_SERVER_ERROR", "500"),
            (r"digest:\s*['\"]\w+",     "digest"),
            (r"Error:\s+[A-Z]\w+Error", "JSerr"),
        ]:
            if re.search(pat, body):
                markers.append(label)
        loc = r.headers.get("location", "")
        suffix = f" -> {loc}" if r.status_code in (301, 302, 307, 308) else ""
        print(f"{r.status_code:<6} {size:>8}  {','.join(markers) or '-':<5}  {path}{suffix}")


def main() -> int:
    session = sign_in()
    print(f"signed in as {session.get('user', {}).get('email')}")
    cookie_name = f"sb-{PROJECT_REF}-auth-token"
    cookies = {cookie_name: cookie_value(session)}
    print(f"cookie: {cookie_name} (len={len(cookies[cookie_name])})")
    print()

    routes = [
        "/",
        "/login",
        "/signup",
        "/forgot-password",
        "/check-email",
        "/dashboard",
        "/dashboard/positions",
        "/dashboard/track-record",
        "/dashboard/emerging",
        "/dashboard/settings/branding",
        "/admin",
        "/account/update-password",
    ]
    probe(routes, cookies)
    return 0


if __name__ == "__main__":
    sys.exit(main())
