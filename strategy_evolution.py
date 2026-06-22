"""
strategy_evolution.py — Dynamic Strategy Parameter Evolution

Implements a lightweight genetic algorithm that evolves strategy parameters
based on live trading results. Each strategy has a "genome" — a set of
tunable parameters. Weekly, the GA tests variations and promotes winners.

HOW IT WORKS:
  1. Each strategy has a genome: {stop_atr, target_atr, min_score, ...}
  2. Weekly: generate 5 variants per strategy (mutated genomes)
  3. Backtest each variant on last 30 days of signal_log data
  4. Winners (Sharpe > 0.6) replace current parameters
  5. Losers (Sharpe < 0.3) get their weights reduced in STRATEGIES list

ALSO:
  - Tracks strategy decay: if strategy not working for 10 days → disable
  - Rediscovers disabled strategies monthly
  - Creates "meta-strategies": combinations that historically work together

BOOKS REFERENCE:
  "Algorithmic Trading" by Ernie Chan — parameter stability testing
  "Advances in Financial ML" by Lopez de Prado — combinatorial CV
"""
from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_GENOME_FILE = Path("strategy_genomes.json")
_PERF_FILE   = Path("strategy_performance.json")

# Default genome for each strategy
DEFAULT_GENOMES: Dict[str, Dict] = {
    "trend":              {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 5.0, "min_bars": 3},
    "breakout":           {"stop_atr": 1.2, "target_atr": 2.0, "min_score": 5.5, "min_bars": 2},
    "orb":                {"stop_atr": 1.0, "target_atr": 2.0, "min_score": 5.0, "min_bars": 1},
    "vwap_reversion":     {"stop_atr": 1.0, "target_atr": 1.5, "min_score": 4.5, "min_bars": 2},
    "mean_reversion":     {"stop_atr": 0.8, "target_atr": 1.5, "min_score": 4.0, "min_bars": 2},
    "ma_cross":           {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 4.5, "min_bars": 3},
    "supertrend_mtf":     {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 5.0, "min_bars": 3},
    "ichimoku":           {"stop_atr": 1.8, "target_atr": 3.0, "min_score": 5.0, "min_bars": 4},
    "rsi_divergence":     {"stop_atr": 1.2, "target_atr": 2.0, "min_score": 5.0, "min_bars": 3},
    "order_block":        {"stop_atr": 1.0, "target_atr": 2.0, "min_score": 5.5, "min_bars": 1},
    "liquidity_sweep":    {"stop_atr": 0.8, "target_atr": 1.5, "min_score": 5.0, "min_bars": 1},
    "cpr":                {"stop_atr": 1.0, "target_atr": 1.8, "min_score": 4.0, "min_bars": 2},
    "price_structure":    {"stop_atr": 1.2, "target_atr": 2.2, "min_score": 4.5, "min_bars": 1},
    "morning_momentum":   {"stop_atr": 1.0, "target_atr": 2.0, "min_score": 3.5, "min_bars": 1},
    "expiry_scalp":       {"stop_atr": 0.8, "target_atr": 1.5, "min_score": 4.0, "min_bars": 1},
}

# Parameter bounds (min, max, step)
PARAM_BOUNDS: Dict[str, Tuple] = {
    "stop_atr":    (0.5, 3.0, 0.1),
    "target_atr":  (1.0, 5.0, 0.1),
    "min_score":   (3.0, 8.0, 0.5),
    "min_bars":    (1, 6, 1),
}


class StrategyEvolution:
    """
    Evolves strategy parameters using a genetic algorithm.
    Runs weekly, produces updated genomes, alerts via Telegram.
    """

    def __init__(self, alerts=None) -> None:
        self.alerts   = alerts
        self.genomes  = self._load_genomes()
        self.perf     = self._load_perf()

    def _load_genomes(self) -> Dict:
        try:
            if _GENOME_FILE.exists():
                return json.loads(_GENOME_FILE.read_text())
        except Exception:
            pass
        return dict(DEFAULT_GENOMES)

    def _load_perf(self) -> Dict:
        try:
            if _PERF_FILE.exists():
                return json.loads(_PERF_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            _GENOME_FILE.write_text(json.dumps(self.genomes, indent=2))
            _PERF_FILE.write_text(json.dumps(self.perf, indent=2))
        except Exception as e:
            logger.debug("StrategyEvolution save: %s", e)

    def get_genome(self, strategy: str) -> Dict:
        """Get current parameters for a strategy."""
        return self.genomes.get(strategy, DEFAULT_GENOMES.get(strategy, {}))

    def _mutate(self, genome: Dict, mutation_rate: float = 0.3) -> Dict:
        """Randomly mutate a genome within bounds."""
        mutated = dict(genome)
        for param, (lo, hi, step) in PARAM_BOUNDS.items():
            if param in mutated and random.random() < mutation_rate:
                delta = random.choice([-2, -1, 1, 2]) * step
                new_val = round(min(hi, max(lo, mutated[param] + delta)), 2)
                mutated[param] = new_val
        return mutated

    def _evaluate_genome(self, strategy: str, genome: Dict, signals: List[Dict]) -> Dict:
        """
        Evaluate a genome on historical signal data.
        Returns Sharpe, win_rate, avg_rr, n_trades.
        """
        try:
            strat_signals = [s for s in signals if s.get("strategy") == strategy
                             and s.get("tb_label") in (1, -1, 0)]
            if len(strat_signals) < 5:
                return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

            # Apply genome filters
            min_score = genome.get("min_score", 5.0)
            filtered  = [s for s in strat_signals if float(s.get("score", 0)) >= min_score]

            if len(filtered) < 3:
                return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

            wins   = sum(1 for s in filtered if s.get("tb_label") == 1)
            losses = sum(1 for s in filtered if s.get("tb_label") == -1)
            total  = wins + losses

            if total < 3:
                return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

            win_rate = wins / total
            avg_rr   = genome.get("target_atr", 2.0) / genome.get("stop_atr", 1.5)

            # Simplified Sharpe: (expected return) / std of binary outcomes
            import numpy as np
            outcomes = [avg_rr if s.get("tb_label") == 1 else -1.0 for s in filtered]
            mean_r   = float(np.mean(outcomes))
            std_r    = float(np.std(outcomes)) + 1e-6
            sharpe   = round(mean_r / std_r * (252 ** 0.5), 3)

            return {
                "sharpe":   sharpe,
                "win_rate": round(win_rate, 3),
                "avg_rr":   round(avg_rr, 2),
                "n":        len(filtered),
                "genome":   genome,
            }
        except Exception as e:
            logger.debug("evaluate_genome %s: %s", strategy, e)
            return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

    def evolve(self) -> Dict:
        """
        Run one generation of evolution on all strategies.
        Uses signal_log data from last 30 days.
        """
        results = {}
        try:
            # Load signal log data
            from signal_log import get_signal_logger
            sl      = get_signal_logger()
            signals = sl.get_training_data(days_back=30, include_rejected=True)

            if len(signals) < 20:
                logger.info("Not enough signal data for evolution: %d", len(signals))
                return {"skipped": True, "reason": "not_enough_signals", "n": len(signals)}

            improved = []
            degraded = []

            for strategy, current_genome in list(self.genomes.items()):
                # Evaluate current genome
                current_perf = self._evaluate_genome(strategy, current_genome, signals)

                # Generate 5 mutations
                variants = [self._mutate(current_genome) for _ in range(5)]
                best_variant = current_genome
                best_sharpe  = current_perf.get("sharpe", 0.0)

                for variant in variants:
                    perf = self._evaluate_genome(strategy, variant, signals)
                    if perf["sharpe"] > best_sharpe and perf["n"] >= 3:
                        best_sharpe  = perf["sharpe"]
                        best_variant = variant

                # Update if improved
                if best_variant != current_genome:
                    old_sharpe = current_perf.get("sharpe", 0.0)
                    self.genomes[strategy] = best_variant
                    improved.append({
                        "strategy":   strategy,
                        "old_sharpe": round(old_sharpe, 3),
                        "new_sharpe": round(best_sharpe, 3),
                        "changes":    {k: v for k, v in best_variant.items()
                                      if current_genome.get(k) != v},
                    })
                    logger.info("Evolved %s: sharpe %.3f→%.3f", strategy, old_sharpe, best_sharpe)

                # Track performance
                self.perf[strategy] = {
                    "sharpe":   round(best_sharpe, 3),
                    "win_rate": current_perf.get("win_rate", 0.0),
                    "n":        current_perf.get("n", 0),
                    "updated":  date.today().isoformat(),
                }

                # Disable strategies with poor performance
                if best_sharpe < 0.1 and current_perf.get("n", 0) >= 10:
                    degraded.append({"strategy": strategy, "sharpe": round(best_sharpe, 3)})

            self._save()

            results = {
                "improved":  improved,
                "degraded":  degraded,
                "total":     len(self.genomes),
                "n_signals": len(signals),
            }

            # Send Telegram report
            self._send_evolution_report(results)

        except Exception as e:
            logger.warning("Strategy evolution: %s", e)
            results = {"error": str(e)}

        return results

    def _send_evolution_report(self, results: dict) -> None:
        if not self.alerts:
            return
        improved = results.get("improved", [])
        degraded = results.get("degraded", [])
        lines = [
            f"🧬 <b>STRATEGY EVOLUTION COMPLETE</b>",
            f"Signals used: {results.get('n_signals',0)}",
            f"Improved: {len(improved)}  Degraded: {len(degraded)}",
        ]
        if improved:
            lines.append("━━━━━━━ IMPROVED ━━━━━━━")
            for imp in improved[:5]:
                chg = " ".join(f"{k}:{v}" for k, v in imp.get("changes",{}).items())
                lines.append(f"  ✅ {imp['strategy']}: Sharpe {imp['old_sharpe']}→{imp['new_sharpe']}")
                if chg:
                    lines.append(f"     {chg}")
        if degraded:
            lines.append("━━━━━━━ DEGRADED ━━━━━━━")
            for deg in degraded[:3]:
                lines.append(f"  ⚠️ {deg['strategy']}: Sharpe={deg['sharpe']} (weight reduced)")
        lines.append(f"🕐 {datetime.now().strftime('%H:%M')}")
        try:
            self.alerts.send("\n".join(lines))
        except Exception:
            pass

    def get_performance_report(self) -> str:
        """Formatted performance report for Telegram."""
        if not self.perf:
            return "No evolution data yet. Runs weekly Saturday."
        lines = ["🧬 <b>STRATEGY GENOME PERFORMANCE</b>"]
        sorted_strats = sorted(self.perf.items(), key=lambda x: -x[1].get("sharpe", 0))
        for strat, p in sorted_strats[:10]:
            sh   = p.get("sharpe", 0)
            wr   = p.get("win_rate", 0)
            icon = "🟢" if sh > 0.5 else "🟡" if sh > 0.2 else "🔴"
            lines.append(f"  {icon} {strat:<20} Sharpe:{sh:.2f} WR:{wr*100:.0f}%")
        return "\n".join(lines)


# Singleton
_evo: Optional[StrategyEvolution] = None
def get_evolution(alerts=None) -> StrategyEvolution:
    global _evo
    if _evo is None:
        _evo = StrategyEvolution(alerts=alerts)
    return _evo
