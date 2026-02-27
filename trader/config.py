"""Runtime configuration for the event trader."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class TraderConfig:
    poly_event_input: str
    bot_mode: str = "live"
    max_entry_price: float = 0.80
    min_signal_confidence: float = 0.52
    min_signal_edge: float = 0.10
    enable_flow_signals: bool = True
    flow_weight_preset: str = "v1"
    flow_block_delta_threshold: float = 0.10
    flow_unknown_ratio_cutoff: float = 0.35
    flow_unknown_delta_scale: float = 0.5
    flow_ew_half_life_seconds: float = 15.0
    flow_vpin_bucket_volume: float = 300.0
    flow_vpin_num_buckets: int = 10
    flow_large_trade_size: float = 75.0
    flow_large_ratio_window_seconds: int = 30
    trade_side_tolerance: float = 0.001
    enable_reversal_imminent: bool = True
    vwap_up_delta_15s: float = 0.003
    mid_flat_delta_15s: float = 0.001
    momentum_accel_5s: float = 0.002
    signal_persist_ticks: int = 4
    entry_cooldown_ms: int = 750
    hedge_max_exposure_ratio: float = 0.25
    hedge_min_confidence: float = 0.85
    hedge_max_entry_price: float = 0.35
    max_wager_per_side_usdc: float = 20.0
    max_single_wager_usdc: float = 5.0
    min_wager_usdc: float = 2.0
    min_shares_per_purchase: float = 5.0
    enable_convexity_budget_reservation: bool = False
    allow_both_sides: bool = True
    count_open_orders_in_exposure: bool = True
    disallow_duplicate_event_run: bool = True
    enable_take_profit: bool = True
    take_profit_trigger_price: float = 0.94
    take_profit_limit_price: float = 0.95
    take_profit_min_remaining_sec: int = 120
    entry_warmup_min_seconds: float = 60.0
    entry_warmup_min_ws_ticks: int = 5
    entry_warmup_min_indicator_updates: int = 5
    enable_event_driven_loops: bool = True
    event_driven_max_wait_ms: int = 250
    indicator_interval_ms: int = 100
    signal_interval_ms: int = 100
    execution_interval_ms: int = 75
    supabase_flush_interval_ms: int = 200
    ws_tick_log_sample_every: int = 25
    min_remaining_seconds_to_run: int = 1


TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def load_config() -> TraderConfig:
    event_input = os.getenv("POLY_EVENT_INPUT")
    if not event_input:
        raise ValueError("POLY_EVENT_INPUT is required")

    cfg = TraderConfig(
        poly_event_input=event_input,
        bot_mode=os.getenv("BOT_MODE", "live"),
        max_entry_price=_env_float("MAX_ENTRY_PRICE", 0.80),
        min_signal_confidence=_env_float("MIN_SIGNAL_CONFIDENCE", 0.52),
        min_signal_edge=_env_float("MIN_SIGNAL_EDGE", 0.10),
        enable_flow_signals=_env_bool("ENABLE_FLOW_SIGNALS", True),
        flow_weight_preset=os.getenv("FLOW_WEIGHT_PRESET", "v1"),
        flow_block_delta_threshold=_env_float("FLOW_BLOCK_DELTA_THRESHOLD", 0.10),
        flow_unknown_ratio_cutoff=_env_float("FLOW_UNKNOWN_RATIO_CUTOFF", 0.35),
        flow_unknown_delta_scale=_env_float("FLOW_UNKNOWN_DELTA_SCALE", 0.5),
        flow_ew_half_life_seconds=_env_float("FLOW_EW_HALF_LIFE_SECONDS", 15.0),
        flow_vpin_bucket_volume=_env_float("VPIN_BUCKET_VOLUME", 300.0),
        flow_vpin_num_buckets=_env_int("VPIN_NUM_BUCKETS", 10),
        flow_large_trade_size=_env_float("LARGE_TRADE_SIZE", 75.0),
        flow_large_ratio_window_seconds=_env_int("LARGE_RATIO_WINDOW_SECONDS", 30),
        trade_side_tolerance=_env_float("TRADE_SIDE_TOLERANCE", 0.001),
        enable_reversal_imminent=_env_bool("ENABLE_REVERSAL_IMMINENT", True),
        vwap_up_delta_15s=_env_float("VWAP_UP_DELTA_15S", 0.003),
        mid_flat_delta_15s=_env_float("MID_FLAT_DELTA_15S", 0.001),
        momentum_accel_5s=_env_float("MOMENTUM_ACCEL_5S", 0.002),
        signal_persist_ticks=_env_int("SIGNAL_PERSIST_TICKS", 4),
        entry_cooldown_ms=_env_int("ENTRY_COOLDOWN_MS", 750),
        hedge_max_exposure_ratio=_env_float("HEDGE_MAX_EXPOSURE_RATIO", 0.25),
        hedge_min_confidence=_env_float("HEDGE_MIN_CONFIDENCE", 0.85),
        hedge_max_entry_price=_env_float("HEDGE_MAX_ENTRY_PRICE", 0.35),
        max_wager_per_side_usdc=_env_float("MAX_WAGER_PER_SIDE_USDC", 20.0),
        max_single_wager_usdc=_env_float("MAX_SINGLE_WAGER_USDC", 5.0),
        min_wager_usdc=_env_float("MIN_WAGER_USDC", 2.0),
        min_shares_per_purchase=_env_float("MIN_SHARES_PER_PURCHASE", 5.0),
        enable_convexity_budget_reservation=_env_bool("ENABLE_CONVEXITY_BUDGET_RESERVATION", False),
        allow_both_sides=_env_bool("ALLOW_BOTH_SIDES", True),
        count_open_orders_in_exposure=_env_bool("COUNT_OPEN_ORDERS_IN_EXPOSURE", True),
        disallow_duplicate_event_run=_env_bool("DISALLOW_DUPLICATE_EVENT_RUN", True),
        enable_take_profit=_env_bool("ENABLE_TAKE_PROFIT", True),
        take_profit_trigger_price=_env_float("TAKE_PROFIT_TRIGGER_PRICE", 0.94),
        take_profit_limit_price=_env_float("TAKE_PROFIT_LIMIT_PRICE", 0.95),
        take_profit_min_remaining_sec=_env_int("TAKE_PROFIT_MIN_REMAINING_SEC", 120),
        entry_warmup_min_seconds=_env_float("ENTRY_WARMUP_MIN_SECONDS", 60.0),
        entry_warmup_min_ws_ticks=_env_int("ENTRY_WARMUP_MIN_WS_TICKS", 5),
        entry_warmup_min_indicator_updates=_env_int("ENTRY_WARMUP_MIN_INDICATOR_UPDATES", 5),
        enable_event_driven_loops=_env_bool("ENABLE_EVENT_DRIVEN_LOOPS", True),
        event_driven_max_wait_ms=_env_int("EVENT_DRIVEN_MAX_WAIT_MS", 250),
        indicator_interval_ms=_env_int("INDICATOR_INTERVAL_MS", 100),
        signal_interval_ms=_env_int("SIGNAL_INTERVAL_MS", 100),
        execution_interval_ms=_env_int("EXECUTION_INTERVAL_MS", 75),
        supabase_flush_interval_ms=_env_int("SUPABASE_FLUSH_INTERVAL_MS", 200),
        ws_tick_log_sample_every=_env_int("WS_TICK_LOG_SAMPLE_EVERY", 25),
        min_remaining_seconds_to_run=_env_int("MIN_REMAINING_SECONDS_TO_RUN", 1),
    )

    if cfg.bot_mode not in {"live", "dry_run"}:
        raise ValueError("BOT_MODE must be 'live' or 'dry_run'")
    if cfg.flow_weight_preset not in {"baseline", "flow_v1", "v1"}:
        raise ValueError("FLOW_WEIGHT_PRESET must be one of: baseline, flow_v1, v1")

    return cfg
