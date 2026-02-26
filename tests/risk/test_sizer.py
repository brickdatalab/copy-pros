from trader.risk.sizer import propose_order_size


def test_sizer_honors_minimums_and_caps() -> None:
    size = propose_order_size(
        target_confidence=0.9,
        price=0.40,
        current_side_exposure_usdc=0,
        max_wager_per_side_usdc=10,
        max_single_wager_usdc=10,
        min_wager_usdc=1,
        min_shares_per_purchase=5,
    )
    assert size.shares >= 5
    assert size.wager_usdc >= 1
    assert size.wager_usdc <= 10


def test_sizer_rejects_when_side_budget_exhausted() -> None:
    size = propose_order_size(
        target_confidence=0.8,
        price=0.45,
        current_side_exposure_usdc=10,
        max_wager_per_side_usdc=10,
        max_single_wager_usdc=10,
        min_wager_usdc=1,
        min_shares_per_purchase=5,
    )
    assert size.allowed is False
    assert size.reason_code == "side_budget_exhausted"


def test_reversal_sizer_clamps_within_small_remaining_budget() -> None:
    size = propose_order_size(
        target_confidence=0.90,
        price=0.20,
        current_side_exposure_usdc=8.0,
        max_wager_per_side_usdc=10,
        max_single_wager_usdc=10,
        min_wager_usdc=1,
        min_shares_per_purchase=5,
        reversal_imminent=True,
    )
    assert size.allowed is True
    assert size.wager_usdc <= 2.0
    assert size.wager_usdc >= 1.0
    assert size.shares >= 5.0


def test_reversal_sizer_blocks_when_remaining_budget_below_minimum_wager() -> None:
    size = propose_order_size(
        target_confidence=0.95,
        price=0.20,
        current_side_exposure_usdc=9.3,
        max_wager_per_side_usdc=10,
        max_single_wager_usdc=10,
        min_wager_usdc=1,
        min_shares_per_purchase=5,
        reversal_imminent=True,
    )
    assert size.allowed is False
    assert size.reason_code in {"below_min_wager", "cannot_satisfy_min_shares"}
