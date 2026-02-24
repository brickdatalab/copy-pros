-- Migration 006: pg_cron jobs
--
-- Job 1 — fetch-activity: polls Polymarket CLOB for new trades every minute
-- Job 2 — resolve-outcomes: checks Gamma API for market resolutions at :02/:17/:32/:47
--
-- URL and service_role_key are read from copy_pros.config (see migration 008).
-- ALTER DATABASE SET requires superuser not available in Supabase managed Postgres.

-- Remove existing jobs if re-running migration
SELECT cron.unschedule('fetch-activity-every-1min')  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'fetch-activity-every-1min');
SELECT cron.unschedule('fetch-activity-every-2min')  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'fetch-activity-every-2min');
SELECT cron.unschedule('resolve-outcomes-15min')     WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'resolve-outcomes-15min');

-- Job 1: Activity polling every minute
SELECT cron.schedule(
  'fetch-activity-every-1min',
  '* * * * *',
  $$
    SELECT net.http_post(
      url     := (SELECT value FROM copy_pros.config WHERE key = 'supabase_url') || '/functions/v1/fetch-activity',
      headers := jsonb_build_object(
        'Content-Type',  'application/json',
        'Authorization', 'Bearer ' || (SELECT value FROM copy_pros.config WHERE key = 'service_role_key')
      ),
      body    := '{"trigger":"cron"}'::jsonb
    );
  $$
);

-- Job 2: Outcome resolution at :02, :17, :32, :47 of every hour
SELECT cron.schedule(
  'resolve-outcomes-15min',
  '2,17,32,47 * * * *',
  $$
    SELECT net.http_post(
      url     := (SELECT value FROM copy_pros.config WHERE key = 'supabase_url') || '/functions/v1/resolve-outcomes',
      headers := jsonb_build_object(
        'Content-Type',  'application/json',
        'Authorization', 'Bearer ' || (SELECT value FROM copy_pros.config WHERE key = 'service_role_key')
      ),
      body    := '{"trigger":"cron"}'::jsonb
    );
  $$
);
