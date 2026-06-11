"""
trade_guardian.py — Intelligent Trade Management Engine

The Trade Guardian sits alongside the existing system (never modifies it)
and manages trades that YOU place manually — whether options, futures, or stocks.

Core responsibilities:
  1. Register trades (from Telegram /in command or direct API)
  2. Compute initial SL / Target / Trailing SL (instrument-aware)
  3. Monitor prices every 5 seconds
  4. Run signal engine every 60 seconds to get live intelligence
  5. Apply FOMO / Fear / Greed guards
  6. Detect options spikes and alert
  7. Adjust SL/Target based on regime, VIX, news, GEX, skew
  8. Send actionable Telegram alerts

Does NOT touch:
  - main_autonomous.py or live_signal_engine.py (the bot's own trades)
  - manual_trade_tracker.py (the order-book detector — runs in parallel)
  - Any existing database tables
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_PATH   = Path("trade_guardian.yaml")
_DB_PATH     = "trade_guardian.db"
_STATE_FILE  = Path("trade_guardian_state.json")


# ─────────────────────────────────────────────────────────────────────────────
# Config loader (hot-reload on every cycle)
# ─────────────────────────────────────────────────────────────────────────────

def _load_cfg() -> Dict:
    """Load trade_guardian.yaml. Returns empty dict on error."""
    try:
        return yaml.safe_load(_YAML_PATH.read_text()) or {}
    except Exception as e:
        logger.warning("trade_guardian.yaml load failed: %s", e)
        return {}


def _cfg_get(path: str, default=None):
    """Dot-path accessor into the YAML config. e.g. 'options.initial_sl_pct'"""
    cfg = _load_cfg()
    parts = path.split(".")
    for p in parts:
        if not isinstance(cfg, dict):
            return default
        cfg = cfg.get(p, default)
    return cfg if cfg is not None else default


# NSE lot sizes — current index contracts, with stock lots as fallbacks.
# Override per-symbol in trade_guardian.yaml under lot_sizes: section.
_NSE_LOT_SIZES: Dict[str, int] = {
    # Index derivatives
    "NIFTY":       65,
    "BANKNIFTY":   30,
    "FINNIFTY":    60,
    "MIDCPNIFTY":  120,
    "SENSEX":      20,
    "BANKEX":      30,
    # Popular F&O stocks (lot sizes as of 2024 — verify at nseindia.com)
    "RELIANCE":    250,
    "TCS":         150,
    "INFY":        300,
    "HDFCBANK":    550,
    "ICICIBANK":   700,
    "SBIN":       1500,
    "AXISBANK":    625,
    "KOTAKBANK":   400,
    "LT":          175,
    "ITC":        1600,
    "HINDUNILVR":  300,
    "BAJFINANCE":   125,
    "MARUTI":       50,
    "TATAMOTORS":  1425,
    "WIPRO":       1500,
    "HCLTECH":      700,
}


def _get_lot_size(symbol: str, instrument: str = "OPTIONS") -> int:
    """
    Return lot size for a symbol.
    Checks trade_guardian.yaml lot_sizes section first, then built-in table.
    For STOCK instrument with no F&O, returns 1 (qty = raw shares).
    """
    sym = symbol.upper()
    # Check YAML override
    yaml_sizes = _load_cfg().get("lot_sizes", {})
    if sym in yaml_sizes:
        return int(yaml_sizes[sym])
    # Built-in table
    if sym in _NSE_LOT_SIZES:
        return _NSE_LOT_SIZES[sym]
    # Unknown stock — assume 1 (user enters shares directly)
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# Trade data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GuardedTrade:
    trade_id: str                    # unique: symbol+timestamp
    symbol: str                      # underlying (NIFTY, BANKNIFTY, RELIANCE...)
    instrument: str                  # OPTIONS / FUTURES / STOCK
    option_type: str                 # CE / PE / "" for non-options
    strike: float                    # 0 for futures/stocks
    expiry: str                      # "weekly" / "monthly" / "YYYY-MM-DD"
    side: str                        # BUY / SELL
    qty: int
    entry_price: float               # premium for options, price for others
    entry_time: str

    # Levels (computed by engine)
    stop_loss: float         = 0.0
    target_1: float          = 0.0
    target_2: float          = 0.0
    target_3: float          = 0.0
    trailing_sl: float       = 0.0
    breakeven_price: float   = 0.0

    # Live state
    current_price: float     = 0.0
    peak_price: float        = 0.0   # highest (long) / lowest (short) since entry
    pnl: float               = 0.0
    pnl_pct: float           = 0.0
    r_multiple: float        = 0.0   # current P&L in R units

    # State flags
    breakeven_activated: bool = False
    t1_hit: bool             = False
    t2_hit: bool             = False
    partial_booked: bool     = False
    target_extended: bool    = False
    status: str              = "OPEN"   # OPEN / CLOSED / PAUSED
    exit_price: float        = 0.0
    exit_time: str           = ""
    exit_reason: str         = ""

    # Intelligence
    regime: str              = ""
    signal_score: float      = 0.0
    signal_direction: str    = ""
    vix: float               = 0.0
    narrative: str           = ""
    last_signal_time: str    = ""
    target_extensions: int   = 0     # how many times target was extended

    # WOW Factors state (updated every 2 min)
    wow_score: float         = 0.0   # total WOW adjustment (−3 to +3)
    wow_verdict: str         = ""    # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    wow_reasons: str         = ""    # top WOW reasons joined as string
    wow_factors: str         = ""    # JSON of individual factor scores
    last_wow_time: str       = ""
    wow_tighten_applied: bool = False
    wow_extend_applied: bool  = False

    # Spike tracking (options only)
    price_5min_ago: float    = 0.0
    spike_alerted: bool      = False

    # Notes
    notes: str               = ""
    is_positional: bool      = False
    # Lot size: units per lot (NIFTY=25, BANKNIFTY=15, stocks=1 or F&O lot size)
    # P&L = (exit - entry) * qty * lot_size
    lot_size: int            = 1

    def risk_points(self) -> float:
        """Initial risk per unit in price terms (|entry - SL|)."""
        if self.stop_loss <= 0:
            return self.entry_price * 0.02
        return abs(self.entry_price - self.stop_loss)

    def total_risk_inr(self) -> float:
        """Total monetary risk for the entire position (entry to SL)."""
        return self.risk_points() * self.qty * self.lot_size

    def is_long(self) -> bool:
        return self.side == "BUY"

    def to_dict(self) -> Dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

def _init_db() -> None:
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guarded_trades (
            trade_id     TEXT PRIMARY KEY,
            symbol       TEXT,
            instrument   TEXT,
            option_type  TEXT,
            strike       REAL,
            expiry       TEXT,
            side         TEXT,
            qty          INTEGER,
            entry_price  REAL,
            entry_time   TEXT,
            stop_loss    REAL,
            target_1     REAL,
            target_2     REAL,
            target_3     REAL,
            regime       TEXT,
            signal_score REAL,
            status       TEXT DEFAULT 'OPEN',
            exit_price   REAL,
            exit_time    TEXT,
            exit_reason  TEXT,
            pnl          REAL,
            pnl_pct      REAL,
            notes        TEXT,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS guardian_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id     TEXT,
            event        TEXT,
            price        REAL,
            pnl          REAL,
            pnl_pct      REAL,
            detail       TEXT,
            ts           TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()


def _save_trade(trade: GuardedTrade) -> None:
    try:
        conn = sqlite3.connect(_DB_PATH)
        d = trade.to_dict()
        cols = ["trade_id","symbol","instrument","option_type","strike","expiry",
                "side","qty","entry_price","entry_time","stop_loss","target_1",
                "target_2","target_3","regime","signal_score","status",
                "exit_price","exit_time","exit_reason","pnl","pnl_pct","notes"]
        vals = [d.get(c) for c in cols]
        phs  = ",".join(["?" for _ in cols])
        upd  = ",".join([f"{c}=?" for c in cols if c != "trade_id"])
        conn.execute(
            f"INSERT OR REPLACE INTO guarded_trades ({','.join(cols)}) VALUES ({phs})",
            vals
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("_save_trade: %s", e)


def _log_event(trade_id: str, event: str, price: float,
               pnl: float, pnl_pct: float, detail: str) -> None:
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "INSERT INTO guardian_events (trade_id,event,price,pnl,pnl_pct,detail) VALUES (?,?,?,?,?,?)",
            (trade_id, event, price, pnl, pnl_pct, detail)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("_log_event: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Level calculator
# ─────────────────────────────────────────────────────────────────────────────

def _compute_levels(trade: GuardedTrade, cfg: Dict, regime: str, vix: float) -> GuardedTrade:
    """
    Compute initial SL, T1, T2, T3, trailing SL for a new trade.
    Uses instrument-specific rules from YAML config, adjusted for regime and VIX.
    """
    ep    = trade.entry_price
    side  = trade.side
    instr = trade.instrument.upper()

    # ── Regime multipliers ────────────────────────────────────────────────────
    reg_cfg = cfg.get("regime_adjustments", {}).get(regime, {})
    sl_mult = float(reg_cfg.get("sl_multiplier", 1.0))
    tgt_mult = float(reg_cfg.get("target_multiplier", 1.0))

    # ── VIX multipliers ───────────────────────────────────────────────────────
    for level in cfg.get("vix_adjustments", {}).get("levels", []):
        if float(vix) > float(level.get("vix_above", 999)):
            sl_mult  *= float(level.get("sl_multiplier", 1.0))
            tgt_mult *= float(level.get("target_multiplier", 1.0))
            break

    if instr == "OPTIONS":
        opt_cfg = cfg.get("options", {})
        sl_pct   = float(opt_cfg.get("initial_sl_pct", 30)) / 100
        t1_pct   = float(opt_cfg.get("target_1_pct",   50)) / 100
        t2_pct   = float(opt_cfg.get("target_2_pct",  100)) / 100
        t3_pct   = float(opt_cfg.get("target_3_pct",  150)) / 100

        # For options: BUY side always loses premium if SL hit
        # SELL side: different risk (assignment risk), but for premium sellers
        # we treat SL as premium rising against us
        if side == "BUY":
            trade.stop_loss    = round(ep * (1 - sl_pct * sl_mult), 2)
            trade.target_1     = round(ep * (1 + t1_pct * tgt_mult), 2)
            trade.target_2     = round(ep * (1 + t2_pct * tgt_mult), 2)
            trade.target_3     = round(ep * (1 + t3_pct * tgt_mult), 2)
            trade.breakeven_price = ep
        else:  # options writer (SELL) — SL is when premium rises against you
            trade.stop_loss    = round(ep * (1 + sl_pct * sl_mult), 2)
            trade.target_1     = round(ep * (1 - t1_pct * tgt_mult), 2)
            trade.target_2     = round(ep * (1 - t2_pct * tgt_mult), 2)
            trade.target_3     = round(ep * (1 - t3_pct * tgt_mult), 2)
            trade.breakeven_price = ep

    elif instr == "FUTURES":
        fut_cfg  = cfg.get("futures", {})
        # Get ATR from signal engine if available
        atr      = _get_atr(trade.symbol)
        sl_atr   = float(fut_cfg.get("initial_sl_atr_mult", 1.5)) * sl_mult
        t1_rr    = float(fut_cfg.get("target_1_rr", 1.5)) * tgt_mult
        t2_rr    = float(fut_cfg.get("target_2_rr", 3.0)) * tgt_mult
        t3_rr    = float(fut_cfg.get("target_3_rr", 5.0)) * tgt_mult

        risk = atr * sl_atr
        if side == "BUY":
            trade.stop_loss  = round(ep - risk, 2)
            trade.target_1   = round(ep + risk * t1_rr, 2)
            trade.target_2   = round(ep + risk * t2_rr, 2)
            trade.target_3   = round(ep + risk * t3_rr, 2)
        else:
            trade.stop_loss  = round(ep + risk, 2)
            trade.target_1   = round(ep - risk * t1_rr, 2)
            trade.target_2   = round(ep - risk * t2_rr, 2)
            trade.target_3   = round(ep - risk * t3_rr, 2)
        trade.breakeven_price = ep

    else:  # STOCK
        stk_cfg  = cfg.get("stocks", {})
        atr      = _get_atr(trade.symbol)
        sl_atr   = float(stk_cfg.get("initial_sl_atr_mult" if not trade.is_positional
                                     else "positional_sl_atr_mult", 1.5)) * sl_mult
        t1_rr    = float(stk_cfg.get("target_1_rr" if not trade.is_positional
                                     else "positional_target_1_rr", 2.0)) * tgt_mult
        t2_rr    = float(stk_cfg.get("target_2_rr", 4.0)) * tgt_mult
        t3_rr    = float(stk_cfg.get("target_3_rr", 6.0)) * tgt_mult

        risk = atr * sl_atr
        if side == "BUY":
            trade.stop_loss = round(ep - risk, 2)
            trade.target_1  = round(ep + risk * t1_rr, 2)
            trade.target_2  = round(ep + risk * t2_rr, 2)
            trade.target_3  = round(ep + risk * t3_rr, 2)
        else:
            trade.stop_loss = round(ep + risk, 2)
            trade.target_1  = round(ep - risk * t1_rr, 2)
            trade.target_2  = round(ep - risk * t2_rr, 2)
            trade.target_3  = round(ep - risk * t3_rr, 2)
        trade.breakeven_price = ep

    trade.trailing_sl = trade.stop_loss
    return trade


def _get_atr(symbol: str, period: int = 14) -> float:
    """Fetch current ATR for the underlying from market data."""
    try:
        from data_fetcher import DataFetcher
        df = DataFetcher(paper_trade=False).get_market_data(symbol, interval="5m", days=3)
        if df is not None and len(df) >= period:
            from indicators import calculate_atr
            atr = calculate_atr(df, period)
            return float(atr.dropna().iloc[-1])
    except Exception:
        pass
    # Fallback: 1.5% of estimated price
    return {"NIFTY": 50, "BANKNIFTY": 150}.get(symbol.upper(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Signal engine bridge
# ─────────────────────────────────────────────────────────────────────────────

def _get_signal_intelligence(symbol: str) -> Dict:
    """
    Run generate_signal on the underlying to get live intelligence.
    Returns dict with: score, direction, regime, narrative, indicators.
    """
    result = {"score": 0.0, "direction": "", "regime": "UNKNOWN", "narrative": ""}
    try:
        from data_fetcher import DataFetcher
        df = DataFetcher(paper_trade=False).get_market_data(symbol, interval="5m", days=3)
        if df is None or len(df) < 20:
            return result
        from signal_engine import generate_signal
        sig = generate_signal(df=df, df_htf=df, symbol=symbol)
        result["score"]     = float(sig.get("score", 0))
        result["direction"] = str(sig.get("direction") or sig.get("side", ""))
        result["regime"]    = str(sig.get("regime", "UNKNOWN"))
        result["narrative"] = str(sig.get("ai_reason", ""))
        result["vix"]       = float(sig.get("signal_meta", {}).get("vix", 15) or 15)
        result["n_agree"]   = int(sig.get("n_agree", 0) or 0)
        result["chop"]      = float(sig.get("signal_meta", {}).get(
                                  "choppiness_index", 50) or 50)
        result["gex_mod"]   = float(sig.get("signal_meta", {}).get("gex_modifier", 0) or 0)
        result["skew_vel"]  = float(sig.get("signal_meta", {}).get(
                                  "skew_velocity_mod", 0) or 0)
    except Exception as e:
        logger.debug("_get_signal_intelligence[%s]: %s", symbol, e)
    return result


def _get_news_sentiment(symbol: str) -> float:
    """Get news sentiment score for the symbol. Returns 0.0 on failure."""
    try:
        from news_sentiment import get_news_score
        return float(get_news_score(symbol))
    except Exception:
        try:
            from market_intelligence_hub import get_news_sentiment_score
            return float(get_news_sentiment_score(symbol))
        except Exception:
            return 0.0


def _get_vix() -> float:
    try:
        from market_regime import get_current_vix
        return float(get_current_vix())
    except Exception:
        try:
            from config import INDIA_VIX_THRESHOLD
            return 15.0
        except Exception:
            return 15.0


def _get_wow_intelligence(symbol: str, direction: str, df=None) -> Dict:
    """
    Run ALL WOW factors for a symbol/direction and return aggregated result.
    Combines wow_factors_engine.get_wow_score (22 factors) and
    wow_factors_v2.get_all_wow_scores (extended factors: unusual options,
    smart/dumb money, intermarket, momentum quality, promoter confidence).

    Returns:
      wow_score   float  — total modifier (−3 to +3)
      verdict     str    — STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
      reasons     list   — top human-readable reasons
      factors     dict   — individual factor scores
      pcr         float
      oi_signal   str
    """
    result: Dict = {
        "wow_score": 0.0, "verdict": "NEUTRAL",
        "reasons": [], "factors": {},
        "pcr": 1.0, "oi_signal": "",
    }
    try:
        from wow_factors_engine import get_wow_score as _wow_v1
        v1 = _wow_v1(symbol, direction)
        result["wow_score"] += float(v1.get("wow_score", 0))
        result["verdict"]    = v1.get("verdict", "NEUTRAL")
        result["reasons"]   += v1.get("reasons", [])
        result["factors"].update(v1.get("factors", {}))
        result["pcr"]        = v1.get("pcr", 1.0)
    except Exception as e:
        logger.debug("wow_v1 %s: %s", symbol, e)

    try:
        from wow_factors_v2 import get_all_wow_scores as _wow_v2
        v2 = _wow_v2(symbol, direction, df_ohlcv=df)
        v2_adj = float(v2.get("total_wow_adj", 0))
        result["wow_score"] += v2_adj

        # Collect v2 reasons
        for key in ["unusual_options", "promoter", "smart_dumb_money", "intermarket"]:
            sub = v2.get(key, {})
            if sub and sub.get("signal", "NEUTRAL") not in ("NEUTRAL", ""):
                reason = f"{key.replace('_',' ').title()}: {sub.get('signal','')} — {str(sub.get('reason',''))[:60]}"
                result["reasons"].append(reason)

        result["factors"]["unusual_options"]  = v2.get("unusual_options", {}).get("score_adj", 0)
        result["factors"]["promoter_v2"]      = v2.get("promoter", {}).get("score_adj", 0)
        result["factors"]["smart_dumb_money"] = v2.get("smart_dumb_money", {}).get("score_adj", 0)
        result["factors"]["intermarket"]      = v2.get("intermarket", {}).get("score_adj", 0)
        result["oi_signal"]                   = str(v2.get("unusual_options", {}).get("signal", ""))
    except Exception as e:
        logger.debug("wow_v2 %s: %s", symbol, e)

    # Cap at ±3.0
    result["wow_score"] = round(max(-3.0, min(3.0, result["wow_score"])), 3)

    # Re-compute verdict from total
    ws = result["wow_score"]
    if   ws >= 1.5:  result["verdict"] = "STRONG_BUY"  if direction == "BUY" else "STRONG_SELL"
    elif ws >= 0.5:  result["verdict"] = "BUY"          if direction == "BUY" else "SELL"
    elif ws >= -0.5: result["verdict"] = "NEUTRAL"
    elif ws >= -1.5: result["verdict"] = "WEAK"
    else:            result["verdict"] = "AVOID"

    result["reasons"] = list(dict.fromkeys(result["reasons"]))[:5]  # deduplicate, top 5
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Guardian decision engine (per-trade logic)
# ─────────────────────────────────────────────────────────────────────────────

class TradeGuardian:
    """
    Core engine: manages all registered trades.
    Runs in a background thread; sends Telegram alerts via provided callback.
    """

    def __init__(self, send_fn=None) -> None:
        """
        Args:
            send_fn: callable(text: str) → sends Telegram message.
                     Injected by trade_guardian_bot.py.
        """
        self._trades: Dict[str, GuardedTrade] = {}
        self._lock   = threading.Lock()
        self._running = False
        self._send   = send_fn or (lambda msg: logger.info("ALERT: %s", msg))
        _init_db()
        self._load_state()
        logger.info("TradeGuardian initialised. Active trades: %d", len(self._trades))

    # ── State persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        try:
            data = {tid: t.to_dict() for tid, t in self._trades.items()
                    if t.status == "OPEN"}
            _STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.debug("_save_state: %s", e)

    def _load_state(self) -> None:
        try:
            if _STATE_FILE.exists():
                data = json.loads(_STATE_FILE.read_text())
                for tid, d in data.items():
                    t = GuardedTrade(**{k: v for k, v in d.items()
                                        if k in GuardedTrade.__dataclass_fields__})
                    self._trades[tid] = t
        except Exception as e:
            logger.debug("_load_state: %s", e)

    # ── Trade registration ────────────────────────────────────────────────────

    def register_trade(
        self,
        symbol: str,
        instrument: str,
        side: str,
        entry_price: float,
        qty: int,
        option_type: str = "",
        strike: float = 0.0,
        expiry: str = "weekly",
        notes: str = "",
        is_positional: bool = False,
        manual_sl: float = 0.0,
        manual_target: float = 0.0,
    ) -> Dict:
        """
        Register a new trade for the guardian to manage.
        Returns a dict with trade_id, computed levels, and FOMO assessment.
        """
        cfg    = _load_cfg()
        vix    = _get_vix()
        intel  = _get_signal_intelligence(symbol)
        regime = intel.get("regime", "UNKNOWN")

        # Run WOW factors at registration
        wow = {}
        if cfg.get("wow_factors", {}).get("enabled", True):
            wow = _get_wow_intelligence(symbol, side.upper())

        trade_id = f"{symbol}_{datetime.now().strftime('%H%M%S')}"
        lot_sz   = _get_lot_size(symbol, instrument)

        trade = GuardedTrade(
            trade_id    = trade_id,
            symbol      = symbol.upper(),
            instrument  = instrument.upper(),
            option_type = option_type.upper(),
            strike      = float(strike),
            expiry      = expiry,
            side        = side.upper(),
            qty         = int(qty),
            entry_price = float(entry_price),
            entry_time  = datetime.now().isoformat(),
            regime      = regime,
            signal_score= intel.get("score", 0.0),
            signal_direction = intel.get("direction", ""),
            vix         = vix,
            narrative   = intel.get("narrative", ""),
            notes       = notes,
            is_positional = is_positional,
            lot_size    = lot_sz,
            wow_score   = float(wow.get("wow_score", 0.0)),
            wow_verdict = str(wow.get("verdict", "")),
            wow_reasons = " | ".join(wow.get("reasons", [])),
            wow_factors = json.dumps({k: round(float(v), 3)
                                      for k, v in wow.get("factors", {}).items()}),
            last_wow_time = datetime.now().isoformat(),
        )
        trade.peak_price = entry_price

        # Compute levels
        trade = _compute_levels(trade, cfg, regime, vix)

        # Override with manual SL/target if provided
        if manual_sl > 0:
            trade.stop_loss   = manual_sl
            trade.trailing_sl = manual_sl
        if manual_target > 0:
            trade.target_1 = manual_target

        # FOMO assessment (now includes WOW factors)
        fomo_warn = self._assess_fomo(trade, intel, cfg, wow)

        with self._lock:
            self._trades[trade_id] = trade
        self._save_state()
        _save_trade(trade)
        _log_event(trade_id, "REGISTERED", entry_price, 0, 0,
                   f"SL={trade.stop_loss} T1={trade.target_1} T2={trade.target_2} "
                   f"wow={trade.wow_score:+.2f}")

        return {
            "trade_id":   trade_id,
            "trade":      trade,
            "fomo_warn":  fomo_warn,
            "regime":     regime,
            "signal":     intel,
            "wow":        wow,
        }

    def _assess_fomo(self, trade: GuardedTrade, intel: Dict,
                     cfg: Dict, wow: Optional[Dict] = None) -> Optional[str]:
        """Return FOMO warning string (or None if all clear), incorporating WOW factors."""
        warnings = []
        fg  = cfg.get("fomo_guard", {})
        wfg = cfg.get("wow_factors", {})

        score  = float(intel.get("score", 0))
        regime = str(intel.get("regime", ""))
        chop   = float(intel.get("chop", 50))

        blocked_regimes = fg.get("block_in_regimes", [])
        warned_regimes  = fg.get("warn_in_regimes", [])

        # Hard blocks (signal engine)
        if fg.get("enabled", True):
            if regime in blocked_regimes:
                return f"BLOCKED: Market in {regime} — system advises against entry."
            if score < float(fg.get("block_if_score_below", 2.0)):
                return f"BLOCKED: Very weak signal (score {score:.1f}) — high-risk entry."

        # WOW factor hard block
        if wow and wfg.get("enabled", True):
            ws = float(wow.get("wow_score", 0))
            if ws < float(wfg.get("block_entry_below", -2.0)):
                top = wow.get("reasons", [])[:2]
                return (f"BLOCKED by WOW Factors (score {ws:+.2f}): "
                        + "; ".join(top) if top else f"All WOW factors negative.")

        # Warnings (collect all, then join)
        if fg.get("enabled", True):
            if regime in warned_regimes:
                warnings.append(f"Market in {regime} — reduce position size")
            if score < float(fg.get("warn_if_score_below", 4.0)):
                warnings.append(f"Low signal score ({score:.1f}/10)")
            if chop > float(fg.get("warn_if_chop_above", 60)):
                warnings.append(f"Choppiness {chop:.0f} — market ranging")

        # WOW warnings
        if wow and wfg.get("enabled", True):
            ws = float(wow.get("wow_score", 0))
            if ws < float(wfg.get("warn_entry_below", -1.0)):
                top = wow.get("reasons", [])[:1]
                warnings.append(f"WOW score {ws:+.2f} negative"
                                 + (f": {top[0]}" if top else ""))
            # Individual factor alerts
            factors = wow.get("factors", {})
            if wfg.get("alert_on_pcr_extreme") and abs(float(wow.get("pcr", 1.0)) - 1.0) > 0.8:
                pcr = float(wow.get("pcr", 1.0))
                warnings.append(f"PCR {pcr:.2f} — {'extreme fear' if pcr < 0.5 else 'extreme greed'}")

        return "CAUTION: " + "; ".join(warnings) if warnings else None

    # ── Manual override commands ──────────────────────────────────────────────

    def update_sl(self, trade_id: str, new_sl: float) -> bool:
        with self._lock:
            t = self._trades.get(trade_id)
            if not t:
                return False
            t.stop_loss   = new_sl
            t.trailing_sl = new_sl
        self._save_state()
        _log_event(trade_id, "SL_OVERRIDE", t.current_price, t.pnl, t.pnl_pct,
                   f"Manual SL set to {new_sl}")
        return True

    def update_target(self, trade_id: str, new_target: float, level: int = 1) -> bool:
        with self._lock:
            t = self._trades.get(trade_id)
            if not t:
                return False
            if level == 1:   t.target_1 = new_target
            elif level == 2: t.target_2 = new_target
            elif level == 3: t.target_3 = new_target
        self._save_state()
        return True

    def protect_profit(self, trade_id: str, pct: float = 50.0) -> Optional[str]:
        """Move SL to protect pct% of current profit."""
        with self._lock:
            t = self._trades.get(trade_id)
            if not t or t.pnl <= 0:
                return "No profit to protect."
            ep  = t.entry_price
            cur = t.current_price
            # New SL = entry + (current_profit × pct/100)
            profit_pts = abs(cur - ep)
            protected  = profit_pts * (pct / 100)
            if t.is_long():
                new_sl = round(ep + protected, 2)
                if new_sl <= t.trailing_sl:
                    return f"SL already at ₹{t.trailing_sl:.2f} — no change needed."
            else:
                new_sl = round(ep - protected, 2)
                if new_sl >= t.trailing_sl:
                    return f"SL already at ₹{t.trailing_sl:.2f} — no change needed."
            t.stop_loss   = new_sl
            t.trailing_sl = new_sl
        self._save_state()
        return f"SL moved to ₹{new_sl:.2f} (protecting {pct:.0f}% of profit)"

    def close_trade(self, trade_id: str, exit_price: float, reason: str = "manual") -> bool:
        with self._lock:
            t = self._trades.get(trade_id)
            if not t:
                return False
            t.exit_price  = exit_price
            t.exit_time   = datetime.now().isoformat()
            t.exit_reason = reason
            t.status      = "CLOSED"
            ep   = t.entry_price
            units = t.qty * t.lot_size   # actual units = lots × lot_size
            if t.is_long():
                t.pnl     = (exit_price - ep) * units
                t.pnl_pct = (exit_price - ep) / ep * 100
            else:
                t.pnl     = (ep - exit_price) * units
                t.pnl_pct = (ep - exit_price) / ep * 100
            _save_trade(t)
            _log_event(trade_id, "CLOSED", exit_price, t.pnl, t.pnl_pct, reason)
            del self._trades[trade_id]
        self._save_state()
        return True

    def get_open_trades(self) -> List[GuardedTrade]:
        with self._lock:
            return list(self._trades.values())

    # ── Price update cycle ────────────────────────────────────────────────────

    def _update_price(self, trade: GuardedTrade) -> Optional[float]:
        """
        Fetch current LTP for the trade's instrument.

        Priority:
          1. Angel One getLtpData() — real-time LTP (<1s latency). Used for options
             where 1-min candle polling would miss intraday spikes entirely.
          2. angel.get_ltp() wrapper — handles NSE symbol mapping.
          3. DataFetcher 1-min candle — last resort, ~60s stale for options.
        """
        sym = trade.symbol.upper()

        # Priority 1: Angel getLtpData (fastest — real-time tick)
        try:
            from angel import AngelOne as _AOne
            import os as _os_p
            _ang = _AOne(
                api_key    = _os_p.getenv("API_KEY", ""),
                client_id  = _os_p.getenv("CLIENT_ID", ""),
                password   = _os_p.getenv("PASSWORD", ""),
                totp_secret= _os_p.getenv("TOTP_SECRET", ""),
            )
            if _ang and _ang.obj:
                ltp = _ang.get_ltp(sym)
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception as e:
            logger.debug("_update_price Angel LTP[%s]: %s", sym, e)

        # Priority 2: DataFetcher 1-min candle (fallback — up to 60s stale)
        try:
            from data_fetcher import DataFetcher
            df = DataFetcher(paper_trade=False).get_market_data(sym, interval="1m", days=1)
            if df is not None and len(df) > 0:
                close_col = "close" if "close" in df.columns else df.columns[-1]
                return float(df[close_col].iloc[-1])
        except Exception as e:
            logger.debug("_update_price DataFetcher[%s]: %s", sym, e)

        return None

    def _process_price_update(self, trade: GuardedTrade, ltp: float, cfg: Dict) -> List[str]:
        """
        Process a new price tick for a trade.
        Returns list of alert messages to send.
        """
        alerts = []
        ep     = trade.entry_price
        is_long = trade.is_long()

        # Update state
        old_price  = trade.current_price
        trade.current_price = ltp

        units = trade.qty * trade.lot_size   # actual units for ₹P&L
        if is_long:
            if ltp > trade.peak_price: trade.peak_price = ltp
            trade.pnl     = (ltp - ep) * units
            trade.pnl_pct = (ltp - ep) / ep * 100
        else:
            if ltp < trade.peak_price or trade.peak_price == 0:
                trade.peak_price = ltp
            trade.pnl     = (ep - ltp) * units
            trade.pnl_pct = (ep - ltp) / ep * 100

        # r_multiple: how many R units of profit/loss
        # total_risk_inr = risk_points × qty × lot_size (same units as pnl)
        total_risk = trade.total_risk_inr()
        trade.r_multiple = trade.pnl / total_risk if total_risk > 0 else 0

        # ── Break-even activation ────────────────────────────────────────────
        instr = trade.instrument.upper()
        be_threshold = float(cfg.get("options" if instr == "OPTIONS" else
                             "futures" if instr == "FUTURES" else
                             "stocks", {}).get(
                             "breakeven_at_pct" if instr == "OPTIONS" else
                             "breakeven_at_r", 20 if instr == "OPTIONS" else 1.0))

        reached_be = (instr == "OPTIONS" and trade.pnl_pct >= be_threshold) or \
                     (instr != "OPTIONS" and trade.r_multiple >= be_threshold)

        if not trade.breakeven_activated and reached_be:
            trade.breakeven_activated = True
            trade.trailing_sl = ep
            alerts.append(
                f"🔒 <b>BREAKEVEN LOCKED</b> — {trade.symbol}\n"
                f"SL moved to entry ₹{ep:,.2f} | P&L: {trade.pnl_pct:+.1f}%"
            )

        # ── Trailing SL update ───────────────────────────────────────────────
        if trade.breakeven_activated:
            if instr == "OPTIONS":
                trail_pct = float(cfg.get("options",{}).get("trail_step_pct", 10)) / 100
                if is_long:
                    new_trail = round(trade.peak_price * (1 - trail_pct), 2)
                    if new_trail > trade.trailing_sl:
                        trade.trailing_sl = new_trail
                        if cfg.get("telegram",{}).get("alert_every_trail_update"):
                            alerts.append(f"📈 Trail SL → ₹{new_trail:.2f}")
                else:
                    new_trail = round(trade.peak_price * (1 + trail_pct), 2)
                    if new_trail < trade.trailing_sl:
                        trade.trailing_sl = new_trail
            else:
                atr_val = _get_atr(trade.symbol)
                mult = float(cfg.get(
                    "futures" if instr == "FUTURES" else "stocks", {}
                ).get("trail_atr_mult", 1.0))
                if is_long:
                    new_trail = round(trade.peak_price - atr_val * mult, 2)
                    if new_trail > trade.trailing_sl:
                        trade.trailing_sl = new_trail
                else:
                    new_trail = round(trade.peak_price + atr_val * mult, 2)
                    if new_trail < trade.trailing_sl:
                        trade.trailing_sl = new_trail

        # ── SL check ─────────────────────────────────────────────────────────
        sl_hit = (is_long and ltp <= trade.trailing_sl) or \
                 (not is_long and ltp >= trade.trailing_sl)
        if sl_hit:
            trade.status      = "CLOSED"
            trade.exit_price  = ltp
            trade.exit_time   = datetime.now().isoformat()
            trade.exit_reason = f"SL hit ₹{trade.trailing_sl:.2f}"
            alerts.append(
                f"🛑 <b>STOP LOSS HIT</b> — {trade.symbol}\n"
                f"Exit: ₹{ltp:,.2f} | P&L: ₹{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)\n"
                f"SL: ₹{trade.trailing_sl:.2f}"
            )
            return alerts

        # ── Target checks ────────────────────────────────────────────────────
        if not trade.t1_hit:
            t1_hit = (is_long and ltp >= trade.target_1) or \
                     (not is_long and ltp <= trade.target_1)
            if t1_hit:
                trade.t1_hit = True
                partial = cfg.get("options" if instr == "OPTIONS" else
                           "futures" if instr == "FUTURES" else
                           "stocks", {}).get("partial_at_t1_pct", 50)
                # For qty=1: can only book full lot; show lot count not raw qty
                partial_lots = max(1, int(trade.qty * partial / 100))
                if trade.qty == 1:
                    partial_msg = "Consider booking this lot (full position for T2)"
                else:
                    partial_msg = (f"Consider booking {partial_lots} of {trade.qty} lots "
                                   f"({partial}%) — hold rest for T2")
                alerts.append(
                    f"🎯 <b>TARGET 1 HIT</b> — {trade.symbol}\n"
                    f"₹{ltp:,.2f} | P&L: ₹{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)\n"
                    f"💡 {partial_msg}"
                )

        if trade.t1_hit and not trade.t2_hit:
            t2_hit = (is_long and ltp >= trade.target_2) or \
                     (not is_long and ltp <= trade.target_2)
            if t2_hit:
                trade.t2_hit = True
                alerts.append(
                    f"🎯🎯 <b>TARGET 2 HIT</b> — {trade.symbol}\n"
                    f"₹{ltp:,.2f} | P&L: ₹{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)\n"
                    f"💡 Consider booking remaining position"
                )

        # ── Options spike detection ──────────────────────────────────────────
        if instr == "OPTIONS" and not trade.spike_alerted:
            if trade.price_5min_ago > 0:
                spike_pct = (ltp - trade.price_5min_ago) / trade.price_5min_ago * 100
                alert_thr = float(cfg.get("options",{}).get("spike_alert_pct", 35))
                partial_thr = float(cfg.get("options",{}).get("spike_partial_exit_pct", 50))
                if is_long and spike_pct >= partial_thr:
                    trade.spike_alerted = True
                    alerts.append(
                        f"⚡ <b>SPIKE ALERT</b> — {trade.symbol}\n"
                        f"+{spike_pct:.0f}% in 5 min! ₹{trade.price_5min_ago:.0f}→₹{ltp:.0f}\n"
                        f"💡 STRONG SUGGEST: Book 50-75% now. Spikes reverse fast!"
                    )
                elif is_long and spike_pct >= alert_thr:
                    alerts.append(
                        f"📈 <b>SPIKE DETECTED</b> — {trade.symbol}\n"
                        f"+{spike_pct:.0f}% in 5 min. Consider partial booking."
                    )

        # ── Greed guard at 2R / 3R ───────────────────────────────────────────
        gg = cfg.get("greed_guard", {})
        if gg.get("enabled", True):
            # qty=1: can't split — suggest trailing SL instead of partial booking
            if trade.r_multiple >= 3.0 and not trade.t2_hit:
                if trade.qty == 1:
                    action = "Move SL to lock in 2R profit. Let it run to T3."
                else:
                    action = f"Book {max(1,int(trade.qty*0.7))} of {trade.qty} lots. Trail rest."
                alerts.append(
                    f"💰 <b>3R ACHIEVED</b> — {trade.symbol}\n"
                    f"P&L: ₹{trade.pnl:+,.0f} | R: {trade.r_multiple:.1f}\n"
                    f"💡 {action}"
                )
            elif trade.r_multiple >= 2.0 and not trade.t1_hit:
                if trade.qty == 1:
                    action = "Move SL to lock in 1R profit. Ride to T2."
                else:
                    action = f"Book {max(1,int(trade.qty*0.5))} of {trade.qty} lots."
                alerts.append(
                    f"💰 <b>2R ACHIEVED</b> — {trade.symbol}\n"
                    f"P&L: ₹{trade.pnl:+,.0f} | R: {trade.r_multiple:.1f}\n"
                    f"💡 {action}"
                )

        return alerts

    # ── Signal intelligence cycle ─────────────────────────────────────────────

    def _apply_signal_intelligence(self, trade: GuardedTrade, intel: Dict,
                                   cfg: Dict) -> List[str]:
        """
        Apply signal engine insights to a live trade.
        Returns list of alert messages.
        """
        alerts  = []
        score   = float(intel.get("score", 0))
        sig_dir = str(intel.get("direction", ""))
        regime  = str(intel.get("regime", "UNKNOWN"))
        chop    = float(intel.get("chop", 50))
        gex_mod = float(intel.get("gex_mod", 0))
        skew_vel = float(intel.get("skew_vel", 0))
        narrative = str(intel.get("narrative", ""))

        se_cfg = cfg.get("signal_engine", {})
        if not se_cfg.get("enabled", True):
            return alerts

        trade.signal_score     = score
        trade.signal_direction = sig_dir
        trade.regime           = regime
        trade.last_signal_time = datetime.now().isoformat()

        is_long = trade.is_long()
        aligned = (is_long and sig_dir == "BUY") or \
                  (not is_long and sig_dir == "SELL")

        # ── Exit suggestion ───────────────────────────────────────────────────
        force_exit = float(se_cfg.get("force_exit_score_below", 1.5))
        suggest_exit = float(se_cfg.get("suggest_exit_score_below", 3.0))

        if score < force_exit and not aligned:
            alerts.append(
                f"🚨 <b>STRONG EXIT SIGNAL</b> — {trade.symbol}\n"
                f"Signal score {score:.1f}/10 | Regime: {regime}\n"
                f"System strongly recommends exiting this position.\n"
                f"{narrative}"
            )
        elif score < suggest_exit and not aligned:
            alerts.append(
                f"⚠️ <b>SIGNAL WEAKENING</b> — {trade.symbol}\n"
                f"Score {score:.1f}/10 | Regime: {regime}\n"
                f"Consider tightening SL or taking partial profit.\n"
                f"{narrative}"
            )

        # ── Fear override (don't exit when signals still support) ─────────────
        hold_threshold = float(se_cfg.get("hold_if_score_above", 5.0))
        if score >= hold_threshold and aligned and trade.pnl < 0:
            hold_msg = se_cfg.get("hold_message",
                "Signal engine supports your position. Wait for SL.")
            alerts.append(
                f"💪 <b>HOLD YOUR POSITION</b> — {trade.symbol}\n"
                f"Score {score:.1f}/10 | Regime: {regime} | Aligned: YES\n"
                f"{hold_msg}"
            )

        # ── Target extension ──────────────────────────────────────────────────
        extend_thr  = float(se_cfg.get("extend_target_score_above", 7.5))
        conf_needed = int(se_cfg.get("confluence_to_extend", 3))
        max_ext     = int(cfg.get("greed_guard",{}).get("max_target_extensions", 2))

        if (score >= extend_thr and aligned and
                int(intel.get("n_agree", 0)) >= conf_needed and
                trade.t1_hit and not trade.target_extended and
                trade.target_extensions < max_ext):
            trade.target_extensions += 1
            trade.target_extended = True
            alerts.append(
                f"🚀 <b>TARGET EXTENDED</b> — {trade.symbol}\n"
                f"Score {score:.1f}/10 | {intel.get('n_agree',0)} strategies agree\n"
                f"New T3: ₹{trade.target_3:.2f} — hold for extended move!"
            )

        # ── SL tightening ─────────────────────────────────────────────────────
        tighten_thr    = float(se_cfg.get("tighten_sl_score_below", 4.5))
        tighten_factor = float(se_cfg.get("tighten_factor", 0.5))
        if score < tighten_thr and not aligned and trade.pnl > 0:
            ep  = trade.entry_price
            cur = trade.current_price
            distance = abs(cur - trade.trailing_sl)
            if trade.is_long():
                new_sl = round(cur - distance * tighten_factor, 2)
                if new_sl > trade.trailing_sl:
                    trade.trailing_sl = new_sl
                    alerts.append(
                        f"🔧 <b>SL TIGHTENED</b> — {trade.symbol}\n"
                        f"Signal score dropped to {score:.1f}. SL → ₹{new_sl:.2f}"
                    )
            else:
                new_sl = round(cur + distance * tighten_factor, 2)
                if new_sl < trade.trailing_sl:
                    trade.trailing_sl = new_sl
                    alerts.append(
                        f"🔧 <b>SL TIGHTENED</b> — {trade.symbol}\n"
                        f"Signal score dropped to {score:.1f}. SL → ₹{new_sl:.2f}"
                    )

        # ── Regime change alert ───────────────────────────────────────────────
        if cfg.get("telegram",{}).get("alert_on_regime_change") and regime != trade.regime:
            alerts.append(
                f"🔄 <b>REGIME CHANGED</b> — {trade.symbol}\n"
                f"{trade.regime} → {regime}\n"
                f"SL/Target parameters adjusting automatically."
            )
            # Recompute levels for new regime
            old_sl = trade.stop_loss
            trade = _compute_levels(trade, cfg, regime,
                                    float(intel.get("vix", trade.vix)))
            if abs(trade.stop_loss - old_sl) > 0.5:
                alerts.append(
                    f"📊 Levels recalculated: SL={trade.stop_loss:.2f} "
                    f"T1={trade.target_1:.2f} T2={trade.target_2:.2f}"
                )

        # ── IV Skew velocity alert ────────────────────────────────────────────
        iv_cfg = cfg.get("iv_skew", {})
        if iv_cfg.get("enabled", True):
            steep = float(iv_cfg.get("steepen_threshold", 0.1))
            if skew_vel > steep and trade.is_long():
                alerts.append(
                    f"📉 <b>SKEW STEEPENING</b> — fear rising\n"
                    f"IV skew velocity: {skew_vel:+.3f} vol-pts/min\n"
                    f"Put buyers becoming aggressive. Consider protecting profits."
                )

        # ── GEX modifier alert ────────────────────────────────────────────────
        if abs(gex_mod) > 0.4:
            if (gex_mod < -0.4 and trade.is_long()) or (gex_mod > 0.4 and not trade.is_long()):
                alerts.append(
                    f"⚡ <b>GEX WARNING</b> — {trade.symbol}\n"
                    f"GEX regime opposes your direction (modifier {gex_mod:+.2f}).\n"
                    f"Dealer hedging flows may cap your target."
                )

        return alerts

    # ── News check ────────────────────────────────────────────────────────────

    def _check_news(self, trade: GuardedTrade, cfg: Dict) -> List[str]:
        alerts = []
        news_cfg = cfg.get("news", {})
        if not news_cfg.get("enabled", True):
            return alerts
        try:
            sentiment = _get_news_sentiment(trade.symbol)
            bad_thr   = float(news_cfg.get("bad_news_threshold", -0.5))
            if sentiment < bad_thr and news_cfg.get("tighten_sl_on_bad_news", True):
                factor = float(news_cfg.get("tighten_factor_on_bad_news", 0.6))
                cur    = trade.current_price
                if trade.is_long():
                    new_sl = round(trade.trailing_sl + (cur - trade.trailing_sl) * (1 - factor), 2)
                    if new_sl > trade.trailing_sl:
                        trade.trailing_sl = new_sl
                        alerts.append(
                            f"📰 <b>NEWS ALERT</b> — {trade.symbol}\n"
                            f"Negative sentiment ({sentiment:.2f}). SL tightened → ₹{new_sl:.2f}"
                        )
        except Exception as e:
            logger.debug("_check_news: %s", e)
        return alerts

    # ── WOW factor monitoring ─────────────────────────────────────────────────

    def _apply_wow_intelligence(self, trade: GuardedTrade, cfg: Dict) -> List[str]:
        """
        Re-run all WOW factors for this trade and generate alerts if scores
        shift materially. Called every wow_factors.refresh_seconds (default 120s).

        Decisions driven by WOW score:
          > 1.5  → extend target to T3 if not already extended
          < -1.0 → tighten SL by 30% of remaining distance
          < -1.8 → suggest exit
          < -2.5 → strong exit alert
        Also alerts on individual factor extremes (PCR, unusual options, etc.)
        """
        alerts = []
        wfg = cfg.get("wow_factors", {})
        if not wfg.get("enabled", True):
            return alerts

        wow = _get_wow_intelligence(trade.symbol, trade.side)
        new_score  = float(wow.get("wow_score", 0))
        old_score  = float(trade.wow_score)
        verdict    = str(wow.get("verdict", "NEUTRAL"))
        reasons    = wow.get("reasons", [])
        reasons_str = " | ".join(reasons[:3])
        is_long    = trade.is_long()

        # Update trade state
        trade.wow_score   = new_score
        trade.wow_verdict = verdict
        trade.wow_reasons = " | ".join(reasons)
        trade.wow_factors = json.dumps({k: round(float(v), 3)
                                        for k, v in wow.get("factors", {}).items()})
        trade.last_wow_time = datetime.now().isoformat()

        score_shift = new_score - old_score

        # ── Score shift alert (significant swing) ────────────────────────────
        if abs(score_shift) >= 0.8 and old_score != 0.0:
            direction_icon = "📉" if score_shift < 0 else "📈"
            alerts.append(
                f"{direction_icon} <b>WOW SHIFT</b> — {trade.symbol}\n"
                f"WOW score: {old_score:+.2f} → {new_score:+.2f} ({score_shift:+.2f})\n"
                f"Verdict: {verdict}\n"
                + (f"<i>{reasons_str}</i>" if reasons_str else "")
            )

        extend_thr      = float(wfg.get("extend_target_above", 1.5))
        tighten_thr     = float(wfg.get("tighten_sl_below", -1.0))
        suggest_exit    = float(wfg.get("suggest_exit_below", -1.8))
        force_alert     = float(wfg.get("force_alert_below", -2.5))

        # ── Target extension ─────────────────────────────────────────────────
        max_ext = int(cfg.get("greed_guard", {}).get("max_target_extensions", 2))
        if (new_score >= extend_thr and trade.t1_hit and
                not trade.wow_extend_applied and
                trade.target_extensions < max_ext):
            trade.wow_extend_applied = True
            trade.target_extensions += 1
            alerts.append(
                f"🚀 <b>WOW EXTENDS TARGET</b> — {trade.symbol}\n"
                f"WOW score {new_score:+.2f} ({verdict}) — all factors aligned\n"
                f"Target extended to T3: ₹{trade.target_3:.2f}\n"
                + (f"<i>{reasons_str}</i>" if reasons_str else "")
            )

        # ── SL tightening ────────────────────────────────────────────────────
        if (new_score < tighten_thr and not trade.wow_tighten_applied
                and trade.pnl >= 0 and trade.current_price > 0):
            cur = trade.current_price
            dist = abs(cur - trade.trailing_sl)
            if dist > 0:
                tighten_by = dist * 0.30  # tighten 30% of remaining distance
                new_sl = round(cur - tighten_by, 2) if is_long else round(cur + tighten_by, 2)
                if (is_long and new_sl > trade.trailing_sl) or \
                   (not is_long and new_sl < trade.trailing_sl):
                    trade.wow_tighten_applied = True
                    trade.trailing_sl = new_sl
                    alerts.append(
                        f"🔧 <b>WOW TIGHTENS SL</b> — {trade.symbol}\n"
                        f"WOW score {new_score:+.2f} turning negative\n"
                        f"SL tightened → ₹{new_sl:.2f}\n"
                        + (f"<i>{reasons_str}</i>" if reasons_str else "")
                    )

        # ── Exit suggestions ─────────────────────────────────────────────────
        if new_score < force_alert:
            alerts.append(
                f"🚨 <b>WOW STRONG EXIT SIGNAL</b> — {trade.symbol}\n"
                f"WOW score: {new_score:+.2f} ({verdict})\n"
                f"Multiple factors strongly negative — consider exiting now.\n"
                + (f"<i>{reasons_str}</i>" if reasons_str else "")
            )
        elif new_score < suggest_exit:
            alerts.append(
                f"⚠️ <b>WOW EXIT SUGGESTION</b> — {trade.symbol}\n"
                f"WOW score: {new_score:+.2f} ({verdict})\n"
                f"WOW factors turning against your position.\n"
                + (f"<i>{reasons_str}</i>" if reasons_str else "")
            )

        # ── Individual factor extremes ────────────────────────────────────────
        factors = wow.get("factors", {})
        pcr_val = float(wow.get("pcr", 1.0))
        if wfg.get("alert_on_pcr_extreme") and pcr_val < 0.5 and is_long:
            alerts.append(
                f"📊 <b>PCR EXTREME FEAR</b> — {trade.symbol}\n"
                f"PCR={pcr_val:.2f} — extreme bearishness. Contrarian: expect bounce.\n"
                f"Consider holding — fear spikes often precede reversals."
            )
        elif wfg.get("alert_on_pcr_extreme") and pcr_val > 2.0 and not is_long:
            alerts.append(
                f"📊 <b>PCR EXTREME GREED</b> — {trade.symbol}\n"
                f"PCR={pcr_val:.2f} — extreme complacency. Contrarian: expect reversal."
            )

        uoa_adj = float(factors.get("unusual_options", 0))
        if wfg.get("alert_on_unusual_options") and abs(uoa_adj) > 0.3:
            bias = "bullish" if uoa_adj > 0 else "bearish"
            direction_match = (is_long and uoa_adj > 0) or (not is_long and uoa_adj < 0)
            icon = "✅" if direction_match else "⚠️"
            alerts.append(
                f"{icon} <b>UNUSUAL OPTIONS ACTIVITY</b> — {trade.symbol}\n"
                f"Smart money positioning: {bias} (adj={uoa_adj:+.2f})"
            )

        sdm = float(factors.get("smart_dumb_money", 0))
        if wfg.get("alert_on_smart_dumb_money") and abs(sdm) > 0.3:
            if (is_long and sdm < -0.3) or (not is_long and sdm > 0.3):
                alerts.append(
                    f"🧠 <b>SMART/DUMB DIVERGENCE</b> — {trade.symbol}\n"
                    f"Smart money going {'bearish' if sdm < 0 else 'bullish'} "
                    f"while retail goes the other way (adj={sdm:+.2f}).\n"
                    f"Contrarian: your position may be on the wrong side."
                )

        im = float(factors.get("intermarket", 0))
        if wfg.get("alert_on_intermarket_divergence") and abs(im) > 0.3:
            alerts.append(
                f"🌐 <b>INTERMARKET SIGNAL</b> — {trade.symbol}\n"
                f"Cross-market divergence detected (adj={im:+.2f})"
            )

        return alerts

    # ── Main monitoring loop ──────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        price_interval  = int(_cfg_get("monitoring.price_poll_seconds", 5))
        signal_interval = int(_cfg_get("monitoring.signal_refresh_seconds", 60))
        wow_interval    = int(_cfg_get("wow_factors.refresh_seconds", 120))
        last_signal_ts  = {}  # {trade_id: last_signal_time}
        last_wow_ts     = {}  # {trade_id: last_wow_time}
        price_history   = {}  # {trade_id: [(ts, price)]}

        while self._running:
            cfg = _load_cfg()
            with self._lock:
                trades = list(self._trades.values())

            closed = []
            for trade in trades:
                if trade.status != "OPEN":
                    continue

                # ── Price update ───────────────────────────────────────────
                ltp = self._update_price(trade)
                if ltp and ltp > 0:
                    # Track price history for spike detection
                    hist = price_history.setdefault(trade.trade_id, [])
                    hist.append((time.time(), ltp))
                    # Keep only last 10 min
                    cutoff = time.time() - 600
                    price_history[trade.trade_id] = [(ts, p) for ts, p in hist if ts > cutoff]
                    # Set 5-min-ago price for spike calculation
                    spike_window = int(cfg.get("monitoring",{}).get("spike_window_seconds", 300))
                    ref_ts = time.time() - spike_window
                    old_prices = [(ts, p) for ts, p in hist if ts <= ref_ts]
                    if old_prices:
                        trade.price_5min_ago = old_prices[-1][1]

                    alerts = self._process_price_update(trade, ltp, cfg)
                    for a in alerts:
                        self._send(a)
                    if trade.status == "CLOSED":
                        closed.append(trade.trade_id)
                        self._send(
                            f"\n📊 <b>TRADE SUMMARY</b> — {trade.symbol}\n"
                            f"Entry: ₹{trade.entry_price:,.2f} | Exit: ₹{trade.exit_price:,.2f}\n"
                            f"P&L: ₹{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)\n"
                            f"Reason: {trade.exit_reason}"
                        )

                # ── Signal intelligence (every 60s) ───────────────────────
                last_sig = last_signal_ts.get(trade.trade_id, 0)
                if time.time() - last_sig >= signal_interval:
                    intel  = _get_signal_intelligence(trade.symbol)
                    alerts = self._apply_signal_intelligence(trade, intel, cfg)
                    alerts += self._check_news(trade, cfg)
                    for a in alerts:
                        self._send(a)
                    last_signal_ts[trade.trade_id] = time.time()

                # ── WOW factors (every 120s) ───────────────────────────────
                last_wow = last_wow_ts.get(trade.trade_id, 0)
                if (time.time() - last_wow >= wow_interval and
                        cfg.get("wow_factors", {}).get("enabled", True)):
                    wow_alerts = self._apply_wow_intelligence(trade, cfg)
                    for a in wow_alerts:
                        self._send(a)
                    last_wow_ts[trade.trade_id] = time.time()

                _save_trade(trade)

            # Remove closed trades
            with self._lock:
                for tid in closed:
                    self._trades.pop(tid, None)
            if closed:
                self._save_state()

            time.sleep(price_interval)

    def start(self) -> None:
        self._running = True
        t = threading.Thread(target=self._monitor_loop, daemon=True, name="TradeGuardian")
        t.start()
        logger.info("TradeGuardian monitoring started.")

    def stop(self) -> None:
        self._running = False
        logger.info("TradeGuardian monitoring stopped.")
