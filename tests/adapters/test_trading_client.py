from trader.adapters.polymarket.trading_client import normalize_side


def test_normalize_side_maps_aliases() -> None:
    assert normalize_side("UP") == "BUY"
    assert normalize_side("DOWN") == "BUY"
    assert normalize_side("SELL") == "SELL"
