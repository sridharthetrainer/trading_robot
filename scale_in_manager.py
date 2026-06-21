"""
scale_in_manager.py

Institutional scale-in and runner management.

The single biggest difference between retail and institutional trading
is HOW they enter and manage positions.

RETAIL (what we currently do):
    Signal fires → enter full position → set stop → wait for target
    
INSTITUTIONAL (what we need):
    Signal fires → enter 50% → wait for pullback → add 33% → 
    add final 17% only if strong trend confirmed
    
    Then on exits:
    T1 hit → close 33%, trail stop to breakeven
    T2 hit → close another 33%, trail stop to T1
    Runner (33%) → hold until structure breaks or EOD

Why scale-in works better:
    1. If you're wrong, you only have 50% size at the worst price
    2. If you're right, you get a better average entry
    3. Runners let profitable trades run without arbitrary targets
    4. Reduces the "stopped out then price runs" frustration

ATR-Based Trailing for Runners
────────────────────────────────
After T2 is hit, the runner uses a trail that follows:
    BUY runner: trail = highest_price - 2.0 ATR
    SELL runner: trail = lowest_price + 2.0 ATR
Never tightens below breakeven once in profit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScaleEntry:
    """One tranche of a scaled entry."""
    tranche_id:    str    # "T1_50pct", "T2_33pct", "T3_17pct"
    entry_price:   float
    qty:           int
    entry_time:    float
    condition:     str    # "signal", "pullback", "confirmation"
    status:        str    = "PENDING"  # PENDING | FILLED | CANCELLED
    order_id:      str    = ""


@dataclass
class ScaledPosition:
    """Full scaled position with multiple tranches and runner tracking."""
    position_id:    str
    symbol:         str
    side:           str           # "BUY" or "SELL"
    strategy:       str
    signal_score:   float
    initial_qty:    int           # planned total size
    atr_at_entry:   float

    tranches:       List[ScaleEntry] = field(default_factory=list)

    # Targets
    stop_loss:      float = 0.0
    target1:        float = 0.0   # T1: close 33%, move stop to breakeven
    target2:        float = 0.0   # T2: close 33%, trail runner
    runner_trail:   float = 0.0   # runner trailing stop

    # Status
    is_runner_active: bool  = False
    breakeven_locked: bool  = False
    highest_price:    float = 0.0
    lowest_price:     float = 0.0
    total_filled_qty: int   = 0
    realized_pnl:     float = 0.0

    created_at:       float = field(default_factory=time.time)

    @property
    def avg_entry(self) -> float:
        filled = [t for t in self.tranches if t.status == "FILLED"]
        if not filled:
            return 0.0
        total_value = sum(t.entry_price * t.qty for t in filled)
        total_qty   = sum(t.qty for t in filled)
        return total_value / total_qty if total_qty > 0 else 0.0

    @property
    def filled_qty(self) -> int:
        return sum(t.qty for t in self.tranches if t.status == "FILLED")


class ScaleInManager:
    """
    Manages institutional-style scale-in entries and runner exits.

    Works alongside trade_manager — does NOT replace it.
    ScaleInManager decides WHEN and HOW MUCH to enter.
    trade_manager does the actual order placement.

    Usage:
        sim = ScaleInManager()
        
        # On signal:
        pos = sim.create_position(symbol, "BUY", qty=75, score=8.5, atr=45)
        # Returns tranche 1 (50% = 37 lots) to execute NOW
        
        # After T1 fills and price pulls back:
        tranche2 = sim.should_add_tranche(pos, current_price, current_atr)
        if tranche2:
            # Execute tranche 2 (33% = 25 lots)
        
        # On price update:
        action = sim.update_position(pos, current_price, current_atr, bar_idx)
        # Returns: "HOLD" | "CLOSE_PARTIAL_T1" | "CLOSE_PARTIAL_T2" | "CLOSE_ALL"
    """

    def __init__(self) -> None:
        self.positions: Dict[str, ScaledPosition] = {}

    def create_position(
        self,
        symbol:       str,
        side:         str,
        total_qty:    int,
        strategy:     str,
        score:        float,
        entry_price:  float,
        atr:          float,
        stop_pct:     float = 0.10,   # 10% of premium as stop
    ) -> ScaledPosition:
        """
        Create a new scaled position plan.
        Returns the position with tranche 1 (50%) ready to execute.
        Tranches 2 and 3 are PENDING until conditions are met.
        """
        pos_id  = f"SP_{symbol}_{int(time.time())}"
        qty_t1  = max(1, int(total_qty * 0.50))   # 50%
        qty_t2  = max(1, int(total_qty * 0.33))   # 33%
        qty_t3  = total_qty - qty_t1 - qty_t2     # 17%

        atr_v  = max(atr, entry_price * 0.003)
        stop   = (entry_price * (1 - stop_pct) if side == "BUY"
                  else entry_price * (1 + stop_pct))
        t1     = (entry_price + 1.5 * atr_v if side == "BUY"
                  else entry_price - 1.5 * atr_v)
        t2     = (entry_price + 2.5 * atr_v if side == "BUY"
                  else entry_price - 2.5 * atr_v)

        pos = ScaledPosition(
            position_id  = pos_id,
            symbol       = symbol,
            side         = side,
            strategy     = strategy,
            signal_score = score,
            initial_qty  = total_qty,
            atr_at_entry = atr_v,
            stop_loss    = stop,
            target1      = t1,
            target2      = t2,
            runner_trail = stop,
            highest_price = entry_price,
            lowest_price  = entry_price,
            tranches = [
                ScaleEntry("T1_50pct", entry_price, qty_t1, time.time(),
                           "signal", "FILLED"),
                ScaleEntry("T2_33pct", entry_price, qty_t2, time.time(),
                           "pullback", "PENDING"),
                ScaleEntry("T3_17pct", entry_price, qty_t3, time.time(),
                           "confirmation", "PENDING"),
            ]
        )
        pos.total_filled_qty = qty_t1
        self.positions[pos_id] = pos

        logger.info(
            "ScaleIn position created | %s %s %s T1=%d/T2=%d/T3=%d "
            "entry=%.2f stop=%.2f T1tgt=%.2f T2tgt=%.2f",
            pos_id, symbol, side, qty_t1, qty_t2, qty_t3,
            entry_price, stop, t1, t2,
        )
        return pos

    def should_add_tranche(
        self,
        pos:           ScaledPosition,
        current_price: float,
        current_atr:   float,
    ) -> Optional[ScaleEntry]:
        """
        Check if it's time to add the next pending tranche.

        Tranche 2 conditions (pullback entry):
        - Price has moved ≥ 0.5 ATR toward target (confirmed move)
        - Price pulled back ≤ 0.3 ATR toward entry (pullback, better price)
        - Position is still valid (not stopped)

        Tranche 3 conditions (confirmation entry):
        - T1 target hit (price reached +1.5 ATR)
        - Still in uptrend (not reversing)
        """
        if pos.side == "BUY":
            move     = current_price - float(pos.avg_entry)
            pullback = pos.highest_price - current_price
        else:
            move     = float(pos.avg_entry) - current_price
            pullback = current_price - pos.lowest_price

        atr = max(current_atr, pos.atr_at_entry * 0.5)

        # Find next pending tranche
        for tranche in pos.tranches:
            if tranche.status != "PENDING":
                continue

            if tranche.tranche_id == "T2_33pct":
                # Add T2 on pullback after initial move
                moved_enough = move >= 0.5 * atr
                pulled_back  = 0.05 * atr <= pullback <= 0.4 * atr
                if moved_enough and pulled_back:
                    tranche.entry_price = current_price
                    tranche.entry_time  = time.time()
                    tranche.status      = "FILLED"
                    pos.total_filled_qty += tranche.qty
                    logger.info(
                        "ScaleIn T2 triggered | %s price=%.2f pullback=%.2f",
                        pos.position_id, current_price, pullback,
                    )
                    return tranche

            elif tranche.tranche_id == "T3_17pct":
                # Add T3 only when T1 target has been hit
                t1_hit = (
                    (pos.side == "BUY"  and pos.highest_price >= pos.target1)
                    or
                    (pos.side == "SELL" and pos.lowest_price  <= pos.target1)
                )
                if t1_hit:
                    tranche.entry_price = current_price
                    tranche.entry_time  = time.time()
                    tranche.status      = "FILLED"
                    pos.total_filled_qty += tranche.qty
                    logger.info(
                        "ScaleIn T3 triggered (T1 hit) | %s price=%.2f",
                        pos.position_id, current_price,
                    )
                    return tranche
            break

        return None

    def update_position(
        self,
        pos:           ScaledPosition,
        current_price: float,
        current_atr:   float,
        bar_index:     int,
    ) -> Dict[str, Any]:
        """
        Update position with latest price. Returns action to take.

        Returns dict:
        {
            "action":   "HOLD"|"CLOSE_T1_PARTIAL"|"CLOSE_T2_PARTIAL"|"CLOSE_RUNNER"|"CLOSE_STOP",
            "qty":      int,
            "reason":   str,
            "new_stop": float (if stop updated),
        }
        """
        # Track extremes
        if current_price > pos.highest_price:
            pos.highest_price = current_price
        if current_price < pos.lowest_price:
            pos.lowest_price = current_price

        atr = max(current_atr, pos.atr_at_entry * 0.5)

        # ── Stop hit ─────────────────────────────────────────────────────────
        if pos.side == "BUY" and current_price <= pos.stop_loss:
            return {"action": "CLOSE_STOP", "qty": pos.total_filled_qty,
                    "reason": f"stop_hit_{current_price:.0f}_{pos.stop_loss:.0f}"}
        if pos.side == "SELL" and current_price >= pos.stop_loss:
            return {"action": "CLOSE_STOP", "qty": pos.total_filled_qty,
                    "reason": f"stop_hit_{current_price:.0f}_{pos.stop_loss:.0f}"}

        # ── Runner trailing stop ──────────────────────────────────────────────
        if pos.is_runner_active:
            # Update runner trail
            if pos.side == "BUY":
                new_trail = pos.highest_price - 2.0 * atr
                new_trail = max(new_trail, float(pos.avg_entry))  # never below breakeven
                if new_trail > pos.runner_trail:
                    pos.runner_trail = new_trail
                if current_price <= pos.runner_trail:
                    runner_qty = next(
                        (t.qty for t in pos.tranches if t.tranche_id.startswith("T3")), 1
                    )
                    return {"action": "CLOSE_RUNNER", "qty": runner_qty,
                            "reason": "runner_trail_hit", "new_stop": pos.runner_trail}
            else:
                new_trail = pos.lowest_price + 2.0 * atr
                new_trail = min(new_trail, float(pos.avg_entry))
                if new_trail < pos.runner_trail:
                    pos.runner_trail = new_trail
                if current_price >= pos.runner_trail:
                    runner_qty = next(
                        (t.qty for t in pos.tranches if t.tranche_id.startswith("T3")), 1
                    )
                    return {"action": "CLOSE_RUNNER", "qty": runner_qty,
                            "reason": "runner_trail_hit", "new_stop": pos.runner_trail}

        # ── T1 partial close ──────────────────────────────────────────────────
        t1_qty = next((t.qty for t in pos.tranches if t.tranche_id == "T1_50pct"
                       and t.status == "FILLED"), 0)
        if t1_qty > 0:
            t1_hit = (pos.side == "BUY" and current_price >= pos.target1) or \
                     (pos.side == "SELL" and current_price <= pos.target1)
            if t1_hit:
                # Move stop to breakeven
                pos.stop_loss = float(pos.avg_entry)
                pos.breakeven_locked = True
                return {
                    "action":   "CLOSE_T1_PARTIAL",
                    "qty":      t1_qty,
                    "reason":   "t1_target_hit",
                    "new_stop": pos.stop_loss,
                }

        # ── T2 partial close → activate runner ───────────────────────────────
        t2_qty = next((t.qty for t in pos.tranches if t.tranche_id == "T2_33pct"
                       and t.status == "FILLED"), 0)
        if t2_qty > 0 and pos.breakeven_locked:
            t2_hit = (pos.side == "BUY" and current_price >= pos.target2) or \
                     (pos.side == "SELL" and current_price <= pos.target2)
            if t2_hit:
                pos.is_runner_active = True
                pos.runner_trail = (
                    pos.highest_price - 2.0 * atr if pos.side == "BUY"
                    else pos.lowest_price + 2.0 * atr
                )
                return {
                    "action":         "CLOSE_T2_PARTIAL",
                    "qty":            t2_qty,
                    "reason":         "t2_target_hit_runner_activated",
                    "new_stop":       pos.runner_trail,
                    "runner_active":  True,
                }

        # ── Update trailing stop (if breakeven locked and not runner yet) ─────
        if pos.breakeven_locked and not pos.is_runner_active:
            if pos.side == "BUY":
                new_trail = max(pos.stop_loss, pos.highest_price - 1.5 * atr)
                if new_trail > pos.stop_loss:
                    pos.stop_loss = new_trail
                    return {"action": "HOLD", "qty": 0,
                            "reason": "stop_trailed", "new_stop": pos.stop_loss}
            else:
                new_trail = min(pos.stop_loss, pos.lowest_price + 1.5 * atr)
                if new_trail < pos.stop_loss:
                    pos.stop_loss = new_trail
                    return {"action": "HOLD", "qty": 0,
                            "reason": "stop_trailed", "new_stop": pos.stop_loss}

        return {"action": "HOLD", "qty": 0, "reason": "monitoring"}

    def remove_position(self, position_id: str) -> None:
        self.positions.pop(position_id, None)

    def get_summary(self) -> Dict[str, Any]:
        active = [p for p in self.positions.values() if p.total_filled_qty > 0]
        return {
            "active_scaled_positions": len(active),
            "positions": [
                {
                    "id":         p.position_id,
                    "symbol":     p.symbol,
                    "side":       p.side,
                    "filled_qty": p.total_filled_qty,
                    "avg_entry":  round(p.avg_entry, 2),
                    "runner":     p.is_runner_active,
                    "stop":       round(p.stop_loss, 2),
                }
                for p in active
            ]
        }


# ── Module singleton ──────────────────────────────────────────────────────────
_scale_manager: Optional[ScaleInManager] = None


def get_scale_manager() -> ScaleInManager:
    global _scale_manager
    if _scale_manager is None:
        _scale_manager = ScaleInManager()
    return _scale_manager
