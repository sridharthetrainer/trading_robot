"""
capital_allocator.py

Fund allocation engine for parallel scalping, swing and intraday trading.

Architecture
────────────
Capital is divided into four distinct buckets. Each trade style draws
same rupees.

    ┌─────────────────────────────────────────────────────┐
    │                TOTAL CAPITAL  ₹X                    │
    ├──────────────┬──────────────┬──────────┬────────────┤
    │ SWING 45%    │ INTRADAY 30% │ SCALP 15%│ RESERVE 10%│
    │ (multi-day)  │ (same day)   │ (minutes)│ (buffer)   │
    └──────────────┴──────────────┴──────────┴────────────┘

Scalping: 5-minute momentum trades. Hold 1-4 bars (5-20 minutes).
          Tight stops (0.5-1 ATR). Multiple trades per day possible.
          Uses 15% of capital. Maximum 2 concurrent scalp positions.

Intraday: 5-minute to 1-hour trades. Hold same day, close by EOD.
          Medium stops (1.5-2 ATR). Uses 30% of capital.
          Maximum 2 concurrent intraday positions.

Swing:    Multi-day options trades. Hold 2-10 days (DTE ≥ 5).
          Wide stops (25% of premium). Uses 45% of capital.
          Maximum 2 concurrent swing positions.

Reserve:  10% always held back. Used for margin calls, emergency
          positions, or opportunity when markets gap.

Capital allocation is dynamic: as real broker balance updates, all
buckets scale proportionally. High-watermark drawdown protection halves
all buckets after 15% drawdown.

Usage
─────
    from capital_allocator import CapitalAllocator

    alloc = CapitalAllocator(total_capital=500_000)
    trade_cap = alloc.capital_for_trade("swing")    # → 225000 * max_trade_pct
    trade_cap = alloc.capital_for_trade("scalping") # → 75000 * max_trade_pct
    alloc.update_total(new_balance)                 # sync from broker
    alloc.record_trade_start("swing", 30_000)       # lock capital
    alloc.record_trade_end("swing", 30_000, 2_500)  # release + record pnl
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Default allocation percentages ───────────────────────────────────────────
DEFAULT_SWING_PCT    = 0.45   # 45% for multi-day swing trades
DEFAULT_INTRADAY_PCT = 0.30   # 30% for same-day intraday trades
DEFAULT_SCALPING_PCT = 0.15   # 15% for scalping trades
DEFAULT_RESERVE_PCT  = 0.10   # 10% always reserved

# ── Per-trade limits ──────────────────────────────────────────────────────────
MAX_TRADE_PCT_SWING    = 0.40   # max 40% of swing bucket per trade
MAX_TRADE_PCT_INTRADAY = 0.50   # max 50% of intraday bucket per trade
MAX_TRADE_PCT_SCALPING = 0.60   # max 60% of scalp bucket per trade (small bucket)

# ── Concurrent position limits per style ─────────────────────────────────────
MAX_CONCURRENT_SWING    = 2
MAX_CONCURRENT_INTRADAY = 2
MAX_CONCURRENT_SCALPING = 3   # can run more scalps since they're short-duration

# ── Drawdown protection ───────────────────────────────────────────────────────
DRAWDOWN_HALVE_THRESHOLD = 0.15   # halve all buckets if >15% drawdown
DRAWDOWN_RESTORE_THRESHOLD = 0.08  # restore when drawdown < 8%


@dataclass
class BucketState:
    """State of one capital bucket (swing / intraday / scalping / reserve)."""
    name:              str
    allocation_pct:    float
    max_trade_pct:     float
    max_concurrent:    int
    total_allocated:   float = 0.0    # current ₹ in this bucket
    deployed:          float = 0.0    # ₹ currently in open positions
    cumulative_pnl:    float = 0.0
    trades_today:      int   = 0
    wins_today:        int   = 0
    drawdown_halved:   bool  = False

    @property
    def available(self) -> float:
        return max(0.0, self.total_allocated - self.deployed)

    @property
    def win_rate_today(self) -> float:
        return self.wins_today / self.trades_today if self.trades_today > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name":           self.name,
            "allocated":      round(self.total_allocated, 2),
            "deployed":       round(self.deployed, 2),
            "available":      round(self.available, 2),
            "pnl_today":      round(self.cumulative_pnl, 2),
            "trades_today":   self.trades_today,
            "win_rate_today": round(self.win_rate_today, 4),
        }


class CapitalAllocator:
    """
    Manages fund allocation across scalping, intraday and swing styles
    so both can run in parallel without competing for the same capital.
    """

    def __init__(
        self,
        total_capital:   float = 100_000.0,
        swing_pct:       float = DEFAULT_SWING_PCT,
        intraday_pct:    float = DEFAULT_INTRADAY_PCT,
        scalping_pct:    float = DEFAULT_SCALPING_PCT,
        reserve_pct:     float = DEFAULT_RESERVE_PCT,
    ) -> None:
        # Use actual balance as peak on first run — prevents false drawdown alert
        # when paper capital (₹1L) ≠ live balance (₹34K)
        self._peak_capital  = float(total_capital)
        self._initialized   = False  # will update peak on first real balance check
        self._drawdown_mode = False

        # Normalise percentages so they sum to 1.0
        total_pct = swing_pct + intraday_pct + scalping_pct + reserve_pct
        if abs(total_pct - 1.0) > 0.01:
            logger.warning(
                "CapitalAllocator: pcts sum to %.2f, normalising", total_pct
            )
            swing_pct    /= total_pct
            intraday_pct /= total_pct
            scalping_pct /= total_pct
            reserve_pct  /= total_pct

        self.buckets: Dict[str, BucketState] = {
            "swing": BucketState(
                name="swing", allocation_pct=swing_pct,
                max_trade_pct=MAX_TRADE_PCT_SWING,
                max_concurrent=MAX_CONCURRENT_SWING,
            ),
            "intraday": BucketState(
                name="intraday", allocation_pct=intraday_pct,
                max_trade_pct=MAX_TRADE_PCT_INTRADAY,
                max_concurrent=MAX_CONCURRENT_INTRADAY,
            ),
            "scalping": BucketState(
                name="scalping", allocation_pct=scalping_pct,
                max_trade_pct=MAX_TRADE_PCT_SCALPING,
                max_concurrent=MAX_CONCURRENT_SCALPING,
            ),
            "reserve": BucketState(
                name="reserve", allocation_pct=reserve_pct,
                max_trade_pct=0.0,
                max_concurrent=0,
            ),
        }

        self.update_total(total_capital)
        self._initialized = False  # reset: next call = first real broker balance

    # ── Correlation / Sector Limits ───────────────────────────────────────────

    SECTOR_MAP = {
        "TCS":"IT","INFY":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT",
        "HDFCBANK":"BANKING","ICICIBANK":"BANKING","SBIN":"BANKING","AXISBANK":"BANKING",
        "RELIANCE":"ENERGY","ONGC":"ENERGY","BPCL":"ENERGY","IOC":"ENERGY",
        "HINDUNILVR":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG","DABUR":"FMCG",
        "SUNPHARMA":"PHARMA","DRREDDY":"PHARMA","CIPLA":"PHARMA","DIVISLAB":"PHARMA",
        "TATASTEEL":"METAL","JSWSTEEL":"METAL","HINDALCO":"METAL","VEDL":"METAL",
        "DLF":"REALTY","GODREJPROP":"REALTY","OBEROIRLTY":"REALTY",
    }

    def get_sector(self, symbol: str) -> str:
        return self.SECTOR_MAP.get(symbol.upper(), "OTHER")

    def can_open_position(self, symbol: str, open_positions: list) -> tuple:
        """
        Check if new position allowed given correlation limits.
        Returns (allowed: bool, reason: str)
        Inspired by Two Sigma sector exposure rules.
        """
        sector = self.get_sector(symbol)
        # Count open positions in same sector
        same_sector = [s for s in open_positions
                       if self.get_sector(s) == sector and sector != "OTHER"]
        if len(same_sector) >= 2:
            return False, f"Already {len(same_sector)} positions in {sector} — max 2"

        # Total position limit
        if len(open_positions) >= 8:
            return False, f"Max 8 concurrent positions reached"

        return True, "OK"

    # ── Core API ──────────────────────────────────────────────────────────────

    def update_total(self, new_total: float) -> None:
        """
        Sync all bucket allocations to a new total capital figure.
        Called every cycle with live broker balance.
        """
        if new_total <= 0:
            logger.warning("CapitalAllocator.update_total: invalid capital %.2f", new_total)
            return

        # First call from __init__ sets peak to config value and still allocates
        # buckets so paper/training mode has usable capital immediately.  The
        # next real broker update can reset the peak without zeroing buckets.
        if not self._initialized:
            self._peak_capital = new_total  # always reset peak on first real update
            self._initialized  = True
            logger.info("CapitalAllocator peak initialised: ₹%.0f", new_total)
        elif new_total > self._peak_capital:
            self._peak_capital = new_total

        # Check drawdown
        drawdown_pct = (self._peak_capital - new_total) / self._peak_capital
        if drawdown_pct >= DRAWDOWN_HALVE_THRESHOLD and not self._drawdown_mode:
            logger.warning(
                "Drawdown %.1f%% — halving all position sizes",
                drawdown_pct * 100,
            )
            self._drawdown_mode = True
        elif drawdown_pct <= DRAWDOWN_RESTORE_THRESHOLD and self._drawdown_mode:
            logger.info("Drawdown recovered — restoring normal sizes")
            self._drawdown_mode = False

        # Effective capital: halved in drawdown mode
        effective = new_total * 0.5 if self._drawdown_mode else new_total

        for bucket in self.buckets.values():
            bucket.total_allocated = round(effective * bucket.allocation_pct, 2)

        logger.debug(
            "CapitalAllocator updated | total=₹%.0f effective=₹%.0f "
            "swing=₹%.0f intraday=₹%.0f scalp=₹%.0f reserve=₹%.0f",
            new_total, effective,
            self.buckets["swing"].total_allocated,
            self.buckets["intraday"].total_allocated,
            self.buckets["scalping"].total_allocated,
            self.buckets["reserve"].total_allocated,
        )

    def capital_for_trade(
        self,
        style:         str,
        max_trade_pct: Optional[float] = None,
    ) -> float:
        """
        Return the maximum capital available for a single trade of the given style.

        Parameters
        ----------
        style         : "swing" | "intraday" | "scalping" | "fallback" | "auto"
        max_trade_pct : Override the default max-per-trade percentage

        Returns
        -------
        float — maximum capital in ₹ to deploy for this one trade
        """
        style_key = self._normalise_style(style)
        bucket    = self.buckets.get(style_key)

        if bucket is None or bucket.max_trade_pct == 0:
            # Unknown style or reserve bucket — use 10% of swing bucket
            fallback = self.buckets["swing"].available * 0.10
            logger.debug("capital_for_trade: unknown style '%s' → fallback ₹%.0f", style, fallback)
            return round(fallback, 2)

        max_pct   = max_trade_pct if max_trade_pct is not None else bucket.max_trade_pct
        available = bucket.available
        trade_cap = round(available * max_pct, 2)

        logger.debug(
            "capital_for_trade: style=%s bucket_avail=₹%.0f max_pct=%.0f%% → ₹%.0f",
            style_key, available, max_pct * 100, trade_cap,
        )
        return trade_cap

    def can_open_trade(self, style: str, open_count_for_style: int) -> bool:
        """
        Returns True if a new trade of this style can be opened.
        Checks both capital availability and concurrent position limit.
        """
        style_key = self._normalise_style(style)
        bucket    = self.buckets.get(style_key)
        if not bucket:
            return False
        if open_count_for_style >= bucket.max_concurrent:
            logger.debug(
                "can_open_trade: %s at max concurrent (%d/%d)",
                style_key, open_count_for_style, bucket.max_concurrent,
            )
            return False
        if bucket.available <= 0:
            logger.debug("can_open_trade: %s bucket exhausted", style_key)
            return False
        return True

    def record_trade_start(self, style: str, capital_deployed: float) -> None:
        """Lock capital when a trade opens."""
        style_key = self._normalise_style(style)
        bucket    = self.buckets.get(style_key)
        if bucket:
            bucket.deployed   = round(bucket.deployed + capital_deployed, 2)
            bucket.trades_today += 1

    def record_trade_end(
        self, style: str, capital_released: float, pnl: float
    ) -> None:
        """Release capital and record P&L when a trade closes."""
        style_key = self._normalise_style(style)
        bucket    = self.buckets.get(style_key)
        if bucket:
            bucket.deployed        = max(0.0, round(bucket.deployed - capital_released, 2))
            bucket.cumulative_pnl  = round(bucket.cumulative_pnl + pnl, 2)
            if pnl > 0:
                bucket.wins_today += 1

    def reset_daily(self) -> None:
        """Reset intraday counters at market open."""
        for bucket in self.buckets.values():
            bucket.trades_today   = 0
            bucket.wins_today     = 0
            bucket.cumulative_pnl = 0.0
            bucket.deployed       = 0.0   # reset — previous day positions closed

    def get_allocation_summary(self) -> Dict[str, Any]:
        """Return full allocation status for logging and Telegram alerts."""
        return {
            "peak_capital":    round(self._peak_capital, 2),
            "drawdown_mode":   self._drawdown_mode,
            "buckets":         {k: v.to_dict() for k, v in self.buckets.items()},
            "total_deployed":  round(sum(b.deployed for b in self.buckets.values()), 2),
            "total_available": round(sum(
                b.available for k, b in self.buckets.items() if k != "reserve"
            ), 2),
        }

    # ── Style normalisation ───────────────────────────────────────────────────

    @staticmethod
    def _normalise_style(style: str) -> str:
        """Map incoming style names to bucket keys."""
        s = str(style).lower().strip()
        if s in ("scalp", "scalping", "momentum_5m", "quick_trade"):
            return "scalping"
        if s in ("swing", "multi_day", "positional", "position", "position_trading"):
            return "swing"
        if s in ("intraday", "day_trade", "fallback", "auto", "default",
                 "trend", "breakout", "mean_reversion", "orb",
                 "vwap_reversion", "supertrend_mtf", "ma_cross"):
            return "intraday"
        return "intraday"   # safe default

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def swing_available(self) -> float:
        return self.buckets["swing"].available

    @property
    def intraday_available(self) -> float:
        return self.buckets["intraday"].available

    @property
    def scalping_available(self) -> float:
        return self.buckets["scalping"].available

    @property
    def reserve_available(self) -> float:
        return self.buckets["reserve"].available
