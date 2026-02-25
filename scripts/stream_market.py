#!/usr/bin/env python3
"""
stream_market.py — Real-time Polymarket market data streaming engine.

Usage:
    python scripts/stream_market.py

What it does:
    1. Loads all unresolved markets from copy_pros.markets
    2. Opens a WebSocket to the Polymarket CLOB
       (wss://ws-subscriptions-clob.polymarket.com/ws/market)
    3. Subscribes to YES and NO token streams for every registered market
    4. For every tick:
         - Updates in-memory orderbook state
         - Records rolling trade windows (1m, 5m)
    5. Every second, writes to Supabase:
         - market_ticks   (raw tick row per update)
         - market_snapshots (aggregated market state)
         - indicators     (VWAP, order imbalance, spread momentum, signal)

Run this while you are working. Stop it with Ctrl+C.
Data stays in Supabase. Purge runs automatically 48h after each market resolves.
"""

import asyncio
import asyncpg
import websockets
import json
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("stream")

# ── Constants ─────────────────────────────────────────────────────────────────
CLOB_WS           = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SNAPSHOT_INTERVAL = 1.0    # seconds between snapshot/indicator writes
RECONNECT_DELAY   = 3.0    # seconds before WebSocket reconnect
SPREAD_HIST_SECS  = 30     # lookback window for spread momentum
MID_HIST_SECS     = 300    # longest mid-price momentum window (5m)
TICK_BATCH_SIZE   = 50     # max raw ticks to flush per snapshot cycle


# ── Orderbook ─────────────────────────────────────────────────────────────────
@dataclass
class OrderBook:
    """In-memory orderbook for a single token (YES or NO)."""
    bids: dict = field(default_factory=dict)  # price (float) → size (float)
    asks: dict = field(default_factory=dict)
    updated_at: Optional[datetime] = None

    def apply_snapshot(self, bids: list, asks: list) -> None:
        self.bids = {float(b["price"]): float(b["size"]) for b in bids if float(b["size"]) > 0}
        self.asks = {float(a["price"]): float(a["size"]) for a in asks if float(a["size"]) > 0}
        self.updated_at = _now()

    def apply_change(self, changes: list) -> None:
        for c in changes:
            price = float(c["price"])
            size  = float(c["size"])
            side  = c["side"]
            book  = self.bids if side == "BUY" else self.asks
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size
        self.updated_at = _now()

    @property
    def best_bid(self) -> float:
        return max(self.bids) if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return min(self.asks) if self.asks else 0.0

    @property
    def mid(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        if bb and ba:
            return (bb + ba) / 2.0
        return bb or ba

    @property
    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        return (ba - bb) if (bb and ba) else None

    @property
    def bid_depth(self) -> float:
        return sum(self.bids.values())

    @property
    def ask_depth(self) -> float:
        return sum(self.asks.values())

    @property
    def imbalance(self) -> float:
        total = self.bid_depth + self.ask_depth
        return (self.bid_depth - self.ask_depth) / total if total else 0.0

    def is_ready(self) -> bool:
        return bool(self.bids or self.asks)


# ── Market state ──────────────────────────────────────────────────────────────
@dataclass
class MarketState:
    """Per-market in-memory state combining YES + NO books and rolling windows."""
    market_id:    str
    condition_id: str
    token_yes:    str
    token_no:     str
    book_yes:     OrderBook = field(default_factory=OrderBook)
    book_no:      OrderBook = field(default_factory=OrderBook)

    # Rolling trade windows: deque of (ts, price, size)
    trades_1m: deque = field(default_factory=deque)
    trades_5m: deque = field(default_factory=deque)

    # History deques for momentum: (ts, value)
    spread_hist: deque = field(default_factory=deque)   # YES spread
    mid_hist:    deque = field(default_factory=deque)   # YES mid

    # Pending raw tick rows to flush
    pending_ticks: list = field(default_factory=list)

    def book_for(self, asset_id: str) -> OrderBook:
        return self.book_yes if asset_id == self.token_yes else self.book_no

    def side_for(self, asset_id: str) -> str:
        return "YES" if asset_id == self.token_yes else "NO"

    def add_trade(self, price: float, size: float, ts: datetime) -> None:
        entry = (ts, price, size)
        self.trades_1m.append(entry)
        self.trades_5m.append(entry)
        _prune_deque(self.trades_1m, ts, 60)
        _prune_deque(self.trades_5m, ts, 300)

    def update_history(self, now: datetime) -> None:
        yes_mid    = self.book_yes.mid
        yes_spread = self.book_yes.spread
        if yes_mid:
            self.mid_hist.append((now, yes_mid))
        if yes_spread is not None:
            self.spread_hist.append((now, yes_spread))
        _prune_deque(self.mid_hist,    now, MID_HIST_SECS)
        _prune_deque(self.spread_hist, now, MID_HIST_SECS)

    # ── Rolling computations ──────────────────────────────────────────────────

    def vwap(self, window_secs: int) -> Optional[float]:
        now    = _now()
        cutoff = now - timedelta(seconds=window_secs)
        src    = self.trades_1m if window_secs <= 60 else self.trades_5m
        items  = [(p, s) for ts, p, s in src if ts >= cutoff]
        if not items:
            return None
        total_vol = sum(s for _, s in items)
        return sum(p * s for p, s in items) / total_vol if total_vol else None

    def volume(self, window_secs: int) -> float:
        now    = _now()
        cutoff = now - timedelta(seconds=window_secs)
        src    = self.trades_1m if window_secs <= 60 else self.trades_5m
        return sum(s for ts, _, s in src if ts >= cutoff)

    def spread_momentum(self) -> Optional[float]:
        """Rate of change of YES spread over last SPREAD_HIST_SECS seconds."""
        if len(self.spread_hist) < 2:
            return None
        cutoff = _now() - timedelta(seconds=SPREAD_HIST_SECS)
        old    = next((s for ts, s in self.spread_hist if ts >= cutoff), None)
        cur    = self.spread_hist[-1][1] if self.spread_hist else None
        if old and cur and old != 0:
            return (cur - old) / old
        return None

    def mid_momentum(self, window_secs: int) -> Optional[float]:
        """Rate of change of YES mid price over last window_secs seconds."""
        if not self.mid_hist:
            return None
        cutoff = _now() - timedelta(seconds=window_secs)
        old    = next((m for ts, m in self.mid_hist if ts >= cutoff), None)
        cur    = self.mid_hist[-1][1] if self.mid_hist else None
        if old and cur and old != 0:
            return (cur - old) / old
        return None

    def compute_signal(
        self,
        imbalance: float,
        vwap_1m: Optional[float],
        mid: float,
        sp_mom: Optional[float],
    ) -> str:
        """
        Simple signal generation:
          LONG  — strong buy-side imbalance + price above or near VWAP + spread tightening
          SHORT — strong sell-side imbalance + price below VWAP + spread tightening
          NEUTRAL — insufficient conviction
        """
        if not mid or abs(imbalance) < 0.15:
            return "NEUTRAL"
        spread_tightening = sp_mom is not None and sp_mom < 0
        if imbalance > 0.15:
            above_vwap = vwap_1m is None or mid >= vwap_1m * 0.995
            if above_vwap and spread_tightening:
                return "LONG"
        if imbalance < -0.15:
            below_vwap = vwap_1m is None or mid <= vwap_1m * 1.005
            if below_vwap and spread_tightening:
                return "SHORT"
        return "NEUTRAL"


# ── Streamer ──────────────────────────────────────────────────────────────────
class MarketStreamer:
    def __init__(self, pool: asyncpg.Pool, markets: list[dict]):
        # market_id → MarketState
        self.states: dict[str, MarketState] = {}
        # asset_id (token) → market_id (for fast lookup on WS message)
        self.token_to_market: dict[str, str] = {}

        for m in markets:
            ms = MarketState(
                market_id    = m["id"],
                condition_id = m["condition_id"],
                token_yes    = m["token_id_yes"],
                token_no     = m["token_id_no"],
            )
            self.states[m["id"]] = ms
            self.token_to_market[m["token_id_yes"]] = m["id"]
            self.token_to_market[m["token_id_no"]]  = m["id"]

        self.pool       = pool
        self._ws_ready  = asyncio.Event()

    @property
    def all_token_ids(self) -> list[str]:
        return list(self.token_to_market.keys())

    # ── WebSocket loop ────────────────────────────────────────────────────────

    async def ws_loop(self) -> None:
        token_ids = self.all_token_ids
        if not token_ids:
            log.warning("No token IDs to subscribe to.")
            return

        while True:
            try:
                log.info(f"Connecting to Polymarket WS ({len(token_ids)} tokens)…")
                async with websockets.connect(
                    CLOB_WS,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=10,
                ) as ws:
                    sub = json.dumps({"assets_ids": token_ids, "type": "market"})
                    await ws.send(sub)
                    log.info("Subscribed. Streaming…")
                    self._ws_ready.set()

                    async for raw in ws:
                        try:
                            self._handle_message(raw)
                        except Exception as e:
                            log.debug(f"Message parse error: {e} — raw: {raw[:200]}")

            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                self._ws_ready.clear()
                log.warning(f"WS disconnected: {e}. Reconnecting in {RECONNECT_DELAY}s…")
                await asyncio.sleep(RECONNECT_DELAY)
            except Exception as e:
                self._ws_ready.clear()
                log.error(f"WS fatal: {e}. Reconnecting in {RECONNECT_DELAY}s…")
                await asyncio.sleep(RECONNECT_DELAY)

    def _handle_message(self, raw: str) -> None:
        msg = json.loads(raw)

        # Polymarket sends a list of events in one message
        if isinstance(msg, list):
            for item in msg:
                self._dispatch(item)
        else:
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        asset_id   = msg.get("asset_id")
        event_type = msg.get("event_type")
        if not asset_id or not event_type:
            return

        market_id = self.token_to_market.get(asset_id)
        if not market_id:
            return

        state = self.states[market_id]
        book  = state.book_for(asset_id)
        side  = state.side_for(asset_id)
        now   = _now()
        ts_raw = msg.get("timestamp", "")

        if event_type == "book":
            book.apply_snapshot(msg.get("bids", []), msg.get("asks", []))
            state.pending_ticks.append(_tick_row(
                market_id, asset_id, side, "book", book, None, None, now, msg
            ))

        elif event_type == "price_change":
            book.apply_change(msg.get("changes", []))
            state.pending_ticks.append(_tick_row(
                market_id, asset_id, side, "price_change", book, None, None, now, msg
            ))

        elif event_type == "last_trade_price":
            price = _f(msg.get("price"))
            size  = _f(msg.get("size"))
            if price and size and side == "YES":  # Track trades on YES token
                state.add_trade(price, size, now)
            state.pending_ticks.append(_tick_row(
                market_id, asset_id, side, "last_trade", book, price, size, now, msg
            ))

    # ── Snapshot loop ─────────────────────────────────────────────────────────

    async def snapshot_loop(self) -> None:
        """Every SNAPSHOT_INTERVAL seconds: flush ticks + write snapshots + indicators."""
        log.info("Snapshot loop started. Waiting for WS…")
        await self._ws_ready.wait()
        log.info("Snapshot loop active.")

        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            now = _now()
            try:
                await self._flush(now)
            except Exception as e:
                log.error(f"Snapshot flush error: {e}")

    async def _flush(self, now: datetime) -> None:
        async with self.pool.acquire() as conn:
            for mid, state in self.states.items():
                by = state.book_yes
                bn = state.book_no

                if not by.is_ready() and not bn.is_ready():
                    continue  # No data yet for this market

                # ── Flush raw ticks ───────────────────────────────────────────
                if state.pending_ticks:
                    batch = state.pending_ticks[:TICK_BATCH_SIZE]
                    state.pending_ticks = state.pending_ticks[TICK_BATCH_SIZE:]
                    await conn.executemany("""
                        INSERT INTO copy_pros.market_ticks
                          (market_id, asset_id, token_side, tick_type,
                           best_bid, best_ask, bid_size, ask_size,
                           bid_depth, ask_depth, spread,
                           trade_price, trade_size, raw, created_at)
                        VALUES
                          ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                    """, batch)

                # ── Compute snapshot values ───────────────────────────────────
                state.update_history(now)

                yes_mid = by.mid
                no_mid  = bn.mid
                vwap1   = state.vwap(60)
                vwap5   = state.vwap(300)
                vol1    = state.volume(60)
                vol5    = state.volume(300)
                imb_yes = by.imbalance
                imb_no  = bn.imbalance

                await conn.execute("""
                    INSERT INTO copy_pros.market_snapshots
                      (market_id, ts,
                       yes_price, yes_bid, yes_ask, yes_spread, yes_bid_depth, yes_ask_depth,
                       no_price,  no_bid,  no_ask,  no_spread,  no_bid_depth,  no_ask_depth,
                       vwap_1m, vwap_5m, volume_1m, volume_5m,
                       imbalance_yes, imbalance_no)
                    VALUES
                      ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                       $15,$16,$17,$18,$19,$20)
                    ON CONFLICT (market_id, ts) DO NOTHING
                """,
                    mid, now,
                    _round(yes_mid), _round(by.best_bid), _round(by.best_ask),
                    _round(by.spread), _round(by.bid_depth), _round(by.ask_depth),
                    _round(no_mid),  _round(bn.best_bid), _round(bn.best_ask),
                    _round(bn.spread), _round(bn.bid_depth), _round(bn.ask_depth),
                    _round(vwap1), _round(vwap5), _round(vol1), _round(vol5),
                    _round(imb_yes), _round(imb_no),
                )

                # ── Compute indicators ────────────────────────────────────────
                sp_mom  = state.spread_momentum()
                mom30s  = state.mid_momentum(30)
                mom1m   = state.mid_momentum(60)
                mom5m   = state.mid_momentum(300)
                signal  = state.compute_signal(imb_yes, vwap1, yes_mid, sp_mom)

                await conn.execute("""
                    INSERT INTO copy_pros.indicators
                      (market_id, ts, computed_at,
                       vwap_1m, vwap_5m, order_imbalance,
                       spread_momentum,
                       mid_momentum_30s, mid_momentum_1m, mid_momentum_5m,
                       mid_price, signal)
                    VALUES
                      ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (market_id, ts) DO NOTHING
                """,
                    mid, now, now,
                    _round(vwap1), _round(vwap5), _round(imb_yes),
                    _round(sp_mom),
                    _round(mom30s), _round(mom1m), _round(mom5m),
                    _round(yes_mid), signal,
                )

            # Log summary every 10 seconds
            if int(now.timestamp()) % 10 == 0:
                active = sum(1 for s in self.states.values() if s.book_yes.is_ready())
                log.info(f"Active markets: {active}/{len(self.states)}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None

def _round(v) -> Optional[float]:
    return round(float(v), 6) if v is not None else None

def _prune_deque(d: deque, now: datetime, secs: int) -> None:
    cutoff = now - timedelta(seconds=secs)
    while d and d[0][0] < cutoff:
        d.popleft()

def _tick_row(
    market_id: str,
    asset_id: str,
    token_side: str,
    tick_type: str,
    book: OrderBook,
    trade_price: Optional[float],
    trade_size: Optional[float],
    now: datetime,
    raw: dict,
) -> tuple:
    return (
        market_id,
        asset_id,
        token_side,
        tick_type,
        _round(book.best_bid)   if book.is_ready() else None,
        _round(book.best_ask)   if book.is_ready() else None,
        _round(next(iter(book.bids.values()), None)) if book.bids else None,  # size at best bid
        _round(next(iter(book.asks.values()), None)) if book.asks else None,  # size at best ask
        _round(book.bid_depth)  if book.is_ready() else None,
        _round(book.ask_depth)  if book.is_ready() else None,
        _round(book.spread),
        _round(trade_price),
        _round(trade_size),
        json.dumps(raw),
        now,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("ERROR: SUPABASE_DB_URL not set in .env")
        sys.exit(1)

    log.info("Connecting to Supabase…")
    pool = await asyncpg.create_pool(
        db_url,
        min_size=2,
        max_size=8,
        server_settings={"search_path": "copy_pros,public"},
    )

    # Load all active, unresolved markets
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.id, m.condition_id, m.token_id_yes, m.token_id_no,
                   e.event_slug, e.title
            FROM   copy_pros.markets m
            JOIN   copy_pros.events  e ON e.id = m.event_id
            WHERE  m.is_resolved = false
              AND  e.is_active   = true
        """)

    if not rows:
        log.warning("No active unresolved markets found. Register an event first:")
        log.warning("  python scripts/register_event.py <event-slug>")
        await pool.close()
        return

    markets = [dict(r) for r in rows]
    log.info(f"Loaded {len(markets)} market(s) across "
             f"{len(set(m['event_slug'] for m in markets))} event(s):")
    for m in markets:
        log.info(f"  [{m['event_slug']}] {m['condition_id'][:20]}…")

    streamer = MarketStreamer(pool, markets)

    log.info("Starting stream. Press Ctrl+C to stop.")
    try:
        await asyncio.gather(
            streamer.ws_loop(),
            streamer.snapshot_loop(),
        )
    except KeyboardInterrupt:
        log.info("Stopped by user.")
    finally:
        await pool.close()
        log.info("Connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
