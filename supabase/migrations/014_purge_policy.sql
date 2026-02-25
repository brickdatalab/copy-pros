-- Migration 014: Auto-purge market data 48h after market resolution
--
-- Runs every 4 hours via pg_cron.
-- Deletes market_ticks, market_snapshots, and indicators for any market
-- that resolved more than 48 hours ago.
-- events and markets rows are kept (lightweight metadata).

SELECT cron.unschedule('purge-market-data')
WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'purge-market-data');

SELECT cron.schedule(
  'purge-market-data',
  '0 */4 * * *',
  $cron$
    DO $body$
    DECLARE
      purge_before timestamptz := NOW() - INTERVAL '48 hours';
    BEGIN
      -- Purge raw ticks
      DELETE FROM copy_pros.market_ticks
      WHERE market_id IN (
        SELECT id FROM copy_pros.markets
        WHERE resolved_at IS NOT NULL
          AND resolved_at < purge_before
      );

      -- Purge 1-second snapshots
      DELETE FROM copy_pros.market_snapshots
      WHERE market_id IN (
        SELECT id FROM copy_pros.markets
        WHERE resolved_at IS NOT NULL
          AND resolved_at < purge_before
      );

      -- Purge indicators
      DELETE FROM copy_pros.indicators
      WHERE market_id IN (
        SELECT id FROM copy_pros.markets
        WHERE resolved_at IS NOT NULL
          AND resolved_at < purge_before
      );
    END;
    $body$;
  $cron$
);
