"""
oi_tracker.py — Real-Time 5-Min OI Flow Tracker

Refreshes every 5 minutes (matching the trading bar interval).
Defines market direction from OI delta in real-time.

DIRECTION LOGIC:
  CE OI building > PE OI building  → BEARISH  (bears writing calls)
  PE OI building > CE OI building  → BULLISH  (bulls writing puts)
  Both building equally            → NEUTRAL
  CE OI unwinding                  → BULLISH  (call shorts covering)
  PE OI unwinding                  → BEARISH  (put shorts covering)

ALERTS:
  • Every 5 min: direction + dominant strike stored (no spam)
  • Direction CHANGE: immediate alert ("Market flipped BEARISH")
  • Surge alert: single strike adds >20% OI in 5 min
  • Auto-sent at: 10:30, 12:30, 14:30, 15:15

ON DEMAND: /oitrend  /oitrend BANKNIFTY
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_STATE_FILE     = Path("oi_tracker_state.json")
_SNAP_INTERVAL  = 300          # 5 minutes = 300 seconds
_MAX_SNAPS      = 78           # 6.5 market hours × 12 snaps/hour
_MAX_BAR        = 10
_BLOCKS         = "▁▂▃▄▅▆▇█"
_SURGE_THRESH   = 0.20         # 20% OI jump in 5 min = alert
_DIR_FLIP_MIN_DELTA = 0.05     # 5% difference to call a direction


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    try:
        s.get("https://www.nseindia.com/", timeout=5)
    except Exception:
        pass
    return s


def _fetch_chain(symbol: str) -> Optional[dict]:
    try:
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=12,
        )
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.debug("fetch_chain %s: %s", symbol, e)
        return None


def _parse_chain(raw: dict) -> Tuple[float, Dict[str, dict]]:
    """
    Parse option chain. Returns (spot, {strike_key: {oi, oi_chg, ltp, ltp_chg, iv, vol}}).
    strike_key format: "22800CE" or "22800PE"
    """
    records  = raw.get("records", {})
    all_data = records.get("data", [])
    spot     = float(records.get("underlyingValue", 0))

    # Nearest expiry
    expiry_dates = sorted(set(r.get("expiryDate", "") for r in all_data if r.get("expiryDate")))
    target = expiry_dates[0] if expiry_dates else ""
    data   = [r for r in all_data if r.get("expiryDate", "") == target] or all_data

    strikes: Dict[str, dict] = {}
    for row in data:
        k = int(float(row.get("strikePrice", 0)))
        for side in ("CE", "PE"):
            if side in row:
                d = row[side]
                strikes[f"{k}{side}"] = {
                    "oi":     float(d.get("openInterest", 0)),
                    "oi_chg": float(d.get("changeinOpenInterest", 0)),
                    "ltp":    float(d.get("lastPrice", 0)),
                    "ltp_chg":float(d.get("change", 0)),
                    "iv":     float(d.get("impliedVolatility", 0)),
                    "vol":    float(d.get("totalTradedVolume", 0)),
                }
    return spot, strikes


def _compute_direction(
    strikes_now:  Dict[str, dict],
    strikes_prev: Dict[str, dict],
    spot:         float,
) -> dict:
    """
    Determine market direction from 5-min OI delta.

    Logic:
      net_ce_delta = total CE OI added in last 5 min (near ATM ±5 strikes)
      net_pe_delta = total PE OI added in last 5 min (near ATM ±5 strikes)

      pe_delta >> ce_delta → BULLISH  (institutions writing puts = they expect up)
      ce_delta >> pe_delta → BEARISH  (institutions writing calls = they expect down)
      Both declining       → NEUTRAL/SQUEEZE (covering both sides)
    """
    step  = 50   # NIFTY strike step
    atm   = round(spot / step) * step

    # Focus on strikes within 500 points of ATM (10 strikes each side)
    near  = [k for k in strikes_now if abs(int(k[:-2]) - atm) <= 500]

    ce_delta = pe_delta = 0.0
    ce_oi    = pe_oi    = 0.0
    surge_strikes: List[dict] = []

    for k in near:
        prev = strikes_prev.get(k, {})
        curr = strikes_now[k]
        delta = curr["oi"] - prev.get("oi", curr["oi"])  # 0 if no prev

        if k.endswith("CE"):
            ce_delta += delta
            ce_oi    += curr["oi"]
        else:
            pe_delta += delta
            pe_oi    += curr["oi"]

        # Surge check: >20% jump in single strike OI
        prev_oi = prev.get("oi", 0)
        if prev_oi > 0 and delta / prev_oi > _SURGE_THRESH and delta > 10000:
            surge_strikes.append({
                "strike": k,
                "delta":  delta,
                "pct":    delta / prev_oi * 100,
                "ltp":    curr["ltp"],
            })

    total_delta = abs(ce_delta) + abs(pe_delta) + 1
    ce_pct      = ce_delta / total_delta
    pe_pct      = pe_delta / total_delta
    net_pcr     = (pe_oi + 1) / (ce_oi + 1)

    # Direction
    if pe_delta > 0 and ce_delta <= 0:
        direction = "BULLISH"
        strength  = min(1.0, pe_pct)
    elif ce_delta > 0 and pe_delta <= 0:
        direction = "BEARISH"
        strength  = min(1.0, ce_pct)
    elif pe_delta > ce_delta * (1 + _DIR_FLIP_MIN_DELTA):
        direction = "BULLISH"
        strength  = (pe_delta - ce_delta) / total_delta
    elif ce_delta > pe_delta * (1 + _DIR_FLIP_MIN_DELTA):
        direction = "BEARISH"
        strength  = (ce_delta - pe_delta) / total_delta
    else:
        direction = "NEUTRAL"
        strength  = 0.0

    # Conviction levels
    if strength > 0.6:   conviction = "STRONG"
    elif strength > 0.3: conviction = "MODERATE"
    elif direction != "NEUTRAL": conviction = "WEAK"
    else:                conviction = ""

    return {
        "direction":   direction,
        "conviction":  conviction,
        "strength":    round(strength, 3),
        "ce_delta":    ce_delta,
        "pe_delta":    pe_delta,
        "ce_oi":       ce_oi,
        "pe_oi":       pe_oi,
        "net_pcr":     round(net_pcr, 2),
        "surge":       sorted(surge_strikes, key=lambda x: -x["delta"])[:3],
    }


class OITracker:
    """
    Real-time 5-minute OI flow tracker.
    Defines market direction from OI delta every bar.
    """

    def __init__(self, alerts=None) -> None:
        self.alerts           = alerts
        self._snaps:          Dict[str, List] = {}   # symbol → [(ts, strikes, spot)]
        self._directions:     Dict[str, List] = {}   # symbol → [(ts, direction_dict)]
        self._last_direction: Dict[str, str]  = {}   # symbol → last direction
        self._lock            = threading.Lock()
        self._thread:         Optional[threading.Thread] = None
        self._alert_sent:     Dict[str, bool] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if _STATE_FILE.exists():
                saved = json.loads(_STATE_FILE.read_text())
                if saved.get("date") == date.today().isoformat():
                    self._snaps      = saved.get("snaps", {})
                    self._directions = saved.get("directions", {})
                    self._last_direction = saved.get("last_dir", {})
        except Exception:
            pass

    def _save(self) -> None:
        try:
            _STATE_FILE.write_text(json.dumps({
                "date":       date.today().isoformat(),
                "snaps":      self._snaps,
                "directions": self._directions,
                "last_dir":   self._last_direction,
            }))
        except Exception:
            pass

    # ── Background loop ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="OITracker5Min"
        )
        self._thread.start()
        logger.info("OITracker 5-min started")

    def _loop(self) -> None:
        last_bar       = -1
        alert_schedule = {"10:30": False, "12:30": False, "14:30": False, "15:15": False}

        while True:
            try:
                now = datetime.now()
                if not (dtime(9, 14) <= now.time() <= dtime(15, 32)):
                    time.sleep(60)
                    continue

                # 5-min bar (same as trading engine)
                bar_id = (now.hour * 60 + now.minute) // 5
                if bar_id != last_bar:
                    last_bar = bar_id
                    for sym in ["NIFTY", "BANKNIFTY"]:
                        self._refresh(sym)

                # Scheduled summary alerts
                ts_key = now.strftime("%H:%M")
                for at, sent in alert_schedule.items():
                    if not sent and ts_key == at:
                        alert_schedule[at] = True
                        self._send_summary_alert("NIFTY")

            except Exception as e:
                logger.debug("OITracker loop: %s", e)
            time.sleep(20)   # check every 20s to catch 5-min boundary

    def _refresh(self, symbol: str) -> None:
        """Fetch, compute direction, detect changes."""
        try:
            raw = _fetch_chain(symbol)
            if not raw:
                return
            spot, strikes_now = _parse_chain(raw)
            ts = datetime.now().strftime("%H:%M")

            with self._lock:
                # Previous snapshot
                prev_snaps  = self._snaps.get(symbol, [])
                strikes_prev = prev_snaps[-1][1] if prev_snaps else {}

                # Direction from 5-min delta
                direction   = _compute_direction(strikes_now, strikes_prev, spot)

                # Store snapshot
                if symbol not in self._snaps:
                    self._snaps[symbol] = []
                self._snaps[symbol].append((ts, strikes_now, spot))
                self._snaps[symbol] = self._snaps[symbol][-_MAX_SNAPS:]

                # Store direction
                if symbol not in self._directions:
                    self._directions[symbol] = []
                self._directions[symbol].append((ts, direction, spot))
                self._directions[symbol] = self._directions[symbol][-_MAX_SNAPS:]

                prev_dir = self._last_direction.get(symbol, "NEUTRAL")
                curr_dir = direction["direction"]
                self._last_direction[symbol] = curr_dir

            self._save()
            logger.debug("OI 5min %s %s → %s (str=%.2f)",
                         symbol, ts, direction["direction"], direction["strength"])

            # ── Event-driven alerts ───────────────────────────────────────
            # 1. Direction flip
            if (prev_dir != curr_dir and curr_dir != "NEUTRAL"
                    and direction["conviction"] in ("STRONG", "MODERATE")):
                self._alert_direction_flip(symbol, prev_dir, direction, spot, ts)

            # 2. Surge in single strike
            if direction["surge"]:
                self._alert_surge(symbol, direction["surge"], spot, ts)

        except Exception as e:
            logger.debug("OI refresh %s: %s", symbol, e)

    def _alert_direction_flip(
        self, sym: str, prev: str, d: dict, spot: float, ts: str,
    ) -> None:
        if not self.alerts:
            return
        curr     = d["direction"]
        conv     = d["conviction"]
        icon     = "📈" if curr == "BULLISH" else "📉"
        pi       = "🟢" if curr == "BULLISH" else "🔴"
        ce_d     = d["ce_delta"]
        pe_d     = d["pe_delta"]

        def _k(v): return f"{abs(v)/1000:.0f}K" if abs(v) >= 1000 else str(int(abs(v)))

        lines = [
            f"{icon} <b>OI DIRECTION FLIP: {curr}</b>  [{conv}]",
            f"  {sym}  ₹{spot:,.0f}  {ts}",
            f"  Was: {prev}  →  Now: {curr}",
            f"  CE delta: {'↑' if ce_d>0 else '↓'}{_k(ce_d)}  "
            f"PE delta: {'↑' if pe_d>0 else '↓'}{_k(pe_d)}",
            f"  PCR: {d['net_pcr']:.2f}",
        ]
        if curr == "BULLISH":
            lines.append(f"  💡 Put writing dominant — supports bullish move")
        else:
            lines.append(f"  💡 Call writing dominant — caps upside, watch {sym}")

        self.alerts.send(
            "\n".join(lines),
            dedup_key=f"oi_flip:{sym}:{curr}:{ts}",
            dedup_cooldown_override=300,
        )

    def _alert_surge(self, sym: str, surges: list, spot: float, ts: str) -> None:
        if not self.alerts:
            return
        lines = [f"⚡ <b>OI SURGE DETECTED</b>  {sym}  {ts}"]
        for s in surges[:2]:
            side = "CE (bearish)" if s["strike"].endswith("CE") else "PE (bullish)"
            lines.append(
                f"  {s['strike']}  +{s['pct']:.0f}% OI  "
                f"LTP ₹{s['ltp']:.0f}  → {side}"
            )
        lines.append(f"  ₹{spot:,.0f}")
        self.alerts.send(
            "\n".join(lines),
            dedup_key=f"oi_surge:{sym}:{'_'.join(s['strike'] for s in surges[:2])}:{ts}",
            dedup_cooldown_override=600,
        )

    def _send_summary_alert(self, sym: str) -> None:
        if not self.alerts:
            return
        text = self.format_trend(sym, bars=12)  # last 12 bars = 1 hour
        if text:
            self.alerts.send(
                text,
                dedup_key=f"oi_summary:{sym}:{datetime.now().strftime('%H%M')}",
                dedup_cooldown_override=1500,
            )

    # ── Public: format trend ──────────────────────────────────────────────
    def format_trend(self, symbol: str = "NIFTY", bars: int = 78) -> str:
        """
        TradingT-style OI trend table.
        bars = how many 5-min bars to show (default: all day).
        """
        with self._lock:
            snaps  = list(self._snaps.get(symbol, []))[-bars:]
            dirs   = list(self._directions.get(symbol, []))[-bars:]

        if len(snaps) < 2:
            return (f"⏳ <b>{symbol} OI TREND</b>\n"
                    f"  Building data... ({len(snaps)}/2 snapshots)\n"
                    f"  Refreshes every 5 min from 9:15 AM")

        first_ts, first_strikes, first_spot = snaps[0]
        now_ts,   now_strikes,   now_spot   = snaps[-1]

        # Aggregate OI across all snapshots to find top 6 active strikes
        activity: Dict[str, float] = defaultdict(float)
        for _, strikes, _ in snaps:
            for k, d in strikes.items():
                activity[k] += d.get("oi", 0)
        top6 = sorted(activity, key=lambda x: -activity[x])[:6]

        def _k(v): return (f"{v/1_000_000:.1f}M" if abs(v) >= 1_000_000
                           else f"{v/1000:.0f}K" if abs(v) >= 1000 else str(int(v)))

        # ── Direction history (last 6 bars = 30 min) ───────────────────────
        dir_icons = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪"}
        recent_dirs = [d["direction"] for _, d, _ in dirs[-6:]]
        dir_bar     = " ".join(dir_icons.get(d, "⚪") for d in recent_dirs)

        # Current direction
        curr_dir_data = dirs[-1][1] if dirs else {}
        curr_dir  = curr_dir_data.get("direction", "NEUTRAL")
        conv      = curr_dir_data.get("conviction", "")
        pcr_now   = curr_dir_data.get("net_pcr", 0)
        dir_icon  = "📈" if curr_dir == "BULLISH" else "📉" if curr_dir == "BEARISH" else "⚖️"

        spot_chg   = now_spot - first_spot
        spot_icon  = "🟢" if spot_chg >= 0 else "🔴"

        L = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  📊 <b>{symbol} OI TREND  {now_ts}</b>",
            f"  ₹{now_spot:,.0f}  {spot_icon}{spot_chg:+.0f}  [{first_ts}→{now_ts}]",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"<b>🧭 MARKET DIRECTION  {dir_icon} {curr_dir} [{conv}]</b>",
            f"  PCR: {pcr_now:.2f}   Last 30 min: {dir_bar}",
            f"  CE added: {_k(curr_dir_data.get('ce_delta',0))}   "
            f"PE added: {_k(curr_dir_data.get('pe_delta',0))}",
            "",
        ]

        # ── Strike trend table ─────────────────────────────────────────────
        L.append(f"<b>Strike      9:15    Now    +/-   Chart</b>")
        L.append(f"{'─'*46}")

        for strike in top6:
            oi_first = first_strikes.get(strike, {}).get("oi", 0)
            oi_now   = now_strikes.get(strike, {}).get("oi", 0)
            delta    = oi_now - oi_first
            pct      = delta / max(oi_first, 1) * 100

            chg_icon = "🔺" if delta > oi_first * 0.2 else "🔻" if delta < -oi_first * 0.2 else "─"
            spark    = self._spark(snaps, strike)

            L.append(
                f"{strike:<10}  {_k(oi_first):>6}  {_k(oi_now):>6}  "
                f"{'+' if delta>=0 else ''}{_k(delta):<6}{chg_icon}  {spark}"
            )

        # ── Interpretation ─────────────────────────────────────────────────
        L += ["", f"<b>💡 INTERPRETATION</b>"]

        # Most active CE and PE
        top_ce = sorted([t for t in top6 if t.endswith("CE")],
                        key=lambda x: -(now_strikes.get(x,{}).get("oi",0)))[:2]
        top_pe = sorted([t for t in top6 if t.endswith("PE")],
                        key=lambda x: -(now_strikes.get(x,{}).get("oi",0)))[:2]

        if top_ce:
            ce_str = "  ".join(top_ce)
            L.append(f"  🔴 Call wall (resistance): {ce_str}")
        if top_pe:
            pe_str = "  ".join(top_pe)
            L.append(f"  🟢 Put floor (support):    {pe_str}")

        # Direction narrative
        if curr_dir == "BULLISH":
            L.append(f"  📈 Put writers are active — market expects support to hold")
            L.append(f"  → Favour: CE Buy above call wall  /  PE Sell at support")
        elif curr_dir == "BEARISH":
            L.append(f"  📉 Call writers are dominant — resistance is stiff")
            L.append(f"  → Favour: PE Buy below put floor  /  CE Sell at resistance")
        else:
            L.append(f"  ⚖️ No clear OI bias — wait for a side to dominate")

        # Surge/reversal check
        for strike in top6:
            snap_ois = [s[1].get(strike, {}).get("oi", 0) for s in snaps]
            if len(snap_ois) >= 6:
                peak_idx = snap_ois.index(max(snap_ois))
                if peak_idx < len(snap_ois) - 3 and snap_ois[-1] < max(snap_ois) * 0.75:
                    L.append(f"  ⚠️ {strike} peaked at {_k(max(snap_ois))}, now unwinding → reversal signal")

        # Bar-by-bar direction count
        bull_bars = sum(1 for _, d, _ in dirs if d.get("direction") == "BULLISH")
        bear_bars = sum(1 for _, d, _ in dirs if d.get("direction") == "BEARISH")
        neut_bars = len(dirs) - bull_bars - bear_bars
        L += [
            "",
            f"<b>📊 SESSION SCORE  ({len(dirs)} bars)</b>",
            f"  🟢 Bullish: {bull_bars} bars  "
            f"🔴 Bearish: {bear_bars} bars  "
            f"⚪ Neutral: {neut_bars} bars",
            f"  Win: {'📈 BULLS' if bull_bars > bear_bars else '📉 BEARS' if bear_bars > bull_bars else '⚖️ TIED'}",
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  📱 /oitrend BANKNIFTY  /oi snapshot",
            f"🕐 {now_ts}",
        ]
        return "\n".join(L)

    def _spark(self, snaps: list, strike: str) -> str:
        """ASCII sparkline for a strike's OI over the session."""
        vals = [s[1].get(strike, {}).get("oi", 0) for s in snaps]
        if not vals or max(vals) == 0:
            return "─" * 8
        mx = max(vals)
        step = max(1, len(vals) // 8)
        sampled = [vals[i] for i in range(0, len(vals), step)][:8]
        return "".join(_BLOCKS[min(7, int(v / mx * 7))] for v in sampled).ljust(8)

    def get_current_direction(self, symbol: str = "NIFTY") -> Optional[dict]:
        """Used by signal_engine to get current OI direction as a score modifier."""
        with self._lock:
            dirs = self._directions.get(symbol, [])
            return dirs[-1][1] if dirs else None

    def get_snapshot_now(self, symbol: str) -> str:
        """Instant live snapshot (no history needed)."""
        try:
            raw = _fetch_chain(symbol)
            if not raw:
                return "❌ Could not fetch option chain"
            spot, strikes = _parse_chain(raw)
            top8 = sorted(strikes.items(), key=lambda x: -x[1]["oi"])[:8]
            mx   = max(d["oi"] for _, d in top8) if top8 else 1
            ts   = datetime.now().strftime("%H:%M")
            L    = [
                f"📸 <b>{symbol} LIVE OI</b>  {ts}",
                f"  Spot: ₹{spot:,.0f}",
                f"{'─'*38}",
                f"{'Strike':<10} {'OI':>7}  Bar",
            ]
            for k, d in top8:
                bar = "█" * int(d["oi"] / mx * 10) + "░" * (10 - int(d["oi"] / mx * 10))
                chg_arrow = "↑" if d["oi_chg"] > 0 else "↓" if d["oi_chg"] < 0 else "─"
                L.append(f"{k:<10} {d['oi']/1000:>6.0f}K {chg_arrow}  {bar}")
            L += [f"{'─'*38}", f"  /oitrend for intraday trend", f"🕐 {ts}"]
            return "\n".join(L)
        except Exception as e:
            return f"❌ Snapshot error: {str(e)[:50]}"


# ── Singleton ──────────────────────────────────────────────────────────────────
_tracker: Optional[OITracker] = None

def get_oi_tracker(alerts=None) -> OITracker:
    global _tracker
    if _tracker is None:
        _tracker = OITracker(alerts=alerts)
    return _tracker
