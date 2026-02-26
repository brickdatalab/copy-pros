"""Deterministic decision policy from indicator snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DecisionAction(StrEnum):
    BUY_UP = "BUY_UP"
    BUY_DOWN = "BUY_DOWN"
    TAKE_PROFIT_UP = "TAKE_PROFIT_UP"
    TAKE_PROFIT_DOWN = "TAKE_PROFIT_DOWN"
    HOLD = "HOLD"


@dataclass(frozen=True)
class WeightBlend:
    local: float
    external: float


@dataclass(frozen=True)
class DecisionResult:
    action: DecisionAction
    confidence: float
    edge: float
    reason_code: str
    effective_min_confidence: float
    threshold_relaxed: bool


def blend_weights(local_weight: float, external_weight: float) -> WeightBlend:
    ext = min(max(external_weight, 0.0), 0.51)
    loc = max(local_weight, 0.0)
    if loc == 0.0 and ext == 0.0:
        return WeightBlend(local=1.0, external=0.0)
    if loc + ext > 1.0:
        loc = 1.0 - ext
    return WeightBlend(local=loc, external=ext)


class DecisionPolicy:
    def __init__(self, min_confidence: float = 0.52, min_edge: float = 0.10) -> None:
        self._min_confidence = min_confidence
        self._min_edge = min_edge

    def decide(
        self,
        indicators: dict[str, float | bool | None],
        remaining_sec: int,
        *,
        candidate_up_price: float | None = None,
        candidate_down_price: float | None = None,
    ) -> DecisionResult:
        imbalance = float(indicators.get("order_imbalance") or 0.0)
        mid_momentum = float(indicators.get("mid_momentum_30s") or 0.0)
        spread_momentum = float(indicators.get("spread_momentum_30s") or 0.0)
        mid_price = indicators.get("mid_price")
        vwap_1m = indicators.get("vwap_1m")

        price_vs_vwap = 0.0
        if mid_price is not None and vwap_1m is not None and vwap_1m > 0:
            price_vs_vwap = (mid_price - vwap_1m) / vwap_1m

        up_score = (
            (imbalance * 1.1)
            + (mid_momentum * 2.2)
            + ((-spread_momentum) * 0.6)
            + (price_vs_vwap * 1.5)
        )
        down_score = (
            (-imbalance * 1.1)
            + (-mid_momentum * 2.2)
            + ((-spread_momentum) * 0.6)
            + ((-price_vs_vwap) * 1.5)
        )

        damp = 1.0 if remaining_sec >= 30 else 0.7
        up_score *= damp
        down_score *= damp

        max_score = max(up_score, down_score, 0.0)
        confidence = min(max(max_score, 0.0), 1.0)

        edge = min(abs(up_score - down_score), 1.0)
        candidate_action = DecisionAction.BUY_UP if up_score >= down_score else DecisionAction.BUY_DOWN
        reversal_imminent = indicators.get("reversal_imminent") is True
        effective_min_confidence = self._min_confidence
        threshold_relaxed = False
        if (
            reversal_imminent
            and candidate_action == DecisionAction.BUY_UP
            and candidate_up_price is not None
            and candidate_up_price < 0.25
        ):
            effective_min_confidence = 0.40
            threshold_relaxed = True

        if confidence < effective_min_confidence:
            return DecisionResult(
                action=DecisionAction.HOLD,
                confidence=confidence,
                edge=edge,
                reason_code="weak_signal",
                effective_min_confidence=effective_min_confidence,
                threshold_relaxed=threshold_relaxed,
            )

        if edge < self._min_edge:
            return DecisionResult(
                action=DecisionAction.HOLD,
                confidence=confidence,
                edge=edge,
                reason_code="low_edge",
                effective_min_confidence=effective_min_confidence,
                threshold_relaxed=threshold_relaxed,
            )

        if candidate_action == DecisionAction.BUY_UP:
            return DecisionResult(
                action=DecisionAction.BUY_UP,
                confidence=confidence,
                edge=edge,
                reason_code="bullish_reversal_setup" if threshold_relaxed else "bullish_alignment",
                effective_min_confidence=effective_min_confidence,
                threshold_relaxed=threshold_relaxed,
            )

        return DecisionResult(
            action=DecisionAction.BUY_DOWN,
            confidence=confidence,
            edge=edge,
            reason_code="bearish_alignment",
            effective_min_confidence=effective_min_confidence,
            threshold_relaxed=False,
        )
