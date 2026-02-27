"""Per-market rolling state used by indicators and strategy."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import math

from trader.engine.orderbook import OrderBook


@dataclass
class MarketState:
    market_id: str
    book_yes: OrderBook = field(default_factory=OrderBook)
    book_no: OrderBook = field(default_factory=OrderBook)
    trades: deque[tuple[datetime, float, float, int]] = field(default_factory=deque)
    spread_hist: deque[tuple[datetime, float]] = field(default_factory=deque)
    mid_hist: deque[tuple[datetime, float]] = field(default_factory=deque)
    vwap_30s_hist: deque[tuple[datetime, float]] = field(default_factory=deque)
    mid_momentum_30s_hist: deque[tuple[datetime, float]] = field(default_factory=deque)
    flow_30s_window: deque[tuple[datetime, float, bool, bool]] = field(default_factory=deque)
    flow_total_volume_30s: float = 0.0
    flow_large_volume_30s: float = 0.0
    flow_trade_count_30s: int = 0
    flow_unknown_trade_count_30s: int = 0
    flow_large_trade_size: float = 75.0
    flow_large_ratio_window_seconds: int = 30
    ew_half_life_seconds: float = 15.0
    ew_delta: float = 0.0
    ew_abs_vol: float = 0.0
    min_ew_volume: float = 0.0
    ew_last_ts: datetime | None = None
    vpin_bucket_volume: float = 300.0
    vpin_num_buckets: int = 10
    vpin_bucket_buy: float = 0.0
    vpin_bucket_sell: float = 0.0
    vpin_bucket_total: float = 0.0
    vpin_toxicity_buffer: deque[float] = field(default_factory=deque)
    vpin_toxicity_sum: float = 0.0

    def configure_flow(
        self,
        *,
        ew_half_life_seconds: float,
        vpin_bucket_volume: float,
        vpin_num_buckets: int,
        large_trade_size: float,
        large_ratio_window_seconds: int,
        min_ew_volume: float = 0.0,
    ) -> None:
        self.ew_half_life_seconds = max(1e-3, ew_half_life_seconds)
        self.vpin_bucket_volume = max(1e-6, vpin_bucket_volume)
        self.vpin_num_buckets = max(1, vpin_num_buckets)
        self.flow_large_trade_size = max(0.0, large_trade_size)
        self.flow_large_ratio_window_seconds = max(1, large_ratio_window_seconds)
        self.min_ew_volume = max(0.0, min_ew_volume)

    def add_trade(self, price: float, size: float, ts: datetime, side: int = 0) -> None:
        clipped_side = 1 if side > 0 else (-1 if side < 0 else 0)
        self.trades.append((ts, price, size, clipped_side))
        self._prune_trade(self.trades, ts, 300)
        self._update_ew_signed_delta(size=size, side=clipped_side, ts=ts)
        self._update_vpin(size=size, side=clipped_side)
        self._update_flow_30s(size=size, side=clipped_side, ts=ts)

    def record_mid(self, mid: float, ts: datetime) -> None:
        self.mid_hist.append((ts, mid))
        self._prune_metric(self.mid_hist, ts, 300)

    def record_spread(self, spread: float, ts: datetime) -> None:
        self.spread_hist.append((ts, spread))
        self._prune_metric(self.spread_hist, ts, 300)

    def record_vwap_30s(self, value: float, ts: datetime) -> None:
        self.vwap_30s_hist.append((ts, value))
        self._prune_metric(self.vwap_30s_hist, ts, 300)

    def record_mid_momentum_30s(self, value: float, ts: datetime) -> None:
        self.mid_momentum_30s_hist.append((ts, value))
        self._prune_metric(self.mid_momentum_30s_hist, ts, 300)

    @property
    def ew_delta_imbalance(self) -> float:
        if self.ew_abs_vol < self.min_ew_volume:
            return 0.0
        denom = self.ew_abs_vol + 1e-9
        value = self.ew_delta / denom
        return min(max(value, -1.0), 1.0)

    @property
    def flow_toxicity(self) -> float:
        if not self.vpin_toxicity_buffer:
            return 0.0
        return min(max(self.vpin_toxicity_sum / len(self.vpin_toxicity_buffer), 0.0), 1.0)

    @property
    def large_trade_ratio(self) -> float:
        return min(max(self.flow_large_volume_30s / (self.flow_total_volume_30s + 1e-9), 0.0), 1.0)

    @property
    def unknown_trade_ratio(self) -> float:
        if self.flow_trade_count_30s <= 0:
            return 0.0
        return min(max(self.flow_unknown_trade_count_30s / self.flow_trade_count_30s, 0.0), 1.0)

    def snapshot_book_metrics(self, now: datetime) -> None:
        if self.book_yes.mid:
            self.record_mid(self.book_yes.mid, now)
        spread = self.book_yes.spread
        if spread is not None:
            self.record_spread(spread, now)

    @staticmethod
    def _prune_metric(dq: deque[tuple[datetime, float]], now: datetime, secs: int) -> None:
        cutoff = now - timedelta(seconds=secs)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    @staticmethod
    def _prune_trade(dq: deque[tuple[datetime, float, float, int]], now: datetime, secs: int) -> None:
        cutoff = now - timedelta(seconds=secs)
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _update_ew_signed_delta(self, *, size: float, side: int, ts: datetime) -> None:
        if self.ew_last_ts is None:
            decay = 1.0
        else:
            dt = max(0.0, (ts - self.ew_last_ts).total_seconds())
            lam = math.log(2.0) / self.ew_half_life_seconds
            decay = math.exp(-lam * dt)
        self.ew_delta = self.ew_delta * decay + (float(side) * size)
        self.ew_abs_vol = self.ew_abs_vol * decay + abs(size)
        self.ew_last_ts = ts

    def _update_vpin(self, *, size: float, side: int) -> None:
        if side > 0:
            self.vpin_bucket_buy += size
        elif side < 0:
            self.vpin_bucket_sell += size
        self.vpin_bucket_total += size

        if self.vpin_bucket_total < self.vpin_bucket_volume:
            return
        tox = abs(self.vpin_bucket_buy - self.vpin_bucket_sell) / max(self.vpin_bucket_total, 1e-9)
        if len(self.vpin_toxicity_buffer) >= self.vpin_num_buckets:
            self.vpin_toxicity_sum -= self.vpin_toxicity_buffer.popleft()
        self.vpin_toxicity_buffer.append(tox)
        self.vpin_toxicity_sum += tox

        self.vpin_bucket_buy = 0.0
        self.vpin_bucket_sell = 0.0
        self.vpin_bucket_total = 0.0

    def _update_flow_30s(self, *, size: float, side: int, ts: datetime) -> None:
        is_large = size >= self.flow_large_trade_size
        is_unknown = side == 0
        self.flow_30s_window.append((ts, size, is_large, is_unknown))
        self.flow_total_volume_30s += size
        if is_large:
            self.flow_large_volume_30s += size
        self.flow_trade_count_30s += 1
        if is_unknown:
            self.flow_unknown_trade_count_30s += 1

        cutoff = ts - timedelta(seconds=self.flow_large_ratio_window_seconds)
        while self.flow_30s_window and self.flow_30s_window[0][0] < cutoff:
            _, old_size, old_large, old_unknown = self.flow_30s_window.popleft()
            self.flow_total_volume_30s = max(0.0, self.flow_total_volume_30s - old_size)
            if old_large:
                self.flow_large_volume_30s = max(0.0, self.flow_large_volume_30s - old_size)
            self.flow_trade_count_30s = max(0, self.flow_trade_count_30s - 1)
            if old_unknown:
                self.flow_unknown_trade_count_30s = max(0, self.flow_unknown_trade_count_30s - 1)
