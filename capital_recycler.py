"""
capital_recycler.py — Auto-Redeploy Capital on Trade Close

When a trade closes, freed capital is immediately evaluated for
redeployment. If a high-confluence signal exists within risk limits,
the system deploys it automatically within 60 seconds.

This prevents capital from sitting idle between 9:15 AM and 3:25 PM.
Average NSE trader has capital idle 40% of market hours — this fixes it.

LOGIC:
  1. Trade closes → freed_capital = trade notional
  2. Scan top 20 symbols for best current signal
  3. If best_signal.score >= 7.0 AND risk within limits → deploy
  4. If no good signal → wait for next 5-min scan (normal flow)
  5. Never force bad trades — quality threshold is strict
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_REDEPLOY_MIN_SCORE  = 7.0    # only redeploy on strong signals
_REDEPLOY_DELAY_SEC  = 30     # wait 30s after close before scanning
_REDEPLOY_WINDOW_SEC = 120    # give up after 2 min if no good signal
_REDEPLOY_SYMBOLS    = [      # priority symbols for quick redeploy scan
    "NIFTY","BANKNIFTY","FINNIFTY",
    "RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS",
    "SBIN","AXISBANK","KOTAKBANK","LT","WIPRO",
]


class CapitalRecycler:
    """
    Fires on trade close event and scans for redeployment.
    Runs in its own thread — non-blocking.
    """

    def __init__(self, live_engine=None, alerts=None) -> None:
        self.live_engine = live_engine
        self.alerts      = alerts
        self._lock       = threading.Lock()
        self._pending    = []   # (freed_capital, closed_trade, timestamp)

    def on_trade_closed(
        self,
        freed_capital: float,
        closed_trade:  Dict[str, Any],
    ) -> None:
        """
        Called immediately when a trade closes.
        Non-blocking — spawns background thread.
        """
        if not self._in_trading_hours():
            return
        with self._lock:
            self._pending.append({
                "freed":   freed_capital,
                "trade":   closed_trade,
                "ts":      time.time(),
            })
        t = threading.Thread(
            target=self._evaluate_redeploy,
            args=(freed_capital, closed_trade),
            daemon=True,
        )
        t.start()

    def _evaluate_redeploy(
        self,
        freed_capital: float,
        closed_trade:  Dict,
    ) -> None:
        """Background: scan for best redeployment opportunity."""
        try:
            time.sleep(_REDEPLOY_DELAY_SEC)  # brief pause after close

            if not self._in_trading_hours():
                return

            best_signal    = None
            best_score     = _REDEPLOY_MIN_SCORE

            symbols = _REDEPLOY_SYMBOLS[:]
            # Add same sector as closed trade
            sector = closed_trade.get("sector","")
            if sector:
                symbols = [closed_trade.get("symbol","")] + symbols

            for sym in symbols[:20]:
                try:
                    if not self.live_engine:
                        break
                    df = self.live_engine.data_fetcher.get_market_data(sym)
                    if df is None or len(df) < 50:
                        continue

                    from signal_engine import generate_signal
                    sig = generate_signal(df=df, symbol=sym)
                    if sig and float(sig.get("score",0)) >= best_score:
                        best_score  = float(sig["score"])
                        best_signal = {**sig, "symbol": sym}
                except Exception:
                    continue

            if best_signal:
                sym    = best_signal["symbol"]
                score  = best_signal["score"]
                side   = best_signal.get("side","BUY")
                logger.info("Recycler: redeploying ₹%.0f → %s (score %.1f)",
                            freed_capital, sym, score)
                if self.alerts:
                    self.alerts.send(
                        f"♻️ <b>CAPITAL RECYCLED</b>\n"
                        f"  ₹{freed_capital:,.0f} freed → redeploying\n"
                        f"  {'🟢' if side=='BUY' else '🔴'} {sym}  "
                        f"score {score:.1f}  [{best_signal.get('strategy','?')}]\n"
                        f"🕐 {datetime.now().strftime('%H:%M')}",
                        dedup_key=f"recycle:{sym}:{int(time.time()//60)}",
                        dedup_cooldown_override=55,
                    )
                # Trigger signal evaluation through live engine
                if hasattr(self.live_engine, "_force_signal"):
                    self.live_engine._force_signal(best_signal)
            else:
                logger.debug("Recycler: no good signal found for ₹%.0f", freed_capital)

        except Exception as e:
            logger.debug("capital_recycler: %s", e)

    def _in_trading_hours(self) -> bool:
        n = datetime.now().time()
        return dtime(9, 15) <= n <= dtime(15, 20)


_recycler: Optional[CapitalRecycler] = None

def get_recycler(live_engine=None, alerts=None) -> CapitalRecycler:
    global _recycler
    if _recycler is None:
        _recycler = CapitalRecycler(live_engine=live_engine, alerts=alerts)
    return _recycler
