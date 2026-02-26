#!/usr/bin/env python3
"""Run one event-scoped trading bot instance."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable when running via scripts/run_event_bot.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader.main import main  # noqa: E402


if __name__ == "__main__":
    main()
