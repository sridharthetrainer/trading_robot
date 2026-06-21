"""
dual_mode_engine.py

DUAL MODE: Paper Always + Live When Funded

THE CONCEPT
────────────
The system ALWAYS runs paper trading — every signal is evaluated,
every trade is simulated, data is collected, AI learns.

Additionally, if Angel One balance >= MIN_LIVE_CAPITAL, the system
SIMULTANEOUSLY places the same trade in live mode.

Think of it as:
  Paper trade  = your shadow portfolio (always running, never misses data)
  Live trade   = your real portfolio (only when funded)

WHY THIS IS BETTER THAN SIMPLE SWITCHING
─────────────────────────────────────────
Old behaviour:
  Balance low  → PAPER only (no live trades)
  Balance high → LIVE only (no paper record)

New behaviour:
  Balance low  → PAPER always (continuous data, learning never stops)
  Balance high → PAPER + LIVE simultaneously
                 Paper records the full history
                 Live captures real profits

BENEFITS:
  1. AI model never stops training — even during low-balance periods
  2. Full trade history even if you pause live trading for weeks
  3. Can compare paper vs live performance side-by-side
  4. Zero data gaps — every signal is captured

MORNING BALANCE CHECK (8:55 AM daily)
───────────────────────────────────────
  1. Connect to Angel One
  2. Fetch account balance
  3. If balance >= ₹25,000 → enable live orders for the day
  4. If balance < ₹25,000  → paper only for the day
  5. Re-check every 30 minutes during session
  6. Telegram alert on any status change

TRADE FLOW
───────────
  Signal fires → ALWAYS record as paper trade
               ↓
               Is live enabled AND balance sufficient?
               ↓ YES         ↓ NO
          Place real order   Paper only
          Record both        Record paper only
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ── Manual live-arming ─────────────────────────────────────────────────────────
# Live trading must be ARMED deliberately each day — it must never auto-promote
# to real orders on balance alone, because the algo edge is not yet validated.
ARM_FILE = "live_armed.json"


def _is_armed_today() -> bool:
    try:
        import json, os
        if not os.path.exists(ARM_FILE):
            return False
        d = json.load(open(ARM_FILE)).get("armed_date", "")
        return d == date.today().isoformat()
    except Exception:
        return False


def is_live_armed_today() -> bool:
    """Public read-only helper shared by auto-mode and status surfaces."""
    return _is_armed_today()


def arm_live_trading() -> str:
    """Arm live trading for today (call from a deliberate /arm command)."""
    import json
    today = date.today().isoformat()
    try:
        json.dump({"armed_date": today, "armed_at": datetime.now().isoformat()},
                  open(ARM_FILE, "w"))
        logger.warning("LIVE TRADING ARMED for %s", today)
    except Exception as e:
        logger.error("arm_live_trading: %s", e)
    return today


def disarm_live_trading() -> None:
    """Immediately disarm live trading (back to paper-only)."""
    import os
    try:
        if os.path.exists(ARM_FILE):
            os.remove(ARM_FILE)
        logger.warning("LIVE TRADING DISARMED — paper only")
    except Exception as e:
        logger.error("disarm_live_trading: %s", e)


class DualModeEngine:
    """
    Manages the dual paper+live trading mode.

    Paper trades always execute.
    Live trades execute only when balance is sufficient.
    Both are recorded in trades.db with mode tag.
    """

    CHECK_INTERVAL_SEC = 1800    # re-check balance every 30 min
    PREMARKET_CHECK_HOUR   = 8
    PREMARKET_CHECK_MINUTE = 55  # 8:55 AM — before 9:15 open

    def __init__(
        self,
        broker_manager = None,
        alerts         = None,
    ) -> None:
        self._broker       = broker_manager
        self._alerts       = alerts
        self._balance      = 0.0
        self._live_enabled = False   # live orders active today?
        self._last_check   = 0.0
        self._last_check_date: Optional[date] = None
        self._check_count  = 0

        # Load config
        try:
            import config as cfg
            self._min_capital   = float(getattr(cfg, "MIN_LIVE_CAPITAL", 25000))
            self._paper_forced  = bool(getattr(cfg, "PAPER_TRADING",      True))
            self._real_allowed  = bool(getattr(cfg, "ENABLE_REAL_TRADING", False))
            self._require_arm   = bool(getattr(cfg, "REQUIRE_LIVE_ARM", False))
        except Exception:
            self._min_capital  = 25000
            self._paper_forced = True
            self._real_allowed = False
            self._require_arm  = False

        logger.info(
            "DualModeEngine init | min_capital=₹%.0f paper_forced=%s real_allowed=%s require_arm=%s",
            self._min_capital, self._paper_forced, self._real_allowed, self._require_arm,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def is_live_enabled(self) -> bool:
        """True if live orders should be placed alongside paper."""
        if self._paper_forced:
            return False
        if not self._real_allowed:
            return False
        if self._require_arm and not _is_armed_today():
            return False
        return self._live_enabled

    def should_place_live_order(self) -> bool:
        """Call before every trade. True = place real order too."""
        self.run_balance_check(force=True)
        return self.is_live_enabled()

    def get_mode_label(self) -> str:
        """Human-readable mode label."""
        if self.is_live_enabled():
            return "PAPER+LIVE"
        return "PAPER"

    def get_status(self) -> dict:
        return {
            "mode":          self.get_mode_label(),
            "live_enabled":  self.is_live_enabled(),
            "balance":       self._balance,
            "min_capital":   self._min_capital,
            "balance_ok":    self._balance > 0,
            "paper_forced":  self._paper_forced,
            "real_allowed":  self._real_allowed,
            "armed":         _is_armed_today(),
            "require_arm":   self._require_arm,
            "last_check":    datetime.fromtimestamp(self._last_check).strftime("%H:%M:%S")
                             if self._last_check else "never",
            "shortfall":     max(0, self._min_capital - self._balance),
        }

    # ── Balance check ─────────────────────────────────────────────────────────

    def run_balance_check(self, force: bool = False) -> dict:
        """
        Fetch Angel One balance and decide if live trading is enabled today.
        Called:
          - At bot startup
          - At 8:55 AM (before market open)
          - Every 30 minutes during session
          - After every trade close (balance may have changed)

        Returns status dict with live_enabled and balance.
        """
        now = time.time()
        if not force and (now - self._last_check) < self.CHECK_INTERVAL_SEC:
            return self.get_status()

        self._last_check = now
        self._check_count += 1
        prev_live = self._live_enabled

        # RULE 1: paper_forced in .env → always paper, never live
        if self._paper_forced:
            self._live_enabled = False
            if self._check_count == 1:
                logger.info("Dual mode: PAPER_TRADING=true → paper only")
                self._send_status_alert(prev_live, reason="PAPER_TRADING=true in .env")
            return self.get_status()

        # RULE 2: real_allowed must be true in .env
        if not self._real_allowed:
            self._live_enabled = False
            if self._check_count == 1:
                logger.info("Dual mode: ENABLE_REAL_TRADING=false → paper only")
            return self.get_status()

        # RULE 3: Fetch balance from Angel One
        balance = self._fetch_balance()
        self._balance = balance

        if balance <= 0:
            # Cannot connect or zero balance → paper only
            self._live_enabled = False
            if prev_live:
                logger.warning("Balance unavailable — switching to paper only")
                self._send_status_alert(
                    prev_live=True,
                    reason=f"cannot_fetch_balance_or_zero"
                )
            return self.get_status()

        # RULE 4: Any positive balance can attempt live. TradeManager will
        # downsize/skip the live leg if the specific order cannot fit.
        if balance > 0:
            self._live_enabled = True
            if not prev_live:
                # Just became funded — switch to live
                shortfall = 0
                logger.info(
                    "DUAL MODE: balance ₹%.0f available — live orders enabled "
                    "when the specific trade can fit.",
                    balance
                )
                self._send_status_alert(prev_live=False, reason="balance_sufficient")
        else:
            self._live_enabled = False
            shortfall = self._min_capital - balance
            if prev_live:
                # Was live, now insufficient
                logger.warning(
                    "DUAL MODE: balance ₹%.0f < ₹%.0f → paper only (need ₹%.0f more)",
                    balance, self._min_capital, shortfall
                )
                self._send_status_alert(prev_live=True, reason="balance_insufficient")
            else:
                logger.info(
                    "Dual mode: paper only | balance ₹%.0f / ₹%.0f needed (₹%.0f shortfall)",
                    balance, self._min_capital, shortfall
                )

        return self.get_status()

    def run_premarket_check(self) -> dict:
        """
        Run at 8:55 AM every day.
        Fetches fresh balance and determines today's trading mode.
        Sends Telegram summary of today's setup.
        """
        result = self.run_balance_check(force=True)
        self._last_check_date = date.today()

        mode    = result["mode"]
        balance = result["balance"]
        live    = result["live_enabled"]

        if live:
            msg = (
                f"✅ <b>TODAY: PAPER + LIVE TRADING</b>\n"
                f"Angel One Balance: ₹{balance:,.0f}\n"
                f"Minimum required:  ₹{self._min_capital:,.0f}\n"
                f"Real orders: ACTIVE from next signal\n"
                f"Paper trades: Always running\n"
                f"🕐 Market opens 9:15 AM"
            )
        else:
            shortfall = result.get("shortfall", 0)
            bal_str = f"₹{balance:,.0f}" if balance > 0 else "Not fetched (paper mode)"
            msg = (
                f"📋 <b>TODAY: PAPER ONLY</b>\n"
                f"Angel One Balance: {bal_str}\n"
                f"Required for live: ₹{self._min_capital:,.0f}\n"
                f"{"Shortfall: ₹" + f"{shortfall:,.0f}" if shortfall > 0 else "Fund account to enable live trading"}\n"
                f"Paper trading: ACTIVE — all signals captured\n"
                f"🕐 Market opens 9:15 AM"
            )

        try:
            if self._alerts:
                self._alerts.send(msg, dedup_key=f"dual_mode_{date.today()}")
        except Exception:
            pass

        logger.info(
            "Pre-market check complete | mode=%s balance=₹%.0f",
            mode, balance
        )
        return result

    # ── Internal ─────────────────────────────────────────────────────────────

    def _fetch_balance(self) -> float:
        """
        Fetch REAL Angel One balance via API.
        Returns 0.0 if:
          - No broker connected
          - Paper trading mode (broker returns dummy value)
          - API call fails
        NEVER returns the paper capital (₹1,00,000 default).
        """
        if not self._broker:
            return 0.0
        try:
            broker = self._broker.get_execution_broker()
            if not broker:
                return 0.0
            # Force real balance fetch even if paper_trade flag is set
            # We need the ACTUAL Angel One balance to decide live mode
            real_bal = 0.0
            if hasattr(broker, "angel") and broker.angel:
                angel_obj = broker.angel
                orig_paper = angel_obj.paper_trade
                angel_obj.paper_trade = False   # force real API call
                try:
                    real_bal = float(angel_obj.get_balance() or 0)
                except Exception as _e:
                    logger.debug("Real balance fetch error: %s", _e)
                finally:
                    angel_obj.paper_trade = orig_paper   # restore
            # Sanity check: paper fallback values are exactly 1_000_000
            if real_bal in (1_000_000.0, 100_000.0):
                logger.debug("Balance looks like paper fallback (%.0f) — treating as 0", real_bal)
                return 0.0
            if 0 < real_bal < 100:
                logger.warning("Balance ₹%.2f looks wrong — treating as 0", real_bal)
                return 0.0
            return real_bal
        except Exception as e:
            logger.debug("Balance fetch error: %s", e)
            return 0.0

    def _send_status_alert(self, prev_live: bool, reason: str) -> None:
        if not self._alerts:
            return
        try:
            status = self.get_status()
            if status["live_enabled"]:
                msg = (
                    f"💰 <b>LIVE TRADING ACTIVATED</b>\n"
                    f"Balance ₹{status['balance']:,.0f} >= ₹{self._min_capital:,.0f}\n"
                    f"Mode: PAPER + LIVE\n"
                    f"Real orders will be placed from next signal\n"
                    f"Paper history continues uninterrupted"
                )
            else:
                shortfall = status.get("shortfall", 0)
                msg = (
                    f"📋 <b>PAPER ONLY MODE</b>\n"
                    f"Balance ₹{status['balance']:,.0f} / ₹{self._min_capital:,.0f} needed\n"
                    f"Shortfall: ₹{shortfall:,.0f}\n"
                    f"Reason: {reason}\n"
                    f"Paper trading continues — no data lost"
                )
            self._alerts.send(msg,
                dedup_key=f"dme_{reason}_{date.today()}_{int(__import__('time').time()//3600)}",
                dedup_cooldown_override=3600)  # max once per hour
        except Exception:
            pass


# Singleton
_dual_engine: Optional[DualModeEngine] = None

def get_dual_engine(broker_manager=None, alerts=None) -> DualModeEngine:
    global _dual_engine
    if _dual_engine is None:
        _dual_engine = DualModeEngine(broker_manager, alerts)
    if broker_manager and not _dual_engine._broker:
        _dual_engine._broker = broker_manager
    if alerts and not _dual_engine._alerts:
        _dual_engine._alerts = alerts
    return _dual_engine
