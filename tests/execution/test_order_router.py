from trader.execution.order_router import build_entry_order


def test_build_entry_order_rounds_price_and_size() -> None:
    order = build_entry_order(
        side="UP",
        price=0.50312,
        shares=7.2349,
        client_order_id="r1",
    )
    assert order["side"] == "UP"
    assert order["price"] == 0.5031
    assert order["shares"] == 7.234
    assert order["client_order_id"] == "r1"
