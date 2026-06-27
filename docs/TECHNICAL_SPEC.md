# Fortis Platform — Technical Specification (Current-State Audit)

**Generated:** 2026-06-17 · **Method:** read of the actual repo + live Supabase
introspection (`information_schema`, row counts, max timestamps) + workflow/orchestrator
tracing. This documents the platform **as the code literally is today**, not as planned.

**Confidence markers used below:** ✅ verified by reading the file/querying the DB ·
⚠️ inferred from names/imports/orchestrator wiring (not line-by-line verified) ·
🟥 **honest discrepancy** between code and apparent intent.

---

## 0. How "active" was determined

A module is **ACTIVE** only if it is reachable from a scheduled entry point — i.e.
invoked by a `.github/workflows/*.yml` run step, or by an orchestrator that those
workflows call (`pipeline/scan/run_scan.py`, `pipeline/commodities/scan.py`,
`pipeline/run_track_record.py`, the multibagger workflow stages). Everything else is
present in the repo but **not on any current execution path**.

This was cross-checked empirically: the DB tables that "inactive" producer agents would
write are **stale** (see §3), confirming those agents do not run today.

---

## 1. Repository layout (source, excluding `pipeline/venv/`)

- `pipeline/scan/` — the daily stock funnel (Layers 1–4 + gate). ✅
- `pipeline/agents/` — 30+ agent modules; **only ~7 are on the active scan path** (§2/§3). ✅
- `pipeline/commodities/` — commodities engine (`registry`, `analyze`, `scan`, `sources/`, `analysis/`). ✅
- `pipeline/multibagger/` — emerging engine (`universe`, `screener`, `scorer`, `deep_research`, `watchlist_manager`, `tracker`, `reports`). ✅
- `pipeline/quant/` — `monte_carlo`, `garch`, `dcf`, `fama_french`, `factor_*`, `models_config`, `utils`. Mostly **not** on the active path (§3). ⚠️
- `pipeline/processors/`, `pipeline/data/` — universe builders + helpers.
- `pipeline/llm.py` — shared LLM provider gateway. ✅
- `pipeline/config.py` — env/secret loading. ✅
- `src/app/` — Next.js 16 App Router frontend (§5). ✅
- `supabase/migrations/` — 001–055 SQL migrations.
- `.github/workflows/` — 9 workflow files (§4). ✅

---

## 2. The active stock-scan funnel — `pipeline/scan/run_scan.py` ✅

Orchestrator `run_scan.py` runs these steps in order (verified, lines ~190–330). Layers
3–4 run against a **soft deadline** (`SCAN_SOFT_DEADLINE_SEC`, 3780 s in CI); once it is
hit, remaining steps are skipped and the scan finalizes.

| Step | File | Reads | Writes |
|---|---|---|---|
| **Layer 1 — fast scan** | `pipeline/scan/layer1_fast_scan.py` | `pipeline/data/full_universe.csv` + yfinance (1y daily) | `scan_results` (one row/ticker, `composite_score`=NULL). Pure math, no LLM. |
| **Layer 2 — rank** | `pipeline/scan/layer2_rank.py` | `scan_results` for the scan | `ranked_focus_list` (top-N=35) with a 0–100 composite (momentum/volume/breakout/proximity/RSI). Sets `scan_results.advanced`. |
| **Price bands** | `pipeline/scan/price_bands.py` | yfinance price history; `pipeline/quant/monte_carlo.py` | `ranked_focus_list.price_reference` (jsonb) — zero-drift Monte-Carlo cone. Deterministic, always runs. |
| **L3 — catalyst** | `pipeline/agents/catalyst_agent.py` | Finnhub (earnings/analyst, **live**); `sec_filings`,`anomaly_flags`,`news_items`,`options_signals`,`ticker_debates` (**stale**, §3) | `ranked_focus_list.catalyst_*`. Groq-written, deterministic template fallback. |
| **L3 — smart money** | `pipeline/agents/smart_money_intel.py` | `earnings_transcripts`, `form4_transactions`, short-interest, est. revisions | `smart_money_intel` (**fresh**, last write 2026-06-16). |
| **L3 — debate** | `pipeline/agents/debate_synthesizer.py` | yfinance technicals (**fresh**); `smart_money_intel`(fresh); `news_items`,`sec_filings`,`options_signals`,`pattern_matches`,`ticker_debates`,`validated_signals` (**stale**) | `ranked_focus_list.thesis/bull_case/bear_case/price_target_*/dossier_*`. |
| **L3 — critic** | `pipeline/agents/critic_agent.py` | the debate dossier + smart-money | `ranked_focus_list.critic_*` + lowers `dossier_quality_grade`. |
| **L4 — conviction grade** | `pipeline/agents/conviction_grader.py` | `ranked_focus_list.composite_score`; critic objection; **(quant bonuses from `monte_carlo_results`/risk/`factor_attribution`/`dcf_valuations`/`garch_forecasts` — stale, §3)** 🟥 | `ranked_focus_list.conviction_grade/conviction_score_adjusted`. |
| **Gate** | `pipeline/scan/dossier_gate.py` | `ranked_focus_list` | sets `dossier_complete` + syncs `scan_results.advanced` (§6). |

Finalize (`run_scan.py` ~line 380): promotes the scan to `scan_state.latest_scan_id`
**only if** `status=='complete'` AND `complete >= SCAN_MIN_DISPLAY_DOSSIERS` (=15); else the
last good board stays displayed. ✅

Shared support (active): `pipeline/agents/factcheck_agent.py` (imported by debate/critic),
`pipeline/agents/scan_target.py` (scan-id arg parsing), `pipeline/llm.py` (gateway).

---

## 3. Pipeline agent inventory — ACTIVE vs INACTIVE

### Active (on a scheduled path) ✅
- **Stock funnel:** `layer1_fast_scan`, `layer2_rank`, `price_bands`, `catalyst_agent`, `smart_money_intel`, `debate_synthesizer`, `critic_agent`, `conviction_grader`, `dossier_gate`, `factcheck_agent`, `scan_target`, `llm`.
- **Commodities:** `commodities/scan.py`, `analyze.py`, `registry.py`, `cross_link.py`, all `commodities/sources/*` (eia, cot, news, macro, prices, usda, metals_supply, common), all `commodities/analysis/*` (price_technicals, supply_demand, curve_structure, positioning, seasonality, macro_driver, narrative).
- **Emerging:** `multibagger/universe.py`, `screener.py`, `scorer.py`, `deep_research.py`, `watchlist_manager.py`, `tracker.py`, `reports.py`.
- **Maintenance/track-record:** `agents/outcome_tracker.py` + `agents/backtest_engine.py` (via `run_track_record.py`), `agents/feedback_aggregator.py`, `agents/weight_retrainer.py` (check-only), `data/build_full_universe.py`, `health_check.py`.
- **Quant on active path:** `quant/monte_carlo.py` (used by `price_bands` + commodities technicals). `quant/utils.py`, `quant/models_config.py` (support).

### Inactive / not on any current path — present but not run 🟥
Determined by: not invoked by any workflow/orchestrator **and** the tables they write are
stale. Empirical freshness (max timestamp, queried 2026-06-17):

| Producer agent (file) | Writes table | Latest row | Status |
|---|---|---|---|
| `agents/news_harvester.py` | `news_items` | **2026-05-18** | stale |
| `agents/sec_watcher.py` | `sec_filings` | **2026-05-18** | stale |
| `agents/options_flow.py` | `options_signals` | **2026-05-18** | stale |
| `agents/pattern_matcher.py` | `pattern_matches` | **2026-05-19** | stale |
| `agents/crowd_intelligence.py` | `ticker_debates` | **2026-05-19** | stale |
| `agents/cross_validator.py` | `validated_signals` | **2026-05-20** | stale |
| (vs `smart_money_intel`) | `smart_money_intel` | **2026-06-16** | **fresh ✅** |

Also present but **not on any active path** (⚠️ classified by orchestrator/workflow
tracing, not line-by-line): `agents/market_data.py`, `news_resolver.py`, `ranking_engine.py`,
`factor_overlay.py`, `peer_definition.py`, `industry_research.py`, `peer_industry_analysis.py`,
`anomaly_detector.py`, `sec_transcript_fetcher.py`, `ir_rss_fetcher.py`, `audio_transcriber.py`,
`whisper_transcribe.py`, `backfill_transcripts.py`, `backfill_form4.py`, `hallucination_check.py`,
`processors/sentiment_scorer.py`, `processors/fingerprint.py`, `data/sp1500.py`,
`data/build_ticker_master.py`, `data/build_historical_patterns.py`, and the standalone quant
runners `quant/garch.py`, `dcf.py`, `fama_french.py`, `run_garch.py`, `run_factor_dcf.py`,
`test_iv_computation.py`. (`pipeline/run_full_pipeline.py` and `run_resync.py` exist as
legacy orchestrators; **not** referenced by any current workflow.)

🟥 **Consequence for dossier quality:** `debate_synthesizer` and `catalyst_agent` still
*read* `news_items / sec_filings / options_signals / pattern_matches / ticker_debates /
validated_signals`, but these have not updated since ~May 18–20. So a current dossier's
"news flow / SEC filings / options positioning / historical analog / crowd consensus /
cross-validation" blocks are **stale or fall back to "No data."** The genuinely fresh
inputs to a dossier today are: **price/technicals (yfinance), catalyst (Finnhub
earnings/analyst), smart-money intel, the Monte-Carlo price band, and the composite
score** — plus the LLM-written thesis/bull/bear/critique synthesized from those.

🟥 **Conviction grade:** `conviction_grader.py` documents quant bonuses (Monte-Carlo
prob_up, VaR, Fama-French alpha, DCF upside, GARCH regime). Their input tables
(`monte_carlo_results` 30 rows, `dcf_valuations` 60, `garch_forecasts` 30,
`factor_exposure` 30) are low-volume and have no active producer, so those bonuses are
**likely inactive** — the grade is effectively composite-percentile + critic cap. ⚠️ (Not
verified line-by-line in the grader's null handling.)

---

## 4. Scheduled workflows — `.github/workflows/` ✅

**Client-facing scans → triggered by Supabase `pg_cron` → GitHub `repository_dispatch`**
(migrations 053/054/055; native `schedule:` removed for these). Times UTC / ET below.

| Workflow | Trigger | Schedule (UTC) | ET (EST / EDT) | Runs |
|---|---|---|---|---|
| `market_scan.yml` | pg_cron jobs 1,2 | `0 12` & `30 16` Mon–Fri | 7:00 & 11:30 AM / 8:00 & 12:30 | `run_scan.py` premarket + midday |
| `commodities_scan.yml` | pg_cron 5,4 | `5 12` Mon–Fri; `45 22` Mon | 7:05 AM daily; 5:45 PM Mon | `commodities.scan --mode tactical` / `full` |
| `multibagger_discovery.yml` | pg_cron 3 | `0 15` Mon | 10:00 AM Mon | universe→screener→scorer→deep_research→watchlist→reports |

**Maintenance / track-record → still on GitHub-native `schedule:`** (NOT pg_cron — 🟥 these
remain subject to GitHub's cron lag/drop):

| Workflow | cron (UTC) | Runs |
|---|---|---|
| `data_quality.yml` | `0 12 * * 1-5` | `pipeline.health_check` |
| `outcome_tracker.yml` | `0 1 * * 2-6` | `run_track_record.py daily` → `outcome_tracker.update` |
| `feedback_aggregator.yml` | `0 2 * * 2-6` | `feedback_aggregator` + `weight_retrainer check` |
| `weekly_rollup.yml` | `0 4 * * 1` | `run_track_record.py weekly` & `monthly` (→ `outcome_tracker.rollup`, `backtest_engine.report`) |
| `universe_weekly.yml` | `0 13 * * 0` | `data.build_full_universe` (rebuilds `full_universe.csv`) |
| `multibagger_weekly.yml` | `0 14 * * 0` | `watchlist_manager --update/--graduate`, `tracker --refresh/--stats` |

Live `pg_cron` jobs (verified in `cron.job`): 1 `fire_market_scan('premarket')`,
2 `fire_market_scan('intraday')`, 3 `fire_github_dispatch('run-multibagger-discovery')`,
4 `fire_github_dispatch('run-commodity-scan','{"mode":"full"}')`,
5 `fire_github_dispatch('run-commodity-scan','{"mode":"tactical"}')`. Token in Supabase
Vault (`github_pat_market_scan`).

---

## 5. Data sources — `pipeline/config.py` ✅

| Source | Env var | Status |
|---|---|---|
| Supabase (Postgres) | `NEXT_PUBLIC_SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | **Required & live** ✅ |
| yfinance (prices) | (no key) | **Live** — Layer 1, price_bands, debate technicals, commodities ✅ |
| Finnhub | `FINNHUB_API_KEY` | **Live** — catalyst (earnings/analyst), smart-money, multibagger screener ✅ |
| FRED | `FRED_API_KEY` | **Live** — commodities macro/metals ✅ |
| EIA | `EIA_API_KEY` | **Live** — commodities energy inventories ✅ |
| USDA NASS | `USDA_NASS_API_KEY` | **Live** — commodities ag supply/demand ✅ |
| CFTC COT | (public, no key) | **Live** — commodities positioning ✅ |
| **LLM gateway** | `GROQ_API_KEY`, `GROQ_API_KEY_2`, `CEREBRAS_API_KEY`, `CEREBRAS_API_KEY_2`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | **Live** (validated this week) ✅ |
| Tiingo | `TIINGO_API_KEY` | Configured (passed in `market_scan.yml` env); ⚠️ active usage unconfirmed — likely read by now-inactive agents. |
| FMP | `FMP_API_KEY` | Configured (GitHub secret exists); ⚠️ usage by inactive agents only — unconfirmed live use. |
| API Ninjas | `API_NINJAS_KEY` | Configured; ⚠️ transcript-related; usage unconfirmed. |

---

## 6. The funnel as implemented ✅

- **Universe:** ~3,300 liquid US tickers (`full_universe.csv`).
- **Shortlist size (top-N):** **35** — `market_scan.yml` passes `--top-n 35`; `DEFAULT_TOP_N=35` in `run_scan.py`. Layer 3 runs on these 35.
- **Displayed board:** **exactly 15** — `BOARD_SIZE=15` in `src/lib/board.ts`; Today (`dashboard/page.tsx`) and Focus list (`dashboard/focus-list/page.tsx`) both query the promoted scan's `dossier_complete` rows, `rank` asc, `.limit(15)`.
- **LLM failover chain** — `pipeline/llm.py` `_providers()`, ordered by `(tier, speed, bucketed-remaining-budget, random)`:
  - tier 0 (fast): **groq** & **groq_2** (`llama-3.3-70b-versatile`, 100k tok/day each); **cerebras** & **cerebras_2** (`gpt-oss-120b`, ~1M tok/day each); **nvidia** (`meta/llama-3.3-70b-instruct`, marked slow → tried after the others).
  - tier 1: **gemini** (`gemini-2.5-flash`, ~250 req/day).
  - tier 2 (overflow): **openrouter-gptoss** & **openrouter-llama** (`:free`, ~50 req/day).
  - Per-call retry on per-minute 429; a daily-cap 429 disables that provider for the run. Random tie-break shards the two cerebras/groq accounts to avoid 429 storms. Budget ledger = `llm_usage` (per UTC day).
- **Dossier completeness** — `pipeline/scan/dossier_gate.py` `_is_complete()`: a name is `dossier_complete=true` only if it has `catalyst_description, thesis, bull_case, bear_case, critic_objection_level, conviction_grade, price_reference` **and** `dossier_quality_grade ∈ {VERIFIED, PARTIALLY_VERIFIED}` (or legacy NULL) **and** `looks_complete(bull_case)` **and** `looks_complete(bear_case)`.
  - `looks_complete()` (`agents/factcheck_agent.py`): ≥400 chars, ends on sentence-terminal punctuation, and contains no leftover `[DATA REF` tag. Added 2026-06-17 to stop truncated/stub cases displaying.
  - The gate sets `scan_results.advanced` to match, hiding incomplete names on every surface.
- **Promotion floor:** `SCAN_MIN_DISPLAY_DOSSIERS=15` — a scan replaces the live board only with ≥15 complete dossiers.

---

## 7. Commodities engine — `pipeline/commodities/` ✅

- **Registry:** `registry.py` — **10 commodities** (wti, brent, natgas, gold, silver, copper, soybeans, corn, wheat, coffee). Iron ore was removed 2026-06-15.
- **Orchestration:** `scan.py` → `analyze.py` runs per-commodity layers (`price_technicals`, `supply_demand`, `curve_structure`, `positioning`, `seasonality`, `macro_driver`) and a dual-horizon LLM narrative (`analysis/narrative.py`): **tactical** (1-week Monte-Carlo band; `HORIZONS['tactical_1w']=5`) and **structural** (252-day band).
- **Two cadences (2026-06-17):** `--mode tactical` (daily pre-market) regenerates the short-term read + **carries the structural block forward** from the last full run (no structural LLM call); `--mode full` (weekly Monday) regenerates everything.
- **Sources:** FRED, EIA, USDA NASS, CFTC COT, yfinance futures, Google-News RSS — all live ✅. Metals supply/demand is honestly flagged thin (free-data limits).
- **Persistence:** one row per run to `commodity_scans` (jsonb `payload` per commodity + `cross_link`). Dashboard reads the latest. Cross-link feeds the stock macro context (`agents/macro_agent.py`). ⚠️ `macro_agent` activeness in the current scan not individually re-verified here.

---

## 8. Emerging (multibagger) engine — `pipeline/multibagger/` ✅

Weekly Monday discovery (`multibagger_discovery.yml`), stages:
1. `universe.py` — small/micro-cap universe via NASDAQ screener + Finnhub IPO calendar → `mb_universe.csv`.
2. `screener.py` — 6 DNA traits (small base, durable growth, improving unit econ, large TAM [LLM-flagged], aligned owners, under-discovered); ≥60 passes; 5+ traits advance → `multibagger_candidates`.
3. `scorer.py` — 0–100 `multibagger_score` (growth/reinvestment/unit-econ/alignment/discovery) with downward quality caps.
4. `deep_research.py` — closed-context LLM thesis (Groq) with `[DATA REF]` + factcheck → `multibagger_theses`.
5. `watchlist_manager.py` — `--add` qualifying (tier-1/2 always, tier-3 if score ≥60) → `emerging_watchlist`; `--update` prices/drawdowns; `--graduate` names >$10B.
6. `reports.py --weekly` → `reports`.

Weekly Sunday `multibagger_weekly.yml` refreshes prices/drawdowns + `tracker.py`.

**Frontend (`src/app/dashboard/emerging/page.tsx`):** shows the **top 12** active watchlist
names by conviction tier then score (`MAX_SHOWN=12`); the DB list is larger (~32). The
return-since-added / peak / max-drawdown metrics were **removed** from the card (2026-06-15).

---

## 9. Database schema — 47 base tables (live, `public`) ✅

**Stock scan (active):** `scan_state` (singleton; `latest_scan_id`), `market_scans` (run
ledger), `scan_results` (Layer-1 rows + `advanced`), `ranked_focus_list` (the dossier:
thesis/bull/bear/critic/conviction/price_reference/dossier_*), `smart_money_intel` (fresh),
`llm_usage` (budget ledger).

**Commodities (active):** `commodity_scans`.

**Emerging (active):** `multibagger_candidates`, `multibagger_theses`, `emerging_watchlist`,
`multibagger_outcomes`, `auto_added_watchlist`.

**Tenancy / app (active):** `tenants`, `tenant_members`, `tenant_invites`, `platform_admins`,
`reports`, `dashboard_events`, `user_action_summary`.

**Track record (active via track-record jobs):** `pick_outcomes`, `backtest_picks`,
`system_performance_rollup`, `agent_attribution`.

**Stale / inactive producers (data not refreshed since ~May, §3):** `news_items`,
`sec_filings`, `options_signals`, `pattern_matches`, `ticker_debates`, `validated_signals`,
`social_mentions`, `market_snapshots`, `ticker_sentiment_rollup`, `anomaly_flags`,
`macro_context`, `peer_sets`, `peer_industry_analysis`.

**Quant tables — present, low-volume, likely stale (§3):** `monte_carlo_results`,
`ticker_risk_metrics`, `garch_forecasts`, `dcf_valuations`, `factor_attribution`,
`factor_exposure`, `factor_concentration`.

**Transcripts (read by smart-money/multibagger when present):** `earnings_transcripts`,
`transcript_fetch_metrics`, `audio_transcripts`, `form4_transactions`.

⚠️ Per-table column-level purpose was not enumerated for all 47; classifications above are
by domain + producer mapping.

---

## 10. Frontend routes — `src/app/` (Next.js 16 App Router) ✅

**Public:** `/` (`page.tsx`), `/login` `/signup` `/forgot-password` (`(auth)/`),
`/privacy` `/terms` `/disclaimer` (`(legal)/`), `/account/update-password`,
`/auth/confirm` (route handler).

**Dashboard (auth-gated, `dashboard/layout.tsx`):** `/dashboard` (Today, the Sharp 15),
`/dashboard/focus-list`, `/dashboard/emerging`, `/dashboard/commodities`,
`/dashboard/commodities/[key]`, `/dashboard/research/[ticker]`,
`/dashboard/reports/[date]/[run_type]`, `/dashboard/account`,
`/dashboard/scan-history` + `/[id]` (**owner-only**, `isOwner` gate).

**Owner-only:** `/design-system` (gated 2026-06-16), `/admin`, `/admin/tenants/[id]`.

🟥 **No `/api/*` route handlers exist** except `auth/confirm/route.ts`. Workflow comments
reference an "`/api/scan` manual refresh," but **no such route is present in the code today.**

---

## 11. Summary of honest discrepancies (🟥)

1. **Stale enrichment layers.** The dossier template surfaces news/SEC/options/pattern/crowd/
   cross-validation context, but those tables stopped updating ~2026-05-18→20. Their producer
   agents are not on any active path. Fresh dossier substance = price/technicals + catalyst
   (Finnhub) + smart-money + Monte-Carlo band + composite score + the LLM synthesis.
2. **Conviction-grade quant bonuses likely inactive** — their input tables are stale/low-volume.
3. **Maintenance jobs still on GitHub-native cron** (not pg_cron): they can lag/drop, unlike the
   3 client-facing scans which now fire punctually via pg_cron.
4. **`/api/scan` referenced but absent.**
5. **Large amount of legacy code present but unused** (old orchestrators `run_full_pipeline.py`,
   `run_resync.py`; ~20 inactive agents; standalone quant runners). Not harmful, but it inflates
   the surface area and should not be read as "the live pipeline."
6. **Tiingo / FMP / API-Ninjas keys configured** but their only readers are inactive agents — so
   they are wired but not demonstrably in live use.

*End of audit.*
