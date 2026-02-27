from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trader.engine.state import MarketState


def test_ew_signed_delta_tracks_aggressive_buy_flow_and_decays() -> None:
    state = MarketState(market_id="btc-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=300.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    state.add_trade(price=0.20, size=100.0, ts=t0, side=1)
    assert state.ew_delta_imbalance > 0.99

    # 30s gap should materially decay historical contribution.
    state.add_trade(price=0.21, size=1.0, ts=t0 + timedelta(seconds=30), side=0)
    assert state.ew_abs_vol < 30.0
    assert state.ew_delta_imbalance > 0.9


def test_vpin_one_sided_bucket_is_toxic() -> None:
    state = MarketState(market_id="eth-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=100.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    state.add_trade(price=0.2, size=100.0, ts=t0, side=1)
    assert state.flow_toxicity > 0.99


def test_vpin_balanced_bucket_is_low_toxicity() -> None:
    state = MarketState(market_id="sol-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=100.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    state.add_trade(price=0.2, size=50.0, ts=t0, side=1)
    state.add_trade(price=0.2, size=50.0, ts=t0 + timedelta(seconds=1), side=-1)
    assert state.flow_toxicity < 0.01


def test_large_and_unknown_trade_ratios_use_rolling_window() -> None:
    state = MarketState(market_id="btc-up")
    state.configure_flow(
        ew_half_life_seconds=15.0,
        vpin_bucket_volume=300.0,
        vpin_num_buckets=10,
        large_trade_size=75.0,
        large_ratio_window_seconds=30,
    )
    t0 = datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc)
    state.add_trade(price=0.2, size=100.0, ts=t0, side=0)
    state.add_trade(price=0.2, size=20.0, ts=t0 + timedelta(seconds=1), side=1)

    assert round(state.large_trade_ratio, 3) == round(100.0 / 120.0, 3)
    assert round(state.unknown_trade_ratio, 3) == 0.5

    # Advancing 40s prunes both old rows from the 30s window.
    state.add_trade(price=0.2, size=10.0, ts=t0 + timedelta(seconds=40), side=1)
    assert round(state.large_trade_ratio, 3) == 0.0
    assert round(state.unknown_trade_ratio, 3) == 0.0


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
