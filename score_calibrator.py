"""
score_calibrator.py — Empirical Score Threshold Calibration

Tracks win rate by score bucket to empirically validate thresholds.
Answers: "Is a score-7 signal actually better than score-5?"

After 100+ trades, this will tell you:
  score 3.5-4.0: win_rate=XX%  (too low? raise threshold)
  score 4.0-5.0: win_rate=XX%
  score 5.0-6.0: win_rate=XX%
  score 6.0-7.0: win_rate=XX%
  score 7.0+:    win_rate=XX%  (if not highest, confluence broken)

Also validates confluence levels:
  SINGLE:      win_rate=XX%
  WEAK:        win_rate=XX%
  MEDIUM:      win_rate=XX%
  STRONG:      win_rate=XX%
  VERY_STRONG: win_rate=XX%  (should be highest)
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_FILE = Path("score_calibration.json")


class ScoreCalibrator:
    """Records signal outcomes by score bucket and confluence level."""

    SCORE_BUCKETS = [(3.5,4.0),(4.0,5.0),(5.0,6.0),(6.0,7.0),(7.0,9.0),(9.0,20.0)]
    CONFLUENCE    = ["SINGLE","WEAK","MEDIUM","STRONG","VERY_STRONG"]

    def __init__(self) -> None:
        self._data = self._load()

    def record(
        self,
        score:      float,
        confluence: str,
        strategy:   str,
        won:        bool,
        pnl:        float,
        regime:     str = "",
        day:        Optional[str] = None,
    ) -> None:
        """Record a closed trade outcome."""
        day = day or date.today().isoformat()
        bucket = self._bucket(score)
        key    = f"{bucket}|{confluence}"
        if key not in self._data:
            self._data[key] = {"wins":0,"losses":0,"pnl":0.0,"scores":[]}
        d = self._data[key]
        d["wins"]   += 1 if won else 0
        d["losses"] += 0 if won else 1
        d["pnl"]    += pnl
        d["scores"].append(round(score,2))
        d["scores"]  = d["scores"][-200:]  # keep last 200
        # Track per-strategy outcome count for the min-sample guard
        if strategy:
            counts = self._data.setdefault("_strategy_counts", {})
            counts[strategy] = counts.get(strategy, 0) + 1
        self._save()

    def strategy_sample_count(self, strategy: str) -> int:
        """Number of recorded outcomes for this strategy."""
        return self._data.get("_strategy_counts", {}).get(strategy, 0)

    def has_min_samples(self, strategy: str, min_outcomes: int = 30) -> bool:
        """
        Returns True only when a strategy has >= min_outcomes recorded outcomes.
        Callers MUST check this before adjusting strategy weights to avoid
        moving weights on statistically insignificant data.
        """
        n = self.strategy_sample_count(strategy)
        if n < min_outcomes:
            logger.debug(
                "Score calibrator: %s has %d outcomes (need %d) — weight update blocked",
                strategy, n, min_outcomes,
            )
            return False
        return True

    def _bucket(self, score: float) -> str:
        for lo,hi in self.SCORE_BUCKETS:
            if lo <= score < hi:
                return f"{lo}-{hi}"
        return "9+"

    def summary(self) -> str:
        """Format calibration report for Telegram."""
        if not self._data:
            return "📊 No calibration data yet — needs 100+ trades"
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "  📊 SCORE CALIBRATION REPORT",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "<b>By Score Bucket:</b>",
        ]
        for lo,hi in self.SCORE_BUCKETS:
            key = f"{lo}-{hi}"
            matches = {k:v for k,v in self._data.items() if k.startswith(f"{key}|")}
            if not matches:
                continue
            total_w = sum(d["wins"]   for d in matches.values())
            total_l = sum(d["losses"] for d in matches.values())
            total   = total_w + total_l
            if total < 5:
                continue
            wr  = total_w / total * 100
            icon = "🟢" if wr >= 55 else "🟡" if wr >= 48 else "🔴"
            lines.append(f"  {icon} Score {key:>7}: {wr:.0f}% WR ({total} trades)")

        lines += ["", "<b>By Confluence Level:</b>"]
        for conf in self.CONFLUENCE:
            matches = {k:v for k,v in self._data.items() if conf in k}
            if not matches:
                continue
            total_w = sum(d["wins"]   for d in matches.values())
            total_l = sum(d["losses"] for d in matches.values())
            total   = total_w + total_l
            if total < 3:
                continue
            wr   = total_w / total * 100
            icon = "🟢" if wr >= 55 else "🟡" if wr >= 48 else "🔴"
            lines.append(f"  {icon} {conf:<12}: {wr:.0f}% WR ({total} trades)")

        # Recommendation
        lines += ["", "<b>💡 THRESHOLD RECOMMENDATION</b>"]
        best_bucket = None; best_wr = 0
        for lo,hi in self.SCORE_BUCKETS:
            key = f"{lo}-{hi}"
            matches = {k:v for k,v in self._data.items() if k.startswith(f"{key}|")}
            if not matches: continue
            tw = sum(d["wins"] for d in matches.values())
            tl = sum(d["losses"] for d in matches.values())
            if tw+tl < 10: continue
            wr = tw/(tw+tl)*100
            if wr > best_wr: best_wr, best_bucket = wr, lo
        if best_bucket:
            lines.append(f"  Raise threshold to {best_bucket:.1f} for best win rate ({best_wr:.0f}%)")
        else:
            lines.append("  Need 100+ trades for reliable recommendation")

        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    def _save(self) -> None:
        try: _FILE.write_text(json.dumps(self._data))
        except Exception: pass

    def _load(self) -> dict:
        try:
            if _FILE.exists():
                return json.loads(_FILE.read_text())
        except Exception: pass
        return {}


_cal: Optional[ScoreCalibrator] = None
def get_calibrator() -> ScoreCalibrator:
    global _cal
    if _cal is None: _cal = ScoreCalibrator()
    return _cal
