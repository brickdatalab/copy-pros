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
