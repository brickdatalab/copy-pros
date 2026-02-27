import pytest

from trader.config import TraderConfig


def test_config_has_flow_min_ew_volume_default() -> None:
    cfg = TraderConfig(poly_event_input="btc-updown-5m-0", bot_mode="dry_run")
    assert cfg.flow_min_ew_volume == 100.0


def test_config_requires_event_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLY_EVENT_INPUT", raising=False)
    from trader.config import load_config

    with pytest.raises(ValueError):
        load_config()


def test_config_loads_default_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLY_EVENT_INPUT", "https://polymarket.com/event/example")
    from trader.config import load_config

    cfg = load_config()
    assert cfg.max_entry_price == 0.80
    assert cfg.min_signal_confidence == 0.52
    assert cfg.min_signal_edge == 0.10
    assert cfg.enable_flow_signals is True
    assert cfg.flow_weight_preset == "v1"
    assert cfg.flow_block_delta_threshold == 0.10
    assert cfg.flow_unknown_ratio_cutoff == 0.35
    assert cfg.flow_unknown_delta_scale == 0.5
    assert cfg.flow_ew_half_life_seconds == 15.0
    assert cfg.flow_vpin_bucket_volume == 300.0
    assert cfg.flow_vpin_num_buckets == 10
    assert cfg.flow_large_trade_size == 75.0
    assert cfg.flow_large_ratio_window_seconds == 30
    assert cfg.trade_side_tolerance == 0.001
    assert cfg.enable_reversal_imminent is True
    assert cfg.vwap_up_delta_15s == 0.003
    assert cfg.mid_flat_delta_15s == 0.001
    assert cfg.momentum_accel_5s == 0.002
    assert cfg.enable_convexity_budget_reservation is False
    assert cfg.signal_persist_ticks == 4
    assert cfg.entry_cooldown_ms == 750
    assert cfg.hedge_max_exposure_ratio == 0.25
    assert cfg.hedge_min_confidence == 0.85
    assert cfg.hedge_max_entry_price == 0.35
    assert cfg.max_wager_per_side_usdc == 5.0
    assert cfg.max_single_wager_usdc == 5.0
    assert cfg.min_wager_usdc == 1.0
    assert cfg.min_shares_per_purchase == 5.0
