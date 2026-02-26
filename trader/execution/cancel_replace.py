"""Cancel/replace heuristics."""

from __future__ import annotations


def should_cancel(order_age_s: float, spread_momentum: float | None, mid_momentum_30s: float | None) -> bool:
    if order_age_s < 2.0:
        return False
    if spread_momentum is None or mid_momentum_30s is None:
        return False
    return spread_momentum > 0.15 and mid_momentum_30s < -0.05
