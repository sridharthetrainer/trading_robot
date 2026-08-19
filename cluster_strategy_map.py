"""
cluster_strategy_map.py -- maps this system's REAL strategy names to the A-J
cluster taxonomy used by cluster_risk_gate.py / cluster_matrix.json.

Built 2026-08-19 from the actual live registry, not guessed: every name in
FACTOR_TO_CLUSTER is grounded in strategy_clusters.factor_of()'s real
keyword classification (already live-wired into signal_engine.py's
confluence de-dup) run against all 79 entries of signal_engine.STRATEGIES
and their real "strategy" short-name outputs. The 6 option-catalog
categories come directly from option_strategy_registry.py's own `category`
field (42 entries, mostly PLACEHOLDER logic_status -- only C1/C3/D1 are
IMPLEMENTED as of this writing).

Mapping rationale (documented, not hidden):
  BREAKOUT, STRUCTURE           -> A  (intraday scalp / breakout / structure)
  MOMENTUM                      -> B  (continuation, matches spec's "Afternoon Momentum")
  MEAN_REVERSION                -> D  (direct match)
  TREND, PATTERN, PRICE_TRANSFORM -> E (trend-following in different forms)
  EVENT                         -> F  (direct match)
  RELATIVE_VALUE                -> G  (stat-arb/pairs, direct match)
  VOLATILITY                    -> H  (vol-regime tactics, closest to spec's
                                        Options Tactical cluster)
  FLOW                          -> I  (volume/OI-adjacent, direct match)
  option catalog "directional"          -> A
  option catalog "defined_risk_spread"  -> B
  option catalog "non_directional_theta", "dynamic_adjustment" -> C (theta engine)
  option catalog "long_vega_event"      -> F
  no live strategy or catalog entry maps to J except the explicit override
  below -- "expiry_scalp" is the only name with "expiry" in it.
"""
from __future__ import annotations

from strategy_clusters import factor_of

FACTOR_TO_CLUSTER = {
    "BREAKOUT":        "A",
    "STRUCTURE":       "A",
    "MOMENTUM":        "B",
    "MEAN_REVERSION":  "D",
    "TREND":           "E",
    "PATTERN":         "E",
    "PRICE_TRANSFORM": "E",
    "EVENT":           "F",
    "RELATIVE_VALUE":  "G",
    "VOLATILITY":      "H",
    "FLOW":            "I",
}

OPTION_CATEGORY_TO_CLUSTER = {
    "directional":            "A",
    "defined_risk_spread":    "B",
    "non_directional_theta":  "C",
    "dynamic_adjustment":     "C",
    "long_vega_event":        "F",
}

# Explicit per-strategy overrides that beat the factor-based default --
# each one documented with why the generic factor mapping doesn't fit.
STRATEGY_NAME_OVERRIDES = {
    # "expiry_scalp" classifies as MOMENTUM via factor_of() (keyword "scalp"),
    # but it's specifically an expiry-day strategy -- matches spec Cluster J.
    "expiry_scalp": "J",
}

DEFAULT_CLUSTER = "A"  # conservative fallback for anything genuinely unclassified


def cluster_of(strategy_name: str) -> str:
    """Cluster letter for a live strategy's short name (the exact string in
    signal["strategy"] / trade.strategy -- e.g. "orb", "trend", "expiry_scalp")."""
    name = str(strategy_name or "").strip().lower()
    if name in STRATEGY_NAME_OVERRIDES:
        return STRATEGY_NAME_OVERRIDES[name]
    factor = factor_of(name)
    return FACTOR_TO_CLUSTER.get(factor, DEFAULT_CLUSTER)


def cluster_of_option_catalog_category(category: str) -> str:
    """Cluster letter for an option_strategy_registry.py catalog entry's
    `category` field (a different classification axis than cluster_of(),
    since multi-leg option structures aren't in signal_engine.STRATEGIES)."""
    return OPTION_CATEGORY_TO_CLUSTER.get(str(category or "").strip().lower(), DEFAULT_CLUSTER)
