# Event Trader Runtime

## What this does
Runs one local process per Polymarket event (URL or slug), computes local indicators from the market websocket, generates directional decisions, and places constrained limit orders.

## Key constraints implemented
- Entry price cap: `<= 0.80` USDC/share
- Max amount wagered per side per event: `10` USDC (default)
- Max single wager amount: `10` USDC (default)
- Min shares per purchase: `5` (default)
- Min wager amount: `1` USDC (default)
- Directional conviction gate + signal persistence before entry
- Optional opposite-side hedging with strict ratio/confidence/price caps
- Tracks decisions/orders/runtime events in Supabase asynchronously

## Run
```bash
python scripts/run_event_bot.py --event https://polymarket.com/event/<slug> --mode dry_run
python scripts/run_event_bot.py --event <slug> --mode live
```

## Continuous market mode
Runs selected streams continuously (auto-rotates to each next event slug):

```bash
python scripts/run_continuous_bot.py \
  --mode dry_run \
  --markets btc15,eth15,sol15,btc5,eth5,sol5 \
  --duration-minutes 60
```

CLI controls during run:
- `p` pause/resume (halts decision + execution loops)
- `q` stop gracefully and print final summary

Final run artifacts:
- JSON report in `runtime-logs/continuous_report_*.json`

## Local interactive playground (localhost)
Start the local UI server:

```bash
python scripts/run_local_playground.py
```

Open:
- `http://127.0.0.1:8080`

From the page you can:
- select markets
- start a session
- pause/resume
- stop and view rollups + report path

## Required env
- `POLY_PRIVATE_KEY`
- `CLOB_ADDRESS`
- `CLOB_API_KEY`
- `CLOB_SECRET`
- `CLOB_PASS_PHRASE`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

See `build-plan/ENV_SPEC.md` for full list.

## Supabase migration
Apply:
- `supabase/migrations/017_bot_tracking_tables.sql`

This adds:
- `bot_runs`
- `bot_decisions`
- `bot_orders`
- `bot_fills`
- `bot_runtime_events`
