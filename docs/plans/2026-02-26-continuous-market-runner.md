# Continuous Multi-Market Runner Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run selected BTC/ETH/SOL 5m and 15m markets continuously in parallel for a fixed duration, auto-rotating to each next event with paper trades and clean stop/pause summary output.

**Architecture:** Add a supervisor loop that owns one worker per selected market stream (`symbol + timeframe`). Each worker discovers the currently active event slug, runs `BotRuntime` for that event, then advances to the next event automatically. Add interactive CLI control (`p` pause/resume, `q` stop) plus final aggregate reporting from structured per-event run results.

**Tech Stack:** Python 3.11, asyncio, existing trader runtime/adapters, pytest, mypy, ruff.

---

### Task 1: Add failing tests for scheduler and rollup primitives

**Files:**
- Create: `tests/runtime/test_continuous_runner.py`
- Modify: `tests/test_smoke_import.py`

**Step 1: Write tests for current-event slug generation**
- Assert `current_event_slug("btc", 15, now_ts)` rounds to next 15-minute boundary.
- Assert `current_event_slug("eth", 5, now_ts)` rounds to next 5-minute boundary.

**Step 2: Write tests for market selector parsing**
- Assert selector parsing supports `btc15,eth15,sol15,btc5,eth5,sol5`.
- Assert invalid selectors raise `ValueError`.

**Step 3: Write tests for final rollup aggregation**
- Build fake per-event reports with mixed UP/DOWN entries and take-profit sells.
- Assert output totals: wager, shares, average price, and per-market stats.

**Step 4: Run tests to verify failure**
Run: `pytest -q tests/runtime/test_continuous_runner.py`
Expected: FAIL (module/functions missing).

### Task 2: Add structured runtime result reporting

**Files:**
- Modify: `trader/runtime/orchestrator.py`

**Step 1: Add dataclasses for order and run result**
- Add `OrderRecord` and `EventRunResult`.

**Step 2: Capture order records in `_submit_order` and `_reconcile_open_orders` paths**
- Record side/action/price/shares/wager/status/timestamp.

**Step 3: Return `EventRunResult` from `BotRuntime.run()`**
- Include event metadata, totals, winner, prediction side, and order records.

**Step 4: Run targeted tests**
Run: `pytest -q tests/runtime/test_orchestrator.py tests/runtime/test_accuracy.py`
Expected: PASS.

### Task 3: Implement continuous runner supervisor

**Files:**
- Create: `trader/runtime/continuous_runner.py`
- Create: `trader/continuous_cli.py`
- Create: `scripts/run_continuous_bot.py`

**Step 1: Implement market spec parser and slug generator**
- Parse market keys (e.g., `btc15`) into typed specs.
- Generate current event slug by timeframe boundary.

**Step 2: Implement worker loop per market**
- Discover active slug.
- Build event-scoped config via `dataclasses.replace`.
- Run `BotRuntime` sequentially per event.
- Advance automatically to next event until global stop/deadline.

**Step 3: Implement global control loop**
- `p` toggles pause/resume.
- `q` triggers graceful stop.
- periodic status output shows each active market and current event slug/state.

**Step 4: Implement final summary printer**
- Per market: total buys/sells, net shares per side, avg buy price per side, event count, resolved outcomes.

**Step 5: Run new test file**
Run: `pytest -q tests/runtime/test_continuous_runner.py`
Expected: PASS.

### Task 4: Wire CLI and docs

**Files:**
- Modify: `README_TRADER.md`
- Modify: `.env.example`
- Modify: `pyproject.toml` (if script entrypoints added)

**Step 1: Add CLI options**
- `--markets` (comma list)
- `--duration-minutes` (default 60)
- `--mode` (`dry_run` default)

**Step 2: Document usage and controls**
- Show run command and key controls.
- Show output summary expectations.

### Task 5: Verify end-to-end

**Step 1: Quality checks**
Run:
- `ruff check trader tests`
- `mypy trader`
- `pytest -q`

**Step 2: Smoke run (short duration)**
Run:
- `python scripts/run_continuous_bot.py --mode dry_run --markets btc15,eth15,sol15,btc5,eth5,sol5 --duration-minutes 0.2`
Expected:
- starts six workers
- prints periodic status
- exits at duration
- prints final summary table
