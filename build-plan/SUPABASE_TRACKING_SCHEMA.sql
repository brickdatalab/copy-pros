-- Tracking schema for bot decisions/orders/fills (non-blocking analytics path)
-- This SQL can be used directly as migration 017.

create table if not exists copy_pros.bot_runs (
  id uuid primary key default gen_random_uuid(),
  run_tag text unique not null,
  event_slug text not null,
  event_id text,
  market_slug text,
  symbol text,
  timeframe_minutes int not null check (timeframe_minutes in (5, 15)),
  mode text not null check (mode in ('live', 'dry_run')),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  status text not null default 'running' check (status in ('running', 'completed', 'failed', 'cancelled')),
  final_winning_side text check (final_winning_side in ('UP', 'DOWN')),
  was_prediction_accurate boolean,
  total_decisions int not null default 0,
  total_orders int not null default 0,
  total_fills int not null default 0,
  gross_notional numeric(18,6) not null default 0,
  gross_realized_pnl numeric(18,6) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_bot_runs_started_at
  on copy_pros.bot_runs (started_at desc);
create index if not exists idx_bot_runs_event_slug
  on copy_pros.bot_runs (event_slug, started_at desc);

create table if not exists copy_pros.bot_decisions (
  id bigserial primary key,
  run_id uuid not null references copy_pros.bot_runs(id) on delete cascade,
  ts timestamptz not null default now(),
  remaining_seconds int,
  action text not null check (action in ('BUY_UP', 'BUY_DOWN', 'TAKE_PROFIT_UP', 'TAKE_PROFIT_DOWN', 'HOLD', 'SKIP')),
  confidence numeric(8,6),
  reason_code text not null,
  reason_details jsonb not null default '{}'::jsonb,
  indicator_snapshot jsonb not null default '{}'::jsonb,
  risk_snapshot jsonb not null default '{}'::jsonb
);

create index if not exists idx_bot_decisions_run_ts
  on copy_pros.bot_decisions (run_id, ts desc);

create table if not exists copy_pros.bot_orders (
  id bigserial primary key,
  run_id uuid not null references copy_pros.bot_runs(id) on delete cascade,
  decision_id bigint references copy_pros.bot_decisions(id) on delete set null,
  ts timestamptz not null default now(),
  client_order_id text not null unique,
  exchange_order_id text,
  action text not null check (action in ('BUY_UP', 'BUY_DOWN', 'SELL_UP', 'SELL_DOWN', 'CANCEL', 'REPRICE')),
  side text not null check (side in ('UP', 'DOWN')),
  limit_price numeric(10,6) not null,
  shares numeric(18,6) not null,
  wager_usdc numeric(18,6) not null,
  status text not null check (status in ('submitted', 'accepted', 'partial_fill', 'filled', 'cancelled', 'rejected', 'expired')),
  rejection_reason text,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_bot_orders_run_ts
  on copy_pros.bot_orders (run_id, ts desc);
create index if not exists idx_bot_orders_status
  on copy_pros.bot_orders (status, ts desc);

create table if not exists copy_pros.bot_fills (
  id bigserial primary key,
  run_id uuid not null references copy_pros.bot_runs(id) on delete cascade,
  order_id bigint not null references copy_pros.bot_orders(id) on delete cascade,
  ts timestamptz not null default now(),
  fill_id text unique,
  fill_price numeric(10,6) not null,
  fill_shares numeric(18,6) not null,
  fill_notional_usdc numeric(18,6) not null,
  fee_usdc numeric(18,6),
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_bot_fills_run_ts
  on copy_pros.bot_fills (run_id, ts desc);

create table if not exists copy_pros.bot_runtime_events (
  id bigserial primary key,
  run_id uuid not null references copy_pros.bot_runs(id) on delete cascade,
  ts timestamptz not null default now(),
  level text not null check (level in ('info', 'warn', 'error')),
  event_type text not null,
  payload jsonb not null default '{}'::jsonb
);

create index if not exists idx_bot_runtime_events_run_ts
  on copy_pros.bot_runtime_events (run_id, ts desc);

-- Public read-only access (matching current project style)
grant select on copy_pros.bot_runs to anon, authenticated;
grant select on copy_pros.bot_decisions to anon, authenticated;
grant select on copy_pros.bot_orders to anon, authenticated;
grant select on copy_pros.bot_fills to anon, authenticated;
grant select on copy_pros.bot_runtime_events to anon, authenticated;

grant all on copy_pros.bot_runs to service_role;
grant all on copy_pros.bot_decisions to service_role;
grant all on copy_pros.bot_orders to service_role;
grant all on copy_pros.bot_fills to service_role;
grant all on copy_pros.bot_runtime_events to service_role;

grant usage, select on all sequences in schema copy_pros to service_role;

