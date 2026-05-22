-- ============================================================
-- 016_garch_iv_metadata.sql
-- Adds implied-volatility provenance columns to garch_forecasts.
--
-- Numbered 016 (the next sequential migration after 015). The original
-- request said 022 -- renumbered to the real sequence, same convention
-- as every prior migration.
--
-- Implied volatility is now computed by inverting Black-Scholes on real
-- bid/ask option prices (pipeline/quant/utils.py), NOT read from yfinance's
-- unreliable `impliedVolatility` field. These columns record how each IV
-- was derived so every comparison is auditable.
-- ============================================================

ALTER TABLE public.garch_forecasts
  ADD COLUMN IF NOT EXISTS iv_source         text,    -- computed_from_bid_ask | low_quality | unavailable
  ADD COLUMN IF NOT EXISTS iv_strike_used    numeric, -- ATM strike the IV was solved at
  ADD COLUMN IF NOT EXISTS iv_days_to_expiry int,     -- DTE of the expiration used
  ADD COLUMN IF NOT EXISTS iv_parity_spread  numeric, -- |call IV - put IV|, a quality indicator
  ADD COLUMN IF NOT EXISTS iv_quality        text;    -- high | medium | low | failed

COMMENT ON COLUMN public.garch_forecasts.iv_parity_spread IS
  'Absolute difference between the ATM call IV and put IV. Put-call parity '
  'implies these should be near-equal; a wide spread flags a stale quote.';
