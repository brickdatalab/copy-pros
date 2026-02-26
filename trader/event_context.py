"""Event lifecycle context and timing helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventContext:
    event_slug: str
    event_id: str
    timeframe_minutes: int
    started_at_ts: int
    ends_at_ts: int
    now_ts: int
    remaining_sec: int
    is_closed: bool


def infer_timeframe_minutes(event_slug: str, default: int = 15) -> int:
    slug = event_slug.lower()
    if "15m" in slug:
        return 15
    if "5m" in slug:
        return 5
    return default


def build_event_context(
    event_slug: str,
    event_id: str,
    started_at_ts: int,
    duration_sec: int,
    now_ts: int,
) -> EventContext:
    ends_at_ts = started_at_ts + duration_sec
    remaining = max(0, ends_at_ts - now_ts)
    return EventContext(
        event_slug=event_slug,
        event_id=event_id,
        timeframe_minutes=max(1, duration_sec // 60),
        started_at_ts=started_at_ts,
        ends_at_ts=ends_at_ts,
        now_ts=now_ts,
        remaining_sec=remaining,
        is_closed=remaining == 0,
    )
