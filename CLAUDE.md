# Fortis Stock Intelligence — CLAUDE.md

## Project Purpose
Stock intelligence platform for **The Fortis Agency** — a login-only research surface for licensed advisors. We scan the full liquid US market twice each weekday and surface the highest-scoring opportunities with deep analysis.

## Architecture (post-lean-rewrite, May 2026)

**One sentence:** twice each weekday, run a two-speed funnel over ~3,300 liquid US common stocks; the dashboard reads the latest scan.

```
pipeline/data/build_full_universe.py        weekly
  → pipeline/data/full_universe.csv          ~3,300 tickers ($1M ADV floor)

pipeline/scan/run_scan.py                    2x/weekday (pre-market + midday)
  Layer 1   layer1_fast_scan   pure math on EVERY ticker      ~3-4 min
  Layer 2   layer2_rank        composite score, top 30         ~10 s
  Layer 3   catalyst, smart_money_intel, debate, critic       ~20 min (top 30)
  Layer 4   conviction_grader + factcheck                      ~5 s

  → market_scans         row per scan; dashboard reads LATEST status='complete'
  → scan_results         per-ticker Layer-1 metrics + composite_score (ALL ~3,300)
  → ranked_focus_list    top 30 with deep analysis + A/B/C grade
                         (Layer 2 stamps run_type='midday' so the existing
                          deep agents pick them up unmodified; scan_id is
                          the real lineage key)
```

**Cadence & cost.** The slow part is Layer 3 — `critic_agent` runs ~30s/ticker, so deep analysis is capped at the top 30 (not 80) to fit the workflow timeout and the GitHub Actions free 2,000-min/month budget. Automated cadence is **3×/weekday** (pre-market 12:00, midday 16:30, post-close 22:00 UTC) at ~25-30 min/run ≈ ~1,900-2,000 min/month — close to the free ceiling, so heavy multibagger runs + manual refreshes may require a paid Actions plan.

**Shared cached scan + manual refresh.** The scan result is **global and cached** — one latest scan serves all users; the dashboard always shows it instantly on load. `scan_state` (migration 046, a singleton row) is the source of truth: `current_status` (idle/running/failed), `latest_scan_id`, `latest_scan_completed_at`, `running_since`. The pipeline (`run_scan`) writes it on start/success/failure, so **cron and manual triggers update the same state**. The dashboard refresh control (`src/components/scan-refresh.tsx`) derives from `GET /api/scan/status`; `POST /api/scan/trigger` (authenticated) gates on two rules — a scan already `running` (shared, do nothing) or a cached scan younger than **2h** (`too_soon`) — then fires the *same* `market_scan.yml` via `workflow_dispatch` (needs `GH_DISPATCH_TOKEN` with Actions:write). A scan never runs inside a web request. A `running` state older than 70 min is treated as a crashed run so a timed-out scan can't lock the button forever.

**No email. No portfolios. No positions.** All gone in migration 044 (lean rewrite). The product is login-only research; we hold no user-held data.

**Dashboard:** `/dashboard` shows the latest scan's top 80 with a "Last updated · 15-min delayed" honesty banner. Click any ticker for `/dashboard/research/[ticker]`. `/dashboard/focus-list` shows the graded shortlist by grade. `/dashboard/track-record` reports honest forward returns on past picks (uses `pick_outcomes`, not portfolios).

## Tech Stack

### Web App (`/src`)
- **Framework**: Next.js 16 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS + shadcn/ui v4 (neutral palette, Base UI primitives)
- **Database / Auth**: Supabase (PostgreSQL + Row Level Security)
- **Package Manager**: npm

### Data Pipeline (`/pipeline`)
- **Language**: Python 3.12
- **Data sources**: yfinance (prices), Finnhub (fundamentals), SEC EDGAR (filings), feedparser (news)
- **AI / ML**: Groq (LLM inference), Gemini (LLM inference), HuggingFace Transformers (embeddings)
- **Virtual env**: `pipeline/venv/` (gitignored)
- **Cadence**: every 2 hours during market hours (`.github/workflows/market_scan.yml`); universe refresh weekly (`universe_weekly.yml`).

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
    ├── .python-version         # 3.12
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
1. `universe.py` (weekly) — NASDAQ screener API + Finnhub IPO calendar → ~4000 US-listed names in $50M–$10B. Cached to `pipeline/multibagger/data/mb_universe.csv`. Council decision: iShares CSVs were the first choice but BlackRock gates direct CSV access server-side; NASDAQ screener gives broader coverage including micro-caps below the R2000 floor.
2. `screener.py` (weekly) — scores each ticker on the 6 multibagger DNA traits (small_base, durable_growth, improving_unit_econ, large_tam_proxy, aligned_owners, under_discovered). Persists to `multibagger_candidates`. Names passing 5+ advance, 4 are watch. Disk cache on Finnhub responses (14-day TTL).
3. `scorer.py` (weekly) — weighted composite (growth 30 / reinvestment 25 / unit-econ 20 / alignment 15 / discovery 10) with **hard caps**: cash runway < 12mo & unprofitable → cap 50, debt/equity > 2 → cap 40, deceleration → cap 50, accounting flags → exclude. Top 35 advance to deep research.
4. `deep_research.py` (weekly) — Groq closed-context thesis with strict [DATA REF: key] discipline. Sections: business_model, the_10x_path, moat_assessment, founder_assessment, what_has_to_go_right, **what_kills_it** (as rigorous as the 10x path), key_metrics_to_track, risk_rating, conviction_tier. `factcheck_agent` runs over every thesis. Forbidden phrases ("to the moon", "guaranteed", "next Nvidia"…) cause discard.
5. `watchlist_manager.py` — adds tier_1/tier_2 (and tier_3 ≥ 60) to `emerging_watchlist`. Weekly price/drawdown updates. Names crossing $10B `graduate` into the daily pipeline universe. Broken theses get `archived`.
6. `tracker.py` — `multibagger_outcomes` keeps the long-tail return record for every name ever added. Reports honest stats: % hitting 2x/5x/10x, % archived as broken, average current return, median time-to-double.
7. `reports.py` — the weekly *Emerging Opportunities* report persisted to `public.reports` (`report_type='weekly_emerging'`), prepended/appended with the relentless risk banner. (Quarterly *Thesis Review* removed June 2026.)

**Dashboard surface.** `/dashboard/emerging` lists the watchlist by conviction tier with the 10x path / what-kills-it / key metrics / return / status. A prominent risk banner at the top: "Emerging candidates are speculative, long-horizon, high-risk positions. Most will not become multibaggers." A footer link on the daily dashboard points users here, but the two surfaces are deliberately separate.

**Scheduling.** `.github/workflows/multibagger_weekly.yml` (Sun 14:00 UTC — watchlist price/drawdown refresh + tracker) and `multibagger_discovery.yml` (Sat 15:00 UTC — **weekly** full screen + research + report; the heavyweight, ~30–60 min). Cadence changed monthly→weekly and the quarterly thesis-review workflow was removed (June 2026).

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

**Signup is open with instant access** (invite gate + email confirmation removed June 2026 for the friends-and-family launch). `/signup` creates the account pre-confirmed via `auth.admin.createUser({ email_confirm: true })` (no confirmation email), links it to the Fortis tenant as `member`, signs the user in, and redirects straight to `/dashboard`. Admin-panel invites (`tenant_invites` + `lookup_invite()`) still exist for onboarding other tenants but are no longer required to sign up.

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
| `GROQ_API_KEY` | Groq LLM inference (gateway provider) |
| `GEMINI_API_KEY` | Google Gemini LLM inference (gateway provider) |
| `CEREBRAS_API_KEY` | Cerebras Cloud LLM inference (gateway provider; optional) |
| `NVIDIA_API_KEY` | NVIDIA NIM LLM inference (gateway provider; optional) |
| `FINNHUB_API_KEY` | Finnhub market data |
| `API_NINJAS_KEY` | API Ninjas (earnings transcripts — note: free tier no longer includes the transcripts endpoint; kept here in case the plan is upgraded) |
| `DATABASE_URL` | Direct Postgres URL (session pooler) for `pipeline/apply_migration.py` |

## LLM gateway (`pipeline/llm.py`)

Every agent generates text through one shared `complete(prompt, system, temperature, max_tokens, json_mode)` call instead of rolling its own provider logic. It runs a **quota-aware waterfall**: `cerebras → groq → nvidia → gemini`. On a 429 / quota error a provider is disabled for the rest of the process and the next one takes over; a provider with no API key is skipped. All providers serve the same `llama-3.3-70b` except Gemini, so output stays reproducible regardless of who answered.

**Why:** a single deep scan can exhaust one free tier's daily token budget (this took the critic agent down in June 2026). Fanning the same workload across several free providers multiplies the effective daily budget and removes the single point of failure. Add a provider in one place (`_providers()`). For production reliability, a paid tier (e.g. Groq Dev) is still recommended as the primary; the free chain is then insurance. Refactored agents: catalyst, smart_money_intel, debate_synthesizer, critic_agent (daily scan) + multibagger deep_research. (`conviction_grader` is rule-based — no LLM. `crowd_intelligence` keeps its own fallback.)

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
