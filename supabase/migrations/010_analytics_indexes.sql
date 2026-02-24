-- Migration 010: Analytics indexes
--
-- These indexes are purpose-built for the query patterns expected on this schema:
-- cross-profile market comparison, event-level aggregation, P&L analysis,
-- direction breakdowns (YES/NO × BUY/SELL), and win/loss join performance.
--
-- All five are additions — nothing here overlaps with existing indexes.

-- Index 1: Market-centric cross-profile comparison
-- Use: WHERE condition_id = $1 GROUP BY profile_id, outcome, side
-- "Show me every profile who traded this market and what they did."
CREATE INDEX idx_activity_market_profile
  ON copy_pros.activity (condition_id, profile_id);

-- Index 2: Event-level analytics (event_slug currently unindexed)
-- Use: WHERE event_slug = $1 [AND profile_id = $2]
-- "How did each profile trade everything within event Y (across all sub-markets)?"
CREATE INDEX idx_activity_event_profile
  ON copy_pros.activity (event_slug, profile_id);

-- Index 3: Covering index — profile-first P&L and position summary
-- Use: WHERE profile_id = $1 GROUP BY condition_id, outcome, side
-- "For profile X, per market: total bet, avg entry price, direction."
-- INCLUDE enables index-only scan — no heap fetch needed for aggregation.
CREATE INDEX idx_activity_profile_market_covering
  ON copy_pros.activity (profile_id, condition_id)
  INCLUDE (outcome, side, usdc_amount, shares, price);

-- Index 4: Covering index — market-first direction analytics
-- Use: WHERE condition_id = $1 GROUP BY outcome, side
-- "On this market, what was the $ volume split by YES/NO × BUY/SELL?"
-- Also serves: "which markets had the most YES buying?" (scan + group by condition_id, outcome)
CREATE INDEX idx_activity_market_direction_covering
  ON copy_pros.activity (condition_id, outcome, side)
  INCLUDE (usdc_amount, shares, price);

-- Index 5: Partial covering index — resolved markets only, for win/loss JOINs
-- Use: JOIN activity ON activity.condition_id = market_outcomes.condition_id WHERE is_resolved = TRUE
-- Stores winning_outcome and resolved_at in the index itself → index-only scan on the JOIN side.
-- Partial (is_resolved = TRUE) keeps it small as unresolved markets accumulate.
CREATE INDEX idx_market_outcomes_resolved_covering
  ON copy_pros.market_outcomes (condition_id)
  INCLUDE (winning_outcome, resolved_at)
  WHERE is_resolved = TRUE;
