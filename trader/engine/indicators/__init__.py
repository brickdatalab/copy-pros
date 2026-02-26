"""Indicator computation engine."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from trader.engine.state import MarketState


@dataclass
class IndicatorEngine:
    def compute(self, state: MarketState, now: datetime) -> dict[str, float | None]:
        state.snapshot_book_metrics(now)

        vwap_30s = _vwap(state, now, 30)
        vwap_1m = _vwap(state, now, 60)
        vwap_5m = _vwap(state, now, 300)

        order_imbalance = _order_imbalance(state)
        spread_momentum_30s = _spread_momentum(state, now, 30)
        mid_momentum_15s = _mid_momentum(state, now, 15)
        mid_momentum_30s = _mid_momentum(state, now, 30)
        mid_momentum_1m = _mid_momentum(state, now, 60)

        return {
            "vwap_30s": vwap_30s,
            "vwap_1m": vwap_1m,
            "vwap_5m": vwap_5m,
            "order_imbalance": order_imbalance,
            "spread_momentum_30s": spread_momentum_30s,
            "mid_momentum_15s": mid_momentum_15s,
            "mid_momentum_30s": mid_momentum_30s,
            "mid_momentum_1m": mid_momentum_1m,
            "mid_price": state.book_yes.mid,
        }


def _vwap(state: MarketState, now: datetime, window_sec: int) -> float | None:
    cutoff = now - timedelta(seconds=window_sec)
    recent = [(price, size) for ts, price, size in state.trades if ts >= cutoff]
    if not recent:
        return None
    total_volume = sum(size for _, size in recent)
    if total_volume <= 0:
        return None
    return sum(price * size for price, size in recent) / total_volume


def _order_imbalance(state: MarketState) -> float:
    bid_depth = state.book_yes.bid_depth
    ask_depth = state.book_yes.ask_depth
    total = bid_depth + ask_depth
    if total <= 0:
        return 0.0
    return (bid_depth - ask_depth) / total


def _spread_momentum(state: MarketState, now: datetime, window_sec: int) -> float | None:
    cutoff = now - timedelta(seconds=window_sec)
    old = _value_at_or_before_cutoff(state.spread_hist, cutoff)
    cur = state.spread_hist[-1][1] if state.spread_hist else None
    if old is None or cur is None or old == 0:
        return None
    return (cur - old) / old


def _mid_momentum(state: MarketState, now: datetime, window_sec: int) -> float | None:
    cutoff = now - timedelta(seconds=window_sec)
    old = _value_at_or_before_cutoff(state.mid_hist, cutoff)
    cur = state.mid_hist[-1][1] if state.mid_hist else None
    if old is None or cur is None or old == 0:
        return None
    return (cur - old) / old


def _value_at_or_before_cutoff(
    series: Iterable[tuple[datetime, float]],
    cutoff: datetime,
) -> float | None:
    # deque supports iteration in insertion order; keep the latest <= cutoff.
    # If no historical point exists before cutoff, use the earliest datapoint available.
    latest_before: float | None = None
    first_value: float | None = None
    for ts, value in series:
        if first_value is None:
            first_value = value
        if ts <= cutoff:
            latest_before = value
    return latest_before if latest_before is not None else first_value
