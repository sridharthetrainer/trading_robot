"""
portfolio_risk.py

Portfolio-level risk controls for live/options trading.

Fixes applied
-------------
1. Fallback stop risk calculation was wrong for options
   Original: `entry_price * 0.05 * qty` when no stop_loss provided.
   For a ₹180 option premium × 50 qty that gives estimated risk = ₹450.
   But for a BOUGHT option the true maximum loss is the FULL premium paid
   = ₹180 × 50 = ₹9,000. Using 5% of premium as the risk estimate made
   the portfolio risk % check meaningless — it would approve trades 20×
   larger than intended.

   The second fallback `entry * 0.02 * qty` (when stop == entry) had the
   same issue.

   Fix: add `is_options` parameter to `evaluate_new_trade()`. When True:
   - No-stop fallback = full premium × qty × fallback_loss_fraction
     (default 1.0 — assume full premium can be lost)
   - Zero-distance fallback = same
   Callers that pass a proper stop_loss are unaffected.

2. No maximum premium-per-trade guard
   An options buyer can allocate an unreasonable fraction of capital to
   a single premium outlay even when exposure and risk checks pass.
   e.g. with capital=₹1,00,000 and max_total_exposure_pct=1.0, buying
   a ₹500 option × 200 qty (4 lots × 50) costs ₹1,00,000 — 100% of
   capital in one trade. A dedicated max_premium_per_trade_pct guard
   (default 10%) caps this independently of the portfolio risk % check.

   When is_options=False (futures/equity) this check is skipped.

3. Notional exposure added to metadata
   When spot_price and lot_size are provided, the result's metadata now
   includes notional_value = spot_price × lots × lot_size. This is purely
   informational but helps dashboards show the true underlying exposure
   rather than just the premium outlay.
"""

from __future__ import annotations
try:
    from value_at_risk import get_var_engine as _get_var
    _VAR_AVAIL = True
except ImportError:
    _VAR_AVAIL = False

import math
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any

# logger was used (VaR size-reduction path) but never defined → NameError in the
# risk layer when that path ran. Define it here.
logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    allowed:                     bool
    approved_quantity:           int
    approved_lots:               int
    reason:                      str
    estimated_trade_risk:        float
    resulting_total_exposure:    float
    resulting_symbol_exposure:   float
    resulting_portfolio_risk_pct: float
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PortfolioRiskManager:
    """
    Portfolio-level risk controls for live/options trading.

    Checks (in order):
    1. Capital validity
    2. Requested quantity > 0
    3. Max open positions
    4. Daily loss limit
    5. Correlated position count
    6. Max premium per trade % (options only)
    7. Exposure and risk limits — reduce quantity to lot boundary

    For options trading, always pass:
        is_options=True
        stop_loss = the premium level at which you would exit
                    (or None to use the full-premium fallback)
    """

    def __init__(
        self,
        capital: float,
        max_open_positions: int       = 1,
        max_portfolio_risk_pct: float = 0.03,
        max_symbol_exposure_pct: float = 0.25,
        max_total_exposure_pct: float  = 1.00,
        max_correlated_positions: int  = 1,
        max_premium_per_trade_pct: float = 0.10,  # options only — 10% of capital per trade
        fallback_option_loss_fraction: float = 1.0,  # assume full premium loss when no stop
    ) -> None:
        self.capital                       = float(capital)
        self.max_open_positions            = int(max_open_positions)
        self.max_portfolio_risk_pct        = float(max_portfolio_risk_pct)
        self.max_symbol_exposure_pct       = float(max_symbol_exposure_pct)
        self.max_total_exposure_pct        = float(max_total_exposure_pct)
        self.max_correlated_positions      = int(max_correlated_positions)
        self.max_premium_per_trade_pct     = float(max_premium_per_trade_pct)
        self.fallback_option_loss_fraction = float(fallback_option_loss_fraction)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def evaluate_new_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: Optional[float],
        requested_quantity: int,
        open_positions: Optional[List[Dict[str, Any]]] = None,
        correlation_group: Optional[str] = None,
        current_daily_pnl: float = 0.0,
        daily_loss_limit: Optional[float] = None,
        lot_size: int = 65,
        is_options: bool = False,       # True for all options trades
        spot_price: Optional[float] = None,  # underlying spot, for notional
    ) -> RiskDecision:
        """
        Evaluate whether a new trade can be opened.

        Returns RiskDecision with:
        - allowed=True  → approved_quantity (may be reduced to lot boundary)
        - allowed=False → reason string explaining the block
        """
        open_positions = open_positions or []

        # ---- Basic guards -------------------------------------------
        if self.capital <= 0:
            return self._deny("Invalid capital", open_positions, symbol)

        if requested_quantity <= 0:
            return self._deny("Requested quantity must be positive", open_positions, symbol)

        if len(open_positions) >= self.max_open_positions:
            return self._deny("Max open positions reached", open_positions, symbol)

        # ── VaR check: new trade + existing portfolio ─────────────────
        _enable_var = False
        try:
            import config as _cfg
            _enable_var = bool(getattr(_cfg, "ENABLE_VAR", False))
        except Exception:
            _enable_var = False

        if _VAR_AVAIL and _enable_var:
            try:
                _var_eng = _get_var(self.capital)
                _stop_dist = abs(entry_price - stop_loss) if stop_loss else entry_price * 0.015
                _trade_risk = _stop_dist * requested_quantity
                _var_check = _var_eng.check_new_trade_var(_trade_risk)
                if not _var_check["allowed"]:
                    # Soft block: reduce quantity to fit within VaR, not hard reject
                    _reduction = _var_check["headroom"] / max(_stop_dist, 1)
                    if _reduction < lot_size:  # can't even fit 1 lot
                        return self._deny(
                            f"VaR limit: {_var_check['reason']}",
                            open_positions, symbol
                        )
                    # Reduce to VaR-safe quantity
                    requested_quantity = int((_reduction // lot_size) * lot_size)
                    logger.info("VaR size reduction: %s → %d units (%s)",
                                symbol, requested_quantity, _var_check["reason"])
            except Exception as _ve:
                logger.debug("VaR check skipped: %s", _ve)


        if daily_loss_limit is not None and current_daily_pnl <= -abs(float(daily_loss_limit)):
            return self._deny("Daily loss limit already breached", open_positions, symbol)

        if correlation_group:
            corr_count = self._count_correlated_positions(open_positions, correlation_group)
            if corr_count >= self.max_correlated_positions:
                return self._deny(
                    f"Max correlated positions reached for group {correlation_group}",
                    open_positions, symbol,
                    metadata={"correlation_group": correlation_group},
                )

        # ---- Options premium cap (before quantity reduction loop) ----
        if is_options and self.max_premium_per_trade_pct > 0:
            max_premium_budget = self.capital * self.max_premium_per_trade_pct
            requested_cost     = float(entry_price) * int(requested_quantity)
            if requested_cost > max_premium_budget:
                # Can we reduce to fit?
                max_feasible_qty = int(max_premium_budget / max(float(entry_price), 1e-9))
                max_feasible_qty = (max_feasible_qty // max(1, lot_size)) * lot_size
                if max_feasible_qty <= 0:
                    return self._deny(
                        f"Premium cost ₹{requested_cost:.0f} exceeds "
                        f"max_premium_per_trade budget ₹{max_premium_budget:.0f}",
                        open_positions, symbol,
                        metadata={
                            "requested_premium_cost": round(requested_cost, 2),
                            "max_premium_budget":     round(max_premium_budget, 2),
                        },
                    )
                # Silently cap to the budget — the quantity loop below
                # may reduce further based on portfolio risk limits
                requested_quantity = max_feasible_qty

        # ---- Exposure + risk reduction loop -------------------------
        approved_qty = self._approved_quantity_by_exposure_and_risk(
            symbol=symbol,
            entry_price=float(entry_price),
            stop_loss=stop_loss,
            requested_quantity=int(requested_quantity),
            open_positions=open_positions,
            lot_size=lot_size,
            is_options=is_options,
        )

        if approved_qty <= 0:
            return self._deny(
                "Trade blocked by portfolio risk or exposure limits",
                open_positions, symbol,
            )

        approved_lots  = max(0, approved_qty // max(1, lot_size))
        est_trade_risk = self._estimate_trade_risk(
            entry_price, stop_loss, approved_qty, is_options=is_options
        )

        cur_total_exp  = self._current_total_exposure(open_positions)
        cur_sym_exp    = self._current_symbol_exposure(open_positions, symbol)
        cur_risk_amt   = self._current_portfolio_risk_amount(open_positions, is_options=is_options)

        res_total_exp  = cur_total_exp + float(entry_price) * approved_qty
        res_sym_exp    = cur_sym_exp   + float(entry_price) * approved_qty
        res_risk_pct   = (cur_risk_amt + est_trade_risk) / self.capital

        reduced = approved_qty < int(requested_quantity)
        reason  = "Approved" if not reduced else "Approved with reduced quantity due to risk limits"

        # Notional value (informational)
        notional_value: Optional[float] = None
        if spot_price and spot_price > 0:
            notional_value = float(spot_price) * approved_lots * lot_size

        return RiskDecision(
            allowed                     = True,
            approved_quantity           = approved_qty,
            approved_lots               = approved_lots,
            reason                      = reason,
            estimated_trade_risk        = round(est_trade_risk, 2),
            resulting_total_exposure    = round(res_total_exp,  2),
            resulting_symbol_exposure   = round(res_sym_exp,    2),
            resulting_portfolio_risk_pct= round(res_risk_pct,   6),
            metadata={
                "requested_quantity":          int(requested_quantity),
                "lot_size":                    lot_size,
                "is_options":                  is_options,
                "premium_cost":                round(float(entry_price) * approved_qty, 2),
                "notional_value":              round(notional_value, 2) if notional_value else None,
                "current_total_exposure":      round(cur_total_exp,  2),
                "current_symbol_exposure":     round(cur_sym_exp,    2),
                "current_portfolio_risk_amount": round(cur_risk_amt, 2),
                "correlation_group":           correlation_group,
            },
        )

    # ------------------------------------------------------------------
    # Convenience deny builder
    # ------------------------------------------------------------------
    def _deny(
        self,
        reason: str,
        open_positions: List[Dict[str, Any]],
        symbol: str,
        metadata: Optional[Dict[str, Any]] = None,
        is_options: bool = False,
    ) -> RiskDecision:
        return RiskDecision(
            allowed                     = False,
            approved_quantity           = 0,
            approved_lots               = 0,
            reason                      = reason,
            estimated_trade_risk        = 0.0,
            resulting_total_exposure    = self._current_total_exposure(open_positions),
            resulting_symbol_exposure   = self._current_symbol_exposure(open_positions, symbol),
            resulting_portfolio_risk_pct= self._current_portfolio_risk_pct(open_positions),
            metadata                    = metadata,
        )

    # ------------------------------------------------------------------
    # Exposure / risk helpers
    # ------------------------------------------------------------------
    def _current_total_exposure(self, open_positions: List[Dict[str, Any]]) -> float:
        return sum(
            self._safe_float(p.get("entry_price")) * self._safe_int(p.get("qty"))
            for p in open_positions
        )

    def _current_symbol_exposure(
        self, open_positions: List[Dict[str, Any]], symbol: str
    ) -> float:
        return sum(
            self._safe_float(p.get("entry_price")) * self._safe_int(p.get("qty"))
            for p in open_positions
            if str(p.get("symbol")) == str(symbol)
        )

    def _current_portfolio_risk_amount(
        self,
        open_positions: List[Dict[str, Any]],
        is_options: bool = False,
    ) -> float:
        return sum(
            self._estimate_trade_risk(
                self._safe_float(p.get("entry_price")),
                p.get("stop_loss"),
                self._safe_int(p.get("qty")),
                is_options=bool(p.get("is_options", is_options)),
            )
            for p in open_positions
        )

    def _current_portfolio_risk_pct(self, open_positions: List[Dict[str, Any]]) -> float:
        if self.capital <= 0:
            return 0.0
        return self._current_portfolio_risk_amount(open_positions) / self.capital

    def _estimate_trade_risk(
        self,
        entry_price: float,
        stop_loss: Optional[float],
        quantity: int,
        is_options: bool = False,
    ) -> float:
        """
        Estimate the monetary risk of a trade.

        Equity/Futures
        --------------
        - With stop: abs(entry - stop) × qty
        - No stop: entry × 0.05 × qty (5% adverse move)
        - Zero-distance stop: entry × 0.02 × qty

        Options (is_options=True)
        -------------------------
        - With stop (premium level): abs(entry - stop) × qty
        - No stop: entry × qty × fallback_option_loss_fraction
          (default 1.0 = assume full premium can be lost)
        - Zero-distance stop: same full-premium fallback
        """
        entry = self._safe_float(entry_price)
        qty   = self._safe_int(quantity)

        if entry <= 0 or qty <= 0:
            return 0.0

        if stop_loss is None:
            if is_options:
                # Max loss for bought option = full premium paid
                return entry * qty * self.fallback_option_loss_fraction
            else:
                return entry * 0.05 * qty

        stop = self._safe_float(stop_loss)
        dist = abs(entry - stop)

        if dist <= 0:
            if is_options:
                return entry * qty * self.fallback_option_loss_fraction
            else:
                return entry * 0.02 * qty

        return dist * qty

    def _count_correlated_positions(
        self,
        open_positions: List[Dict[str, Any]],
        correlation_group: str,
    ) -> int:
        target = str(correlation_group).upper()
        return sum(
            1 for p in open_positions
            if str(p.get("correlation_group", "")).upper() == target
        )

    def _approved_quantity_by_exposure_and_risk(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: Optional[float],
        requested_quantity: int,
        open_positions: List[Dict[str, Any]],
        lot_size: int,
        is_options: bool = False,
    ) -> int:
        """
        Reduce requested_quantity in lot-size steps until all three
        limits pass:
          1. max_total_exposure_pct
          2. max_symbol_exposure_pct
          3. max_portfolio_risk_pct
        """
        lot = max(1, int(lot_size))
        qty = (int(requested_quantity) // lot) * lot  # round to whole lots
        if qty <= 0:
            return 0

        cur_total_exp  = self._current_total_exposure(open_positions)
        cur_sym_exp    = self._current_symbol_exposure(open_positions, symbol)
        cur_risk_amt   = self._current_portfolio_risk_amount(open_positions, is_options=is_options)

        max_total_exp  = self.capital * self.max_total_exposure_pct
        max_sym_exp    = self.capital * self.max_symbol_exposure_pct
        max_risk_amt   = self.capital * self.max_portfolio_risk_pct

        while qty > 0:
            new_exp     = entry_price * qty
            new_risk    = self._estimate_trade_risk(entry_price, stop_loss, qty, is_options)

            if cur_total_exp + new_exp > max_total_exp:
                qty -= lot
                continue
            if cur_sym_exp + new_exp > max_sym_exp:
                qty -= lot
                continue
            if cur_risk_amt + new_risk > max_risk_amt:
                qty -= lot
                continue

            return qty

        return 0

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return default if value is None else float(value)
        except Exception:
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return default if value is None else int(value)
        except Exception:
            return default


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mgr = PortfolioRiskManager(
        capital                    = 100_000,
        max_open_positions         = 2,
        max_portfolio_risk_pct     = 0.03,
        max_symbol_exposure_pct    = 0.25,
        max_total_exposure_pct     = 1.00,
        max_correlated_positions   = 1,
        max_premium_per_trade_pct  = 0.10,
    )

    existing = [
        {
            "symbol":            "NIFTY26MAR22000CE",
            "entry_price":       210.0,
            "qty":               50,
            "stop_loss":         105.0,   # 50% stop on premium
            "correlation_group": "NIFTY",
            "is_options":        True,
        }
    ]

    # Should be blocked (max_correlated_positions=1, NIFTY group already has 1)
    result = mgr.evaluate_new_trade(
        symbol            = "NIFTY26MAR22100CE",
        entry_price       = 180.0,
        stop_loss         = 90.0,
        requested_quantity= 100,
        open_positions    = existing,
        correlation_group = "NIFTY",
        current_daily_pnl = -500.0,
        daily_loss_limit  = 3000.0,
        lot_size          = 50,
        is_options        = True,
        spot_price        = 22100.0,
    )
    print("Test 1 (should be BLOCKED - correlated):", result.to_dict())
    print()

    # Should pass (different correlation group)
    result2 = mgr.evaluate_new_trade(
        symbol            = "BANKNIFTY26MAR48000CE",
        entry_price       = 150.0,
        stop_loss         = None,   # no stop — uses full-premium fallback
        requested_quantity= 50,
        open_positions    = existing,
        correlation_group = "BANKNIFTY",
        lot_size          = 15,
        is_options        = True,
        spot_price        = 48000.0,
    )
    print("Test 2 (should be ALLOWED):", result2.to_dict())
    print()

    # Test premium cap: 200 qty × ₹180 = ₹36,000 > 10% of ₹1,00,000 = ₹10,000
    result3 = mgr.evaluate_new_trade(
        symbol            = "NIFTY26MAR22200CE",
        entry_price       = 180.0,
        stop_loss         = 90.0,
        requested_quantity= 200,
        open_positions    = [],
        lot_size          = 50,
        is_options        = True,
    )
    print("Test 3 (qty reduced to fit 10% premium cap):", result3.to_dict())
