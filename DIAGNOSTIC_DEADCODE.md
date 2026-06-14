# DIAGNOSTIC_DEADCODE.md — Fortis loose-ends / unused-artifact inventory

_Phase 1 (inventory only — nothing deleted). Generated 2026-06-14 on branch `pre-launch-diagnostic`._

**"Referenced" = imported/called in `src/` OR `pipeline/` (Python) OR `.github/` workflows.** Each item lists where I grepped. Per your rules: code removals happen in Phase 5 (small batches + typecheck/lint/build after each); **DB-table drops only via migration after confirming zero references**; **SUSPECTED items need your explicit sign-off**. Nothing below is deleted yet.

---

## ✅ CONFIRMED-UNUSED (proven unreferenced)

### Dependencies
1. **`resend`** (`package.json:26`) — email delivery. grep `resend` (case-insensitive, whole repo excl `node_modules`): only `package.json`/`package-lock.json`, `.env.local.example`, historical migrations `039`/`044`, `docs/CRON.md`, and 2 pipeline CSVs (company-name substring false-positives). **Zero imports in `src`.** Email was removed (migration `044_drop_email_and_portfolios`).
2. **`juice`** (`package.json:19`) — email HTML inliner. grep `juice`: same package/CSV hits only. **Zero imports.**

### Dead exports (TypeScript)
3. **`getActiveTenant`** — `src/lib/tenant.ts:42`. grep `getActiveTenant\b` across `src`: definition only (the live one is `getActiveTenantMember`, a different function). No callers.
4. **`canAccessFeature`** + **`featureLimit`** — `src/lib/permissions.ts:46,58`. grep across `src`: definitions only, no callers. Only `checkAccess` is used (`dashboard/layout.tsx:7,68`).

### Hooks
5. **`useTrackEvent`** — `src/hooks/useTrackEvent.ts`. grep `useTrackEvent` across repo: its own file + `docs/FEEDBACK.md` only. **No component imports it.** (Anchors the dormant client-tracking chain — see SUSPECTED-A.)

### Orphaned env vars (`.env.local.example`)
6. Website reads only: `NEXT_PUBLIC_SITE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (grep `process.env.` across `src`). The following are documented but **unused by the site, and their referenced API routes do not exist**:
   - `GH_DISPATCH_TOKEN`, `GH_REPO` — for `/api/scan/trigger` (route absent; manual-scan trigger removed). grep: only `.env.local.example`, `CLAUDE.md`, `docs/CRON.md`.
   - `CRON_SECRET` — for `/api/cron/send-reports` (absent).
   - `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`, `EMAIL_FROM_ADDRESS`, `EMAIL_PHYSICAL_ADDRESS` — email removed; `/api/webhooks/resend` absent.
   - `NEXT_PUBLIC_APP_URL` — stale name; code reads `NEXT_PUBLIC_SITE_URL` (BUG MEDIUM-6).
   - ⚠️ **Caveat:** some names (RESEND/EMAIL/GH/data keys) may still exist as **GitHub Actions secrets** for the pipeline/CI — `.github` workflows did not match these names, but verify before deleting any *actual* secret. Cleaning the **example file** is safe regardless.

### Orphan DB tables (created, never referenced by current code) — *drop only via a Phase-5 migration, with sign-off*
7. **`portfolio_optimization`** (migration `020`) — grep `portfolio_optimization`: only migration `020`. The Black-Litterman code that wrote it is gone (`pipeline/quant/black_litterman.py` deleted).
   **`portfolio_fit`** (migration `023`) — grep: migrations `023`/`026` + `docs/CRON.md` only; `pipeline/agents/portfolio_fit.py` is gone.
   - 🚫 **Do NOT drop `factor_exposure`** (migration `022`): it is **LIVE** — written by `pipeline/agents/factor_overlay.py:328`, read by `feedback_aggregator.py:100` and `peer_definition.py`.

### Stale references to removed Black-Litterman (text/comments only — safe cleanup)
8. `docs/CRON.md` (`quant.run_black_litterman`, `portfolio_fit` step), `supabase/migrations/020` comment, `pipeline/agents/conviction_grader.py:6` (**docstring only** — confirmed not an import). The module `pipeline/quant/black_litterman.py` is already deleted.

---

## ⚠️ SUSPECTED-UNUSED (may be load-bearing or a deliberate dormant capability — **do NOT delete without your sign-off**)

- **A. Client event-tracking chain** — `src/lib/track-client.ts` (`trackEventClient`) + `src/app/api/track/route.ts`. Reachable **only** via the unused `useTrackEvent` hook (CONFIRMED #5); if the hook goes, these orphan. **But** it's a documented capability (`docs/FEEDBACK.md`) and `/api/track` is a public HTTP surface — removing it drops client-side tracking entirely. Also ~12 of 13 `EventType` values in `lib/track.ts` are never emitted (only `dashboard_today_opened` is). **Decision:** keep dormant, or remove the whole client chain + prune `EventType`. The **server** path (`lib/track.ts` → `dashboard_events`, read by the pipeline's `feedback_aggregator`) stays regardless.
- **B. Route `/check-email`** — `src/app/(auth)/check-email/page.tsx`. No inbound links (signup auto-confirms; forgot-password shows an inline message). Reachable by direct URL only (probe 200). Likely vestigial from the old email-confirm flow; may be wanted for a future flow.
- **C. `thesis` field + UI** (also BUG HIGH-3) — `ranked_focus_list.thesis` column, research-page Thesis section, focus-list `cardBody` thesis branch, `Pick.thesis` type. Dead as shipped, **but the fix may be to WIRE it, not delete it.** Product decision.
- **D. Vestigial tenant feature flags** — `multiple_portfolios`, `max_portfolios`, `max_holdings`, `custom_email` in `src/lib/tenant.ts` (`FeatureFlags`) + written in `src/app/admin/actions.ts:32,35`. Portfolios + custom-email features are removed; these are written but never read for gating (and `canAccessFeature`/`featureLimit` that would read them are themselves unused — #4). Low-risk to prune, but they are part of the `tenants.feature_flags` jsonb contract.
- **E. `scan_test.log`** (repo root) — transient local scan log, accidentally tracked; not source. → gitignore + `git rm --cached` (Phase 5). Currently dirties the working tree.

---

## 🟢 INTENTIONAL ORPHANS — KEEP (listed for completeness)
- **`src/app/design-system/page.tsx`** — themed component preview; deliberately has no inbound links (internal reference, recreated per the design rebrand). Probe 200. **Keep.**
- **Root `BUGS.md`** — superseded by `DIAGNOSTIC_BUGS.md`; recommend delete/archive but it's docs, your call (BUG LOW-3).

## ⛔ Out of scope / NOT dead
- **`pipeline/india/`, `pipeline/india_portfolio/`** scratch files (`smoke_jugaad2.py`, `liquidation3.py`, `inspect_*.py`, etc.) — these were **uncommitted WIP** captured in the Phase-0 checkpoint; a separate India sub-project, likely **active work**, not website dead code. Excluded unless you say otherwise.
- **`factor_exposure`** table — LIVE (see #7).

---

## Components / libs verified USED (no action — for your confidence)
All of `src/components/ui/*` are referenced: `aurora-field` (landing/auth/legal/admin layouts + update-password), `glow-card`/`parallax`/`section` (landing), `skeleton` (every `loading.tsx`), `count-up`/`word-reveal`/`ticker-tape`/`page-transition`/`cursor-glow`/`reveal`/`badge`/`button`/`card`/`form`/`input`/`label`/`table` (various). `shortlist-table` → dashboard. `use-media-query` → count-up/cursor-glow/reveal. `lib/track.ts` (server), `lib/commodities.ts`, `lib/landing-data.ts`, `lib/site.ts`, `lib/theme.ts`, `lib/tenant.ts` (`getActiveTenantMember`/`isPlatformAdmin`), `lib/permissions.ts` (`checkAccess`), all `lib/supabase/*` — used.
