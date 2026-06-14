# DIAGNOSTIC_BUGS.md — Fortis pre-launch bug inventory

_Phase 1 (diagnose only — nothing fixed). Generated 2026-06-14 on branch `pre-launch-diagnostic` (checkpoint `d5b2774`; `main` left pristine at `de429e2`)._

## Method & evidence

| Check | Result |
|---|---|
| `npm run build` (Next 16.2.6, Turbopack, production) | **exit 0** — compiled 3.5s, TypeScript passed, **0 warnings**, all 15 static pages prerendered, 27 routes built |
| `npx eslint src` | **exit 0** — 0 problems |
| TypeScript (run inside build) | **pass** |
| Live HTTP probe (`next start` :3100, unauthenticated) | 11/11 public routes **200**; `/account/update-password` **307**; 8/8 authed routes **307 → /login**; unknown route **404**. **No 5xx anywhere.** |
| DB ground-truth (latest *complete* scan `#36`, intraday 2026-06-12) | 25 picks — bull 25/25, bear 21/25, price targets **13/25**, thesis **0/25**, dossier_complete 18/25 |

**Headline:** Nothing crashes — no build error, no SSR/runtime 500, no dead nav link, no redirect loop; every route renders with graceful empty states. **No BLOCKERs found.** The substantive items are a product-policy *decision* (scan display), a financial-claim *labeling* gap (price levels), and a never-populated `thesis` field the UI still reads.

---

## Named priority 1 — Redirection / navigation audit → **CLEAN**

Every redirect, `<Link>`, `router.push`, nav item, and middleware path was mapped and tested (static + live 307/200/404 probe).

- **Middleware** (`src/proxy.ts` → `lib/supabase/middleware.ts`): session refresh only — no routing redirects. Route protection is per-layout.
- **Guards** → `/login` (or `/forgot-password`): `dashboard/layout.tsx:63`, `admin/layout.tsx:21`, `dashboard/account/page.tsx:15`, `account/update-password/page.tsx:21`. Verified live (307s).
- **Flows**: login → `/dashboard`; signup (auto-confirm) → `/dashboard`; signout (`dashboard/actions.ts:9`) → `/`; `updatePassword` → `/dashboard`; `requestPasswordReset` → `/auth/confirm?next=/account/update-password`; `auth/confirm` success → `next` (default `/dashboard`), failure → `/login?error=…`; admin create-tenant → `/admin/tenants/{id}`.
- **Nav / links**: dashboard nav (`/dashboard`, `/dashboard/focus-list`, `/dashboard/emerging`, `/dashboard/commodities`, `/dashboard/scan-history`), Account, ticker tape → research, focus-list cards → research, research back → focus-list, commodities cards → `[key]`, scan-history rows → `[id]` → research, reports viewer → dashboard, site-footer → privacy/terms/disclaimer, admin → tenants.

**Result:** every destination resolves to a real route. **No dead links, no 404 targets, no loops.** (The stale `BUGS.md` flagged `/dashboard/focus-list` and `/dashboard/research/[ticker]` as dead — they have since been built.) The only redirect-related issue is the open-redirect below (MEDIUM-1).

---

## Named priority 2 — Scan-output audit → **coverage / generation gap, NOT a render bug**

Per-ticker DB state for the latest complete scan (`#36`):

| field | populated | missing on |
|---|---|---|
| `bull_case` | 25/25 | — |
| `bear_case` | 21/25 | VECO, CAKE, WSM, SNDK |
| `price_target_upside`/`downside` | **13/25** | ~half the list |
| `thesis` | **0/25** | every pick |
| `dossier_complete=true` | 18/25 | — |

**The render layer is correct.** `research/[ticker]/page.tsx` and `focus-list/page.tsx` show each field when present and gracefully badge/withhold when absent ("Deep analysis pending" / "Dossier completing"). This is **not** "generated-but-hidden."

**Root causes (distinguished as you asked):**
1. **Partial bear cases + price targets = generation/coverage gap (not generated).** `debate_synthesizer` + `critic_agent` process the ranked list top-down until the LLM budget / `SCAN_SOFT_DEADLINE` is hit, so lower-ranked names get partial dossiers. Price targets are *also* not in `dossier_gate.py`'s required-field set, so a "complete" dossier can still lack them (e.g. AVAL, PHIN, MOV, CDNL: complete yet no targets).
2. **`thesis` 0/25 = broken data contract (regression).** `ranking_engine.py:277` initializes `"thesis": None` with the comment *"filled later by debate synthesizer"* — but nothing ever fills it (no write exists anywhere). See HIGH-3.

### ⚠️ DECISION REQUIRED before Phase 2 (mutually-exclusive policies)
- **Shipped behavior** (commit `de429e2` + `dossier_gate.py` + `focus-list` + `dashboard` + research page): the **whole 20–25 pick shortlist displays**; `dossier_complete` is a **label**; incomplete names show a "Dossier completing" badge and withhold prose.
- **Your Phase-2 directive**: the **opposite** — a name displays **only** if it has a COMPLETE dossier (full bull + bear + price reference levels); a shorter list is correct.

Enforcing your invariant **reverses a recent owner decision** and, on scan #36, drops the visible list from **25 → ~18–21** (requiring bull+bear+critic) or **→ ~13** (also requiring price levels). I will not touch this until you confirm which rule you want.

---

## BLOCKER
**None observed.** No build failure, no SSR/runtime 5xx, no dead navigation, no compile/type/lint error.

## HIGH

### HIGH-1 — Scan display policy must be decided (named priority 2)
- **Files:** `pipeline/scan/dossier_gate.py`, `src/app/dashboard/focus-list/page.tsx:152-170`, `src/app/dashboard/research/[ticker]/page.tsx:51-74`, `src/app/dashboard/page.tsx:96-107`, `src/app/dashboard/layout.tsx:26-32` (ticker tape filters `advanced=true`).
- **Cause:** product-direction reversal (see decision box above). **Action deferred to your call.**

### HIGH-2 — Price levels presented as "targets / forecasts," not statistical reference levels
- **File:** `src/app/dashboard/research/[ticker]/page.tsx:304-321` — heading "**Price targets · debate synthesis**", labels "Upside target" / "Downside target".
- **Symptom:** LLM-debate-derived numbers are shown with forecast language and no UNVERIFIED/reference framing. For a financial-advisory product this fails the Phase-4 hallucination bar ("price predictions labeled as statistical reference levels, NOT forecasts"). Commodities already do this correctly (zero-drift cones).
- **Action:** trace `price_target_*` to its source (`debate_synthesizer`), confirm it's reference-band-derived vs LLM-asserted, and relabel accordingly. (Belongs to Phase 4; surfaced here because it's a present-tense labeling defect.)

### HIGH-3 — `thesis` is never generated, yet the UI reads it everywhere
- **Files:** `pipeline/agents/ranking_engine.py:277` (`"thesis": None`, never filled); `src/app/dashboard/research/[ticker]/page.tsx:262-269` (Thesis section never renders); `src/app/dashboard/focus-list/page.tsx:106-108` (cardBody thesis branch is dead — always falls through to bull_case).
- **Cause:** broken/abandoned data contract. `debate_synthesizer` writes `bull_case`/`bear_case` but not `thesis`.
- **Action:** either wire the synthesizer to write `thesis`, or remove the field + UI references (see DEADCODE SUSPECTED-C). Currently a silent missing section.

## MEDIUM

### MEDIUM-1 — Open redirect in `/auth/confirm`
- **File:** `src/app/auth/confirm/route.ts:9,16` — `next` is read from the query string and used unsanitized in `NextResponse.redirect(new URL(next, origin))`. An absolute or `//host` value escapes the origin.
- **Risk:** gated behind a *valid* OTP (low exploitability) but still a latent open-redirect. Validate `next` is a local path (`startsWith("/")` and not `//`).

### MEDIUM-2 — `dossier_gate.py` docstring is stale (doc-vs-code drift)
- **File:** `pipeline/scan/dossier_gate.py:25-30` (docstring) vs `:98-110` (code). The docstring claims it sets `scan_results.advanced=false` to *hide* bare names and that "the focus list filters on `dossier_complete`." The code explicitly **no longer touches `advanced`**, and the focus list does **not** filter. Misleads the next maintainer and is directly relevant to HIGH-1.

### MEDIUM-3 — Stuck / orphaned scans linger
- **Evidence:** `market_scans #37` is `status='running'` permanently (the crashed local manual run that was writing `scan_test.log`); `#33` is `complete` with 0 picks.
- **File:** `src/app/dashboard/scan-history/page.tsx` shows them ("running", blank rows). No watchdog flips a stale `running` scan to `failed`.
- **Action:** add a reaper (or hide manual/stuck scans). Cosmetic but confusing pre-launch.

### MEDIUM-4 — No `global-error.tsx`
- `src/app/error.tsx` catches errors that escape route segments (verified present), but an error thrown in the **root layout itself** is not caught → Next's default error screen. Add `src/app/global-error.tsx` to satisfy the Phase-2 "no route ever crashes the whole page" goal.

### MEDIUM-5 — Report viewer uses `dangerouslySetInnerHTML`
- **File:** `src/app/dashboard/reports/[date]/[run_type]/page.tsx:122` — renders `reports.content_html` raw. Pipeline-generated today (low risk), but any LLM/3rd-party HTML reaching that column is an XSS vector. Sanitize or confirm provenance. (Viewer serves deprecated historical reports only.)

### MEDIUM-6 — `.env.local.example` documents the wrong site-URL var
- The example documents `NEXT_PUBLIC_APP_URL`, but the code reads **`NEXT_PUBLIC_SITE_URL`** (`src/lib/site.ts:4`, `src/app/(auth)/actions.ts:10`). Anyone configuring from the example sets the wrong var → `siteOrigin()` silently falls back to request headers (recovery / canonical links can be wrong behind a proxy). Fix the example. (Also a dead-env item — see DEADCODE #6.)

## LOW

- **LOW-1** — `lucide-react` pinned `^1.16.0` (very old major). All icons in use resolve; bump at a sprint boundary. (`package.json:20`)
- **LOW-2** — `next.config.ts` is empty: no security headers / CSP / image domains. Consider hardening before launch.
- **LOW-3** — Stale root `BUGS.md` (2026-05-29) references removed files (`dashboard/positions`, `dashboard/track-record`, `dashboard/settings/branding`, `src/lib/email.ts`). Misleading; superseded by this file. (Also DEADCODE.)
- **LOW-4** — `scan_test.log` committed (transient local scan log). Should be gitignored. (Also DEADCODE.)

---

## Routes verified (render + live status)

| Route | Status | Notes |
|---|---|---|
| `/` | 200 | landing (ISR 15m), reads real scan data |
| `/login` `/signup` `/forgot-password` `/check-email` | 200 | auth; signup is open/auto-confirm |
| `/privacy` `/terms` `/disclaimer` | 200 | legal |
| `/design-system` | 200 | internal themed preview (intentional orphan) |
| `/robots.txt` `/sitemap.xml` | 200 | |
| `/account/update-password` | 307 → forgot-password | recovery landing (unauthed) |
| `/dashboard` + all subroutes, `/admin` | 307 → /login | correct gating (unauthed) |
| unknown path | 404 | Next default |

All dynamic dashboard pages were code-reviewed for data-fetch + empty-state handling: dashboard (Today), focus-list, research/[ticker], emerging, commodities + [key], scan-history + [id], reports/[date]/[run_type], account, admin + tenants/[id]. Each has a real empty state; `admin/tenants/[id]` HIGH-6 from the old report (the `listUsers({perPage:1000})` slowdown) is **already fixed** (batched `getUserById`).
