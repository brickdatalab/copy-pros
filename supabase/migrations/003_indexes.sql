-- Migration 003: Indexes
-- Following Supabase/Postgres best practices:
--   • Composite indexes: equality columns first, range columns last
--   • Partial indexes for high-selectivity filter predicates (is_active, is_resolved)
--   • INCLUDE columns for covering index scans where beneficial

-- ── profiles ──────────────────────────────────
-- Case-insensitive unique lookup by wallet address
CREATE UNIQUE INDEX idx_profiles_wallet_lower
  ON copy_pros.profiles (lower(proxy_wallet_address));

-- Cron job filter: only active profiles
CREATE INDEX idx_profiles_is_active
  ON copy_pros.profiles (id)
  WHERE is_active = TRUE;

-- ── activity ──────────────────────────────────
-- FK lookup (Postgres does not auto-index FKs)
CREATE INDEX idx_activity_profile_id
  ON copy_pros.activity (profile_id);

-- Market-level lookups (join to market_outcomes)
CREATE INDEX idx_activity_condition_id
  ON copy_pros.activity (condition_id);

-- Per-profile chronological queries (most common read pattern)
CREATE INDEX idx_activity_profile_event_time
  ON copy_pros.activity (profile_id, event_timestamp DESC);

-- Global time-series queries
CREATE INDEX idx_activity_event_timestamp
  ON copy_pros.activity (event_timestamp DESC);

-- UPSERT dedup check (trade_id is UNIQUE, but explicit index aids planner)
-- Note: unique constraint already creates an index; this comment is informational only.

-- ── market_outcomes ───────────────────────────
-- The UNIQUE constraint on condition_id already creates an index.
-- Partial index for the cron job — only scans unresolved markets
CREATE INDEX idx_market_outcomes_unresolved
  ON copy_pros.market_outcomes (condition_id)
  WHERE is_resolved = FALSE;
