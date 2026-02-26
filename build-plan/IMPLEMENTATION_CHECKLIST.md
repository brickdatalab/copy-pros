# Implementation Checklist (Production-First)

## Phase 0 - Repo Reset
- [ ] Archive old scripts/docs that are no longer needed.
- [ ] Keep reference snippets for:
  - Polymarket auth/signing
  - WS message handling
  - event registration resolution logic
- [ ] Create new `trader/` runtime package skeleton.

## Phase 1 - Event-Scoped Runtime Boot
- [ ] CLI input supports event URL or slug.
- [ ] Resolve event metadata and detect timeframe (5m/15m).
- [ ] Compute remaining time from server timestamps.
- [ ] Refuse run if event already closed.
- [ ] Create run record in Supabase at startup.

Acceptance:
- Start bot at t+7m in 15m event and runtime target is ~8m.
- Start bot at t+13:30 in 15m event and runtime target is ~90s.

## Phase 2 - WS Feed and In-Memory State
- [ ] Subscribe to event token IDs.
- [ ] Maintain in-memory orderbooks for Up/Down tokens.
- [ ] Maintain rolling windows for trades and book states.
- [ ] Keep message parse/update path allocation-light.

Acceptance:
- Continuous updates without blocking for at least 30 minutes.
- Reconnect behavior is automatic and quick.

## Phase 3 - Indicator Registry
- [ ] Implement base indicator interface.
- [ ] Implement first indicator set:
  - vwap_30s, vwap_1m, vwap_5m
  - order_imbalance_10s, order_imbalance_30s
  - spread_momentum_15s, spread_momentum_30s
  - mid_momentum_15s, mid_momentum_30s, mid_momentum_1m
- [ ] Enable via config list; no hardcoded references in orchestration layer.

Acceptance:
- Add/remove indicator module without editing orchestration code.

## Phase 4 - Signal Registry and Decision Resolver
- [ ] Implement signal module interface and registry.
- [ ] Add initial rule modules:
  - momentum alignment
  - spread tightening continuation
  - pressure breakout
- [ ] Aggregate weighted outputs into final action.
- [ ] Emit reason codes and confidence every decision cycle.

Acceptance:
- Decision loop runs while execution loop is active (no overlap stalls).

## Phase 5 - Risk + Sizing + Execution
- [ ] Enforce entry cap (`<= 0.80`) for opening bets.
- [ ] Enforce min wager and min shares.
- [ ] Enforce side budget with open + filled exposure.
- [ ] Support both-side positions.
- [ ] Implement cancel/replace on momentum reversal.
- [ ] Implement take-profit trigger near high prices.

Acceptance:
- No entry orders above 0.80.
- No wager below 1 USDC.
- No side budget breach.

## Phase 6 - Tracking Pipeline (Supabase)
- [ ] Batch-write decisions, orders, fills, runtime events.
- [ ] Non-blocking writer queue with timeout + retry.
- [ ] Write run summary on shutdown.
- [ ] Resolve final event outcome and set prediction accuracy.

Acceptance:
- Trading continues even if Supabase temporarily fails.
- Full run audit trail available in tables.

## Phase 7 - Terminal Operator UX
- [ ] Build compact, high-contrast terminal dashboard.
- [ ] Panels:
  - event/time/status
  - indicator state
  - decision reasons
  - order/fill tape
  - risk budget consumption
  - latency/health
- [ ] Color coding for success/pending/errors.

Acceptance:
- Two or more bot instances are easy to monitor side-by-side.

## Phase 8 - Launch Protocol
- [ ] Dry-run for 30+ minutes across multiple events.
- [ ] Live run with conservative limits.
- [ ] 24h soak with log review and tuning notes.

## Tuning Knobs (No Code Overhaul)
- [ ] Indicator enable/disable list.
- [ ] Indicator lookbacks.
- [ ] Signal weights.
- [ ] Trigger thresholds.
- [ ] Execution cadence and reprice timing.
- [ ] Risk budget values.

## Out-of-Scope for v1
- AI decision API.
- Chirp/synthetic integrations.
- Vercel deployment.
- Rust rewrite.

