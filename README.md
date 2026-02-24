# copy-pros

Autonomous Polymarket activity tracker. Insert a proxy wallet → activity collection starts immediately and runs forever.

## Architecture

```
profiles INSERT → DB trigger → fetch-activity (Edge Function)
                                     ↓
                      pg_cron (*/2 * * * *) → fetch-activity
                                     ↓
                         copy_pros.activity (append-only)
                                     ↓
                      pg_cron (2,17,32,47 * * * *) → resolve-outcomes
                                     ↓
                         copy_pros.market_outcomes
```

## Tables

| Table | Description |
|---|---|
| `copy_pros.profiles` | Wallets to track. INSERT to add. |
| `copy_pros.activity` | Every trade — append-only, deduplicated by `trade_id`. |
| `copy_pros.market_outcomes` | One row per unique market; outcome populated by cron. |

## Setup (one-time)

### 1. Supabase Dashboard — Extra search path

Settings → API → **Extra search path** → add `copy_pros`

This exposes the schema to the PostgREST API.

### 2. Set database app settings (SQL Editor)

```sql
ALTER DATABASE postgres SET app.supabase_url = 'https://cxvntzszdkyggjjenefn.supabase.co';
ALTER DATABASE postgres SET app.service_role_key = 'YOUR_SERVICE_ROLE_KEY';
```

This allows the INSERT trigger to call Edge Functions.

### 3. Edge Function secrets

```bash
supabase secrets set \
  CLOB_API_KEY=your_key \
  CLOB_SECRET=your_secret \
  CLOB_PASS_PHRASE=your_passphrase
```

## Add a Profile

```bash
deno run --allow-env --allow-net --allow-read scripts/add-profile.ts \
  --wallet 0x63ce342161250d705dc0b16df89036c8e5f9ba9a \
  --name "Whale #1"
```

## Check Activity

```bash
deno run --allow-env --allow-net --allow-read scripts/check-activity.ts \
  --wallet 0x63ce342161250d705dc0b16df89036c8e5f9ba9a
```

## Edge Functions

| Function | Trigger | Frequency |
|---|---|---|
| `fetch-activity` | pg_cron + profile INSERT | Every 2 minutes |
| `resolve-outcomes` | pg_cron | :02/:17/:32/:47 |

## PostgREST API

Once the schema is exposed, all tables are available via the Supabase REST API:

```
GET /rest/v1/activity?profile_id=eq.{id}&order=event_timestamp.desc
GET /rest/v1/market_outcomes?is_resolved=eq.true
```
