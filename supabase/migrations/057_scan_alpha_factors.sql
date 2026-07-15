-- ============================================================
-- 057_scan_alpha_factors.sql
-- Three alpha-bench factor columns on scan_results (2026-07-15).
-- Source: two-window factor bench (pipeline/backtest_v2/factor_bench.py),
-- factors absorbed from the HKUDS/Vibe-Trading Alpha Zoo families and
-- re-implemented natively. All three were HEALTHY with a consistent sign
-- in both test windows (May 19–Jun 17, Jun 18–Jul 15):
--   volume_vol_20d  std of daily volume %-change over 20d   (IC −0.079)
--   low20_ratio     min(close, 20d) / close                 (IC +0.077)
--   pv_corr_20d     corr(close, log1p(volume)) over 20d     (IC −0.075)
-- Additive + nullable: old rows stay NULL; no RLS/index changes needed.
-- ============================================================

ALTER TABLE public.scan_results
  ADD COLUMN IF NOT EXISTS volume_vol_20d numeric,
  ADD COLUMN IF NOT EXISTS low20_ratio    numeric,
  ADD COLUMN IF NOT EXISTS pv_corr_20d    numeric;

COMMENT ON COLUMN public.scan_results.volume_vol_20d IS
  'Std dev of daily volume %-change, 20d window. Alpha-bench factor (negative IC: volume churn precedes 5d underperformance).';
COMMENT ON COLUMN public.scan_results.low20_ratio IS
  'min(close, 20d) / close. Alpha-bench factor (positive IC: names near 20d lows outperform at 5d).';
COMMENT ON COLUMN public.scan_results.pv_corr_20d IS
  'Correlation of close vs log1p(volume), 20d window. Alpha-bench factor (negative IC: rally-on-volume names underperform at 5d).';
