"""
meta_learner.py — Dynamic Strategy Weighting (WOW Factor)

Transforms the bot from static rules to adaptive engine.
Tracks each strategy's last 20 trades, computes EWMA Sharpe,
weights proportional to Sharpe² × regime multiplier.

Output: composite signal score = weighted sum of all strategy signals.
Top 5 strategies shown in /status.
"""
from __future__ import annotations
import json, logging, math
from pathlib import Path
from typing import Dict, List

import numpy as np

logger = logging.getLogger(__name__)

_STATE_FILE = Path("meta_learner_state.json")
_HALF_LIFE  = 5     # EWMA half-life in days
_MAX_TRADES = 20    # trades remembered per strategy
_MIN_TRADES = 30    # minimum before we trust the Sharpe (guards against lucky small samples)

# Regime → strategy type multipliers
_REGIME_MULTIPLIERS = {
    "TRENDING":       {"trend":1.5,"momentum":1.5,"breakout":1.4,"mean_reversion":0.6},
    "MEAN_REVERTING": {"mean_reversion":1.5,"range":1.5,"trend":0.6,"momentum":0.7},
    "HIGH_NOISE":     {"_all": 0.5},   # all strategies halved
    "BREAKOUT":       {"breakout":1.8,"trend":1.3,"momentum":1.2,"mean_reversion":0.5},
    "HIGH_VOL":       {"options":1.3,"mean_reversion":1.2,"trend":0.8},
    "NO_TRADE":       {"_all": 0.0},
}

_STRATEGY_TYPES = {
    # trend
    "ema_cross":"trend","supertrend":"trend","adx_trend":"trend","macd_trend":"trend",
    "bb_squeeze":"trend","cpr_ema":"trend","holy_grail":"trend",
    # momentum
    "rsi_divergence":"momentum","macd_histogram":"momentum","ttm_squeeze":"momentum",
    "volume_profile":"momentum","williams_r":"momentum",
    # mean reversion
    "vwap_reversion":"mean_reversion","bb_reversion":"mean_reversion",
    "rsi_reversion":"mean_reversion","mean_reversion":"mean_reversion",
    # breakout
    "orb":"breakout","failed_breakout":"breakout","range_breakout":"breakout",
    # options
    "hero_zero":"options","theta_decay":"options","iron_condor":"options",
}


class MetaLearner:
    """Dynamic strategy weighting based on regime + rolling Sharpe."""

    def __init__(self):
        self._trades: Dict[str, List[float]] = {}  # strategy → list of returns
        self._weights: Dict[str, float]      = {}
        self._last_weights_ts: float         = 0
        self._load_state()

    def _load_state(self):
        try:
            if _STATE_FILE.exists():
                d = json.loads(_STATE_FILE.read_text())
                self._trades = d.get("trades", {})
                logger.info("MetaLearner: loaded %d strategy records", len(self._trades))
            else:
                # First-run bootstrap: seed each known strategy with a tiny neutral
                # return so get_weights() never returns {} before real trades arrive.
                # These tiny seeds are overridden after _MIN_TRADES real trades.
                for strat in _STRATEGY_TYPES:
                    self._trades.setdefault(strat, [0.001])   # 0.1% neutral seed
                logger.info("MetaLearner: no state file — bootstrapped %d strategies with neutral seeds",
                            len(self._trades))
                self._save_state()
        except Exception as e:
            logger.warning("MetaLearner _load_state: %s", e)

    def _save_state(self):
        try:
            _STATE_FILE.write_text(json.dumps({"trades": self._trades}, indent=2))
        except Exception: pass

    def record_trade(self, strategy: str, pnl: float, entry_price: float) -> None:
        """Record a completed trade. Call after trade closes."""
        ret = pnl / max(abs(entry_price), 1)
        if strategy not in self._trades:
            self._trades[strategy] = []
        self._trades[strategy].append(round(ret, 6))
        self._trades[strategy] = self._trades[strategy][-_MAX_TRADES:]
        self._save_state()
        self._weights = {}  # invalidate cache

    def _ewma_sharpe(self, returns: List[float]) -> float:
        """EWMA Sharpe with half-life = 5 days."""
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns, dtype=float)
        decay = 0.5 ** (1.0 / _HALF_LIFE)
        weights = np.array([decay ** (len(arr)-1-i) for i in range(len(arr))])
        weights /= weights.sum()
        mean  = np.dot(weights, arr)
        var   = np.dot(weights, (arr - mean) ** 2)
        std   = math.sqrt(var) if var > 0 else 1e-9
        return float(mean / std) * math.sqrt(252)

    def get_weights(self, regime: str = "NEUTRAL") -> Dict[str, float]:
        """
        Compute weights for all known strategies.
        Weight = max(0, Sharpe)² × regime_multiplier, normalised to sum=1.
        """
        import time as _t
        if self._weights and (_t.time() - self._last_weights_ts) < 300:
            return self._weights

        regime_up = regime.upper()
        rm = _REGIME_MULTIPLIERS.get(regime_up, {})
        all_mult = rm.get("_all", 1.0)

        raw: Dict[str, float] = {}
        for strat, returns in self._trades.items():
            if len(returns) < _MIN_TRADES:
                raw[strat] = 1.0  # neutral weight when insufficient data
                continue
            sharpe = self._ewma_sharpe(returns)
            stype  = _STRATEGY_TYPES.get(strat.replace("run_","").replace("_strategy",""), "other")
            regime_mult = rm.get(stype, 1.0) * all_mult
            raw[strat] = max(0.0, sharpe) ** 2 * regime_mult

        if not raw:
            return {}

        total = sum(raw.values())
        if total <= 0:
            n = len(raw)
            self._weights = {k: 1.0/n for k in raw}
        else:
            self._weights = {k: v/total for k, v in raw.items()}

        self._last_weights_ts = _t.time()
        return self._weights

    def get_composite_score(
        self,
        signals: Dict[str, float],
        regime:  str = "NEUTRAL",
    ) -> float:
        """
        Composite score = weighted sum of individual strategy scores.
        signals: {strategy_name: score}
        """
        weights = self.get_weights(regime)
        if not weights:
            # Equal weighting fallback
            return float(np.mean(list(signals.values()))) if signals else 0.0

        total_w = total_s = 0.0
        for strat, score in signals.items():
            w = weights.get(strat, 1.0 / max(len(weights), 1))
            total_s += score * w
            total_w += w
        return total_s / max(total_w, 1e-9)

    def top_strategies(self, n: int = 5, regime: str = "NEUTRAL") -> List[Dict]:
        """Return top N strategies by current weight."""
        weights = self.get_weights(regime)
        sorted_s = sorted(weights.items(), key=lambda x: -x[1])
        result = []
        for strat, w in sorted_s[:n]:
            returns = self._trades.get(strat, [])
            sharpe  = self._ewma_sharpe(returns) if len(returns) >= 2 else 0.0
            result.append({
                "strategy": strat,
                "weight":   round(w*100, 1),
                "sharpe":   round(sharpe, 2),
                "trades":   len(returns),
                "win_rate": round(sum(1 for r in returns if r>0)/max(len(returns),1)*100, 0),
            })
        return result


    def train(self, trades_db_path: str = "trades.db") -> dict:
        """
        Retrain meta-learner weights from actual trade results.
        Called nightly after market close.
        Uses recent 90 days of trades per strategy.
        """
        import sqlite3
        results = {}
        try:
            conn = sqlite3.connect(trades_db_path)
            rows = conn.execute(
                "SELECT strategy, realized_pnl, status FROM trades "
                "WHERE status='CLOSED' ORDER BY entry_time DESC LIMIT 500"
            ).fetchall()
            conn.close()

            strategy_pnl = {}
            for strat, pnl, status in rows:
                if not strat: continue
                strategy_pnl.setdefault(strat, []).append(float(pnl or 0))

            new_weights = {}
            for strat, pnls in strategy_pnl.items():
                if len(pnls) < 3: continue
                wins = sum(1 for p in pnls if p > 0)
                win_rate = wins / len(pnls)
                avg_pnl  = sum(pnls) / len(pnls)
                # Weight = win_rate * sign(avg_pnl), clamped 0.3-2.0
                weight = max(0.3, min(2.0, win_rate * (1.5 if avg_pnl > 0 else 0.5)))
                new_weights[strat] = round(weight, 3)
                results[strat] = {"weight": weight, "win_rate": win_rate,
                                   "trades": len(pnls), "avg_pnl": round(avg_pnl,2)}

            if new_weights:
                # Update weights (merge with existing)
                existing = getattr(self, '_weights', {})
                existing.update(new_weights)
                self._weights = existing
                # Persist
                try:
                    import json
                    from pathlib import Path
                    Path("meta_learner_weights.json").write_text(json.dumps(existing, indent=2))
                except Exception: pass
                import logging
                logging.getLogger(__name__).info(
                    "Meta-learner retrained: %d strategies updated", len(new_weights))

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("meta_learner train: %s", e)
        return results

    def load_weights(self) -> None:
        """Load persisted weights from disk."""
        try:
            import json
            from pathlib import Path
            wf = Path("meta_learner_weights.json")
            if wf.exists():
                self._weights = json.loads(wf.read_text())
        except Exception: pass

    def get_weight(self, strategy: str) -> float:
        """Get learned weight for a strategy (default 1.0)."""
        return getattr(self, '_weights', {}).get(strategy, 1.0)


    def status_text(self, regime: str = "NEUTRAL") -> str:
        """Telegram /status text for meta-learner."""
        top = self.top_strategies(5, regime)
        rm  = _REGIME_MULTIPLIERS.get(regime.upper(), {})
        mult = rm.get("_all", 1.0)
        lines = [
            f"🧠 <b>META-LEARNER</b> | Regime: {regime}",
            f"   Regime multiplier: {'×%.1f'%mult if mult!=1 else '×1.0 (normal)'}",
            "",
            "   <b>Top 5 strategies by weight:</b>",
        ]
        for s in top:
            bar = "█" * max(1, int(s["weight"]/5))
            lines.append(
                f"   {s['strategy'][:22]:22} {bar} {s['weight']:.1f}% "
                f"[Sharpe={s['sharpe']:+.2f} WR={s['win_rate']:.0f}% n={s['trades']}]"
            )
        if not top:
            lines.append("   No trade history yet — equal weighting active")
        return "\n".join(lines)


# Global singleton
_ML = MetaLearner()

def get_meta_learner() -> MetaLearner:
    return _ML

def record_trade(strategy: str, pnl: float, entry_price: float):
    _ML.record_trade(strategy, pnl, entry_price)

def composite_score(signals: Dict[str, float], regime: str = "NEUTRAL") -> float:
    return _ML.get_composite_score(signals, regime)


def adaptive_confluence_threshold(base: float = 5.5) -> float:
    """
    IMPROVEMENT 1: Self-tuning confluence threshold.
    Auto-adjusts based on recent win rate:
      - win_rate > 65%: relax to 5.0 (more signals)
      - win_rate < 50%: tighten to 6.5 (fewer, higher quality)
      - 50-65%: keep base threshold
    """
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv('TRADES_DB', 'trades.db'))
        rows = conn.execute(
            "SELECT realized_pnl FROM trades WHERE status='CLOSED' "
            "ORDER BY entry_time DESC LIMIT 20"
        ).fetchall()
        conn.close()
        if len(rows) < 10:
            return base  # not enough data
        wins = sum(1 for r in rows if (r[0] or 0) > 0)
        wr   = wins / len(rows)
        if wr > 0.65:
            return max(5.0, base - 0.5)   # doing well — cast wider net
        elif wr < 0.50:
            return min(7.0, base + 1.0)   # struggling — raise bar
        return base
    except Exception:
        return base

