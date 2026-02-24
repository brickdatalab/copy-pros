-- Migration 002: Create profiles, activity, market_outcomes tables

-- ─────────────────────────────────────────────
-- profiles
-- ─────────────────────────────────────────────
CREATE TABLE copy_pros.profiles (
  id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  proxy_wallet_address TEXT        NOT NULL UNIQUE,
  display_name         TEXT,
  is_active            BOOLEAN     NOT NULL DEFAULT TRUE,
  tracking_start_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_polled_at       TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  copy_pros.profiles IS
  'Polymarket proxy wallets to track. INSERT a row to begin autonomous activity collection.';
COMMENT ON COLUMN copy_pros.profiles.proxy_wallet_address IS
  'Lowercase 0x-prefixed Ethereum proxy wallet address (Polymarket proxy).';
COMMENT ON COLUMN copy_pros.profiles.tracking_start_at IS
  'Epoch start — only trades at or after this timestamp are ingested. No backfill.';
COMMENT ON COLUMN copy_pros.profiles.last_polled_at IS
  'Updated after each successful CLOB API poll. Used as the "after" cursor on next poll.';

-- ─────────────────────────────────────────────
-- activity
-- ─────────────────────────────────────────────
CREATE TABLE copy_pros.activity (
  id               BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  profile_id       UUID         NOT NULL REFERENCES copy_pros.profiles(id) ON DELETE CASCADE,
  trade_id         TEXT         NOT NULL UNIQUE,
  condition_id     TEXT         NOT NULL,
  market_slug      TEXT,
  event_slug       TEXT,
  question         TEXT,
  outcome          TEXT         NOT NULL CHECK (outcome IN ('YES', 'NO')),
  side             TEXT         NOT NULL CHECK (side IN ('BUY', 'SELL')),
  price            NUMERIC(18,6) NOT NULL,
  shares           NUMERIC(18,6) NOT NULL,
  usdc_amount      NUMERIC(18,6) NOT NULL,
  transaction_hash TEXT,
  event_timestamp  TIMESTAMPTZ  NOT NULL,
  inserted_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  copy_pros.activity IS
  'Every trade made by tracked profiles. Append-only; deduplicated by trade_id from the Polymarket CLOB.';
COMMENT ON COLUMN copy_pros.activity.trade_id IS
  'Polymarket CLOB trade hash — used as the deduplication key on UPSERT.';
COMMENT ON COLUMN copy_pros.activity.condition_id IS
  'Polymarket market condition ID. Joins to market_outcomes.condition_id.';
COMMENT ON COLUMN copy_pros.activity.outcome IS
  'Which side of the market the trade is on: YES or NO.';
COMMENT ON COLUMN copy_pros.activity.side IS
  'BUY = opening/adding position. SELL = closing/reducing position.';
COMMENT ON COLUMN copy_pros.activity.price IS
  'Price per share in USDC (0–1 range, representing implied probability).';
COMMENT ON COLUMN copy_pros.activity.shares IS
  'Number of conditional token shares traded.';
COMMENT ON COLUMN copy_pros.activity.usdc_amount IS
  'Total USDC value of the trade = price × shares.';

-- ─────────────────────────────────────────────
-- market_outcomes
-- ─────────────────────────────────────────────
CREATE TABLE copy_pros.market_outcomes (
  id              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  condition_id    TEXT         NOT NULL UNIQUE,
  market_slug     TEXT,
  event_slug      TEXT,
  question        TEXT,
  winning_outcome TEXT         CHECK (winning_outcome IN ('YES', 'NO')),
  is_resolved     BOOLEAN      NOT NULL DEFAULT FALSE,
  resolved_at     TIMESTAMPTZ,
  last_checked_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

COMMENT ON TABLE  copy_pros.market_outcomes IS
  'One row per unique market seen in activity. Outcome is populated by the resolve-outcomes cron at :02/:17/:32/:47.';
COMMENT ON COLUMN copy_pros.market_outcomes.condition_id IS
  'Natural key — Polymarket market condition ID. Matches activity.condition_id.';
COMMENT ON COLUMN copy_pros.market_outcomes.winning_outcome IS
  'NULL until the market resolves. Then YES or NO (the winning side).';
COMMENT ON COLUMN copy_pros.market_outcomes.last_checked_at IS
  'Timestamp of the most recent Gamma API check for this market''s resolution status.';
