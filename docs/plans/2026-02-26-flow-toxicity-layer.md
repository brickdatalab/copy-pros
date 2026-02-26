# Flow Toxicity + Informed Order Flow Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add O(1) flow indicators (EW signed delta, VPIN-lite toxicity, large-trade ratio), integrate them into strategy scoring + flow-against-direction gating, and extend telemetry/tests without changing existing safety behavior.

**Architecture:** Keep the existing async runtime/orchestrator and WebSocket pipeline intact. Enrich UP token trades at ingest with aggressor side, update rolling flow state in `MarketState` with O(1) math, expose flow indicators in `IndicatorEngine`, blend flow terms into `DecisionPolicy` via preset switch, and add a pre-entry gate in runtime. Record flow fields in existing JSON payloads and derive run summaries from existing report artifacts.

**Tech Stack:** Python 3.11, asyncio runtime loops, pytest, mypy, ruff.

---

### Task 1: Complete Runtime Wiring + Missing Helpers

**Files:**
- Modify: `trader/runtime/orchestrator.py`
- Test: `tests/runtime/test_orchestrator.py`

**Step 1: Write failing tests**
- Add tests for `_flow_blocks_entry`:
  - BUY_UP blocked when `ew_delta_imbalance < -0.10`.
  - BUY_DOWN blocked when `ew_delta_imbalance > +0.10`.
  - HOLD/not-crossing-threshold cases do not block.

**Step 2: Run tests to verify RED**
- Run: `pytest tests/runtime/test_orchestrator.py -q`
- Expected: fail for missing helper.

**Step 3: Implement minimal runtime code**
- Add `_flow_blocks_entry` helper.
- Ensure `_compact_indicator_snapshot` preserves string fields (e.g., `flow_weight_preset`).
- Add flow-block counter on runtime state.

**Step 4: Run tests to verify GREEN**
- Run: `pytest tests/runtime/test_orchestrator.py -q`

---

### Task 2: Add MarketState Flow Unit Coverage (TDD)

**Files:**
- Modify: `tests/engine/test_state.py`

**Step 1: Write failing tests**
- EW delta grows with aggressive buy flow and decays after time gap.
- VPIN one-sided bucket high toxicity and balanced buckets low toxicity.
- Large-trade ratio / unknown-trade ratio update on add/prune.

**Step 2: Run tests to verify RED**
- Run: `pytest tests/engine/test_state.py -q`

**Step 3: Implement minimal state adjustments (only if needed)**
- Fix edge cases surfaced by tests without changing architecture.

**Step 4: Run tests to verify GREEN**
- Run: `pytest tests/engine/test_state.py -q`

---

### Task 3: Decision Policy Coverage for Flow Gating + Weight Presets

**Files:**
- Modify: `tests/strategy/test_decision_policy.py`

**Step 1: Write failing tests**
- Verify flow preset updates directional decision when delta aligns.
- Verify unknown-ratio safeguard scales flow contribution.
- Verify baseline preset remains unchanged behaviorally.

**Step 2: Run tests to verify RED**
- Run: `pytest tests/strategy/test_decision_policy.py -q`

**Step 3: Implement minimal fixes**
- Adjust policy only where tests prove gaps.

**Step 4: Run tests to verify GREEN**
- Run: `pytest tests/strategy/test_decision_policy.py -q`

---

### Task 4: Reporting + Telemetry Summary Requirements

**Files:**
- Modify: `scripts/run_continuous_bot.py`
- Modify: `trader/runtime/orchestrator.py`
- Test: `tests/scripts/test_run_continuous_bot.py`

**Step 1: Write failing tests**
- Add summary assertions for:
  - avg `ew_delta_imbalance` at entry for winners vs losers
  - avg `flow_toxicity` at entry for winners vs losers
  - flow-blocked entry count

**Step 2: Run tests to verify RED**
- Run: `pytest tests/scripts/test_run_continuous_bot.py -q`

**Step 3: Implement minimal summary extensions**
- Include entry-time flow fields in order records/report aggregation.
- Keep existing schema unchanged.

**Step 4: Run tests to verify GREEN**
- Run: `pytest tests/scripts/test_run_continuous_bot.py -q`

---

### Task 5: Full Verification

**Files:**
- Modify if needed from prior tasks only.

**Step 1: Run static + type checks**
- `ruff check trader tests scripts/run_event_bot.py scripts/run_continuous_bot.py scripts/run_local_playground.py`
- `mypy trader`

**Step 2: Run full tests**
- `pytest -q`

**Step 3: Commit**
- `git add ...`
- `git commit -m "feat: add flow toxicity signals and strategy integration"`

