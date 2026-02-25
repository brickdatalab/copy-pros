-- Migration 011: Market data tables
--
-- Stores Polymarket event/market metadata for registered events.
-- All market data lives exclusively in copy_pros schema.
--
-- Hierarchy: events → markets (one event has one or many YES/NO markets)
-- Events are registered on-demand; nothing is tracked unless explicitly registered.

-- ── events ────────────────────────────────────────────────────────────────────
-- One row per registered Polymarket event.
CREATE TABLE copy_pros.events (
  id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  event_slug   text        UNIQUE NOT NULL,
  title        text,
  category     text,
  is_active    boolean     NOT NULL DEFAULT true,
  resolved_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- ── markets ───────────────────────────────────────────────────────────────────
-- One row per YES/NO binary market within a registered event.
-- Neg-risk events have multiple markets under one event.
CREATE TABLE copy_pros.markets (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id        uuid        NOT NULL REFERENCES copy_pros.events(id) ON DELETE CASCADE,
  condition_id    text        UNIQUE NOT NULL,
  question        text,
  token_id_yes    text        NOT NULL,
  token_id_no     text        NOT NULL,
  is_neg_risk     boolean     NOT NULL DEFAULT false,
  is_resolved     boolean     NOT NULL DEFAULT false,
  winning_outcome text        CHECK (winning_outcome IN ('YES', 'NO')),
  resolved_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_markets_event_id    ON copy_pros.markets(event_id);
CREATE INDEX idx_markets_active      ON copy_pros.markets(event_id) WHERE is_resolved = false;
CREATE INDEX idx_markets_token_yes   ON copy_pros.markets(token_id_yes);
CREATE INDEX idx_markets_token_no    ON copy_pros.markets(token_id_no);
