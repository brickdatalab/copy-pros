from trader.runtime.orchestrator import dominant_side


def test_dominant_side_prefers_larger_exposure() -> None:
    side = dominant_side({"UP": 7.5, "DOWN": 3.2})
    assert side == "UP"


def test_dominant_side_none_when_equal() -> None:
    side = dominant_side({"UP": 4.0, "DOWN": 4.0})
    assert side is None
