# copy-pros

Autonomous, multi-profile Polymarket activity tracker built entirely on Supabase
(Postgres + Edge Functions + pg_cron + pg_net). Insert a proxy wallet address
into `copy_pros.profiles` and the system immediately starts collecting every
trade that wallet makes, forever, with no other configuration required.

**Project:** `poly` — Supabase ref `cxvntzszdkyggjjenefn` (us-east-2)
**Schema:** `copy_pros` (all objects live here; no other schema is touched)
**Repo:** https://github.com/brickdatalab/copy-pros

---

## How It Works

```
INSERT INTO copy_pros.profiles (proxy_wallet_address, ...)
         │
         ▼
  AFTER INSERT trigger
  trigger_on_profile_insert()
         │  pg_net.http_post → fetch-activity (immediate)
         │
         ▼
  pg_cron: every 1 min ──────────────────────► fetch-activity (Edge Fn)
                                                      │
                                         Polymarket CLOB API (L2 auth)
                                         maker_address + taker_address
                                                      │
                                              UPSERT activity rows
                                           (conflict on trade_id → skip)
                                                      │
                                              UPSERT market_outcomes seeds
                                           (condition_id, is_resolved=false)
                                                      │
  pg_cron: :02/:17/:32/:47 ──────────────► resolve-outcomes (Edge Fn)
                                                      │
                                         Polymarket Gamma API (public)
                                                      │
                                         UPDATE market_outcomes SET
                                           winning_outcome, is_resolved=true
                                           WHERE market settled
```

---

## Schema

### `copy_pros.profiles`
One row per tracked proxy wallet.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | auto-generated |
| `proxy_wallet_address` | `text` UNIQUE | lowercase-normalised by index |
| `display_name` | `text` | optional label |
| `is_active` | `bool` DEFAULT true | set false to pause tracking |
| `tracking_start_at` | `timestamptz` DEFAULT now() | used as "since" on first poll |
| `last_polled_at` | `timestamptz` | updated after every successful fetch |
| `created_at` | `timestamptz` DEFAULT now() | |

### `copy_pros.activity`
Append-only ledger of every individual trade. Never updated, only inserted.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` IDENTITY PK | |
| `profile_id` | `uuid` FK → profiles | ON DELETE CASCADE |
| `trade_id` | `text` UNIQUE | Polymarket trade ID — dedup key |
| `condition_id` | `text` | Polymarket condition/market ID |
| `market_slug` | `text` | human-readable market slug |
| `event_slug` | `text` | parent event slug (groups sub-markets) |
| `question` | `text` | market question text |
| `outcome` | `text` CHECK ('YES','NO') | which token was traded |
| `side` | `text` CHECK ('BUY','SELL') | trade direction |
| `price` | `numeric(18,6)` | price per share (0–1) |
| `shares` | `numeric(18,6)` | number of shares |
| `usdc_amount` | `numeric(18,6)` | price × shares — USDC cost |
| `transaction_hash` | `text` | on-chain tx hash |
| `event_timestamp` | `timestamptz` | match_time from CLOB |
| `inserted_at` | `timestamptz` DEFAULT now() | |

### `copy_pros.market_outcomes`
One row per unique market (condition_id). Seeded by fetch-activity, resolved by resolve-outcomes.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` IDENTITY PK | |
| `condition_id` | `text` UNIQUE | Polymarket condition ID |
| `market_slug` | `text` | |
| `event_slug` | `text` | |
| `question` | `text` | |
| `winning_outcome` | `text` CHECK ('YES','NO') | null until resolved |
| `is_resolved` | `bool` DEFAULT false | set true when market settles |
| `resolved_at` | `timestamptz` | when it settled |
| `last_checked_at` | `timestamptz` | updated every resolve-outcomes run |
| `created_at` | `timestamptz` DEFAULT now() | |

### `copy_pros.config`
Stores Supabase URL and service_role_key for use by trigger + cron (see below).

| `key` | `value` |
|---|---|
| `supabase_url` | `https://cxvntzszdkyggjjenefn.supabase.co` |
| `service_role_key` | `eyJ...` (live JWT) |

Access: REVOKED from anon/authenticated. SELECT only for service_role.

---

## Migrations (applied in order)

| # | File | What it does |
|---|---|---|
| 001 | `001_rename_schema.sql` | Renames `copy-pros` → `copy_pros` |
| 002 | `002_create_tables.sql` | Creates profiles, activity, market_outcomes |
| 003 | `003_indexes.sql` | Core operational indexes (FK, time-series, partial) |
| 004 | `004_rls.sql` | Enables RLS; anon/authenticated get SELECT only |
| 005 | `005_triggers.sql` | AFTER INSERT trigger on profiles → calls fetch-activity |
| 006 | `006_cron_jobs.sql` | pg_cron: fetch-activity every 1 min; resolve-outcomes at :02/:17/:32/:47 |
| 007 | `007_expose_api.sql` | GRANT USAGE + SELECT on schema to anon/authenticated/service_role |
| 008 | `008_config_table.sql` | Creates copy_pros.config; stores supabase_url + service_role_key |
| 009 | `009_expose_pgrst_schema.sql` | ALTER ROLE authenticator pgrst.db_schemas; NOTIFY reload |
| 010 | `010_analytics_indexes.sql` | 5 analytics covering indexes for query patterns |

---

## Edge Functions

Both deployed to Supabase, JWT verification enabled.

### `fetch-activity`
- **Called by:** profile INSERT trigger (single profile) + pg_cron every 1 min (all active profiles)
- **Auth:** service_role Bearer token (from `copy_pros.config`)
- **Flow:**
  1. Load active profiles (or single profile if `profile_id` in body)
  2. For each: `since = last_polled_at ?? tracking_start_at` → Unix seconds
  3. Query CLOB `GET /data/trades?maker_address=` AND `?taker_address=` (both, merged + deduped)
  4. Batch-enrich condition_ids via Gamma API `/markets?conditionIds=...`
  5. UPSERT `activity` rows (`onConflict: trade_id, ignoreDuplicates: true`)
  6. UPSERT `market_outcomes` seeds for new condition_ids (`is_resolved=false`)
  7. Update `profiles.last_polled_at` to now

### `resolve-outcomes`
- **Called by:** pg_cron at :02, :17, :32, :47 of every hour
- **Flow:**
  1. Query all `market_outcomes WHERE is_resolved = FALSE`
  2. Also find condition_ids in `activity` not yet in `market_outcomes`
  3. Batch-fetch Gamma API for all targets
  4. Determine `winning_outcome`: `outcomePrices[0]='1'` → YES, `[1]='1'` → NO
  5. UPSERT: if resolved → set `winning_outcome`, `resolved_at`, `is_resolved=true`; else → update `last_checked_at`

### Shared libraries
- `_shared/polymarket.ts` — L2 HMAC-SHA256 auth, paginated CLOB trade fetcher (handles maker+taker, cursor pagination, ms/s timestamp normalization), Gamma batch client, `resolveWinningOutcome()`
- `_shared/supabase.ts` — `createAdminClient()` using system-injected `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, scoped to `db: { schema: 'copy_pros' }`

### Required Edge Function secrets (set in Supabase Dashboard → Edge Functions → Secrets)
```
CLOB_API_KEY       — Polymarket L2 API key (the wallet address)
CLOB_SECRET        — base64-encoded HMAC secret
CLOB_PASS_PHRASE   — API passphrase
```
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are auto-injected by Supabase runtime.

---

## Indexes

### Operational (migration 003)
| Index | Definition | Purpose |
|---|---|---|
| `idx_profiles_wallet_lower` | UNIQUE `lower(proxy_wallet_address)` | Case-insensitive dedup on insert |
| `idx_profiles_is_active` | Partial `WHERE is_active = TRUE` | Cron profile scan |
| `idx_activity_profile_id` | `(profile_id)` | FK cascade + profile lookups |
| `idx_activity_condition_id` | `(condition_id)` | Market joins |
| `idx_activity_profile_event_time` | `(profile_id, event_timestamp DESC)` | Time-series per profile |
| `idx_activity_event_timestamp` | `(event_timestamp DESC)` | Global time-series |
| `idx_market_outcomes_unresolved` | Partial `(condition_id) WHERE is_resolved = FALSE` | resolve-outcomes cron scan |

### Analytics (migration 010)
| Index | Definition | Use case |
|---|---|---|
| `idx_activity_market_profile` | `(condition_id, profile_id)` | Cross-profile comparison on a specific market |
| `idx_activity_event_profile` | `(event_slug, profile_id)` | Event-level grouping; event_slug was previously unindexed |
| `idx_activity_profile_market_covering` | `(profile_id, condition_id) INCLUDE (outcome, side, usdc_amount, shares, price)` | P&L per profile per market — index-only scan |
| `idx_activity_market_direction_covering` | `(condition_id, outcome, side) INCLUDE (usdc_amount, shares, price)` | Direction analytics per market (YES/NO × BUY/SELL volumes) — index-only scan |
| `idx_market_outcomes_resolved_covering` | Partial `(condition_id) INCLUDE (winning_outcome, resolved_at) WHERE is_resolved = TRUE` | Win/loss JOIN on resolved markets — index-only scan on JOIN side |

---

## PostgREST Exposure

`copy_pros` is exposed via `ALTER ROLE authenticator SET pgrst.db_schemas TO 'public, copy_pros'` (migration 009). No Dashboard change required.

Example queries via REST API:
```
GET /rest/v1/activity?profile_id=eq.{uuid}&order=event_timestamp.desc
GET /rest/v1/market_outcomes?is_resolved=eq.true&order=resolved_at.desc
GET /rest/v1/activity?condition_id=eq.{id}&select=profile_id,outcome,side,usdc_amount
```

The Supabase JS client routes to the schema via `db: { schema: 'copy_pros' }`.

---

## Known Constraints & Workarounds

**`ALTER DATABASE SET` is blocked** — Supabase's managed `postgres` role is not a true superuser and cannot set custom GUC parameters via `ALTER DATABASE`. The trigger and cron jobs therefore read `supabase_url` and `service_role_key` from `copy_pros.config` table instead of `current_setting('app.*')`. Access to `copy_pros.config` is restricted to `service_role`.

**PostgREST schema reload** — After adding `copy_pros` to `pgrst.db_schemas`, two NOTIFYs are required: `'reload config'` and `'reload schema'`. Only the config reload was not sufficient for table cache warm-up.

---

## Adding a Profile

```bash
deno run --allow-env --allow-net --allow-read scripts/add-profile.ts \
  --wallet 0xADDRESS \
  --name "Display Name"
```

Or directly in SQL:
```sql
INSERT INTO copy_pros.profiles (proxy_wallet_address, display_name)
VALUES ('0xaddress', 'Name');
-- Trigger fires immediately → fetch-activity called → tracking begins
```

## Checking Activity

```bash
deno run --allow-env --allow-net --allow-read scripts/check-activity.ts \
  --wallet 0xADDRESS
```

---

## Key Analytics Queries

**P&L per profile per market (requires resolved outcomes):**
```sql
SELECT
  a.profile_id,
  a.condition_id,
  a.market_slug,
  mo.winning_outcome,
  SUM(a.usdc_amount)                                              AS total_wagered,
  SUM(a.usdc_amount) FILTER (WHERE a.outcome = mo.winning_outcome) AS winning_bets,
  SUM(a.usdc_amount) FILTER (WHERE a.outcome != mo.winning_outcome) AS losing_bets,
  COUNT(*)                                                        AS trade_count
FROM copy_pros.activity a
JOIN copy_pros.market_outcomes mo ON mo.condition_id = a.condition_id
WHERE mo.is_resolved = TRUE
GROUP BY a.profile_id, a.condition_id, a.market_slug, mo.winning_outcome
ORDER BY total_wagered DESC;
```

**Cross-profile comparison on one market:**
```sql
SELECT
  p.display_name,
  a.outcome,
  a.side,
  COUNT(*)              AS trades,
  SUM(a.usdc_amount)    AS total_usdc,
  AVG(a.price)          AS avg_price
FROM copy_pros.activity a
JOIN copy_pros.profiles p ON p.id = a.profile_id
WHERE a.condition_id = 'YOUR_CONDITION_ID'
GROUP BY p.display_name, a.outcome, a.side
ORDER BY total_usdc DESC;
```

**Direction breakdown per event (all sub-markets):**
```sql
SELECT
  a.market_slug,
  a.outcome,
  a.side,
  COUNT(*)           AS trades,
  SUM(a.usdc_amount) AS usdc_volume
FROM copy_pros.activity a
WHERE a.event_slug = 'YOUR_EVENT_SLUG'
GROUP BY a.market_slug, a.outcome, a.side
ORDER BY usdc_volume DESC;
```

**Win rate across all profiles:**
```sql
SELECT
  p.display_name,
  COUNT(*) FILTER (WHERE a.outcome = mo.winning_outcome) AS correct,
  COUNT(*) FILTER (WHERE a.outcome != mo.winning_outcome) AS wrong,
  ROUND(
    COUNT(*) FILTER (WHERE a.outcome = mo.winning_outcome)::numeric
    / NULLIF(COUNT(*), 0) * 100, 1
  ) AS win_rate_pct
FROM copy_pros.activity a
JOIN copy_pros.market_outcomes mo ON mo.condition_id = a.condition_id
JOIN copy_pros.profiles p ON p.id = a.profile_id
WHERE mo.is_resolved = TRUE
GROUP BY p.display_name
ORDER BY win_rate_pct DESC;
```

---

## Repo Structure

```
copy-pros/
├── .env.example                          # Required env vars
├── PLAN.md                               # Original design plan
├── scripts/
│   ├── add-profile.ts                    # CLI: insert a wallet and start tracking
│   └── check-activity.ts                 # CLI: inspect recent trades for a wallet
└── supabase/
    ├── config.toml                       # project_id, schema refs
    ├── functions/
    │   ├── _shared/
    │   │   ├── polymarket.ts             # CLOB L2 auth, trade fetcher, Gamma client
    │   │   └── supabase.ts               # Admin client factory (copy_pros schema)
    │   ├── fetch-activity/index.ts       # Ingestion function
    │   └── resolve-outcomes/index.ts     # Outcome resolution function
    └── migrations/
        ├── 001_rename_schema.sql
        ├── 002_create_tables.sql
        ├── 003_indexes.sql
        ├── 004_rls.sql
        ├── 005_triggers.sql
        ├── 006_cron_jobs.sql
        ├── 007_expose_api.sql
        ├── 008_config_table.sql
        ├── 009_expose_pgrst_schema.sql
        └── 010_analytics_indexes.sql
```

---

## Current Production State

- **Profiles tracked:** 1 (`0x63ce342161250d705dc0b16df89036c8e5f9ba9a`, "Tracked Whale #1")
- **Cron — fetch-activity:** `* * * * *` (every 1 minute), job name `fetch-activity-every-1min`
- **Cron — resolve-outcomes:** `2,17,32,47 * * * *`, job name `resolve-outcomes-15min`
- **Edge Functions:** both `ACTIVE`, version 1, JWT verification on
- **PostgREST:** `copy_pros` exposed via authenticator role config + schema reload (migration 009)
- **All 10 migrations applied** to the `poly` project
