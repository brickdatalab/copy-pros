/**
 * Polymarket CLOB + Gamma API client
 * L2 auth implementation verified against py-clob-client source:
 * https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/signing/hmac.py
 * https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/headers/headers.py
 */

const CLOB_BASE  = 'https://clob.polymarket.com';
const GAMMA_BASE = 'https://gamma-api.polymarket.com';

// The path signed in the HMAC — query params are NOT included in the signature.
// Source: RequestArgs(method="GET", request_path=TRADES) where TRADES="/data/trades"
const TRADES_PATH = '/data/trades';

// ─── Auth ────────────────────────────────────────────────────────────────────

/**
 * Build L2 request headers per the actual py-clob-client spec.
 *
 * Headers (L2):
 *   POLY_ADDRESS    — wallet address (signer.address())
 *   POLY_SIGNATURE  — HMAC-SHA256 of (timestamp + method + requestPath), URL-safe base64
 *   POLY_TIMESTAMP  — Unix seconds
 *   POLY_API_KEY    — API key UUID (creds.api_key) — DISTINCT from POLY_ADDRESS
 *   POLY_PASSPHRASE — API passphrase
 *
 * NOT included in L2: POLY_NONCE (that is L1 only).
 *
 * HMAC message: timestamp + method + requestPath  (NO nonce, NO query params)
 * Secret decode: URL-safe base64  (base64.urlsafe_b64decode)
 * Signature encode: URL-safe base64  (base64.urlsafe_b64encode)
 *
 * IMPORTANT — two env vars required:
 *   CLOB_ADDRESS    — wallet address  → POLY_ADDRESS
 *   CLOB_API_KEY    — API key UUID    → POLY_API_KEY
 *   CLOB_SECRET     — URL-safe base64 HMAC secret
 *   CLOB_PASS_PHRASE — passphrase
 *
 * If CLOB_ADDRESS is absent, falls back to CLOB_API_KEY for POLY_ADDRESS
 * (handles legacy single-var setups where the wallet address was stored as CLOB_API_KEY).
 */
async function buildL2Headers(method: string, requestPath: string): Promise<Record<string, string>> {
  const apiKey     = Deno.env.get('CLOB_API_KEY')!;
  const secret     = Deno.env.get('CLOB_SECRET')!;
  const passphrase = Deno.env.get('CLOB_PASS_PHRASE')!;
  // CLOB_ADDRESS is the wallet address for POLY_ADDRESS.
  // Falls back to CLOB_API_KEY if not set (legacy config where wallet addr = CLOB_API_KEY).
  const walletAddress = Deno.env.get('CLOB_ADDRESS') ?? apiKey;

  const ts = Math.floor(Date.now() / 1000);

  // HMAC message: timestamp + method + requestPath — NO nonce, NO query params.
  // Source: message = str(timestamp) + str(method) + str(requestPath)
  const msg = `${ts}${method}${requestPath}`;

  // Secret is URL-safe base64 encoded. Must use urlsafe decode (- and _ variants).
  // Source: base64_secret = base64.urlsafe_b64decode(secret)
  const urlSafeB64ToBytes = (b64: string): Uint8Array => {
    const standard = b64.replace(/-/g, '+').replace(/_/g, '/');
    // Pad to multiple of 4
    const padded = standard + '='.repeat((4 - standard.length % 4) % 4);
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  };

  const rawSecret = urlSafeB64ToBytes(secret);
  const cryptoKey = await crypto.subtle.importKey(
    'raw', rawSecret,
    { name: 'HMAC', hash: 'SHA-256' },
    false, ['sign'],
  );
  const sigBytes = await crypto.subtle.sign('HMAC', cryptoKey, new TextEncoder().encode(msg));

  // Signature must be URL-safe base64 (- and _ instead of + and /).
  // Source: base64.urlsafe_b64encode(h.digest()).decode("utf-8")
  const bytesToUrlSafeB64 = (bytes: Uint8Array): string => {
    const b64 = btoa(String.fromCharCode(...bytes));
    return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };

  const signature = bytesToUrlSafeB64(new Uint8Array(sigBytes));

  // L2 headers — no POLY_NONCE (that belongs to L1 only).
  // Source: create_level_2_headers() in headers.py
  return {
    'POLY_ADDRESS':    walletAddress,
    'POLY_SIGNATURE':  signature,
    'POLY_TIMESTAMP':  String(ts),
    'POLY_API_KEY':    apiKey,
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
 * Fetch ALL trades for a wallet address since `afterUnixSeconds`.
 * Queries by maker_address only — taker_address is not a supported TradeParams field
 * in the CLOB API (verified from py-clob-client TradeParams dataclass).
 * Handles pagination automatically via next_cursor.
 */
export async function fetchTradesForWallet(
  walletAddress: string,
  afterUnixSeconds: number,
): Promise<ClobTrade[]> {
  return fetchTradesByMakerAddress(walletAddress, afterUnixSeconds);
}

async function fetchTradesByMakerAddress(
  address: string,
  afterUnixSeconds: number,
): Promise<ClobTrade[]> {
  const trades: ClobTrade[] = [];
  let cursor: string | null = null;

  while (true) {
    // Sign only the base path — query params are NOT part of the HMAC message.
    // Source: RequestArgs(method="GET", request_path=TRADES) where TRADES="/data/trades"
    const headers = await buildL2Headers('GET', TRADES_PATH);

    // Build the full URL with query params (separate from signing).
    const fullPath = buildTradeQueryPath(address, afterUnixSeconds, cursor);
    const url = `${CLOB_BASE}${fullPath}`;

    const resp = await fetch(url, { headers });
    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`CLOB /data/trades ${resp.status} ${resp.statusText}: ${errText}`);
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

function buildTradeQueryPath(
  address: string,
  after: number,
  cursor: string | null,
): string {
  const params = new URLSearchParams({
    maker_address: address.toLowerCase(),
    after:         String(after),
    limit:         '500',
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
    const events    = m.events as Array<{ slug?: string }> | undefined;
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
