-- ============================================================
-- 026_peer_sets.sql
-- Peer sets produced by pipeline/agents/peer_definition.py
--
-- Numbered 026 (the next sequential migration after 025). The original
-- request said 023, but 023 (portfolio_fit) and 024 (holdings_alerts) are
-- already applied -- so this follows the real sequence, same renumbering
-- convention as every prior migration.
--
-- One row per (target_ticker, snapshot_date, run_type). The five-method
-- peer construction (GICS / FMP / market-cap / correlation / LLM business
-- model) is summarized in `peers` as an array of {ticker, score,
-- methods_matched: [...]} objects; the top 8 by score are the official set.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.peer_sets (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  target_ticker      text NOT NULL,
  snapshot_date      date NOT NULL DEFAULT current_date,
  run_type           text NOT NULL DEFAULT 'midday',
  peers              jsonb DEFAULT '[]'::jsonb,    -- [{ticker, score, methods_matched: ['gics','fmp','marketcap','correlation','llm']}]
  peer_count         integer DEFAULT 0,
  gics_industry      text,
  gics_sub_industry  text,
  peer_set_quality   text,                          -- high | medium | low
  validation_notes   text,
  scoring_breakdown  jsonb DEFAULT '{}'::jsonb,     -- per-method intermediate lists for audit
  created_at         timestamptz DEFAULT now(),
  UNIQUE (target_ticker, snapshot_date, run_type)
);

CREATE INDEX IF NOT EXISTS idx_peer_sets_date
  ON public.peer_sets (snapshot_date DESC, target_ticker);

ALTER TABLE public.peer_sets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "authenticated_read" ON public.peer_sets
  FOR SELECT TO authenticated USING (true);
