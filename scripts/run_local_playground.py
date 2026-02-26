#!/usr/bin/env python3
"""Run local interactive playground server."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    uvicorn.run("trader.playground.app:app", host="127.0.0.1", port=8080, reload=False)


if __name__ == "__main__":
    main()

