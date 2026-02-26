# Loop Contracts and Concurrency Rules

## Objective
Prevent execution from blocking signal detection while keeping deterministic state.

## Shared Runtime Objects
- `MarketStateStore`: atomic snapshot of orderbook + windows + latest indicators.
- `IntentQueue`: candidate actions from signal loop.
- `ExecutionQueue`: validated trade intents.
- `TrackingQueue`: append-only tracking events.

## Loop Contracts

### 1) Ingest Loop
- Input: raw WS messages.
- Output: updates to `MarketStateStore`.
- Must never perform network writes besides WS connection itself.

### 2) Indicator Loop
- Input: latest state snapshot.
- Output: indicator map written atomically.
- Must complete within fixed interval and skip missed cycle rather than backlog.

### 3) Signal Loop
- Input: indicator map + exposure snapshot.
- Output: `IntentQueue` messages with reason code and confidence.
- No direct API calls.

### 4) Risk Filter
- Input: intent + current exposure + order state.
- Output: `ExecutionQueue` accepted intents OR risk reject event.
- Risk rejects are logged to `TrackingQueue`.

### 5) Execution Loop
- Input: accepted intents.
- Output: order API calls + order state updates.
- Handles cancel/replace independently from signal production.
- Must stay idempotent by client order ID strategy.

### 6) Tracking Flush Loop
- Input: `TrackingQueue`.
- Output: batched Supabase inserts.
- Failure policy: retry with backoff; never block other loops.

### 7) Event Guard Loop
- Input: event metadata + server-time checks.
- Output: graceful shutdown signal when event closes/ends.

## Non-Overlap Rules
- Signal loop can continue emitting while execution loop processes prior intents.
- Execution loop enforces per-side order throttle and dedupe.
- Last-write-wins snapshots avoid lock contention.

## Deterministic Reason Logging
Every submitted order must include:
- `reason_code`
- indicator snapshot hash
- risk snapshot hash
- decision timestamp

That guarantees post-run explainability.

