import asyncio
from rich.console import Console

from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.config import TraderConfig
from trader.runtime.orchestrator import (
    BotRuntime,
    _flow_blocks_entry,
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
