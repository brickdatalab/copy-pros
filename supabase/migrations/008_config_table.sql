-- Migration 008: App config table
-- Stores runtime configuration (supabase_url, service_role_key) used by the
-- profile insert trigger and pg_cron jobs to call Edge Functions.
--
-- Supabase restricts ALTER DATABASE SET to superuser — this table is the alternative.
-- anon/authenticated have NO access to this table.

CREATE TABLE IF NOT EXISTS copy_pros.config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Restrict access: only service_role (and postgres) can read
REVOKE ALL ON copy_pros.config FROM anon, authenticated;
GRANT SELECT ON copy_pros.config TO service_role;

-- Seed values (replace service_role_key with your actual key if re-running)
INSERT INTO copy_pros.config (key, value) VALUES
  ('supabase_url',     'https://cxvntzszdkyggjjenefn.supabase.co'),
  ('service_role_key', '<YOUR_SERVICE_ROLE_KEY>')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
