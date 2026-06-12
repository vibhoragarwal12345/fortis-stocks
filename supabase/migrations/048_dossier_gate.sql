-- ============================================================
-- 048_dossier_gate.sql -- dossier-coverage invariant + LLM usage ledger
--
-- PART A: dossier columns on ranked_focus_list.
-- A name may only appear on the displayed shortlist if it carries a
-- COMPLETED, FACT-CHECKED dossier (thesis + critic + catalyst + grade,
-- factcheck grade VERIFIED / PARTIALLY_VERIFIED). The dossier gate
-- (pipeline/scan/dossier_gate.py) sets dossier_complete after Layer 4;
-- the dashboard and landing page filter on it. A shorter list when the
-- LLM budget runs out is correct behavior -- a bare name is not.
--
-- PART B: llm_usage -- per-provider daily request/token ledger.
-- The LLM gateway (pipeline/llm.py) increments this after every call so
-- budget awareness survives across agent subprocesses AND across the three
-- daily CI scan jobs. Written only by the pipeline via service role.
-- ============================================================

-- ── Part A: dossier verification columns ──────────────────────────────
ALTER TABLE public.ranked_focus_list
  ADD COLUMN IF NOT EXISTS dossier_complete           boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS dossier_verification_score numeric,
  ADD COLUMN IF NOT EXISTS dossier_quality_grade      text
    CHECK (dossier_quality_grade IN ('VERIFIED','PARTIALLY_VERIFIED','UNVERIFIED')
           OR dossier_quality_grade IS NULL),
  ADD COLUMN IF NOT EXISTS dossier_provider           text,
  ADD COLUMN IF NOT EXISTS dossier_completed_at       timestamptz;

-- The display queries read (scan_id, dossier_complete) together.
CREATE INDEX IF NOT EXISTS idx_rfl_scan_dossier
  ON public.ranked_focus_list (scan_id, dossier_complete);

-- One-time grandfather of pre-048 dossiers: rows written before the
-- factcheck columns existed (dossier_quality_grade IS NULL) but that carry
-- every dossier section stay visible, so the live shortlist doesn't blank
-- out at deploy. All rows written after 048 get a grade from the agents,
-- so this clause can never re-admit a new unverified dossier.
UPDATE public.ranked_focus_list
SET dossier_complete = true
WHERE dossier_complete = false
  AND dossier_quality_grade IS NULL
  AND catalyst_description    IS NOT NULL
  AND bull_case               IS NOT NULL
  AND bear_case               IS NOT NULL
  AND critic_objection_level  IS NOT NULL
  AND conviction_grade        IS NOT NULL;

-- ── Part B: per-provider daily usage ledger ───────────────────────────
CREATE TABLE IF NOT EXISTS public.llm_usage (
  usage_date   date        NOT NULL,
  provider     text        NOT NULL,
  requests     integer     NOT NULL DEFAULT 0,
  total_tokens bigint      NOT NULL DEFAULT 0,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (usage_date, provider)
);

-- Service-role only: enable RLS with no policies (anon/authenticated see
-- nothing; the service role bypasses RLS).
ALTER TABLE public.llm_usage ENABLE ROW LEVEL SECURITY;

-- Atomic concurrent-safe increment -- agent subprocesses race within a scan.
CREATE OR REPLACE FUNCTION public.increment_llm_usage(
  p_provider text,
  p_requests integer DEFAULT 1,
  p_tokens   bigint  DEFAULT 0
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  INSERT INTO llm_usage (usage_date, provider, requests, total_tokens, updated_at)
  VALUES (current_date, p_provider, p_requests, p_tokens, now())
  ON CONFLICT (usage_date, provider) DO UPDATE
    SET requests     = llm_usage.requests + EXCLUDED.requests,
        total_tokens = llm_usage.total_tokens + EXCLUDED.total_tokens,
        updated_at   = now();
$$;
