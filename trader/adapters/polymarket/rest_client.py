"""Polymarket REST utilities for event metadata discovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlparse

import httpx

from trader.event_context import infer_timeframe_minutes


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


@dataclass(frozen=True)
class EventMarketContext:
    event_slug: str
    event_id: str
    title: str
    condition_id: str
    token_up: str
    token_down: str
    start_ts: int
    end_ts: int
    timeframe_minutes: int

    @property
    def duration_sec(self) -> int:
        return max(0, self.end_ts - self.start_ts)


def extract_slug(arg: str) -> str:
    if arg.startswith("http"):
        path = urlparse(arg).path
        parts = [p for p in path.split("/") if p]
        if "event" in parts:
            idx = parts.index("event")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        if parts:
            return parts[-1]
    return arg


def _parse_ts(value: str | None) -> int | None:
    if not value:
        return None
    try:
        # Gamma timestamps are usually ISO8601 with Z suffix.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return None


def _infer_start_from_slug(slug: str, timeframe_minutes: int) -> int | None:
    match = re.search(r"(\d{10})$", slug)
    if not match:
        return None
    start = int(match.group(1))
    return start - (timeframe_minutes * 60)


def _resolve_tokens(tokens: list[dict[str, object]]) -> tuple[str, str]:
    up: str | None = None
    down: str | None = None

    for token in tokens:
        outcome = str(token.get("outcome", "")).upper()
        token_id = str(token.get("token_id", ""))
        if not token_id:
            continue
        if outcome in {"UP", "YES"}:
            up = token_id
        elif outcome in {"DOWN", "NO"}:
            down = token_id

    if up and down:
        return up, down

    token_ids = [str(t.get("token_id", "")) for t in tokens if t.get("token_id")]
    if len(token_ids) >= 2:
        return token_ids[0], token_ids[1]

    raise ValueError("Unable to resolve Up/Down token IDs")


async def fetch_event_market_context(event_input: str) -> EventMarketContext:
    slug = extract_slug(event_input)

    async with httpx.AsyncClient(timeout=10) as client:
        event_resp = await client.get(f"{GAMMA_BASE}/events", params={"slug": slug})
        event_resp.raise_for_status()
        events = event_resp.json()
        if not events:
            raise ValueError(f"No event found for slug: {slug}")

        event = events[0]
        markets = event.get("markets") or []
        if not markets:
            raise ValueError(f"No markets attached to event: {slug}")

        market = markets[0]
        condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
        if not condition_id:
            raise ValueError(f"Missing condition id for event: {slug}")

        market_resp = await client.get(f"{CLOB_BASE}/markets/{condition_id}")
        market_resp.raise_for_status()
        market_json = market_resp.json()

    tokens = market_json.get("tokens") or []
    token_up, token_down = _resolve_tokens(tokens)

    timeframe = infer_timeframe_minutes(slug)

    end_iso = market.get("endDate") or market.get("end_date") or market_json.get("end_date_iso")
    start_iso = market.get("startDate") or market.get("start_date") or market_json.get("start_date_iso")

    end_ts = _parse_ts(str(end_iso) if end_iso else None)
    start_ts = _parse_ts(str(start_iso) if start_iso else None)

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if end_ts is None and start_ts is not None:
        end_ts = start_ts + timeframe * 60
    if start_ts is None and end_ts is not None:
        start_ts = end_ts - timeframe * 60
    if start_ts is None:
        from_slug = _infer_start_from_slug(slug, timeframe)
        start_ts = from_slug if from_slug is not None else now_ts
    if end_ts is None:
        end_ts = start_ts + timeframe * 60

    return EventMarketContext(
        event_slug=slug,
        event_id=str(event.get("id") or event.get("slug") or slug),
        title=str(event.get("title") or event.get("name") or slug),
        condition_id=condition_id,
        token_up=token_up,
        token_down=token_down,
        start_ts=start_ts,
        end_ts=end_ts,
        timeframe_minutes=timeframe,
    )


async def fetch_winning_side(condition_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=10) as client:
        market_resp = await client.get(f"{CLOB_BASE}/markets/{condition_id}")
        market_resp.raise_for_status()
        market_json = market_resp.json()

    tokens = market_json.get("tokens") or []
    for token in tokens:
        if token.get("winner"):
            outcome = str(token.get("outcome", "")).upper()
            if outcome in {"UP", "YES"}:
                return "UP"
            if outcome in {"DOWN", "NO"}:
                return "DOWN"
    return None


def serialize_context(ctx: EventMarketContext) -> str:
    return json.dumps(
        {
            "event_slug": ctx.event_slug,
            "event_id": ctx.event_id,
            "title": ctx.title,
            "condition_id": ctx.condition_id,
            "token_up": ctx.token_up,
            "token_down": ctx.token_down,
            "start_ts": ctx.start_ts,
            "end_ts": ctx.end_ts,
            "timeframe_minutes": ctx.timeframe_minutes,
        }
    )
