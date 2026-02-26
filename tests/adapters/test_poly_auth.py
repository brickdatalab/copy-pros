from trader.adapters.polymarket.rest_client import extract_slug


def test_extract_slug_from_url() -> None:
    slug = extract_slug("https://polymarket.com/event/will-btc-hit-100k")
    assert slug == "will-btc-hit-100k"


def test_extract_slug_passthrough() -> None:
    slug = extract_slug("btc-updown-5m-123")
    assert slug == "btc-updown-5m-123"
