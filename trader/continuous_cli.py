"""CLI parsing for the continuous multi-market runner."""

from __future__ import annotations

import argparse


def build_continuous_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run continuous Polymarket market streams")
    parser.add_argument(
        "--markets",
        default="btc15,eth15,sol15,btc5,eth5,sol5",
        help="Comma-separated market selectors (btc15,eth15,sol15,btc5,eth5,sol5)",
    )
    parser.add_argument("--duration-minutes", type=float, default=60.0, help="Total run duration in minutes")
    parser.add_argument("--mode", default="dry_run", choices=["live", "dry_run"], help="Run mode")
    parser.add_argument(
        "--status-interval-sec",
        type=float,
        default=2.0,
        help="Status refresh interval for CLI output",
    )
    return parser

