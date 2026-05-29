# Step 7.4 — GitHub Actions cron pipeline

Six workflows live in `.github/workflows/` and together run the 20-agent
system end-to-end on a recurring schedule without anyone touching it.

## Schedule overview

All times are **UTC** because GitHub Actions does not honour daylight saving.
Each slot is tied to **EST (UTC-5)**; during EDT (UTC-4) the wall-clock time
shifts one hour later. None of the wall-clock targets below need to be exact —
the briefs are aligned to the market open / midday / close *band*, not a
specific minute.

| Workflow                       | Cron (UTC)         | EST wall-clock    | EDT wall-clock    |
| ------------------------------ | ------------------ | ----------------- | ----------------- |
| `premarket.yml`                | `0 9 * * 1-5`      | 04:00 Mon–Fri ET  | 05:00 Mon–Fri ET  |
| `midday.yml`                   | `30 16 * * 1-5`    | 11:30 Mon–Fri ET  | 12:30 Mon–Fri ET  |
| `close.yml`                    | `30 21 * * 1-5`    | 16:30 Mon–Fri ET  | 17:30 Mon–Fri ET  |
| `data_quality.yml`             | `0 12 * * 1-5`     | 07:00 Mon–Fri ET  | 08:00 Mon–Fri ET  |
| `outcome_tracker.yml`          | `0 1 * * 2-6`      | 20:00 Mon–Fri ET  | 21:00 Mon–Fri ET  |
| `weekly_rollup.yml`            | `0 4 * * 1`        | 23:00 Sun ET      | 00:00 Mon ET      |

The three primary workflows (`premarket`, `midday`, `close`) share an
identical 6-stage shape and reuse the composite action in
`.github/actions/setup-pipeline/` for checkout + Python + `pip install`.

## Pipeline stages

| Stage                          | Modules                                                                                              | Why this order |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- | -------------- |
| **1 — Signal Generation**      | `market_data`, `news_harvester`, `sec_watcher`, `crowd_intelligence`, `macro_agent`, `options_flow`  | Cross-source raw signals. |
| **2 — Signal Validation**      | `processors.sentiment_scorer`, `anomaly_detector`, `pattern_matcher`, `cross_validator`              | Each needs Stage 1 features as input. |
| **3 — Thesis Formation**       | `ranking_engine`, `catalyst_agent`, `smart_money_intel`, `debate_synthesizer`, `critic_agent`        | Ranking feeds debate/critic; smart_money_intel snapshot today is consumed by tomorrow's catalyst/critic. |
| **4 — Quant Models**           | `quant.run_desk` (MC + VaR/CVaR), `quant.run_garch`, `quant.run_factor_dcf`, `quant.run_black_litterman` | BL invokes `conviction_grader` v1 internally as its first step. |
| **5 — Risk & Conviction**      | `peer_definition`, `peer_industry_analysis`, `factor_overlay`, `portfolio_fit`, `risk_flagger`, `position_manager` | Peer / factor / portfolio overlays applied to graded picks. |
| **6 — Compose & Deliver**      | `report_composer`, `outcome_tracker record`, POST `/api/cron/send-reports`                            | Persists the brief, locks in entry prices for forward tracking, kicks off email send. |

If a stage's `job` fails, downstream jobs short-circuit and `notify_failure`
runs a Slack post (only if `SLACK_WEBHOOK_URL` is set).

## Required GitHub Actions secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret                       | Source / notes                                                              |
| ---------------------------- | --------------------------------------------------------------------------- |
| `SUPABASE_URL`               | Project URL. The workflow maps this to `NEXT_PUBLIC_SUPABASE_URL` because that is the env-var name `pipeline/config.py` reads. |
| `SUPABASE_SERVICE_ROLE_KEY`  | Supabase service-role key.                                                  |
| `FINNHUB_API_KEY`            | Market data + filings.                                                      |
| `FMP_API_KEY`                | Financial Modeling Prep — analyst peers / financials.                       |
| `API_NINJAS_KEY`             | Earnings-call transcripts.                                                  |
| `FRED_API_KEY`               | St. Louis Fed macro data (used by `macro_agent`).                           |
| `TIINGO_API_KEY`             | Historical fundamentals, prices, news.                                      |
| `GROQ_API_KEY`               | Groq LLM inference (debate, critic, MC explanations).                       |
| `GEMINI_API_KEY`             | Gemini LLM inference (composer / synthesis).                                |
| `RESEND_API_KEY`             | Email delivery — currently consumed by the Next.js cron route, not the GH Actions runners. Add it to Vercel env, not here, unless you decide to send from CI. |
| `CRON_SECRET`                | Random 32-char string protecting `/api/cron/send-reports`. Must match the value in Vercel env. |
| `APP_URL`                    | Public origin of the deployed app, e.g. `https://app.fortis.example.com`.   |

Optional:

| Secret                | Purpose                                                                |
| --------------------- | ---------------------------------------------------------------------- |
| `SLACK_WEBHOOK_URL`   | Where `notify_failure` posts. Omit to disable Slack alerts.            |

## Testing a workflow before the next cron tick

1. Push the branch with all six workflows to GitHub.
2. Open the **Actions** tab.
3. Pick a workflow (e.g. *Pre-Market Pipeline*) → **Run workflow** → main → **Run workflow**.
4. Watch the per-stage logs in real time.
5. After it finishes, verify:
   - The brief shows up at `${APP_URL}/dashboard` (Step 7.2 page).
   - `email_delivery_log` has rows for each confirmed user.
   - The expected email arrives in your inbox (check spam first time).

## Failure modes & recovery

- A single stage failure stops downstream stages. Re-run the workflow from the
  Actions UI; the agents are idempotent against today's run_date and the
  `email_delivery_log` (report_id, user_id) check prevents duplicate sends.
- The `notify_failure` job runs *iff* any of the six stages reports failure;
  it is gated by `if: failure()` and a presence check on the Slack webhook.
- `pip install` is cached on `pipeline/requirements.txt`'s hash, so a warm
  cache hit makes setup ~30 s instead of ~3 min per job.

## Adapting to your TZ later

If you ever want EST-aligned timing year-round, switch each `cron` to two
schedules (one for EST months, one for EDT) and gate them with a
`workflow_dispatch` step that exits early outside the active range — or move
the orchestration to a self-hosted runner with `TZ=America/New_York` and
`apscheduler`.
