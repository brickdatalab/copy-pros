from rich.console import Console

from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.config import TraderConfig
from trader.runtime.orchestrator import BotRuntime, compute_target_runtime_seconds
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
