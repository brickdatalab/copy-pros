# Production Build Plan - Polymarket Event Trader

## Mission
Build a local, high-speed, event-scoped trading bot that:
- runs one process per event URL/slug
- starts at any point in a live event
- continues until event end
- computes indicators from live Polymarket WebSocket data
- executes limit orders under hard constraints
- tracks decisions + orders + outcomes in Supabase
- remains plug-and-play for indicator formulas and signal logic

This plan treats the current repo as expendable and optimizes for straight-line delivery.

## Locked Decisions (from your answers)
- Runtime language: Python first for tonight's launch speed.
- One instance per event is the operating mode.
- Market shape: binary Up/Down only (YES/NO token sides).
- AI/chirp integration: disabled in v1.
- Entry price cap: opening bets must be <= `0.80` USDC/share.
- Side-level risk:
  - max amount wagered per side per event = `10` USDC
  - max single wager amount = `10` USDC
  - min shares per purchase = `5`
  - min wager amount = `1` USDC
- Exposure checks include both filled and open orders.
- Bot can open both sides when logic says so.
- No blanket "stop opening new positions in final 30s" rule.
- Egress behavior near high prices:
  - if held side is strong and best bid reaches take-profit threshold (default trigger `0.94`), stage a take-profit limit (default `0.95`) when enough event time remains.
- Open-order cleanup at end is best-effort, not strict hard-fail.

## System Principles
1. Speed-first hot path.
2. Deterministic rule engine for decisions.
3. Non-blocking architecture: execution cannot stall signal ingestion.
4. Plugin-style indicator and signal registry.
5. Append-only tracking for post-run analysis.
6. Minimal moving parts for tonight's launch.

## High-Level Architecture

### Per-Event Process (single Python process)
Independent process with its own in-memory state and own run ID.

### Concurrent Runtime Loops
- `ws_ingest_loop`:
  - receives orderbook and trade updates
  - updates in-memory books and rolling windows
  - pushes compact snapshots to an internal queue
- `indicator_loop`:
  - computes configured indicators from rolling windows
  - updates current feature vector atomically
- `signal_loop`:
  - runs scoring/trigger logic on latest features
  - emits candidate intents (`BUY_YES`, `BUY_NO`, `TAKE_PROFIT_YES`, `TAKE_PROFIT_NO`, `HOLD`)
- `execution_loop`:
  - validates risk constraints
  - posts/cancels/reprices limit orders
  - tracks open orders and fills
- `tracking_flush_loop`:
  - batches decision/order/fill events
  - writes to Supabase asynchronously
- `event_guard_loop`:
  - recomputes remaining time
  - exits cleanly when event resolves/expires

Each loop is isolated by queues and snapshots so trade execution does not block signal scanning.

## Drone-Like Order of Operations
1. Parse event URL/slug.
2. Fetch event + market metadata from Polymarket.
3. Determine event duration (`5m`/`15m`) and remaining time.
4. Resolve token IDs (Up/Down).
5. Initialize run context + risk counters.
6. Connect WebSocket and start loops.
7. Compute indicators continuously.
8. Run trigger logic continuously.
9. Place/cancel/replace limit orders under constraints.
10. Track all signals, decisions, orders, fills in Supabase.
11. On event end, reconcile market outcome.
12. Mark prediction accuracy for this run.
13. Emit terminal summary and exit.

## Plug-and-Play Indicator Design
Indicator modules share one interface:
- input: `MarketState` + `now`
- output: `{name, value, ts}`

Registry pattern:
- config enables/disables indicator modules
- config sets weights and lookbacks
- adding/removing indicators does not change core engine

Initial indicator set:
- `vwap_30s`
- `vwap_1m`
- `vwap_5m` (if enough history)
- `order_imbalance_10s`
- `order_imbalance_30s`
- `spread_momentum_15s`
- `spread_momentum_30s`
- `mid_momentum_15s`
- `mid_momentum_30s`
- `mid_momentum_1m`
- `microprice_bias`
- `book_pressure_delta`

## Plug-and-Play Signal Logic
Signal modules also use a registry.
Each module outputs:
- side intent
- confidence
- reason code

Final intent resolver:
- weighted aggregate of active signal modules
- conflict resolver for opposite intents
- cooldown/throttle per side
- pass-through to risk engine

No hard coupling between indicator formulas and order router.

## Risk and Sizing Rules (v1)
Entry orders:
- reject if entry price > `0.80`
- reject if wager < `1.00`
- enforce `min_shares_per_purchase = 5`
- cap by `max_single_wager_amount = 10`
- cap by remaining side budget (`max_amount_wagered_per_side = 10`)

Take-profit orders:
- allowed above `0.80` because they reduce risk/realize gain
- trigger threshold configurable (`TAKE_PROFIT_TRIGGER=0.94`, `TAKE_PROFIT_LIMIT=0.95`)
- can be time-gated (only if remaining time > configurable minimum)

## Speed Budget Targets
- WS parse + state update: p95 < 5ms
- indicator compute cycle: p95 < 15ms
- signal decision cycle: p95 < 10ms
- order submit request construction: p95 < 5ms
- Supabase flush loop: async, non-blocking, batch every 150-300ms

## Supabase Tracking Strategy
Track, not drive, trading decisions.
Runtime decisioning remains local.

Event types to persist:
- `signal_snapshot`
- `decision`
- `order_submitted`
- `order_cancelled`
- `order_repriced`
- `fill`
- `risk_reject`
- `event_closed`
- `run_summary`
- `prediction_accuracy`

Schema is in `build-plan/SUPABASE_TRACKING_SCHEMA.sql`.

## UI/TUI Direction (from video frames)
Terminal should stay clean and operator-fast:
- top header: event, side exposure, remaining time
- middle panels:
  - live indicators
  - active logic reasons
  - order/fill tape
- footer: latency + health counters
- color coding:
  - green success/fill/profit
  - yellow pending/cancel/replace
  - red rejects/errors/risk blocks

No decorative overhead; optimize scan speed.

## Existing Repo Reuse vs Replace
Reuse as reference only:
- `scripts/stream_market.py` orderbook/indicator patterns
- Polymarket auth logic from `supabase/functions/_shared/polymarket.ts`

Replace/retire for v1 runtime:
- old Supabase-first streamer persistence path
- edge-function cron ingestion path for trade decisions

## Implementation Phases
1. Scaffold new runtime package + CLI entrypoint.
2. Implement metadata resolver + event clock.
3. Implement WS ingestion and state engine.
4. Implement indicator + signal registries.
5. Implement risk engine and order executor.
6. Add Supabase tracking pipeline.
7. Add outcome reconciliation and accuracy logging.
8. Add operator-first terminal UI.
9. Run dry-run soak, then live run.

## Non-Negotiables During Build
- No fake runtime components.
- No blocking network call in hot decision path except required order API call.
- No hidden global mutable state across processes.
- No hardcoded event-specific logic.
- Every trigger and order action must have an explainable reason code.

