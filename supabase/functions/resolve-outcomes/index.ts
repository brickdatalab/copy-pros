/**
 * resolve-outcomes — Market outcome resolution
 *
 * Called by pg_cron at :02, :17, :32, :47 of every hour.
 *
 * Flow:
 *   1. Query distinct condition_ids from activity that are not yet resolved
 *   2. Fetch market details from Gamma API
 *   3. UPSERT into market_outcomes:
 *      - If resolved: set winning_outcome, resolved_at, is_resolved=true
 *      - If not resolved: update last_checked_at only
 */

import 'jsr:@supabase/functions-js/edge-runtime.d.ts';

import {
  fetchMarketDetailsBatch,
  resolveWinningOutcome,
} from '../_shared/polymarket.ts';
import { createAdminClient } from '../_shared/supabase.ts';

Deno.serve(async (_req: Request) => {
  try {
    const supabase = createAdminClient();
    const nowIso   = new Date().toISOString();

    // Step 1: Find condition_ids that need checking
    // (in activity but not resolved in market_outcomes)
    const { data: unresolved, error: qErr } = await supabase
      .from('market_outcomes')
      .select('condition_id')
      .eq('is_resolved', false);

    if (qErr) throw new Error(`market_outcomes query failed: ${qErr.message}`);

    // Also find condition_ids in activity that aren't in market_outcomes yet
    const { data: activityMarkets, error: aErr } = await supabase
      .from('activity')
      .select('condition_id')
      .not('condition_id', 'is', null);

    if (aErr) throw new Error(`activity query failed: ${aErr.message}`);

    const knownIds   = new Set((unresolved ?? []).map((r: { condition_id: string }) => r.condition_id));
    const activityIds = [...new Set((activityMarkets ?? []).map((r: { condition_id: string }) => r.condition_id))];

    // All condition_ids to check: unresolved ones + any in activity not yet in market_outcomes
    const toCheck = [
      ...new Set([
        ...(unresolved ?? []).map((r: { condition_id: string }) => r.condition_id),
        ...activityIds.filter((id: string) => !knownIds.has(id)),
      ])
    ];

    if (toCheck.length === 0) {
      return json({ ok: true, message: 'No markets to resolve', checked: 0 });
    }

    console.log(`Checking ${toCheck.length} markets for resolution...`);

    // Step 2: Fetch from Gamma API
    const marketDetails = await fetchMarketDetailsBatch(toCheck);

    let resolved   = 0;
    let unresolved_ = 0;

    const upsertRows = [];

    for (const condId of toCheck) {
      const md = marketDetails.get(condId);

      if (!md) {
        // Gamma returned nothing for this ID — update last_checked_at
        upsertRows.push({
          condition_id:    condId,
          is_resolved:     false,
          last_checked_at: nowIso,
        });
        unresolved_++;
        continue;
      }

      const winningOutcome = md.outcomePrices
        ? resolveWinningOutcome(md.outcomePrices)
        : null;

      const isResolved = md.resolved && winningOutcome !== null;

      if (isResolved) {
        resolved++;
        console.log(`  ✓ Resolved: ${condId} → ${winningOutcome}`);
      } else {
        unresolved_++;
      }

      upsertRows.push({
        condition_id:    condId,
        market_slug:     md.slug       ?? null,
        event_slug:      md.eventSlug  ?? null,
        question:        md.question   ?? null,
        winning_outcome: isResolved ? winningOutcome : null,
        is_resolved:     isResolved,
        resolved_at:     isResolved ? (md.resolvedAt ?? nowIso) : null,
        last_checked_at: nowIso,
      });
    }

    // Step 3: Batch upsert
    if (upsertRows.length > 0) {
      const { error: upsertErr } = await supabase
        .from('market_outcomes')
        .upsert(upsertRows, { onConflict: 'condition_id' });

      if (upsertErr) throw new Error(`market_outcomes upsert failed: ${upsertErr.message}`);
    }

    return json({
      ok:          true,
      checked:     toCheck.length,
      resolved,
      still_open:  unresolved_,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error('resolve-outcomes error:', msg);
    return json({ ok: false, error: msg }, 500);
  }
});

// ─── Util ────────────────────────────────────────────────────────────────────

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
