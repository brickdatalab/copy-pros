"""Order payload builders and router helpers."""

from __future__ import annotations

import math


def build_entry_order(side: str, price: float, shares: float, client_order_id: str) -> dict[str, object]:
    rounded_price = math.floor(price * 10000) / 10000
    rounded_shares = math.floor(shares * 1000) / 1000
    return {
        "client_order_id": client_order_id,
        "side": side,
        "price": rounded_price,
        "shares": rounded_shares,
        "order_type": "LIMIT",
        "time_in_force": "GTC",
    }
