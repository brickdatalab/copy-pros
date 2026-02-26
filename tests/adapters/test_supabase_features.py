import pytest

from trader.adapters.supabase.writer import BufferedSupabaseWriter


@pytest.mark.asyncio
async def test_writer_can_buffer_without_client() -> None:
    writer = BufferedSupabaseWriter(enabled=False)
    await writer.enqueue_runtime_event("run-1", "info", "test", {"ok": True})
    assert writer.buffered_events() == 1
