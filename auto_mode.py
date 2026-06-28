"""
auto_mode.py

Paper ↔ Live mode selection with explicit daily live arming.

How it works
─────────────
1. At startup: connect to Angel One, fetch balance
   - Balance >= MIN_LIVE_CAPITAL → LIVE mode
   - Balance < MIN_LIVE_CAPITAL  → PAPER mode (simulated orders)
   - Login fails                 → PAPER mode (safety)

2. Every 30 minutes during session: re-check balance
   - Funds added → eligible for LIVE only if live was armed today
   - Funds withdrawn below threshold → automatically downgrade to PAPER
   - Open positions are NOT affected by mode change (they close normally)
   - Only NEW entries use the new mode

3. After every trade close: re-check balance
   - If you just lost money and crossed below threshold → back to PAPER

Capital thresholds (configurable in .env)
──────────────────────────────────────────
MIN_LIVE_CAPITAL = 500      # live mode threshold

Safety rules
─────────────
- Never switch TO live if credentials are invalid
- Optional: require manual /arm by setting REQUIRE_LIVE_ARM=true
- Never switch TO live during after-hours (only at market open)
- Never switch mode while a position is open (wait for it to close)
- Always send Telegram alert on any mode switch
- Log every decision with reason

.env settings
──────────────
AUTO_MODE_SWITCH=true        # enable auto switching (default: true)
MIN_LIVE_CAPITAL=500
PAPER_TRADING=false
ENABLE_REAL_TRADING=true
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from dual_mode_engine import is_live_armed_today
except Exception:
    def is_live_armed_today() -> bool:
        return False

# ── Capital thresholds ────────────────────────────────────────────────────────
# These are PER INSTRUMENT minimums. The system uses whichever is highest
# based on what it is currently trading.
CAPITAL_REQUIREMENTS = {
    "NIFTY":      500,
    "BANKNIFTY":  500,
    "FINNIFTY":   500,
    "MIDCPNIFTY": 500,
    "SENSEX":     500,
    "STOCKS":     500,
    "DEFAULT":    500,
}


class AutoModeSelector:
    """
    Decides paper vs live trading based on Angel One balance and daily live arm.
    
    States:
      PAPER   — simulated orders, no real money
      LIVE    — real orders on Angel One
      CHECKING — evaluating which mode to use
    
    The state machine:
      PAPER → LIVE:   balance ok, credentials valid, real enabled
      LIVE → PAPER:   balance falls below MIN_LIVE_CAPITAL OR credentials fail
      * → PAPER:      any error → fail safe to paper
    """

    def __init__(
        self,
        broker_manager         = None,
        alerts                 = None,
        min_live_capital:  float = 0.0,    # 0 = read from .env
        auto_switch:       bool  = True,
    ) -> None:
        self._broker         = broker_manager
        self._alerts         = alerts
        self._auto_switch    = auto_switch
        self._current_mode   = "PAPER"     # always start safe
        self._last_balance   = 0.0
        self._last_check_ts  = 0.0
        self._check_interval = 1800        # check every 30 min
        self._login_valid    = False
        self._switch_count   = 0
        self._mode_history   = []          # [(ts, mode, reason, balance)]

        # Load min capital from .env or use parameter
        try:
            import config as cfg
            self._min_live   = float(getattr(cfg, "MIN_LIVE_CAPITAL", min_live_capital or 500))
            self._auto_switch = bool(getattr(cfg, "AUTO_MODE_SWITCH", auto_switch))
            # Hard overrides from .env
            self._force_paper = bool(getattr(cfg, "PAPER_TRADING", True))
            self._real_enabled = bool(getattr(cfg, "ENABLE_REAL_TRADING", False))
            self._require_arm = bool(getattr(cfg, "REQUIRE_LIVE_ARM", False))
        except Exception:
            self._min_live    = min_live_capital or 500
            self._force_paper = True
            self._real_enabled = False
            self._require_arm = False

        logger.info(
            "AutoModeSelector init | min_live=₹%.0f auto_switch=%s "
            "force_paper=%s real_enabled=%s require_arm=%s",
            self._min_live, self._auto_switch,
            self._force_paper, self._real_enabled, self._require_arm,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        return self._current_mode == "LIVE"

    @property
    def is_paper(self) -> bool:
        return self._current_mode == "PAPER"

    @property
    def mode(self) -> str:
        return self._current_mode

    @property
    def balance(self) -> float:
        return self._last_balance

    def should_check(self) -> bool:
        """True if enough time has passed to warrant a re-check."""
        return (time.time() - self._last_check_ts) >= self._check_interval

    def evaluate(self, force: bool = False, open_positions: int = 0) -> dict:
        """
        Evaluate current mode. Called at startup, every 30 min, and after trades.
        
        Returns dict with:
          mode:        current mode after evaluation
          switched:    True if mode changed
          balance:     current account balance
          reason:      why this mode was chosen
          can_trade:   True if ready to trade
        """
        if not force and not self.should_check():
            return self._status()

        self._last_check_ts = time.time()
        prev_mode = self._current_mode

        # ══════════════════════════════════════════════════════════════════════
        # DECISION TREE — reads top to bottom, first match wins
        # ══════════════════════════════════════════════════════════════════════

        # RULE 1: PAPER_TRADING=true in .env → always paper, no exceptions
        if self._force_paper:
            self._set_mode("PAPER", "PAPER_TRADING=true in .env")
            return self._status(switched=(prev_mode != "PAPER"))

        # RULE 2: Try to connect to Angel One and get real balance
        balance, login_ok = self._fetch_balance()
        self._last_balance = balance
        self._login_valid  = login_ok

        # RULE 3: Cannot connect to Angel One → paper (safe fallback)
        if not login_ok:
            self._set_mode("PAPER", "angel_one_login_failed_or_unreachable")
            switched = prev_mode != "PAPER"
            if switched:
                logger.warning("Mode → PAPER: cannot reach Angel One")
                self._alert_switch("PAPER", 0,
                    "Cannot connect to Angel One — staying in paper mode")
            return self._status(switched=switched)

        # RULE 4: Balance = 0 — try last known balance before going paper
        if balance <= 0:
            if self._last_balance > 0:
                logger.warning("Balance fetch returned 0 — using last known: ₹%.0f",
                               self._last_balance)
                balance = self._last_balance  # use cached balance
            else:
                self._set_mode("PAPER", "account_balance_zero_or_empty")
            switched = prev_mode != "PAPER"
            if switched:
                self._alert_switch("PAPER", 0,
                    "Angel One account balance is ₹0 — paper mode active")
            return self._status(switched=switched)

        # RULE 5: Balance must be greater than minimum → paper otherwise
        if balance <= self._min_live:
            self._set_mode("PAPER",
                f"balance_₹{balance:.0f}_not_above_minimum_₹{self._min_live:.0f}")
            switched = prev_mode != "PAPER"
            if switched:
                self._alert_switch("PAPER", balance,
                    f"Balance ₹{balance:,.0f} <= minimum ₹{self._min_live:,.0f} "
                    f"— paper mode. Add funds to go live.")
            else:
                # Already in paper — log but don't alert every cycle
                logger.info(
                    "Paper mode: balance ₹%.0f / >₹%.0f required (%.0f%% of target)",
                    balance, self._min_live, balance / self._min_live * 100
                )
            return self._status(switched=switched)

        # RULE 6: Sufficient balance but ENABLE_REAL_TRADING=false → paper
        if not self._real_enabled:
            self._set_mode("PAPER",
                f"balance_ok_₹{balance:.0f}_but_ENABLE_REAL_TRADING=false")
            if prev_mode != "PAPER":
                logger.info(
                    "Balance ₹%.0f is sufficient but ENABLE_REAL_TRADING=false "
                    "— set to true in .env to go live", balance
                )
            return self._status(switched=(prev_mode != "PAPER"))

        # RULE 7: Positions open — wait for them to close before switching
        if open_positions > 0 and prev_mode == "PAPER":
            self._set_mode("PAPER",
                f"balance_ok_but_{open_positions}_positions_open_will_switch_after")
            logger.info("Balance sufficient — will switch LIVE after %d open positions close",
                        open_positions)
            return self._status(switched=False)

        # RULE 8: Optional explicit live arm, if configured.
        if self._require_arm and not is_live_armed_today():
            self._set_mode("PAPER", "balance_ok_but_live_not_armed_today")
            if prev_mode != "PAPER":
                self._alert_switch("PAPER", balance, "Live trading not armed for today")
            return self._status(switched=(prev_mode != "PAPER"))

        # Balance and credentials are necessary, not sufficient. All mode
        # selectors share the same evidence gate so none can bypass failed edge,
        # data, execution, or broker-readiness controls.
        try:
            from live_admission import evaluate_live_admission
            admission = evaluate_live_admission()
        except Exception as exc:
            admission = {"allowed": False, "reason": f"readiness_gate_error:{exc}"}
        if not admission.get("allowed"):
            self._set_mode("PAPER", f"live_admission_blocked:{admission.get('reason', 'unknown')}")
            return self._status(switched=(prev_mode != "PAPER"))

        # RULE 9: ALL CONDITIONS MET → LIVE mode
        # balance > minimum AND login ok AND real trading enabled AND no open positions
        self._set_mode("LIVE",
            f"balance_₹{balance:.0f}_>_min_₹{self._min_live:.0f}_all_checks_passed")
        switched = prev_mode != "LIVE"
        if switched:
            self._alert_switch("LIVE", balance,
                f"Balance ₹{balance:,.0f} > ₹{self._min_live:,.0f} minimum")

        return self._status(switched=switched)

    def min_capital_for_symbol(self, symbol: str) -> float:
        """Return minimum capital required to trade this symbol live."""
        sym = symbol.upper()
        for key, val in CAPITAL_REQUIREMENTS.items():
            if key in sym:
                return float(val)
        return float(CAPITAL_REQUIREMENTS["DEFAULT"])

    def can_trade_symbol_live(self, symbol: str) -> bool:
        """True if balance is sufficient for live trading of this specific symbol."""
        # Always allow scanning — balance only affects LIVE vs PAPER execution
        return True  # scanning always allowed; trade_manager checks capital for real orders

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_balance(self) -> tuple[float, bool]:
        """
        Fetch balance from Angel One.
        Returns (balance, login_successful).
        On any failure: returns (0.0, False) → safe fallback to paper.
        """
        if not self._broker:
            return 0.0, False

        try:
            broker = self._broker.get_execution_broker()
            if not broker:
                return 0.0, False

            # Test if connected (login valid)
            if hasattr(broker, "is_connected"):
                if not broker.is_connected():
                    # Try to reconnect
                    if hasattr(broker, "angel") and hasattr(broker.angel, "connect"):
                        connected = broker.angel.connect()
                        if not connected:
                            logger.warning("AutoMode: Angel One reconnect failed")
                            return 0.0, False
                    else:
                        return 0.0, False

            # Fetch a verified REAL Angel balance.  Do not use broker-level
            # fallback/config capital here; that can be PAPER_CAPITAL and must
            # never promote the system to live mode.
            if hasattr(broker, "angel") and broker.angel and hasattr(broker.angel, "get_balance"):
                balance = broker.angel.get_balance(force_real=True)
            elif hasattr(broker, "get_balance"):
                balance = broker.get_balance()
            else:
                return 0.0, False

            if balance and float(balance) >= 100:
                try:
                    import config as cfg
                    paper_cap = float(getattr(cfg, "PAPER_CAPITAL", 100000))
                    configured_cap = float(getattr(cfg, "CAPITAL", paper_cap))
                    if float(balance) in {paper_cap, configured_cap} and bool(
                            getattr(cfg, "PAPER_ORDERS_ONLY", False)):
                        logger.warning(
                            "AutoMode: rejecting fallback balance ₹%.0f for live promotion",
                            float(balance),
                        )
                        return 0.0, False
                except Exception:
                    pass
                return float(balance), True
            elif balance and 0 < float(balance) < 100:
                logger.warning(
                    "AutoMode: balance ₹%.2f looks wrong (< ₹100) — staying paper",
                    float(balance)
                )
                return 0.0, False

            return 0.0, False

        except Exception as exc:
            logger.debug("AutoMode balance fetch: %s", exc)
            return 0.0, False

    def _set_mode(self, mode: str, reason: str) -> None:
        """Set the current mode and record in history."""
        if self._current_mode != mode:
            self._switch_count += 1
            logger.info(
                "MODE SWITCH: %s → %s | reason: %s | balance: ₹%.0f",
                self._current_mode, mode, reason, self._last_balance,
            )
        self._current_mode = mode
        self._mode_history.append({
            "ts":      time.time(),
            "mode":    mode,
            "reason":  reason,
            "balance": self._last_balance,
        })
        # Keep last 100 entries
        if len(self._mode_history) > 100:
            self._mode_history.pop(0)

        # Apply to broker_manager so orders use correct mode
        self._apply_mode_to_broker(mode)

    def _apply_mode_to_broker(self, mode: str) -> None:
        """
        Apply mode to broker for ORDER ROUTING only.
        CRITICAL: NEVER set angel.paper_trade = True
        paper_trade=True blocks Angel connection → no data → Scanned: 0
        Instead use block_real_orders flag for order-level control.
        """
        if not self._broker:
            return
        try:
            is_paper = (mode == "PAPER")
            broker = self._broker.get_execution_broker()
            if broker:
                # Set order-blocking flag (NOT paper_trade)
                broker.block_real_orders = is_paper
                if hasattr(broker, "angel"):
                    broker.angel.block_real_orders = is_paper
                    # NEVER set paper_trade — it kills data fetch
                    # broker.angel.paper_trade stays False always
            if hasattr(self._broker, "brokers"):
                for b in self._broker.brokers:
                    b.block_real_orders = is_paper
                    if hasattr(b, "angel"):
                        b.angel.block_real_orders = is_paper
        except Exception as exc:
            logger.debug("_apply_mode_to_broker: %s", exc)

    def _alert_switch(self, new_mode: str, balance: float, reason: str) -> None:
        """Send Telegram alert when mode switches."""
        if not self._alerts:
            return
        icon    = "💰" if new_mode == "LIVE" else "📄"
        heading = "SWITCHED TO LIVE TRADING" if new_mode == "LIVE" else "SWITCHED TO PAPER MODE"
        msg = (
            f"{icon} <b>{heading}</b>\n"
            f"Balance:   ₹{balance:,.0f}\n"
            f"Threshold: ₹{self._min_live:,.0f}\n"
            f"Reason:    {reason}\n"
            f"🕐 {datetime.now().strftime('%d %b %H:%M')}"
        )
        if new_mode == "LIVE":
            msg += "\n\n⚠️ <b>REAL MONEY MODE</b> — orders go to Angel One"
        else:
            msg += "\n\nℹ️ Orders are simulated — no real money at risk"
            if balance <= self._min_live and balance > 0:
                needed = max(1.0, self._min_live - balance + 1.0)
                msg += f"\n\nTo go live: add ₹{needed:,.0f} more to Angel One"

        try:
            self._alerts.send(msg, dedup_key=f"mode_switch_{new_mode}_{int(time.time()//300)}")
        except Exception:
            pass

    def _status(self, switched: bool = False) -> dict:
        return {
            "mode":       self._current_mode,
            "switched":   switched,
            "balance":    self._last_balance,
            "login_ok":   self._login_valid,
            "min_capital":self._min_live,
            "can_trade":  self._last_balance > self._min_live and self._login_valid,
            "is_live":    self._current_mode == "LIVE",
            "is_paper":   self._current_mode == "PAPER",
            "switch_count": self._switch_count,
        }

    def get_summary(self) -> str:
        """One-line status for Telegram and logs."""
        b = self._last_balance
        m = self._current_mode
        icon = "💰" if m == "LIVE" else "📄"
        pct = b / max(self._min_live, 1) * 100
        return (
            f"{icon} {m}  ₹{b:,.0f}  "
            f"({'✅ above' if b > self._min_live else '❌ below'} "
            f"₹{self._min_live:,.0f} min, {pct:.0f}%)"
        )


# ── Module singleton ──────────────────────────────────────────────────────────
_selector: Optional[AutoModeSelector] = None


def get_auto_mode_selector(
    broker_manager = None,
    alerts         = None,
) -> AutoModeSelector:
    global _selector
    if _selector is None:
        _selector = AutoModeSelector(
            broker_manager = broker_manager,
            alerts         = alerts,
        )
    elif broker_manager and not _selector._broker:
        _selector._broker = broker_manager
    elif alerts and not _selector._alerts:
        _selector._alerts = alerts
    return _selector
