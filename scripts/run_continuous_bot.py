#!/usr/bin/env python3
"""Run continuous market streams across selected Polymarket up/down markets."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trader.continuous_main import main  # noqa: E402


if __name__ == "__main__":
    main()
