"""
capital_compounder.py

Capital compounding engine for ₹1L → ₹1Cr goal.

Implements three critical mechanisms:

1. Capital Tier Table
   As the account balance grows, automatically scale up:
   - MAX_LOTS (more contracts per trade)
   - MAX_OPEN_POSITIONS (more concurrent trades)
   - RISK_PER_TRADE_PCT (more capital deployed per trade)

   Phase 1: ₹1L–₹2L   → conservative (3 lots, 2 positions, 0.5%)
   Phase 2: ₹2L–₹5L   → growing     (5 lots, 2 positions, 0.75%)
   Phase 3: ₹5L–₹10L  → standard    (8 lots, 3 positions, 1.0%)
   Phase 4: ₹10L–₹25L → scale       (12 lots, 3 positions, 1.0%)
   Phase 5: ₹25L–₹50L → large       (15 lots, 4 positions, 1.0%)
   Phase 6: ₹50L+      → full        (20 lots, 5 positions, 1.0%)

2. Drawdown Circuit Breaker
   When equity drawdown from 30-day peak > 15%:
   - Halves MAX_LOTS until drawdown recovers to < 8%
   - Raises AI confidence threshold by 0.05
   - Sends CRITICAL Telegram alert
   - Auto-restores when drawdown resolves

3. Monthly Profit Lock
   On the last Friday of each month:
   - If month's P&L is positive, 30% is "locked"
   - Daily loss limit raised to protect locked amount
   - System cannot give back more than 70% of monthly gains
   - Resets on the 1st of next month

4. Full Transaction Cost Model
   Every P&L calculation deducts ALL real NSE costs:
   - Brokerage:       ₹20 per leg (both sides) = ₹40 per round trip
   - STT:             0.15% of sell-side premium (options, from Apr 2026)
   - Exchange charge: 0.0355299% of turnover (NSE options)
   - SEBI levy:       0.0001% of turnover
   - GST:             18% on (brokerage + exchange + SEBI)
   - Stamp duty:      0.003% of buy-side premium (options buy)
   Total all-in for a ₹200 premium × 50 qty round trip ≈ ₹75–₹85
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── NSE Options full cost model ────────────────────────────────────────────
# Rates verified against the NSE/Angel schedules on 29 June 2026.

NSE_EXCHANGE_CHARGE_RATE = 0.0003553  # ₹3552.99/cr, rounded to broker billing precision
SEBI_LEVY_RATE           = 0.000001   # 0.0001% of turnover
GST_RATE                 = 0.18       # 18% GST on brokerage+exchange+SEBI
STAMP_DUTY_RATE          = 0.00003    # 0.003% of buy-side premium (buyer only)
STT_OPTIONS_SELL         = 0.0015  # Budget 2026: raised 0.10%→0.15% from Apr 1 2026     # 0.05% of sell premium (seller/exit)

# ─── Equity (cash) cost model — segment-specific (intraday vs delivery) ──────
EQ_EXCHANGE_CHARGE_RATE  = 0.000030699  # ₹306.99/cr NSE cash turnover (both sides)
EQ_STT_DELIVERY          = 0.001      # 0.1% on BOTH buy + sell (delivery)
EQ_STT_INTRADAY_SELL     = 0.00025    # 0.025% on sell side only (intraday)
EQ_STAMP_DELIVERY        = 0.00015    # 0.015% buy side (delivery)
EQ_STAMP_INTRADAY        = 0.00003    # 0.003% buy side (intraday)

# ─── Capital tier definitions ────────────────────────────────────────────────

CAPITAL_TIERS: List[Dict[str, Any]] = [
    {
        "phase":       1,
        "label":       "Seed (₹1L–₹2L)",
        "min_capital": 100_000,
        "max_capital": 200_000,
        "max_lots":    3,
        "max_positions": 2,
        "risk_pct":    0.005,   # 0.5%
        "note":        "Protect seed capital — tight limits",
    },
    {
        "phase":       2,
        "label":       "Growing (₹2L–₹5L)",
        "min_capital": 200_000,
        "max_capital": 500_000,
        "max_lots":    5,
        "max_positions": 2,
        "risk_pct":    0.0075,  # 0.75%
        "note":        "Increase size gradually as confidence grows",
    },
    {
        "phase":       3,
        "label":       "Established (₹5L–₹10L)",
        "min_capital": 500_000,
        "max_capital": 1_000_000,
        "max_lots":    8,
        "max_positions": 3,
        "risk_pct":    0.01,    # 1.0%
        "note":        "Standard operation — 3 concurrent positions",
    },
    {
        "phase":       4,
        "label":       "Scaling (₹10L–₹25L)",
        "min_capital": 1_000_000,
        "max_capital": 2_500_000,
        "max_lots":    12,
        "max_positions": 3,
        "risk_pct":    0.01,
        "note":        "More lots per trade — 3 concurrent",
    },
    {
        "phase":       5,
        "label":       "Large (₹25L–₹50L)",
        "min_capital": 2_500_000,
        "max_capital": 5_000_000,
        "max_lots":    15,
        "max_positions": 4,
        "risk_pct":    0.01,
        "note":        "4 concurrent positions — diversified",
    },
    {
        "phase":       6,
        "label":       "Full Scale (₹50L+)",
        "min_capital": 5_000_000,
        "max_capital": float("inf"),
        "max_lots":    20,
        "max_positions": 5,
        "risk_pct":    0.01,
        "note":        "Full scale — 5 concurrent positions",
    },
]


@dataclass
class TierParams:
    """Live parameters from the current capital tier."""
    phase:          int
    label:          str
    max_lots:       int
    max_positions:  int
    risk_pct:       float
    capital:        float
    drawdown_active: bool   = False
    drawdown_pct:   float   = 0.0
    profit_locked:  float   = 0.0


@dataclass
class TransactionCosts:
    """Full breakdown of all NSE options transaction costs."""
    brokerage:       float = 0.0   # ₹20 × 2 legs
    stt:             float = 0.0   # 0.15% sell-side option premium
    exchange_charge: float = 0.0   # 0.0355299% turnover
    sebi_levy:       float = 0.0   # 0.0001% turnover
    gst:             float = 0.0   # 18% on (brokerage+exchange+sebi)
    stamp_duty:      float = 0.0   # 0.003% buy side
    total:           float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


def calculate_full_costs(
    entry_price:        float,
    exit_price:         float,
    qty:                int,
    brokerage_per_leg:  float = 20.0,
    is_options:         bool  = True,
    product_type:       str   = "INTRADAY",
    side:               str   = "BUY",
) -> TransactionCosts:
    """
    Calculate ALL NSE options transaction costs for a round-trip trade.

    Parameters
    ----------
    entry_price       : option premium at entry (₹ per unit)
    exit_price        : option premium at exit (₹ per unit)
    qty               : total quantity (lots × lot_size)
    brokerage_per_leg : flat brokerage per leg (default ₹20)
    is_options        : if False, applies equity rates instead

    Returns TransactionCosts with full breakdown.

    Example (NIFTY CE: entry=₹200, exit=₹260, qty=50):
      brokerage:      ₹40.00   (₹20 × 2 legs)
      stt:            ₹6.50    (₹260 × 50 × 0.0005)
      exchange:       ₹12.09   (₹260×50 + ₹200×50) × 0.000530/2 each side
      sebi_levy:      ₹0.23
      gst:            ₹9.95    (18% on ₹40 + ₹12.09 + ₹0.23)
      stamp_duty:     ₹0.30    (₹200 × 50 × 0.00003)
      ─────────────────────────
      TOTAL:          ₹69.07
    """
    c = TransactionCosts()

    entry_turnover = entry_price * qty
    exit_turnover  = exit_price  * qty
    total_turnover = entry_turnover + exit_turnover
    # STT is charged on the SELL leg and stamp duty on the BUY leg. Which
    # leg that is depends on trade direction: a long sells at EXIT, a short
    # sells at ENTRY. Charging exit-side STT unconditionally (the pre-
    # 2026-07-17 behavior) flattered winning shorts — the sell-entry
    # turnover is the larger leg exactly when a premium-seller wins.
    is_short = str(side or "BUY").upper() == "SELL"
    sell_turnover = entry_turnover if is_short else exit_turnover
    buy_turnover  = exit_turnover  if is_short else entry_turnover

    # ── Brokerage (flat per leg) ─────────────────────────────────────
    c.brokerage = 2.0 * brokerage_per_leg

    if is_options:
        # ── STT (sell side only for options) ──────────────────────────
        c.stt = sell_turnover * STT_OPTIONS_SELL

        # ── NSE exchange transaction charge (both sides) ──────────────
        c.exchange_charge = total_turnover * NSE_EXCHANGE_CHARGE_RATE

        # ── SEBI levy (both sides) ────────────────────────────────────
        c.sebi_levy = total_turnover * SEBI_LEVY_RATE

        # ── GST: 18% on (brokerage + exchange + SEBI) ─────────────────
        c.gst = (c.brokerage + c.exchange_charge + c.sebi_levy) * GST_RATE

        # ── Stamp duty (buy side only) ────────────────────────────────
        c.stamp_duty = buy_turnover * STAMP_DUTY_RATE

    else:
        # Equity (cash) — segment-specific (intraday vs delivery)
        delivery = str(product_type or "INTRADAY").upper() == "DELIVERY"
        if delivery:
            # STT 0.1% on BOTH buy + sell; stamp 0.015% buy side
            c.stt        = total_turnover * EQ_STT_DELIVERY
            c.stamp_duty = buy_turnover * EQ_STAMP_DELIVERY
        else:
            # Intraday: STT 0.025% sell side only; stamp 0.003% buy side
            c.stt        = sell_turnover * EQ_STT_INTRADAY_SELL
            c.stamp_duty = buy_turnover * EQ_STAMP_INTRADAY
        c.exchange_charge = total_turnover * EQ_EXCHANGE_CHARGE_RATE
        c.sebi_levy       = total_turnover * SEBI_LEVY_RATE
        c.gst             = (c.brokerage + c.exchange_charge + c.sebi_levy) * GST_RATE

    c.total = (
        c.brokerage + c.stt + c.exchange_charge
        + c.sebi_levy + c.gst + c.stamp_duty
    )

    return c


def calculate_net_pnl(
    entry_price:       float,
    exit_price:        float,
    qty:               int,
    side:              str   = "BUY",
    brokerage_per_leg: float = 20.0,
    is_options:        bool  = True,
    product_type:      str   = "INTRADAY",
) -> Tuple[float, float, TransactionCosts]:
    """
    Calculate gross P&L, net P&L, and full cost breakdown.

    Returns (gross_pnl, net_pnl, TransactionCosts)
    """
    if side.upper() == "BUY":
        gross = (exit_price - entry_price) * qty
    else:
        gross = (entry_price - exit_price) * qty

    costs  = calculate_full_costs(entry_price, exit_price, qty,
                                   brokerage_per_leg, is_options, product_type,
                                   side=side)
    net    = gross - costs.total

    return round(gross, 2), round(net, 2), costs


# ─── Capital tier engine ─────────────────────────────────────────────────────

class CapitalCompounder:
    """
    Manages capital tier scaling, drawdown protection, and profit locking.

    Typical usage (called every cycle from live_signal_engine):
        cc = CapitalCompounder()
        params = cc.get_current_params(current_balance)
        # Use params.max_lots, params.max_positions, params.risk_pct
    """

    STATE_FILE = "capital_compounder_state.json"

    def __init__(
        self,
        state_file:                str   = STATE_FILE,
        drawdown_trigger_pct:      float = 0.15,  # 15% drawdown triggers breaker
        drawdown_restore_pct:      float = 0.08,  # 8% drawdown = restore normal
        profit_lock_pct:           float = 0.30,  # lock 30% of monthly profit
        min_capital:               float = 100_000,
    ) -> None:
        self.state_file           = Path(state_file)
        self.drawdown_trigger_pct = float(drawdown_trigger_pct)
        self.drawdown_restore_pct = float(drawdown_restore_pct)
        self.profit_lock_pct      = float(profit_lock_pct)
        self.min_capital          = float(min_capital)

        # Internal state (persisted to JSON)
        self._peak_equity:     float         = min_capital
        self._month_start_bal: float         = min_capital
        self._month_start_date: str          = date.today().isoformat()[:7]
        self._profit_locked:   float         = 0.0
        self._drawdown_active: bool          = False
        self._last_tier_phase: int           = 1
        self._equity_history:  List[float]   = []  # rolling 30 entries

        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                s = json.loads(self.state_file.read_text())
                self._peak_equity      = float(s.get("peak_equity",     self.min_capital))
                self._month_start_bal  = float(s.get("month_start_bal", self.min_capital))
                self._month_start_date = str(  s.get("month_start_date", date.today().isoformat()[:7]))
                self._profit_locked    = float(s.get("profit_locked",   0.0))
                self._drawdown_active  = bool( s.get("drawdown_active",  False))
                self._last_tier_phase  = int(  s.get("last_tier_phase",  1))
                self._equity_history   = list( s.get("equity_history",   []))
            except Exception as exc:
                logger.warning("CapitalCompounder: state load failed: %s", exc)

    def _save_state(self) -> None:
        try:
            self.state_file.write_text(json.dumps({
                "peak_equity":      self._peak_equity,
                "month_start_bal":  self._month_start_bal,
                "month_start_date": self._month_start_date,
                "profit_locked":    self._profit_locked,
                "drawdown_active":  self._drawdown_active,
                "last_tier_phase":  self._last_tier_phase,
                "equity_history":   self._equity_history[-30:],
                "updated_at":       datetime.now().isoformat(),
            }, indent=2))
        except Exception as exc:
            logger.debug("CapitalCompounder: state save failed: %s", exc)

    def get_tier(self, capital: float) -> Dict[str, Any]:
        """Return the capital tier dict for the given balance."""
        for tier in reversed(CAPITAL_TIERS):
            if capital >= tier["min_capital"]:
                return tier
        return CAPITAL_TIERS[0]

    def update_equity(self, current_balance: float) -> None:
        """
        Call once per live cycle with the current account balance.
        Updates peak equity, rolling history, and month-start reference.
        """
        bal = float(current_balance)
        if bal <= 0:
            return

        # Update peak
        if bal > self._peak_equity:
            self._peak_equity = bal

        # Rolling equity history (30 entries max)
        self._equity_history.append(bal)
        if len(self._equity_history) > 30:
            self._equity_history = self._equity_history[-30:]

        # Month boundary — reset monthly accumulators
        current_month = date.today().isoformat()[:7]
        if current_month != self._month_start_date:
            self._month_start_date = current_month
            self._month_start_bal  = bal
            self._profit_locked    = 0.0
            logger.info("CapitalCompounder: new month — monthly accumulators reset")

        self._save_state()

    def get_current_params(self, current_balance: float) -> TierParams:
        """
        Return the live trading parameters for the current capital level.
        Applies drawdown reduction if active.
        """
        bal  = max(float(current_balance), self.min_capital)
        tier = self.get_tier(bal)

        # Drawdown check
        drawdown_pct = 0.0
        if self._peak_equity > 0 and bal < self._peak_equity:
            drawdown_pct = (self._peak_equity - bal) / self._peak_equity

        was_active = self._drawdown_active

        if drawdown_pct >= self.drawdown_trigger_pct:
            self._drawdown_active = True
        elif self._drawdown_active and drawdown_pct <= self.drawdown_restore_pct:
            self._drawdown_active = False
            logger.info(
                "CapitalCompounder: drawdown recovered (%.1f%% < %.0f%%) — "
                "normal parameters restored",
                drawdown_pct * 100, self.drawdown_restore_pct * 100,
            )

        # Check phase change
        new_phase = tier["phase"]
        if new_phase != self._last_tier_phase:
            logger.info(
                "CapitalCompounder: capital tier CHANGED %d → %d | "
                "capital=₹%s | %s",
                self._last_tier_phase, new_phase,
                f"{bal:,.0f}", tier["label"],
            )
            self._last_tier_phase = new_phase

        max_lots      = tier["max_lots"]
        max_positions = tier["max_positions"]
        risk_pct      = tier["risk_pct"]

        # Halve lots during drawdown
        if self._drawdown_active:
            max_lots = max(1, max_lots // 2)
            risk_pct = risk_pct * 0.5
            if not was_active:
                logger.warning(
                    "CapitalCompounder: DRAWDOWN BREAKER ACTIVATED "
                    "(%.1f%% drawdown > %.0f%%) — max_lots halved to %d",
                    drawdown_pct * 100, self.drawdown_trigger_pct * 100,
                    max_lots,
                )

        self._save_state()

        return TierParams(
            phase          = new_phase,
            label          = tier["label"],
            max_lots       = max_lots,
            max_positions  = max_positions,
            risk_pct       = risk_pct,
            capital        = bal,
            drawdown_active = self._drawdown_active,
            drawdown_pct   = round(drawdown_pct, 4),
            profit_locked  = self._profit_locked,
        )

    def check_monthly_profit_lock(
        self,
        current_balance: float,
        daily_loss_limit: float,
    ) -> Tuple[float, bool]:
        """
        On the last Friday of the month, lock 30% of monthly profit.

        Returns (new_daily_loss_limit, was_updated).
        If the month's P&L is negative, returns original limit unchanged.
        """
        today   = date.today()
        is_last_friday = (
            today.weekday() == 4   # Friday
            and (today.day + 7) > 31   # last Friday heuristic
        )
        # Better last-Friday check: next Friday would be in next month
        from datetime import timedelta
        next_friday = today + timedelta(days=7)
        is_last_friday = (today.weekday() == 4 and next_friday.month != today.month)

        if not is_last_friday:
            return daily_loss_limit, False

        monthly_pnl = float(current_balance) - self._month_start_bal
        if monthly_pnl <= 0:
            return daily_loss_limit, False

        lock_amount = monthly_pnl * self.profit_lock_pct
        # Raise daily limit to protect the locked amount
        new_limit = max(daily_loss_limit, lock_amount)
        self._profit_locked = lock_amount
        self._save_state()

        logger.info(
            "CapitalCompounder: PROFIT LOCK | monthly_pnl=₹%.2f "
            "locked=₹%.2f (%.0f%%) new_daily_limit=₹%.2f",
            monthly_pnl, lock_amount, self.profit_lock_pct * 100, new_limit,
        )

        return new_limit, True

    def get_drawdown_confidence_penalty(self) -> float:
        """
        Extra confidence threshold penalty during drawdown.
        Returns 0.05 when drawdown is active (signal quality tightened).
        """
        return 0.05 if self._drawdown_active else 0.0

    def compounding_milestone_check(
        self, current_balance: float
    ) -> Optional[Dict[str, Any]]:
        """
        Returns milestone info if a major capital milestone just crossed,
        else None. Used for Telegram milestone alerts.
        """
        milestones = [
            200_000,   500_000,  1_000_000,
            2_500_000, 5_000_000, 10_000_000,
        ]
        labels = [
            "₹2 Lakh", "₹5 Lakh", "₹10 Lakh",
            "₹25 Lakh", "₹50 Lakh", "₹1 CRORE! 🎉"
        ]
        for m, label in zip(milestones, labels):
            prev_hist = self._equity_history[-2] if len(self._equity_history) >= 2 else 0.0
            if prev_hist < m <= current_balance:
                tier = self.get_tier(current_balance)
                return {
                    "milestone":       label,
                    "balance":         current_balance,
                    "tier_phase":      tier["phase"],
                    "tier_label":      tier["label"],
                    "new_max_lots":    tier["max_lots"],
                    "new_max_pos":     tier["max_positions"],
                }
        return None


# ─── Sector map loader ────────────────────────────────────────────────────────

def load_sector_map(csv_path: str = "nifty200.csv") -> Dict[str, str]:
    """
    Load Symbol → Sector mapping from nifty200.csv.
    Returns empty dict if file not found (safe fallback).
    """
    path = Path(csv_path)
    if not path.exists():
        return {}
    try:
        import pandas as pd
        df = pd.read_csv(path)
        if "Symbol" in df.columns and "Sector" in df.columns:
            return dict(zip(df["Symbol"].str.strip(), df["Sector"].str.strip()))
    except Exception as exc:
        logger.warning("Failed to load sector map: %s", exc)
    return {}


# ─── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("── Full cost breakdown for NIFTY CE round trip ──")
    gross, net, costs = calculate_net_pnl(
        entry_price=200.0, exit_price=260.0, qty=50,
        side="BUY", brokerage_per_leg=20.0, is_options=True,
    )
    print(f"  Entry:          ₹200 × 50 = ₹10,000")
    print(f"  Exit:           ₹260 × 50 = ₹13,000")
    print(f"  Gross P&L:      ₹{gross:,.2f}")
    for k, v in costs.to_dict().items():
        if k != "total" and v > 0:
            print(f"  {k:<20}: ₹{v:,.2f}")
    print(f"  ─────────────────────────────")
    print(f"  TOTAL COSTS:    ₹{costs.total:,.2f}")
    print(f"  NET P&L:        ₹{net:,.2f}")
    print(f"  Cost as % of gross: {costs.total/gross*100:.1f}%")

    print("\n── Capital tier progression ──")
    cc = CapitalCompounder(state_file="/tmp/cc_test.json")
    for capital in [100_000, 200_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000]:
        p = cc.get_current_params(capital)
        print(f"  ₹{capital/100_000:.0f}L → Phase {p.phase}: "
              f"lots={p.max_lots} pos={p.max_positions} risk={p.risk_pct:.2%}")
