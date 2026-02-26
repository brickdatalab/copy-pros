from trader.execution.cancel_replace import should_cancel


def test_cancel_unfilled_when_momentum_reversal() -> None:
    assert should_cancel(order_age_s=3.0, spread_momentum=0.22, mid_momentum_30s=-0.18)


def test_keep_order_when_signal_stable() -> None:
    assert not should_cancel(order_age_s=1.2, spread_momentum=-0.05, mid_momentum_30s=0.04)
