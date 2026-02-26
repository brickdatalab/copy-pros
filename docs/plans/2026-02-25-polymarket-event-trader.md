# Polymarket Event Trader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local, high-speed, multi-instance Python trading bot that accepts one active Polymarket event per process, computes real-time indicators from live orderbook/trade data, and executes constrained limit orders until event expiration.

**Architecture:** Use a modular-monolith Python service per event instance (one process = one event) with an async event loop and strict module boundaries: feed ingestion, indicator engine, decision engine, risk guardrails, execution, and telemetry. Keep write-path overhead minimal by running in-memory state first and deferring heavy persistence/reporting until event end, with optional async low-overhead snapshots. Supabase is treated as an optional auxiliary signal source (bounded influence), never as a hard dependency for runtime decisions.

**Tech Stack:** Python 3.11+, asyncio, websockets, httpx, asyncpg (optional), sqlite3 (optional local ledger), pytest, mypy, ruff, rich, pydantic-settings (or dataclass config), uv (or pip) for dependency management.

---

## Locked Constraints (from current requirements)

- Run locally on MacBook M3 Max; multiple concurrent terminal instances.
- Each process is pinned to one event (5m or 15m BTC/ETH/SOL market set).
- Start at any point in event lifetime, run until event end.
- Limit orders only.
- Price cap: do not place orders above $0.80.
- Position constraints:
  - per-order share cap (default 10, configurable)
  - side-level max shares (default 100, configurable)
  - side-level max dollars (configurable, supersedes share cap)
  - minimum notional floor supported (default $1.00).
- Strategy focus is directional accuracy, no arbitrage-specific logic.
- Bot may place multiple orders over event lifetime and may hold both sides if logic warrants.
- If Supabase or external APIs lag, bot must continue on local data.
- Optional AI assist must be bounded by low-latency budget and not block order path.
- Dry-run mode required.
- Colorized CLI monitoring required.

## Architecture Decision

### Option A: Keep Supabase-centric write/read loop
- Pros: reuses existing tables and scripts.
- Cons: added latency and network dependency in hot path; directly conflicts with low-overhead requirement.

### Option B: Pure local engine, Supabase disabled entirely
- Pros: fastest runtime path.
- Cons: loses optional external signal enrichment and post-event central analytics.

### Option C (Chosen): Local-first execution + optional async enrichment
- Pros: preserves speed and resiliency while retaining optional external signal value.
- Cons: more moving parts than pure local.

Decision: Option C.

---

### Task 1: Bootstrap runtime package, tooling, and test harness

**Files:**
- Create: `pyproject.toml`
- Create: `trader/__init__.py`
- Create: `trader/main.py`
- Create: `trader/config.py`
- Create: `tests/test_config.py`
- Create: `tests/test_smoke_import.py`
- Modify: `.gitignore`

**Step 1: Write the failing test**

```python
def test_config_requires_event_identifier(monkeypatch):
    monkeypatch.delenv("EVENT_ID", raising=False)
    from trader.config import load_config
    with pytest.raises(ValueError):
        load_config()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_config_requires_event_identifier -v`
Expected: FAIL due to missing `load_config` implementation.

**Step 3: Write minimal implementation**

Implement typed config loader with required env keys and defaults for risk constraints.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_config_requires_event_identifier -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add pyproject.toml trader tests .gitignore
git commit -m "chore: bootstrap trader package and typed config"
```

### Task 2: Event lifecycle resolver (remaining runtime window)

**Files:**
- Create: `trader/event_context.py`
- Test: `tests/test_event_context.py`

**Step 1: Write the failing test**

```python
def test_remaining_seconds_mid_event():
    ctx = build_event_context(start_ts=0, duration_sec=900, now_ts=420)
    assert ctx.remaining_sec == 480
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_event_context.py::test_remaining_seconds_mid_event -v`
Expected: FAIL because builder is missing.

**Step 3: Write minimal implementation**

Implement event context parser that supports slug/id inputs and computes remaining duration safely using server time fallback and monotonic clock.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_event_context.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/event_context.py tests/test_event_context.py
git commit -m "feat: add event lifecycle and remaining-time resolver"
```

### Task 3: Polymarket adapters (REST + WebSocket + auth)

**Files:**
- Create: `trader/adapters/polymarket/auth.py`
- Create: `trader/adapters/polymarket/http_client.py`
- Create: `trader/adapters/polymarket/ws_client.py`
- Create: `trader/adapters/polymarket/models.py`
- Test: `tests/adapters/test_poly_auth.py`
- Test: `tests/adapters/test_ws_decode.py`

**Step 1: Write the failing test**

```python
def test_l2_header_contains_required_fields():
    headers = build_l2_headers("GET", "/data/orders")
    assert "POLY_API_KEY" in headers
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/adapters/test_poly_auth.py::test_l2_header_contains_required_fields -v`
Expected: FAIL because adapter not implemented.

**Step 3: Write minimal implementation**

Implement signed headers, resilient REST wrapper, and websocket consumer yielding normalized tick events.

**Step 4: Run test to verify it passes**

Run: `pytest tests/adapters/test_poly_auth.py tests/adapters/test_ws_decode.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/adapters tests/adapters
git commit -m "feat: add polymarket rest/ws adapters with auth"
```

### Task 4: In-memory orderbook and indicator engine

**Files:**
- Create: `trader/engine/orderbook.py`
- Create: `trader/engine/indicators.py`
- Create: `trader/engine/state.py`
- Test: `tests/engine/test_orderbook.py`
- Test: `tests/engine/test_indicators.py`

**Step 1: Write the failing test**

```python
def test_vwap_1m_uses_recent_trades_only():
    eng = IndicatorEngine()
    # append trades at t-70s and t-10s
    assert eng.vwap_1m(now_ts=100) == pytest.approx(0.62)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/engine/test_indicators.py::test_vwap_1m_uses_recent_trades_only -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement high-frequency rolling windows for vwap/order imbalance/spread momentum/mid momentum across required horizons.

**Step 4: Run test to verify it passes**

Run: `pytest tests/engine/test_orderbook.py tests/engine/test_indicators.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/engine tests/engine
git commit -m "feat: add orderbook and realtime indicator engine"
```

### Task 5: Decision policy (local-first, optional auxiliary signals)

**Files:**
- Create: `trader/strategy/feature_vector.py`
- Create: `trader/strategy/decision_policy.py`
- Create: `trader/strategy/scoring.py`
- Test: `tests/strategy/test_decision_policy.py`

**Step 1: Write the failing test**

```python
def test_external_signal_weight_cannot_exceed_0_51():
    w = blend_weights(local_weight=0.4, external_weight=0.7)
    assert w.external <= 0.51
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/strategy/test_decision_policy.py::test_external_signal_weight_cannot_exceed_0_51 -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement deterministic scoring + thresholds for UP/DOWN/NO_TRADE with confidence and decay by time remaining.

**Step 4: Run test to verify it passes**

Run: `pytest tests/strategy/test_decision_policy.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/strategy tests/strategy
git commit -m "feat: add decision policy with bounded external influence"
```

### Task 6: Risk, sizing, and guardrail engine

**Files:**
- Create: `trader/risk/constraints.py`
- Create: `trader/risk/sizer.py`
- Test: `tests/risk/test_constraints.py`
- Test: `tests/risk/test_sizer.py`

**Step 1: Write the failing test**

```python
def test_reject_order_price_above_cap():
    assert not validate_order(price=0.81, shares=2).allowed
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/risk/test_constraints.py::test_reject_order_price_above_cap -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement price cap, per-order shares, side max shares, side max dollars precedence, and minimum notional floor behavior.

**Step 4: Run test to verify it passes**

Run: `pytest tests/risk/test_constraints.py tests/risk/test_sizer.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/risk tests/risk
git commit -m "feat: add risk guards and order sizing"
```

### Task 7: Execution engine (limit order lifecycle)

**Files:**
- Create: `trader/execution/order_router.py`
- Create: `trader/execution/order_tracker.py`
- Create: `trader/execution/cancel_replace.py`
- Test: `tests/execution/test_order_router.py`
- Test: `tests/execution/test_cancel_replace.py`

**Step 1: Write the failing test**

```python
def test_cancel_unfilled_when_momentum_reversal():
    assert should_cancel(order_age_s=3.0, spread_momentum=0.22, mid_momentum_30s=-0.18)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/execution/test_cancel_replace.py::test_cancel_unfilled_when_momentum_reversal -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement place/refresh/cancel order lifecycle and slippage-aware repricing logic.

**Step 4: Run test to verify it passes**

Run: `pytest tests/execution/test_order_router.py tests/execution/test_cancel_replace.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/execution tests/execution
git commit -m "feat: add limit order execution lifecycle"
```

### Task 8: Async orchestration per event instance

**Files:**
- Create: `trader/runtime/orchestrator.py`
- Create: `trader/runtime/tasks.py`
- Test: `tests/runtime/test_orchestrator.py`

**Step 1: Write the failing test**

```python
def test_orchestrator_stops_at_event_end(fake_clock):
    assert run_until_end(duration_sec=90, start_offset=80).runtime_sec == 10
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/runtime/test_orchestrator.py::test_orchestrator_stops_at_event_end -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement concurrent tasks: feed ingest, indicator update, decision loop, execution loop, account sync, graceful shutdown at event expiry.

**Step 4: Run test to verify it passes**

Run: `pytest tests/runtime/test_orchestrator.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/runtime tests/runtime
git commit -m "feat: add per-event async orchestrator"
```

### Task 9: Telemetry, CLI UX, and dry-run controls

**Files:**
- Create: `trader/telemetry/console.py`
- Create: `trader/cli.py`
- Create: `scripts/run_event_bot.py`
- Test: `tests/test_cli.py`

**Step 1: Write the failing test**

```python
def test_cli_requires_event_and_mode(capsys):
    with pytest.raises(SystemExit):
        main([])
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::test_cli_requires_event_and_mode -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement `--event`, `--duration`, `--dry-run`, `--profile`, and colorized summaries of signals/orders/fills/risk locks.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/telemetry trader/cli.py scripts/run_event_bot.py tests/test_cli.py
git commit -m "feat: add cli and realtime colored monitoring"
```

### Task 10: Optional Supabase + external model adapters (non-blocking)

**Files:**
- Create: `trader/adapters/supabase_features.py`
- Create: `trader/adapters/aux_model.py`
- Test: `tests/adapters/test_supabase_features.py`
- Test: `tests/adapters/test_aux_model.py`

**Step 1: Write the failing test**

```python
def test_trading_continues_when_supabase_timeout():
    assert enrichment_result.fallback_to_local is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/adapters/test_supabase_features.py::test_trading_continues_when_supabase_timeout -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement strict timeout budgets and async fire-and-forget enrichment updates. Enrichment failure never blocks decision/execution loops.

**Step 4: Run test to verify it passes**

Run: `pytest tests/adapters/test_supabase_features.py tests/adapters/test_aux_model.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/adapters tests/adapters
git commit -m "feat: add non-blocking supabase and aux model integrations"
```

### Task 11: Session ledger + end-of-event reporting

**Files:**
- Create: `trader/storage/ledger.py`
- Create: `trader/storage/report.py`
- Test: `tests/storage/test_ledger.py`
- Test: `tests/storage/test_report.py`

**Step 1: Write the failing test**

```python
def test_report_contains_order_fill_and_pnl_summary(tmp_path):
    report = build_report(tmp_path / "run.sqlite")
    assert "fills" in report
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_ledger.py::test_report_contains_order_fill_and_pnl_summary -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement low-overhead local ledger writes (buffered), and end-of-event summary export JSON/CSV plus optional Supabase upload.

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_ledger.py tests/storage/test_report.py -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add trader/storage tests/storage
git commit -m "feat: add lightweight session ledger and event-end reports"
```

### Task 12: Replay backtester, performance budget, and final verification

**Files:**
- Create: `trader/replay/replay_runner.py`
- Create: `tests/replay/test_replay_regression.py`
- Create: `Makefile`
- Create: `README_TRADER.md`

**Step 1: Write the failing test**

```python
def test_replay_meets_decision_latency_budget():
    stats = replay_fixture("fixtures/ws_sample.jsonl")
    assert stats.p95_decision_ms < 120
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/replay/test_replay_regression.py::test_replay_meets_decision_latency_budget -v`
Expected: FAIL.

**Step 3: Write minimal implementation**

Implement replay harness for deterministic tuning, then tune task cadences and queue sizes.

**Step 4: Run test to verify it passes**

Run: `pytest -q`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add trader tests Makefile README_TRADER.md
git commit -m "feat: add replay regression and production runbook"
```

---

## API Contracts (for integrations)

### Auxiliary model endpoint contract (non-blocking)
- Request: compact feature snapshot + event metadata + time remaining.
- Response JSON:
  - `action`: `UP | DOWN | HOLD`
  - `confidence`: `0.0-1.0`
  - `ttl_ms`: integer
  - `reason_code`: string enum
- Runtime guardrails:
  - strict timeout (default 120ms)
  - if timeout/error, ignore and continue local policy.

### Optional Supabase feature endpoint contract
- Request includes event id + market ids + current monotonic timestamp.
- Response includes bounded supplemental features only.
- Weighting cap enforced in policy: external contribution <= 0.51.

---

## Verification Commands (before any completion claims)

```bash
pytest -q
mypy trader --strict
ruff check trader tests
python scripts/run_event_bot.py --event <event_id_or_slug> --dry-run
python scripts/run_event_bot.py --event <event_id_or_slug> --live
```

Expected:
- tests/lint/typecheck all pass.
- dry-run prints decision/order simulation with no live orders.
- live mode starts, trades until event end, then exits gracefully with summary.

---

## Known Tradeoffs

- Profitability cannot be guaranteed; the system is optimized for directional accuracy and execution discipline under strict constraints.
- Supabase is treated as an enhancement layer, not a required dependency, to avoid runtime fragility.
- External model integration is optional and hard-timeboxed to preserve speed.
