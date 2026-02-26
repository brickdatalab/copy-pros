"""Buffered async writer for Supabase tracking tables."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from typing import Any

import httpx


@dataclass
class BufferedSupabaseWriter:
    enabled: bool = True
    schema: str = "copy_pros"
    timeout_sec: float = 0.25
    _buffer: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        self._url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self._service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    def buffered_events(self) -> int:
        return sum(len(rows) for rows in self._buffer.values())

    async def enqueue(self, table: str, row: dict[str, Any]) -> None:
        self._buffer[table].append(row)

    async def enqueue_runtime_event(
        self,
        run_id: str,
        level: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self.enqueue(
            "bot_runtime_events",
            {
                "run_id": run_id,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "level": level,
                "event_type": event_type,
                "payload": payload,
            },
        )

    async def create_run(self, row: dict[str, Any]) -> None:
        await self.enqueue("bot_runs", row)

    async def complete_run(self, run_id: str, updates: dict[str, Any]) -> None:
        updates = dict(updates)
        updates["id"] = run_id
        updates["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        await self.enqueue("bot_runs_updates", updates)

    async def flush(self) -> None:
        if not self.enabled or not self._url or not self._service_key:
            return

        headers = {
            "Content-Type": "application/json",
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
            "Accept-Profile": self.schema,
            "Content-Profile": self.schema,
            "Prefer": "return=minimal",
        }

        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            # bot_runs_updates uses PATCH, all others use POST inserts.
            for table in list(self._buffer.keys()):
                rows = self._buffer.get(table, [])
                if not rows:
                    continue

                try:
                    if table == "bot_runs_updates":
                        for row in rows:
                            run_id = row["id"]
                            body = {k: v for k, v in row.items() if k != "id"}
                            endpoint = f"{self._url}/rest/v1/bot_runs?id=eq.{run_id}"
                            response = await client.patch(endpoint, headers=headers, json=body)
                            response.raise_for_status()
                    else:
                        endpoint = f"{self._url}/rest/v1/{table}"
                        response = await client.post(endpoint, headers=headers, json=rows)
                        response.raise_for_status()
                except Exception:
                    # Keep rows buffered; retry later.
                    continue

                self._buffer[table].clear()
