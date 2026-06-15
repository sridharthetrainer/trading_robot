"""
advisory_engine.gateway — LAYER 4
=================================
Risk governance + execution state machine.

SAFETY DEFAULTS (project rule: capital preservation first, PAPER mode):
  - ExecutionMode.PAPER is the default. Fills are simulated; no broker call
    that places/modifies/cancels an order is ever made in PAPER mode.
  - LIVE mode requires BOTH ExecutionMode.LIVE *and* a broker object — and even
    then it rides the existing AngelOne client, which has its own
    block_real_orders guard.
  - If account equity cannot be determined, the trade is REJECTED for the
    cycle — never sized off a guessed number (mirrors pending improvement #2).

Risk rules implemented:
  - Gap control:   BUY: LTP > entry_max  -> cancel.
                   LTP < entry_min -> LIMIT pegged at entry_max.
                   in-zone         -> LIMIT at LTP. (mirrored for SELL)
  - Fixed-fractional sizing:  qty = equity * risk_pct / |entry_ref - stop_loss|
  - IV crush filter (options): live BS implied vol vs 30D realised vol;
    IV > 1.5x HV30 -> reject. Uncomputable IV -> reject (fail-safe).
  - OCO bracket: on entry fill, SL-market + limit target are placed and linked;
    one filling cancels the other.
"""

from __future__ import annotations

import itertools
import logging
import math
from datetime import datetime
from typing import Optional, Dict

import numpy as np
import pandas as pd

from .models import (Action, AdvisorySignal, ExecutionMode, ManagedPosition,
                     MarketSnapshot, OrderPlan, OrderState)

logger = logging.getLogger("advisory.gateway")

RISK_PCT = 0.01                 # 1% max capital risk per trade
IV_HV_MULT = 1.5                # reject if live IV > 1.5x HV30
RISK_FREE_RATE = 0.065          # annualised, for BS inversion


# ── Volatility helpers ─────────────────────────────────────────────────────────

def historical_vol_30d(daily_close: pd.Series) -> Optional[float]:
    """Annualised 30-session realised volatility from daily closes."""
    if daily_close is None or len(daily_close) < 31:
        return None
    rets = np.log(daily_close.iloc[-31:] / daily_close.iloc[-31:].shift(1)).dropna()
    if len(rets) < 20:
        return None
    return float(rets.std(ddof=1) * math.sqrt(252))


def _bs_price(spot: float, strike: float, t: float, vol: float,
              is_call: bool, r: float = RISK_FREE_RATE) -> float:
    if t <= 0 or vol <= 0:
        intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
        return intrinsic
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if is_call:
        return spot * N(d1) - strike * math.exp(-r * t) * N(d2)
    return strike * math.exp(-r * t) * N(-d2) - spot * N(-d1)


def implied_vol(option_price: float, spot: float, strike: float,
                days_to_expiry: float, is_call: bool) -> Optional[float]:
    """Bisection BS inversion; None when the premium is outside no-arb bounds."""
    if min(option_price, spot, strike, days_to_expiry) <= 0:
        return None
    t = days_to_expiry / 365.0
    lo, hi = 1e-4, 5.0
    if not (_bs_price(spot, strike, t, lo, is_call) <= option_price
            <= _bs_price(spot, strike, t, hi, is_call)):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if _bs_price(spot, strike, t, mid, is_call) < option_price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ── Risk governor ──────────────────────────────────────────────────────────────

class RiskGovernor:
    """
    Converts a validated signal into an OrderPlan, or rejects it.

    equity_provider: () -> float | None. Wire AngelOne.get_balance here for
    live rmsLimit() equity. None / failure => trade rejected this cycle.
    lot_size_provider: (symbol) -> int, for derivative contracts. Defaults to 1
    (equity); derivative trades without a real lot size are rejected.
    days_to_expiry_provider: (signal) -> float, for the IV filter.
    """

    def __init__(self, equity_provider, lot_size_provider=None,
                 days_to_expiry_provider=None,
                 risk_pct: float = RISK_PCT,
                 max_position_pct: float = 0.25):
        self.equity_provider = equity_provider
        self.lot_size_provider = lot_size_provider
        self.days_to_expiry_provider = days_to_expiry_provider
        self.risk_pct = risk_pct
        self.max_position_pct = max_position_pct

    def build_plan(self, sig: AdvisorySignal,
                   snap: MarketSnapshot) -> OrderPlan:
        plan = OrderPlan(signal=sig)

        def reject(reason: str) -> OrderPlan:
            plan.rejection_reason = reason
            logger.warning("L4 ✖ REJECTED %s: %s", sig.resolved_symbol, reason)
            return plan

        ltp = snap.ltp
        if ltp is None or ltp <= 0:
            return reject("no live LTP for tradable instrument — cannot price entry")

        # -- Slippage & gap control -------------------------------------------
        if sig.action is Action.BUY:
            if ltp > sig.entry_max:
                return reject(f"gap control: LTP {ltp:.2f} > entry_max "
                              f"{sig.entry_max:.2f} — refusing to chase")
            if ltp < sig.entry_min:
                plan.order_type, plan.limit_price = "LIMIT", sig.entry_max
                logger.info("L4 gap control: LTP %.2f below zone — LIMIT pegged "
                            "at entry_max %.2f", ltp, sig.entry_max)
            else:
                plan.order_type, plan.limit_price = "LIMIT", ltp
        else:  # SELL: mirror image
            if ltp < sig.entry_min:
                return reject(f"gap control: LTP {ltp:.2f} < entry_min "
                              f"{sig.entry_min:.2f} — refusing to chase down-gap")
            if ltp > sig.entry_max:
                plan.order_type, plan.limit_price = "LIMIT", sig.entry_min
            else:
                plan.order_type, plan.limit_price = "LIMIT", ltp

        # -- Equity (live only — no guessed fallback) ---------------------------
        try:
            equity = self.equity_provider()
        except Exception as e:
            equity = None
            logger.error("L4 equity fetch raised: %s", e)
        if not equity or equity <= 0:
            return reject("account equity unavailable — skipping cycle "
                          "(fail-safe: never size off a guessed balance)")

        # -- Fixed fractional sizing -------------------------------------------
        entry_ref = sig.entry_max if sig.action is Action.BUY else sig.entry_min
        risk_per_unit = abs(entry_ref - sig.stop_loss)
        if risk_per_unit <= 0:
            return reject("stop_loss equals entry — risk per unit is zero")
        raw_qty = (equity * self.risk_pct) / risk_per_unit

        lot = 1
        if sig.is_derivative:
            if self.lot_size_provider is None:
                return reject("derivative trade but no lot_size_provider wired")
            try:
                lot = int(self.lot_size_provider(sig.resolved_symbol))
            except Exception as e:
                return reject(f"lot size lookup failed: {e}")
            if lot <= 0:
                return reject(f"invalid lot size {lot}")

        lots = int(raw_qty // lot)
        qty = lots * lot
        if qty <= 0:
            return reject(f"1% risk budget (₹{equity * self.risk_pct:,.0f}) too "
                          f"small for risk/unit ₹{risk_per_unit:.2f} x lot {lot}")

        # exposure cap so a tight stop can't balloon notional
        notional = qty * plan.limit_price
        max_notional = equity * self.max_position_pct
        if notional > max_notional:
            qty = int(max_notional / plan.limit_price) // lot * lot
            lots = qty // lot
            if qty <= 0:
                return reject(f"notional cap {self.max_position_pct:.0%} of equity "
                              "leaves no room at this price")
            logger.info("L4 notional capped: qty reduced to %d", qty)

        plan.quantity, plan.lots, plan.lot_size = qty, lots, lot
        plan.risk_per_unit = risk_per_unit
        plan.capital_at_risk = qty * risk_per_unit

        # -- IV crush filter (options only) -------------------------------------
        if sig.is_derivative:
            hv = historical_vol_30d(snap.daily["close"]) if snap.has_frame("daily") else None
            dte = None
            if self.days_to_expiry_provider is not None:
                try:
                    dte = self.days_to_expiry_provider(sig)
                except Exception as e:
                    logger.warning("L4 DTE provider failed: %s", e)
            iv = (implied_vol(ltp, snap.underlying_ltp, sig.strike, dte,
                              sig.segment.value == "DERIVATIVE_CALL")
                  if None not in (snap.underlying_ltp, sig.strike, dte) else None)
            if hv is None or iv is None:
                return reject(f"IV filter unverifiable (iv={iv}, hv30={hv}) — "
                              "rejecting option entry (fail-safe)")
            if iv > hv * IV_HV_MULT:
                return reject(f"IV crush risk: live IV {iv:.1%} > "
                              f"{IV_HV_MULT}x HV30 {hv:.1%}")
            logger.info("L4 IV filter ok: IV %.1f%% vs HV30 %.1f%%",
                        iv * 100, hv * 100)

        logger.info(
            "L4 ✓ plan %s %s x%d (%d lot(s) of %d) %s @ %.2f | risk/unit %.2f | "
            "capital at risk ₹%s (%.2f%% of ₹%s)",
            sig.action.value, sig.resolved_symbol, qty, lots, lot,
            plan.order_type, plan.limit_price, risk_per_unit,
            f"{plan.capital_at_risk:,.0f}",
            100 * plan.capital_at_risk / equity, f"{equity:,.0f}")
        return plan


# ── Execution state machine (paper default, OCO bracket) ──────────────────────

class ExecutionGateway:
    """
    Drives one ManagedPosition per accepted plan through:
      PENDING -> ENTRY_PLACED -> ENTRY_FILLED -> EXITS_PLACED
              -> CLOSED_TARGET | CLOSED_STOP   (software OCO)

    PAPER mode simulates instant fills at the limit price and resolves the
    bracket from subsequent LTPs supplied to poll(). LIVE mode delegates to an
    AngelOne-compatible client: place_order / place_sl_order / cancel_order /
    get_order_status.
    """

    _ids = itertools.count(1)

    def __init__(self, mode: ExecutionMode = ExecutionMode.PAPER, broker=None):
        self.mode = mode
        self.broker = broker
        if mode is ExecutionMode.LIVE and broker is None:
            logger.warning("LIVE mode requested without broker — forcing PAPER")
            self.mode = ExecutionMode.PAPER
        self.positions: Dict[str, ManagedPosition] = {}
        logger.info("L4 ExecutionGateway up in %s mode", self.mode.value)

    # -- entry -------------------------------------------------------------------

    def submit(self, plan: OrderPlan) -> ManagedPosition:
        pos = ManagedPosition(plan=plan)
        key = f"ADV-{next(self._ids)}-{plan.signal.resolved_symbol}"
        self.positions[key] = pos

        if not plan.accepted:
            pos.transition(OrderState.REJECTED, plan.rejection_reason or "rejected")
            return pos

        sig = plan.signal
        if self.mode is ExecutionMode.PAPER:
            pos.entry_order_id = f"PAPER-ENTRY-{key}"
            pos.transition(OrderState.ENTRY_PLACED,
                           f"paper {plan.order_type} @ {plan.limit_price:.2f}")
            # paper assumption: a marketable limit fills at the limit price
            pos.fill_price = plan.limit_price
            pos.transition(OrderState.ENTRY_FILLED,
                           f"paper fill @ {pos.fill_price:.2f}")
            self._place_bracket(key, pos)
        else:
            try:
                resp = self.broker.place_order(
                    symbol=sig.resolved_symbol, qty=plan.quantity,
                    direction=sig.action.value, order_type=plan.order_type,
                    price=plan.limit_price)
                pos.entry_order_id = (resp or {}).get("order_id")
                if not pos.entry_order_id:
                    pos.transition(OrderState.REJECTED, f"broker rejected entry: {resp}")
                    return pos
                pos.transition(OrderState.ENTRY_PLACED,
                               f"live order {pos.entry_order_id}")
            except Exception as e:
                pos.transition(OrderState.REJECTED, f"entry placement error: {e}")
        logger.info("L4 %s: %s", key, pos.state.value)
        return pos

    # -- OCO bracket --------------------------------------------------------------

    def _place_bracket(self, key: str, pos: ManagedPosition) -> None:
        """Contingent SL-market + limit target, linked one-cancels-other."""
        sig = pos.plan.signal
        if self.mode is ExecutionMode.PAPER:
            pos.sl_order_id = f"PAPER-SL-{key}"
            pos.target_order_id = f"PAPER-TGT-{key}"
        else:
            exit_side = "SELL" if sig.action is Action.BUY else "BUY"
            try:
                sl = self.broker.place_sl_order(
                    symbol=sig.resolved_symbol, qty=pos.plan.quantity,
                    direction=exit_side, trigger_price=sig.stop_loss)
                tgt = self.broker.place_order(
                    symbol=sig.resolved_symbol, qty=pos.plan.quantity,
                    direction=exit_side, order_type="LIMIT", price=sig.target)
                pos.sl_order_id = (sl or {}).get("order_id")
                pos.target_order_id = (tgt or {}).get("order_id")
            except Exception as e:
                logger.error("L4 bracket placement failed for %s: %s — "
                             "POSITION IS UNPROTECTED, manual action needed",
                             key, e)
                pos.transition(OrderState.ENTRY_FILLED, f"bracket error: {e}")
                return
        pos.transition(
            OrderState.EXITS_PLACED,
            f"OCO bracket: SL-MKT @ {sig.stop_loss:.2f} <-> "
            f"TGT-LIMIT @ {sig.target:.2f}")
        logger.info("L4 %s: OCO armed (sl=%s, tgt=%s)",
                    key, pos.sl_order_id, pos.target_order_id)

    # -- lifecycle polling ----------------------------------------------------------

    def poll(self, key: str, ltp: Optional[float] = None) -> ManagedPosition:
        """
        Advance one position. PAPER: resolve bracket against the supplied LTP.
        LIVE: read order books; on entry fill place bracket; on one exit filling,
        cancel the sibling (software OCO).
        """
        pos = self.positions[key]
        sig = pos.plan.signal
        if pos.state not in (OrderState.ENTRY_PLACED, OrderState.EXITS_PLACED):
            return pos

        if self.mode is ExecutionMode.PAPER:
            if pos.state is OrderState.EXITS_PLACED and ltp is not None:
                long_exposure = sig.action is Action.BUY
                hit_sl = ltp <= sig.stop_loss if long_exposure else ltp >= sig.stop_loss
                hit_tgt = ltp >= sig.target if long_exposure else ltp <= sig.target
                if hit_sl:                       # SL checked first: conservative
                    pos.exit_price = sig.stop_loss
                    pos.transition(OrderState.CLOSED_STOP,
                                   f"paper SL hit at LTP {ltp:.2f}; target cancelled (OCO)")
                elif hit_tgt:
                    pos.exit_price = sig.target
                    pos.transition(OrderState.CLOSED_TARGET,
                                   f"paper target hit at LTP {ltp:.2f}; SL cancelled (OCO)")
            return pos

        # LIVE
        try:
            if pos.state is OrderState.ENTRY_PLACED:
                st = self.broker.get_order_status(pos.entry_order_id) or {}
                if str(st.get("status", "")).lower() in ("complete", "filled"):
                    pos.fill_price = float(st.get("averageprice") or
                                           pos.plan.limit_price)
                    pos.transition(OrderState.ENTRY_FILLED,
                                   f"live fill @ {pos.fill_price:.2f}")
                    self._place_bracket(key, pos)
            elif pos.state is OrderState.EXITS_PLACED:
                sl_st = self.broker.get_order_status(pos.sl_order_id) or {}
                tg_st = self.broker.get_order_status(pos.target_order_id) or {}
                if str(sl_st.get("status", "")).lower() in ("complete", "filled"):
                    self.broker.cancel_order(pos.target_order_id)
                    pos.exit_price = float(sl_st.get("averageprice") or sig.stop_loss)
                    pos.transition(OrderState.CLOSED_STOP, "live SL filled; OCO cancelled target")
                elif str(tg_st.get("status", "")).lower() in ("complete", "filled"):
                    self.broker.cancel_order(pos.sl_order_id)
                    pos.exit_price = float(tg_st.get("averageprice") or sig.target)
                    pos.transition(OrderState.CLOSED_TARGET, "live target filled; OCO cancelled SL")
        except Exception as e:
            logger.error("L4 poll %s error: %s", key, e)
        return pos
