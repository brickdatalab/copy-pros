import pytest


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
    assert cfg.signal_persist_ticks == 4
    assert cfg.entry_cooldown_ms == 750
    assert cfg.hedge_max_exposure_ratio == 0.25
    assert cfg.hedge_min_confidence == 0.85
    assert cfg.hedge_max_entry_price == 0.35
    assert cfg.max_wager_per_side_usdc == 10.0
    assert cfg.max_single_wager_usdc == 10.0
    assert cfg.min_wager_usdc == 1.0
    assert cfg.min_shares_per_purchase == 5.0
