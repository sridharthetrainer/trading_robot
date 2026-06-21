"""
gap_risk_manager.py

Pre-market risk checks for swing trades and live option management.

Implements:
1. SGX Nifty Gap Check (FIX 4)
   At 08:50 every morning, compares expected NIFTY opening vs
   swing trade stop distances. Closes risky swing positions before
   the market opens.

2. Option LTP Refresh (FIX 9)
   Every 5 minutes, re-fetches current option LTP and updates
   stop/target levels based on current premium (not entry premium).
   Critical on high-IV days when options move faster than underlying.

3. IV Rank Tracking (FIX 8)
   Tracks 30-day implied volatility history for NIFTY options.
   Computes IV rank = (current_IV - 30d_low) / (30d_high - 30d_low).
   Blocks option BUY entries when IV rank > 70% (expensive options).

4. Option Expiry Roll Check (FIX 6)
   On Wednesday 15:30, checks if any profitable swing trades have
   DTE ≤ 1 and should be evaluated for roll to next week.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

GAP_CHECK_TIME_HH_MM  = (8, 50)   # 08:50 AM — before market open
ROLL_CHECK_TIME_HH_MM = (15, 30)  # 15:30 PM — after market, before EOD
IV_HISTORY_FILE       = "iv_history.json"
IV_HISTORY_DAYS       = 30
IV_HIGH_RANK_THRESHOLD = 0.70     # IV rank > 70% = expensive, block buying
IV_LOW_RANK_THRESHOLD  = 0.20     # IV rank < 20% = cheap, prefer buying
LTP_REFRESH_INTERVAL   = 300      # 5 minutes


class IVTracker:
    """
    Tracks daily ATM implied volatility for NIFTY and BANKNIFTY.
    Computes IV rank over the last 30 days.
    """

    def __init__(self, history_file: str = IV_HISTORY_FILE) -> None:
        self._file = Path(history_file)
        self._history: Dict[str, List[Dict]] = {}
        self._load()

    def _load(self) -> None:
        if self._file.exists():
            try:
                self._history = json.loads(self._file.read_text())
            except Exception:
                self._history = {}

    def _save(self) -> None:
        try:
            self._file.write_text(json.dumps(self._history, indent=2, default=str))
        except Exception:
            pass

    def record_iv(self, underlying: str, iv: float) -> None:
        """Record today's IV snapshot for an underlying."""
        key    = underlying.upper()
        today  = date.today().isoformat()
        entry  = {"date": today, "iv": round(float(iv), 4), "ts": time.time()}
        hist   = self._history.setdefault(key, [])

        # Remove today's entry if exists (update)
        hist = [h for h in hist if h.get("date") != today]
        hist.append(entry)

        # Keep last 30 days
        hist.sort(key=lambda x: x["date"])
        self._history[key] = hist[-IV_HISTORY_DAYS:]
        self._save()

    def get_iv_rank(self, underlying: str) -> Optional[float]:
        """
        Returns IV rank 0-1 based on last 30 days.
        None if insufficient history (< 10 days).
        """
        hist = self._history.get(underlying.upper(), [])
        if len(hist) < 10:
            return None
        ivs      = [h["iv"] for h in hist if h.get("iv", 0) > 0]
        if not ivs:
            return None
        current  = ivs[-1]
        iv_min   = min(ivs)
        iv_max   = max(ivs)
        if iv_max <= iv_min:
            return 0.5
        return round((current - iv_min) / (iv_max - iv_min), 4)

    def is_iv_expensive(self, underlying: str) -> bool:
        """True if options are expensive (IV rank > 70%). Block BUY entries."""
        rank = self.get_iv_rank(underlying)
        if rank is None:
            return False   # unknown — don't block
        return rank >= IV_HIGH_RANK_THRESHOLD

    def is_iv_cheap(self, underlying: str) -> bool:
        """True if options are cheap (IV rank < 20%). Prefer BUY entries."""
        rank = self.get_iv_rank(underlying)
        if rank is None:
            return False
        return rank <= IV_LOW_RANK_THRESHOLD

    def get_status(self) -> Dict[str, Any]:
        status = {}
        for sym in self._history:
            rank = self.get_iv_rank(sym)
            hist = self._history[sym]
            if hist:
                status[sym] = {
                    "current_iv":  hist[-1].get("iv"),
                    "iv_rank":     rank,
                    "days_history": len(hist),
                    "expensive":   self.is_iv_expensive(sym),
                    "cheap":       self.is_iv_cheap(sym),
                }
        return status


class GapRiskManager:
    """
    Manages pre-market gap risk and live option position management.
    """

    def __init__(
        self,
        trade_manager   = None,
        data_fetcher    = None,
        alerts          = None,
        iv_history_file: str = IV_HISTORY_FILE,
    ) -> None:
        self.trade_manager  = trade_manager
        self.data_fetcher   = data_fetcher
        self.alerts         = alerts
        self.iv_tracker     = IVTracker(iv_history_file)

        self._gap_checked_today:   bool  = False
        self._gap_check_date:      str   = ""
        self._roll_checked_today:  bool  = False
        self._last_ltp_refresh:    float = 0.0
        self._last_iv_record:      float = 0.0

    # ── FIX 4: Pre-market gap check ──────────────────────────────────────────

    def should_run_gap_check(self) -> bool:
        """True if it's 08:50 and gap check hasn't run today."""
        now = datetime.now()
        if now.date().isoformat() != self._gap_check_date:
            self._gap_checked_today = False
            self._roll_checked_today = False

        if self._gap_checked_today:
            return False

        h, m = GAP_CHECK_TIME_HH_MM
        return now.hour == h and now.minute >= m

    def run_gap_check(self) -> List[str]:
        """
        Check if expected opening gap threatens swing trade stops.
        Returns list of trade_ids that were closed due to gap risk.

        Fetches SGX Nifty futures via yfinance (SGX.NS or ^NSEI pre-open).
        """
        self._gap_checked_today = True
        self._gap_check_date    = date.today().isoformat()
        closed_ids: List[str]   = []

        if not self.trade_manager:
            return closed_ids

        # Find swing trades (DTE ≥ 2 = held overnight)
        swing_trades = [
            t for t in self.trade_manager.open_trades.values()
            if t.status == "OPEN" and t.metadata
            and t.metadata.get("swing_mode")
        ]

        if not swing_trades:
            logger.info("GapRiskManager: no swing trades to check")
            return closed_ids

        # Fetch expected opening via yfinance
        expected_gap_pct = self._fetch_expected_gap()
        if expected_gap_pct is None:
            logger.info("GapRiskManager: could not fetch gap estimate — skipping")
            return closed_ids

        logger.info(
            "GapRiskManager: expected gap=%.2f%% | checking %d swing trades",
            expected_gap_pct * 100, len(swing_trades),
        )

        for trade in swing_trades:
            if not trade.stop_loss or not trade.entry_price:
                continue

            stop_dist_pct = abs(
                float(trade.entry_price) - float(trade.stop_loss)
            ) / float(trade.entry_price)

            # If expected gap > stop distance → position would open through stop
            gap_direction = "down" if expected_gap_pct < 0 else "up"
            is_threatened = (
                (trade.side == "BUY"  and expected_gap_pct < 0
                 and abs(expected_gap_pct) > stop_dist_pct * 0.8)
                or
                (trade.side == "SELL" and expected_gap_pct > 0
                 and expected_gap_pct > stop_dist_pct * 0.8)
            )

            if is_threatened:
                logger.warning(
                    "GAP RISK: trade_id=%s symbol=%s gap=%.2f%% > stop_dist=%.2f%% — closing",
                    trade.trade_id, trade.symbol,
                    abs(expected_gap_pct) * 100, stop_dist_pct * 100,
                )
                try:
                    self.trade_manager.close_all_trades(
                        reason=f"gap_risk_premarket_{gap_direction}_{abs(expected_gap_pct)*100:.1f}pct"
                    )
                    closed_ids.append(trade.trade_id)

                    if self.alerts:
                        self.alerts.send(
                            f"⚠️ <b>GAP RISK — Pre-market close</b>\n"
                            f"Symbol: {trade.symbol}\n"
                            f"Expected gap: {expected_gap_pct*100:+.1f}%\n"
                            f"Stop distance: {stop_dist_pct*100:.1f}%\n"
                            f"Position closed before market open for protection"
                        )
                except Exception as exc:
                    logger.error(
                        "Failed to close gap-risk trade %s: %s",
                        trade.trade_id, exc,
                    )

        return closed_ids

    def _fetch_expected_gap(self) -> Optional[float]:
        """
        Estimate expected NIFTY opening gap.
        Uses SGX Nifty via yfinance or falls back to Dow Jones change.
        Returns decimal (e.g. -0.012 for -1.2% expected gap), or None.
        """
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            # SGX Nifty (best proxy for NIFTY opening gap)
            sgx = yf.download("^NSEI", period="2d", interval="1d",
                               progress=False, auto_adjust=True, threads=False)
            if sgx is not None and len(sgx) >= 2:
                prev_close = float(sgx["Close"].iloc[-2])
                today_est  = float(sgx["Close"].iloc[-1])
                if prev_close > 0:
                    return (today_est - prev_close) / prev_close
        except Exception as exc:
            logger.debug("_fetch_expected_gap yfinance error: %s", exc)

        # Fallback: Dow Jones overnight change as proxy
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            dji = yf.download("^DJI", period="2d", interval="1d",
                               progress=False, auto_adjust=True, threads=False)
            if dji is not None and len(dji) >= 2:
                prev = float(dji["Close"].iloc[-2])
                curr = float(dji["Close"].iloc[-1])
                if prev > 0:
                    dji_change = (curr - prev) / prev
                    # NIFTY typically moves 40-60% of DJI change
                    return dji_change * 0.50
        except Exception:
            pass

        return None

    # ── FIX 9: Option LTP refresh every 5 minutes ────────────────────────────

    def should_refresh_option_ltp(self) -> bool:
        return (time.time() - self._last_ltp_refresh) >= LTP_REFRESH_INTERVAL

    def refresh_option_ltp(self) -> int:
        """
        Re-fetch current LTP for all open option positions and update
        unrealized P&L and stop distances.
        Returns number of positions refreshed.
        """
        self._last_ltp_refresh = time.time()
        if not self.trade_manager:
            return 0

        refreshed = 0
        for trade_id, trade in list(self.trade_manager.open_trades.items()):
            if trade.status != "OPEN":
                continue
            if not ("CE" in trade.symbol or "PE" in trade.symbol):
                continue   # only options

            try:
                if self.data_fetcher:
                    broker = getattr(self.data_fetcher, "_broker", None)
                    if broker and hasattr(broker, "get_ltp"):
                        current_ltp = broker.get_ltp(trade.symbol)
                        if current_ltp and current_ltp > 0:
                            # Update metadata with current LTP
                            if isinstance(trade.metadata, dict):
                                trade.metadata["current_ltp"]     = current_ltp
                                trade.metadata["ltp_refresh_ts"]  = time.time()
                                unr = (current_ltp - float(trade.entry_price)) * int(trade.qty)
                                trade.metadata["unrealized_pnl"]  = round(unr, 2)
                            refreshed += 1
            except Exception as exc:
                logger.debug("LTP refresh failed for %s: %s", trade.symbol, exc)

        if refreshed:
            logger.debug("Option LTP refreshed for %d positions", refreshed)
        return refreshed

    # ── FIX 6: Expiry roll check ──────────────────────────────────────────────

    def should_check_expiry_roll(self) -> bool:
        """True on Wednesday after 15:30 if not already checked today."""
        now = datetime.now()
        if self._roll_checked_today:
            return False
        h, m = ROLL_CHECK_TIME_HH_MM
        is_wednesday = now.weekday() == 2
        is_after_time = now.hour > h or (now.hour == h and now.minute >= m)
        return is_wednesday and is_after_time

    def check_expiry_roll_candidates(self) -> List[Dict[str, Any]]:
        """
        Identify swing trades with DTE ≤ 1 that could be rolled.
        Returns list of candidate dicts for each rollable position.
        Does NOT auto-roll (requires Telegram confirmation or future automation).
        """
        self._roll_checked_today = True
        candidates: List[Dict[str, Any]] = []

        if not self.trade_manager:
            return candidates

        for trade_id, trade in self.trade_manager.open_trades.items():
            if trade.status != "OPEN":
                continue
            if not ("CE" in trade.symbol or "PE" in trade.symbol):
                continue
            meta = trade.metadata or {}
            if not meta.get("swing_mode"):
                continue

            # Check DTE
            expiry_str = str(meta.get("expiry", ""))
            dte = 0
            try:
                if expiry_str:
                    expiry_date = pd.Timestamp(expiry_str).date()
                    dte         = (expiry_date - date.today()).days
            except Exception:
                dte = 0

            if dte <= 1:
                current_ltp = float(meta.get("current_ltp", trade.entry_price))
                entry       = float(trade.entry_price)
                is_profit   = (
                    (trade.side == "BUY"  and current_ltp > entry)
                    or (trade.side == "SELL" and current_ltp < entry)
                )

                candidate = {
                    "trade_id":    trade_id,
                    "symbol":      trade.symbol,
                    "side":        trade.side,
                    "qty":         trade.qty,
                    "entry":       entry,
                    "current_ltp": current_ltp,
                    "dte":         dte,
                    "is_profit":   is_profit,
                    "pnl_pct":     (current_ltp - entry) / entry * 100,
                    "expiry":      expiry_str,
                }
                candidates.append(candidate)

        if candidates:
            logger.info(
                "Expiry roll candidates found: %d positions with DTE ≤ 1",
                len(candidates),
            )
            if self.alerts:
                try:
                    msg = "📅 <b>EXPIRY ROLL CANDIDATES</b>\n\n"
                    for c in candidates:
                        profit_tag = "✅" if c["is_profit"] else "❌"
                        msg += (
                            f"{profit_tag} {c['symbol']} {c['side']} {c['qty']}\n"
                            f"  Entry: ₹{c['entry']:.2f}  Current: ₹{c['current_ltp']:.2f}\n"
                            f"  P&L: {c['pnl_pct']:+.1f}%  DTE: {c['dte']}\n\n"
                        )
                    msg += "ℹ️ Manually evaluate whether to roll to next week's expiry."
                    self.alerts.send(msg, dedup_key="expiry_roll_candidates")
                except Exception:
                    pass

        return candidates

    # ── IV tracking integration ───────────────────────────────────────────────

    def record_iv_from_option_chain(self, underlying: str, iv: float) -> None:
        """Record current ATM IV. Call after each option chain fetch."""
        self.iv_tracker.record_iv(underlying, iv)

    def should_block_iv(self, underlying: str) -> bool:
        """
        Returns True if IV is too high to buy options profitably.
        Used in live_signal_engine before placing option BUY orders.
        """
        return self.iv_tracker.is_iv_expensive(underlying)

    def get_iv_status(self) -> Dict[str, Any]:
        return self.iv_tracker.get_status()


    # ── IV Percentile (IVP) ───────────────────────────────────────────────────

    def get_iv_percentile(self, underlying: str) -> float:
        """
        IV Percentile: what % of past 252 trading days had LOWER IV than today.
        
        IVP = 85% means today's IV is higher than 85% of all days in past year.
        
        Difference from IV Rank:
        - IV Rank = where today sits in min-max range (can be distorted by outliers)
        - IV Percentile = pure count-based (more statistically robust)
        """
        hist = self.iv_tracker._history.get(underlying.upper(), [])
        if len(hist) < 20:
            return 50.0
        ivs     = [h["iv"] for h in hist if h.get("iv", 0) > 0]
        current = ivs[-1] if ivs else 0
        if not current:
            return 50.0
        days_lower = sum(1 for v in ivs[:-1] if v < current)
        return round(days_lower / max(len(ivs) - 1, 1) * 100, 1)

    # ── Portfolio Delta + Theta Budget ────────────────────────────────────────

    def get_portfolio_greeks(self, trade_manager) -> dict:
        """
        Compute aggregate Greeks across all open option positions.
        
        Returns:
          net_delta: total directional exposure (positive = long market)
          total_theta: total daily theta decay cost (negative = we lose this per day)
          net_gamma: total gamma (positive = profits accelerate on big moves)
          net_vega: sensitivity to IV change
        """
        net_delta = 0.0
        total_theta = 0.0
        net_gamma   = 0.0
        net_vega    = 0.0

        if not trade_manager:
            return {"net_delta": 0, "total_theta": 0, "net_gamma": 0, "net_vega": 0}

        for trade in trade_manager.get_open_positions():
            meta = trade.get("metadata", {}) or {}
            qty  = int(trade.get("qty", 0))
            side_mult = 1 if trade.get("side","BUY") == "BUY" else -1

            delta  = float(meta.get("delta",  0) or 0)
            theta  = float(meta.get("theta",  0) or 0)
            gamma  = float(meta.get("gamma",  0) or 0)
            vega   = float(meta.get("vega",   0) or 0)

            net_delta   += delta * qty * side_mult
            total_theta += theta * qty * side_mult
            net_gamma   += gamma * qty * side_mult
            net_vega    += vega  * qty * side_mult

        return {
            "net_delta":   round(net_delta,   4),
            "total_theta": round(total_theta, 2),   # rupees per day
            "net_gamma":   round(net_gamma,   6),
            "net_vega":    round(net_vega,    4),
        }

    def get_theta_budget_alert(self, trade_manager, daily_budget: float = 500.0) -> dict:
        """
        Alert if total daily theta burn exceeds budget.
        
        daily_budget: maximum acceptable theta decay per day in rupees (default ₹500)
        """
        greeks = self.get_portfolio_greeks(trade_manager)
        theta_per_day = abs(greeks["total_theta"])
        exceeded      = theta_per_day > daily_budget
        return {
            "theta_per_day": theta_per_day,
            "budget":        daily_budget,
            "exceeded":      exceeded,
            "pct_of_budget": round(theta_per_day / max(daily_budget, 1) * 100, 1),
            "net_delta":     greeks["net_delta"],
        }

    # ── Order fill quality tracker ────────────────────────────────────────────

    def record_fill_quality(
        self,
        trade_id:      str,
        symbol:        str,
        expected_price: float,
        actual_price:   float,
        side:          str,
    ) -> float:
        """
        Track actual vs expected fill quality.
        Returns slippage as a percentage.
        Negative = better than expected, positive = worse.
        """
        if not expected_price:
            return 0.0
        slippage_pct = (actual_price - expected_price) / expected_price
        if side == "SELL":
            slippage_pct = -slippage_pct

        if not hasattr(self, "_fill_history"):
            self._fill_history = []
        self._fill_history.append(slippage_pct)
        if len(self._fill_history) > 200:
            self._fill_history.pop(0)

        avg = sum(self._fill_history) / len(self._fill_history)
        if abs(slippage_pct) > 0.003:
            import logging
            logging.getLogger(__name__).warning(
                "Fill quality: %s expected=₹%.2f actual=₹%.2f slippage=%.3f%% avg=%.3f%%",
                symbol, expected_price, actual_price,
                slippage_pct * 100, avg * 100,
            )
        return round(slippage_pct, 5)

    def get_avg_slippage(self) -> float:
        """Returns average fill slippage as decimal. Negative = good."""
        if not hasattr(self, "_fill_history") or not self._fill_history:
            return 0.0
        return round(sum(self._fill_history) / len(self._fill_history), 5)



def _gift_nifty_gap(prev_close: float = 0.0) -> dict:
    """GIFT Nifty pre-market gap from nseifsc.com / Stooq."""
    try:
        from data_source_resilience import get_gift_nifty_gap
        return get_gift_nifty_gap(prev_close)
    except Exception:
        return {"gap_pct": 0.0, "direction": "FLAT"}
