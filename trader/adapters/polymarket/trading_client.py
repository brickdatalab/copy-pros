"""Thin async wrapper around py_clob_client for live limit orders."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any

from py_clob_client.client import ClobClient  # type: ignore[import-untyped]
from py_clob_client.clob_types import ApiCreds, OrderArgs  # type: ignore[import-untyped]


def normalize_side(side: str) -> str:
    value = side.upper()
    if value in {"UP", "DOWN", "BUY"}:
        return "BUY"
    return "SELL"


@dataclass
class TradingClient:
    enabled: bool
    _client: ClobClient | None = None

    def __post_init__(self) -> None:
        if not self.enabled:
            return

        host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        private_key = os.getenv("POLY_PRIVATE_KEY")
        clob_address = os.getenv("CLOB_ADDRESS")
        api_key = os.getenv("CLOB_API_KEY")
        api_secret = os.getenv("CLOB_SECRET")
        api_passphrase = os.getenv("CLOB_PASS_PHRASE")

        if not private_key or not clob_address or not api_key or not api_secret or not api_passphrase:
            raise ValueError("Missing required CLOB credentials")

        chain_id = int(os.getenv("POLY_CHAIN_ID", "137"))
        signature_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))

        client = ClobClient(
            host,
            chain_id=chain_id,
            key=private_key,
            signature_type=signature_type,
            funder=clob_address,
        )
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_passphrase))
        self._client = client

    async def place_limit_order(self, token_id: str, price: float, shares: float, side: str = "BUY") -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise RuntimeError("Trading client disabled")
        client = self._client

        normalized_side = normalize_side(side)

        def _create_and_post() -> dict[str, Any]:
            signed_order = client.create_order(
                OrderArgs(token_id=token_id, price=price, size=shares, side=normalized_side)
            )
            result = client.post_order(signed_order)
            if isinstance(result, dict):
                return result
            return {"raw": result}

        return await asyncio.to_thread(_create_and_post)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise RuntimeError("Trading client disabled")
        client = self._client

        def _cancel() -> dict[str, Any]:
            result = client.cancel(order_id)
            if isinstance(result, dict):
                return result
            return {"raw": result}

        return await asyncio.to_thread(_cancel)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        if not self.enabled or self._client is None:
            raise RuntimeError("Trading client disabled")
        client = self._client

        def _get() -> dict[str, Any]:
            result = client.get_order(order_id)
            if isinstance(result, dict):
                return result
            return {"raw": result}

        return await asyncio.to_thread(_get)
