"""Localhost web playground for controlling and monitoring the continuous runner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from trader.config import TraderConfig, load_config
from trader.runtime.continuous_runner import (
    ContinuousRunner,
    ContinuousRunnerConfig,
    StatusCallback,
    parse_market_selection,
)

DEFAULT_MARKETS: list[str] = ["btc15", "eth15", "sol15", "btc5", "eth5", "sol5"]
PLAYGROUND_UI_PATH = Path(__file__).resolve().parents[2] / "playground-mock.html"


class StartSessionRequest(BaseModel):
    markets: list[str] = Field(default_factory=lambda: list(DEFAULT_MARKETS))
    duration_minutes: float = Field(default=60.0, gt=0)
    mode: Literal["dry_run", "live"] = "dry_run"
    status_interval_sec: float = Field(default=1.0, gt=0.2, le=10.0)


class PlaygroundController:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runner: ContinuousRunner | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._latest_snapshot: dict[str, object] = {
            "running": False,
            "paused": False,
            "stop_requested": False,
            "mode": "dry_run",
            "duration_minutes": 0.0,
            "markets": [],
            "workers": {},
            "rollups": {},
            "earnings": {"totals": {}, "events": [], "orders": []},
            "completed_runs": 0,
            "report_path": None,
        }

    async def start(self, req: StartSessionRequest) -> dict[str, object]:
        async with self._lock:
            if self._runner_task is not None and not self._runner_task.done():
                raise RuntimeError("A session is already running")

            specs = parse_market_selection(",".join(req.markets))
            base_cfg = _load_base_cfg(req.mode)
            cfg = ContinuousRunnerConfig(
                specs=specs,
                mode=req.mode,
                duration_minutes=req.duration_minutes,
                status_interval_sec=req.status_interval_sec,
            )
            runner = _build_runner(cfg=cfg, base_cfg=base_cfg, callback=self._on_runner_status)
            self._runner = runner
            self._runner_task = asyncio.create_task(self._run_runner(runner))
            self._latest_snapshot = runner.snapshot()

        await self._broadcast(self._latest_snapshot)
        return self._latest_snapshot

    async def pause(self) -> dict[str, object]:
        async with self._lock:
            runner = self._runner
            if runner is None:
                return self._latest_snapshot
            runner.pause()
            self._latest_snapshot = runner.snapshot()
        await self._broadcast(self._latest_snapshot)
        return self._latest_snapshot

    async def resume(self) -> dict[str, object]:
        async with self._lock:
            runner = self._runner
            if runner is None:
                return self._latest_snapshot
            runner.resume()
            self._latest_snapshot = runner.snapshot()
        await self._broadcast(self._latest_snapshot)
        return self._latest_snapshot

    async def stop(self) -> dict[str, object]:
        async with self._lock:
            runner = self._runner
            if runner is None:
                return self._latest_snapshot
            runner.request_stop()
            self._latest_snapshot = runner.snapshot()
        await self._broadcast(self._latest_snapshot)
        return self._latest_snapshot

    async def state(self) -> dict[str, object]:
        async with self._lock:
            return dict(self._latest_snapshot)

    async def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)
            payload = json.dumps(self._latest_snapshot)
        queue.put_nowait(payload)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def _run_runner(self, runner: ContinuousRunner) -> None:
        try:
            await runner.run()
        except Exception as err:
            async with self._lock:
                self._latest_snapshot = dict(runner.snapshot())
                self._latest_snapshot["error"] = str(err)
                if self._runner is runner:
                    self._runner = None
                    self._runner_task = None
            await self._broadcast(self._latest_snapshot)
            return

        async with self._lock:
            self._latest_snapshot = runner.snapshot()
            if self._runner is runner:
                self._runner = None
                self._runner_task = None
        await self._broadcast(self._latest_snapshot)

    async def _on_runner_status(self, snapshot: dict[str, object]) -> None:
        async with self._lock:
            self._latest_snapshot = snapshot
        await self._broadcast(snapshot)

    async def _broadcast(self, payload: dict[str, object]) -> None:
        serialized = json.dumps(payload)
        async with self._lock:
            subscribers = list(self._subscribers)

        stale: list[asyncio.Queue[str]] = []
        for queue in subscribers:
            try:
                if queue.full():
                    _ = queue.get_nowait()
                queue.put_nowait(serialized)
            except asyncio.QueueEmpty:
                queue.put_nowait(serialized)
            except Exception:
                stale.append(queue)
        if stale:
            async with self._lock:
                for queue in stale:
                    self._subscribers.discard(queue)


def _load_base_cfg(mode: str) -> TraderConfig:
    os.environ.setdefault("POLY_EVENT_INPUT", "btc-updown-5m-0")
    os.environ["BOT_MODE"] = mode
    return load_config()


def _build_runner(
    cfg: ContinuousRunnerConfig,
    base_cfg: TraderConfig,
    callback: StatusCallback,
) -> ContinuousRunner:
    return ContinuousRunner(
        cfg=cfg,
        base_cfg=base_cfg,
        enable_stdin_controls=False,
        enable_status_table=False,
        status_callback=callback,
    )


controller = PlaygroundController()
app = FastAPI(title="Copy Pros Local Playground", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    if PLAYGROUND_UI_PATH.exists():
        return PLAYGROUND_UI_PATH.read_text(encoding="utf-8")
    return _PLAYGROUND_HTML


@app.get("/api/state", response_class=JSONResponse)
async def get_state() -> JSONResponse:
    return JSONResponse(await controller.state())


@app.post("/api/start", response_class=JSONResponse)
async def start_session(req: StartSessionRequest) -> JSONResponse:
    try:
        state = await controller.start(req)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return JSONResponse(state)


@app.post("/api/pause", response_class=JSONResponse)
async def pause_session() -> JSONResponse:
    return JSONResponse(await controller.pause())


@app.post("/api/resume", response_class=JSONResponse)
async def resume_session() -> JSONResponse:
    return JSONResponse(await controller.resume())


@app.post("/api/stop", response_class=JSONResponse)
async def stop_session() -> JSONResponse:
    return JSONResponse(await controller.stop())


@app.get("/api/stream")
async def stream_state() -> StreamingResponse:
    queue = await controller.subscribe()

    async def event_generator() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            await controller.unsubscribe(queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


_PLAYGROUND_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Copy Pros Local Playground</title>
  <style>
    :root { --bg: #0b1220; --panel: #0f172a; --panel-soft: #111b32; --line: #24314a; --line-soft: #1f2a3f; --txt: #e2e8f0; --muted: #94a3b8; --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444; --accent:#38bdf8; }
    body { margin:0; font-family: "SF Pro Text", "Segoe UI", -apple-system, BlinkMacSystemFont, "Inter", "Helvetica Neue", sans-serif; background: radial-gradient(circle at 10% 10%, #11203a 0%, #0b1220 35%, #070d18 100%); color: var(--txt); }
    .wrap { max-width: 1820px; margin: 0 auto; padding: 20px; display: grid; gap: 16px; }
    .panel { background: linear-gradient(180deg, rgba(17,27,50,0.96), rgba(15,23,42,0.96)); border:1px solid var(--line); border-radius: 12px; padding: 16px; box-shadow: 0 10px 24px rgba(2, 8, 23, 0.35); }
    .title { font-size: 22px; margin: 0 0 10px; font-weight: 650; letter-spacing: -0.01em; }
    .row { display:flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    label { color: var(--muted); }
    input, select, button { font: inherit; }
    input[type="number"], select { width: 90px; background:#0a1322; border:1px solid var(--line); color:var(--txt); border-radius:8px; padding:7px 10px; }
    .markets { display:grid; grid-template-columns: repeat(6,minmax(120px,1fr)); gap: 8px; margin-top: 12px; }
    .mk { border:1px solid var(--line); border-radius:10px; padding:8px; display:flex; gap:8px; align-items:center; justify-content:center; background:#0b1427; }
    button { border:1px solid var(--line); border-radius:10px; padding:8px 14px; background:#0b1427; color:var(--txt); cursor:pointer; transition: border-color .15s ease, transform .12s ease; }
    button:hover { border-color: var(--accent); transform: translateY(-1px); }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    table { width:100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid var(--line-soft); padding: 8px 8px; text-align: left; font-size: 13px; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; font-weight: 560; text-transform: uppercase; letter-spacing: 0.04em; }
    .grid2 { display:grid; grid-template-columns: 1.15fr 1fr; gap: 16px; align-items: start; }
    pre { background:#0a1322; border:1px solid var(--line); border-radius:10px; padding:12px; overflow:auto; max-height:280px; }
    .metricbar { display:grid; grid-template-columns: repeat(5, minmax(130px, 1fr)); gap: 8px; margin-top: 10px; }
    .metricbar.compact { grid-template-columns: repeat(4, minmax(150px, 1fr)); }
    .metric { border:1px solid var(--line); background:#0b1427; border-radius:10px; padding:9px; }
    .metric .k { color: var(--muted); font-size: 12px; display:block; }
    .metric .v { font-size: 17px; font-weight: 630; }
    .mono-sm { font-size: 12px; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .section-title { margin: 12px 0 8px; color: var(--muted); font-size: 12px; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; }
    .table-wrap { border:1px solid var(--line); border-radius:10px; overflow-x:auto; background:#0b1427; }
    .table-wrap table { min-width: 100%; }
    details { border:1px solid var(--line); border-radius:10px; padding:10px; background:#0a1322; }
    summary { cursor:pointer; color:var(--muted); }
    @media (max-width: 1360px){ .grid2 { grid-template-columns: 1fr; } .markets { grid-template-columns: repeat(3,minmax(110px,1fr)); } }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1 class="title">Copy Pros Local Playground</h1>
      <div class="row">
        <label>Duration (min) <input id="duration" type="number" min="0.1" step="0.1" value="60" /></label>
        <label>Mode
          <select id="mode">
            <option value="dry_run" selected>dry_run</option>
            <option value="live">live</option>
          </select>
        </label>
        <button id="startBtn">Start</button>
        <button id="pauseBtn">Pause</button>
        <button id="resumeBtn">Resume</button>
        <button id="stopBtn">Stop</button>
      </div>
      <div class="markets" id="markets"></div>
      <p id="statusLine" class="warn">Idle</p>
      <div id="metricBar" class="metricbar"></div>
    </div>

    <div class="grid2">
      <div class="panel">
        <h2 class="title">Pipeline Health</h2>
        <div class="table-wrap">
          <table id="workersTable">
            <thead><tr><th>Market</th><th>Status</th><th>Ingest</th><th>Indicators</th><th>Decision</th><th>Orders</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <h2 class="title">Earnings</h2>
        <div id="earningsSummary" class="metricbar compact"></div>
        <p class="section-title">Event Outcomes</p>
        <div class="table-wrap">
          <table id="eventsTable">
            <thead><tr><th>Market</th><th>Event</th><th>Status</th><th>Winner / Pred</th><th>Accurate</th><th>Wagered</th><th>Returned</th><th>PnL</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <p class="section-title">Order Feed</p>
        <div class="table-wrap">
          <table id="ordersTable">
            <thead><tr><th>Time</th><th>Market</th><th>Event</th><th>Action</th><th>Px x Sh</th><th>Wager</th><th>Status</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
        <p id="earningsFooter" class="mono-sm"></p>
      </div>
    </div>

    <div class="panel">
      <details>
        <summary>Raw State (debug)</summary>
        <pre id="rawState"></pre>
      </details>
    </div>
  </div>

  <script>
    const DEFAULT_MARKETS = ["btc15","eth15","sol15","btc5","eth5","sol5"];
    const marketsNode = document.getElementById("markets");
    const statusLine = document.getElementById("statusLine");
    const rawState = document.getElementById("rawState");
    const workersBody = document.querySelector("#workersTable tbody");
    const metricBar = document.getElementById("metricBar");
    const earningsSummary = document.getElementById("earningsSummary");
    const eventsBody = document.querySelector("#eventsTable tbody");
    const ordersBody = document.querySelector("#ordersTable tbody");
    const earningsFooter = document.getElementById("earningsFooter");

    function mkMarketCheckboxes() {
      marketsNode.innerHTML = "";
      DEFAULT_MARKETS.forEach((key) => {
        const wrapper = document.createElement("label");
        wrapper.className = "mk";
        wrapper.innerHTML = `<input type="checkbox" value="${key}" checked /> ${key}`;
        marketsNode.appendChild(wrapper);
      });
    }

    function selectedMarkets() {
      return Array.from(marketsNode.querySelectorAll("input[type='checkbox']:checked")).map((n) => n.value);
    }

    function fmt(v, digits = 3) {
      if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
      return Number(v).toFixed(digits);
    }

    function ageSec(ts) {
      if (!ts) return null;
      const age = (Date.now() / 1000) - Number(ts);
      if (!Number.isFinite(age) || age < 0) return null;
      return age;
    }

    function freshnessClass(age, warn, bad) {
      if (age === null) return "warn";
      if (age >= bad) return "bad";
      if (age >= warn) return "warn";
      return "ok";
    }

    function cleanMarketLabel(raw) {
      const text = String(raw || "");
      const m = text.match(/^([a-zA-Z]+)(\\d+)$/);
      if (!m) return text || "-";
      return `${m[1].toUpperCase()} ${m[2]}m`;
    }

    function parseEventSlug(slug) {
      const text = String(slug || "");
      const m = text.match(/^([a-zA-Z0-9]+)-updown-(\\d+)m-(\\d{10})$/);
      if (!m) return null;
      const symbol = m[1].toUpperCase();
      const timeframe = Number(m[2]);
      const startEpoch = Number(m[3]);
      if (!Number.isFinite(startEpoch)) return null;
      const endEpoch = startEpoch + (timeframe * 60);
      return { symbol, timeframe, startEpoch, endEpoch };
    }

    function formatEpochShort(epochSeconds) {
      if (!Number.isFinite(Number(epochSeconds))) return "-";
      const dt = new Date(Number(epochSeconds) * 1000);
      return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }

    function cleanEventLabel(slug, marketKey) {
      const parsed = parseEventSlug(slug);
      if (!parsed) {
        const market = cleanMarketLabel(marketKey);
        return {
          primary: market,
          secondary: slug || "-",
        };
      }
      return {
        primary: `${parsed.symbol} ${parsed.timeframe}m`,
        secondary: `${formatEpochShort(parsed.startEpoch)} - ${formatEpochShort(parsed.endEpoch)}`,
      };
    }

    function formatIsoTimestamp(ts) {
      const text = String(ts || "");
      if (!text) return "-";
      const dt = new Date(text);
      if (Number.isNaN(dt.getTime())) return text;
      return dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }

    function renderMetrics(state) {
      const workers = state.workers || {};
      const keys = Object.keys(workers);
      let wsTicks = 0;
      let indicatorUpdates = 0;
      let decisions = 0;
      let orders = 0;
      let freshStreams = 0;
      keys.forEach((key) => {
        const activity = (workers[key] || {}).activity || {};
        wsTicks += Number(activity.ws_ticks || 0);
        indicatorUpdates += Number(activity.indicator_updates || 0);
        decisions += Number(activity.decision_count || 0);
        orders += Number(activity.order_count || 0);
        const wsAge = ageSec(activity.ws_last_ts);
        const indAge = ageSec(activity.indicator_last_ts);
        if ((wsAge !== null && wsAge < 4) && (indAge !== null && indAge < 4)) freshStreams += 1;
      });
      metricBar.innerHTML = `
        <div class="metric"><span class="k">Workers</span><span class="v">${keys.length}</span></div>
        <div class="metric"><span class="k">Fresh Streams</span><span class="v">${freshStreams}/${keys.length || 0}</span></div>
        <div class="metric"><span class="k">WS Ticks</span><span class="v">${wsTicks}</span></div>
        <div class="metric"><span class="k">Indicator Updates</span><span class="v">${indicatorUpdates}</span></div>
        <div class="metric"><span class="k">Decisions / Orders</span><span class="v">${decisions} / ${orders}</span></div>
      `;
    }

    function renderEarnings(earnings) {
      const totals = earnings.totals || {};
      const resolvedPnl = Number(totals.resolved_pnl_usdc || 0);
      const pnlClass = resolvedPnl > 0 ? "ok" : (resolvedPnl < 0 ? "bad" : "warn");
      earningsSummary.innerHTML = `
        <div class="metric"><span class="k">Resolved Wagered</span><span class="v">${fmt(totals.resolved_wagered_usdc || 0)}</span></div>
        <div class="metric"><span class="k">Resolved Returned</span><span class="v">${fmt(totals.resolved_returned_usdc || 0)}</span></div>
        <div class="metric"><span class="k">Resolved PnL</span><span class="v ${pnlClass}">${fmt(resolvedPnl)}</span></div>
        <div class="metric"><span class="k">Accurate / Inaccurate</span><span class="v">${totals.accurate_events || 0} / ${totals.inaccurate_events || 0}</span></div>
      `;

      const events = earnings.events || [];
      eventsBody.innerHTML = "";
      events.forEach((eventRow) => {
        const pnl = eventRow.pnl_usdc;
        const pnlNum = pnl === null || pnl === undefined ? null : Number(pnl);
        const pnlTone = pnlNum === null ? "warn" : (pnlNum > 0 ? "ok" : (pnlNum < 0 ? "bad" : "warn"));
        const status = eventRow.status || "-";
        const statusTone = status === "resolved" ? "ok" : (status === "running" ? "warn" : "warn");
        const accuracy = eventRow.was_prediction_accurate === true ? "YES" : (eventRow.was_prediction_accurate === false ? "NO" : "-");
        const winnerPred = `${eventRow.winning_side || "-"} / ${eventRow.predicted_side || "-"}`;
        const marketLabel = cleanMarketLabel(eventRow.market_key);
        const eventLabel = cleanEventLabel(eventRow.event_slug, eventRow.market_key);
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${marketLabel}</td>
          <td><div>${eventLabel.primary}</div><div class="mono-sm">${eventLabel.secondary}</div></td>
          <td class="${statusTone}">${status}</td>
          <td>${winnerPred}</td>
          <td>${accuracy}</td>
          <td>${fmt(eventRow.wagered_usdc || 0)}</td>
          <td>${fmt(eventRow.returned_usdc || 0)}</td>
          <td class="${pnlTone}">${pnlNum === null ? "-" : fmt(pnlNum)}</td>
        `;
        eventsBody.appendChild(tr);
      });
      if (events.length === 0) {
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="8" class="mono-sm">No event outcomes yet</td>';
        eventsBody.appendChild(tr);
      }

      const orders = earnings.orders || [];
      ordersBody.innerHTML = "";
      orders.forEach((order) => {
        const actionTone = String(order.action || "").startsWith("SELL") ? "warn" : "ok";
        const marketLabel = cleanMarketLabel(order.market_key);
        const eventLabel = cleanEventLabel(order.event_slug, order.market_key);
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${formatIsoTimestamp(order.ts)}</td>
          <td>${marketLabel}</td>
          <td><div>${eventLabel.primary}</div><div class="mono-sm">${eventLabel.secondary}</div></td>
          <td class="${actionTone}">${order.action || "-"}</td>
          <td>${fmt(order.price)} x ${fmt(order.shares)}</td>
          <td>${fmt(order.wager_usdc || 0)}</td>
          <td>${order.status || "-"}</td>
        `;
        ordersBody.appendChild(tr);
      });
      if (orders.length === 0) {
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="7" class="mono-sm">No order activity yet</td>';
        ordersBody.appendChild(tr);
      }

      earningsFooter.textContent = `Resolved=${totals.resolved_events || 0} | Pending=${totals.pending_events || 0} | Running=${totals.running_events || 0} | Gained=${fmt(totals.gained_usdc || 0)} | Lost=${fmt(totals.lost_usdc || 0)} | Total Wagered=${fmt(totals.total_wagered_usdc || 0)} | Orders=${totals.order_rows || 0}`;
    }

    function render(state) {
      const running = !!state.running;
      const paused = !!state.paused;
      const report = state.report_path ? ` report=${state.report_path}` : "";
      statusLine.className = running ? (paused ? "warn" : "ok") : "warn";
      statusLine.textContent = running ? (paused ? "Paused" : "Running") : "Stopped";
      statusLine.textContent += ` | completed_runs=${state.completed_runs || 0}${report}`;

      const workers = state.workers || {};
      renderMetrics(state);
      workersBody.innerHTML = "";
      Object.keys(workers).sort().forEach((key) => {
        const w = workers[key] || {};
        const activity = w.activity || {};
        const wsAge = ageSec(activity.ws_last_ts);
        const indicatorAge = ageSec(activity.indicator_last_ts);
        const decisionAge = ageSec(activity.decision_last_ts);
        const orderAge = ageSec(activity.last_order_ts);
        const indicators = activity.indicators || {};
        const ingestClass = freshnessClass(wsAge, 2.5, 5.0);
        const indClass = freshnessClass(indicatorAge, 2.5, 5.0);
        const decisionClass = freshnessClass(decisionAge, 4.0, 8.0);
        const statusClass = w.last_error ? "bad" : (w.status === "running" ? "ok" : "warn");
        const orderClass = freshnessClass(orderAge, 6.0, 12.0);
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>${key}</strong><div class="mono-sm">${w.event_slug || "-"}</div></td>
          <td class="${statusClass}">${w.status || "-"}<div class="mono-sm">done=${w.completed_events || 0}${w.last_error ? " err=" + w.last_error : ""}</div></td>
          <td class="${ingestClass}">
            ${activity.ws_ticks || 0} ticks<div class="mono-sm">${activity.ws_last_event_type || "-"} | ${wsAge === null ? "-" : wsAge.toFixed(1) + "s"}</div>
          </td>
          <td class="${indClass}">
            ${activity.indicator_updates || 0} updates<div class="mono-sm">mid=${fmt(indicators.mid_price)} vwap=${fmt(indicators.vwap_1m)} | ${indicatorAge === null ? "-" : indicatorAge.toFixed(1) + "s"}</div>
          </td>
          <td class="${decisionClass}">
            ${activity.decision_last_action || "WAIT"}<div class="mono-sm">n=${activity.decision_count || 0} c=${fmt(activity.decision_last_confidence, 2)} e=${fmt(activity.decision_last_edge, 2)} | ${decisionAge === null ? "-" : decisionAge.toFixed(1) + "s"}</div>
          </td>
          <td class="${orderClass}">
            ${activity.order_count || 0} / fills ${activity.fill_count || 0}<div class="mono-sm">${activity.last_order_action || "-"} ${activity.last_order_side || ""} @ ${fmt(activity.last_order_price)} x ${fmt(activity.last_order_shares)} | open=${activity.open_orders || 0}</div>
          </td>
        `;
        workersBody.appendChild(tr);
      });
      renderEarnings(state.earnings || {});

      rawState.textContent = JSON.stringify(state, null, 2);
    }

    async function post(path, body = {}) {
      const resp = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || `Request failed (${resp.status})`);
      }
      render(data);
    }

    async function fetchState() {
      const resp = await fetch("/api/state");
      render(await resp.json());
    }

    document.getElementById("startBtn").addEventListener("click", async () => {
      try {
        await post("/api/start", {
          markets: selectedMarkets(),
          duration_minutes: parseFloat(document.getElementById("duration").value || "60"),
          mode: document.getElementById("mode").value,
          status_interval_sec: 1.0
        });
      } catch (err) { alert(err.message); }
    });
    document.getElementById("pauseBtn").addEventListener("click", async () => { try { await post("/api/pause"); } catch (err) { alert(err.message); } });
    document.getElementById("resumeBtn").addEventListener("click", async () => { try { await post("/api/resume"); } catch (err) { alert(err.message); } });
    document.getElementById("stopBtn").addEventListener("click", async () => { try { await post("/api/stop"); } catch (err) { alert(err.message); } });

    mkMarketCheckboxes();
    fetchState();
    const stream = new EventSource("/api/stream");
    stream.onmessage = (evt) => {
      try { render(JSON.parse(evt.data)); } catch (_) {}
    };
  </script>
</body>
</html>
"""
