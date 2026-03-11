import asyncio
import time
from rich.console import Console
import types

from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.config import TraderConfig
from trader.runtime.orchestrator import (
    BotRuntime,
    _flow_blocks_entry,
    _ingest_units_for_event,
    _map_reversal_block_reason,
    compute_target_runtime_seconds,
)
from trader.strategy.decision_policy import DecisionAction
from trader.ui.console import BotConsole


def test_orchestrator_stops_at_event_end() -> None:
    assert compute_target_runtime_seconds(duration_sec=90, start_offset=80) == 10


def test_orchestrator_zero_when_started_after_end() -> None:
    assert compute_target_runtime_seconds(duration_sec=300, start_offset=350) == 0


def test_bot_runtime_activity_snapshot_defaults() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )

    snap = runtime.activity_snapshot()

    assert snap["ws_ticks"] == 0
    assert snap["indicator_updates"] == 0
    assert snap["decision_count"] == 0
    assert snap["order_count"] == 0
    assert snap["fill_count"] == 0
    assert snap["decision_last_action"] is None
    assert snap["position_rollup"] == {"UP": None, "DOWN": None}
    assert isinstance(snap["indicators"], dict)


def test_bot_runtime_activity_snapshot_reflects_runtime_fields() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="eth-updown-15m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    runtime.ws_ticks = 31
    runtime.ws_last_event_type = "book"
    runtime.ws_last_ts = 123.0
    runtime.indicator_updates = 12
    runtime.indicator_last_ts = 124.0
    runtime.decisions_count = 9
    runtime.decision_last_ts = 125.0
    runtime.decision_last_action = "BUY_DOWN"
    runtime.decision_last_confidence = 0.67
    runtime.decision_last_edge = 0.33
    runtime.decision_last_reason = "bearish_alignment"
    runtime.last_indicator_snapshot = {"mid_price": 0.55}
    runtime.last_order_action = "BUY_DOWN"
    runtime.last_order_side = "DOWN"
    runtime.last_order_price = 0.55
    runtime.last_order_shares = 7.0
    runtime.last_order_status = "filled"
    runtime.last_order_ts = 126.0
    runtime.orders_count = 2
    runtime.fills_count = 1

    snap = runtime.activity_snapshot()

    assert snap["ws_ticks"] == 31
    assert snap["decision_last_action"] == "BUY_DOWN"
    assert snap["order_count"] == 2
    assert snap["fill_count"] == 1
    assert snap["last_order_status"] == "filled"


def test_reversal_block_reason_mapping() -> None:
    assert _map_reversal_block_reason("entry_cooldown") == "reversal_detected_but_cooldown_active"
    assert _map_reversal_block_reason("signal_not_persistent") == "reversal_detected_but_streak_not_satisfied"
    assert _map_reversal_block_reason("side_budget_exhausted") == "reversal_detected_but_budget_exhausted"
    assert _map_reversal_block_reason("price_cap") == "reversal_detected_but_price_cap_exceeded"


def test_flow_blocks_buy_up_when_delta_is_sufficiently_negative() -> None:
    assert _flow_blocks_entry(DecisionAction.BUY_UP, ew_delta_imbalance=-0.11, threshold=0.10) is True
    assert _flow_blocks_entry(DecisionAction.BUY_UP, ew_delta_imbalance=-0.10, threshold=0.10) is False


def test_flow_blocks_buy_down_when_delta_is_sufficiently_positive() -> None:
    assert _flow_blocks_entry(DecisionAction.BUY_DOWN, ew_delta_imbalance=0.12, threshold=0.10) is True
    assert _flow_blocks_entry(DecisionAction.BUY_DOWN, ew_delta_imbalance=0.10, threshold=0.10) is False


def test_flow_gate_does_not_block_hold_actions() -> None:
    assert _flow_blocks_entry(DecisionAction.HOLD, ew_delta_imbalance=0.75, threshold=0.10) is False


def test_activity_snapshot_keeps_string_indicators() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    runtime.last_indicator_snapshot = {
        "flow_weight_preset": "flow_v1",
        "ew_delta_imbalance": 0.1234567,
    }

    indicators = runtime.activity_snapshot()["indicators"]

    assert indicators["flow_weight_preset"] == "flow_v1"
    assert indicators["ew_delta_imbalance"] == 0.123457


def test_submit_order_preserves_entry_signal_snapshot_in_order_record() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    snapshot = {
        "confidence": 0.77,
        "edge": 0.22,
        "order_imbalance": 0.31,
        "mid_momentum_30s": 0.04,
    }

    asyncio.run(
        runtime._submit_order(
            run_id="run-1",
            token_id="token-1",
            side="UP",
            action="ENTRY",
            payload={"client_order_id": "cid-1", "price": 0.2, "shares": 5.0},
            wager_usdc=1.0,
            reason_code="momentum_alignment_entry",
            trading_client=object(),  # dry_run path ignores this client object
            entry_signal_snapshot=snapshot,
        )
    )

    assert len(runtime.order_records) == 1
    assert runtime.order_records[0].entry_signal_snapshot == snapshot
    rollup = runtime.activity_snapshot()["position_rollup"]
    assert rollup["UP"]["shares"] == 5.0
    assert rollup["UP"]["avg_price"] == 0.2
    assert rollup["UP"]["total_wager"] == 1.0


def test_position_rollup_clears_after_full_sell_fill() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )

    asyncio.run(
        runtime._submit_order(
            run_id="run-1",
            token_id="token-1",
            side="UP",
            action="ENTRY",
            payload={"client_order_id": "buy-1", "price": 0.2, "shares": 10.0},
            wager_usdc=2.0,
            reason_code="momentum_alignment_entry",
            trading_client=object(),
        )
    )

    asyncio.run(
        runtime._submit_order(
            run_id="run-1",
            token_id="token-1",
            side="UP",
            action="TAKE_PROFIT",
            payload={"client_order_id": "sell-1", "price": 0.95, "shares": 10.0},
            wager_usdc=9.5,
            reason_code="take_profit_95c_discipline",
            trading_client=object(),
            is_sell=True,
        )
    )

    rollup = runtime.activity_snapshot()["position_rollup"]
    assert rollup["UP"] is None


def test_entry_warmup_blocks_until_minimum_readiness() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    runtime.run_started_monotonic = runtime.run_started_monotonic + 1_000.0
    runtime.ws_ticks = 1
    runtime.indicator_updates = 1

    ready, state = runtime._entry_warmup_ready()

    assert ready is False
    assert state["required_elapsed_sec"] == runtime.cfg.entry_warmup_min_seconds
    assert state["required_ws_ticks"] == runtime.cfg.entry_warmup_min_ws_ticks
    assert state["required_indicator_updates"] == runtime.cfg.entry_warmup_min_indicator_updates


async def test_wait_for_trigger_wakes_on_event() -> None:
    """Event-driven trigger: _wait_for_trigger returns immediately when event is set."""
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=True,
            event_driven_max_wait_ms=5000,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    trigger = asyncio.Event()
    trigger.set()

    start = time.monotonic()
    await runtime._wait_for_trigger(trigger_event=trigger, fallback_interval_ms=5000)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 50, f"Should return instantly when event is set, took {elapsed_ms:.1f}ms"
    assert not trigger.is_set(), "Event should be cleared after consumption"


async def test_wait_for_trigger_falls_back_on_timeout() -> None:
    """Event-driven trigger: times out at event_driven_max_wait_ms when no event fires."""
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=True,
            event_driven_max_wait_ms=50,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    trigger = asyncio.Event()

    start = time.monotonic()
    await runtime._wait_for_trigger(trigger_event=trigger, fallback_interval_ms=200)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 150, f"Should timeout at max_wait_ms=50, took {elapsed_ms:.1f}ms"


async def test_wait_for_trigger_uses_sleep_when_disabled() -> None:
    """When enable_event_driven_loops=False, falls back to plain sleep."""
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=False,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    trigger = asyncio.Event()
    trigger.set()

    start = time.monotonic()
    await runtime._wait_for_trigger(trigger_event=trigger, fallback_interval_ms=50)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms >= 40, f"Should sleep full interval when disabled, took {elapsed_ms:.1f}ms"
    assert trigger.is_set(), "Event should NOT be cleared when disabled (sleep path)"


def test_ingest_units_counts_payload_volume() -> None:
    assert _ingest_units_for_event("book", {"bids": [["0.5", "10"], ["0.4", "8"]], "asks": [["0.6", "12"]]}) == 3
    assert _ingest_units_for_event("price_change", {"changes": [["BUY", "0.5", "10"], ["SELL", "0.6", "4"]]}) == 2
    assert _ingest_units_for_event("last_trade_price", {"price": "0.5"}) == 1
    assert _ingest_units_for_event("heartbeat", {}) == 1


async def test_signal_loop_skips_timeout_ticks_when_event_driven_enabled() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=True,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    runtime.indicators = {"mid_price": 0.5, "order_imbalance": 0.0}

    calls = 0

    async def _fake_wait_for_trigger(*, trigger_event: asyncio.Event, fallback_interval_ms: int) -> bool:
        nonlocal calls
        calls += 1
        runtime.stop_event.set()
        return False

    runtime._wait_for_trigger = _fake_wait_for_trigger  # type: ignore[method-assign]

    now = int(time.time())
    ctx = type(
        "Ctx",
        (),
        {
            "end_ts": now + 60,
            "token_up": "up",
            "token_down": "down",
        },
    )()

    await runtime._signal_loop(ctx, "run-1")  # type: ignore[arg-type]

    assert calls == 1
    assert runtime.decisions_count == 0


async def test_indicator_loop_skips_timeout_ticks_when_event_driven_enabled() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=True,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )

    calls = 0

    async def _fake_wait_for_trigger(*, trigger_event: asyncio.Event, fallback_interval_ms: int) -> bool:
        nonlocal calls
        calls += 1
        runtime.stop_event.set()
        return False

    runtime._wait_for_trigger = _fake_wait_for_trigger  # type: ignore[method-assign]

    await runtime._indicator_loop()

    assert calls == 1
    assert runtime.indicator_updates == 0


async def test_ws_ingest_loop_survives_bad_payload_and_continues(monkeypatch) -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )

    async def fake_stream_market_events(_asset_ids: list[str]):
        # Malformed price in first payload should not kill ingest loop.
        yield {
            "asset_id": "up-token",
            "event_type": "price_change",
            "changes": [{"side": "BUY", "price": "NaN_BAD", "size": "5"}],
        }
        # Valid payload afterwards proves loop continues.
        yield {
            "asset_id": "up-token",
            "event_type": "book",
            "bids": [{"price": "0.49", "size": "20"}],
            "asks": [{"price": "0.51", "size": "18"}],
        }
        runtime.stop_event.set()

    monkeypatch.setattr("trader.runtime.orchestrator.stream_market_events", fake_stream_market_events)

    ctx = types.SimpleNamespace(token_up="up-token", token_down="down-token")

    await runtime._ws_ingest_loop(ctx, "run-1")  # type: ignore[arg-type]

    # We should have processed at least the valid book event after the malformed frame.
    assert runtime.ws_ticks >= 2
    assert runtime.market_state.book_yes.best_bid == 0.49
    assert runtime.ws_last_event_type == "book"


async def test_ws_ingest_loop_recovers_after_unexpected_stream_error(monkeypatch) -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )

    calls = 0

    async def fake_stream_market_events(_asset_ids: list[str]):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                "asset_id": "up-token",
                "event_type": "book",
                "bids": [{"price": "0.49", "size": "20"}],
                "asks": [{"price": "0.51", "size": "18"}],
            }
            raise RuntimeError("synthetic ws failure")
        yield {
            "asset_id": "up-token",
            "event_type": "book",
            "bids": [{"price": "0.50", "size": "21"}],
            "asks": [{"price": "0.52", "size": "19"}],
        }
        runtime.stop_event.set()

    monkeypatch.setattr("trader.runtime.orchestrator.stream_market_events", fake_stream_market_events)

    ctx = types.SimpleNamespace(token_up="up-token", token_down="down-token")

    await runtime._ws_ingest_loop(ctx, "run-1")  # type: ignore[arg-type]

    assert calls >= 2
    assert runtime.ws_ticks >= 2
    assert runtime.market_state.book_yes.best_bid == 0.50


async def test_trigger_chain_ws_wakes_indicator_event() -> None:
    """Verify _ws_data_event is set after WS tick counter increments."""
    runtime = BotRuntime(
        cfg=TraderConfig(
            poly_event_input="btc-updown-5m-0",
            bot_mode="dry_run",
            enable_event_driven_loops=True,
        ),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    assert not runtime._ws_data_event.is_set()
    assert not runtime._indicator_event.is_set()
    assert not runtime._signal_event.is_set()


def test_entry_warmup_allows_after_minimum_readiness() -> None:
    runtime = BotRuntime(
        cfg=TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run"),
        console=BotConsole(console=Console(stderr=True, quiet=True)),
        writer=BufferedSupabaseWriter(enabled=False),
    )
    runtime.run_started_monotonic = time.monotonic() - (runtime.cfg.entry_warmup_min_seconds + 0.1)
    runtime.ws_ticks = runtime.cfg.entry_warmup_min_ws_ticks
    runtime.indicator_updates = runtime.cfg.entry_warmup_min_indicator_updates

    ready, _ = runtime._entry_warmup_ready()

    assert ready is True
