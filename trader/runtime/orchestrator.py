"""Async orchestration for one-event bot runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, TextIO
from uuid import uuid4

from trader.adapters.polymarket.rest_client import (
    EventMarketContext,
    fetch_event_market_context,
    fetch_winning_side,
)
from trader.adapters.polymarket.trading_client import TradingClient
from trader.adapters.polymarket.ws_client import classify_trade_side, stream_market_events
from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.config import TraderConfig
from trader.engine.indicators import IndicatorEngine, IndicatorValue
from trader.engine.state import MarketState
from trader.execution.cancel_replace import should_cancel
from trader.execution.order_router import build_entry_order
from trader.risk.constraints import OrderCandidate, RiskSnapshot, validate_order
from trader.risk.sizer import propose_order_size
from trader.strategy.entry_policy import EntryPolicyInput, evaluate_entry_policy
from trader.strategy.decision_policy import DecisionAction, DecisionPolicy
from trader.ui.console import BotConsole


def compute_target_runtime_seconds(duration_sec: int, start_offset: int) -> int:
    if duration_sec <= 0:
        return 0
    remaining = duration_sec - start_offset
    return max(0, remaining)


_INDICATOR_ACTIVITY_KEYS: tuple[str, ...] = (
    "mid_price",
    "order_imbalance",
    "vwap_1m",
    "mid_momentum_30s",
    "spread_momentum_30s",
    "reversal_imminent",
    "vwap_delta_15s",
    "mid_delta_15s",
    "momentum_delta_5s",
    "ew_delta_imbalance",
    "flow_toxicity",
    "large_trade_ratio",
    "unknown_trade_ratio",
    "flow_weight_preset",
)


def _ingest_units_for_event(event_type: str, payload: dict[str, Any]) -> int:
    """Estimate WS ingest workload units for UI telemetry.

    Counting only one unit per WS frame can make active streams look stale when
    large batch payloads arrive less frequently. This helper counts the size of
    payload mutations so ingest reflects actual applied market data volume.
    """
    if event_type == "book":
        bids = payload.get("bids")
        asks = payload.get("asks")
        bid_n = len(bids) if isinstance(bids, list) else 0
        ask_n = len(asks) if isinstance(asks, list) else 0
        return max(1, bid_n + ask_n)
    if event_type == "price_change":
        changes = payload.get("changes")
        return max(1, len(changes) if isinstance(changes, list) else 0)
    return 1


def empty_runtime_activity_snapshot() -> dict[str, Any]:
    return {
        "ws_ticks": 0,
        "ws_last_event_type": None,
        "ws_last_ts": None,
        "indicator_updates": 0,
        "indicator_last_ts": None,
        "decision_count": 0,
        "decision_last_ts": None,
        "decision_last_action": None,
        "decision_last_confidence": None,
        "decision_last_edge": None,
        "decision_last_reason": None,
        "intent_queue_depth": 0,
        "order_count": 0,
        "fill_count": 0,
        "open_orders": 0,
        "last_order_action": None,
        "last_order_side": None,
        "last_order_price": None,
        "last_order_shares": None,
        "last_order_status": None,
        "last_order_ts": None,
        "flow_blocked_entries_count": 0,
        "filled_up_exposure_usdc": 0.0,
        "filled_down_exposure_usdc": 0.0,
        "open_up_exposure_usdc": 0.0,
        "open_down_exposure_usdc": 0.0,
        "side_budget_limit_usdc": 0.0,
        "open_position_sides_count": 0,
        "position_rollup": {"UP": None, "DOWN": None},
        "indicators": {key: None for key in _INDICATOR_ACTIVITY_KEYS},
    }


@dataclass
class OpenOrder:
    order_id: str
    side: str
    action: str
    price: float
    shares: float
    wager_usdc: float
    reason_code: str | None
    entry_signal_snapshot: dict[str, IndicatorValue] | None
    created_at: float


@dataclass(frozen=True)
class EventOrderRecord:
    action: str
    side: str
    price: float
    shares: float
    wager_usdc: float
    status: str
    ts: str
    reason_code: str | None = None
    entry_ew_delta_imbalance: float | None = None
    entry_flow_toxicity: float | None = None
    entry_large_trade_ratio: float | None = None
    entry_unknown_trade_ratio: float | None = None
    entry_flow_weight_preset: str | None = None
    entry_signal_snapshot: dict[str, IndicatorValue] | None = None


@dataclass(frozen=True)
class EventRunResult:
    run_id: str
    event_slug: str
    condition_id: str
    timeframe_minutes: int
    started_at: str
    ended_at: str
    winning_side: str | None
    predicted_side: str | None
    was_prediction_accurate: bool | None
    total_decisions: int
    total_orders: int
    total_fills: int
    flow_blocked_entries_count: int
    up_notional: float
    down_notional: float
    orders: list[EventOrderRecord]


@dataclass
class BotRuntime:
    cfg: TraderConfig
    console: BotConsole
    writer: BufferedSupabaseWriter
    resume_event: asyncio.Event | None = None
    market_state: MarketState = field(default_factory=lambda: MarketState(market_id="market"))
    indicator_engine: IndicatorEngine = field(default_factory=IndicatorEngine)
    decision_policy: DecisionPolicy = field(default_factory=DecisionPolicy)
    indicators: dict[str, IndicatorValue] = field(default_factory=dict)
    intents: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    open_orders: dict[str, OpenOrder] = field(default_factory=dict)
    exposure_filled_usdc: dict[str, float] = field(default_factory=lambda: {"UP": 0.0, "DOWN": 0.0})
    exposure_open_usdc: dict[str, float] = field(default_factory=lambda: {"UP": 0.0, "DOWN": 0.0})
    filled_shares: dict[str, float] = field(default_factory=lambda: {"UP": 0.0, "DOWN": 0.0})
    anchor_side: str | None = None
    last_signal_action: DecisionAction | None = None
    signal_action_streak: int = 0
    last_entry_emit_ms: dict[str, int] = field(default_factory=lambda: {"UP": 0, "DOWN": 0})
    decisions_count: int = 0
    orders_count: int = 0
    fills_count: int = 0
    order_records: list[EventOrderRecord] = field(default_factory=list)
    ws_ticks: int = 0
    ws_last_event_type: str | None = None
    ws_last_ts: float | None = None
    indicator_updates: int = 0
    indicator_last_ts: float | None = None
    last_indicator_snapshot: dict[str, IndicatorValue] = field(default_factory=dict)
    decision_last_ts: float | None = None
    decision_last_action: str | None = None
    decision_last_confidence: float | None = None
    decision_last_edge: float | None = None
    decision_last_reason: str | None = None
    last_order_action: str | None = None
    last_order_side: str | None = None
    last_order_price: float | None = None
    last_order_shares: float | None = None
    last_order_status: str | None = None
    last_order_ts: float | None = None
    flow_weight_preset_used: str = "flow_v1"
    flow_blocked_entries_count: int = 0
    run_started_monotonic: float = field(default_factory=time.monotonic)
    last_warmup_block_log_ts: float = 0.0
    _ws_data_event: asyncio.Event = field(default_factory=asyncio.Event)
    _indicator_event: asyncio.Event = field(default_factory=asyncio.Event)
    _signal_event: asyncio.Event = field(default_factory=asyncio.Event)

    def activity_snapshot(self) -> dict[str, Any]:
        snapshot = empty_runtime_activity_snapshot()
        snapshot.update(
            {
                "ws_ticks": self.ws_ticks,
                "ws_last_event_type": self.ws_last_event_type,
                "ws_last_ts": self.ws_last_ts,
                "indicator_updates": self.indicator_updates,
                "indicator_last_ts": self.indicator_last_ts,
                "decision_count": self.decisions_count,
                "decision_last_ts": self.decision_last_ts,
                "decision_last_action": self.decision_last_action,
                "decision_last_confidence": self.decision_last_confidence,
                "decision_last_edge": self.decision_last_edge,
                "decision_last_reason": self.decision_last_reason,
                "intent_queue_depth": self.intents.qsize(),
                "order_count": self.orders_count,
                "fill_count": self.fills_count,
                "open_orders": len(self.open_orders),
                "last_order_action": self.last_order_action,
                "last_order_side": self.last_order_side,
                "last_order_price": self.last_order_price,
                "last_order_shares": self.last_order_shares,
                "last_order_status": self.last_order_status,
                "last_order_ts": self.last_order_ts,
                "flow_blocked_entries_count": self.flow_blocked_entries_count,
                "filled_up_exposure_usdc": self.exposure_filled_usdc["UP"],
                "filled_down_exposure_usdc": self.exposure_filled_usdc["DOWN"],
                "open_up_exposure_usdc": self.exposure_open_usdc["UP"],
                "open_down_exposure_usdc": self.exposure_open_usdc["DOWN"],
                "side_budget_limit_usdc": self.cfg.max_wager_per_side_usdc,
                "open_position_sides_count": int(self.exposure_filled_usdc["UP"] > 0)
                + int(self.exposure_filled_usdc["DOWN"] > 0),
                "position_rollup": self._position_rollup_snapshot(),
                "indicators": _compact_indicator_snapshot(
                    self.last_indicator_snapshot if self.last_indicator_snapshot else self.indicators
                ),
            }
        )
        return snapshot

    async def run(self) -> EventRunResult | None:
        self.run_started_monotonic = time.monotonic()
        flow_weight_preset = "flow_v1" if self.cfg.flow_weight_preset == "v1" else self.cfg.flow_weight_preset
        self.flow_weight_preset_used = flow_weight_preset
        self.decision_policy = DecisionPolicy(
            min_confidence=self.cfg.min_signal_confidence,
            min_edge=self.cfg.min_signal_edge,
            enable_flow_signals=self.cfg.enable_flow_signals,
            flow_weight_preset=flow_weight_preset,
            flow_unknown_ratio_cutoff=self.cfg.flow_unknown_ratio_cutoff,
            flow_unknown_delta_scale=self.cfg.flow_unknown_delta_scale,
        )
        self.indicator_engine = IndicatorEngine(
            vwap_up_delta_15s=self.cfg.vwap_up_delta_15s,
            mid_flat_delta_15s=self.cfg.mid_flat_delta_15s,
            momentum_accel_5s=self.cfg.momentum_accel_5s,
            enable_reversal_imminent=self.cfg.enable_reversal_imminent,
            flow_weight_preset=flow_weight_preset,
        )
        self.market_state.configure_flow(
            ew_half_life_seconds=self.cfg.flow_ew_half_life_seconds,
            vpin_bucket_volume=self.cfg.flow_vpin_bucket_volume,
            vpin_num_buckets=self.cfg.flow_vpin_num_buckets,
            large_trade_size=self.cfg.flow_large_trade_size,
            large_ratio_window_seconds=self.cfg.flow_large_ratio_window_seconds,
            min_ew_volume=self.cfg.flow_min_ew_volume,
        )
        ctx = await fetch_event_market_context(self.cfg.poly_event_input)
        now_ts = int(time.time())
        start_offset = max(0, now_ts - ctx.start_ts)
        runtime_sec = compute_target_runtime_seconds(ctx.duration_sec, start_offset)

        if runtime_sec <= 0:
            self.console.warn("Event already closed. Nothing to run.")
            return None

        if runtime_sec < self.cfg.min_remaining_seconds_to_run:
            self.console.warn(
                f"Remaining runtime {runtime_sec}s is below MIN_REMAINING_SECONDS_TO_RUN={self.cfg.min_remaining_seconds_to_run}s"
            )
            return None

        run_id = str(uuid4())
        self.market_state.market_id = ctx.condition_id
        started_at = datetime.now(tz=timezone.utc).isoformat()

        await self.writer.create_run(
            {
                "id": run_id,
                "run_tag": run_id,
                "event_slug": ctx.event_slug,
                "event_id": ctx.event_id,
                "market_slug": ctx.event_slug,
                "symbol": ctx.title,
                "timeframe_minutes": ctx.timeframe_minutes,
                "mode": self.cfg.bot_mode,
                "started_at": started_at,
                "status": "running",
            }
        )

        self.console.header(
            f"RUN {run_id[:8]} event={ctx.event_slug} timeframe={ctx.timeframe_minutes}m remaining={runtime_sec}s mode={self.cfg.bot_mode}"
        )

        live_trading = self.cfg.bot_mode == "live"
        trading_client = TradingClient(enabled=live_trading)

        with _EventRunLock(ctx.event_slug, self.cfg.disallow_duplicate_event_run):
            tasks = [
                asyncio.create_task(self._ws_ingest_loop(ctx, run_id)),
                asyncio.create_task(self._indicator_loop()),
                asyncio.create_task(self._signal_loop(ctx, run_id)),
                asyncio.create_task(self._execution_loop(ctx, run_id, trading_client)),
                asyncio.create_task(self._tracking_flush_loop()),
                asyncio.create_task(self._event_guard_loop(ctx)),
            ]

            try:
                await self.stop_event.wait()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

                winning_side: str | None = None
                predicted_side = dominant_side(self.exposure_filled_usdc)
                try:
                    winning_side = await fetch_winning_side(ctx.condition_id)
                except Exception as err:
                    await self.writer.enqueue_runtime_event(
                        run_id,
                        "warn",
                        "winning_side_lookup_failed",
                        {"error": str(err)},
                    )

                ended_at = datetime.now(tz=timezone.utc).isoformat()
                accurate = winning_side == predicted_side if winning_side and predicted_side else None
                await self.writer.complete_run(
                    run_id,
                    {
                        "status": "completed",
                        "ended_at": ended_at,
                        "final_winning_side": winning_side,
                        "was_prediction_accurate": accurate,
                        "total_decisions": self.decisions_count,
                        "total_orders": self.orders_count,
                        "total_fills": self.fills_count,
                        "gross_notional": self.exposure_filled_usdc["UP"] + self.exposure_filled_usdc["DOWN"],
                    },
                )
                await self.writer.flush()

                self.console.summary(
                    "Run complete "
                    f"decisions={self.decisions_count} orders={self.orders_count} fills={self.fills_count} "
                    f"up={self.exposure_filled_usdc['UP']:.2f} down={self.exposure_filled_usdc['DOWN']:.2f}"
                )
                return EventRunResult(
                    run_id=run_id,
                    event_slug=ctx.event_slug,
                    condition_id=ctx.condition_id,
                    timeframe_minutes=ctx.timeframe_minutes,
                    started_at=started_at,
                    ended_at=ended_at,
                    winning_side=winning_side,
                    predicted_side=predicted_side,
                    was_prediction_accurate=accurate,
                    total_decisions=self.decisions_count,
                    total_orders=self.orders_count,
                    total_fills=self.fills_count,
                    flow_blocked_entries_count=self.flow_blocked_entries_count,
                    up_notional=self.exposure_filled_usdc["UP"],
                    down_notional=self.exposure_filled_usdc["DOWN"],
                    orders=list(self.order_records),
                )

    async def _ws_ingest_loop(self, ctx: EventMarketContext, run_id: str) -> None:
        while not self.stop_event.is_set():
            try:
                async for msg in stream_market_events([ctx.token_up, ctx.token_down]):
                    if self.stop_event.is_set():
                        return

                    asset_id = str(msg.get("asset_id", ""))
                    event_type = str(msg.get("event_type", ""))
                    if not asset_id or not event_type:
                        continue

                    self.ws_ticks += _ingest_units_for_event(event_type, msg)
                    self.ws_last_event_type = event_type
                    self.ws_last_ts = time.time()
                    self._ws_data_event.set()

                    try:
                        async with self.state_lock:
                            book = self.market_state.book_yes if asset_id == ctx.token_up else self.market_state.book_no

                            if event_type == "book":
                                bids = msg.get("bids", [])
                                asks = msg.get("asks", [])
                                if isinstance(bids, list) and isinstance(asks, list):
                                    book.apply_snapshot(bids=bids, asks=asks)
                            elif event_type == "price_change":
                                changes = msg.get("changes", [])
                                if isinstance(changes, list):
                                    book.apply_change(changes=changes)
                            elif event_type == "last_trade_price":
                                if asset_id == ctx.token_up:
                                    price = _to_float(msg.get("price"))
                                    size = _to_float(msg.get("size"))
                                    if price is not None and size is not None and price > 0 and size > 0:
                                        bid = self.market_state.book_yes.best_bid
                                        ask = self.market_state.book_yes.best_ask
                                        side = classify_trade_side(
                                            price=price,
                                            best_bid=bid if bid > 0 else None,
                                            best_ask=ask if ask > 0 else None,
                                            tolerance=self.cfg.trade_side_tolerance,
                                        )
                                        self.market_state.add_trade(
                                            price=price,
                                            size=size,
                                            side=side,
                                            ts=datetime.now(tz=timezone.utc),
                                        )
                    except Exception as err:
                        # Keep stream alive on malformed payloads; dropping one frame is
                        # safer than killing the market ingest task.
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "warn",
                            "ws_message_process_error",
                            {
                                "asset_id": asset_id,
                                "event_type": event_type,
                                "error": str(err)[:200],
                            },
                        )
                        continue

                    if self.cfg.ws_tick_log_sample_every > 0 and (self.ws_ticks % self.cfg.ws_tick_log_sample_every) == 0:
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "info",
                            "ws_tick",
                            {"event_type": event_type, "asset_id": asset_id},
                        )
            except Exception as err:
                if self.stop_event.is_set():
                    return
                await self.writer.enqueue_runtime_event(
                    run_id,
                    "warn",
                    "ws_stream_recovered",
                    {"error": str(err)[:200]},
                )
                await asyncio.sleep(0.25)

    async def _indicator_loop(self) -> None:
        while not self.stop_event.is_set():
            triggered = await self._wait_for_trigger(
                trigger_event=self._ws_data_event,
                fallback_interval_ms=self.cfg.indicator_interval_ms,
            )
            if self.cfg.enable_event_driven_loops and not triggered:
                continue
            now = datetime.now(tz=timezone.utc)
            async with self.state_lock:
                self.indicators = self.indicator_engine.compute(
                    self.market_state,
                    now=now,
                )
            self.indicator_updates += 1
            self.indicator_last_ts = time.time()
            self.last_indicator_snapshot = _compact_indicator_snapshot(self.indicators)
            if triggered:
                self._indicator_event.set()

    async def _signal_loop(self, ctx: EventMarketContext, run_id: str) -> None:
        while not self.stop_event.is_set():
            await self._wait_until_resumed()
            triggered = await self._wait_for_trigger(
                trigger_event=self._indicator_event,
                fallback_interval_ms=self.cfg.signal_interval_ms,
            )
            if self.cfg.enable_event_driven_loops and not triggered:
                continue

            remaining_sec = max(0, ctx.end_ts - int(time.time()))
            if remaining_sec == 0:
                self.stop_event.set()
                return

            async with self.state_lock:
                indicators = dict(self.indicators)
                up_bid = self.market_state.book_yes.best_bid
                up_ask = self.market_state.book_yes.best_ask
                down_bid = self.market_state.book_no.best_bid
                down_ask = self.market_state.book_no.best_ask

            if not indicators:
                continue

            decision = self.decision_policy.decide(
                indicators,
                remaining_sec,
                candidate_up_price=up_ask if up_ask > 0 else None,
                candidate_down_price=down_ask if down_ask > 0 else None,
            )
            streak = self._advance_signal_streak(decision.action)
            self.decisions_count += 1
            self.decision_last_ts = time.time()
            self.decision_last_action = decision.action.value
            self.decision_last_confidence = decision.confidence
            self.decision_last_edge = decision.edge
            self.decision_last_reason = decision.reason_code
            self.console.decision(
                decision.action.value,
                decision.confidence,
                decision.edge,
                decision.reason_code,
                remaining_sec,
            )

            await self.writer.enqueue(
                "bot_decisions",
                {
                    "run_id": run_id,
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "remaining_seconds": remaining_sec,
                    "action": _map_action_for_db(decision.action),
                    "confidence": decision.confidence,
                    "reason_code": decision.reason_code,
                    "reason_details": {
                        "policy": "default",
                        "edge": decision.edge,
                        "streak": streak,
                        "effective_min_confidence": decision.effective_min_confidence,
                        "threshold_relaxed": decision.threshold_relaxed,
                        "reversal_imminent": indicators.get("reversal_imminent") is True,
                        "flow_weight_preset": self.flow_weight_preset_used,
                        "flow_boost": decision.flow_boost,
                    },
                    "indicator_snapshot": indicators,
                    "risk_snapshot": {
                        "filled_up": self.exposure_filled_usdc["UP"],
                        "filled_down": self.exposure_filled_usdc["DOWN"],
                    },
                },
            )

            up_exposure = self.exposure_filled_usdc["UP"]
            down_exposure = self.exposure_filled_usdc["DOWN"]
            if self.cfg.count_open_orders_in_exposure:
                up_exposure += self.exposure_open_usdc["UP"]
                down_exposure += self.exposure_open_usdc["DOWN"]

            now_ms = int(time.time() * 1000)
            reversal_imminent = indicators.get("reversal_imminent") is True
            ew_delta_imbalance = float(indicators.get("ew_delta_imbalance") or 0.0)
            common_entry_signal_snapshot = _build_entry_signal_snapshot(
                indicators=indicators,
                confidence=decision.confidence,
                edge=decision.edge,
                reason_code=decision.reason_code,
                effective_min_confidence=decision.effective_min_confidence,
                threshold_relaxed=decision.threshold_relaxed,
                flow_boost=decision.flow_boost,
                streak=streak,
                remaining_sec=remaining_sec,
            )
            warmup_ok, warmup_state = self._entry_warmup_ready()
            if decision.action == DecisionAction.BUY_UP and up_ask > 0:
                if not warmup_ok:
                    await self._log_warmup_block(run_id=run_id, side="UP", warmup_state=warmup_state)
                    continue
                if _flow_blocks_entry(
                    action=decision.action,
                    ew_delta_imbalance=ew_delta_imbalance,
                    threshold=self.cfg.flow_block_delta_threshold,
                ):
                    self.flow_blocked_entries_count += 1
                    await self.writer.enqueue_runtime_event(
                        run_id,
                        "warn",
                        "entry_blocked",
                        {
                            "reason_code": "entry_blocked_flow_against_direction",
                            "side": "UP",
                            "ew_delta_imbalance": ew_delta_imbalance,
                        },
                    )
                    continue
                gate = evaluate_entry_policy(
                    EntryPolicyInput(
                        side="UP",
                        confidence=decision.confidence,
                        price=up_ask,
                        up_exposure_usdc=up_exposure,
                        down_exposure_usdc=down_exposure,
                        anchor_side=self.anchor_side,
                        allow_both_sides=self.cfg.allow_both_sides,
                        hedge_max_exposure_ratio=self.cfg.hedge_max_exposure_ratio,
                        hedge_min_confidence=self.cfg.hedge_min_confidence,
                        hedge_max_entry_price=self.cfg.hedge_max_entry_price,
                        signal_streak=streak,
                        min_signal_streak=self.cfg.signal_persist_ticks,
                        last_entry_emit_ms=self.last_entry_emit_ms["UP"],
                        now_ms=now_ms,
                        entry_cooldown_ms=self.cfg.entry_cooldown_ms,
                    )
                )
                if gate.allowed:
                    if self.anchor_side is None:
                        self.anchor_side = "UP"
                    self.last_entry_emit_ms["UP"] = now_ms
                    await self.intents.put(
                        {
                            "type": "ENTRY",
                            "side": "UP",
                            "price": up_ask,
                            "confidence": decision.confidence,
                            "reason_code": decision.reason_code,
                            "reversal_imminent": reversal_imminent,
                            "entry_ew_delta_imbalance": float(indicators.get("ew_delta_imbalance") or 0.0),
                            "entry_flow_toxicity": float(indicators.get("flow_toxicity") or 0.0),
                            "entry_unknown_trade_ratio": float(indicators.get("unknown_trade_ratio") or 0.0),
                            "entry_large_trade_ratio": float(indicators.get("large_trade_ratio") or 0.0),
                            "entry_flow_weight_preset": str(
                                indicators.get("flow_weight_preset") or self.flow_weight_preset_used
                            ),
                            "entry_signal_snapshot": {
                                **common_entry_signal_snapshot,
                                "action": "BUY_UP",
                                "entry_side": "UP",
                                "entry_price": round(up_ask, 6),
                            },
                        }
                    )
                    if decision.flow_boost > 0:
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "info",
                            "flow_confirmed_entry",
                            {
                                "reason_code": "flow_confirmed_entry",
                                "side": "UP",
                                "flow_boost": decision.flow_boost,
                            },
                        )
                elif reversal_imminent:
                    # Reversal observability: emit explicit block reasons without
                    # changing baseline entry filters.
                    await self.writer.enqueue_runtime_event(
                        run_id,
                        "warn",
                        "reversal_entry_blocked",
                        {
                            "reason_code": _map_reversal_block_reason(gate.reason_code),
                            "source_reason": gate.reason_code,
                            "side": "UP",
                        },
                    )
            elif decision.action == DecisionAction.BUY_DOWN and down_ask > 0:
                if not warmup_ok:
                    await self._log_warmup_block(run_id=run_id, side="DOWN", warmup_state=warmup_state)
                    continue
                if _flow_blocks_entry(
                    action=decision.action,
                    ew_delta_imbalance=ew_delta_imbalance,
                    threshold=self.cfg.flow_block_delta_threshold,
                ):
                    self.flow_blocked_entries_count += 1
                    await self.writer.enqueue_runtime_event(
                        run_id,
                        "warn",
                        "entry_blocked",
                        {
                            "reason_code": "entry_blocked_flow_against_direction",
                            "side": "DOWN",
                            "ew_delta_imbalance": ew_delta_imbalance,
                        },
                    )
                    continue
                gate = evaluate_entry_policy(
                    EntryPolicyInput(
                        side="DOWN",
                        confidence=decision.confidence,
                        price=down_ask,
                        up_exposure_usdc=up_exposure,
                        down_exposure_usdc=down_exposure,
                        anchor_side=self.anchor_side,
                        allow_both_sides=self.cfg.allow_both_sides,
                        hedge_max_exposure_ratio=self.cfg.hedge_max_exposure_ratio,
                        hedge_min_confidence=self.cfg.hedge_min_confidence,
                        hedge_max_entry_price=self.cfg.hedge_max_entry_price,
                        signal_streak=streak,
                        min_signal_streak=self.cfg.signal_persist_ticks,
                        last_entry_emit_ms=self.last_entry_emit_ms["DOWN"],
                        now_ms=now_ms,
                        entry_cooldown_ms=self.cfg.entry_cooldown_ms,
                    )
                )
                if gate.allowed:
                    if self.anchor_side is None:
                        self.anchor_side = "DOWN"
                    self.last_entry_emit_ms["DOWN"] = now_ms
                    await self.intents.put(
                        {
                            "type": "ENTRY",
                            "side": "DOWN",
                            "price": down_ask,
                            "confidence": decision.confidence,
                            "reason_code": decision.reason_code,
                            "reversal_imminent": reversal_imminent,
                            "entry_ew_delta_imbalance": float(indicators.get("ew_delta_imbalance") or 0.0),
                            "entry_flow_toxicity": float(indicators.get("flow_toxicity") or 0.0),
                            "entry_unknown_trade_ratio": float(indicators.get("unknown_trade_ratio") or 0.0),
                            "entry_large_trade_ratio": float(indicators.get("large_trade_ratio") or 0.0),
                            "entry_flow_weight_preset": str(
                                indicators.get("flow_weight_preset") or self.flow_weight_preset_used
                            ),
                            "entry_signal_snapshot": {
                                **common_entry_signal_snapshot,
                                "action": "BUY_DOWN",
                                "entry_side": "DOWN",
                                "entry_price": round(down_ask, 6),
                            },
                        }
                    )
                    if decision.flow_boost > 0:
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "info",
                            "flow_confirmed_entry",
                            {
                                "reason_code": "flow_confirmed_entry",
                                "side": "DOWN",
                                "flow_boost": decision.flow_boost,
                            },
                        )

            if self.cfg.enable_take_profit and remaining_sec >= self.cfg.take_profit_min_remaining_sec:
                if self.exposure_filled_usdc["UP"] > 0 and up_bid >= self.cfg.take_profit_trigger_price:
                    await self.intents.put(
                        {
                            "type": "TAKE_PROFIT",
                            "side": "UP",
                            "price": max(up_bid, self.cfg.take_profit_limit_price),
                            "confidence": 1.0,
                            "reason_code": "take_profit_95c_discipline",
                        }
                    )
                if self.exposure_filled_usdc["DOWN"] > 0 and down_bid >= self.cfg.take_profit_trigger_price:
                    await self.intents.put(
                        {
                            "type": "TAKE_PROFIT",
                            "side": "DOWN",
                            "price": max(down_bid, self.cfg.take_profit_limit_price),
                            "confidence": 1.0,
                            "reason_code": "take_profit_95c_discipline",
                        }
                    )

            if triggered:
                self._signal_event.set()

    def _entry_warmup_ready(self) -> tuple[bool, dict[str, float | int]]:
        elapsed_sec = max(0.0, time.monotonic() - self.run_started_monotonic)
        state: dict[str, float | int] = {
            "elapsed_sec": round(elapsed_sec, 3),
            "ws_ticks": self.ws_ticks,
            "indicator_updates": self.indicator_updates,
            "required_elapsed_sec": self.cfg.entry_warmup_min_seconds,
            "required_ws_ticks": self.cfg.entry_warmup_min_ws_ticks,
            "required_indicator_updates": self.cfg.entry_warmup_min_indicator_updates,
        }
        ready = (
            elapsed_sec >= self.cfg.entry_warmup_min_seconds
            and self.ws_ticks >= self.cfg.entry_warmup_min_ws_ticks
            and self.indicator_updates >= self.cfg.entry_warmup_min_indicator_updates
        )
        return ready, state

    async def _log_warmup_block(self, run_id: str, side: str, warmup_state: dict[str, float | int]) -> None:
        now_ts = time.time()
        if now_ts - self.last_warmup_block_log_ts < 0.5:
            return
        self.last_warmup_block_log_ts = now_ts
        await self.writer.enqueue_runtime_event(
            run_id,
            "info",
            "entry_blocked",
            {
                "reason_code": "entry_blocked_warmup_not_ready",
                "side": side,
                "warmup": warmup_state,
            },
        )

    async def _execution_loop(self, ctx: EventMarketContext, run_id: str, trading_client: TradingClient) -> None:
        while not self.stop_event.is_set():
            await self._wait_until_resumed()
            await self._wait_for_trigger(
                trigger_event=self._signal_event,
                fallback_interval_ms=self.cfg.execution_interval_ms,
            )

            while not self.intents.empty():
                intent = await self.intents.get()
                side = str(intent["side"])
                action_type = str(intent["type"])
                price = float(intent["price"])
                confidence = float(intent["confidence"])
                reversal_imminent = bool(intent.get("reversal_imminent", False))
                reason_code = str(intent.get("reason_code", ""))
                entry_ew_delta_imbalance = _to_float(intent.get("entry_ew_delta_imbalance"))
                entry_flow_toxicity = _to_float(intent.get("entry_flow_toxicity"))
                entry_large_trade_ratio = _to_float(intent.get("entry_large_trade_ratio"))
                entry_unknown_trade_ratio = _to_float(intent.get("entry_unknown_trade_ratio"))
                entry_flow_weight_preset = str(intent.get("entry_flow_weight_preset", "")).strip() or None
                raw_entry_signal_snapshot = intent.get("entry_signal_snapshot")
                entry_signal_snapshot = (
                    dict(raw_entry_signal_snapshot)
                    if isinstance(raw_entry_signal_snapshot, dict)
                    else None
                )

                current_exposure = self.exposure_filled_usdc[side]
                if self.cfg.count_open_orders_in_exposure:
                    current_exposure += self.exposure_open_usdc[side]

                if action_type == "ENTRY":
                    size = propose_order_size(
                        target_confidence=confidence,
                        price=price,
                        current_side_exposure_usdc=current_exposure,
                        max_wager_per_side_usdc=self.cfg.max_wager_per_side_usdc,
                        max_single_wager_usdc=self.cfg.max_single_wager_usdc,
                        min_wager_usdc=self.cfg.min_wager_usdc,
                        min_shares_per_purchase=self.cfg.min_shares_per_purchase,
                        reversal_imminent=reversal_imminent,
                        enable_convexity_budget_reservation=self.cfg.enable_convexity_budget_reservation,
                    )
                    if size.throttle_applied:
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "info",
                            "convexity_budget_throttle",
                            {
                                "reason_code": "expensive_entry_throttled_to_preserve_convexity_budget",
                                "side": side,
                                "price": price,
                                "throttle_cap_usdc": size.throttle_cap_usdc,
                            },
                        )
                    if not size.allowed:
                        reason_code = _map_reversal_block_reason(size.reason_code) if reversal_imminent else size.reason_code
                        if reversal_imminent:
                            await self.writer.enqueue_runtime_event(
                                run_id,
                                "warn",
                                "reversal_entry_blocked",
                                {"reason_code": reason_code, "source_reason": size.reason_code, "side": side},
                            )
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "warn",
                            "risk_reject",
                            {"side": side, "reason": reason_code},
                        )
                        continue

                    candidate = OrderCandidate(
                        side=side,
                        action="ENTRY",
                        price=price,
                        shares=size.shares,
                        wager_usdc=size.wager_usdc,
                    )
                    risk = RiskSnapshot(
                        max_entry_price=self.cfg.max_entry_price,
                        max_wager_per_side_usdc=self.cfg.max_wager_per_side_usdc,
                        current_side_exposure_usdc=current_exposure,
                        max_single_wager_usdc=self.cfg.max_single_wager_usdc,
                        min_wager_usdc=self.cfg.min_wager_usdc,
                        min_shares_per_purchase=self.cfg.min_shares_per_purchase,
                    )
                    risk_decision = validate_order(candidate, risk)
                    if not risk_decision.allowed:
                        base_reason = (
                            "entry_blocked_price_too_high"
                            if risk_decision.reason_code == "price_cap"
                            else risk_decision.reason_code
                        )
                        if risk_decision.reason_code == "price_cap":
                            await self.writer.enqueue_runtime_event(
                                run_id,
                                "warn",
                                "entry_blocked",
                                {
                                    "reason_code": "entry_blocked_price_too_high",
                                    "side": side,
                                    "price": price,
                                },
                            )
                        reason_code = (
                            _map_reversal_block_reason(risk_decision.reason_code)
                            if reversal_imminent
                            else base_reason
                        )
                        if reversal_imminent:
                            await self.writer.enqueue_runtime_event(
                                run_id,
                                "warn",
                                "reversal_entry_blocked",
                                {
                                    "reason_code": reason_code,
                                    "source_reason": risk_decision.reason_code,
                                    "side": side,
                                },
                            )
                        await self.writer.enqueue_runtime_event(
                            run_id,
                            "warn",
                            "risk_reject",
                            {"side": side, "reason": reason_code},
                        )
                        continue

                    token_id = ctx.token_up if side == "UP" else ctx.token_down
                    client_order_id = f"{run_id[:8]}-{int(time.time() * 1000)}-{side.lower()}"
                    payload = build_entry_order(side=side, price=price, shares=size.shares, client_order_id=client_order_id)

                    await self._submit_order(
                        run_id=run_id,
                        token_id=token_id,
                        side=side,
                        action="ENTRY",
                        payload=payload,
                        wager_usdc=size.wager_usdc,
                        reason_code=reason_code,
                        trading_client=trading_client,
                        entry_ew_delta_imbalance=entry_ew_delta_imbalance,
                        entry_flow_toxicity=entry_flow_toxicity,
                        entry_large_trade_ratio=entry_large_trade_ratio,
                        entry_unknown_trade_ratio=entry_unknown_trade_ratio,
                        entry_flow_weight_preset=entry_flow_weight_preset,
                        entry_signal_snapshot=entry_signal_snapshot,
                    )
                else:
                    if self.exposure_filled_usdc[side] <= 0:
                        continue
                    shares = max(self.cfg.min_shares_per_purchase, self.exposure_filled_usdc[side] / max(price, 0.01))
                    shares = round(shares, 3)
                    token_id = ctx.token_up if side == "UP" else ctx.token_down
                    client_order_id = f"{run_id[:8]}-{int(time.time() * 1000)}-tp-{side.lower()}"
                    payload = build_entry_order(side=side, price=price, shares=shares, client_order_id=client_order_id)

                    await self._submit_order(
                        run_id=run_id,
                        token_id=token_id,
                        side=side,
                        action="TAKE_PROFIT",
                        payload=payload,
                        wager_usdc=price * shares,
                        reason_code=reason_code or "take_profit_95c_discipline",
                        trading_client=trading_client,
                        is_sell=True,
                        entry_ew_delta_imbalance=entry_ew_delta_imbalance,
                        entry_flow_toxicity=entry_flow_toxicity,
                        entry_large_trade_ratio=entry_large_trade_ratio,
                        entry_unknown_trade_ratio=entry_unknown_trade_ratio,
                        entry_flow_weight_preset=entry_flow_weight_preset,
                        entry_signal_snapshot=entry_signal_snapshot,
                    )

            # Cancel stale open orders when reversal is strong.
            stale_ids = []
            spread_mom = self.indicators.get("spread_momentum_30s") if self.indicators else None
            mid_mom = self.indicators.get("mid_momentum_30s") if self.indicators else None
            for order_id, order in self.open_orders.items():
                age = time.time() - order.created_at
                if should_cancel(age, _to_float(spread_mom), _to_float(mid_mom)):
                    stale_ids.append(order_id)

            for order_id in stale_ids:
                stale_order = self.open_orders.get(order_id)
                if stale_order is None:
                    continue
                if self.cfg.bot_mode == "live":
                    try:
                        await trading_client.cancel_order(order_id)
                    except Exception as err:
                        self.console.warn(f"cancel failed order_id={order_id}: {err}")
                        continue
                self.exposure_open_usdc[stale_order.side] = max(
                    0.0,
                    self.exposure_open_usdc[stale_order.side] - stale_order.wager_usdc,
                )
                self.open_orders.pop(order_id, None)
                self._record_order_activity(
                    action="CANCEL",
                    side=stale_order.side,
                    price=stale_order.price,
                    shares=stale_order.shares,
                    status="cancelled",
                )
                await self.writer.enqueue(
                    "bot_orders",
                    {
                        "run_id": run_id,
                        "ts": datetime.now(tz=timezone.utc).isoformat(),
                        "client_order_id": order_id,
                        "exchange_order_id": order_id,
                        "action": "CANCEL",
                        "side": stale_order.side,
                        "limit_price": stale_order.price,
                        "shares": stale_order.shares,
                        "wager_usdc": stale_order.wager_usdc,
                        "status": "cancelled",
                        "metadata": {"reason": "momentum_reversal"},
                    },
                )

            if self.cfg.bot_mode == "live" and self.open_orders:
                await self._reconcile_open_orders(run_id, trading_client)

    async def _submit_order(
        self,
        run_id: str,
        token_id: str,
        side: str,
        action: str,
        payload: dict[str, Any],
        wager_usdc: float,
        reason_code: str,
        trading_client: TradingClient,
        is_sell: bool = False,
        entry_ew_delta_imbalance: float | None = None,
        entry_flow_toxicity: float | None = None,
        entry_large_trade_ratio: float | None = None,
        entry_unknown_trade_ratio: float | None = None,
        entry_flow_weight_preset: str | None = None,
        entry_signal_snapshot: dict[str, IndicatorValue] | None = None,
    ) -> None:
        self.orders_count += 1
        action_label = "SELL_UP" if (is_sell and side == "UP") else (
            "SELL_DOWN" if (is_sell and side == "DOWN") else ("BUY_UP" if side == "UP" else "BUY_DOWN")
        )

        if self.cfg.bot_mode == "dry_run":
            order_id = str(payload["client_order_id"])
            self.console.order(
                f"DRY {action} side={side} price={payload['price']} shares={payload['shares']} wager={wager_usdc:.2f}"
            )
            if not is_sell:
                self.exposure_filled_usdc[side] += wager_usdc
                self.filled_shares[side] += float(payload["shares"])
                self.fills_count += 1
            else:
                self.exposure_filled_usdc[side] = max(0.0, self.exposure_filled_usdc[side] - wager_usdc)
                self.filled_shares[side] = max(0.0, self.filled_shares[side] - float(payload["shares"]))
                self.fills_count += 1

            await self.writer.enqueue(
                "bot_orders",
                {
                    "run_id": run_id,
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "client_order_id": f"{order_id}-fill",
                    "exchange_order_id": order_id,
                    "action": action_label,
                    "side": side,
                    "limit_price": payload["price"],
                    "shares": payload["shares"],
                    "wager_usdc": wager_usdc,
                    "status": "filled",
                    "metadata": {
                        "mode": "dry_run",
                        "action": action,
                        "reason_code": reason_code,
                        "entry_ew_delta_imbalance": entry_ew_delta_imbalance,
                        "entry_flow_toxicity": entry_flow_toxicity,
                        "entry_large_trade_ratio": entry_large_trade_ratio,
                        "entry_unknown_trade_ratio": entry_unknown_trade_ratio,
                        "entry_flow_weight_preset": entry_flow_weight_preset,
                        "entry_signal_snapshot": entry_signal_snapshot,
                    },
                },
            )
            self.order_records.append(
                EventOrderRecord(
                    action=action_label,
                    side=side,
                    price=float(payload["price"]),
                    shares=float(payload["shares"]),
                    wager_usdc=wager_usdc,
                    status="filled",
                    ts=datetime.now(tz=timezone.utc).isoformat(),
                    reason_code=reason_code,
                    entry_ew_delta_imbalance=entry_ew_delta_imbalance,
                    entry_flow_toxicity=entry_flow_toxicity,
                    entry_large_trade_ratio=entry_large_trade_ratio,
                    entry_unknown_trade_ratio=entry_unknown_trade_ratio,
                    entry_flow_weight_preset=entry_flow_weight_preset,
                    entry_signal_snapshot=entry_signal_snapshot,
                )
            )
            self._record_order_activity(
                action=action_label,
                side=side,
                price=float(payload["price"]),
                shares=float(payload["shares"]),
                status="filled",
            )
            return

        try:
            exchange_resp = await trading_client.place_limit_order(
                token_id=token_id,
                price=float(payload["price"]),
                shares=float(payload["shares"]),
                side="SELL" if is_sell else "BUY",
            )
        except Exception as err:
            self.console.error(f"LIVE order failed {action} side={side}: {err}")
            await self.writer.enqueue_runtime_event(
                run_id,
                "error",
                "order_submit_error",
                {"action": action, "side": side, "error": str(err)},
            )
            return

        order_id = (
            str(exchange_resp.get("orderID") or exchange_resp.get("id") or payload["client_order_id"])
        )
        self.console.order(
            f"LIVE {action} side={side} price={payload['price']} shares={payload['shares']} order_id={order_id}"
        )

        if not is_sell:
            self.exposure_open_usdc[side] += wager_usdc
            self.open_orders[order_id] = OpenOrder(
                order_id=order_id,
                side=side,
                action=action,
                price=float(payload["price"]),
                shares=float(payload["shares"]),
                wager_usdc=wager_usdc,
                reason_code=reason_code,
                entry_signal_snapshot=entry_signal_snapshot,
                created_at=time.time(),
            )

        await self.writer.enqueue(
            "bot_orders",
            {
                "run_id": run_id,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "client_order_id": str(payload["client_order_id"]),
                "exchange_order_id": order_id,
                "action": action_label,
                "side": side,
                "limit_price": payload["price"],
                "shares": payload["shares"],
                "wager_usdc": wager_usdc,
                "status": "submitted",
                "metadata": {
                    "exchange_resp": exchange_resp,
                    "reason_code": reason_code,
                    "entry_ew_delta_imbalance": entry_ew_delta_imbalance,
                    "entry_flow_toxicity": entry_flow_toxicity,
                    "entry_large_trade_ratio": entry_large_trade_ratio,
                    "entry_unknown_trade_ratio": entry_unknown_trade_ratio,
                    "entry_flow_weight_preset": entry_flow_weight_preset,
                    "entry_signal_snapshot": entry_signal_snapshot,
                },
            },
        )
        self.order_records.append(
            EventOrderRecord(
                action=action_label,
                side=side,
                price=float(payload["price"]),
                shares=float(payload["shares"]),
                wager_usdc=wager_usdc,
                status="submitted",
                ts=datetime.now(tz=timezone.utc).isoformat(),
                reason_code=reason_code,
                entry_ew_delta_imbalance=entry_ew_delta_imbalance,
                entry_flow_toxicity=entry_flow_toxicity,
                entry_large_trade_ratio=entry_large_trade_ratio,
                entry_unknown_trade_ratio=entry_unknown_trade_ratio,
                entry_flow_weight_preset=entry_flow_weight_preset,
                entry_signal_snapshot=entry_signal_snapshot,
            )
        )
        self._record_order_activity(
            action=action_label,
            side=side,
            price=float(payload["price"]),
            shares=float(payload["shares"]),
            status="submitted",
        )

    async def _reconcile_open_orders(self, run_id: str, trading_client: TradingClient) -> None:
        resolved_ids: list[str] = []
        for order_id, open_order in self.open_orders.items():
            try:
                order_resp = await trading_client.get_order(order_id)
            except Exception:
                continue

            status = _extract_order_status(order_resp)
            if status not in {"filled", "matched", "executed"}:
                continue

            resolved_ids.append(order_id)
            self.exposure_open_usdc[open_order.side] = max(
                0.0,
                self.exposure_open_usdc[open_order.side] - open_order.wager_usdc,
            )
            self.exposure_filled_usdc[open_order.side] += open_order.wager_usdc
            self.filled_shares[open_order.side] += open_order.shares
            self.fills_count += 1

            await self.writer.enqueue(
                "bot_orders",
                {
                    "run_id": run_id,
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "client_order_id": order_id,
                    "exchange_order_id": order_id,
                    "action": "BUY_UP" if open_order.side == "UP" else "BUY_DOWN",
                    "side": open_order.side,
                    "limit_price": open_order.price,
                    "shares": open_order.shares,
                    "wager_usdc": open_order.wager_usdc,
                    "status": "filled",
                    "metadata": {
                        "source_status": status,
                        "phase": "reconcile",
                        "reason_code": open_order.reason_code,
                        "entry_signal_snapshot": open_order.entry_signal_snapshot,
                    },
                },
            )
            self.order_records.append(
                EventOrderRecord(
                    action="BUY_UP" if open_order.side == "UP" else "BUY_DOWN",
                    side=open_order.side,
                    price=open_order.price,
                    shares=open_order.shares,
                    wager_usdc=open_order.wager_usdc,
                    status="filled",
                    ts=datetime.now(tz=timezone.utc).isoformat(),
                    reason_code=open_order.reason_code,
                    entry_signal_snapshot=open_order.entry_signal_snapshot,
                )
            )
            self._record_order_activity(
                action="BUY_UP" if open_order.side == "UP" else "BUY_DOWN",
                side=open_order.side,
                price=open_order.price,
                shares=open_order.shares,
                status="filled",
            )

        for order_id in resolved_ids:
            self.open_orders.pop(order_id, None)

    async def _tracking_flush_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.cfg.supabase_flush_interval_ms / 1000)
            await self.writer.flush()

    async def _event_guard_loop(self, ctx: EventMarketContext) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(0.25)
            if int(time.time()) >= ctx.end_ts:
                self.stop_event.set()
                return

    async def _wait_until_resumed(self) -> None:
        if self.resume_event is None:
            return
        while not self.stop_event.is_set() and not self.resume_event.is_set():
            await asyncio.sleep(0.1)

    async def _wait_for_trigger(self, *, trigger_event: asyncio.Event, fallback_interval_ms: int) -> bool:
        """Wait for a trigger event or timeout.

        Returns True if the event fired (real data), False on timeout.
        """
        if not self.cfg.enable_event_driven_loops:
            await asyncio.sleep(max(1, fallback_interval_ms) / 1000)
            return True

        timeout_ms = max(1, min(max(1, fallback_interval_ms), self.cfg.event_driven_max_wait_ms))
        try:
            await asyncio.wait_for(trigger_event.wait(), timeout=timeout_ms / 1000)
        except TimeoutError:
            return False
        trigger_event.clear()
        return True


    def _advance_signal_streak(self, action: DecisionAction) -> int:
        if action in {DecisionAction.BUY_UP, DecisionAction.BUY_DOWN}:
            if action == self.last_signal_action:
                self.signal_action_streak += 1
            else:
                self.last_signal_action = action
                self.signal_action_streak = 1
            return self.signal_action_streak

        self.last_signal_action = None
        self.signal_action_streak = 0
        return 0

    def _record_order_activity(
        self,
        *,
        action: str,
        side: str,
        price: float,
        shares: float,
        status: str,
    ) -> None:
        self.last_order_action = action
        self.last_order_side = side
        self.last_order_price = price
        self.last_order_shares = shares
        self.last_order_status = status
        self.last_order_ts = time.time()

    def _position_rollup_snapshot(self) -> dict[str, dict[str, float] | None]:
        rollup: dict[str, dict[str, float] | None] = {"UP": None, "DOWN": None}
        for side in ("UP", "DOWN"):
            shares = max(0.0, self.filled_shares[side])
            total_wager = max(0.0, self.exposure_filled_usdc[side])
            if shares <= 0 or total_wager <= 0:
                continue
            rollup[side] = {
                "shares": round(shares, 6),
                "avg_price": round(total_wager / shares, 6),
                "total_wager": round(total_wager, 6),
            }
        return rollup


def _map_action_for_db(action: DecisionAction) -> str:
    mapping = {
        DecisionAction.BUY_UP: "BUY_UP",
        DecisionAction.BUY_DOWN: "BUY_DOWN",
        DecisionAction.TAKE_PROFIT_UP: "TAKE_PROFIT_UP",
        DecisionAction.TAKE_PROFIT_DOWN: "TAKE_PROFIT_DOWN",
        DecisionAction.HOLD: "HOLD",
    }
    return mapping[action]


def _compact_indicator_snapshot(indicators: dict[str, IndicatorValue]) -> dict[str, IndicatorValue]:
    compact: dict[str, IndicatorValue] = {}
    for key in _INDICATOR_ACTIVITY_KEYS:
        raw = indicators.get(key)
        if isinstance(raw, bool):
            compact[key] = raw
            continue
        if isinstance(raw, str):
            compact[key] = raw
            continue
        value = _to_float(raw)
        compact[key] = None if value is None else round(value, 6)
    return compact


def _build_entry_signal_snapshot(
    *,
    indicators: dict[str, IndicatorValue],
    confidence: float,
    edge: float,
    reason_code: str,
    effective_min_confidence: float,
    threshold_relaxed: bool,
    flow_boost: float,
    streak: int,
    remaining_sec: int,
) -> dict[str, IndicatorValue]:
    mid_price = _to_float(indicators.get("mid_price"))
    vwap_1m = _to_float(indicators.get("vwap_1m"))
    price_vs_vwap: float | None = None
    if mid_price is not None and vwap_1m is not None and vwap_1m > 0:
        price_vs_vwap = (mid_price - vwap_1m) / vwap_1m

    return {
        "confidence": round(confidence, 6),
        "edge": round(edge, 6),
        "reason_code": reason_code,
        "effective_min_confidence": round(effective_min_confidence, 6),
        "threshold_relaxed": threshold_relaxed,
        "flow_boost": round(flow_boost, 6),
        "remaining_sec": float(remaining_sec),
        "signal_streak": float(streak),
        "mid_momentum_30s": _to_float(indicators.get("mid_momentum_30s")),
        "price_vs_vwap": price_vs_vwap,
        "order_imbalance": _to_float(indicators.get("order_imbalance")),
        "spread_momentum_30s": _to_float(indicators.get("spread_momentum_30s")),
        "ew_delta_imbalance": _to_float(indicators.get("ew_delta_imbalance")),
        "flow_toxicity": _to_float(indicators.get("flow_toxicity")),
        "large_trade_ratio": _to_float(indicators.get("large_trade_ratio")),
        "unknown_trade_ratio": _to_float(indicators.get("unknown_trade_ratio")),
        "reversal_imminent": indicators.get("reversal_imminent") is True,
        "flow_weight_preset": (
            str(indicators["flow_weight_preset"])
            if isinstance(indicators.get("flow_weight_preset"), str)
            else None
        ),
    }


def _flow_blocks_entry(action: DecisionAction, ew_delta_imbalance: float, threshold: float) -> bool:
    if action == DecisionAction.BUY_UP:
        return ew_delta_imbalance < (-threshold)
    if action == DecisionAction.BUY_DOWN:
        return ew_delta_imbalance > threshold
    return False


def _map_reversal_block_reason(reason: str) -> str:
    mapping = {
        "signal_not_persistent": "reversal_detected_but_streak_not_satisfied",
        "entry_cooldown": "reversal_detected_but_cooldown_active",
        "side_budget_exhausted": "reversal_detected_but_budget_exhausted",
        "below_min_wager": "reversal_detected_but_budget_exhausted",
        "side_budget": "reversal_detected_but_exposure_limit",
        "cannot_satisfy_min_shares": "reversal_detected_but_budget_exhausted",
        "price_cap": "reversal_detected_but_price_cap_exceeded",
    }
    return mapping.get(reason, f"reversal_detected_but_{reason}")


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_order_status(resp: dict[str, Any]) -> str:
    # py_clob_client responses vary by endpoint/version; normalize common layouts.
    direct = resp.get("status")
    if isinstance(direct, str):
        return direct.lower()

    order_obj = resp.get("order")
    if isinstance(order_obj, dict):
        nested = order_obj.get("status")
        if isinstance(nested, str):
            return nested.lower()

    return ""


def dominant_side(exposure: dict[str, float]) -> str | None:
    up = exposure.get("UP", 0.0)
    down = exposure.get("DOWN", 0.0)
    if up > down:
        return "UP"
    if down > up:
        return "DOWN"
    return None


@dataclass
class _EventRunLock:
    event_slug: str
    enabled: bool
    lock_path: Path | None = None

    def __enter__(self) -> "_EventRunLock":
        if not self.enabled:
            return self

        safe_slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in self.event_slug)
        self.lock_path = Path("/tmp") / f"copy_pros_event_{safe_slug}.lock"
        try:
            fd = os_open_exclusive(self.lock_path)
        except FileExistsError as err:
            raise RuntimeError(f"Event lock already active for slug={self.event_slug}") from err
        fd.close()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.lock_path and self.lock_path.exists():
            self.lock_path.unlink(missing_ok=True)


def os_open_exclusive(path: Path) -> TextIO:
    import os

    return os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR), "w")
