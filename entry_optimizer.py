"""
entry_optimizer.py

Entry optimizer for options/spot-trigger systems.

Evaluates the current candle for quality of entry given a directional
signal (BUY_CALL or BUY_PUT) and returns an EntryDecision with a
score and trigger / stop reference prices.

Supported entry styles (scored components):
- Breakout continuation  — close above/below structural level
- Wick rejection         — long wick against direction, body in direction
- VWAP reclaim / reject  — price crosses VWAP with momentum
- Pullback confirmation  — inside bar / tight-range bar after a move (NEW)
- Volume confirmation    — volume relative to recent average

Fixes applied
-------------
1. max_entry_candle_body_pct = 0.006 (0.6%) was too tight for NIFTY
   On a 5-min NIFTY bar at 22000, a 0.6% body = 132 points. Strong
   directional 5-min bars routinely have bodies of 50-200 points
   (0.23-0.9%). The old threshold rejected the majority of valid entry
   candles on volatile or trending days.
   Updated default: 0.015 (1.5%) = ~330 points at 22000.

2. min_trigger_body_pct = 0.0008 (0.08%) was too loose
   At NIFTY 22000 that is only 17 points — a doji or spinning-top
   with a tiny body would pass as a valid trigger candle.
   Updated default: 0.0015 (0.15%) = ~33 points minimum body.

3. breakout_buffer_pct = 0.0003 (0.03%) generated noise breakouts
   Only 6.6 points above previous high at NIFTY 22000 — any small
   uptick registered as a breakout. The filter had no real filtering
   effect on index intraday data.
   Updated default: 0.001 (0.1%) = ~22 points, requires a meaningful
   structural penetration.

4. vwap_reclaim required strict prev_close <= prev_vwap
   Price oscillating just above VWAP (e.g. prev_close = prev_vwap + 3)
   would never trigger the reclaim signal. Added vwap_cross_tolerance
   (default 0.1%) so price within that band of VWAP counts as "at VWAP".

5. Pullback confirmation was listed in the docstring but not implemented
   Added as a scored component (1.5 pts): a tight inside bar preceded
   by a prior directional move, indicating controlled consolidation
   before continuation rather than exhaustion.

6. Added `instrument` parameter ('index' | 'equity')
   Index instruments (NIFTY, BANKNIFTY) have larger absolute ATRs.
   When instrument='equity', all body/buffer thresholds are scaled by
   equity_tighten_factor (default 0.60) so the optimizer applies
   proportionally tighter filters for smaller-cap equity options.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class EntryDecision:
    allowed:         bool
    signal:          Optional[str]
    score:           float
    reason:          str
    entry_type:      str
    trigger_price:   Optional[float]
    stop_reference:  Optional[float]
    metadata:        Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EntryOptimizer:
    """
    Entry optimizer for options/spot-trigger systems.

    Parameters
    ----------
    lookback_bars              : bars of recent history used for structural levels
    max_entry_candle_body_pct  : reject candles with body > this % of close
    min_trigger_body_pct       : reject candles with body < this % of close
    breakout_buffer_pct        : close must exceed structural level by at least this %
    vwap_reclaim_tolerance     : close must be this % above/below VWAP for reclaim/reject
    vwap_cross_tolerance       : prev bar within this % of VWAP counts as "at VWAP"
    max_wick_body_ratio        : reject if opposing wick > body × this ratio
    min_volume_ratio           : volume must be >= avg_volume × this ratio
    pullback_max_body_pct      : max body % for a valid pullback confirmation candle
    instrument                 : 'index' (default) or 'equity'
    equity_tighten_factor      : scales all body/buffer thresholds for equity instruments
    """

    MIN_PASS_SCORE = 4.0   # minimum score required for approval

    def __init__(
        self,
        lookback_bars:             int   = 6,
        max_entry_candle_body_pct: float = 0.015,
        min_trigger_body_pct:      float = 0.0015,
        breakout_buffer_pct:       float = 0.001,
        vwap_reclaim_tolerance:    float = 0.0008,
        vwap_cross_tolerance:      float = 0.001,
        max_wick_body_ratio:       float = 1.2,
        min_volume_ratio:          float = 0.80,
        pullback_max_body_pct:     float = 0.003,
        instrument:                str   = "index",
        equity_tighten_factor:     float = 0.60,
    ) -> None:
        self.lookback_bars          = int(lookback_bars)
        self.vwap_reclaim_tolerance = float(vwap_reclaim_tolerance)
        self.vwap_cross_tolerance   = float(vwap_cross_tolerance)
        self.max_wick_body_ratio    = float(max_wick_body_ratio)
        self.min_volume_ratio       = float(min_volume_ratio)
        self.pullback_max_body_pct  = float(pullback_max_body_pct)
        self.instrument             = str(instrument).lower().strip()
        self.equity_tighten_factor  = float(equity_tighten_factor)

        # Scale thresholds for equity vs index instruments
        factor = (
            self.equity_tighten_factor
            if self.instrument not in ("index", "idx")
            else 1.0
        )
        self.max_entry_candle_body_pct = float(max_entry_candle_body_pct) * factor
        self.min_trigger_body_pct      = float(min_trigger_body_pct)      * factor
        self.breakout_buffer_pct       = float(breakout_buffer_pct)       * factor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate(
        self,
        signal: str,
        df: pd.DataFrame,
        symbol: Optional[str] = None,
    ) -> EntryDecision:
        """
        Evaluate entry quality for a directional signal.

        Parameters
        ----------
        signal : 'BUY_CALL' or 'BUY_PUT'
        df     : recent intraday OHLCV DataFrame
        symbol : optional instrument name (for logging only)
        """
        min_bars = max(8, self.lookback_bars + 2)

        if df is None or len(df) < min_bars:
            return self._deny(signal, "Not enough candles for entry optimization")

        work = self._prepare_df(df)
        if work is None or len(work) < min_bars:
            return self._deny(signal, "Invalid/insufficient OHLCV data after preparation")

        if signal == "BUY_CALL":
            return self._evaluate_buy_call(work)
        if signal == "BUY_PUT":
            return self._evaluate_buy_put(work)

        return self._deny(signal, f"Unsupported signal: {signal}")

    # ------------------------------------------------------------------
    # BUY_CALL evaluation
    # ------------------------------------------------------------------
    def _evaluate_buy_call(self, df: pd.DataFrame) -> EntryDecision:
        last   = df.iloc[-1]
        prev   = df.iloc[-2]
        recent = df.iloc[-self.lookback_bars:]

        last_open   = float(last["Open"])
        last_high   = float(last["High"])
        last_low    = float(last["Low"])
        last_close  = float(last["Close"])
        last_vwap   = float(last["VWAP"]) if pd.notna(last["VWAP"]) else None
        last_volume = float(last["Volume"])

        prev_close = float(prev["Close"])
        prev_high  = float(prev["High"])
        prev_low   = float(prev["Low"])
        prev_vwap  = float(prev["VWAP"]) if pd.notna(prev["VWAP"]) else None

        recent_high = float(recent["High"].iloc[:-1].max()) if len(recent) > 1 else prev_high
        avg_volume  = float(recent["Volume"].iloc[:-1].mean()) if len(recent) > 1 else last_volume

        body       = abs(last_close - last_open)
        body_pct   = body / max(last_close, 1e-9)
        upper_wick = last_high - max(last_open, last_close)
        lower_wick = min(last_open, last_close) - last_low

        # Hard rejects
        reject = self._hard_reject_bullish("BUY_CALL", body_pct, upper_wick, body)
        if reject is not None:
            return reject

        # Scored components
        bullish        = last_close > last_open
        strong_body    = bullish and body > upper_wick
        rejection_body = bullish and lower_wick > upper_wick * 0.8

        breakout_prev   = last_close > prev_high   * (1 + self.breakout_buffer_pct)
        breakout_recent = last_close > recent_high * (1 + self.breakout_buffer_pct)

        # VWAP reclaim: prev bar was at or below VWAP (within tolerance)
        vwap_reclaim = (
            last_vwap is not None
            and prev_vwap is not None
            and last_close > last_vwap * (1 + self.vwap_reclaim_tolerance)
            and prev_close <= prev_vwap * (1 + self.vwap_cross_tolerance)
        )

        # Pullback confirmation: tight consolidation after prior upward move
        prior_move_up = (
            len(df) >= 3
            and prev_close > float(df.iloc[-3]["Close"])
        )
        pullback_ok = (
            prior_move_up
            and body_pct <= self.pullback_max_body_pct
            and last_low >= prev_low
        )

        volume_ok = avg_volume <= 0 or last_volume >= avg_volume * self.min_volume_ratio

        score   = 0.0
        reasons = []

        if strong_body:
            score += 2.0
            reasons.append("strong bullish body")
        if rejection_body:
            score += 1.5
            reasons.append("lower wick rejection")
        if breakout_prev:
            score += 2.0
            reasons.append("broke previous high")
        if breakout_recent:
            score += 1.5
            reasons.append("broke recent high")
        if vwap_reclaim:
            score += 2.0
            reasons.append("VWAP reclaim")
        if pullback_ok:
            score += 1.5
            reasons.append("pullback confirmation")
        if volume_ok:
            score += 1.0
            reasons.append("volume acceptable")
        if bullish and last_close > prev_close:
            score += 0.5
            reasons.append("bullish continuation close")

        structural = breakout_prev or breakout_recent or vwap_reclaim or pullback_ok

        if score >= self.MIN_PASS_SCORE and structural:
            if breakout_prev or breakout_recent:
                entry_type = "breakout"
            elif vwap_reclaim:
                entry_type = "vwap_reclaim"
            else:
                entry_type = "pullback"

            return EntryDecision(
                allowed        = True,
                signal         = "BUY_CALL",
                score          = round(score, 2),
                reason         = " | ".join(reasons),
                entry_type     = entry_type,
                trigger_price  = round(last_close, 2),
                stop_reference = round(min(last_low, prev_low), 2),
                metadata={
                    "body_pct":     round(body_pct, 6),
                    "recent_high":  round(recent_high, 2),
                    "prev_high":    round(prev_high, 2),
                    "vwap":         round(last_vwap, 2) if last_vwap is not None else None,
                    "volume_ratio": round(last_volume / avg_volume, 3) if avg_volume > 0 else None,
                    "pullback_ok":  pullback_ok,
                },
            )

        return EntryDecision(
            allowed        = False,
            signal         = "BUY_CALL",
            score          = round(score, 2),
            reason         = "No strong bullish entry trigger",
            entry_type     = "none",
            trigger_price  = None,
            stop_reference = None,
            metadata={
                "breakout_prev":   breakout_prev,
                "breakout_recent": breakout_recent,
                "vwap_reclaim":    vwap_reclaim,
                "pullback_ok":     pullback_ok,
                "strong_body":     strong_body,
                "rejection_body":  rejection_body,
            },
        )

    # ------------------------------------------------------------------
    # BUY_PUT evaluation
    # ------------------------------------------------------------------
    def _evaluate_buy_put(self, df: pd.DataFrame) -> EntryDecision:
        last   = df.iloc[-1]
        prev   = df.iloc[-2]
        recent = df.iloc[-self.lookback_bars:]

        last_open   = float(last["Open"])
        last_high   = float(last["High"])
        last_low    = float(last["Low"])
        last_close  = float(last["Close"])
        last_vwap   = float(last["VWAP"]) if pd.notna(last["VWAP"]) else None
        last_volume = float(last["Volume"])

        prev_close = float(prev["Close"])
        prev_high  = float(prev["High"])
        prev_low   = float(prev["Low"])
        prev_vwap  = float(prev["VWAP"]) if pd.notna(prev["VWAP"]) else None

        recent_low = float(recent["Low"].iloc[:-1].min()) if len(recent) > 1 else prev_low
        avg_volume = float(recent["Volume"].iloc[:-1].mean()) if len(recent) > 1 else last_volume

        body       = abs(last_close - last_open)
        body_pct   = body / max(last_close, 1e-9)
        upper_wick = last_high - max(last_open, last_close)
        lower_wick = min(last_open, last_close) - last_low

        reject = self._hard_reject_bearish("BUY_PUT", body_pct, lower_wick, body)
        if reject is not None:
            return reject

        bearish          = last_close < last_open
        strong_body      = bearish and body > lower_wick
        rejection_body   = bearish and upper_wick > lower_wick * 0.8
        breakdown_prev   = last_close < prev_low   * (1 - self.breakout_buffer_pct)
        breakdown_recent = last_close < recent_low * (1 - self.breakout_buffer_pct)

        vwap_reject = (
            last_vwap is not None
            and prev_vwap is not None
            and last_close < last_vwap * (1 - self.vwap_reclaim_tolerance)
            and prev_close >= prev_vwap * (1 - self.vwap_cross_tolerance)
        )

        prior_move_dn = (
            len(df) >= 3
            and prev_close < float(df.iloc[-3]["Close"])
        )
        pullback_ok = (
            prior_move_dn
            and body_pct <= self.pullback_max_body_pct
            and last_high <= prev_high
        )

        volume_ok = avg_volume <= 0 or last_volume >= avg_volume * self.min_volume_ratio

        score   = 0.0
        reasons = []

        if strong_body:
            score += 2.0
            reasons.append("strong bearish body")
        if rejection_body:
            score += 1.5
            reasons.append("upper wick rejection")
        if breakdown_prev:
            score += 2.0
            reasons.append("broke previous low")
        if breakdown_recent:
            score += 1.5
            reasons.append("broke recent low")
        if vwap_reject:
            score += 2.0
            reasons.append("VWAP rejection")
        if pullback_ok:
            score += 1.5
            reasons.append("pullback confirmation")
        if volume_ok:
            score += 1.0
            reasons.append("volume acceptable")
        if bearish and last_close < prev_close:
            score += 0.5
            reasons.append("bearish continuation close")

        structural = breakdown_prev or breakdown_recent or vwap_reject or pullback_ok

        if score >= self.MIN_PASS_SCORE and structural:
            if breakdown_prev or breakdown_recent:
                entry_type = "breakdown"
            elif vwap_reject:
                entry_type = "vwap_reject"
            else:
                entry_type = "pullback"

            return EntryDecision(
                allowed        = True,
                signal         = "BUY_PUT",
                score          = round(score, 2),
                reason         = " | ".join(reasons),
                entry_type     = entry_type,
                trigger_price  = round(last_close, 2),
                stop_reference = round(max(last_high, prev_high), 2),
                metadata={
                    "body_pct":     round(body_pct, 6),
                    "recent_low":   round(recent_low, 2),
                    "prev_low":     round(prev_low, 2),
                    "vwap":         round(last_vwap, 2) if last_vwap is not None else None,
                    "volume_ratio": round(last_volume / avg_volume, 3) if avg_volume > 0 else None,
                    "pullback_ok":  pullback_ok,
                },
            )

        return EntryDecision(
            allowed        = False,
            signal         = "BUY_PUT",
            score          = round(score, 2),
            reason         = "No strong bearish entry trigger",
            entry_type     = "none",
            trigger_price  = None,
            stop_reference = None,
            metadata={
                "breakdown_prev":    breakdown_prev,
                "breakdown_recent":  breakdown_recent,
                "vwap_reject":       vwap_reject,
                "pullback_ok":       pullback_ok,
                "strong_body":       strong_body,
                "rejection_body":    rejection_body,
            },
        )

    # ------------------------------------------------------------------
    # Hard-reject helpers
    # ------------------------------------------------------------------
    def _hard_reject_bullish(
        self, signal: str, body_pct: float, upper_wick: float, body: float
    ) -> Optional[EntryDecision]:
        if body_pct > self.max_entry_candle_body_pct:
            return self._deny(
                signal,
                f"Entry candle too stretched ({body_pct:.3%} > {self.max_entry_candle_body_pct:.3%})",
                metadata={"body_pct": round(body_pct, 6)},
            )
        if body_pct < self.min_trigger_body_pct:
            return self._deny(
                signal,
                f"Trigger candle too small ({body_pct:.3%} < {self.min_trigger_body_pct:.3%})",
                metadata={"body_pct": round(body_pct, 6)},
            )
        if upper_wick > max(body, 1e-9) * self.max_wick_body_ratio:
            return self._deny(
                signal,
                "Upper wick too large for bullish continuation",
                metadata={"upper_wick": round(upper_wick, 4), "body": round(body, 4)},
            )
        return None

    def _hard_reject_bearish(
        self, signal: str, body_pct: float, lower_wick: float, body: float
    ) -> Optional[EntryDecision]:
        if body_pct > self.max_entry_candle_body_pct:
            return self._deny(
                signal,
                f"Entry candle too stretched ({body_pct:.3%} > {self.max_entry_candle_body_pct:.3%})",
                metadata={"body_pct": round(body_pct, 6)},
            )
        if body_pct < self.min_trigger_body_pct:
            return self._deny(
                signal,
                f"Trigger candle too small ({body_pct:.3%} < {self.min_trigger_body_pct:.3%})",
                metadata={"body_pct": round(body_pct, 6)},
            )
        if lower_wick > max(body, 1e-9) * self.max_wick_body_ratio:
            return self._deny(
                signal,
                "Lower wick too large for bearish continuation",
                metadata={"lower_wick": round(lower_wick, 4), "body": round(body, 4)},
            )
        return None

    # ------------------------------------------------------------------
    # Deny builder
    # ------------------------------------------------------------------
    @staticmethod
    def _deny(
        signal: Optional[str],
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EntryDecision:
        return EntryDecision(
            allowed=False, signal=signal, score=0.0,
            reason=reason, entry_type="none",
            trigger_price=None, stop_reference=None,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # DataFrame preparation
    # ------------------------------------------------------------------
    def _prepare_df(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None

        work = df.copy()

        if isinstance(work.columns, pd.MultiIndex):
            work.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in work.columns]

        rename_map = {}
        for col in work.columns:
            c = str(col).strip().lower()
            if c == "open":     rename_map[col] = "Open"
            elif c == "high":   rename_map[col] = "High"
            elif c == "low":    rename_map[col] = "Low"
            elif c == "close":  rename_map[col] = "Close"
            elif c == "volume": rename_map[col] = "Volume"
        work = work.rename(columns=rename_map)

        if not {"Open", "High", "Low", "Close", "Volume"}.issubset(work.columns):
            return None

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            work[col] = pd.to_numeric(work[col], errors="coerce")
        work = work.dropna(subset=["Open", "High", "Low", "Close"])
        work["Volume"] = work["Volume"].fillna(0)

        if work.empty:
            return None

        work["VWAP"] = self._compute_intraday_vwap(work)
        return work

    def _compute_intraday_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Session-reset VWAP when DatetimeIndex is available."""
        typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
        pv      = typical * df["Volume"]

        if isinstance(df.index, pd.DatetimeIndex):
            session_key = pd.Series(df.index.date, index=df.index)
            cum_pv  = pv.groupby(session_key).cumsum()
            cum_vol = df["Volume"].groupby(session_key).cumsum().replace(0, pd.NA)
            return (cum_pv / cum_vol).fillna(method="ffill")

        # No DatetimeIndex — cumulative VWAP (accurate only for single-session data)
        cum_vol = df["Volume"].cumsum().replace(0, pd.NA)
        return (pv.cumsum() / cum_vol).fillna(method="ffill")


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    idx   = pd.date_range("2026-03-20 09:15", periods=25, freq="5min")
    base  = 22000.0
    opens = [base + i * 15 for i in range(25)]
    np.random.seed(42)
    closes = [o + np.random.choice([-5, 10, 20, 25, 30]) for o in opens]
    highs  = [max(o, c) + np.random.randint(5, 20) for o, c in zip(opens, closes)]
    lows   = [min(o, c) - np.random.randint(5, 15) for o, c in zip(opens, closes)]
    vols   = np.random.randint(5000, 15000, 25).tolist()

    # Force last bar to be a strong bullish breakout
    closes[-1] = opens[-1] + 90
    highs[-1]  = closes[-1] + 12
    lows[-1]   = opens[-1]  - 8

    df_test = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
        index=idx,
    )

    opt = EntryOptimizer(instrument="index")
    res = opt.evaluate("BUY_CALL", df_test)

    print(f"Allowed      : {res.allowed}")
    print(f"Score        : {res.score}")
    print(f"Entry type   : {res.entry_type}")
    print(f"Trigger      : {res.trigger_price}")
    print(f"Stop ref     : {res.stop_reference}")
    print(f"Reason       : {res.reason}")
    print()
    print(f"Thresholds (index):")
    print(f"  max_entry_candle_body_pct : {opt.max_entry_candle_body_pct:.4%}")
    print(f"  min_trigger_body_pct      : {opt.min_trigger_body_pct:.4%}")
    print(f"  breakout_buffer_pct       : {opt.breakout_buffer_pct:.4%}")
