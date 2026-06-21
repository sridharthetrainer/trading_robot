"""
trailing.py

Reusable trailing stop manager for backtesting and live-style execution.

Interface expected by backtest.py:
    trailing = TrailingStop(config)
    stop_price, targets = trailing.initialize(trade_id, entry_price, side, atr)
    exit_ok, exit_price, exit_qty, reason = trailing.check_exit(
        trade_id, current_price, side, current_atr, remaining_qty, bar_index
    )
    trailing.cleanup(trade_id)

Fixes applied
-------------
Lazy trailing-stop update was described in the docstring and stored in
`__init__` but never executed in `check_exit()`.

`last_update_bar`, `lazy_update_threshold`, and `lazy_delay_bars` were
all stored in the position dict or on the instance but `check_exit()`
read `current_atr` to compute `new_trail` unconditionally on every bar,
ignoring both the delay and the threshold. This caused the trail to
ratchet tightly on every single bar — desirable for fast scalping but
too aggressive for swing or positional trades.

The lazy update policy works as follows:
- The trailing stop ALWAYS updates on the FIRST activation (when
  `trail_stop is None`). This sets the initial trail reference.
- On SUBSEQUENT bars, the update fires only if EITHER condition is met:
    a) At least `lazy_delay_bars` bars have elapsed since the last
       trail update, OR
    b) The new trail value improves by at least
       `lazy_update_threshold × entry_atr` over the current trail
       (a "large move" override — tighten immediately on big runs).

This makes the trailing stop configurable across strategies:
- Scalping: lazy_delay_bars=0, lazy_update_threshold=0 → update every bar
- Intraday: lazy_delay_bars=2, lazy_update_threshold=1.5 → default
- Swing:    lazy_delay_bars=5, lazy_update_threshold=2.0 → slow trail

Both `lazy_delay_bars=0` and `lazy_update_threshold=0` reproduce the
original every-bar behaviour, so existing call sites are unaffected if
they pass zeros.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


class TrailingStop:
    def __init__(self, config: Dict) -> None:
        self.config = config or {}

        self.stop_atr    = float(self.config.get("STOP_ATR_MULTIPLIER", 2.0))
        self.target1_atr = float(self.config.get("TARGET1_ATR", 1.0))
        self.target2_atr = float(self.config.get("TARGET2_ATR", 1.5))
        self.target3_atr = float(self.config.get("TARGET3_ATR", 2.0))

        self.target1_size = float(self.config.get("TARGET1_SIZE", 0.33))
        self.target2_size = float(self.config.get("TARGET2_SIZE", 0.33))
        self.target3_size = float(self.config.get("TARGET3_SIZE", 0.34))

        # Lazy trail update — now actually used in check_exit()
        self.lazy_update_threshold = float(self.config.get("LAZY_UPDATE_THRESHOLD", 1.5))
        self.lazy_delay_bars       = int(  self.config.get("LAZY_DELAY_BARS",       2))
        self.max_hold_bars         = int(  self.config.get("MAX_HOLD_BARS",         0))  # 0 = disabled
        self.swing_stop_pct        = float(self.config.get("SWING_STOP_PCT",       0.25))  # 25% stop for swing
        self.trail_activate_after  = float(self.config.get("TRAIL_ACTIVATE_AFTER",  0.0))

        self.positions: Dict[int, Dict] = {}

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def initialize(
        self,
        trade_id:    int,
        entry_price: float,
        side:        str,
        atr:         float,
    ) -> Tuple[float, Dict[str, float]]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"Invalid side: {side!r}")
        if atr <= 0:
            raise ValueError("ATR must be > 0")

        if side == "BUY":
            stop_price = entry_price - self.stop_atr * atr
            targets    = {
                "t1": entry_price + self.target1_atr * atr,
                "t2": entry_price + self.target2_atr * atr,
                "t3": entry_price + self.target3_atr * atr,
            }
        else:
            stop_price = entry_price + self.stop_atr * atr
            targets    = {
                "t1": entry_price - self.target1_atr * atr,
                "t2": entry_price - self.target2_atr * atr,
                "t3": entry_price - self.target3_atr * atr,
            }

        self.positions[trade_id] = {
            "entry_price":    float(entry_price),
            "side":           side,
            "entry_atr":      float(atr),
            "stop_price":     float(stop_price),
            "trail_stop":     None,
            "highest_price":  float(entry_price),
            "lowest_price":   float(entry_price),
            "targets":        targets,
            "t1_done":        False,
            "t2_done":        False,
            "t3_done":        False,
            "last_update_bar": None,   # bar index of most recent trail update
        }

        return float(stop_price), targets

    # ------------------------------------------------------------------
    # Lazy-update decision
    # ------------------------------------------------------------------
    def _should_update_trail(
        self,
        pos:          Dict,
        new_trail:    float,
        current_atr:  float,
        bar_index:    int,
    ) -> bool:
        """
        Return True if the trailing stop should be updated this bar.

        Rules
        -----
        1. Always update on first activation (trail_stop is None).
        2. Enough bars have passed since last update.
        3. The improvement is large enough to justify an immediate update
           (big-run override).
        """
        # First activation
        if pos["trail_stop"] is None:
            return True

        # Determine improvement magnitude
        side = pos["side"]
        if side == "BUY":
            improvement = new_trail - pos["trail_stop"]
        else:
            improvement = pos["trail_stop"] - new_trail

        # Big-run override: improvement >= threshold × entry_atr
        if self.lazy_update_threshold > 0:
            threshold_distance = self.lazy_update_threshold * pos["entry_atr"]
            if improvement >= threshold_distance:
                return True

        # Bar-delay check
        if self.lazy_delay_bars <= 0:
            return True   # lazy_delay_bars=0 → update every bar (legacy behaviour)

        last_bar = pos["last_update_bar"]
        if last_bar is None:
            return True

        return (bar_index - last_bar) >= self.lazy_delay_bars

    # ------------------------------------------------------------------
    # Exit check
    # ------------------------------------------------------------------
    def check_exit(
        self,
        trade_id:      int,
        current_price: float,
        side:          str,
        current_atr:   float,
        remaining_qty: int,
        bar_index:     int,
    ) -> Tuple[bool, float, int, str]:
        """
        Check whether a position should exit (fully or partially).

        Returns
        -------
        (exit_triggered, exit_price, exit_qty, reason)
        """
        pos = self.positions.get(trade_id)
        if pos is None:
            return False, 0.0, 0, ""

        side = side.upper()
        if remaining_qty <= 0 or current_atr <= 0:
            return False, 0.0, 0, ""

        entry_price = pos["entry_price"]

        # ----------------------------------------------------------
        # BUY side
        # ----------------------------------------------------------
        # Time-stop: max hold duration (skipped in swing mode)
        if (self.max_hold_bars > 0
                and pos.get("entry_bar_index") is not None
                and not pos.get("swing_mode", False)):
            bars_held = bar_index - pos["entry_bar_index"]
            if bars_held >= self.max_hold_bars:
                return True, float(current_price), int(remaining_qty), "max_hold_bars"

        if side == "BUY":
            pos["highest_price"] = max(pos["highest_price"], current_price)

            profit_move    = pos["highest_price"] - entry_price
            activate_level = self.trail_activate_after * pos["entry_atr"]

            if profit_move >= activate_level:
                new_trail = pos["highest_price"] - self.stop_atr * current_atr

                if self._should_update_trail(pos, new_trail, current_atr, bar_index):
                    if pos["trail_stop"] is None:
                        pos["trail_stop"] = new_trail
                    else:
                        pos["trail_stop"] = max(pos["trail_stop"], new_trail)
                    pos["last_update_bar"] = bar_index

            effective_stop = pos["stop_price"]
            if pos["trail_stop"] is not None:
                effective_stop = max(effective_stop, pos["trail_stop"])

            if current_price <= effective_stop:
                return True, float(effective_stop), int(remaining_qty), "stop_loss"

            t1, t2, t3 = pos["targets"]["t1"], pos["targets"]["t2"], pos["targets"]["t3"]

            if not pos["t1_done"] and current_price >= t1:
                qty = min(max(1, int(round(remaining_qty * self.target1_size))), remaining_qty)
                pos["t1_done"] = True
                return True, float(t1), qty, "target1"

            if not pos["t2_done"] and current_price >= t2:
                qty = min(max(1, int(round(remaining_qty * self.target2_size))), remaining_qty)
                pos["t2_done"] = True
                return True, float(t2), qty, "target2"

            if not pos["t3_done"] and current_price >= t3:
                pos["t3_done"] = True
                return True, float(t3), int(remaining_qty), "target3"

        # ----------------------------------------------------------
        # SELL side
        # ----------------------------------------------------------
        elif side == "SELL":
            pos["lowest_price"] = min(pos["lowest_price"], current_price)

            profit_move    = entry_price - pos["lowest_price"]
            activate_level = self.trail_activate_after * pos["entry_atr"]

            if profit_move >= activate_level:
                new_trail = pos["lowest_price"] + self.stop_atr * current_atr

                if self._should_update_trail(pos, new_trail, current_atr, bar_index):
                    if pos["trail_stop"] is None:
                        pos["trail_stop"] = new_trail
                    else:
                        pos["trail_stop"] = min(pos["trail_stop"], new_trail)
                    pos["last_update_bar"] = bar_index

            effective_stop = pos["stop_price"]
            if pos["trail_stop"] is not None:
                effective_stop = min(effective_stop, pos["trail_stop"])

            if current_price >= effective_stop:
                return True, float(effective_stop), int(remaining_qty), "stop_loss"

            t1, t2, t3 = pos["targets"]["t1"], pos["targets"]["t2"], pos["targets"]["t3"]

            if not pos["t1_done"] and current_price <= t1:
                qty = min(max(1, int(round(remaining_qty * self.target1_size))), remaining_qty)
                pos["t1_done"] = True
                return True, float(t1), qty, "target1"

            if not pos["t2_done"] and current_price <= t2:
                qty = min(max(1, int(round(remaining_qty * self.target2_size))), remaining_qty)
                pos["t2_done"] = True
                return True, float(t2), qty, "target2"

            if not pos["t3_done"] and current_price <= t3:
                pos["t3_done"] = True
                return True, float(t3), int(remaining_qty), "target3"

        return False, 0.0, 0, ""

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self, trade_id: int) -> None:
        self.positions.pop(trade_id, None)

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------
    def get_position(self, trade_id: int) -> Optional[Dict]:
        """Return a copy of the internal position state for inspection."""
        pos = self.positions.get(trade_id)
        return dict(pos) if pos is not None else None

    def active_trade_ids(self):
        """Return list of currently tracked trade IDs."""
        return list(self.positions.keys())


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Config with lazy update: only update trail every 3 bars
    config = {
        "STOP_ATR_MULTIPLIER":  2.0,
        "TARGET1_ATR":          1.0,
        "TARGET2_ATR":          1.5,
        "TARGET3_ATR":          2.0,
        "TARGET1_SIZE":         0.33,
        "TARGET2_SIZE":         0.33,
        "TARGET3_SIZE":         0.34,
        "LAZY_UPDATE_THRESHOLD": 1.5,
        "LAZY_DELAY_BARS":       3,
        "TRAIL_ACTIVATE_AFTER":  0.5,
    }

    ts = TrailingStop(config)
    entry, atr = 22000.0, 50.0
    stop, targets = ts.initialize(1, entry, "BUY", atr)
    print(f"Entry={entry}  ATR={atr}  InitialStop={stop:.0f}")
    print(f"Targets: T1={targets['t1']:.0f}  T2={targets['t2']:.0f}  T3={targets['t3']:.0f}")
    print()

    # Simulate price moving up with lazy trail updates
    prices = [22000, 22020, 22040, 22080, 22100, 22150,  # moving up
              22090, 22060, 22020, 21960]                 # pulling back

    for bar, price in enumerate(prices):
        ok, ep, qty, reason = ts.check_exit(1, float(price), "BUY", atr, 50, bar)
        pos = ts.get_position(1)
        trail = pos["trail_stop"] if pos else None
        print(
            f"bar={bar:2d}  price={price:6.0f}  "
            f"trail={trail:.0f if trail else 'None'!r}  "
            f"last_upd_bar={pos['last_update_bar'] if pos else '-'}  "
            f"exit={ok}  reason={reason or '-'}"
        )
        if ok:
            print(f"  → EXIT at {ep:.0f}  qty={qty}  reason={reason}")
            break
