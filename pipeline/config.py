"""
Pipeline configuration — loads secrets from the repo root.

Resolution order (last one wins):
  1. .env          — base secrets file
  2. .env.local    — local overrides (Next.js convention; present during dev)

This means you can use a single .env.local for both the web app and the
pipeline without maintaining a separate .env file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root is one level above /pipeline
ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent

load_dotenv(ROOT_DIR / ".env")                        # base (if present)
load_dotenv(ROOT_DIR / ".env.local", override=True)   # local overrides win

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
SUPABASE_SERVICE_KEY: str = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# ── External APIs ─────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
FINNHUB_API_KEY: str = os.environ.get("FINNHUB_API_KEY", "")
RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")

# ── Paths ─────────────────────────────────────────────────────────────────────
TICKER_UNIVERSE_PATH: Path = PIPELINE_DIR / "data" / "tickers.csv"
