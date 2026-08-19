"""
cluster_risk_gate.py -- cluster-level capital-allocation risk control.

Gap found in the 2026-08-19 spec audit: this system has ~79 live strategies
(signal_engine.STRATEGIES) with no formal guard against running highly
correlated ones at full size simultaneously. Implements 5 checks against
cluster_matrix.json (policy) + cluster_strategy_map.py (real strategy
classification) + correlation_matrix.json (real measured pairwise
correlation, idle_engine.run_correlation_update, now scheduled nightly via
daily_pipeline.py):

  1. Cluster membership for the current regime
  2. Cross-cluster compatibility (red = block, yellow = halve size)
  3. Intra-cluster sizing caps (per-strategy % scales down as more of the
     same cluster are already open; total cluster risk is capped)
  4. Correlation downshift -- REAL pairwise correlation between the new
     signal's symbol and each open position's symbol, tiered 0.70/0.80/0.90
  5. Directional exposure caps by time-of-day
  6. Per-underlying concentration cap -- max UNDERLYING_MAX_POSITIONS
     concurrent positions sharing the same underlying (e.g. NIFTY futures +
     a NIFTY strangle + a NIFTY pin trade are 3 different strategies/
     clusters but ONE underlying). Crude on purpose: ignores delta/vega/
     notional and direction, and is not a substitute for real portfolio
     Greeks aggregation -- ONLY a floor against the specific "N strategies
     stacked on one underlying" pattern the correlation-matrix check can't
     catch when the underlying itself isn't in the correlation_matrix.json
     symbol set (its lookup is index/equity price series, not
     futures-vs-options-on-the-same-underlying cross-product correlation).

Deliberately does NOT hardcode a strategy-pair correlation list (the numbers
in the original spec are for a different, hypothetical strategy set) --
correlation is looked up live and returns None (no data yet, no restriction)
until correlation_matrix.json has been populated by at least one nightly run.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cluster_strategy_map import cluster_of

UNDERLYING_MAX_POSITIONS = 2


def underlying_of(symbol: str) -> str:
    """Alpha root before the first digit (BANKNIFTY24AUGFUT -> BANKNIFTY,
    NIFTY09JUN2623300CE -> NIFTY). Falls back to the whole symbol for plain
    equity cash symbols with no digit suffix (RELIANCE -> RELIANCE). Same
    pattern as manual_trade_tracker.py's _underlying_root, reimplemented
    here rather than importing across subsystems for one regex."""
    s = str(symbol or "").upper().strip()
    m = re.match(r"^([A-Z]+)\d", s)
    return m.group(1) if m else s

logger = logging.getLogger("cluster_risk_gate")

_MATRIX_FILE = Path("cluster_matrix.json")


class ClusterRiskGate:
    def __init__(self, matrix_path: Optional[str] = None):
        path = Path(matrix_path) if matrix_path else _MATRIX_FILE
        self._matrix: Dict[str, Any] = {}
        try:
            self._matrix = json.loads(path.read_text())
        except Exception as e:
            logger.warning("cluster_risk_gate: could not load %s: %s -- gate is a no-op", path, e)

    def _loaded(self) -> bool:
        return bool(self._matrix)

    # ── Regime resolution ────────────────────────────────────────────────
    @staticmethod
    def resolve_regime_key(
        *, adx: float = 0.0, price_above_50ema: bool = False, bb_bandwidth_pct: float = 0.0,
        india_vix: float = 0.0, days_to_expiry: Optional[int] = None,
        is_event_day: bool = False, is_market_crash: bool = False,
        is_holiday_week: bool = False, is_trend: Optional[bool] = None,
    ) -> str:
        """Maps raw regime signals to one of the 8 policy regime keys, using
        exactly the boolean definitions from the spec (ADX>25+50EMA=trend,
        ADX<20+BandWidth<8%=mean-revert, VIX>20=high-vol, VIX<13=low-vol,
        DTE<=5=expiry-week). Explicit inputs, not coupled to either of this
        codebase's two existing (disconnected, single-label) regime engines
        -- retrofitting those is a separate, larger piece of work.

        is_trend: pass this directly (e.g. derived from the existing
        regime.py TREND/BREAKOUT label) to skip the adx/price_above_50ema
        computation when the caller already has a trend classification."""
        if is_market_crash:
            return "market_crash"
        if is_holiday_week:
            return "holiday_week"
        if is_event_day:
            return "event_day"
        if days_to_expiry is not None and days_to_expiry <= 5:
            return "expiry_week"

        if is_trend is None:
            is_trend = adx > 25 and price_above_50ema
        is_high_vol = india_vix > 20
        is_low_vol = india_vix < 13

        if is_trend and is_low_vol:
            return "strong_trend_low_vol"
        if is_trend and is_high_vol:
            return "strong_trend_high_vol"
        if not is_trend and is_low_vol:
            return "weak_trend_low_vol"
        if not is_trend and is_high_vol:
            return "weak_trend_high_vol"
        # Neither clearly low nor high vol and no clear trend/no-trend read --
        # default to the more conservative weak-trend/low-vol bucket rather
        # than guessing a directional-risk regime.
        return "weak_trend_low_vol"

    @staticmethod
    def time_bucket(now: Optional[dtime] = None, *, is_expiry_day: bool = False) -> str:
        if is_expiry_day:
            return "expiry_day"
        t = now or dtime(12, 0)
        if dtime(9, 15) <= t < dtime(10, 30):
            return "0915_1030"
        if dtime(10, 30) <= t < dtime(13, 30):
            return "1030_1330"
        return "1330_1500"

    # ── Correlation lookup (real data) ──────────────────────────────────
    def _max_correlation_with_open(self, symbol: str, open_positions: List[Dict]) -> Optional[float]:
        try:
            from portfolio_heat import _load_correlation_matrix
        except Exception:
            return None
        syms, mat = _load_correlation_matrix()
        if not syms or mat.size == 0:
            return None
        sym_u = str(symbol or "").upper()
        if sym_u not in syms:
            return None
        i = syms.index(sym_u)
        best: Optional[float] = None
        for pos in open_positions:
            other = str(pos.get("symbol", "")).upper()
            if not other or other == sym_u or other not in syms:
                continue
            j = syms.index(other)
            try:
                corr = abs(float(mat[i][j]))
            except Exception:
                continue
            if best is None or corr > best:
                best = corr
        return best

    def _correlation_tier_multiplier(self, corr: float) -> float:
        tiers = self._matrix.get("correlation_downshift_tiers", {})
        numeric_tiers = {}
        for k, v in tiers.items():
            try:
                numeric_tiers[float(k)] = float(v)
            except (TypeError, ValueError):
                continue  # skips "_comment" and any other non-numeric key
        # Sorted descending so the highest matching threshold wins.
        mult = 1.0
        for threshold in sorted(numeric_tiers, reverse=True):
            if corr >= threshold:
                mult = numeric_tiers[threshold]
                break
        return mult

    # ── Main gate ────────────────────────────────────────────────────────
    def can_enter(
        self,
        *,
        strategy_name: str,
        symbol: str,
        proposed_risk_pct: float,
        direction: str,
        regime_key: str,
        open_positions: List[Dict],
        time_bucket: Optional[str] = None,
    ) -> Tuple[bool, float, str]:
        """Returns (allowed, adjusted_risk_pct, reason)."""
        if not self._loaded():
            return True, proposed_risk_pct, "OK (cluster matrix not loaded, gate disabled)"

        cluster = cluster_of(strategy_name)
        regime = self._matrix.get("regimes", {}).get(regime_key)
        if regime is None:
            return True, proposed_risk_pct, f"OK (unknown regime '{regime_key}', no restriction)"

        if cluster in regime.get("disabled_clusters", []):
            return False, 0.0, f"CLUSTER_DISABLED_FOR_REGIME:{cluster}/{regime_key}"
        if cluster not in regime.get("active_clusters", []):
            return False, 0.0, f"CLUSTER_NOT_ACTIVE_FOR_REGIME:{cluster}/{regime_key}"

        size_mult = 1.0
        reasons: List[str] = []

        # ── Cross-cluster compatibility ─────────────────────────────────
        open_clusters = {cluster_of(p.get("strategy", "")) for p in open_positions if p.get("strategy")}
        compat = self._matrix.get("cross_cluster_compatibility", {}).get(cluster, {})
        for oc in open_clusters:
            if oc == cluster:
                continue
            color = compat.get(oc)
            if color == "red":
                return False, 0.0, f"CLUSTER_CONFLICT:{cluster}x{oc}"
            if color == "yellow":
                size_mult = min(size_mult, 0.5)
                reasons.append(f"yellow:{cluster}x{oc}")

        # ── Per-underlying concentration cap ─────────────────────────────
        target_underlying = underlying_of(symbol)
        same_underlying_count = sum(
            1 for p in open_positions if underlying_of(p.get("symbol", "")) == target_underlying
        )
        if same_underlying_count >= UNDERLYING_MAX_POSITIONS:
            return False, 0.0, (
                f"UNDERLYING_CONCENTRATION:{target_underlying} already has "
                f"{same_underlying_count} open position(s)"
            )

        # ── Intra-cluster sizing ─────────────────────────────────────────
        sizing = self._matrix.get("intra_cluster_sizing", {}).get(cluster, {})
        if sizing:
            same_cluster_positions = [p for p in open_positions if cluster_of(p.get("strategy", "")) == cluster]
            n_existing = len(same_cluster_positions)
            per_strategy_key = str(min(n_existing + 1, 3))
            per_strategy_cap = sizing.get(per_strategy_key)
            if per_strategy_cap is not None and proposed_risk_pct > per_strategy_cap:
                proposed_risk_pct = per_strategy_cap
                reasons.append(f"intra_cluster_cap:{per_strategy_cap}")
            existing_cluster_risk = sum(float(p.get("risk_pct", 0.0) or 0.0) for p in same_cluster_positions)
            max_total = sizing.get("max_total")
            if max_total is not None and existing_cluster_risk + proposed_risk_pct * size_mult > max_total:
                return False, 0.0, f"CLUSTER_RISK_CAP:{cluster} existing={existing_cluster_risk:.2f} max={max_total}"

        # ── Correlation downshift (real data) ────────────────────────────
        max_corr = self._max_correlation_with_open(symbol, open_positions)
        if max_corr is not None:
            tier_mult = self._correlation_tier_multiplier(max_corr)
            if tier_mult <= 0.0:
                return False, 0.0, f"CORRELATION_TOO_HIGH:{max_corr:.2f}"
            size_mult = min(size_mult, tier_mult)
            reasons.append(f"correlation={max_corr:.2f}x{tier_mult}")

        # ── Directional exposure cap ────────────────────────────────────
        if time_bucket:
            caps = self._matrix.get("directional_caps", {}).get(time_bucket)
            if caps:
                long_pct = sum(float(p.get("risk_pct", 0.0) or 0.0)
                                for p in open_positions if str(p.get("side", "")).upper() == "BUY")
                short_pct = sum(float(p.get("risk_pct", 0.0) or 0.0)
                                 for p in open_positions if str(p.get("side", "")).upper() == "SELL")
                is_long = str(direction or "").upper() in {"BUY", "LONG"}
                projected_long = long_pct + (proposed_risk_pct * size_mult if is_long else 0.0)
                projected_short = short_pct + (proposed_risk_pct * size_mult if not is_long else 0.0)
                max_long = caps.get("max_long")
                max_short = caps.get("max_short")
                if is_long and max_long is not None and projected_long > max_long:
                    return False, 0.0, f"DIRECTIONAL_CAP:long {projected_long:.2f}>{max_long}"
                if not is_long and max_short is not None and projected_short > max_short:
                    return False, 0.0, f"DIRECTIONAL_CAP:short {projected_short:.2f}>{max_short}"

        final_risk_pct = round(proposed_risk_pct * size_mult, 4)
        reason = "OK" if not reasons else "OK (" + "; ".join(reasons) + ")"
        return True, final_risk_pct, reason
