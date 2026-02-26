"""CLI parsing for event bot runner."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one Polymarket event bot instance")
    parser.add_argument("--event", required=True, help="Polymarket event URL or slug")
    parser.add_argument("--mode", default="live", choices=["live", "dry_run"], help="Run mode")
    return parser
