-- Migration 004: Row Level Security
-- All writes go through service_role (Edge Functions + pg_cron), which bypasses RLS.
-- anon and authenticated roles get read-only SELECT.

ALTER TABLE copy_pros.profiles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_pros.activity        ENABLE ROW LEVEL SECURITY;
ALTER TABLE copy_pros.market_outcomes ENABLE ROW LEVEL SECURITY;

-- Read policies (public read is intentional — this data is not PII-sensitive)
CREATE POLICY "allow_select_profiles"
  ON copy_pros.profiles
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

CREATE POLICY "allow_select_activity"
  ON copy_pros.activity
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

CREATE POLICY "allow_select_market_outcomes"
  ON copy_pros.market_outcomes
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

-- No INSERT/UPDATE/DELETE policies for anon/authenticated.
-- service_role bypasses RLS entirely — all writes happen via Edge Functions.
