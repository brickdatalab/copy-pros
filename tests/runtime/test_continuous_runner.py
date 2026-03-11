from __future__ import annotations

import asyncio

from trader.config import TraderConfig
from trader.runtime import continuous_runner
from trader.runtime.continuous_runner import (
    ContinuousRunner,
    ContinuousRunnerConfig,
    EventOrder,
    EventRunSummary,
    aggregate_market_results,
    current_event_slug,
    parse_market_selection,
)


def test_current_event_slug_uses_current_bucket_start() -> None:
    # 2026-02-26 02:16:40 UTC
    now_ts = 1772072200
    assert current_event_slug("btc", 15, now_ts) == "btc-updown-15m-1772072100"
    assert current_event_slug("eth", 5, now_ts) == "eth-updown-5m-1772072100"


def test_parse_market_selection_accepts_all_supported_streams() -> None:
    specs = parse_market_selection("btc15,eth15,sol15,btc5,eth5,sol5")
    assert [(s.symbol, s.timeframe_minutes) for s in specs] == [
        ("btc", 15),
        ("eth", 15),
        ("sol", 15),
        ("btc", 5),
        ("eth", 5),
        ("sol", 5),
    ]


def test_parse_market_selection_rejects_unknown_keys() -> None:
    try:
        parse_market_selection("btc15,doge5")
    except ValueError as err:
        assert "Unsupported market selector" in str(err)
    else:
        raise AssertionError("Expected ValueError for invalid selector")


def test_aggregate_market_results_tracks_wager_and_average_prices() -> None:
    runs = [
        EventRunSummary(
            market_key="btc15",
            event_slug="btc-updown-15m-1",
            winning_side="UP",
            orders=[
                EventOrder(action="BUY_UP", side="UP", price=0.4, shares=10.0, wager_usdc=4.0),
                EventOrder(action="BUY_DOWN", side="DOWN", price=0.2, shares=5.0, wager_usdc=1.0),
                EventOrder(action="SELL_UP", side="UP", price=0.95, shares=4.0, wager_usdc=3.8),
            ],
        ),
        EventRunSummary(
            market_key="btc15",
            event_slug="btc-updown-15m-2",
            winning_side="DOWN",
            orders=[
                EventOrder(action="BUY_DOWN", side="DOWN", price=0.3, shares=10.0, wager_usdc=3.0),
            ],
        ),
    ]

    summary = aggregate_market_results(runs)
    btc = summary["btc15"]

    assert btc.events_run == 2
    assert btc.up.buy_shares == 10.0
    assert btc.up.sell_shares == 4.0
    assert btc.up.net_shares == 6.0
    assert btc.up.buy_notional == 4.0
    assert round(btc.up.avg_buy_price, 3) == 0.4

    assert btc.down.buy_shares == 15.0
    assert btc.down.sell_shares == 0.0
    assert btc.down.net_shares == 15.0
    assert btc.down.buy_notional == 4.0
    assert round(btc.down.avg_buy_price, 3) == round(4.0 / 15.0, 3)


def test_runner_control_flags_and_snapshot() -> None:
    specs = parse_market_selection("btc5,eth15")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )

    snap = runner.snapshot()
    assert snap["running"] is False
    assert snap["paused"] is False
    assert sorted(snap["markets"]) == ["btc5", "eth15"]

    runner.pause()
    assert runner.is_paused is True
    snap = runner.snapshot()
    assert snap["paused"] is True

    runner.resume()
    assert runner.is_paused is False

    runner.request_stop()
    assert runner.snapshot()["stop_requested"] is True
    assert runner.is_paused is False


def test_runner_snapshot_includes_default_activity_payload() -> None:
    specs = parse_market_selection("btc5")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )

    snap = runner.snapshot()
    worker = snap["workers"]["btc5"]
    activity = worker["activity"]

    assert activity["ws_ticks"] == 0
    assert activity["indicator_updates"] == 0
    assert activity["decision_count"] == 0
    assert activity["order_count"] == 0
    assert activity["fill_count"] == 0
    assert activity["open_orders"] == 0
    assert activity["decision_last_action"] is None
    assert "indicators" in activity


class _FakeRuntime:
    def activity_snapshot(self) -> dict[str, object]:
        return {
            "ws_ticks": 14,
            "ws_last_event_type": "book",
            "ws_last_ts": 1000.0,
            "indicator_updates": 8,
            "indicator_last_ts": 1001.0,
            "decision_count": 5,
            "decision_last_ts": 1002.0,
            "decision_last_action": "BUY_UP",
            "decision_last_confidence": 0.71,
            "decision_last_edge": 0.25,
            "decision_last_reason": "bullish_alignment",
            "intent_queue_depth": 1,
            "order_count": 2,
            "fill_count": 1,
            "open_orders": 0,
            "last_order_action": "BUY_UP",
            "last_order_side": "UP",
            "last_order_price": 0.44,
            "last_order_shares": 5.0,
            "last_order_status": "filled",
            "last_order_ts": 1003.0,
            "indicators": {"mid_price": 0.44},
        }


def test_runner_snapshot_uses_live_runtime_activity() -> None:
    specs = parse_market_selection("btc5")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )

    runner.active_runtimes["btc5"] = _FakeRuntime()  # type: ignore[assignment]
    snap = runner.snapshot()
    activity = snap["workers"]["btc5"]["activity"]

    assert activity["ws_ticks"] == 14
    assert activity["decision_last_action"] == "BUY_UP"
    assert activity["last_order_status"] == "filled"


def test_runner_snapshot_includes_earnings_totals_and_order_rows() -> None:
    specs = parse_market_selection("btc5")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )
    runner.completed_runs = [
        EventRunSummary(
            market_key="btc5",
            event_slug="btc-updown-5m-1772121300",
            winning_side="UP",
            predicted_side="UP",
            was_prediction_accurate=True,
            orders=[
                EventOrder(
                    action="BUY_UP",
                    side="UP",
                    price=0.4,
                    shares=10.0,
                    wager_usdc=4.0,
                    reason_code="momentum_alignment_entry",
                    entry_ew_delta_imbalance=0.2,
                    entry_flow_toxicity=0.4,
                    entry_large_trade_ratio=0.1,
                    entry_unknown_trade_ratio=0.2,
                    entry_flow_weight_preset="flow_v1",
                    entry_signal_snapshot={
                        "confidence": 0.74,
                        "edge": 0.21,
                        "order_imbalance": 0.33,
                    },
                    status="filled",
                    ts="2026-02-26T15:55:01+00:00",
                ),
                EventOrder(
                    action="SELL_UP",
                    side="UP",
                    price=0.9,
                    shares=4.0,
                    wager_usdc=3.6,
                    reason_code="take_profit_95c_discipline",
                    status="filled",
                    ts="2026-02-26T15:56:01+00:00",
                ),
            ],
        )
    ]

    snap = runner.snapshot()
    earnings = snap["earnings"]
    totals = earnings["totals"]

    assert totals["resolved_events"] == 1
    assert totals["accurate_events"] == 1
    assert round(totals["resolved_wagered_usdc"], 3) == 4.0
    assert round(totals["resolved_returned_usdc"], 3) == 9.6
    assert round(totals["resolved_pnl_usdc"], 3) == 5.6
    assert round(totals["win_rate"], 3) == 1.0
    assert round(totals["average_win_usdc"], 3) == 5.6
    assert round(totals["average_loss_usdc"], 3) == 0.0
    assert totals["profit_factor"] is None
    assert totals["reason_code_breakdown"]["momentum_alignment_entry"]["entries"] == 1
    assert round(totals["reason_code_breakdown"]["momentum_alignment_entry"]["attributed_pnl_usdc"], 3) == 5.6

    assert len(earnings["events"]) == 1
    assert earnings["events"][0]["status"] == "resolved"
    assert earnings["events"][0]["was_prediction_accurate"] is True

    assert len(earnings["orders"]) == 2
    assert earnings["orders"][0]["action"] == "SELL_UP"
    assert earnings["orders"][0]["reason_code"] == "take_profit_95c_discipline"
    assert earnings["orders"][1]["entry_flow_weight_preset"] == "flow_v1"
    assert earnings["orders"][1]["entry_signal_snapshot"]["confidence"] == 0.74


def test_runner_snapshot_includes_flow_entry_profile_and_flow_block_count() -> None:
    specs = parse_market_selection("btc5")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )
    runner.completed_runs = [
        EventRunSummary(
            market_key="btc5",
            event_slug="btc-updown-5m-1772121300",
            winning_side="UP",
            predicted_side="UP",
            was_prediction_accurate=True,
            flow_blocked_entries_count=3,
            orders=[
                EventOrder(
                    action="BUY_UP",
                    side="UP",
                    price=0.2,
                    shares=10.0,
                    wager_usdc=2.0,
                    reason_code="momentum_alignment_entry",
                    entry_ew_delta_imbalance=0.4,
                    entry_flow_toxicity=0.6,
                )
            ],
        ),
        EventRunSummary(
            market_key="btc5",
            event_slug="btc-updown-5m-1772121600",
            winning_side="DOWN",
            predicted_side="UP",
            was_prediction_accurate=False,
            flow_blocked_entries_count=2,
            orders=[
                EventOrder(
                    action="BUY_UP",
                    side="UP",
                    price=0.4,
                    shares=10.0,
                    wager_usdc=4.0,
                    reason_code="momentum_alignment_entry",
                    entry_ew_delta_imbalance=-0.2,
                    entry_flow_toxicity=0.9,
                )
            ],
        ),
    ]

    totals = runner.snapshot()["earnings"]["totals"]
    profile = totals["entry_flow_profile"]

    assert totals["entry_blocked_flow_against_direction_count"] == 5
    assert profile["avg_ew_delta_imbalance_winner_entries"] == 0.4
    assert profile["avg_ew_delta_imbalance_loser_entries"] == -0.2
    assert profile["avg_flow_toxicity_winner_entries"] == 0.6
    assert profile["avg_flow_toxicity_loser_entries"] == 0.9


def test_pending_resolution_updates_event_to_resolved(monkeypatch: object) -> None:
    specs = parse_market_selection("btc5")
    runner = ContinuousRunner(
        cfg=ContinuousRunnerConfig(specs=specs, mode="dry_run", duration_minutes=1.0),
        base_cfg=TraderConfig(poly_event_input="btc-updown-5m-0"),
    )
    runner.completed_runs = [
        EventRunSummary(
            market_key="btc5",
            event_slug="btc-updown-5m-1772121300",
            winning_side=None,
            predicted_side="UP",
            orders=[],
        )
    ]

    async def _fake_resolve(_: str) -> str | None:
        return "UP"

    monkeypatch.setattr(continuous_runner, "_resolve_event", _fake_resolve)
    changed = asyncio.run(runner._resolve_pending_outcomes_once())

    assert changed is True
    assert runner.completed_runs[0].winning_side == "UP"
    assert runner.completed_runs[0].was_prediction_accurate is True
