# Fortis Stock Intelligence — CLAUDE.md

## Project Purpose
Stock intelligence SaaS for **The Fortis Agency**, a financial advisory firm. The platform provides institutional-grade stock research, screening, and analytics tools for financial advisors and their clients.

## Tech Stack

### Web App (`/src`)
- **Framework**: Next.js 16 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS + shadcn/ui v4 (neutral palette, Base UI primitives)
- **Database / Auth**: Supabase (PostgreSQL + Row Level Security)
- **Package Manager**: npm

### Data Pipeline (`/pipeline`)
- **Language**: Python 3.11
- **Data sources**: yfinance (prices), Finnhub (fundamentals), SEC EDGAR (filings), feedparser (news)
- **AI / ML**: Groq (LLM inference), Gemini (LLM inference), HuggingFace Transformers (embeddings)
- **Email**: Resend
- **Virtual env**: `pipeline/venv/` (gitignored)

## Folder Structure
```
fortis-stocks/
├── src/                        # Next.js web app
│   ├── app/
│   │   ├── (auth)/             # Auth route group — login, signup, password reset
│   │   ├── (dashboard)/        # Protected dashboard route group
│   │   └── api/                # API route handlers
│   ├── components/
│   │   ├── ui/                 # shadcn/ui primitives (auto-generated, do not edit manually)
│   │   └── ...                 # Feature-level components
│   ├── lib/
│   │   ├── supabase/           # Supabase client helpers (server.ts, client.ts, middleware.ts)
│   │   └── utils.ts            # Shared utilities
│   └── types/                  # Shared TypeScript type definitions
└── pipeline/                   # Python data pipeline
    ├── config.py               # Loads .env, exports all API key constants
    ├── requirements.txt
    ├── .python-version         # 3.11
    ├── data/
    │   ├── tickers.csv         # Ticker universe
    │   └── sp1500.py           # Populates tickers.csv from Wikipedia
    ├── ingestors/              # One module per data source (prices, filings, news…)
    ├── processors/             # Transform & enrich raw data (sentiment, embeddings…)
    └── loaders/                # Write processed data to Supabase
```

## Conventions
- **Server Components by default.** Only add `"use client"` at the top of a file when you need interactivity, browser APIs, or React hooks (`useState`, `useEffect`, etc.).
- **Route groups** (`(auth)`, `(dashboard)`) each have their own `layout.tsx` for shared UI (e.g., sidebar, nav).
- **Supabase clients**: use SSR-aware helpers from `@/lib/supabase/`:
  - `createServerClient()` — for Server Components, Server Actions, and Route Handlers
  - `createBrowserClient()` — for Client Components only
- **Environment variables**: all secrets live in `.env.local` (never committed). See `.env.local.example` for required keys.
- **Components**: shadcn/ui primitives go in `src/components/ui/`. Feature components go directly in `src/components/`.
- **Imports**: use the `@/` alias (maps to `src/`).

## Database Tables

Migration: `supabase/migrations/001_initial_schema.sql`

| Table | Key columns | RLS |
|-------|-------------|-----|
| `news_items` | ticker, headline, url (unique), published_at, sentiment_score | authenticated SELECT |
| `market_snapshots` | ticker, snapshot_time, price, OHLCV, gap_pct, relative_volume | authenticated SELECT |
| `sec_filings` | ticker, cik, form_type, filing_date, filing_url (unique) | authenticated SELECT |
| `social_mentions` | ticker, source, mention_count, snapshot_date — unique (ticker, source, date) | authenticated SELECT |
| `ranked_focus_list` | run_date, run_type, ticker, rank, composite_score, thesis, signals (jsonb) | authenticated SELECT |
| `reports` | user_id → auth.users, report_type, report_date, content_html/markdown, delivered_email | owner only |
| `portfolios` | user_id → auth.users, name | owner only |
| `portfolio_holdings` | portfolio_id → portfolios, ticker, shares, cost_basis | owner only (via portfolio join) |

**RLS notes:**
- Shared tables (news, snapshots, filings, social, rankings): authenticated users can SELECT; pipeline writes via `service_role` key which bypasses RLS.
- User-owned tables (reports, portfolios, holdings): full CRUD for owner only.

## Environment Variables

See `.env.local.example` for all required variables.

**Web app** — secrets live in `.env.local` (Next.js, gitignored):

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/public key (safe for browser) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (server-side only) |

**Pipeline** — secrets live in `.env` at repo root (gitignored), loaded by `pipeline/config.py`:

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_SUPABASE_URL` | Same as above |
| `SUPABASE_SERVICE_ROLE_KEY` | Same as above |
| `GROQ_API_KEY` | Groq LLM inference |
| `GEMINI_API_KEY` | Google Gemini LLM inference |
| `FINNHUB_API_KEY` | Finnhub market data |
| `RESEND_API_KEY` | Resend email delivery |
| `API_NINJAS_KEY` | API Ninjas (earnings transcripts — note: free tier no longer includes the transcripts endpoint; kept here in case the plan is upgraded) |
| `DATABASE_URL` | Direct Postgres URL (session pooler) for `pipeline/apply_migration.py` |

## Data integrity principles

- **LLM transcript processing.** When an LLM is involved in transcript processing, it can ONLY identify structural metadata (timestamps, speaker boundaries). The actual transcript text must always be a verbatim concatenation of ASR output. The LLM never returns transcript content.

## Known limitations

- **Earnings transcripts** — `pipeline/agents/sec_transcript_fetcher.py` orchestrates a five-tier fallback chain (no recurring API costs):
  1. **SEC 8-K Item 2.02 exhibit** — transcript filed as an 8-K exhibit.
  2. **SEC 8-K Item 7.01** — transcript filed separately under Reg FD.
  3. **Company IR RSS feed** (`agents/ir_rss_fetcher.py`) — a transcript linked from the IR RSS feed (verified feeds seeded in `pipeline/data/ir_rss_feeds.csv`).
  4. **Press release fallback** — fetches the actual EX-99.1 body from SEC.
  5. **Audio transcription** (`agents/audio_transcriber.py`) — *conditional*: attempted only when the press release is thin (`minimal_press_release` / `unavailable`). Locates the publicly-webcast audio replay, transcribes locally with **whisper** (`agents/whisper_transcribe.py` auto-detects whisper.cpp or `openai-whisper`); a Groq pass adds speaker labels without altering words. Honours robots.txt; install with `bash pipeline/quant/install_whisper.sh`.
  **Transcript reality (2026):** most S&P 1500 mega-caps file only the earnings *press release* with SEC (typically 1500–9000 words) and keep the full call on their IR sites. A press release is not a failure — it carries the financials, guidance and prepared commentary; it lacks only live Q&A. So `validate_transcript_quality` grades on a **six-tier rubric** that drives `earnings_strength_baseline` in `smart_money_intel.py`: `full_call_with_qa`→100, `full_call_prepared_only`→85, `substantive_press_release` (>3000 words)→75, `standard_press_release` (1000–3000)→60, `minimal_press_release` (<1000)→40, `unavailable`→0. When the analysis rests on a press release the intel note discloses it verbatim (`"Analysis based on earnings press release filed via 8-K Item 2.02. Full transcript not publicly available -- Q&A pushback analysis not included."`) — honest disclosure, not a degradation flag. Transcripts cached on disk (30-day TTL) + `earnings_transcripts`; audio results also in `audio_transcripts` (migration 036). Validate the audio path with `python pipeline/test_audio_transcription.py`.
- **Form 4 transaction codes** — insider sentiment is computed ONLY from *directional* SEC Form 4 codes: **P** (open-market purchase), **S** (open-market sale that is not a 10b5-1 scheduled trade), and **V** (voluntary non-10b5-1 transaction). All other codes are mechanical and excluded — A (grants), F (tax withholding), G (gifts), M/X (derivative exercises), etc. `smart_money_intel.py` filters its in-memory parse; `form4_transactions` (migration 037) persists every transaction with an `is_directional_signal` flag so `anomaly_detector` / `risk_flagger` / `catalyst_agent` filter the same way. Populate it with `python pipeline/agents/backfill_form4.py`; until then `insider_cluster` falls back to a filing count with direction "unknown". Picks generated after this fix carry `pick_outcomes.signal_quality_after_form4_fix = true` for backtest integrity.
- **10b5-1 detection** — regex-based on Form 4 footnote text; a 10b5-1 code-S sale is scheduled, not discretionary, so it is excluded from directional signals. See `pipeline/test_10b5_1.py` for the spot test (also reports the transaction-code breakdown).
