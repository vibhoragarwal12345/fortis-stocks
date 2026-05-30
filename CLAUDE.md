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
| `reports` | user_id → auth.users, report_type, report_date, content_html/markdown | owner only |

**RLS notes:**
- Shared tables (news, snapshots, filings, social, rankings): authenticated users can SELECT; pipeline writes via `service_role` key which bypasses RLS.
- Email + portfolios were removed in the lean rewrite (migration 044). Login-only platform; no user-held data.

## Multibagger Discovery Engine (parallel to the daily pipeline)

**Architecturally separate** from the daily conviction grader. Lives in `pipeline/multibagger/`. Hunts for early-stage, under-discovered, long-horizon **asymmetric** bets — names that score *terribly* on cross-source confirmation (that's why they're still cheap). Different philosophy, different scoring, different cadence, much stronger risk framing.

**Stages**
1. `universe.py` (monthly) — NASDAQ screener API + Finnhub IPO calendar → ~4000 US-listed names in $50M–$10B. Cached to `pipeline/multibagger/data/mb_universe.csv`. Council decision: iShares CSVs were the first choice but BlackRock gates direct CSV access server-side; NASDAQ screener gives broader coverage including micro-caps below the R2000 floor.
2. `screener.py` (monthly) — scores each ticker on the 6 multibagger DNA traits (small_base, durable_growth, improving_unit_econ, large_tam_proxy, aligned_owners, under_discovered). Persists to `multibagger_candidates`. Names passing 5+ advance, 4 are watch. Disk cache on Finnhub responses (14-day TTL).
3. `scorer.py` (monthly) — weighted composite (growth 30 / reinvestment 25 / unit-econ 20 / alignment 15 / discovery 10) with **hard caps**: cash runway < 12mo & unprofitable → cap 50, debt/equity > 2 → cap 40, deceleration → cap 50, accounting flags → exclude. Top 30 advance to deep research.
4. `deep_research.py` (monthly) — Groq closed-context thesis with strict [DATA REF: key] discipline. Sections: business_model, the_10x_path, moat_assessment, founder_assessment, what_has_to_go_right, **what_kills_it** (as rigorous as the 10x path), key_metrics_to_track, risk_rating, conviction_tier. `factcheck_agent` runs over every thesis. Forbidden phrases ("to the moon", "guaranteed", "next Nvidia"…) cause discard.
5. `watchlist_manager.py` — adds tier_1/tier_2 (and tier_3 ≥ 60) to `emerging_watchlist`. Weekly price/drawdown updates. Names crossing $10B `graduate` into the daily pipeline universe. Broken theses get `archived`.
6. `quarterly_review.py` — re-screens every active watchlist row, classifies as `thesis_intact` / `thesis_weakening` / `thesis_broken` with reasoning.
7. `tracker.py` — `multibagger_outcomes` keeps the long-tail return record for every name ever added. Reports honest stats: % hitting 2x/5x/10x, % archived as broken, average current return, median time-to-double.
8. `reports.py` — monthly *Emerging Opportunities* + quarterly *Thesis Review* persisted to `public.reports` with their own `report_type`. Both prepend/append the relentless risk banner.

**Dashboard surface.** `/dashboard/emerging` lists the watchlist by conviction tier with the 10x path / what-kills-it / key metrics / return / status. A prominent risk banner at the top: "Emerging candidates are speculative, long-horizon, high-risk positions. Most will not become multibaggers." A footer link on the daily dashboard points users here, but the two surfaces are deliberately separate.

**Scheduling.** `.github/workflows/multibagger_weekly.yml` (Sun 14:00 UTC), `multibagger_monthly.yml` (1st of month, 15:00 UTC, full screen + research + report), `multibagger_quarterly.yml` (5th of Jan/Apr/Jul/Oct, thesis review). Monthly is the heavyweight (~30–60 min depending on universe size).

**Tables** (migrations 042 + 043). Tenant-scoped, RLS via the standard `is_tenant_member(tenant_id)` pattern, default tenant_id = Fortis.
- `multibagger_candidates` — screener+scorer output per `(ticker, screen_date)`.
- `multibagger_theses` — append-only generated theses with factcheck score.
- `emerging_watchlist` — curated persistent watchlist with status / return / drawdown / current price.
- `multibagger_outcomes` — append-on-upsert long-tail return record incl. 2x/5x/10x milestone dates.

**Hard rules in the engine**
- Never frame engine output as high-confidence prediction. It is structured speculation.
- The "what_kills_it" section is treated as a first-class deliverable, not an asterisk.
- The tracker reports failures honestly. Broken theses do not vanish; they are surfaced.
- No hype language anywhere — the deep_research LLM is told to discard "to the moon", "next Nvidia", "guaranteed", etc.
- Migrations were originally specified as 041/042; 041 was already multi-tenancy, so we used 042 and 043.

## Multi-tenancy (Step 7.6 — MVP)

Migration `041_multi_tenancy.sql` introduces tenant-scoped data isolation. The pilot tenant is **The Fortis Agency** (uuid `00000000-0000-4000-8000-000000000001`).

**Governance tables**
- `tenants` — name, slug, `primary_color`, `logo_url`, `email_from_name`, `access_status` (`active`/`suspended`/`archived`), `access_granted_until` (NULL = indefinite), `feature_flags` (jsonb), `notes`.
- `tenant_members` — `(tenant_id, user_id, role)` with `role` in (`admin`,`member`).
- `tenant_invites` — URL `token`, target `email`, `role`, `expires_at` (default 30 days). Anonymous lookup via the `lookup_invite(text)` SECURITY DEFINER function.
- `platform_admins` — `(user_id)`. Reach `/admin`. Bootstrapped to `vibhora030@gmail.com`.

**Tenant-scoped data tables (RLS on)** — every one carries a `tenant_id` with a DEFAULT pointing at the Fortis tenant so older insert paths (report composer, cron route) keep working:
- reports, dashboard_events, user_action_summary (dashboard_events + user_action_summary keep an owner check: `user_id = auth.uid() AND is_tenant_member(tenant_id)`)
- portfolios + email tables were dropped in migration 044 alongside the lean rewrite.

**Access control — NO BILLING.** Tier gating is replaced by `tenant.access_status` + `tenant.feature_flags`. There is no Stripe, no pricing, no subscription tier, no checkout, no trial-expiration logic. Billing is deliberately deferred until the product is validated. `src/lib/permissions.ts` (`checkAccess`, `canAccessFeature`) is the single source of truth.

**Signup is invite-only.** `/signup` requires `?token=…`; the token is validated via `lookup_invite()`, the email field is locked to the invite address, and on success the new user is linked to the tenant via `tenant_members` and the invite is marked accepted. Bare `/signup` and invalid tokens render an "access is invite-only" card.

**Root `/` is a login gateway, not a marketing/pricing page.** Serious dark-green + ivory. Product name, "Sign in" button, and a single "Access is currently invite-only" line. No pricing or feature grid.

**White-label theming.** `src/lib/theme.ts` resolves `{name, logoUrl, primaryColor, emailFromName}` from the active tenant; the dashboard layout applies it as CSS vars + header logo. Tenants self-customize at `/dashboard/settings/branding` (admin role only) — display name, primary color, From name, logo URL or file upload to the `tenant-branding` Supabase Storage bucket (auto-created on first upload).

**Admin panel.** `/admin` (platform-admin only): tenant list, create tenant + admin invite, per-tenant page to edit `feature_flags`, `access_status`, `access_granted_until`, `notes`, and manage members + invites. To assign yourself: `INSERT INTO platform_admins (user_id) VALUES ('<uuid>')` (the migration does this for `vibhora030@gmail.com` automatically when that user exists).

**Onboarding a new tenant** (no code changes required):
1. Sign in to `/admin`.
2. Create tenant → enter name + admin email. An invite is generated.
3. Open the new tenant's page; copy the `/signup?token=…` link from the invites table and send it to the admin (or any additional invitees).
4. The invitee signs up at that URL with a chosen password; they land in their branded dashboard with full access.

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
- **Form 4 transaction codes** — insider sentiment is computed ONLY from *directional* SEC Form 4 codes: **P** (open-market purchase), **S** (open-market sale that is not a 10b5-1 scheduled trade), and **V** (voluntary non-10b5-1 transaction). All other codes are mechanical and excluded — A (grants), F (tax withholding), G (gifts), M/X (derivative exercises), etc. `smart_money_intel.py` filters its in-memory parse; `form4_transactions` (migration 037) persists every transaction with an `is_directional_signal` flag so `anomaly_detector` / `catalyst_agent` filter the same way. Populate it with `python pipeline/agents/backfill_form4.py`; until then `insider_cluster` falls back to a filing count with direction "unknown". Picks generated after this fix carry `pick_outcomes.signal_quality_after_form4_fix = true` for backtest integrity.
- **10b5-1 detection** — regex-based on Form 4 footnote text; a 10b5-1 code-S sale is scheduled, not discretionary, so it is excluded from directional signals. See `pipeline/test_10b5_1.py` for the spot test (also reports the transaction-code breakdown).
