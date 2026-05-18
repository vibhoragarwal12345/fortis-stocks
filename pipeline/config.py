"""
Pipeline configuration — loads secrets from .env at the repo root.

Create a .env file at the repo root (one level above this directory) with the
keys listed in .env.local.example. The .env file is gitignored.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo root is one level above /pipeline
ROOT_DIR = Path(__file__).resolve().parent.parent
PIPELINE_DIR = Path(__file__).resolve().parent

load_dotenv(ROOT_DIR / ".env")

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
