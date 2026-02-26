from trader.adapters.polymarket.ws_client import normalize_ws_payload


def test_normalize_ws_payload_handles_batch() -> None:
    payload = [
        {"asset_id": "a1", "event_type": "book", "bids": [], "asks": []},
        {"asset_id": "a2", "event_type": "price_change", "changes": []},
    ]
    out = normalize_ws_payload(payload)
    assert len(out) == 2
    assert out[0]["asset_id"] == "a1"


def test_normalize_ws_payload_handles_single() -> None:
    payload = {"asset_id": "a1", "event_type": "last_trade_price", "price": "0.5"}
    out = normalize_ws_payload(payload)
    assert len(out) == 1
    assert out[0]["event_type"] == "last_trade_price"
