-- ============================================================
-- 047_return_52w.sql -- one simple 52-week metric for display
--
-- The dashboard shows a single "52W change" number (the trailing
-- ~52-week price return) instead of the separate from-high / from-low
-- distances. The distance columns stay: Layer 2 scores on
-- pct_from_52w_high; they're just no longer surfaced in the UI.
-- ============================================================

ALTER TABLE public.scan_results
  ADD COLUMN IF NOT EXISTS return_52w_pct numeric;
