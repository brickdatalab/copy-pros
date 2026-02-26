"""Continuous multi-market supervisor for rotating event runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.table import Table

from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.config import TraderConfig
from trader.runtime.orchestrator import BotRuntime, EventRunResult, empty_runtime_activity_snapshot
from trader.ui.console import BotConsole

SUPPORTED_MARKETS: tuple[str, ...] = (
    "btc15",
    "eth15",
    "sol15",
    "btc5",
    "eth5",
    "sol5",
)
StatusCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    timeframe_minutes: int

    @property
    def key(self) -> str:
        return f"{self.symbol}{self.timeframe_minutes}"


@dataclass(frozen=True)
class EventOrder:
    action: str
    side: str
    price: float
    shares: float
    wager_usdc: float
    status: str = "filled"
    ts: str | None = None


@dataclass(frozen=True)
class EventRunSummary:
    market_key: str
    event_slug: str
    winning_side: str | None
    orders: list[EventOrder]
    predicted_side: str | None = None
    was_prediction_accurate: bool | None = None


@dataclass
class SideRollup:
    buy_shares: float = 0.0
    buy_notional: float = 0.0
    sell_shares: float = 0.0
    sell_notional: float = 0.0

    @property
    def net_shares(self) -> float:
        return self.buy_shares - self.sell_shares

    @property
    def avg_buy_price(self) -> float:
        if self.buy_shares <= 0:
            return 0.0
        return self.buy_notional / self.buy_shares


@dataclass
class MarketRollup:
    market_key: str
    events_run: int = 0
    resolved_up: int = 0
    resolved_down: int = 0
    unresolved: int = 0
    up: SideRollup = field(default_factory=SideRollup)
    down: SideRollup = field(default_factory=SideRollup)


@dataclass
class WorkerState:
    market_key: str
    status: str = "idle"
    event_slug: str | None = None
    completed_events: int = 0
    last_update_ts: float = field(default_factory=time.time)
    last_error: str | None = None
    activity: dict[str, Any] = field(default_factory=empty_runtime_activity_snapshot)


@dataclass(frozen=True)
class ContinuousRunnerConfig:
    specs: tuple[MarketSpec, ...]
    mode: str
    duration_minutes: float
    status_interval_sec: float = 2.0
    discovery_retry_sec: float = 1.0
    poll_resolution_attempts: int = 6
    poll_resolution_interval_sec: float = 5.0


class QuietBotConsole(BotConsole):
    @classmethod
    def create(cls) -> "QuietBotConsole":
        return cls(console=Console(stderr=True, quiet=True))

    def header(self, text: str) -> None:
        return

    def decision(self, action: str, confidence: float, edge: float, reason: str, remaining_sec: int) -> None:
        return

    def order(self, text: str) -> None:
        return

    def warn(self, text: str) -> None:
        return

    def error(self, text: str) -> None:
        return

    def summary(self, text: str) -> None:
        return


def current_event_slug(symbol: str, timeframe_minutes: int, now_ts: int | None = None) -> str:
    ts = int(time.time()) if now_ts is None else now_ts
    timeframe_sec = timeframe_minutes * 60
    bucket_start_ts = (ts // timeframe_sec) * timeframe_sec
    return f"{symbol.lower()}-updown-{timeframe_minutes}m-{bucket_start_ts}"


def parse_market_selection(raw: str) -> tuple[MarketSpec, ...]:
    tokens = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not tokens:
        raise ValueError("At least one market selector is required")

    specs: list[MarketSpec] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in SUPPORTED_MARKETS:
            raise ValueError(f"Unsupported market selector: {token}")
        if token in seen:
            continue
        seen.add(token)

        if token.endswith("15"):
            symbol = token[:-2]
            timeframe = 15
        elif token.endswith("5"):
            symbol = token[:-1]
            timeframe = 5
        else:
            raise ValueError(f"Unsupported market selector: {token}")
        specs.append(MarketSpec(symbol=symbol, timeframe_minutes=timeframe))
    return tuple(specs)


def aggregate_market_results(runs: list[EventRunSummary]) -> dict[str, MarketRollup]:
    summary: dict[str, MarketRollup] = {}
    for run in runs:
        rollup = summary.setdefault(run.market_key, MarketRollup(market_key=run.market_key))
        rollup.events_run += 1
        if run.winning_side == "UP":
            rollup.resolved_up += 1
        elif run.winning_side == "DOWN":
            rollup.resolved_down += 1
        else:
            rollup.unresolved += 1

        for order in run.orders:
            target = rollup.up if order.side == "UP" else rollup.down
            if order.action.startswith("BUY"):
                target.buy_shares += order.shares
                target.buy_notional += order.wager_usdc
            elif order.action.startswith("SELL"):
                target.sell_shares += order.shares
                target.sell_notional += order.wager_usdc
    return summary


class ContinuousRunner:
    def __init__(
        self,
        cfg: ContinuousRunnerConfig,
        base_cfg: TraderConfig,
        *,
        enable_stdin_controls: bool = True,
        enable_status_table: bool = True,
        status_callback: StatusCallback | None = None,
        console: Console | None = None,
    ) -> None:
        self.cfg = cfg
        self.base_cfg = base_cfg
        self.enable_stdin_controls = enable_stdin_controls
        self.enable_status_table = enable_status_table
        self.status_callback = status_callback
        self.console = console or Console()
        self.stop_event = asyncio.Event()
        self.resume_event = asyncio.Event()
        self.resume_event.set()
        self.worker_states: dict[str, WorkerState] = {spec.key: WorkerState(market_key=spec.key) for spec in cfg.specs}
        self.active_runtimes: dict[str, BotRuntime] = {}
        self.completed_runs: list[EventRunSummary] = []
        self.deadline_monotonic = time.monotonic() + (cfg.duration_minutes * 60)
        self.last_report_path: Path | None = None
        self._run_started = False
        self._run_finished = False

    @property
    def is_paused(self) -> bool:
        return not self.resume_event.is_set()

    def pause(self) -> None:
        self.resume_event.clear()

    def resume(self) -> None:
        self.resume_event.set()

    def request_stop(self) -> None:
        self.stop_event.set()

    def snapshot(self) -> dict[str, Any]:
        rollups = aggregate_market_results(self.completed_runs)
        earnings_payload = _build_earnings_payload(
            completed_runs=self.completed_runs,
            worker_states=self.worker_states,
            active_runtimes=self.active_runtimes,
        )
        worker_payload: dict[str, dict[str, Any]] = {}
        rollup_payload: dict[str, dict[str, Any]] = {}
        for key in sorted(self.worker_states.keys()):
            state = self.worker_states[key]
            runtime = self.active_runtimes.get(key)
            if runtime is not None:
                try:
                    state.activity = runtime.activity_snapshot()
                except Exception:
                    state.activity = empty_runtime_activity_snapshot()
            worker_payload[key] = {
                "status": state.status,
                "event_slug": state.event_slug,
                "completed_events": state.completed_events,
                "last_update_ts": state.last_update_ts,
                "last_error": state.last_error,
                "activity": dict(state.activity),
            }
            item = rollups.get(key, MarketRollup(market_key=key))
            rollup_payload[key] = {
                "events_run": item.events_run,
                "resolved_up": item.resolved_up,
                "resolved_down": item.resolved_down,
                "unresolved": item.unresolved,
                "up": {
                    "buy_shares": item.up.buy_shares,
                    "buy_notional": item.up.buy_notional,
                    "sell_shares": item.up.sell_shares,
                    "sell_notional": item.up.sell_notional,
                    "net_shares": item.up.net_shares,
                    "avg_buy_price": item.up.avg_buy_price,
                },
                "down": {
                    "buy_shares": item.down.buy_shares,
                    "buy_notional": item.down.buy_notional,
                    "sell_shares": item.down.sell_shares,
                    "sell_notional": item.down.sell_notional,
                    "net_shares": item.down.net_shares,
                    "avg_buy_price": item.down.avg_buy_price,
                },
            }

        return {
            "running": self._run_started and not self._run_finished,
            "paused": self.is_paused,
            "stop_requested": self.stop_event.is_set(),
            "mode": self.cfg.mode,
            "duration_minutes": self.cfg.duration_minutes,
            "markets": sorted(self.worker_states.keys()),
            "workers": worker_payload,
            "rollups": rollup_payload,
            "earnings": earnings_payload,
            "completed_runs": len(self.completed_runs),
            "report_path": str(self.last_report_path) if self.last_report_path else None,
        }

    async def run(self) -> Path:
        self._run_started = True
        self._run_finished = False
        if self.enable_status_table:
            self.console.print(
                "[cyan]Continuous runner started.[/cyan] Controls: [bold]p[/bold]=pause/resume, [bold]q[/bold]=stop"
            )
        await self._emit_status_update()
        tasks = [
            asyncio.create_task(self._worker_loop(spec), name=f"worker-{spec.key}")
            for spec in self.cfg.specs
        ]
        if self.enable_status_table or self.status_callback is not None:
            tasks.append(asyncio.create_task(self._status_loop(), name="status"))
        if self.enable_stdin_controls:
            tasks.append(asyncio.create_task(self._input_loop(), name="input"))
        tasks.append(asyncio.create_task(self._duration_guard_loop(), name="duration-guard"))

        try:
            await self.stop_event.wait()
        finally:
            self.resume_event.set()
            for runtime in list(self.active_runtimes.values()):
                runtime.stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        await self._resolve_pending_outcomes()
        report_path = self._write_report()
        self.last_report_path = report_path
        self._run_finished = True
        self._print_final_summary(report_path)
        await self._emit_status_update()
        return report_path

    async def _worker_loop(self, spec: MarketSpec) -> None:
        market_key = spec.key
        while not self.stop_event.is_set():
            if time.monotonic() >= self.deadline_monotonic:
                self.stop_event.set()
                return

            await self._wait_until_resumed()
            if self.stop_event.is_set():
                return

            slug = current_event_slug(spec.symbol, spec.timeframe_minutes)
            state = self.worker_states[market_key]
            state.status = "starting"
            state.event_slug = slug
            state.last_update_ts = time.time()
            state.last_error = None
            state.activity = empty_runtime_activity_snapshot()

            run_cfg = replace(
                self.base_cfg,
                poly_event_input=slug,
                bot_mode=self.cfg.mode,
                disallow_duplicate_event_run=False,
            )
            writer = BufferedSupabaseWriter(
                enabled=self._supabase_enabled(),
                schema=self._supabase_schema(),
                timeout_sec=self._supabase_timeout(),
            )
            runtime = BotRuntime(
                cfg=run_cfg,
                console=QuietBotConsole.create(),
                writer=writer,
                resume_event=self.resume_event,
            )
            self.active_runtimes[market_key] = runtime
            state.status = "running"
            try:
                result = await runtime.run()
            except Exception as err:
                state.status = "error"
                state.last_error = str(err)
                state.last_update_ts = time.time()
                state.activity = runtime.activity_snapshot()
                await asyncio.sleep(self.cfg.discovery_retry_sec)
                continue
            finally:
                state.activity = runtime.activity_snapshot()
                self.active_runtimes.pop(market_key, None)

            if result is not None:
                self.completed_runs.append(_to_event_run_summary(market_key, result))
                state.completed_events += 1
                state.status = "completed"
            else:
                state.status = "no_event"
            state.last_update_ts = time.time()
            await asyncio.sleep(self.cfg.discovery_retry_sec)

    async def _status_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.cfg.status_interval_sec)
            if self.enable_status_table:
                paused = not self.resume_event.is_set()
                table = Table(title=f"Continuous Runner {datetime.now(tz=timezone.utc).isoformat()}")
                table.add_column("Market", justify="left")
                table.add_column("State", justify="left")
                table.add_column("Event", justify="left")
                table.add_column("Completed", justify="right")
                table.add_column("Last Error", justify="left")
                for key in sorted(self.worker_states.keys()):
                    state = self.worker_states[key]
                    table.add_row(
                        key,
                        f"{state.status}{' (paused)' if paused else ''}",
                        state.event_slug or "-",
                        str(state.completed_events),
                        state.last_error or "-",
                    )
                self.console.print(table)
            await self._emit_status_update()

    async def _input_loop(self) -> None:
        while not self.stop_event.is_set():
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                await asyncio.sleep(0.2)
                continue
            command = line.strip().lower()
            if command in {"p", "pause", "resume", "r"}:
                if not self.is_paused:
                    self.pause()
                    self.console.print("[yellow]Paused[/yellow]")
                else:
                    self.resume()
                    self.console.print("[green]Resumed[/green]")
            elif command in {"q", "quit", "stop"}:
                self.console.print("[yellow]Stop requested[/yellow]")
                self.request_stop()
                return

    async def _duration_guard_loop(self) -> None:
        while not self.stop_event.is_set():
            if time.monotonic() >= self.deadline_monotonic:
                self.stop_event.set()
                return
            await asyncio.sleep(0.25)

    async def _wait_until_resumed(self) -> None:
        while not self.stop_event.is_set() and not self.resume_event.is_set():
            await asyncio.sleep(0.1)

    async def _resolve_pending_outcomes(self) -> None:
        unresolved_indices = [
            idx for idx, run in enumerate(self.completed_runs) if run.winning_side not in {"UP", "DOWN"}
        ]
        if not unresolved_indices:
            return
        for _ in range(self.cfg.poll_resolution_attempts):
            pending: list[int] = []
            for idx in unresolved_indices:
                run = self.completed_runs[idx]
                runtime_result = await _resolve_event(run.event_slug)
                if runtime_result is None:
                    pending.append(idx)
                    continue
                self.completed_runs[idx] = EventRunSummary(
                    market_key=run.market_key,
                    event_slug=run.event_slug,
                    winning_side=runtime_result,
                    orders=run.orders,
                    predicted_side=run.predicted_side,
                    was_prediction_accurate=(runtime_result == run.predicted_side) if run.predicted_side else None,
                )
            unresolved_indices = pending
            if not unresolved_indices:
                return
            await asyncio.sleep(self.cfg.poll_resolution_interval_sec)

    def _write_report(self) -> Path:
        report_dir = Path("runtime-logs")
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"continuous_report_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        payload: dict[str, Any] = {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "markets": [spec.key for spec in self.cfg.specs],
            "mode": self.cfg.mode,
            "duration_minutes": self.cfg.duration_minutes,
            "runs": [
                {
                    "market_key": run.market_key,
                    "event_slug": run.event_slug,
                    "winning_side": run.winning_side,
                    "predicted_side": run.predicted_side,
                    "was_prediction_accurate": run.was_prediction_accurate,
                    "orders": [
                        {
                            "action": order.action,
                            "side": order.side,
                            "price": order.price,
                            "shares": order.shares,
                            "wager_usdc": order.wager_usdc,
                            "status": order.status,
                            "ts": order.ts,
                        }
                        for order in run.orders
                    ],
                }
                for run in self.completed_runs
            ],
        }
        report_path.write_text(json.dumps(payload, indent=2))
        return report_path

    def _print_final_summary(self, report_path: Path) -> None:
        if not self.enable_status_table:
            return
        rollups = aggregate_market_results(self.completed_runs)
        table = Table(title="Final Continuous Run Summary")
        table.add_column("Market")
        table.add_column("Events", justify="right")
        table.add_column("Resolved", justify="left")
        table.add_column("UP Shares", justify="right")
        table.add_column("UP Avg Buy", justify="right")
        table.add_column("DOWN Shares", justify="right")
        table.add_column("DOWN Avg Buy", justify="right")
        for key in sorted(rollups.keys()):
            item = rollups[key]
            table.add_row(
                key,
                str(item.events_run),
                f"UP:{item.resolved_up} DOWN:{item.resolved_down} PENDING:{item.unresolved}",
                f"{item.up.net_shares:.3f}",
                f"{item.up.avg_buy_price:.3f}",
                f"{item.down.net_shares:.3f}",
                f"{item.down.avg_buy_price:.3f}",
            )
        self.console.print(table)
        self.console.print(f"[cyan]Report written:[/cyan] {report_path}")

    async def _emit_status_update(self) -> None:
        callback = self.status_callback
        if callback is None:
            return
        result = callback(self.snapshot())
        if inspect.isawaitable(result):
            await result

    def _supabase_enabled(self) -> bool:
        from os import getenv

        return getenv("SUPABASE_ENABLED", "true").lower() in {"1", "true", "yes", "y", "on"}

    def _supabase_schema(self) -> str:
        from os import getenv

        return getenv("SUPABASE_SCHEMA", "copy_pros")

    def _supabase_timeout(self) -> float:
        from os import getenv

        return float(getenv("SUPABASE_TRACKING_TIMEOUT_MS", "250")) / 1000


def _build_earnings_payload(
    *,
    completed_runs: list[EventRunSummary],
    worker_states: dict[str, WorkerState],
    active_runtimes: dict[str, BotRuntime],
) -> dict[str, Any]:
    event_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []

    for run in completed_runs:
        event_row, orders = _build_event_and_orders(run)
        event_rows.append(event_row)
        order_rows.extend(orders)

    for market_key, runtime in active_runtimes.items():
        state = worker_states.get(market_key)
        if state is None:
            continue
        running_run = EventRunSummary(
            market_key=market_key,
            event_slug=state.event_slug or "",
            winning_side=None,
            orders=_runtime_orders(runtime),
        )
        event_row, orders = _build_event_and_orders(running_run, status_override="running")
        event_rows.append(event_row)
        order_rows.extend(orders)

    event_rows.sort(key=lambda row: (_event_status_rank(str(row.get("status"))), -_slug_epoch(str(row.get("event_slug")))))
    order_rows.sort(key=lambda row: -_order_epoch(row))
    order_rows = order_rows[:250]

    resolved_rows = [row for row in event_rows if row.get("status") == "resolved" and row.get("pnl_usdc") is not None]
    resolved_wagered = sum(float(row.get("wagered_usdc", 0.0) or 0.0) for row in resolved_rows)
    resolved_returned = sum(float(row.get("returned_usdc", 0.0) or 0.0) for row in resolved_rows)
    resolved_pnl = sum(float(row.get("pnl_usdc", 0.0) or 0.0) for row in resolved_rows)
    gained = sum(max(float(row.get("pnl_usdc", 0.0) or 0.0), 0.0) for row in resolved_rows)
    lost = sum(max(-float(row.get("pnl_usdc", 0.0) or 0.0), 0.0) for row in resolved_rows)
    accurate = sum(1 for row in resolved_rows if row.get("was_prediction_accurate") is True)
    inaccurate = sum(1 for row in resolved_rows if row.get("was_prediction_accurate") is False)

    return {
        "totals": {
            "resolved_events": len(resolved_rows),
            "pending_events": sum(1 for row in event_rows if row.get("status") != "resolved"),
            "running_events": sum(1 for row in event_rows if row.get("status") == "running"),
            "accurate_events": accurate,
            "inaccurate_events": inaccurate,
            "resolved_wagered_usdc": round(resolved_wagered, 6),
            "resolved_returned_usdc": round(resolved_returned, 6),
            "resolved_pnl_usdc": round(resolved_pnl, 6),
            "gained_usdc": round(gained, 6),
            "lost_usdc": round(lost, 6),
            "total_wagered_usdc": round(sum(float(row.get("wagered_usdc", 0.0) or 0.0) for row in event_rows), 6),
            "order_rows": len(order_rows),
        },
        "events": event_rows,
        "orders": order_rows,
    }


def _build_event_and_orders(
    run: EventRunSummary,
    *,
    status_override: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metrics = _event_metrics(run.orders, run.winning_side)
    status = status_override or ("resolved" if run.winning_side in {"UP", "DOWN"} else "pending")
    resolved = status == "resolved"
    was_prediction_accurate = run.was_prediction_accurate
    if resolved and was_prediction_accurate is None and run.predicted_side and run.winning_side:
        was_prediction_accurate = run.predicted_side == run.winning_side

    event_row: dict[str, Any] = {
        "market_key": run.market_key,
        "event_slug": run.event_slug,
        "status": status,
        "winning_side": run.winning_side if resolved else None,
        "predicted_side": run.predicted_side,
        "was_prediction_accurate": was_prediction_accurate if resolved else None,
        "wagered_usdc": round(_number_or_zero(metrics["buy_wager_usdc"]), 6),
        "returned_usdc": round(_number_or_zero(metrics["returned_usdc"]), 6),
        "pnl_usdc": round(_number_or_zero(metrics["pnl_usdc"]), 6) if metrics["pnl_usdc"] is not None else None,
        "orders_count": metrics["orders_count"],
        "buy_orders_count": metrics["buy_orders_count"],
        "sell_orders_count": metrics["sell_orders_count"],
        "net_up_shares": round(_number_or_zero(metrics["net_up_shares"]), 6),
        "net_down_shares": round(_number_or_zero(metrics["net_down_shares"]), 6),
    }

    order_rows = [
        {
            "market_key": run.market_key,
            "event_slug": run.event_slug,
            "ts": order.ts,
            "action": order.action,
            "side": order.side,
            "price": order.price,
            "shares": order.shares,
            "wager_usdc": order.wager_usdc,
            "status": order.status,
        }
        for order in run.orders
    ]
    return event_row, order_rows


def _event_metrics(orders: list[EventOrder], winning_side: str | None) -> dict[str, float | int | None]:
    buy_wager_usdc = 0.0
    sell_proceeds_usdc = 0.0
    net_shares = {"UP": 0.0, "DOWN": 0.0}
    buy_orders_count = 0
    sell_orders_count = 0

    for order in orders:
        side = order.side if order.side in {"UP", "DOWN"} else None
        if side is None:
            continue
        if order.action.startswith("BUY"):
            buy_orders_count += 1
            buy_wager_usdc += max(order.wager_usdc, 0.0)
            net_shares[side] += max(order.shares, 0.0)
        elif order.action.startswith("SELL"):
            sell_orders_count += 1
            sell_proceeds_usdc += max(order.wager_usdc, 0.0)
            net_shares[side] -= max(order.shares, 0.0)

    returned_usdc = sell_proceeds_usdc
    pnl_usdc: float | None = None
    if winning_side in {"UP", "DOWN"}:
        remaining_winning_shares = max(0.0, net_shares[winning_side])
        returned_usdc += remaining_winning_shares
        pnl_usdc = returned_usdc - buy_wager_usdc

    return {
        "buy_wager_usdc": buy_wager_usdc,
        "returned_usdc": returned_usdc,
        "pnl_usdc": pnl_usdc,
        "orders_count": buy_orders_count + sell_orders_count,
        "buy_orders_count": buy_orders_count,
        "sell_orders_count": sell_orders_count,
        "net_up_shares": max(0.0, net_shares["UP"]),
        "net_down_shares": max(0.0, net_shares["DOWN"]),
    }


def _runtime_orders(runtime: BotRuntime) -> list[EventOrder]:
    rows: list[EventOrder] = []
    for record in getattr(runtime, "order_records", []):
        action = str(getattr(record, "action", ""))
        side = str(getattr(record, "side", ""))
        if not action or side not in {"UP", "DOWN"}:
            continue
        rows.append(
            EventOrder(
                action=action,
                side=side,
                price=_safe_float(getattr(record, "price", 0.0)),
                shares=_safe_float(getattr(record, "shares", 0.0)),
                wager_usdc=_safe_float(getattr(record, "wager_usdc", 0.0)),
                status=str(getattr(record, "status", "submitted")),
                ts=str(getattr(record, "ts", "")) or None,
            )
        )
    return rows


def _event_status_rank(status: str) -> int:
    if status == "running":
        return 0
    if status == "pending":
        return 1
    if status == "resolved":
        return 2
    return 3


def _slug_epoch(event_slug: str) -> int:
    match = re.search(r"(\d{10})$", event_slug)
    if not match:
        return 0
    return int(match.group(1))


def _parse_iso_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _order_epoch(row: dict[str, Any]) -> float:
    ts = _parse_iso_ts(str(row.get("ts") or ""))
    if ts > 0:
        return ts
    return float(_slug_epoch(str(row.get("event_slug") or "")))


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _number_or_zero(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _to_event_run_summary(market_key: str, result: EventRunResult) -> EventRunSummary:
    return EventRunSummary(
        market_key=market_key,
        event_slug=result.event_slug,
        winning_side=result.winning_side,
        predicted_side=result.predicted_side,
        was_prediction_accurate=result.was_prediction_accurate,
        orders=[
            EventOrder(
                action=order.action,
                side=order.side,
                price=order.price,
                shares=order.shares,
                wager_usdc=order.wager_usdc,
                status=order.status,
                ts=order.ts,
            )
            for order in result.orders
            if order.status in {"filled", "submitted"}
        ],
    )


async def _resolve_event(event_slug: str) -> str | None:
    from trader.adapters.polymarket.rest_client import fetch_event_market_context, fetch_winning_side

    try:
        ctx = await fetch_event_market_context(event_slug)
        winner = await fetch_winning_side(ctx.condition_id)
    except Exception:
        return None
    return winner
