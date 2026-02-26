from trader.event_context import build_event_context


def test_remaining_seconds_mid_event() -> None:
    ctx = build_event_context(
        event_slug="btc-updown-15m-test",
        event_id="ev-1",
        started_at_ts=0,
        duration_sec=900,
        now_ts=420,
    )
    assert ctx.remaining_sec == 480


def test_remaining_seconds_near_end() -> None:
    ctx = build_event_context(
        event_slug="eth-updown-15m-test",
        event_id="ev-2",
        started_at_ts=0,
        duration_sec=900,
        now_ts=810,
    )
    assert ctx.remaining_sec == 90


def test_remaining_seconds_zero_after_close() -> None:
    ctx = build_event_context(
        event_slug="sol-updown-5m-test",
        event_id="ev-3",
        started_at_ts=0,
        duration_sec=300,
        now_ts=400,
    )
    assert ctx.remaining_sec == 0
    assert ctx.is_closed is True
