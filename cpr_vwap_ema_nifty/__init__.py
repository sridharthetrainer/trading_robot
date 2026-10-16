"""
CPR_VWAP_EMA_Nifty -- self-contained, RESEARCH-ONLY strategy package.

Independent of the live system: it is not imported by signal_engine.py or any
live module, keeps its own config (config.json), rules (rules.json), data
cache (data_cache.db), logs (logs/) and results (results/). Existing code is
used only through read-only calls (angel.AngelOne for downloads,
request_governor pacing, nse_cost_model costs, option_intraday_pricer pricing,
and read-only sqlite opens of existing databases).

Live use is OFF: config.json "enabled_live": false and "research_only": true.
is_enabled_live() returns True only if BOTH are flipped, and even then nothing
in the live engine calls this package until it is explicitly registered.
"""
from __future__ import annotations

STRATEGY_NAME = "CPR_VWAP_EMA_Nifty"


def is_enabled_live() -> bool:
    from .backtest import is_enabled_live as _f
    return _f()
