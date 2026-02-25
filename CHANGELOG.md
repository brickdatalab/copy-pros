# Changelog

All notable changes to `copy_pros` are documented here. Commits are listed newest-first.

---

## [1576373] — 2026-02-25 — feat: real-time Polymarket event streaming pipeline

Adds a complete second subsystem to `copy_pros`: on-demand registration of Polymarket events with live WebSocket orderbook streaming. Runs locally while the user is at their computer. Data purges automatically 48h after market resolution to keep tables clean.

**Why:** The existing system (Subsystem A) tracks profile activity passively. This adds Subsystem B: you register a specific event you care about, run a local script, and get real-time orderbook state, VWAP, order imbalance, spread momentum, and directional signals streaming directly into Supabase — suitable for feeding a live trading system.

### Added

**Migrations:**
- `011_market_data_tables.sql` — Creates `copy_pros.events` and `copy_pros.markets`. Events are the top-level anchor; markets are the individual YES/NO binary contracts within an event. Neg-risk events have multiple markets under one event. Indexed on event_id, token IDs (YES and NO), and partial index on active (unresolved) markets.
- `012_market_ticks.sql` — Append-only raw WebSocket tick table. One row per token per WebSocket message. Stores orderbook state (`best_bid`, `best_ask`, `bid_depth`, `ask_depth`, `spread`) and trade execution data (`trade_price`, `trade_size`) plus the full raw `jsonb` payload for auditability. Indexed on `(market_id, created_at DESC)` and `(asset_id, created_at DESC)`.
- `013_market_snapshots_indicators.sql` — Two tables: `market_snapshots` (1-second aggregated combined view of YES+NO orderbooks with VWAP and volume rolling windows) and `indicators` (VWAP 1m/5m, order imbalance, spread momentum, mid-price momentum at 30s/1m/5m, and a directional signal: LONG/SHORT/NEUTRAL). Both have `UNIQUE(market_id, ts)`.
- `014_purge_policy.sql` — pg_cron job (`purge-market-data`, every 4 hours) that deletes market_ticks, market_snapshots, and indicators for any market whose `resolved_at < NOW() - INTERVAL '48 hours'`. Events and markets metadata rows are retained. Fixed: nested dollar-quoting conflict resolved by using `$cron$...$body$` instead of `$$...$$`.

**Python scripts:**
- `scripts/register_event.py` — CLI tool to register a Polymarket event by slug or URL. Fetches event metadata from Gamma API, fetches token IDs for each sub-market from CLOB public endpoint (no auth), upserts into `copy_pros.events` + `copy_pros.markets` via asyncpg direct connection.
- `scripts/stream_market.py` — Core async streaming engine. Opens WebSocket to `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscribes to all token IDs for all active registered markets, maintains in-memory orderbook per token, and every 1 second flushes pending ticks + writes snapshots + indicators to Supabase. Uses asyncpg connection pool (bypasses PostgREST for speed). Key classes: `OrderBook`, `MarketState`, `MarketStreamer`.
- `scripts/requirements_stream.txt` — Python deps: `asyncpg>=0.29.0`, `websockets>=12.0`, `httpx>=0.27.0`, `python-dotenv>=1.0.0`.

**Config:**
- `.env.example` — Added `SUPABASE_DB_URL` (Session mode pooler URL for direct asyncpg connection) and `CLOB_ADDRESS` (wallet address, distinct from `CLOB_API_KEY` UUID).

---

## [b4c686b] — 2026-02-25 — docs: add CHANGELOG covering initial build (bca4691 → b1f6387)

**Why:** During debugging of the `fetch-activity` edge function, the session pivoted to understanding what had been built and why. The CHANGELOG was created to record the first four commits (bca4691 through b1f6387) before the credential issue was surfaced — establishing a clear record of working state up to the point the problem was discovered.

---

## [b1f6387] — 2026-02-24 — feat: analytics indexes + comprehensive README

### Added
- **Migration 010** — Five targeted covering indexes for analytics query patterns:
  - `idx_activity_market_profile` — cross-profile comparison queries per market
  - `idx_activity_event_profile` — event-level aggregation (event_slug was previously unindexed)
  - `idx_activity_profile_market_covering` — covering index for P&L queries, profile-first scan (index-only)
  - `idx_activity_market_direction_covering` — covering index for direction analytics, market-first scan (index-only)
  - `idx_market_outcomes_resolved_covering` — partial covering index for win/loss JOIN patterns (WHERE is_resolved = true, index-only on JOIN side)
- **README.md** — Production documentation covering schema, all migrations, edge functions, indexes, example analytics queries, known constraints, and system state

---

## [378c26d] — 2026-02-24 — chore: increase fetch-activity cron to every minute

### Changed
- **Migration 006** (updated) — Rescheduled `fetch-activity` cron from `*/2 * * * *` (every 2 min) to `* * * * *` (every minute). `resolve-outcomes` schedule unchanged.

---

## [c7df85f] — 2026-02-24 — fix: expose copy_pros schema to PostgREST

### Fixed
- **Migration 009** — Added `ALTER ROLE authenticator SET pgrst.db_schemas TO 'public,copy_pros'`. Without this, Edge Functions using `db: { schema: 'copy_pros' }` received `Invalid schema` errors from PostgREST, silently preventing all table reads and writes. Both `NOTIFY pgrst, 'reload config'` and `NOTIFY pgrst, 'reload schema'` were required for the cache to warm.

---

## [bca4691] — 2026-02-24 — feat: initial copy_pros schema

Initial implementation of the autonomous Polymarket activity tracker (Subsystem A). Insert a proxy wallet address into `copy_pros.profiles` and the system begins collecting every trade that wallet makes.

### Added

**Migrations 001–008:**
- `001_rename_schema.sql` — Renames `copy-pros` → `copy_pros`
- `002_create_tables.sql` — Core tables: `profiles`, `activity`, `market_outcomes`, `config`
  - `profiles`: tracked wallets with tracking window (`tracking_start_at`, `last_polled_at`) and pause toggle (`is_active`)
  - `activity`: per-trade rows with dedup key (`trade_id`), full market context, outcome, side, price, shares, usdc_amount, tx hash
  - `market_outcomes`: one row per `condition_id`, tracks resolution state and winning outcome
  - `config`: key/value store for supabase_url + service_role_key (workaround: Supabase's managed postgres role cannot use `ALTER DATABASE SET` which requires true superuser)
- `003_indexes.sql` — Base indexes on activity (profile_id, condition_id, event_timestamp, trade_id) and market_outcomes (condition_id, partial WHERE is_resolved=FALSE)
- `004_rls.sql` — RLS on all tables; anon/authenticated get SELECT only; service_role bypasses RLS
- `005_triggers.sql` — `AFTER INSERT ON profiles` → `pg_net.http_post` to fetch-activity; immediate first fetch without waiting for cron
- `006_cron_jobs.sql` — pg_cron: `fetch-activity` every 2 min, `resolve-outcomes` at `:02/:17/:32/:47`; both read URL and key from `copy_pros.config`
- `007_expose_api.sql` — GRANT USAGE + SELECT on copy_pros to anon, authenticated; DEFAULT PRIVILEGES for future tables
- `008_config_table.sql` — Seeds config with `supabase_url` and `service_role_key`; restricted to service_role

**Edge Functions:**
- `fetch-activity/index.ts` — Loads active profiles, determines `since` (`last_polled_at ?? tracking_start_at`), fetches from CLOB, enriches with Gamma, upserts activity + market_outcomes, advances last_polled_at
- `resolve-outcomes/index.ts` — Queries unresolved market_outcomes, calls Gamma API, sets winning_outcome/resolved_at when market closes

**Shared library (`_shared/`):**
- `polymarket.ts` — L2 HMAC-SHA256 auth verified against py-clob-client source; paginated trade fetcher with cursor; Gamma batch client; timestamp normalizer; outcome resolver
- `supabase.ts` — Admin Supabase client factory scoped to `copy_pros`

**Scripts:**
- `scripts/add-profile.ts` — Deno CLI: insert wallet, trigger immediate fetch
- `scripts/check-activity.ts` — Deno CLI: display recent activity for a wallet

**Config:**
- `.env.example`, `.gitignore`, `supabase/config.toml`
