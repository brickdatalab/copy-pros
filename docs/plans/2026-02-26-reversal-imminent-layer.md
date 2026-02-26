# Reversal Imminent Layer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `reversal_imminent` as an additive bullish signal layer for distressed UP pricing without changing baseline behavior outside explicit conditional adjustments.

**Architecture:** Extend indicator computation with reversal diagnostics/history lookbacks, apply conditional BUY_UP confidence relaxation in decision gating, add reversal-aware share targeting in sizer, and wire observability reason codes through runtime logging. Keep all existing constraints, filters, and exit logic intact.

**Tech Stack:** Python 3.11, asyncio runtime, pytest, mypy, ruff.

---

### Task 1: Failing tests for indicator + decision + sizer behavior
- Add tests for reversal true/false cases in `tests/engine/test_indicators.py`.
- Add tests for threshold relaxation only on bullish reversal under `<0.25` candidate entry in `tests/strategy/test_decision_policy.py`.
- Add tests for reversal sizing under constrained side budget in `tests/risk/test_sizer.py`.
- Run targeted tests and confirm failures first.

### Task 2: Implement additive config and indicator outputs
- Extend `trader/config.py` with:
  - `ENABLE_REVERSAL_IMMINENT`
  - `VWAP_UP_DELTA_15S`
  - `MID_FLAT_DELTA_15S`
  - `MOMENTUM_ACCEL_5S`
- Extend `trader/engine/state.py` to keep derived short history for `vwap_30s` and `mid_momentum_30s`.
- Extend `trader/engine/indicators/__init__.py` to compute:
  - `reversal_imminent`
  - `vwap_delta_15s`
  - `mid_delta_15s`
  - `momentum_delta_5s`

### Task 3: Implement conditional decision threshold relaxation
- Update `trader/strategy/decision_policy.py` to:
  - keep existing scoring weights and edge logic,
  - relax confidence threshold to `0.40` only when:
    - `reversal_imminent == true`,
    - candidate action is `BUY_UP`,
    - candidate entry price `< 0.25`.
- Keep default confidence threshold `0.52` otherwise.

### Task 4: Implement reversal-aware sizing
- Update `trader/risk/sizer.py` with additive reversal sizing path:
  - share-target monotone with confidence,
  - derive wager from shares*price,
  - enforce existing bounds/caps/minimums unchanged.
- Preserve non-reversal sizing exactly.

### Task 5: Runtime integration + observability reason codes
- Update `trader/runtime/orchestrator.py` to pass candidate prices into decision policy and pass reversal context into sizing/logging.
- Add explicit reversal blocked reason logging for cooldown/streak/risk/budget/price cap paths.
- Persist reversal fields through existing indicator snapshot payload (no schema changes).

### Task 6: Verification
- Run targeted tests for new logic.
- Run full checks:
  - `ruff check ...`
  - `mypy trader`
  - `pytest -q`
