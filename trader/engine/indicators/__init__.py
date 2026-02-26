"""Indicator computation engine."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from trader.engine.state import MarketState

IndicatorValue = float | bool | str | None


@dataclass
class IndicatorEngine:
    vwap_up_delta_15s: float = 0.003
    mid_flat_delta_15s: float = 0.001
    momentum_accel_5s: float = 0.002
    enable_reversal_imminent: bool = True
    flow_weight_preset: str = "flow_v1"

    def compute(self, state: MarketState, now: datetime) -> dict[str, IndicatorValue]:
        state.snapshot_book_metrics(now)

        vwap_30s = _vwap(state, now, 30)
        vwap_1m = _vwap(state, now, 60)
        vwap_5m = _vwap(state, now, 300)

        order_imbalance = _order_imbalance(state)
        spread_momentum_30s = _spread_momentum(state, now, 30)
        mid_momentum_15s = _mid_momentum(state, now, 15)
        mid_momentum_30s = _mid_momentum(state, now, 30)
        mid_momentum_1m = _mid_momentum(state, now, 60)
        mid_price = state.book_yes.mid

        if vwap_30s is not None:
            state.record_vwap_30s(vwap_30s, now)
        if mid_momentum_30s is not None:
            state.record_mid_momentum_30s(mid_momentum_30s, now)

        vwap_30s_15s_ago = _value_at_or_before_cutoff(state.vwap_30s_hist, now - timedelta(seconds=15))
        mid_15s_ago = _value_at_or_before_cutoff(state.mid_hist, now - timedelta(seconds=15))
        mid_momentum_30s_5s_ago = _value_at_or_before_cutoff(
            state.mid_momentum_30s_hist,
            now - timedelta(seconds=5),
        )

        vwap_delta_15s = (
            (vwap_30s - vwap_30s_15s_ago)
            if (vwap_30s is not None and vwap_30s_15s_ago is not None)
            else None
        )
        mid_delta_15s = (
            (mid_price - mid_15s_ago)
            if (mid_price is not None and mid_15s_ago is not None)
            else None
        )
        momentum_delta_5s = (
            (mid_momentum_30s - mid_momentum_30s_5s_ago)
            if (mid_momentum_30s is not None and mid_momentum_30s_5s_ago is not None)
            else None
        )

        # Smart-money reversal marker: only emits when all distressed bullish
        # accumulation conditions pass on the same tick.
        reversal_imminent = False
        if self.enable_reversal_imminent:
            distressed = mid_price < 0.25
            strong_imbalance = order_imbalance > 0.30
            vwap_divergence = (
                vwap_delta_15s is not None
                and mid_delta_15s is not None
                and vwap_delta_15s >= self.vwap_up_delta_15s
                and mid_delta_15s <= self.mid_flat_delta_15s
            )
            momentum_turn = (
                mid_momentum_30s is not None
                and mid_momentum_30s_5s_ago is not None
                and mid_momentum_30s > 0
                and mid_momentum_30s_5s_ago <= 0
            )
            momentum_accel = momentum_delta_5s is not None and momentum_delta_5s >= self.momentum_accel_5s
            reversal_imminent = distressed and strong_imbalance and vwap_divergence and (momentum_turn or momentum_accel)

        return {
            "vwap_30s": vwap_30s,
            "vwap_1m": vwap_1m,
            "vwap_5m": vwap_5m,
            "order_imbalance": order_imbalance,
            "spread_momentum_30s": spread_momentum_30s,
            "mid_momentum_15s": mid_momentum_15s,
            "mid_momentum_30s": mid_momentum_30s,
            "mid_momentum_1m": mid_momentum_1m,
            "mid_price": mid_price,
            "vwap_delta_15s": vwap_delta_15s,
            "mid_delta_15s": mid_delta_15s,
            "momentum_delta_5s": momentum_delta_5s,
            "reversal_imminent": reversal_imminent,
            "ew_delta_imbalance": state.ew_delta_imbalance,
            "flow_toxicity": state.flow_toxicity,
            "large_trade_ratio": state.large_trade_ratio,
            "unknown_trade_ratio": state.unknown_trade_ratio,
            "flow_weight_preset": self.flow_weight_preset,
        }


def _vwap(state: MarketState, now: datetime, window_sec: int) -> float | None:
    cutoff = now - timedelta(seconds=window_sec)
    recent = [(price, size) for ts, price, size, _ in state.trades if ts >= cutoff]
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
