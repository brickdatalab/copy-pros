"""Polymarket WebSocket market feed helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import websockets


CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def normalize_ws_payload(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def classify_trade_side(
    price: float,
    best_bid: float | None,
    best_ask: float | None,
    tolerance: float = 0.001,
) -> int:
    if best_ask is not None and best_ask > 0 and price >= best_ask * (1 - tolerance):
        return 1
    if best_bid is not None and best_bid > 0 and price <= best_bid * (1 + tolerance):
        return -1
    return 0


async def stream_market_events(asset_ids: list[str]) -> AsyncIterator[dict[str, object]]:
    if not asset_ids:
        return

    while True:
        try:
            async with websockets.connect(
                CLOB_WS,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=10,
            ) as ws:
                await ws.send(json.dumps({"assets_ids": asset_ids, "type": "market"}))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    payload = json.loads(raw)
                    for event in normalize_ws_payload(payload):
                        yield event
        except (
            OSError,
            websockets.ConnectionClosed,
            asyncio.TimeoutError,
            websockets.InvalidStatus,
            websockets.InvalidHandshake,
            json.JSONDecodeError,
        ):
            await asyncio.sleep(1.0)
        except Exception:
            # Never let the market stream task die silently; reconnect.
            await asyncio.sleep(1.0)
