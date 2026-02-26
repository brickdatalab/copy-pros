def test_import() -> None:
    import trader
    from trader.runtime.continuous_runner import parse_market_selection

    assert trader is not None
    assert parse_market_selection("btc15,eth5")
