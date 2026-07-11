"""
spread_strategy.py

Options selling strategies: Bull Put Spread, Bear Call Spread, Iron Condor.

These strategies sell options premium rather than buy it. Time decay (theta)
works FOR the position. Maximum loss is defined and capped.

Capital requirements:
    Bull Put Spread (100pt): ~₹35,000 margin per lot
    Iron Condor (200pt):     ~₹65,000 margin per lot
    These are MARGIN requirements (blocked, not spent) at Angel One.

Usage in live_signal_engine:
    from spread_strategy import SpreadStrategy
    engine = SpreadStrategy(broker_manager=bm, trade_manager=tm, alerts=alerts)
    result = engine.enter_bull_put_spread("NIFTY", spot=22000, capital=500000)
    result = engine.enter_iron_condor("NIFTY", spot=22000, capital=500000)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Persistent store for one-tap spreads (2026-07-12): the legacy class below
# keeps positions in memory only, which loses them on restart. The one-tap
# Telegram path runs in transient handler calls, so open spreads live here.
OPEN_SPREADS_FILE = Path("open_spreads.json")

# ── Capital requirements ─────────────────────────────────────────────────────
BULL_PUT_SPREAD_MARGIN_PER_LOT  = 35_000   # 100pt width
IRON_CONDOR_MARGIN_PER_LOT      = 65_000
MIN_CAPITAL_FOR_SPREADS         = 1_50_000  # ₹1.5 lakh minimum
MIN_CAPITAL_FOR_IRON_CONDOR     = 5_00_000  # ₹5 lakh minimum
MIN_CAPITAL_FOR_STRADDLE        = 10_00_000 # ₹10 lakh minimum
LOT_SIZE_NIFTY                  = 75
LOT_SIZE_BANKNIFTY              = 30
LOT_SIZE_FINNIFTY               = 65

LOT_SIZES = {
    "NIFTY":     LOT_SIZE_NIFTY,
    "BANKNIFTY": LOT_SIZE_BANKNIFTY,
    "FINNIFTY":  LOT_SIZE_FINNIFTY,
    "MIDCPNIFTY": 75,
}


@dataclass
class SpreadLeg:
    """One leg of a multi-leg option spread."""
    symbol:      str
    strike:      int
    option_type: str        # "CE" or "PE"
    action:      str        # "BUY" or "SELL"
    expiry:      str        # DDMMMYY format e.g. "27MAR25"
    qty:         int
    order_id:    str  = ""
    fill_price:  float = 0.0
    status:      str  = "PENDING"


@dataclass
class SpreadPosition:
    """Complete multi-leg spread position."""
    spread_id:    str
    spread_type:  str          # "bull_put_spread" | "bear_call_spread" | "iron_condor"
    underlying:   str
    legs:         List[SpreadLeg] = field(default_factory=list)
    net_credit:   float = 0.0
    max_profit:   float = 0.0
    max_loss:     float = 0.0
    status:       str   = "PENDING"  # PENDING | OPEN | CLOSED | PARTIAL
    entry_time:   float = 0.0
    exit_time:    float = 0.0
    realized_pnl: float = 0.0
    metadata:     Dict  = field(default_factory=dict)


class SpreadStrategy:
    """
    Manages entry and exit of options selling spread strategies.

    Spreads are multi-leg: two or four simultaneous orders must all fill
    for the position to be valid. Uses Angel One's REST API sequentially
    (no native multi-leg support in retail API).
    """

    def __init__(
        self,
        broker_manager = None,
        trade_manager  = None,
        alerts         = None,
        lot_sizes:     Optional[Dict[str, int]] = None,
    ) -> None:
        self.broker_manager = broker_manager
        self.trade_manager  = trade_manager
        self.alerts         = alerts
        self.lot_sizes      = lot_sizes or LOT_SIZES
        self._open_spreads: Dict[str, SpreadPosition] = {}

    def _lot_size(self, underlying: str) -> int:
        return self.lot_sizes.get(underlying.upper(), 75)

    def _get_option_symbol(
        self, underlying: str, strike: int, option_type: str, expiry: str
    ) -> str:
        """Build Angel One option symbol string e.g. NIFTY27MAR2522000CE"""
        return f"{underlying}{expiry}{strike}{option_type}"

    def _max_lots_for_capital(
        self, capital: float, margin_per_lot: float
    ) -> int:
        """How many lots can we run given available capital and margin needs."""
        # Keep 2× margin as safety buffer
        usable = capital * 0.40     # never deploy more than 40% as margin
        return max(1, int(usable / margin_per_lot))

    def _is_capital_sufficient(
        self, capital: float, spread_type: str
    ) -> Tuple[bool, str]:
        """Check if capital meets minimum for spread type."""
        if spread_type in ("bull_put_spread", "bear_call_spread"):
            if capital < MIN_CAPITAL_FOR_SPREADS:
                return False, f"Need ₹{MIN_CAPITAL_FOR_SPREADS:,} for spreads (have ₹{capital:,.0f})"
        elif spread_type == "iron_condor":
            if capital < MIN_CAPITAL_FOR_IRON_CONDOR:
                return False, f"Need ₹{MIN_CAPITAL_FOR_IRON_CONDOR:,} for iron condor (have ₹{capital:,.0f})"
        elif spread_type in ("theta_straddle", "iron_fly"):
            if capital < MIN_CAPITAL_FOR_STRADDLE:
                return False, f"Need ₹{MIN_CAPITAL_FOR_STRADDLE:,} for straddle (have ₹{capital:,.0f})"
        return True, "OK"

    def _place_leg(
        self, leg: SpreadLeg, exchange: str = "NFO"
    ) -> bool:
        """Place a single option leg order. Returns True if fill confirmed."""
        if not self.broker_manager:
            leg.status   = "SIM"
            leg.fill_price = 100.0  # paper price
            leg.order_id  = f"SIM_{int(time.time())}"
            return True
        try:
            broker = self.broker_manager.get_execution_broker()
            if not broker:
                return False
            result = broker.place_order(
                symbol    = leg.symbol,
                qty       = leg.qty,
                buy_sell  = leg.action,
                order_type = "MARKET",
                price      = 0,
                exchange   = exchange,
            )
            if not result:
                logger.error("Leg order failed: %s %s %s", leg.action, leg.qty, leg.symbol)
                return False
            order_id = result[0] if isinstance(result, tuple) else str(result)
            leg.order_id = order_id
            # Poll fill
            fill = broker.poll_order_fill(order_id, timeout_sec=10)
            if fill:
                leg.fill_price = float(fill.get("averageprice") or 0)
                leg.status     = "FILLED"
                return True
            else:
                logger.warning("Leg fill not confirmed: %s", leg.symbol)
                leg.status = "PENDING"
                return False
        except Exception as exc:
            logger.exception("_place_leg error: %s", exc)
            return False

    def enter_bull_put_spread(
        self,
        underlying:  str,
        spot:        float,
        expiry:      str,
        capital:     float,
        width_pts:   int   = 100,
        lots:        int   = 1,
    ) -> Optional[SpreadPosition]:
        """
        Bull Put Spread: sell higher strike PE, buy lower strike PE.
        Profit when price stays above short strike.

        Example at NIFTY 22000:
            SELL 22000 PE (premium ~₹120)
            BUY  21900 PE (premium ~₹65)
            Net credit: ₹55 per unit. Max profit: ₹55 × 75 = ₹4,125.
        """
        ok, msg = self._is_capital_sufficient(capital, "bull_put_spread")
        if not ok:
            logger.warning("Bull Put Spread rejected: %s", msg)
            return None

        max_lots   = self._max_lots_for_capital(capital, BULL_PUT_SPREAD_MARGIN_PER_LOT)
        actual_lots = min(lots, max_lots)
        lot_size   = self._lot_size(underlying)
        qty        = actual_lots * lot_size

        # Round strikes to nearest 50
        short_strike = int(round(spot / 50) * 50)
        long_strike  = short_strike - width_pts

        legs = [
            SpreadLeg(
                symbol      = self._get_option_symbol(underlying, short_strike, "PE", expiry),
                strike      = short_strike,
                option_type = "PE",
                action      = "SELL",
                expiry      = expiry,
                qty         = qty,
            ),
            SpreadLeg(
                symbol      = self._get_option_symbol(underlying, long_strike, "PE", expiry),
                strike      = long_strike,
                option_type = "PE",
                action      = "BUY",
                expiry      = expiry,
                qty         = qty,
            ),
        ]

        spread_id = f"BPS_{underlying}_{int(time.time())}"
        spread    = SpreadPosition(
            spread_id   = spread_id,
            spread_type = "bull_put_spread",
            underlying  = underlying,
            legs        = legs,
            entry_time  = time.time(),
            metadata    = {
                "spot":         spot,
                "short_strike": short_strike,
                "long_strike":  long_strike,
                "width":        width_pts,
                "lots":         actual_lots,
            },
        )

        # Place both legs sequentially
        for leg in legs:
            ok = self._place_leg(leg)
            if not ok:
                # Rollback: if short was filled, close it
                self._rollback_spread(spread)
                return None

        # Calculate spread P&L metrics
        short_fill = legs[0].fill_price
        long_fill  = legs[1].fill_price
        net_credit = (short_fill - long_fill) * qty
        max_profit = net_credit
        max_loss   = (width_pts - (short_fill - long_fill)) * qty

        spread.net_credit = round(net_credit, 2)
        spread.max_profit = round(max_profit, 2)
        spread.max_loss   = round(max_loss,   2)
        spread.status     = "OPEN"

        self._open_spreads[spread_id] = spread

        logger.info(
            "Bull Put Spread opened | %s | credit=₹%.0f | max_profit=₹%.0f | max_loss=₹%.0f",
            spread_id, net_credit, max_profit, max_loss,
        )

        if self.alerts:
            try:
                self.alerts.send(
                    f"📊 <b>BULL PUT SPREAD OPENED</b>\n"
                    f"Underlying: {underlying}\n"
                    f"Short: {short_strike}PE  Long: {long_strike}PE\n"
                    f"Net Credit: ₹{net_credit:,.0f}\n"
                    f"Max Profit: ₹{max_profit:,.0f}  Max Loss: ₹{max_loss:,.0f}"
                )
            except Exception:
                pass

        return spread

    def enter_iron_condor(
        self,
        underlying: str,
        spot:       float,
        expiry:     str,
        capital:    float,
        put_width:  int = 200,
        call_width: int = 200,
        lots:       int = 1,
    ) -> Optional[SpreadPosition]:
        """
        Iron Condor = Bull Put Spread + Bear Call Spread.
        Profit when price stays within the short strikes range.
        """
        ok, msg = self._is_capital_sufficient(capital, "iron_condor")
        if not ok:
            logger.warning("Iron Condor rejected: %s", msg)
            return None

        max_lots    = self._max_lots_for_capital(capital, IRON_CONDOR_MARGIN_PER_LOT)
        actual_lots = min(lots, max_lots)
        lot_size    = self._lot_size(underlying)
        qty         = actual_lots * lot_size

        atm = int(round(spot / 50) * 50)
        put_short  = atm
        put_long   = atm - put_width
        call_short = atm
        call_long  = atm + call_width

        legs = [
            SpreadLeg(symbol=self._get_option_symbol(underlying, put_short,  "PE", expiry),
                      strike=put_short,  option_type="PE", action="SELL", expiry=expiry, qty=qty),
            SpreadLeg(symbol=self._get_option_symbol(underlying, put_long,   "PE", expiry),
                      strike=put_long,   option_type="PE", action="BUY",  expiry=expiry, qty=qty),
            SpreadLeg(symbol=self._get_option_symbol(underlying, call_short, "CE", expiry),
                      strike=call_short, option_type="CE", action="SELL", expiry=expiry, qty=qty),
            SpreadLeg(symbol=self._get_option_symbol(underlying, call_long,  "CE", expiry),
                      strike=call_long,  option_type="CE", action="BUY",  expiry=expiry, qty=qty),
        ]

        spread_id = f"IC_{underlying}_{int(time.time())}"
        spread    = SpreadPosition(
            spread_id   = spread_id,
            spread_type = "iron_condor",
            underlying  = underlying,
            legs        = legs,
            entry_time  = time.time(),
            metadata    = {
                "spot": spot, "atm": atm,
                "put_range":  (put_long, put_short),
                "call_range": (call_short, call_long),
                "lots": actual_lots,
            },
        )

        for leg in legs:
            if not self._place_leg(leg):
                self._rollback_spread(spread)
                return None

        fills      = [l.fill_price for l in legs]
        # Net credit = sum of sold premiums - sum of bought premiums
        net_credit = (fills[0] - fills[1] + fills[2] - fills[3]) * qty
        max_profit = net_credit
        max_loss   = (put_width - (fills[0] - fills[1])) * qty   # worse side

        spread.net_credit = round(net_credit, 2)
        spread.max_profit = round(max_profit, 2)
        spread.max_loss   = round(max_loss,   2)
        spread.status     = "OPEN"
        self._open_spreads[spread_id] = spread

        logger.info(
            "Iron Condor opened | %s | range=[%d-%d] | credit=₹%.0f",
            spread_id, put_long, call_long, net_credit,
        )

        if self.alerts:
            try:
                self.alerts.send(
                    f"🔷 <b>IRON CONDOR OPENED</b>\n"
                    f"Underlying: {underlying}\n"
                    f"Range: {put_long}–{call_long}\n"
                    f"Net Credit: ₹{net_credit:,.0f}\n"
                    f"Max Profit: ₹{max_profit:,.0f}  Max Loss: ₹{max_loss:,.0f}"
                )
            except Exception:
                pass

        return spread

    def close_spread(self, spread_id: str, reason: str = "manual") -> float:
        """Close all legs of a spread. Returns realized P&L."""
        spread = self._open_spreads.get(spread_id)
        if not spread or spread.status != "OPEN":
            return 0.0

        total_exit = 0.0
        for leg in spread.legs:
            if leg.status != "FILLED":
                continue
            exit_action = "BUY" if leg.action == "SELL" else "SELL"
            close_leg = SpreadLeg(
                symbol      = leg.symbol,
                strike      = leg.strike,
                option_type = leg.option_type,
                action      = exit_action,
                expiry      = leg.expiry,
                qty         = leg.qty,
            )
            ok = self._place_leg(close_leg)
            if ok:
                if leg.action == "SELL":
                    total_exit -= close_leg.fill_price * leg.qty
                else:
                    total_exit += close_leg.fill_price * leg.qty

        pnl = spread.net_credit + total_exit
        spread.realized_pnl = round(pnl, 2)
        spread.status       = "CLOSED"
        spread.exit_time    = time.time()
        del self._open_spreads[spread_id]

        logger.info("Spread closed | %s | reason=%s | pnl=₹%.0f",
                    spread_id, reason, pnl)
        return pnl

    def _rollback_spread(self, spread: SpreadPosition) -> None:
        """Emergency close of any filled legs after partial failure."""
        for leg in spread.legs:
            if leg.status == "FILLED":
                try:
                    reverse = "BUY" if leg.action == "SELL" else "SELL"
                    rollback_leg = SpreadLeg(
                        symbol=leg.symbol, strike=leg.strike,
                        option_type=leg.option_type, action=reverse,
                        expiry=leg.expiry, qty=leg.qty,
                    )
                    self._place_leg(rollback_leg)
                    logger.warning("Rollback leg: %s %s", reverse, leg.symbol)
                except Exception as exc:
                    logger.error("Rollback FAILED for %s: %s", leg.symbol, exc)

    def get_open_spreads(self) -> List[Dict[str, Any]]:
        return [
            {
                "spread_id":   s.spread_id,
                "type":        s.spread_type,
                "underlying":  s.underlying,
                "net_credit":  s.net_credit,
                "max_profit":  s.max_profit,
                "max_loss":    s.max_loss,
                "status":      s.status,
                "entry_time":  s.entry_time,
                "legs":        len(s.legs),
            }
            for s in self._open_spreads.values()
        ]

    @staticmethod
    def capital_unlock_summary(capital: float) -> Dict[str, Any]:
        """Return which spread strategies are available at this capital level."""
        return {
            "bull_put_spread": capital >= MIN_CAPITAL_FOR_SPREADS,
            "bear_call_spread": capital >= MIN_CAPITAL_FOR_SPREADS,
            "iron_condor": capital >= MIN_CAPITAL_FOR_IRON_CONDOR,
            "theta_straddle": capital >= MIN_CAPITAL_FOR_STRADDLE,
            "min_for_spreads":      MIN_CAPITAL_FOR_SPREADS,
            "min_for_iron_condor":  MIN_CAPITAL_FOR_IRON_CONDOR,
            "min_for_straddle":     MIN_CAPITAL_FOR_STRADDLE,
        }


# ── One-tap defined-risk vertical (2026-07-12) ───────────────────────────────
# First real consumer of this module (it was fully built and never imported
# anywhere). These functions work with the AngelOne client directly (the
# legacy class expects a broker_manager the one-tap path doesn't have), place
# the LONG leg FIRST (a debit spread's short leg is margin-hedged only once
# the long leg exists — sell-first would demand full naked-short margin), and
# persist open spreads to disk so a restart doesn't orphan them.

def _load_spread_store() -> Dict[str, Any]:
    try:
        if OPEN_SPREADS_FILE.exists():
            return json.loads(OPEN_SPREADS_FILE.read_text())
    except Exception as exc:
        logger.debug("spread store load: %s", exc)
    return {"open": {}, "closed": []}


def _save_spread_store(store: Dict[str, Any]) -> None:
    try:
        store["closed"] = store.get("closed", [])[-50:]
        OPEN_SPREADS_FILE.write_text(json.dumps(store, indent=2, default=str))
    except Exception as exc:
        logger.warning("spread store save: %s", exc)


def enter_debit_vertical(
    angel,
    long_symbol: str,
    short_symbol: str,
    qty: int,
    width_pts: float,
    order_tag: str = "TGSPREAD",
) -> Dict[str, Any]:
    """BUY long_symbol, SELL short_symbol (same qty) as a defined-risk debit
    vertical. Returns {"ok", "error"?, "spread"?}. On a failed short leg the
    long leg is immediately sold back (rollback) so no naked position is left.
    """
    res_long = None
    try:
        res_long = angel.place_order(long_symbol, qty, "BUY", order_type="MARKET",
                                     producttype="CARRYFORWARD", exchange="NFO",
                                     order_tag=order_tag)
    except Exception as exc:
        return {"ok": False, "error": f"long leg error: {exc}"}
    if not res_long or not res_long[0]:
        return {"ok": False, "error": "long leg order failed"}
    long_id, long_fill = res_long[0], float(res_long[1] or 0)

    res_short = None
    try:
        res_short = angel.place_order(short_symbol, qty, "SELL", order_type="MARKET",
                                      producttype="CARRYFORWARD", exchange="NFO",
                                      order_tag=order_tag)
    except Exception as exc:
        res_short = None
        logger.error("short leg error: %s", exc)
    if not res_short or not res_short[0]:
        # Rollback: sell the long leg back so nothing naked remains.
        try:
            angel.place_order(long_symbol, qty, "SELL", order_type="MARKET",
                              producttype="CARRYFORWARD", exchange="NFO",
                              order_tag=order_tag)
            rolled = True
        except Exception as exc:
            rolled = False
            logger.error("ROLLBACK FAILED — naked long %s x%d remains: %s",
                         long_symbol, qty, exc)
        return {"ok": False,
                "error": ("short leg failed (likely margin); long leg "
                          + ("rolled back cleanly" if rolled else
                             "ROLLBACK FAILED — check broker app NOW"))}

    short_id, short_fill = res_short[0], float(res_short[1] or 0)
    debit = max(0.0, long_fill - short_fill)
    spread_id = f"TGV_{int(time.time())}"
    spread = {
        "spread_id": spread_id,
        "spread_type": "debit_vertical",
        "long": {"symbol": long_symbol, "order_id": str(long_id), "fill": long_fill},
        "short": {"symbol": short_symbol, "order_id": str(short_id), "fill": short_fill},
        "qty": qty,
        "width_pts": width_pts,
        "net_debit_unit": round(debit, 2),
        "max_loss": round(debit * qty, 2),
        "max_profit": round(max(0.0, width_pts - debit) * qty, 2),
        "entry_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "OPEN",
    }
    store = _load_spread_store()
    store.setdefault("open", {})[spread_id] = spread
    _save_spread_store(store)
    logger.info("Debit vertical opened %s: +%s / -%s x%d debit=%.2f",
                spread_id, long_symbol, short_symbol, qty, debit)
    return {"ok": True, "spread": spread}


def close_spread_by_id(angel, spread_id: str) -> Dict[str, Any]:
    """Market-close both legs of a persisted one-tap spread. Returns
    {"ok", "pnl"?, "error"?}. A leg that fails to close is reported and the
    spread stays OPEN (retryable) rather than being half-forgotten."""
    store = _load_spread_store()
    spread = (store.get("open") or {}).get(spread_id)
    if not spread:
        return {"ok": False, "error": f"unknown/closed spread {spread_id}"}
    qty = int(spread["qty"])
    try:
        res_l = angel.place_order(spread["long"]["symbol"], qty, "SELL",
                                  order_type="MARKET", producttype="CARRYFORWARD",
                                  exchange="NFO", order_tag="TGSPREAD")
        res_s = angel.place_order(spread["short"]["symbol"], qty, "BUY",
                                  order_type="MARKET", producttype="CARRYFORWARD",
                                  exchange="NFO", order_tag="TGSPREAD")
    except Exception as exc:
        return {"ok": False, "error": f"close error: {exc} — spread still OPEN"}
    if not res_l or not res_l[0] or not res_s or not res_s[0]:
        return {"ok": False, "error": "a close leg failed — spread still OPEN, retry"}
    exit_long, exit_short = float(res_l[1] or 0), float(res_s[1] or 0)
    pnl = ((exit_long - float(spread["long"]["fill"]))
           + (float(spread["short"]["fill"]) - exit_short)) * qty
    spread["status"] = "CLOSED"
    spread["exit_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    spread["realized_pnl"] = round(pnl, 2)
    store["open"].pop(spread_id, None)
    store.setdefault("closed", []).append(spread)
    _save_spread_store(store)
    logger.info("Spread %s closed pnl=%.0f", spread_id, pnl)
    return {"ok": True, "pnl": round(pnl, 2), "spread": spread}


def list_open_spreads() -> List[Dict[str, Any]]:
    return list((_load_spread_store().get("open") or {}).values())
