-- Migration 005: Profile insert trigger
-- When a profile is inserted, immediately invoke the fetch-activity Edge Function
-- via pg_net so data collection starts right away (without waiting for the next cron cycle).
--
-- URL and service_role_key are read from copy_pros.config (see migration 008).
-- ALTER DATABASE SET requires superuser not available in Supabase managed Postgres.

CREATE OR REPLACE FUNCTION copy_pros.trigger_on_profile_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = copy_pros, public
AS $$
DECLARE
  _url TEXT;
  _key TEXT;
BEGIN
  SELECT value INTO _url FROM copy_pros.config WHERE key = 'supabase_url';
  SELECT value INTO _key FROM copy_pros.config WHERE key = 'service_role_key';

  IF _url IS NULL OR _key IS NULL THEN
    RAISE WARNING 'copy_pros trigger: supabase_url or service_role_key missing from copy_pros.config — skipping immediate fetch for profile %', NEW.id;
    RETURN NEW;
  END IF;

  PERFORM net.http_post(
    url     := _url || '/functions/v1/fetch-activity',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'Authorization', 'Bearer ' || _key
    ),
    body    := jsonb_build_object(
      'trigger',    'profile_insert',
      'profile_id', NEW.id::text
    )
  );

  RETURN NEW;
END;
$$;

CREATE TRIGGER after_profile_insert
  AFTER INSERT ON copy_pros.profiles
  FOR EACH ROW
  EXECUTE FUNCTION copy_pros.trigger_on_profile_insert();
