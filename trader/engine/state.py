"""Per-market rolling state used by indicators and strategy."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trader.engine.orderbook import OrderBook


@dataclass
class MarketState:
    market_id: str
    book_yes: OrderBook = field(default_factory=OrderBook)
    book_no: OrderBook = field(default_factory=OrderBook)
    trades: deque[tuple[datetime, float, float]] = field(default_factory=deque)
    spread_hist: deque[tuple[datetime, float]] = field(default_factory=deque)
    mid_hist: deque[tuple[datetime, float]] = field(default_factory=deque)

    def add_trade(self, price: float, size: float, ts: datetime) -> None:
        self.trades.append((ts, price, size))
        self._prune_trade(self.trades, ts, 300)

    def record_mid(self, mid: float, ts: datetime) -> None:
        self.mid_hist.append((ts, mid))
        self._prune_metric(self.mid_hist, ts, 300)

    def record_spread(self, spread: float, ts: datetime) -> None:
        self.spread_hist.append((ts, spread))
        self._prune_metric(self.spread_hist, ts, 300)

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
    def _prune_trade(dq: deque[tuple[datetime, float, float]], now: datetime, secs: int) -> None:
        cutoff = now - timedelta(seconds=secs)
        while dq and dq[0][0] < cutoff:
            dq.popleft()
