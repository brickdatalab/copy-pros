#!/usr/bin/env -S deno run --allow-env --allow-net --allow-read
/**
 * Add a Polymarket proxy wallet to copy_pros.profiles.
 * The INSERT fires the DB trigger which immediately kicks off activity collection.
 *
 * Usage:
 *   deno run --allow-env --allow-net --allow-read scripts/add-profile.ts \
 *     --wallet 0xYourWalletAddress \
 *     [--name "Display Name"]
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { parse } from 'https://deno.land/std@0.208.0/flags/mod.ts';

const args = parse(Deno.args, {
  string:  ['wallet', 'name'],
  alias:   { w: 'wallet', n: 'name' },
});

const wallet = args.wallet ?? args._[0]?.toString();
if (!wallet) {
  console.error('Usage: add-profile.ts --wallet 0x...');
  Deno.exit(1);
}

// Load env from .env file if present
try {
  const env = await Deno.readTextFile('.env');
  for (const line of env.split('\n')) {
    const [k, ...vs] = line.split('=');
    if (k && vs.length) Deno.env.set(k.trim(), vs.join('=').trim());
  }
} catch { /* no .env — rely on system env */ }

const supabaseUrl = Deno.env.get('SUPABASE_URL') ?? 'https://cxvntzszdkyggjjenefn.supabase.co';
const serviceKey  = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
if (!serviceKey) {
  console.error('SUPABASE_SERVICE_ROLE_KEY not set');
  Deno.exit(1);
}

const supabase = createClient(supabaseUrl, serviceKey, {
  db:   { schema: 'copy_pros' },
  auth: { persistSession: false, autoRefreshToken: false },
});

const { data, error } = await supabase
  .from('profiles')
  .insert({
    proxy_wallet_address: wallet.toLowerCase(),
    display_name:         args.name ?? null,
  })
  .select()
  .single();

if (error) {
  console.error('Failed to insert profile:', error.message);
  Deno.exit(1);
}

console.log('✅ Profile created:');
console.log(`   ID:      ${data.id}`);
console.log(`   Wallet:  ${data.proxy_wallet_address}`);
console.log(`   Name:    ${data.display_name ?? '(none)'}`);
console.log(`   Tracking from: ${data.tracking_start_at}`);
console.log('');
console.log('The DB trigger has fired — fetch-activity is now running for this profile.');
console.log('Activity will continue to be collected every 2 minutes via pg_cron.');
