-- ============================================================
-- 001_initial_schema.sql
-- Fortis Stock Intelligence — Initial database schema
-- ============================================================


-- ── 1. news_items ─────────────────────────────────────────────────────────────
--    Raw news articles fetched by the pipeline. Sentiment filled in later.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.news_items (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker          text        NOT NULL,
  headline        text        NOT NULL,
  url             text        NOT NULL UNIQUE,
  source          text        NOT NULL,
  published_at    timestamptz,
  fetched_at      timestamptz DEFAULT now(),
  summary         text,
  sentiment_score numeric
);

CREATE INDEX idx_news_items_ticker       ON public.news_items (ticker);
CREATE INDEX idx_news_items_published_at ON public.news_items (published_at DESC);


-- ── 2. market_snapshots ───────────────────────────────────────────────────────
--    OHLCV snapshots written by the price ingestor. Derived fields pre-computed.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.market_snapshots (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker          text        NOT NULL,
  snapshot_time   timestamptz DEFAULT now(),
  price           numeric,
  open            numeric,
  high            numeric,
  low             numeric,
  volume          bigint,
  avg_volume_30d  bigint,
  gap_pct         numeric,       -- (open - prev_close) / prev_close * 100
  relative_volume numeric        -- volume / avg_volume_30d
);

CREATE INDEX idx_market_snapshots_ticker_time
  ON public.market_snapshots (ticker, snapshot_time DESC);


-- ── 3. sec_filings ────────────────────────────────────────────────────────────
--    SEC EDGAR filing metadata. Full text downloaded separately.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.sec_filings (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker       text,
  cik          text,
  form_type    text,            -- '8-K', '10-Q', '10-K', 'Form 4', etc.
  filing_date  timestamptz,
  filing_url   text UNIQUE,
  description  text,
  fetched_at   timestamptz DEFAULT now()
);

CREATE INDEX idx_sec_filings_ticker_date
  ON public.sec_filings (ticker, filing_date DESC);


-- ── 4. social_mentions ────────────────────────────────────────────────────────
--    Aggregated daily mention counts from Reddit, StockTwits, etc.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.social_mentions (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker          text    NOT NULL,
  source          text,           -- 'reddit', 'stocktwits', etc.
  mention_count   int     DEFAULT 1,
  sentiment_score numeric,
  snapshot_date   date    DEFAULT current_date,
  UNIQUE (ticker, source, snapshot_date)
);

CREATE INDEX idx_social_mentions_ticker_date
  ON public.social_mentions (ticker, snapshot_date DESC);


-- ── 5. ranked_focus_list ──────────────────────────────────────────────────────
--    Output of the ranking pipeline. thesis filled by LLM agent post-run.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ranked_focus_list (
  id              bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_date        date    NOT NULL,
  run_type        text    NOT NULL,   -- 'premarket', 'midday', 'close'
  ticker          text    NOT NULL,
  rank            int     NOT NULL,
  composite_score numeric NOT NULL,
  thesis          text,              -- LLM-generated after ranking
  signals         jsonb              -- { "gap_pct": 4.2, "rel_vol": 3.1, ... }
);

CREATE INDEX idx_ranked_focus_list_run
  ON public.ranked_focus_list (run_date, run_type, rank);


-- ── 6. reports ────────────────────────────────────────────────────────────────
--    Generated advisory reports delivered to users via email.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.reports (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid REFERENCES auth.users,
  report_type      text,           -- 'premarket', 'midday', 'close'
  report_date      date,
  content_html     text,
  content_markdown text,
  delivered_email  boolean     DEFAULT false,
  created_at       timestamptz DEFAULT now()
);

CREATE INDEX idx_reports_user_date_type
  ON public.reports (user_id, report_date, report_type);


-- ── 7. portfolios ─────────────────────────────────────────────────────────────
--    User-owned watchlists / portfolios.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.portfolios (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid REFERENCES auth.users NOT NULL,
  name       text        NOT NULL,
  created_at timestamptz DEFAULT now()
);


-- ── 8. portfolio_holdings ─────────────────────────────────────────────────────
--    Individual positions inside a portfolio.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.portfolio_holdings (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  portfolio_id uuid    REFERENCES public.portfolios NOT NULL,
  ticker       text    NOT NULL,
  shares       numeric NOT NULL,
  cost_basis   numeric,
  added_at     timestamptz DEFAULT now()
);


-- ═════════════════════════════════════════════════════════════════════════════
-- Row-Level Security
-- ═════════════════════════════════════════════════════════════════════════════
--
-- Shared tables (news, prices, filings, social, rankings):
--   - RLS enabled; authenticated users can SELECT.
--   - Pipeline writes via the service_role key, which bypasses RLS entirely.
--   - Anonymous / unauthenticated callers are blocked.
--
-- User-owned tables (reports, portfolios, holdings):
--   - Users can only SELECT / INSERT / UPDATE / DELETE their own rows.
-- ─────────────────────────────────────────────────────────────────────────────

-- Shared read-only tables
ALTER TABLE public.news_items        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sec_filings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.social_mentions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ranked_focus_list ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.news_items
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.market_snapshots
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.sec_filings
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.social_mentions
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "authenticated_read" ON public.ranked_focus_list
  FOR SELECT TO authenticated USING (true);

-- reports
ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "owner_all" ON public.reports
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- portfolios
ALTER TABLE public.portfolios ENABLE ROW LEVEL SECURITY;

CREATE POLICY "owner_all" ON public.portfolios
  USING     (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- portfolio_holdings (ownership derived via portfolios join)
ALTER TABLE public.portfolio_holdings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "owner_all" ON public.portfolio_holdings
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
