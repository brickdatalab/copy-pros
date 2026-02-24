#!/usr/bin/env -S deno run --allow-env --allow-net --allow-read
/**
 * Inspect recent activity for a wallet address.
 *
 * Usage:
 *   deno run --allow-env --allow-net --allow-read scripts/check-activity.ts \
 *     --wallet 0x... [--limit 20]
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { parse }        from 'https://deno.land/std@0.208.0/flags/mod.ts';

const args  = parse(Deno.args, { string: ['wallet'], default: { limit: 20 } });
const wallet = args.wallet ?? args._[0]?.toString();

if (!wallet) {
  console.error('Usage: check-activity.ts --wallet 0x...');
  Deno.exit(1);
}

try {
  const env = await Deno.readTextFile('.env');
  for (const line of env.split('\n')) {
    const [k, ...vs] = line.split('=');
    if (k && vs.length) Deno.env.set(k.trim(), vs.join('=').trim());
  }
} catch { /* ignore */ }

const url = Deno.env.get('SUPABASE_URL') ?? 'https://cxvntzszdkyggjjenefn.supabase.co';
const key = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;

const supabase = createClient(url, key, {
  db:   { schema: 'copy_pros' },
  auth: { persistSession: false, autoRefreshToken: false },
});

// Look up the profile
const { data: profile, error: pErr } = await supabase
  .from('profiles')
  .select('*')
  .ilike('proxy_wallet_address', wallet)
  .single();

if (pErr || !profile) {
  console.error(`Profile not found for wallet ${wallet}`);
  Deno.exit(1);
}

console.log(`\n📋 Profile: ${profile.display_name ?? wallet}`);
console.log(`   Active:      ${profile.is_active}`);
console.log(`   Tracking from: ${profile.tracking_start_at}`);
console.log(`   Last polled:   ${profile.last_polled_at ?? 'never'}\n`);

// Fetch recent activity
const { data: trades, error: tErr } = await supabase
  .from('activity')
  .select('*, market_outcomes!condition_id(winning_outcome, is_resolved)')
  .eq('profile_id', profile.id)
  .order('event_timestamp', { ascending: false })
  .limit(Number(args.limit));

if (tErr) { console.error(tErr.message); Deno.exit(1); }

if (!trades || trades.length === 0) {
  console.log('No activity recorded yet.');
  Deno.exit(0);
}

console.log(`Recent ${trades.length} trade(s):\n`);
for (const t of trades) {
  const resolved = t.market_outcomes?.is_resolved ? ` → WON: ${t.market_outcomes.winning_outcome}` : '';
  console.log(
    `  ${t.event_timestamp.slice(0,19)}  ${t.side.padEnd(4)} ${t.outcome.padEnd(3)}  ` +
    `$${t.usdc_amount.toFixed(2).padStart(10)} @ ${(t.price * 100).toFixed(1)}%  ` +
    `[${(t.market_slug ?? t.condition_id.slice(0,8)).slice(0,30)}]${resolved}`
  );
}
