-- Migration 009: Expose copy_pros schema to PostgREST
--
-- PostgREST only serves schemas listed in its db_schemas config.
-- Setting pgrst.db_schemas on the authenticator role is the SQL-level way
-- to add schemas without using the Supabase Dashboard (Settings → API → Exposed schemas).
-- The NOTIFY triggers an immediate PostgREST config reload.

ALTER ROLE authenticator SET pgrst.db_schemas TO 'public, copy_pros';
NOTIFY pgrst, 'reload config';
NOTIFY pgrst, 'reload schema';
