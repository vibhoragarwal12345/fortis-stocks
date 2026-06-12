# Stock-Scan Pipeline Diagnostic — 2026-06-12

Diagnostic-only report. No fixes implemented. All times UTC unless noted.

## TL;DR

All three symptoms share one root cause: commit `bb1acf4` (pushed today ~07:19 UTC,
"feat(scan): dossier-coverage invariant + LLM provider capacity") made each scan
**materially slower** (factcheck on every thesis AND critique, retry passes, and a
tier-0 LLM balancer that routes ~1/3 of calls to NVIDIA at 60–90 s/call). The scan
now needs ~65–70 min cold, but `market_scan.yml` has `timeout-minutes: 50`. Both
runs today were killed at the 50-minute ceiling **mid-critic, before Layer 4, the
dossier gate, and finalize ran** — so no scan completed today, `scan_state` still
points at yesterday's scan 30 (site stale), and the displayed list is yesterday's
28-name gated list, not any 20–25 shortlist. Separately, **no "20–25" shortlist
configuration exists anywhere in the repo** — the committed change is exactly 20.

---

## Symptom 1 — "The pipeline is not scanning"

**Root cause (confidence: HIGH):** scans ARE being scheduled and started, but both
runs today were cancelled at the GitHub Actions job timeout (`timeout-minutes: 50`,
`.github/workflows/market_scan.yml:71`) before completing. bb1acf4 pushed per-scan
runtime past 50 minutes.

**Evidence**

- `gh run list --workflow=market_scan.yml`:
  - `27401003151` (workflow_dispatch, 2026-06-12 07:19) — **cancelled, 50m22s**
  - `27414055501` (schedule, 2026-06-12 11:55) — **cancelled, 50m20s**
  - Last green run: 2026-06-11 22:59 (35m02s). All of Jun 8–11 green at 30–44 min.
- Both logs end with `##[error]The operation was canceled.` during the
  `critic_agent` stage (08:09:59 and 12:45:21 respectively). `concurrency` has
  `cancel-in-progress: false`, so this is the job timeout, not a concurrency cancel.
- Stage timings, run 27401003151 (cold): setup ~3m, Layer 1 310s, Layer 2 2s,
  catalyst 373s, smart_money_intel 500s, **debate_synthesizer 905s**, critic killed
  ~12 min in. ≈42 min consumed before critic even started; critic alone needs ~25 min.
- Stage timings, run 27414055501 (warm — theses carried over): Layer 1 336s,
  catalyst 227s, smart_money_intel 844s, debate 2s ("20 picks already have theses"),
  critic ran 12:21→12:45 (~19/20 critiques done, in retry pass) and was killed.
  Even the warm path missed by ~5 minutes.
- Why the critic is slow now (all introduced/aggravated by bb1acf4):
  1. Every critique gets a `factcheck()` LLM pass, with discard-and-retry on
     UNVERIFIED (log: MPTI, BFLY, BNED discarded, 30 s cooldown retry pass).
  2. Tier-0 balancing in `pipeline/llm.py:266-272` picks the provider with the
     largest remaining daily-budget fraction. Today's ledger (`llm_usage`,
     2026-06-12): groq 17 req/21,488 tok, cerebras 135 req/211,278 tok, nvidia
     106 req/139,096 tok → remaining fractions 0.785 / 0.789 / 0.788 — i.e. the
     balancer deliberately equalizes, sending ~45% of calls to **nvidia, whose
     calls take 60–90 s each** in the log (groq/cerebras respond in 1–3 s).
     Latency is not a factor in the balancing. Budgets are NOT exhausted —
     this is a throughput problem, not a quota problem.
- DB state confirms no scan finished today (`market_scans`):
  - id=32 intraday, **status='running'**, started 11:57:43, completed_at NULL
  - id=31 manual,   **status='running'**, started 07:22:26, completed_at NULL
  - id=30 intraday, status='complete', completed 2026-06-11 23:34:39 (last good)
- Aggravator: `pipeline/agents/critic_agent.py:337-366` persists ALL critiques in
  one batch at the END of the stage. The kill loses the whole stage's work —
  scan 32 has `critic_objection_level` set on **0/20 rows** despite ~16 successful
  critiques in the log. So every retry starts the critic cold; the failure is
  self-perpetuating. (The debate writer persists incrementally, which is why
  theses survived run 31 and made run 32's debate take 2 s.)
- Prognosis if unfixed: the 16:30 UTC run today will likely be killed again
  (warm path measured at >50 min), and the first run of every new `run_date`
  is fully cold (~65–70 min) — deterministic timeout every morning.
- Margin was already thin pre-bb1acf4: two runs on Jun 4 were also cancelled at
  50m. bb1acf4 turned an occasional overrun into a certain one.

**Proposed fix (do not blindly raise the timeout)**

1. `pipeline/llm.py` — stop sending heavy critic/debate traffic to nvidia:
   either demote nvidia to tier 1/2 (`llm.py:103-105`), or weight
   `_eligible_providers()` (`llm.py:266-272`) by observed latency, not just
   remaining budget fraction. groq+cerebras alone served 30-name scans in
   30–35 min before.
2. `pipeline/agents/critic_agent.py` — persist each critique as it completes
   (move the `db.table("ranked_focus_list").update(...)` from the post-loop
   batch at lines 337–366 into the per-ticker loop), so a timeout loses minutes,
   not the stage.
3. `pipeline/scan/run_scan.py` — add an internal soft deadline (e.g. 42 min):
   when reached, skip remaining Layer-3 work and ALWAYS run Layer 4 + dossier
   gate + finalize. The dossier gate is designed for exactly this ("budget
   exhausted = shorter list"); today it never runs because the kill is external.
4. Only then consider `timeout-minutes: 50 → ~55` as headroom. Note the Actions
   free-minutes math in the workflow header: 3 runs/day already ≈ 1,900–2,000
   min/month; 70-min runs would blow the 2,000-min free ceiling, so making the
   scan faster is the real lever.
5. Data backfill (cheap hygiene): set `market_scans.status='failed'` for ids 31
   and 32, and reset `scan_state.current_status` from 'running' to 'idle' —
   both are stuck (see Symptom 2) and may block the /api/scan manual-refresh
   gate (unverified, did not read that route).

---

## Symptom 2 — "The site shows stale data"

**Root cause (confidence: HIGH):** downstream of Symptom 1. The landing page only
ever shows the last **completed** scan, and no scan has completed since
2026-06-11 23:34:39 UTC.

**Evidence**

- `src/lib/landing-data.ts:52-58` reads `scan_state.latest_scan_id` /
  `latest_scan_completed_at`. `pipeline/scan/run_scan.py:314-322` updates
  `scan_state.latest_scan_id` **only when status == 'complete'** — the finalize
  block at `run_scan.py:304-329` never executed in either run today (killed
  mid-critic).
- Current `scan_state` row: `latest_scan_id=30`,
  `latest_scan_completed_at=2026-06-11 23:34:39`, and `current_status='running'`
  with `running_since=2026-06-12 11:57:42` — stuck, because only run_scan's
  finalize resets it and the job was SIGKILLed. (The dashboard likely shows a
  perpetual "scan running" state; the once-per-2h manual refresh gate may also
  be wedged by this — flagged, not verified, since I did not read the /api/scan
  route.)
- The data path itself is healthy — no schema drift: scan 30 has 30
  `ranked_focus_list` rows, 28 with `dossier_complete=true`, and
  `landing-data.ts:71` filters `.eq("dossier_complete", true)` which matches what
  `pipeline/scan/dossier_gate.py` writes. The dossier-coverage invariant is NOT
  filtering everything out (28/30 pass for scan 30). For scan 32 it would filter
  everything (0/20 complete — gate never ran), but the site never points at scan
  32 precisely because it never completed. ISR 15-min revalidation is irrelevant
  here; the upstream pointer is stale.

**Proposed fix:** fix Symptom 1; additionally reset the stuck `scan_state` row
(`current_status='idle'`) and mark scans 31/32 'failed' so the dashboard reflects
reality. No change needed in `src/lib/landing-data.ts` — it is behaving exactly
as designed ("at most 15 minutes behind the freshest **completed** scan").

---

## Symptom 3 — "Final scan is not honoring the 20–25 share shortlist"

**Root cause (confidence: HIGH on repo contents; the user's intended change is
not findable):** there is **no 20–25 shortlist configuration anywhere in the
repo**. The recent committed change (bb1acf4, today) set the shortlist from 30 to
a fixed **20**:

- `.github/workflows/market_scan.yml:98` — `--top-n 20` (was `--top-n 30`)
- `pipeline/scan/run_scan.py:65` — `DEFAULT_TOP_N = 20`
- bb1acf4 commit message: "Shortlist top-N 30 -> 20: measured per-scan dossier
  budget…"

Searched for any 20–25 range: working tree (`git status` shows only
commodities/india files modified, nothing in pipeline/scan or workflows),
`git stash list` (empty), all local+remote branches, `git log --all -S` for
top-n/DEFAULT_TOP_N — only bb1acf4 touches it. **Flag: if a 20–25 change was made,
it was never committed and has left no trace I can find; it may have been a lost
local edit or a misremembered version of the 30→20 change.**

What the user actually observes is also compounded by the other two symptoms:

1. No post-bb1acf4 scan has completed, so the site still displays **scan 30** — a
   pre-change top-30 scan, gated down to 28 names by the dossier gate
   (`dossier_complete` 28/30). 28 names ≠ 20–25, hence "not honoring".
2. Even once a post-change scan completes, the displayed count will be
   "≤ 20" (20 minus any names the dossier gate hides), never 20–25. To
   distinguish: `market_scans.shortlist_count` is rewritten to the post-gate
   displayed count (`run_scan.py:276-278`) and the hidden names are listed in
   `market_scans.notes`; "scan produced too few" vs "gate demoted them" is
   readable from there per scan. (Scans 28 and 30 had notes=NULL with 22/22 and
   28/30 coverage — the note is only written when coverage < shortlist, and
   scan 30's 28/30 gap predates the notes wiring or was overwritten; minor,
   unverified.)

**Proposed fix:** decide the real target and implement it explicitly in BOTH
places that currently hardcode 20: `.github/workflows/market_scan.yml:98`
(`--top-n`) and `pipeline/scan/run_scan.py:65` (`DEFAULT_TOP_N`). If the intent
is "display 20–25", note the dossier gate makes the displayed count ≤ top-n, so
the shortlist must be sized at 25 (with the LLM budget re-checked — bb1acf4's
stated reason for 20 was that 30-name lists only shipped 19–27 dossiers/day; 25
needs the throughput fix from Symptom 1 to be viable inside the time and token
budgets).

---

## Side findings

- **OPENROUTER_API_KEY (known pending item): nothing hard-depends on it.**
  `pipeline/config.py:35` defaults it to `""`, and `pipeline/llm.py:119`
  (`return [p for p in candidates if p.api_key]`) silently drops keyless
  providers. The workflow references the secret (`market_scan.yml:64`) but a
  missing secret just yields an empty env var. Effect is only that tier-2
  overflow capacity (~50 req/day) doesn't exist. Adding the secret is optional
  capacity, not a fix for any of today's symptoms.
- `llm_usage` ledger (migration 048) is live and being written (rows exist for
  2026-06-12 only — the table was created today). No provider is near its daily
  cap: groq 21,488/100,000 tokens; cerebras 211,278/1,000,000; nvidia 106/500
  requests. Dossier starvation today is from job cancellation, NOT budget
  exhaustion.
- `ranked_focus_list` rows are upserted on (run_date, run_type, ticker) with
  `scan_id` restamped — that is why scan 31's rows "vanished" (absorbed into
  scan 32) and why scan 32 inherited 20 completed theses. Same-day reruns are
  warm; the first run of each day is fully cold.
- GitHub deprecation warning in every run: actions/checkout@v4 +
  actions/setup-python@v5 will be forced onto Node 24 starting **2026-06-16**.
  Worth bumping soon; not implicated in today's failures.
- Runner log noise: `smart_money_intel.py:1362` emits a pandas FutureWarning
  per ticker ("Calling float on a single element Series is deprecated") — will
  become a TypeError on a future pandas bump.

## Could not verify

- Whether the user's 20–25 change ever existed (no trace in git history,
  stashes, branches, or working tree).
- The /api/scan manual-refresh route's behavior when `scan_state.current_status`
  is stuck 'running' (did not read src/ beyond the explicitly sanctioned
  `landing-data.ts`).
- Exact NVIDIA per-call latency (inferred 60–90 s from log timestamp deltas).
- Why scan 30 (28/30 coverage) has `notes=NULL` despite the coverage-note code
  path in `run_scan.py:272-275`.
