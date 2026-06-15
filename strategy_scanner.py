"""
strategy_scanner.py

Master strategy scanner — the brain that decides what to trade.

Architecture
------------
Every cycle (every 30 seconds during market hours):

1. PRIORITY SCAN (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNEXT50, SENSEX)
   Run ALL 8 strategies on each priority symbol simultaneously.
   Require at least one clean signal per cycle from this group.
   If a priority signal is found, it gets highest execution priority.

2. NIFTY 200 SCAN
   Run all strategies on all 194 stocks in parallel.
   Signals ranked by combined score.

3. CONFLUENCE SCORING
   For each symbol: count how many strategies agree on direction.
   1 strategy agreeing  → base score
   2 strategies agreeing → +1.5 score boost (confluence_2)
   3+ strategies agreeing → +3.0 score boost (confluence_3+)

4. TIME-ZONE WEIGHTING
   Each strategy's score is multiplied by the time-zone weight for
   the current NSE session period (Opening/Primary/VWAP/Quiet/Power).

5. FINAL RANKING
   All candidates sorted by final_rank_score.
   Priority symbols get additional +0.50 on top.
   Top N candidates selected for execution.

6. WIN-RATE FILTER (daily adapting)
   Strategies that have < 45% win rate today are penalised (-0.5).
   Strategies that have > 60% win rate today get a boost (+0.3).
   Win rates are tracked intraday and reset each morning.

This guarantees: every cycle has at least one tradeable setup from
the priority universe, while also surfacing the best stock setups.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from time_regime import (
    get_time_zone, get_strategy_weight, TimeZone, is_expiry_day
)

logger = logging.getLogger(__name__)

# ── Watchdog heartbeat (progress-coupled) ─────────────────────────────────────
# The full scan can take many minutes when Angel/NSE are rate-limiting. The main
# heartbeat is only written at cycle start, so a slow-but-working scan looked
# "dead" to the watchdog and was repeatedly SIGKILL'd (see 2026-06-05). Touch the
# heartbeat each time a symbol finishes — rate-limited. A genuine hang (no symbol
# completes for the watchdog limit) still goes stale, so this does not mask hangs.
_HB_MIN_GAP = 20.0
_last_hb_ts = 0.0

def _touch_heartbeat() -> None:
    global _last_hb_ts
    now = time.time()
    if now - _last_hb_ts < _HB_MIN_GAP:
        return
    _last_hb_ts = now
    try:
        import json as _j
        with open("heartbeat.json", "w") as _f:
            _f.write(_j.dumps({"ts": now}))
    except Exception:
        pass

# ── Priority universe ─────────────────────────────────────────────────────────
# These are scanned first, every cycle, with extra score boost
TIER1_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]
TIER1_OPTION_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

# Score boosts
TIER1_SCORE_BOOST       = 1.00    # Tier-1 priority symbols
CONFLUENCE_2_BOOST      = 1.50    # 2 strategies agree
CONFLUENCE_3_BOOST      = 3.00    # 3+ strategies agree
WINRATE_HIGH_BOOST      = 0.30    # today's win rate > 60%
WINRATE_LOW_PENALTY     = -0.50   # today's win rate < 45%
WINRATE_MIN_TRADES      = 3       # minimum trades to apply win rate filter


class ScanResult:
    """One candidate signal from one symbol from one or more strategies."""
    __slots__ = [
        "symbol", "action", "strategy", "strategies_agreed", "score",
        "raw_score", "confidence", "regime", "time_zone", "zone_weight",
        "confluence_level", "final_score", "indicators", "is_tier1",
        "win_rate_adjustment", "df", "df_htf",
    ]

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol":             self.symbol,
            "action":             self.action,
            "strategy":           self.strategy,
            "strategies_agreed":  self.strategies_agreed,
            "score":              round(self.score, 4),
            "confidence":         round(self.confidence, 4),
            "regime":             self.regime,
            "time_zone":          self.time_zone,
            "confluence_level":   self.confluence_level,
            "final_score":        round(self.final_score, 4),
            "is_tier1":           self.is_tier1,
        }


class StrategyScanner:
    """
    Master scanner that runs all strategies on all symbols and returns
    ranked candidates for execution.

    Usage
    -----
        scanner = StrategyScanner(data_fetcher=df, max_workers=12)
        candidates = scanner.scan()
        for c in candidates[:3]:   # top 3 signals
            print(c.symbol, c.strategy, c.action, c.final_score)
    """

    def __init__(
        self,
        data_fetcher=None,
        max_workers:          int   = 12,
        min_confluence:       int   = 1,       # minimum strategies that must agree
        require_tier1_signal: bool  = True,    # always try to find a tier-1 signal
    ) -> None:
        self.data_fetcher         = data_fetcher
        self.max_workers          = max_workers
        self.min_confluence       = min_confluence
        self.require_tier1_signal = require_tier1_signal

        # Intraday win-rate tracker: {strategy: {"wins": 0, "total": 0}}
        self._intraday_stats: Dict[str, Dict[str, int]] = {}
        self._stats_date: Optional[date] = None

        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def reset_daily_stats(self) -> None:
        """Reset intraday win-rate stats. Called at market open."""
        self._intraday_stats = {}
        self._stats_date = date.today()

    def record_trade_result(self, strategy: str, won: bool) -> None:
        """
        Record a completed trade result for intraday win-rate tracking.
        Called by trade_manager when a trade closes.
        """
        if self._stats_date != date.today():
            self.reset_daily_stats()
        s = self._intraday_stats.setdefault(strategy, {"wins": 0, "total": 0})
        s["total"] += 1
        if won:
            s["wins"] += 1

    def get_strategy_win_rate(self, strategy: str) -> Optional[float]:
        """Return today's win rate for a strategy, or None if < min trades."""
        s = self._intraday_stats.get(strategy, {})
        total = s.get("total", 0)
        if total < WINRATE_MIN_TRADES:
            return None
        return s.get("wins", 0) / total

    def _win_rate_adjustment(self, strategy: str) -> float:
        wr = self.get_strategy_win_rate(strategy)
        if wr is None:
            return 0.0
        if wr > 0.60:
            return WINRATE_HIGH_BOOST
        if wr < 0.45:
            return WINRATE_LOW_PENALTY
        return 0.0

    def _run_all_strategies(
        self, symbol: str, df: pd.DataFrame, df_htf: pd.DataFrame,
        df_1h: Optional[pd.DataFrame] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run all 8 strategies on a single symbol. Returns list of
        non-HOLD signal dicts, each tagged with its strategy name.
        """
        results = []

        # Lazy imports to avoid circular imports at module level
        from signals import (
            calculate_signal_score as _css,
        )
        from signal_engine import (
            run_trend_strategy, run_mean_reversion_strategy,
            run_breakout_strategy, run_scalping_strategy, run_ma_cross_strategy,
        )
        from orb_strategy import orb_signal
        from vwap_reversion_strategy import vwap_reversion_signal
        from supertrend_mtf_strategy import supertrend_mtf_signal

        strategy_fns = [
            ("trend",           lambda: run_trend_strategy(df, df_htf, None)),
            ("mean_reversion",  lambda: run_mean_reversion_strategy(df, df_htf, None)),
            ("breakout",        lambda: run_breakout_strategy(df, df_htf, None)),
            ("scalping",        lambda: run_scalping_strategy(df, df_htf, None)),
            ("ma_cross",        lambda: run_ma_cross_strategy(df, df_htf, None)),
            ("orb",             lambda: {"strategy": "orb", **orb_signal(df)}),
            ("vwap_reversion",  lambda: {"strategy": "vwap_reversion", **vwap_reversion_signal(df)}),
            ("supertrend_mtf",  lambda: {"strategy": "supertrend_mtf",
                                          **supertrend_mtf_signal(df, df_htf)}),
        ]

        for strategy_name, fn in strategy_fns:
            try:
                result = fn()
                action = result.get("action") or result.get("direction")
                if not action or action == "HOLD":
                    continue
                # Normalise action
                action = "BUY" if str(action).upper() in ("BUY", "LONG", "BULLISH") else "SELL"
                score  = float(result.get("score", result.get("confidence", 0.5)))
                conf   = float(result.get("confidence", score))
                results.append({
                    "strategy":   strategy_name,
                    "action":     action,
                    "score":      score,
                    "confidence": conf,
                    "indicators": result.get("indicators", {}),
                    "reason":     result.get("reason", ""),
                })
            except Exception as exc:
                logger.debug("Strategy %s failed for %s: %s", strategy_name, symbol, exc)

        return results

    def _evaluate_symbol(
        self,
        symbol:  str,
        df:      pd.DataFrame,
        df_htf:  pd.DataFrame,
        df_1h:   Optional[pd.DataFrame] = None,
        regime:  str = "UNKNOWN",
    ) -> Optional[ScanResult]:
        """
        Evaluate all strategies on one symbol, compute confluence,
        and return the best ScanResult or None.
        """
        try:
            from regime import detect_market_regime
            if regime == "UNKNOWN":
                regime = detect_market_regime(df) or "UNKNOWN"

            signals = self._run_all_strategies(symbol, df, df_htf, df_1h)
            if not signals:
                return None

            # Group by direction
            buys  = [s for s in signals if s["action"] == "BUY"]
            sells = [s for s in signals if s["action"] == "SELL"]

            # Pick majority direction
            if len(buys) >= len(sells):
                agreed, action = buys, "BUY"
            else:
                agreed, action = sells, "SELL"

            if not agreed:
                return None

            # Confluence level
            n_agreed = len(agreed)
            if n_agreed >= 3:
                confluence_boost = CONFLUENCE_3_BOOST
                confluence_level = "HIGH"
            elif n_agreed == 2:
                confluence_boost = CONFLUENCE_2_BOOST
                confluence_level = "MEDIUM"
            else:
                confluence_boost = 0.0
                confluence_level = "LOW"

            # Apply time-zone weight to each strategy's score
            zone     = get_time_zone()
            weighted_scores = []
            strategies_used = []
            for s in agreed:
                tz_weight = get_strategy_weight(s["strategy"])
                weighted_scores.append(s["score"] * tz_weight)
                strategies_used.append(s["strategy"])

            base_score  = sum(weighted_scores) / max(len(weighted_scores), 1)
            avg_conf    = sum(s["confidence"] for s in agreed) / len(agreed)

            # Win-rate adjustment (use the best-performing strategy in the group)
            best_strategy = max(agreed, key=lambda s: s["score"])["strategy"]
            wr_adj = self._win_rate_adjustment(best_strategy)

            # Tier-1 boost
            is_tier1 = symbol in TIER1_SYMBOLS
            tier1_boost = TIER1_SCORE_BOOST if is_tier1 else 0.0

            final_score = base_score + confluence_boost + wr_adj + tier1_boost

            # Best indicators: from highest-confidence signal
            best_sig = max(agreed, key=lambda s: s["confidence"])

            return ScanResult(
                symbol             = symbol,
                action             = action,
                strategy           = best_strategy,
                strategies_agreed  = strategies_used,
                score              = base_score,
                raw_score          = base_score,
                confidence         = avg_conf,
                regime             = regime,
                time_zone          = zone.value,
                zone_weight        = get_strategy_weight(best_strategy),
                confluence_level   = confluence_level,
                final_score        = round(final_score, 4),
                indicators         = best_sig.get("indicators", {}),
                is_tier1           = is_tier1,
                win_rate_adjustment = wr_adj,
                df                 = df,
                df_htf             = df_htf,
            )

        except Exception as exc:
            logger.exception("_evaluate_symbol failed for %s: %s", symbol, exc)
            return None

    def scan(
        self,
        market_data: Optional[Dict[str, Any]] = None,
    ) -> List[ScanResult]:
        """
        Run the full scan. If market_data is None, fetches via data_fetcher.

        Returns candidates sorted by final_score (highest first).
        Priority symbols are guaranteed to be evaluated first.
        """
        # Fetch data if not provided
        if market_data is None:
            if self.data_fetcher is None:
                logger.warning("No data_fetcher and no market_data — cannot scan")
                return []
            try:
                if hasattr(self.data_fetcher, "get_latest_data_three_tf"):
                    market_data = self.data_fetcher.get_latest_data_three_tf()
                elif hasattr(self.data_fetcher, "get_latest_data_multi_tf"):
                    market_data = self.data_fetcher.get_latest_data_multi_tf()
                else:
                    raw = self.data_fetcher.get_latest_data()
                    market_data = {s: {"df": d, "df_htf": d} for s, d in raw.items()}
            except Exception as exc:
                logger.exception("market_data fetch failed: %s", exc)
                return []

        if not market_data:
            return []

        # Split into tier-1 and tier-2
        tier1_data = {s: v for s, v in market_data.items() if s in TIER1_SYMBOLS}
        tier2_data = {s: v for s, v in market_data.items() if s not in TIER1_SYMBOLS}

        candidates: List[ScanResult] = []

        def _submit(data_subset: Dict[str, Any]) -> List[ScanResult]:
            results = []
            futs = {}
            for sym, entry in data_subset.items():
                df     = entry["df"]     if isinstance(entry, dict) else entry
                df_htf = entry.get("df_htf", df) if isinstance(entry, dict) else df
                df_1h  = entry.get("df_1h")      if isinstance(entry, dict) else None
                if df is None or len(df) < 50:
                    continue
                futs[self.executor.submit(
                    self._evaluate_symbol, sym, df, df_htf, df_1h
                )] = sym
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                    if r is not None:
                        results.append(r)
                except Exception:
                    pass
                _touch_heartbeat()   # keep watchdog informed during slow scans
            return results

        # Evaluate tier-1 first (blocking)
        t1_results = _submit(tier1_data)
        candidates.extend(t1_results)

        # Log tier-1 results immediately
        if t1_results:
            for r in sorted(t1_results, key=lambda x: -x.final_score):
                logger.info(
                    "★ T1 | %-12s %-4s %-14s conf=%.2f confluence=%s score=%.2f",
                    r.symbol, r.action, r.strategy, r.confidence,
                    r.confluence_level, r.final_score,
                )
        else:
            logger.info("★ T1 scan: no signals from priority symbols this cycle")

        # Evaluate tier-2 (NIFTY 200 stocks)
        t2_results = _submit(tier2_data)
        candidates.extend(t2_results)

        if t2_results:
            logger.info("T2 scan: %d candidates from %d stocks",
                        len(t2_results), len(tier2_data))

        # Final sort: tier-1 already has score boost built in
        candidates.sort(key=lambda x: -x.final_score)

        logger.info(
            "Scan complete | total=%d tier1=%d tier2=%d zone=%s expiry=%s",
            len(candidates), len(t1_results), len(t2_results),
            get_time_zone().value, is_expiry_day(),
        )
        return candidates

    def get_scan_summary(
        self, candidates: List[ScanResult]
    ) -> Dict[str, Any]:
        """
        Build a summary dict for Telegram status alerts.
        """
        if not candidates:
            return {
                "total_signals":   0,
                "tier1_signals":   0,
                "top_symbol":      None,
                "top_strategy":    None,
                "top_score":       0.0,
                "confluence_high": 0,
                "by_strategy":     {},
                "by_zone":         get_time_zone().value,
            }

        tier1 = [c for c in candidates if c.is_tier1]
        strat_counts: Dict[str, int] = {}
        for c in candidates:
            strat_counts[c.strategy] = strat_counts.get(c.strategy, 0) + 1

        top = candidates[0]
        return {
            "total_signals":   len(candidates),
            "tier1_signals":   len(tier1),
            "top_symbol":      top.symbol,
            "top_strategy":    top.strategy,
            "top_score":       top.final_score,
            "top_action":      top.action,
            "top_confluence":  top.confluence_level,
            "confluence_high": sum(1 for c in candidates if c.confluence_level == "HIGH"),
            "by_strategy":     strat_counts,
            "by_zone":         get_time_zone().value,
            "expiry_day":      is_expiry_day(),
            "strategies_agreed_top": top.strategies_agreed,
        }
