"""Main entrypoint for running one event bot process."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from trader.adapters.supabase.writer import BufferedSupabaseWriter
from trader.cli import build_parser
from trader.config import load_config
from trader.runtime.orchestrator import BotRuntime
from trader.ui.console import BotConsole


async def run_from_cli() -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    os.environ["POLY_EVENT_INPUT"] = args.event
    os.environ["BOT_MODE"] = args.mode

    cfg = load_config()
    console = BotConsole.create()
    writer = BufferedSupabaseWriter(
        enabled=os.getenv("SUPABASE_ENABLED", "true").lower() in {"1", "true", "yes", "y", "on"},
        schema=os.getenv("SUPABASE_SCHEMA", "copy_pros"),
        timeout_sec=float(os.getenv("SUPABASE_TRACKING_TIMEOUT_MS", "250")) / 1000,
    )

    runtime = BotRuntime(cfg=cfg, console=console, writer=writer)
    try:
        await runtime.run()
    except Exception as err:
        console.error(f"Fatal runtime error: {err}")
        raise


def main() -> None:
    asyncio.run(run_from_cli())


if __name__ == "__main__":
    main()
