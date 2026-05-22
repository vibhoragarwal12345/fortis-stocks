-- ============================================================
-- 037_form4_transactions.sql
-- Form 4 transaction-code data integrity fix.
--
-- The 10b5-1 spot test surfaced that downstream agents (anomaly_detector,
-- risk_flagger, catalyst_agent) could only see Form 4 *filing counts* -- they
-- read sec_filings, which is one row per filing and has no trade direction.
-- A wave of compensation grants, GSU vestings, or charitable gifts looked
-- identical to a wave of real open-market selling.
--
-- This table stores Form 4 data at the TRANSACTION level (a single filing can
-- contain a purchase, a tax-withholding, and a derivative exercise, each with
-- a different SEC transaction code). The is_directional_signal column is the
-- filter the rest of the system uses.
--
-- Spec named this 030_form4_transaction_codes.sql, but 030 is already taken
-- (030_backtest_picks.sql); using 037 as the next free slot. The spec also
-- said to ALTER sec_filings, but that table is one row per filing and cannot
-- hold per-transaction codes -- hence this dedicated table.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.form4_transactions (
  id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ticker                  text NOT NULL,
  cik                     text,
  accession               text NOT NULL,
  txn_index               int  NOT NULL,        -- ordinal of the transaction within the filing

  insider                 text,
  role                    text,                 -- CEO | CFO | COO | Director | 10% Owner | Other Officer | Other

  transaction_code        text,                 -- single-letter SEC Form 4 code (P, S, A, F, G, M, V, ...)
  transaction_subcode     text,                 -- reserved for compound codings when present
  is_acquisition          boolean,              -- TRUE if shares acquired (A), FALSE if disposed (D)
  is_10b5_1               boolean DEFAULT false,
  price_per_share         numeric,
  shares                  numeric,
  transaction_value       numeric,              -- shares * price_per_share
  transaction_date        date,

  -- The integrity filter. TRUE only for transactions that reflect a real,
  -- discretionary view: P (open-market buy), S (open-market sale) that is NOT
  -- a scheduled 10b5-1 trade, and V (voluntary transaction not under 10b5-1).
  -- Everything else (A grants, F tax withholding, G gifts, M/X derivative
  -- exercises, etc.) is mechanical or non-directional and is excluded.
  is_directional_signal   boolean DEFAULT false,

  footnote                text,
  fetched_at              timestamptz DEFAULT now(),

  UNIQUE (accession, txn_index)
);

CREATE INDEX IF NOT EXISTS idx_form4_txns_ticker
  ON public.form4_transactions (ticker, transaction_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_form4_txns_directional
  ON public.form4_transactions (ticker, transaction_date DESC)
  WHERE is_directional_signal = true;
CREATE INDEX IF NOT EXISTS idx_form4_txns_accession
  ON public.form4_transactions (accession);

ALTER TABLE public.form4_transactions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "authenticated_read" ON public.form4_transactions;
CREATE POLICY "authenticated_read" ON public.form4_transactions
  FOR SELECT TO authenticated USING (true);

COMMENT ON TABLE public.form4_transactions IS
  'Form 4 insider transactions at the transaction level. is_directional_signal '
  'is the filter used by anomaly_detector / risk_flagger / catalyst_agent so '
  'that grants, vestings and gifts no longer masquerade as insider sentiment.';
COMMENT ON COLUMN public.form4_transactions.is_directional_signal IS
  'TRUE only for code P, code S that is not 10b5-1, and code V. All mechanical '
  'or non-discretionary codes are FALSE.';

-- ── pick_outcomes: backtest-integrity flag ──────────────────────────────────
-- Picks generated before the Form 4 fix may carry corrupted insider signals
-- (gifts/vestings counted as sells). This flag lets the backtest engine
-- segment or exclude pre-fix picks. Existing rows keep the default (false);
-- outcome_tracker stamps new picks true going forward.
ALTER TABLE public.pick_outcomes
  ADD COLUMN IF NOT EXISTS signal_quality_after_form4_fix boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.pick_outcomes.signal_quality_after_form4_fix IS
  'TRUE for picks generated after the Form 4 transaction-code fix was '
  'deployed. FALSE picks may have insider signals corrupted by grants/'
  'vestings/gifts being miscounted as directional trades.';
