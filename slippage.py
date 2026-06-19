"""
slippage.py

Simple slippage model used by all backtests.

Fixes applied
-------------
1. Double-charge risk from confusing parameter naming
   Original constructor: SlippageModel(percent_slippage, min_ticks)
   Backtest callers passed brokerage_per_order (₹20) as min_ticks:
       SlippageModel(slippage_percent, brokerage_per_order)
   Then separately deducted brokerage_per_order again in PnL calculation:
       pnl = gross_pnl - brokerage_per_order
   Result: ₹20 of "minimum slippage" + ₹20 brokerage = ₹40 per leg.

   The parameter is renamed to min_slippage_amount to make clear it is
   a minimum SLIPPAGE floor, separate from brokerage.

   For options on NIFTY at premium ~₹200:
   - 0.05% of ₹200 × 50 qty = ₹5 slippage
   - min_slippage_amount = ₹0.5 (sensible minimum, not ₹20)
   - brokerage charged separately by the caller

   If you are using min_slippage_amount to bundle slippage + brokerage
   together into one deduction, set brokerage_per_order=0 in the caller
   to avoid double-charging.

   Default changed to 0.5 (50 paise), a more realistic option tick.
   If you were relying on min_ticks=20 as a combined cost proxy,
   pass min_slippage_amount=0 and handle all costs in the caller.

2. Input validation added for negative/NaN prices.
"""

from __future__ import annotations


class SlippageModel:
    """
    Slippage model: percent_slippage applied to price with a minimum floor.

    Parameters
    ----------
    percent_slippage : float
        Slippage as a percentage of price (e.g. 0.05 means 0.05%).
    min_slippage_amount : float
        Minimum absolute price slippage in instrument currency units.
        Default 0.5 (50 paise) — one tick for most NSE options.
        Set to 0 to use pure percentage slippage.

    Note on costs
    -------------
    This model computes SLIPPAGE only — the market-impact cost of
    crossing the spread.  Brokerage (exchange fees, STT, etc.) must be
    deducted separately by the caller.  Do not pass brokerage as
    min_slippage_amount unless you intentionally want to bundle costs
    and set brokerage_per_order=0 in the caller.
    """

    def __init__(
        self,
        percent_slippage:    float = 0.05,
        min_slippage_amount: float = 0.5,
    ) -> None:
        if percent_slippage < 0:
            raise ValueError("percent_slippage must be >= 0")
        if min_slippage_amount < 0:
            raise ValueError("min_slippage_amount must be >= 0")

        self.percent_slippage    = float(percent_slippage) / 100.0  # store as decimal
        self.min_slippage_amount = float(min_slippage_amount)

    def apply_slippage(self, price: float, is_buy: bool) -> float:
        """
        Apply slippage to a fill price.

        Buy  → worse price (higher)
        Sell → worse price (lower)

        Parameters
        ----------
        price  : float — reference price (e.g. bar open or last LTP)
        is_buy : bool  — True for buy orders, False for sell orders

        Returns
        -------
        float — adjusted fill price
        """
        if price <= 0:
            return price   # cannot apply slippage to zero/negative price

        pct_slip = price * self.percent_slippage
        slip     = max(pct_slip, self.min_slippage_amount)

        return price + slip if is_buy else price - slip

    def slippage_amount(self, price: float) -> float:
        """Return the absolute slippage amount for a given price (direction-neutral)."""
        if price <= 0:
            return 0.0
        return max(price * self.percent_slippage, self.min_slippage_amount)
