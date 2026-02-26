"""Order sizing utilities."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SizeProposal:
    allowed: bool
    shares: float
    wager_usdc: float
    reason_code: str


def propose_order_size(
    target_confidence: float,
    price: float,
    current_side_exposure_usdc: float,
    max_wager_per_side_usdc: float,
    max_single_wager_usdc: float,
    min_wager_usdc: float,
    min_shares_per_purchase: float,
    reversal_imminent: bool = False,
) -> SizeProposal:
    available_side = max_wager_per_side_usdc - current_side_exposure_usdc
    if available_side <= 0:
        return SizeProposal(False, 0.0, 0.0, "side_budget_exhausted")
    if price <= 0:
        return SizeProposal(False, 0.0, 0.0, "invalid_price")

    confidence = min(max(target_confidence, 0.0), 1.0)
    upper = min(available_side, max_single_wager_usdc)
    if reversal_imminent:
        # Convexity mode: target shares first (monotone by confidence), then
        # derive wager from shares*price while preserving existing risk caps.
        max_affordable_shares = math.floor((upper / price) * 1000) / 1000
        if max_affordable_shares < min_shares_per_purchase:
            return SizeProposal(False, 0.0, 0.0, "cannot_satisfy_min_shares")

        target_shares = min_shares_per_purchase + (max_affordable_shares - min_shares_per_purchase) * confidence
        shares = math.floor(target_shares * 1000) / 1000
        shares = max(min_shares_per_purchase, shares)
        wager = shares * price

        if wager > upper:
            shares = math.floor((upper / price) * 1000) / 1000
            wager = shares * price
        if shares < min_shares_per_purchase:
            return SizeProposal(False, 0.0, 0.0, "cannot_satisfy_min_shares")
        if wager < min_wager_usdc:
            return SizeProposal(False, 0.0, 0.0, "below_min_wager")
        return SizeProposal(True, shares, wager, "ok")

    target_wager = max(min_wager_usdc, upper * max(0.35, confidence))
    target_wager = min(target_wager, upper)

    if target_wager < min_wager_usdc:
        return SizeProposal(False, 0.0, 0.0, "below_min_wager")

    min_shares_from_wager = target_wager / price
    shares = max(min_shares_per_purchase, min_shares_from_wager)

    # Round down to 3 decimals for stable API order quantities.
    shares = math.floor(shares * 1000) / 1000
    wager = shares * price

    if wager > upper:
        capped_shares = math.floor((upper / price) * 1000) / 1000
        if capped_shares < min_shares_per_purchase:
            return SizeProposal(False, 0.0, 0.0, "cannot_satisfy_min_shares")
        shares = capped_shares
        wager = shares * price

    if wager < min_wager_usdc:
        return SizeProposal(False, 0.0, 0.0, "below_min_wager")

    return SizeProposal(True, shares, wager, "ok")
