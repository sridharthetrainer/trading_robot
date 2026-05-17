"""
live_signal_engine.py

Live trading engine: data fetching, signal generation, AI/RL filtering,
option selection, risk gating, and order execution.

Fixes applied
-------------
1. df_htf = df (same DataFrame passed as both timeframes)
   generate_signal(df=df, df_htf=df, ...) meant the HTF (higher
   time-frame) bias check was always comparing a symbol to itself.
   The multi-timeframe alignment logic in signal_engine.py was
   effectively disabled for every symbol.

   Fix: DataFetcher.get_latest_data() is replaced by a call to
   get_market_data_multi_tf() (or a fallback that fetches 5m then
   15m separately).  If the fetcher doesn't support the multi-TF
   method, we fall back to using df as df_htf with a warning.

2. AI feature vector mismatch (4 features vs 8 trained)
   _get_ai_probability() built a 4-element feature list:
       [strength, score, volatility, confidence]
   The model was trained (in SelfLearningEngine) with 8 features:
       [confidence, score, regime_score, volatility, entry_atr,
        regime_num, side_num, strategy_num]
   Passing 4 to a model expecting 8 raises ValueError or produces
   garbage predictions silently.

   Fix: build the same 8-feature vector that the model was trained on,
   matching _extract_features() in self_learning_engine.py exactly.

3. No daily_loss_manager attribute
   main_autonomous._on_new_trading_day() calls:
       self.live_engine.daily_loss_manager.reset_day()
   LiveSignalEngine had no such attribute — AttributeError every
   morning.

   Fix: instantiate DailyLossLimitManager in __init__ and wire it
   to trade_manager so lock/unlock propagate correctly.

4. No reset_daily_state() method
   main_autonomous also calls self.live_engine.reset_daily_state().
   Added as a thin wrapper that resets daily_loss_manager and
   trade_manager daily state together.

5. Risk-approved quantity was not passed to trade_manager.open_trade()
   _execute_candidate() computed final_qty from PortfolioRiskManager
   but open_trade() ignored it and re-ran AdaptivePositionSizer
   internally.  Added qty_override=final_qty to the open_trade() call.
"""

from __future__ import annotations
try:
    from signal_log import get_signal_logger as _get_sig_log
    _SIG_LOG_AVAIL = True
except ImportError:
    _SIG_LOG_AVAIL = False

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import config as cfg
from ai_trade_filter import AITradeFilter
from capital_allocator import CapitalAllocator
from alerts import AlertManager
try:
    from websocket_engine import WebSocketEngine
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
try:
    from param_bridge import get_param_bridge as _get_pb
    _PB_AVAILABLE = True
except ImportError:
    _PB_AVAILABLE = False
try:
    from capital_compounder import CapitalCompounder, load_sector_map
    _CC_AVAILABLE = True
except ImportError:
    _CC_AVAILABLE = False
    CapitalCompounder = None
from daily_loss_limit import DailyLossLimitManager
from data_fetcher import DataFetcher
from option_chain_fetcher import NSEOptionChainFetcher
from option_selector import OptionSelector
from portfolio_risk import PortfolioRiskManager
from adaptive_position_sizer import AdaptivePositionSizer
from self_learning_engine import SelfLearningEngine
from signal_engine import generate_signal
from trade_manager import TradeManager
from broker_manager import BrokerManager

# ── Optional module imports — all guarded so missing deps never crash startup ──
try:
    from market_context import get_market_context as _get_mctx
    _MCTX_AVAILABLE = True
except ImportError:
    _MCTX_AVAILABLE = False

try:
    from sl_hunt_guard import (
        get_sl_guard         as _get_sl_guard,
        get_swing_protection as _get_swing_protect,
        compute_smart_stop   as _smart_stop,
    )
    _SLHG_AVAILABLE = True
except ImportError:
    _SLHG_AVAILABLE = False

try:
    from option_chain_engine import OptionChainEngine as _OCE, NSE_LOT_SIZES as _LOT_SIZES
    _OCE_AVAILABLE = True
except ImportError:
    _OCE_AVAILABLE = False
    _LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 65}

try:
    from day_classifier import get_day_classifier as _get_dc, DAY_VOLATILE
    _DC_AVAILABLE = True
except ImportError:
    _DC_AVAILABLE = False
    DAY_VOLATILE = "VOLATILE_DAY"

try:
    from option_intelligence import get_option_intelligence as _get_oi
    _OI_AVAILABLE = True
except ImportError:
    _OI_AVAILABLE = False

try:
    from scale_in_manager import get_scale_manager as _get_sim
    _SIM_AVAILABLE = True
except ImportError:
    _SIM_AVAILABLE = False

try:
    from option_chain_intelligence import OptionChainIntelligence as _OCI
    _OCI_AVAILABLE = True
except ImportError:
    _OCI_AVAILABLE = False

try:
    from connection_monitor import get_monitor as _get_monitor
    _CM_AVAILABLE = True
except ImportError:
    _CM_AVAILABLE = False

try:
    from smart_order_router import SmartOrderRouter as _SOR
    _SOR_AVAILABLE = True
except ImportError:
    _SOR_AVAILABLE = False

try:
    from bhav_copy import get_bhav_copy_modifier as _get_bhav
    _BHAV_AVAIL = True
except ImportError:
    _BHAV_AVAIL = False
try:
    from cross_asset import get_market_bias as _get_cross_asset
    _CA_AVAIL = True
except ImportError:
    _CA_AVAIL = False
try:
    from news_nlp import get_news_score as _get_news
    _NEWS_AVAIL = True
except ImportError:
    _NEWS_AVAIL = False
try:
    from bulk_deals import get_bulk_deal_modifier as _get_bulk
    _BULK_AVAIL = True
except ImportError:
    _BULK_AVAIL = False
try:
    from fno_ban_list import get_ban_status as _get_ban, filter_banned as _filter_banned
    _BAN_AVAIL = True
except ImportError:
    _BAN_AVAIL = False; _filter_banned = lambda x: x
try:
    from whale_tracker import get_whale_composite_score as _get_whale
    _WHALE_AVAIL = True
except ImportError:
    _WHALE_AVAIL = False
try:
    from corporate_actions import get_action_modifier as _get_corp_action
    _CORP_AVAIL = True
except ImportError:
    _CORP_AVAIL = False

# ── Pre-cycle intelligence cache (refreshed every 30 min) ────────────────
_INTEL_CACHE: dict = {
    "ts": 0, "vix": 15.0, "fii_bias": 0.0, "cross_asset_bias": "NEUTRAL",
    "news_score": 0.0, "expiry_dte": 5, "expiry_regime": "NORMAL",
    "whale_index": {}, "bulk_mods": {}, "bhav_mods": {}, "ban_list": set(),
}

def _refresh_intel_cache() -> None:
    """Refresh intelligence cache every 30 min — called before scan cycle."""
    global _INTEL_CACHE
    import time as _t
    if _t.time() - _INTEL_CACHE["ts"] < 1800:  # 30-min TTL
        return
    _INTEL_CACHE["ts"] = _t.time()
    # VIX
    try:
        import yf_compat as _yf
        _vdf = _yf.download("^INDIAVIX", period="1d", interval="5m",
                            progress=False, auto_adjust=True)
        if _vdf is not None and len(_vdf) > 0:
            _INTEL_CACHE["vix"] = float(_vdf["Close"].iloc[-1])
    except Exception: pass
    # Cross-asset
    if _CA_AVAIL:
        try:
            _cb = _get_cross_asset()
            _INTEL_CACHE["cross_asset_bias"] = _cb.get("bias","NEUTRAL")
        except Exception: pass
    # News
    if _NEWS_AVAIL:
        try:
            _INTEL_CACHE["news_score"] = float(_get_news("MARKET") or 0)
        except Exception: pass
    # Expiry
    try:
        from expiry_regime import get_expiry_regime
        _er = get_expiry_regime()
        _INTEL_CACHE["expiry_dte"]    = _er.get("days_to_expiry", 5)
        _INTEL_CACHE["expiry_regime"] = _er.get("regime_label","NORMAL")
    except Exception: pass
    # FnO ban list
    if _BAN_AVAIL:
        try:
            from fno_ban_list import get_banned_symbols
            _INTEL_CACHE["ban_list"] = set(get_banned_symbols() or [])
        except Exception: pass
    # Whale index signals
    if _WHALE_AVAIL:
        try:
            for _idx in ["NIFTY","BANKNIFTY","FINNIFTY"]:
                _w = _get_whale(_idx)
                _INTEL_CACHE["whale_index"][_idx] = _w.get("score_mod",0)
        except Exception: pass
    # Option chain intelligence
    if _OCI_AVAILABLE:
        try:
            _oci = _OCI()
            for _idx_sym in ["NIFTY","BANKNIFTY","FINNIFTY"]:
                _oci_res = _oci.analyze(_idx_sym)
                if _oci_res:
                    _INTEL_CACHE["whale_index"][_idx_sym] = (
                        _INTEL_CACHE["whale_index"].get(_idx_sym, 0)
                        + float(_oci_res.get("score_mod", 0) or 0)
                    )
        except Exception: pass
    logger.info("Intel cache refreshed | VIX=%.1f cross=%s news=%.2f",
                _INTEL_CACHE["vix"], _INTEL_CACHE["cross_asset_bias"], _INTEL_CACHE["news_score"])

try:
    from market_data_feeds import get_market_feeds as _get_feeds
    _FEEDS_AVAILABLE = True
except ImportError:
    _FEEDS_AVAILABLE = False

try:
    from institutional_alpha import get_alpha_engine as _get_alpha_engine
    _ALPHA_AVAILABLE = True
except ImportError:
    _ALPHA_AVAILABLE = False

try:
    from three_confirm import evaluate_three_confirmations as _three_conf
    _3C_AVAILABLE = True
except ImportError:
    _3C_AVAILABLE = False

try:
    from advanced_strategies import (
        expiry_week_regime  as _expiry_week_regime,
        get_rs_filter       as _get_rs_filter,
        get_global_macro    as _get_global_macro,
        get_kelly_sizer     as _get_kelly_sizer,
    )
    _ADV_LIVE_AVAILABLE = True
except ImportError:
    _ADV_LIVE_AVAILABLE = False

try:
    from strategy_scanner import StrategyScanner
    _SS_AVAILABLE = True
except ImportError:
    _SS_AVAILABLE = False

try:
    from angel_option_chain import compute_pcr_and_maxpain as _compute_pcr
    _PCR_AVAILABLE = True
except ImportError:
    _PCR_AVAILABLE = False
try:
    from indicators import is_market_structured as _is_structured
    _ENTROPY_AVAILABLE = True
except ImportError:
    _ENTROPY_AVAILABLE = False
try:
    from execution_algo import get_execution_algo as _get_exec_algo
    _EXEC_ALGO_AVAILABLE = True
except ImportError:
    _EXEC_ALGO_AVAILABLE = False
try:
    from entry_timing_1m import get_1m_entry, get_1m_stop, INDEX_SYMBOLS as _INDEX_SYMS
    _ENTRY_1M_AVAILABLE = True
except ImportError:
    _ENTRY_1M_AVAILABLE = False
    _INDEX_SYMS = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}
try:
    from global_market_filter import get_global_filter as _get_gf
    _GF_AVAILABLE = True
except ImportError:
    _GF_AVAILABLE = False
try:
    from strategy_performance_matrix import get_strategy_matrix as _get_sm
    _SM_AVAILABLE = True
except ImportError:
    _SM_AVAILABLE = False
try:
    from expiry_strategy import is_expiry_today, get_expiry_score_boost
    _EXPIRY_SIG_AVAILABLE = True
except ImportError:
    _EXPIRY_SIG_AVAILABLE = False
try:
    from greeks_sizer import get_greeks_sizer as _get_gs
    _GS_AVAILABLE = True
except ImportError:
    _GS_AVAILABLE = False
try:
    from event_calendar import get_event_calendar as _get_ec
    _EC_AVAILABLE = True
except ImportError:
    _EC_AVAILABLE = False
try:
    from trailing import TrailingStop as _TrailingMgr
    _TRAILING_AVAILABLE = True
except ImportError:
    _TRAILING_AVAILABLE = False
    _TrailingMgr = None

try:
    from lstm_model import LSTMPredictor as _LSTMModel
    _LSTM_AVAILABLE = True
except ImportError:
    _LSTM_AVAILABLE = False
    _LSTMModel = None

logger = logging.getLogger(__name__)

NO_SIGNAL_LOG             = getattr(cfg, "NO_SIGNAL_LOG_FILE",        "no_signal.log")
# NSE indices with weekly/monthly F&O contracts
# SENSEX uses BSE F&O (BFO exchange) — handled separately
SUPPORTED_OPTION_UNDERLYINGS = {
    "NIFTY",       # NSE, lot=75, NFO
    "BANKNIFTY",   # NSE, lot=15, NFO
    "FINNIFTY",    # NSE, lot=40, NFO
    "MIDCPNIFTY",  # NSE, lot=75, NFO
    "NIFTYNEXT50", # NSE, lot=25, NFO
    "SENSEX",      # BSE, lot=10, BFO — signal only (no option auto-entry yet)
}

# Tier-1 priority symbols — always scanned first, always need at least one signal
PRIORITY_SYMBOLS  = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]
TIER1_SCORE_BOOST = 1.00  # extra boost for tier-1 symbols
DEFAULT_TOTAL_CAPITAL     = float(getattr(cfg, "CAPITAL",              100_000))
DEFAULT_MAX_OPEN_POSITIONS = int(getattr(cfg, "MAX_OPEN_POSITIONS",    2))
DEFAULT_MAX_DAILY_LOSS    = float(getattr(cfg, "MAX_DAILY_LOSS",        3000.0))
DEFAULT_MAX_SIGNALS_PER_CYCLE = int(getattr(cfg, "MAX_SIGNALS_PER_CYCLE", 2))
DEFAULT_WORKERS           = int(getattr(cfg, "LIVE_SIGNAL_MAX_WORKERS",  8))

# HTF interval mapping (5m data → 15m HTF for bias)
_HTF_INTERVAL_MAP = {"5m": "15m", "1m": "5m", "15m": "1h", "3m": "15m"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Feature vector builder — must match SelfLearningEngine._extract_features()
# ---------------------------------------------------------------------------

def _regime_to_num(regime: str) -> float:
    r = str(regime or "").upper()
    if r in ("TREND", "BULLISH_TREND", "BEARISH_TREND", "BREAKOUT"):
        return 1.0
    if r in ("RANGE", "SIDEWAYS", "EARLY_TREND"):
        return 0.0
    return -1.0


def _side_to_num(side: str) -> float:
    s = str(side or "").upper()
    return 1.0 if s == "BUY" else (-1.0 if s == "SELL" else 0.0)


def _strategy_to_num(strategy: str) -> float:
    return {
        "TREND": 1.0, "BREAKOUT": 2.0, "MEAN_REVERSION": 3.0,
        "FALLBACK": 4.0, "AUTO": 5.0, "SCALPING": 6.0, "SWING": 7.0,
    }.get(str(strategy or "").upper(), 0.0)


def _build_ai_features(signal: Dict[str, Any]) -> List[float]:
    """Build the 8-feature vector that SelfLearningEngine trained on."""
    return [
        _safe_float(signal.get("confidence"),   0.0),
        _safe_float(signal.get("score"),        0.0),
        _safe_float(signal.get("regime_score"), 0.0),
        _safe_float(signal.get("volatility"),   0.0),
        _safe_float(signal.get("atr"),          0.0),
        _regime_to_num(signal.get("regime",    "UNKNOWN")),
        _side_to_num(  signal.get("side",      "")),
        _strategy_to_num(signal.get("strategy", "")),
    ]


# ── Bounded LRU cache helpers (prevent memory leaks) ─────────────────────────
from functools import lru_cache
_OHLCV_CACHE: dict = {}          # symbol → (ts, df) — max 200 entries
_NEWS_SCORE_CACHE: dict = {}     # symbol → (ts, score) — max 200 entries
_MAX_CACHE_ENTRIES = 200

def _cache_set(cache: dict, key: str, value) -> None:
    import time
    if len(cache) >= _MAX_CACHE_ENTRIES:
        # Remove oldest 20% of entries
        oldest = sorted(cache.items(), key=lambda x: x[1][0] if isinstance(x[1], tuple) else 0)
        for k, _ in oldest[:_MAX_CACHE_ENTRIES // 5]:
            cache.pop(k, None)
    cache[key] = (time.time(), value)

def _cache_get(cache: dict, key: str, ttl: float = 300):
    import time
    entry = cache.get(key)
    if entry and time.time() - entry[0] < ttl:
        return entry[1]
    return None


class LiveSignalEngine:
    """
    Live engine: scan → signal → AI/RL filter → option selection
                 → risk gate → order execution.
    """

    def __init__(self, gap_risk_manager=None) -> None:
        self.total_capital = DEFAULT_TOTAL_CAPITAL
        self._gap_risk_manager = gap_risk_manager  # GA-6: direct ref instead of gc hack
        # Market context: VIX direction, FII/DII, prev-day bias, sector rotation
        self._market_context   = _get_mctx() if _MCTX_AVAILABLE else None
        # Day classifier: classifies TREND/RANGE/VOLATILE by 10 AM
        self._day_classifier   = _get_dc()  if _DC_AVAILABLE   else None
        # Option intelligence: delta, gamma, theta tracking
        self._option_intel     = _get_oi()  if _OI_AVAILABLE   else None
        # Scale-in manager: institutional 3-tranche entries
        self._scale_manager    = _get_sim() if _SIM_AVAILABLE  else None
        # Option chain engine: CE/PE, real-time ATM, smart expiry
        self._option_chain_engine = _OCE() if _OCE_AVAILABLE else None
        # Unified market data feeds (VIX, greeks, breadth, circuits, margins)
        self._feeds = _get_feeds() if _FEEDS_AVAILABLE else None
        # Institutional alpha engine
        self._alpha_engine    = _get_alpha_engine() if _ALPHA_AVAILABLE else None
        # Execution algo (TWAP/VWAP order splitting)
        self._exec_algo     = _get_exec_algo(paper_mode=True) if _EXEC_ALGO_AVAILABLE else None
        self._global_filter = _get_gf() if _GF_AVAILABLE else None
        self._strat_matrix  = _get_sm() if _SM_AVAILABLE else None
        self._greeks_sizer  = _get_gs() if _GS_AVAILABLE else None
        self._event_cal     = _get_ec() if _EC_AVAILABLE else None

        # SL Hunt Guard and Swing Protection
        self._sl_guard         = _get_sl_guard()         if _SLHG_AVAILABLE else None
        self._swing_protect    = _get_swing_protect()    if _SLHG_AVAILABLE else None
        # Advanced strategy engines
        self._rs_filter        = _get_rs_filter()        if _ADV_LIVE_AVAILABLE else None
        self._global_macro    = _get_global_macro() if _ADV_LIVE_AVAILABLE else None
        self._kelly_sizer     = _get_kelly_sizer()  if _ADV_LIVE_AVAILABLE else None
        self._expiry_pattern:  dict  = {}
        self._macro_bias:      float = 0.0
        self._last_signal_bar: dict  = {}   # symbol → last bar index that fired a signal
        # ATM straddle cache for expected-range filter
        self._atm_straddle:    float = 0.0
        self._day_profile      = None

        # Locate nifty200.csv — check multiple paths
        import os as _os
        _csv_candidates = [
            "nifty200.csv",
            _os.path.join(_os.path.dirname(__file__), "nifty200.csv"),
            _os.path.expanduser("~/Desktop/trading_robot/nifty200.csv"),
            _os.path.expanduser("~/trading_robot/nifty200.csv"),
        ]
        _csv_path = next((p for p in _csv_candidates if _os.path.exists(p)), None)
        if _csv_path:
            logger.info("Loading symbol universe from: %s", _csv_path)
        else:
            logger.warning("nifty200.csv not found — using default 37 symbols")
        self.data_fetcher    = DataFetcher(symbols_csv=_csv_path)
        self.learning_engine = SelfLearningEngine(strategy_state_file="strategy_state.json")
        self.ai_filter       = AITradeFilter()

        broker_config = {
            "API_KEY":         getattr(cfg, "API_KEY",         ""),
            "CLIENT_ID":       getattr(cfg, "CLIENT_ID",       ""),
            "PASSWORD":        getattr(cfg, "PASSWORD",        ""),
            "TOTP_SECRET":     getattr(cfg, "TOTP_SECRET",     ""),
            "DHAN_CLIENT_CODE": getattr(cfg, "DHAN_CLIENT_CODE", ""),
            "DHAN_TOKEN_ID":   getattr(cfg, "DHAN_TOKEN_ID",   ""),
            "PAPER_TRADE":     bool(getattr(cfg, "PAPER_TRADE", getattr(cfg, "PAPER_TRADING", True))),
        }
        self.broker_manager = BrokerManager(broker_config)

        _alerts = AlertManager(
            bot_token          = getattr(cfg, 'TELEGRAM_BOT_TOKEN', None),
            chat_id            = getattr(cfg, 'TELEGRAM_CHAT_ID',   None),
            enabled            = bool(getattr(cfg, 'TELEGRAM_ENABLED', True)),
        )
        self.trade_manager = TradeManager(
            broker_manager      = self.broker_manager,
            capital             = self.total_capital,
            max_open_positions  = DEFAULT_MAX_OPEN_POSITIONS,
            daily_loss_limit    = DEFAULT_MAX_DAILY_LOSS,
            brokerage_per_order = float(getattr(cfg, "BROKERAGE_PER_ORDER", 20.0)),
            stt_rate            = float(getattr(cfg, "STT_RATE", 0.0005)),
            enable_trailing     = bool(getattr(cfg, "ENABLE_TRAILING", True)),
            db_path             = getattr(cfg, "TRADES_DB", "trades.db"),
            restore_state       = True,
            alert_manager       = _alerts,
        )

        # Trailing stop manager — real-time position monitoring
        self.trailing_manager = None
        if _TRAILING_AVAILABLE:
            try:
                self.trailing_manager = _TrailingMgr(
                    config={
                        'STOP_ATR_MULT':       float(getattr(cfg,'STOP_ATR_MULT',2.0)),
                        'TRAIL_ATR_MULT':      float(getattr(cfg,'TRAIL_ATR_MULT',1.5)),
                        'TRAIL_ACTIVATE_MULT': float(getattr(cfg,'TRAIL_ACTIVATE_MULT',0.0)),
                        'TARGET1_ATR_MULT':    float(getattr(cfg,'TARGET1_ATR_MULT',1.5)),
                        'TARGET2_ATR_MULT':    float(getattr(cfg,'TARGET2_ATR_MULT',2.5)),
                        'TARGET3_ATR_MULT':    float(getattr(cfg,'TARGET3_ATR_MULT',4.0)),
                        'MAX_HOLD_BARS':       int(getattr(cfg,'MAX_HOLD_BARS',12)),
                    }
                )
                logger.info('TrailingStopManager initialised')
            except Exception as exc:
                logger.warning('TrailingStopManager init failed: %s', exc)

        # Daily loss manager — wired to trade_manager so lock/unlock propagates
        self.daily_loss_manager = DailyLossLimitManager(
            hard_limit            = DEFAULT_MAX_DAILY_LOSS,
            soft_limit            = float(getattr(cfg, "SOFT_DAILY_LOSS_LIMIT", 2000.0)),
            include_unrealized    = False,   # realized only in live engine
            auto_close_on_breach  = True,
            trade_manager         = self.trade_manager,
        )

        self.option_fetchers: Dict[str, NSEOptionChainFetcher] = {
            symbol: NSEOptionChainFetcher(underlying=symbol)
            for symbol in SUPPORTED_OPTION_UNDERLYINGS
        }

        self.option_selector = OptionSelector(
            lot_size         = int(getattr(cfg, "OPTION_LOT_SIZE", 50)),
            max_lots_per_trade = int(getattr(cfg, "MAX_LOTS", 3)),
        )

        self.position_sizer = AdaptivePositionSizer(
            min_risk_pct     = float(getattr(cfg, "MIN_RISK_PCT",       0.0025)),
            max_risk_pct     = float(getattr(cfg, "MAX_RISK_PCT",       0.02)),
            default_risk_pct = float(getattr(cfg, "RISK_PER_TRADE_PCT", 0.01)),
            min_lots         = int(getattr(cfg,   "MIN_LOTS",           1)),
            max_lots         = int(getattr(cfg,   "MAX_LOTS",           20)),
            option_lot_size  = int(getattr(cfg,   "OPTION_LOT_SIZE",    50)),
        )

        self.capital_allocator = CapitalAllocator(
            total_capital   = self.total_capital,
            swing_pct       = float(getattr(cfg, "SWING_CAPITAL_PCT",    0.45)),
            intraday_pct    = float(getattr(cfg, "INTRADAY_CAPITAL_PCT",  0.30)),
            scalping_pct    = float(getattr(cfg, "SCALPING_CAPITAL_PCT",  0.15)),
            reserve_pct     = float(getattr(cfg, "RESERVE_CAPITAL_PCT",   0.10)),
        )

        self.risk_manager = PortfolioRiskManager(
            capital                  = self.total_capital,
            max_open_positions       = DEFAULT_MAX_OPEN_POSITIONS,
            max_portfolio_risk_pct   = float(getattr(cfg, "MAX_PORTFOLIO_RISK_PCT",   0.03)),
            max_symbol_exposure_pct  = float(getattr(cfg, "MAX_SYMBOL_EXPOSURE_PCT",  0.25)),
            max_total_exposure_pct   = float(getattr(cfg, "MAX_TOTAL_EXPOSURE_PCT",   1.0)),
            max_correlated_positions = int(  getattr(cfg, "MAX_CORRELATED_POSITIONS", 1)),
            max_premium_per_trade_pct = float(getattr(cfg, "MAX_PREMIUM_PER_TRADE_PCT", 0.10)),
        )

        self.last_run_time: Optional[float] = None
        self.max_signals_per_cycle = DEFAULT_MAX_SIGNALS_PER_CYCLE
        self.max_workers           = DEFAULT_WORKERS

        # Reuse executor across cycles — avoids 960 thread-pool create/destroy per day
        self.executor = ThreadPoolExecutor(max_workers=max(1, self.max_workers))

        # Circuit breaker: pause new entries after repeated order failures
        self._consecutive_exec_failures: int   = 0
        self._circuit_breaker_until:     float = 0.0   # epoch seconds
        self._circuit_breaker_pause_sec: int   = int(getattr(cfg, 'CIRCUIT_BREAKER_PAUSE_SEC', 300))
        self._circuit_breaker_threshold: int   = int(getattr(cfg, 'CIRCUIT_BREAKER_THRESHOLD', 3))

        # India VIX cache
        self._vix_cache_val: float = 0.0
        self._vix_cache_ts:  float = 0.0

        # Param bridge: backtest-optimised params → live signal config
        self._param_bridge = _get_pb() if _PB_AVAILABLE else None

        # WebSocket engine for real-time LTP streaming
        self.ws_engine: Optional[Any] = None
        if _WS_AVAILABLE:
            try:
                angel_obj = None
                broker = self.broker_manager.get_execution_broker()
                if broker and hasattr(broker, 'angel'):
                    angel_obj = broker.angel
                self.ws_engine = WebSocketEngine(
                    angel_obj      = angel_obj,
                    trade_manager  = self.trade_manager,
                    trailing       = self.trailing_manager,
                    alerts         = self.alerts if hasattr(self, 'alerts') else None,
                )
                # Wire trailing_manager (created above)
                self.ws_engine.trailing = self.trailing_manager
                logger.info('WebSocketEngine initialised (trailing wired)')
                # Wire broker into option chain engine
                if self._option_chain_engine and angel_obj:
                    self._option_chain_engine._broker = angel_obj
                # Wire broker into all market data feeds
                if self._feeds and angel_obj:
                    self._feeds.set_broker(angel_obj)
            except Exception as exc:
                logger.warning('WebSocketEngine init failed: %s', exc)
        if self._param_bridge:
            logger.info('ParamBridge loaded: %s',
                        self._param_bridge.get_status_summary())

        # Capital compounder — auto-scales params with equity
        self.capital_compounder = CapitalCompounder() if _CC_AVAILABLE else None

        # Sector map for correlation limits
        self._sector_map: dict = load_sector_map() if _CC_AVAILABLE else {}
        logger.info('Sector map loaded: %d symbols', len(self._sector_map))

        # Peak equity tracking for drawdown calculation
        self._peak_equity: float = self.total_capital

    # ------------------------------------------------------------------
    # Daily reset — called by main_autonomous._on_new_trading_day()
    # ------------------------------------------------------------------
    def reset_daily_state(self) -> None:
        """Reset all day-scoped accumulators for a new trading day."""
        self.daily_loss_manager.reset_day()
        self.trade_manager.reset_daily_state()
        logger.info("LiveSignalEngine: daily state reset")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _broadcast_signal_via_quality_gate(self, signal: dict) -> None:
        """Wire SignalBroadcaster for quality-gated subscriber distribution."""
        try:
            from signal_broadcaster import get_broadcaster
            # Enrich signal with context
            signal["vix"]    = getattr(self, "_vix_cache_val", 15.0)
            signal["regime"] = signal.get("regime", "TRENDING")
            get_broadcaster(alerts=self.alerts).broadcast(signal)
        except Exception as _be:
            logger.debug("broadcaster: %s", _be)

    def _in_market_open_warmup(self) -> bool:
        """
        9:15-9:20 AM: observe-only mode.
        First 5 min of trading are volatile with wide spreads.
        Signals are generated (for monitoring) but not executed.
        """
        import datetime as _dt2
        now = _dt2.datetime.now().time()
        return (_dt2.time(9, 15) <= now < _dt2.time(9, 20))


    def run(self) -> None:
        logger.info("Live Signal Engine started")
        # Start WebSocket for real-time price streaming
        if self.ws_engine:
            started = self.ws_engine.start()
            if started:
                logger.info('WebSocket streaming started — trailing stops now real-time')
            else:
                logger.warning('WebSocket not started — using 30s REST polling fallback')
        sleep_sec = int(getattr(cfg, "MAIN_LOOP_SLEEP_SEC", 30))

        while True:
            try:
                self._run_cycle()
            except Exception:
                logger.exception("Live cycle failed")
            time.sleep(max(5, sleep_sec))

    # ------------------------------------------------------------------
    # One cycle
    # ------------------------------------------------------------------
    def _run_cycle(self) -> None:
        # RULE: Scanning ALWAYS runs regardless of capital.
        # Capital only determines PAPER vs LIVE for order placement.
        # Even with ₹0 balance, scan + paper trades continue.
        now = time.time()
        if self.last_run_time and (now - self.last_run_time) < 20:
            return
        self.last_run_time = now

        # Event calendar block (EXTREME impact days = no new trades)
        if _EC_AVAILABLE and self._event_cal:
            try:
                _blocked, _ec_reason = self._event_cal.should_block_new_trades()
                if _blocked:
                    self._log_no_signal(f"event_calendar_blocked_{_ec_reason}")
                    return
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Check daily loss limit before doing any work this cycle
        self.daily_loss_manager.auto_reset_if_new_day()
        if not self.daily_loss_manager.can_trade():
            self._log_no_signal("daily_loss_limit_locked")
            return

        # Block new entries during EOD exit window — positions are being closed,
        # not opened. main_autonomous handles the actual square-off.
        # ── Holiday-aware: no trade 30min before holiday + last 30min ─────────
        try:
            from holiday_engine import is_tomorrow_holiday
            from datetime import datetime as _dt
            if is_tomorrow_holiday() and _dt.now().hour >= 15 and _dt.now().minute >= 0:
                logger.debug("Pre-holiday no-trade window")
                return  # observation only
        except Exception: pass

        if self._in_eod_window():
            self._log_no_signal("eod_exit_window")
            return

        # India VIX gate — sets flag, only blocks OPTION BUYING (not stocks/futures)
        _vix_blocks_options = self._is_vix_too_high()

        # ── Price alert check (UX-16) ─────────────────────────────────────────
        try:
            from ux_engine import check_price_alerts
            _cur_prices = {}
            if hasattr(self, "_last_nifty_price"):
                _cur_prices["NIFTY"] = self._last_nifty_price
            triggered = check_price_alerts(_cur_prices)
            for _alrt in triggered:
                self.alerts.send(_alrt["message"])
        except Exception: pass

        # Entropy check — skip signals when market is pure noise
        if _ENTROPY_AVAILABLE and self._vix_cache_val < 20:
            try:
                # Sample NIFTY data for entropy check
                _nf_d = getattr(self, "_nifty_df_cache", None)
                if _nf_d is not None and len(_nf_d) >= 25:
                    if not _is_structured(_nf_d, threshold=0.72):
                        self._log_no_signal("entropy_too_high_noisy_market")
                        # Do NOT return — just reduce scores in candidates
                        # (entropy is advisory, not a hard block)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Circuit breaker: pause after repeated broker order failures
        if time.time() < self._circuit_breaker_until:
            remaining = int(self._circuit_breaker_until - time.time())
            self._log_no_signal("circuit_breaker_active", {"resume_in_sec": remaining})
            return

        # ── Kill switch check ────────────────────────────────────────────
        try:
            from kill_switch import KillSwitch
            if KillSwitch().is_active():
                logger.warning("KILL SWITCH ACTIVE — skipping scan cycle")
                return
        except Exception: pass

        # Force session refresh at market open (first 20 min)
        try:
            import datetime as _dtx
            _now_t = _dtx.datetime.now()
            _is_open_window = (_now_t.hour == 9 and _now_t.minute <= 35)
            _last_force = getattr(self, "_last_force_refresh", 0)
            import time as _tx
            if _is_open_window and _tx.time() - _last_force > 600:
                _ang = getattr(getattr(self,"data_fetcher",None),"angel",None)
                if _ang and hasattr(_ang,"_auto_refresh_session"):
                    _ang._auto_refresh_session()
                    self._last_force_refresh = _tx.time()
                    logger.info("Session force-refreshed at market open")
        except Exception: pass

        # Refresh live balance from Angel every 5 min
        self._refresh_angel_balance()
        # Update capital compounder with current balance
        self._update_capital_tier()
        # paper_trade_if_no_capital: if balance too low for live,
        # all signals are logged as paper trades

        # GA-4: REST fallback monitoring when WebSocket disconnected
        ws_ok = self.ws_engine and self.ws_engine.is_connected()
        now_ts_mon = time.time()
        if not ws_ok and (now_ts_mon - self._last_rest_monitor_ts) >= 5:
            self._last_rest_monitor_ts = now_ts_mon
            self._monitor_open_positions_rest()

        market_data = self._fetch_market_data_with_htf()
        if not market_data:
            self._log_no_signal("no_data")
            return

        # INSTITUTIONAL RULE: never trade first 5 minutes of market open
        now_t = __import__('datetime').datetime.now().time()
        import datetime as _dt
        if _dt.time(9,15) <= now_t <= _dt.time(9,20):
            logger.info("FIRST_5_MIN_BLOCK: no entries before 9:20 AM")
            return

        # Fetch BANKNIFTY for cross-index divergence check
        try:
            _bnf_df = self._fetcher.get_market_data("BANKNIFTY", interval="5m", days=1)
        except Exception:
            _bnf_df = None
        self._bnf_df_cache = _bnf_df

        # Cache NIFTY df for relative strength filter
        try:
            _nifty_df = self._fetcher.get_market_data("NIFTY", interval="5m", days=1)
            self._nifty_df_cache = _nifty_df
        except Exception:
            self._nifty_df_cache = None

        # Refresh expiry week pattern and global macro bias
        if _ADV_LIVE_AVAILABLE:
            try:
                self._expiry_pattern = _expiry_week_regime()
                self._macro_bias     = _get_global_macro().get_nifty_bias()
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Refresh day profile every 30 min if day_classifier available
        if self._day_classifier and market_data:
            try:
                _nf_sym = "NIFTY"
                _nf_data = market_data.get(_nf_sym, {})
                _nf_df   = _nf_data.get("df") if isinstance(_nf_data, dict) else _nf_data
                if _nf_df is not None:
                    self._day_profile = self._day_classifier.get_profile(
                        df_nifty     = _nf_df,
                        df_banknifty = _bnf_df,
                        vix          = getattr(self, "_vix_cache_val", 15.0) or 15.0,
                        atm_straddle = self._atm_straddle,
                    )
                    if self._day_profile and self._day_profile.day_type == DAY_VOLATILE:
                        logger.warning("DAY_CLASSIFIER: VOLATILE_DAY — no new entries")
                        return
            except Exception as _dc_exc:
                logger.debug("day_classifier error: %s", _dc_exc)

        # Sync broker positions to detect silent closes
        if self._feeds:
            try:
                _closed = self._feeds.sync_positions(self.trade_manager)
                if _closed:
                    logger.info("Position sync: %d silent closes detected", len(_closed))
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        candidates = self._evaluate_market_parallel(market_data)

        # Record ALL strategy scores to DB (regardless of capital)
        try:
            from strategy_score_tracker import record_strategy_score
            for _cand in (candidates or []):
                record_strategy_score(
                    symbol=_cand.get("symbol",""),
                    strategy=_cand.get("strategy",""),
                    score=_cand.get("score",0) or _cand.get("final_rank_score",0),
                    direction=_cand.get("direction",""),
                    regime=_cand.get("regime",""),
                    vix=self._vix_cache_val,
                    price=_cand.get("price",0),
                    reasons=_cand.get("reasons",[]),
                )
        except Exception: pass


        if not candidates:
            logger.warning("No candidates from full filter path, trying fallback scan")
            candidates = self._build_fallback_candidates(market_data)

        if not candidates:
            self._log_no_signal("no_valid_signal")
            return

        open_positions  = self.trade_manager.get_open_positions()
        available_slots = max(0, DEFAULT_MAX_OPEN_POSITIONS - len(open_positions))
        if available_slots <= 0:
            self._log_no_signal(
                "max_open_positions_reached",
                {"open_positions": len(open_positions)},
            )
            return

        max_new_trades = min(self.max_signals_per_cycle, available_slots)
        selected       = candidates[:max_new_trades]

        # Log top candidates before executing
        if candidates:
            logger.info(
                "Top %d candidates this cycle:",
                min(5, len(candidates))
            )
            for i, c in enumerate(candidates[:5], 1):
                sig = c.get("signal", {})
                logger.info(
                    "  #%d %s%s | strategy=%-12s side=%-4s score=%.2f ai=%.2f",
                    i,
                    "★ " if c.get("priority_symbol") else "  ",
                    c.get("symbol", "?"),
                    sig.get("strategy", "?"),
                    sig.get("side", "?"),
                    c.get("final_rank_score", 0),
                    c.get("ai_probability", 0),
                )

        executed = 0
        for candidate in selected:
            if self._execute_candidate(candidate):
                executed += 1

        if executed == 0:
            self._log_no_signal("all_candidates_blocked")

    # ------------------------------------------------------------------
    # Multi-timeframe data fetch
    # ------------------------------------------------------------------
    def _fetch_market_data_with_htf(self) -> Optional[Dict[str, Any]]:
        """
        Attempt to fetch both primary (5m) and HTF (15m) data.
        Returns a dict keyed by symbol with values:
            {"df": DataFrame, "df_htf": DataFrame}
        Falls back to using df as df_htf if HTF fetch fails.
        """
        try:
            # Try the multi-TF method if DataFetcher supports it
            if hasattr(self.data_fetcher, "get_latest_data_multi_tf"):
                return self.data_fetcher.get_latest_data_multi_tf()

            # Fallback: fetch primary data, attempt separate HTF fetch
            primary_data = self.data_fetcher.get_latest_data()
            if not primary_data:
                return None

            result: Dict[str, Any] = {}
            primary_interval = getattr(cfg, "DEFAULT_INTERVAL", "5m")
            htf_interval     = _HTF_INTERVAL_MAP.get(primary_interval, "15m")

            for symbol, df in primary_data.items():
                df_htf = df  # fallback
                try:
                    if hasattr(self.data_fetcher, "get_market_data"):
                        htf_df = self.data_fetcher.get_market_data(
                            symbol, interval=htf_interval, days=5
                        )
                        if htf_df is not None and len(htf_df) >= 20:
                            df_htf = htf_df
                        else:
                            logger.debug("HTF data insufficient for %s, using primary", symbol)
                except Exception:
                    logger.debug("HTF fetch failed for %s, using primary as df_htf", symbol)

                result[symbol] = {"df": df, "df_htf": df_htf}

            return result if result else None

        except Exception:
            logger.exception("Market data fetch failed")
            # ── Last-resort fallback: yfinance + bhavcopy for all symbols ──────
            try:
                from data_fetcher import get_ohlcv_with_fallback
                symbols = getattr(self, "_symbols", [])[:20]  # limit in fallback
                result_fb = {}
                for sym in symbols:
                    df_fb = get_ohlcv_with_fallback(sym, days=5, interval="5m")
                    if df_fb is not None and len(df_fb) > 5:
                        result_fb[sym] = {"df": df_fb, "df_htf": df_fb}
                if result_fb:
                    logger.warning("Using fallback data for %d symbols", len(result_fb))
                    return result_fb
            except Exception as _fe:
                logger.debug("Fallback also failed: %s", _fe)
            return None

    # ------------------------------------------------------------------
    # Parallel market evaluation
    # ------------------------------------------------------------------
    def _evaluate_market_parallel(
        self, market_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        # GA-5: Try StrategyScanner first (has confluence + time-zone + tier-1 boost)
        scanner = getattr(self, '_strategy_scanner', None)
        if scanner is not None:
            try:
                scan_results = scanner.scan(market_data)
                if scan_results:
                    # Convert ScanResult objects to candidate dicts
                    candidates = []
                    for r in scan_results:
                        # Build a signal dict from scan result
                        signal = {
                            'symbol':     r.symbol,
                            'side':       r.action,
                            'strategy':   r.strategy,
                            'score':      r.score,
                            'confidence': r.confidence,
                            'regime':     r.regime,
                            'confluence': r.confluence_level,
                        }
                        candidates.append({
                            'symbol':          r.symbol,
                            'signal':          signal,
                            'df':              r.df,
                            'df_htf':          r.df_htf,
                            'ai_probability':  r.confidence,
                            'rl_bias':         self._rl_score_adjustment(r.strategy),
                            'option_signal':   None,
                            'option_summary':  None,
                            'option_bias':     1.5 if r.is_tier1 else 0.0,
                            'ai_penalty':      0.0,
                            'priority_symbol': r.is_tier1,
                            'final_rank_score': r.final_score,
                        })
                    logger.info(
                        'StrategyScanner: %d candidates (tier1=%d)',
                        len(candidates),
                        sum(1 for c in candidates if c.get('priority_symbol')),
                    )
                    return candidates
            except Exception as exc:
                logger.warning('StrategyScanner failed, using fallback: %s', exc)
        # Fallback: original parallel evaluation
        candidates: List[Dict[str, Any]] = []

        # Reuse the persistent executor (created once in __init__)
        futures = {
            self.executor.submit(
                self._evaluate_symbol_candidate,
                symbol,
                entry["df"]     if isinstance(entry, dict) else entry,
                entry.get("df_htf") if isinstance(entry, dict) else entry,
                entry,
            ): symbol
            for symbol, entry in market_data.items()
        }
        for future in as_completed(futures):
            try:
                candidate = future.result()
                if candidate:
                    candidates.append(candidate)
            except Exception:
                logger.exception("Parallel symbol evaluation failed")

        # ── Pre-scan connection ping ─────────────────────────────────
        if self._conn_monitor:
            try:
                if not self._conn_monitor.run_ping():
                    logger.warning("Critical feed down — skipping scan cycle")
                    return []
            except Exception: pass

        # Refresh intelligence cache before evaluating candidates
        _refresh_intel_cache()
        # Apply F&O ban filter
        _ban_list = _INTEL_CACHE.get("ban_list", set())
        if _ban_list:
            _before = len(market_data)
            market_data = {s: d for s,d in market_data.items() if s not in _ban_list}
            if len(market_data) < _before:
                logger.info("F&O ban filter removed %d symbols", _before-len(market_data))

        # Log ALL candidates to signal_log (executed + rejected)
        if _SIG_LOG_AVAIL:
            try:
                _sl = _get_sig_log()
                # Gather market context once per cycle
                _vix_ctx  = float(getattr(self, "_vix_cache_val", 15) or 15)
                _fii_ctx  = {}
                try:
                    from participant_oi import get_participant_data, get_cumulative_fii
                    _pd_ctx = get_participant_data()
                    _fii_d  = (_pd_ctx or {}).get("FII", {})
                    _fii_ctx = {
                        "fii_net_cash": float((_fii_d or {}).get("net_cash", 0) or 0) / 100,
                        "fii_fut_ratio": float((_fii_d or {}).get("fut_long", 1) or 1) /
                            max(float((_fii_d or {}).get("fut_short", 1) or 1), 0.001),
                        "fii_cum_5d": get_cumulative_fii(5),
                    }
                except Exception: pass
                _er_ctx = {}
                try:
                    from expiry_regime import get_expiry_regime
                    _er = get_expiry_regime()
                    _er_ctx = {"expiry_dte": _er.get("days_to_expiry",5), "expiry_regime": _er.get("regime_label","NORMAL")}
                except Exception: pass
                for _cand in candidates:
                    _sl.log_candidate(
                        signal           = _cand,
                        executed         = False,  # updated later if executed
                        india_vix        = _vix_ctx,
                        **_fii_ctx,
                        **_er_ctx,
                        trade_num_today  = self._rejection_stats.get("passed", 0),
                    )
            except Exception as _sle:
                logger.debug("Signal log candidates: %s", _sle)

        # Priority boost: NIFTY/BANKNIFTY/indices score higher when equal
        for c in candidates:
            if c.get("symbol") in PRIORITY_SYMBOLS:
                c["final_rank_score"] = c.get("final_rank_score", 0) + TIER1_SCORE_BOOST
                c["priority_symbol"] = True
        candidates.sort(key=lambda x: x.get("final_rank_score", -9999), reverse=True)
        # Track rejection stats for EOD report
        if not hasattr(self, "_rejection_stats"):
            self._rejection_stats = {"total": 0, "passed": 0, "reasons": {}}
        self._rejection_stats["total"] += len(market_data)
        self._rejection_stats["passed"] += len(candidates)
        return candidates

    def _evaluate_symbol_candidate(
        self, symbol: str, df, df_htf, entry_raw=None
    ) -> Optional[Dict[str, Any]]:
        try:
            # Reduce minimum bars for morning session when multi-day data loaded
            _min_bars = 5 if (df is not None and len(df) < 20 and len(df) >= 5) else (
                20 if datetime.now().hour < 10 else 50)
            if df is None or len(df) < _min_bars:
                return None

            if df_htf is None or id(df_htf) == id(df):
                logger.debug("HTF not available for %s — using primary TF as fallback", symbol)
                df_htf = df

            # Extract 1-hour data when available (from get_latest_data_three_tf)
            df_1h = entry_raw.get("df_1h") if isinstance(entry_raw, dict) else None

            # Get backtest-optimised params for this symbol
            _pb_config = None
            if self._param_bridge:
                _best_strat = self._param_bridge.get_best_strategy(symbol)
                if _best_strat:
                    _pb_config = self._param_bridge.get_config(symbol, _best_strat)
                    _pb_config["strategy"] = _best_strat

            # Pass market context into generate_signal
            _ctx = {
                "vix":              _INTEL_CACHE.get("vix", 15.0),
                "cross_asset_bias": _INTEL_CACHE.get("cross_asset_bias","NEUTRAL"),
                "news_score":       _INTEL_CACHE.get("news_score", 0.0),
                "expiry_dte":       _INTEL_CACHE.get("expiry_dte", 5),
                "expiry_regime":    _INTEL_CACHE.get("expiry_regime","NORMAL"),
                "whale_mod":        _INTEL_CACHE["whale_index"].get(symbol, 0),
            }
            if _pb_config:
                _pb_config.update(_ctx)
            else:
                _pb_config = _ctx
            signal = generate_signal(df=df, df_htf=df_htf, symbol=symbol,
                                      config=_pb_config)
            # Sector + vol adjustments
            if signal and signal.get("side"):
                try:
                    from sector_rotation_engine import get_sector_multiplier as _gsm
                    _sm = _gsm(symbol)
                    if _sm != 1.0: signal["sector_multiplier"] = _sm
                except Exception: pass
                try:
                    from wow_factors_v2 import volatility_targeted_size as _vts
                    import config as _cfg_v
                    _cap = float(getattr(_cfg_v,"REAL_CAPITAL",26964))
                    _vt  = _vts(symbol, _cap, df)
                    if _vt: signal["vol_scalar"] = _vt.get("vol_scalar",1.0)
                except Exception: pass
                try:
                    _open_s = list(getattr(getattr(self,"trade_manager",None),
                                          "open_trades",{}).keys())
                    _ca = getattr(self,"capital_allocator",None)
                    if _ca and hasattr(_ca,"can_open_position"):
                        _ok,_why = _ca.can_open_position(symbol,_open_s)
                        if not _ok:
                            logger.debug("Sector limit %s: %s",symbol,_why)
                            return None
                except Exception: pass
            if not signal:
                return None
            # Normalise direction/side — both keys accepted
            if not signal.get("side") and signal.get("direction"):
                signal["side"] = signal["direction"]
            if not signal.get("side"):
                return None

            # 1-hour confirmation filter (skipped in RANGE / mean_reversion)
            if df_1h is not None and len(df_1h) >= 10:
                regime = str(signal.get("regime", "")).upper()
                strategy = str(signal.get("strategy", "")).lower()
                if regime not in ("RANGE",) and strategy != "mean_reversion":
                    if not self._passes_1h_filter(df_1h, signal.get("side")):
                        return None

            ai_prob            = self._get_ai_probability(signal)
            decision, filter_meta = self.ai_filter.evaluate(signal, ai_prob)

            ai_penalty    = 0.0 if decision else -1.0
            signal["confidence"] = ai_prob
            signal["filter_meta"] = filter_meta
            signal["ai_penalty"]  = ai_penalty

            option_signal = None
            option_summary = None
            option_bias   = 0.0

            if symbol in self.option_fetchers:
                try:
                    option_result = self.option_fetchers[symbol].fetch_and_analyze()
                    if option_result:
                        option_signal  = option_result.signal
                        option_summary = option_result.summary
                except Exception:
                    logger.debug("Option chain fetch failed for %s", symbol)

            if symbol in SUPPORTED_OPTION_UNDERLYINGS and option_signal:
                if signal["side"] == "BUY"  and option_signal.get("signal") == "BUY_CALL":
                    option_bias = 1.5
                elif signal["side"] == "SELL" and option_signal.get("signal") == "BUY_PUT":
                    option_bias = 1.5
                else:
                    option_bias = -1.0

            rl_bias = self._rl_score_adjustment(signal.get("strategy"))
            final_rank_score = (
                float(signal.get("score", 0))
                + rl_bias + option_bias
                + float(ai_prob) + ai_penalty
            )

            is_priority = symbol in PRIORITY_SYMBOLS
            logger.info(
                "SIGNAL | %s symbol=%s strategy=%s side=%s score=%.2f "
                "ai=%.2f rl=%.2f opt_bias=%.1f regime=%s conf=%s",
                "★" if is_priority else " ",
                symbol,
                signal.get("strategy", "?"),
                signal.get("side", "?"),
                float(signal.get("score", 0)),
                ai_prob, rl_bias, option_bias,
                signal.get("regime", "?"),
                signal.get("confluence", "LOW"),
            )
            return {
                "symbol":          symbol,
                "signal":          signal,
                "df":              df,
                "df_htf":          df_htf,
                "ai_probability":  ai_prob,
                "rl_bias":         rl_bias,
                "option_signal":   option_signal,
                "option_summary":  option_summary,
                "option_bias":     option_bias,
                "ai_penalty":      ai_penalty,
                "priority_symbol": is_priority,
                "final_rank_score": round(final_rank_score, 4),
            }
        except Exception:
            logger.exception("Failed to evaluate symbol=%s", symbol)
            return None

    def _build_fallback_candidates(
        self, market_data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        fallback: List[Dict[str, Any]] = []

        for symbol, entry in market_data.items():
            try:
                df     = entry["df"]     if isinstance(entry, dict) else entry
                df_htf = entry.get("df_htf", df) if isinstance(entry, dict) else df

                if df is None or len(df) < 100:
                    continue

                signal = generate_signal(df=df, df_htf=df_htf, symbol=symbol)
                if signal and signal.get("side"):
                    # Apply AI filter with same penalty as main path
                    # Fallback uses fixed 0.5 probability (no model inference)
                    # but still respects the filter's decision
                    fallback_prob = 0.5
                    decision, filter_meta = self.ai_filter.evaluate(signal, fallback_prob)
                    ai_penalty = 0.0 if decision else -1.0
                    signal["confidence"]   = fallback_prob
                    signal["filter_meta"]  = filter_meta
                    signal["ai_penalty"]   = ai_penalty
                    rl_bias = self._rl_score_adjustment(signal.get("strategy"))
                    final_score = float(signal.get("score", 0)) + rl_bias + ai_penalty
                    fallback.append({
                        "symbol":           symbol,
                        "signal":           signal,
                        "df":               df,
                        "df_htf":           df_htf,
                        "ai_probability":   fallback_prob,
                        "rl_bias":          rl_bias,
                        "option_signal":    None,
                        "option_summary":   None,
                        "option_bias":      0.0,
                        "ai_penalty":       ai_penalty,
                        "final_rank_score": round(final_score, 4),
                    })
            except Exception:
                logger.exception("Fallback evaluation failed for symbol=%s", symbol)

        fallback.sort(key=lambda x: x.get("final_rank_score", -9999), reverse=True)
        return fallback

    # ------------------------------------------------------------------
    # AI / RL helpers
    # ------------------------------------------------------------------
    def _get_ai_probability(self, signal: Dict[str, Any]) -> float:
        """
        Get AI win-probability from the per-strategy model when available,
        falling back to the shared model.
        """
        strategy = str(signal.get("strategy", "")).lower()
        # Use per-strategy model if learning engine supports it
        if (hasattr(self.learning_engine, "predict_for_strategy")
                and strategy in self.learning_engine._strategy_models):
            try:
                return self.learning_engine.predict_for_strategy(signal, strategy)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        # Fall through to original shared-model path
        if not getattr(self.learning_engine, "model", None):
            return 0.5

        try:
            features = _build_ai_features(signal)   # 8-feature vector matching training
            return float(self.learning_engine.model.predict_proba([features])[0][1])
        except Exception:
            logger.exception("AI model inference failed")
            return 0.5

    def _rl_score_adjustment(self, strategy: Optional[str]) -> float:
        if not strategy:
            return 0.0
        rl_state       = getattr(self.learning_engine, "rl_state", {}) or {}
        strategy_state = (
            rl_state.get(str(strategy).upper(), {})
            or rl_state.get(str(strategy), {})
        )
        score = _safe_float(strategy_state.get("score"), 0.0)
        return max(-1.5, min(score / 10.0, 1.5))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _passes_daily_chart_check(
        self,
        symbol:    str,
        signal_side: str,
    ) -> bool:
        """
        Validate swing trade entries against the daily chart.
        A BUY signal on 5-min chart should only become a swing trade
        if the DAILY chart is also bullish (price above 50-day EMA).
        
        Returns True (pass) or False (reject as swing).
        """
        try:
            df_daily = self.data_fetcher.get_market_data(symbol, interval="1d", days=60)
            if df_daily is None or len(df_daily) < 20:
                return True   # no daily data → don't block

            from indicators import calculate_ema
            close_col = "Close" if "Close" in df_daily.columns else "close"
            close     = float(df_daily[close_col].iloc[-1])
            ema50     = float(calculate_ema(df_daily, 50).iloc[-1])

            if signal_side == "BUY" and close < ema50 * 0.98:
                logger.info(
                    "SWING rejected: %s price %.2f below daily EMA50 %.2f",
                    symbol, close, ema50,
                )
                return False
            if signal_side == "SELL" and close > ema50 * 1.02:
                logger.info(
                    "SWING rejected: %s price %.2f above daily EMA50 %.2f — bearish signal on bullish daily",
                    symbol, close, ema50,
                )
                return False
            return True
        except Exception:
            return True   # on error, don't block

    def _execute_candidate(self, candidate: Dict[str, Any]) -> bool:
        signal       = candidate["signal"]
        symbol       = candidate["symbol"]
        option_signal = candidate.get("option_signal")
        style        = self._decide_style(signal, option_signal)

        # Capital from the correct bucket for this style
        trade_capital = self.capital_allocator.capital_for_trade(
            style=style,
        )
        # Update total capital from live broker balance
        self.capital_allocator.update_total(self.total_capital)

        execution_plan = self._build_execution_plan(
            symbol=symbol, signal=signal, option_signal=option_signal,
            trade_capital=trade_capital, style=style,
        )
        # Use SmartOrderRouter if available for better execution
        if _SOR_AVAILABLE and execution_plan:
            try:
                _sor = _SOR(brokers=[self.broker_manager])
                _route = _sor.build_route_decision(execution_plan)
                if _route and _route.get("order_type"):
                    execution_plan["order_type"] = _route["order_type"]
                    execution_plan["limit_price"] = _route.get("limit_price")
            except Exception: pass
        if not execution_plan:
            self._log_no_signal("execution_plan_failed", {"symbol": symbol})
            return False

        open_positions = self.trade_manager.get_open_positions()
        risk_decision  = self.risk_manager.evaluate_new_trade(
            symbol            = execution_plan["execution_symbol"],
            entry_price       = execution_plan["entry_price"],
            stop_loss         = execution_plan["stop_loss"],
            requested_quantity = execution_plan["requested_quantity"],
            open_positions    = open_positions,
            correlation_group = execution_plan["correlation_group"],
            current_daily_pnl = self.trade_manager.get_daily_pnl(),
            daily_loss_limit  = DEFAULT_MAX_DAILY_LOSS,
            lot_size          = execution_plan["lot_size"],
            is_options        = execution_plan.get("asset_type") == "OPTION",
            spot_price        = _safe_float(signal.get("price")),
        )

        if not risk_decision.allowed:
            self._log_no_signal(
                "risk_blocked",
                {"symbol": symbol, "risk_decision": risk_decision.to_dict()},
            )
            return False

        final_qty = risk_decision.approved_quantity
        if final_qty <= 0:
            self._log_no_signal("zero_qty_after_risk", {"symbol": symbol})
            return False

        # Kelly Criterion sizing adjustment
        if self._kelly_sizer:
            try:
                kelly_frac = self._kelly_sizer.get_fraction(
                    strategy    = str(signal.get("strategy", "unknown")),
                    default_wr  = 0.55,
                    default_rr  = 1.5,
                )
                kelly_qty = max(1, int(trade_capital * kelly_frac
                    / max(execution_plan.get("entry_price", 1), 1)))
                # Use the LOWER of risk-approved qty and kelly qty
                final_qty = min(final_qty, kelly_qty) if kelly_qty > 0 else final_qty
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── VaR gate ─────────────────────────────────────────────────────────
        try:
            from value_at_risk import get_portfolio_var as _gpv
            _var = _gpv()
            if _var and _var > 0.03:
                final_qty = max(1, int(final_qty * 0.03 / _var))
                logger.debug("VaR=%.1f%% → qty=%d", _var*100, final_qty)
        except Exception: pass

        # ── Beta sizing ───────────────────────────────────────────────────────
        try:
            _BETAS = {"YESBANK":1.8,"ADANIENT":1.7,"ZOMATO":1.6,"PAYTM":1.9,
                      "TATAMOTORS":1.5,"INDUSINDBK":1.4,"BANKBARODA":1.4,
                      "RELIANCE":0.9,"HDFCBANK":0.8,"INFY":0.9,"TCS":0.85,
                      "HINDUNILVR":0.7,"NESTLEIND":0.6}
            _beta = _BETAS.get(symbol.upper(), 1.0)
            if _beta != 1.0:
                final_qty = max(1, int(final_qty / _beta))
                logger.debug("Beta %.2f → qty=%d for %s", _beta, final_qty, symbol)
        except Exception: pass

        # Option intelligence: gamma size multiplier
        if self._option_intel and execution_plan.get("asset_type") == "OPTION":
            try:
                _dte      = execution_plan.get("dte", 3)
                _premium  = execution_plan.get("entry_price", 0)
                _spot     = _safe_float(signal.get("price"), _premium)
                _iv_rank  = getattr(self, "_iv_rank_cache", {}).get(symbol, 0.5)

                # Check overpriced
                _overpriced = self._option_intel.is_option_overpriced(
                    _premium, _spot, _dte, _iv_rank
                )
                if _overpriced["overpriced"]:
                    self._log_no_signal(
                        f"option_overpriced_{_overpriced['reason']}",
                        {"symbol": symbol}
                    )
                    return False

                # Gamma size multiplier
                _gamma = self._option_intel.get_gamma_risk(_dte)
                _gmult = self._option_intel.get_gamma_size_multiplier(_gamma)
                if _gmult < 1.0:
                    final_qty = max(1, int(final_qty * _gmult))
                    logger.info("Gamma risk %s → size reduced to %d", _gamma, final_qty)
            except Exception as _oi_exc:
                logger.debug("option_intel error: %s", _oi_exc)

        # Determine exchange — options trade on NFO, equities on NSE
        exchange = (
            "NFO" if execution_plan.get("asset_type") == "OPTION"
            else getattr(cfg, "EXCHANGE", "NSE")
        )


        # ── Order book depth check (GAP 19) ──────────────────────────────────
        try:
            from market_intelligence_hub import check_order_book_depth
            _depth_ok, _depth_reason = check_order_book_depth(symbol, final_qty)
            if not _depth_ok:
                logger.warning("Low depth %s: %s — halving size", symbol, _depth_reason)
                final_qty = max(1, final_qty // 2)
        except Exception: pass

        # ── Macro event size override ─────────────────────────────────────────
        try:
            import json as _mj, os as _mo
            _mef = "macro_event_override.json"
            if _mo.path.exists(_mef):
                _mov = _mj.loads(open(_mef).read())
                if _mov.get("reduce_size"):
                    _mf = float(_mov.get("factor", 0.5))
                    final_qty = max(1, int(final_qty * _mf))
                    logger.debug("Macro override: size x%.1f", _mf)
        except Exception: pass

        # ── HIGH_NOISE regime: skip trade ──────────────────────────────────────
        try:
            _sig_regime = str(signal.get("regime","")).upper()
            if "HIGH_NOISE" in _sig_regime or "CHOPPY" in _sig_regime:
                if final_qty > 0:
                    final_qty = max(1, int(final_qty * 0.4))  # 40% size in noisy market
                    logger.debug("HIGH_NOISE regime → qty=%d for %s", final_qty, symbol)
        except Exception: pass

        # ── Global market filter modifier ─────────────────────────────────────
        _global_mod = 0.0
        if _GF_AVAILABLE and self._global_filter:
            try:
                _gblock, _greason = self._global_filter.should_block(signal_side)
                if _gblock:
                    self._log_no_signal(f"global_market_blocked_{_greason}",{"symbol":symbol})
                    return False
                _global_mod = (self._global_filter.get_size_multiplier(signal_side) - 1.0) * 2
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Strategy matrix modifier ───────────────────────────────────────────
        _matrix_mod = 0.0
        if _SM_AVAILABLE and self._strat_matrix:
            try:
                _tb  = self._strat_matrix.get_time_bucket()
                _dtp = "UNKNOWN"
                if self._day_classifier:
                    _lp = getattr(self._day_classifier, "_last_profile", None)
                    if _lp: _dtp = getattr(_lp, "day_type", "UNKNOWN")
                _mm = self._strat_matrix.get_condition_multiplier(
                    strategy=str(candidate.get("signal",{}).get("strategy","")).lower(),
                    day_type=_dtp, time_bucket=_tb,
                )
                _matrix_mod = (_mm - 1.0) * 2   # convert multiplier to score delta
                if _mm == 0.0:
                    self._log_no_signal("strategy_matrix_blocked",{"symbol":symbol})
                    return False
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Expiry day score boost ─────────────────────────────────────────────
        _expiry_boost = 0.0
        if _EXPIRY_SIG_AVAILABLE:
            try:
                _expiry_boost = get_expiry_score_boost()
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Event calendar size multiplier ─────────────────────────────────────
        _cal_size_mult = 1.0
        if _EC_AVAILABLE and self._event_cal:
            try:
                _cal_size_mult = self._event_cal.get_size_multiplier()
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── PCR + Delivery + OI Spurt score modifiers ─────────────────────────
        _pcr_mod        = 0.0
        _delivery_boost = 0.0
        _oi_spurt_boost = 0.0

        if _PCR_AVAILABLE:
            try:
                _opt_sig = candidate.get("option_signal") or {}
                if _opt_sig:
                    _pcr_result = _compute_pcr(_opt_sig)
                    _pcr_sig    = _pcr_result.get("pcr_signal", "NEUTRAL")
                    if _pcr_sig == "EXTREME_BEARISH" and signal_side == "BUY":
                        _pcr_mod = 1.0
                    elif _pcr_sig == "EXTREME_BULLISH" and signal_side == "SELL":
                        _pcr_mod = 1.0
                    elif _pcr_sig == "BEARISH" and signal_side == "BUY":
                        _pcr_mod = 0.3
                    elif _pcr_sig == "BULLISH" and signal_side == "SELL":
                        _pcr_mod = 0.3
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        if self._feeds:
            try:
                _d52 = getattr(self._feeds, "delivery_52wh", None)
                if _d52:
                    _delivery_boost = _d52.get_momentum_boost(symbol, signal_side)
                _ois = getattr(self._feeds, "oi_spurts", None)
                if _ois:
                    _oi_spurt_boost = _ois.get_spurt_boost(symbol, signal_side)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Sector correlation check ─────────────────────────────────
        # Block same-sector entries (e.g. HDFCBANK when ICICIBANK open)
        if self._sector_map:
            exec_sym = execution_plan["execution_symbol"]
            sym_sector = self._sector_map.get(symbol, "")  # underlying symbol
            if sym_sector:
                for pos in open_positions:
                    pos_sym = str(pos.get("symbol", "")).split("CE")[0].split("PE")[0].strip()
                    pos_sector = self._sector_map.get(pos_sym, "")
                    if pos_sector and pos_sector == sym_sector:
                        self._log_no_signal(
                            "sector_correlation_blocked",
                            {"symbol": symbol, "sector": sym_sector,
                             "blocking_pos": pos.get("symbol")},
                        )
                        return False

        # ── Bid-ask spread check ──────────────────────────────────────
        # Skip entry if spread is wider than MAX_SPREAD_PCT.
        # Entering on a wide spread means you're already losing before
        # the trade begins. Default 0.5% — configurable via MAX_SPREAD_PCT.
        if not self._spread_acceptable(
            symbol   = execution_plan["execution_symbol"],
            exchange = exchange,
        ):
            self._log_no_signal(
                "spread_too_wide",
                {"symbol": execution_plan["execution_symbol"], "exchange": exchange},
            )
            return False

        try:
            trade_id = self.trade_manager.open_trade(
                symbol            = execution_plan["execution_symbol"],
                side              = execution_plan["trade_side"],
                strategy          = str(signal.get("strategy", "AUTO")).upper(),
                entry_price       = execution_plan["entry_price"],
                stop_loss         = execution_plan["stop_loss"],
                target_price      = execution_plan["target_price"],
                score             = float(signal.get("score",      0)),
                regime            = str(signal.get("regime",   "UNKNOWN")),
                atr               = _safe_float(signal.get("atr"), execution_plan["entry_price"] * 0.05),
                confidence        = _safe_float(signal.get("confidence"), 0.5),
                correlation_group = execution_plan["correlation_group"],
                qty_override      = final_qty,    # honour PortfolioRiskManager approval
                exchange          = exchange,
                metadata={
                    "asset_type":        execution_plan["asset_type"],
                    "style":             style,
                    "source_symbol":     symbol,
                    "option_signal":     option_signal,
                    "option_summary":    candidate.get("option_summary"),
                    "ai_probability":    candidate.get("ai_probability"),
                    "rl_bias":           candidate.get("rl_bias"),
                    "option_bias":       candidate.get("option_bias"),
                    "ai_penalty":        candidate.get("ai_penalty"),
                    "final_rank_score":  candidate.get("final_rank_score"),
                    "requested_quantity": execution_plan["requested_quantity"],
                    "approved_quantity": final_qty,
                    "filter_meta":       signal.get("filter_meta"),
                    "signal_data":       signal,
                },
            )
            logger.info(
                "Trade executed | trade_id=%s symbol=%s exec_symbol=%s qty=%s style=%s exchange=%s",
                trade_id, symbol, execution_plan["execution_symbol"],
                final_qty, style, exchange,
            )

            # Successful execution — reset circuit breaker counter
            self._consecutive_exec_failures = 0
            # Record trade result in consecutive loss tracker
            try:
                if pnl is not None:
                    self.daily_loss_manager.record_trade_result(float(pnl))
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Store alpha factors in candidate for Telegram alert
            if '_alpha_factors' in dir():
                candidate['alpha_factors'] = _alpha_factors
            # Lock capital in allocator bucket
            if trade_id and trade_capital:
                self.capital_allocator.record_trade_start(style, trade_capital)
            # Record result for Kelly sizer (updated after trade closes)
            if self._kelly_sizer and trade_id:
                try:
                    _strat = str(signal.get("strategy", "unknown"))
                    _ep    = float(execution_plan.get("entry_price", 0))
                    _sl    = float(execution_plan.get("stop_loss", _ep * 0.9))
                    _risk  = abs(_ep - _sl)
                    # Will be updated with actual P&L in trade_manager callback
                    setattr(self, f"_kelly_pending_{trade_id}", (_strat, _risk))
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Register with SLHuntGuard for wick/hunt detection
            if trade_id and self._sl_guard and execution_plan:
                try:
                    _ep  = float(execution_plan.get("entry_price", 0))
                    _sl  = float(execution_plan.get("stop_loss", _ep * 0.95))
                    # soft stop = midpoint between entry and hard stop
                    _ssoft = (_ep + _sl) / 2 if signal_side == "BUY" else (_ep + _sl) / 2
                    self._sl_guard.register(
                        trade_id    = trade_id,
                        symbol      = execution_plan.get("execution_symbol", symbol),
                        side        = signal_side,
                        entry_price = _ep,
                        soft_stop   = _ssoft,
                        hard_stop   = _sl,
                    )
                    # Record VIX at entry for swing trend-change detection
                    if self._swing_protect and style == "swing":
                        _cur_vix = self._vix_cache_val if hasattr(self,"_vix_cache_val") else 0.0
                        self._swing_protect.record_vix_at_entry(trade_id, _cur_vix or 0.0)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Register with option intelligence for theta/gamma tracking
            if trade_id and self._option_intel and execution_plan.get("asset_type") == "OPTION":
                try:
                    _sym = execution_plan.get("execution_symbol", "")
                    _ot  = "CE" if _sym.endswith("CE") else "PE"
                    self._option_intel.register_position(
                        trade_id      = trade_id,
                        symbol        = _sym,
                        strike        = execution_plan.get("strike", 0),
                        option_type   = _ot,
                        underlying    = symbol,
                        entry_premium = execution_plan.get("entry_price", 0),
                        dte           = execution_plan.get("dte", 3),
                    )
                except Exception: pass
            # Subscribe new position to WebSocket stream
            if self.ws_engine and trade_id:
                try:
                    exec_sym = execution_plan.get('execution_symbol', symbol)
                    exchange = execution_plan.get('asset_type') == 'OPTION' and 'NFO' or 'NSE'
                    self.ws_engine.subscribe([exec_sym], exchange)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            # Record execution for strategy scanner win-rate tracking
            scanner = getattr(self, '_strategy_scanner', None)
            if scanner and signal.get('strategy'):
                pass   # Win/loss recorded on close in trade_manager
            # ── Broadcast signal to subscribers ─────────────────────────────
            if trade_id:
                try:
                    _sig_broadcast = dict(signal)
                    _sig_broadcast.update({
                        "price":     execution_plan.get("entry_price", 0),
                        "stop_loss": execution_plan.get("stop_loss", 0),
                        "target":    execution_plan.get("target_price", 0),
                        "regime":    signal.get("regime","?"),
                        "vix":       getattr(self,"_vix_cache_val",0),
                        "horizon":   style,
                    })
                    self._broadcast_signal_via_quality_gate(_sig_broadcast)
                except Exception as _be:
                    logger.debug("broadcast: %s", _be)
            return bool(trade_id)
        except Exception:
            logger.exception("Execution failed for symbol=%s", symbol)
            # Track consecutive failures and trip circuit breaker if threshold reached
            self._consecutive_exec_failures += 1
            if self._consecutive_exec_failures >= self._circuit_breaker_threshold:
                self._circuit_breaker_until = time.time() + self._circuit_breaker_pause_sec
                logger.error(
                    "Circuit breaker TRIPPED | consecutive_failures=%d threshold=%d "
                    "pause=%ds",
                    self._consecutive_exec_failures,
                    self._circuit_breaker_threshold,
                    self._circuit_breaker_pause_sec,
                )
            return False

    def _build_execution_plan(
        self, *, symbol: str, signal: Dict[str, Any],
        option_signal: Optional[Dict[str, Any]],
        trade_capital: float, style: str,
    ) -> Optional[Dict[str, Any]]:
        signal_side = str(signal.get("side", "")).upper()
        entry_price = _safe_float(signal.get("price"), 0.0)
        volatility  = _safe_float(signal.get("volatility"), 0.01)
        atr_est     = _safe_float(
            signal.get("atr"),
            max(entry_price * max(volatility, 0.01), entry_price * 0.005),
        )

        if symbol in SUPPORTED_OPTION_UNDERLYINGS:
            # IV rank check: block when options are expensive (GA-6: direct ref)
            try:
                _grm = self._gap_risk_manager
                if _grm and _grm.should_block_iv(symbol):
                    iv_status = _grm.iv_tracker.get_status().get(symbol, {})
                    iv_rank   = iv_status.get('iv_rank', 0)
                    logger.info(
                        'IV rank filter: blocking %s option BUY '
                        '(IV rank=%.0f%% > 70%% — options expensive)',
                        symbol, (iv_rank or 0) * 100,
                    )
                    return None
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # ── OptionChainEngine: CE/PE, real-time ATM, smart expiry ─────
            contract     = None
            option_entry = 0.0
            dte          = 1
            lot_size_opt = _LOT_SIZES.get(symbol, 75)
            qty          = lot_size_opt
            exec_symbol  = ""

            if self._option_chain_engine:
                try:
                    _chain_sig = option_signal.get("signal") if option_signal else None
                    contract   = self._option_chain_engine.select_option(
                        underlying          = symbol,
                        signal_side         = signal_side,
                        style               = style,
                        confidence          = _safe_float(signal.get("confidence"), 0.5),
                        trade_capital       = trade_capital,
                        df                  = df,
                        option_chain_signal = _chain_sig,
                        max_lots            = int(getattr(cfg, "MAX_LOTS", 10)),
                    )
                except Exception as _oce_exc:
                    logger.debug("OptionChainEngine error: %s", _oce_exc)

            if contract:
                option_entry = contract.premium
                dte          = contract.dte
                lot_size_opt = contract.lot_size
                qty          = contract.quantity
                exec_symbol  = contract.symbol
            else:
                # Fallback to legacy selector
                option_trade = self.option_selector.choose_option_from_signal(
                    signal=signal, trade_capital=trade_capital, index=symbol,
                )
                if not option_trade:
                    return None
                option_entry = float(option_trade.premium)
                dte          = self._estimate_dte(option_trade)
                lot_size_opt = option_trade.lot_size
                qty          = option_trade.quantity
                exec_symbol  = option_trade.symbol

            if option_entry <= 0 or not exec_symbol:
                return None

            stop_loss, target_price = self._dte_aware_stops(
                option_entry=option_entry,
                dte=dte,
                atr_est=atr_est,
                signal_side=signal_side,
            )

            sizing = self.position_sizer.size_position(
                capital          = trade_capital,
                entry_price      = option_entry,
                stop_loss        = stop_loss,
                confidence       = _safe_float(signal.get("confidence"), 0.5),
                score            = _safe_float(signal.get("score"),      0.0),
                regime           = str(signal.get("regime", "UNKNOWN")),
                strategy         = str(signal.get("strategy", "AUTO")),
                atr              = max(atr_est * 0.25, option_entry * 0.05),
                peak_equity      = self.total_capital,
                lot_size         = lot_size_opt,
                base_risk_pct    = float(getattr(cfg, "RISK_PER_TRADE_PCT", 0.01)),
            )

            requested_quantity = qty if sizing.quantity <= 0 else sizing.quantity

            return {
                "asset_type":        "OPTION",
                "execution_symbol":  exec_symbol,
                "trade_side":        "BUY",
                "entry_price":       option_entry,
                "stop_loss":         stop_loss,
                "target_price":      target_price,
                "requested_quantity": requested_quantity,
                "lot_size":          lot_size_opt,
                "correlation_group": symbol,
                "dte":               dte,
                "strike":            contract.strike if contract else 0,
            }

        # Cash equity fallback
        if not bool(getattr(cfg, "ENABLE_CASH_EQUITY_EXECUTION", True)):
            return None
        if entry_price <= 0:
            return None

        stop_buffer = max(atr_est, entry_price * 0.01)
        # Use smart stop (below swing low) when data available
        if self._sl_guard and df is not None and len(df) >= 12:
            try:
                _ss = _smart_stop(
                    df          = df,
                    side        = signal_side,
                    entry_price = entry_price,
                    atr         = atr_est,
                    style       = style,
                )
                stop_loss    = _ss["hard_stop"]
                target_price = round(entry_price + abs(entry_price - stop_loss) * 1.5, 2)
            except Exception:
                if signal_side == "BUY":
                    stop_loss    = round(entry_price - stop_buffer, 2)
                    target_price = round(entry_price + stop_buffer * 1.5, 2)
                else:
                    stop_loss    = round(entry_price + stop_buffer, 2)
                    target_price = round(entry_price - stop_buffer * 1.5, 2)
        elif signal_side == "BUY":
            stop_loss    = round(entry_price - stop_buffer, 2)
            target_price = round(entry_price + stop_buffer * 1.5, 2)
        else:
            stop_loss    = round(entry_price + stop_buffer, 2)
            target_price = round(entry_price - stop_buffer * 1.5, 2)

        sizing = self.position_sizer.size_position(
            capital          = trade_capital,
            entry_price      = entry_price,
            stop_loss        = stop_loss,
            confidence       = _safe_float(signal.get("confidence"), 0.5),
            score            = _safe_float(signal.get("score"),      0.0),
            regime           = str(signal.get("regime", "UNKNOWN")),
            strategy         = str(signal.get("strategy", "AUTO")),
            atr              = atr_est,
            peak_equity      = self.total_capital,
            lot_size         = 1,
            base_risk_pct    = float(getattr(cfg, "RISK_PER_TRADE_PCT", 0.01)),
        )

        return {
            "asset_type":        "CASH",
            "execution_symbol":  symbol,
            "trade_side":        signal_side,
            "entry_price":       entry_price,
            "stop_loss":         stop_loss,
            "target_price":      target_price,
            "requested_quantity": max(1, sizing.quantity),
            "lot_size":          1,
            "correlation_group": symbol,
        }

    def _decide_style(
        self, signal: Dict[str, Any], option_signal: Optional[Dict[str, Any]]
    ) -> str:
        """
        Determine trade style using multi-factor logic:
          SWING    — score≥SWING_MIN_SCORE, DTE≥5, TREND/BREAKOUT regime,
                     high confidence, not expiry week
          SCALPING — score<5.0 OR RANGE regime, short-lived signal
          INTRADAY — default: same-day close, medium conviction
        """
        if option_signal:
            # Option signal explicitly sets style
            explicit = str(option_signal.get("style", "")).lower().strip()
            if explicit in ("swing", "scalping", "intraday"): return explicit

        score      = float(signal.get("score",       0.0) or 0.0)
        confidence = float(signal.get("confidence",  0.0) or 0.0)
        regime     = str(signal.get("regime", "")).upper()
        strategy   = str(signal.get("strategy", "")).lower()

        swing_min_score = float(getattr(cfg, "SWING_MIN_SCORE",       7.0))
        swing_min_conf  = float(getattr(cfg, "SWING_MIN_CONFIDENCE",  0.75))
        swing_min_dte   = int(  getattr(cfg, "SWING_MIN_DTE",         5))
        swing_enabled   = bool( getattr(cfg, "SWING_MODE_ENABLED",    True))

        # SCALPING: fast strategies or low-conviction signals
        if strategy in ("scalping", "momentum_5m", "quick_trade"):
            return "scalping"
        if regime == "RANGE" and score < 5.0:
            return "scalping"

        # SWING: strong trending signals with high DTE requirement
        if (swing_enabled
                and score >= swing_min_score
                and confidence >= swing_min_conf
                and regime in ("TREND", "EARLY_TREND")
                and strategy not in ("orb", "vwap_reversion", "scalping")):
            # Verify we can find a swing option (DTE≥5)
            if option_signal:
                dte = int(option_signal.get("dte", 0))
                if dte >= swing_min_dte:
                    return "swing"
            else:
                return "swing"

        # DEFAULT: intraday (same-day close)
        return "intraday"

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------



    # ------------------------------------------------------------------
    # DTE-aware option stops
    # ------------------------------------------------------------------
    def _estimate_dte(self, option_trade) -> int:
        """
        Estimate days-to-expiry from the option_trade expiry field.
        Returns 1 if expiry is today or unknown.
        """
        try:
            import pandas as pd
            from datetime import date
            expiry = getattr(option_trade, "expiry", None)
            if expiry is None:
                return 1
            if hasattr(expiry, "date"):
                exp_date = expiry.date()
            else:
                exp_date = pd.Timestamp(str(expiry)).date()
            dte = (exp_date - date.today()).days
            return max(0, int(dte))
        except Exception:
            return 1

    def _dte_aware_stops(
        self,
        option_entry: float,
        dte:          int,
        atr_est:      float,
        signal_side:  str,
    ):
        """
        Set stop-loss and target based on days-to-expiry.

        Logic
        -----
        0-DTE  : tight stop (10% of premium) — theta burns every minute
        1-DTE  : normal (15%) — still fast decay
        2-DTE  : slightly wider (18%)
        3+ DTE : full width (20%) — ATR-based target preferred

        Target is set at 2× the stop distance (R:R ≥ 1:2 on every trade).
        ATR-based target used when atr_est is available (preferred).
        """
        if dte == 0:
            stop_pct   = float(getattr(cfg, "OPTION_STOP_0DTE",  0.10))
        elif dte == 1:
            stop_pct   = float(getattr(cfg, "OPTION_STOP_1DTE",  0.15))
        elif dte == 2:
            stop_pct   = float(getattr(cfg, "OPTION_STOP_2DTE",  0.18))
        else:
            stop_pct   = float(getattr(cfg, "OPTION_STOP_MULT",  0.20))

        stop_loss    = round(option_entry * (1.0 - stop_pct), 2)
        stop_loss    = max(stop_loss, 0.05)   # never below ₹0.05

        # Target: prefer 2× risk; if ATR available use ATR-based estimate
        risk_per_unit = option_entry - stop_loss
        if atr_est > 0 and option_entry > 0:
            # Approximate delta: ATM options ≈ 0.45 delta
            delta_est  = 0.45
            atr_target = atr_est * 1.5 * delta_est   # 1.5-ATR move on underlying
            target_move = max(risk_per_unit * 2.0, atr_target)
        else:
            target_move = risk_per_unit * 2.0

        target_price = round(option_entry + target_move, 2)

        logger.debug(
            "DTE-aware stops | dte=%d premium=%.2f stop_pct=%.0f%% "
            "stop=%.2f target=%.2f",
            dte, option_entry, stop_pct * 100, stop_loss, target_price,
        )
        return stop_loss, target_price

    # ------------------------------------------------------------------
    # India VIX live filter
    # ------------------------------------------------------------------
    def get_ws_ltp(self, symbol: str) -> Optional[float]:
        """Get LTP from WebSocket cache (real-time) or fall back to REST."""
        if self.ws_engine:
            ltp = self.ws_engine.get_ltp(symbol)
            if ltp and ltp > 0:
                return ltp
        # Fallback: REST API
        try:
            broker = self.broker_manager.get_execution_broker()
            if broker:
                return broker.get_ltp(symbol)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        return None

    def _get_india_vix(self) -> float:
        """India VIX — NSE allIndices primary. Never returns 0."""
        import time as _tvix
        _now = _tvix.time()
        if getattr(self,"_vix_cache_val",0) > 0 and (_now-getattr(self,"_vix_cache_ts",0)) < 900:
            return self._vix_cache_val
        try:
            _v = self._fetch_nse_vix()
            if _v and float(_v) > 0:
                self._vix_cache_val = float(_v)
                self._vix_cache_ts  = _now
                return float(_v)
        except Exception: pass
        try:
            import yf_compat as _yfc
            _df = _yfc.download("^INDIAVIX")
            if _df is not None and len(_df) > 0:
                _v2 = float(_df["Close"].iloc[-1])
                if _v2 > 0:
                    self._vix_cache_val = _v2
                    self._vix_cache_ts  = _now
                    return _v2
        except Exception: pass
        self._vix_cache_val = 15.0
        self._vix_cache_ts  = _now
        return 15.0

    def _fetch_nse_vix(self) -> float:
        try:
            import requests as _rq
            _s2 = _rq.Session()
            _s2.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            _s2.get("https://www.nseindia.com/", timeout=4)
            _r2 = _s2.get("https://www.nseindia.com/api/allIndices", timeout=7)
            for _ix in _r2.json().get("data",[]):
                if "INDIA VIX" in str(_ix.get("index","")).upper():
                    _vv = float(_ix.get("last",0) or 0)
                    if _vv > 0: return _vv
        except Exception: pass
        return self._vix_cache_val or 15.0

    def _is_vix_too_high(self) -> bool:
        """
        Returns True when India VIX > VIX_MAX_FOR_BUYING (default 22).
        When True: option BUYING is blocked.
        Stocks (equity), futures, option SELLING still allowed.
        Set VIX_MAX_FOR_BUYING=0 to disable.
        """
        vix_max = float(getattr(cfg, "VIX_MAX_FOR_BUYING", 22.0))
        if vix_max <= 0:
            return False
        vix = self._get_india_vix()
        if vix <= 0:
            return False   # unknown — don't block
        if vix > vix_max:
            logger.warning("India VIX %.2f > %.2f — blocking new option entries", vix, vix_max)
            return True
        return False



    def _update_capital_with_recycler(self) -> None:
        """Compound capital from closed trades (capital recycler)."""
        try:
            from capital_recycler import get_recycler
            recycler = get_recycler()
            new_cap = recycler.get_current_capital()
            if new_cap > 0 and abs(new_cap - self.total_capital) > 100:
                logger.info("Capital recycler: %.2f → %.2f",
                             self.total_capital, new_cap)
                self.total_capital = new_cap
                self._update_capital_tier()
        except Exception as e:
            logger.debug("recycler: %s", e)


    def _refresh_angel_balance(self) -> None:
        """Refresh live balance from Angel One every 5 minutes."""
        try:
            now = __import__('time').time()
            _last = getattr(self, '_last_balance_refresh', 0)
            if now - _last < 300:  # 5 min cooldown
                return
            self._last_balance_refresh = now
            # Try direct Angel balance
            _bal = 0.0
            try:
                if hasattr(self, 'broker_manager') and self.broker_manager:
                    _bm = self.broker_manager
                    if hasattr(_bm, 'angel') and _bm.angel:
                        _bal = float(_bm.angel.get_balance(force_real=True) or 0)
            except Exception: pass
            if _bal > 0 and abs(_bal - self.total_capital) > 100:
                logger.info('Balance refresh: ₹%.0f → ₹%.0f',
                            self.total_capital, _bal)
                self.total_capital = _bal
                self._update_capital_tier()
                # Update config so position sizing uses real balance
                try:
                    import config as _cfg
                    _cfg.REAL_CAPITAL = _bal
                    _cfg.CAPITAL      = _bal
                    import os as _os
                    _os.environ['REAL_CAPITAL'] = str(int(_bal))
                except Exception: pass
            elif _bal > 0:
                logger.debug('Balance unchanged: ₹%.0f', _bal)
        except Exception as e:
            logger.debug('balance_refresh: %s', e)

    def _update_capital_tier(self) -> None:
        """
        Read current account balance from Angel One (live) or config (paper).
        
        Priority:
        1. Live mode: fetch actual balance from Angel One rmsLimit API
        2. Paper mode: use PAPER_CAPITAL from .env
        3. Fallback: use CAPITAL from .env
        
        Updates all components (risk_manager, position_sizer, allocator).
        Runs every trading cycle so capital compounding is always current.
        """
        if not self.capital_compounder:
            return
        try:
            import config as _cfg
            paper_mode = bool(getattr(_cfg, "PAPER_TRADING", True))
            balance    = self.total_capital

            if not paper_mode:
                # LIVE MODE — fetch real balance from Angel One
                try:
                    broker = self.broker_manager.get_execution_broker()
                    if broker and hasattr(broker, "get_balance"):
                        live_bal = broker.get_balance()
                        if live_bal and float(live_bal) > 1000:
                            balance = float(live_bal)
                            self.total_capital          = balance
                            self.trade_manager.capital   = balance
                            self.risk_manager.capital    = balance
                            logger.info("Live balance from Angel One: ₹%.0f", balance)
                        else:
                            logger.warning(
                                "Angel One returned balance ₹%.0f — using config value ₹%.0f",
                                live_bal or 0, self.total_capital
                            )
                except Exception as exc:
                    logger.warning("Balance fetch failed: %s — using config capital", exc)
            else:
                # PAPER MODE — use PAPER_CAPITAL from .env
                paper_cap = float(getattr(_cfg, "PAPER_CAPITAL",
                                          getattr(_cfg, "CAPITAL", 100000)))
                if paper_cap > 0 and abs(paper_cap - balance) > 100:
                    balance                     = paper_cap
                    self.total_capital          = paper_cap
                    self.trade_manager.capital   = paper_cap
                    self.risk_manager.capital    = paper_cap
                    logger.debug("Paper capital: ₹%.0f", paper_cap)

            # Update peak equity
            if balance > self._peak_equity:
                self._peak_equity = balance

            self.capital_compounder.update_equity(balance)
            params = self.capital_compounder.get_current_params(balance)

            # Apply tier params to position sizer and risk manager
            self.position_sizer.max_lots      = params.max_lots
            self.position_sizer.default_risk_pct = params.risk_pct
            self.risk_manager.max_open_positions = params.max_positions

            # Apply drawdown confidence penalty to AI filter
            penalty = self.capital_compounder.get_drawdown_confidence_penalty()
            if penalty > 0:
                self.ai_filter.threshold = max(1.5, self.ai_filter.threshold + penalty)
            else:
                # Restore default threshold
                try:
                    import config as _cfg
                    self.ai_filter.threshold = float(getattr(_cfg, "AI_FILTER_THRESHOLD", 2.0))
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Milestone check
            milestone = self.capital_compounder.compounding_milestone_check(balance)
            if milestone:
                logger.info(
                    "🎉 CAPITAL MILESTONE REACHED: %s | ₹%.0f",
                    milestone["milestone"], balance
                )
                # Will be picked up by main_autonomous for Telegram alert

        except Exception as exc:
            logger.debug("_update_capital_tier failed: %s", exc)


    def _monitor_open_positions_rest(self) -> None:
        """
        GA-4: REST polling fallback for trailing stops when WebSocket is down.
        Called every 5 seconds from _run_cycle when ws_engine is disconnected.
        Prevents stops being missed during WebSocket reconnection.
        """
        if not self.trade_manager.open_trades:
            return
        try:
            broker = self.broker_manager.get_execution_broker()
            if not broker:
                return

            bar_index = int(time.time() // 300)

            for trade_id, trade in list(self.trade_manager.open_trades.items()):
                if trade.status != "OPEN":
                    continue
                try:
                    ltp = broker.get_ltp(trade.symbol)
                    if not ltp or ltp <= 0:
                        continue

                    pos = self.trailing_manager.positions.get(trade_id) if hasattr(self, 'trailing_manager') else None
                    if pos is None:
                        # Soft stop check without trailing manager
                        if trade.stop_loss and trade.side == "BUY" and ltp <= float(trade.stop_loss):
                            logger.warning(
                                "REST stop hit | trade_id=%s ltp=%.2f stop=%.2f",
                                trade_id, ltp, trade.stop_loss,
                            )
                            self.trade_manager._close_trade_internal(
                                trade_id=trade_id, exit_price=ltp,
                                exit_reason="rest_poll_stop"
                            )
                        elif trade.stop_loss and trade.side == "SELL" and ltp >= float(trade.stop_loss):
                            self.trade_manager._close_trade_internal(
                                trade_id=trade_id, exit_price=ltp,
                                exit_reason="rest_poll_stop"
                            )
                        continue

                    exit_now, exit_price, exit_qty, reason = self.trailing_manager.check_exit(
                        trade_id      = trade_id,
                        current_price = ltp,
                        side          = trade.side,
                        current_atr   = float(trade.entry_atr or ltp * 0.005),
                        remaining_qty = trade.qty,
                        bar_index     = bar_index,
                    )
                    if exit_now:
                        logger.info(
                            "REST poll stop hit | trade_id=%s ltp=%.2f reason=%s",
                            trade_id, ltp, reason,
                        )
                        self.trade_manager._cancel_broker_sl_order(trade)
                        self.trade_manager._close_trade_internal(
                            trade_id=trade_id, exit_price=ltp,
                            exit_reason=f"rest_poll_{reason}"
                        )
                    else:
                        # GA-3: update broker SL if trail improved
                        new_stop = pos.get("stop_price") if pos else None
                        if (new_stop and trade.sl_order_id
                                and abs(new_stop - float(trade.stop_loss or 0)) > 0.50):
                            self.trade_manager._update_broker_sl_order(trade, new_stop)
                            trade.stop_loss = new_stop

                except Exception as exc:
                    logger.debug("REST monitor error trade_id=%s: %s", trade_id, exc)

        except Exception as exc:
            logger.debug("_monitor_open_positions_rest outer error: %s", exc)

    _last_rest_monitor_ts: float = 0.0

    def _spread_acceptable(self, symbol: str, exchange: str = "NFO") -> bool:
        """
        Returns True when the bid-ask spread is within acceptable limits.

        Configured via MAX_SPREAD_PCT in .env (default 0.005 = 0.5%).
        Returns True when:
          - broker doesn't support bid-ask (unknown spread → don't block)
          - MAX_SPREAD_PCT = 0 (disabled)
          - spread is within threshold

        Returns False when spread is too wide → caller skips entry.
        """
        max_spread = float(getattr(cfg, "MAX_SPREAD_PCT", 0.005))
        if max_spread <= 0:
            return True   # disabled

        try:
            broker = self.broker_manager.get_execution_broker()
            if broker is None:
                return True   # no broker — don't block
            if not hasattr(broker, "check_spread"):
                return True   # broker doesn't support spread check

            spread_ok, spread_pct, bid, ask = broker.check_spread(
                symbol         = symbol,
                exchange       = exchange,
                max_spread_pct = max_spread,
            )
            if not spread_ok:
                logger.warning(
                    "Entry blocked: spread %.3f%% > %.3f%% | %s bid=%.2f ask=%.2f",
                    spread_pct * 100, max_spread * 100, symbol, bid, ask,
                )
            return spread_ok
        except Exception as exc:
            logger.debug("_spread_acceptable check failed: %s", exc)
            return True   # on error, don't block trading


    def _select_strategy_for_capital(self, symbol: str) -> str:
        """
        Dynamically select the best strategy based on:
        1. Current live broker balance (real capital available)
        2. Capital compounder tier (what strategies are unlocked at this level)
        3. ParamBridge affinity scores (what works best for this symbol)
        4. Time-of-day zone (what strategies suit the current session window)
        5. Current regime (trend/range/breakout/volatile)

        Capital unlock thresholds:
          < ₹1.5L  → option buying only (trend/breakout/scalping)
          ₹1.5-5L  → + spread strategies (bull_put_spread unlocked)
          ₹5-10L   → + iron condor
          ₹10L+    → + theta straddle / all strategies
        """
        try:
            from time_regime import get_time_zone, get_strategy_weight, TimeZone

            capital = self.total_capital   # synced from broker in _update_capital_tier

            # ── Capital tier: unlock strategies ──────────────────────────────
            buying_only   = capital <  150_000
            spreads_ok    = capital >= 150_000
            condors_ok    = capital >= 500_000
            theta_ok      = capital >= 1_000_000

            # ── ParamBridge: get affinity ranking for this symbol ─────────────
            if self._param_bridge:
                ranking = self._param_bridge.get_strategy_ranking(symbol)
                if ranking:
                    # Filter by capital unlock
                    allowed = self._capital_allowed_strategies(
                        buying_only, spreads_ok, condors_ok, theta_ok
                    )
                    for strat, score in ranking:
                        if strat in allowed and score > 0:
                            logger.debug(
                                "Dynamic strategy: %s → %s (affinity=%.2f, capital=₹%.0f)",
                                symbol, strat, score, capital,
                            )
                            return strat

            # ── Time-zone + regime fallback ───────────────────────────────────
            zone = get_time_zone()
            if zone == TimeZone.OPENING_RANGE:
                return "orb"
            if zone == TimeZone.VWAP_ZONE:
                return "vwap_reversion"

            # Default by capital
            if condors_ok:
                return "breakout"   # highest Sharpe for index options
            return "trend"          # safe default for smaller capital

        except Exception:
            return "trend"

    def _capital_allowed_strategies(
        self, buying_only, spreads_ok, condors_ok, theta_ok
    ) -> set:
        """Return set of strategy names allowed at current capital tier."""
        allowed = {"trend", "mean_reversion", "breakout", "scalping",
                   "ma_cross", "orb", "vwap_reversion", "supertrend_mtf"}
        if not spreads_ok:
            # Buying only — no spread strategies (those need margin)
            allowed -= {"bull_put_spread", "iron_condor", "theta_straddle"}
        if not condors_ok:
            allowed -= {"iron_condor", "theta_straddle"}
        if not theta_ok:
            allowed -= {"theta_straddle"}
        return allowed

    def _in_eod_window(self) -> bool:
        """
        Returns True if the current time is inside the EOD exit window.

        The window start is configured via EOD_EXIT_BUFFER_MIN in config.py
        (default: 15 minutes before market close = 15:15 for a 15:30 close).
        New entries are blocked during this window so positions are only being
        closed, not opened.
        """
        try:
            from datetime import datetime as _dt
            import config as _cfg
            now          = _dt.now().time()
            mend_str     = getattr(_cfg, "MARKET_END", "15:30")
            buf_min      = int(getattr(_cfg, "EOD_EXIT_BUFFER_MIN", 15))
            h, m         = mend_str.split(":")
            market_end_min = int(h) * 60 + int(m)
            eod_min      = market_end_min - buf_min
            from datetime import time as _dtime
            eod_time     = _dtime(eod_min // 60, eod_min % 60)
            market_end   = _dtime(int(h), int(m))
            return eod_time <= now <= market_end
        except Exception:
            return False

    def _log_no_signal(
        self, reason: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        data = {"timestamp": time.time(), "reason": reason, "extra": extra or {}}
        try:
            with open(NO_SIGNAL_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, default=str) + "\n")
        except Exception:
            logger.exception("Failed to write no-signal log")
        logger.info("No Signal: %s", reason)
