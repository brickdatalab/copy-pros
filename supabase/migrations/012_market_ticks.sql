-- Migration 012: market_ticks — raw WebSocket tick data
--
-- Append-only. Every message from the Polymarket CLOB WebSocket
-- (wss://ws-subscriptions-clob.polymarket.com/ws/market) lands here.
-- One row per asset_id (YES token or NO token) per message.
--
-- Purged automatically 48h after market resolution (see migration 014).

CREATE TABLE copy_pros.market_ticks (
  id           bigserial   PRIMARY KEY,
  market_id    uuid        NOT NULL REFERENCES copy_pros.markets(id) ON DELETE CASCADE,
  asset_id     text        NOT NULL,       -- token_id_yes or token_id_no
  token_side   text        NOT NULL CHECK (token_side IN ('YES', 'NO')),
  created_at   timestamptz NOT NULL DEFAULT now(),

  -- tick type from WebSocket event_type field
  tick_type    text        NOT NULL CHECK (tick_type IN ('book', 'price_change', 'last_trade')),

  -- orderbook state at this tick (null for last_trade ticks)
  best_bid     numeric(10,6),
  best_ask     numeric(10,6),
  bid_size     numeric(18,4),   -- total depth at best bid
  ask_size     numeric(18,4),   -- total depth at best ask
  bid_depth    numeric(18,4),   -- total bid-side liquidity
  ask_depth    numeric(18,4),   -- total ask-side liquidity
  spread       numeric(10,6),

  -- trade execution (null unless tick_type = 'last_trade')
  trade_price  numeric(10,6),
  trade_size   numeric(18,4),

  -- full raw payload for auditability
  raw          jsonb        NOT NULL
);

-- Primary query pattern: recent ticks for a market, ordered by time
CREATE INDEX idx_ticks_market_time  ON copy_pros.market_ticks(market_id, created_at DESC);
-- Secondary: ticks by asset_id (YES or NO token)
CREATE INDEX idx_ticks_asset_time   ON copy_pros.market_ticks(asset_id, created_at DESC);
-- For purge job efficiency
CREATE INDEX idx_ticks_market_only  ON copy_pros.market_ticks(market_id);
