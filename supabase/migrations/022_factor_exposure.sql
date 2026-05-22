-- ============================================================
-- 022_factor_exposure.sql
-- Per-ticker factor exposures + cross-portfolio concentration from
-- pipeline/agents/factor_overlay.py.
--
-- Numbered 022 (the next sequential migration after 021). The original
-- request said 011, but 011 is long-applied (thesis_columns) and a number
-- collision would silently no-op the migration runner -- so this follows
-- the real sequence, the same convention every prior migration uses.
--
-- Two tables:
--   factor_exposure       one row per (ticker, snapshot_date)
--   factor_concentration  one row per (run_date, run_type) -- portfolio view
-- ============================================================

CREATE TABLE IF NOT EXISTS public.factor_exposure (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker            text NOT NULL,
  snapshot_date     date NOT NULL DEFAULT current_date,
  value_exposure    numeric,   -- -1 (expensive)  .. +1 (cheap)
  growth_exposure   numeric,   -- -1 (shrinking)  .. +1 (fast-growing)
  quality_exposure  numeric,   -- -1 (junk)       .. +1 (high-quality)
  momentum_exposure numeric,   -- -1 (lagging)    .. +1 (leading)
  low_vol_exposure  numeric,   -- -1 (high vol)   .. +1 (low vol)
  size_classification text,    -- large_cap | mid_cap | small_cap
  sector            text,      -- GICS sector
  factor_tags       jsonb DEFAULT '[]'::jsonb,    -- ['growth','momentum','large_cap',...]
  raw_metrics       jsonb DEFAULT '{}'::jsonb,    -- PE, ROE, D/E, growth rates, vols
  created_at        timestamptz DEFAULT now(),
  UNIQUE (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_factor_exposure_date
  ON public.factor_exposure (snapshot_date DESC, ticker);

CREATE TABLE IF NOT EXISTS public.factor_concentration (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_date               date NOT NULL DEFAULT current_date,
  run_type               text NOT NULL DEFAULT 'midday',
  factor_distribution    jsonb DEFAULT '{}'::jsonb,   -- {tag: count}
  sector_distribution    jsonb DEFAULT '{}'::jsonb,   -- {sector: count}
  concentration_alerts   jsonb DEFAULT '[]'::jsonb,   -- list of warning strings
  diversification_score  numeric,   -- 0 (concentrated) .. 100 (diversified)
  picks_analyzed         int,
  created_at             timestamptz DEFAULT now(),
  UNIQUE (run_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_factor_concentration_date
  ON public.factor_concentration (run_date DESC);

ALTER TABLE public.factor_exposure      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.factor_concentration ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.factor_exposure
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_read" ON public.factor_concentration
  FOR SELECT TO authenticated USING (true);
