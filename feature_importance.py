"""
feature_importance.py  —  Track which signal modifiers actually predict outcomes.

After every closed trade, record:
  - All 15+ score modifiers applied to that signal
  - The actual outcome (TB label or net P&L)

Periodically compute Information Coefficient (IC) for each modifier:
  IC = rank_correlation(modifier_value, actual_return)
  IC > 0.05  → modifier has predictive value → KEEP
  IC < 0.02  → modifier is noise → flag for removal
  IC < 0     → modifier is ANTI-predictive → reduce weight

This is how Renaissance knows which of their 100s of signals matter.
"""
from __future__ import annotations
import json, logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)
_IC_FILE = Path("feature_ic.json")

MODIFIER_NAMES = [
    "pivot_indicator", "ai_filter", "rl_bias", "ofi_score", "iv_skew",
    "hurst", "pcr", "delivery_pct", "fii_penalty", "breadth",
    "regime_adj", "time_weight", "strategy_matrix", "vix_adj",
    "cross_asset", "strike_pcr", "bhav_delivery",
]


class FeatureImportanceTracker:
    """
    Tracks IC of each score modifier.
    Stores: modifier_value + actual outcome per trade.
    Computes: Spearman rank IC after 20+ observations.
    """

    MIN_SAMPLES_FOR_IC = 20

    def __init__(self) -> None:
        self._data: Dict[str, List[dict]] = defaultdict(list)
        self._load()

    def record(self, modifiers: dict, outcome: float) -> None:
        """
        Record modifier values and outcome for one trade.
        modifiers = {"pivot_indicator": 1.5, "ai_filter": 0.8, ...}
        outcome   = net P&L or Triple Barrier label (+1/0/-1)
        """
        for name, value in modifiers.items():
            self._data[name].append({
                "value":   float(value),
                "outcome": float(outcome),
            })
        self._save()

    def compute_ic(self) -> Dict[str, dict]:
        """
        Compute IC for all modifiers with sufficient data.
        Returns {modifier: {ic, n, verdict}}.
        """
        results = {}
        for name, records in self._data.items():
            if len(records) < self.MIN_SAMPLES_FOR_IC:
                results[name] = {
                    "ic": None, "n": len(records),
                    "verdict": f"insufficient ({len(records)}/{self.MIN_SAMPLES_FOR_IC})",
                }
                continue
            values   = [r["value"]   for r in records]
            outcomes = [r["outcome"] for r in records]
            ic = _spearman_ic(values, outcomes)
            if ic > 0.05:
                verdict = "STRONG — keep and potentially increase weight"
            elif ic > 0.02:
                verdict = "WEAK — marginal predictive value"
            elif ic > -0.02:
                verdict = "NOISE — remove from scoring"
            else:
                verdict = "ANTI-PREDICTIVE — invert or remove immediately"
            results[name] = {"ic": round(ic, 4), "n": len(records), "verdict": verdict}

        return results

    def get_weight_adjustments(self) -> dict:
        """
        Returns multipliers for each modifier based on IC.
        Strong IC → use at full weight.
        Noise IC  → weight = 0 (suppress).
        Anti-predictive → weight = -0.5 (flip sign).
        """
        ic_data = self.compute_ic()
        adjustments = {}
        for name, d in ic_data.items():
            ic = d.get("ic")
            if ic is None:
                adjustments[name] = 1.0   # unknown → keep at full weight
            elif ic > 0.05:
                adjustments[name] = 1.0 + min(ic * 5, 0.5)  # up to 1.5×
            elif ic > 0.02:
                adjustments[name] = 0.7
            elif ic > -0.02:
                adjustments[name] = 0.1   # nearly suppress
            else:
                adjustments[name] = -0.3  # flip anti-predictive modifiers
        return adjustments

    def format_report(self) -> str:
        """Human-readable IC report for Telegram."""
        ic_data = self.compute_ic()
        lines   = ["📊 <b>FEATURE IMPORTANCE REPORT</b>", "─" * 30]
        strong  = [(n, d) for n, d in ic_data.items() if d.get("ic") and d["ic"] > 0.05]
        noise   = [(n, d) for n, d in ic_data.items() if d.get("ic") and d["ic"] < 0.02]
        for n, d in sorted(strong, key=lambda x: x[1].get("ic",0), reverse=True)[:5]:
            lines.append(f"✅ {n}: IC={d['ic']:.3f} ({d['n']} trades)")
        if noise:
            lines.append("\n⚠️ Low-IC modifiers (consider removing):")
            for n, d in noise[:3]:
                lines.append(f"  ❌ {n}: IC={d.get('ic','?')} n={d['n']}")
        return "\n".join(lines)

    def _save(self) -> None:
        try:
            _IC_FILE.write_text(json.dumps(dict(self._data)))
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if _IC_FILE.exists():
                raw = json.loads(_IC_FILE.read_text())
                self._data = defaultdict(list, raw)
        except Exception:
            pass


def _spearman_ic(x: list, y: list) -> float:
    """Spearman rank correlation between x and y."""
    n = len(x)
    if n < 3:
        return 0.0
    rx = _rank(x); ry = _rank(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    if n == 1:
        return 0.0
    ic = 1 - 6 * d2 / (n * (n**2 - 1))
    return float(ic)


def _rank(lst: list) -> list:
    sorted_vals = sorted(enumerate(lst), key=lambda x: x[1])
    ranks = [0.0] * len(lst)
    for rank, (idx, _) in enumerate(sorted_vals, 1):
        ranks[idx] = float(rank)
    return ranks


# Singleton
_tracker = None
def get_tracker() -> FeatureImportanceTracker:
    global _tracker
    if _tracker is None:
        _tracker = FeatureImportanceTracker()
    return _tracker
