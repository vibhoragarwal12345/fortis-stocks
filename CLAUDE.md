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
_To be documented as tables are added._

| Table | Description |
|-------|-------------|
| _(none yet)_ | |

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
