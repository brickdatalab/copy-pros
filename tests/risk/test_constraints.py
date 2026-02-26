from trader.risk.constraints import OrderCandidate, RiskSnapshot, validate_order


def test_reject_order_price_above_cap() -> None:
    decision = validate_order(
        OrderCandidate(side="UP", action="ENTRY", price=0.81, shares=5, wager_usdc=4.05),
        RiskSnapshot(
            max_entry_price=0.80,
            max_wager_per_side_usdc=10,
            current_side_exposure_usdc=0,
            max_single_wager_usdc=10,
            min_wager_usdc=1,
            min_shares_per_purchase=5,
        ),
    )
    assert not decision.allowed
    assert decision.reason_code == "price_cap"


def test_allow_take_profit_above_entry_cap() -> None:
    decision = validate_order(
        OrderCandidate(side="UP", action="TAKE_PROFIT", price=0.95, shares=5, wager_usdc=4.75),
        RiskSnapshot(
            max_entry_price=0.80,
            max_wager_per_side_usdc=10,
            current_side_exposure_usdc=4,
            max_single_wager_usdc=10,
            min_wager_usdc=1,
            min_shares_per_purchase=5,
        ),
    )
    assert decision.allowed
