"""Main entrypoint for continuous market-mode execution."""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from trader.config import load_config
from trader.continuous_cli import build_continuous_parser
from trader.runtime.continuous_runner import ContinuousRunner, ContinuousRunnerConfig, parse_market_selection


async def run_continuous_from_cli() -> None:
    load_dotenv()
    parser = build_continuous_parser()
    args = parser.parse_args()

    # load_config requires POLY_EVENT_INPUT; use a placeholder and override per event in the runner.
    os.environ.setdefault("POLY_EVENT_INPUT", "btc-updown-5m-0")
    os.environ["BOT_MODE"] = args.mode

    base_cfg = load_config()
    specs = parse_market_selection(args.markets)
    cfg = ContinuousRunnerConfig(
        specs=specs,
        mode=args.mode,
        duration_minutes=args.duration_minutes,
        status_interval_sec=args.status_interval_sec,
    )
    runner = ContinuousRunner(cfg=cfg, base_cfg=base_cfg)
    await runner.run()


def main() -> None:
    asyncio.run(run_continuous_from_cli())


if __name__ == "__main__":
    main()

