# copy-pros

**Supabase project:** `poly` — ref `cxvntzszdkyggjjenefn` (us-east-2)
**Schema:** `copy_pros` — all objects live here; no other schema is ever touched
**Repo:** https://github.com/brickdatalab/copy-pros

---

## What this system does

Two independent subsystems, both writing to the `copy_pros` schema:

**Subsystem A — Profile Activity Tracker**
Watch proxy wallets on Polymarket. Insert a wallet address into `copy_pros.profiles` and the system begins collecting every trade that wallet makes — outcome, direction, price, USDC value. Resolves market outcomes automatically on a 15-minute cycle. Runs fully autonomously via Supabase Edge Functions + pg_cron.

**Subsystem B — Real-Time Event Streamer**
On-demand. Register a specific Polymarket event (by slug or URL) and then run a local Python script that opens a WebSocket to the Polymarket CLOB and streams live orderbook data into Supabase. Writes raw ticks, 1-second snapshots, and computed technical indicators (VWAP, order imbalance, spread momentum, mid-price momentum) in real time. Designed to feed a trading signal engine. Data auto-purges 48h after market resolution.

---

## Architecture

### Subsystem A — Profile Activity Tracker

```
INSERT INTO copy_pros.profiles (proxy_wallet_address, ...)
         │
         ▼
  AFTER INSERT trigger: copy_pros.trigger_on_profile_insert()
  reads URL + service_role_key from copy_pros.config
  fires pg_net.http_post → fetch-activity (immediate first run)
         │
         ▼
  pg_cron: every 1 min ──────────────────────► fetch-activity (Edge Function)
                                                      │
                                         GET https://clob.polymarket.com/data/trades
                                         ?maker_address={wallet} (L2 HMAC auth)
                                                      │
                                           Batch enrich via Gamma API
                                         /markets?conditionIds={id1},{id2},...
                                                      │
                                    UPSERT copy_pros.activity      (conflict: trade_id)
                                    UPSERT copy_pros.market_outcomes (conflict: condition_id)
                                    UPDATE copy_pros.profiles.last_polled_at
                                                      │
  pg_cron: :02/:17/:32/:47 ──────────────► resolve-outcomes (Edge Function)
                                                      │
                                         Gamma API /markets/{condition_id}
                                                      │
                                    UPDATE copy_pros.market_outcomes SET
                                      winning_outcome, resolved_at, is_resolved=true
                                      WHERE market settled
```

### Subsystem B — Real-Time Event Streamer

```
python scripts/register_event.py <event-slug-or-url>
         │
         ▼
  GET https://gamma-api.polymarket.com/events?slug={slug}
  GET https://clob.polymarket.com/markets/{condition_id}  (for token IDs, public)
         │
  UPSERT copy_pros.events
  UPSERT copy_pros.markets  (one row per YES/NO market in the event)
         │
         ▼
python scripts/stream_market.py
         │
  asyncpg pool → direct Postgres connection (bypasses PostgREST)
  server_settings={"search_path": "copy_pros,public"}
         │
  wss://ws-subscriptions-clob.polymarket.com/ws/market
  subscribe: {"assets_ids": [token_yes, token_no, ...], "type": "market"}
         │
  On every message:
    ├── Update in-memory OrderBook (bids/asks dict)
    └── Append to pending_ticks list
         │
  Every 1 second (snapshot_loop):
    ├── INSERT copy_pros.market_ticks    (one row per token per pending tick)
    ├── INSERT copy_pros.market_snapshots (combined YES+NO view, VWAP, volume, imbalance)
    └── INSERT copy_pros.indicators       (VWAP 1m/5m, order_imbalance, spread_momentum,
                                           mid_momentum 30s/1m/5m, signal LONG/SHORT/NEUTRAL)

  pg_cron: every 4 hours
    DELETE market_ticks, market_snapshots, indicators
    WHERE market resolved_at < NOW() - INTERVAL '48 hours'
```

---

## Schema

All 9 tables in `copy_pros`. Read access via PostgREST for `anon`/`authenticated`. Writes only via `service_role` (Edge Functions) or direct asyncpg (local scripts).

### `copy_pros.profiles`
One row per tracked proxy wallet.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | auto-generated |
| `proxy_wallet_address` | `text` UNIQUE | lowercase-normalised |
| `display_name` | `text` | optional label |
| `is_active` | `bool` DEFAULT true | false = pause tracking |
| `tracking_start_at` | `timestamptz` DEFAULT now() | used as "since" on first poll |
| `last_polled_at` | `timestamptz` | updated after every successful fetch-activity run |
| `created_at` | `timestamptz` | |

### `copy_pros.activity`
Append-only trade ledger. Never updated; only inserted.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` IDENTITY PK | |
| `profile_id` | `uuid` FK → profiles | CASCADE delete |
| `trade_id` | `text` UNIQUE | Polymarket CLOB trade ID — dedup key |
| `condition_id` | `text` | Polymarket market condition ID |
| `market_slug` | `text` | human-readable market slug |
| `event_slug` | `text` | parent event slug |
| `question` | `text` | market question text |
| `outcome` | `text` CHECK ('YES','NO') | which token was traded |
| `side` | `text` CHECK ('BUY','SELL') | trade direction |
| `price` | `numeric(18,6)` | price per share (0–1) |
| `shares` | `numeric(18,6)` | |
| `usdc_amount` | `numeric(18,6)` | price × shares — total USDC |
| `transaction_hash` | `text` | on-chain tx hash |
| `event_timestamp` | `timestamptz` | match_time from CLOB |
| `inserted_at` | `timestamptz` | |

### `copy_pros.market_outcomes`
One row per unique market seen in activity. Seeded by `fetch-activity`, resolved by `resolve-outcomes`.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigint` IDENTITY PK | |
| `condition_id` | `text` UNIQUE | Polymarket condition ID |
| `market_slug` | `text` | |
| `event_slug` | `text` | |
| `question` | `text` | |
| `winning_outcome` | `text` CHECK ('YES','NO') | null until resolved |
| `is_resolved` | `bool` DEFAULT false | |
| `resolved_at` | `timestamptz` | |
| `last_checked_at` | `timestamptz` | updated every resolve-outcomes run |
| `created_at` | `timestamptz` | |

### `copy_pros.config`
Key/value store for runtime secrets used by trigger + pg_cron jobs. Access revoked from anon/authenticated; SELECT only for service_role.

| key | value |
|---|---|
| `supabase_url` | `https://cxvntzszdkyggjjenefn.supabase.co` |
| `service_role_key` | live JWT (rotation: update this row + redeploy) |

**Why this exists:** Supabase's managed `postgres` role cannot run `ALTER DATABASE SET` (requires true superuser). The trigger and pg_cron jobs read these values from the config table instead of `current_setting('app.*')`.

### `copy_pros.events`
One row per registered Polymarket event. The anchor for Subsystem B.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `event_slug` | `text` UNIQUE | e.g. `will-btc-hit-100k` |
| `title` | `text` | |
| `category` | `text` | |
| `is_active` | `bool` DEFAULT true | |
| `resolved_at` | `timestamptz` | set when event resolves |
| `created_at` | `timestamptz` | |

### `copy_pros.markets`
One row per YES/NO binary market within an event. Neg-risk events have multiple markets under one event.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `event_id` | `uuid` FK → events | CASCADE delete |
| `condition_id` | `text` UNIQUE | Polymarket condition ID |
| `question` | `text` | market question |
| `token_id_yes` | `text` NOT NULL | ERC-1155 YES token ID (used as WebSocket asset_id) |
| `token_id_no` | `text` NOT NULL | ERC-1155 NO token ID |
| `is_neg_risk` | `bool` DEFAULT false | |
| `is_resolved` | `bool` DEFAULT false | |
| `winning_outcome` | `text` CHECK ('YES','NO') | null until resolved |
| `resolved_at` | `timestamptz` | used by purge policy |
| `created_at` | `timestamptz` | |

### `copy_pros.market_ticks`
Raw WebSocket message data. Append-only. One row per token per message. Purged 48h after market resolution.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `market_id` | `uuid` FK → markets | CASCADE delete |
| `asset_id` | `text` | token_id_yes or token_id_no |
| `token_side` | `text` CHECK ('YES','NO') | which side |
| `created_at` | `timestamptz` | |
| `tick_type` | `text` CHECK ('book','price_change','last_trade') | WebSocket event_type |
| `best_bid` | `numeric(10,6)` | null for last_trade ticks |
| `best_ask` | `numeric(10,6)` | |
| `bid_size` | `numeric(18,4)` | depth at best bid level |
| `ask_size` | `numeric(18,4)` | depth at best ask level |
| `bid_depth` | `numeric(18,4)` | total bid-side liquidity |
| `ask_depth` | `numeric(18,4)` | total ask-side liquidity |
| `spread` | `numeric(10,6)` | best_ask - best_bid |
| `trade_price` | `numeric(10,6)` | null unless last_trade |
| `trade_size` | `numeric(18,4)` | null unless last_trade |
| `raw` | `jsonb` | full original payload |

### `copy_pros.market_snapshots`
1-second aggregated market view. Written by `stream_market.py` every second per active market. Purged 48h after resolution.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `market_id` | `uuid` FK → markets | |
| `ts` | `timestamptz` | snapshot time (UNIQUE with market_id) |
| `yes_price` | `numeric(10,6)` | YES token mid-price |
| `yes_bid` | `numeric(10,6)` | |
| `yes_ask` | `numeric(10,6)` | |
| `yes_spread` | `numeric(10,6)` | |
| `yes_bid_depth` | `numeric(18,4)` | total bid liquidity |
| `yes_ask_depth` | `numeric(18,4)` | total ask liquidity |
| `no_price` | `numeric(10,6)` | NO token mid-price |
| `no_bid` | `numeric(10,6)` | |
| `no_ask` | `numeric(10,6)` | |
| `no_spread` | `numeric(10,6)` | |
| `no_bid_depth` | `numeric(18,4)` | |
| `no_ask_depth` | `numeric(18,4)` | |
| `vwap_1m` | `numeric(10,6)` | YES token VWAP over last 60s |
| `vwap_5m` | `numeric(10,6)` | YES token VWAP over last 300s |
| `volume_1m` | `numeric(18,4)` | YES token trade volume last 60s |
| `volume_5m` | `numeric(18,4)` | YES token trade volume last 300s |
| `imbalance_yes` | `numeric(8,6)` | (bid_depth - ask_depth) / (bid_depth + ask_depth), range [-1,1] |
| `imbalance_no` | `numeric(8,6)` | same for NO token |

### `copy_pros.indicators`
Computed technical indicators. Written alongside snapshots. The primary feed for the trading signal engine. Purged 48h after resolution.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `market_id` | `uuid` FK → markets | |
| `ts` | `timestamptz` | UNIQUE with market_id |
| `computed_at` | `timestamptz` DEFAULT now() | |
| `vwap_1m` | `numeric(10,6)` | YES token volume-weighted avg price, 60s window |
| `vwap_5m` | `numeric(10,6)` | YES token VWAP, 300s window |
| `order_imbalance` | `numeric(8,6)` | YES book: (bid_depth - ask_depth) / total, range [-1,1] |
| `spread_momentum` | `numeric(10,6)` | (spread_now - spread_30s_ago) / spread_30s_ago; negative = tightening |
| `mid_momentum_30s` | `numeric(10,6)` | (mid_now - mid_30s_ago) / mid_30s_ago |
| `mid_momentum_1m` | `numeric(10,6)` | same, 60s window |
| `mid_momentum_5m` | `numeric(10,6)` | same, 300s window |
| `mid_price` | `numeric(10,6)` | current YES mid-price |
| `signal` | `text` CHECK ('LONG','SHORT','NEUTRAL') | directional signal; see signal logic below |

**Signal logic:** LONG when `order_imbalance > 0.15` AND `mid_price > vwap_1m` AND `spread_momentum < 0` (tightening). SHORT when `order_imbalance < -0.15` AND `mid_price < vwap_1m`. Otherwise NEUTRAL.

---

## Migrations

Applied in order to Supabase project `cxvntzszdkyggjjenefn`.

| # | File | What it does |
|---|---|---|
| 001 | `001_rename_schema.sql` | Renames `copy-pros` → `copy_pros` |
| 002 | `002_create_tables.sql` | Creates profiles, activity, market_outcomes |
| 003 | `003_indexes.sql` | Operational indexes (FK, time-series, partial) |
| 004 | `004_rls.sql` | RLS enabled; anon/authenticated get SELECT only |
| 005 | `005_triggers.sql` | AFTER INSERT on profiles → fires fetch-activity immediately via pg_net |
| 006 | `006_cron_jobs.sql` | pg_cron: fetch-activity every 1 min; resolve-outcomes at :02/:17/:32/:47 |
| 007 | `007_expose_api.sql` | GRANT USAGE + SELECT on copy_pros to anon/authenticated |
| 008 | `008_config_table.sql` | Creates and seeds copy_pros.config (supabase_url + service_role_key) |
| 009 | `009_expose_pgrst_schema.sql` | `ALTER ROLE authenticator SET pgrst.db_schemas TO 'public,copy_pros'` + NOTIFY reload |
| 010 | `010_analytics_indexes.sql` | 5 covering indexes for P&L and analytics queries |
| 011 | `011_market_data_tables.sql` | Creates events + markets tables |
| 012 | `012_market_ticks.sql` | Creates market_ticks table with time+asset indexes |
| 013 | `013_market_snapshots_indicators.sql` | Creates market_snapshots + indicators tables |
| 014 | `014_purge_policy.sql` | pg_cron purge job: every 4h, deletes tick/snapshot/indicator data 48h after market resolution |

---

## Edge Functions

Both deployed to Supabase. JWT verification on. Invoked by pg_cron via `pg_net.http_post`.

### `fetch-activity`
**Trigger:** AFTER INSERT on profiles (single profile) + pg_cron every 1 min (all active profiles)

**Flow:**
1. Load active profiles (or single if `profile_id` in request body)
2. For each: compute `since = UNIX(last_polled_at ?? tracking_start_at)`
3. Call CLOB `GET /data/trades?maker_address={wallet}&after={since}&limit=500` with L2 HMAC auth
4. Paginate using `next_cursor` until `LTE=` sentinel or empty batch
5. Batch-enrich condition IDs via Gamma `GET /markets?conditionIds={ids}`
6. `UPSERT activity` (ON CONFLICT trade_id DO NOTHING)
7. `UPSERT market_outcomes` for new condition IDs (is_resolved=false)
8. `UPDATE profiles.last_polled_at = now()`

**Known issue — credential config:** The CLOB L2 auth requires two distinct secrets:
- `CLOB_ADDRESS` — the proxy wallet address (goes into `POLY_ADDRESS` header)
- `CLOB_API_KEY` — the UUID API key from `create_or_derive_api_creds()` (goes into `POLY_API_KEY` header)

If `CLOB_API_KEY` is set to the wallet address instead of the UUID, the CLOB returns `401 Unauthorized/Invalid api key`. The fix: set `CLOB_ADDRESS = wallet`, `CLOB_API_KEY = UUID from py-clob-client`. These must be set as Supabase Edge Function secrets in the Dashboard.

### `resolve-outcomes`
**Trigger:** pg_cron at :02, :17, :32, :47 of every hour

**Flow:**
1. Query `market_outcomes WHERE is_resolved = FALSE`
2. Also find condition_ids in activity not yet in market_outcomes
3. Batch-fetch Gamma API for all targets
4. If `resolved=true`: set `winning_outcome`, `resolved_at`, `is_resolved=true`
5. Otherwise: update `last_checked_at`

**Outcome determination:** Gamma `outcomePrices` array — index 0 = YES, index 1 = NO. A price of `"1"` means that outcome won.

### Shared libraries (`supabase/functions/_shared/`)

**`polymarket.ts`**
- `buildL2Headers(method, requestPath)` — HMAC-SHA256 auth per py-clob-client spec. HMAC message = `timestamp + method + requestPath` (NO nonce, NO query params). Secret is URL-safe base64 encoded. Signature is URL-safe base64 encoded. Source verified against `py_clob_client/signing/hmac.py`.
- `fetchTradesForWallet(address, afterUnixSeconds)` — paginated CLOB trade fetcher, cursor-based pagination via `next_cursor`
- `fetchMarketDetailsBatch(conditionIds)` — Gamma batch fetch, returns `Map<conditionId, MarketDetail>`
- `parsePolyTimestamp(ts)` — normalizes CLOB timestamps (ms vs seconds heuristic: > 1e12 → ms)
- `resolveWinningOutcome(outcomePrices)` — returns 'YES' | 'NO' | null

**`supabase.ts`**
- `createAdminClient()` — uses system-injected `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, sets `db: { schema: 'copy_pros' }`

### Required Edge Function secrets
Set in Supabase Dashboard → Settings → Edge Functions → Secrets:
```
CLOB_ADDRESS       — proxy wallet address (0x...)
CLOB_API_KEY       — UUID API key from create_or_derive_api_creds() — NOT the wallet address
CLOB_SECRET        — URL-safe base64 HMAC secret
CLOB_PASS_PHRASE   — API passphrase
```
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are auto-injected by Supabase runtime.

---

## Scripts (local Python — Subsystem B)

All scripts read from `.env`. Run while connected to the internet. No background process needed — only runs when you run them.

### `scripts/register_event.py`
Register a Polymarket event by slug or full URL.

```bash
python scripts/register_event.py will-btc-hit-100k-by-dec-2025
# or
python scripts/register_event.py https://polymarket.com/event/will-btc-hit-100k-by-dec-2025
```

**What it does:**
1. Fetches `GET https://gamma-api.polymarket.com/events?slug={slug}` for event metadata + sub-markets
2. For each sub-market: fetches `GET https://clob.polymarket.com/markets/{condition_id}` (public, no auth) for token IDs
3. Upserts into `copy_pros.events` and `copy_pros.markets` via asyncpg direct connection

### `scripts/stream_market.py`
Open WebSocket to Polymarket CLOB and stream all registered active markets.

```bash
python scripts/stream_market.py
```

**What it does:**
- Reads all active, unresolved markets from `copy_pros.markets` via asyncpg
- Subscribes to `wss://ws-subscriptions-clob.polymarket.com/ws/market` with `{"assets_ids": [...all token IDs...], "type": "market"}`
- Maintains in-memory `OrderBook` per token (bids/asks dicts, incremental updates)
- Maintains `MarketState` per market: rolling trade deques (1m, 5m), spread history, mid-price history
- Flushes every 1 second: writes ticks → snapshots → indicators to Supabase

**Key classes:**
- `OrderBook` — `apply_snapshot(bids, asks)` for full book reset, `apply_change(changes)` for diffs. Properties: `best_bid`, `best_ask`, `mid`, `spread`, `bid_depth`, `ask_depth`, `imbalance`
- `MarketState` — holds YES+NO `OrderBook`s, `deque`-based rolling trade windows, `spread_hist`, `mid_hist`. Methods: `vwap(secs)`, `volume(secs)`, `spread_momentum()`, `mid_momentum(secs)`, `compute_signal()`
- `MarketStreamer` — `ws_loop()` (WebSocket reader), `snapshot_loop()` (1s aggregation writer), asyncpg pool (`min_size=2, max_size=8`)

### `scripts/requirements_stream.txt`
```
asyncpg>=0.29.0
websockets>=12.0
httpx>=0.27.0
python-dotenv>=1.0.0
```

### Deno scripts (Subsystem A)

**`scripts/add-profile.ts`** — Insert a wallet into `copy_pros.profiles`. The DB trigger fires immediately, kicking off the first activity fetch.
```bash
deno run --allow-env --allow-net --allow-read scripts/add-profile.ts --wallet 0x... --name "Label"
```

**`scripts/check-activity.ts`** — Query and display recent trades for a wallet.
```bash
deno run --allow-env --allow-net --allow-read scripts/check-activity.ts --wallet 0x...
```

---

## Environment Variables

```env
# Supabase (required for all scripts)
SUPABASE_URL=https://cxvntzszdkyggjjenefn.supabase.co
SUPABASE_SERVICE_ROLE_KEY=

# Direct Postgres connection (required for Python streaming scripts)
# Use Session mode pooler URL from Supabase Dashboard → Settings → Database → Connection pooling
SUPABASE_DB_URL=postgresql://postgres.cxvntzszdkyggjjenefn:[password]@aws-0-us-east-2.pooler.supabase.com:5432/postgres

# Polymarket CLOB auth (also set as Edge Function secrets in Dashboard)
CLOB_ADDRESS=0x...        # wallet address — goes to POLY_ADDRESS header
CLOB_API_KEY=             # UUID from create_or_derive_api_creds() — goes to POLY_API_KEY header
CLOB_SECRET=              # URL-safe base64 HMAC secret
CLOB_PASS_PHRASE=         # API passphrase
```

---

## Indexes

### Operational (migrations 003, 006)
| Index | Definition | Purpose |
|---|---|---|
| `idx_profiles_wallet_lower` | UNIQUE `lower(proxy_wallet_address)` | Case-insensitive dedup |
| `idx_profiles_is_active` | Partial `WHERE is_active = TRUE` | pg_cron profile scan |
| `idx_activity_profile_id` | `(profile_id)` | FK + profile lookups |
| `idx_activity_condition_id` | `(condition_id)` | Market joins |
| `idx_activity_profile_event_time` | `(profile_id, event_timestamp DESC)` | Time-series per profile |
| `idx_activity_event_timestamp` | `(event_timestamp DESC)` | Global time-series |
| `idx_market_outcomes_unresolved` | Partial `(condition_id) WHERE is_resolved = FALSE` | resolve-outcomes scan |

### Analytics (migration 010)
| Index | Definition | Purpose |
|---|---|---|
| `idx_activity_market_profile` | `(condition_id, profile_id)` | Cross-profile market comparison |
| `idx_activity_event_profile` | `(event_slug, profile_id)` | Event-level grouping |
| `idx_activity_profile_market_covering` | `(profile_id, condition_id) INCLUDE (outcome, side, usdc_amount, shares, price)` | P&L per profile per market (index-only scan) |
| `idx_activity_market_direction_covering` | `(condition_id, outcome, side) INCLUDE (usdc_amount, shares, price)` | Direction analytics (index-only scan) |
| `idx_market_outcomes_resolved_covering` | Partial `(condition_id) INCLUDE (winning_outcome, resolved_at) WHERE is_resolved = TRUE` | Win/loss JOIN |

### Streaming (migrations 012, 013)
| Index | Definition | Purpose |
|---|---|---|
| `idx_markets_event_id` | `(event_id)` | FK + event→market joins |
| `idx_markets_active` | Partial `(event_id) WHERE is_resolved = FALSE` | stream_market.py startup query |
| `idx_markets_token_yes` | `(token_id_yes)` | WebSocket asset_id → market lookup |
| `idx_markets_token_no` | `(token_id_no)` | same for NO token |
| `idx_ticks_market_time` | `(market_id, created_at DESC)` | Recent ticks per market |
| `idx_ticks_asset_time` | `(asset_id, created_at DESC)` | Ticks by token |
| `idx_ticks_market_only` | `(market_id)` | Purge job scan |
| `idx_snapshots_market_time` | `(market_id, ts DESC)` | Recent snapshots per market |
| `idx_snapshots_market_only` | `(market_id)` | Purge job scan |
| `idx_indicators_market_time` | `(market_id, ts DESC)` | Latest indicators per market |
| `idx_indicators_market_only` | `(market_id)` | Purge job scan |

---

## PostgREST exposure

`copy_pros` exposed via migration 009: `ALTER ROLE authenticator SET pgrst.db_schemas TO 'public,copy_pros'` + `NOTIFY pgrst, 'reload config'` + `NOTIFY pgrst, 'reload schema'`. No Dashboard change required.

Example REST queries:
```
GET /rest/v1/activity?profile_id=eq.{uuid}&order=event_timestamp.desc
GET /rest/v1/market_outcomes?is_resolved=eq.true&order=resolved_at.desc
GET /rest/v1/indicators?market_id=eq.{uuid}&order=ts.desc&limit=1
GET /rest/v1/market_snapshots?market_id=eq.{uuid}&order=ts.desc&limit=60
```

---

## Analytics queries (Subsystem A)

**P&L per profile per market:**
```sql
SELECT
  a.profile_id,
  a.condition_id,
  a.market_slug,
  mo.winning_outcome,
  SUM(a.usdc_amount)                                                AS total_wagered,
  SUM(a.usdc_amount) FILTER (WHERE a.outcome = mo.winning_outcome)  AS winning_bets,
  SUM(a.usdc_amount) FILTER (WHERE a.outcome != mo.winning_outcome) AS losing_bets,
  COUNT(*)                                                          AS trade_count
FROM copy_pros.activity a
JOIN copy_pros.market_outcomes mo ON mo.condition_id = a.condition_id
WHERE mo.is_resolved = TRUE
GROUP BY a.profile_id, a.condition_id, a.market_slug, mo.winning_outcome
ORDER BY total_wagered DESC;
```

**Win rate across profiles:**
```sql
SELECT
  p.display_name,
  COUNT(*) FILTER (WHERE a.outcome = mo.winning_outcome)  AS correct,
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

**Latest indicators for a market:**
```sql
SELECT * FROM copy_pros.indicators
WHERE market_id = '{uuid}'
ORDER BY ts DESC
LIMIT 1;
```

---

## Current production state

- **Profiles tracked:** 1 — `0x63ce342161250d705dc0b16df89036c8e5f9ba9a`
- **pg_cron jobs active:** `fetch-activity-every-1min`, `resolve-outcomes-15min`, `purge-market-data` (every 4h)
- **Edge Functions:** `fetch-activity` v3, `resolve-outcomes` v1 — both JWT-verified, ACTIVE
- **All 14 migrations applied** to project `cxvntzszdkyggjjenefn`
- **Activity collection status:** Blocked — `CLOB_API_KEY` secret is set to wallet address, not UUID API key. See credential issue in fetch-activity section above.

---

## Repo structure

```
copy-pros/
├── .env.example                              # All required env vars
├── .gitignore
├── CHANGELOG.md
├── README.md                                 # This file
├── scripts/
│   ├── add-profile.ts                        # Deno: insert wallet, trigger immediate fetch
│   ├── check-activity.ts                     # Deno: inspect activity for a wallet
│   ├── register_event.py                     # Python: register Polymarket event → seeds events+markets
│   ├── stream_market.py                      # Python: WebSocket streamer → ticks+snapshots+indicators
│   └── requirements_stream.txt              # Python deps for stream scripts
└── supabase/
    ├── config.toml
    ├── functions/
    │   ├── _shared/
    │   │   ├── polymarket.ts                 # CLOB L2 auth, trade fetcher, Gamma client
    │   │   └── supabase.ts                   # Admin client factory (copy_pros schema)
    │   ├── fetch-activity/
    │   │   └── index.ts                      # Ingestion edge function
    │   └── resolve-outcomes/
    │       └── index.ts                      # Outcome resolution edge function
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
        ├── 010_analytics_indexes.sql
        ├── 011_market_data_tables.sql
        ├── 012_market_ticks.sql
        ├── 013_market_snapshots_indicators.sql
        └── 014_purge_policy.sql
```
