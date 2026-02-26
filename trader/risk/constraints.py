"""Order validation against hard risk and sizing constraints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderCandidate:
    side: str
    action: str
    price: float
    shares: float
    wager_usdc: float


@dataclass(frozen=True)
class RiskSnapshot:
    max_entry_price: float
    max_wager_per_side_usdc: float
    current_side_exposure_usdc: float
    max_single_wager_usdc: float
    min_wager_usdc: float
    min_shares_per_purchase: float


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason_code: str


def validate_order(candidate: OrderCandidate, risk: RiskSnapshot) -> RiskDecision:
    if candidate.action == "ENTRY" and candidate.price > risk.max_entry_price:
        return RiskDecision(allowed=False, reason_code="price_cap")

    if candidate.action == "ENTRY" and candidate.wager_usdc > risk.max_single_wager_usdc:
        return RiskDecision(allowed=False, reason_code="single_wager_cap")

    if candidate.action == "ENTRY" and candidate.wager_usdc < risk.min_wager_usdc:
        return RiskDecision(allowed=False, reason_code="min_wager")

    if candidate.action == "ENTRY" and candidate.shares < risk.min_shares_per_purchase:
        return RiskDecision(allowed=False, reason_code="min_shares")

    projected = risk.current_side_exposure_usdc + candidate.wager_usdc
    if candidate.action == "ENTRY" and projected > risk.max_wager_per_side_usdc:
        return RiskDecision(allowed=False, reason_code="side_budget")

    return RiskDecision(allowed=True, reason_code="ok")
