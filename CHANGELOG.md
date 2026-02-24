# Changelog

All notable changes to `copy_pros` are documented here.

---

## [b1f6387] — 2026-02-24 — feat: analytics indexes + comprehensive README

### Added
- **Migration 010** — Five targeted indexes for analytics query patterns:
  - `idx_activity_market_profile` — cross-profile comparison queries per market
  - `idx_activity_event_profile` — event-level aggregation (event_slug was previously unindexed)
  - `idx_activity_profile_market_covering` — covering index for P&L queries, profile-first scan
  - `idx_activity_market_direction_covering` — covering index for direction analytics, market-first scan
  - `idx_market_outcomes_resolved_covering` — partial covering index for win/loss JOIN patterns (WHERE is_resolved = true)
- **README.md** — Full production documentation covering schema design, all migrations, edge functions, indexes, example analytics queries, known constraints, and current system state

---

## [378c26d] — 2026-02-24 — chore: increase fetch-activity cron to every minute

### Changed
- **Migration 006** (updated) — Rescheduled `fetch-activity` cron from `*/2 * * * *` (every 2 min) to `* * * * *` (every minute). `resolve-outcomes` schedule unchanged (`:02/:17/:32/:47`).

---

## [c7df85f] — 2026-02-24 — fix: expose copy_pros schema to PostgREST

### Fixed
- **Migration 009** — Added `ALTER ROLE authenticator SET pgrst.db_schemas TO 'public,copy_pros'`. Without this, Edge Functions using `db: { schema: 'copy_pros' }` received `Invalid schema` / schema cache miss errors from PostgREST, silently preventing all table reads and writes.

---

## [bca4691] — 2026-02-24 — feat: initial copy_pros schema

Initial implementation of the autonomous Polymarket copy-trading activity tracker.

### Added

**Migrations (001–008)**
- `001_rename_schema.sql` — Renames the existing public working schema to `copy_pros`
- `002_create_tables.sql` — Core tables: `profiles`, `activity`, `market_outcomes`, `config`
  - `profiles`: tracked wallets with `proxy_wallet_address`, `tracking_start_at`, `last_polled_at`, `is_active`
  - `activity`: per-trade rows with `trade_id` (unique), condition/market/event metadata, outcome, side, price, shares, usdc_amount, transaction_hash, event_timestamp
  - `market_outcomes`: one row per `condition_id`, tracks resolution state and winning outcome
  - `config`: key/value store for runtime config (used to pass `supabase_url` and `service_role_key` to pg_cron without `ALTER DATABASE SET`, which requires superuser)
- `003_indexes.sql` — Base indexes on `activity(profile_id)`, `activity(condition_id)`, `activity(event_timestamp)`, `market_outcomes(condition_id)`
- `004_rls.sql` — Row-level security policies on all tables; service role bypasses RLS
- `005_triggers.sql` — `AFTER INSERT ON profiles` trigger fires `fetch-activity` immediately via `pg_net` for a new profile, seeding historical trades without waiting for the next cron tick
- `006_cron_jobs.sql` — pg_cron scheduled jobs: `fetch-activity` every 2 min, `resolve-outcomes` at `:02/:17/:32/:47`; both use `net.http_post` reading URL and key from `copy_pros.config`
- `007_expose_api.sql` — Grants PostgREST access to `copy_pros` tables for the Edge Function client
- `008_config_table.sql` — Seeds `copy_pros.config` with `supabase_url` and `service_role_key`; workaround for the Supabase superuser restriction on `ALTER DATABASE SET`

**Edge Functions**
- `fetch-activity/index.ts` — Main polling function: loads active profiles, determines `since` timestamp (`last_polled_at ?? tracking_start_at`), fetches trades from Polymarket CLOB, enriches with Gamma market metadata, upserts into `activity` and seeds `market_outcomes`, advances `last_polled_at`
- `resolve-outcomes/index.ts` — Checks unresolved markets in `market_outcomes` against Gamma API, updates `is_resolved`, `winning_outcome`, `resolved_at`

**Shared library (`_shared/`)**
- `polymarket.ts` — Polymarket CLOB + Gamma API client: L2 HMAC-SHA256 auth, paginated trade fetcher, Gamma market detail batch fetch, timestamp parser, outcome resolver
- `supabase.ts` — Admin Supabase client factory scoped to `copy_pros` schema

**Scripts**
- `scripts/add-profile.ts` — Deno CLI to insert a new profile row and trigger immediate history backfill
- `scripts/check-activity.ts` — Deno CLI to query and display current activity table state

**Config**
- `.env.example` — Documents required environment variables
- `.gitignore` — Excludes `.env`, `node_modules`, Supabase local state
- `supabase/config.toml` — Supabase project config
- `PLAN.md` — Original implementation plan
