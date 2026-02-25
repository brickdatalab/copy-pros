-- Migration 013: market_snapshots + indicators
--
-- market_snapshots: 1-second aggregated view of a market.
--   Written by the local stream script every second per active market.
--   Combines YES and NO token orderbook state into a single market view.
--
-- indicators: technical indicators derived from snapshots.
--   Written alongside snapshots. Feeds the real-time trading system.
--
-- Both tables purged 48h after market resolution (see migration 014).

-- ── market_snapshots ──────────────────────────────────────────────────────────
CREATE TABLE copy_pros.market_snapshots (
  id               bigserial    PRIMARY KEY,
  market_id        uuid         NOT NULL REFERENCES copy_pros.markets(id) ON DELETE CASCADE,
  ts               timestamptz  NOT NULL,

  -- YES token orderbook
  yes_price        numeric(10,6),   -- mid price of YES token
  yes_bid          numeric(10,6),   -- best bid
  yes_ask          numeric(10,6),   -- best ask
  yes_spread       numeric(10,6),
  yes_bid_depth    numeric(18,4),
  yes_ask_depth    numeric(18,4),

  -- NO token orderbook
  no_price         numeric(10,6),
  no_bid           numeric(10,6),
  no_ask           numeric(10,6),
  no_spread        numeric(10,6),
  no_bid_depth     numeric(18,4),
  no_ask_depth     numeric(18,4),

  -- rolling trade windows (YES token — primary signal)
  vwap_1m          numeric(10,6),
  vwap_5m          numeric(10,6),
  volume_1m        numeric(18,4),
  volume_5m        numeric(18,4),

  -- order book imbalance per side: (bid_depth - ask_depth) / (bid_depth + ask_depth)
  -- range [-1, 1]; positive = more buy pressure
  imbalance_yes    numeric(8,6),
  imbalance_no     numeric(8,6),

  UNIQUE (market_id, ts)
);

CREATE INDEX idx_snapshots_market_time ON copy_pros.market_snapshots(market_id, ts DESC);
CREATE INDEX idx_snapshots_market_only ON copy_pros.market_snapshots(market_id);

-- ── indicators ────────────────────────────────────────────────────────────────
CREATE TABLE copy_pros.indicators (
  id                    bigserial    PRIMARY KEY,
  market_id             uuid         NOT NULL REFERENCES copy_pros.markets(id) ON DELETE CASCADE,
  ts                    timestamptz  NOT NULL,
  computed_at           timestamptz  NOT NULL DEFAULT now(),

  -- VWAP (YES token, rolling windows)
  vwap_1m               numeric(10,6),
  vwap_5m               numeric(10,6),

  -- Order book imbalance (YES token)
  -- (bid_depth - ask_depth) / (bid_depth + ask_depth); range [-1, 1]
  order_imbalance       numeric(8,6),

  -- Spread momentum: rate of change of YES spread over last 30s
  -- (spread_now - spread_30s_ago) / spread_30s_ago
  -- negative = spread tightening (market becoming more liquid)
  spread_momentum       numeric(10,6),

  -- Mid-price momentum: rate of change of YES mid price
  mid_momentum_30s      numeric(10,6),
  mid_momentum_1m       numeric(10,6),
  mid_momentum_5m       numeric(10,6),

  -- Current mid price
  mid_price             numeric(10,6),

  -- Directional signal for trading system
  signal                text         CHECK (signal IN ('LONG', 'SHORT', 'NEUTRAL')),

  UNIQUE (market_id, ts)
);

CREATE INDEX idx_indicators_market_time ON copy_pros.indicators(market_id, ts DESC);
-- Latest indicator per market (what the trading system reads)
CREATE INDEX idx_indicators_market_only ON copy_pros.indicators(market_id);
