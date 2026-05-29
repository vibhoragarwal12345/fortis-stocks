# Fortis Stock Intelligence — Bug Report

_Diagnostic run on 2026-05-29, dev server `next dev` (Turbopack), signed in as `vibhora030@gmail.com` (platform admin + Fortis tenant admin)._

## Scope of evidence

| Evidence | Result |
|---|---|
| `npm run dev` startup | clean — `Ready in 433ms`, no compile errors |
| HTTP probe of 12 routes (unauthed) | every public route 200, every authed route 307 → `/login` (correct) |
| HTTP probe of 12 routes (authed cookie) | every route 200; dev-server log shows no server-side stack traces |
| Server console warnings | one — React `encType` warning on the branding form (see HIGH-3) |
| Database data presence | `reports today=1, ranked_focus_list today=0, portfolios=1, portfolio_holdings=15, position_signals=15, pick_outcomes=94, system_performance_rollup=7, emerging_watchlist=4, multibagger_theses=5` |

**Headline finding**: nothing is crashing server-side. The "blank/broken screens" symptom is driven by **dead nav links** (HIGH-1, HIGH-2), **two duplicate-header pages** (HIGH-4, HIGH-5), and **empty-state gaps** on Today's dashboard when the daily pipeline hasn't run for the current date (MEDIUM-2, MEDIUM-3).

---

## BLOCKER

_None observed. No route returns 5xx, no SSR crash, no compile error._

---

## HIGH

### HIGH-1 — Dead nav links: `/dashboard/focus-list` and `/dashboard/research/{ticker}`

- **Files**:
  - `src/app/dashboard/layout.tsx:17,20` — Nav points to `/dashboard/focus-list` twice ("Focus list" and "Research" labels)
  - `src/app/dashboard/page.tsx:300` — "See full focus list" CTA
  - `src/app/dashboard/page.tsx:326` — per-pick `Link href={`/dashboard/research/${p.ticker}`}`
- **Symptom**: Clicking any of these from the header nav or the Today page lands on Next.js's default 404. Two of the six top-level nav buttons are dead.
- **Cause**: There's no `src/app/dashboard/focus-list/` directory and no `src/app/dashboard/research/[ticker]/` directory in the tree; they were referenced but never built.
- **Fix direction**: Either build stub pages with "Coming soon" cards, or remove the nav items and link to existing routes (e.g. point "Focus list" at the Today page's picks section anchor). Stubs preferred so future pipeline output has a home.

### HIGH-2 — Dead deep link: `/dashboard/reports/{date}/{run_type}`

- **Files**:
  - `src/app/dashboard/page.tsx:260` — "Open report" button (visible only when a report exists for today)
  - `src/lib/email.ts:266` — `reportUrl()` used by report-delivery emails
- **Symptom**: The "Open report" button on the Today page (when a brief exists) goes to `/dashboard/reports/2026-05-29/midday` → 404. Same URL is embedded in every report email that's been sent, so prior emails have a permanent 404.
- **Cause**: No `src/app/dashboard/reports/[date]/[run_type]/page.tsx`.
- **Fix direction**: Either build the report viewer page (it's been promised since Step 7.2), or hide the "Open report" button + scrub email links until it exists. Building the viewer is the better path because it's already plumbed in `lib/email.ts`.

### HIGH-3 — `encType` on server-action form (React warning, may strip the upload)

- **File**: `src/app/dashboard/settings/branding/page.tsx:49`
- **Symptom**: Dev console warns _"Cannot specify a encType or method for a form that specifies a function as the action. React provides those automatically. They will get overridden."_
- **Cause**: When `form action={...}` references a server action, React requires you NOT to set `encType` — React picks the correct encoding (multipart when a `File` field is present). My explicit `encType="multipart/form-data"` is overridden.
- **Risk**: With Turbopack + React 19 server actions, the override happens correctly today, but the warning is a sign we're driving outside the supported API. The logo-file upload path might silently break on a future Next minor.
- **Fix direction**: Remove `encType="multipart/form-data"` from the `<form>` — React will set it.

### HIGH-4 — Double header on `/dashboard/positions`

- **Files**:
  - `src/app/dashboard/layout.tsx:33-85` — renders the shared header with nav
  - `src/app/dashboard/positions/page.tsx:185, 433-459` — renders its own inline `<Header>` component
- **Symptom**: Two stacked headers — the layout's branded header on top, then a second hardcoded "Fortis Stock Intelligence | Dashboard | Track Record | Positions" header below it. Visually broken; conflicts with the multi-tenant theming (the inner header is hardcoded "Fortis" even for a non-Fortis tenant).
- **Cause**: Page was written before the dashboard layout's header existed; the inline header was never removed.
- **Fix direction**: Delete the inline `<Header>` component + its two render sites in `positions/page.tsx`. Rely entirely on the layout.

### HIGH-5 — Double header on `/dashboard/track-record`

- **Files**:
  - `src/app/dashboard/layout.tsx:33-85` — shared header
  - `src/app/dashboard/track-record/page.tsx:231-247` — page renders its own inline `<header>`
- **Symptom**: Same double-header as HIGH-4.
- **Cause**: Same — predates the layout header.
- **Fix direction**: Delete the inline `<header>` block in `track-record/page.tsx`.

### HIGH-6 — `/admin` tenant detail page hits `auth.admin.listUsers` with `perPage: 1000` on every render

- **File**: `src/app/admin/tenants/[id]/page.tsx:73-78`
- **Symptom**: Dev server logs `GET /admin … application-code: 43s`. The Admin tenant-detail page takes ~30–45s to render in dev because it pulls every user from auth on every load and matches against the tenant's member list.
- **Cause**: Lazy lookup — we list every user, then build an email map keyed by id, just to display member emails. Production won't be 45s but it'll still be ~2s for a single-tenant pilot and grow with users.
- **Fix direction**: Loop over the tenant's `tenant_members` and call `service.auth.admin.getUserById(user_id)` (1 call per member, with `Promise.all`). Acceptable until the tenant has dozens of members, then we can cache.

---

## MEDIUM

### MEDIUM-1 — Empty Today dashboard because daily pipeline hasn't run for `report_date = CURRENT_DATE`

- **File**: `src/app/dashboard/page.tsx:118-135`
- **Symptom**: On opening `/dashboard` today, all three Brief cards say "Awaiting", the "Today's top picks" panel says "No A-grade picks for today yet", because `reports` and `ranked_focus_list` have 0 rows for today's date.
- **Cause**: This is data state, not a bug. The premarket/midday/close GitHub Actions workflows run on weekdays; the data we have for "today" is just the multibagger monthly report. The page renders the "Awaiting" empty states correctly — but a fresh visitor sees an empty-looking dashboard.
- **Fix direction**:
  - Empty-state copy should explicitly say _"Daily briefs run weekdays. The next pre-market brief lands at 6:00am ET Monday."_ instead of just "Will be available at 6:00am ET" (which sounds like _today_).
  - Consider showing the LATEST brief (most recent date) below the today-row, with a "Today's brief hasn't run yet — here's yesterday's" header. Keeps the dashboard never-empty.

### MEDIUM-2 — `dashboard/page.tsx` "Recent activity" card is permanently a placeholder

- **File**: `src/app/dashboard/page.tsx:448-467`
- **Symptom**: Bottom card says _"Activity feed enabled in step 7.5"_. Step 7.5 is done — the feedback aggregator wrote 0 rows for this user, so the placeholder remains.
- **Cause**: Step 7.5 shipped the back-end (`dashboard_events`, `user_action_summary`) but the page card was never wired to read from them.
- **Fix direction**: Replace the placeholder with a real query (`dashboard_events` for this user, last N) + a real empty state ("No activity yet — open a brief or a pick to start populating this feed").

### MEDIUM-3 — Today's `pick_outcomes` and `system_performance_rollup` exist but track-record page may render mostly "—"

- **File**: `src/app/dashboard/track-record/page.tsx`
- **Symptom**: 94 pick_outcomes and 7 rollup rows do exist. But the latest rollup may not match the page's filter `r.rollup_date === latestRollupDate && r.run_type === "all"` — if the latest rollup is per-run-type rather than "all", the aggregate row will be undefined and all four headline metrics show "—".
- **Cause**: Possible mismatch between the rollup aggregator's `run_type` values and what the page expects. Need to query the latest rollup to confirm.
- **Fix direction**: After verification, either change the page filter to fall back to summed-by-grade when "all" is missing, or fix the aggregator to always write an `all` row.

### MEDIUM-4 — `globals.css` self-referencing `--font-sans`

- **File**: `src/app/globals.css:10,12`
- **Symptom**: `--font-sans: var(--font-sans);` is a circular CSS variable reference. Combined with `--font-heading: var(--font-sans);`, the body class `font-sans` and `font-heading` classes silently resolve to the browser default (`-apple-system, …`) instead of Geist.
- **Cause**: Tailwind v4 + Next 16 fonts integration: the `@theme inline` should reference `var(--font-geist-sans)` (Geist's actual variable from `layout.tsx`), not the same name. Likely a mis-merge during the shadcn v4 migration.
- **Risk**: Cosmetic — the design will look like the browser default sans-serif, not Geist. Not a render-blocker.
- **Fix direction**: In `globals.css:10`, change `--font-sans: var(--font-sans);` → `--font-sans: var(--font-geist-sans);` (and ensure layout.tsx still exposes that var).

### MEDIUM-5 — Lucide-react pinned at `^1.16.0` (very old)

- **File**: `package.json`
- **Symptom**: 1.x is a long-deprecated lucide-react line. Icons used in `dashboard/page.tsx` (`AlertTriangle, ArrowRight, Briefcase, Moon, Sun, Sunrise`) all exist in 1.16, so they render — but any future icon add risks "icon not found".
- **Fix direction**: Bump to `^0.4xx.0` (current line as of 2026) at next sprint boundary. Not urgent.

### MEDIUM-6 — `/dashboard/emerging` always lists every tier even when empty

- **File**: `src/app/dashboard/emerging/page.tsx`
- **Symptom**: With 4 watchlist rows (3 tier_3, 1 tier_2, 0 tier_1), the Tier 1 section never appears (correct), but if a user lands here before the first monthly run there'd be no copy explaining what they're looking at.
- **Cause**: The empty-state card only shows if `rows.length === 0`. Once the first monthly run lands, the page never shows context again.
- **Fix direction**: Persistent intro paragraph above the tiers explaining what "Emerging Watchlist" is, even when populated. Already partially covered by the amber risk banner but the banner doesn't explain the tier system.

---

## LOW / cosmetic

### LOW-1 — `/dashboard` brief cards use `Card size="sm"` but never declare the `size` type on `Card`'s prop spread

- **File**: `src/components/ui/card.tsx`
- Component supports `size` but only `"default" | "sm"`. Tests fine.

### LOW-2 — TypeScript: `Tenant` and other types defined inline per-page rather than centralised

- Minor maintenance debt; not affecting render.

---

## Routes that render cleanly (no findings)

| Route | Notes |
|---|---|
| `/` | Gateway. Clean. |
| `/login` | Forgot-password link visible. Clean. |
| `/signup` (no token) | Renders invite-only card. Clean. |
| `/forgot-password` | Clean. |
| `/check-email` | Clean. |
| `/account/update-password` | Redirects to `/forgot-password` correctly when unauthed. Clean. |
| `/dashboard` | Renders, but see MEDIUM-1 and MEDIUM-2. |
| `/dashboard/positions` | Renders, but see HIGH-4 double header. |
| `/dashboard/track-record` | Renders, but see HIGH-5 and MEDIUM-3. |
| `/dashboard/emerging` | Renders 4 watchlist rows. |
| `/dashboard/settings/branding` | Renders, see HIGH-3 warning. |
| `/admin` | Renders. Admin tenant detail is slow (HIGH-6). |
| `/admin/tenants/{id}` | Renders. |

---

## Fix plan (proposed order)

1. **HIGH-4** + **HIGH-5** — strip duplicate headers. (Trivial — minutes.)
2. **HIGH-3** — drop `encType` from the branding form. (Trivial.)
3. **HIGH-1** — build `/dashboard/focus-list` stub and `/dashboard/research/[ticker]` stub. (Stub pages with proper empty states.)
4. **HIGH-2** — build `/dashboard/reports/[date]/[run_type]/page.tsx` as a real report viewer (renders `reports.content_html` / `content_markdown`).
5. **HIGH-6** — replace `listUsers({perPage:1000})` with batched `getUserById` lookups.
6. **MEDIUM-1, MEDIUM-2, MEDIUM-3** — proper empty-state copy + wire activity card + verify rollup `run_type`.
7. **MEDIUM-4, MEDIUM-5, MEDIUM-6** — font var, lucide bump, emerging intro.
8. **Add error boundaries** in `src/app/error.tsx` and per-route `error.tsx` so any one query failure shows a graceful card instead of blanking the whole page.

Estimated time: HIGH section ~1.5 hours, MEDIUM ~2 hours, boundaries ~30 min.

_End of report._
