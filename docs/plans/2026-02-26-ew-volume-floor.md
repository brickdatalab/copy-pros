# EW Volume Floor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prevent spurious c=1.00 signals from thin EW delta data at startup by adding a minimum volume floor to `ew_delta_imbalance`.

**Architecture:** Guard the `ew_delta_imbalance` property in `MarketState` to return 0.0 when `ew_abs_vol` is below a configurable threshold. The threshold is env-driven via `FLOW_MIN_EW_VOLUME` (default: 100 shares). Once sufficient volume accumulates (~2-3 real trades), behavior is identical to current code.

**Tech Stack:** Python 3.11+, dataclasses, pytest, mypy strict

---

### Task 1: Add failing tests for volume floor guard

**Files:**
- Modify: `tests/engine/test_state.py`

**Step 1: Write two failing tests**

Append these tests to `tests/engine/test_state.py`:

```python
def test_ew_delta_imbalance_returns_zero_below_volume_floor() -> None:
    """A single small trade should not produce a signal when below volume floor."""
    state = MarketState(market_id="btc-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=300.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
        min_ew_volume=100.0,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    # One 50-share trade — below the 100-share floor
    state.add_trade(price=0.20, size=50.0, ts=t0, side=1)
    assert state.ew_delta_imbalance == 0.0, "Should be squelched below volume floor"


def test_ew_delta_imbalance_activates_above_volume_floor() -> None:
    """Once cumulative EW volume exceeds the floor, signal should activate."""
    state = MarketState(market_id="btc-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=300.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
        min_ew_volume=100.0,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    # Two trades totalling 150 shares — above the 100-share floor
    state.add_trade(price=0.20, size=80.0, ts=t0, side=1)
    state.add_trade(price=0.20, size=70.0, ts=t0 + timedelta(seconds=1), side=1)
    assert state.ew_delta_imbalance > 0.9, "Should activate once volume exceeds floor"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/engine/test_state.py -v`
Expected: Both new tests FAIL — `configure_flow` does not accept `min_ew_volume` yet.

**Step 3: Commit**

```bash
git add tests/engine/test_state.py
git commit -m "test: add failing tests for EW volume floor guard"
```

---

### Task 2: Implement volume floor in MarketState

**Files:**
- Modify: `trader/engine/state.py:30-55` (field + configure_flow)
- Modify: `trader/engine/state.py:81-85` (ew_delta_imbalance property)

**Step 1: Add `min_ew_volume` field to `MarketState`**

Add after line 32 (`ew_abs_vol: float = 0.0`):

```python
    min_ew_volume: float = 0.0
```

**Step 2: Accept `min_ew_volume` in `configure_flow`**

Add parameter to `configure_flow` signature (keyword-only):

```python
    def configure_flow(
        self,
        *,
        ew_half_life_seconds: float,
        vpin_bucket_volume: float,
        vpin_num_buckets: int,
        large_trade_size: float,
        large_ratio_window_seconds: int,
        min_ew_volume: float = 0.0,
    ) -> None:
```

Add at end of method body:

```python
        self.min_ew_volume = max(0.0, min_ew_volume)
```

**Step 3: Guard `ew_delta_imbalance` property**

Replace the property (lines 81-85) with:

```python
    @property
    def ew_delta_imbalance(self) -> float:
        if self.ew_abs_vol < self.min_ew_volume:
            return 0.0
        denom = self.ew_abs_vol + 1e-9
        value = self.ew_delta / denom
        return min(max(value, -1.0), 1.0)
```

**Step 4: Run tests to verify Task 1 tests now pass**

Run: `pytest tests/engine/test_state.py -v`
Expected: All tests PASS, including the two new ones.

**Step 5: Verify existing test still passes**

The test `test_ew_signed_delta_tracks_aggressive_buy_flow_and_decays` does NOT pass `min_ew_volume` to `configure_flow`, so it defaults to 0.0 — no floor, no behavior change. The assertion `state.ew_delta_imbalance > 0.99` after a 100-share trade will still pass because `min_ew_volume=0.0` means no guard.

**Step 6: Commit**

```bash
git add trader/engine/state.py
git commit -m "feat: add min_ew_volume floor to ew_delta_imbalance"
```

---

### Task 3: Wire config through TraderConfig and load_config

**Files:**
- Modify: `trader/config.py:10-58` (add field to TraderConfig)
- Modify: `trader/config.py:85-139` (add to load_config)
- Modify: `.env.example` (add FLOW_MIN_EW_VOLUME)

**Step 1: Add field to TraderConfig**

Add after `flow_ew_half_life_seconds` (line 21):

```python
    flow_min_ew_volume: float = 100.0
```

**Step 2: Add to load_config**

Add after `flow_ew_half_life_seconds` line in the `TraderConfig(...)` constructor call:

```python
        flow_min_ew_volume=_env_float("FLOW_MIN_EW_VOLUME", 100.0),
```

**Step 3: Add to .env.example**

Add after the `FLOW_EW_HALF_LIFE_SECONDS=15` line:

```
FLOW_MIN_EW_VOLUME=100
```

**Step 4: Run typecheck**

Run: `mypy trader/config.py`
Expected: PASS with no errors.

**Step 5: Commit**

```bash
git add trader/config.py .env.example
git commit -m "feat: add FLOW_MIN_EW_VOLUME config param (default 100)"
```

---

### Task 4: Wire orchestrator to pass config value

**Files:**
- Modify: `trader/runtime/orchestrator.py:254-260` (configure_flow call)

**Step 1: Pass `min_ew_volume` to `configure_flow`**

In the `configure_flow` call (~line 254-260), add the new parameter:

```python
        self.market_state.configure_flow(
            ew_half_life_seconds=self.cfg.flow_ew_half_life_seconds,
            vpin_bucket_volume=self.cfg.flow_vpin_bucket_volume,
            vpin_num_buckets=self.cfg.flow_vpin_num_buckets,
            large_trade_size=self.cfg.flow_large_trade_size,
            large_ratio_window_seconds=self.cfg.flow_large_ratio_window_seconds,
            min_ew_volume=self.cfg.flow_min_ew_volume,
        )
```

**Step 2: Run typecheck**

Run: `mypy trader/runtime/orchestrator.py`
Expected: PASS.

**Step 3: Commit**

```bash
git add trader/runtime/orchestrator.py
git commit -m "feat: wire flow_min_ew_volume through orchestrator"
```

---

### Task 5: Add config-level test

**Files:**
- Modify: `tests/test_config.py` (if exists, else `tests/runtime/test_orchestrator.py`)

**Step 1: Write config test**

Add a test that verifies the default value:

```python
def test_config_has_flow_min_ew_volume_default() -> None:
    cfg = TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run")
    assert cfg.flow_min_ew_volume == 100.0
```

**Step 2: Run test**

Run: `pytest tests/ -k "flow_min_ew_volume" -v`
Expected: PASS.

**Step 3: Commit**

```bash
git add tests/
git commit -m "test: verify flow_min_ew_volume config default"
```

---

### Task 6: Full verification

**Step 1: Run full lint**

Run: `ruff check trader tests scripts/run_event_bot.py scripts/run_continuous_bot.py scripts/run_local_playground.py`
Expected: No errors.

**Step 2: Run full typecheck**

Run: `mypy trader`
Expected: No errors.

**Step 3: Run full test suite**

Run: `pytest -q`
Expected: All tests PASS (except the pre-existing `test_config_loads_default_constraints` which expects `max_wager_per_side_usdc == 5.0` — this is a known pre-existing issue unrelated to our change).

**Step 4: Commit (if any lint fixes needed)**

```bash
git add -A && git commit -m "chore: lint/type fixes for EW volume floor"
```

---

## Impact Summary

| What changes | How |
|---|---|
| `MarketState.ew_delta_imbalance` | Returns 0.0 when `ew_abs_vol < min_ew_volume` |
| `MarketState.configure_flow()` | Accepts optional `min_ew_volume` param (default 0.0) |
| `TraderConfig` | New field `flow_min_ew_volume` (default 100.0) |
| `load_config()` | Reads `FLOW_MIN_EW_VOLUME` env var |
| `.env.example` | Documents `FLOW_MIN_EW_VOLUME=100` |
| Orchestrator | Passes `cfg.flow_min_ew_volume` to `configure_flow()` |
| Existing tests | Unchanged — they don't pass `min_ew_volume`, so default 0.0 applies (no floor) |
| New tests | 3 tests: below-floor returns 0.0, above-floor activates, config default check |
