-- ============================================================
-- 024_holdings_alerts.sql
-- Per-holding material-event alerts produced by
-- pipeline/agents/risk_flagger.py
--
-- Numbered 024 (the next sequential migration after 023). The original
-- request said 013, but 013-023 are long-applied -- a number collision
-- would break ordered migration runners, so this follows the same
-- renumbering convention used by 002-006 and 010.
--
-- Two tables:
--   * holdings_alerts          -- one row per (portfolio, ticker, run, alert_type)
--   * portfolio_alert_summary  -- per-portfolio rollup of severity counts +
--                                portfolio_at_risk_pct + top 3 priorities
-- ============================================================

-- ── 1. holdings_alerts ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.holdings_alerts (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id    uuid REFERENCES public.portfolios,
  ticker          text NOT NULL,
  alert_date      date NOT NULL DEFAULT current_date,
  run_type        text NOT NULL DEFAULT 'midday',    -- premarket | midday | close
  severity        text NOT NULL,                     -- CRITICAL | HIGH | MEDIUM | INFORMATIONAL
  alert_type      text NOT NULL,                     -- earnings | sec_event | analyst | news | anomaly | insider
  message         text,                              -- one-sentence advisor-facing summary
  supporting_data jsonb DEFAULT '{}'::jsonb,         -- raw signal payload (numbers, urls, flag context)
  requires_action boolean DEFAULT false,
  created_at      timestamptz DEFAULT now(),
  UNIQUE (portfolio_id, ticker, alert_date, run_type, alert_type)
);

CREATE INDEX IF NOT EXISTS idx_holdings_alerts_portfolio
  ON public.holdings_alerts (portfolio_id, alert_date DESC);
CREATE INDEX IF NOT EXISTS idx_holdings_alerts_severity
  ON public.holdings_alerts (alert_date DESC, severity);

ALTER TABLE public.holdings_alerts ENABLE ROW LEVEL SECURITY;

-- Owner-only: derived via the portfolios join, same shape as portfolio_holdings.
CREATE POLICY "owner_all" ON public.holdings_alerts
  USING (
    portfolio_id IN (
      SELECT id FROM public.portfolios WHERE user_id = auth.uid()
    )
  )
  WITH CHECK (
    portfolio_id IN (
      SELECT id FROM public.portfolios WHERE user_id = auth.uid()
    )
  );


-- ── 2. portfolio_alert_summary ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.portfolio_alert_summary (
  id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id          uuid REFERENCES public.portfolios,
  snapshot_date         date NOT NULL DEFAULT current_date,
  run_type              text NOT NULL DEFAULT 'midday',
  critical_count        integer DEFAULT 0,
  high_count            integer DEFAULT 0,
  medium_count          integer DEFAULT 0,
  info_count            integer DEFAULT 0,
  portfolio_at_risk_pct numeric,                       -- sum of weights of HIGH+CRITICAL alerts (0..1)
  top_3_priorities      jsonb DEFAULT '[]'::jsonb,     -- [{ticker, severity, message, weight}, ...]
  created_at            timestamptz DEFAULT now(),
  UNIQUE (portfolio_id, snapshot_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_alert_summary_date
  ON public.portfolio_alert_summary (snapshot_date DESC, portfolio_id);

ALTER TABLE public.portfolio_alert_summary ENABLE ROW LEVEL SECURITY;

CREATE POLICY "owner_all" ON public.portfolio_alert_summary
  USING (
    portfolio_id IN (
      SELECT id FROM public.portfolios WHERE user_id = auth.uid()
    )
  )
  WITH CHECK (
    portfolio_id IN (
      SELECT id FROM public.portfolios WHERE user_id = auth.uid()
    )
  );
