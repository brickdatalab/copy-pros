/**
 * Polymarket CLOB + Gamma API client
 * Uses L2 (API key) authentication for CLOB private endpoints.
 */

const CLOB_BASE  = 'https://clob.polymarket.com';
const GAMMA_BASE = 'https://gamma-api.polymarket.com';

// ─── Auth ────────────────────────────────────────────────────────────────────

async function buildL2Headers(method: string, path: string): Promise<Record<string, string>> {
  const apiKey    = Deno.env.get('CLOB_API_KEY')!;
  const secret    = Deno.env.get('CLOB_SECRET')!;
  const passphrase = Deno.env.get('CLOB_PASS_PHRASE')!;

  const ts    = Math.floor(Date.now() / 1000);
  const nonce = 0;
  const msg   = `${ts}${nonce}${method}${path}`;

  // CLOB_SECRET is base64-encoded; decode before using as HMAC key
  const rawSecret  = Uint8Array.from(atob(secret), (c) => c.charCodeAt(0));
  const cryptoKey  = await crypto.subtle.importKey(
    'raw', rawSecret,
    { name: 'HMAC', hash: 'SHA-256' },
    false, ['sign']
  );
  const sigBytes   = await crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(msg));
  const signature  = btoa(String.fromCharCode(...new Uint8Array(sigBytes)));

  return {
    'POLY_ADDRESS':    apiKey,
    'POLY_SIGNATURE':  signature,
    'POLY_TIMESTAMP':  String(ts),
    'POLY_NONCE':      String(nonce),
    'POLY_PASSPHRASE': passphrase,
    'Content-Type':    'application/json',
  };
}

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ClobTrade {
  id:               string;
  market:           string;   // condition_id
  asset_id:         string;   // YES/NO token ID
  outcome:          string;   // "Yes" | "No"
  side:             string;   // "BUY" | "SELL"
  size:             string;   // shares (string from API)
  price:            string;   // price per share (string from API)
  match_time:       string;   // Unix seconds or ms (string)
  transaction_hash: string | null;
  maker_address:    string;
  status:           string;
}

export interface MarketDetail {
  conditionId: string;
  slug:        string | null;
  eventSlug:   string | null;
  question:    string | null;
  resolved:    boolean;
  resolvedAt:  string | null;
  outcomePrices: string[] | null; // e.g. ["1","0"] — index 0 = YES
}

// ─── CLOB — fetch trades ──────────────────────────────────────────────────────

/**
 * Fetch ALL trades for a maker address since `afterUnixSeconds`.
 * Handles pagination automatically via next_cursor.
 * Queries both maker_address and taker_address to capture all activity.
 */
export async function fetchTradesForWallet(
  walletAddress: string,
  afterUnixSeconds: number,
): Promise<ClobTrade[]> {
  const makerTrades  = await fetchTradesByParam('maker_address', walletAddress, afterUnixSeconds);
  const takerTrades  = await fetchTradesByParam('taker_address', walletAddress, afterUnixSeconds);

  // Merge and deduplicate by trade id
  const seen  = new Set<string>();
  const all: ClobTrade[] = [];
  for (const trade of [...makerTrades, ...takerTrades]) {
    if (!seen.has(trade.id)) {
      seen.add(trade.id);
      all.push(trade);
    }
  }
  return all;
}

async function fetchTradesByParam(
  param: 'maker_address' | 'taker_address',
  address: string,
  afterUnixSeconds: number,
): Promise<ClobTrade[]> {
  const trades: ClobTrade[] = [];
  let   cursor: string | null = null;

  while (true) {
    const path = buildTradePath(param, address, afterUnixSeconds, cursor);
    const headers = await buildL2Headers('GET', path);

    const resp = await fetch(`${CLOB_BASE}${path}`, { headers });
    if (!resp.ok) {
      console.error(`CLOB ${param} ${resp.status}: ${await resp.text()}`);
      break;
    }

    const json = await resp.json() as { data?: ClobTrade[]; next_cursor?: string };
    const batch = json.data ?? [];
    trades.push(...batch);

    // No more pages
    if (!json.next_cursor || json.next_cursor === 'LTE=' || batch.length === 0) break;
    cursor = json.next_cursor;
  }

  return trades;
}

function buildTradePath(
  param: string,
  address: string,
  after: number,
  cursor: string | null,
): string {
  const params = new URLSearchParams({
    [param]: address.toLowerCase(),
    after:   String(after),
    limit:   '500',
  });
  if (cursor) params.set('next_cursor', cursor);
  return `/data/trades?${params}`;
}

// ─── Gamma — market metadata ──────────────────────────────────────────────────

/**
 * Fetch market metadata for a batch of condition IDs.
 * Returns a map: condition_id → MarketDetail.
 */
export async function fetchMarketDetailsBatch(
  conditionIds: string[],
): Promise<Map<string, MarketDetail>> {
  const result = new Map<string, MarketDetail>();
  if (conditionIds.length === 0) return result;

  // Gamma supports conditionIds as comma-separated in a single request
  const ids    = conditionIds.join(',');
  const url    = `${GAMMA_BASE}/markets?conditionIds=${encodeURIComponent(ids)}`;
  const resp   = await fetch(url, { headers: { 'Accept': 'application/json' } });

  if (!resp.ok) {
    console.error(`Gamma API ${resp.status}: ${await resp.text()}`);
    return result;
  }

  const markets = await resp.json() as Array<Record<string, unknown>>;

  for (const m of markets) {
    const condId  = (m.conditionId ?? m.condition_id) as string | undefined;
    if (!condId) continue;

    // Parse outcomePrices — may be a JSON string or an array
    let outcomePrices: string[] | null = null;
    if (m.outcomePrices) {
      outcomePrices = typeof m.outcomePrices === 'string'
        ? JSON.parse(m.outcomePrices)
        : m.outcomePrices as string[];
    }

    // Event slug: first event in the events array, or groupSlug
    const events   = m.events as Array<{ slug?: string }> | undefined;
    const eventSlug = events?.[0]?.slug ?? (m.groupSlug as string | null) ?? null;

    result.set(condId, {
      conditionId:   condId,
      slug:          (m.slug as string | null) ?? null,
      eventSlug,
      question:      (m.question as string | null) ?? null,
      resolved:      Boolean(m.closed ?? m.resolved),
      resolvedAt:    (m.resolutionTime as string | null) ?? (m.resolvedAt as string | null) ?? null,
      outcomePrices,
    });
  }

  return result;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Safely parse a Polymarket timestamp (seconds or milliseconds, string or number)
 * into an ISO-8601 string.
 */
export function parsePolyTimestamp(ts: string | number): string {
  const n  = typeof ts === 'string' ? parseFloat(ts) : ts;
  // Heuristic: > 1e12 → milliseconds, otherwise → seconds
  const ms = n > 1_000_000_000_000 ? n : n * 1000;
  return new Date(ms).toISOString();
}

/**
 * Determine the winning outcome from Gamma outcomePrices array.
 * Polymarket convention: index 0 = YES, index 1 = NO.
 * A price of "1" means that outcome won.
 */
export function resolveWinningOutcome(outcomePrices: string[]): 'YES' | 'NO' | null {
  if (outcomePrices[0] === '1') return 'YES';
  if (outcomePrices[1] === '1') return 'NO';
  return null; // not yet resolved, or ambiguous
}
