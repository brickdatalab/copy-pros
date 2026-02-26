from __future__ import annotations

import inspect

from trader.engine.state import MarketState
from trader.runtime.orchestrator import BotRuntime
from trader.strategy.decision_policy import DecisionPolicy


def test_high_frequency_paths_do_not_embed_rest_fetch_calls() -> None:
    for fn in (BotRuntime._indicator_loop, BotRuntime._signal_loop, BotRuntime._execution_loop, DecisionPolicy.decide):
        src = inspect.getsource(fn)
        assert "fetch_event_market_context" not in src
        assert "fetch_winning_side" not in src


def test_flow_updates_avoid_unbounded_for_loops_in_per_trade_path() -> None:
    for fn in (
        MarketState.add_trade,
        MarketState._update_ew_signed_delta,
        MarketState._update_vpin,
        MarketState._update_flow_30s,
    ):
        src = inspect.getsource(fn)
        assert "for " not in src
