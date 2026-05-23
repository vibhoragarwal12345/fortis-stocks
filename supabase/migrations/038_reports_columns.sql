-- ============================================================
-- 038_reports_columns.sql
-- Extend the reports table for Step 7.1 -- the Report Composer.
--
-- The base reports table was created in 001_initial_schema (id uuid, user_id,
-- report_type, report_date, content_html, content_markdown, delivered_email,
-- created_at). This migration adds the columns the composer needs:
-- structured JSON of the full report, grade-tier counts and verification
-- score for at-a-glance quality, generation timing and LLM-call counters for
-- cost tracking, delivery / view metrics, and a tenant_id placeholder
-- (nullable now; populated in the multi-tenant step 7.6).
--
-- Spec named this 037_reports.sql; 037 is already taken (037_form4_transactions),
-- so this is 038 -- the next free slot.
-- ============================================================

ALTER TABLE public.reports
  ADD COLUMN IF NOT EXISTS tenant_id              uuid,
  ADD COLUMN IF NOT EXISTS content_structured     jsonb,
  ADD COLUMN IF NOT EXISTS a_grade_count          int     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS b_grade_count          int     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS c_grade_count          int     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS avg_verification_score numeric,
  ADD COLUMN IF NOT EXISTS generation_time_seconds numeric,
  ADD COLUMN IF NOT EXISTS llm_calls_made         int     DEFAULT 0,
  ADD COLUMN IF NOT EXISTS generated_at           timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS email_delivered_at     timestamptz,
  ADD COLUMN IF NOT EXISTS viewed_in_dashboard    boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS view_count             int     DEFAULT 0;

-- Lookups by tenant + date (dashboard queries) and by report_type + date
-- (cross-tenant cohort reporting later).
CREATE INDEX IF NOT EXISTS idx_reports_tenant_date
  ON public.reports (tenant_id, report_date DESC);
CREATE INDEX IF NOT EXISTS idx_reports_type_date
  ON public.reports (report_type, report_date DESC);

COMMENT ON COLUMN public.reports.content_structured IS
  'Full structured data of the report (every section, every claim) as jsonb, '
  'for dashboard / API access and re-rendering without re-running the composer.';
COMMENT ON COLUMN public.reports.tenant_id IS
  'Multi-tenant filter. Nullable in step 7.1; populated by step 7.6.';
COMMENT ON COLUMN public.reports.avg_verification_score IS
  'Mean verification_score across the picks featured in the report -- shows '
  'the advisor the at-a-glance quality of today''s brief.';
