# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Copy Pros Trader — a high-speed local Python bot for Polymarket directional event markets (BTC/ETH/SOL, 5m and 15m timeframes). It places limit orders only, deciding `BUY_UP` or `BUY_DOWN` per event based on deterministic signal scoring from WebSocket order book and trade data.

## Commands

```bash
# Install (editable)
python -m pip install -e .
python -m pip install -e ".[dev]"

# Lint, typecheck, test
ruff check trader tests scripts/run_event_bot.py scripts/run_continuous_bot.py scripts/run_local_playground.py
mypy trader
pytest -q

# Run single test file
pytest tests/strategy/test_decision_policy.py -q

# Run one event (dry run)
python scripts/run_event_bot.py --event <slug-or-url> --mode dry_run

# Run continuous multi-market session
python scripts/run_continuous_bot.py --mode dry_run --markets btc15,eth15,sol15 --duration-minutes 60

# Run localhost control UI (FastAPI + SSE on port 8080)
python scripts/run_local_playground.py
```

## Architecture

The data pipeline is strictly linear and async:

```
Polymarket REST → EventMarketContext (token IDs, timestamps)
Polymarket WS   → MarketState (order books + rolling trade windows)
                 → IndicatorEngine (vwap, momentum, imbalance, flow metrics)
                 → DecisionPolicy (score → BUY_UP / BUY_DOWN / HOLD)
                 → EntryPolicy (streak, cooldown, hedge gates)
                 → Sizer + RiskValidator (price/wager caps)
                 → OrderRouter (limit order build + submit)
                 → Async Supabase writer (non-blocking telemetry)
```

### Key module boundaries

- **`trader/config.py`** — Frozen `TraderConfig` dataclass; all params are env-driven via `.env`. The `load_config()` function is the single source of truth.
- **`trader/engine/state.py`** — `MarketState` holds mutable rolling windows (trades, spreads, mid prices, flow tracking). Pruned to 300s continuously.
- **`trader/engine/indicators/__init__.py`** — `IndicatorEngine.compute()` produces a `dict[str, IndicatorValue]` snapshot from `MarketState`. Pure computation, no side effects.
- **`trader/strategy/decision_policy.py`** — `DecisionPolicy.evaluate()` scores UP vs DOWN using weighted indicators. Two weight presets: `baseline` (legacy 4-indicator) and `flow_v1` (adds `ew_delta_imbalance`, `flow_toxicity`, `large_trade_ratio`). Returns `DecisionResult` with action, confidence, edge, reason code.
- **`trader/strategy/entry_policy.py`** — `evaluate_entry_policy()` gates entries with signal persistence, cooldown, hedge rules, and flow-against-direction hard block.
- **`trader/risk/sizer.py`** + **`trader/risk/constraints.py`** — `propose_order_size()` and `validate_order()`. Enforces price caps, wager caps, min shares, side budgets.
- **`trader/runtime/orchestrator.py`** — `EventOrchestrator` runs one event end-to-end: WS ingest loop, indicator loop, decision loop, execution loop, reconciliation.
- **`trader/runtime/continuous_runner.py`** — Runs multiple market workers in parallel, auto-discovers next active slug on event end.
- **`trader/adapters/polymarket/`** — REST discovery (`rest_client.py`), WebSocket stream (`ws_client.py`), live CLOB orders (`trading_client.py`).
- **`trader/adapters/supabase/writer.py`** — `BufferedSupabaseWriter` buffers rows and flushes async. Tables in `copy_pros` schema.

### Test structure

Tests mirror the `trader/` package: `tests/strategy/`, `tests/engine/`, `tests/risk/`, `tests/runtime/`, `tests/execution/`, `tests/adapters/`, `tests/playground/`. All tests are async-compatible (`asyncio_mode = "auto"` in pyproject.toml).

## Important Conventions

- **Python 3.11+** required. Uses `StrEnum`, `from __future__ import annotations`, `dataclass(frozen=True)` patterns throughout.
- **mypy strict mode** is enforced (`strict = true` in pyproject.toml).
- **ruff** line length is 100 chars, target `py311`.
- **All config values come from environment variables** — never hardcode thresholds. Add new params to `TraderConfig`, `load_config()`, and `.env.example`.
- **Two bot modes**: `dry_run` (simulated instant fills) and `live` (real CLOB orders). Mode affects execution path in orchestrator.
- **Deterministic signals only** — no randomness in decision logic. Same indicator snapshot must produce same decision.
- **Supabase writes are non-blocking** — if Supabase is unreachable, trading continues unaffected.
- **Supabase schema**: `copy_pros`. Migration files in `supabase/migrations/` numbered sequentially.

## Critical Domain Rules

- **95-cent discipline**: take-profit triggers at 0.94, limit sells at 0.95. This is the core exit strategy.
- **Reversal confidence relaxation**: only applies when `reversal_imminent == true`, candidate is `BUY_UP`, and entry price < 0.25. Lowers min confidence from 0.52 to 0.40.
- **Flow-against-direction hard gate**: blocks entries when `ew_delta_imbalance` strongly opposes the intended direction (threshold: 0.10).
- **Weight presets**: `flow_v1`/`v1` is the default active preset. `baseline` is legacy. The preset name is stored in indicator snapshots as `flow_weight_preset`.
- Operating philosophy documented in `docs/OPERATING_PHILOSOPHY.md`.
