#!/usr/bin/env python3
"""
nifty_scalper_bot.py — standalone Telegram bot for NIFTY option scalp SIGNALS.

SIGNAL-ONLY: this bot NEVER places orders. It analyses, explains, and sends a
fully-specified trade card (entry / SL / target / reason) to Telegram. Execution
stays with the human (or the main engine, separately, in PAPER mode).

⚠️ The strategy below is UNVALIDATED — no out-of-sample backtest proves it
profitable after costs. Treat every card as a study signal, not advice.

Strategy: "NIFTY Scalp v1" (intraday long-options momentum)
  Direction engine (needs >= 3 of 5, on Angel 1m/5m spot candles):
    1. VWAP side       — 5m close detached >= 0.05% from session VWAP
    2. EMA stack       — EMA9 vs EMA21 on 5m agrees with direction
    3. RSI band        — 5m RSI14 in 55-75 (long) / 25-45 (short)
    4. Spike trigger   — last 1m bar range > 1.8x ATR14(1m) AND vol > 2x avg20
    5. OI flow         — oi_tracker direction (PE writing=bullish, CE=bearish)
  Instrument selection:
    - Strike:   score>=4 → ATM;  score==3 → 1 strike ITM (delta ~0.6, lower
                theta %/₹). OTM is never selected for scalps.
    - Expiry:   nearest weekly (NIFTY = Thursday). On expiry day itself only
                the 12:30-14:30 "hero-zero" window is allowed, flagged HIGH
                RISK (gamma max, theta max); otherwise next week's contract.
  Option-level gates (each can veto):
    - IV crush  — Black-Scholes IV from live premium; reject IV > 1.5x HV30
    - Theta     — BS theta/day; reject if burn > 5%% of premium per trading
                  hour UNLESS the spike trigger fired (fast in-and-out)
    - Liquidity — premium >= ₹20 (sub-₹20 scalps die to spread)
  Risk plan on every card:
    - Entry zone = LTP … LTP+0.4%%   - SL = entry − 30%% of premium
    - Target = entry + 45%% (1.5R)   - Time-stop 20 min  - 1%% capital risk lots

Commands: /scan /auto on|off /report /status /help
Env:  SCALPER_BOT_TOKEN (separate bot, falls back to TELEGRAM_BOT_TOKEN),
      SCALPER_CHAT_ID (falls back to TELEGRAM_CHAT_ID),
      API_KEY / CLIENT_ID / PASSWORD / TOTP_SECRET (Angel One, same as main).

Run:  python3 nifty_scalper_bot.py            # telegram long-poll service
      python3 nifty_scalper_bot.py --once     # one scan, print + send
      python3 nifty_scalper_bot.py --selftest # offline, synthetic data
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
logger = logging.getLogger("nifty_scalper")

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("SCALPER_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("SCALPER_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
RISK_PCT = float(os.getenv("SCALPER_RISK_PCT", "0.01"))
SIGNAL_LOG = Path("scalper_signals.json")
OI_STATE = Path("oi_tracker_state.json")
STRIKE_STEP = 50
SESSION_HOURS = 6.25                      # 09:15-15:30
RISK_FREE = 0.065

MIN_CONFLUENCE = 3                        # of 5 direction checks
VWAP_DETACH = 0.0005
SPIKE_RANGE_X = 1.8
SPIKE_VOL_X = 2.0
IV_HV_MULT = 1.5
MAX_THETA_PCT_HR = 0.05
MIN_PREMIUM = 20.0
SL_PCT = 0.30
TGT_PCT = 0.45                            # = 1.5R on the 30% SL
TIME_STOP_MIN = 20


# ── Black-Scholes helpers ──────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def bs_price(spot, strike, t_years, vol, is_call, r=RISK_FREE) -> float:
    if t_years <= 0 or vol <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t_years) * _norm_cdf(d2)
    return strike * math.exp(-r * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_implied_vol(price, spot, strike, t_years, is_call) -> Optional[float]:
    if min(price, spot, strike, t_years) <= 0:
        return None
    lo, hi = 1e-4, 5.0
    if not (bs_price(spot, strike, t_years, lo, is_call) <= price
            <= bs_price(spot, strike, t_years, hi, is_call)):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t_years, mid, is_call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bs_theta_per_day(spot, strike, t_years, vol, is_call, r=RISK_FREE) -> float:
    """Negative number: premium decay per calendar day."""
    if t_years <= 0 or vol <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * t_years) / (vol * math.sqrt(t_years))
    d2 = d1 - vol * math.sqrt(t_years)
    term1 = -(spot * _norm_pdf(d1) * vol) / (2 * math.sqrt(t_years))
    if is_call:
        term2 = -r * strike * math.exp(-r * t_years) * _norm_cdf(d2)
    else:
        term2 = r * strike * math.exp(-r * t_years) * _norm_cdf(-d2)
    return (term1 + term2) / 365.0


def hv30(daily_close: pd.Series) -> Optional[float]:
    if daily_close is None or len(daily_close) < 31:
        return None
    r = np.log(daily_close.iloc[-31:] / daily_close.iloc[-31:].shift(1)).dropna()
    return float(r.std(ddof=1) * math.sqrt(252)) if len(r) >= 20 else None


# ── Indicators (self-contained) ────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi14(close: pd.Series) -> float:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = g.iloc[-1] / max(l.iloc[-1], 1e-9)
    return 100 - 100 / (1 + rs)


def session_vwap(df: pd.DataFrame) -> float:
    today = df.index[-1].normalize()
    d = df[df.index >= today]
    if d.empty or d["volume"].sum() <= 0:
        d = df.tail(75)
    tp = (d["high"] + d["low"] + d["close"]) / 3
    vol = d["volume"].replace(0, np.nan).fillna(1.0)
    return float((tp * vol).sum() / vol.sum())


def atr(df: pd.DataFrame, n: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


# ── Expiry / contract selection ────────────────────────────────────────────────

def next_weekly_expiry(today: Optional[date] = None) -> date:
    """Nearest NIFTY weekly expiry (Thursday, weekday 3)."""
    today = today or date.today()
    days = (3 - today.weekday()) % 7
    return today + timedelta(days=days)


def angel_option_symbol(strike: int, opt: str, expiry: date) -> str:
    """Angel NFO symbol: NIFTY18JUN2624500CE."""
    return f"NIFTY{expiry.strftime('%d%b%y').upper()}{strike}{opt}"


def select_strike(spot: float, direction: str, score: int) -> tuple[int, str]:
    atm = int(round(spot / STRIKE_STEP) * STRIKE_STEP)
    opt = "CE" if direction == "LONG" else "PE"
    if score >= 4:
        return atm, opt
    itm = atm - STRIKE_STEP if direction == "LONG" else atm + STRIKE_STEP
    return itm, opt


# ── OI flow (read-only from oi_tracker state) ──────────────────────────────────

def oi_flow_direction() -> tuple[Optional[str], str]:
    """Returns (BULLISH|BEARISH|NEUTRAL|None, detail)."""
    try:
        st = json.loads(OI_STATE.read_text())
        if st.get("date") != date.today().isoformat():
            return None, f"OI state stale (date={st.get('date')})"
        d = st.get("last_dir", {}).get("NIFTY")
        return (d or None), f"oi_tracker last_dir={d}"
    except Exception as e:
        return None, f"OI state unavailable ({e})"


# ── Signal card ────────────────────────────────────────────────────────────────

@dataclass
class ScalpSignal:
    ts: str
    direction: str                 # LONG / SHORT (on the index)
    symbol: str                    # Angel NFO option symbol
    strike: int
    opt_type: str                  # CE / PE
    expiry: str
    spot: float
    premium: float
    entry_min: float
    entry_max: float
    stop_loss: float
    target: float
    lots: int
    lot_size: int
    iv: Optional[float]
    hv: Optional[float]
    theta_day: float
    theta_pct_hr: float
    score: int
    reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)

    def telegram_card(self) -> str:
        opt_word = "CALL" if self.opt_type == "CE" else "PUT"
        lines = [
            f"⚡ <b>NIFTY SCALP — BUY {self.strike} {self.opt_type} ({opt_word})</b>",
            f"📅 Expiry: <b>{self.expiry}</b>  |  Spot: {self.spot:.1f}",
            f"🎯 Confluence: <b>{self.score}/5</b>",
            "",
            f"🟢 <b>Entry:</b> ₹{self.entry_min:.2f} – ₹{self.entry_max:.2f}",
            f"🛑 <b>Stop-loss:</b> ₹{self.stop_loss:.2f}  (−{SL_PCT:.0%})",
            f"🏁 <b>Target:</b> ₹{self.target:.2f}  (+{TGT_PCT:.0%}, 1.5R)",
            f"⏱ Time-stop: exit after {TIME_STOP_MIN} min either way",
            f"📦 Size @1% risk: <b>{self.lots} lot(s)</b> × {self.lot_size}",
            "",
            "📋 <b>Reasons:</b>",
            *[f"  • {r}" for r in self.reasons],
            "",
            f"📐 IV {self.iv:.1%} vs HV30 {self.hv:.1%}" if self.iv and self.hv
            else "📐 IV/HV: n/a",
            f"⏳ Theta ₹{self.theta_day:.2f}/day "
            f"(~{self.theta_pct_hr:.1%} of premium per hour)",
        ]
        if self.flags:
            lines += ["", "⚠️ " + " | ".join(self.flags)]
        lines += ["", "🧪 PAPER / study signal — strategy is UNVALIDATED"]
        return "\n".join(lines)


# ── The engine ─────────────────────────────────────────────────────────────────

class ScalpEngine:
    """Builds at most one signal per scan; every rejection is logged with reason."""

    def __init__(self, broker=None, equity: float = 0.0, lot_size: int = 75,
                 now_fn=datetime.now):
        self.broker = broker
        self.equity = equity
        self.lot_size = lot_size
        self.now_fn = now_fn
        self.last_reject = ""

    # -- data ---------------------------------------------------------------

    def _candles(self, interval: str, days: int) -> Optional[pd.DataFrame]:
        if self.broker is None:
            return None
        end = self.now_fn()
        start = end - timedelta(days=days)
        fmt = "%Y-%m-%d %H:%M"
        try:
            return self.broker.get_historical_data(
                symbol="NIFTY", interval=interval,
                from_date=start.strftime(fmt), to_date=end.strftime(fmt),
                exchange="NSE")
        except Exception as e:
            logger.warning("candles %s: %s", interval, e)
            return None

    # -- main ---------------------------------------------------------------

    def scan(self) -> Optional[ScalpSignal]:
        now = self.now_fn()
        sig = self._scan_inner(now)
        if sig is None:
            logger.info("scan: no signal — %s", self.last_reject)
        return sig

    def _reject(self, why: str) -> None:
        self.last_reject = why

    def _scan_inner(self, now: datetime) -> Optional[ScalpSignal]:
        # 1) time gates
        hm = now.hour * 60 + now.minute
        if not (9 * 60 + 20 <= hm <= 15 * 60 + 15):
            return self._reject("outside market window 09:20-15:15")
        expiry = next_weekly_expiry(now.date())
        dte = (expiry - now.date()).days
        hero_zero = False
        if dte == 0:
            if not (12 * 60 + 30 <= hm <= 14 * 60 + 30):
                return self._reject(
                    "expiry day: only 12:30-14:30 hero-zero window allowed")
            hero_zero = True
        elif hm > 14 * 60 + 30:
            return self._reject("after 14:30 non-expiry: theta + close risk")

        # 2) spot data
        m5 = self._candles("5m", 7)
        m1 = self._candles("1m", 3)
        daily = self._candles("1d", 80)
        if m5 is None or len(m5) < 40 or m1 is None or len(m1) < 40:
            return self._reject("insufficient 1m/5m NIFTY candles from Angel")
        spot = float(m5["close"].iloc[-1])

        # 3) direction confluence
        reasons, long_votes, short_votes = [], 0, 0
        vwap = session_vwap(m5)
        if spot > vwap * (1 + VWAP_DETACH):
            long_votes += 1; reasons.append(f"price above VWAP ({spot:.0f} > {vwap:.0f})")
        elif spot < vwap * (1 - VWAP_DETACH):
            short_votes += 1; reasons.append(f"price below VWAP ({spot:.0f} < {vwap:.0f})")

        e9, e21 = ema(m5["close"], 9).iloc[-1], ema(m5["close"], 21).iloc[-1]
        if e9 > e21:
            long_votes += 1; reasons.append(f"EMA9 > EMA21 on 5m ({e9:.0f}/{e21:.0f})")
        elif e9 < e21:
            short_votes += 1; reasons.append(f"EMA9 < EMA21 on 5m ({e9:.0f}/{e21:.0f})")

        r = rsi14(m5["close"])
        if 55 <= r <= 75:
            long_votes += 1; reasons.append(f"RSI14(5m) {r:.0f} in bullish band 55-75")
        elif 25 <= r <= 45:
            short_votes += 1; reasons.append(f"RSI14(5m) {r:.0f} in bearish band 25-45")

        bar = m1.iloc[-1]
        rng, a1 = float(bar["high"] - bar["low"]), atr(m1.iloc[:-1])
        vol_avg = float(m1["volume"].iloc[-21:-1].mean())
        spike = rng > SPIKE_RANGE_X * a1 and float(bar["volume"]) > SPIKE_VOL_X * max(vol_avg, 1)
        if spike:
            up = float(bar["close"]) > float(bar["open"])
            if up:
                long_votes += 1
            else:
                short_votes += 1
            reasons.append(
                f"1m spike: range {rng:.1f} > {SPIKE_RANGE_X}xATR {a1:.1f}, "
                f"vol {bar['volume']:.0f} > {SPIKE_VOL_X}x avg ({'up' if up else 'down'} bar)")

        oi_dir, oi_detail = oi_flow_direction()
        if oi_dir == "BULLISH":
            long_votes += 1; reasons.append(f"OI flow bullish ({oi_detail})")
        elif oi_dir == "BEARISH":
            short_votes += 1; reasons.append(f"OI flow bearish ({oi_detail})")
        else:
            reasons.append(f"OI flow neutral/unavailable: {oi_detail}")

        score, direction = max((long_votes, "LONG"), (short_votes, "SHORT"))
        if long_votes == short_votes or score < MIN_CONFLUENCE:
            return self._reject(
                f"confluence too weak (long={long_votes} short={short_votes}, "
                f"need >= {MIN_CONFLUENCE})")
        reasons = [x for x in reasons if x]

        # 4) contract selection
        strike, opt = select_strike(spot, direction, score)
        symbol = angel_option_symbol(strike, opt, expiry)
        premium = None
        if self.broker is not None:
            try:
                premium = self.broker.get_ltp(symbol, exchange="NFO")
            except Exception as e:
                logger.warning("option LTP %s: %s", symbol, e)
        if not premium or premium <= 0:
            return self._reject(f"no live premium for {symbol}")
        if premium < MIN_PREMIUM:
            return self._reject(f"premium ₹{premium:.2f} < ₹{MIN_PREMIUM:.0f} "
                                "(spread eats sub-₹20 scalps)")

        # 5) IV / theta gates
        t_years = max(dte, 0.25) / 365.0          # intraday floor on expiry day
        iv = bs_implied_vol(premium, spot, strike, t_years, opt == "CE")
        hv = hv30(daily["close"]) if daily is not None else None
        if iv is not None and hv is not None and iv > hv * IV_HV_MULT:
            return self._reject(f"IV crush risk: IV {iv:.1%} > {IV_HV_MULT}x HV30 {hv:.1%}")
        theta_day = bs_theta_per_day(spot, strike, t_years, iv or (hv or 0.15), opt == "CE")
        theta_pct_hr = abs(theta_day) / premium / SESSION_HOURS
        if theta_pct_hr > MAX_THETA_PCT_HR and not spike:
            return self._reject(
                f"theta burn {theta_pct_hr:.1%}/hr > {MAX_THETA_PCT_HR:.0%} "
                "and no spike trigger to justify it")

        # 6) risk plan + sizing
        entry_min, entry_max = premium, round(premium * 1.004, 2)
        stop = round(premium * (1 - SL_PCT), 2)
        target = round(premium * (1 + TGT_PCT), 2)
        lots = 0
        if self.equity > 0:
            risk_per_lot = (entry_max - stop) * self.lot_size
            lots = int((self.equity * RISK_PCT) // max(risk_per_lot, 1e-9))
        if self.equity > 0 and lots <= 0:
            return self._reject(
                f"1% of equity (₹{self.equity * RISK_PCT:,.0f}) can't cover one "
                f"lot's risk (₹{(entry_max - stop) * self.lot_size:,.0f})")

        flags = []
        if hero_zero:
            flags.append("HERO-ZERO (0DTE): max gamma AND max theta — half size, "
                         "hard time-stop")
        if iv is None:
            flags.append("IV could not be computed — premium may be off-fair")
        if oi_dir is None:
            flags.append("OI feed unavailable — scored without OI vote")

        return ScalpSignal(
            ts=now.isoformat(timespec="seconds"), direction=direction,
            symbol=symbol, strike=strike, opt_type=opt,
            expiry=expiry.strftime("%d %b %Y"), spot=spot, premium=premium,
            entry_min=entry_min, entry_max=entry_max, stop_loss=stop,
            target=target, lots=lots, lot_size=self.lot_size,
            iv=iv, hv=hv, theta_day=theta_day, theta_pct_hr=theta_pct_hr,
            score=score, reasons=reasons, flags=flags)


# ── Signal log + daily report ──────────────────────────────────────────────────

def log_signal(sig: ScalpSignal) -> None:
    data = []
    if SIGNAL_LOG.exists():
        try:
            data = json.loads(SIGNAL_LOG.read_text())
        except Exception:
            data = []
    data.append(asdict(sig))
    SIGNAL_LOG.write_text(json.dumps(data[-500:], indent=1))


def daily_report() -> str:
    today = date.today().isoformat()
    rows: List[Dict[str, Any]] = []
    if SIGNAL_LOG.exists():
        try:
            rows = [r for r in json.loads(SIGNAL_LOG.read_text())
                    if str(r.get("ts", "")).startswith(today)]
        except Exception:
            rows = []
    oi_dir, oi_detail = oi_flow_direction()
    lines = [
        f"📊 <b>NIFTY SCALPER — DAILY REPORT {today}</b>",
        f"Signals today: <b>{len(rows)}</b>",
    ]
    if rows:
        longs = sum(1 for r in rows if r["direction"] == "LONG")
        ivs = [r["iv"] for r in rows if r.get("iv")]
        lines += [
            f"  Long {longs} / Short {len(rows) - longs}",
            f"  Strikes: {', '.join(str(r['strike']) + r['opt_type'] for r in rows[-8:])}",
            f"  Avg confluence: {sum(r['score'] for r in rows) / len(rows):.1f}/5",
        ]
        if ivs:
            lines.append(f"  IV range: {min(ivs):.1%} – {max(ivs):.1%}")
        last = rows[-1]
        lines += ["", "🕐 <b>Last signal:</b>",
                  f"  {last['ts'][11:]} BUY {last['strike']}{last['opt_type']} "
                  f"@ ₹{last['entry_min']:.2f} SL ₹{last['stop_loss']:.2f} "
                  f"TGT ₹{last['target']:.2f}"]
    lines += ["", f"OI flow now: {oi_dir or 'n/a'} ({oi_detail})",
              "🧪 Signals are PAPER/study only — edge UNVALIDATED"]
    return "\n".join(lines)


# ── Telegram plumbing (long-poll, no external bot lib) ─────────────────────────

class TelegramBot:
    def __init__(self, token: str, chat_id: str, engine: ScalpEngine):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.engine = engine
        self.offset = 0
        self.auto = False
        self._auto_thread: Optional[threading.Thread] = None
        self._last_card_key = ""

    def send(self, text: str) -> None:
        try:
            requests.post(f"{self.api}/sendMessage", timeout=10, json={
                "chat_id": self.chat_id, "text": text, "parse_mode": "HTML"})
        except Exception as e:
            logger.warning("telegram send: %s", e)

    # -- commands -------------------------------------------------------------

    def handle(self, text: str) -> None:
        cmd = text.strip().split()[0].lower().lstrip("/")
        arg = (text.strip().split() + [""])[1].lower()
        if cmd == "scan":
            sig = self.engine.scan()
            if sig:
                log_signal(sig)
                self.send(sig.telegram_card())
            else:
                self.send(f"🚫 No signal: {self.engine.last_reject}")
        elif cmd == "auto":
            self.auto = (arg != "off")
            self.send(f"🔁 Auto-scan {'ON (every 75s, market hours)' if self.auto else 'OFF'}")
        elif cmd == "report":
            self.send(daily_report())
        elif cmd == "status":
            self.send(f"✅ Scalper bot alive | auto={'on' if self.auto else 'off'} | "
                      f"equity ₹{self.engine.equity:,.0f} | lot {self.engine.lot_size} | "
                      f"last reject: {self.engine.last_reject or '—'}")
        elif cmd == "help":
            self.send("⚡ <b>NIFTY Scalper</b>\n"
                      "/scan — analyse now, send card if signal\n"
                      "/auto on|off — continuous scan (75s)\n"
                      "/report — detailed daily report\n"
                      "/status — health\n"
                      "All signals: entry, SL, target, reasons, IV/OI/theta. "
                      "Signal-only — never places orders.")

    # -- loops ------------------------------------------------------------------

    def _auto_loop(self) -> None:
        while True:
            try:
                if self.auto:
                    sig = self.engine.scan()
                    if sig:
                        key = f"{sig.direction}-{sig.strike}-{sig.ts[:15]}"  # 10-min bucket
                        if key != self._last_card_key:
                            self._last_card_key = key
                            log_signal(sig)
                            self.send(sig.telegram_card())
            except Exception:
                logger.exception("auto loop")
            time.sleep(75)

    def run(self) -> None:
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()
        self.send("⚡ NIFTY Scalper bot started (signal-only). /help for commands.")
        logger.info("long-polling…")
        while True:
            try:
                r = requests.get(f"{self.api}/getUpdates", timeout=40,
                                 params={"offset": self.offset + 1, "timeout": 30})
                for upd in r.json().get("result", []):
                    self.offset = max(self.offset, upd["update_id"])
                    msg = upd.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue          # only the configured chat may command
                    if msg.get("text", "").startswith("/"):
                        self.handle(msg["text"])
            except Exception as e:
                logger.warning("poll: %s", e)
                time.sleep(5)


# ── Wiring ─────────────────────────────────────────────────────────────────────

def build_engine() -> ScalpEngine:
    broker, equity, lot = None, 0.0, 75
    try:
        from angel import AngelOne
        broker = AngelOne(api_key=os.getenv("API_KEY", ""),
                          client_id=os.getenv("CLIENT_ID", ""),
                          password=os.getenv("PASSWORD", ""),
                          totp_secret=os.getenv("TOTP_SECRET", ""))
        broker.connect()
        equity = float(broker.get_balance(force_real=True) or 0)
    except Exception as e:
        logger.warning("Angel unavailable (%s) — scans will reject on data", e)
    try:
        from nse_master import NSEMaster
        lot = int(NSEMaster().get_lot_size("NIFTY") or 75)
    except Exception:
        pass
    if equity <= 0:
        logger.warning("live equity unavailable — lots will be 0 (fail-safe), "
                       "cards still carry entry/SL/target")
    return ScalpEngine(broker=broker, equity=equity, lot_size=lot)


# ── Offline self-test ──────────────────────────────────────────────────────────

def _selftest() -> int:
    class FakeBroker:
        def __init__(self):
            n = 90
            t5 = pd.date_range(datetime.now().replace(hour=9, minute=20, second=0,
                                                      microsecond=0), periods=n, freq="5min")
            up = 24000 + np.linspace(0, 120, n) + np.random.default_rng(3).normal(0, 4, n)
            self.m5 = pd.DataFrame({"open": up, "high": up + 8, "low": up - 8,
                                    "close": up, "volume": np.full(n, 250_000.0)}, index=t5)
            t1 = pd.date_range(t5[-1], periods=60, freq="1min")
            c1 = np.full(60, float(up[-1]))
            self.m1 = pd.DataFrame({"open": c1, "high": c1 + 3, "low": c1 - 3,
                                    "close": c1, "volume": np.full(60, 50_000.0)}, index=t1)
            # final 1m bar: range + volume spike, up bar
            self.m1.iloc[-1, self.m1.columns.get_indexer(["high"])[0]] = c1[-1] + 25
            self.m1.iloc[-1, self.m1.columns.get_indexer(["close"])[0]] = c1[-1] + 22
            self.m1.iloc[-1, self.m1.columns.get_indexer(["volume"])[0]] = 200_000.0
            d = pd.date_range(end=date.today() - timedelta(days=1), periods=60, freq="B")
            dc = 24000 * np.cumprod(1 + np.random.default_rng(5).normal(0.0004, 0.011, 60))
            self.daily = pd.DataFrame({"open": dc, "high": dc * 1.005, "low": dc * 0.995,
                                       "close": dc, "volume": np.full(60, 3e8)}, index=d)

        def get_historical_data(self, symbol, interval, from_date, to_date, exchange="NSE"):
            return {"5m": self.m5, "1m": self.m1, "1d": self.daily}.get(interval)

        def get_ltp(self, symbol, exchange=None):
            return 185.0    # plausible ATM weekly premium

    fixed_now = datetime.now().replace(hour=11, minute=5)
    eng = ScalpEngine(broker=FakeBroker(), equity=1_000_000, lot_size=75,
                      now_fn=lambda: fixed_now)
    sig = eng.scan()
    assert sig is not None, f"selftest: expected a signal, got reject: {eng.last_reject}"
    card = sig.telegram_card()
    print(card)
    assert sig.direction == "LONG" and sig.opt_type == "CE"
    assert sig.score >= MIN_CONFLUENCE
    assert sig.stop_loss < sig.entry_min < sig.target
    assert sig.lots >= 1
    assert "Entry" in card and "Stop-loss" in card and "Target" in card \
           and "Reasons" in card

    # gate checks: time window + weak confluence must reject
    eng2 = ScalpEngine(broker=FakeBroker(), equity=1_000_000, lot_size=75,
                       now_fn=lambda: fixed_now.replace(hour=15, minute=25))
    assert eng2.scan() is None and "outside market window" in eng2.last_reject

    print("\nSELFTEST PASSED — signal card complete, gates reject correctly")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    engine = build_engine()
    if "--once" in sys.argv:
        s = engine.scan()
        print(s.telegram_card() if s else f"No signal: {engine.last_reject}")
        sys.exit(0)
    if not BOT_TOKEN or not CHAT_ID:
        print("Set SCALPER_BOT_TOKEN (from @BotFather) and SCALPER_CHAT_ID in .env\n"
              "Falls back to TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID if unset.")
        sys.exit(1)
    TelegramBot(BOT_TOKEN, CHAT_ID, engine).run()
