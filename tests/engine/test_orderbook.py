from trader.engine.orderbook import OrderBook


def test_snapshot_sets_best_levels_and_depth() -> None:
    book = OrderBook()
    book.apply_snapshot(
        bids=[{"price": "0.42", "size": "120"}, {"price": "0.40", "size": "55"}],
        asks=[{"price": "0.46", "size": "100"}, {"price": "0.50", "size": "88"}],
    )

    assert book.best_bid == 0.42
    assert book.best_ask == 0.46
    assert book.best_bid_size == 120.0
    assert book.best_ask_size == 100.0
    assert book.bid_depth == 175.0
    assert book.ask_depth == 188.0


def test_price_change_applies_incremental_updates() -> None:
    book = OrderBook()
    book.apply_snapshot(
        bids=[{"price": "0.42", "size": "120"}],
        asks=[{"price": "0.46", "size": "100"}],
    )
    book.apply_change(
        changes=[
            {"side": "BUY", "price": "0.43", "size": "90"},
            {"side": "SELL", "price": "0.46", "size": "0"},
        ]
    )

    assert book.best_bid == 0.43
    assert book.best_ask == 0.0
    assert book.best_bid_size == 90.0

