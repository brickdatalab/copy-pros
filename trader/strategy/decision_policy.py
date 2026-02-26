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
    flow_boost: float


@dataclass(frozen=True)
class WeightPreset:
    w_mid_momentum: float
    w_price_vs_vwap: float
    w_order_imbalance: float
    w_spread_momentum: float
    w_ew_delta: float
    w_toxicity: float
    w_large_ratio: float


def blend_weights(local_weight: float, external_weight: float) -> WeightBlend:
    ext = min(max(external_weight, 0.0), 0.51)
    loc = max(local_weight, 0.0)
    if loc == 0.0 and ext == 0.0:
        return WeightBlend(local=1.0, external=0.0)
    if loc + ext > 1.0:
        loc = 1.0 - ext
    return WeightBlend(local=loc, external=ext)


class DecisionPolicy:
    def __init__(
        self,
        min_confidence: float = 0.52,
        min_edge: float = 0.10,
        *,
        enable_flow_signals: bool = True,
        flow_weight_preset: str = "flow_v1",
        flow_unknown_ratio_cutoff: float = 0.35,
        flow_unknown_delta_scale: float = 0.5,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_edge = min_edge
        self._enable_flow_signals = enable_flow_signals
        self._flow_weight_preset = "flow_v1" if flow_weight_preset == "v1" else flow_weight_preset
        self._flow_unknown_ratio_cutoff = flow_unknown_ratio_cutoff
        self._flow_unknown_delta_scale = flow_unknown_delta_scale

    def decide(
        self,
        indicators: dict[str, float | bool | str | None],
        remaining_sec: int,
        *,
        candidate_up_price: float | None = None,
        candidate_down_price: float | None = None,
    ) -> DecisionResult:
        imbalance = float(indicators.get("order_imbalance") or 0.0)
        mid_momentum = float(indicators.get("mid_momentum_30s") or 0.0)
        spread_momentum = float(indicators.get("spread_momentum_30s") or 0.0)
        mid_price = _as_float(indicators.get("mid_price"))
        vwap_1m = _as_float(indicators.get("vwap_1m"))

        price_vs_vwap = 0.0
        if mid_price is not None and vwap_1m is not None and vwap_1m > 0:
            price_vs_vwap = (mid_price - vwap_1m) / vwap_1m

        weights = self._weights()
        up_score = (
            (imbalance * weights.w_order_imbalance)
            + (mid_momentum * weights.w_mid_momentum)
            + ((-spread_momentum) * weights.w_spread_momentum)
            + (price_vs_vwap * weights.w_price_vs_vwap)
        )
        down_score = (
            (-imbalance * weights.w_order_imbalance)
            + (-mid_momentum * weights.w_mid_momentum)
            + ((-spread_momentum) * weights.w_spread_momentum)
            + ((-price_vs_vwap) * weights.w_price_vs_vwap)
        )

        flow_boost = 0.0
        if self._enable_flow_signals:
            ew_delta = _clamp(float(indicators.get("ew_delta_imbalance") or 0.0), -1.0, 1.0)
            toxicity = _clamp(float(indicators.get("flow_toxicity") or 0.0), 0.0, 1.0)
            large_ratio = _clamp(float(indicators.get("large_trade_ratio") or 0.0), 0.0, 1.0)
            unknown_ratio = _clamp(float(indicators.get("unknown_trade_ratio") or 0.0), 0.0, 1.0)

            if unknown_ratio > self._flow_unknown_ratio_cutoff:
                ew_delta *= self._flow_unknown_delta_scale

            up_delta_align = max(0.0, ew_delta)
            down_delta_align = max(0.0, -ew_delta)
            up_flow = (
                (weights.w_ew_delta * ew_delta)
                + (weights.w_toxicity * toxicity * up_delta_align)
                + (weights.w_large_ratio * large_ratio * up_delta_align)
            )
            down_flow = (
                (weights.w_ew_delta * (-ew_delta))
                + (weights.w_toxicity * toxicity * down_delta_align)
                + (weights.w_large_ratio * large_ratio * down_delta_align)
            )
            up_score += up_flow
            down_score += down_flow
            flow_boost = max(up_flow, down_flow, 0.0)

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
        # Reversal layer: selectively relax BUY_UP confidence when setup is
        # distressed and the candidate entry price remains below 0.25.
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
                flow_boost=flow_boost,
            )

        if edge < self._min_edge:
            return DecisionResult(
                action=DecisionAction.HOLD,
                confidence=confidence,
                edge=edge,
                reason_code="low_edge",
                effective_min_confidence=effective_min_confidence,
                threshold_relaxed=threshold_relaxed,
                flow_boost=flow_boost,
            )

        if candidate_action == DecisionAction.BUY_UP:
            return DecisionResult(
                action=DecisionAction.BUY_UP,
                confidence=confidence,
                edge=edge,
                reason_code="bullish_reversal_setup" if threshold_relaxed else "momentum_alignment_entry",
                effective_min_confidence=effective_min_confidence,
                threshold_relaxed=threshold_relaxed,
                flow_boost=flow_boost,
            )

        return DecisionResult(
            action=DecisionAction.BUY_DOWN,
            confidence=confidence,
            edge=edge,
            reason_code="momentum_alignment_entry",
            effective_min_confidence=effective_min_confidence,
            threshold_relaxed=False,
            flow_boost=flow_boost,
        )

    def _weights(self) -> WeightPreset:
        if self._flow_weight_preset == "baseline":
            return WeightPreset(
                w_mid_momentum=2.2,
                w_price_vs_vwap=1.5,
                w_order_imbalance=1.1,
                w_spread_momentum=0.6,
                w_ew_delta=0.0,
                w_toxicity=0.0,
                w_large_ratio=0.0,
            )
        return WeightPreset(
            w_mid_momentum=1.4,
            w_price_vs_vwap=0.7,
            w_order_imbalance=0.6,
            w_spread_momentum=0.5,
            w_ew_delta=2.2,
            w_toxicity=0.6,
            w_large_ratio=0.4,
        )


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None
