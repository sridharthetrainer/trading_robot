"""
daily_loss_limit.py

Production-ready daily loss limit controller.

Fixes applied
-------------
- Added `last_reset_date` tracking so state auto-resets when the calendar
  date changes (call `auto_reset_if_new_day()` each cycle).
  Previously `reset_day()` existed but was never called anywhere, meaning
  realized P&L accumulated forever across days and the system stayed
  permanently locked after the first hard-limit breach.

- Added STT (Securities Transaction Tax) modeling.
  NSE levies STT on the sell side of options at 0.05% of premium × qty.
  This was completely absent from all cost calculations, understating
  real brokerage by ~₹5–25 per trade.  Use `add_trade_costs()` to book
  both brokerage and STT together.

- `add_realized_pnl()` now accepts an optional `stt` kwarg so callers
  that already compute STT separately can pass it in one call.

- `evaluate()` logs a per-cycle DEBUG line showing current P&L vs limits,
  making it easier to trace state in production logs.

- `summary()` now includes `today_date` so dashboards can show which
  trading day the numbers belong to.

- All public methods are safe to call with None trade_manager / alert_manager
  (they were before, but now explicitly tested in __main__).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional

from alerts import AlertManager
from trade_manager import TradeManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STT rate for NSE options (sell side, as of FY2024-25)
# Override via DailyLossLimitManager(stt_rate=...) if the rate changes.
# ---------------------------------------------------------------------------
DEFAULT_STT_RATE = 0.0005   # 0.05% on sell-side option premium × qty


@dataclass
class DailyLossStatus:
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    soft_limit: float
    hard_limit: float
    soft_breached: bool
    hard_breached: bool
    locked: bool
    reason: Optional[str]
    today_date: str         # YYYY-MM-DD of the current trading day


class DailyLossLimitManager:
    """
    Daily risk controller.

    Lifecycle per trading day
    -------------------------
    1. Call `auto_reset_if_new_day()` once per main loop iteration.
       It resets all counters and unlocks when the calendar date changes.
    2. Call `add_realized_pnl(pnl)` after every closed trade.
       Or use the convenience wrapper `add_trade_costs(pnl, premium, qty)`
       which automatically books STT + brokerage as well.
    3. Call `evaluate(open_positions, ltp_map)` to check whether limits
       have been breached and trigger auto-close if configured.
    4. Check `can_trade()` before placing any new order.

    Modes
    -----
    - soft limit : warning alert only, trading continues
    - hard limit : lock trading, optionally auto-close all open positions
    """

    def __init__(
        self,
        hard_limit: float = 3000.0,
        soft_limit: Optional[float] = 2000.0,
        include_unrealized: bool = True,
        auto_close_on_breach: bool = True,
        stt_rate: float = DEFAULT_STT_RATE,
        alert_manager: Optional[AlertManager] = None,
        trade_manager: Optional[TradeManager] = None,
    ) -> None:
        if hard_limit <= 0:
            raise ValueError("hard_limit must be > 0")
        if soft_limit is not None and soft_limit <= 0:
            raise ValueError("soft_limit must be > 0 when provided")
        if soft_limit is not None and soft_limit >= hard_limit:
            raise ValueError("soft_limit must be less than hard_limit")

        self.hard_limit            = float(hard_limit)
        self.soft_limit            = float(soft_limit) if soft_limit is not None else None
        self.include_unrealized    = bool(include_unrealized)
        self.auto_close_on_breach  = bool(auto_close_on_breach)
        self.stt_rate              = float(stt_rate)

        self.alerts        = alert_manager
        self.trade_manager = trade_manager

        # Daily accumulators
        self.realized_pnl:      float         = 0.0
        self.manual_adjustments: float        = 0.0
        self.total_stt_paid:    float         = 0.0
        self.total_brokerage_paid: float      = 0.0

        # Lock state
        self.locked:            bool          = False
        self.lock_reason:       Optional[str] = None

        # Consecutive-loss / trade-count state (Mechanism B). These were used by
        # record_trade_result()/can_trade_by_count() but NEVER initialized →
        # record_trade_result AttributeError'd (the consecutive-loss stop never
        # worked). Limits from config; counters reset each day in reset_day().
        import config as _cfg_dl
        self._consecutive_loss_limit = int(getattr(_cfg_dl, "MAX_CONSECUTIVE_LOSSES", 3))
        self._max_trades_today       = int(getattr(_cfg_dl, "MAX_TRADES_PER_DAY", 6))
        self._trades_today           = 0
        self._consecutive_losses     = 0
        self._last_trade_was_loss    = False

        # Alert de-dupe flags — reset each day
        self.soft_alert_sent:   bool          = False
        self.hard_alert_sent:   bool          = False

        # Day tracking
        self.last_reset_date:   Optional[date] = None

    # ------------------------------------------------------------------
    # Alert helper
    # ------------------------------------------------------------------
    def _notify(self, fn_name: str, *args, **kwargs) -> None:
        if self.alerts is None:
            return
        try:
            fn = getattr(self.alerts, fn_name, None)
            if callable(fn):
                fn(*args, **kwargs)
        except Exception as exc:
            logger.exception(
                "Alert failed in DailyLossLimitManager (%s): %s", fn_name, exc
            )

    # ------------------------------------------------------------------
    # Day-boundary management
    # ------------------------------------------------------------------
    def auto_reset_if_new_day(self) -> bool:
        """
        Check whether the calendar date has changed since the last reset.
        If so, call reset_day() automatically.

        Returns True if a reset was performed.

        Call this once per main-loop iteration — it is cheap (just a
        date comparison) and safe to call many times per day.
        """
        today = date.today()
        if self.last_reset_date is None or self.last_reset_date < today:
            self.reset_day()
            logger.info(
                "Daily loss state auto-reset | date=%s (was %s)",
                today.isoformat(),
                self.last_reset_date.isoformat() if self.last_reset_date else "never",
            )
            return True
        return False

    def record_trade_result(self, pnl: float) -> bool:
        """
        Record a completed trade result.
        Returns False and locks trading if consecutive losses reached.
        Called by trade_manager after every trade close.
        """
        self._trades_today += 1
        
        if pnl < 0:
            self._consecutive_losses += 1
            self._last_trade_was_loss = True
            if self._consecutive_losses >= self._consecutive_loss_limit:
                # Use self.lock() so self.locked (what can_trade() checks) is set
                # AND the trade manager is locked — previously it set self._locked,
                # which can_trade() never reads, so trading wasn't actually stopped.
                self.lock(
                    f"consecutive_losses_{self._consecutive_losses}_"
                    f"limit_{self._consecutive_loss_limit}"
                )
                logger.warning(
                    "CONSECUTIVE LOSS STOP: %d losses in a row — trading locked for today",
                    self._consecutive_losses,
                )
                return False
        else:
            self._consecutive_losses = 0   # reset on any win
            self._last_trade_was_loss = False
        return True

    def can_trade_by_count(self) -> bool:
        """Returns False if daily trade count limit reached."""
        if self._max_trades_today > 0 and self._trades_today >= self._max_trades_today:
            logger.info(
                "Max trades/day reached: %d/%d",
                self._trades_today, self._max_trades_today,
            )
            return False
        return True

    def get_daily_stats(self) -> dict:
        """Return current daily trading stats."""
        return {
            "trades_today":        self._trades_today,
            "max_trades":          self._max_trades_today,
            "consecutive_losses":  self._consecutive_losses,
            "consecutive_limit":   self._consecutive_loss_limit,
            "locked":              self.locked,
            "lock_reason":         self.lock_reason or "",
        }


    # ── Weekly drawdown tracking ──────────────────────────────────────────────
    def _init_weekly(self) -> None:
        """Initialise weekly loss tracker."""
        if not hasattr(self, "_weekly_loss"):
            self._weekly_loss  = 0.0
            self._weekly_limit = float(getattr(__import__("config"), "WEEKLY_LOSS_LIMIT",
                                               self.hard_limit * 3))
            self._week_start   = __import__("datetime").date.today().isocalendar()[:2]
            logger.info("Weekly loss limit: ₹%.0f", self._weekly_limit)

    def _reset_week_if_new(self) -> None:
        """Reset weekly counter on new ISO week."""
        self._init_weekly()
        today_week = __import__("datetime").date.today().isocalendar()[:2]
        if today_week != self._week_start:
            logger.info("New week — resetting weekly loss tracker (was ₹%.0f)", self._weekly_loss)
            self._weekly_loss = 0.0
            self._week_start  = today_week

    def add_weekly_loss(self, amount: float) -> bool:
        """
        Record realised loss for weekly tracking.
        Returns False if weekly limit breached → lock trading.
        """
        self._init_weekly()
        self._reset_week_if_new()
        if amount < 0:
            self._weekly_loss += abs(amount)
        if self._weekly_loss >= self._weekly_limit:
            self.lock(
                f"weekly_loss_₹{self._weekly_loss:.0f}_>_limit_₹{self._weekly_limit:.0f}"
            )
            logger.warning(
                "WEEKLY LOSS LIMIT BREACHED: ₹%.0f >= ₹%.0f — trading locked until Monday",
                self._weekly_loss, self._weekly_limit,
            )
            return False
        return True

    def get_weekly_stats(self) -> dict:
        self._init_weekly()
        self._reset_week_if_new()
        return {
            "weekly_loss":  round(self._weekly_loss, 2),
            "weekly_limit": self._weekly_limit,
            "weekly_pct":   round(self._weekly_loss / max(self._weekly_limit,1) * 100, 1),
            "week":         str(self._week_start),
        }


    def reset_day(self) -> None:
        """
        Reset all daily accumulators and unlock trading.
        Called automatically by `auto_reset_if_new_day()`.
        Can also be called manually at session start.
        """
        self.realized_pnl          = 0.0
        self.manual_adjustments    = 0.0
        self.total_stt_paid        = 0.0
        self.total_brokerage_paid  = 0.0
        self.locked                = False
        self.lock_reason           = None
        self.soft_alert_sent       = False
        self.hard_alert_sent       = False
        self.last_reset_date       = date.today()
        # Reset Mechanism B daily counters (limits are config, kept).
        self._trades_today         = 0
        self._consecutive_losses   = 0
        self._last_trade_was_loss  = False
        logger.info(
            "Daily loss state reset | date=%s", self.last_reset_date.isoformat()
        )

    def lock(self, reason: str) -> None:
        self.locked      = True
        self.lock_reason = str(reason)
        logger.warning("Daily loss lock activated: %s", self.lock_reason)
        if self.trade_manager is not None:
            self.trade_manager.lock_trading(self.lock_reason)

    def unlock(self) -> None:
        self.locked      = False
        self.lock_reason = None
        logger.info("Daily loss lock removed")
        if self.trade_manager is not None:
            self.trade_manager.unlock_trading()

    # ------------------------------------------------------------------
    # P&L / cost booking
    # ------------------------------------------------------------------
    def add_realized_pnl(self, pnl: float, stt: float = 0.0) -> None:
        """
        Book a realized P&L value (negative = loss).

        Parameters
        ----------
        pnl : float
            Net P&L of the closed leg, already net of brokerage.
        stt : float
            Securities Transaction Tax for this trade (optional).
            Pass it here so total_stt_paid stays accurate.
        """
        self.realized_pnl    += float(pnl)
        self.total_stt_paid  += float(stt)
        logger.info(
            "Realized P&L booked | trade_pnl=%.2f stt=%.2f "
            "cumulative_realized=%.2f cumulative_stt=%.2f",
            pnl, stt, self.realized_pnl, self.total_stt_paid,
        )

    def add_trade_costs(
        self,
        gross_pnl: float,
        brokerage: float,
        premium: float = 0.0,
        qty: int = 0,
    ) -> float:
        """
        Convenience method: book a completed trade including all costs.

        Computes STT automatically from premium × qty × stt_rate (sell side).
        Returns the net P&L after all costs.

        Parameters
        ----------
        gross_pnl   : float — raw P&L before any costs
        brokerage   : float — brokerage charged for this trade (both legs)
        premium     : float — option premium per unit (for STT calc)
        qty         : int   — number of units sold (for STT calc)
        """
        stt       = float(premium) * int(qty) * self.stt_rate
        net_pnl   = float(gross_pnl) - float(brokerage) - stt

        self.total_brokerage_paid += float(brokerage)
        self.add_realized_pnl(net_pnl, stt=stt)

        logger.debug(
            "Trade costs | gross=%.2f brokerage=%.2f stt=%.2f net=%.2f",
            gross_pnl, brokerage, stt, net_pnl,
        )
        return net_pnl

    def apply_manual_adjustment(self, amount: float, note: str = "") -> None:
        self.manual_adjustments += float(amount)
        logger.info(
            "Manual adjustment | amount=%.2f cumulative=%.2f note=%s",
            amount, self.manual_adjustments, note,
        )

    # ------------------------------------------------------------------
    # Unrealized P&L
    # ------------------------------------------------------------------
    def calculate_unrealized_pnl(
        self,
        open_positions: List[Dict],
        ltp_map: Dict[str, float],
        brokerage_per_order: float = 20.0,
    ) -> float:
        """
        Estimate unrealized P&L across all open positions.
        Deducts estimated exit brokerage and STT for each position.
        """
        total_unrealized = 0.0

        for pos in open_positions:
            symbol       = pos.get("symbol")
            side         = str(pos.get("side", "")).upper().strip()
            qty          = int(pos.get("qty", 0))
            entry_price  = pos.get("entry_price")

            if symbol is None or entry_price is None or qty <= 0:
                continue
            if symbol not in ltp_map:
                continue

            current_price = float(ltp_map[symbol])
            entry_price   = float(entry_price)

            if side == "BUY":
                gross = (current_price - entry_price) * qty
            elif side == "SELL":
                gross = (entry_price - current_price) * qty
            else:
                continue

            # Estimated exit costs: brokerage + STT on sell side
            est_stt = current_price * qty * self.stt_rate
            total_unrealized += gross - float(brokerage_per_order) - est_stt

        return float(total_unrealized)

    def get_total_pnl(
        self,
        open_positions: Optional[List[Dict]] = None,
        ltp_map: Optional[Dict[str, float]] = None,
        brokerage_per_order: float = 20.0,
    ) -> float:
        realized   = self.realized_pnl + self.manual_adjustments
        unrealized = 0.0
        if self.include_unrealized and open_positions is not None and ltp_map is not None:
            unrealized = self.calculate_unrealized_pnl(
                open_positions=open_positions,
                ltp_map=ltp_map,
                brokerage_per_order=brokerage_per_order,
            )
        return float(realized + unrealized)

    # ------------------------------------------------------------------
    # Evaluation — call this each cycle
    # ------------------------------------------------------------------
    def evaluate(
        self,
        open_positions: Optional[List[Dict]] = None,
        ltp_map: Optional[Dict[str, float]] = None,
        brokerage_per_order: float = 20.0,
        exchange: Optional[str] = None,
    ) -> DailyLossStatus:
        """
        Evaluate current P&L against soft and hard limits.
        Triggers alerts and auto-close on breach.
        Safe to call many times per cycle.
        """
        # Auto-reset on new calendar day before evaluation
        self.auto_reset_if_new_day()

        realized   = float(self.realized_pnl + self.manual_adjustments)
        unrealized = 0.0

        if self.include_unrealized and open_positions is not None and ltp_map is not None:
            unrealized = self.calculate_unrealized_pnl(
                open_positions=open_positions,
                ltp_map=ltp_map,
                brokerage_per_order=brokerage_per_order,
            )

        total_pnl = realized + unrealized

        logger.debug(
            "Daily P&L | realized=%.2f unrealized=%.2f total=%.2f "
            "soft_limit=%.2f hard_limit=%.2f locked=%s",
            realized, unrealized, total_pnl,
            self.soft_limit or 0.0, self.hard_limit, self.locked,
        )

        # ---- Soft breach -----------------------------------------------
        soft_breached = (
            self.soft_limit is not None and total_pnl <= -abs(self.soft_limit)
        )
        if soft_breached and not self.soft_alert_sent:
            msg = (
                f"Daily soft loss threshold reached. "
                f"Realized={realized:.2f}, Unrealized={unrealized:.2f}, "
                f"Total={total_pnl:.2f}, SoftLimit=-{self.soft_limit:.2f}"
            )
            logger.warning(msg)
            self._notify("warning", msg, dedup_key="daily_loss_soft")
            self.soft_alert_sent = True

        # ---- Hard breach -----------------------------------------------
        hard_breached = total_pnl <= -abs(self.hard_limit)
        if hard_breached and not self.hard_alert_sent:
            reason = (
                f"Daily hard loss limit breached. "
                f"Realized={realized:.2f}, Unrealized={unrealized:.2f}, "
                f"Total={total_pnl:.2f}, HardLimit=-{self.hard_limit:.2f}"
            )
            logger.error(reason)
            self.lock(reason)
            self._notify("critical", reason, dedup_key="daily_loss_hard")
            self.hard_alert_sent = True

            if self.auto_close_on_breach and self.trade_manager is not None:
                try:
                    closed = self.trade_manager.close_all_trades(
                        reason="daily_loss_limit_breach",
                        exchange=exchange,
                    )
                    logger.warning(
                        "Auto-closed %d trades due to daily loss breach", closed
                    )
                    self._notify(
                        "critical",
                        f"Auto-closed {closed} open trades due to daily loss limit breach",
                        dedup_key="daily_loss_auto_close",
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed auto-close on daily loss breach: %s", exc
                    )
                    self._notify(
                        "error",
                        f"Failed to auto-close trades on daily loss breach: {exc}",
                        dedup_key="daily_loss_auto_close_fail",
                    )

        return DailyLossStatus(
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_pnl=total_pnl,
            soft_limit=float(self.soft_limit) if self.soft_limit is not None else 0.0,
            hard_limit=self.hard_limit,
            soft_breached=soft_breached,
            hard_breached=hard_breached,
            locked=self.locked,
            reason=self.lock_reason,
            today_date=(
                self.last_reset_date.isoformat()
                if self.last_reset_date
                else date.today().isoformat()
            ),
        )

    def can_trade(self) -> bool:
        """Return True if trading is not locked."""
        return not self.locked

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def summary(
        self,
        open_positions: Optional[List[Dict]] = None,
        ltp_map: Optional[Dict[str, float]] = None,
        brokerage_per_order: float = 20.0,
    ) -> Dict:
        status = self.evaluate(
            open_positions=open_positions,
            ltp_map=ltp_map,
            brokerage_per_order=brokerage_per_order,
        )
        return {
            "today_date":           status.today_date,
            "realized_pnl":         status.realized_pnl,
            "unrealized_pnl":       status.unrealized_pnl,
            "total_pnl":            status.total_pnl,
            "total_stt_paid":       round(self.total_stt_paid, 2),
            "total_brokerage_paid": round(self.total_brokerage_paid, 2),
            "soft_limit":           status.soft_limit,
            "hard_limit":           status.hard_limit,
            "soft_breached":        status.soft_breached,
            "hard_breached":        status.hard_breached,
            "locked":               status.locked,
            "reason":               status.reason,
            "include_unrealized":   self.include_unrealized,
            "auto_close_on_breach": self.auto_close_on_breach,
        }


# ---------------------------------------------------------------------------
# Quick self-test (no broker required)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mgr = DailyLossLimitManager(
        hard_limit=3000.0,
        soft_limit=2000.0,
        include_unrealized=False,
        auto_close_on_breach=False,
        alert_manager=None,
        trade_manager=None,
    )

    # Simulate three losing trades
    mgr.add_trade_costs(gross_pnl=-800.0,  brokerage=40.0, premium=200.0, qty=50)
    mgr.add_trade_costs(gross_pnl=-900.0,  brokerage=40.0, premium=180.0, qty=50)
    mgr.add_trade_costs(gross_pnl=-1500.0, brokerage=40.0, premium=150.0, qty=50)

    status = mgr.evaluate()
    print(f"Total P&L       : {status.total_pnl:.2f}")
    print(f"STT paid        : {mgr.total_stt_paid:.2f}")
    print(f"Brokerage paid  : {mgr.total_brokerage_paid:.2f}")
    print(f"Soft breached   : {status.soft_breached}")
    print(f"Hard breached   : {status.hard_breached}")
    print(f"Locked          : {status.locked}")
    print(f"Today           : {status.today_date}")
    print()

    # Simulate day rollover
    mgr.last_reset_date = None   # force a reset on next call
    reset_happened = mgr.auto_reset_if_new_day()
    print(f"Reset happened  : {reset_happened}")
    print(f"Locked after reset: {mgr.locked}")
    print(f"Realized after reset: {mgr.realized_pnl:.2f}")
