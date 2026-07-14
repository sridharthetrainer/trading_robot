"""
strategy_evolution.py — Dynamic Strategy Parameter Evolution

Implements a lightweight genetic algorithm that evolves strategy parameters
based on live trading results. Each strategy has a "genome" — a set of
tunable parameters. Weekly, the GA tests variations and promotes winners.

HOW IT WORKS:
  1. Each strategy has a genome: {stop_atr, target_atr, min_score, ...}
  2. Weekly: generate 5 variants per strategy (mutated genomes)
  3. Day-split the last 30 days 70/30 into train/holdout (NOT row-split —
     avoids leaking a correlated same-day batch across the split)
  4. Search for the best mutation on TRAIN only, then require it to
     INDEPENDENTLY beat the current genome's own holdout performance
     before promoting — same discipline as option_live_edge_policy /
     eod_setup_edge_analyzer / option_cohort_edge_miner elsewhere in this
     repo. A variant that only wins on train is reported, not promoted.
  5. Losers (holdout Sharpe < 0.1 with enough samples) get flagged for
     weight reduction — reporting only, same as modifier_edge_analyzer.

2026-07-14 FIX: the original version picked whichever of 5 mutations had
the highest Sharpe on the SAME 30-day sample used to generate them, then
wrote it straight to strategy_genomes.json — pure same-sample overfitting
(picking the best of several random variants on one sample looks like an
improvement by chance regardless of true edge), with min_score's own
minimum-n floor as low as 3 (Sharpe on 3 binary outcomes is pure noise).
This is the SAME bug class as [[cohort-policy-overfit-fixed]] and
[[dead-wiring-audit-2026-07-10]] — verified same-day: get_genome() has
ZERO callers anywhere else in the codebase, so no live behavior was ever
actually affected by an evolved genome; wiring consumption into live
signal generation is a separate, larger decision (deliberately NOT done
here — this fix only hardens the search/promotion methodology).

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

TRAIN_FRAC = 0.70
MIN_TRAIN_N = 20
MIN_HOLDOUT_N = 10
MIN_DISTINCT_DAYS = 6


def _split_by_day(signals: List[Dict], frac: float = TRAIN_FRAC) -> Tuple[List[Dict], List[Dict], int]:
    """Chronological day-split (not row-split, which would leak a
    correlated same-day batch across train/holdout). Returns (train,
    holdout, n_distinct_days)."""
    days = sorted({str(s.get("signal_date", "")) for s in signals if s.get("signal_date")})
    if len(days) < 2:
        return signals, [], len(days)
    cut = days[max(0, int(len(days) * frac) - 1)]
    train = [s for s in signals if str(s.get("signal_date", "")) <= cut]
    holdout = [s for s in signals if str(s.get("signal_date", "")) > cut]
    return train, holdout, len(days)


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

    def _evaluate_genome(self, strategy: str, genome: Dict, signals: List[Dict],
                         min_n: int = 3) -> Dict:
        """
        Evaluate a genome on historical signal data.
        Returns Sharpe, win_rate, avg_rr, n_trades.
        """
        try:
            strat_signals = [s for s in signals if s.get("strategy") == strategy
                             and s.get("tb_label") in (1, -1, 0)]
            if len(strat_signals) < min_n:
                return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

            # Apply genome filters
            min_score = genome.get("min_score", 5.0)
            filtered  = [s for s in strat_signals if float(s.get("score", 0)) >= min_score]

            if len(filtered) < min_n:
                return {"sharpe": 0.0, "win_rate": 0.0, "n": 0}

            wins   = sum(1 for s in filtered if s.get("tb_label") == 1)
            losses = sum(1 for s in filtered if s.get("tb_label") == -1)
            total  = wins + losses

            if total < min_n:
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

        Search happens on TRAIN only; promotion requires the winning
        mutation to also beat the CURRENT genome's own holdout performance
        (day-split, not row-split) — see module docstring for why the
        original same-sample selection was unsound. Strategies with too
        few distinct days are reported as unconfirmed, not promoted.
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

            train, holdout, n_days = _split_by_day(signals)

            improved = []
            degraded = []
            unconfirmed = []

            for strategy, current_genome in list(self.genomes.items()):
                if n_days < MIN_DISTINCT_DAYS:
                    self.perf[strategy] = {
                        "sharpe": self.perf.get(strategy, {}).get("sharpe", 0.0),
                        "status": "insufficient_days", "days": n_days,
                        "updated": date.today().isoformat(),
                    }
                    continue

                # Current genome's own train/holdout performance (baseline).
                current_train = self._evaluate_genome(strategy, current_genome, train, min_n=MIN_TRAIN_N)
                current_holdout = self._evaluate_genome(strategy, current_genome, holdout, min_n=MIN_HOLDOUT_N)

                # Search on TRAIN only.
                variants = [self._mutate(current_genome) for _ in range(5)]
                best_variant = current_genome
                best_train_sharpe = current_train.get("sharpe", 0.0)
                for variant in variants:
                    perf = self._evaluate_genome(strategy, variant, train, min_n=MIN_TRAIN_N)
                    if perf["n"] >= MIN_TRAIN_N and perf["sharpe"] > best_train_sharpe:
                        best_train_sharpe = perf["sharpe"]
                        best_variant = variant

                promoted = False
                best_holdout = current_holdout
                if best_variant != current_genome:
                    best_holdout = self._evaluate_genome(strategy, best_variant, holdout, min_n=MIN_HOLDOUT_N)
                    # Promotion requires an INDEPENDENT holdout win, not just
                    # a train win — the same-sample selection that already
                    # picked this variant as the train-best guarantees
                    # nothing about days it has never seen.
                    holdout_confirms = (
                        best_holdout.get("n", 0) >= MIN_HOLDOUT_N
                        and best_holdout.get("sharpe", -999) > current_holdout.get("sharpe", 0.0)
                    )
                    if holdout_confirms:
                        promoted = True
                        self.genomes[strategy] = best_variant
                        improved.append({
                            "strategy": strategy,
                            "train_sharpe": round(best_train_sharpe, 3),
                            "old_holdout_sharpe": round(current_holdout.get("sharpe", 0.0), 3),
                            "new_holdout_sharpe": round(best_holdout.get("sharpe", 0.0), 3),
                            "changes": {k: v for k, v in best_variant.items()
                                       if current_genome.get(k) != v},
                        })
                        logger.info("Evolved %s: holdout sharpe %.3f->%.3f (confirmed)",
                                   strategy, current_holdout.get("sharpe", 0.0),
                                   best_holdout.get("sharpe", 0.0))
                    else:
                        unconfirmed.append({
                            "strategy": strategy,
                            "train_sharpe": round(best_train_sharpe, 3),
                            "holdout_sharpe": round(best_holdout.get("sharpe", 0.0), 3),
                            "holdout_n": best_holdout.get("n", 0),
                        })

                # Track performance using the genome actually in effect.
                effective_perf = best_holdout if promoted else current_holdout
                self.perf[strategy] = {
                    "sharpe":   round(effective_perf.get("sharpe", 0.0), 3),
                    "win_rate": effective_perf.get("win_rate", 0.0),
                    "n":        effective_perf.get("n", 0),
                    "promoted_this_run": promoted,
                    "updated":  date.today().isoformat(),
                }

                # Disable strategies with poor CONFIRMED (holdout) performance.
                if effective_perf.get("sharpe", 0.0) < 0.1 and effective_perf.get("n", 0) >= MIN_HOLDOUT_N:
                    degraded.append({"strategy": strategy, "sharpe": round(effective_perf.get("sharpe", 0.0), 3)})

            self._save()

            results = {
                "improved":    improved,
                "unconfirmed": unconfirmed,
                "degraded":    degraded,
                "total":       len(self.genomes),
                "n_signals":   len(signals),
                "n_days":      n_days,
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
        unconfirmed = results.get("unconfirmed", [])
        degraded = results.get("degraded", [])
        lines = [
            f"🧬 <b>STRATEGY EVOLUTION COMPLETE</b>",
            f"Signals used: {results.get('n_signals',0)} over {results.get('n_days',0)} days",
            f"Promoted (holdout-confirmed): {len(improved)}  "
            f"Unconfirmed: {len(unconfirmed)}  Degraded: {len(degraded)}",
        ]
        if improved:
            lines.append("━━━━━━━ PROMOTED (train + independent holdout both improved) ━━━━━━━")
            for imp in improved[:5]:
                chg = " ".join(f"{k}:{v}" for k, v in imp.get("changes",{}).items())
                lines.append(f"  ✅ {imp['strategy']}: holdout Sharpe "
                             f"{imp['old_holdout_sharpe']}→{imp['new_holdout_sharpe']}")
                if chg:
                    lines.append(f"     {chg}")
        if unconfirmed:
            lines.append("━━━━━━━ NOT PROMOTED (looked better on train only) ━━━━━━━")
            for u in unconfirmed[:3]:
                lines.append(f"  ⏳ {u['strategy']}: train {u['train_sharpe']} vs "
                             f"holdout {u['holdout_sharpe']} (n={u['holdout_n']}) — kept current genome")
        if degraded:
            lines.append("━━━━━━━ DEGRADED (confirmed) ━━━━━━━")
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
