from datetime import datetime, timedelta, timezone

import pytest

from trader.engine.state import MarketState
from trader.engine.indicators import IndicatorEngine


def _ts(offset_sec: int) -> datetime:
    return datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_sec)


def test_vwap_1m_uses_recent_trades_only() -> None:
    state = MarketState(market_id="m1")
    state.add_trade(price=0.20, size=10.0, ts=_ts(-70))
    state.add_trade(price=0.60, size=10.0, ts=_ts(-20))
    state.add_trade(price=0.64, size=10.0, ts=_ts(-10))

    eng = IndicatorEngine()
    out = eng.compute(state, now=_ts(0))

    assert out["vwap_1m"] == pytest.approx(0.62)


def test_order_imbalance_in_range() -> None:
    state = MarketState(market_id="m2")
    state.book_yes.apply_snapshot(
        bids=[{"price": "0.45", "size": "200"}],
        asks=[{"price": "0.46", "size": "100"}],
    )

    eng = IndicatorEngine()
    out = eng.compute(state, now=_ts(0))

    assert -1.0 <= out["order_imbalance"] <= 1.0
    assert out["order_imbalance"] > 0


def test_mid_momentum_30s_positive() -> None:
    state = MarketState(market_id="m3")
    state.record_mid(0.40, _ts(-35))
    state.record_mid(0.45, _ts(-5))

    eng = IndicatorEngine()
    out = eng.compute(state, now=_ts(0))

    assert out["mid_momentum_30s"] is not None
    assert out["mid_momentum_30s"] > 0


def test_reversal_imminent_true_on_distressed_bullish_accumulation() -> None:
    state = MarketState(market_id="m4")
    eng = IndicatorEngine(
        vwap_up_delta_15s=0.003,
        mid_flat_delta_15s=0.001,
        momentum_accel_5s=0.002,
        enable_reversal_imminent=True,
    )

    state.record_mid(0.200, _ts(-40))
    state.add_trade(price=0.198, size=20.0, ts=_ts(-20))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.203", "size": "300"}],
        asks=[{"price": "0.205", "size": "100"}],
    )
    eng.compute(state, now=_ts(-15))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.199", "size": "300"}],
        asks=[{"price": "0.201", "size": "100"}],
    )
    eng.compute(state, now=_ts(-5))

    state.add_trade(price=0.206, size=20.0, ts=_ts(-2))
    state.book_yes.apply_snapshot(
        bids=[{"price": "0.200", "size": "400"}],
        asks=[{"price": "0.2098", "size": "100"}],
    )
    out = eng.compute(state, now=_ts(0))

    assert out["reversal_imminent"] is True
    assert out["vwap_delta_15s"] is not None
    assert out["vwap_delta_15s"] >= 0.003
    assert out["mid_delta_15s"] is not None
    assert out["mid_delta_15s"] <= 0.001
    assert out["momentum_delta_5s"] is not None


def test_reversal_imminent_false_when_vwap_not_rising() -> None:
    state = MarketState(market_id="m5")
    eng = IndicatorEngine(enable_reversal_imminent=True)

    state.record_mid(0.200, _ts(-40))
    state.add_trade(price=0.200, size=20.0, ts=_ts(-20))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.203", "size": "300"}],
        asks=[{"price": "0.205", "size": "100"}],
    )
    eng.compute(state, now=_ts(-15))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.199", "size": "300"}],
        asks=[{"price": "0.201", "size": "100"}],
    )
    eng.compute(state, now=_ts(-5))

    state.add_trade(price=0.200, size=20.0, ts=_ts(-2))
    state.book_yes.apply_snapshot(
        bids=[{"price": "0.200", "size": "400"}],
        asks=[{"price": "0.210", "size": "100"}],
    )
    out = eng.compute(state, now=_ts(0))

    assert out["reversal_imminent"] is False


def test_reversal_imminent_false_when_price_not_distressed() -> None:
    state = MarketState(market_id="m6")
    eng = IndicatorEngine(enable_reversal_imminent=True)

    state.record_mid(0.560, _ts(-40))
    state.add_trade(price=0.560, size=20.0, ts=_ts(-20))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.559", "size": "300"}],
        asks=[{"price": "0.561", "size": "100"}],
    )
    eng.compute(state, now=_ts(-15))

    state.book_yes.apply_snapshot(
        bids=[{"price": "0.559", "size": "300"}],
        asks=[{"price": "0.561", "size": "100"}],
    )
    eng.compute(state, now=_ts(-5))

    state.add_trade(price=0.569, size=20.0, ts=_ts(-2))
    state.book_yes.apply_snapshot(
        bids=[{"price": "0.560", "size": "400"}],
        asks=[{"price": "0.560", "size": "100"}],
    )
    out = eng.compute(state, now=_ts(0))

    assert out["reversal_imminent"] is False
