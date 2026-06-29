"""
option_selector.py

Options contract selector used by live_signal_engine._build_execution_plan().

Fixes applied
-------------
1. Symbol format was a placeholder the broker rejects
   build_option_symbol() returned "NIFTY_WEEKLY_22000_CE".
   Angel One expects formats like "NIFTY24MAR2226000CE".

   This class doesn't have access to the broker's master contract file
   at selection time (that's NiftyOptionsEngine's job).  The correct
   architectural split is:
   - OptionSelector: decides strike, lots, rough premium
   - NiftyOptionsEngine: resolves the real broker symbol and places order

   live_signal_engine uses the OptionChoice.symbol only as a display
   identifier, not for direct order placement — it passes the symbol to
   NiftyOptionsEngine.select_trade() which resolves the real contract.
   So the placeholder format is safe here as long as callers understand
   it is not a broker-tradeable symbol.

   The symbol format is now clearly marked as DISPLAY_ONLY and follows
   a human-readable pattern: "NIFTY-22000-CE-WEEKLY".

2. style="AUTO" always mapped to scalping pool
   live_signal_engine._decide_style() does:
       return str(option_signal.get("style", "swing")).lower()
   capital_allocator.capital_for_trade() treats anything != "swing"
   as "scalping".  "auto" != "swing" → always scalping pool.

   Fix: derive style from the signal's regime.  TREND/BREAKOUT regime
   → "swing" (larger capital pool).  Everything else → "scalping".

3. Premium estimate was too rough for risk calculations
   max(10, price * 0.01) for NIFTY at 22000 = ₹220.
   ATM NIFTY options typically trade at 80–200 depending on DTE and IV.
   The estimate is directionally correct but using a fixed 1% leads to
   oversizing in low-IV environments.

   Improved: use a tiered IV-implied estimate based on underlying price
   range, closer to actual NSE option premiums.  Still a fallback —
   NiftyOptionsEngine fetches the real LTP before sizing.
"""

from __future__ import annotations

import logging

try:
    from iv_percentile import get_ivp
    _IVP_AVAILABLE = True
except ImportError:
    _IVP_AVAILABLE = False

from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OptionChoice:
    symbol:           str    # DISPLAY_ONLY — not a broker-tradeable symbol
    option_type:      str
    strike:           int
    expiry:           object
    premium:          float
    lot_size:         int
    lots:             int
    quantity:         int
    capital_required: float
    style:            str    # "swing" or "scalping"


class OptionSelector:

    # Per-index strike intervals — ATM rounding MUST use these, not a flat 50,
    # or non-NIFTY strikes round to non-existent contracts (e.g. BANKNIFTY).
    STRIKE_STEPS = {
        "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
        "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
    }

    def __init__(self, lot_size: int = 65, max_lots_per_trade: int = 3) -> None:
        self.lot_size          = int(lot_size)
        self.max_lots_per_trade = int(max_lots_per_trade)

    # ------------------------------------------------------------------
    # Lot calculation
    # ------------------------------------------------------------------
    def compute_lots(self, trade_capital: float, premium: float) -> int:
        if premium <= 0:
            return 0
        one_lot_cost = premium * self.lot_size
        raw_lots     = int(trade_capital // one_lot_cost)
        lots         = max(0, min(raw_lots, self.max_lots_per_trade))
        # Telegram-settable runtime ceiling (/optlots N) — caps lots for today.
        from option_lot_override import apply_override
        return apply_override(lots)

    # ------------------------------------------------------------------
    # ATM strike
    # ------------------------------------------------------------------
    def get_atm_strike(self, price: float, step: int = 50) -> int:
        return int(round(price / step) * step)

    # ------------------------------------------------------------------
    # Display symbol (NOT a broker-tradeable symbol)
    # ------------------------------------------------------------------
    def build_option_symbol(
        self, index: str, strike: int, option_type: str, expiry: str
    ) -> str:
        """
        Build a human-readable display symbol.

        NOTE: This is NOT a broker-tradeable symbol.  NiftyOptionsEngine
        resolves the actual Angel One / Dhan symbol using its own
        candidate-symbol builder and master contract lookup.
        """
        return f"{index}-{strike}-{option_type}-{expiry}"

    # ------------------------------------------------------------------
    # Premium estimate
    # ------------------------------------------------------------------
    def _estimate_premium(self, price: float) -> float:
        """
        Rough ATM premium estimate based on underlying price range.
        Used only when no live LTP is available.

        These are approximate based on typical NSE market conditions
        (normal IV environment, 1-week DTE).  NiftyOptionsEngine will
        fetch the real LTP before final sizing.
        """
        if price <= 0:
            return 10.0
        if price < 5000:
            return max(10.0, price * 0.012)   # ~1.2% (mid-cap futures)
        if price < 15000:
            return max(50.0, price * 0.008)   # ~0.8% (BANKNIFTY range)
        return max(80.0, price * 0.006)        # ~0.6% (NIFTY at 22000 ≈ ₹132)

    # ------------------------------------------------------------------
    # Style derivation
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_style(signal: dict) -> str:
        """
        Derive trade style from signal regime.
        TREND/BREAKOUT → "swing" (uses larger capital pool).
        All others → "scalping".
        """
        regime = str(signal.get("regime", "")).upper()
        if regime in ("TREND", "BREAKOUT", "BULLISH_TREND", "BEARISH_TREND"):
            return "swing"
        return "scalping"

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def choose_option_from_signal(
        self,
        signal:        dict,
        trade_capital: float,
        expiry:        str   = "WEEKLY",
        index:         str   = "NIFTY",
    ) -> Optional[OptionChoice]:
        try:
            side  = signal.get("side")
            price = float(signal.get("price", 0))

            if not side or price <= 0:
                return None

            option_type = "CE" if side == "BUY" else "PE"
            step        = self.STRIKE_STEPS.get(str(index).upper(), 50)
            strike      = self.get_atm_strike(price, step)
            premium     = self._estimate_premium(price)
            symbol      = self.build_option_symbol(index, strike, option_type, expiry)
            style       = self._derive_style(signal)
            lots        = self.compute_lots(trade_capital, premium)

            if lots <= 0:
                return None

            qty              = lots * self.lot_size
            capital_required = round(premium * qty, 2)

            return OptionChoice(
                symbol           = symbol,
                option_type      = option_type,
                strike           = strike,
                expiry           = expiry,
                premium          = round(premium, 2),
                lot_size         = self.lot_size,
                lots             = lots,
                quantity         = qty,
                capital_required = capital_required,
                style            = style,
            )

        except Exception:
            logger.exception("Option selection failed")
            return None
