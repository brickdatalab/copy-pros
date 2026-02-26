"""In-memory orderbook primitives."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrderBook:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)

    def apply_snapshot(self, bids: list[dict[str, str]], asks: list[dict[str, str]]) -> None:
        self.bids = {
            float(level["price"]): float(level["size"])
            for level in bids
            if float(level["size"]) > 0
        }
        self.asks = {
            float(level["price"]): float(level["size"])
            for level in asks
            if float(level["size"]) > 0
        }

    def apply_change(self, changes: list[dict[str, str]]) -> None:
        for change in changes:
            side = change.get("side")
            price = float(change["price"])
            size = float(change["size"])

            book = self.bids if side == "BUY" else self.asks
            if size <= 0:
                book.pop(price, None)
            else:
                book[price] = size

    @property
    def best_bid(self) -> float:
        return max(self.bids) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return min(self.asks) if self.asks else 0.0

    @property
    def best_bid_size(self) -> float:
        if not self.bids:
            return 0.0
        return self.bids[self.best_bid]

    @property
    def best_ask_size(self) -> float:
        if not self.asks:
            return 0.0
        return self.asks[self.best_ask]

    @property
    def bid_depth(self) -> float:
        return sum(self.bids.values())

    @property
    def ask_depth(self) -> float:
        return sum(self.asks.values())

    @property
    def mid(self) -> float:
        bb = self.best_bid
        ba = self.best_ask
        if bb and ba:
            return (bb + ba) / 2.0
        return bb or ba

    @property
    def spread(self) -> float | None:
        bb = self.best_bid
        ba = self.best_ask
        if bb and ba:
            return ba - bb
        return None
