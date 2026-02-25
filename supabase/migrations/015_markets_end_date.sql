-- Migration 015: Add end_date to copy_pros.markets
--
-- Stores the scheduled close time from Polymarket's Gamma API (endDate field).
-- Used by stream_market.py resolution_loop() to know when to poll for resolution.
-- Without this, the 48h purge policy (migration 014) never fires because
-- resolved_at stays NULL indefinitely.

ALTER TABLE copy_pros.markets ADD COLUMN IF NOT EXISTS end_date timestamptz;

-- Backfill currently registered markets where we know end_date from the slug
-- (eth-updown-5m slugs encode the start timestamp; end_date = start + 300s)
-- This is a best-effort backfill; register_event.py sets it correctly going forward.
UPDATE copy_pros.markets m
SET end_date = to_timestamp(
    (regexp_match(e.event_slug, '\d+$'))[1]::bigint + 300
)
FROM copy_pros.events e
WHERE m.event_id = e.id
  AND e.event_slug ~ '\d+$'
  AND m.end_date IS NULL;
