from trader.strategy.entry_policy import EntryPolicyInput, evaluate_entry_policy


def _base_input(**overrides: float | int | bool | str) -> EntryPolicyInput:
    data: dict[str, float | int | bool | str] = {
        "side": "UP",
        "confidence": 0.9,
        "price": 0.45,
        "up_exposure_usdc": 0.0,
        "down_exposure_usdc": 0.0,
        "anchor_side": None,
        "allow_both_sides": True,
        "hedge_max_exposure_ratio": 0.25,
        "hedge_min_confidence": 0.85,
        "hedge_max_entry_price": 0.35,
        "signal_streak": 4,
        "min_signal_streak": 4,
        "last_entry_emit_ms": 0,
        "now_ms": 10_000,
        "entry_cooldown_ms": 750,
    }
    data.update(overrides)
    return EntryPolicyInput(**data)


def test_rejects_when_signal_not_persistent() -> None:
    decision = evaluate_entry_policy(_base_input(signal_streak=2))
    assert decision.allowed is False
    assert decision.reason_code == "signal_not_persistent"


def test_rejects_when_inside_entry_cooldown() -> None:
    decision = evaluate_entry_policy(_base_input(last_entry_emit_ms=9_500, now_ms=10_000))
    assert decision.allowed is False
    assert decision.reason_code == "entry_cooldown"


def test_rejects_weak_hedge_on_opposite_side() -> None:
    decision = evaluate_entry_policy(
        _base_input(
            side="DOWN",
            confidence=0.70,
            up_exposure_usdc=8.0,
            down_exposure_usdc=0.0,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "hedge_confidence"


def test_rejects_hedge_when_price_too_high() -> None:
    decision = evaluate_entry_policy(
        _base_input(
            side="DOWN",
            confidence=0.90,
            price=0.50,
            up_exposure_usdc=8.0,
            down_exposure_usdc=0.0,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "hedge_price_cap"


def test_rejects_hedge_when_ratio_cap_reached() -> None:
    decision = evaluate_entry_policy(
        _base_input(
            side="DOWN",
            confidence=0.90,
            price=0.20,
            up_exposure_usdc=8.0,
            down_exposure_usdc=2.1,
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "hedge_ratio_cap"


def test_allows_small_opposite_side_hedge() -> None:
    decision = evaluate_entry_policy(
        _base_input(
            side="DOWN",
            confidence=0.90,
            price=0.20,
            up_exposure_usdc=8.0,
            down_exposure_usdc=1.0,
        )
    )
    assert decision.allowed is True
    assert decision.reason_code == "ok"


def test_anchor_side_prevents_symmetric_tie_bypass() -> None:
    decision = evaluate_entry_policy(
        _base_input(
            side="DOWN",
            confidence=0.70,
            up_exposure_usdc=2.0,
            down_exposure_usdc=2.0,
            anchor_side="UP",
        )
    )
    assert decision.allowed is False
    assert decision.reason_code == "hedge_confidence"
