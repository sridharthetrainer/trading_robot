from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv

def _env(key: str, default: str = "") -> str:
    """Get env var, stripping inline comments (e.g. 'value  # comment')."""
    val = os.getenv(key, default)
    if val and "#" in val:
        val = val.split("#")[0]
    return val.strip()

def _fenv(key: str, default: float) -> float:
    """Get env var as float, safe against inline comments."""
    return float(_env(key, str(default)))

def _ienv(key: str, default: int) -> int:
    """Get env var as int, safe against inline comments."""
    return int(_env(key, str(default)))

def _benv(key: str, default: bool) -> bool:
    """Get env var as bool, safe against inline comments."""
    v = _env(key, str(default)).lower()
    return v in ("1", "true", "yes", "on")


load_dotenv(override=True)  # .env always overrides shell env vars

# =============================================================================
# PROJECT / ENV
# =============================================================================

PROJECT_NAME = "autonomous_trading_system"
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev").lower()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")

BASE_DIR = Path(os.getenv("PROJECT_BASE_DIR", ".")).resolve()

# =============================================================================
# MODE
# =============================================================================

# Default false — scanning always needs real connection for data
# Paper mode only affects ORDER PLACEMENT, not data fetch
PAPER_TRADING = _env("PAPER_TRADING", "false").lower() == "true"
# Auto-init paper balance if insufficient
_paper_bal_raw = float(os.getenv("PAPER_CAPITAL", "0") or 0)
if PAPER_TRADING and _paper_bal_raw < 25000:
    os.environ["PAPER_CAPITAL"] = "100000"
    import logging as _lg
    _lg.getLogger(__name__).info("Initialised paper balance to ₹1,00,000")
PAPER_TRADE = PAPER_TRADING
_ENABLE_REAL_TRADING_REQUESTED = _env("ENABLE_REAL_TRADING", "false").lower() == "true"
ENABLE_REAL_TRADING = bool(_ENABLE_REAL_TRADING_REQUESTED and not PAPER_TRADING)
if _ENABLE_REAL_TRADING_REQUESTED and PAPER_TRADING:
    import logging as _lg
    _lg.getLogger(__name__).warning(
        "ENABLE_REAL_TRADING=true ignored because PAPER_TRADING=true"
    )

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEBUG_MODE = _env("DEBUG_MODE", "true").lower() == "true"
DRY_RUN_ORDERS = _env("DRY_RUN_ORDERS", "false").lower() == "true"

# =============================================================================
# BROKER (ANGEL ONE PRIMARY)
# =============================================================================

API_KEY = os.getenv("API_KEY")
CLIENT_ID = os.getenv("CLIENT_ID")
PASSWORD = os.getenv("PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

# Optional secondary / future broker support
DHAN_CLIENT_CODE = os.getenv("DHAN_CLIENT_CODE")
DHAN_TOKEN_ID = os.getenv("DHAN_TOKEN_ID")
UPSTOX_ANALYTICS_TOKEN = os.getenv("UPSTOX_ANALYTICS_TOKEN")
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")
OPTION_CHAIN_PROVIDER_ORDER = os.getenv("OPTION_CHAIN_PROVIDER_ORDER", "upstox,dhan")

PRIMARY_BROKER = os.getenv("PRIMARY_BROKER", "angel").lower()
SECONDARY_BROKER = os.getenv("SECONDARY_BROKER", "").lower().strip() or None
ENABLE_BROKER_FAILOVER = _env("ENABLE_BROKER_FAILOVER", "true").lower() == "true"

# =============================================================================
# TELEGRAM ALERTS
# =============================================================================

TELEGRAM_ENABLED = _env("TELEGRAM_ENABLED", "true").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_SCALPING_CHANNEL_ID = os.getenv("TELEGRAM_SCALPING_CHANNEL_ID", "").strip()

ENABLE_STARTUP_ALERT = True
ENABLE_SHUTDOWN_ALERT = True
ENABLE_HEARTBEAT_ALERTS = True
HEARTBEAT_INTERVAL_MIN = 30

# =============================================================================
# MARKET SETTINGS
# =============================================================================

EXCHANGE = os.getenv("EXCHANGE", "NSE")
DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE") or None

DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "NIFTY")
DEFAULT_INTERVAL = os.getenv("DEFAULT_INTERVAL", "5m")
DEFAULT_HTF_INTERVAL = os.getenv("DEFAULT_HTF_INTERVAL", "15m")

TRADE_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("TRADE_SYMBOLS", "NIFTY,BANKNIFTY").split(",")
    if s.strip()
]

# Universe tiers. Use a larger universe for paper/shadow learning while keeping
# probation/live restricted to the most liquid symbols.
LEARNING_UNIVERSE_MODE = os.getenv("LEARNING_UNIVERSE_MODE", "all").lower()
LEARNING_UNIVERSE_MAX_SYMBOLS = _ienv("LEARNING_UNIVERSE_MAX_SYMBOLS", 220)
LEARNING_UNIVERSE_EXTRA_SYMBOLS = [
    s.strip().upper()
    for s in os.getenv("LEARNING_UNIVERSE_EXTRA_SYMBOLS", "").split(",")
    if s.strip()
]
PROBATION_UNIVERSE = [
    s.strip().upper()
    for s in os.getenv("PROBATION_UNIVERSE", "NIFTY,BANKNIFTY,SENSEX").split(",")
    if s.strip()
]
LIVE_UNIVERSE = [
    s.strip().upper()
    for s in os.getenv("LIVE_UNIVERSE", "").split(",")
    if s.strip()
]

MARKET_START = os.getenv("MARKET_START", "09:15")
MARKET_END = os.getenv("MARKET_END", "15:30")

TRADE_START_BUFFER_MIN = _ienv("TRADE_START_BUFFER_MIN", 5)
TRADE_END_BUFFER_MIN = _ienv("TRADE_END_BUFFER_MIN", 15)
AVOID_LAST_N_MINUTES = TRADE_END_BUFFER_MIN

# Optional explicit no-trade windows
AVOID_OPENING_WINDOW = _env("AVOID_OPENING_WINDOW", "true").lower() == "true"
OPENING_NO_TRADE_START = os.getenv("OPENING_NO_TRADE_START", "09:15")
OPENING_NO_TRADE_END = os.getenv("OPENING_NO_TRADE_END", "09:20")

AVOID_LUNCH_CHOP = _env("AVOID_LUNCH_CHOP", "false").lower() == "true"
LUNCH_NO_TRADE_START = os.getenv("LUNCH_NO_TRADE_START", "13:00")
LUNCH_NO_TRADE_END = os.getenv("LUNCH_NO_TRADE_END", "13:45")

MAIN_LOOP_SLEEP_SEC = _ienv("MAIN_LOOP_SLEEP_SEC", 10)
LOOKBACK_DAYS = _ienv("LOOKBACK_DAYS", 20)
MIN_CANDLES_REQUIRED = _ienv("MIN_CANDLES_REQUIRED", 120)

# Safety override flags
FORCE_MARKET_OPEN = _env("FORCE_MARKET_OPEN", "false").lower() == "true"
FORCE_MARKET_CLOSE = _env("FORCE_MARKET_CLOSE", "false").lower() == "true"

# =============================================================================
# CAPITAL / COST
# =============================================================================

# ── Auto mode switching ────────────────────────────────────────────────────
# Minimum balance required to trade live. Below this → paper mode automatically.
# Set to 0 to disable auto-switching (use PAPER_TRADING flag instead).
AUTO_MODE_SWITCH   = _benv("AUTO_MODE_SWITCH",   True)
MIN_LIVE_CAPITAL   = _fenv("MIN_LIVE_CAPITAL",   25000.0)
REQUIRE_LIVE_ARM   = _benv("REQUIRE_LIVE_ARM",   False)
ALLOW_VALIDATION_BLOCKED_LIVE = _benv("ALLOW_VALIDATION_BLOCKED_LIVE", False)
LIVE_ELIGIBILITY_FILE = os.getenv("LIVE_ELIGIBILITY_FILE", "live_eligibility.json")
LIVE_BALANCE_USE_PCT = _fenv("LIVE_BALANCE_USE_PCT", 0.95)

# Early live probation: tiny live legs for paper-training strategies.
# Disabled by default. This does not bypass broker/dual-mode requirements; it
# only allows a validation-blocked signal to place a capped live leg when the
# probation gate approves.
LIVE_PROBATION_ENABLED = _benv("LIVE_PROBATION_ENABLED", False)
LIVE_PROBATION_STATE_FILE = os.getenv("LIVE_PROBATION_STATE_FILE", "live_probation_state.json")
LIVE_PROBATION_MAX_TRADES_PER_DAY = _ienv("LIVE_PROBATION_MAX_TRADES_PER_DAY", 1)
LIVE_PROBATION_MAX_CAPITAL = _fenv("LIVE_PROBATION_MAX_CAPITAL", 500.0)
LIVE_PROBATION_MAX_LOTS = _ienv("LIVE_PROBATION_MAX_LOTS", 1)
LIVE_PROBATION_MAX_DAILY_LOSS = _fenv("LIVE_PROBATION_MAX_DAILY_LOSS", 500.0)
LIVE_PROBATION_MIN_SCORE = _fenv("LIVE_PROBATION_MIN_SCORE", 0.0)
LIVE_PROBATION_MIN_CONFIDENCE = _fenv("LIVE_PROBATION_MIN_CONFIDENCE", 0.0)
LIVE_PROBATION_OPTIONS_ONLY = _benv("LIVE_PROBATION_OPTIONS_ONLY", False)
LIVE_PROBATION_BLOCK_HERO_ZERO = _benv("LIVE_PROBATION_BLOCK_HERO_ZERO", True)
# When True and ENABLE_REAL_TRADING=True, system auto-switches to live
# when Angel One balance >= MIN_LIVE_CAPITAL

# Capital — set to 0 for auto-detect from Angel One at runtime
# In paper mode this is ignored and PAPER_CAPITAL is used
CAPITAL = _fenv("CAPITAL", 100000.0)
if CAPITAL == 0:
    # Auto-detect signal — will be replaced at runtime by actual balance
    CAPITAL = 100000.0   # safe default until broker reports real balance
    _AUTO_DETECT_CAPITAL = True
else:
    _AUTO_DETECT_CAPITAL = False
DEFAULT_INITIAL_CAPITAL = CAPITAL

PAPER_CAPITAL = _fenv("PAPER_CAPITAL", CAPITAL)
REAL_CAPITAL = _fenv("REAL_CAPITAL", CAPITAL)

DEFAULT_BROKERAGE_PER_ORDER = _fenv("DEFAULT_BROKERAGE_PER_ORDER", 20.0)
BROKERAGE_PER_ORDER = _fenv("BROKERAGE_PER_ORDER", DEFAULT_BROKERAGE_PER_ORDER)

SLIPPAGE_PCT = _fenv("SLIPPAGE_PCT", 0.05)

# ── Signal quality & filters (GA-9) ──────────────────────────────────────────
VIX_MAX_FOR_BUYING         = _fenv("VIX_MAX_FOR_BUYING", 22.0)
AI_FILTER_THRESHOLD        = _fenv("AI_FILTER_THRESHOLD", 2.75)
STATUS_ALERT_INTERVAL_SEC  = _ienv("STATUS_ALERT_INTERVAL_SEC", 900)

# ── Swing trade (GA-9) ────────────────────────────────────────────────────────
SWING_MODE_ENABLED         = _env("SWING_MODE_ENABLED", "true").lower() == "true"
SWING_MIN_SCORE            = _fenv("SWING_MIN_SCORE", 7.0)
SWING_MIN_CONFIDENCE       = _fenv("SWING_MIN_CONFIDENCE", 0.75)
SWING_MIN_DTE              = _ienv("SWING_MIN_DTE", 5)
SWING_STOP_PCT             = _fenv("SWING_STOP_PCT", 0.25)

# ── DTE-aware option stops (GA-9) ─────────────────────────────────────────────
OPTION_STOP_0DTE           = _fenv("OPTION_STOP_0DTE", 0.1)
OPTION_STOP_1DTE           = _fenv("OPTION_STOP_1DTE", 0.15)
OPTION_STOP_2DTE           = _fenv("OPTION_STOP_2DTE", 0.18)
OPTION_STOP_MULT           = _fenv("OPTION_STOP_MULT", 0.2)

# ── Walk-forward validation (GA-9) ────────────────────────────────────────────
WF_TRAIN_DAYS              = _ienv("WF_TRAIN_DAYS", 60)
WF_TEST_DAYS               = _ienv("WF_TEST_DAYS", 30)
WF_MIN_WINDOWS             = _ienv("WF_MIN_WINDOWS", 3)
WF_TOTAL_DAYS              = _ienv("WF_TOTAL_DAYS", 210)

# ── IV rank filter (GA-9) ─────────────────────────────────────────────────────
IV_HIGH_RANK_BLOCK         = _fenv("IV_HIGH_RANK_BLOCK", 0.7)
IV_HISTORY_FILE            = os.getenv("IV_HISTORY_FILE",          "iv_history.json")

# ── Capital compounding (GA-9) ────────────────────────────────────────────────
DRAWDOWN_TRIGGER_PCT       = _fenv("DRAWDOWN_TRIGGER_PCT", 0.15)
DRAWDOWN_RESTORE_PCT       = _fenv("DRAWDOWN_RESTORE_PCT", 0.08)
PROFIT_LOCK_PCT            = _fenv("PROFIT_LOCK_PCT", 0.3)

# ── WebSocket (GA-9) ──────────────────────────────────────────────────────────
WS_RECONNECT_DELAY         = _ienv("WS_RECONNECT_DELAY", 5)
WS_MAX_RECONNECT           = _ienv("WS_MAX_RECONNECT", 10)
WS_SUBSCRIBE_SIGNAL_UNIVERSE = _env("WS_SUBSCRIBE_SIGNAL_UNIVERSE", "true").lower() == "true"


# =============================================================================
# RISK MANAGEMENT
# =============================================================================

# =============================================================================
# CHANGELOG v1.1 — Phase-in settings (C01, C02, C03)
#
# C01: DAILY_LOSS_LIMIT    ₹2,000 (Phase 1, day 0-59) → ₹3,000 (Phase 2, day 60+)
# C02: RISK_PER_TRADE_PCT  0.5%   (Phase 1)           → 1.0%   (Phase 2)
# C03: MAX_PARALLEL_TRADES 3      (Phase 1)           → 5      (Phase 2)
#
# HOW TO ACTIVATE: set LIVE_START_DATE="YYYY-MM-DD" in your .env on go-live day.
# Phase 2 unlocks automatically after 60 calendar days.
# If LIVE_START_DATE is not set, Phase 1 values are always used (conservative).
# =============================================================================

LIVE_START_DATE: str = os.getenv("LIVE_START_DATE", "")   # e.g. "2026-04-01"
PHASE_IN_DAYS: int   = _ienv("PHASE_IN_DAYS", 60)  # days until Phase 2


def _live_days_elapsed() -> int:
    """
    Returns the number of calendar days since LIVE_START_DATE.
    Returns 0 if LIVE_START_DATE is not set or unparseable.
    """
    if not LIVE_START_DATE:
        return 0
    try:
        from datetime import date as _date
        start = _date.fromisoformat(LIVE_START_DATE.strip())
        return max(0, (_date.today() - start).days)
    except Exception:
        return 0


def _is_phase2() -> bool:
    """True when the system has been live for >= PHASE_IN_DAYS days."""
    return _live_days_elapsed() >= PHASE_IN_DAYS


# C01: Daily loss limit — phase-in (overrideable via env)
VAR_LIMIT_PCT  = float(os.getenv("VAR_LIMIT_PCT",  "0.05"))  # 5% capital VaR limit
VAR_CONFIDENCE = float(os.getenv("VAR_CONFIDENCE", "0.95"))  # 95% confidence
MAX_DAILY_LOSS: float = float(
    os.getenv("MAX_DAILY_LOSS",
              "3000" if _is_phase2() else "2000")
)
SOFT_DAILY_LOSS_LIMIT: float = float(
    os.getenv("SOFT_DAILY_LOSS_LIMIT",
              str(MAX_DAILY_LOSS * 0.67))
)
if not PAPER_TRADING:
    # A static rupee limit must never become a catastrophic percentage when a
    # small account is connected. Live mode is capped at 2% of real capital.
    MAX_DAILY_LOSS = min(MAX_DAILY_LOSS, max(1.0, REAL_CAPITAL * 0.02))
    SOFT_DAILY_LOSS_LIMIT = min(SOFT_DAILY_LOSS_LIMIT, MAX_DAILY_LOSS * 0.67)

# C02: Risk per trade — phase-in
RISK_PER_TRADE_PCT: float = float(
    os.getenv("RISK_PER_TRADE_PCT",
              "0.010" if _is_phase2() else "0.005")
)

# C03: Max parallel trades — phase-in
_MAX_PARALLEL_DEFAULT = "5" if _is_phase2() else "3"
MAX_OPEN_POSITIONS: int = _ienv("MAX_OPEN_POSITIONS", int(_MAX_PARALLEL_DEFAULT))
DYNAMIC_MAX_OPEN_POSITIONS: bool = _benv("DYNAMIC_MAX_OPEN_POSITIONS", True)
MIN_DYNAMIC_OPEN_POSITIONS: int = _ienv("MIN_DYNAMIC_OPEN_POSITIONS", 1)
MAX_DYNAMIC_OPEN_POSITIONS: int = _ienv(
    "MAX_DYNAMIC_OPEN_POSITIONS",
    max(MAX_OPEN_POSITIONS, int(_MAX_PARALLEL_DEFAULT), 5),
)
CAPITAL_PER_OPEN_POSITION: float = _fenv("CAPITAL_PER_OPEN_POSITION", 1000.0)

MAX_TRADES_PER_DAY = _ienv("MAX_TRADES_PER_DAY", 6)
MAX_TRADES_PER_SYMBOL_PER_DAY = _ienv("MAX_TRADES_PER_SYMBOL_PER_DAY", 3)
MAX_SIGNALS_PER_CYCLE = _ienv("MAX_SIGNALS_PER_CYCLE", MAX_DYNAMIC_OPEN_POSITIONS)
MAX_CANDIDATES_LOGGED_PER_CYCLE = _ienv("MAX_CANDIDATES_LOGGED_PER_CYCLE", 25)
SHADOW_LOG_STRATEGY_CANDIDATES = _benv("SHADOW_LOG_STRATEGY_CANDIDATES", True)
SHADOW_MAX_CANDIDATES_PER_SYMBOL = _ienv("SHADOW_MAX_CANDIDATES_PER_SYMBOL", 25)

MAX_PORTFOLIO_RISK_PCT = _fenv("MAX_PORTFOLIO_RISK_PCT", 0.03)
MAX_SYMBOL_EXPOSURE_PCT = _fenv("MAX_SYMBOL_EXPOSURE_PCT", 0.25)
MAX_TOTAL_EXPOSURE_PCT = _fenv("MAX_TOTAL_EXPOSURE_PCT", 1.0)
MAX_CORRELATED_POSITIONS = _ienv("MAX_CORRELATED_POSITIONS", 1)

ENABLE_DAILY_LOSS_LOCK = True
ENABLE_CONSECUTIVE_LOSS_LOCK = True
MAX_CONSECUTIVE_LOSSES = _ienv("MAX_CONSECUTIVE_LOSSES", 3)
LOSS_LOCK_COOLDOWN_MIN = _ienv("LOSS_LOCK_COOLDOWN_MIN", 60)

# =============================================================================
# POSITION SIZING
# =============================================================================

MIN_RISK_PCT = _fenv("MIN_RISK_PCT", 0.0025)
MAX_RISK_PCT = _fenv("MAX_RISK_PCT", 0.02)
MIN_LOTS = _ienv("MIN_LOTS", 1)
MAX_LOTS = _ienv("MAX_LOTS", 20)

ENABLE_ADAPTIVE_POSITION_SIZING = os.getenv(
    "ENABLE_ADAPTIVE_POSITION_SIZING", "true"
).lower() == "true"

REDUCE_SIZE_AFTER_LOSS_STREAK = True
LOSS_STREAK_SIZE_REDUCTION_FACTOR = float(
    os.getenv("LOSS_STREAK_SIZE_REDUCTION_FACTOR", "0.5")
)

# =============================================================================
# OPTIONS TRADING
# =============================================================================

TRADE_OPTIONS = _env("TRADE_OPTIONS", "true").lower() == "true"
OPTION_LOT_SIZE = _ienv("OPTION_LOT_SIZE", 65)
STRIKE_INTERVAL = _ienv("STRIKE_INTERVAL", 50)
WEEKLY_EXPIRY_DAY = _ienv("WEEKLY_EXPIRY_DAY", 3)  # Thursday = 3 if Monday=0
OPTION_STRIKE_LADDER_OTM_STEPS = _ienv("OPTION_STRIKE_LADDER_OTM_STEPS", 3)
OPTION_STRIKE_LADDER_ITM_STEPS = _ienv("OPTION_STRIKE_LADDER_ITM_STEPS", 1)
REQUIRE_OPTION_CHAIN_FOR_OPTION_TRADE = _benv("REQUIRE_OPTION_CHAIN_FOR_OPTION_TRADE", True)
AUTONOMOUS_OPTION_FIRST = _benv("AUTONOMOUS_OPTION_FIRST", True)
ENABLE_CASH_STOCK_LAST_RESORT = _benv("ENABLE_CASH_STOCK_LAST_RESORT", True)
CASH_LAST_RESORT_MIN_SCORE = _fenv("CASH_LAST_RESORT_MIN_SCORE", 7.0)
AUTONOMOUS_ALLOWED_STYLES = [
    s.strip().lower()
    for s in os.getenv("AUTONOMOUS_ALLOWED_STYLES", "scalping,intraday,swing,position,hero_zero").split(",")
    if s.strip()
]
ENABLE_PARALLEL_OPTION_STYLES = _benv("ENABLE_PARALLEL_OPTION_STYLES", True)
OPTION_PARALLEL_STYLE_ORDER = [
    s.strip().lower()
    for s in os.getenv("OPTION_PARALLEL_STYLE_ORDER", "scalping,intraday,swing,position,hero_zero").split(",")
    if s.strip()
]
MAX_NEW_TRADES_PER_STYLE_PER_CYCLE = _ienv("MAX_NEW_TRADES_PER_STYLE_PER_CYCLE", 1)
MAX_NEW_TRADES_PER_UNDERLYING_PER_CYCLE = _ienv("MAX_NEW_TRADES_PER_UNDERLYING_PER_CYCLE", 1)
OPTION_STYLE_QTY_MULTIPLIERS = {
    "scalping": _fenv("OPTION_QTY_MULT_SCALPING", 0.75),
    "intraday": _fenv("OPTION_QTY_MULT_INTRADAY", 1.00),
    "swing": _fenv("OPTION_QTY_MULT_SWING", 0.70),
    "position": _fenv("OPTION_QTY_MULT_POSITION", 0.50),
    "hero_zero": _fenv("OPTION_QTY_MULT_HERO_ZERO", 0.25),
}
CASH_LAST_RESORT_QTY_MULTIPLIER = _fenv("CASH_LAST_RESORT_QTY_MULTIPLIER", 0.50)
OPTION_CHAIN_MAX_AGE_SEC = _ienv("OPTION_CHAIN_MAX_AGE_SEC", 180)
MIN_OPTION_ATM_LEG_VOLUME = _fenv("MIN_OPTION_ATM_LEG_VOLUME", 100.0)
MIN_OPTION_ATM_LEG_OI = _fenv("MIN_OPTION_ATM_LEG_OI", 100.0)
MAX_OPTION_ATM_SPREAD_PCT = _fenv("MAX_OPTION_ATM_SPREAD_PCT", 0.20)
MIN_OPTION_EXPECTED_MOVE_PCT = _fenv("MIN_OPTION_EXPECTED_MOVE_PCT", 0.35)
OPTION_EXPECTED_MOVE_USAGE_LIMIT = _fenv("OPTION_EXPECTED_MOVE_USAGE_LIMIT", 0.70)
ENABLE_SELECTED_OPTION_EXECUTION_QUALITY = _benv("ENABLE_SELECTED_OPTION_EXECUTION_QUALITY", True)
REQUIRE_SELECTED_OPTION_LIQUIDITY_FIELDS = _benv("REQUIRE_SELECTED_OPTION_LIQUIDITY_FIELDS", False)
MIN_SELECTED_OPTION_OI = _fenv("MIN_SELECTED_OPTION_OI", 100.0)
MIN_SELECTED_OPTION_VOLUME = _fenv("MIN_SELECTED_OPTION_VOLUME", 100.0)
MAX_SELECTED_OPTION_SPREAD_PCT = _fenv("MAX_SELECTED_OPTION_SPREAD_PCT", 0.20)
MIN_SELECTED_OPTION_PREMIUM = _fenv("MIN_SELECTED_OPTION_PREMIUM", 1.0)
MAX_SELECTED_OPTION_PREMIUM = _fenv("MAX_SELECTED_OPTION_PREMIUM", 5000.0)
OPTION_TRAP_MOVE_MULTIPLE = _fenv("OPTION_TRAP_MOVE_MULTIPLE", 3.0)
OPTION_TRAP_MIN_UNDERLYING_MOVE_PCT = _fenv("OPTION_TRAP_MIN_UNDERLYING_MOVE_PCT", 0.08)
OPTION_EDGE_POLICY_BLOCK_QUARANTINED = _benv("OPTION_EDGE_POLICY_BLOCK_QUARANTINED", True)
OPTION_EDGE_POLICY_REQUIRE_PROMISING = _benv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", True)
OPTION_EDGE_POLICY_PROMISING_SCORE_BONUS = _fenv("OPTION_EDGE_POLICY_PROMISING_SCORE_BONUS", 3.0)
ENABLE_OPTION_EXECUTION_ROUTER = _benv("ENABLE_OPTION_EXECUTION_ROUTER", True)
OPTION_EXECUTION_MAX_SLIPPAGE_PCT = _fenv("OPTION_EXECUTION_MAX_SLIPPAGE_PCT", 0.0075)
OPTION_EXECUTION_WAIT_SEC = _fenv("OPTION_EXECUTION_WAIT_SEC", 4.0)
OPTION_EXECUTION_REPRICE_ATTEMPTS = _ienv("OPTION_EXECUTION_REPRICE_ATTEMPTS", 1)

OPTION_MAX_HOLD_MINUTES = _ienv("OPTION_MAX_HOLD_MINUTES", 120)
CLOSE_OPTIONS_BEFORE_MARKET_END = os.getenv(
    "CLOSE_OPTIONS_BEFORE_MARKET_END", "true"
).lower() == "true"
OPTIONS_EXIT_BUFFER_MIN = _ienv("OPTIONS_EXIT_BUFFER_MIN", 20)

# Dedicated CPR/Camarilla pivot option scalper.
ENABLE_PIVOT_SCALPING_STRATEGY = _benv("ENABLE_PIVOT_SCALPING_STRATEGY", True)
PIVOT_SCALPING_CAPITAL = _fenv("PIVOT_SCALPING_CAPITAL", 30000.0)
PIVOT_SCALPING_MAX_LOTS = _ienv("PIVOT_SCALPING_MAX_LOTS", 2)
PIVOT_SCALPING_UNDERLYINGS = [
    s.strip().upper()
    for s in os.getenv("PIVOT_SCALPING_UNDERLYINGS", "NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX").split(",")
    if s.strip()
]
PIVOT_SCALPING_FETCH_1M = _benv("PIVOT_SCALPING_FETCH_1M", True)
PIVOT_SCALPING_OPTION_STOP_0DTE = _fenv("PIVOT_SCALPING_OPTION_STOP_0DTE", 0.08)
PIVOT_SCALPING_OPTION_TARGET_RR = _fenv("PIVOT_SCALPING_OPTION_TARGET_RR", 1.6)
PIVOT_SCALPING_MAX_HOLD_MINUTES = _ienv("PIVOT_SCALPING_MAX_HOLD_MINUTES", 30)

# =============================================================================
# TECHNICAL SETTINGS
# =============================================================================

RSI_PERIOD = _ienv("RSI_PERIOD", 14)
RSI_BUY_LEVEL = _fenv("RSI_BUY_LEVEL", 35.0)
RSI_SELL_LEVEL = _fenv("RSI_SELL_LEVEL", 65.0)

EMA_FAST = _ienv("EMA_FAST", 20)
EMA_SLOW = _ienv("EMA_SLOW", 50)
EMA_TREND_FILTER = _ienv("EMA_TREND_FILTER", 200)

ADX_PERIOD = _ienv("ADX_PERIOD", 14)
ADX_THRESHOLD = _fenv("ADX_THRESHOLD", 20.0)
ADX_TREND_THRESHOLD = _fenv("ADX_TREND_THRESHOLD", 20.0)
ADX_STRONG_THRESHOLD = _fenv("ADX_STRONG_THRESHOLD", 24.0)
ADX_RANGE_THRESHOLD = _fenv("ADX_RANGE_THRESHOLD", 16.0)

ATR_PERIOD = _ienv("ATR_PERIOD", 14)
VWAP_ENABLED = _env("VWAP_ENABLED", "true").lower() == "true"

# =============================================================================
# STRATEGY DEFAULTS
# =============================================================================

STOP_ATR_MULT = _fenv("STOP_ATR_MULT", 1.5)
TRAIL_ATR_MULT = _fenv("TRAIL_ATR_MULT", 1.0)

MR_STOP_ATR_MULT = _fenv("MR_STOP_ATR_MULT", 1.4)
MR_TRAIL_ATR_MULT = _fenv("MR_TRAIL_ATR_MULT", 0.9)

BREAKOUT_LOOKBACK_BARS = _ienv("BREAKOUT_LOOKBACK_BARS", 20)
TREND_CONFIRM_BARS = _ienv("TREND_CONFIRM_BARS", 1)
MR_REENTRY_COOLDOWN_BARS = _ienv("MR_REENTRY_COOLDOWN_BARS", 3)

ALLOW_LATE_CONTINUATION_ENTRY = os.getenv(
    "ALLOW_LATE_CONTINUATION_ENTRY", "true"
).lower() == "true"
ALLOW_SAME_BAR_FLIP = _env("ALLOW_SAME_BAR_FLIP", "false").lower() == "true"

# =============================================================================
# AUTO STRATEGY SELECTOR
# =============================================================================

ENABLE_AUTO_STRATEGY_SELECTOR = os.getenv(
    "ENABLE_AUTO_STRATEGY_SELECTOR", "true"
).lower() == "true"

STRATEGY_SELECTION_METRIC = os.getenv("STRATEGY_SELECTION_METRIC", "sharpe")
MIN_TRADES_FOR_SELECTION = _ienv("MIN_TRADES_FOR_SELECTION", 5)
STRATEGY_UPDATE_INTERVAL_HOURS = _ienv("STRATEGY_UPDATE_INTERVAL_HOURS", 6)
STRATEGY_STATE_FILE = os.getenv("STRATEGY_STATE_FILE", "strategy_state.json")

AVAILABLE_STRATEGIES = [
    "trend",
    "breakout",
    "mean_reversion",
    "ma_cross",
    "full_system",
]

DEFAULT_STRATEGY = os.getenv("DEFAULT_STRATEGY", "trend")

# =============================================================================
# REGIME / STRATEGY SWITCHING
# =============================================================================

ENABLE_REGIME_SWITCHING = _env("ENABLE_REGIME_SWITCHING", "true").lower() == "true"
ENABLE_DYNAMIC_STRATEGY_SWITCHING = os.getenv(
    "ENABLE_DYNAMIC_STRATEGY_SWITCHING", "true"
).lower() == "true"

REGIME_USE_ADX = True
REGIME_USE_VWAP = True
REGIME_USE_VOLATILITY = True

TREND_REGIME_MIN_ADX = _fenv("TREND_REGIME_MIN_ADX", 20.0)
RANGE_REGIME_MAX_ADX = _fenv("RANGE_REGIME_MAX_ADX", 17.0)
HIGH_VOLATILITY_ATR_PCT = _fenv("HIGH_VOLATILITY_ATR_PCT", 1.2)

ENABLE_STANDBY_MODE = True
NO_SIGNAL_ALERT_INTERVAL_MIN = _ienv("NO_SIGNAL_ALERT_INTERVAL_MIN", 60)

# =============================================================================
# LIVE SIGNAL ENGINE / AI FILTER
# =============================================================================

ENABLE_AI_TRADE_FILTER = _env("ENABLE_AI_TRADE_FILTER", "true").lower() == "true"

AI_MIN_SCORE_THRESHOLD = _fenv("AI_MIN_SCORE_THRESHOLD", 5.0)
AI_TREND_SCORE_THRESHOLD = _fenv("AI_TREND_SCORE_THRESHOLD", 6.0)
AI_MR_SCORE_THRESHOLD = _fenv("AI_MR_SCORE_THRESHOLD", 4.0)
AI_REQUIRE_HTF_ALIGNMENT = _env("AI_REQUIRE_HTF_ALIGNMENT", "true").lower() == "true"

TREND_SCORE_THRESHOLD = _fenv("TREND_SCORE_THRESHOLD", 5.0)
MR_SCORE_THRESHOLD = _fenv("MR_SCORE_THRESHOLD", 3.5)
BREAKOUT_SCORE_THRESHOLD = _fenv("BREAKOUT_SCORE_THRESHOLD", 5.0)

ENABLE_SIGNAL_DIAGNOSTICS = True
ENABLE_BAR_DIAGNOSTICS = True
ENABLE_BLOCK_REASON_LOGGING = True
WRITE_NO_SIGNAL_EVENTS = True

# =============================================================================
# FALLBACK SIGNAL LOGIC
# =============================================================================

ENABLE_SMART_FALLBACK = _env("ENABLE_SMART_FALLBACK", "true").lower() == "true"
FALLBACK_CONFIDENCE = _fenv("FALLBACK_CONFIDENCE", 0.5)
FALLBACK_DIRECTIONAL_SCORE = _fenv("FALLBACK_DIRECTIONAL_SCORE", 1.5)
# Raise fallback threshold — live data shows 41.6% WR at default 2.5 threshold
FALLBACK_SCORE_THRESHOLD = _fenv("FALLBACK_SCORE_THRESHOLD", 5.0)
# Fallback must not open trades until its edge is validated over multiple months.
# Set FALLBACK_TRADE=true in .env only after confirmed positive out-of-sample WR.
FALLBACK_TRADE = _benv("FALLBACK_TRADE", False)
FALLBACK_ENABLE_TREND_CONTINUATION = True
FALLBACK_ENABLE_BREAKOUT_RETEST = True

# =============================================================================
# EXECUTION SAFETY / SMART ROUTER
# =============================================================================

ENABLE_SMART_ORDER_ROUTER = _env("ENABLE_SMART_ORDER_ROUTER", "true").lower() == "true"

MAX_SLIPPAGE_PCT = _fenv("MAX_SLIPPAGE_PCT", 0.5)
MAX_SPREAD_PCT = _fenv("MAX_SPREAD_PCT", 0.8)
LIMIT_PRICE_BUFFER_PCT = _fenv("LIMIT_PRICE_BUFFER_PCT", 0.1)

STALE_TRADE_SEC = _ienv("STALE_TRADE_SEC", 1800)
MAX_PRICE_DRIFT_PCT = _fenv("MAX_PRICE_DRIFT_PCT", 3.0)
MAX_SIGNAL_AGE_SEC  = _ienv("MAX_SIGNAL_AGE_SEC",  90)   # discard signal older than N seconds
MAX_HOLD_MINUTES    = _ienv("MAX_HOLD_MINUTES",     90)   # time-exit zombie positions after N min

PREFER_LIMIT_FOR_OPTIONS = _env("PREFER_LIMIT_FOR_OPTIONS", "true").lower() == "true"
ROUTER_RETRY_ATTEMPTS = _ienv("ROUTER_RETRY_ATTEMPTS", 2)
ROUTER_RETRY_SLEEP_SEC = _fenv("ROUTER_RETRY_SLEEP_SEC", 0.75)

BROKER_FAILURE_COOLDOWN_SEC = _ienv("BROKER_FAILURE_COOLDOWN_SEC", 30)
BROKER_MAX_FAILURES_BEFORE_COOLDOWN = int(
    os.getenv("BROKER_MAX_FAILURES_BEFORE_COOLDOWN", "2")
)

# =============================================================================
# EXECUTION MONITOR
# =============================================================================

ENABLE_EXECUTION_MONITOR = _env("ENABLE_EXECUTION_MONITOR", "true").lower() == "true"
EXECUTION_MONITOR_MAX_RETRIES = _ienv("EXECUTION_MONITOR_MAX_RETRIES", 2)
EXECUTION_MONITOR_FILL_TIMEOUT_SEC = int(
    os.getenv("EXECUTION_MONITOR_FILL_TIMEOUT_SEC", "5")
)
RECONCILE_ORDERS_WITH_BROKER = True

# =============================================================================
# HEALTH / SELF HEALING
# =============================================================================

MAX_DATA_AGE_SEC = _ienv("MAX_DATA_AGE_SEC", 60)
MAX_NO_BROKER_SEC = _ienv("MAX_NO_BROKER_SEC", 120)
HEALTH_CRITICAL_ISSUE_THRESHOLD = int(
    os.getenv("HEALTH_CRITICAL_ISSUE_THRESHOLD", "3")
)

ENABLE_SELF_HEALING = _env("ENABLE_SELF_HEALING", "true").lower() == "true"
SELF_HEAL_MAX_ATTEMPTS = _ienv("SELF_HEAL_MAX_ATTEMPTS", 3)
SELF_HEAL_COOLDOWN_SEC = _ienv("SELF_HEAL_COOLDOWN_SEC", 30)
SELF_HEAL_FAILURE_THRESHOLD = _ienv("SELF_HEAL_FAILURE_THRESHOLD", 3)

ENABLE_HEALTH_MONITOR = _env("ENABLE_HEALTH_MONITOR", "true").lower() == "true"
ENABLE_KILL_SWITCH = _env("ENABLE_KILL_SWITCH", "true").lower() == "true"
AUTO_DISABLE_TRADING_ON_CRITICAL_HEALTH = True

# =============================================================================
# POSITION / EXIT CONTROL
# =============================================================================

CLOSE_POSITIONS_BEFORE_END = _env("CLOSE_POSITIONS_BEFORE_END", "true").lower() == "true"
EOD_EXIT_BUFFER_MIN = _ienv("EOD_EXIT_BUFFER_MIN", 10)
FORCE_EXIT_ALL_ON_SHUTDOWN = False

ENABLE_TRAILING = True
ENABLE_PARTIAL_BOOKING = True
ENABLE_BREAKEVEN_SHIFT = True
BREAKEVEN_AFTER_R_MULT = _fenv("BREAKEVEN_AFTER_R_MULT", 1.0)

# =============================================================================
# TRACKER / STATE / DB / LOGGING
# =============================================================================

TRACKER_STALE_AFTER_SEC = _ienv("TRACKER_STALE_AFTER_SEC", 120)

DB_PATH = os.getenv("DB_PATH", "trading_system.db")
TRADES_DB = os.getenv("TRADES_DB", "trades.db")
RUN_STATE_DB = os.getenv("RUN_STATE_DB", "run_system_state.db")
SKIP_JOURNAL_DB = os.getenv("SKIP_JOURNAL_DB", "skip_journal.db")

LOG_FILE = os.getenv("LOG_FILE", "system.log")
MAIN_LIVE_LOG_FILE = os.getenv("MAIN_LIVE_LOG_FILE", "main_live.log")
ERROR_LOG_FILE = os.getenv("ERROR_LOG_FILE", "system_error.log")
AFTER_HOURS_SIGNAL_LOG = os.getenv("AFTER_HOURS_SIGNAL_LOG", "after_hours_signal.log")
TRAINING_LOG_FILE = os.getenv("TRAINING_LOG_FILE", "training.log")

RUN_SYSTEM_STATE_FILE = os.getenv("RUN_SYSTEM_STATE_FILE", "run_system_state.json")
STRATEGY_RUNTIME_STATE_FILE = os.getenv("STRATEGY_RUNTIME_STATE_FILE", "strategy_state.json")
HEALTH_STATE_FILE = os.getenv("HEALTH_STATE_FILE", "health_state.json")

NO_SIGNAL_LOG_FILE = os.getenv("NO_SIGNAL_LOG_FILE", "no_signal.log")
DIAGNOSTIC_LOG_FILE = os.getenv("DIAGNOSTIC_LOG_FILE", "diagnostics.log")

# =============================================================================
# FILES / OUTPUT
# =============================================================================

EQUITY_CSV_FILE = os.getenv("EQUITY_CSV_FILE", "equity_NIFTY.csv")
BACKTEST_TRADES_CSV_FILE = os.getenv("BACKTEST_TRADES_CSV_FILE", "backtest_NIFTY.csv")
SIGNAL_SNAPSHOT_FILE = os.getenv("SIGNAL_SNAPSHOT_FILE", "signal_snapshot.json")
HEALTH_SNAPSHOT_FILE = os.getenv("HEALTH_SNAPSHOT_FILE", "health_snapshot.json")

MASTER_CONTRACT_FILE = os.getenv("MASTER_CONTRACT_FILE", "MasterContract_NFO.csv")
OPENAPI_SCRIP_MASTER_FILE = os.getenv("OPENAPI_SCRIP_MASTER_FILE", "OpenAPIScripMaster.json")

# =============================================================================
# ADVANCED / OPTIONAL FEATURES
# =============================================================================

ENABLE_PORTFOLIO_RISK = _env("ENABLE_PORTFOLIO_RISK", "true").lower() == "true"
ENABLE_META_LEARNER = _env("ENABLE_META_LEARNER", "false").lower() == "true"
ENABLE_WALK_FORWARD = _env("ENABLE_WALK_FORWARD", "false").lower() == "true"

ENABLE_SENTIMENT = _env("ENABLE_SENTIMENT", "false").lower() == "true"
ENABLE_TWITTER_SENTIMENT = _env("ENABLE_TWITTER_SENTIMENT", "false").lower() == "true"
ENABLE_REDDIT_SENTIMENT = _env("ENABLE_REDDIT_SENTIMENT", "false").lower() == "true"

ENABLE_ORDERBOOK = _env("ENABLE_ORDERBOOK", "false").lower() == "true"
ENABLE_VOLUME_PROFILE = _env("ENABLE_VOLUME_PROFILE", "false").lower() == "true"
ENABLE_MACRO = _env("ENABLE_MACRO", "false").lower() == "true"
ENABLE_EARNINGS = _env("ENABLE_EARNINGS", "false").lower() == "true"
ENABLE_VAR = _env("ENABLE_VAR", "false").lower() == "true"
ENABLE_CORRELATION_BREAKDOWN_ALERT = os.getenv(
    "ENABLE_CORRELATION_BREAKDOWN_ALERT", "false"
).lower() == "true"

USE_ML_FILTER = _env("USE_ML_FILTER", "false").lower() == "true"
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "ml_model.pkl")

USE_LSTM = _env("USE_LSTM", "false").lower() == "true"
LSTM_MODEL_PATH = os.getenv("LSTM_MODEL_PATH", "lstm_model.h5")

USE_ENSEMBLE = _env("USE_ENSEMBLE", "false").lower() == "true"

META_LEARNER_LOOKBACK_DAYS = _ienv("META_LEARNER_LOOKBACK_DAYS", 5)
META_LEARNER_PARAM_FILES = {"default": os.getenv("META_LEARNER_PARAM_FILE", "best_params.json")}

COOLDOWN_MINUTES = _ienv("COOLDOWN_MINUTES", 30)

ENABLE_WEBHOOK = _env("ENABLE_WEBHOOK", "false").lower() == "true"
WEBHOOK_PORT = _ienv("WEBHOOK_PORT", 5000)

# =============================================================================
# EXTERNAL API KEYS (OPTIONAL)
# =============================================================================

FRED_API_KEY         = os.getenv("FRED_API_KEY")
NEWS_API_KEY         = os.getenv("NEWS_API_KEY")
TIINGO_KEY           = os.getenv("TIINGO_KEY", "")
TWELVE_DATA_KEY      = os.getenv("TWELVE_DATA_KEY", "")
ALPHA_VANTAGE_KEY    = os.getenv("ALPHA_VANTAGE_KEY", "")
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
FYERS_TOKEN          = os.getenv("FYERS_TOKEN", "")
GITHUB_TOKEN         = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO          = os.getenv("GITHUB_REPO", "sridharthetrainer/trading_robot")

TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TWITTER_CONSUMER_KEY = os.getenv("TWITTER_CONSUMER_KEY")
TWITTER_CONSUMER_SECRET = os.getenv("TWITTER_CONSUMER_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
TWITTER_USE_FREE_TIER = _env("TWITTER_USE_FREE_TIER", "true").lower() == "true"

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")

LOGSTASH_HOST = os.getenv("LOGSTASH_HOST", "localhost")
LOGSTASH_PORT = _ienv("LOGSTASH_PORT", 5000)

# =============================================================================
# INTERNAL HELPERS
# =============================================================================


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _is_non_empty_string(value: Optional[str]) -> bool:
    return bool(value and str(value).strip())


def _ensure_parent_dir(path_str: str) -> None:
    path = BASE_DIR / path_str
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def _validate_choice(name: str, value: str, allowed: Iterable[str], errors: list[str]) -> None:
    if value not in allowed:
        errors.append(f"{name} must be one of {sorted(set(allowed))}, got '{value}'")


@dataclass(frozen=True)
class SessionWindow:
    market_start: time
    market_end: time
    trade_start: time
    trade_end: time
    opening_no_trade_start: time
    opening_no_trade_end: time
    lunch_no_trade_start: time
    lunch_no_trade_end: time


def get_session_window() -> SessionWindow:
    market_start = _parse_time(MARKET_START)
    market_end = _parse_time(MARKET_END)

    trade_start_minutes = market_start.hour * 60 + market_start.minute + TRADE_START_BUFFER_MIN
    trade_end_minutes = market_end.hour * 60 + market_end.minute - TRADE_END_BUFFER_MIN

    trade_start = time(trade_start_minutes // 60, trade_start_minutes % 60)
    trade_end = time(trade_end_minutes // 60, trade_end_minutes % 60)

    return SessionWindow(
        market_start=market_start,
        market_end=market_end,
        trade_start=trade_start,
        trade_end=trade_end,
        opening_no_trade_start=_parse_time(OPENING_NO_TRADE_START),
        opening_no_trade_end=_parse_time(OPENING_NO_TRADE_END),
        lunch_no_trade_start=_parse_time(LUNCH_NO_TRADE_START),
        lunch_no_trade_end=_parse_time(LUNCH_NO_TRADE_END),
    )


# =============================================================================
# CHANGELOG v1.1 — Settings C04–C12
# =============================================================================

# C04: Sandbox Sharpe minimum (was 0.80 — too strict for early live)
SANDBOX_SHARPE_MIN: float = _fenv("SANDBOX_SHARPE_MIN", 0.7)

# C05: Sandbox win-rate minimum (was 55% — luck-sensitive at 30 days)
SANDBOX_WINRATE_MIN: float = _fenv("SANDBOX_WINRATE_MIN", 52.0)

# C06: Probation graduated allocation schedule
# Replaces flat 50% for 15 days with graduated exposure over 20 days.
# Format: list of (day_from, day_to_inclusive, allocation_fraction)
PROBATION_DAYS: int = _ienv("PROBATION_DAYS", 20)

PROBATION_SCHEDULE: list = [
    # (from_day, to_day_inclusive, allocation_fraction)
    (1,   7,  0.30),   # Days  1-7:  30%  — minimal exposure, watching behaviour
    (8,  14,  0.50),   # Days  8-14: 50%  — P&L must be positive/neutral to stay
    (15, 19,  0.75),   # Days 15-19: 75%  — Sharpe > 0.5 required
    (20, 999, 1.00),   # Day 20+:   100%  — full ML allocation
]

# Consecutive losses → halve size; 3 losses → return to paper
PROBATION_LOSS_HALVE_AT: int   = int(  os.getenv("PROBATION_LOSS_HALVE_AT",  "2"))
PROBATION_PAPER_AT:      int   = int(  os.getenv("PROBATION_PAPER_AT",       "3"))


def get_probation_allocation(probation_day: int) -> float:
    """
    Return the capital allocation fraction for a given probation day.
    Uses PROBATION_SCHEDULE to look up the correct tier.
    Returns 0.0 if day is 0 (not yet started) or negative.
    Returns 1.0 if beyond the final scheduled tier.
    """
    if probation_day <= 0:
        return 0.0
    for from_day, to_day, fraction in PROBATION_SCHEDULE:
        if from_day <= probation_day <= to_day:
            return float(fraction)
    return 1.0   # beyond schedule — full allocation


# C07: ML confidence threshold split by mode
# Paper/sandbox: lower threshold (0.52) — more signals for training
# Live:          higher threshold (0.62) — stricter gate on real capital
ML_CONFIDENCE_LIVE:   float = _fenv("ML_CONFIDENCE_LIVE", 0.62)
ML_CONFIDENCE_PAPER:  float = _fenv("ML_CONFIDENCE_PAPER", 0.52)
ML_CONFIDENCE_DEFAULT: float = _fenv("ML_CONFIDENCE_DEFAULT", 0.55)


def get_ml_threshold(mode: str = "paper") -> float:
    """
    Return the ML confidence threshold for the given trading mode.

    Parameters
    ----------
    mode : str — "live", "paper", or "sandbox"

    Returns
    -------
    float — confidence threshold (0.0–1.0)
      live:          ML_CONFIDENCE_LIVE   (default 0.58)
      paper/sandbox: ML_CONFIDENCE_PAPER  (default 0.52)
    """
    m = str(mode).lower().strip()
    if m == "live":
        return ML_CONFIDENCE_LIVE
    if m in ("paper", "sandbox"):
        return ML_CONFIDENCE_PAPER
    return ML_CONFIDENCE_DEFAULT


# C08: ML strong signal threshold (was 0.80 — rarely triggered)
# Above this → 125% position size boost
ML_STRONG_THRESHOLD: float = _fenv("ML_STRONG_THRESHOLD", 0.78)

# C09: Opening Range Breakout candle type
# Was "3x5min" (3 separate 5-min candles) — now "15min" (single 15-min candle)
# Mathematically identical range, simpler implementation, no partial-candle edge cases.
ORB_CANDLE_TYPE: str = os.getenv("ORB_CANDLE_TYPE", "15min")   # was "3x5min"

# C10: Minimum gap % to trigger gap strategy
# Was 0.5% (≈120 NIFTY pts, only 2-4 signals/week)
# Now 0.4% (≈96 NIFTY pts, 4-7 signals/week — more ML training data)
GAP_MIN_PCT: float = _fenv("GAP_MIN_PCT", 0.4)    # was 0.5

# C11: Nightly optimisation run time
# Was 15:45 — Angel One data sometimes delayed 15-20 min post-close
# Now 16:15 — data fully settled, no stale-candle risk
NIGHTLY_OPTIM_TIME: str = os.getenv("NIGHTLY_OPTIM_TIME", "16:15")   # was "15:45"

# C12: Token refresh schedule
# Was: every 6h (1h safety margin — risky if token expires mid-session)
# Now: every 4h at fixed times 08:00 and 12:00 (2-3h safety margin)
TOKEN_REFRESH_HOURS: int        = int( os.getenv("TOKEN_REFRESH_HOURS", "4"))
TOKEN_REFRESH_TIMES: list       = [t.strip() for t in
                                    os.getenv("TOKEN_REFRESH_TIMES", "08:00,12:00").split(",")]


def get_runtime_capital() -> float:
    return PAPER_CAPITAL if PAPER_TRADING else REAL_CAPITAL


# =============================================================================
# KEYS USED BY MODULES BUT PREVIOUSLY MISSING FROM config.py
# All read from .env with sensible defaults
# =============================================================================

WEEKLY_LOSS_LIMIT           = _fenv("WEEKLY_LOSS_LIMIT",              9000.0)
MIN_VOLUME_RATIO_ENTRY      = _fenv("MIN_VOLUME_RATIO_ENTRY",         0.40)
MIN_BREAKOUT_VOLUME_RATIO   = _fenv("MIN_BREAKOUT_VOLUME_RATIO",      1.20)
MIN_CASH_VOLUME_RATIO_ENTRY = _fenv("MIN_CASH_VOLUME_RATIO_ENTRY",    0.80)
COST_HURDLE_MULTIPLIER      = _fenv("COST_HURDLE_MULTIPLIER",         2.50)
MIN_EXPECTED_NET_PROFIT     = _fenv("MIN_EXPECTED_NET_PROFIT",        0.0)
ALLOW_LEGACY_TRADE_MODELS   = _benv("ALLOW_LEGACY_TRADE_MODELS",      False)
CASH_NO_NEW_ENTRY_AFTER     = _env("CASH_NO_NEW_ENTRY_AFTER",         "14:45")
OPTION_NO_NEW_ENTRY_AFTER   = _env("OPTION_NO_NEW_ENTRY_AFTER",       "15:00")
# ML-training window: all model training/retraining must run between these hours
# (default 7am–9pm) so heavy jobs never fire overnight. Enforced in
# post_market_ml / autonomous_param_trainer / calibrator (trading_calendar
# .in_ml_training_window is the single source of truth). --force bypasses.
ML_TRAINING_WINDOW_START    = _env("ML_TRAINING_WINDOW_START",        "07:00")
ML_TRAINING_WINDOW_END      = _env("ML_TRAINING_WINDOW_END",          "21:00")
ML_TRAINING_ENFORCE_WINDOW  = _benv("ML_TRAINING_ENFORCE_WINDOW",     True)
MIN_BARS_FOR_SIGNAL         = _ienv("MIN_BARS_FOR_SIGNAL", 5)  # lowered: bhavcopy gives ~39-250 bars
LIVE_MIN_SIGNAL_SCORE       = _fenv("LIVE_MIN_SIGNAL_SCORE", 6.0)
LIVE_MIN_AI_PROBABILITY     = _fenv("LIVE_MIN_AI_PROBABILITY", ML_CONFIDENCE_LIVE)
LIVE_MIN_FILTER_SCORE       = _fenv("LIVE_MIN_FILTER_SCORE", AI_FILTER_THRESHOLD)
PAPER_ONLY_STRATEGIES = {
    s.strip().lower()
    for s in _env(
        "PAPER_ONLY_STRATEGIES",
        "fallback,expiry_scalp,scalping,holy_grail,chart_pattern_inverse_head_shoulders,"
        "chart_pattern_descending_triangle,rsi_divergence,chart_pattern_head_and_shoulders",
    ).split(",")
    if s.strip()
}
ENABLE_1M_ENTRY_TIMING      = _benv("ENABLE_1M_ENTRY_TIMING",         True)
OVERNIGHT_UNCERTAINTY_THRESHOLD = _fenv("OVERNIGHT_UNCERTAINTY_THRESHOLD", 0.65)
OVERNIGHT_GAP_ALERT_PCT     = _fenv("OVERNIGHT_GAP_ALERT_PCT",        0.005)
OVERNIGHT_GAP_CLOSE_PCT     = _fenv("OVERNIGHT_GAP_CLOSE_PCT",        0.010)
OVERNIGHT_NEWS_SCAN         = _benv("OVERNIGHT_NEWS_SCAN",            True)
BACKUP_GDRIVE_FOLDER        = _env("BACKUP_GDRIVE_FOLDER",            "TradingRobotBackup")
BOT_NAME                    = _env("BOT_NAME",                        "NIFTY Algo Bot")
SWING_CAPITAL_PCT           = _fenv("SWING_CAPITAL_PCT",              0.45)
INTRADAY_CAPITAL_PCT        = _fenv("INTRADAY_CAPITAL_PCT",           0.30)
SCALPING_CAPITAL_PCT        = _fenv("SCALPING_CAPITAL_PCT",           0.15)
RESERVE_CAPITAL_PCT         = _fenv("RESERVE_CAPITAL_PCT",            0.10)

def validate() -> None:
    errors: list[str] = []

    # -------------------------------------------------------------------------
    # Mode / broker
    # -------------------------------------------------------------------------
    if ENABLE_REAL_TRADING and PAPER_TRADING:
        errors.append("ENABLE_REAL_TRADING=True requires PAPER_TRADING=False")

    if not PAPER_TRADING:
        if not _is_non_empty_string(API_KEY):
            errors.append("Missing API_KEY")
        if not _is_non_empty_string(CLIENT_ID):
            errors.append("Missing CLIENT_ID")
        if not _is_non_empty_string(PASSWORD):
            errors.append("Missing PASSWORD")
        if not _is_non_empty_string(TOTP_SECRET):
            errors.append("Missing TOTP_SECRET")

    # -------------------------------------------------------------------------
    # Telegram
    # -------------------------------------------------------------------------
    if TELEGRAM_ENABLED:
        if not _is_non_empty_string(TELEGRAM_BOT_TOKEN):
            errors.append("Missing TELEGRAM_BOT_TOKEN")
        if not _is_non_empty_string(TELEGRAM_CHAT_ID):
            errors.append("Missing TELEGRAM_CHAT_ID")

    # -------------------------------------------------------------------------
    # Market / symbols
    # -------------------------------------------------------------------------
    if not TRADE_SYMBOLS:
        errors.append("TRADE_SYMBOLS cannot be empty")

    try:
        session = get_session_window()
        if session.trade_start >= session.trade_end:
            errors.append("Trading window invalid: trade_start must be earlier than trade_end")
        if session.market_start >= session.market_end:
            errors.append("Market window invalid: market_start must be earlier than market_end")
    except Exception as exc:
        errors.append(f"Invalid market/session time format: {exc}")

    if DEFAULT_SYMBOL not in TRADE_SYMBOLS:
        errors.append("DEFAULT_SYMBOL must be present in TRADE_SYMBOLS")

    # -------------------------------------------------------------------------
    # Capital / risk
    # -------------------------------------------------------------------------
    if CAPITAL <= 0:
        errors.append("CAPITAL must be > 0")
    if STATUS_ALERT_INTERVAL_SEC < 60:
        errors.append("STATUS_ALERT_INTERVAL_SEC must be >= 60 seconds")
    if not (0.0 < AI_FILTER_THRESHOLD <= 5.0):
        errors.append("AI_FILTER_THRESHOLD must be between 0 and 5")
    if WF_TRAIN_DAYS < 30:
        errors.append("WF_TRAIN_DAYS must be >= 30")
    if OPTION_STOP_0DTE <= 0 or OPTION_STOP_0DTE >= 1:
        errors.append("OPTION_STOP_0DTE must be between 0 and 1")

    if PAPER_CAPITAL <= 0:
        errors.append("PAPER_CAPITAL must be > 0")
    if REAL_CAPITAL <= 0:
        errors.append("REAL_CAPITAL must be > 0")


    # -------------------------------------------------------------------------
    # Changelog v1.1 validations
    # -------------------------------------------------------------------------
    if ML_CONFIDENCE_LIVE < ML_CONFIDENCE_PAPER:
        errors.append(
            "ML_CONFIDENCE_LIVE must be >= ML_CONFIDENCE_PAPER "
            f"(got live={ML_CONFIDENCE_LIVE}, paper={ML_CONFIDENCE_PAPER})"
        )
    if ML_STRONG_THRESHOLD <= ML_CONFIDENCE_LIVE:
        errors.append(
            "ML_STRONG_THRESHOLD must be > ML_CONFIDENCE_LIVE "
            f"(got {ML_STRONG_THRESHOLD} vs {ML_CONFIDENCE_LIVE})"
        )
    if PROBATION_DAYS <= 0:
        errors.append("PROBATION_DAYS must be > 0")
    if SANDBOX_WINRATE_MIN < 0 or SANDBOX_WINRATE_MIN > 100:
        errors.append("SANDBOX_WINRATE_MIN must be between 0 and 100")
    if SANDBOX_SHARPE_MIN < 0:
        errors.append("SANDBOX_SHARPE_MIN must be >= 0")
    if not isinstance(PAPER_TRADING, bool):
        errors.append(f"PAPER_TRADING must be true/false, got: {PAPER_TRADING!r}")
    if not isinstance(ENABLE_REAL_TRADING, bool):
        errors.append(f"ENABLE_REAL_TRADING must be true/false, got: {ENABLE_REAL_TRADING!r}")
    if not (0 < RISK_PER_TRADE_PCT <= 0.05):
        errors.append("RISK_PER_TRADE_PCT must be between 0 and 0.05")

    if SOFT_DAILY_LOSS_LIMIT <= 0:
        errors.append("SOFT_DAILY_LOSS_LIMIT must be > 0")

    if MAX_DAILY_LOSS <= 0:
        errors.append("MAX_DAILY_LOSS must be > 0")

    if SOFT_DAILY_LOSS_LIMIT > MAX_DAILY_LOSS:
        errors.append("SOFT_DAILY_LOSS_LIMIT must be <= MAX_DAILY_LOSS")

    if MAX_OPEN_POSITIONS <= 0:
        errors.append("MAX_OPEN_POSITIONS must be > 0")
    if MIN_DYNAMIC_OPEN_POSITIONS <= 0:
        errors.append("MIN_DYNAMIC_OPEN_POSITIONS must be > 0")
    if MAX_DYNAMIC_OPEN_POSITIONS < MIN_DYNAMIC_OPEN_POSITIONS:
        errors.append("MAX_DYNAMIC_OPEN_POSITIONS must be >= MIN_DYNAMIC_OPEN_POSITIONS")
    if CAPITAL_PER_OPEN_POSITION <= 0:
        errors.append("CAPITAL_PER_OPEN_POSITION must be > 0")

    if MAX_TRADES_PER_DAY <= 0:
        errors.append("MAX_TRADES_PER_DAY must be > 0")

    if MAX_TRADES_PER_SYMBOL_PER_DAY <= 0:
        errors.append("MAX_TRADES_PER_SYMBOL_PER_DAY must be > 0")

    if not (0 < MAX_PORTFOLIO_RISK_PCT <= 1):
        errors.append("MAX_PORTFOLIO_RISK_PCT must be between 0 and 1")

    if not (0 < MAX_SYMBOL_EXPOSURE_PCT <= 1):
        errors.append("MAX_SYMBOL_EXPOSURE_PCT must be between 0 and 1")

    if not (0 < MAX_TOTAL_EXPOSURE_PCT <= 2):
        errors.append("MAX_TOTAL_EXPOSURE_PCT must be between 0 and 2")

    if MAX_CORRELATED_POSITIONS < 0:
        errors.append("MAX_CORRELATED_POSITIONS must be >= 0")

    # -------------------------------------------------------------------------
    # Position sizing
    # -------------------------------------------------------------------------
    if not (0 < MIN_RISK_PCT <= MAX_RISK_PCT):
        errors.append("MIN_RISK_PCT must be > 0 and <= MAX_RISK_PCT")

    if MAX_RISK_PCT > 0.05:
        errors.append("MAX_RISK_PCT should not exceed 0.05")

    if MIN_LOTS <= 0:
        errors.append("MIN_LOTS must be > 0")

    if MAX_LOTS < MIN_LOTS:
        errors.append("MAX_LOTS must be >= MIN_LOTS")

    # -------------------------------------------------------------------------
    # Options / trade settings
    # -------------------------------------------------------------------------
    if OPTION_LOT_SIZE <= 0:
        errors.append("OPTION_LOT_SIZE must be > 0")

    if STRIKE_INTERVAL <= 0:
        errors.append("STRIKE_INTERVAL must be > 0")

    if OPTION_STRIKE_LADDER_OTM_STEPS < 0:
        errors.append("OPTION_STRIKE_LADDER_OTM_STEPS must be >= 0")

    if OPTION_STRIKE_LADDER_ITM_STEPS < 0:
        errors.append("OPTION_STRIKE_LADDER_ITM_STEPS must be >= 0")

    if OPTION_STRIKE_LADDER_OTM_STEPS + OPTION_STRIKE_LADDER_ITM_STEPS <= 0:
        errors.append("Option strike ladder must include at least one OTM or ITM step")

    if OPTION_CHAIN_MAX_AGE_SEC <= 0:
        errors.append("OPTION_CHAIN_MAX_AGE_SEC must be > 0")

    if MIN_OPTION_ATM_LEG_VOLUME < 0:
        errors.append("MIN_OPTION_ATM_LEG_VOLUME must be >= 0")

    if MIN_OPTION_ATM_LEG_OI < 0:
        errors.append("MIN_OPTION_ATM_LEG_OI must be >= 0")

    if MAX_OPTION_ATM_SPREAD_PCT <= 0:
        errors.append("MAX_OPTION_ATM_SPREAD_PCT must be > 0")

    if MIN_OPTION_EXPECTED_MOVE_PCT < 0:
        errors.append("MIN_OPTION_EXPECTED_MOVE_PCT must be >= 0")

    if OPTION_EXPECTED_MOVE_USAGE_LIMIT < 0:
        errors.append("OPTION_EXPECTED_MOVE_USAGE_LIMIT must be >= 0")

    if BROKERAGE_PER_ORDER < 0:
        errors.append("BROKERAGE_PER_ORDER must be >= 0")

    if SLIPPAGE_PCT < 0:
        errors.append("SLIPPAGE_PCT must be >= 0")

    # -------------------------------------------------------------------------
    # Technical thresholds
    # -------------------------------------------------------------------------
    if RSI_PERIOD <= 1:
        errors.append("RSI_PERIOD must be > 1")

    if not (0 < RSI_BUY_LEVEL < 100):
        errors.append("RSI_BUY_LEVEL must be between 0 and 100")

    if not (0 < RSI_SELL_LEVEL < 100):
        errors.append("RSI_SELL_LEVEL must be between 0 and 100")

    if RSI_BUY_LEVEL >= RSI_SELL_LEVEL:
        errors.append("RSI_BUY_LEVEL must be less than RSI_SELL_LEVEL")

    if EMA_FAST <= 0 or EMA_SLOW <= 0 or EMA_TREND_FILTER <= 0:
        errors.append("EMA periods must be > 0")

    if EMA_FAST >= EMA_SLOW:
        errors.append("EMA_FAST must be less than EMA_SLOW")

    if not (0 <= ADX_THRESHOLD <= 100):
        errors.append("ADX_THRESHOLD must be between 0 and 100")

    if not (0 <= ADX_TREND_THRESHOLD <= 100):
        errors.append("ADX_TREND_THRESHOLD must be between 0 and 100")

    if not (0 <= ADX_STRONG_THRESHOLD <= 100):
        errors.append("ADX_STRONG_THRESHOLD must be between 0 and 100")

    if not (0 <= ADX_RANGE_THRESHOLD <= 100):
        errors.append("ADX_RANGE_THRESHOLD must be between 0 and 100")

    # -------------------------------------------------------------------------
    # Strategy selection
    # -------------------------------------------------------------------------
    _validate_choice(
        "STRATEGY_SELECTION_METRIC",
        STRATEGY_SELECTION_METRIC,
        {"sharpe", "total_pnl", "win_rate", "final_capital"},
        errors,
    )

    if DEFAULT_STRATEGY not in AVAILABLE_STRATEGIES:
        errors.append(
            f"DEFAULT_STRATEGY must be one of {AVAILABLE_STRATEGIES}, got '{DEFAULT_STRATEGY}'"
        )

    if MIN_TRADES_FOR_SELECTION < 0:
        errors.append("MIN_TRADES_FOR_SELECTION must be >= 0")

    if STRATEGY_UPDATE_INTERVAL_HOURS <= 0:
        errors.append("STRATEGY_UPDATE_INTERVAL_HOURS must be > 0")

    # -------------------------------------------------------------------------
    # Runtime / execution
    # -------------------------------------------------------------------------
    if MAIN_LOOP_SLEEP_SEC <= 0:
        errors.append("MAIN_LOOP_SLEEP_SEC must be > 0")

    if LOOKBACK_DAYS <= 0:
        errors.append("LOOKBACK_DAYS must be > 0")

    if MIN_CANDLES_REQUIRED < 30:
        errors.append("MIN_CANDLES_REQUIRED should be at least 30")

    if MAX_DATA_AGE_SEC <= 0:
        errors.append("MAX_DATA_AGE_SEC must be > 0")

    if MAX_NO_BROKER_SEC <= 0:
        errors.append("MAX_NO_BROKER_SEC must be > 0")

    if ROUTER_RETRY_ATTEMPTS < 0:
        errors.append("ROUTER_RETRY_ATTEMPTS must be >= 0")

    if EXECUTION_MONITOR_MAX_RETRIES < 0:
        errors.append("EXECUTION_MONITOR_MAX_RETRIES must be >= 0")

    # -------------------------------------------------------------------------
    # File paths
    # -------------------------------------------------------------------------
    for path_str in [
        DB_PATH,
        TRADES_DB,
        RUN_STATE_DB,
        SKIP_JOURNAL_DB,
        LOG_FILE,
        MAIN_LIVE_LOG_FILE,
        ERROR_LOG_FILE,
        AFTER_HOURS_SIGNAL_LOG,
        TRAINING_LOG_FILE,
        RUN_SYSTEM_STATE_FILE,
        STRATEGY_STATE_FILE,
        STRATEGY_RUNTIME_STATE_FILE,
        HEALTH_STATE_FILE,
        NO_SIGNAL_LOG_FILE,
        DIAGNOSTIC_LOG_FILE,
        EQUITY_CSV_FILE,
        BACKTEST_TRADES_CSV_FILE,
        SIGNAL_SNAPSHOT_FILE,
        HEALTH_SNAPSHOT_FILE,
    ]:
        try:
            _ensure_parent_dir(path_str)
        except Exception as exc:
            errors.append(f"Failed to prepare path '{path_str}': {exc}")

    if errors:
        raise ValueError("Config validation failed: " + " | ".join(errors))


def summary() -> dict:
    return {
        "project_name": PROJECT_NAME,
        "environment": ENVIRONMENT,
        "paper_trading": PAPER_TRADING,
        "enable_real_trading": ENABLE_REAL_TRADING,
        "default_symbol": DEFAULT_SYMBOL,
        "trade_symbols": TRADE_SYMBOLS,
        "default_interval": DEFAULT_INTERVAL,
        "strategy": DEFAULT_STRATEGY,
        "regime_switching": ENABLE_REGIME_SWITCHING,
        "dynamic_strategy_switching": ENABLE_DYNAMIC_STRATEGY_SWITCHING,
        "ai_trade_filter": ENABLE_AI_TRADE_FILTER,
        "signal_diagnostics": ENABLE_SIGNAL_DIAGNOSTICS,
        "capital": get_runtime_capital(),
        "max_daily_loss": MAX_DAILY_LOSS,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "dynamic_max_open_positions": DYNAMIC_MAX_OPEN_POSITIONS,
        "capital_per_open_position": CAPITAL_PER_OPEN_POSITION,
        "force_market_open": FORCE_MARKET_OPEN,
        "force_market_close": FORCE_MARKET_CLOSE,
        "primary_broker": PRIMARY_BROKER,
        "secondary_broker": SECONDARY_BROKER,
        "timezone": TIMEZONE,
    }


if __name__ == "__main__":
    validate()
    print("Config OK")
    print(summary())

# ── Auto-added missing keys ─────────────────────────────────────────────
MAX_MONTHLY_LOSS = float(os.getenv("MAX_MONTHLY_LOSS", str(MAX_DAILY_LOSS * 8)))
# Set DISABLE_YFINANCE=true in .env to skip the yfinance fallback entirely.
# Angel One + bhavcopy are the preferred data sources; yfinance is Yahoo-dependent.
DISABLE_YFINANCE = os.getenv("DISABLE_YFINANCE", "false").lower() == "true"
OI_TRACKER_ENABLED = os.getenv("OI_TRACKER_ENABLED","true").lower()=="true"
REGIME_ENGINE_ENABLED = os.getenv("REGIME_ENGINE_ENABLED","true").lower()=="true"
MIN_CONFLUENCE_SCORE = float(os.getenv("MIN_CONFLUENCE_SCORE","3.5"))
POST_CONFLUENCE_MIN_SCORE = float(os.getenv("POST_CONFLUENCE_MIN_SCORE","4.5"))  # Raised: April 2026 STT doubles breakeven
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC","300"))

# Data collection / scan expansion
ENABLE_TIERED_FULL_UNIVERSE_SCAN = _env("ENABLE_TIERED_FULL_UNIVERSE_SCAN", "true").lower() == "true"
FULL_UNIVERSE_SCAN_INTERVAL_MIN = int(os.getenv("FULL_UNIVERSE_SCAN_INTERVAL_MIN", "15"))
FULL_UNIVERSE_SCAN_MAX_SYMBOLS = int(os.getenv("FULL_UNIVERSE_SCAN_MAX_SYMBOLS", "220"))
EOD_SIGNAL_MINER_MAX_SYMBOLS = int(os.getenv("EOD_SIGNAL_MINER_MAX_SYMBOLS", str(FULL_UNIVERSE_SCAN_MAX_SYMBOLS)))
CANDLE_COVERAGE_MAX_SYMBOLS = int(os.getenv("CANDLE_COVERAGE_MAX_SYMBOLS", str(FULL_UNIVERSE_SCAN_MAX_SYMBOLS)))
CANDLE_COVERAGE_INTERVALS = os.getenv("CANDLE_COVERAGE_INTERVALS", "1m,5m,15m,1h,1d")
OPTION_CHAIN_SNAPSHOT_INTERVAL_SEC = int(os.getenv("OPTION_CHAIN_SNAPSHOT_INTERVAL_SEC", "300"))
MARKET_SNAPSHOT_INTERVAL_SEC = int(os.getenv("MARKET_SNAPSHOT_INTERVAL_SEC", "300"))
SNAPSHOT_OPTION_UNDERLYINGS = [
    s.strip().upper()
    for s in os.getenv("SNAPSHOT_OPTION_UNDERLYINGS", "NIFTY,BANKNIFTY,FINNIFTY,SENSEX").split(",")
    if s.strip()
]
ML_MIN_SAMPLES = int(os.getenv("ML_MIN_SAMPLES","50"))
MAX_SAME_SECTOR_POSITIONS = int(os.getenv("MAX_SAME_SECTOR_POSITIONS","3"))
