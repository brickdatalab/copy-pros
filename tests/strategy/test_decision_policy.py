from trader.strategy.decision_policy import DecisionPolicy, DecisionAction, blend_weights


def test_external_signal_weight_cannot_exceed_0_51() -> None:
    w = blend_weights(local_weight=0.4, external_weight=0.7)
    assert w.external <= 0.51


def test_policy_emits_buy_up_for_bullish_snapshot() -> None:
    policy = DecisionPolicy()
    action = policy.decide(
        indicators={
            "order_imbalance": 0.28,
            "mid_momentum_30s": 0.03,
            "spread_momentum_30s": -0.08,
            "mid_price": 0.47,
            "vwap_1m": 0.44,
        },
        remaining_sec=300,
    )
    assert action.action == DecisionAction.BUY_UP
    assert action.confidence > 0.5
    assert action.edge > 0.1


def test_policy_emits_hold_on_weak_signal() -> None:
    policy = DecisionPolicy()
    action = policy.decide(
        indicators={
            "order_imbalance": 0.01,
            "mid_momentum_30s": 0.0,
            "spread_momentum_30s": 0.0,
            "mid_price": 0.50,
            "vwap_1m": 0.50,
        },
        remaining_sec=240,
    )
    assert action.action == DecisionAction.HOLD


def test_policy_holds_when_edge_too_low() -> None:
    policy = DecisionPolicy(min_confidence=0.1, min_edge=0.9)
    action = policy.decide(
        indicators={
            "order_imbalance": 0.05,
            "mid_momentum_30s": 0.01,
            "spread_momentum_30s": -0.01,
            "mid_price": 0.51,
            "vwap_1m": 0.50,
        },
        remaining_sec=240,
    )
    assert action.action == DecisionAction.HOLD
    assert action.reason_code == "low_edge"
