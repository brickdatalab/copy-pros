# copy-pros — Implementation Plan

**Project:** `brickdatalab/copy-pros`
**Supabase Project:** `poly` (ref: `cxvntzszdkyggjjenefn`, `us-east-2`)
**Schema:** `copy_pros` (renamed from `copy-pros`)
**Date:** 2026-02-24

---

## What We're Building

A fully autonomous, multi-profile Polymarket activity tracker. You drop a proxy wallet address into `copy_pros.profiles` and it automatically:

1. Starts collecting every trade that wallet makes on Polymarket in real time (via pg_cron + Edge Function → CLOB API)
2. Stores each trade with full context: market, event, direction (YES/NO + BUY/SELL), price, shares, USDC value
3. Runs a 15-minute cron that resolves outcomes for every unique market seen in activity

---

## Architecture Overview

```
profiles INSERT trigger
        │
        ▼
  net.http_post ──► Edge Function: fetch-activity
        │                  │
        │                  ▼
pg_cron (every 2min)  Polymarket CLOB API (/data/trades?maker_address=...)
                           │
                           ▼
                  copy_pros.activity (upsert by trade_id)
                           │
                           ▼
          pg_cron (:02/:17/:32/:47) ──► Edge Function: resolve-outcomes
                                              │
                                              ▼
                                   Gamma API (/markets?condition_id=...)
                                              │
                                              ▼
                                  copy_pros.market_outcomes (upsert)
```

---

## Repo Structure

```
copy-pros/
├── supabase/
│   ├── config.toml
│   ├── migrations/
│   │   ├── 001_rename_schema.sql           ← renames copy-pros → copy_pros
│   │   ├── 002_create_tables.sql            ← profiles, activity, market_outcomes
│   │   ├── 003_indexes.sql                  ← all indexes
│   │   ├── 004_rls.sql                      ← RLS + policies
│   │   ├── 005_triggers.sql                 ← profile insert trigger → net.http_post
│   │   ├── 006_cron_jobs.sql                ← pg_cron jobs
│   │   └── 007_expose_api.sql               ← GRANT USAGE + SELECT to anon/authenticated
│   └── functions/
│       ├── fetch-activity/
│       │   ├── index.ts                     ← polls CLOB API, upserts activity
│       │   └── _shared/
│       │       ├── polymarket.ts            ← CLOB API client + auth headers
│       │       └── supabase.ts              ← Supabase admin client helper
│       └── resolve-outcomes/
│           ├── index.ts                     ← finds unresolved markets, calls Gamma API
│           └── _shared/ (symlink/shared)
├── scripts/
│   ├── add-profile.ts                       ← CLI: deno run add-profile.ts <wallet>
│   └── check-activity.ts                    ← CLI: inspect activity for a wallet
├── .env.example
└── README.md
```

---

## Step 1 — Schema Rename

```sql
-- 001_rename_schema.sql
ALTER SCHEMA "copy-pros" RENAME TO copy_pros;
```

> **Note:** This is a single DDL statement. Existing objects automatically move to the new schema name.

---

## Step 2 — Tables

### 2a. `copy_pros.profiles`

```sql
-- 002_create_tables.sql (excerpt)
CREATE TABLE copy_pros.profiles (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  proxy_wallet_address TEXT        NOT NULL UNIQUE,
  display_name        TEXT,
  is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
  tracking_start_at   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- no backfill before this
  last_polled_at      TIMESTAMPTZ,                         -- updated after each successful poll
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE copy_pros.profiles IS 'Polymarket proxy wallets to track. Insert a row to begin autonomous activity tracking.';
COMMENT ON COLUMN copy_pros.profiles.proxy_wallet_address IS 'Lowercase 0x-prefixed Ethereum proxy wallet address';
COMMENT ON COLUMN copy_pros.profiles.tracking_start_at IS 'Epoch start: only trades at or after this timestamp are collected';
COMMENT ON COLUMN copy_pros.profiles.last_polled_at IS 'Timestamp of the most recent successful CLOB API poll';
```

### 2b. `copy_pros.activity`

```sql
CREATE TABLE copy_pros.activity (
  id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  profile_id        UUID        NOT NULL REFERENCES copy_pros.profiles(id) ON DELETE CASCADE,
  trade_id          TEXT        NOT NULL UNIQUE,            -- Polymarket CLOB trade ID (dedup key)
  condition_id      TEXT        NOT NULL,                   -- Polymarket market condition ID
  market_slug       TEXT,                                   -- market-level slug
  event_slug        TEXT,                                   -- parent event slug
  question          TEXT,                                   -- human-readable market question
  outcome           TEXT        NOT NULL CHECK (outcome IN ('YES', 'NO')),
  side              TEXT        NOT NULL CHECK (side IN ('BUY', 'SELL')),
  price             NUMERIC(18,6) NOT NULL,                 -- price per share (0–1 range)
  shares            NUMERIC(18,6) NOT NULL,                 -- number of shares/contracts
  usdc_amount       NUMERIC(18,6) NOT NULL,                 -- total USDC (price × shares)
  transaction_hash  TEXT,                                   -- on-chain tx hash
  event_timestamp   TIMESTAMPTZ NOT NULL,                   -- when the trade occurred (from CLOB)
  inserted_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE copy_pros.activity IS 'Every trade made by tracked profiles. Append-only, deduplicated by trade_id.';
COMMENT ON COLUMN copy_pros.activity.outcome IS 'Which side of the market: YES or NO';
COMMENT ON COLUMN copy_pros.activity.side IS 'Trade direction: BUY (opening/adding) or SELL (closing/reducing)';
COMMENT ON COLUMN copy_pros.activity.usdc_amount IS 'Total USDC value = price × shares';
```

### 2c. `copy_pros.market_outcomes`

```sql
CREATE TABLE copy_pros.market_outcomes (
  id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  condition_id      TEXT        NOT NULL UNIQUE,            -- natural key, matches activity.condition_id
  market_slug       TEXT,
  event_slug        TEXT,
  question          TEXT,
  winning_outcome   TEXT        CHECK (winning_outcome IN ('YES', 'NO')),  -- NULL until resolved
  is_resolved       BOOLEAN     NOT NULL DEFAULT FALSE,
  resolved_at       TIMESTAMPTZ,
  last_checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE copy_pros.market_outcomes IS 'One row per unique market seen in activity. Outcome populated by cron at :02/:17/:32/:47.';
COMMENT ON COLUMN copy_pros.market_outcomes.winning_outcome IS 'NULL until market resolves. Then YES or NO.';
```

---

## Step 3 — Indexes

```sql
-- 003_indexes.sql

-- profiles
CREATE UNIQUE INDEX idx_profiles_wallet
  ON copy_pros.profiles (lower(proxy_wallet_address));

CREATE INDEX idx_profiles_active
  ON copy_pros.profiles (is_active)
  WHERE is_active = TRUE;                               -- partial: only active profiles

-- activity
CREATE INDEX idx_activity_profile_id
  ON copy_pros.activity (profile_id);

CREATE INDEX idx_activity_condition_id
  ON copy_pros.activity (condition_id);

CREATE INDEX idx_activity_profile_event_time
  ON copy_pros.activity (profile_id, event_timestamp DESC);   -- composite for per-profile queries

CREATE INDEX idx_activity_event_timestamp
  ON copy_pros.activity (event_timestamp DESC);

CREATE INDEX idx_activity_trade_id
  ON copy_pros.activity (trade_id);                    -- supports fast UPSERT dedup check

-- market_outcomes
CREATE INDEX idx_market_outcomes_unresolved
  ON copy_pros.market_outcomes (condition_id)
  WHERE is_resolved = FALSE;                            -- partial: cron only scans unresolved
```

> **Why partial indexes:** The cron jobs filter constantly on `is_active = TRUE` and `is_resolved = FALSE`. Partial indexes are 5–20× smaller and faster than full indexes for these high-selectivity predicates (Supabase rule 1.5).

---

## Step 4 — Row Level Security

```sql
-- 004_rls.sql

-- Enable RLS on all tables
ALTER TABLE copy_pros.profiles         ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_pros.activity         ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_pros.market_outcomes  ENABLE ROW LEVEL SECURITY;

-- service_role bypasses RLS by design (Supabase default)
-- Anon/authenticated: read-only SELECT allowed (public read of this data is OK)

CREATE POLICY "anon_select_profiles"
  ON copy_pros.profiles FOR SELECT
  TO anon, authenticated
  USING (TRUE);

CREATE POLICY "anon_select_activity"
  ON copy_pros.activity FOR SELECT
  TO anon, authenticated
  USING (TRUE);

CREATE POLICY "anon_select_market_outcomes"
  ON copy_pros.market_outcomes FOR SELECT
  TO anon, authenticated
  USING (TRUE);

-- All writes go through service_role (Edge Functions), so no INSERT/UPDATE/DELETE policies needed
-- service_role bypasses RLS — no policy required for it
```

---

## Step 5 — Trigger: Profile Insert → Immediate Fetch

When a profile is inserted, we want an *immediate* first fetch rather than waiting up to 2 minutes for the cron cycle. This trigger fires a `net.http_post` to the `fetch-activity` Edge Function.

```sql
-- 005_triggers.sql

-- Requires: ALTER DATABASE postgres SET app.supabase_url = 'https://cxvntzszdkyggjjenefn.supabase.co';
--           ALTER DATABASE postgres SET app.service_role_key = '<key>';
-- (one-time setup, run manually or via migration with a DO block)

CREATE OR REPLACE FUNCTION copy_pros.trigger_on_profile_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = copy_pros, public
AS $$
DECLARE
  _url  TEXT;
  _key  TEXT;
BEGIN
  _url := current_setting('app.supabase_url', true);
  _key := current_setting('app.service_role_key', true);

  IF _url IS NULL OR _key IS NULL THEN
    RAISE WARNING 'copy_pros trigger: app.supabase_url or app.service_role_key not set — skipping immediate fetch';
    RETURN NEW;
  END IF;

  PERFORM net.http_post(
    url     := _url || '/functions/v1/fetch-activity',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'Authorization', 'Bearer ' || _key
    ),
    body    := jsonb_build_object(
      'trigger',     'profile_insert',
      'profile_id',  NEW.id::text
    )
  );

  RETURN NEW;
END;
$$;

CREATE TRIGGER after_profile_insert
  AFTER INSERT ON copy_pros.profiles
  FOR EACH ROW
  EXECUTE FUNCTION copy_pros.trigger_on_profile_insert();
```

**Critical note on security:** `app.service_role_key` is stored as a database-level setting. It is accessible to superusers/service_role. This is acceptable for a fully internal, service-owned system. Do **not** expose this to anon/authenticated roles.

---

## Step 6 — pg_cron Jobs

```sql
-- 006_cron_jobs.sql

-- Job 1: Poll CLOB API for new activity every 2 minutes
SELECT cron.schedule(
  'fetch-activity-every-2min',
  '*/2 * * * *',
  $$
    SELECT net.http_post(
      url     := current_setting('app.supabase_url') || '/functions/v1/fetch-activity',
      headers := jsonb_build_object(
        'Content-Type',  'application/json',
        'Authorization', 'Bearer ' || current_setting('app.service_role_key')
      ),
      body    := '{"trigger": "cron"}'::jsonb
    );
  $$
);

-- Job 2: Resolve outcomes at :02, :17, :32, :47 of every hour
SELECT cron.schedule(
  'resolve-outcomes-15min',
  '2,17,32,47 * * * *',
  $$
    SELECT net.http_post(
      url     := current_setting('app.supabase_url') || '/functions/v1/resolve-outcomes',
      headers := jsonb_build_object(
        'Content-Type',  'application/json',
        'Authorization', 'Bearer ' || current_setting('app.service_role_key')
      ),
      body    := '{"trigger": "cron"}'::jsonb
    );
  $$
);
```

---

## Step 7 — Expose Schema to PostgREST (Public API)

```sql
-- 007_expose_api.sql

-- Grant schema usage
GRANT USAGE ON SCHEMA copy_pros TO anon, authenticated;

-- Grant read access to all current tables
GRANT SELECT ON copy_pros.profiles        TO anon, authenticated;
GRANT SELECT ON copy_pros.activity        TO anon, authenticated;
GRANT SELECT ON copy_pros.market_outcomes TO anon, authenticated;

-- Grant read access to any future tables added to the schema
ALTER DEFAULT PRIVILEGES IN SCHEMA copy_pros
  GRANT SELECT ON TABLES TO anon, authenticated;
```

> **PostgREST configuration:** In Supabase Dashboard → Settings → API → "Extra search path", add `copy_pros`. This makes the schema visible to the auto-generated REST API.

---

## Step 8 — Edge Function: `fetch-activity`

**File:** `supabase/functions/fetch-activity/index.ts`

**Responsibilities:**
1. Accept invocation from trigger (single profile_id) OR cron (all active profiles)
2. For each target profile, fetch trades from Polymarket CLOB API since `last_polled_at` (or `tracking_start_at` if never polled)
3. For each trade returned, enrich with market/event slug from Gamma API (batch lookup)
4. UPSERT into `copy_pros.activity` (conflict on `trade_id` → DO NOTHING)
5. Update `profiles.last_polled_at` to now()
6. Upsert new condition_ids into `copy_pros.market_outcomes` as `is_resolved = false` (seeding for the outcomes cron)

**CLOB API endpoint:**
```
GET https://clob.polymarket.com/data/trades
  ?maker_address={proxy_wallet_address}
  &after={cursor_trade_id_or_timestamp}     ← pagination cursor
  &limit=500
```

**Auth headers (L2 — API key based):**
```typescript
// HMAC-SHA256 of timestamp:nonce:method:requestPath
const signature = await hmacSign(CLOB_SECRET, `${ts}:${nonce}:GET:/data/trades`);

headers: {
  'POLY_ADDRESS':    CLOB_API_KEY,
  'POLY_SIGNATURE':  signature,
  'POLY_TIMESTAMP':  ts,
  'POLY_NONCE':      nonce,
}
```

**Gamma API enrichment (batch):**
```
GET https://gamma-api.polymarket.com/markets?condition_id={id1}&condition_id={id2}&...
```
Returns market slugs, event slugs, question text. Cached per invocation to avoid re-fetching the same market metadata on every trade.

---

## Step 9 — Edge Function: `resolve-outcomes`

**File:** `supabase/functions/resolve-outcomes/index.ts`

**Responsibilities:**
1. Query: `SELECT DISTINCT condition_id FROM copy_pros.activity WHERE condition_id NOT IN (SELECT condition_id FROM copy_pros.market_outcomes WHERE is_resolved = TRUE)`
2. For each unresolved condition_id, call Gamma API
3. If `resolved = true` in response: update `market_outcomes` with `winning_outcome`, `resolved_at`, `is_resolved = true`
4. If not resolved yet: update `last_checked_at` only
5. Insert new condition_ids not yet in `market_outcomes`

**Gamma API for resolution:**
```
GET https://gamma-api.polymarket.com/markets/{condition_id}
```
Response includes: `resolved`, `resolutionTime`, `outcomePrices: ["1","0"]` (index 0 = YES, index 1 = NO).
A price of "1" means that outcome won.

---

## Step 10 — One-Time Setup Commands

These must be run manually **once** on the Supabase database (via SQL editor or migration):

```sql
-- Set database-level app settings (do this in Supabase SQL Editor)
ALTER DATABASE postgres SET app.supabase_url = 'https://cxvntzszdkyggjjenefn.supabase.co';
ALTER DATABASE postgres SET app.service_role_key = '<YOUR_SERVICE_ROLE_KEY>';
```

And add `copy_pros` to the PostgREST extra search path in the Supabase Dashboard.

---

## Step 11 — Environment Variables (.env.example)

```env
# Supabase
SUPABASE_URL=https://cxvntzszdkyggjjenefn.supabase.co
SUPABASE_SERVICE_ROLE_KEY=

# Polymarket CLOB (already in Edge Function secrets)
CLOB_API_KEY=
CLOB_SECRET=
CLOB_PASS_PHRASE=
```

The Edge Functions automatically have access to `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASS_PHRASE` via Supabase secrets — no extra config needed for them.

---

## Execution Order

| # | File | Description | Run via |
|---|------|-------------|---------|
| 1 | `001_rename_schema.sql` | Rename `copy-pros` → `copy_pros` | Supabase migration |
| 2 | `002_create_tables.sql` | Create profiles, activity, market_outcomes | Supabase migration |
| 3 | `003_indexes.sql` | All indexes | Supabase migration |
| 4 | `004_rls.sql` | RLS + read policies | Supabase migration |
| 5 | `005_triggers.sql` | Profile insert trigger | Supabase migration |
| 6 | `006_cron_jobs.sql` | pg_cron schedules | Supabase migration |
| 7 | `007_expose_api.sql` | GRANT schema to anon/authenticated | Supabase migration |
| 8 | One-time SQL | Set `app.supabase_url` + `app.service_role_key` | Manual in SQL Editor |
| 9 | Dashboard | Add `copy_pros` to PostgREST extra search path | Dashboard |
| 10 | Deploy Edge Functions | `supabase functions deploy fetch-activity` | CLI / CI |
| 11 | Deploy Edge Functions | `supabase functions deploy resolve-outcomes` | CLI / CI |
| 12 | Seed profile | Insert `0x63ce342161250d705dc0b16df89036c8e5f9ba9a` | scripts/add-profile.ts |

---

## Things to Verify Before Implementation

- [ ] Does the Polymarket CLOB `/data/trades?maker_address=` endpoint paginate by cursor ID or timestamp?
  → Plan uses cursor (trade_id) since the API documents `after` as a cursor param
- [ ] Does Gamma API `/markets?condition_id=` support multi-value batch?
  → If not, will serialize requests with a small concurrency limit
- [ ] `app.service_role_key` database setting is only needed if trigger is in use; pg_cron jobs can hardcode the service role key in the vault as an alternative

---

## Data Flow Diagram (Autonomous Profile Add)

```
1. User inserts: INSERT INTO copy_pros.profiles (proxy_wallet_address, display_name)
                 VALUES ('0x63ce342161250d705dc0b16df89036c8e5f9ba9a', 'Whale #1');

2. Trigger fires → net.http_post → fetch-activity (profile_id=X)

3. fetch-activity:
   - Sets tracking window: trades after profiles.tracking_start_at
   - GET clob.polymarket.com/data/trades?maker_address=0x63ce...
   - Enriches with Gamma API (market_slug, event_slug, question)
   - UPSERT into activity (ON CONFLICT trade_id DO NOTHING)
   - Upsert condition_ids into market_outcomes (is_resolved=false)
   - UPDATE profiles.last_polled_at = now()

4. Every 2 min: pg_cron → fetch-activity (all active profiles)
   - Same flow, uses last_polled_at as the "since" cursor

5. :02/:17/:32/:47: pg_cron → resolve-outcomes
   - Finds unresolved condition_ids
   - Calls Gamma API
   - Updates market_outcomes.winning_outcome where resolved=true
```

---

*This plan was generated after loading and analyzing: designing-tests, designing-architecture, supabase, designing-apis, python, typescript-pro skills, inspecting the poly Supabase project (schemas, existing tables, extensions), and confirming all answers via targeted questions.*
