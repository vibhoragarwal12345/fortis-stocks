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
# Additional LLM providers in the gateway waterfall (pipeline/llm.py). Optional;
# a missing key just drops that provider from the chain.
CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")  # Cerebras Cloud — fast, generous free tier
NVIDIA_API_KEY: str = os.environ.get("NVIDIA_API_KEY", "")      # NVIDIA NIM (build.nvidia.com)
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")  # OpenRouter — free-model overflow router (last resort)
FINNHUB_API_KEY: str = os.environ.get("FINNHUB_API_KEY", "")
FRED_API_KEY: str = os.environ.get("FRED_API_KEY", "")  # St. Louis Fed — economic data
FMP_API_KEY: str  = os.environ.get("FMP_API_KEY", "")   # Financial Modeling Prep — analyst peers, financials
TIINGO_API_KEY: str = os.environ.get("TIINGO_API_KEY", "")  # Tiingo — historical fundamentals, prices, news
API_NINJAS_KEY: str = os.environ.get("API_NINJAS_KEY", "")  # API Ninjas — earnings call transcripts (free tier)

# ── Paths ─────────────────────────────────────────────────────────────────────
TICKER_UNIVERSE_PATH: Path = PIPELINE_DIR / "data" / "tickers.csv"
