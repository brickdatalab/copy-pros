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
                async for raw in ws:
                    payload = json.loads(raw)
                    for event in normalize_ws_payload(payload):
                        yield event
        except (OSError, websockets.ConnectionClosed, asyncio.TimeoutError):
            await asyncio.sleep(1.0)
