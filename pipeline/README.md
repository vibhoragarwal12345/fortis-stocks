# Fortis Pipeline

Python data pipeline that ingests market data, filings, and news into the Supabase database.

## Setup

```bash
cd pipeline
python3.11 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file at the **repo root** (not inside `/pipeline`) with all required keys — see `.env.local.example` for the full list.

## Structure

```
pipeline/
├── config.py           # Loads .env, exports all constants
├── requirements.txt
├── .python-version     # 3.11
├── data/
│   ├── tickers.csv     # Ticker universe (populated by sp1500.py)
│   └── sp1500.py       # Scrapes S&P 1500 constituents from Wikipedia
├── ingestors/          # (coming) one module per data source
│   ├── prices.py       # yfinance — OHLCV daily/intraday
│   ├── fundamentals.py # yfinance / Finnhub — financials, ratios
│   ├── filings.py      # sec-edgar-downloader — 10-K, 10-Q, 8-K
│   └── news.py         # feedparser + aiohttp — RSS / news APIs
├── processors/         # (coming) transform & enrich raw data
│   ├── sentiment.py    # Groq / Gemini LLM sentiment scoring
│   └── embeddings.py   # transformers — text embeddings for search
└── loaders/            # (coming) write processed data to Supabase
    └── supabase.py
```

## Scripts

| Script | What it does |
|--------|-------------|
| `python data/sp1500.py` | Downloads S&P 1500 tickers from Wikipedia → `data/tickers.csv` |

## Configuration (`config.py`)

All environment variables are read from the repo root `.env`. The module exports:

| Constant | Source variable | Notes |
|----------|----------------|-------|
| `SUPABASE_URL` | `NEXT_PUBLIC_SUPABASE_URL` | |
| `SUPABASE_SERVICE_KEY` | `SUPABASE_SERVICE_ROLE_KEY` | Bypasses RLS — server-side only |
| `GROQ_API_KEY` | `GROQ_API_KEY` | LLM inference |
| `GEMINI_API_KEY` | `GEMINI_API_KEY` | LLM inference |
| `FINNHUB_API_KEY` | `FINNHUB_API_KEY` | Market data |
| `TICKER_UNIVERSE_PATH` | — | `Path` to `data/tickers.csv` |

## Conventions

- All ingestors accept a list of tickers and operate idempotently (safe to re-run).
- Async I/O (`aiohttp`) for network-bound ingestors; `asyncio.run()` at the entry point.
- Heavy ML work (embeddings, transformers) runs in a separate processor step after raw data is loaded.
- Never import from `src/` — the pipeline is a standalone Python project.
