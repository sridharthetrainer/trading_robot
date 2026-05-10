"""
strategy_performance_matrix.py

Tracks which strategies work best under which conditions.

Conditions tracked per trade:
  - Day type:    TREND / RANGE / VOLATILE / UNKNOWN
  - Time bucket: OPEN (9:15-10:00) / MORNING (10:00-12:00) /
                 LUNCH (12:00-13:30) / AFTERNOON (13:30-15:00)
  - VIX regime:  LOW (<13) / NORMAL (13-18) / HIGH (18-25) / EXTREME (>25)
  - Regime:      TRENDING / RANGING / VOLATILE

After enough data, the system only activates strategies in their
proven best conditions — e.g. ORB only on trend days 9:15-10:00.
"""
from __future__ import annotations
import json, logging, os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)
MATRIX_FILE = "strategy_matrix.json"
MIN_TRADES_FOR_FILTER = 10   # need at least 10 trades before filtering


class StrategyPerformanceMatrix:
    """
    Records and analyses per-strategy performance by market condition.
    Provides score multipliers based on historical condition-specific win rate.
    """

    def __init__(self, matrix_file: str = MATRIX_FILE) -> None:
        self._file  = Path(matrix_file)
        self._data: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(
        self,
        strategy:  str,
        pnl:       float,
        day_type:  str = "UNKNOWN",
        time_bucket: str = "UNKNOWN",
        vix:       float = 15.0,
        regime:    str = "UNKNOWN",
    ) -> None:
        """Record a closed trade outcome for matrix learning."""
        key   = self._make_key(day_type, time_bucket, self._vix_regime(vix), regime)
        won   = 1 if pnl > 0 else 0
        entry = {"won": won, "pnl": round(pnl, 2), "ts": datetime.now().isoformat()}
        self._data[strategy][key].append(entry)
        if len(self._data[strategy][key]) % 5 == 0:
            self._save()

    def get_condition_multiplier(
        self,
        strategy:    str,
        day_type:    str = "UNKNOWN",
        time_bucket: str = "UNKNOWN",
        vix:         float = 15.0,
        regime:      str = "UNKNOWN",
    ) -> float:
        """
        Returns a score multiplier (0.3 - 1.5) based on historical
        performance of this strategy under current conditions.

        1.0 = neutral (no data or average performance)
        >1.0 = strategy does well in these conditions
        <1.0 = strategy struggles in these conditions
        0.0 = strategy should be blocked in these conditions
        """
        key     = self._make_key(day_type, time_bucket, self._vix_regime(vix), regime)
        trades  = self._data.get(strategy, {}).get(key, [])
        n       = len(trades)

        if n < MIN_TRADES_FOR_FILTER:
            return 1.0   # not enough data — neutral

        wins    = sum(t["won"] for t in trades)
        win_rate = wins / n
        avg_pnl  = sum(t["pnl"] for t in trades) / n

        # Convert win rate to multiplier
        if win_rate >= 0.70:   return 1.5   # excellent
        if win_rate >= 0.60:   return 1.2   # good
        if win_rate >= 0.50:   return 1.0   # neutral
        if win_rate >= 0.40:   return 0.7   # below average
        if win_rate >= 0.30:   return 0.4   # poor
        return 0.0   # terrible — block this strategy in these conditions

    def get_best_conditions(self, strategy: str) -> list:
        """Return the top 3 conditions where this strategy performs best."""
        results = []
        for key, trades in self._data.get(strategy, {}).items():
            if len(trades) >= MIN_TRADES_FOR_FILTER:
                wr  = sum(t["won"] for t in trades) / len(trades)
                results.append((wr, len(trades), key))
        results.sort(reverse=True)
        return results[:3]

    def get_time_bucket(self) -> str:
        """Return time bucket for current time."""
        t = datetime.now().time()
        from datetime import time as dtime
        if   dtime( 9, 15) <= t < dtime(10,  0): return "OPEN"
        elif dtime(10,  0) <= t < dtime(12,  0): return "MORNING"
        elif dtime(12,  0) <= t < dtime(13, 30): return "LUNCH"
        elif dtime(13, 30) <= t < dtime(15,  0): return "AFTERNOON"
        return "UNKNOWN"


    # ── Auto-disable / auto-enable rules ──────────────────────────────────────

    DISABLE_THRESHOLD  = 0.20   # only disable if win rate < 20% (was 35%)
    # Drawdown-based weight (0.2 to 1.5 based on rolling performance)
    def get_strategy_weight(self, strategy: str, lookback: int = 10) -> float:
        """Proportional weight based on recent Sharpe. Not binary enable/disable."""
        conditions = self._data.get(strategy, {})
        recent = [t for v in conditions.values() for t in v][-lookback:]
        if len(recent) < 5: return 1.0
        pnls = [t.get("pnl", 0) for t in recent]
        avg = sum(pnls) / len(pnls)
        std = (sum((p-avg)**2 for p in pnls)/len(pnls))**0.5
        sharpe = avg / std if std > 0 else 0
        # Map sharpe to weight: -2→0.2, 0→1.0, 2→1.5
        return round(max(0.2, min(1.5, 1.0 + sharpe * 0.25)), 2)

    ENABLE_THRESHOLD   = 0.50   # win rate above 50% over 10 trades → re-enable
    MIN_TRADES_DISABLE = 20     # minimum 20 trades before disabling (not 3 losses)

    def get_disabled_strategies(self) -> list:
        """Return list of strategies that should be disabled due to poor performance."""
        disabled = []
        for strat, conditions in self._data.items():
            total  = sum(len(v) for v in conditions.values())
            if total < self.MIN_TRADES_DISABLE:
                continue
            wins   = sum(sum(t["won"] for t in v) for v in conditions.values())
            wr     = wins / total
            if wr < self.DISABLE_THRESHOLD:
                disabled.append({
                    "strategy":   strat,
                    "win_rate":   round(wr * 100, 1),
                    "trades":     total,
                    "reason":     f"win_rate_{wr:.0%}_below_{self.DISABLE_THRESHOLD:.0%}_threshold",
                })
        return disabled

    def summary(self) -> dict:
        """Summary of all recorded data."""
        out = {}
        for strat, conditions in self._data.items():
            total  = sum(len(v) for v in conditions.values())
            total_wins = sum(sum(t["won"] for t in v) for v in conditions.values())
            out[strat] = {
                "total_trades": total,
                "overall_wr":   round(total_wins/max(total,1)*100, 1),
                "conditions":   len(conditions),
            }
        return out

    # ── Internal ─────────────────────────────────────────────────────────────

    def _make_key(self, day_type, time_bucket, vix_regime, regime) -> str:
        return f"{day_type}|{time_bucket}|{vix_regime}|{regime}"

    def _vix_regime(self, vix: float) -> str:
        if vix < 13:   return "VIX_LOW"
        if vix < 18:   return "VIX_NORMAL"
        if vix < 25:   return "VIX_HIGH"
        return "VIX_EXTREME"

    def _load(self) -> None:
        try:
            if self._file.exists():
                raw = json.loads(self._file.read_text())
                for strat, conds in raw.items():
                    for key, trades in conds.items():
                        self._data[strat][key] = trades
                logger.debug("Strategy matrix loaded: %d strategies", len(self._data))
        except Exception as e:
            logger.debug("Matrix load error: %s", e)


    def record_result(self, strategy: str, symbol: str,
                       won: bool, pnl: float, duration_min: int = 0) -> None:
        """Record a completed trade result for this strategy."""
        import time
        key = f"{strategy}:{symbol}"
        if key not in self._data:
            self._data[key] = {
                "strategy": strategy, "symbol": symbol,
                "wins": 0, "losses": 0, "total_pnl": 0.0,
                "total_duration": 0, "last_updated": 0,
            }
        d = self._data[key]
        if won: d["wins"] += 1
        else:   d["losses"] += 1
        d["total_pnl"]      += pnl
        d["total_duration"] += duration_min
        d["last_updated"]    = int(time.time())
        self._persist()
        import logging
        logging.getLogger(__name__).debug(
            "matrix: %s %s %s pnl=%.2f", strategy, symbol,
            "WIN" if won else "LOSS", pnl)

    def should_run_strategy(self, strategy: str, symbol: str = "") -> tuple:
        """
        Decide if strategy should run based on recent performance.
        Returns (should_run: bool, reason: str).
        Blocks strategies with <30% win rate after 10+ trades.
        """
        key = f"{strategy}:{symbol}"
        general = f"{strategy}:"
        d = self._data.get(key) or self._data.get(general)
        if not d:
            return True, "No history yet"
        trades = d.get("wins",0) + d.get("losses",0)
        if trades < 10:
            return True, f"Only {trades} trades — insufficient history"
        wr = d["wins"] / trades
        if wr < 0.25:
            return False, f"Win rate {wr:.0%} < 25% threshold — blocked"
        return True, f"Win rate {wr:.0%} OK"


    def _save(self) -> None:
        try:
            out = {s: dict(c) for s, c in self._data.items()}
            self._file.write_text(json.dumps(out, indent=2))
        except Exception as e:
            logger.debug("Matrix save error: %s", e)


_matrix: Optional[StrategyPerformanceMatrix] = None
def get_strategy_matrix() -> StrategyPerformanceMatrix:
    global _matrix
    if _matrix is None:
        _matrix = StrategyPerformanceMatrix()
    return _matrix
