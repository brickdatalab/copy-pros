/**
 * fetch-activity — Polymarket activity ingestion
 *
 * Called by:
 *   • AFTER INSERT trigger on copy_pros.profiles (immediate kick-off for new profile)
 *   • pg_cron every 2 minutes (all active profiles)
 *
 * Flow per profile:
 *   1. Determine "since" timestamp (last_polled_at ?? tracking_start_at)
 *   2. Fetch all trades from Polymarket CLOB since that timestamp (paginated)
 *   3. Enrich condition_ids with market/event slug via Gamma API
 *   4. UPSERT into copy_pros.activity (conflict on trade_id → ignore)
 *   5. Seed copy_pros.market_outcomes for any new condition_ids (is_resolved=false)
 *   6. Update profiles.last_polled_at
 */

import 'jsr:@supabase/functions-js/edge-runtime.d.ts';

import {
  fetchTradesForWallet,
  fetchMarketDetailsBatch,
  parsePolyTimestamp,
} from '../_shared/polymarket.ts';
import { createAdminClient } from '../_shared/supabase.ts';

// ─── Handler ─────────────────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  try {
    const body       = await req.json().catch(() => ({})) as Record<string, unknown>;
    const profileId  = typeof body.profile_id === 'string' ? body.profile_id : null;

    const supabase   = createAdminClient();

    // Fetch target profiles: either a single one (trigger) or all active (cron)
    let query = supabase.from('profiles').select('*').eq('is_active', true);
    if (profileId) query = query.eq('id', profileId);

    const { data: profiles, error: profilesErr } = await query;
    if (profilesErr) throw new Error(`Failed to load profiles: ${profilesErr.message}`);
    if (!profiles || profiles.length === 0) {
      return json({ ok: true, message: 'No active profiles to process' });
    }

    const results: Array<{ profile_id: string; trades_inserted: number; error?: string }> = [];

    for (const profile of profiles) {
      try {
        const count = await processProfile(supabase, profile);
        results.push({ profile_id: profile.id, trades_inserted: count });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`Profile ${profile.id} failed:`, msg);
        results.push({ profile_id: profile.id, trades_inserted: 0, error: msg });
      }
    }

    return json({ ok: true, processed: results.length, results });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('fetch-activity fatal error:', msg);
    return json({ ok: false, error: msg }, 500);
  }
});

// ─── Core logic ──────────────────────────────────────────────────────────────

async function processProfile(
  // deno-lint-ignore no-explicit-any
  supabase: any,
  // deno-lint-ignore no-explicit-any
  profile: Record<string, any>,
): Promise<number> {
  const since     = profile.last_polled_at ?? profile.tracking_start_at;
  const afterSecs = Math.floor(new Date(since).getTime() / 1000);
  const nowIso    = new Date().toISOString();

  console.log(`Fetching trades for ${profile.proxy_wallet_address} since ${since}`);

  const trades = await fetchTradesForWallet(profile.proxy_wallet_address, afterSecs);
  console.log(`  → ${trades.length} trade(s) returned from CLOB`);

  // Always advance last_polled_at, even if no new trades
  await supabase
    .from('profiles')
    .update({ last_polled_at: nowIso })
    .eq('id', profile.id);

  if (trades.length === 0) return 0;

  // Collect unique condition_ids for Gamma enrichment
  const conditionIds  = [...new Set(trades.map((t) => t.market).filter(Boolean))];
  const marketDetails = await fetchMarketDetailsBatch(conditionIds);

  // Build activity rows
  const activityRows = trades.map((trade) => {
    const md           = marketDetails.get(trade.market);
    const priceNum     = parseFloat(trade.price)  || 0;
    const sharesNum    = parseFloat(trade.size)   || 0;

    return {
      profile_id:       profile.id,
      trade_id:         trade.id,
      condition_id:     trade.market,
      market_slug:      md?.slug        ?? null,
      event_slug:       md?.eventSlug   ?? null,
      question:         md?.question    ?? null,
      outcome:          trade.outcome.toUpperCase() as 'YES' | 'NO',
      side:             trade.side.toUpperCase() as 'BUY' | 'SELL',
      price:            priceNum,
      shares:           sharesNum,
      usdc_amount:      Math.round(priceNum * sharesNum * 1_000_000) / 1_000_000,
      transaction_hash: trade.transaction_hash ?? null,
      event_timestamp:  parsePolyTimestamp(trade.match_time),
    };
  });

  // UPSERT — on conflict (trade_id) do nothing
  const { error: actErr } = await supabase
    .from('activity')
    .upsert(activityRows, { onConflict: 'trade_id', ignoreDuplicates: true });

  if (actErr) throw new Error(`activity upsert failed: ${actErr.message}`);

  // Seed market_outcomes for any new condition_ids (is_resolved starts false)
  const outcomeSeeds = conditionIds.map((condId) => {
    const md = marketDetails.get(condId);
    return {
      condition_id:    condId,
      market_slug:     md?.slug       ?? null,
      event_slug:      md?.eventSlug  ?? null,
      question:        md?.question   ?? null,
      is_resolved:     false,
      last_checked_at: nowIso,
    };
  });

  const { error: seedErr } = await supabase
    .from('market_outcomes')
    .upsert(outcomeSeeds, { onConflict: 'condition_id', ignoreDuplicates: true });

  if (seedErr) console.warn(`market_outcomes seed warning: ${seedErr.message}`);

  const inserted = activityRows.length;
  console.log(`  → ${inserted} row(s) upserted into activity`);
  return inserted;
}

// ─── Util ─────────────────────────────────────────────────────────────────────

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
