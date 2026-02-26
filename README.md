# Copy Pros Trader

Local high-speed Polymarket directional trader for BTC/ETH/SOL 5m and 15m events.

This repo now focuses on one active runtime stack:
- `trader/` Python runtime
- `scripts/run_event_bot.py` single-event runner
- `scripts/run_continuous_bot.py` rotating multi-market runner
- `scripts/run_local_playground.py` localhost control + monitoring UI

## What It Does
- Connects to Polymarket CLOB websocket per event.
- Computes local indicators (VWAP, imbalance, spread/mid momentum, mid price).
- Applies deterministic decision logic (`BUY_UP`, `BUY_DOWN`, `HOLD`).
- Places constrained limit orders (live mode) or paper trades (dry run).
- Tracks runs/decisions/orders/runtime events asynchronously into Supabase.

## Core Constraints
- Max entry price: `MAX_ENTRY_PRICE` (default `0.80`)
- Max wager per side/event: `MAX_WAGER_PER_SIDE_USDC`
- Max single wager: `MAX_SINGLE_WAGER_USDC`
- Min wager and shares floor: `MIN_WAGER_USDC`, `MIN_SHARES_PER_PURCHASE`
- Optional take-profit behavior via `TAKE_PROFIT_*` env vars

## Quick Start
1. Install:
```bash
python -m pip install -e .
```

2. Configure `.env` (copy from `.env.example`).

3. Run one event:
```bash
python scripts/run_event_bot.py --event https://polymarket.com/event/<slug> --mode dry_run
```

4. Run continuous rotation:
```bash
python scripts/run_continuous_bot.py \
  --mode dry_run \
  --markets btc15,eth15,sol15,btc5,eth5,sol5 \
  --duration-minutes 60
```

5. Run local playground:
```bash
python scripts/run_local_playground.py
```
Open `http://127.0.0.1:8080`.

## Environment Variables
Primary runtime limits are controlled in `.env`:
- `MAX_WAGER_PER_SIDE_USDC`
- `MAX_SINGLE_WAGER_USDC`
- `MIN_WAGER_USDC`
- `MIN_SHARES_PER_PURCHASE`
- `MAX_ENTRY_PRICE`

Full env reference: `build-plan/ENV_SPEC.md`

## Supabase
Tracking tables migration:
- `supabase/migrations/017_bot_tracking_tables.sql`

Current runtime writes to:
- `bot_runs`
- `bot_decisions`
- `bot_orders`
- `bot_runtime_events`

## Verification
```bash
ruff check trader tests scripts/run_event_bot.py scripts/run_continuous_bot.py scripts/run_local_playground.py
mypy trader
pytest -q
```

