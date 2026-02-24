-- Migration 007: Expose copy_pros schema to PostgREST public API
--
-- NOTE: In addition to these SQL grants, you must add "copy_pros" to
-- Settings → API → Extra search path in the Supabase Dashboard.
-- Without that setting, PostgREST will not serve this schema.

-- Grant schema-level access
GRANT USAGE ON SCHEMA copy_pros TO anon, authenticated, service_role;

-- Grant read access on all current tables
GRANT SELECT ON copy_pros.profiles        TO anon, authenticated;
GRANT SELECT ON copy_pros.activity        TO anon, authenticated;
GRANT SELECT ON copy_pros.market_outcomes TO anon, authenticated;

-- Grant full write access to service_role (used by Edge Functions)
GRANT ALL ON copy_pros.profiles        TO service_role;
GRANT ALL ON copy_pros.activity        TO service_role;
GRANT ALL ON copy_pros.market_outcomes TO service_role;

-- Grant access to sequences (needed for IDENTITY columns)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA copy_pros TO service_role;

-- Ensure future tables follow the same pattern
ALTER DEFAULT PRIVILEGES IN SCHEMA copy_pros
  GRANT SELECT ON TABLES TO anon, authenticated;

ALTER DEFAULT PRIVILEGES IN SCHEMA copy_pros
  GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA copy_pros
  GRANT USAGE, SELECT ON SEQUENCES TO service_role;
