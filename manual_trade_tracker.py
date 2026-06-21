"""
manual_trade_tracker.py — Detect & analyze your manual trades with AI

SEPARATE from the main trading bot. Runs alongside it.
Detects trades you place manually on Angel One app/web.
Applies all 63 strategies to find SL, target, trailing SL.
Sends updates to a DEDICATED Telegram channel.

Usage:
    python3 manual_trade_tracker.py  (runs as standalone service)
    OR: import and call from main_autonomous.py

Architecture:
    Your Manual Trade (Angel app) 
        → Detected via order book polling (30s)
        → AI Analysis (63 strategies)  
        → WebSocket tracking (trailing SL, break-even, T1/T2)
        → Dedicated Telegram channel (live updates)
"""
from __future__ import annotations
import os
import re
import sys
import time
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("manual_tracker.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("manual_tracker")

# ── Config ────────────────────────────────────────────────────────────────
POLL_INTERVAL       = int(os.getenv("MANUAL_POLL_INTERVAL_SEC", "30"))
UPDATE_INTERVAL     = int(os.getenv("MANUAL_UPDATE_INTERVAL_SEC", "900"))
# Prefer a dedicated manual-trade channel. Fall back to the main bot chat ONLY
# when no Guardian bot is configured either — otherwise manual trades would noise
# up the autonomous bot's own channel. With a Guardian bot set, that bot is the
# dedicated home for manual-trade cards/updates, so the main-chat fallback is
# skipped and notifications are never silently dropped.
CHANNEL_ID          = os.getenv("TELEGRAM_MANUAL_CHANNEL_ID", "")
if not CHANNEL_ID and not (os.getenv("GUARDIAN_BOT_TOKEN") and os.getenv("GUARDIAN_CHAT_ID")):
    CHANNEL_ID      = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_TOKEN           = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Dedicated manual-trade ("Trade Guardian") bot — detected trades + image cards
# are also pushed here so the manual-trade bot shows live status.
GUARDIAN_BOT_TOKEN  = os.getenv("GUARDIAN_BOT_TOKEN", "")
GUARDIAN_CHAT_ID    = os.getenv("GUARDIAN_CHAT_ID", "")
DB_PATH             = "manual_trades.db"
BOT_ORDER_TAG       = "ALGO_BOT"  # tag our bot uses — manual trades won't have this

# Never post manual-trade messages into the AUTOMATED bot's chat. This holds even
# when GUARDIAN_CHAT_ID == TELEGRAM_CHAT_ID (Guardian configured to the same chat
# as the main bot) — otherwise manual trades leak into the automated bot. Set
# MANUAL_ALLOW_MAIN_CHAT=true to override. Give manual trades a SEPARATE chat by
# pointing GUARDIAN_CHAT_ID / TELEGRAM_MANUAL_CHANNEL_ID at a different chat id.
_MAIN_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
_ALLOW_MAIN_CHAT = os.getenv("MANUAL_ALLOW_MAIN_CHAT", "false").lower() == "true"


def _is_main_chat(chat_id: str) -> bool:
    """True if chat_id is the automated bot's chat (which we must not post to)."""
    return bool(chat_id) and str(chat_id) == str(_MAIN_CHAT_ID) and not _ALLOW_MAIN_CHAT

# Broker-side protection (GTT SL + target that survive a tracker crash).
AUTO_PROTECT        = os.getenv("MANUAL_AUTO_PROTECT",       "true").lower() == "true"
OPTION_SL_PCT       = float(os.getenv("MANUAL_OPTION_SL_PCT",     "0.30"))  # 30% of premium
OPTION_TGT_PCT      = float(os.getenv("MANUAL_OPTION_TARGET_PCT", "0.50"))
OPTION_TGT2_PCT     = float(os.getenv("MANUAL_OPTION_TARGET2_PCT", "1.00"))
OPTION_BREAKEVEN_PCT= float(os.getenv("MANUAL_OPTION_BREAKEVEN_PCT", "0.20"))
OPTION_TRAIL_PCT    = float(os.getenv("MANUAL_OPTION_TRAIL_PCT", "0.10"))
OPTION_GREED_1_PCT  = float(os.getenv("MANUAL_OPTION_GREED1_PCT", "0.50"))
OPTION_GREED_2_PCT  = float(os.getenv("MANUAL_OPTION_GREED2_PCT", "1.00"))
OPTION_GREED_3_PCT  = float(os.getenv("MANUAL_OPTION_GREED3_PCT", "1.50"))
OPTION_LOCK_1_PCT   = float(os.getenv("MANUAL_OPTION_LOCK1_PCT", "0.25"))
OPTION_LOCK_2_PCT   = float(os.getenv("MANUAL_OPTION_LOCK2_PCT", "0.50"))
OPTION_LOCK_3_PCT   = float(os.getenv("MANUAL_OPTION_LOCK3_PCT", "0.75"))
OPTION_FLOW_CHECK_SECS = float(os.getenv("MANUAL_OPTION_FLOW_CHECK_SECS", "120"))
OPTION_REFRESH_CHAIN   = os.getenv("MANUAL_OPTION_REFRESH_CHAIN", "true").lower() == "true"
OPTION_SPIKE_WINDOW_SECS = float(os.getenv("MANUAL_OPTION_SPIKE_WINDOW_SECS", "300"))
OPTION_SPIKE_PCT      = float(os.getenv("MANUAL_OPTION_SPIKE_PCT", "0.50"))
OPTION_EXPIRY_TIGHTEN_DTE = int(os.getenv("MANUAL_OPTION_EXPIRY_TIGHTEN_DTE", "1"))
OPTION_FLOW_WEAK_SCORE = float(os.getenv("MANUAL_OPTION_FLOW_WEAK_SCORE", "-1.0"))
# Hybrid option stop (review consensus): the broker GTT is a DEEP catastrophe
# floor (crash/gap protection) so premium noise / IV-vega spikes don't shake you
# out; the PRIMARY intraday stop is the underlying breaking structure (live loop).
CATASTROPHE_SL_PCT  = float(os.getenv("MANUAL_CATASTROPHE_SL_PCT", "0.60"))  # 60% premium
STRUCT_STOP_ENABLED = os.getenv("MANUAL_STRUCT_STOP", "true").lower() == "true"
STRUCT_CHECK_SECS   = float(os.getenv("MANUAL_STRUCT_CHECK_SECS", "60"))  # underlying re-check
STRUCT_SWING_LB     = int(os.getenv("MANUAL_STRUCT_SWING", "3"))          # swing pivot lookback
# Roots whose underlying is an INDEX spot (no -EQ). Angel index candle/LTP is
# unreliable (see angel_option_chain), so for these the struct-stop only ARMS if
# we can actually fetch the spot candles; otherwise the trade keeps the tight
# premium GTT (the data-fail fallback).
_INDEX_UNDERLYINGS  = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                       "NIFTYNXT50", "SENSEX", "BANKEX"}
EQUITY_SL_PCT       = float(os.getenv("MANUAL_EQUITY_SL_PCT",     "0.02"))
EQUITY_TGT_PCT      = float(os.getenv("MANUAL_EQUITY_TARGET_PCT", "0.04"))
# Dynamic exit engine — how often to recompute indicator-based levels (candle
# fetch is heavier than the per-cycle ratchet).
DYN_RECOMPUTE_SECS  = float(os.getenv("MANUAL_DYN_RECOMPUTE_SECS", "120"))
# End-of-day hold/close decision. Recommendation alert is on by default;
# AUTO-closing (real square-off order) is OFF by default — opt in explicitly.
EOD_CHECK_ENABLED   = os.getenv("MANUAL_EOD_CHECK",     "true").lower() == "true"
AUTO_CLOSE_EOD      = os.getenv("MANUAL_AUTO_CLOSE_EOD", "false").lower() == "true"
# HARD rule (unanimous review consensus): a long option with DTE <= this is
# force-closed at EOD — never carried overnight into expiry week (gap → ~0,
# GTT fills at the open print = max loss). Set to -1 to disable.
EOD_FORCE_CLOSE_DTE = int(os.getenv("MANUAL_EOD_FORCE_DTE", "1"))


@dataclass
class ManualTrade:
    """A manually placed trade detected from Angel order book."""
    order_id: str
    symbol: str
    exchange: str
    side: str           # BUY or SELL
    qty: int
    entry_price: float
    product: str        # INTRADAY / DELIVERY / MARGIN
    order_time: str
    
    # AI-generated analysis
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    trailing_sl: float = 0.0
    breakeven_price: float = 0.0
    
    # Strategy alignment
    strategies_bullish: List[str] = field(default_factory=list)
    strategies_bearish: List[str] = field(default_factory=list)
    regime: str = ""
    vix: float = 0.0
    wow_factors: List[str] = field(default_factory=list)
    
    # Live tracking state
    current_price: float = 0.0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 999999.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    breakeven_activated: bool = False
    t1_hit: bool = False
    status: str = "OPEN"        # OPEN / CLOSED / TRACKING
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""

    # Broker-side protection (survives a tracker crash)
    sl_gtt_id: str = ""         # GTT order id for the stop-loss
    target_gtt_id: str = ""     # GTT order id for the target
    hwm: float = 0.0            # high-water-mark of LTP (for trailing)
    protected: bool = False     # protection orders placed


class ManualTradeTracker:
    """Main tracker: detect, analyze, track, report manual trades."""
    
    _ANGEL_RECONNECT_SECS = float(os.getenv("MANUAL_ANGEL_RECONNECT_SECS", "120"))
    _ANGEL_RECONNECT_CLOSED_SECS = float(os.getenv("MANUAL_ANGEL_RECONNECT_CLOSED_SECS", "1800"))

    def __init__(self):
        self._angel = None
        self._last_angel_attempt: float = 0.0
        self._angel_down_alerted: bool = False   # avoid repeat "no Angel" alerts
        self._protect_warned: set = set()        # avoid repeat "past SL" warnings
        self._fomo_warned: set = set()           # avoid repeat entry-quality warnings
        self._greed_stage: dict = {}             # order_id -> last profit-lock stage
        self._flow_stage: dict = {}              # order_id -> latest flow guard stage
        self._flow_ts: dict = {}                 # order_id -> last OI/volume check
        self._price_hist: dict = {}              # order_id -> [(ts, ltp)] for spike checks
        self._chain_refresh_ts: dict = {}        # underlying -> last chain refresh
        self._dyn_ts: dict = {}                   # last dynamic-exit recompute per trade
        self._struct_ts: dict = {}                # last structural-stop check per trade
        self._underlying: dict = {}               # cached (sym,exch) underlying or None
        self._eod_done_date: str = ""             # date the EOD check last ran
        self._known_orders: set = set()     # order IDs we've already processed
        self._active_trades: Dict[str, ManualTrade] = {}
        self._running = False
        self._lock = threading.Lock()
        
        # Initialize Angel
        self._init_angel(log_level="error" if self._in_market_hours() else "warning")
        
        # Initialize DB
        self._init_db()
        
        # Load previously known orders + resume still-open trades
        self._load_known_orders()
        self._load_open_trades()
    
    @staticmethod
    def _in_market_hours() -> bool:
        now = datetime.now()
        return (
            (now.hour > 9 or (now.hour == 9 and now.minute >= 15))
            and (now.hour < 15 or (now.hour == 15 and now.minute <= 30))
        )

    def _init_angel(self, log_level: str = "error") -> bool:
        """Connect to Angel One. Returns True if a live connection is up."""
        log_fn = logger.error if log_level == "error" else logger.warning if log_level == "warning" else logger.debug
        try:
            if self._angel is not None:
                # The (singleton) client's constructor won't re-login on
                # re-instantiation (it short-circuits on _initialised), so a
                # failed startup login could never recover. Reconnect explicitly.
                self._angel.connect()
            else:
                from angel import AngelOne
                self._angel = AngelOne(
                    api_key=os.getenv("API_KEY", ""),
                    client_id=os.getenv("CLIENT_ID", ""),
                    password=os.getenv("PASSWORD", ""),
                    totp_secret=os.getenv("TOTP_SECRET", ""),
                )
            if self._angel and self._angel.obj:
                logger.info("Angel connected: %s", os.getenv("CLIENT_ID"))
                return True
            log_fn("Angel connection failed")
        except Exception as e:
            log_fn("Angel init: %s", e)
        return False

    def _ensure_angel(self) -> bool:
        """
        Reconnect if we have no live Angel session — e.g. the initial
        startup login failed (Angel's login API is flaky pre-market).

        Only attempts when there is NO connection, and at most once every
        _ANGEL_RECONNECT_SECS. It never re-logs-in while already connected,
        so it will not ping-pong / invalidate the main bot's session.
        """
        if self._angel and self._angel.obj:
            return True
        now = time.time()
        should_watch_now = self._in_market_hours() or bool(self._active_trades)
        reconnect_secs = (
            self._ANGEL_RECONNECT_SECS
            if should_watch_now
            else self._ANGEL_RECONNECT_CLOSED_SECS
        )
        if now - self._last_angel_attempt < reconnect_secs:
            return False
        self._last_angel_attempt = now
        if should_watch_now:
            logger.info("Angel session down — attempting reconnect...")
        else:
            logger.debug("Angel session down after-hours — quiet reconnect attempt")
        ok = self._init_angel(log_level="error" if should_watch_now else "debug")
        if ok:
            # Recovered — only notify if we had previously warned about an outage.
            if self._angel_down_alerted:
                self._angel_down_alerted = False
                self.send_channel(
                    "✅ <b>Manual Tracker reconnected to Angel</b>\n"
                    "Manual-trade monitoring restored.")
        else:
            # Down — alert once so a blind tracker can't go unnoticed all day.
            if should_watch_now and not self._angel_down_alerted:
                self._angel_down_alerted = True
                self.send_channel(
                    "⚠️ <b>Manual Tracker: NO Angel connection</b>\n"
                    "Your manual trades are NOT being monitored.\n"
                    f"Retrying every {int(reconnect_secs)}s.")
        return ok
    
    def _init_db(self):
        """Create SQLite tables for manual trade tracking."""
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS manual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                symbol TEXT,
                exchange TEXT,
                side TEXT,
                qty INTEGER,
                entry_price REAL,
                product TEXT,
                order_time TEXT,
                stop_loss REAL,
                target_1 REAL,
                target_2 REAL,
                strategies_bullish TEXT,
                strategies_bearish TEXT,
                regime TEXT,
                vix REAL,
                wow_factors TEXT,
                status TEXT DEFAULT 'OPEN',
                exit_price REAL,
                exit_time TEXT,
                exit_reason TEXT,
                pnl REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS manual_trade_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                timestamp TEXT,
                price REAL,
                pnl REAL,
                pnl_pct REAL,
                trailing_sl REAL,
                event TEXT
            );
        """)
        # Broker-protection columns (added idempotently for existing DBs)
        for col, typ in (("sl_gtt_id", "TEXT"), ("target_gtt_id", "TEXT"),
                         ("hwm", "REAL"), ("protected", "INTEGER"),
                         ("current_price", "REAL"), ("pnl_pct", "REAL")):
            try:
                conn.execute(f"ALTER TABLE manual_trades ADD COLUMN {col} {typ}")
            except Exception:
                pass  # already exists
        conn.commit()
        conn.close()
    
    def _load_known_orders(self):
        """Load already-processed order IDs from DB."""
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            rows = conn.execute("SELECT order_id FROM manual_trades").fetchall()
            self._known_orders = {r[0] for r in rows}
            conn.close()
            logger.info("Loaded %d known manual orders", len(self._known_orders))
        except Exception:
            pass

    def _load_open_trades(self):
        """
        Reload still-OPEN trades into active tracking on startup so trailing and
        exit-cancellation resume after a restart — and so a position that's
        already protected is NOT protected again (protected flag is restored).
        """
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM manual_trades WHERE status='OPEN'").fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                trade = ManualTrade(
                    order_id   = d["order_id"], symbol = d["symbol"],
                    exchange   = d.get("exchange") or "NSE",
                    side       = d.get("side") or "BUY",
                    qty        = int(d.get("qty") or 0),
                    entry_price= float(d.get("entry_price") or 0),
                    product    = d.get("product") or "INTRADAY",
                    order_time = d.get("order_time") or "",
                )
                trade.stop_loss     = float(d.get("stop_loss") or 0)
                trade.target_1      = float(d.get("target_1") or 0)
                trade.trailing_sl   = trade.stop_loss
                trade.sl_gtt_id     = d.get("sl_gtt_id") or ""
                trade.target_gtt_id = d.get("target_gtt_id") or ""
                trade.hwm           = float(d.get("hwm") or 0)
                trade.protected     = bool(d.get("protected"))
                self._active_trades[trade.order_id] = trade
            if rows:
                logger.info("Resumed %d open manual trade(s) from DB", len(rows))
        except Exception as e:
            logger.debug("_load_open_trades: %s", e)

    # ── Order Book Polling ────────────────────────────────────────────────
    
    def poll_order_book(self) -> List[ManualTrade]:
        """Check Angel order book for new fills not placed by our bot."""
        new_trades = []
        
        if not self._angel or not self._angel.obj:
            return new_trades
        
        try:
            with self._angel._lock:
                book = self._angel.obj.orderBook()
            
            if not book or not book.get("data"):
                return new_trades
            
            for order in book["data"]:
                oid = str(order.get("orderid", ""))
                status = str(order.get("orderstatus", "")).upper()
                
                # Skip: already processed
                if oid in self._known_orders:
                    continue
                
                # Skip: not filled
                if status not in ("COMPLETE", "TRADED"):
                    continue
                
                # Skip: placed by our bot (has algo tag)
                tag = str(order.get("tag", "") or order.get("ordertag", "") or "")
                if BOT_ORDER_TAG in tag.upper():
                    self._known_orders.add(oid)
                    continue
                
                # NEW MANUAL TRADE detected!
                symbol = order.get("tradingsymbol", "")
                exchange = order.get("exchange", "NSE")
                side = order.get("transactiontype", "BUY")
                qty = int(order.get("filledshares", 0) or order.get("quantity", 0) or 0)
                price = float(order.get("averageprice", 0) or order.get("price", 0) or 0)
                product = order.get("producttype", "INTRADAY")
                order_time = order.get("updatetime", "") or order.get("ordertime", "")
                
                if qty <= 0 or price <= 0:
                    continue
                
                trade = ManualTrade(
                    order_id=oid,
                    symbol=symbol,
                    exchange=exchange,
                    side=side,
                    qty=qty,
                    entry_price=price,
                    product=product,
                    order_time=order_time,
                )
                
                new_trades.append(trade)
                self._known_orders.add(oid)
                logger.info("MANUAL TRADE DETECTED: %s %s %d @ %.2f", side, symbol, qty, price)
        
        except Exception as e:
            logger.debug("Order book poll: %s", e)
        
        return new_trades
    
    # ── AI Strategy Analysis ──────────────────────────────────────────────
    
    def analyze_trade(self, trade: ManualTrade) -> ManualTrade:
        """Run all 63 strategies on the symbol to find SL, target, context."""
        logger.info("Analyzing %s %s...", trade.side, trade.symbol)
        
        # Get market data
        df = None
        try:
            from data_fetcher import DataFetcher
            fetcher = DataFetcher(angel=self._angel, paper_trade=False)
            df = fetcher.get_market_data(trade.symbol, interval="5m", days=5)
        except Exception as e:
            logger.debug("Data fetch for analysis: %s", e)
        
        if df is None or len(df) < 5:
            # Fallback: use entry price for basic SL/target
            atr_est = trade.entry_price * 0.015  # estimate 1.5% ATR
            if trade.side == "BUY":
                trade.stop_loss = round(trade.entry_price - 2 * atr_est, 2)
                trade.target_1 = round(trade.entry_price + 2 * atr_est, 2)
                trade.target_2 = round(trade.entry_price + 3.5 * atr_est, 2)
            else:
                trade.stop_loss = round(trade.entry_price + 2 * atr_est, 2)
                trade.target_1 = round(trade.entry_price - 2 * atr_est, 2)
                trade.target_2 = round(trade.entry_price - 3.5 * atr_est, 2)
            trade.trailing_sl = trade.stop_loss
            return self._apply_option_premium_plan(trade)
        
        # Run indicator cache
        try:
            from indicator_cache import get_indicators
            ind = get_indicators(df, trade.symbol)
        except Exception:
            ind = {}
        
        close = df["close"].iloc[-1] if "close" in df.columns else trade.entry_price
        
        # ATR-based SL and target
        atr = float(ind.get("atr_14", df["close"].rolling(14).std()).iloc[-1]) if "atr_14" in ind else trade.entry_price * 0.015
        
        if trade.side == "BUY":
            trade.stop_loss = round(close - 2 * atr, 2)
            trade.target_1 = round(close + 2 * atr, 2)
            trade.target_2 = round(close + 3.5 * atr, 2)
        else:
            trade.stop_loss = round(close + 2 * atr, 2)
            trade.target_1 = round(close - 2 * atr, 2)
            trade.target_2 = round(close - 3.5 * atr, 2)
        
        trade.trailing_sl = trade.stop_loss
        trade.breakeven_price = trade.entry_price
        
        # Strategy alignment check
        bullish = []
        bearish = []
        
        # RSI
        rsi = float(ind.get("rsi_14", [50])[-1]) if "rsi_14" in ind else 50
        if rsi > 55: bullish.append(f"RSI {rsi:.0f} bullish")
        elif rsi < 45: bearish.append(f"RSI {rsi:.0f} bearish")
        
        # MACD
        if "macd_hist" in ind:
            macd_h = float(ind["macd_hist"].iloc[-1])
            if macd_h > 0: bullish.append("MACD bullish")
            else: bearish.append("MACD bearish")
        
        # EMA trend
        if "ema_20" in ind and "ema_50" in ind:
            e20 = float(ind["ema_20"].iloc[-1])
            e50 = float(ind["ema_50"].iloc[-1])
            if e20 > e50: bullish.append("EMA 20>50 uptrend")
            else: bearish.append("EMA 20<50 downtrend")
        
        # Bollinger position
        if "bb_pct" in ind:
            bb = float(ind["bb_pct"].iloc[-1])
            if bb > 0.8: bearish.append(f"BB overbought {bb:.0%}")
            elif bb < 0.2: bullish.append(f"BB oversold {bb:.0%}")
        
        # SuperTrend
        if "supertrend_lower" in ind:
            st = float(ind["supertrend_lower"].iloc[-1])
            if close > st: bullish.append("SuperTrend BUY")
            else: bearish.append("SuperTrend SELL")
        
        # VWAP
        if "vwap" in ind:
            vwap = float(ind["vwap"].iloc[-1])
            if close > vwap: bullish.append("Above VWAP")
            else: bearish.append("Below VWAP")
        
        # Volume
        if "vol_ratio" in ind:
            vr = float(ind["vol_ratio"].iloc[-1])
            if vr > 1.5: bullish.append(f"Volume {vr:.1f}x surge")
        
        # ADX trend strength
        if "adx" in ind:
            adx = float(ind["adx"].iloc[-1])
            if adx > 25: bullish.append(f"ADX {adx:.0f} strong trend")
        
        # Stochastic
        if "stoch_k" in ind:
            sk = float(ind["stoch_k"].iloc[-1])
            if sk > 80: bearish.append(f"Stoch overbought {sk:.0f}")
            elif sk < 20: bullish.append(f"Stoch oversold {sk:.0f}")
        
        trade.strategies_bullish = bullish
        trade.strategies_bearish = bearish
        
        # WOW factors
        wow = []
        if len(bullish) >= 5:
            wow.append(f"STRONG: {len(bullish)}/9 indicators bullish")
        if len(bearish) >= 5:
            wow.append(f"WARNING: {len(bearish)}/9 indicators bearish")
        
        # FII signal
        try:
            from strategy_score_tracker import get_fii_dii_history
            fii = get_fii_dii_history(5)
            if fii:
                net = sum(f.get("fii_net", 0) for f in fii[:3])
                if net > 1000: wow.append("FII buying last 3 days")
                elif net < -1000: wow.append("FII selling last 3 days")
        except Exception:
            pass
        
        # VIX
        try:
            from market_intelligence_hub import get_composite_sentiment
            sent = get_composite_sentiment()
            if sent:
                trade.vix = sent.get("vix", 0)
                wow.append(f"Sentiment: {sent.get('score',50)}/100")
        except Exception:
            pass
        
        trade.wow_factors = wow
        self._add_pattern_context(trade, df)
        
        # R:R ratio
        rr = abs(trade.target_1 - trade.entry_price) / abs(trade.entry_price - trade.stop_loss) if trade.stop_loss != trade.entry_price else 0
        alignment = len(bullish) if str(trade.side).upper() == "BUY" else len(bearish)
        
        logger.info("Analysis: SL=%.2f T1=%.2f T2=%.2f R:R=%.1f align=%d/%d",
                    trade.stop_loss, trade.target_1, trade.target_2, rr,
                    alignment, len(bullish) + len(bearish))

        return self._apply_option_premium_plan(trade)
    
    # ── Telegram Channel ──────────────────────────────────────────────────
    
    def send_channel(self, text: str):
        """Send message to dedicated manual trades channel."""
        # Main-chat send only when a manual channel is configured (CHANNEL_ID).
        # When it isn't, we still fall through to the Guardian mirror below so
        # text alerts aren't dropped just because there's no main-chat target.
        if BOT_TOKEN and CHANNEL_ID and not _is_main_chat(CHANNEL_ID):
            try:
                import urllib.request, urllib.parse
                data = urllib.parse.urlencode({
                    "chat_id": CHANNEL_ID,
                    "text": text,
                    "parse_mode": "HTML",
                }).encode()
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    data=data, timeout=10,
                )
            except Exception as e:
                logger.debug("Channel send: %s", e)
        # Mirror to the dedicated manual-trade (Guardian) bot if configured — but
        # never when it resolves to the automated bot's own chat.
        if GUARDIAN_BOT_TOKEN and GUARDIAN_CHAT_ID and not _is_main_chat(GUARDIAN_CHAT_ID):
            try:
                import urllib.request, urllib.parse
                data = urllib.parse.urlencode({
                    "chat_id": GUARDIAN_CHAT_ID, "text": text,
                    "parse_mode": "HTML"}).encode()
                urllib.request.urlopen(
                    f"https://api.telegram.org/bot{GUARDIAN_BOT_TOKEN}/sendMessage",
                    data=data, timeout=10)
            except Exception as e:
                logger.debug("Guardian send: %s", e)

    def _send_photo(self, path: str, caption: str = ""):
        """Send an image to the manual channel and the Guardian bot."""
        targets = []
        if BOT_TOKEN and CHANNEL_ID and not _is_main_chat(CHANNEL_ID):
            targets.append((BOT_TOKEN, CHANNEL_ID))
        if GUARDIAN_BOT_TOKEN and GUARDIAN_CHAT_ID and not _is_main_chat(GUARDIAN_CHAT_ID):
            targets.append((GUARDIAN_BOT_TOKEN, GUARDIAN_CHAT_ID))
        if not targets:
            return
        try:
            import requests
            for tok, chat in targets:
                try:
                    with open(path, "rb") as fh:
                        requests.post(
                            f"https://api.telegram.org/bot{tok}/sendPhoto",
                            data={"chat_id": chat, "caption": caption,
                                  "parse_mode": "HTML"},
                            files={"photo": fh}, timeout=20)
                except Exception as e:
                    logger.debug("sendPhoto: %s", e)
        except Exception as e:
            logger.debug("photo send: %s", e)

    def _send_trade_card(self, trade: ManualTrade, caption: str = ""):
        """Render a status image for a trade and push it to Telegram."""
        try:
            from trade_card import render_trade_card
            ltp = trade.current_price or trade.entry_price
            extra = ""
            if trade.sl_gtt_id or trade.target_gtt_id:
                extra = "GTT SL+target at broker · survives crash"
            path = render_trade_card(
                symbol=trade.symbol, side=trade.side, qty=trade.qty,
                entry=trade.entry_price, ltp=ltp,
                sl=trade.stop_loss or trade.trailing_sl, target=trade.target_1,
                pnl=trade.pnl, pnl_pct=trade.pnl_pct,
                out_path=f"trade_card_{trade.order_id.replace('/', '_')}.png",
                extra=extra)
            self._send_photo(path, caption or f"<b>{trade.symbol}</b>")
        except Exception as e:
            logger.debug("trade card: %s", e)

    def send_trade_detected(self, trade: ManualTrade):
        """Send detailed trade detection + AI analysis to channel."""
        is_long = trade.side == "BUY"
        icon = "\U0001f7e2" if is_long else "\U0001f534"
        total = trade.entry_price * trade.qty
        
        rr = abs(trade.target_1 - trade.entry_price) / abs(trade.entry_price - trade.stop_loss) if trade.stop_loss != trade.entry_price else 0
        gain_pct = abs(trade.target_1 - trade.entry_price) / trade.entry_price * 100
        loss_pct = abs(trade.entry_price - trade.stop_loss) / trade.entry_price * 100
        
        bull_count = len(trade.strategies_bullish)
        bear_count = len(trade.strategies_bearish)
        total_ind = bull_count + bear_count
        alignment = bull_count if str(trade.side).upper() == "BUY" else bear_count
        align_pct = alignment / total_ind * 100 if total_ind > 0 else 50
        
        align_icon = "\u2705" if align_pct >= 60 else "\u26a0\ufe0f" if align_pct >= 40 else "\u274c"
        
        msg = f"""{'━' * 32}
{icon} <b>MANUAL TRADE DETECTED</b>
{'━' * 32}

  {trade.side} <b>{trade.symbol}</b> x {trade.qty}
  Entry: \u20b9{trade.entry_price:,.2f}  |  Total: \u20b9{total:,.0f}
  Product: {trade.product}
  Time: {trade.order_time}

  \U0001f916 <b>AI ANALYSIS</b>
  \u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  \u2502 Stop Loss:     \u20b9{trade.stop_loss:,.2f} (-{loss_pct:.1f}%)
  \u2502 Target 1:      \u20b9{trade.target_1:,.2f} (+{gain_pct:.1f}%)
  \u2502 Target 2:      \u20b9{trade.target_2:,.2f}
  \u2502 Trailing SL:   {OPTION_TRAIL_PCT:.0%} from option high
  \u2502 Break-even at: +{OPTION_BREAKEVEN_PCT:.0%}
  \u2502 R:R Ratio:     1:{rr:.1f}
  \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

  \U0001f4ca <b>STRATEGY ALIGNMENT</b> {align_icon} {alignment}/{total_ind} ({align_pct:.0f}%)"""
        
        # Bullish indicators
        if trade.strategies_bullish:
            msg += "\n  <b>Bullish:</b>"
            for s in trade.strategies_bullish[:6]:
                msg += f"\n  \u2502 \u2705 {s}"
        
        # Bearish indicators
        if trade.strategies_bearish:
            msg += "\n  <b>Bearish:</b>"
            for s in trade.strategies_bearish[:6]:
                msg += f"\n  \u2502 \u274c {s}"
        
        # WOW factors
        if trade.wow_factors:
            msg += f"\n\n  \u2728 <b>WOW FACTORS</b>"
            for w in trade.wow_factors[:5]:
                msg += f"\n  \u2502 {w}"
        
        msg += f"""

{'━' * 32}
  \u23f0 {datetime.now().strftime('%H:%M:%S')} | Tracking started
  \U0001f4f1 Updates every 15 min
{'━' * 32}"""

        self.send_channel(msg)
        self._send_trade_card(trade, "🆕 <b>Manual trade detected & protected</b>")

    def send_update(self, trade: ManualTrade):
        """Send periodic P&L update."""
        is_long = trade.side == "BUY"
        icon = "\U0001f7e2" if trade.pnl >= 0 else "\U0001f534"
        be = " \U0001f512 BE" if trade.breakeven_activated else ""
        t1 = " T1\u2713" if trade.t1_hit else ""
        
        msg = (f"\U0001f4ca <b>UPDATE</b> — {trade.symbol}{be}{t1}\n"
               f"  Now: \u20b9{trade.current_price:,.2f}  |  P&L: \u20b9{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)\n"
               f"  High: \u20b9{trade.highest_since_entry:,.2f}  |  Trail SL: \u20b9{trade.trailing_sl:,.2f}\n"
               f"  Duration: {self._duration(trade.order_time)}")

        self.send_channel(msg)
        self._send_trade_card(trade, "📊 <b>Status update</b>")

    def send_exit(self, trade: ManualTrade):
        """Send exit notification."""
        icon = "\U0001f7e2" if trade.pnl >= 0 else "\U0001f534"
        
        bull = len(trade.strategies_bullish)
        bear = len(trade.strategies_bearish)
        correct = bull if (trade.side == "BUY" and trade.pnl > 0) or (trade.side == "SELL" and trade.pnl > 0) else bear
        
        msg = f"""{icon} <b>MANUAL TRADE CLOSED</b>
  {trade.symbol}  {trade.side} \u2192 {'SELL' if trade.side == 'BUY' else 'BUY'}
  Entry: \u20b9{trade.entry_price:,.2f}  \u2192  Exit: \u20b9{trade.exit_price:,.2f}
  P&L: \u20b9{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)
  Duration: {self._duration(trade.order_time)}
  Reason: {trade.exit_reason}
  
  Strategy accuracy: {correct}/{bull + bear} aligned"""

        self.send_channel(msg)
        self._send_trade_card(trade, "🏁 <b>Trade closed</b>")
        self._record_learning(trade)

    def _record_learning(self, trade: ManualTrade) -> None:
        """Record this closed trade's context+outcome for the edge learner."""
        try:
            from manual_learning import record_outcome
            slp = OPTION_SL_PCT if self._is_option(trade) else EQUITY_SL_PCT
            r_mult = (trade.pnl_pct / (slp * 100)) if slp else 0.0
            bull = len(trade.strategies_bullish)
            bear = len(trade.strategies_bearish)
            tot  = bull + bear
            align = (bull if trade.side == "BUY" else bear)
            align_pct = (100.0 * align / tot) if tot else 50.0
            overnight = False
            try:
                ot = str(trade.order_time)[:10]
                overnight = bool(ot) and ot != date.today().isoformat()
            except Exception:
                pass
            entry_hour = None
            m = re.search(r"[ T](\d{2}):", str(trade.order_time))
            if m:
                entry_hour = int(m.group(1))
            record_outcome({
                "order_id": trade.order_id, "symbol": trade.symbol,
                "side": trade.side, "is_option": self._is_option(trade),
                "entry": trade.entry_price, "exit": trade.exit_price,
                "pnl": trade.pnl, "pnl_pct": trade.pnl_pct, "r_multiple": r_mult,
                "regime": trade.regime, "vix": trade.vix,
                "alignment_pct": align_pct, "dte": self._dte_for(trade),
                "held_overnight": overnight, "entry_hour": entry_hour,
            })
        except Exception as e:
            logger.debug("record_learning: %s", e)
    
    # ── Live Price Tracking ───────────────────────────────────────────────
    
    def update_prices(self):
        """Update current prices for all active trades."""
        for oid, trade in list(self._active_trades.items()):
            try:
                ltp = None
                if self._angel:
                    ltp = self._angel.get_ltp(trade.symbol, trade.exchange)
                
                if not ltp or ltp <= 0:
                    continue
                
                trade.current_price = ltp
                is_long = trade.side == "BUY"
                
                # Update high/low
                if ltp > trade.highest_since_entry:
                    trade.highest_since_entry = ltp
                if ltp < trade.lowest_since_entry:
                    trade.lowest_since_entry = ltp
                
                # P&L
                if is_long:
                    trade.pnl = (ltp - trade.entry_price) * trade.qty
                    trade.pnl_pct = (ltp - trade.entry_price) / trade.entry_price * 100
                else:
                    trade.pnl = (trade.entry_price - ltp) * trade.qty
                    trade.pnl_pct = (trade.entry_price - ltp) / trade.entry_price * 100
                
                # PRIMARY intraday stop: underlying structure break (long
                # options). On a real adverse spot move we market-square-off now;
                # check_exits (same cycle) finalizes the close. Premium GTT stays
                # as the deep catastrophe floor underneath.
                if self._check_struct_stop(trade, ltp):
                    continue

                self._manage_option_psychology(trade, ltp)
                self._apply_option_flow_guard(trade, ltp)

                if trade.sl_gtt_id:
                    # Broker GTT is the real stop \u2014 trail it at the broker. The
                    # actual exit is detected by check_exits (position gone), so
                    # we never fake-close a position that is still open.
                    self._adjust_protection(trade, ltp)
                else:
                    # Legacy software-only advisory trailing (no broker SL).
                    # Break-even activation (+1%)
                    if not trade.breakeven_activated and trade.pnl_pct >= 1.0:
                        trade.breakeven_activated = True
                        trade.trailing_sl = trade.entry_price
                        self.send_channel(
                            f"\U0001f512 <b>BREAKEVEN</b> {trade.symbol}\n"
                            f"  SL moved to entry: \u20b9{trade.entry_price:,.2f}")

                    # Trailing SL update
                    if trade.breakeven_activated:
                        if is_long:
                            new_trail = trade.highest_since_entry * 0.995  # 0.5% trail
                            if new_trail > trade.trailing_sl:
                                trade.trailing_sl = new_trail
                        else:
                            new_trail = trade.lowest_since_entry * 1.005
                            if new_trail < trade.trailing_sl:
                                trade.trailing_sl = new_trail

                    # Check SL hit (advisory close \u2014 no broker order exists)
                    sl_hit = (is_long and ltp <= trade.trailing_sl) or (not is_long and ltp >= trade.trailing_sl)
                    if sl_hit and trade.breakeven_activated:
                        trade.exit_price = ltp
                        trade.exit_time = datetime.now().isoformat()
                        trade.exit_reason = f"Trailing SL \u20b9{trade.trailing_sl:.2f}"
                        trade.status = "CLOSED"
                        self.send_exit(trade)
                        self._save_trade(trade)
                        del self._active_trades[oid]
                
                # Check T1 hit
                if not trade.t1_hit:
                    t1_hit = (is_long and ltp >= trade.target_1) or (not is_long and ltp <= trade.target_1)
                    if t1_hit:
                        trade.t1_hit = True
                        self.send_channel(
                            f"\U0001f4b0 <b>TARGET 1 HIT</b> {trade.symbol}\n"
                            f"  \u20b9{trade.target_1:,.2f} reached! Book partial profits")

                # Persist live price/P&L so the Guardian bot's /manual command
                # can render a current status card from the DB.
                if oid in self._active_trades:
                    self._save_trade(trade)

            except Exception as e:
                logger.debug("Price update %s: %s", trade.symbol, e)
    
    def check_exits(self):
        """Check if any manual trades were closed (exit detected in order book)."""
        if not self._angel or not self._angel.obj:
            return
        
        try:
            with self._angel._lock:
                positions = self._angel.obj.position()
            
            if not positions or not positions.get("data"):
                return
            
            # Build current position map
            current = {}
            for p in positions["data"]:
                sym = p.get("tradingsymbol", "")
                qty = int(p.get("netqty", 0) or 0)
                current[sym] = qty
            
            # Check each active trade
            for oid, trade in list(self._active_trades.items()):
                angel_qty = current.get(trade.symbol, 0)
                
                # If position is gone or reduced → trade was closed (manual exit,
                # or one of our GTTs fired). Cancel any leftover GTT so the other
                # leg isn't orphaned at the broker.
                if angel_qty == 0 and trade.status == "OPEN":
                    self._cancel_protection(trade)
                    trade.exit_price = trade.current_price or trade.entry_price
                    trade.exit_time = datetime.now().isoformat()
                    trade.exit_reason = "Exit detected (manual or GTT)"
                    trade.status = "CLOSED"
                    self.send_exit(trade)
                    self._save_trade(trade)
                    del self._active_trades[oid]
        
        except Exception as e:
            logger.debug("Exit check: %s", e)

    def _bot_open_symbols(self) -> set:
        """Symbols the algo bot currently holds — so we don't mis-tag them as
        manual. Best-effort; if unavailable, treat everything as manual."""
        syms = set()
        try:
            conn = sqlite3.connect("trades.db", check_same_thread=False)
            for (s,) in conn.execute(
                "SELECT symbol FROM trades WHERE status='OPEN'").fetchall():
                if s:
                    syms.add(str(s).upper())
            conn.close()
        except Exception:
            pass
        return syms

    def sync_open_positions(self) -> List[ManualTrade]:
        """
        Sync any open broker position that isn't already tracked.

        poll_order_book only catches a trade at the *moment* it fills, so a fill
        that happened while the tracker was down/blind (e.g. the 04:27 Angel
        outage) would never be picked up. This reconciles against the live
        position book every cycle, so an untracked manual position is adopted
        within a minute.
        """
        new_trades: List[ManualTrade] = []
        if not self._angel or not self._angel.obj:
            return new_trades
        try:
            with self._angel._lock:
                positions = self._angel.obj.position()
            if not positions or not positions.get("data"):
                return new_trades
            bot_syms     = self._bot_open_symbols()
            active_syms  = {t.symbol for t in self._active_trades.values()}
            for p in positions["data"]:
                sym = str(p.get("tradingsymbol", "")).strip()
                qty = int(p.get("netqty", 0) or 0)
                if not sym or qty == 0:
                    continue
                pos_id = f"POS-{sym}"
                if (pos_id in self._known_orders or sym in active_syms
                        or sym.upper() in bot_syms):
                    continue
                _avg_key = "totalbuyavgprice" if qty > 0 else "totalsellavgprice"
                avg = float(p.get(_avg_key, 0) or 0)
                if avg <= 0:
                    avg = float(p.get("averageprice", 0)
                                or p.get("netprice", 0)
                                or p.get("ltp", 0) or 0)
                if avg <= 0:
                    continue
                trade = ManualTrade(
                    order_id   = pos_id,
                    symbol     = sym,
                    exchange   = p.get("exchange", "NSE"),
                    side       = "BUY" if qty > 0 else "SELL",
                    qty        = abs(qty),
                    entry_price= avg,
                    product    = p.get("producttype", "INTRADAY"),
                    order_time = datetime.now().isoformat(),
                )
                new_trades.append(trade)
                self._known_orders.add(pos_id)
                logger.info("MANUAL POSITION SYNCED: %s %s %d @ %.2f",
                            trade.side, sym, abs(qty), avg)
        except Exception as e:
            logger.debug("sync_open_positions: %s", e)
        return new_trades

    # ── Broker-side protection (GTT SL + target) ──────────────────────────

    def _is_option(self, trade: ManualTrade) -> bool:
        # Derivatives trade on NFO/BFO. The symbol check requires a DIGIT before
        # CE/PE (a strike) so equities ending in CE/PE (e.g. RELIANCE) aren't
        # mistaken for options.
        if str(trade.exchange).upper() in ("NFO", "BFO"):
            return True
        return bool(re.search(r"\d(CE|PE)$", str(trade.symbol).upper()))

    def _expected_underlying_side(self, trade: ManualTrade) -> str:
        """
        Direction in the underlying that benefits this manual trade.
        BUY CE and SELL PE want the underlying up; BUY PE and SELL CE want it down.
        """
        sym = str(trade.symbol or "").upper()
        side = str(trade.side or "BUY").upper()
        if self._is_option(trade):
            if sym.endswith("CE"):
                return "BUY" if side == "BUY" else "SELL"
            if sym.endswith("PE"):
                return "SELL" if side == "BUY" else "BUY"
        return "BUY" if side == "BUY" else "SELL"

    def _add_pattern_context(self, trade: ManualTrade, df) -> None:
        """Add chart/candlestick pattern context to manual-trade alignment."""
        if df is None or len(df) < 30:
            return
        expected = str(trade.side or "BUY").upper()
        try:
            from chart_patterns import run_chart_pattern_strategy
            pat = run_chart_pattern_strategy(df, symbol=trade.symbol) or {}
            direction = str(pat.get("direction") or pat.get("side") or "").upper()
            score = float(pat.get("score", 0) or 0)
            name = str(pat.get("pattern") or pat.get("strategy") or "chart_pattern")
            if direction in {"BUY", "SELL"} and score > 0:
                note = f"Chart pattern {name} {direction} score {score:.1f}"
                if direction == "BUY":
                    trade.strategies_bullish.append(note)
                else:
                    trade.strategies_bearish.append(note)
                if direction != expected and score >= 5.0:
                    trade.wow_factors.append(f"WARNING: pattern opposes trade ({name} {direction})")
        except Exception as e:
            logger.debug("chart pattern context %s: %s", trade.symbol, e)

        try:
            from candlestick_patterns import latest_pattern_summary
            cs = latest_pattern_summary(df) or {}
            sig = cs.get("signals", {}) or {}
            bullish = int(sig.get("bullish_count", 0) or 0)
            bearish = int(sig.get("bearish_count", 0) or 0)
            strongest = sig.get("strongest_pattern")
            if bullish or bearish:
                if bullish > bearish:
                    trade.strategies_bullish.append(
                        f"Candle pattern {strongest or 'bullish'} ({bullish} bullish)")
                elif bearish > bullish:
                    trade.strategies_bearish.append(
                        f"Candle pattern {strongest or 'bearish'} ({bearish} bearish)")
        except Exception as e:
            logger.debug("candlestick context %s: %s", trade.symbol, e)

        self._add_underlying_pattern_context(trade)

    def _add_underlying_pattern_context(self, trade: ManualTrade) -> None:
        """For options, add pattern context from the underlying chart too."""
        if not self._is_option(trade):
            return
        try:
            und = self._underlying_for(trade)
            if not und:
                return
            df = self._fetch_candles(und[0], und[1])
            if df is None or len(df) < 30:
                return
            expected = self._expected_underlying_side(trade)
            from chart_patterns import run_chart_pattern_strategy
            pat = run_chart_pattern_strategy(df, symbol=und[0]) or {}
            direction = str(pat.get("direction") or pat.get("side") or "").upper()
            score = float(pat.get("score", 0) or 0)
            name = str(pat.get("pattern") or pat.get("strategy") or "underlying_pattern")
            if direction in {"BUY", "SELL"} and score > 0:
                note = f"Underlying pattern {name} {direction} score {score:.1f}"
                if direction == expected:
                    trade.wow_factors.append(f"UNDERLYING SUPPORT: {note}")
                elif score >= 5.0:
                    trade.wow_factors.append(f"WARNING: underlying pattern opposes trade ({name} {direction})")
        except Exception as e:
            logger.debug("underlying pattern context %s: %s", trade.symbol, e)

    def _apply_option_premium_plan(self, trade: ManualTrade) -> ManualTrade:
        """Use deterministic premium rules for options after analysis."""
        if not self._is_option(trade):
            return trade
        entry = float(trade.entry_price or 0)
        if entry <= 0:
            return trade
        if trade.side == "BUY":
            trade.stop_loss   = round(entry * (1 - OPTION_SL_PCT), 2)
            trade.target_1    = round(entry * (1 + OPTION_TGT_PCT), 2)
            trade.target_2    = round(entry * (1 + OPTION_TGT2_PCT), 2)
        else:
            trade.stop_loss   = round(entry * (1 + OPTION_SL_PCT), 2)
            trade.target_1    = round(entry * (1 - OPTION_TGT_PCT), 2)
            trade.target_2    = round(entry * (1 - OPTION_TGT2_PCT), 2)
        trade.trailing_sl = trade.stop_loss
        trade.breakeven_price = entry
        return trade

    def _apply_fast_protection_plan(self, trade: ManualTrade) -> ManualTrade:
        """
        Compute immediate deterministic SL/target levels without candle fetches.

        This is intentionally simple and fast: it lets a newly detected manual
        trade get broker-side protection first, then the heavier AI/context pass
        can run after the position already has a safety net.
        """
        entry = float(trade.entry_price or 0)
        if entry <= 0:
            return trade
        if self._is_option(trade):
            return self._apply_option_premium_plan(trade)
        if trade.side == "BUY":
            trade.stop_loss = round(entry * (1 - EQUITY_SL_PCT), 2)
            trade.target_1 = round(entry * (1 + EQUITY_TGT_PCT), 2)
            trade.target_2 = round(entry * (1 + EQUITY_TGT_PCT * 1.75), 2)
        else:
            trade.stop_loss = round(entry * (1 + EQUITY_SL_PCT), 2)
            trade.target_1 = round(entry * (1 - EQUITY_TGT_PCT), 2)
            trade.target_2 = round(entry * (1 - EQUITY_TGT_PCT * 1.75), 2)
        trade.trailing_sl = trade.stop_loss
        trade.breakeven_price = entry
        return trade

    def _entry_alignment_pct(self, trade: ManualTrade) -> float:
        bull = len(trade.strategies_bullish)
        bear = len(trade.strategies_bearish)
        total = bull + bear
        if total <= 0:
            return 50.0
        aligned = bull if str(trade.side).upper() == "BUY" else bear
        return aligned / total * 100.0

    def _send_option_entry_guard(self, trade: ManualTrade) -> None:
        """FOMO/chase warning immediately after an option is detected."""
        if not self._is_option(trade) or trade.order_id in self._fomo_warned:
            return
        self._fomo_warned.add(trade.order_id)
        align_pct = self._entry_alignment_pct(trade)
        if align_pct >= 40:
            return
        self.send_channel(
            f"⚠️ <b>FOMO GUARD</b> — {trade.symbol}\n"
            f"  Entry alignment is weak ({align_pct:.0f}%).\n"
            "  Bot will protect this trade, but avoid adding more lots unless "
            "fresh signal alignment improves.")

    def _option_meta(self, trade: ManualTrade) -> dict:
        sym = str(trade.symbol or "").upper()
        meta = {"underlying": self._underlying_root(sym), "strike": 0.0, "type": ""}
        m = re.search(r"(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$", sym)
        if m:
            meta["strike"] = float(m.group(2))
            meta["type"] = m.group(3)
            return meta
        m = re.search(r"(\d+)(CE|PE)$", sym)
        if m:
            meta["strike"] = float(m.group(1))
            meta["type"] = m.group(2)
        return meta

    def _maybe_refresh_option_chain(self, underlying: str) -> None:
        if not OPTION_REFRESH_CHAIN or not underlying:
            return
        now = time.time()
        if now - self._chain_refresh_ts.get(underlying, 0) < OPTION_FLOW_CHECK_SECS:
            return
        self._chain_refresh_ts[underlying] = now
        try:
            from option_chain_recorder import record_option_chain_snapshot
            record_option_chain_snapshot(underlying)
        except Exception as e:
            logger.debug("manual option chain refresh %s: %s", underlying, e)

    def _latest_option_chain_rows(self, underlying: str):
        try:
            conn = sqlite3.connect("option_chain_snapshots.db", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT snapshot_time, spot, pcr_oi, pcr_change_oi, rows_json
                FROM option_chain_snapshots
                WHERE upper(underlying)=? AND ok=1
                ORDER BY ts DESC
                LIMIT 1
                """,
                (str(underlying).upper(),),
            ).fetchone()
            conn.close()
            if not row:
                return {}, []
            rows = json.loads(row["rows_json"] or "[]")
            return dict(row), [r for r in rows if isinstance(r, dict)]
        except Exception as e:
            logger.debug("latest option chain rows %s: %s", underlying, e)
            return {}, []

    @staticmethod
    def _row_value(row: dict, *keys: str) -> float:
        for key in keys:
            try:
                return float(row.get(key, 0) or 0)
            except Exception:
                pass
        return 0.0

    def _record_option_price_tick(self, trade: ManualTrade, ltp: float) -> float:
        hist = self._price_hist.setdefault(trade.order_id, [])
        now = time.time()
        hist.append((now, float(ltp)))
        cutoff = now - max(OPTION_SPIKE_WINDOW_SECS * 2, 600)
        hist[:] = [(ts, px) for ts, px in hist if ts >= cutoff]
        ref = now - OPTION_SPIKE_WINDOW_SECS
        old = [px for ts, px in hist if ts <= ref]
        if not old:
            return 0.0
        base = old[-1]
        if base <= 0:
            return 0.0
        return (float(ltp) - base) / base

    def _option_flow_score(self, trade: ManualTrade, snap: dict, rows: list) -> tuple[float, list[str]]:
        meta = self._option_meta(trade)
        opt_type = meta.get("type") or ("CE" if str(trade.symbol).upper().endswith("CE") else "PE")
        strike = float(meta.get("strike") or 0)
        if not rows or not opt_type:
            return 0.0, ["no OI rows"]
        row = min(rows, key=lambda r: abs(self._row_value(r, "strikePrice", "strike", "strike_price") - strike)) if strike else {}
        prefix = "CE" if opt_type == "CE" else "PE"
        other = "PE" if prefix == "CE" else "CE"
        side_oi = self._row_value(row, f"{prefix}_openInterest", f"{prefix}_OI")
        side_chg = self._row_value(row, f"{prefix}_changeinOpenInterest", f"{prefix}_CHG_OI")
        side_vol = self._row_value(row, f"{prefix}_totalTradedVolume", f"{prefix}_VOLUME")
        other_oi = self._row_value(row, f"{other}_openInterest", f"{other}_OI")
        max_vol = max(
            [self._row_value(r, f"{prefix}_totalTradedVolume", f"{prefix}_VOLUME") for r in rows] + [1.0]
        )
        ce_wall = max(rows, key=lambda r: self._row_value(r, "CE_openInterest", "CE_OI"))
        pe_wall = max(rows, key=lambda r: self._row_value(r, "PE_openInterest", "PE_OI"))
        call_wall = self._row_value(ce_wall, "strikePrice", "strike", "strike_price")
        put_wall = self._row_value(pe_wall, "strikePrice", "strike", "strike_price")
        pcr_chg = float(snap.get("pcr_change_oi") or 0)

        score = 0.0
        reasons: list[str] = []
        if side_vol >= 0.70 * max_vol:
            score += 0.6
            reasons.append("high traded volume at strike")
        if side_chg > 0 and side_oi > 0:
            score += 0.4
            reasons.append("fresh OI at traded strike")
        if other_oi > side_oi * 1.25:
            score -= 0.5
            reasons.append("opposite side OI wall dominates this strike")

        if opt_type == "CE":
            if pcr_chg >= 1.0:
                score += 0.7
                reasons.append("change-OI PCR supports calls")
            elif 0 < pcr_chg < 0.75:
                score -= 0.8
                reasons.append("change-OI PCR opposes calls")
            if call_wall and strike >= call_wall:
                score -= 0.6
                reasons.append(f"near/above call wall {call_wall:.0f}")
        else:
            if 0 < pcr_chg <= 1.0:
                score += 0.7
                reasons.append("change-OI PCR supports puts")
            elif pcr_chg > 1.25:
                score -= 0.8
                reasons.append("change-OI PCR opposes puts")
            if put_wall and strike <= put_wall:
                score -= 0.6
                reasons.append(f"near/below put wall {put_wall:.0f}")

        return score, reasons[:4]

    def _apply_option_flow_guard(self, trade: ManualTrade, ltp: float) -> None:
        if not self._is_option(trade) or ltp <= 0:
            return
        spike = self._record_option_price_tick(trade, ltp)
        if spike >= OPTION_SPIKE_PCT and self._flow_stage.get(trade.order_id, "") != "SPIKE":
            lock_sl = trade.entry_price + (ltp - trade.entry_price) * 0.60 if trade.side == "BUY" else trade.entry_price - (trade.entry_price - ltp) * 0.60
            self._tighten_option_sl(trade, round(lock_sl, 2))
            self._flow_stage[trade.order_id] = "SPIKE"
            self.send_channel(
                f"⚡ <b>OPTION SPIKE CAPTURE</b> — {trade.symbol}\n"
                f"  Premium jumped {spike * 100:.0f}% in ~{int(OPTION_SPIKE_WINDOW_SECS/60)}m.\n"
                "  SL tightened to protect the spike. Avoid chasing extra lots.")

        now = time.time()
        if now - self._flow_ts.get(trade.order_id, 0) < OPTION_FLOW_CHECK_SECS:
            return
        self._flow_ts[trade.order_id] = now
        meta = self._option_meta(trade)
        underlying = str(meta.get("underlying") or "").upper()
        if not underlying:
            return
        self._maybe_refresh_option_chain(underlying)
        snap, rows = self._latest_option_chain_rows(underlying)
        if not rows:
            return
        score, reasons = self._option_flow_score(trade, snap, rows)
        dte = self._dte_for(trade)
        expiry_risk = dte is not None and 0 <= dte <= OPTION_EXPIRY_TIGHTEN_DTE
        if expiry_risk and trade.pnl_pct > 0:
            lock_sl = trade.entry_price + (ltp - trade.entry_price) * 0.50 if trade.side == "BUY" else trade.entry_price - (trade.entry_price - ltp) * 0.50
            self._tighten_option_sl(trade, round(lock_sl, 2))
        if score <= OPTION_FLOW_WEAK_SCORE or expiry_risk:
            stage = f"FLOW_{int(score * 10)}_{dte}"
            if self._flow_stage.get(trade.order_id) == stage:
                return
            self._flow_stage[trade.order_id] = stage
            if trade.pnl_pct >= 0:
                lock_sl = trade.entry_price + (ltp - trade.entry_price) * 0.40 if trade.side == "BUY" else trade.entry_price - (trade.entry_price - ltp) * 0.40
                self._tighten_option_sl(trade, round(lock_sl, 2))
            self.send_channel(
                f"📊 <b>OPTION FLOW GUARD</b> — {trade.symbol}\n"
                f"  OI/volume score: {score:+.2f} | DTE: {dte if dte is not None else '?'}\n"
                f"  {'; '.join(reasons) if reasons else 'flow neutral'}\n"
                "  Action: tighten/protect; no averaging unless flow improves.")

    def _ltp_for(self, trade: ManualTrade) -> float:
        try:
            if self._angel:
                v = self._angel.get_ltp(trade.symbol, trade.exchange)
                if isinstance(v, tuple):
                    v = v[-1]
                return float(v or 0)
        except Exception:
            pass
        return 0.0

    def _active_gtts_for(self, symbol: str) -> list:
        """Active (NEW/ACTIVE) GTT rules at the broker for a symbol."""
        try:
            with self._angel._lock:
                g = self._angel.obj.gttLists(status=["NEW", "ACTIVE"], page=1, count=50)
            data = g.get("data") if isinstance(g, dict) else None
            return [x for x in (data or [])
                    if str(x.get("tradingsymbol")) == symbol
                    and str(x.get("status")).upper() in ("NEW", "ACTIVE")]
        except Exception:
            return []

    def _compute_levels(self, trade: ManualTrade):
        """(sl_trigger, target_trigger) sized for the instrument type. For
        options the GTT SL is the DEEP catastrophe level (broker crash/gap
        floor); the structural live stop does the real intraday work."""
        entry = float(trade.entry_price)
        if self._is_option(trade):
            # Only widen to the catastrophe floor when the structural stop is
            # actually armed (we can watch this trade's underlying). If we can't
            # resolve/fetch the underlying, keep the tight premium GTT so no
            # trade is left under-protected.
            armed = STRUCT_STOP_ENABLED and self._underlying_for(trade) is not None
            slp = CATASTROPHE_SL_PCT if armed else OPTION_SL_PCT
            tgp = OPTION_TGT_PCT
        else:
            slp, tgp = EQUITY_SL_PCT, EQUITY_TGT_PCT
        if trade.side == "BUY":
            return round(entry * (1 - slp), 2), round(entry * (1 + tgp), 2)
        return round(entry * (1 + slp), 2), round(entry * (1 - tgp), 2)

    def _place_protection(self, trade: ManualTrade) -> None:
        """
        Place broker-side GTT SL + target so the position is protected even if
        this process dies (GTT lives at Angel). Hard guard: never place a
        trigger on the wrong side of the current price — that would fire
        instantly and square off the position.
        """
        if not AUTO_PROTECT or trade.protected:
            return
        if not self._angel or not self._angel.obj:
            return
        is_long   = trade.side == "BUY"
        exit_side = "SELL" if is_long else "BUY"

        # Anti-duplicate guard: if GTTs already exist at the broker for this
        # symbol, ADOPT them instead of placing more (prevents the duplicate
        # storm if anything ever mis-parses a placement response).
        existing = self._active_gtts_for(trade.symbol)
        if existing:
            for x in existing:
                trig = float(x.get("triggerprice") or 0)
                gid  = str(x.get("id"))
                is_sl = (trig < trade.entry_price) if is_long else (trig > trade.entry_price)
                if is_sl and not trade.sl_gtt_id:
                    trade.sl_gtt_id = gid; trade.stop_loss = trig; trade.trailing_sl = trig
                elif not is_sl and not trade.target_gtt_id:
                    trade.target_gtt_id = gid; trade.target_1 = trig
            trade.protected = True
            trade.hwm = trade.hwm or trade.current_price or self._ltp_for(trade) or float(trade.entry_price)
            logger.info("Adopted %d existing GTT(s) for %s", len(existing), trade.symbol)
            self._save_trade(trade)
            return
        exch      = str(trade.exchange).upper()
        sl_trig, tgt_trig = self._compute_levels(trade)
        trade.stop_loss   = sl_trig
        trade.target_1    = tgt_trig
        trade.trailing_sl = sl_trig
        ltp = trade.current_price or self._ltp_for(trade) or float(trade.entry_price)
        trade.hwm = ltp
        placed = []

        # SL — only if on the protective side of the current price
        sl_ok = (is_long and sl_trig < ltp) or ((not is_long) and sl_trig > ltp)
        if sl_ok:
            lim = round(sl_trig * (0.99 if is_long else 1.01), 2)
            gid = self._angel.place_gtt_order(
                trade.symbol, trade.qty, sl_trig, lim,
                transaction_type=exit_side, exchange=exch)
            if gid:
                trade.sl_gtt_id = str(gid)
                placed.append(f"SL ₹{sl_trig:.2f}")
        elif trade.order_id not in self._protect_warned:
            self._protect_warned.add(trade.order_id)
            self.send_channel(
                f"⚠️ <b>{trade.symbol}</b>: price ₹{ltp:.2f} already at/through SL "
                f"₹{sl_trig:.2f} — NO broker SL placed, manage manually.")

        # Target — only if beyond the current price
        tgt_ok = (is_long and tgt_trig > ltp) or ((not is_long) and tgt_trig < ltp)
        if tgt_ok:
            lim = round(tgt_trig * (0.99 if is_long else 1.01), 2)
            gid = self._angel.place_gtt_order(
                trade.symbol, trade.qty, tgt_trig, lim,
                transaction_type=exit_side, exchange=exch)
            if gid:
                trade.target_gtt_id = str(gid)
                placed.append(f"Target ₹{tgt_trig:.2f}")

        trade.protected = bool(trade.sl_gtt_id or trade.target_gtt_id)
        if placed:
            logger.info("Protection placed %s: %s", trade.symbol, placed)
            self.send_channel(
                f"🛡 <b>Protection set</b> — {trade.symbol}\n  "
                + "  |  ".join(placed)
                + "\n  GTT at broker — survives app restart/crash.")
        self._save_trade(trade)

    def _replace_sl(self, trade: ManualTrade, new_sl: float) -> None:
        """Move the SL GTT to a tighter level (place new, then cancel old)."""
        is_long   = trade.side == "BUY"
        exit_side = "SELL" if is_long else "BUY"
        lim = round(new_sl * (0.99 if is_long else 1.01), 2)
        new_id = self._angel.place_gtt_order(
            trade.symbol, trade.qty, new_sl, lim,
            transaction_type=exit_side, exchange=str(trade.exchange).upper())
        if not new_id:
            return
        old = trade.sl_gtt_id
        trade.sl_gtt_id   = str(new_id)
        trade.stop_loss   = new_sl
        trade.trailing_sl = new_sl
        if old:
            try:
                self._angel.cancel_gtt_order(old, trade.symbol)
            except Exception:
                pass
        logger.info("SL trailed %s → %.2f", trade.symbol, new_sl)
        self.send_channel(f"🔼 <b>SL trailed</b> {trade.symbol} → ₹{new_sl:.2f}")
        self._save_trade(trade)

    def _replace_target(self, trade: ManualTrade, new_tgt: float) -> None:
        """Extend the target GTT (place new, then cancel old)."""
        is_long   = trade.side == "BUY"
        exit_side = "SELL" if is_long else "BUY"
        lim = round(new_tgt * (0.99 if is_long else 1.01), 2)
        new_id = self._angel.place_gtt_order(
            trade.symbol, trade.qty, new_tgt, lim,
            transaction_type=exit_side, exchange=str(trade.exchange).upper())
        if not new_id:
            return
        old = trade.target_gtt_id
        trade.target_gtt_id = str(new_id)
        trade.target_1 = new_tgt
        if old:
            try:
                self._angel.cancel_gtt_order(old, trade.symbol)
            except Exception:
                pass
        logger.info("Target extended %s → %.2f", trade.symbol, new_tgt)
        self.send_channel(f"🎯 <b>Target extended</b> {trade.symbol} → ₹{new_tgt:.2f}")
        self._save_trade(trade)

    def _tighten_option_sl(self, trade: ManualTrade, new_sl: float) -> bool:
        """Tighten option SL at broker when possible; software-only otherwise."""
        if new_sl <= 0:
            return False
        is_long = trade.side == "BUY"
        ltp = trade.current_price or self._ltp_for(trade)
        if ltp <= 0:
            return False
        if is_long:
            if new_sl <= trade.stop_loss + 0.05 or new_sl >= ltp:
                return False
        elif new_sl >= trade.stop_loss - 0.05 or new_sl <= ltp:
            return False
        if trade.sl_gtt_id and self._angel and self._angel.obj:
            self._replace_sl(trade, round(new_sl, 2))
        else:
            trade.stop_loss = round(new_sl, 2)
            trade.trailing_sl = round(new_sl, 2)
            self._save_trade(trade)
        return True

    def _manage_option_psychology(self, trade: ManualTrade, ltp: float) -> None:
        """
        Option-only live guard: breakeven, trailing SL, and greed profit locks.
        This runs even when broker GTT protection exists, so manual option trades
        are actively managed after detection/sync.
        """
        if not self._is_option(trade) or ltp <= 0:
            return
        is_long = trade.side == "BUY"
        pnl_frac = trade.pnl_pct / 100.0

        if not trade.breakeven_activated and pnl_frac >= OPTION_BREAKEVEN_PCT:
            if self._tighten_option_sl(trade, float(trade.entry_price)):
                trade.breakeven_activated = True
                self.send_channel(
                    f"🔒 <b>OPTION BREAKEVEN LOCKED</b> — {trade.symbol}\n"
                    f"  SL moved to entry ₹{trade.entry_price:.2f} "
                    f"after +{trade.pnl_pct:.1f}% profit.")

        hwm = trade.hwm or trade.highest_since_entry or ltp
        if is_long:
            trade.hwm = max(hwm, ltp)
        else:
            trade.hwm = min(hwm, ltp) if hwm > 0 else ltp

        if trade.breakeven_activated:
            trail = (
                trade.hwm * (1 - OPTION_TRAIL_PCT)
                if is_long else trade.hwm * (1 + OPTION_TRAIL_PCT)
            )
            self._tighten_option_sl(trade, round(trail, 2))

        stages = [
            (1, OPTION_GREED_1_PCT, OPTION_LOCK_1_PCT, "Book partial or lock profit."),
            (2, OPTION_GREED_2_PCT, OPTION_LOCK_2_PCT, "Avoid greed: book major part, trail rest."),
            (3, OPTION_GREED_3_PCT, OPTION_LOCK_3_PCT, "Extreme move: protect most profit now."),
        ]
        current_stage = int(self._greed_stage.get(trade.order_id, 0) or 0)
        for stage, threshold, lock_pct, action in stages:
            if pnl_frac < threshold or stage <= current_stage:
                continue
            profit_pts = abs((trade.hwm or ltp) - trade.entry_price)
            lock_pts = profit_pts * lock_pct
            lock_sl = (
                trade.entry_price + lock_pts
                if is_long else trade.entry_price - lock_pts
            )
            self._tighten_option_sl(trade, round(lock_sl, 2))
            self._greed_stage[trade.order_id] = stage
            self.send_channel(
                f"💰 <b>GREED GUARD {stage}</b> — {trade.symbol}\n"
                f"  P&L: {trade.pnl_pct:+.1f}% | locked about {lock_pct:.0%} "
                f"of peak profit.\n  {action}")

    def _fetch_candles(self, symbol: str, exchange: str,
                       days: int = 5, interval: str = "FIVE_MINUTE"):
        """Fetch recent OHLC candles for any tradingsymbol (Angel getCandleData)."""
        try:
            if not self._angel:
                return None
            to_d   = datetime.now()
            from_d = to_d - timedelta(days=days)
            fmt = "%Y-%m-%d %H:%M"
            return self._angel.get_historical_data(
                symbol, interval=interval,
                from_date=from_d.strftime(fmt), to_date=to_d.strftime(fmt),
                exchange=str(exchange).upper())
        except Exception as e:
            logger.debug("candles %s: %s", symbol, e)
            return None

    def _get_candles(self, trade: ManualTrade):
        """Recent OHLC candles for the traded instrument itself (option premium)."""
        return self._fetch_candles(trade.symbol, str(trade.exchange).upper())

    @staticmethod
    def _underlying_root(symbol: str) -> str:
        """Leading alpha root of an option tradingsymbol
        (NIFTY09JUN2623300CE -> NIFTY, RELIANCE26JUN253000CE -> RELIANCE)."""
        m = re.match(r"^([A-Z]+)\d", str(symbol).upper())
        return m.group(1) if m else ""

    def _underlying_for(self, trade: ManualTrade):
        """Resolve & cache the option's underlying as (symbol, exchange) whose
        candles we can actually fetch — or None if not resolvable. Index spots
        are tried in several name forms because Angel's index symbols vary; if
        none yield candles the trade keeps the tight premium GTT (data-fail
        fallback). Result is cached per trade so we resolve at most once."""
        oid = trade.order_id
        if oid in self._underlying:
            return self._underlying[oid]
        resolved = None
        try:
            if STRUCT_STOP_ENABLED and self._is_option(trade) and trade.side == "BUY":
                root = self._underlying_root(trade.symbol)
                if root:
                    if root in _INDEX_UNDERLYINGS:
                        cands = [(root, "NSE"), (f"{root}-INDEX", "NSE")]
                        if root == "NIFTY":
                            cands.append(("Nifty 50", "NSE"))
                        elif root == "BANKNIFTY":
                            cands.append(("Nifty Bank", "NSE"))
                        if str(trade.exchange).upper() == "BFO":
                            cands.insert(0, (root, "BSE"))
                    else:
                        cands = [(f"{root}-EQ", "NSE"), (root, "NSE")]
                    for sym, exch in cands:
                        df = self._fetch_candles(sym, exch)
                        if df is not None and len(df) >= 20:
                            resolved = (sym, exch)
                            break
        except Exception as e:
            logger.debug("underlying resolve %s: %s", trade.symbol, e)
        self._underlying[oid] = resolved
        if resolved:
            logger.info("Struct-stop ARMED for %s via underlying %s/%s",
                        trade.symbol, resolved[0], resolved[1])
        elif STRUCT_STOP_ENABLED and self._is_option(trade) and trade.side == "BUY":
            logger.info("Struct-stop NOT armed for %s (no underlying candles) — "
                        "keeping tight premium GTT", trade.symbol)
        return resolved

    def _check_struct_stop(self, trade: ManualTrade, ltp: float) -> bool:
        """PRIMARY intraday stop for long options: square off when the UNDERLYING
        breaks structure (closes beyond its most recent swing), so premium/IV
        noise doesn't shake us out but a real adverse spot move does. The broker
        GTT remains the deep catastrophe floor. Returns True if it exited."""
        if not STRUCT_STOP_ENABLED or not self._is_option(trade) or trade.side != "BUY":
            return False
        und = self._underlying_for(trade)
        if not und:
            return False
        now = time.time()
        if now - self._struct_ts.get(trade.order_id, 0) < STRUCT_CHECK_SECS:
            return False
        self._struct_ts[trade.order_id] = now
        df = self._fetch_candles(*und)
        if df is None or len(df) < 20:
            return False
        try:
            import pandas as pd
            from indicators import detect_swing_highs_lows
            cc = next((c for c in ("close", "Close") if c in df.columns), None)
            if not cc:
                return False
            closes = pd.to_numeric(df[cc], errors="coerce").dropna()
            if closes.empty:
                return False
            last_close = float(closes.iloc[-1])
            sh, sl_ = detect_swing_highs_lows(df, STRUCT_SWING_LB)
            is_call = str(trade.symbol).upper().endswith("CE")
            if is_call:
                sv = sl_.dropna()
                if sv.empty:
                    return False
                level = float(sv.iloc[-1])
                broke = last_close < level
                desc = (f"underlying {und[0]} closed {last_close:.2f} "
                        f"< swing-low {level:.2f}")
            else:
                sv = sh.dropna()
                if sv.empty:
                    return False
                level = float(sv.iloc[-1])
                broke = last_close > level
                desc = (f"underlying {und[0]} closed {last_close:.2f} "
                        f"> swing-high {level:.2f}")
            if broke:
                return self._square_off(
                    trade, f"STRUCTURAL STOP — {desc} (premium ₹{ltp:.2f})")
        except Exception as e:
            logger.debug("struct stop %s: %s", trade.symbol, e)
        return False

    def _adjust_protection(self, trade: ManualTrade, ltp: float) -> None:
        """
        Dynamically tighten the SL (and extend the target) using the dynamic-exit
        engine — Chandelier/Supertrend/structure/regime/ratchet. Falls back to a
        simple profit ratchet. The SL only ever TIGHTENS and stays on the
        protective side of price; the broker GTT remains the crash-proof floor.
        """
        if not AUTO_PROTECT or not trade.sl_gtt_id or ltp <= 0:
            return
        is_long = trade.side == "BUY"

        # High-water-mark
        if is_long:
            if ltp > trade.hwm:
                trade.hwm = ltp
        elif trade.hwm == 0 or ltp < trade.hwm:
            trade.hwm = ltp

        # Only TRAIL once the trade is in profit. While underwater the initial
        # SL (the planned max loss) stands — tightening a losing trade's stop
        # just locks the loss in. This is standard trade management.
        in_profit = (ltp > trade.entry_price) if is_long else (ltp < trade.entry_price)
        if not in_profit:
            return

        # Baseline: simple profit ratchet (cheap, always available). Options use
        # a dedicated trailing percentage once profitable; equities use the
        # normal equity SL percentage.
        slp = OPTION_TRAIL_PCT if self._is_option(trade) else EQUITY_SL_PCT
        new_sl  = round(trade.hwm * (1 - slp), 2) if is_long else round(trade.hwm * (1 + slp), 2)
        if self._is_option(trade) and trade.breakeven_activated:
            new_sl = max(new_sl, trade.entry_price) if is_long else min(new_sl, trade.entry_price)
        new_tgt = trade.target_1

        # Dynamic engine — rate-limited (candle fetch is heavier).
        now = time.time()
        if now - self._dyn_ts.get(trade.order_id, 0) >= DYN_RECOMPUTE_SECS:
            self._dyn_ts[trade.order_id] = now
            df = self._get_candles(trade)
            if df is not None:
                try:
                    from dynamic_exit import compute_dynamic_levels
                    lv = compute_dynamic_levels(
                        df, trade.side, trade.entry_price, ltp,
                        current_sl=trade.stop_loss, current_target=trade.target_1,
                        hwm=trade.hwm)
                    if is_long:
                        new_sl = max(new_sl, lv.get("sl", new_sl))
                    else:
                        _d = lv.get("sl", new_sl)
                        new_sl = min(new_sl, _d) if (new_sl > 0 and _d > 0) else (new_sl or _d)
                    new_tgt = lv.get("target", new_tgt) or new_tgt
                    if lv.get("reason"):
                        logger.debug("dyn-exit %s: sl=%.2f tgt=%.2f (%s)",
                                     trade.symbol, new_sl, new_tgt, lv["reason"])
                except Exception as e:
                    logger.debug("dynamic exit: %s", e)

        # Apply SL tightening (protective side, never insta-trigger)
        if is_long and new_sl > trade.stop_loss + 0.05 and new_sl < ltp:
            self._replace_sl(trade, new_sl)
        elif (not is_long) and 0 < new_sl < trade.stop_loss - 0.05 and new_sl > ltp:
            self._replace_sl(trade, new_sl)

        # Apply target extension (let profit run; never pull target closer)
        if trade.target_gtt_id and new_tgt:
            if (is_long and new_tgt > trade.target_1 + 0.05) or \
               ((not is_long) and 0 < new_tgt < trade.target_1 - 0.05):
                self._replace_target(trade, new_tgt)

    def _cancel_protection(self, trade: ManualTrade) -> None:
        """Cancel any remaining GTTs (on exit) so nothing is orphaned."""
        for gid in (trade.sl_gtt_id, trade.target_gtt_id):
            if gid:
                try:
                    self._angel.cancel_gtt_order(gid, trade.symbol)
                except Exception:
                    pass
        trade.sl_gtt_id = ""
        trade.target_gtt_id = ""

    # ── End-of-day hold/close decision ────────────────────────────────────

    def _dte_for(self, trade: ManualTrade):
        """Days-to-expiry parsed from an option symbol ({dd}{MMM}{yy}), else None."""
        m = re.search(r"(\d{2})([A-Z]{3})(\d{2})\d+(CE|PE)$",
                      str(trade.symbol).upper())
        if not m:
            return None
        try:
            d = datetime.strptime(
                f"{m.group(1)}{m.group(2)}20{m.group(3)}", "%d%b%Y").date()
            return (d - date.today()).days
        except Exception:
            return None

    def _square_off(self, trade: ManualTrade, reason: str) -> bool:
        """Market-close the position (real order) and cancel its GTTs."""
        if not self._angel or not self._angel.obj:
            return False
        exit_side = "SELL" if trade.side == "BUY" else "BUY"
        # Close with the SAME product type as the position. A CARRYFORWARD/NRML
        # long is NOT netted by an INTRADAY sell — the broker reads it as a NEW
        # short, demands margin, and rejects it (insufficient funds). That, plus
        # the missing status-update, is why 206 auto-close orders all failed and
        # the position never closed. Use trade.product (captured at detection).
        _prod = (getattr(trade, "product", None) or "INTRADAY").upper()
        try:
            _res = self._angel.place_order(
                trade.symbol, trade.qty, exit_side,
                order_type="MARKET", producttype=_prod)
        except Exception as e:
            logger.error("square_off %s: %s", trade.symbol, e)
            return False
        # place_order returns (order_id, fill_price) — take the id.
        oid = _res[0] if isinstance(_res, (list, tuple)) else _res
        if not oid:
            return False
        self._cancel_protection(trade)
        # CRITICAL: mark the trade CLOSED, persist it, and drop it from active
        # tracking. Without this, the next cycle re-evaluated the same "open"
        # trade and fired ANOTHER close order every ~60s — a 200+ order re-sell
        # loop on the live account. _save_trade persists CLOSED so a restart
        # doesn't reload it as OPEN and resume the loop.
        trade.exit_time   = datetime.now().isoformat()
        trade.exit_reason = reason
        trade.status      = "CLOSED"
        try:
            self._save_trade(trade)
        except Exception as e:
            logger.error("save closed trade %s failed: %s", trade.symbol, e)
        self._active_trades.pop(getattr(trade, "order_id", None), None)
        logger.info("AUTO-CLOSED %s — %s", trade.symbol, reason)
        self.send_channel(
            f"🔴 <b>AUTO-CLOSED</b> {trade.symbol} {exit_side} {trade.qty}\n  {reason}")
        return True

    def _run_eod_check(self) -> None:
        """Near the close, recommend HOLD / CLOSE / TIGHTEN per open trade."""
        if not EOD_CHECK_ENABLED or not self._active_trades:
            return
        today = date.today().isoformat()
        if self._eod_done_date == today:
            return
        self._eod_done_date = today
        try:
            from exit_decision import eod_recommendation
        except Exception as e:
            logger.debug("eod import: %s", e)
            return
        for trade in list(self._active_trades.values()):
            dte = self._dte_for(trade)
            df  = self._get_candles(trade)
            ltp = trade.current_price or trade.entry_price
            rec = eod_recommendation(
                trade.side, trade.entry_price, ltp, trade.pnl_pct, dte, df,
                self._is_option(trade))
            emoji = {"CLOSE": "🔴", "HOLD": "🟢", "TIGHTEN": "🟠"}.get(
                rec["action"], "ℹ️")
            # Empirical edge from this trader's own closed-trade history.
            edge_line = ""
            try:
                from manual_learning import get_bias
                bias = get_bias({"is_option": self._is_option(trade),
                                 "held_overnight": True})
                if bias:
                    edge_line = f"\n  📚 {bias['note']}"
            except Exception:
                pass
            self.send_channel(
                f"{emoji} <b>EOD DECISION — {trade.symbol}</b>\n"
                f"  Recommendation: <b>{rec['action']}</b>"
                f"  ({rec.get('urgency', '')})\n"
                f"  P&L: ₹{trade.pnl:+,.0f} ({trade.pnl_pct:+.1f}%)  |  "
                f"DTE: {dte if dte is not None else '?'}\n"
                f"  {rec['reason']}{edge_line}")
            self._send_trade_card(trade, f"{emoji} EOD: {rec['action']}")
            # HARD rule: long option with DTE<=threshold is force-closed at EOD,
            # regardless of the auto-close flag — no overnight expiry-week risk.
            force = (self._is_option(trade) and trade.side == "BUY"
                     and dte is not None and 0 <= dte <= EOD_FORCE_CLOSE_DTE)
            if force:
                if self._square_off(
                        trade, f"EOD HARD close — long option DTE={dte}, "
                               "no overnight expiry-week gap risk"):
                    continue
            if AUTO_CLOSE_EOD and rec["action"] == "CLOSE":
                self._square_off(trade, "EOD auto-close — " + rec["reason"])

    # ── Persistence ───────────────────────────────────────────────────────
    
    def _save_trade(self, trade: ManualTrade):
        """Save trade to database."""
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute(
                "INSERT OR REPLACE INTO manual_trades "
                "(order_id,symbol,exchange,side,qty,entry_price,product,order_time,"
                "stop_loss,target_1,target_2,strategies_bullish,strategies_bearish,"
                "regime,vix,wow_factors,status,exit_price,exit_time,exit_reason,pnl,"
                "sl_gtt_id,target_gtt_id,hwm,protected,current_price,pnl_pct) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade.order_id, trade.symbol, trade.exchange, trade.side,
                 trade.qty, trade.entry_price, trade.product, trade.order_time,
                 trade.stop_loss, trade.target_1, trade.target_2,
                 json.dumps(trade.strategies_bullish),
                 json.dumps(trade.strategies_bearish),
                 trade.regime, trade.vix, json.dumps(trade.wow_factors),
                 trade.status, trade.exit_price, trade.exit_time,
                 trade.exit_reason, trade.pnl,
                 trade.sl_gtt_id, trade.target_gtt_id, trade.hwm,
                 1 if trade.protected else 0,
                 trade.current_price, trade.pnl_pct)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Save trade: %s", e)
    
    def _save_update(self, trade: ManualTrade, event: str):
        """Save price update to DB."""
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.execute(
                "INSERT INTO manual_trade_updates "
                "(order_id,timestamp,price,pnl,pnl_pct,trailing_sl,event) "
                "VALUES (?,?,?,?,?,?,?)",
                (trade.order_id, datetime.now().isoformat(),
                 trade.current_price, trade.pnl, trade.pnl_pct,
                 trade.trailing_sl, event)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    
    def _duration(self, entry_time: str) -> str:
        """Calculate duration since entry."""
        try:
            # Try parsing various formats
            for fmt in ("%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
                try:
                    start = datetime.strptime(entry_time, fmt)
                    if start.year < 2000:
                        start = start.replace(year=datetime.now().year,
                                            month=datetime.now().month,
                                            day=datetime.now().day)
                    break
                except ValueError:
                    continue
            else:
                return "?"
            delta = datetime.now() - start
            mins = int(delta.total_seconds() / 60)
            if mins < 60: return f"{mins}m"
            return f"{mins // 60}h {mins % 60}m"
        except Exception:
            return "?"
    
    # ── Main Loop ─────────────────────────────────────────────────────────
    
    def run(self):
        """Main loop: poll → detect → analyze → track → report."""
        self._running = True
        last_update_time = 0
        last_watch_log   = 0

        logger.info("Manual Trade Tracker started")
        if CHANNEL_ID or (GUARDIAN_BOT_TOKEN and GUARDIAN_CHAT_ID):
            _angel_ok = bool(self._angel and self._angel.obj)
            if _angel_ok:
                self.send_channel(
                    "\U0001f440 <b>MANUAL TRADE TRACKER ONLINE</b>\n\n"
                    "  ✅ Connected to Angel One\n"
                    "  Monitoring your Angel One order book\n"
                    "  Manual trades will be detected and analyzed\n"
                    f"  Polling every {POLL_INTERVAL}s\n"
                    f"  Updates every {UPDATE_INTERVAL // 60}min\n\n"
                    f"  Option rules: SL {OPTION_SL_PCT:.0%}, BE +{OPTION_BREAKEVEN_PCT:.0%}, "
                    f"trail {OPTION_TRAIL_PCT:.0%}, T1 +{OPTION_TGT_PCT:.0%}\n"
                    "  Place a trade on Angel app — I'll handle the rest")
            elif self._in_market_hours() or self._active_trades:
                # Don't claim ONLINE when blind — say so and note auto-retry.
                self._angel_down_alerted = True
                self.send_channel(
                    "\U0001f440 <b>MANUAL TRADE TRACKER STARTED — DEGRADED</b>\n\n"
                    "  ⚠️ NO Angel connection yet\n"
                    "  Manual trades are NOT being monitored.\n"
                    f"  Auto-retrying every {int(self._ANGEL_RECONNECT_SECS)}s — "
                    "you'll get a ✅ when it connects.")
        
        while self._running:
            try:
                now = datetime.now()
                # Market hours 9:15–15:30; we still RECONCILE positions outside
                # them (so an open/carry-forward trade is always synced) but slow
                # the loop when the market is closed and nothing is open.
                in_hours = (
                    (now.hour > 9 or (now.hour == 9 and now.minute >= 15))
                    and (now.hour < 15 or (now.hour == 15 and now.minute <= 30))
                )

                # 0. Make sure we have a live Angel session (recovers from a
                #    failed startup login). Gentle, rate-limited — see _ensure_angel.
                self._ensure_angel()

                # 1. Detect new trades — from the order book (fresh fills) AND by
                #    reconciling open positions (catches fills missed while down).
                new_trades = self.poll_order_book() + self.sync_open_positions()
                for trade in new_trades:
                    # 2. Immediate broker-side safety first. Do not wait for
                    #    candle/API-heavy analysis before placing SL + target.
                    trade = self._apply_fast_protection_plan(trade)
                    self._save_trade(trade)
                    self._place_protection(trade)
                    protected_sl = trade.stop_loss
                    protected_tgt = trade.target_1
                    protected_trail = trade.trailing_sl

                    # 3. AI analysis/context after the safety net is already at
                    #    the broker. Preserve the live broker triggers so alerts
                    #    and DB remain consistent with actual protection orders.
                    trade = self.analyze_trade(trade)
                    if trade.sl_gtt_id:
                        trade.stop_loss = protected_sl
                        trade.trailing_sl = protected_trail
                    if trade.target_gtt_id:
                        trade.target_1 = protected_tgt
                    # 4. Save to DB
                    self._save_trade(trade)
                    # 5. Send to channel
                    self.send_trade_detected(trade)
                    self._send_option_entry_guard(trade)
                    # 5b. If first protection failed because Angel/LTP was
                    #     temporarily unavailable, try again after analysis.
                    self._place_protection(trade)
                    # 6. Add to active tracking
                    self._active_trades[trade.order_id] = trade

                # 5a. Protect any open trade that isn't protected yet — covers
                #     trades resumed from DB on restart, and earlier syncs.
                if AUTO_PROTECT and self._angel and self._angel.obj:
                    for _t in list(self._active_trades.values()):
                        if not _t.protected:
                            self._place_protection(_t)

                # 5b. Per-minute "watching" heartbeat so it's visible the tracker
                #     is alive and what it's tracking.
                if time.time() - last_watch_log >= 60:
                    logger.info("Watching: %d open manual trade(s) | angel=%s",
                                len(self._active_trades),
                                "up" if (self._angel and self._angel.obj) else "DOWN")
                    last_watch_log = time.time()

                # 6. Update prices for active trades
                if self._active_trades:
                    self.update_prices()
                    
                    # 7. Check for exits
                    self.check_exits()

                    # 7b. End-of-day hold/close decision (once, ~3:10–3:25 PM)
                    if now.hour == 15 and 10 <= now.minute <= 25:
                        self._run_eod_check()

                    # 8. Send periodic updates (every 15 min)
                    if time.time() - last_update_time >= UPDATE_INTERVAL:
                        for trade in self._active_trades.values():
                            self.send_update(trade)
                            self._save_update(trade, "periodic")
                        last_update_time = time.time()

                # Poll fast during market hours or while tracking a live trade;
                # otherwise reconcile slowly (once a minute).
                time.sleep(POLL_INTERVAL if (in_hours or self._active_trades) else 60)

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error("Main loop error: %s", e)
                time.sleep(30)
        
        self._running = False
        logger.info("Manual Trade Tracker stopped")
    
    def stop(self):
        self._running = False


# ── Standalone entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    tracker = ManualTradeTracker()
    tracker.run()
