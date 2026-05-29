# Step 7.5 — Feedback collector & weight learning

The system gets better the more it's used. Every meaningful dashboard
interaction lands in `public.dashboard_events`; a nightly aggregator rolls
it up into `public.user_action_summary`; once we have 90+ days of
`pick_outcomes` data, a separate retrainer proposes (but never applies)
new ranking weights.

## Event taxonomy

The canonical list lives in `src/lib/track.ts` as the `EventType` union and
in `src/lib/track-client.ts` as a re-export. The DB column is plain `text`
so new event types ship with code, not migrations.

| Event                       | Where it fires                                                          |
| --------------------------- | ----------------------------------------------------------------------- |
| `dashboard_today_opened`    | `/dashboard` server render                                              |
| `positions_opened`          | `/dashboard/positions` server render                                    |
| `track_record_opened`       | `/dashboard/track-record` server render                                 |
| `report_opened`             | `/dashboard/reports/[date]/[type]` — lands with Page 2 of Step 7.2      |
| `pick_clicked`              | Focus-list item click-through                                           |
| `thesis_expanded`           | Bull/bear case section expand                                           |
| `quant_expanded`            | Quant models section expand                                             |
| `smart_money_expanded`      | Smart-money section expand                                              |
| `peer_expanded`             | Peer comparison expand                                                  |
| `ticker_searched`           | Ad-hoc ticker search                                                    |
| `position_acknowledged`     | "Mark reviewed" on a position alert                                     |
| `pick_marked_useful`        | Thumbs-up next to a pick                                                |
| `pick_marked_unhelpful`     | Thumbs-down next to a pick                                              |
| `sub_added` / `unsub_added` | Watchlist add/remove (lands with the watchlist feature)                 |

Three are wired *today*. The rest become live as their pages/components
ship; the tracking infrastructure is already in place.

## Plumbing

**Server components** call `trackEvent("report_opened", { report_id })` —
import from `@/lib/track`. The helper looks up the cookie session, inserts
with `user_id = auth.uid()`, and swallows every error so a tracking
failure never breaks a render.

**Client components** call `trackEventClient(...)` from
`@/lib/track-client`, which POSTs to `/api/track`. For "fire once on
mount" use `useTrackEvent` from `@/hooks/useTrackEvent` — it guards
against React StrictMode double-mounts.

**`/api/track` (`POST`)** validates the body with `zod`, ensures the
caller has a Supabase session, inserts into `dashboard_events`, and
returns **204 No Content**. The dedup `UNIQUE` constraint on
`(user_id, event_type, occurred_at_bucket, event_data_hash)` collapses
duplicate logs inside a 5-second window — Postgres returns error code
`23505`, which the route quietly treats as success.

## Aggregation cadence

`feedback_aggregator.yml` runs every weekday morning at **02:00 UTC**
(21:00 ET in EST, an hour after `outcome_tracker.yml` finishes). It does
three things:

1. **`daily_rollup(yesterday)`** — UPSERTs one row per active user into
   `user_action_summary`. Joins `pick_clicked` events to `ranked_focus_list`
   to compute `preferred_signal_types` (share of clicked picks' weighted
   contributions per category) and to `factor_exposure` for
   `preferred_sectors`.
2. **`identify_signal_value(90)`** — for each signal category, the
   fraction of strongly-dominant picks that were clicked and their mean
   forward alpha. Written as JSON to `pipeline/proposals/`.
3. **`correlate_signals_to_outcomes(30)`** — Spearman correlation between
   each category's raw score and the pick's 5-day / 20-day / 60-day
   alpha. Also written to `pipeline/proposals/`.

The workflow uploads `pipeline/proposals/` as an artifact so weekly
review is one Actions-tab click away.

## Engagement score (0-100)

```
reports_opened          × 10  capped at 30
picks_clicked           ×  5  capped at 25
any_section_expansion   ×  5  capped at 15
explicit_feedback       × 10  capped at 20
ticker_searches         ×  5  capped at 10
```

Lives in `pipeline/agents/feedback_aggregator.py` (`SCORE_WEIGHTS`) so
the formula can evolve without a schema migration.

## Weight retraining (gated)

`pipeline/agents/weight_retrainer.py` proposes new ranking-engine weights
but **never applies them**. Three hard gates:

1. **90+ days** between earliest and latest `pick_outcomes.recommended_date`
2. **100+** A-grade picks tracked
3. **t-statistic** of mean `alpha_20d` on A-grade picks > **1.5**

When all three pass, `propose_new_weights(run_type)`:

- loads every `pick_outcomes` row with `alpha_20d != null`, joined to
  `ranked_focus_list.signals` to recover the per-category scores;
- for each category, sweeps its weight through `{0.80, 0.85, …, 1.20}` of
  its current value, rescaling the rest so the sum stays 1.0;
- re-ranks the historical picks under each candidate vector and keeps
  the top-20 per (date, run_type);
- reports mean `alpha_20d` of the kept picks;
- combines each category's best tweak into a composite vector, re-normalises,
  and writes a markdown proposal to
  `pipeline/proposals/weight_change_<date>.md`.

The proposal contains:

- current vs proposed weights (with deltas + best multipliers)
- expected alpha lift on the top-20 reeval
- a ready-to-paste `WEIGHTS["<run_type>"] = […]` snippet for whoever
  approves it

**Approval workflow:** an engineer reviews the proposal, edits
`pipeline/agents/ranking_engine.py` by hand, runs the test pipeline once,
commits the change. No CLI auto-apply path exists, by design — weights
are the system's DNA.

## Why per-user personalisation is deferred

`user_action_summary` carries `preferred_sectors` and `preferred_signal_
types` from day one so the data accumulates. But generating a per-user
weight overlay needs **60+ days** of stable rows per user; below that
threshold the signal is noise and the overlay produces unstable results.
Once we cross the threshold, the existing rollup data is enough to
generate the overlay — no further schema changes needed.

## Files

```
supabase/migrations/040_feedback_collection.sql   schema
src/lib/track.ts                                  server helper + EventType union
src/lib/track-client.ts                           client wrapper
src/hooks/useTrackEvent.ts                        once-on-mount hook
src/app/api/track/route.ts                        POST endpoint
src/app/dashboard/*/page.tsx                      instrumented page renders
pipeline/agents/feedback_aggregator.py            daily rollup + diagnostics
pipeline/agents/weight_retrainer.py               gated proposal generator
.github/workflows/feedback_aggregator.yml         02:00 UTC cron
docs/FEEDBACK.md                                  this file
```
