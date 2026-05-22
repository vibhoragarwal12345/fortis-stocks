-- ============================================================
-- 005_extended_market_snapshots.sql
-- Extends market_snapshots with the full technical-analysis output
-- produced by the upgraded pipeline/agents/market_data.py
--
-- Numbered 005 (the next sequential migration after 004_options_signals).
-- The original request said 006, but 001-004 are the only prior
-- migrations -- a numbering gap would break ordered migration runners,
-- so this follows the same renumbering convention used by 002-004.
--
-- Each new column is a jsonb blob grouping one family of indicators.
-- The base columns from 001 (price, OHLCV, gap_pct, relative_volume)
-- are left untouched -- this migration is purely additive.
-- ============================================================

ALTER TABLE public.market_snapshots
  ADD COLUMN IF NOT EXISTS price_levels        jsonb,  -- 52w/200d/20d highs & lows, distances
  ADD COLUMN IF NOT EXISTS moving_averages     jsonb,  -- SMA/EMA set, price-vs-MA, golden/death cross
  ADD COLUMN IF NOT EXISTS momentum            jsonb,  -- RSI, MACD, Stochastic
  ADD COLUMN IF NOT EXISTS volatility          jsonb,  -- Bollinger Bands, ATR, realized vol
  ADD COLUMN IF NOT EXISTS volume_analysis     jsonb,  -- avg vol, rel vol, OBV & A/D trends
  ADD COLUMN IF NOT EXISTS patterns            jsonb,  -- breakout/breakdown, trend, gap/inside/outside
  ADD COLUMN IF NOT EXISTS support_resistance  jsonb,  -- swing highs/lows, nearest S/R
  ADD COLUMN IF NOT EXISTS relative_strength   jsonb;  -- performance vs SPY, RS percentile

COMMENT ON COLUMN public.market_snapshots.price_levels IS
  '52-week / 200-day / 20-day highs & lows and current distance from them';
COMMENT ON COLUMN public.market_snapshots.moving_averages IS
  'SMA 10/20/50/100/200, EMA 9/21/50/200, price-vs-MA, golden/death cross flags';
COMMENT ON COLUMN public.market_snapshots.momentum IS
  'RSI(14) + state, MACD line/signal/histogram/crossover, Stochastic(14,3,3)';
COMMENT ON COLUMN public.market_snapshots.volatility IS
  'Bollinger Bands(20,2) + %B, ATR(14) abs & %, 30-day annualized realized vol';
COMMENT ON COLUMN public.market_snapshots.volume_analysis IS
  '30-day avg volume, relative volume, 20-day OBV and A/D line trend';
COMMENT ON COLUMN public.market_snapshots.patterns IS
  'breakout/breakdown, up/down trend, inside/outside day, gap up/down';
COMMENT ON COLUMN public.market_snapshots.support_resistance IS
  'last 3 swing highs (resistance) & lows (support), nearest S/R to price';
COMMENT ON COLUMN public.market_snapshots.relative_strength IS
  'performance vs SPY over 1/5/20/60d, RS percentile within the scanned set';
