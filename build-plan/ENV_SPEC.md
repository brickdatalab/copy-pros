# Environment Spec (v1)

## Core Runtime
```env
# Required
POLY_EVENT_INPUT=
POLY_PRIVATE_KEY=
CLOB_ADDRESS=
CLOB_API_KEY=
CLOB_SECRET=
CLOB_PASS_PHRASE=

# Mode
BOT_MODE=live                 # live | dry_run
LOG_LEVEL=INFO

# Runtime cadence (milliseconds)
INDICATOR_INTERVAL_MS=100
SIGNAL_INTERVAL_MS=100
EXECUTION_INTERVAL_MS=75
SUPABASE_FLUSH_INTERVAL_MS=200

# Event behavior
AUTO_DETECT_DURATION=true     # 5m or 15m inferred from market metadata/slug
MIN_REMAINING_SECONDS_TO_RUN=1
DISALLOW_DUPLICATE_EVENT_RUN=true

# Entry constraints
MAX_ENTRY_PRICE=0.80
MAX_WAGER_PER_SIDE_USDC=10
MAX_SINGLE_WAGER_USDC=10
MIN_WAGER_USDC=1
MIN_SHARES_PER_PURCHASE=5

# Side/position limits
COUNT_OPEN_ORDERS_IN_EXPOSURE=true
ALLOW_BOTH_SIDES=true

# Take-profit behavior (for open positions)
ENABLE_TAKE_PROFIT=true
TAKE_PROFIT_TRIGGER_PRICE=0.94
TAKE_PROFIT_LIMIT_PRICE=0.95
TAKE_PROFIT_MIN_REMAINING_SEC=120

# Latency / execution controls
ORDER_REPRICE_COOLDOWN_MS=250
ORDER_CANCEL_ON_MOMENTUM_REVERSAL=true
MAX_HTTP_RETRY=2
HTTP_TIMEOUT_MS=800

# Supabase tracking (non-blocking)
SUPABASE_ENABLED=true
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_SCHEMA=copy_pros
SUPABASE_TRACKING_TIMEOUT_MS=250
SUPABASE_BATCH_SIZE=100

# AI/chirp integration (disabled in v1)
AUX_SIGNALS_ENABLED=false
```

## Notes
- `MAX_ENTRY_PRICE` only applies to opening bets.
- Take-profit orders can be above `0.80` because they close/reduce risk.
- `MIN_WAGER_USDC` and `MIN_SHARES_PER_PURCHASE` are both enforced; effective order size must satisfy both.
- Supabase writes are buffered and async; failures never block trading loop.
