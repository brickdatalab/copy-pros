"""Entry gating logic to avoid symmetric both-side accumulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryPolicyInput:
    side: str
    confidence: float
    price: float
    up_exposure_usdc: float
    down_exposure_usdc: float
    anchor_side: str | None
    allow_both_sides: bool
    hedge_max_exposure_ratio: float
    hedge_min_confidence: float
    hedge_max_entry_price: float
    signal_streak: int
    min_signal_streak: int
    last_entry_emit_ms: int
    now_ms: int
    entry_cooldown_ms: int


@dataclass(frozen=True)
class EntryPolicyDecision:
    allowed: bool
    reason_code: str


def evaluate_entry_policy(inp: EntryPolicyInput) -> EntryPolicyDecision:
    if inp.signal_streak < inp.min_signal_streak:
        return EntryPolicyDecision(False, "signal_not_persistent")

    if inp.last_entry_emit_ms > 0:
        elapsed = inp.now_ms - inp.last_entry_emit_ms
        if elapsed < inp.entry_cooldown_ms:
            return EntryPolicyDecision(False, "entry_cooldown")

    dominant_side = _dominant_side(inp.up_exposure_usdc, inp.down_exposure_usdc, inp.anchor_side)
    if dominant_side is None or dominant_side == inp.side:
        return EntryPolicyDecision(True, "ok")

    if not inp.allow_both_sides:
        return EntryPolicyDecision(False, "both_sides_disabled")

    if inp.confidence < inp.hedge_min_confidence:
        return EntryPolicyDecision(False, "hedge_confidence")

    if inp.price > inp.hedge_max_entry_price:
        return EntryPolicyDecision(False, "hedge_price_cap")

    dominant_exposure = inp.up_exposure_usdc if dominant_side == "UP" else inp.down_exposure_usdc
    hedge_exposure = inp.up_exposure_usdc if inp.side == "UP" else inp.down_exposure_usdc
    hedge_limit = max(0.0, dominant_exposure * max(0.0, inp.hedge_max_exposure_ratio))
    if hedge_exposure >= hedge_limit:
        return EntryPolicyDecision(False, "hedge_ratio_cap")

    return EntryPolicyDecision(True, "ok")


def _dominant_side(
    up_exposure_usdc: float,
    down_exposure_usdc: float,
    anchor_side: str | None,
) -> str | None:
    if up_exposure_usdc > down_exposure_usdc:
        return "UP"
    if down_exposure_usdc > up_exposure_usdc:
        return "DOWN"
    if up_exposure_usdc > 0 and down_exposure_usdc > 0 and anchor_side in {"UP", "DOWN"}:
        return anchor_side
    return None
