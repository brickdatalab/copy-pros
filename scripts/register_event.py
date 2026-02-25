#!/usr/bin/env python3
"""
register_event.py — Register a Polymarket event for real-time tracking.

Usage:
    python scripts/register_event.py <event-slug-or-url>

Examples:
    python scripts/register_event.py will-btc-hit-100k-by-dec-2025
    python scripts/register_event.py https://polymarket.com/event/will-btc-hit-100k-by-dec-2025

What it does:
    1. Fetches event + all sub-markets from Gamma API (public, no auth)
    2. Fetches YES/NO token IDs from CLOB API (public, no auth)
    3. Inserts into copy_pros.events and copy_pros.markets
    4. Stream script picks up registered events automatically on next run
"""

import sys
import os
import json
import asyncio
import asyncpg
import httpx
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"


def extract_slug(arg: str) -> str:
    """Accept either a slug or a full polymarket.com/event/... URL."""
    if arg.startswith("http"):
        path = urlparse(arg).path          # /event/some-slug
        parts = [p for p in path.split("/") if p]
        if "event" in parts:
            return parts[parts.index("event") + 1]
        return parts[-1]
    return arg


async def fetch_event(slug: str, client: httpx.AsyncClient) -> dict:
    r = await client.get(f"{GAMMA_BASE}/events", params={"slug": slug})
    r.raise_for_status()
    events = r.json()
    if not events:
        raise ValueError(f"No event found for slug: {slug!r}")
    return events[0]


async def fetch_clob_market(condition_id: str, client: httpx.AsyncClient) -> dict | None:
    """Fetch token IDs from CLOB market endpoint (L0, no auth)."""
    try:
        r = await client.get(f"{CLOB_BASE}/markets/{condition_id}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  WARN: CLOB fetch failed for {condition_id}: {e}")
        return None


async def register(slug: str) -> None:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL not set in .env")
        sys.exit(1)

    print(f"Registering event: {slug!r}")

    async with httpx.AsyncClient(timeout=15) as client:
        event = await fetch_event(slug, client)

    event_title    = event.get("title") or event.get("name") or slug
    event_category = event.get("category") or event.get("tags", [None])[0]
    markets_raw    = event.get("markets", [])

    if not markets_raw:
        print("ERROR: No markets found under this event.")
        sys.exit(1)

    print(f"  Event: {event_title!r}")
    print(f"  Markets: {len(markets_raw)}")

    # Fetch CLOB token IDs for each market
    market_rows = []
    async with httpx.AsyncClient(timeout=15) as client:
        for m in markets_raw:
            cid = m.get("conditionId") or m.get("condition_id")
            if not cid:
                continue

            question   = m.get("question") or m.get("title") or cid
            is_neg     = bool(m.get("negRisk") or m.get("neg_risk"))
            is_closed  = bool(m.get("closed") or m.get("resolved"))

            # Try to get token IDs from the market payload first
            clob_token_ids = m.get("clobTokenIds") or m.get("clob_token_ids")
            if isinstance(clob_token_ids, str):
                try:
                    clob_token_ids = json.loads(clob_token_ids)
                except Exception:
                    clob_token_ids = None

            if not clob_token_ids or len(clob_token_ids) < 2:
                # Fall back to CLOB API
                clob = await fetch_clob_market(cid, client)
                if clob:
                    tokens = clob.get("tokens", [])
                    # tokens is a list of {token_id, outcome} dicts
                    yes_token = next((t["token_id"] for t in tokens if t.get("outcome", "").upper() == "YES"), None)
                    no_token  = next((t["token_id"] for t in tokens if t.get("outcome", "").upper() == "NO"),  None)
                else:
                    yes_token = no_token = None
            else:
                yes_token = clob_token_ids[0]
                no_token  = clob_token_ids[1]

            if not yes_token or not no_token:
                print(f"  SKIP {cid}: could not resolve token IDs")
                continue

            market_rows.append({
                "condition_id":  cid,
                "question":      question,
                "token_id_yes":  yes_token,
                "token_id_no":   no_token,
                "is_neg_risk":   is_neg,
                "is_resolved":   is_closed,
            })

    if not market_rows:
        print("ERROR: No valid markets with token IDs found.")
        sys.exit(1)

    # Write to Supabase
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET search_path TO copy_pros, public")

        # Upsert event
        event_id = await conn.fetchval("""
            INSERT INTO events (event_slug, title, category)
            VALUES ($1, $2, $3)
            ON CONFLICT (event_slug) DO UPDATE
              SET title    = EXCLUDED.title,
                  category = EXCLUDED.category,
                  is_active = true
            RETURNING id
        """, slug, event_title, event_category)

        print(f"  event_id: {event_id}")

        # Upsert markets
        inserted = 0
        skipped  = 0
        for mr in market_rows:
            result = await conn.fetchval("""
                INSERT INTO markets
                  (event_id, condition_id, question, token_id_yes, token_id_no, is_neg_risk, is_resolved)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (condition_id) DO UPDATE
                  SET question     = EXCLUDED.question,
                      token_id_yes = EXCLUDED.token_id_yes,
                      token_id_no  = EXCLUDED.token_id_no,
                      is_neg_risk  = EXCLUDED.is_neg_risk
                RETURNING id
            """,
                event_id,
                mr["condition_id"],
                mr["question"],
                mr["token_id_yes"],
                mr["token_id_no"],
                mr["is_neg_risk"],
                mr["is_resolved"],
            )
            if result:
                inserted += 1
                q = mr["question"][:80] + "..." if len(mr["question"]) > 80 else mr["question"]
                print(f"  ✓ {mr['condition_id'][:20]}...  {q!r}")
            else:
                skipped += 1

        print(f"\nDone. {inserted} market(s) registered, {skipped} skipped.")
        print("Run `python scripts/stream_market.py` to start streaming.")
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/register_event.py <event-slug-or-url>")
        sys.exit(1)

    slug = extract_slug(sys.argv[1])
    asyncio.run(register(slug))
