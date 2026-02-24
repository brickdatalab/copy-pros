import { createClient, SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';

/**
 * Returns a Supabase service-role client scoped to the copy_pros schema.
 * Uses the system-injected SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars.
 */
export function createAdminClient(): SupabaseClient {
  const url = Deno.env.get('SUPABASE_URL');
  const key = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');

  if (!url || !key) {
    throw new Error('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set');
  }

  return createClient(url, key, {
    db:   { schema: 'copy_pros' },
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
