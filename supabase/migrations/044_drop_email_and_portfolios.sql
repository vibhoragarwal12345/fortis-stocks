-- ============================================================
-- 044_drop_email_and_portfolios.sql -- Lean rewrite PART 1
--
-- Removes everything tied to the obsolete subsystems:
--   * Email delivery (Resend) -- we no longer email reports.
--   * Portfolios + positions  -- we are a login-only research surface,
--                                no user-held data.
--
-- All RLS policies on these tables drop with the table thanks to
-- CASCADE. Any view, FK, or function depending on them goes too.
-- ============================================================

DROP TABLE IF EXISTS public.email_preferences        CASCADE;
DROP TABLE IF EXISTS public.email_delivery_log       CASCADE;

DROP TABLE IF EXISTS public.holdings_alerts          CASCADE;
DROP TABLE IF EXISTS public.portfolio_alert_summary  CASCADE;
DROP TABLE IF EXISTS public.portfolio_rebalance_suggestions CASCADE;
DROP TABLE IF EXISTS public.portfolio_risk_metrics   CASCADE;
DROP TABLE IF EXISTS public.position_signals         CASCADE;
DROP TABLE IF EXISTS public.portfolio_holdings       CASCADE;
DROP TABLE IF EXISTS public.portfolios               CASCADE;
DROP TABLE IF EXISTS public.breaking_events          CASCADE;
