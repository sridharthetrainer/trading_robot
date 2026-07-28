"""
main_autonomous.py

Autonomous trading system orchestrator.

Fixes applied
-------------
1. Weekend / non-trading-day guard
   _market_window() previously checked only clock time. On a Saturday
   between 09:20-15:15 the system entered live-trading mode. Now it
   checks weekday (Mon-Fri only) and an optional exchange-holiday list.

2. Daily state reset on new trading day
   DailyLossLimitManager.reset_day() existed but was never called.
   _on_new_trading_day() is now triggered whenever the calendar date
   changes between loop iterations, resetting loss limits and
   day-scoped counters on the live engine.

3. Opening-window no-trade guard (09:15-09:20)
   Config defines AVOID_OPENING_WINDOW + OPENING_NO_TRADE_END but the
   orchestrator never used them.  _market_window() now returns a new
   key "in_opening_window" and the main loop delays new entries
   (heartbeat still runs so health state stays current).

4. Outer restart loop
   A fatal exception previously caused the process to exit permanently.
   The main loop is now wrapped in an outer retry loop with exponential
   back-off (capped at 5 minutes).  After MAX_RESTART_ATTEMPTS
   consecutive failures the process exits with code 1 so a supervisor
   (systemd / supervisord) can alert.

5. Removed double bootstrap
   _startup() previously called bootstrap_or_learn() inside
   _restore_from_backup() AND again in the else-branch.  If restore
   succeeded, bootstrap_or_learn() ran twice.  Now _restore_from_backup
   does one call; _startup() only calls bootstrap when restore returns
   False.

6. Learning guard before market open
   After-hours learning is skipped if market open is < 30 minutes away,
   preventing a slow backtest run from blocking the session start.

7. Config session window is used
   get_session_window() and AVOID_OPENING_WINDOW from config.py are
   now actually consumed.  The duplicated time-parsing logic in
   _market_window() is removed; it delegates to config.get_session_window().
"""
from __future__ import annotations

# CRITICAL: Load .env FIRST before any code tries to read credentials
try:
    from dotenv import load_dotenv
    load_dotenv('.env')  # Load from current directory
except ImportError:
    pass  # python-dotenv not installed, but os.getenv() still works for env vars


# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)

import concurrent.futures as _cf
# Shared thread pool — max 8 workers prevents thread exhaustion
_THREAD_POOL = _cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="bot_worker")
try:
    from market_regime import get_regime_engine as _get_regime_eng
    _REGIME_ENG_AVAIL = True
except ImportError:
    _REGIME_ENG_AVAIL = False
try:
    from oi_tracker import get_oi_tracker as _get_oi_tracker
    _OI_TRACKER_AVAIL = True
except ImportError:
    _OI_TRACKER_AVAIL = False
try:
    from gdrive_sync import get_drive_sync as _get_drive_sync
    _DRIVE_SYNC_AVAIL = True
except ImportError:
    _DRIVE_SYNC_AVAIL = False
try:
    from idle_engine import get_idle_engine as _get_idle
    _IDLE_AVAIL = True
except ImportError:
    _IDLE_AVAIL = False
try:
    from connection_monitor import get_monitor as _get_conn_monitor
    _CONN_MON = True
except ImportError:
    _CONN_MON = False
try:
    from connection_monitor import get_monitor as _get_conn_monitor
    _CONN_MON_AVAIL = True
except ImportError:
    _CONN_MON_AVAIL = False
try:
    from connection_monitor import get_monitor as _get_monitor
    _MONITOR_AVAIL = True
except ImportError:
    _MONITOR_AVAIL = False
try:
    from connection_monitor import get_monitor as _get_monitor
    _CONN_MON = True
except ImportError:
    _CONN_MON = False
try:
    from self_healing import SelfHealingSystem as _SHS
    _HEALING_AVAIL = True
except ImportError:
    _HEALING_AVAIL = False
try:
    from strategy_evolution import get_evolution as _get_evolution
    _EVO_AVAIL = True
except ImportError:
    _EVO_AVAIL = False
try:
    from system_state import get_state as _get_sys_state
    _SYSSTATE = True
except ImportError:
    _SYSSTATE = False
try:
    from telegram_commands import TelegramCommandHandler as _TGCmd
    _TGCMD_AVAIL = True
except ImportError:
    _TGCMD_AVAIL = False
try:
    from off_hours_engine import OffHoursEngine as _OffHours, is_market_holiday, fetch_nse_holidays
    _OFFHOURS_AVAIL = True
except ImportError:
    _OFFHOURS_AVAIL = False
try:
    from data_download_tracker import get_tracker as _get_dl_tracker, record as _dl_record
    _DLTRACK_AVAIL = True
except ImportError:
    _DLTRACK_AVAIL = False

import json
import logging
# Silence yfinance logger — Yahoo API broken, using yf_compat instead
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)
import signal
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Set

import config as cfg
from alerts import AlertManager
try:
    from capital_compounder import CapitalCompounder
    _CC_AVAILABLE = True
except ImportError:
    _CC_AVAILABLE = False
try:
    from gap_risk_manager import GapRiskManager
    _GRM_AVAILABLE = True
except ImportError:
    _GRM_AVAILABLE = False
from dashboard import TradingDashboard
from live_signal_engine import LiveSignalEngine
from self_learning import SelfLearningController

# =============================================================================
# LOGGING
# =============================================================================

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"

logging.basicConfig(
    level=getattr(logging, str(getattr(cfg, "LOG_LEVEL", "INFO")).upper(), logging.INFO),
    format=LOG_FORMAT,
)
from logging_security import install_secret_redaction
install_secret_redaction()

logger = logging.getLogger("main_autonomous")

# =============================================================================
# MODULE-LEVEL CONSTANTS
# =============================================================================

BACKUP_DIR     = "backup"
RUN_STATE_FILE = getattr(cfg, "RUN_SYSTEM_STATE_FILE", "run_system_state.json")
HEALTH_FILE       = getattr(cfg, "HEALTH_SNAPSHOT_FILE",   "health_snapshot.json")
LIVE_STATUS_FILE  = getattr(cfg, "LIVE_STATUS_FILE",        "live_status.json")
MAIN_LIVE_LOG  = getattr(cfg, "MAIN_LIVE_LOG_FILE",    "main_live.log")

# Outer restart loop settings
MAX_RESTART_ATTEMPTS    = 10       # give up after this many consecutive crashes
RESTART_BACKOFF_BASE    = 10       # seconds — doubles each attempt
MIN_LIVE_CAPITAL = float(__import__("os").getenv("MIN_LIVE_CAPITAL","500"))
RESTART_BACKOFF_MAX     = 300      # cap at 5 minutes

# How close to market open we refuse to start a learning cycle (seconds)
LEARNING_BLACKOUT_BEFORE_OPEN = 30 * 60   # 30 minutes

# NSE exchange holidays for the current year — add dates as needed.
# Format: date(YYYY, MM, DD)
# These are checked in addition to weekends.
# ── NSE holidays are now loaded dynamically from NSEMaster ──────────────────
# This fallback set is used ONLY if NSEMaster cannot be imported.
_NSE_HOLIDAYS_FALLBACK: Set[date] = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1),
    date(2025, 8, 15), date(2025, 8, 27), date(2025, 10, 2),
    date(2025, 10, 24), date(2025, 11, 5), date(2025, 11, 14),
    date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 3, 3),  date(2026, 3, 26),
    date(2026, 3, 31), date(2026, 4, 3),  date(2026, 4, 14),
    date(2026, 5, 1),  date(2026, 5, 28), date(2026, 6, 26),
    date(2026, 9, 14), date(2026, 10, 2), date(2026, 10, 20),
    date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25),
}

try:
    from auto_mode import get_auto_mode_selector as _get_auto_mode
    _AUTOMODE_AVAILABLE = True
except ImportError:
    _AUTOMODE_AVAILABLE = False
try:
    from sl_hunt_guard import get_swing_protection as _get_swing_protect_ma
    _SLHG_MA = True
except ImportError:
    _SLHG_MA = False
try:
    from nse_master import get_nse_master as _get_nse_master_ma
    _NSE_MASTER_MA = True
except ImportError:
    _NSE_MASTER_MA = False
try:
    from market_data_feeds import get_market_feeds as _get_market_feeds
    _FEEDS_MA = True
except ImportError:
    _FEEDS_MA = False

# Keep NSE_HOLIDAYS as a reference — used by _is_trading_day below
NSE_HOLIDAYS = _NSE_HOLIDAYS_FALLBACK

# High-impact day guard (RBI policy, budget, macro events).
# Populate in config.py: HIGH_IMPACT_DATES = {date(2026,6,6), date(2026,7,3), ...}
try:
    _cfg_hid = __import__("config")
    HIGH_IMPACT_DATES          = set(getattr(_cfg_hid, "HIGH_IMPACT_DATES",          set()))
    HIGH_IMPACT_CONFIDENCE_MIN = float(getattr(_cfg_hid, "HIGH_IMPACT_CONFIDENCE_MIN", 0.80))
except Exception:
    HIGH_IMPACT_DATES          = set()
    HIGH_IMPACT_CONFIDENCE_MIN = 0.80


# =============================================================================
# HELPERS
# =============================================================================

def _append_jsonl(path: str, payload: Dict[str, Any]) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        logger.exception("Failed to append JSONL to %s", path)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _is_trading_day(d: Optional[date] = None) -> bool:
    """
    Return True if d is a valid NSE trading day.
    Uses NSEMaster (live data) when available, hardcoded fallback otherwise.
    """
    if d is None:
        d = date.today()
    if d.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    # Try NSEMaster first (dynamic holidays from NSE API)
    if _NSE_MASTER_MA:
        try:
            return not _get_nse_master_ma().is_trading_holiday(d)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
    # Fallback: hardcoded list
    return d not in NSE_HOLIDAYS


def _minutes_until_market_open() -> int:
    """
    Return the number of minutes until the next market open.
    Returns 0 if the market is currently open.
    """
    try:
        session = cfg.get_session_window()
        now = datetime.now()
        close_dt = now.replace(
            hour=session.market_end.hour, minute=session.market_end.minute,
            second=0, microsecond=0,
        )
        if _is_trading_day(now.date()) and now <= close_dt:
            open_dt = now.replace(
                hour=session.market_start.hour,
                minute=session.market_start.minute,
                second=0,
                microsecond=0,
            )
            if now >= open_dt:
                return 0
        else:
            next_day = now.date() + timedelta(days=1)
            while not _is_trading_day(next_day):
                next_day += timedelta(days=1)
            open_dt = datetime.combine(next_day, session.market_start)
        delta = (open_dt - now).total_seconds() / 60.0
        return max(0, int(delta))
    except Exception:
        return 999   # assume far away if we can't compute


# =============================================================================
# RUNTIME STATE
# =============================================================================

@dataclass
class RuntimeState:
    started_at:               str             = field(default_factory=lambda: datetime.now().isoformat())
    updated_at:               str             = field(default_factory=lambda: datetime.now().isoformat())
    running:                  bool            = False
    mode:                     str             = "PAPER"
    market_phase:             str             = "BOOT"
    current_strategy:         str             = str(getattr(cfg, "DEFAULT_STRATEGY", "trend"))
    current_regime:           str             = "unknown"
    current_regime_confidence: float          = 0.0
    current_regime_reason:    str             = ""
    last_live_cycle_at:       Optional[str]   = None
    last_learning_cycle_at:   Optional[str]   = None
    last_dashboard_at:        Optional[str]   = None
    last_backup_restore_at:   Optional[str]   = None
    last_backup_file:         Optional[str]   = None
    last_new_day_reset_at:    Optional[str]   = None
    heartbeat_count:          int             = 0
    last_heartbeat_at:        Optional[str]   = None
    open_trade_count:         int             = 0
    closed_trade_count:       int             = 0
    daily_realized_pnl:       float           = 0.0
    trading_locked:           bool            = False
    lock_reason:              str             = ""
    restart_count:            int             = 0
    diagnostics:              Dict[str, Any]  = field(default_factory=dict)


# =============================================================================
# SIMPLE EVENT DATABASE
# =============================================================================

class TradeDatabase:
    def __init__(self, db_path: str = "trades.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.cursor().execute(
            """
            CREATE TABLE IF NOT EXISTS bot_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                level      TEXT,
                event_type TEXT,
                message    TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def log_event(self, level: str, event_type: str, message: str) -> None:
        try:
            conn = self._connect()
            conn.cursor().execute(
                "INSERT INTO bot_events VALUES (NULL, ?, ?, ?, ?)",
                (datetime.now().isoformat(), level, event_type, message),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("TradeDatabase.log_event failed")


# =============================================================================
# AUTONOMOUS SYSTEM
# =============================================================================

try:
    from trading_agent import get_agent as _get_agent
    _AGENT_AVAILABLE = True
except ImportError:
    _AGENT_AVAILABLE = False

try:
    from autonomous_backtest import get_backtest as _get_backtest
    _BT_AVAILABLE = True
except ImportError:
    _BT_AVAILABLE = False

try:
    from dual_mode_engine import get_dual_engine as _get_dual_engine
    _DUAL_MODE_AVAILABLE = True
except ImportError:
    _DUAL_MODE_AVAILABLE = False

try:
    from overnight_protection import get_overnight_protection as _get_overnight_prot
    _OVERNIGHT_AVAILABLE = True
except ImportError:
    _OVERNIGHT_AVAILABLE = False
try:
    from global_market_filter import get_global_filter as _get_global_filter
    _GLOBAL_FILTER_AVAILABLE = True
except ImportError:
    _GLOBAL_FILTER_AVAILABLE = False

try:
    from strategy_performance_matrix import get_strategy_matrix as _get_strat_matrix
    _MATRIX_AVAILABLE = True
except ImportError:
    _MATRIX_AVAILABLE = False

try:
    from expiry_strategy import is_expiry_today, expiry_signal, get_expiry_score_boost
    _EXPIRY_AVAILABLE = True
except ImportError:
    _EXPIRY_AVAILABLE = False

try:
    from greeks_sizer import get_greeks_sizer as _get_greeks_sizer
    _GREEKS_AVAILABLE = True
except ImportError:
    _GREEKS_AVAILABLE = False

try:
    from cloud_backup import get_backup as _get_backup
    _BACKUP_AVAILABLE = True
except ImportError:
    _BACKUP_AVAILABLE = False

try:
    from event_calendar import get_event_calendar as _get_event_calendar
    _CALENDAR_AVAILABLE = True
except ImportError:
    _CALENDAR_AVAILABLE = False

try:
    from remote_dashboard import start_remote_dashboard as _start_remote_dashboard
    _REMOTE_DASH_AVAILABLE = True
except ImportError:
    _REMOTE_DASH_AVAILABLE = False


class AutonomousTradingSystem:
    def __init__(self) -> None:
        import time as _ts; self._uptime_start = _ts.time()  # set FIRST
        # ── Optional components (safe defaults if module unavailable) ──────────
        self.gap_risk_manager       = None   # initialised below if available
        self.capital_compounder     = None   # initialised below if available
        self.kill_switch            = None   # initialised below if available
        self.data_fetcher           = None   # convenience ref to live_engine.data_fetcher

        self.db = TradeDatabase(getattr(cfg, "TRADES_DB", "trades.db"))

        self.runtime_state = RuntimeState(
            mode="PAPER"
            if bool(getattr(cfg, "PAPER_TRADE", getattr(cfg, "PAPER_TRADING", True)))
            else "LIVE"
        )
        (
            self._persisted_new_day_reset_date,
            self._persisted_new_day_reset_marker,
        ) = self._load_last_new_day_reset_marker()
        if self._persisted_new_day_reset_marker:
            self.runtime_state.last_new_day_reset_at = self._persisted_new_day_reset_marker

        self.learning_controller = SelfLearningController(
            strategy_state_file = getattr(cfg, "STRATEGY_STATE_FILE", "strategy_state.json"),
            history_file        = "strategy_history.json",
            best_params_file    = "best_params.json",
            backup_dir          = BACKUP_DIR,
            learning_state_file = "learning_state.json",
            model_file          = "ai_model.pkl",
            rl_state_file       = "rl_state.json",
            metric              = str(getattr(cfg, "STRATEGY_SELECTION_METRIC", "sharpe")),
            min_trades          = int(getattr(cfg, "MIN_TRADES_FOR_SELECTION", 5)),
            timeout_sec         = 900,
            max_backup_age_hours= 168,
        )


        # ── Restore runtime state from previous session ───────────────────────
        try:
            from system_state import get_state as _get_sys_state
            _prev = _get_sys_state()
            if _prev and _prev.get('daily_pnl') is not None:
                logger.info("Restoring state: daily_pnl=%.2f open_positions=%s",
                            _prev.get('daily_pnl', 0), _prev.get('open_positions', []))
        except Exception as _rs_e:
            logger.debug("state_restore: %s", _rs_e)

        # ── GapRiskManager ────────────────────────────────────────────────
        try:
            from gap_risk_manager import GapRiskManager
            self.gap_risk_manager = GapRiskManager(
                trade_manager = None,   # wired below after live_engine init
                data_fetcher  = None,
                alerts        = None,   # wired below after alerts init
            )
        except Exception as _grm_e:
            self.gap_risk_manager = None

        # ── CapitalCompounder ─────────────────────────────────────────────
        try:
            from capital_compounder import CapitalCompounder
            self.capital_compounder = CapitalCompounder()
        except Exception:
            self.capital_compounder = None


        # ── F&O Bhavcopy OI baseline seeding at boot ─────────────────────────
        try:
            from fno_bhavcopy_oi import seed_oi_tracker_at_startup
            _n_seeded = seed_oi_tracker_at_startup(
                ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"])
            logger.info("OI baseline seeded for %d symbols", _n_seeded)
        except Exception as _oi_e:
            logger.debug("oi_seed: %s", _oi_e)

        # ── Capital fallback: use REAL_CAPITAL from .env, not PAPER_CAPITAL ──
        try:
            import os as _osc
            _real_cap = float(_osc.getenv("REAL_CAPITAL","0") or 0)
            if _real_cap > 0:
                import config as _cfgc
                if getattr(_cfgc,"CAPITAL",0) in (0,100000,1000000):
                    _cfgc.CAPITAL = _real_cap
                    _cfgc.REAL_CAPITAL = _real_cap
                    logger.info("Capital set from .env REAL_CAPITAL: ₹%.0f", _real_cap)
        except Exception: pass

        # ── Angel One session refresh at boot ────────────────────────────────
        try:
            _ang = getattr(self, "_angel", None) or getattr(self, "angel", None)
            if _ang and hasattr(_ang, "_auto_refresh_session"):
                _ang._auto_refresh_session()
                logger.info("Angel session refreshed at startup")
        except Exception as _ar_e:
            logger.debug("angel_refresh_boot: %s", _ar_e)

        # ── Cold start cache seeding (IMPROVEMENT 6) ────────────────────────
        try:
            from data_source_resilience import seed_cache_from_bhavcopy
            _symbols = getattr(self, "universe", [])[:100]
            if _symbols:
                _seeded = seed_cache_from_bhavcopy(_symbols, self._data_fetcher
                                                   if hasattr(self,"_data_fetcher") else None)
                logger.info("Cold start seeding: %d symbols", _seeded)
        except Exception as _cs_e:
            logger.debug("cold_start_seed: %s", _cs_e)

        # ── Strategy Performance Matrix — load learned weights ────────────────
        try:
            from strategy_performance_matrix import get_strategy_matrix
            _spm = get_strategy_matrix()
            if hasattr(_spm, '_load'): _spm._load()
            logger.info("Strategy matrix loaded from disk")
        except Exception as _spm_e:
            logger.debug("strategy_matrix boot load: %s", _spm_e)

        # ── Meta-Learner — load weights trained from live trades ──────────────
        try:
            from meta_learner import get_meta_learner
            _ml = get_meta_learner()
            if hasattr(_ml, 'load_weights'): _ml.load_weights()
            logger.info("Meta-learner weights loaded from disk")
        except Exception as _ml_e:
            logger.debug("meta_learner boot load: %s", _ml_e)

        # ── RL Agent — Q-table already loaded in __init__ ─────────────────────
        try:
            from rl_agent import get_rl_agent
            _rl = get_rl_agent()
            logger.info("RL agent ready: %d states learned", len(_rl._q))
        except Exception: pass

        # ── KillSwitch ────────────────────────────────────────────────────
        try:
            from kill_switch import KillSwitch
            self.kill_switch = KillSwitch()
        except Exception:
            self.kill_switch = None

        self.live_engine = LiveSignalEngine(gap_risk_manager=self.gap_risk_manager)
        # Wire live_engine refs back
        self.data_fetcher = getattr(self.live_engine, "data_fetcher", None)
        if self.gap_risk_manager:
            self.gap_risk_manager.trade_manager = self.live_engine.trade_manager
            self.gap_risk_manager.data_fetcher  = self.data_fetcher

        self.dashboard = TradingDashboard(
            trades_db_path       = getattr(cfg, "TRADES_DB", "trades.db"),
            strategy_state_file  = getattr(cfg, "STRATEGY_STATE_FILE", "strategy_state.json"),
            rl_state_file        = "rl_state.json",
            no_signal_log_file   = getattr(cfg, "NO_SIGNAL_LOG_FILE",    "no_signal.log"),
            diagnostics_log_file = getattr(cfg, "DIAGNOSTIC_LOG_FILE",   "diagnostics.log"),
            output_file          = "dashboard.html",
        )

        self.last_learning_run_ts:  Optional[float] = None
        self.last_dashboard_run_ts: Optional[float] = None
        self.last_hourly_alert_ts:  Optional[float] = None
        self.last_15min_alert_ts:   Optional[float] = None
        self._scan_summary:         dict             = {}
        try:
            state_path = Path(RUN_STATE_FILE)
            if state_path.exists():
                saved_state = json.loads(state_path.read_text(encoding="utf-8") or "{}")
                last_learning = saved_state.get("last_learning_cycle_at")
                if last_learning:
                    self.runtime_state.last_learning_cycle_at = str(last_learning)
                    self.last_learning_run_ts = datetime.fromisoformat(str(last_learning)).timestamp()
                    logger.info("Restored last learning timestamp: %s", last_learning)
        except Exception as exc:
            logger.debug("learning timestamp restore failed: %s", exc)

        # AlertManager — reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from config
        # Off-hours engine and download tracker
        self._off_hours    = None
        self._tg_cmd       = None
        self._conn_monitor = None
        self._conn_monitor = None
        self.alerts = AlertManager(
            bot_token = getattr(cfg, 'TELEGRAM_BOT_TOKEN', None),
            chat_id   = getattr(cfg, 'TELEGRAM_CHAT_ID',   None),
            enabled   = bool(getattr(cfg, 'TELEGRAM_ENABLED', True)),
            dedup_ttl = 60,
        )

        # Track which calendar date we last ran a new-day reset on
        self._last_trading_date:  Optional[date] = None
        self._last_market_phase:  str            = "BOOT"  # for mode-change alerts
        self._eod_squared_off:             bool  = False  # true once EOD close fires today
        self._high_impact_overrides_active: bool = False  # true when high-impact limits applied
        self._saved_max_lots:               int  = 3      # stores normal max_lots for restoration
        self._watchdog_hb_started:          bool = False

        # Auto mode selector — decides paper vs live automatically
        self.auto_mode = None
        if _AUTOMODE_AVAILABLE:
            try:
                self.auto_mode = _get_auto_mode(
                    broker_manager = self.live_engine.broker_manager,
                    alerts         = self.alerts,
                )
            except Exception:
                self.auto_mode = None


        # ── New improvement modules ────────────────────────────────────────────
        self._overnight_prot = None   # wired after trade_manager ready
        self._global_filter  = _get_global_filter()   if _GLOBAL_FILTER_AVAILABLE  else None
        self._strat_matrix   = _get_strat_matrix()    if _MATRIX_AVAILABLE         else None
        self._greeks_sizer   = _get_greeks_sizer()    if _GREEKS_AVAILABLE         else None
        self._cloud_backup   = _get_backup()          if _BACKUP_AVAILABLE         else None
        self._event_calendar = _get_event_calendar()  if _CALENDAR_AVAILABLE       else None
        self._remote_url     = ""

        # Register SIGTERM handler for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_sigterm)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------
    def _handle_sigterm(self, signum, frame) -> None:
        import os   # os is not module-level here; import locally so os._exit() below works
        logger.info("SIGTERM received — initiating graceful shutdown")
        self.runtime_state.running = False
        # Best-effort graceful cleanup, then FORCE-exit. sys.exit(0) here did NOT
        # terminate the process: scanning runs in a thread pool
        # (_evaluate_market_parallel), so SystemExit raised in the main-thread
        # signal handler was held up by non-daemon workers / executor waits and the
        # bot kept running full cycles — systemctl then SIGKILLed it at the
        # stop-timeout (unclean every restart). os._exit() exits at the OS level
        # immediately and cannot be blocked or swallowed.
        # Do not send Telegram/network alerts here. A blocked network call inside
        # a signal handler prevents os._exit() from being reached, which makes
        # systemd restart/stop commands hang.
        try:
            self._save_runtime_state()
        except Exception:
            logger.exception("SIGTERM cleanup failed — forcing exit anyway")
        os._exit(0)

    # ------------------------------------------------------------------
    # Backup helpers
    # ------------------------------------------------------------------
    def _restore_from_backup(self) -> bool:
        """
        Try to bootstrap state from backup / existing strategy_state.json.
        Returns True if a valid state was found (no full re-training needed).
        """
        try:
            result = self.learning_controller.bootstrap_or_learn(force_relearn=False)
            selected = result.get("selected_strategy")

            if selected:
                self.runtime_state.current_strategy  = str(selected)
                self.runtime_state.last_backup_restore_at = datetime.now().isoformat()
                self.runtime_state.last_backup_file  = result.get("backup_file")
                self._save_runtime_state()
                logger.info(
                    "State bootstrapped | strategy=%s restored_from_backup=%s",
                    selected,
                    result.get("restored_from_backup"),
                )
                return True

            return False

        except Exception:
            logger.exception("Backup restore / bootstrap failed")
            return False

    def _save_strategy_snapshot(self) -> None:
        try:
            snapshot_file = self.learning_controller.create_backup_snapshot(
                extra_payload={"runtime_state": asdict(self.runtime_state)}
            )
            self.runtime_state.last_backup_file = snapshot_file
            self._save_runtime_state()
            logger.info("Snapshot saved: %s", snapshot_file)
        except Exception:
            logger.exception("Snapshot failed")

    # ------------------------------------------------------------------
    # Market window — the single authoritative source of session state
    # ------------------------------------------------------------------
    def _market_window(self) -> Dict[str, Any]:
        """
        Return a dict describing whether the current moment is inside
        the trading session and what phase it is in.

        Keys
        ----
        is_trading_day      : bool — False on weekends and NSE holidays
        in_market           : bool — between market_start and market_end
        in_opening_window   : bool — first N minutes after open (no new entries)
        in_trade_window     : bool — valid window for new entries
        market_start        : "HH:MM"
        market_end          : "HH:MM"
        trade_start         : "HH:MM"
        trade_end           : "HH:MM"
        now                 : "HH:MM:SS"
        """
        today = date.today()
        now   = datetime.now().time()

        # Weekend / holiday guard — the original code had no check at all
        trading_day = _is_trading_day(today)

        try:
            session = cfg.get_session_window()
        except Exception:
            # Fall back to raw config strings if get_session_window() fails
            from datetime import time as _t
            def _p(s: str) -> _t:
                h, m = s.split(":")
                return _t(int(h), int(m))

            mstart_raw = getattr(cfg, "MARKET_START", "09:15")
            mend_raw   = getattr(cfg, "MARKET_END",   "15:30")
            buf_start  = int(getattr(cfg, "TRADE_START_BUFFER_MIN", 5))
            buf_end    = int(getattr(cfg, "TRADE_END_BUFFER_MIN",   15))

            market_start = _p(mstart_raw)
            market_end   = _p(mend_raw)

            ts_min = market_start.hour * 60 + market_start.minute + buf_start
            te_min = market_end.hour   * 60 + market_end.minute   - buf_end

            from collections import namedtuple
            _SW = namedtuple("SW", [
                "market_start", "market_end", "trade_start", "trade_end",
                "opening_no_trade_start", "opening_no_trade_end",
                "lunch_no_trade_start",   "lunch_no_trade_end",
            ])
            session = _SW(
                market_start           = market_start,
                market_end             = market_end,
                trade_start            = _t(ts_min // 60, ts_min % 60),
                trade_end              = _t(te_min // 60, te_min % 60),
                opening_no_trade_start = _p(getattr(cfg, "OPENING_NO_TRADE_START", "09:15")),
                opening_no_trade_end   = _p(getattr(cfg, "OPENING_NO_TRADE_END",   "09:20")),
                lunch_no_trade_start   = _p(getattr(cfg, "LUNCH_NO_TRADE_START",   "13:00")),
                lunch_no_trade_end     = _p(getattr(cfg, "LUNCH_NO_TRADE_END",     "13:45")),
            )

        in_market = trading_day and session.market_start <= now <= session.market_end

        # Opening no-trade window (first 5 minutes — extremely volatile)
        avoid_open = bool(getattr(cfg, "AVOID_OPENING_WINDOW", True))
        in_opening_window = (
            in_market
            and avoid_open
            and session.opening_no_trade_start <= now <= session.opening_no_trade_end
        )

        # Lunch chop window (optional)
        avoid_lunch = bool(getattr(cfg, "AVOID_LUNCH_CHOP", False))
        in_lunch_window = (
            in_market
            and avoid_lunch
            and session.lunch_no_trade_start <= now <= session.lunch_no_trade_end
        )

        # Valid trade window: inside market hours, past opening window, not in
        # any exclusion window, and within the buffered trade_start/trade_end band
        in_trade_window = (
            in_market
            and not in_opening_window
            and not in_lunch_window
            and session.trade_start <= now <= session.trade_end
        )

        # FORCE_MARKET_OPEN / FORCE_MARKET_CLOSE overrides (paper-trade testing)
        if bool(getattr(cfg, "FORCE_MARKET_OPEN", False)):
            in_market        = True
            in_trade_window  = True
            in_opening_window = False

        if bool(getattr(cfg, "FORCE_MARKET_CLOSE", False)):
            in_market        = False
            in_trade_window  = False

        # EOD square-off window: last N minutes of session (default 15 min before close)
        eod_buffer_min  = int(getattr(cfg, "EOD_EXIT_BUFFER_MIN", 15))
        market_end_min  = session.market_end.hour * 60 + session.market_end.minute
        eod_exit_min    = market_end_min - eod_buffer_min
        from datetime import time as _dtime
        eod_exit_time   = _dtime(eod_exit_min // 60, eod_exit_min % 60)

        in_eod_exit_window = (
            in_market
            and not in_opening_window
            and now >= eod_exit_time
        )

        return {
            "is_trading_day":      trading_day,
            "in_market":           in_market,
            "in_opening_window":   in_opening_window,
            "in_trade_window":     in_trade_window,
            "in_eod_exit_window":  in_eod_exit_window,
            "eod_exit_time":       eod_exit_time.strftime("%H:%M"),
            "market_start":        session.market_start.strftime("%H:%M"),
            "market_end":          session.market_end.strftime("%H:%M"),
            "trade_start":         session.trade_start.strftime("%H:%M"),
            "trade_end":           session.trade_end.strftime("%H:%M"),
            "now":                 now.strftime("%H:%M:%S"),
        }

    # ------------------------------------------------------------------
    # New trading day handler
    # ------------------------------------------------------------------
    def _on_new_trading_day(self) -> None:
        """
        Called once when the calendar date changes to a new trading day.
        Resets all day-scoped state: loss limits, trade counters, etc.
        """
        today = date.today()
        logger.info("New trading day detected: %s", today.isoformat())
        self.db.log_event("INFO", "NEW_DAY", f"New trading day: {today.isoformat()}")

        # Reset daily loss limit state on the live engine
        try:
            if hasattr(self.live_engine, "daily_loss_manager"):
                self.live_engine.daily_loss_manager.reset_day()
                logger.info("DailyLossLimitManager reset for %s", today.isoformat())
        except Exception:
            logger.exception("Failed to reset daily_loss_manager")

        # Reset any day-scoped counters on the live engine itself
        try:
            if hasattr(self.live_engine, "reset_daily_state"):
                self.live_engine.reset_daily_state()
        except Exception:
            logger.exception("Failed to call live_engine.reset_daily_state()")

        self._eod_squared_off = False              # allow EOD close to fire again today
        self._high_impact_overrides_active = False  # allow fresh override check each day

        # Reset learning-run timestamp so after-hours learning fires today
        self.last_learning_run_ts = None

        self.runtime_state.last_new_day_reset_at = datetime.now().isoformat()
        self._persisted_new_day_reset_date = today
        self._persisted_new_day_reset_marker = self.runtime_state.last_new_day_reset_at
        self.runtime_state.daily_realized_pnl    = 0.0
        self.runtime_state.trading_locked        = False
        self.runtime_state.lock_reason           = ""
        self._save_runtime_state()

        _append_jsonl(
            MAIN_LIVE_LOG,
            {
                "timestamp": datetime.now().isoformat(),
                "event":     "NEW_DAY",
                "date":      today.isoformat(),
            },
        )

    def _check_and_reset_on_new_day(self) -> None:
        """
        Compare today's date to the last known trading date.
        Triggers _on_new_trading_day() if the date has changed.
        Called once per main-loop iteration.
        """
        today = date.today()
        if not _is_trading_day(today):
            return   # weekends / holidays — no reset needed

        if self._last_trading_date is None or self._last_trading_date < today:
            self._on_new_trading_day()
            self._last_trading_date = today

    def _load_last_new_day_reset_marker(self) -> tuple[Optional[date], Optional[str]]:
        """
        Read the last persisted daily reset marker without restoring the full
        runtime state. This prevents same-day restarts from clearing daily risk
        guards while still allowing a fresh reset on the next trading date.
        """
        try:
            path = Path(RUN_STATE_FILE)
            if not path.exists():
                return None, None
            raw_state = json.loads(path.read_text(encoding="utf-8") or "{}")
            raw_marker = raw_state.get("last_new_day_reset_at")
            if not raw_marker:
                return None, None
            marker_date = datetime.fromisoformat(str(raw_marker)).date()
            return marker_date, str(raw_marker)
        except Exception as exc:
            logger.debug("Could not read last daily reset marker: %s", exc)
            return None, None

    # ------------------------------------------------------------------
    # State + health persistence
    # ------------------------------------------------------------------
    def _save_runtime_state(self) -> None:
        self.runtime_state.updated_at = datetime.now().isoformat()
        path = Path(RUN_STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self.runtime_state), indent=2), encoding="utf-8"
        )


    def _write_live_status(self) -> None:
        """
        Write live_status.json every heartbeat cycle.

        Contains the real-time operational state needed by monitoring tools:
        - Current open positions with unrealized P&L estimate
        - Daily P&L vs daily limit
        - Kill switch state
        - Circuit breaker state
        - High-impact day flag
        - Last heartbeat timestamp

        This file can be served by a simple HTTP server or read by a
        monitoring script. It is written atomically (write temp → rename)
        to avoid partial reads.
        """
        try:
            trade_summary = self.live_engine.trade_manager.summary()
            open_positions = self.live_engine.trade_manager.get_open_positions()

            # Circuit breaker state from live engine
            cb_active = time.time() < getattr(self.live_engine, "_circuit_breaker_until", 0)
            cb_resume = max(0, int(getattr(self.live_engine, "_circuit_breaker_until", 0) - time.time()))

            # Kill switch state
            ks = self.kill_switch if hasattr(self, "kill_switch") else None
            kill_switch_state = ks.summary() if ks is not None else {"active": False}

            today = date.today()
            is_high_impact = today in globals().get("HIGH_IMPACT_DATES", set())

            telegram_command_state = {}
            try:
                if self._tg_cmd and hasattr(self._tg_cmd, "health"):
                    telegram_command_state = self._tg_cmd.health()
            except Exception:
                telegram_command_state = {"error": "health_unavailable"}

            payload = {
                "timestamp":         datetime.now().isoformat(),
                "market_phase":      self.runtime_state.market_phase,
                "current_strategy":  self.runtime_state.current_strategy,
                "is_high_impact_day": is_high_impact,
                "high_impact_dates_today": str(today) if is_high_impact else None,
                "trading": {
                    "locked":                trade_summary.get("trading_locked", False),
                    "lock_reason":           trade_summary.get("lock_reason", ""),
                    "open_positions":        trade_summary.get("open_positions", 0),
                    "closed_positions":      trade_summary.get("closed_positions", 0),
                    "daily_realized_pnl":    trade_summary.get("daily_realized_pnl", 0.0),
                    "daily_loss_limit":      self.live_engine.trade_manager.daily_loss_limit,
                    "pnl_pct_of_limit":      round(
                        trade_summary.get("daily_realized_pnl", 0.0)
                        / max(1.0, self.live_engine.trade_manager.daily_loss_limit) * 100, 1
                    ),
                },
                "kill_switch":       kill_switch_state,
                "circuit_breaker": {
                    "active":         cb_active,
                    "resume_in_sec":  cb_resume if cb_active else 0,
                    "consecutive_failures": getattr(
                        self.live_engine, "_consecutive_exec_failures", 0
                    ),
                },
                "open_positions": [
                    {
                        "trade_id":    p.get("trade_id"),
                        "symbol":      p.get("symbol"),
                        "side":        p.get("side"),
                        "qty":         p.get("qty"),
                        "entry_price": p.get("entry_price"),
                        "stop_loss":   p.get("stop_loss"),
                        "target_price": p.get("target_price"),
                        "strategy":    p.get("strategy"),
                        "regime":      p.get("regime"),
                        "confidence":  p.get("confidence"),
                    }
                    for p in open_positions
                ],
                "heartbeat": {
                    "count":   self.runtime_state.heartbeat_count,
                    "last_at": self.runtime_state.last_heartbeat_at,
                },
                "telegram_command": telegram_command_state,
                "mode": self.runtime_state.mode,
            }

            # Atomic write: temp file → rename
            status_path = Path(LIVE_STATUS_FILE)
            status_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = status_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            tmp_path.replace(status_path)

        except Exception:
            logger.debug("_write_live_status failed", exc_info=True)

    def _write_health_snapshot(self) -> None:
        try:
            trade_summary = self.live_engine.trade_manager.summary()
        except Exception:
            trade_summary = {}

        broker_status = []
        try:
            manager = (
                getattr(self.live_engine, "broker_manager", None)
                or getattr(self.live_engine, "_broker_manager", None)
            )
            if manager is not None:
                broker_status = manager.get_all_broker_status()
        except Exception:
            logger.debug("broker status snapshot failed", exc_info=True)

        payload = {
            "timestamp":     datetime.now().isoformat(),
            "runtime_state": asdict(self.runtime_state),
            "trade_summary": trade_summary,
            "market_window": self._market_window(),
            "broker_status": broker_status,
        }
        path = Path(HEALTH_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _sync_trade_state(self) -> None:
        try:
            summary = self.live_engine.trade_manager.summary()
            self.runtime_state.open_trade_count   = int(summary.get("open_positions",    0))
            self.runtime_state.closed_trade_count = int(summary.get("closed_positions",  0))
            self.runtime_state.daily_realized_pnl = _safe_float(summary.get("daily_realized_pnl"), 0.0)
            self.runtime_state.trading_locked      = bool(summary.get("trading_locked", False))
            self.runtime_state.lock_reason         = str(summary.get("lock_reason") or "")
        except Exception:
            logger.exception("_sync_trade_state failed")

    def _heartbeat(self, phase: str) -> None:
        self.runtime_state.heartbeat_count   += 1
        self.runtime_state.last_heartbeat_at  = datetime.now().isoformat()
        self.runtime_state.market_phase       = phase
        self._sync_trade_state()
        self._write_health_snapshot()
        self._write_live_status()    # real-time monitoring file
        self._save_runtime_state()
        try:
            from runtime_telemetry import heartbeat
            heartbeat("system", phase=phase,
                      last_executed_strategy=self.runtime_state.current_strategy or "")
            heartbeat("strategy_engine", phase=phase)
            heartbeat("data_feed", phase=phase)
            heartbeat("trade_manager", open_trades=self.runtime_state.open_trade_count)
            heartbeat("dashboard_updater", phase=phase)
        except Exception:
            pass

    def _start_watchdog_heartbeat(self) -> None:
        """Keep watchdog live_status fresh even while long jobs block the loop."""
        if self._watchdog_hb_started:
            return
        self._watchdog_hb_started = True
        try:
            import threading as _threading
            import time as _time

            def _writer() -> None:
                while True:
                    try:
                        self.runtime_state.heartbeat_count += 1
                        self.runtime_state.last_heartbeat_at = datetime.now().isoformat()
                        self._write_live_status()
                    except Exception:
                        logger.debug("watchdog heartbeat writer failed", exc_info=True)
                    _time.sleep(30)

            _threading.Thread(
                target=_writer,
                daemon=True,
                name="WatchdogLiveStatusHB",
            ).start()
            logger.info("Watchdog heartbeat writer started")
        except Exception:
            logger.debug("watchdog heartbeat writer init failed", exc_info=True)

    # ------------------------------------------------------------------
    # After-hours tasks
    # ------------------------------------------------------------------
    def _run_early_prep(self) -> None:
        """07:00 — pre-session prep: backfill any stale EOD stores so the day
        starts on fresh data. Extends the active window to 07:00 (runs in a
        daemon thread; best-effort)."""
        try:
            import data_gap_filler
            res = data_gap_filler.fill(execute=True)
            filled = [r.get("name") for r in res if "filled" in str(r.get("action", ""))]
            msg = "🌅 <b>EARLY PREP 07:00</b>\n  EOD data freshness checked"
            msg += ("\n  Backfilled: " + ", ".join(filled)) if filled else "\n  All EOD stores current ✅"
            self.alerts.send(msg, dedup_key=f"earlyprep_{date.today()}")
        except Exception as exc:
            logger.debug("early prep: %s", exc)

    def _run_eod_final(self) -> None:
        """19:00 — final EOD capture. FII/DII + India VIX publish AFTER the 16:00
        pipeline, so the 16:00 capture gets stale values; re-capture now for
        accurate same-day history. Daemon thread; best-effort."""
        try:
            import eod_market_capture
            r = eod_market_capture.run_eod_capture()
            fii = r.get("fii_dii") or {}
            vix = (r.get("vix") or {}).get("vix", "?")
            self.alerts.send(
                "🌙 <b>EOD FINAL 19:00</b>\n"
                f"  FII net: ₹{fii.get('fii_net', '?')} Cr | DII net: ₹{fii.get('dii_net', '?')} Cr\n"
                f"  India VIX: {vix}",
                dedup_key=f"eodfinal_{date.today()}")
        except Exception as exc:
            logger.debug("eod final: %s", exc)

    def _after_hours_tasks(self) -> None:
        logger.info("After-hours mode")
        if _SYSSTATE:
            try:
                _ss = _get_sys_state()
                if _ss.get_state() not in ("AFTER_HOURS", "BACKTEST", "ML_TRAINING"):
                    _ss.set("AFTER_HOURS", "Market closed")
            except Exception:
                pass
        # Write heartbeat FIRST so watchdog knows bot is alive
        self._heartbeat("AFTER_HOURS")
        self._notify_mode_change("LEARNING")
        self._after_hours_position_safety_check()
        # Auto lot scaling check
        try:
            import config as _cfg_s
            if self._strat_matrix:
                _summ = self._strat_matrix.summary()
                _total = sum(v.get("total_trades",0) for v in _summ.values())
                if _total >= 30:
                    _wins = sum(v.get("total_trades",0)*v.get("overall_wr",50)/100 for v in _summ.values())
                    _wr   = _wins / max(_total,1) * 100
                    _cur  = int(getattr(_cfg_s,"MAX_LOTS",1))
                    if _wr >= 55 and _cur < 5:
                        _cfg_s.MAX_LOTS = _cur + 1
                        logger.info("AUTO SCALE UP: wr=%.0f%% MAX_LOTS=%d", _wr, _cfg_s.MAX_LOTS)
                        self.alerts.send(f"📈 Auto scale up: MAX_LOTS→{_cfg_s.MAX_LOTS} (wr={_wr:.0f}%)", dedup_key=f"scaleup_{_cfg_s.MAX_LOTS}")
                    elif _wr < 40 and _cur > 1:
                        _cfg_s.MAX_LOTS = max(_cur-1,1)
                        logger.warning("AUTO SCALE DOWN: wr=%.0f%% MAX_LOTS=%d", _wr, _cfg_s.MAX_LOTS)
                    try: self.alerts.drawdown_alert({"win_rate": _wr, "new_lots": _cfg_s.MAX_LOTS})
                    except Exception: pass
        except Exception: pass

        # Weekly loss stats
        if hasattr(self.live_engine.daily_loss_manager, "get_weekly_stats"):
            try:
                ws = self.live_engine.daily_loss_manager.get_weekly_stats()
                logger.info("Weekly stats: loss=₹%.0f limit=₹%.0f (%.0f%%)",
                            ws["weekly_loss"], ws["weekly_limit"], ws["weekly_pct"])
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Autonomous backtest at 4:30 PM daily ──────────────────────────
        try:
            from datetime import time as _dbt
            _nbt = datetime.now()
            _bot_age = __import__("time").time() - getattr(self,"_uptime_start",0)
            if _dbt(16,28) <= _nbt.time() <= _dbt(17,30) and _bot_age > 1800:
                # 30-min grace: backtest only runs 30+ min after startup
                if _BT_AVAILABLE:
                    _bt_engine = _get_backtest(alerts=self.alerts)
                    if _bt_engine.should_run_today():
                        logger.info("Starting autonomous overnight backtest...")
                        try:
                            import threading as _btt, pathlib as _btp, json as _btj, time as _bts
                            def _bt_heartbeat():
                                """Keep heartbeat alive during long backtest."""
                                while getattr(_bt_flag, "running", True):
                                    try:
                                        _btp.Path("heartbeat.json").write_text(
                                            _btj.dumps({"ts": _bts.time()}))
                                    except Exception: pass
                                    _bts.sleep(120)
                            class _bt_flag: running = True
                            _THREAD_POOL.submit(_bt_heartbeat)
                            _bt_engine.run()
                            _bt_flag.running = False
                            # Auto-sync after backtest
                            if _DRIVE_SYNC_AVAIL:
                                try: _get_drive_sync(alerts=self.alerts).sync_now("push")
                                except Exception: pass
                        except Exception as _bte:
                            logger.warning("Backtest error: %s", _bte)
                            try:
                                if _OFFHOURS_AVAIL and self._off_hours:
                                    self._off_hours.alert_mode_switch("BACKTEST","AFTER_HOURS","Backtest failed")
                                self.alerts.backtest_report({"status":"failed","error":str(_bte)})
                            except Exception: pass
        except Exception: pass

        # Agent evening reflection at 4:15 PM
        try:
            from datetime import time as _dt4
            _now4 = datetime.now()
            if _dt4(16,12) <= _now4.time() <= _dt4(16,20) and self._agent:
                if self._agent.is_available():
                    if not getattr(self,"_reflection_sent",False):
                        self._reflection_sent = True
                        _closed = [vars(t) if hasattr(t,"__dict__") else t
                                   for t in list(self.live_engine.trade_manager.closed_trades)[-20:]]
                        self._agent.evening_reflection(_closed)
            elif _now4.weekday() == 0: self._reflection_sent = False
        except Exception: pass

        # Weekly report — Friday 4 PM
        try:
            from datetime import time as _dtw
            _nw = datetime.now()
            if _nw.weekday() == 4 and _dtw(16,0) <= _nw.time() <= _dtw(16,10):
                if not getattr(self,"_wk_sent",False):
                    self._wk_sent = True
                    self._send_weekly_report()
            elif _nw.weekday() != 4:
                self._wk_sent = False
        except Exception: pass

        # Daily P&L report at 3:35 PM
        try:
            from datetime import time as _dt35
            _now_35 = datetime.now().time()
            if _dt35(15,34) <= _now_35 <= _dt35(15,40):
                if not getattr(self, "_daily_rpt_sent", False):
                    self._daily_rpt_sent = True
                    self._send_daily_report()
                    # EOD Drive sync — push trades + code
                    try:
                        if _DRIVE_SYNC_AVAIL:
                            _get_drive_sync(alerts=self.alerts).sync_now("push")
                    except Exception: pass
                    # Signal rejection summary
                    try:
                        _rej = getattr(self.live_engine, "_rejection_stats", {})
                        if _rej:
                            self.alerts.signal_rejection_summary(
                                total_evaluated=_rej.get("total",0),
                                total_passed=_rej.get("passed",0),
                                rejection_reasons=_rej.get("reasons",{}),
                            )
                    except Exception: pass
                    try:
                        from trade_manager import get_journal as _gj
                        _rpt = _gj().format_daily_telegram()
                        from datetime import date as _d
                        self.alerts.send(_rpt, dedup_key=f"journal_{_d.today()}")
                    except Exception: pass
            elif _now_35 < _dt35(9,0):
                self._daily_rpt_sent = False
        except Exception: pass

        # Agent morning briefing at 9:00 AM
        try:
            from datetime import time as _dt9
            _now9 = datetime.now()
            if _dt9(8,58) <= _now9.time() <= _dt9(9,5) and self._agent:
                if self._agent.is_available():
                    _mkt = {
                        "vix": getattr(self.live_engine, "_vix", 15),
                        "gift_nifty_change_pct": 0,
                        "events_today": [],
                        "cpr_day_type": "NORMAL",
                    }
                    try:
                        _ctx = self.live_engine.market_context
                        if _ctx: _mkt.update(_ctx)
                    except Exception: pass
                    self._agent.morning_briefing(_mkt)
        except Exception: pass

        # ── AUTOMATED DAILY SCHEDULE (wired via off_hours_engine) ─────────────
        if _OFFHOURS_AVAIL and self._off_hours:
            _ohe = self._off_hours
            _now_t = datetime.now()
            _h, _m = _now_t.hour, _now_t.minute

            def _once(flag, fn):
                if not getattr(self, flag, False):
                    setattr(self, flag, True)
                    try: fn()
                    except Exception: pass

            def _reset_daily():
                for _f in ['_gap_warn_sent','_video_sent','_brief_sent',
                            '_fno_sent','_mkt_hb_sent','_acc_sent',
                            '_eodml_sent',
                            '_meta_train_sent','_bhav_sent',
                            '_omni_sent_10','_omni_sent_12','_omni_sent_14',
                            '_lp_sent_10_0','_lp_sent_11_30',
                            '_lp_sent_13_0','_lp_sent_14_30',
                            '_early_prep_sent','_eod_final_sent']:
                    setattr(self, _f, False)

            def _bg(fn):
                import threading as _tbg
                _tbg.Thread(target=fn, daemon=True).start()

            if _h < 7: _reset_daily()

            # 07:00 — pre-session prep; extends the active window to 07:00–20:00.
            if _h == 7 and _m == 0:   _once('_early_prep_sent',    lambda: _bg(self._run_early_prep))
            if _h == 7 and _m >= 43:  _once('_gap_warn_sent',     _ohe._run_swing_gap_warning)
            if _h == 8 and _m < 10:   _once('_video_sent',        _ohe._run_morning_video)
            if _h == 8 and _m >= 28:  _once('_brief_sent',        _ohe._run_morning_brief)
            if _h == 9 and _m >= 4:   _once('_fno_sent',          _ohe._run_fno_ban_check)
            if _h == 10 and _m == 0:  _once('_omni_sent_10',      _ohe._run_omnisource_refresh)
            if _h == 10 and _m == 0:  _once('_lp_sent_10_0',      _ohe._run_live_position_update)
            if _h == 11 and _m == 30: _once('_lp_sent_11_30',     _ohe._run_live_position_update)
            if _h == 12 and _m == 0:  _once('_omni_sent_12',      _ohe._run_omnisource_refresh)
            if _h == 13 and _m == 0:  _once('_lp_sent_13_0',      _ohe._run_live_position_update)
            if _h == 14 and _m == 0:  _once('_omni_sent_14',      _ohe._run_omnisource_refresh)
            if _h == 14 and _m == 29: _once('_lp_sent_14_30',     _ohe._run_live_position_update)
            if _h == 14 and _m >= 29: _once('_mkt_hb_sent',       _ohe._run_heartbeat)
            # accuracy post (15:39) CUT per user — intraday noise. (Omnisource
            # 10/12/14 KEPT: it's a silent data-cache refresh, not a message.)
            # if _h == 15 and _m >= 39: _once('_acc_sent',         _ohe._run_accuracy_post)
            if _h == 15 and _m >= 45: _once('_manualrisk_sent',    self._check_manual_book_risk)
            # EOD ML analysis was only in the Saturday task list — so
            # eod_ml_feedback stayed empty forever. Run it daily post-close.
            if _h == 16 and _m >= 5:  _once('_eodml_sent',        _ohe._run_eod_ml_analysis)
            if _h == 17 and _m == 0:  _once('_meta_train_sent',   _ohe._run_meta_learner_training)
            if _h == 18 and _m == 0:  _once('_bhav_sent',         _ohe._run_bhavcopy_download)
            # 19:00 — final EOD capture (FII/DII + VIX publish after 16:00) — ends
            # the 07:00–20:00 active processing window with accurate same-day data.
            if _h == 19 and _m == 0:  _once('_eod_final_sent',     lambda: _bg(self._run_eod_final))

        # Multi-day circuit breaker
        try:
            if not hasattr(self, "_day_pnl_history"): self._day_pnl_history = []
            if not hasattr(self, "_cb_reduced"): self._cb_reduced = False
            from datetime import time as _dtcb
            if datetime.now().time() >= _dtcb(15,30) and not getattr(self,"_cb_recorded",False):
                self._cb_recorded = True
                self._day_pnl_history.append(self.live_engine.trade_manager.get_daily_pnl())
                self._day_pnl_history = self._day_pnl_history[-10:]  # keep 10 days
                bad_days = sum(1 for p in self._day_pnl_history[-5:] if p < 0)
                import config as _cfcb
                cur_lots = int(getattr(_cfcb,"MAX_LOTS",1))
                if bad_days >= 5 and not self._cb_reduced:
                    _cfcb.MAX_LOTS = max(1, cur_lots - 1)
                    self._cb_reduced = True
                    try:
                        self.alerts.circuit_breaker({
                            "bad_days": bad_days,
                            "new_lots": _cfcb.MAX_LOTS,
                        })
                    except Exception:
                        self.alerts.send(
                            f"🔴 CIRCUIT BREAKER: {bad_days} bad days → MAX_LOTS={_cfcb.MAX_LOTS}",
                            dedup_key="cb_trigger"
                        )
                elif bad_days <= 2 and self._cb_reduced:
                    _cfcb.MAX_LOTS = min(cur_lots + 1, 5)
                    self._cb_reduced = False
            elif datetime.now().time() < _dtcb(9,0):
                self._cb_recorded = False
        except Exception: pass

        # Holiday / weekend check
        if _OFFHOURS_AVAIL and self._off_hours:
            try:
                _today = date.today()
                _is_holiday = is_market_holiday(_today)
                _is_weekend = _today.weekday() >= 5
                _holiday_key = f"holiday_tasks_{_today}"
                if _is_holiday and not _is_weekend and not getattr(self,_holiday_key,False):
                    setattr(self, _holiday_key, True)
                    self._off_hours.alert_mode_switch("TRADING","HOLIDAY",
                        f"{_today} is NSE holiday — running extended tasks")
                    _thr = __import__("threading").Thread(
                        target=self._off_hours.run_holiday_tasks, daemon=True)
                    _thr.start()
                elif _is_weekend:
                    _is_sat = _today.weekday() == 5
                    self._off_hours.run_weekend_tasks(is_saturday=_is_sat)
            except Exception: pass
        # ── Connection check at 8:50 AM ─────────────────────────────────
        try:
            from datetime import time as _dt850
            if _dt850(8,49) <= datetime.now().time() <= _dt850(8,55):
                if _CONN_MON and self._conn_monitor:
                    if not getattr(self,"_conn_check_done",False):
                        self._conn_check_done = True
                        import threading as _thr
                        _thr.Thread(
                            target=self._conn_monitor.run_full_check,
                            args=("PRE-MARKET",), daemon=True
                        ).start()
            elif datetime.now().time() < _dt850(8,0):
                self._conn_check_done = False  # reset for next day
        except Exception: pass

        # ── 8:50 AM connection check ─────────────────────────────────────
        try:
            from datetime import time as _dt850
            _t850 = datetime.now().time()
            if _dt850(8,49) <= _t850 <= _dt850(8,55):
                if _CONN_MON and self._conn_monitor:
                    if not getattr(self,"_premarketconn_done",False):
                        self._premarketconn_done = True
                        import threading as _th850
                        _th850.Thread(
                            target=self._conn_monitor.run_full_check,
                            args=("PRE-MARKET",), daemon=True
                        ).start()
            if _t850 < _dt850(8,0):
                self._premarketconn_done = False
        except Exception: pass

        # Dual mode pre-market balance check at 8:55 AM
        try:
            from datetime import time as _dt855
            _now855 = datetime.now()
            if _dt855(8,54) <= _now855.time() <= _dt855(9,0):
                if _DUAL_MODE_AVAILABLE and self._dual_engine:
                    if getattr(self,"_premarket_dual_done",None) != _now855.date():
                        self._premarket_dual_done = _now855.date()
                        _dm = self._dual_engine.run_premarket_check()
                        logger.info(
                            "Pre-market dual check: %s balance=₹%.0f",
                            _dm.get("mode"), _dm.get("balance",0)
                        )
        except Exception: pass

        # Pivot levels morning summary (sent at 8:30 AM)
        try:
            from datetime import time as _dt
            _now_t = datetime.now().time()
            if _dt(8,28) <= _now_t <= _dt(8,35):
                from pivot_boss import get_pivot_levels_summary
                _nifty_df = self.live_engine.data_fetcher.get_market_data(
                    "NIFTY", interval="5m", days=2)
                if _nifty_df is not None:
                    _pvt_msg = get_pivot_levels_summary(_nifty_df)
                    self.alerts.send(_pvt_msg, dedup_key=f"pivots_{date.today()}")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # ── Daily trading plan at 9:10 AM ──────────────────────────────
        try:
            from datetime import time as _dtp
            if _dtp(9,8) <= datetime.now().time() <= _dtp(9,14):
                if not getattr(self,"_plan_sent",False):
                    self._plan_sent = True
                    try:
                        _nf_df = self.live_engine.data_fetcher.get_market_data(
                            "NIFTY", interval="5m", days=2)
                        if _nf_df is not None and len(_nf_df) >= 2:
                            _pc = float(_nf_df["close"].iloc[-2])
                            from pivot_boss import calc_floor_pivots, calc_weekly_pivots
                            _prev_h = float(_nf_df["high"].max())
                            _prev_l = float(_nf_df["low"].min())
                            _pv = calc_floor_pivots(_prev_h, _prev_l, _pc)
                            from expiry_regime import get_expiry_regime as _ger
                            _er = _ger()
                            _dt = "TRENDING" if _er.get("days_to_expiry",7) > 2 else "EXPIRY"
                            self.alerts.daily_plan(
                                prev_close=_pc,
                                day_type=_dt,
                                regime="TREND",
                                resistance_levels=[
                                    (_pv["R2"], "Daily R2"),
                                    (_pv["R1"], "Daily R1"),
                                ],
                                support_levels=[
                                    (_pv["S1"], "Daily S1"),
                                    (_pv["S2"], "Daily S2"),
                                ],
                                preferred_strategies=["orb","trend","holy_grail"],
                                best_windows=["9:20-10:30","14:30-15:15"],
                            )
                    except Exception: pass
            elif datetime.now().time() < _dtp(9,0):
                self._plan_sent = False
        except Exception: pass

        # ── Pre-market health check (8:25 AM) ────────────────────────────
        try:
            from datetime import time as _dth
            if _dth(8,25) <= datetime.now().time() <= _dth(8,28):
                if _MONITOR_AVAIL and self._conn_monitor:
                    if not getattr(self,"_premarket_health_sent",False):
                        self._premarket_health_sent = True
                        import threading as _thr2
                        def _pre_check():
                            import yf_compat as _yf
                            _vix = 0.0
                            try:
                                _vdf = _yf.download("^INDIAVIX","1d","1d",progress=False,auto_adjust=True)
                                if _vdf is not None and len(_vdf)>0:
                                    _vix = float(_vdf["Close"].iloc[-1])
                            except Exception: pass
                            _nifty_prev = 0.0
                            try:
                                _ndf = _yf.download("^NSEI","2d","1d",progress=False,auto_adjust=True)
                                if _ndf is not None and len(_ndf)>0:
                                    _nifty_prev = float(_ndf["Close"].iloc[-1])
                            except Exception: pass
                            _res = self._conn_monitor.run_full_check()
                            if isinstance(_res, dict):
                                _ok = int(_res.get("ok", 0) or 0)
                                _warn = int(_res.get("warnings", 0) or 0)
                                _fail = int(_res.get("failures", 0) or 0)
                                _critical = _res.get("critical", []) or []
                                _issues = [f"{name}: critical" for name in _critical]
                                if _warn:
                                    _issues.append(f"{_warn} warning feed(s)")
                                if _fail and not _critical:
                                    _issues.append(f"{_fail} failed feed(s)")
                                _total = _ok + _warn + _fail
                            else:
                                _issues = [f"{r.name}: {r.detail}" for r in _res
                                           if getattr(r, "status", None) is False]
                                _ok = sum(1 for r in _res
                                          if getattr(r, "status", None) is True)
                                _total = len(_res)
                            self.alerts.pre_market_ready(
                                checks_ok  =_ok,
                                checks_total=_total,
                                vix        =_vix,
                                nifty_prev =_nifty_prev,
                                issues     =_issues,
                            )
                        _thr2.Thread(target=_pre_check, daemon=True).start()
            elif datetime.now().time() < _dth(8,0):
                self._premarket_health_sent = False
        except Exception: pass

        # ── Full pre-market intelligence brief (8:30 AM) ─────────────────
        try:
            from datetime import time as _dti
            if _dti(8,28) <= datetime.now().time() <= _dti(8,35):
                if not getattr(self,"_intel_brief_sent",False):
                    self._intel_brief_sent = True
                    _lines = []
                    # F&O ban list
                    try:
                        from fno_ban_list import get_ban_status_message
                        _lines.append(get_ban_status_message())
                    except Exception: pass
                    # Expiry regime
                    try:
                        from expiry_regime import get_expiry_regime
                        _er = get_expiry_regime()
                        _lines.append(f"📅 {_er['regime_label']} | DTE={_er['days_to_expiry']} | Expiry={_er['next_expiry']}")
                    except Exception: pass
                    # Participant OI
                    try:
                        from participant_oi import get_participant_data, compute_participant_signal
                        _pd = get_participant_data(force=True)
                        _pm, _pn = compute_participant_signal(_pd, "BUY")
                        _lines.append(f"🏦 Participant OI: {_pn} (mod={_pm:+.1f})")
                        from participant_oi import get_cumulative_fii
                        _cum5 = get_cumulative_fii(5)
                        _lines.append(f"📈 FII 5d cumulative: ₹{_cum5:+,.0f}Cr")
                    except Exception: pass
                    # Bulk deals
                    try:
                        from bulk_deals import get_bulk_deal_summary
                        _lines.append(get_bulk_deal_summary())
                    except Exception: pass
                    # Corporate actions
                    try:
                        from corporate_actions import get_action_summary
                        _lines.append(get_action_summary())
                    except Exception: pass
                    # News brief
                    try:
                        from news_nlp import get_market_news_brief
                        _lines.append(get_market_news_brief())
                    except Exception: pass
                    # Cross-asset bias
                    try:
                        from cross_asset import get_market_bias
                        _cb = get_market_bias()
                        _lines.append(f"🌐 Cross-asset: {_cb['bias']} — {" | ".join(_cb['reasons']) or 'No risk flags'}")
                    except Exception: pass
                    # SENSEX vs NIFTY divergence
                    try:
                        from bse_option_chain import get_sensex_banknifty_divergence, get_bse_pcr
                        _sdiv = get_sensex_banknifty_divergence()
                        if abs(_sdiv.get("divergence",0)) > 0.2:
                            _lines.append(
                                f"🔀 SENSEX-NIFTY: {_sdiv['divergence']:+.2f}% "
                                f"({_sdiv['signal']}) "
                                f"— NIFTY{_sdiv['nifty_pct']:+.1f}% SENSEX{_sdiv['sensex_pct']:+.1f}%"
                            )
                        _spcr = get_bse_pcr("SENSEX")
                        if _spcr.get("signal") != "NEUTRAL":
                            _lines.append(f"📊 SENSEX OC: PCR={_spcr['pcr']:.2f} [{_spcr['signal']}]")
                    except Exception: pass
                    # Theta environment
                    try:
                        from theta_strategy import is_theta_environment, suggest_strangle
                        from iv_percentile import get_ivp
                        _ivp = get_ivp() or 50
                        if is_theta_environment(_ivp):
                            _st = suggest_strangle(22500, _ivp)  # approximate spot
                            _lines.append(
                                f"⚡ THETA ENV: IV%={_ivp:.0f} → Consider strangle\n"
                                f"   Sell {_st['pe_strike']}PE + {_st['ce_strike']}CE"
                                f" for ₹{_st['total_premium']:.0f} premium"
                            )
                    except Exception: pass
                    if _lines:
                        self.alerts.send(
                            "📊 <b>PRE-MARKET INTELLIGENCE</b>\n" + "\n".join(_lines),
                            dedup_key=f"intel_{date.today()}"
                        )
            elif datetime.now().time() < _dti(8,0):
                self._intel_brief_sent = False
        except Exception: pass

        # Event calendar morning alert
        if _CALENDAR_AVAILABLE and self._event_calendar:
            try:
                _cal_msg = self._event_calendar.get_morning_alert()
                if _cal_msg:
                    from datetime import date as _cd
                    _cal_dk = f"calendar_alert_{_cd.today().isoformat()}"
                    self.alerts.send(_cal_msg, dedup_key=_cal_dk, dedup_cooldown_override=86400)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Alert mode switch to after-hours
        if _OFFHOURS_AVAIL and self._off_hours:
            try:
                if not getattr(self,"_after_hours_alerted",False):
                    self._after_hours_alerted = True
                    self._off_hours.alert_mode_switch("TRADING","AFTER_HOURS","Market closed")
                elif datetime.now().time() < __import__("datetime").time(15,0):
                    self._after_hours_alerted = False
            except Exception: pass
        self._heartbeat("LEARNING")

        now_ts            = time.time()
        learning_interval = int(getattr(cfg, "STRATEGY_UPDATE_INTERVAL_HOURS", 6)) * 3600
        min_to_open       = _minutes_until_market_open()

        # Skip learning if market opens soon — avoid blocking session start
        if min_to_open < (LEARNING_BLACKOUT_BEFORE_OPEN // 60):
            logger.info(
                "Market opens in %d min — skipping learning cycle to avoid delay",
                min_to_open,
            )
        elif (
            self.last_learning_run_ts is None
            or (now_ts - self.last_learning_run_ts) >= learning_interval
        ):
            logger.info("Running after-hours learning cycle")
            result = self.learning_controller.run_learning_cycle(reason="after_hours")
            self.runtime_state.last_learning_cycle_at        = datetime.now().isoformat()
            self.runtime_state.current_strategy              = str(
                result.get("selected_strategy") or self.runtime_state.current_strategy
            )
            self.runtime_state.diagnostics["last_learning_result"] = result
            self.last_learning_run_ts = now_ts
            self._save_runtime_state()
            self.db.log_event(
                "INFO", "LEARNING",
                f"After-hours learning complete: {result.get('status')}",
            )
            # Record today's IV for IV rank tracking
            if self.gap_risk_manager:
                try:
                    for sym in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
                        fetchers = getattr(self.live_engine, 'option_fetchers', {})
                        if sym in fetchers:
                            oc_result = fetchers[sym].fetch_and_analyze()
                            if oc_result and oc_result.summary:
                                iv = oc_result.summary.get('CE_impliedVolatility', 0)
                                if iv and iv > 0:
                                    self.gap_risk_manager.record_iv_from_option_chain(sym, iv)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Send learning report to Telegram
            self._send_learning_alert(result)

            # Send comprehensive after-hours system report
            self._send_after_hours_report(result)

        # Snapshot after every after-hours window
        self._save_strategy_snapshot()

        # Update market context for next trading day
        self._update_market_context_daily()

        # Refresh NSEMaster if stale (lot sizes change quarterly)
        self._refresh_nse_master_if_stale()

        # Run EOD data feeds (bulk deals, participant OI, tradebook)
        self._run_eod_feeds()

        # Fetch global macro for next session (S&P500 / DXY / Crude).
        # (Removed a dead duplicate that guarded on `_ADV_LIVE_AVAILABLE` via
        #  `'...' in dir()` — a name with no producer, so it never ran and the
        #  `_get_global_macro()` call was unreachable. cross_asset below is the
        #  real, working implementation.)
        try:
            from cross_asset import get_cross_asset_data, get_market_bias
            _macro = get_cross_asset_data(force=True)
            if _macro:
                _sp   = _macro.get("SP500",  {})
                _dxy  = _macro.get("DXY",    {})
                _oil  = _macro.get("BRENT",  {})
                _gold = _macro.get("GOLD",   {})
                _vix  = _macro.get("USVIX",  {})
                _inr  = _macro.get("USDINR", {})
                _ivix = _macro.get("INDIAVIX",{})
                _u10y = _macro.get("US10Y",  {})

                def _fmt(d, decimals=1):
                    if not d or not d.get("price"): return "N/A"
                    chg = d.get("change_pct", 0) or 0
                    arrow = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
                    return f"{arrow}{chg:+.{decimals}f}%"

                def _px(d, decimals=0):
                    if not d or not d.get("price"): return "?"
                    return f"{d['price']:,.{decimals}f}"

                bias = get_market_bias(_macro)
                bias_str = "🟢 BULLISH" if bias > 0.3 else "🔴 BEARISH" if bias < -0.3 else "⚪ NEUTRAL"

                msg = (
                    f"🌍 <b>GLOBAL MACRO UPDATE</b>\n"
                    f"🕐 {__import__('datetime').datetime.now().strftime('%d %b %H:%M')}\n\n"
                    f"  🇺🇸 S&P 500:  {_px(_sp,0):>8} {_fmt(_sp)}\n"
                    f"  💵 DXY:       {_px(_dxy,2):>8} {_fmt(_dxy)}\n"
                    f"  🛢️ Brent:     {_px(_oil,1):>8} {_fmt(_oil)}\n"
                    f"  🥇 Gold:      {_px(_gold,0):>8} {_fmt(_gold)}\n"
                    f"  📊 US VIX:   {_px(_vix,1):>8} {_fmt(_vix)}\n"
                    f"  📈 US10Y:    {_px(_u10y,2):>7}% {_fmt(_u10y)}\n"
                    f"  💱 USD/INR:  {_px(_inr,2):>8} {_fmt(_inr)}\n"
                    f"  🇮🇳 India VIX:{_px(_ivix,1):>8}\n\n"
                    f"  NIFTY Bias: <b>{bias:+.3f}</b> → {bias_str}"
                )
                self.alerts.send(msg,
                    dedup_key="global_macro_update",
                    dedup_cooldown_override=3600,
                )
        except Exception as _gme:
            logger.debug("Global macro update: %s", _gme)

        # Daily summary alert (once per day, after market close)
        self._send_daily_summary_if_needed()

        # Dashboard — at most once per hour
        dashboard_interval = 3600
        if (
            self.last_dashboard_run_ts is None
            or (now_ts - self.last_dashboard_run_ts) >= dashboard_interval
        ):
            try:
                dashboard_path = self.dashboard.generate()
                self.runtime_state.last_dashboard_at                  = datetime.now().isoformat()
                self.runtime_state.diagnostics["last_dashboard_file"] = dashboard_path
                self.last_dashboard_run_ts = now_ts
                self._save_runtime_state()
                self.db.log_event("INFO", "DASHBOARD", f"Dashboard generated: {dashboard_path}")
            except Exception:
                logger.exception("Dashboard generation failed")

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def _apply_order_block(self, blocked: bool, reason: str = "") -> None:
        """
        Keep config and broker order-routing flags aligned. Data connections stay
        live; this only decides whether real orders are allowed.
        """
        changed = False
        try:
            import config as _cfg_ob
            changed = bool(getattr(_cfg_ob, "PAPER_ORDERS_ONLY", False)) != bool(blocked)
            _cfg_ob.PAPER_ORDERS_ONLY = bool(blocked)
            if not blocked:
                _cfg_ob.PAPER_TRADING = False
                _cfg_ob.PAPER_TRADE = False
        except Exception:
            pass

        try:
            broker_manager = (
                getattr(self.live_engine, "broker_manager", None)
                or getattr(self.live_engine, "_broker_manager", None)
            )
            brokers = []
            if broker_manager is not None:
                try:
                    broker = broker_manager.get_execution_broker()
                    if broker is not None:
                        brokers.append(broker)
                except Exception:
                    pass
                brokers.extend(getattr(broker_manager, "brokers", []) or [])

            seen = set()
            for broker in brokers:
                ident = id(broker)
                if ident in seen:
                    continue
                seen.add(ident)
                try:
                    changed = changed or bool(getattr(broker, "block_real_orders", False)) != bool(blocked)
                    broker.block_real_orders = bool(blocked)
                    if hasattr(broker, "angel") and broker.angel:
                        changed = changed or bool(getattr(broker.angel, "block_real_orders", False)) != bool(blocked)
                        broker.angel.block_real_orders = bool(blocked)
                        broker.angel.paper_trade = False
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("_apply_order_block failed: %s", exc)

        _log = logger.info if changed else logger.debug
        _log(
            "Order routing %s | reason=%s",
            "blocked to paper-only" if blocked else "enabled for live orders",
            reason or "unspecified",
        )

    def _sync_runtime_capital(self, capital: float, reset_allocator_peak: bool = False) -> None:
        """Sync account capital across live engine, risk, sizing and allocator."""
        try:
            cap = max(0.0, float(capital or 0.0))
        except Exception:
            cap = 0.0
        if cap <= 0:
            return
        try:
            self.live_engine.total_capital = cap
            self.live_engine.trade_manager.capital = cap
            self.live_engine.risk_manager.capital = cap
            if reset_allocator_peak:
                try:
                    self.live_engine.capital_allocator._peak_capital = cap
                    self.live_engine.capital_allocator._drawdown_mode = False
                    self.live_engine.capital_allocator._initialized = True
                    for bucket in self.live_engine.capital_allocator.buckets.values():
                        bucket.drawdown_halved = False
                    logger.info("CapitalAllocator peak reset to real balance: ₹%.0f", cap)
                except Exception:
                    pass
            self.live_engine.capital_allocator.update_total(cap)
            self.live_engine._peak_equity = cap
        except Exception as exc:
            logger.debug("_sync_runtime_capital failed: %s", exc)

    def _startup(self) -> None:
        # ── Clear stale dedup entries so restart shows all key alerts ────
        try:
            import json as _jd, pathlib as _pd, time as _td
            _df = _pd.Path("dedup_state.json")
            if _df.exists():
                _dd = _jd.loads(_df.read_text())
                # Remove mode/connection/health keys — always show on restart
                _dd = {k: v for k, v in _dd.items()
                       if not any(k.startswith(p) for p in
                                  ("mode:","conn_","health","startup:","reg_"))}
                _df.write_text(_jd.dumps(_dd))
                # Reload in alerts
                if hasattr(self, "alerts"):
                    self.alerts._dedup_sent = self.alerts._load_dedup()
        except Exception: pass

        # ── HEARTBEAT FIRST: tell watchdog we are alive immediately ──────
        # Must happen before any other startup work.
        # Watchdog kills processes with stale heartbeat.
        # A fresh startup has no live_status.json → watchdog sees inf age.
        try:
            import json as _j, pathlib as _pl
            _pl.Path("live_status.json").write_text(_j.dumps({
                "timestamp":    datetime.now().isoformat(),
                "market_phase": "STARTUP",
                "mode":         "PAPER",
                "pid":          __import__("os").getpid(),
            }))
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        if hasattr(cfg, "validate"):
            cfg.validate()

        self.db.log_event("INFO", "STARTUP", "System starting")

        # ── Auto mode: decide paper vs live based on balance ─────────────
        _startup_balance = 0.0
        if _AUTOMODE_AVAILABLE and self.auto_mode:
            try:
                _mode_result = self.auto_mode.evaluate(force=True)
                _startup_balance = _mode_result.get("balance", 0.0)

                # If auto-mode decided LIVE but balance was fetched
                # If balance is 0 (paper/not connected), use config value
                if _startup_balance <= 0:
                    _startup_balance = self._fetch_startup_balance()
                else:
                    # Sync mode to config
                    import config as _cfg_am
                    if _mode_result.get("is_live"):
                        self._apply_order_block(False, "auto_mode_startup_live")
                        self.runtime_state.mode = "LIVE"
                    else:
                        # _cfg_am.PAPER_TRADING = True  # DISABLED: paper_trade kills data fetch

                        _cfg_pm = __import__("config")

                        self._apply_order_block(True, "auto_mode_startup_paper")
                        # _cfg_am.PAPER_TRADE   = True  # DISABLED: use PAPER_ORDERS_ONLY instead
                        self.runtime_state.mode = "PAPER"
                        _paper_cap = float(getattr(_cfg_pm, "PAPER_CAPITAL",
                                           getattr(_cfg_pm, "CAPITAL", 100000)))
                        if _paper_cap > 0:
                            _startup_balance = _paper_cap

                logger.info(
                    "Auto mode decision: %s | balance=₹%.0f | min=₹%.0f",
                    _mode_result.get("mode"), _startup_balance,
                    _mode_result.get("min_capital", 0),
                )
            except Exception as _am_e:
                logger.warning("Auto mode startup failed: %s", _am_e)
                _startup_balance = self._fetch_startup_balance()
        else:
            _startup_balance = self._fetch_startup_balance()

        try:
            import json as _jhb, pathlib as _phb
            _phb.Path("heartbeat.json").write_text(_jhb.dumps({"ts": __import__("time").time()}))
            _phb.Path("live_status.json").write_text(_jhb.dumps({
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "market_phase": "STARTUP",
                "pid": __import__("os").getpid(),
            }))
        except Exception: pass
        # Fetch real balance at startup and sync to all components
        if _startup_balance <= 0:
            _startup_balance = self._fetch_startup_balance()
        _cfg_start = __import__("config")
        if getattr(_cfg_start, "PAPER_ORDERS_ONLY", False):
            _paper_cap = float(getattr(_cfg_start, "PAPER_CAPITAL",
                               getattr(_cfg_start, "CAPITAL", 100000)))
            if _paper_cap > 0 and abs(_startup_balance - _paper_cap) > 1:
                logger.warning(
                    "Startup in PAPER_ORDERS_ONLY — using PAPER_CAPITAL ₹%.0f "
                    "for simulated sizing; live balance ₹%.0f remains real-order blocked.",
                    _paper_cap, _startup_balance,
                )
                _startup_balance = _paper_cap
        if _startup_balance <= 0:
            _cfg_start = __import__("config")
            _startup_balance = float(getattr(_cfg_start, "PAPER_CAPITAL",
                                     getattr(_cfg_start, "CAPITAL", 100000)))
            self._apply_order_block(True, "startup_balance_unavailable")
            logger.warning(
                "Startup balance still ₹0 after all fetch attempts — "
                "using PAPER_CAPITAL ₹%.0f for simulated sizing. "
                "Scanning and paper signals unaffected; real orders blocked.",
                _startup_balance)
        self._sync_runtime_capital(_startup_balance)

        # Connection monitor
        if _CONN_MON:
            try:
                self._conn_monitor = _get_monitor(alerts=self.alerts)
            except Exception: pass

        # Connection monitor
        self._conn_monitor = None
        if _MONITOR_AVAIL:
            try:
                self._conn_monitor = _get_monitor(alerts=self.alerts)
                logger.info("Connection monitor initialised")
            except Exception as _me:
                logger.debug("Monitor init: %s", _me)

        # ── Connection monitor ────────────────────────────────────────────
        if _CONN_MON:
            try:
                self._conn_monitor = _get_conn_monitor(alerts=self.alerts)
                # Full startup check in background thread
                import threading as _th0
                _th0.Thread(
                    target=self._conn_monitor.run_full_check,
                    args=("STARTUP",), daemon=True
                ).start()
            except Exception as _ce:
                logger.debug("ConnectionMonitor init: %s", _ce)

        # Start Telegram command handler (incoming messages)
        if _TGCMD_AVAIL:
            try:
                # Read directly from os.getenv to avoid cfg import-time race
                import os as _os_tg
                _tg_token   = (_os_tg.getenv("TELEGRAM_BOT_TOKEN") or
                               getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or "")
                _tg_chat_id = (_os_tg.getenv("TELEGRAM_CHAT_ID") or
                               getattr(cfg, "TELEGRAM_CHAT_ID", "") or "")
                self._tg_cmd = _TGCmd(
                    bot_token = _tg_token,
                    chat_id   = _tg_chat_id,
                    bot_ref   = self,
                )
                self._tg_cmd.set_command_menu([
                    ("menu", "Open interactive navigation"),
                    ("controlroom", "Live/profit/ML trade gate"),
                    ("status", "Bot, scanner and position status"),
                    ("signals", "Recent qualified signals"),
                    ("positions", "Open positions"),
                    ("pnl", "Gross, charges and net P&L"),
                    ("direction", "Combined index trade direction"),
                    ("help", "All command groups"),
                ])
                self._tg_cmd.start()
                if _CONN_MON and self._conn_monitor:
                    _m = self._conn_monitor
                    def _cmd_conn(_=""):
                        import threading as _thc
                        _thc.Thread(target=_m.run_full_check,
                                    args=("ON-DEMAND",), daemon=True).start()
                        return "Running connection check — results in ~15s"
                    try: self._tg_cmd.register("connections", _cmd_conn)
                    except Exception: pass
                # Wire connection monitor into /connections command
                if _CONN_MON and self._conn_monitor:
                    _mon_ref = self._conn_monitor
                    def _cmd_connections(_=""):
                        _mon_ref.run_full_check("ON-DEMAND")
                        return ""
                    try: self._tg_cmd.register("connections", _cmd_connections)
                    except Exception: pass
                logger.info("Telegram command handler started (/help to list commands)")
                # ── Second command handler for the SEPARATE option bot channel ──
                # Only when OPTION_BOT_TOKEN + OPTION_CHAT_ID are set — otherwise
                # TelegramCommandHandler would fall back to the main token and two
                # pollers would conflict on getUpdates. Gives the option channel
                # the same interactive commands as the main bot (/status /pnl
                # /positions /health /help …) plus an option-focused /signals.
                _opt_token = (_os_tg.getenv("OPTION_BOT_TOKEN") or "").strip()
                _opt_chat  = (_os_tg.getenv("OPTION_CHAT_ID") or "").strip()
                if _opt_token and _opt_chat and _opt_token != _tg_token:
                    try:
                        self._tg_cmd_option = _TGCmd(
                            bot_token=_opt_token, chat_id=_opt_chat, bot_ref=self)

                        def _cmd_opt_signals(_=""):
                            """Recent DISTINCT live option selections — deduped (the journal
                            re-records the same setup across scans) + most recent first."""
                            from option_bot_views import generated_signals_text
                            return generated_signals_text()
                            # Legacy journal-selection view retained below for
                            # compatibility reference; generated signals are the
                            # authoritative option-bot source.
                            try:
                                import json as _oj
                                from option_decision_journal import (
                                    _is_research_strategy, DEFAULT_JOURNAL_FILE)
                                seen, distinct, dupes = set(), [], 0
                                try:
                                    with open(DEFAULT_JOURNAL_FILE) as _f:
                                        for _ln in _f:
                                            try: _d = _oj.loads(_ln)
                                            except Exception: continue
                                            if not (str(_d.get("decision", "")).startswith("selected")
                                                    and not _is_research_strategy(_d.get("strategy"))):
                                                continue
                                            _s = _d.get("selected") or {}
                                            key = (str(_d.get("time", ""))[:16], _d.get("symbol"),
                                                   _d.get("side"), _s.get("strike"), _s.get("option_type"))
                                            if key in seen:
                                                dupes += 1
                                                continue
                                            seen.add(key); distinct.append(_d)
                                except FileNotFoundError:
                                    return "No option journal yet."
                                if not distinct:
                                    return "No live option selections yet."
                                recent = distinct[-5:][::-1]   # newest first
                                out = [f"🎯 <b>Recent option selections</b> ({len(distinct)} distinct)"]
                                for _d in recent:
                                    _s = _d.get("selected") or {}
                                    out.append(
                                        f"  {str(_d.get('time',''))[:16]} {_d.get('symbol')} "
                                        f"{_d.get('side')} {_s.get('strike','')}{(_s.get('option_type') or '')} "
                                        f"({_d.get('strategy')})")
                                if dupes:
                                    out.append(f"  <i>({dupes} duplicate records collapsed)</i>")
                                return "\n".join(out)
                            except Exception as _se:
                                return f"option /signals error: {_se}"

                        try: self._tg_cmd_option.register("signals", _cmd_opt_signals)
                        except Exception: pass

                        def _cmd_opt_all(_=""):
                            try:
                                from option_bot_views import consolidated_eod_text
                                return consolidated_eod_text()
                            except Exception as _ae:
                                return f"option /all error: {_ae}"
                        for _a in ("all", "optionall", "eodall"):
                            try: self._tg_cmd_option.register(_a, _cmd_opt_all)
                            except Exception: pass

                        def _cmd_opt_status(_=""):
                            """Option-bot status: index scope + today's option activity
                            (NOT the 196-symbol equity universe)."""
                            try:
                                import json as _oj2, sqlite3 as _sq2
                                from datetime import date as _date2
                                from option_decision_journal import (
                                    _is_research_strategy, DEFAULT_JOURNAL_FILE)
                                try:
                                    from live_signal_engine import SUPPORTED_OPTION_UNDERLYINGS as _UND
                                    unders = ", ".join(sorted(_UND))
                                except Exception:
                                    unders = "NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX"
                                _td = _date2.today().isoformat()
                                total = sel = blocked = 0
                                try:
                                    with open(DEFAULT_JOURNAL_FILE) as _f2:
                                        for _ln2 in _f2:
                                            try: _d2 = _oj2.loads(_ln2)
                                            except Exception: continue
                                            if not str(_d2.get("time", "")).startswith(_td):
                                                continue
                                            total += 1
                                            _dec = str(_d2.get("decision", ""))
                                            if _dec.startswith("selected") and not _is_research_strategy(_d2.get("strategy")):
                                                sel += 1
                                            elif _dec.startswith("blocked"):
                                                blocked += 1
                                except FileNotFoundError:
                                    pass
                                opos = 0; opnl = 0.0
                                try:
                                    _c = _sq2.connect("file:trades.db?mode=ro", uri=True, timeout=5)
                                    _optf = "(symbol GLOB '*[0-9]CE' OR symbol GLOB '*[0-9]PE')"
                                    opos = _c.execute(
                                        f"SELECT COUNT(*) FROM trades WHERE status='OPEN' AND {_optf}").fetchone()[0]
                                    _r = _c.execute(
                                        "SELECT COALESCE(SUM(realized_pnl),0) FROM trades WHERE "
                                        "date(entry_time,'unixepoch','+5 hours','30 minutes')=? AND " + _optf,
                                        (_td,)).fetchone()
                                    opnl = float(_r[0] or 0); _c.close()
                                except Exception:
                                    pass
                                return ("📊 <b>OPTION BOT STATUS</b>\n"
                                        f"  Underlyings: {unders}\n"
                                        f"  Today: {total} decisions | {sel} selected (live) | {blocked} blocked\n"
                                        f"  Open option positions: {opos}\n"
                                        f"  Option P&L today: ₹{opnl:+.0f}\n"
                                        f"  Mode: PAPER")
                            except Exception as _ste:
                                return f"option /status error: {_ste}"

                        try: self._tg_cmd_option.register("status", _cmd_opt_status)
                        except Exception: pass

                        def _cmd_opt_edge(_=""):
                            """Option worthiness digest (labelled outcomes + live count)."""
                            try:
                                from option_decision_journal import (
                                    option_performance_summary, format_option_summary)
                                return format_option_summary(option_performance_summary(days=400))
                            except Exception as _ee:
                                return f"option /optedge error: {_ee}"
                        for _a in ("optedge", "edge", "worthiness"):
                            try: self._tg_cmd_option.register(_a, _cmd_opt_edge)
                            except Exception: pass

                        def _cmd_opt_positions(_=""):
                            """Open option positions from trades.db (option symbols only)."""
                            try:
                                import sqlite3 as _sqp
                                _cp = _sqp.connect("file:trades.db?mode=ro", uri=True, timeout=5)
                                _of = "(symbol GLOB '*[0-9]CE' OR symbol GLOB '*[0-9]PE')"
                                _rows = _cp.execute(
                                    f"SELECT symbol,side,qty,entry_price FROM trades "
                                    f"WHERE status='OPEN' AND {_of} ORDER BY entry_time DESC LIMIT 15"
                                ).fetchall()
                                _cp.close()
                                if not _rows:
                                    return "📭 No open option positions."
                                _out = ["📌 <b>Open option positions</b>"]
                                for _sy, _sd, _qt, _ep in _rows:
                                    _out.append(f"  {_sy} {_sd} x{_qt} @ ₹{float(_ep or 0):.1f}")
                                return "\n".join(_out)
                            except Exception as _pe:
                                return f"option /optpositions error: {_pe}"
                        for _a in ("optpositions", "positions"):
                            try: self._tg_cmd_option.register(_a, _cmd_opt_positions)
                            except Exception: pass

                        def _cmd_opt_lots(args=""):
                            """Set option lot ceiling for today (live, no restart).
                            /optlots 2 → cap at 2 lots; /optlots auto (or 0) → clear;
                            /optlots → show current."""
                            try:
                                from option_lot_override import (
                                    set_lots_override, clear_lots_override, status_text)
                                a = str(args or "").strip().split()
                                arg = a[1] if len(a) > 1 else ""
                                if not arg:
                                    return (status_text() +
                                            "\n  Usage: /optlots 1|2|3  ·  /optlots auto")
                                if arg.lower() in ("auto", "off", "clear", "0"):
                                    clear_lots_override()
                                    return "🎚️ Option lots → <b>AUTO</b> (capital/confidence sized)"
                                if not arg.lstrip("-").isdigit():
                                    return "⚠️ Usage: /optlots 1|2|3  (or /optlots auto)"
                                st = set_lots_override(int(arg))
                                if not st.get("active"):
                                    return "🎚️ Option lots → <b>AUTO</b>"
                                return (f"✅ Option lots set to <b>{st['lots']}</b> for today "
                                        f"(ceiling; still bounded by capital + MAX_LOTS).\n"
                                        f"Auto-resets tomorrow.")
                            except Exception as _le:
                                return f"⚠️ /optlots error: {_le}"
                        for _a in ("optlots", "lots", "setlots"):
                            try: self._tg_cmd_option.register(_a, _cmd_opt_lots)
                            except Exception: pass

                        def _cmd_opt_help(_=""):
                            return (
                                "🎯 <b>OPTION BOT — COMMANDS</b>\n\n"
                                "📊 <b>REPORTS</b>\n"
                                "  /report — post-market visual dashboard\n"
                                "  /all — generated, lifecycle, ideal and traded P&L\n"
                                "  /status — today's option summary\n"
                                "  /signals — recent option selections\n"
                                "  /positions — open option positions\n"
                                "  /edge — labelled option performance\n\n"
                                "📈 <b>OI &amp; MARKET</b>\n"
                                "  /oisr — support/resistance image\n"
                                "  /oichart — intraday OI line chart\n"
                                "  /strikeflow — active CE/PE strikes\n"
                                "  /pcr — put/call ratio\n\n"
                                "🎚️ <b>CONTROL</b>\n"
                                "  /optlots 1|2|3 — today's lot ceiling\n"
                                "  /optlots auto — automatic sizing\n"
                                "  /pause  /resume\n\n"
                                "Reports are automatically posted after 3:35 PM IST."
                            )
                        try: self._tg_cmd_option.register("help", _cmd_opt_help)
                        except Exception: pass

                        def _cmd_opt_report(_=""):
                            """Anytime levels/status table, plus visual dashboard post-market."""
                            try:
                                from option_bot_views import anytime_report_table
                                from option_telegram_report import (
                                    generate_option_report, is_post_market)
                                if not is_post_market():
                                    return anytime_report_table()
                                _rep = generate_option_report()
                                uploaded = self._tg_cmd_option.send_photo(
                                    _rep["path"], _rep["caption"])
                                table = anytime_report_table()
                                return table if uploaded else (
                                    table + "\n⚠️ Visual dashboard upload failed."
                                )
                            except Exception as _re:
                                return f"⚠️ /report error: {str(_re)[:100]}"
                        try: self._tg_cmd_option.register("report", _cmd_opt_report)
                        except Exception: pass

                        # Curate the option channel: keep only option/index-relevant
                        # commands so it stops inheriting the full equity menu.
                        _OPT_ALLOWED = {
                            "help", "menu", "start", "status", "report", "signals", "all", "optionall", "eodall",
                            "optedge", "edge", "optpositions", "positions",
                            "controlroom", "readiness", "profitgate",
                            "optionedge", "optionhealth", "optlots", "oisr", "oichart", "strikeflow", "pcr",
                            "spreads",
                            "direction", "tradeview", "view", "nexttrade",
                            "pause", "resume",
                        }
                        try:
                            self._tg_cmd_option.restrict_to(_OPT_ALLOWED)
                            self._tg_cmd_option.set_navigation_profile("option")
                        except Exception: pass

                        try:
                            self._tg_cmd_option.set_command_menu([
                                ("menu", "Open option navigation"),
                                ("report", "Anytime option levels and status"),
                                ("controlroom", "Live/profit/ML trade gate"),
                                ("all", "All signals, lifecycle and P&L"),
                                ("direction", "Combined option trade direction"),
                                ("status", "Today's option bot summary"),
                                ("signals", "All generated option signals"),
                                ("positions", "Open option positions"),
                                ("help", "Grouped option commands"),
                            ])
                        except Exception:
                            pass

                        self._tg_cmd_option.start()
                        logger.info("Option Telegram command handler started (curated %d cmds)",
                                    len(self._tg_cmd_option._handlers))
                    except Exception as _oe:
                        logger.warning("Option TG command handler: %s", _oe)
                elif _opt_token and _opt_chat and _opt_token == _tg_token:
                    logger.error(
                        "Option bot disabled: OPTION_BOT_TOKEN equals "
                        "TELEGRAM_BOT_TOKEN; two getUpdates pollers cannot share a token."
                    )
            except Exception as _tge:
                logger.warning("TG command handler: %s", _tge)

        # Off-hours engine
        if _OFFHOURS_AVAIL:
            try:
                self._off_hours = _OffHours(bot_ref=self, alerts=self.alerts)
                # Fetch fresh NSE holiday list
                global NSE_HOLIDAYS_2026
                _fresh_holidays = fetch_nse_holidays()
                if _fresh_holidays:
                    import off_hours_engine as _ohe
                    _ohe.NSE_HOLIDAYS_2026 = _fresh_holidays
            except Exception as _ohe:
                logger.debug("OffHours init: %s", _ohe)


        # ── Startup connection health check ──────────────────────────────────
        if _CONN_MON_AVAIL:
            try:
                import config as _cfg_cm
                _cm = _get_conn_monitor(alerts=self.alerts, config=_cfg_cm)
                _cm.start_background()
                # Run full check + send Telegram — ONCE per 30 min max
                import time as _t_cm
                # Always run connection check on startup
                _cm.run_full_check(label="STARTUP")
            except Exception as _cme:
                logger.debug("Startup health check: %s", _cme)

        # ── Regulatory change alert (April 1, 2026) ──────────────────────────
        try:
            from datetime import date as _date
            _today = _date.today()
            if _today >= _date(2026, 4, 1):
                _reg_key = f"reg_alert_2026q1_{_today.year}"
                if not self.alerts._is_dedup_blocked(_reg_key, 86400 * 30):
                    self.alerts.regulatory_update(
                        changes=[
                            "💰 STT HIKE (Budget 2026 — effective Apr 1, 2026):",
                            "   Options sell: 0.10% → 0.15% (+50%)",
                            "   Futures sell: 0.02% → 0.05% (+150%)",
                            "   ✅ System STT rates updated",
                            "",
                            "📦 LOT SIZE CHANGES (NSE Oct 2025 circular):",
                            "   NIFTY: 75 → 65",
                            "   BANKNIFTY: 15 → 30",
                            "   FINNIFTY: 40 → 65",
                            "   SENSEX: 10 → 20",
                            "   ✅ System lot sizes updated",
                            "",
                            "📅 WEEKLY EXPIRY (SEBI Nov 2024):",
                            "   NSE: Only NIFTY has weekly (Thu)",
                            "   BSE: Only SENSEX has weekly (Fri)",
                            "   BANKNIFTY/FINNIFTY/MIDCPNIFTY: monthly only",
                            "   ✅ System expiry logic updated",
                        ],
                        effective_date="01-Apr-2026",
                    )
                    self.alerts._mark_dedup_sent(_reg_key)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Run startup health check
        if _MONITOR_AVAIL and self._conn_monitor:
            try:
                import threading as _thr
                def _startup_check():
                    _res = self._conn_monitor.run_full_check()
                    # Send startup report via alerts
                    try:
                        ok  = sum(1 for r in _res if r.ok)
                        wrn = sum(1 for r in _res if not r.ok and not r.critical)
                        fail= sum(1 for r in _res if not r.ok and r.critical)
                        self.alerts.send(
                            f"\U0001F7E2 STARTUP CHECK\n"
                            f"  OK: {ok}  Warn: {wrn}  Failed: {fail}\n"
                            + ("  \u2705 Ready for 9:15 AM" if fail==0 else
                               "  \u274c ACTION REQUIRED:\n"
                               + "\n".join(f"    \u274c {r.label}\n       {r.detail}"
                                            for r in _res if not r.ok and r.critical))
                        )
                    except Exception: pass
                _thr.Thread(target=_startup_check, daemon=True).start()
            except Exception: pass

        # Send restart/resume message based on previous state
        # Startup connection check
        if _CONN_MON:
            try:
                import threading as _thr2
                _thr2.Thread(
                    target=lambda: _get_monitor(self.alerts).run_full_check("STARTUP"),
                    daemon=True
                ).start()
            except Exception: pass

        if _SYSSTATE:
            try:
                _ss = _get_sys_state()
                _prev = _ss.get_state()
                if _prev not in ("STARTUP",""):
                    # Was running something — send resume alert
                    self.alerts.send(_ss.resume_message())
                _ss.set("STARTUP", "Initializing all modules")
            except Exception: pass

        self.alerts.startup(
            bot_name   = getattr(cfg, 'BOT_NAME', 'Autonomous Trading Bot'),
            mode       = self.runtime_state.mode,
            capital    = _startup_balance,
            symbols    = len(getattr(self.live_engine.data_fetcher, 'nifty_200', []) or []) or 200,
            strategies = len(__import__('signal_engine').STRATEGIES),
        )
        try:
            import json as _jhb, pathlib as _phb
            _phb.Path("heartbeat.json").write_text(_jhb.dumps({"ts": __import__("time").time()}))
            _phb.Path("live_status.json").write_text(_jhb.dumps({
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "market_phase": "STARTUP",
                "pid": __import__("os").getpid(),
            }))
        except Exception: pass
        # Daily download report at 8:00 PM
        try:
            from datetime import time as _dtr
            if _dtr(20,0) <= datetime.now().time() <= _dtr(20,5):
                if _OFFHOURS_AVAIL and self._off_hours and not getattr(self,"_dl_report_sent",False):
                    self._dl_report_sent = True
                    self._off_hours.send_daily_download_report()
            elif datetime.now().time() < _dtr(8,0):
                self._dl_report_sent = False
        except Exception: pass

        # Daily data reliability report at 8:05 PM
        try:
            from datetime import time as _dt805
            if _dt805(20,5) <= datetime.now().time() <= _dt805(20,10):
                if _CONN_MON and self._conn_monitor:
                    if not getattr(self,"_data_report_sent",False):
                        self._data_report_sent = True
                        self._conn_monitor.daily_data_report()
            elif datetime.now().time() < _dt805(8,0):
                self._data_report_sent = False
        except Exception: pass

        # ── 8:05 PM data reliability report ─────────────────────────────
        try:
            from datetime import time as _dt805
            if _dt805(20,5) <= datetime.now().time() <= _dt805(20,10):
                if _CONN_MON and self._conn_monitor:
                    _rk = f"data_report_{date.today()}"
                    if not getattr(self, _rk, False):
                        setattr(self, _rk, True)
                        self._conn_monitor.daily_report()
        except Exception: pass

        # Weekly download report on Fridays at 7:00 PM
        try:
            if date.today().weekday() == 4:  # Friday
                from datetime import time as _dtrw
                if _dtrw(19,0) <= datetime.now().time() <= _dtrw(19,5):
                    if _OFFHOURS_AVAIL and self._off_hours:
                        self._off_hours.send_weekly_download_report()
        except Exception: pass

        # Update intraday time-bucket profile from today's trades
        try:
            from intraday_profile import update_profile_from_trades as _upt
            import sqlite3 as _sq
            _conn = _sq.connect(getattr(__import__("config"),"TRADES_DB","trades.db"))
            _rows = _conn.execute(
                "SELECT strategy,net_pnl,entry_time FROM trades WHERE status='CLOSED' "
                "AND date(exit_time,'unixepoch','localtime')=date('now')"
            ).fetchall()
            _conn.close()
            _upt([{"strategy":r[0],"net_pnl":r[1],"entry_time":r[2]} for r in _rows])
        except Exception: pass

        # Auto-update Nifty 200 constituents if stale
        try:
            from data_fetcher import auto_update_nifty200
            if auto_update_nifty200():
                logger.debug("Nifty200 list is current")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Dual mode engine (paper always + live when funded)
        self._dual_engine = None
        if _DUAL_MODE_AVAILABLE:
            try:
                self._dual_engine = _get_dual_engine(
                    broker_manager = self.live_engine.broker_manager,
                    alerts         = self.alerts,
                )
                _initial = self._dual_engine.run_balance_check(force=True)
                if _initial.get("live_enabled"):
                    self._apply_order_block(False, "dual_mode_startup_funded")
                    self._sync_runtime_capital(
                        float(_initial.get("balance", 0) or 0),
                        reset_allocator_peak=True,
                    )
                    self.runtime_state.mode = "LIVE"
                else:
                    self._apply_order_block(True, "dual_mode_startup_unfunded")
                    self.runtime_state.mode = "PAPER"
                logger.info(
                    "Dual mode init: %s | balance=₹%.0f",
                    _initial.get("mode"), _initial.get("balance",0)
                )
            except Exception as _de:
                logger.debug("Dual mode init: %s", _de)

        try:
            import json as _jhb, pathlib as _phb
            _phb.Path("heartbeat.json").write_text(_jhb.dumps({"ts": __import__("time").time()}))
            _phb.Path("live_status.json").write_text(_jhb.dumps({
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "market_phase": "STARTUP",
                "pid": __import__("os").getpid(),
            }))
        except Exception: pass
        # Overnight protection manager
        if _OVERNIGHT_AVAILABLE:
            try:
                self._overnight_prot = _get_overnight_prot(
                    trade_manager = self.live_engine.trade_manager,
                    alerts        = self.alerts,
                )
                logger.info("Overnight protection manager initialised")
            except Exception as _oe:
                logger.debug("Overnight protection: %s", _oe)

        # Start remote monitoring dashboard
        if _REMOTE_DASH_AVAILABLE:
            try:
                self._remote_url = _start_remote_dashboard()
                if self._remote_url:
                    logger.info("Remote dashboard: %s", self._remote_url)
            except Exception as _rde:
                logger.debug("Remote dashboard: %s", _rde)

        _append_jsonl(
            MAIN_LIVE_LOG,
            {
                "timestamp": datetime.now().isoformat(),
                "event":     "STARTUP",
                "mode":      self.runtime_state.mode,
            },
        )

        # Single bootstrap call — returns True when valid state was found
        # (either from backup or existing strategy_state.json).
        # Only triggers full retraining when no valid state exists.
        restored = self._restore_from_backup()

        if restored:
            logger.info(
                "Bootstrapped from existing state | strategy=%s backup=%s",
                self.runtime_state.current_strategy,
                self.runtime_state.last_backup_file,
            )
            self.db.log_event(
                "INFO", "RESTORE",
                f"Bootstrap complete: {self.runtime_state.last_backup_file}",
            )
        else:
            logger.warning("No valid state found — fresh start, retraining required")
            self.db.log_event("WARN", "FRESH_START", "No backup state — retraining")

        # Initialise day tracking
        if _is_trading_day():
            today = date.today()
            self._last_trading_date = today
            reset_date = getattr(self, "_persisted_new_day_reset_date", None)
            reset_marker = getattr(self, "_persisted_new_day_reset_marker", None)
            if reset_date is None:
                reset_date, reset_marker = self._load_last_new_day_reset_marker()
            if reset_date == today:
                self.runtime_state.last_new_day_reset_at = reset_marker
                logger.info(
                    "Daily state already reset for %s — skipping startup reset",
                    today.isoformat(),
                )
            else:
                self._on_new_trading_day()


    # ------------------------------------------------------------------
    # High-impact event day handling
    # ------------------------------------------------------------------
    def _apply_high_impact_day_overrides(self) -> None:
        """
        On RBI MPC days, Budget day, and other high-impact events:
        - Reduce max_lots to 1 (cap from live engine config)
        - Require minimum confidence of HIGH_IMPACT_CONFIDENCE_MIN (0.80)
          by temporarily setting it in the AI filter threshold

        Overrides are applied on every LIVE-phase cycle while the date
        matches. They reset automatically at midnight (new trading day).
        """
        _HID = globals().get("HIGH_IMPACT_DATES", set())
        today = date.today()
        if today not in _HID:
            # Restore normal limits if previously overridden
            if getattr(self, "_high_impact_overrides_active", False):
                self._restore_normal_day_limits()
            return

        if getattr(self, "_high_impact_overrides_active", False):
            return   # Already applied this day

        logger.warning(
            "HIGH-IMPACT EVENT DAY: %s — applying conservative position limits "
            "(max_lots=1, confidence_min=%.2f)",
            today.isoformat(), globals().get("HIGH_IMPACT_CONFIDENCE_MIN", 0.80),
        )

        # Store original values for restoration
        self._saved_max_lots = getattr(
            self.live_engine.option_selector, "max_lots_per_trade", 3
        )

        # Apply reduced limits
        if hasattr(self.live_engine, "option_selector"):
            self.live_engine.option_selector.max_lots_per_trade = 1

        self._high_impact_overrides_active = True
        self.runtime_state.diagnostics["high_impact_day"] = str(today)

    def _restore_normal_day_limits(self) -> None:
        """Restore standard limits after a high-impact day."""
        if hasattr(self.live_engine, "option_selector"):
            saved = getattr(self, "_saved_max_lots", 3)
            self.live_engine.option_selector.max_lots_per_trade = saved

        self._high_impact_overrides_active = False
        self.runtime_state.diagnostics.pop("high_impact_day", None)
        logger.info("High-impact day overrides restored to normal limits")



    def _notify_mode_change(self, new_phase: str) -> None:
        """
        Send Telegram alert when the system changes operational mode.
        Only fires when the phase actually changes — not on every heartbeat.
        """
        old_phase = self._last_market_phase
        if old_phase == new_phase:
            return
        self._last_market_phase = new_phase

        # Phases we always alert on
        alert_transitions = {
            ("LIVE",     "LEARNING"),
            ("LEARNING", "LIVE"),
            ("LIVE",     "STOPPED"),
            ("LIVE",     "CRASHED"),
            ("PAPER",    "LIVE"),
            ("BOOT",     "LIVE"),
            ("BOOT",     "PAPER"),
            ("BOOT",     "HOLIDAY"),
            ("LIVE",     "HOLIDAY"),
            ("HOLIDAY",  "LIVE"),
            ("LEARNING", "BACKTEST"),
            ("BACKTEST", "LEARNING"),
        }
        from_up = old_phase.upper()
        to_up   = new_phase.upper()

        should_alert = (
            (from_up, to_up) in alert_transitions
            or to_up in ("CRASHED", "STOPPED", "KILL_SWITCH",
                         "HOLIDAY", "BACKTEST", "ML_TRAINING")
            or (to_up == "LIVE"     and from_up != "LIVE")
            or (to_up == "HOLIDAY"  and from_up not in ("HOLIDAY",))
            or (to_up == "LEARNING" and from_up == "LIVE")
        )
        if not should_alert:
            return

        try:
            td = self.live_engine.trade_manager.summary()
            self.alerts.mode_change(
                from_mode    = old_phase,
                to_mode      = new_phase,
                reason       = self._phase_reason(old_phase, new_phase),
                daily_pnl    = td.get("daily_realized_pnl"),
                trades_today = td.get("closed_positions", 0),
            )
        except Exception:
            logger.debug("_notify_mode_change alert failed", exc_info=True)

    def _phase_reason(self, from_phase: str, to_phase: str) -> str:
        reasons = {
            ("LIVE",     "LEARNING"): "Market closed — entering after-hours learning",
            ("LEARNING", "LIVE"):     "Market opened — resuming live trading",
            ("BOOT",     "LIVE"):     "System started — entering live trading",
            ("BOOT",     "PAPER"):    "System started — paper trading mode",
            ("BOOT",     "HOLIDAY"):  "System started on NSE market holiday",
            ("LIVE",     "HOLIDAY"):  "NSE market holiday detected",
            ("HOLIDAY",  "LIVE"):     "Holiday over — resuming trading",
            ("LEARNING", "BACKTEST"): "Starting nightly backtest",
            ("BACKTEST", "LEARNING"): "Backtest complete — resuming learning",
            ("LIVE",     "STOPPED"):  "System shutting down",
            ("LIVE",     "CRASHED"):  "Fatal error — crash recovery active",
        }
        return reasons.get(
            (from_phase.upper(), to_phase.upper()),
            f"Phase transition: {from_phase} → {to_phase}",
        )

    # ------------------------------------------------------------------
    # Telegram alert helpers — called from the main loop
    # ------------------------------------------------------------------

    def _send_intraday_brief(self) -> None:
        """
        Build and send the SAHI-style intraday brief.
        Fetches: option chain, PCR, FII, VIX, S/R levels.
        """
        try:
            from datetime import date as _d2
            import requests as _rq, time as _t2

            data = {}

            # ── 1. VIX ────────────────────────────────────────────────────
            try:
                import yf_compat as _yf
                _vdf = _yf.download("^INDIAVIX", period="5d", interval="1d",
                                     progress=False, auto_adjust=True)
                if _vdf is not None and len(_vdf) > 0:
                    _vc = _vdf["Close"]
                    if hasattr(_vc,"columns"): _vc = _vc.iloc[:,0]
                    data["iv"] = float(_vc.iloc[-1])
            except Exception: pass

            # ── 2. NIFTY spot ─────────────────────────────────────────────
            try:
                import yf_compat as _yf2
                _ndf = _yf2.download("^NSEI", period="1d", interval="5m",
                                      progress=False, auto_adjust=True)
                if _ndf is not None and len(_ndf) > 0:
                    _nc = _ndf["Close"]
                    if hasattr(_nc,"columns"): _nc = _nc.iloc[:,0]
                    data["spot"] = float(_nc.iloc[-1])
            except Exception: pass

            # ── 3. NSE Option Chain ───────────────────────────────────────
            try:
                _s = _rq.Session()
                _s.headers.update({"User-Agent":"Mozilla/5.0",
                                    "Referer":"https://www.nseindia.com/"})
                _s.get("https://www.nseindia.com/", timeout=6)
                _r = _s.get(
                    "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
                    timeout=12)
                if _r.status_code == 200:
                    _oc = _r.json()
                    _records = _oc.get("records",{})
                    _data    = _records.get("data",[])
                    data["spot"] = data.get("spot") or float(_records.get("underlyingValue",0))

                    # Aggregate call/put OI by strike
                    _call_oi_by_strike = {}
                    _put_oi_by_strike  = {}
                    _total_call_oi = 0
                    _total_put_oi  = 0
                    _call_oi_chg   = 0
                    _put_oi_chg    = 0

                    for _row in _data:
                        _strike = float(_row.get("strikePrice",0))
                        if "CE" in _row:
                            _coi = float(_row["CE"].get("openInterest",0))
                            _cchg= float(_row["CE"].get("changeinOpenInterest",0))
                            _call_oi_by_strike[_strike] = _coi
                            _total_call_oi += _coi
                            _call_oi_chg   += _cchg
                        if "PE" in _row:
                            _poi = float(_row["PE"].get("openInterest",0))
                            _pchg= float(_row["PE"].get("changeinOpenInterest",0))
                            _put_oi_by_strike[_strike] = _poi
                            _total_put_oi += _poi
                            _put_oi_chg   += _pchg

                    # Max OI strikes (pin zones)
                    if _call_oi_by_strike:
                        data["max_call_strike"] = max(_call_oi_by_strike, key=_call_oi_by_strike.get)
                    if _put_oi_by_strike:
                        data["max_put_strike"]  = max(_put_oi_by_strike,  key=_put_oi_by_strike.get)

                    # OI in lakhs
                    data["total_call_oi"] = _total_call_oi / 100000
                    data["total_put_oi"]  = _total_put_oi  / 100000
                    data["call_oi_chg"]   = _call_oi_chg   / 100000
                    data["put_oi_chg"]    = _put_oi_chg    / 100000

                    # PCR
                    data["pcr"] = _total_put_oi / max(_total_call_oi, 1)

                    # Support / resistance from OI walls
                    _spot = data.get("spot", 0)
                    if _spot:
                        # Resistance = nearest call OI wall ABOVE spot
                        _above_calls = {k:v for k,v in _call_oi_by_strike.items() if k > _spot}
                        if _above_calls:
                            data["resistance"] = max(_above_calls, key=_above_calls.get)
                        # Support = nearest put OI wall BELOW spot
                        _below_puts = {k:v for k,v in _put_oi_by_strike.items() if k < _spot}
                        if _below_puts:
                            data["support"] = max(_below_puts, key=_below_puts.get)

                    # Put / call writer zones
                    if data.get("max_put_strike"):
                        _pw = data["max_put_strike"]
                        data["put_writer_low"]  = _pw - 200
                        data["put_writer_high"] = _pw
                    if data.get("max_call_strike"):
                        data["call_writer"] = data["max_call_strike"]

            except Exception as _oce:
                logger.debug("Option chain fetch: %s", _oce)

            # ── 4. FII bias ────────────────────────────────────────────────
            try:
                from participant_oi import get_participant_data
                _pd = get_participant_data()
                if _pd:
                    _fii_long  = _pd.get("FII",{}).get("long",0)
                    _fii_short = _pd.get("FII",{}).get("short",0)
                    _net = _fii_long - _fii_short
                    data["fii_bias"] = "Bullish" if _net > 0 else "Bearish" if _net < 0 else "Neutral"
            except Exception: pass

            # ── 5. Regime ─────────────────────────────────────────────────
            try:
                from market_regime import get_regime_engine
                _re = get_regime_engine()
                data["regime"] = _re.regime
            except Exception: pass

            # ── 6. Range from yesterday's PDH/PDL + weekly levels ────────
            try:
                from data_fetcher import DataFetcher
                _ndf2 = _get_angel_data_fetcher().get_market_data("NIFTY", days=5)
                if _ndf2 is not None and len(_ndf2) >= 2:
                    _ph = float(_ndf2["high"].iloc[-2]) if "high" in _ndf2.columns else 0
                    _pl = float(_ndf2["low"].iloc[-2])  if "low"  in _ndf2.columns else 0
                    data["range_high"] = data.get("resistance") or _ph
                    data["range_low"]  = data.get("support")    or _pl
                    # Demand zone = support - 200 to support
                    if data.get("support"):
                        data["demand_zone_low"]  = data["support"] - 200
                        data["demand_zone_high"] = data["support"]
            except Exception: pass

            # ── 7. Day type ───────────────────────────────────────────────
            try:
                from expiry_regime import get_expiry_regime
                _er = get_expiry_regime()
                if _er.get("is_expiry"): data["day_type"] = "📆 EXPIRY DAY"
                elif _er.get("dte",99) <= 1: data["day_type"] = "📆 Expiry tomorrow"
            except Exception: pass

            # ── Send it ───────────────────────────────────────────────────
            if any(data.values()):
                self.alerts.nifty_intraday_brief(
                    date_str        = datetime.now().strftime("%d %b %Y"),
                    spot            = data.get("spot", 0),
                    range_high      = data.get("range_high", 0),
                    range_low       = data.get("range_low", 0),
                    demand_zone_high= data.get("demand_zone_high", 0),
                    demand_zone_low = data.get("demand_zone_low", 0),
                    resistance      = data.get("resistance", 0),
                    support         = data.get("support", 0),
                    total_call_oi   = data.get("total_call_oi", 0),
                    call_oi_chg     = data.get("call_oi_chg", 0),
                    max_call_strike = data.get("max_call_strike", 0),
                    total_put_oi    = data.get("total_put_oi", 0),
                    put_oi_chg      = data.get("put_oi_chg", 0),
                    max_put_strike  = data.get("max_put_strike", 0),
                    pcr             = data.get("pcr", 0),
                    iv              = data.get("iv", 0),
                    put_writer_low  = data.get("put_writer_low", 0),
                    put_writer_high = data.get("put_writer_high", 0),
                    call_writer     = data.get("call_writer", 0),
                    fii_bias        = data.get("fii_bias",""),
                    regime          = data.get("regime",""),
                    day_type        = data.get("day_type",""),
                )
                logger.info("Intraday brief sent")

        except Exception as _e:
            logger.warning("_send_intraday_brief: %s", _e)

    def _send_market_open_if_needed(self) -> None:
        """Fire market_open alert once per trading day."""
        today_key = f"mkt_open:{date.today().isoformat()}"
        if self.alerts._is_dedup_blocked(today_key, 86400):
            return
        try:
            td = self.live_engine.trade_manager.summary()
            # Fetch prev closes for all indices
            _idx_closes = {}
            for _isym, _itk in [("NIFTY","^NSEI"),("BANKNIFTY","^NSEBANK"),("SENSEX","^BSESN")]:
                try:
                    import yf_compat as _yf2
                    _idf = _yf2.download(_itk, period="2d", interval="1d",
                                         progress=False, auto_adjust=True)
                    if _idf is not None and len(_idf) >= 1:
                        _idx_closes[_isym] = float(_idf["Close"].iloc[-1])
                except Exception: pass
            if _SYSSTATE:
                try: _get_sys_state().set("TRADING", f"Scanning {len(getattr(self.live_engine.data_fetcher,'nifty_200',[]) or [])} symbols")
                except Exception: pass
            self.alerts.market_open(
                date_str             = date.today().strftime("%d %b %Y"),
                strategy             = self.runtime_state.current_strategy,
                capital              = float(getattr(cfg, "CAPITAL",
                                         getattr(cfg, "PAPER_CAPITAL", 100000))),
                mode                 = self.runtime_state.mode,
                is_high_impact       = date.today() in globals().get("HIGH_IMPACT_DATES", set()),
                nifty_prev_close     = _idx_closes.get("NIFTY", 0),
                banknifty_prev_close = _idx_closes.get("BANKNIFTY", 0),
                sensex_prev_close    = _idx_closes.get("SENSEX", 0),
                symbols_universe     = len(getattr(self.live_engine.data_fetcher, "nifty_200", []) or []) or 200,
            )
            self.alerts._mark_dedup_sent(today_key)
        except Exception:
            logger.debug("_send_market_open_if_needed failed", exc_info=True)

    def _send_hourly_update_if_due(self) -> None:
        """Send hourly P&L update, roughly once per hour during session."""
        now_ts = time.time()
        if self.last_hourly_alert_ts and (now_ts - self.last_hourly_alert_ts) < 3600:
            return
        try:
            td  = self.live_engine.trade_manager.summary()
            pnl = float(td.get("daily_realized_pnl", 0.0))
            lim = float(td.get("daily_loss_limit",
                    getattr(cfg, "MAX_DAILY_LOSS", 3000.0)))
            closed = self.live_engine.trade_manager.get_closed_trades() or []
            today  = date.today().isoformat()
            today_closed = [t for t in closed
                            if str(t.get("exit_time", "")).startswith(today)
                            or (isinstance(t.get("exit_time"), (int, float))
                                and datetime.fromtimestamp(float(t["exit_time"])).date() == date.today())]
            wins   = sum(1 for t in today_closed if float(t.get("pnl", 0)) > 0)
            losses = sum(1 for t in today_closed if float(t.get("pnl", 0)) <= 0)

            self.alerts.hourly_update(
                hour_str       = datetime.now().strftime("%H:%M"),
                daily_pnl      = pnl,
                daily_limit    = lim,
                trades_today   = len(today_closed),
                wins           = wins,
                losses         = losses,
                open_positions = int(td.get("open_positions", 0)),
            )
            # Daily loss warnings at 50% and 80%
            if lim > 0 and pnl < 0:
                pct = abs(pnl) / lim
                if pct >= 0.50:
                    self.alerts.daily_loss_warning(pnl, lim, pct)

            self.last_hourly_alert_ts = now_ts
        except Exception:
            logger.debug("_send_hourly_update_if_due failed", exc_info=True)

    def _send_daily_summary_if_needed(self) -> None:
        """Send end-of-day summary. Fires once after market closes."""
        today_key = f"daily_summary:{date.today().isoformat()}"
        if self.alerts._is_dedup_blocked(today_key, 86400):
            return
        try:
            closed = self.live_engine.trade_manager.get_closed_trades() or []
            today  = date.today()
            today_closed = [
                t for t in closed
                if (isinstance(t.get("exit_time"), (int, float))
                    and datetime.fromtimestamp(float(t["exit_time"])).date() == today)
            ]
            if not today_closed:
                return

            pnls     = [float(t.get("pnl", 0)) for t in today_closed]
            wins     = sum(1 for p in pnls if p > 0)
            losses   = len(pnls) - wins
            total    = sum(pnls)
            avg      = total / len(pnls) if pnls else 0.0
            best     = max(pnls) if pnls else 0.0
            worst    = min(pnls) if pnls else 0.0
            lim      = float(getattr(self.live_engine.trade_manager, "daily_loss_limit",
                             getattr(cfg, "MAX_DAILY_LOSS", 3000.0)))

            # Hold time
            hold_times = []
            for t in today_closed:
                et = t.get("entry_time"); xt = t.get("exit_time")
                if et and xt:
                    try:
                        hold_times.append((float(xt) - float(et)) / 60.0)
                    except Exception as _e:
                        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            avg_hold = sum(hold_times) / len(hold_times) if hold_times else None

            # STT + brokerage
            total_stt = sum(float(t.get("stt", 0.0)) for t in today_closed)
            brok_per  = float(getattr(self.live_engine.trade_manager,
                                      "brokerage_per_order", 20.0))
            total_brok = brok_per * 2 * len(today_closed)

            # Strategy breakdown
            strategy_pnl: Dict[str, float] = {}
            for t in today_closed:
                s = str(t.get("strategy", "unknown"))
                strategy_pnl[s] = strategy_pnl.get(s, 0.0) + float(t.get("pnl", 0))

            td_summary = self.live_engine.trade_manager.summary()
            # GA-15: build per-trade breakdown for daily summary
            _trade_breakdown = []
            for t in today_closed:
                _trade_breakdown.append({
                    'symbol':   t.get('symbol', '?'),
                    'strategy': t.get('strategy', '?'),
                    'pnl':      float(t.get('pnl', 0)),
                    'side':     t.get('side', '?'),
                    'exit_reason': t.get('exit_reason', '?'),
                })

            self.alerts.daily_summary(
                date_str        = today.strftime("%d %b %Y"),
                total_trades    = len(today_closed),
                wins            = wins,
                losses          = losses,
                total_pnl       = total,
                daily_limit     = lim,
                avg_pnl         = avg,
                best_trade      = best,
                worst_trade     = worst,
                avg_hold_min    = avg_hold,
                total_stt       = total_stt if total_stt > 0 else None,
                total_brokerage = total_brok if total_brok > 0 else None,
                strategy_pnl    = strategy_pnl if strategy_pnl else None,
                open_positions  = int(td_summary.get("open_positions", 0)),
                opened_today    = int(td_summary.get("opened_today", len(today_closed))),
                closed_today    = int(td_summary.get("closed_today", len(today_closed))),
            )
            self.alerts._mark_dedup_sent(today_key)
        except Exception:
            logger.debug("_send_daily_summary_if_needed failed", exc_info=True)

        # Option-channel EOD digest (separate channel; no-op if not configured).
        try:
            from option_decision_journal import send_option_daily_summary
            send_option_daily_summary(days=1)
        except Exception:
            logger.debug("option daily summary failed", exc_info=True)

    def _send_learning_alert(self, result: Dict[str, Any]) -> None:
        """Send rich after-hours learning summary to Telegram."""
        try:
            tr          = result.get("training_result") or {}
            sr          = result.get("selector_result") or {}
            rl_result   = result.get("rl_result") or {}
            ranked_raw  = sr.get("ranked") or []

            best_strategy = result.get("selected_strategy")
            prev_strategy = self.runtime_state.current_strategy

            # Enrich ranked list with strategy_state metrics
            state_file = getattr(cfg, "STRATEGY_STATE_FILE", "strategy_state.json")
            try:
                import json as _json
                state      = _json.loads(Path(state_file).read_text())
                strategies = state.get("strategies", {})
                ranked = [
                    {
                        "name":           r.get("name", "?"),
                        "score":          r.get("score", 0.0),
                        "sharpe":         strategies.get(r.get("name",""), {}).get("sharpe"),
                        "win_rate":       strategies.get(r.get("name",""), {}).get("win_rate"),
                        "wf_consistency": strategies.get(r.get("name",""), {}).get("wf_consistency"),
                    }
                    for r in ranked_raw
                ]
            except Exception:
                ranked = ranked_raw

            # RL top strategy
            rl_state    = getattr(self.live_engine.learning_engine, "rl_state", {}) or {}
            rl_strategy = None
            rl_score    = None
            for k, v in rl_state.items():
                if k.startswith("__"):
                    continue
                if isinstance(v, dict) and (rl_score is None or v.get("score", 0) > rl_score):
                    rl_strategy = k
                    rl_score    = float(v.get("score", 0))

            # Walk-forward results if available
            wf_results = None
            try:
                wf_file = Path("walk_forward_results.json")
                if wf_file.exists():
                    wf_data    = _json.loads(wf_file.read_text())
                    wf_results = wf_data.get("results")
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Today's performance
            closed = self.live_engine.trade_manager.get_closed_trades() or []
            today  = date.today()
            today_closed = [
                t for t in closed
                if isinstance(t.get("exit_time"), (int, float))
                and datetime.fromtimestamp(float(t["exit_time"])).date() == today
            ]
            wins_today  = sum(1 for t in today_closed if float(t.get("pnl", 0)) > 0)
            td_summary  = self.live_engine.trade_manager.summary()

            # Compute what specifically improved
            improvements = []
            if bool(tr.get("trained")):
                acc = tr.get("val_accuracy")
                improvements.append(
                    f"ML model retrained on {tr.get('num_trades',0)} trades"
                    + (f" — OOS accuracy {acc:.1%}" if acc else "")
                )
            if rl_result.get("trades_processed", 0) > 0:
                improvements.append(
                    f"RL updated with {rl_result['trades_processed']} new trades"
                )
            if best_strategy and prev_strategy and best_strategy != prev_strategy:
                improvements.append(
                    f"Active strategy switched: {prev_strategy} → {best_strategy}"
                )
            if wf_results:
                best_wf = max(wf_results.values(),
                              key=lambda x: x.get("consistency_score", 0),
                              default=None)
                if best_wf:
                    improvements.append(
                        f"Walk-forward validated: {best_wf.get('strategy','')} "
                        f"consistency={best_wf.get('consistency_score',0):.2f}"
                    )

            self.alerts.rich_learning_summary(
                best_strategy    = best_strategy,
                prev_strategy    = prev_strategy,
                strategy_changed = (best_strategy != prev_strategy and
                                    bool(best_strategy) and bool(prev_strategy)),
                val_accuracy     = tr.get("val_accuracy"),
                total_trades     = tr.get("num_trades", 0),
                new_trades       = rl_result.get("trades_processed", 0),
                model_trained    = bool(tr.get("trained")),
                train_reason     = tr.get("reason"),
                top_strategies   = ranked[:6],
                rl_updates       = rl_result.get("trades_processed", 0),
                rl_top_strategy  = rl_strategy,
                rl_top_score     = rl_score,
                wf_results       = wf_results,
                trades_today     = len(today_closed),
                wins_today       = wins_today,
                daily_pnl        = td_summary.get("daily_realized_pnl"),
                improvements     = improvements,
            )
        except Exception:
            logger.debug("_send_learning_alert failed", exc_info=True)



    def _send_15min_status_if_due(self) -> None:
        """
        Send 15-minute live trading status to Telegram.
        Fires every 15 minutes during market hours.
        Covers scan results, open positions, P&L, system health.
        """
        now_ts = time.time()
        interval = int(getattr(cfg, "STATUS_ALERT_INTERVAL_SEC", 900))  # 15 min default
        if self.last_15min_alert_ts and (now_ts - self.last_15min_alert_ts) < interval:
            return
        try:
            from time_regime import get_time_zone, is_expiry_day

            td   = self.live_engine.trade_manager.summary()
            pnl  = float(td.get("daily_realized_pnl", 0.0))
            lim  = float(td.get("daily_loss_limit",
                          getattr(cfg, "MAX_DAILY_LOSS", 3000.0)))

            # Open positions with unrealized P&L
            open_pos_raw = self.live_engine.trade_manager.get_open_positions() or []
            open_pos_list = []
            _angel_ref = getattr(self.live_engine, "_angel", None)
            for p in open_pos_raw:
                _sym   = p.get("symbol", "?")
                _entry = float(p.get("entry_price", 0))
                _qty   = int(p.get("qty", 0))
                _side  = p.get("side", "BUY")
                _upnl  = None
                try:
                    if _angel_ref and _entry > 0 and _qty > 0:
                        _ltp = _angel_ref._get_real_ltp(_sym)
                        if _ltp:
                            _upnl = round(
                                (_ltp - _entry if _side == "BUY" else _entry - _ltp) * _qty, 2
                            )
                except Exception:
                    pass
                open_pos_list.append({
                    "symbol":          _sym,
                    "side":            _side,
                    "entry_price":     _entry,
                    "qty":             _qty,
                    "unrealized_pnl":  _upnl,
                })

            # Today's closed trades
            closed = self.live_engine.trade_manager.get_closed_trades() or []
            today  = date.today()
            today_closed = [
                t for t in closed
                if isinstance(t.get("exit_time"), (int, float))
                and datetime.fromtimestamp(float(t["exit_time"])).date() == today
            ]
            wins_today = sum(1 for t in today_closed if float(t.get("pnl", 0)) > 0)

            # VIX if cached
            vix_val = getattr(self.live_engine, "_vix_cache_val", None)

            # Circuit breaker state
            cb_active = (
                time.time() < getattr(self.live_engine, "_circuit_breaker_until", 0)
            )

            # Scan summary from last scan cycle
            scan = getattr(self, "_scan_summary", {})
            # Warm-up grace: right after a (mid-session) restart the cache is cold
            # and the first scan cycle hasn't completed, so Scanned reads 0 for
            # ~20 min. Flag it as "warming up" instead of a scary "Scanned: 0".
            _scanned_n = scan.get("total_symbols_scanned", 0)
            _warming = (_scanned_n == 0 and
                        (time.time() - getattr(self, "_uptime_start", 0)) < 1200)

            # Smart throttle: only push the status when something MEANINGFUL changed
            # (signals / positions / trades / P&L bucket / phase / breaker) or once
            # an hour as a heartbeat — instead of an identical "Scanned: N" every
            # 15 min. Cuts the #1 source of Telegram noise without losing info.
            _fp = (scan.get("total_signals", 0), len(open_pos_raw), len(today_closed),
                   round(pnl / 500.0), bool(cb_active), bool(_warming),
                   str(self.runtime_state.market_phase))
            _hb = int(getattr(cfg, "STATUS_HEARTBEAT_SEC", 3600))
            if (_fp == getattr(self, "_last_status_fp", None)
                    and (now_ts - getattr(self, "_last_status_sent_ts", 0.0)) < _hb):
                self.last_15min_alert_ts = now_ts   # advance gate, stay quiet
                return
            self._last_status_fp = _fp
            self._last_status_sent_ts = now_ts

            self.alerts.status_15min(
                warming_up       = _warming,
                symbols_scanned  = scan.get("total_symbols_scanned", 0),
                tier1_scanned    = scan.get("tier1_scanned", 6),
                signals_found    = scan.get("total_signals", 0),
                tier1_signals    = scan.get("tier1_signals", 0),
                top_signal       = scan.get("top_signal"),
                open_positions   = len(open_pos_raw),
                open_pos_list    = open_pos_list,
                daily_pnl        = pnl,
                daily_limit      = lim,
                trades_today     = len(today_closed),
                wins_today       = wins_today,
                market_phase     = self.runtime_state.market_phase,
                current_strategy = self.runtime_state.current_strategy,
                circuit_breaker  = cb_active,
                vix              = vix_val if vix_val and vix_val > 0 else None,
                time_zone        = get_time_zone().value,
                expiry_day       = is_expiry_day(),
            )

            self.last_15min_alert_ts = now_ts
        except Exception:
            logger.debug("_send_15min_status_if_due failed", exc_info=True)

    def _update_scan_summary(self, candidates: list) -> None:
        """Update cached scan summary for 15-min status alert."""
        try:
            if not candidates:
                self._scan_summary = {"total_signals": 0, "tier1_signals": 0}
                return
            from strategy_scanner import TIER1_SYMBOLS
            tier1 = [c for c in candidates if c.get("symbol") in TIER1_SYMBOLS]
            top   = candidates[0] if candidates else {}
            self._scan_summary = {
                "total_symbols_scanned": getattr(getattr(self, "_signal_engine", None), "_last_scan_count", 0) or getattr(self, "_last_scan_count", 0),
                "tier1_scanned":         6,
                "total_signals":         len(candidates),
                "tier1_signals":         len(tier1),
                "top_signal": {
                    "symbol":           top.get("symbol"),
                    "action":           top.get("signal", {}).get("side"),
                    "strategy":         top.get("signal", {}).get("strategy"),
                    "final_score":      top.get("final_rank_score", 0),
                    "confluence_level": top.get("signal", {}).get("confluence", "LOW"),
                    "is_tier1":         top.get("priority_symbol", False),
                } if top else None,
            }
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    # ------------------------------------------------------------------
    # Capital compounding helpers
    # ------------------------------------------------------------------
    def _check_capital_milestone(self) -> None:
        """Check if a capital milestone was crossed this cycle and alert."""
        try:
            cc = getattr(self.live_engine, "capital_compounder", None)
            if not cc:
                return
            balance = self.live_engine.trade_manager.capital
            milestone = cc.compounding_milestone_check(balance)
            if milestone:
                self.alerts.capital_milestone(
                    milestone_label = milestone["milestone"],
                    balance         = milestone["balance"],
                    tier_phase      = milestone["tier_phase"],
                    tier_label      = milestone["tier_label"],
                    new_max_lots    = milestone["new_max_lots"],
                    new_max_pos     = milestone["new_max_pos"],
                )
                logger.info("CAPITAL MILESTONE: %s at ₹%.0f",
                            milestone["milestone"], milestone["balance"])
        except Exception:
            logger.debug("_check_capital_milestone failed", exc_info=True)

    def _check_drawdown_alert(self) -> None:
        """Send CRITICAL alert if drawdown circuit breaker trips."""
        try:
            cc = getattr(self.live_engine, "capital_compounder", None)
            if not cc or not cc._drawdown_active:
                return
            td = self.live_engine.trade_manager.summary()
            pnl = td.get("daily_realized_pnl", 0.0)
            params = cc.get_current_params(cc._peak_equity)
            self.alerts.drawdown_breaker(
                tripped        = True,
                drawdown_pct   = params.drawdown_pct,
                peak_equity    = cc._peak_equity,
                current_equity = cc._peak_equity * (1 - params.drawdown_pct),
                new_max_lots   = params.max_lots,
            )
        except Exception:
            logger.debug("_check_drawdown_alert failed", exc_info=True)

    def _check_profit_lock(self) -> None:
        """Check and apply monthly profit lock on last Friday of month."""
        try:
            cc = getattr(self.live_engine, "capital_compounder", None)
            if not cc:
                return
            balance = self.live_engine.trade_manager.capital
            daily_limit = self.live_engine.trade_manager.daily_loss_limit
            new_limit, updated = cc.check_monthly_profit_lock(balance, daily_limit)
            if updated:
                # Apply the new limit
                self.live_engine.trade_manager.daily_loss_limit = new_limit
                self.live_engine.daily_loss_manager.hard_limit  = new_limit
                self.alerts.monthly_profit_lock(
                    monthly_pnl     = balance - cc._month_start_bal,
                    locked_amount   = cc._profit_locked,
                    new_daily_limit = new_limit,
                    lock_pct        = cc.profit_lock_pct,
                )
        except Exception:
            logger.debug("_check_profit_lock failed", exc_info=True)




    def _check_option_theta_exits(self) -> None:
        """
        Check all open option positions for theta decay exits.
        Called every cycle during market hours.
        Exits positions where theta has consumed > 15% of entry premium.
        """
        try:
            from option_intelligence import get_option_intelligence
            oi   = get_option_intelligence()
            tm   = self.live_engine.trade_manager
            open_trades = tm.get_open_positions()

            for pos in open_trades:
                trade_id = pos.get("trade_id", "")
                sym      = pos.get("symbol", "")
                if not (sym.endswith("CE") or sym.endswith("PE")):
                    continue
                try:
                    ltp = self.live_engine._broker_manager.get_execution_broker().get_ltp(sym)
                    if not ltp or ltp <= 0:
                        continue
                    dte_est = self.live_engine._estimate_dte_from_symbol(sym)
                    check   = oi.should_exit_for_theta(trade_id, float(ltp), dte_est)
                    if check.get("exit"):
                        logger.warning(
                            "THETA EXIT | %s %s theta=%.0f%% reason=%s",
                            trade_id, sym, check.get("theta_pct", 0),
                            check.get("reason", "")
                        )
                        tm._close_trade_internal(
                            trade_id   = trade_id,
                            exit_price = float(ltp),
                            exit_reason= f"theta_exit_{check.get('reason','')}",
                        )
                        self.alerts.send(
                            f"⏰ <b>THETA EXIT</b>\n"
                            f"{sym} | held {check.get('theta_pct',0):.0f}% of premium decayed\n"
                            f"Reason: {check.get('reason','')}\n"
                            f"Exited @ ₹{ltp:.0f}"
                        )
                        oi.remove_position(trade_id)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        except Exception as exc:
            logger.debug("_check_option_theta_exits: %s", exc)

    def _estimate_dte_from_symbol(self, symbol: str) -> int:
        """Estimate DTE from option symbol string."""
        try:
            from datetime import date
            import re
            m = re.search(r'(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})', symbol.upper())
            if m:
                day   = int(m.group(1))
                month = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                         "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}[m.group(2)]
                year  = 2000 + int(m.group(3))
                expiry = date(year, month, day)
                dte    = (expiry - date.today()).days
                return max(0, dte)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        return 1


    def _init_nse_master(self) -> None:
        """
        Initialise NSEMaster and wire broker for lot size refresh.
        Called once at startup. Refreshes stale data in background.
        """
        if not _NSE_MASTER_MA:
            return
        try:
            master = _get_nse_master_ma()
            status = master.get_status()
            logger.info(
                "NSEMaster initialised | lots=%s holidays=%d source_lots=%s source_hol=%s",
                status.get("lot_sizes_count"),
                status.get("holidays_count", 0),
                status.get("lot_sizes_source"),
                status.get("holidays_source"),
            )
            # Log key lot sizes so operator can verify
            logger.info(
                "Lot sizes: NIFTY=%s BANKNIFTY=%s FINNIFTY=%s MIDCPNIFTY=%s",
                master.get_lot_size("NIFTY"),
                master.get_lot_size("BANKNIFTY"),
                master.get_lot_size("FINNIFTY"),
                master.get_lot_size("MIDCPNIFTY"),
            )
            # Send summary to Telegram on startup
            if status.get("lot_sizes_stale") or status.get("holidays_stale"):
                self.alerts.send(
                    "📋 <b>NSE MASTER DATA</b>\n"
                    f"NIFTY lot: {master.get_lot_size('NIFTY')} | "
                    f"BANKNIFTY: {master.get_lot_size('BANKNIFTY')}\n"
                    f"Holidays loaded: {status.get('holidays_count',0)}\n"
                    f"Source: {status.get('lot_sizes_source','?')} / "
                    f"{status.get('holidays_source','?')}\n"
                    f"⚠️ Stale data detected — refreshing in background"
                )
            else:
                self.alerts.send(
                    "📋 <b>NSE MASTER DATA</b>\n"
                    f"NIFTY lot: {master.get_lot_size('NIFTY')} | "
                    f"BANKNIFTY: {master.get_lot_size('BANKNIFTY')}\n"
                    f"Holidays: {status.get('holidays_count',0)} | "
                    f"Source: {status.get('lot_sizes_source','?')} ✅"
                )
        except Exception as exc:
            logger.warning("NSEMaster init warning: %s", exc)


    def _run_eod_feeds(self) -> None:
        """Run EOD data fetches: bulk deals, participant OI, tradebook reconcile."""
        if not _FEEDS_MA:
            return
        try:
            feeds = _get_market_feeds()
            feeds.run_eod_tasks(self.live_engine.trade_manager)
            logger.info("EOD feeds completed")
        except Exception as exc:
            logger.debug("_run_eod_feeds: %s", exc)

    def _check_manual_book_risk(self) -> None:
        """Alert if the MANUAL book's risk breaches limits. Manual trades are not
        governed by the auto risk layer (VaR/daily-loss) — manual_book_risk.py was a
        manual-run reporter; this surfaces it as a daily post-close alert."""
        try:
            import manual_book_risk as _mbr
            positions = _mbr.load_open_positions()
            if not positions:
                return
            cap = float(getattr(self.live_engine.trade_manager, "capital", 0) or 0) \
                or _mbr._capital(None)
            rep = _mbr.build_report(cap, positions, _mbr.today_realized_pnl())
            warns = rep.get("warnings", []) or []
            risk_pct = rep.get("portfolio_risk_pct") or 0.0
            unstopped = rep.get("unstopped", []) or []
            if warns or unstopped or (risk_pct and risk_pct > 0.05):
                lines = [f"⚠️ <b>Manual-book risk</b>",
                         f"positions={rep.get('n_positions')} "
                         f"exposure={rep.get('exposure_pct')} risk={risk_pct}"]
                if unstopped:
                    lines.append(f"NO STOP: {', '.join(unstopped[:5])}")
                lines += [f"• {w}" for w in warns[:4]]
                self.alerts.send("\n".join(lines), dedup_key="manual_book_risk")
        except Exception as exc:
            logger.debug("_check_manual_book_risk: %s", exc)

    def _get_gift_nifty_gap(self) -> float:
        """Return expected opening gap from GIFT Nifty at 8:50 AM."""
        if not _FEEDS_MA:
            return 0.0
        try:
            feeds    = _get_market_feeds()
            gift_px  = feeds.gift_nifty.get_price()
            if gift_px <= 0:
                return 0.0
            # Get previous NIFTY close
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            d = yf.download("^NSEI", period="3d", interval="1d",
                            progress=False, auto_adjust=True, threads=False)
            if d is not None and len(d) >= 2:
                prev_close = float(d["Close"].iloc[-2])
                gap_pct    = (gift_px - prev_close) / prev_close
                logger.info("GIFT Nifty: %.2f prev_close: %.2f gap: %.2f%%",
                            gift_px, prev_close, gap_pct * 100)
                return gap_pct
        except Exception as exc:
            logger.debug("_get_gift_nifty_gap: %s", exc)
        return 0.0

    def _log_live_data_status(self) -> None:
        """Send a quick status of all live data feeds to Telegram (hourly)."""
        if not _FEEDS_MA:
            return
        try:
            feeds  = _get_market_feeds()
            status = feeds.get_status_summary()
            vix    = status.get("vix", 0)
            vix_c  = status.get("vix_change", 0)
            bread  = status.get("breadth_signal", "?")
            gift   = status.get("gift_nifty", 0)
            fii_b  = status.get("fii_futures_bias", 0)
            circuits_u = status.get("circuits_upper", 0)
            circuits_l = status.get("circuits_lower", 0)

            vix_icon = "🔴" if vix > 20 else "🟡" if vix > 16 else "🟢"
            self.alerts.send(
                f"📡 <b>LIVE DATA STATUS</b>\n"
                f"{vix_icon} VIX: {vix:.1f} ({vix_c:+.1f} change)\n"
                f"📊 Breadth: {bread}\n"
                f"🎯 GIFT Nifty: {gift:.0f}\n"
                f"🏦 FII Futures Bias: {fii_b:+.3f}\n"
                f"⚡ Circuits: {circuits_u}↑ {circuits_l}↓",
                dedup_key="live_data_status",
                dedup_cooldown_override=3600,
            )
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)



    def _check_learning_loop_health(self) -> None:
        """
        Hourly: verify the learning loop is active (signal_log.db is growing).
        Alerts once per day if signals are not being logged — means the ML
        training pipeline has no data and is running blind.
        """
        try:
            from pathlib import Path as _P
            import datetime as _dt
            db = _P("signal_log.db")
            if not db.exists() or db.stat().st_size < 1024:
                self.alerts.send(
                    "⚠️ <b>Learning Loop Stalled</b>\n"
                    "signal_log.db is empty or missing.\n"
                    "Signal labelling and ML training have no data.\n"
                    "Check: is live_signal_engine running? "
                    "Is SIG_LOG_AVAIL=True in live_signal_engine?",
                    dedup_key="learning_loop_stalled",
                    dedup_cooldown_override=86400,  # once per day
                )
                return
            # Check if new rows were added in the last hour
            from signal_log import get_signal_logger
            sl = get_signal_logger()
            stats = sl.stats()
            total = int(stats.get("total", 0))
            # Store last-seen count to detect growth
            _state_key = "_learning_loop_last_count"
            last = getattr(self, _state_key, 0)
            if total == last and total > 0:
                # No new signals in this check period — possible stall
                # Only alert if market was open today
                now = _dt.datetime.now()
                if 9 <= now.hour <= 16:
                    self.alerts.send(
                        f"⚠️ <b>Signal Log Not Growing</b>\n"
                        f"signal_log.db has {total} rows (unchanged).\n"
                        f"Live engine may not be logging candidates.",
                        dedup_key="signal_log_not_growing",
                        dedup_cooldown_override=7200,  # every 2h max
                    )
            setattr(self, _state_key, total)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("learning_loop_health: %s", _e)

    def _run_sl_hunt_cycle(self) -> None:
        """
        Every cycle: check open trades for SL hunt (wick stops).
        Runs the SLHuntGuard check against latest candle data.
        """
        if not _SLHG_MA:
            return
        try:
            sl_guard = self.live_engine._sl_guard
            if not sl_guard:
                return
            tm = self.live_engine.trade_manager
            for trade in tm.get_open_positions():
                tid = trade.get("trade_id", "")
                sym = trade.get("symbol", "")
                side = trade.get("side", "BUY")
                ep  = float(trade.get("entry_price", 0))
                if not tid or not ep:
                    continue
                try:
                    df = self.live_engine._fetcher.get_market_data(sym, interval="5m", days=1)
                    if df is None or len(df) < 3:
                        continue
                    from institutional_alpha import get_alpha_engine
                    ofi_val = get_alpha_engine().ofi.compute_ofi(df=df)
                    c_col = "Close" if "Close" in df.columns else "close"
                    l_col = "Low"   if "Low"   in df.columns else "low"
                    h_col = "High"  if "High"  in df.columns else "high"
                    o_col = "Open"  if "Open"  in df.columns else "open"
                    result = sl_guard.check(
                        trade_id      = tid,
                        current_price = float(df[c_col].iloc[-1]),
                        current_low   = float(df[l_col].iloc[-1]),
                        current_high  = float(df[h_col].iloc[-1]),
                        candle_open   = float(df[o_col].iloc[-1]),
                        candle_close  = float(df[c_col].iloc[-1]),
                        ofi           = ofi_val,
                        bar_index     = len(df),
                    )
                    action = result.get("action", "HOLD")
                    if action == "SOFT_EXIT":
                        logger.warning("SL SOFT EXIT | %s %s reason=%s",
                                       tid, sym, result.get("reason",""))
                        ltp = float(df[c_col].iloc[-1])
                        tm._close_trade_internal(tid, ltp, result.get("reason","soft_exit"))
                        sl_guard.remove(tid)
                    elif action == "REENTRY":
                        logger.info("SL HUNT REENTRY SIGNAL | %s", tid)
                        # Reentry is handled via new signal in next cycle
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        except Exception as exc:
            logger.debug("_run_sl_hunt_cycle: %s", exc)

    def _run_swing_trend_check(self) -> None:
        """
        Every 30 minutes: check if swing trade trend thesis has been invalidated.
        """
        if not _SLHG_MA:
            return
        try:
            sp = _get_swing_protect_ma(
                trade_manager = self.live_engine.trade_manager,
                alerts        = self.alerts,
            )
            tm = self.live_engine.trade_manager
            bnf_df = getattr(self.live_engine, "_bnf_df_cache", None)
            vix    = self.live_engine._vix_cache_val if hasattr(self.live_engine, "_vix_cache_val") else 0.0
            for trade in tm.get_open_positions():
                meta = trade.get("metadata", {}) or {}
                if not meta.get("style") == "swing":
                    continue
                tid  = trade.get("trade_id", "")
                sym  = trade.get("symbol", "")
                side = trade.get("side", "BUY")
                ep   = float(trade.get("entry_price", 0))
                try:
                    df = self.live_engine._fetcher.get_market_data(sym, interval="15m", days=5)
                    if df is None or len(df) < 20:
                        continue
                    entry_vix = sp.get_entry_vix(tid)
                    result = sp.check_trend_invalidation(
                        trade_id     = tid,
                        symbol       = sym,
                        side         = side,
                        entry_price  = ep,
                        df           = df,
                        df_banknifty = bnf_df,
                        current_vix  = vix,
                        original_vix = entry_vix,
                    )
                    if result.get("action") == "CLOSE_FULL":
                        ltp = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
                        tm._close_trade_internal(tid, ltp, result.get("reason","trend_invalidated"))
                        sp.cleanup(tid)
                        self.live_engine._sl_guard.remove(tid) if self.live_engine._sl_guard else None
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        except Exception as exc:
            logger.debug("_run_swing_trend_check: %s", exc)

    def _run_friday_protection(self) -> None:
        """Check and close options that shouldn't be held over the weekend."""
        if not _SLHG_MA:
            return
        try:
            from datetime import datetime
            if datetime.now().weekday() != 4:  # Not Friday
                return
            sp = _get_swing_protect_ma(alerts=self.alerts)
            tm = self.live_engine.trade_manager
            actions = sp.friday_risk_check(tm.get_open_positions())
            for act in actions:
                if act.get("action") == "CLOSE_FULL":
                    tid = act.get("trade_id","")
                    sym = act.get("symbol","")
                    try:
                        ltp = float(self.live_engine._broker_manager
                                    .get_execution_broker().get_ltp(sym) or 0)
                    except Exception:
                        ltp = 0.0
                    if ltp > 0:
                        tm._close_trade_internal(tid, ltp, act.get("reason","friday_protection"))
        except Exception as exc:
            logger.debug("_run_friday_protection: %s", exc)


    def _fetch_startup_balance(self) -> float:
        """
        Fetch real account balance from Angel One at startup.
        
        Paper mode:  returns PAPER_CAPITAL from .env
        Live mode:   fetches actual balance from Angel One rmsLimit API.
                     If unavailable, switches to paper-only orders and uses
                     PAPER_CAPITAL for simulated sizing.
        
        This is called ONCE at startup so position sizing is correct
        from the very first trade.
        """
        import config as cfg
        paper_mode = bool(getattr(cfg, "PAPER_TRADING", True))

        if paper_mode:
            balance = float(getattr(cfg, "PAPER_CAPITAL",
                                    getattr(cfg, "CAPITAL", 100000)))
            logger.info("Paper mode — capital: ₹%.0f (from PAPER_CAPITAL in .env)", balance)
            return balance

        # Live mode — fetch from Angel One
        try:
            broker = self.live_engine.broker_manager.get_execution_broker()
            if broker:
                if hasattr(broker, "angel") and broker.angel and hasattr(broker.angel, "get_balance"):
                    live_bal = broker.angel.get_balance(force_real=True)
                elif hasattr(broker, "get_balance"):
                    live_bal = broker.get_balance()
                else:
                    live_bal = 0.0

                live_bal = float(live_bal or 0.0)
                paper_cap = float(getattr(cfg, "PAPER_CAPITAL", getattr(cfg, "CAPITAL", 100000)))
                configured_cap = float(getattr(cfg, "CAPITAL", paper_cap))
                looks_like_paper_fallback = (
                    live_bal in {paper_cap, configured_cap, 100000.0, 1000000.0}
                    and bool(getattr(cfg, "PAPER_ORDERS_ONLY", False))
                )
                if live_bal > 0 and not looks_like_paper_fallback:
                    self._apply_order_block(False, "startup_real_balance_positive")
                    self._sync_runtime_capital(live_bal, reset_allocator_peak=True)
                    self.runtime_state.mode = "LIVE"
                    logger.info(
                        "✅ Live balance fetched from Angel One: ₹%.0f", live_bal
                    )
                    self.alerts.send(
                        f"💰 <b>Live balance confirmed</b>\n"
                        f"Angel One balance: <b>₹{live_bal:,.0f}</b>\n"
                        f"Position sizing set to this amount\n"
                        f"🕐 {__import__('datetime').datetime.now().strftime('%H:%M')}",
                        dedup_key="startup_balance",
                    )
                    return live_bal
                if looks_like_paper_fallback:
                    logger.warning(
                        "Startup balance fetch returned paper fallback ₹%.0f — "
                        "not enabling live orders from that value.",
                        live_bal,
                    )
        except Exception as exc:
            logger.warning("Startup balance fetch failed: %s", exc)

        self._apply_order_block(True, "startup_real_balance_unavailable")
        self.runtime_state.mode = "PAPER"
        paper_cap = float(getattr(cfg, "PAPER_CAPITAL", getattr(cfg, "CAPITAL", 100000)))
        logger.warning(
            "Live balance unavailable — switching to PAPER_ORDERS_ONLY "
            "with PAPER_CAPITAL ₹%.0f. Signals continue as paper trades.",
            paper_cap,
        )
        try:
            self.alerts.send(
                "⚠️ <b>Angel Balance Unavailable — Paper Mode</b>\n"
                "Could not fetch live balance from Angel One.\n"
                "Signals will continue as paper trades. Real orders are blocked.",
                dedup_key="balance_fetch_failed"
            )
        except Exception: pass
        return paper_cap

    def _check_auto_mode_after_trade(self) -> None:
        """Re-evaluate paper/live mode after a trade closes."""
        if not _AUTOMODE_AVAILABLE or not self.auto_mode:
            return
        try:
            _open = len(self.live_engine.trade_manager.get_open_positions())
            _result = self.auto_mode.evaluate(open_positions=_open)
            if _result.get("switched"):
                import config as _cfg_at
                if _result.get("is_live"):
                    self._apply_order_block(False, "auto_mode_after_trade_live")
                    self.runtime_state.mode = "LIVE"
                else:
                    if (
                        getattr(self, "_dual_engine", None)
                        and self._dual_engine.is_live_enabled()
                    ):
                        self._apply_order_block(False, "dual_mode_overrides_auto_after_trade")
                        self.runtime_state.mode = "LIVE"
                        return
                    # _cfg_at.PAPER_TRADING = True  # DISABLED: paper_trade kills data fetch

                    _cfg_pm = __import__("config")

                    self._apply_order_block(True, "auto_mode_after_trade_paper")
                    # _cfg_at.PAPER_TRADE   = True  # DISABLED: use PAPER_ORDERS_ONLY instead
                    self.runtime_state.mode = "PAPER"
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)



    def _send_weekly_report(self) -> None:
        """Friday 4 PM — structured weekly performance report."""
        try:
            import sqlite3
            from datetime import date, timedelta
            today      = date.today()
            week_start = today - timedelta(days=today.weekday())
            conn = sqlite3.connect(
                getattr(__import__("config"), "TRADES_DB", "trades.db")
            )
            rows = conn.execute(
                "SELECT symbol,strategy,side,realized_pnl,entry_time,exit_reason "
                "FROM trades WHERE status=\'CLOSED\' "
                "AND date(entry_time,\'unixepoch\') >= ? ORDER BY entry_time",
                (week_start.isoformat(),)
            ).fetchall()
            conn.close()

            total_pnl = sum(r[3] or 0 for r in rows)
            wins      = sum(1 for r in rows if (r[3] or 0) > 0)
            losses    = len(rows) - wins
            win_rate  = wins / max(len(rows), 1) * 100
            avg_pnl   = total_pnl / max(len(rows), 1)
            best      = max(rows, key=lambda r: r[3] or 0, default=None)
            worst     = min(rows, key=lambda r: r[3] or 0, default=None)

            strat_stats = {}
            for r in rows:
                s = r[1] or "unknown"
                if s not in strat_stats:
                    strat_stats[s] = {"pnl": 0.0, "trades": 0, "wins": 0}
                strat_stats[s]["pnl"]    += r[3] or 0
                strat_stats[s]["trades"] += 1
                strat_stats[s]["wins"]   += 1 if (r[3] or 0) > 0 else 0

            icon = "📈" if total_pnl >= 0 else "📉"
            week_str  = week_start.strftime("%d %b")
            today_str = today.strftime("%d %b %Y")
            lines = [
                icon + " <b>WEEKLY REPORT</b>",
                week_str + " to " + today_str,
                "━" * 28,
                "Total P&L:     " + ("₹{:+,.0f}".format(total_pnl)),
                "Trades:        " + str(len(rows)) + " (" + str(wins) + "W / " + str(losses) + "L)",
                "Win rate:      " + "{:.0f}%".format(win_rate),
                "Avg per trade: " + ("₹{:+.0f}".format(avg_pnl)),
            ]
            if best and (best[3] or 0) > 0:
                lines.append("Best trade:    " + best[0] + " ₹{:+.0f}".format(best[3]))
            if worst and (worst[3] or 0) < 0:
                lines.append("Worst trade:   " + worst[0] + " ₹{:+.0f}".format(worst[3] or 0))
            if strat_stats:
                lines.append("")
                lines.append("<b>Strategy breakdown:</b>")
                for s, v in sorted(strat_stats.items(),
                                   key=lambda x: x[1]["pnl"], reverse=True)[:5]:
                    wr = v["wins"] / max(v["trades"], 1) * 100
                    lines.append(
                        "  " + s + ": " + str(v["trades"]) + " trades "
                        + "₹{:+.0f}".format(v["pnl"]) + " ({:.0f}% WR)".format(wr)
                    )
            msg = "\n".join(lines)
            self.alerts.send(msg, dedup_key="weekly_" + str(today.isocalendar()[:2]))
            logger.info("Weekly report sent: %d trades pnl=%.0f", len(rows), total_pnl)
        except Exception as e:
            logger.debug("Weekly report error: %s", e)


    def _send_daily_report(self) -> None:
        """Send structured daily P&L report at 3:35 PM."""
        try:
            import sqlite3
            from datetime import date as _date
            today = _date.today().isoformat()
            conn  = sqlite3.connect(getattr(__import__("config"), "TRADES_DB", "trades.db"))
            rows  = conn.execute("""
                SELECT symbol, strategy, side, realized_pnl, exit_reason
                FROM trades
                WHERE status='CLOSED'
                  AND date(exit_time,'unixepoch','localtime') = ?
                ORDER BY realized_pnl DESC
            """, (today,)).fetchall()
            conn.close()

            total_pnl = sum(r[3] or 0 for r in rows)
            wins      = sum(1 for r in rows if (r[3] or 0) > 0)
            losses    = len(rows) - wins
            best      = max(rows, key=lambda r: r[3] or 0, default=None)
            worst     = min(rows, key=lambda r: r[3] or 0, default=None)

            icon = "✅" if total_pnl >= 0 else "❌"
            msg  = (
                f"{icon} <b>DAILY REPORT — {today}</b>\n"
                f"{'─'*30}\n"
                f"Total P&L:  ₹{total_pnl:+,.0f}\n"
                f"Trades:     {len(rows)} ({wins}W / {losses}L)\n"
                f"Win rate:   {wins/max(len(rows),1)*100:.0f}%\n"
            )
            if best and (best[3] or 0) > 0:
                msg += f"Best:       {best[0]} ₹{best[3]:+.0f}\n"
            if worst and (worst[3] or 0) < 0:
                msg += f"Worst:      {worst[0]} ₹{worst[3]:+.0f}\n"

            # Strategy breakdown
            strat_pnl = {}
            for r in rows:
                s = r[1] or "unknown"
                strat_pnl[s] = strat_pnl.get(s, 0) + (r[3] or 0)
            if strat_pnl:
                top_strat = max(strat_pnl, key=strat_pnl.get)
                msg += f"Top strat:  {top_strat} ₹{strat_pnl[top_strat]:+.0f}"

            self.alerts.send(msg, dedup_key=f"daily_report_{today}")
            logger.info("Daily report sent: %d trades P&L=%.0f", len(rows), total_pnl)
        except Exception as e:
            logger.debug("Daily report error: %s", e)

    def _record_strategy_outcome(self, trade: dict, pnl: float) -> None:
        """Record trade result in strategy performance matrix."""
        if not _MATRIX_AVAILABLE or not self._strat_matrix:
            return
        try:
            strategy  = str(trade.get("strategy","UNKNOWN")).lower()
            day_type  = "UNKNOWN"
            vix       = 15.0
            regime    = "UNKNOWN"
            # Get day type if available
            if self.live_engine._day_classifier:
                profile  = getattr(self.live_engine._day_classifier, "_last_profile", None)
                if profile:
                    day_type = getattr(profile, "day_type", "UNKNOWN")
            time_bucket = self._strat_matrix.get_time_bucket()
            self._strat_matrix.record_trade(
                strategy=strategy, pnl=pnl,
                day_type=day_type, time_bucket=time_bucket,
                vix=vix, regime=regime,
            )
            # Also record weekly loss
            if hasattr(self.live_engine.daily_loss_manager, "add_weekly_loss"):
                self.live_engine.daily_loss_manager.add_weekly_loss(pnl)
        except Exception as e:
            logger.debug("Strategy matrix record: %s", e)

    def _record_kelly_result(self, trade_id: str, pnl: float, risk: float) -> None:
        """Record completed trade result in Kelly sizer."""
        try:
            kelly_key = f"_kelly_pending_{trade_id}"
            live_eng  = self.live_engine
            if hasattr(live_eng, kelly_key):
                strat, saved_risk = getattr(live_eng, kelly_key)
                use_risk = saved_risk if saved_risk > 0 else max(risk, 1.0)
                from advanced_strategies import get_kelly_sizer
                get_kelly_sizer().record(strat, pnl, use_risk)
                # Also record in alpha engine strategy momentum
                if live_eng._alpha_engine:
                    live_eng._alpha_engine.record_trade_result(strat, pnl > 0, pnl)
                delattr(live_eng, kelly_key)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    def _refresh_nse_master_if_stale(self) -> None:
        """Called nightly — refresh NSEMaster if data is stale."""
        if not _NSE_MASTER_MA:
            return
        try:
            master = _get_nse_master_ma()
            if master._is_lot_stale():
                logger.info("Lot sizes stale — refreshing from Angel One")
                # Try to pass Angel One object for SmartAPI refresh
                angel_obj = None
                try:
                    bm = self.live_engine._broker_manager
                    angel_obj = bm.get_execution_broker()
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                refreshed = master.refresh_lot_sizes(angel_obj=angel_obj)
                if refreshed:
                    status = master.get_status()
                    self.alerts.send(
                        f"🔄 <b>LOT SIZES REFRESHED</b>\n"
                        f"NIFTY: {master.get_lot_size('NIFTY')} | "
                        f"BANKNIFTY: {master.get_lot_size('BANKNIFTY')}\n"
                        f"Source: {status.get('lot_sizes_source')}"
                    )
            if master._is_holiday_stale():
                logger.info("Holiday data stale — refreshing from NSE")
                master.refresh_holidays()
        except Exception as exc:
            logger.debug("_refresh_nse_master_if_stale: %s", exc)

    def _update_market_context_daily(self) -> None:
        """
        Update market context after close for next day's trading.
        Records VIX, previous-day bias, fetches FII/DII, sector rotation.
        """
        try:
            from market_context import get_market_context
            ctx = get_market_context()

            # VIX
            vix_val = getattr(self.live_engine, "_vix_cache_val", 0.0) or 0.0
            if vix_val > 0:
                ctx.record_vix(vix_val)

            # Previous day bias for NIFTY/BANKNIFTY/FINNIFTY
            for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
                try:
                    df = self.data_fetcher.get_market_data(sym, interval="1d", days=10)
                    if df is not None and len(df) >= 5:
                        from indicators import calculate_ema
                        ema5 = calculate_ema(df, 5)
                        # _safe_close was undefined here (NameError, masked by the
                        # surrounding try → prev-day bias silently never recorded).
                        _cl_col = "close" if "close" in df.columns else "Close"
                        last_close = float(df[_cl_col].iloc[-1])
                        last_ema5  = float(ema5.iloc[-1])
                        ctx.update_prev_day_bias(sym, last_close, last_ema5)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Sector rotation (5-day returns)
            perfs = {}
            for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
                try:
                    df = self.data_fetcher.get_market_data(sym, interval="1d", days=8)
                    if df is not None and len(df) >= 6:
                        c = df["Close"] if "Close" in df.columns else df["close"]
                        ret5 = (float(c.iloc[-1]) - float(c.iloc[-6])) / float(c.iloc[-6])
                        perfs[sym] = round(ret5, 4)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            if perfs:
                ctx.update_relative_strength(perfs)

            # FII/DII (NSE provisional, available after 4 PM)
            ctx.fetch_fii_data_from_nse()

            logger.info("Market context updated | status=%s", ctx.status_summary())
        except Exception as exc:
            logger.debug("_update_market_context_daily failed: %s", exc)


    def _startup_position_safety_check(self) -> None:
        """
        Called once on every startup.
        If it is past 3:15 PM and there are open positions, close them immediately.
        This handles the case where the system crashed during EOD squareoff.
        """
        from datetime import datetime, time as dtime
        now_t = datetime.now().time()

        # If past 3:15 PM and positions still open
        if now_t >= dtime(15, 15):
            try:
                open_count = len(self.live_engine.trade_manager.open_trades)
                if open_count > 0:
                    logger.critical(
                        "STARTUP SAFETY: %d open positions found after 3:15 PM — "
                        "triggering emergency squareoff",
                        open_count,
                    )
                    self.alerts.send(
                        f"🚨 <b>STARTUP EMERGENCY SQUAREOFF</b>\n"
                        f"System restarted after 3:15 PM with {open_count} open positions.\n"
                        f"Closing all now."
                    )
                    self.live_engine.trade_manager.close_positions_at_eod(
                        ltp_getter=self._get_squareoff_ltp,
                        reason="startup_eod_emergency",
                    )
                    self._eod_squared_off = True
                    logger.info("Startup emergency squareoff completed")
            except Exception:
                logger.exception("_startup_position_safety_check failed")

        # If market is in trade window and we just restarted, log it
        elif dtime(9, 15) <= now_t <= dtime(15, 10):
            open_count = len(self.live_engine.trade_manager.open_trades)
            if open_count > 0:
                logger.info(
                    "STARTUP: market is LIVE, %d positions restored — "
                    "resuming monitoring",
                    open_count,
                )
                self.alerts.send(
                    f"⚠️ <b>BOT RESTARTED during market hours</b>\n"
                    f"Restored {open_count} open positions.\n"
                    f"Broker SL orders are still active at Angel One.\n"
                    f"Resuming monitoring."
                )

    def _get_squareoff_ltp(self, symbol: str, exchange: str) -> Optional[float]:
        """Best-effort exit LTP for EOD/startup square-off."""
        try:
            broker_manager = getattr(self.live_engine, "broker_manager", None)
            if broker_manager and hasattr(broker_manager, "get_ltp"):
                ltp = broker_manager.get_ltp(symbol, exchange)
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception:
            pass

        try:
            fetcher = getattr(self.live_engine, "data_fetcher", None)
            if fetcher and hasattr(fetcher, "get_market_data"):
                df = fetcher.get_market_data(symbol, interval="5m", days=2)
                if df is not None and len(df) > 0:
                    close_col = "close" if "close" in df.columns else "Close"
                    if close_col in df.columns:
                        ltp = float(df[close_col].iloc[-1])
                        if ltp > 0:
                            return ltp
        except Exception:
            pass
        return None

    def _after_hours_position_safety_check(self) -> None:
        """
        During after-hours, stale intraday positions should not survive into the
        next session. This is deliberately idempotent.
        """
        try:
            open_count = len(self.live_engine.trade_manager.open_trades)
            if open_count <= 0:
                return
            logger.critical(
                "AFTER-HOURS SAFETY: %d open position(s) still active — force-closing",
                open_count,
            )
            closed = self.live_engine.trade_manager.close_positions_at_eod(
                ltp_getter=self._get_squareoff_ltp,
                reason="after_hours_stale_position_safety",
            )
            if closed:
                self._eod_squared_off = True
                try:
                    self.alerts.warning(
                        f"After-hours safety closed {closed} stale position(s).",
                        dedup_key=f"after_hours_stale_close:{datetime.now().date()}",
                    )
                except Exception:
                    pass
        except Exception:
            logger.exception("_after_hours_position_safety_check failed")


    def _check_memory_usage(self) -> None:
        """Alert if Python process memory exceeds 800MB."""
        try:
            import os, resource
            mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            mem_mb = mem_kb / 1024  # Linux reports in KB
            if mem_mb > 800:
                logger.warning("High memory usage: %.0fMB", mem_mb)
                if mem_mb > 900:
                    self.alerts.send(
                        f"⚠️ <b>HIGH MEMORY WARNING</b>\n"
                        f"Process using {mem_mb:.0f}MB\n"
                        f"Watchdog will restart if > 900MB\n"
                        f"Consider restarting manually: ./bot.sh restart"
                    )
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    def _send_after_hours_report(self, learning_result: dict) -> None:
        """Send comprehensive after-hours system improvement report."""
        try:
            tr  = learning_result.get("training_result") or {}
            sr  = learning_result.get("selector_result") or {}
            rl  = learning_result.get("rl_result") or {}

            # Today's trades
            closed = self.live_engine.trade_manager.get_closed_trades() or []
            today  = date.today()
            today_closed = [
                t for t in closed
                if isinstance(t.get("exit_time"), (int, float))
                and datetime.fromtimestamp(float(t["exit_time"])).date() == today
            ]
            wins      = sum(1 for t in today_closed if float(t.get("pnl", 0)) > 0)
            gross_pnl = sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in today_closed)
            net_pnl   = sum(float(t.get("pnl", 0)) for t in today_closed)
            costs     = gross_pnl - net_pnl

            # Backtest results from strategy_state
            bt_results = {}
            try:
                import json as _json
                state = _json.loads(
                    Path(getattr(cfg, "STRATEGY_STATE_FILE", "strategy_state.json")).read_text()
                )
                bt_results = state.get("strategies", {})
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Walk-forward results
            wf_results = None
            try:
                import json as _json
                wf_data = _json.loads(Path("walk_forward_results.json").read_text())
                wf_results = wf_data.get("results")
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Per-strategy new strategy stats from scanner
            scanner = getattr(self.live_engine, "_strategy_scanner", None)
            new_strat_stats = {}
            if scanner:
                for strat in ["orb", "vwap_reversion", "supertrend_mtf"]:
                    stats = scanner._intraday_stats.get(strat, {})
                    if stats.get("total", 0) > 0:
                        new_strat_stats[strat] = stats

            # RL best strategy
            rl_best = None
            try:
                rl_state = getattr(self.live_engine.learning_engine, "rl_state", {}) or {}
                rl_best  = max(
                    (k for k in rl_state if not k.startswith("__")),
                    key=lambda k: rl_state[k].get("score", 0) if isinstance(rl_state[k], dict) else 0,
                    default=None,
                )
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            self.alerts.after_hours_report(
                backtest_results    = bt_results if bt_results else None,
                model_improved      = bool(tr.get("trained")),
                prev_accuracy       = None,
                new_accuracy        = tr.get("val_accuracy"),
                strategies_tested   = len(sr.get("ranked", [])),
                best_strategy       = learning_result.get("selected_strategy"),
                prev_best_strategy  = self.runtime_state.current_strategy,
                wf_results          = wf_results,
                rl_trades_processed = rl.get("trades_processed", 0),
                rl_best_strategy    = rl_best,
                total_trades        = len(today_closed),
                wins                = wins,
                gross_pnl           = gross_pnl,
                net_pnl             = net_pnl,
                total_costs         = costs,
                new_strategy_stats  = new_strat_stats if new_strat_stats else None,
            )
        except Exception:
            logger.debug("_send_after_hours_report failed", exc_info=True)

    # ------------------------------------------------------------------
    # EOD square-off
    # ------------------------------------------------------------------
    def _trigger_eod_squareoff(self) -> None:
        """
        Force-close all open intraday positions at end of day.

        Fires once per trading day when the clock enters the EOD exit
        window (default: 15:15, configured via EOD_EXIT_BUFFER_MIN).
        Subsequent cycles in the same day are blocked by _eod_squared_off.
        """
        self._eod_squared_off = True
        logger.warning("EOD square-off triggered — closing all open intraday positions")

        try:
            closed = self.live_engine.trade_manager.close_positions_at_eod(
                ltp_getter=self._get_squareoff_ltp
            )

            if closed > 0:
                logger.warning("EOD square-off complete | positions_closed=%d", closed)
                if hasattr(self, "alerts") and self.alerts:
                    self.alerts.kill_switch_triggered(
                        reason=f"EOD square-off complete: {closed} position(s) closed",
                        source="health_monitor",
                    )
                if hasattr(self, "alerts") and self.alerts:
                    self.alerts.warning(
                        f"EOD square-off: closed {closed} options position(s) at "                        f"{datetime.now().strftime('%H:%M')}. No new entries until tomorrow.",
                        dedup_key="eod_squareoff",
                    )
            else:
                logger.info("EOD square-off: no open options positions to close")

        except Exception:
            logger.exception("EOD square-off failed")

    # ------------------------------------------------------------------
    # Inner loop — one iteration
    # ------------------------------------------------------------------
    def _run_holiday_off_hours_tasks(self) -> None:
        """Run backtest, ML training, evolution on holidays/weekends."""
        from datetime import datetime, date, time as dtime
        import threading
        now       = datetime.now()
        today_str = date.today().isoformat()
        dow       = date.today().weekday()  # 5=Sat, 6=Sun

        # ── 3:35 PM: EOD performance report ──────────────────────────────
        _eod_key = f"eod_perf_{today_str}"
        if not getattr(self, _eod_key, False):
            if dtime(15, 35) <= now.time() <= dtime(15, 45):
                setattr(self, _eod_key, True)
                try:
                    from performance_analytics import format_telegram_report as _ftr
                    self.alerts.send(_ftr(days=1))
                except Exception as _ep:
                    logger.debug("eod_perf: %s", _ep)

        # ══ PRE-MARKET & INTRADAY SCHEDULED TASKS ══════════════════════════
        _today_s = date.today().isoformat()

        # 7:45 AM — Swing gap warning
        if not getattr(self, f"_gap_warn_{_today_s}", False):
            if dtime(7,45) <= now.time() <= dtime(8,0):
                setattr(self, f"_gap_warn_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours._run_swing_gap_warning()
                    except Exception: pass

        # 8:00 AM — Morning video
        if not getattr(self, f"_vid_{_today_s}", False):
            if dtime(8,0) <= now.time() <= dtime(8,15):
                setattr(self, f"_vid_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours._run_morning_video()
                    except Exception: pass

        # 8:30 AM — Morning brief
        if not getattr(self, f"_brief_{_today_s}", False):
            if dtime(8,30) <= now.time() <= dtime(8,45):
                setattr(self, f"_brief_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours._run_morning_brief()
                    except Exception: pass

        # 9:00 AM — Sector rotation + F&O ban
        if not getattr(self, f"_preopn_{_today_s}", False):
            if dtime(9,0) <= now.time() <= dtime(9,10):
                setattr(self, f"_preopn_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try:
                        self._off_hours._run_sector_rotation_refresh()
                        self._off_hours._run_fno_ban_check()
                    except Exception: pass

        # 10:00/11:30/13:00/14:30 — Live position updates
        for _luh, _lum in [(10,0),(11,30),(13,0),(14,30)]:
            _lu_k = f"_lu_{_today_s}_{_luh}{_lum}"
            if not getattr(self, _lu_k, False):
                if dtime(_luh,_lum) <= now.time() <= dtime(_luh, _lum+5 if _lum<55 else 59):
                    setattr(self, _lu_k, True)
                    if _OFFHOURS_AVAIL and self._off_hours:
                        try: self._off_hours._run_live_position_update()
                        except Exception: pass

        # 15:40 — Accuracy post + heartbeat
        if not getattr(self, f"_acc_{_today_s}", False):
            if dtime(15,40) <= now.time() <= dtime(15,55):
                setattr(self, f"_acc_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try:
                        self._off_hours._run_heartbeat()
                        self._off_hours._run_accuracy_post()
                    except Exception: pass

        # 17:00 — Meta-learner retrain
        # ── Subscription expiry checks (10:30 AM) ──────────────────────────
        if not getattr(self, f'_sub_check_{_today_s}', False):
            if dtime(10,30) <= now.time() <= dtime(10,45):
                setattr(self, f'_sub_check_{_today_s}', True)
                try:
                    from subscription_engine import check_expiring_subscriptions
                    check_expiring_subscriptions(self.alerts)
                except Exception as _sce:
                    logger.debug('sub_check: %s', _sce)

        if not getattr(self, f"_ml_retrain_{_today_s}", False):
            if dtime(17,0) <= now.time() <= dtime(17,15):
                setattr(self, f"_ml_retrain_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours._run_meta_learner_training()
                    except Exception: pass

        # 18:00 — Bhavcopy download
        if not getattr(self, f"_bhav_{_today_s}", False):
            if dtime(18,0) <= now.time() <= dtime(18,15):
                setattr(self, f"_bhav_{_today_s}", True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours._run_bhavcopy_download()
                    except Exception: pass

        # ── Every 10 min during market hours: omnisource refresh ───────────
        _omni_key = f"omni_{now.hour}_{now.minute // 10}"
        if 9 <= now.hour <= 15 and not getattr(self, _omni_key, False):
            setattr(self, _omni_key, True)
            try:
                import threading as _oth
                def _omni_ref():
                    try:
                        from omnisource_news_engine import get_omnisource_intelligence as _omni_i
                        _omni_i(use_cache=False)
                    except Exception: pass
                _oth.Thread(target=_omni_ref, daemon=True, name="omni_refresh").start()
            except Exception: pass

        # Nightly backtest 4:30–5:30 PM
        # 30-min startup grace — avoid running immediately after restart
        _bot_age_hol = __import__("time").time() - getattr(self,"_uptime_start",0)
        _bt_key = f"bt_done_{today_str}"
        if not getattr(self, _bt_key, False) and _bot_age_hol > 1800:
            if dtime(16, 30) <= now.time() <= dtime(17, 30):
                setattr(self, _bt_key, True)
                if _SYSSTATE:
                    try: _get_sys_state().set("BACKTEST", "Nightly backtest")
                    except Exception: pass
                self.alerts.send(
                    "📐 <b>BACKTEST STARTED</b>\n"
                    f"Running nightly backtest — all 199 symbols\n"
                    f"🕐 {now.strftime('%H:%M')}"
                )
                def _run_bt():
                    try:
                        from autonomous_backtest import get_backtest
                        _res = get_backtest().run()
                        if _OFFHOURS_AVAIL and self._off_hours:
                            self._off_hours.send_backtest_report(_res or {})
                        else:
                            self.alerts.send(f"✅ <b>BACKTEST COMPLETE</b>\n🕐 {datetime.now().strftime('%H:%M')}")
                    except Exception as _e:
                        self.alerts.send(f"⚠️ Backtest failed: {_e}")
                    finally:
                        if _SYSSTATE:
                            try: _get_sys_state().set("HOLIDAY", "Backtest complete")
                            except Exception: pass
                _THREAD_POOL.submit(_run_bt)

        # ML training 6:00–7:30 PM
        _ml_key = f"ml_done_{today_str}"
        if not getattr(self, _ml_key, False):
            if dtime(18, 0) <= now.time() <= dtime(19, 30):
                setattr(self, _ml_key, True)
                if _SYSSTATE:
                    try: _get_sys_state().set("ML_TRAINING", "Signal log training")
                    except Exception: pass
                self.alerts.send(
                    "🧠 <b>ML TRAINING STARTED</b>\n"
                    f"Holiday — full signal log training\n"
                    f"🕐 {now.strftime('%H:%M')}"
                )
                def _run_ml():
                    try:
                        from self_learning_engine import SelfLearningEngine
                        import config as _cfg_sle
                        SelfLearningEngine(
                            strategy_state_file=getattr(_cfg_sle,"STRATEGY_STATE_FILE","strategy_state.json")
                        ).run()
                        self.alerts.send(f"✅ <b>ML TRAINING COMPLETE</b>\n🕐 {datetime.now().strftime('%H:%M')}")
                    except Exception as _e:
                        self.alerts.send(f"⚠️ ML training failed: {_e}")
                    finally:
                        if _SYSSTATE:
                            try: _get_sys_state().set("HOLIDAY", "ML complete")
                            except Exception: pass
                _THREAD_POOL.submit(_run_ml)

        # Strategy evolution Saturdays 10 AM–12 PM
        if dow == 5:
            _evo_key = f"evo_done_{today_str}"
            if not getattr(self, _evo_key, False):
                if dtime(10, 0) <= now.time() <= dtime(12, 0):
                    setattr(self, _evo_key, True)
                    if _EVO_AVAIL:
                        def _run_evo():
                            try:
                                _get_evolution(self.alerts).evolve()
                            except Exception as _e:
                                self.alerts.send(f"⚠️ Evolution failed: {_e}")
                        try:
                            _THREAD_POOL.submit(_run_evo)
                        except (RuntimeError, Exception):
                            logger.warning("Thread pool full - evo deferred")

        # Download report 8:00 PM
        _ns = f"night_sync_{today_str}"
        if not getattr(self,_ns,False) and dtime(21,25)<=now.time()<=dtime(21,40):
            setattr(self,_ns,True)
            if _DRIVE_SYNC_AVAIL:
                try:
                    _get_drive_sync(alerts=self.alerts).sync_now("both")
                    self.alerts.send(
                        f"☁️ <b>NIGHTLY SYNC DONE</b>\n"
                        f"  All code+data backed up to Drive\n"
                        f"🕐 {now.strftime('%H:%M')}")
                except Exception: pass
        _dl_key = f"dl_report_{today_str}"
        if not getattr(self, _dl_key, False):
            if dtime(20, 0) <= now.time() <= dtime(20, 10):
                setattr(self, _dl_key, True)
                if _OFFHOURS_AVAIL and self._off_hours:
                    try: self._off_hours.send_daily_download_report()
                    except Exception: pass

        # Sunday connection check 9 AM
        if dow == 6:
            _cc_key = f"conn_check_{today_str}"
            if not getattr(self, _cc_key, False):
                if dtime(9, 0) <= now.time() <= dtime(9, 15):
                    setattr(self, _cc_key, True)
                    if _OFFHOURS_AVAIL and self._off_hours:
                        try: self._off_hours._run_connection_check()
                        except Exception: pass

        import time as _t
        _t.sleep(60)

    def _run_one_iteration(self) -> None:
        """
        A single pass through the main loop.
        Separated out so the outer restart wrapper can call it cleanly.
        """
        # Day-boundary check every iteration
        self._check_and_reset_on_new_day()

        window = self._market_window()

        # Broker reconnect (every ~5 min): Angel can fail its startup login
        # (rate-limit storm) and stay obj=None all session → "No usable broker"
        # → degraded data → 0 signals/OI (this lost Friday 06-26). connect() is
        # cooldown-aware, so retrying on a timer recovers us WITHIN the day.
        try:
            _rc_now = time.time()
            if _rc_now - getattr(self, "_last_broker_reconnect_t", 0.0) > 300:
                self._last_broker_reconnect_t = _rc_now
                _bm = getattr(self.live_engine, "broker_manager", None)
                if _bm is not None and not _bm.has_any_connected_broker():
                    _rec = _bm.reconnect_unusable()
                    if _rec:
                        logger.info("Recovered %d broker(s) via reconnect", _rec)
                        try: self.alerts.send("🔌 Broker reconnected (%d) — data feed restored" % _rec,
                                              dedup_key="broker_reconnect_%s" % date.today())
                        except Exception: pass
        except Exception:
            pass

        if not window["is_trading_day"]:
            logger.debug("Non-trading day (%s)", date.today().isoformat())
            self.runtime_state.market_phase = "HOLIDAY"
            self._notify_mode_change("HOLIDAY")
            self._heartbeat("HOLIDAY")
            # Start heartbeat thread during holiday tasks
            import threading as _hbt, pathlib as _hbp, json as _hbj, time as _hbs
            _hb_active = [True]
            def _keep_alive():
                while _hb_active[0]:
                    try:
                        _hbp.Path("heartbeat.json").write_text(
                            _hbj.dumps({"ts": _hbs.time(), "phase": "HOLIDAY"}))
                    except Exception: pass
                    _hbs.sleep(30)  # update every 30s
            _hb_thread = _hbt.Thread(target=_keep_alive, daemon=True, name="HolidayHB")
            _hb_thread.start()
            try:
                self._run_holiday_off_hours_tasks()
            finally:
                _hb_active[0] = False
            self._heartbeat("HOLIDAY")
            time.sleep(60)
            return

        if window["in_trade_window"]:
            self.runtime_state.market_phase          = "LIVE"
            self.runtime_state.last_live_cycle_at    = datetime.now().isoformat()
            self._notify_mode_change("LIVE")
            self._heartbeat("LIVE")
            # Zero-signal detector
            # BUG FIX 2026-07-23: _last_signal_ts was set ONCE via a `not
            # hasattr` guard and never refreshed anywhere else in the
            # codebase (grep confirmed only these 2 lines touch it) -- so it
            # measured "time since this branch first ran today", not "time
            # since a real signal", and was guaranteed to false-alarm every
            # day once ~2h had passed regardless of actual activity. Caught
            # live: signal_log showed fresh rows every few minutes (965
            # scanned, 416 passed today) while this fired "no signals for
            # 2.1h". Now reads the real ground truth (signal_log.log_time)
            # instead of a dead counter.
            try:
                from datetime import time as _dtz
                _now_tz = datetime.now().time()
                if _dtz(10,0) <= _now_tz <= _dtz(14,30):
                    if not hasattr(self,"_zero_alerted"):   self._zero_alerted=False
                    _last_real_ts = None
                    try:
                        import sqlite3 as _sq3z
                        with _sq3z.connect("signal_log.db") as _conn_z:
                            _row_z = _conn_z.execute(
                                "SELECT MAX(log_time) FROM signal_log WHERE signal_date=?",
                                (datetime.now().strftime("%Y-%m-%d"),)).fetchone()
                        if _row_z and _row_z[0]:
                            _last_real_ts = float(_row_z[0])
                    except Exception as _sig_exc:
                        logger.debug("zero-signal detector: signal_log read failed: %s", _sig_exc)
                    if _last_real_ts is None:
                        _last_real_ts = getattr(self, "_last_signal_ts", __import__("time").time())
                    self._last_signal_ts = _last_real_ts
                    _age = (__import__("time").time()-self._last_signal_ts)/3600
                    if _age>2.0 and not self._zero_alerted:
                        self._zero_alerted=True
                        self.alerts.send(f"WARNING: No signals for {_age:.1f}h — check data feed",dedup_key="zero_sig")
                    elif _age<1.0: self._zero_alerted=False
            except Exception: pass

            # Cloud backup after market close (Google Drive + GitHub)
            from datetime import time as _dt
            _now_t = datetime.now().time()
            if _dt(15,16) <= _now_t <= _dt(15,20):
                # Google Drive backup
                if _BACKUP_AVAILABLE and self._cloud_backup:
                    try:
                        _br = self._cloud_backup.run_backup()
                        logger.info("GDrive backup: %s", _br.get("status"))
                    except Exception as _be:
                        logger.debug("GDrive backup error: %s", _be)
                # GitHub backup
                try:
                    from github_backup import run_github_backup as _ghbk
                    _ghr = _ghbk()
                    if _ghr.get("ok"):
                        logger.info("GitHub backup: %d files pushed", len(_ghr.get("pushed", [])))
                    else:
                        logger.debug("GitHub backup skipped: %s", _ghr.get("error", "not configured"))
                except Exception as _ghe:
                    logger.debug("GitHub backup error: %s", _ghe)
            # Hourly GitHub backup during trading hours (every full hour 10:00-15:00)
            if _dt(10,0) <= _now_t <= _dt(15,0) and datetime.now().minute == 0:
                try:
                    from github_backup import run_github_backup as _ghbk_h
                    _ghr_h = _ghbk_h()
                    if _ghr_h.get("ok"):
                        logger.info("GitHub hourly backup: %d files", len(_ghr_h.get("pushed", [])))
                except Exception: pass

            # Final connection gate at 9:13-9:14 AM before first scan
            try:
                from datetime import time as _dtg
                if _dtg(9,13) <= datetime.now().time() <= _dtg(9,15):
                    if _MONITOR_AVAIL and self._conn_monitor:
                        if not getattr(self,"_open_gate_checked",False):
                            self._open_gate_checked = True
                            _ready, _reason = self._conn_monitor.is_ready_to_trade()
                            if not _ready:
                                self.alerts.send(
                                    f"🚨 <b>MARKET OPEN BLOCKED</b>\n"
                                    f"Critical system failure detected:\n"
                                    f"  {_reason}\n"
                                    f"Retrying every 60s. Fix and /resume when ready.\n"
                                    f"🕐 {datetime.now().strftime(chr(37)+'H:%M')}"
                                )
                elif datetime.now().time() < _dtg(9,0):
                    self._open_gate_checked = False
            except Exception: pass

            # Send market_open alert once per day (first live cycle)
            # Regime + Intraday brief at 9:05 AM
            from datetime import time as _dtre
            if _dtre(9,4) <= datetime.now().time() <= _dtre(9,9):
                _rk = f"regime_{date.today()}"
                if not getattr(self, _rk, False):
                    setattr(self, _rk, True)
                    # Auto-send OI Builder at 9:15 AM
                    try:
                        _oik = f"oi_builder_9am_{date.today()}"
                        if not getattr(self, _oik, False):
                            setattr(self, _oik, True)
                            def _send_oi():
                                try:
                                    from oi_strike_builder import send_oi_builder
                                    send_oi_builder("NIFTY", self.alerts)
                                    send_oi_builder("BANKNIFTY", self.alerts)
                                except Exception as _e:
                                    logger.debug("OI builder: %s", _e)
                            _THREAD_POOL.submit(_send_oi)
                    except Exception: pass
                    # Send SAHI-style intraday brief after regime
                    try:
                        _ibk = f"intraday_brief_{date.today()}"
                        if not getattr(self, _ibk, False):
                            setattr(self, _ibk, True)
                            self._send_intraday_brief()
                    except Exception as _ibe: logger.debug("intraday brief: %s", _ibe)
                    if _REGIME_ENG_AVAIL:
                        try:
                            import yf_compat as _yfr
                            _vdf = _yfr.download("^INDIAVIX", period="1d", interval="1d",
                                                  progress=False, auto_adjust=True)
                            _vc  = _vdf["Close"] if _vdf is not None and len(_vdf)>0 else None
                            if _vc is not None and hasattr(_vc,"columns"): _vc=_vc.iloc[:,0]
                            _vix = float(_vc.iloc[-1]) if _vc is not None and len(_vc)>0 else 15.0
                            _ndf = self.live_engine.data_fetcher.get_market_data("NIFTY")
                            if _ndf is not None:
                                _get_regime_eng(self.alerts).update(_ndf, _vix)
                        except Exception as _re: logger.debug("Regime update: %s", _re)
            # Refresh Angel One session every 6 hours
            try:
                from datetime import time as _dts2
                _h = datetime.now().hour
                if _h in (9, 12, 15) and datetime.now().minute < 2:
                    _rk2 = f"session_refresh_{date.today()}_{_h}"
                    if not getattr(self, _rk2, False):
                        setattr(self, _rk2, True)
                        _bm = self.live_engine.broker_manager
                        if hasattr(_bm, "broker") and hasattr(_bm.broker, "refresh_session"):
                            _bm.broker.refresh_session()
            except Exception: pass
            self._send_market_open_if_needed()
            # 8:15 AM pre-market connection check
            from datetime import time as _dth
            if _dth(8,14) <= datetime.now().time() <= _dth(8,16):
                _pm_key = f"pre_mkt_health_{date.today()}"
                if not getattr(self, _pm_key, False):
                    setattr(self, _pm_key, True)
                    if _CONN_MON_AVAIL:
                        try:
                            import config as _cfg_pm
                            _get_conn_monitor(alerts=self.alerts,
                                              config=_cfg_pm).send_pre_market_report()
                        except Exception: pass

            # Silent 30-min connection check during trading
            if _MONITOR_AVAIL and self._conn_monitor:
                try:
                    _last = getattr(self,"_last_conn_check_t",0)
                    if __import__("time").time() - _last > 1800:
                        self._last_conn_check_t = __import__("time").time()
                        import threading as _thr3
                        def _mid_check():
                            # run_full_check runs all checks and sends a structured
                            # alert (deduped per day) — surfacing a degraded feed
                            # mid-session. (Was run_critical_only/send_alert_if_broken,
                            # which never existed on ConnectionMonitor → AttributeError
                            # crashed this thread every 30 min and the mid-session
                            # connection check never ran.)
                            self._conn_monitor.run_full_check("mid-session")
                        _thr3.Thread(target=_mid_check, daemon=True).start()
                except Exception: pass

            # Gap risk manager: LTP refresh + IV tracking
            if self.gap_risk_manager:
                if self.gap_risk_manager.should_refresh_option_ltp():
                    self.gap_risk_manager.refresh_option_ltp()
                if self.gap_risk_manager.should_check_expiry_roll():
                    self.gap_risk_manager.check_expiry_roll_candidates()

            # Global market filter check (every 30 min)
            if _GLOBAL_FILTER_AVAILABLE and self._global_filter:
                try:
                    _gm  = self._global_filter.get_global_bias()
                    if _gm.get("blocking") and _gm.get("change_pct",0) != 0:
                        logger.info("Global market: %s %.2f%% — monitoring",
                                    _gm.get("bias"), abs(_gm.get("change_pct",0))*100)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Swing trend invalidation check (every 30 min)
            if self.runtime_state.heartbeat_count % 60 == 30:
                self._run_swing_trend_check()

            # ── Overnight protection: EOD risk check at 2:30 PM ──────────
            if _OVERNIGHT_AVAILABLE and self._overnight_prot:
                try:
                    _now_t = datetime.now().time()
                    from datetime import time as _dt
                    if _dt(14,29) <= _now_t <= _dt(14,35):
                        _vix = self.live_engine._vix_cache_val or 15.0
                        _feeds = self.live_engine._feeds
                        _pcr   = 1.0
                        _has_event = False
                        try:
                            from event_calendar import get_event_calendar
                            _has_event = bool(get_event_calendar().get_tomorrow_events())
                        except Exception as _e:
                            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                        _ovn_result = self._overnight_prot.eod_risk_check(
                            vix=_vix, pcr=_pcr, has_event=_has_event
                        )
                        logger.info(
                            "Overnight risk: uncertainty=%.0f%% action=%s closed=%d",
                            _ovn_result.get("uncertainty",0)*100,
                            _ovn_result.get("action","?"),
                            _ovn_result.get("positions_closed",0),
                        )
                    # 0DTE strangle check at 1:50 PM on expiry days
                    elif _dt(13,48) <= _now_t <= _dt(13,55):
                        try:
                            from expiry_strategy import (
                                is_expiry_today, get_0dte_signal
                            )
                            if is_expiry_today("NIFTY"):
                                _spot = 0.0
                                try:
                                    _spot = float(
                                        self.live_engine.ltp_cache.get("NIFTY") or 0
                                    )
                                except Exception as _e:
                                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                                if _spot > 10000:
                                    import config as _c0
                                    _vix = getattr(_c0, "VIX_MAX_FOR_BUYING", 15.0)
                                    _0dte = get_0dte_signal(
                                        spot_price=_spot,
                                        max_pain=0,
                                        vix=_vix,
                                        underlying="NIFTY",
                                    )
                                    if _0dte.get("action") == "STRANGLE_SELL":
                                        logger.info(
                                            "0DTE strangle signal: %s score=%.1f",
                                            _0dte.get("reason",""),
                                            _0dte.get("score",0),
                                        )
                                        _msg = (
                                            f"🎯 <b>0DTE STRANGLE SIGNAL</b>\n"
                                            f"{_0dte.get('reason','')}\n"
                                            f"Score: {_0dte.get('score',0):.1f}\n"
                                            f"Premium est: ₹{_0dte.get('total_premium',0):.0f}/lot\n"
                                            f"Hard exit: {_0dte.get('hard_exit','15:10')}"
                                        )
                                        self.alerts.send(_msg, dedup_key="0dte_today")
                        except Exception as _0e:
                            logger.debug("0DTE check: %s", _0e)

                    # Pre-market check at 8:45 AM
                    elif _dt(8,44) <= _now_t <= _dt(8,50):
                        _prev_close = getattr(self, "_prev_nifty_close", 22000.0)
                        _pm_result = self._overnight_prot.premarket_check(_prev_close)
                        logger.info("Pre-market gap check: %s", _pm_result.get("gap_data",{}))
                    # Post-open assessment at 9:22 AM
                    elif _dt(9,21) <= _now_t <= _dt(9,25):
                        _spot = 0.0
                        try:
                            _spot = float(self.live_engine.ltp_cache.get("NIFTY") or 0)
                        except Exception as _e:
                            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                        if _spot > 10000:
                            _prev = getattr(self, "_prev_nifty_close", _spot)
                            _pa = self._overnight_prot.post_open_assessment(_spot, _prev)
                            if _pa.get("positions_closed", 0) > 0:
                                logger.warning(
                                    "Post-open gap close: %d positions | gap=%.1f%%",
                                    _pa["positions_closed"],
                                    _pa.get("actual_gap_pct",0)*100,
                                )
                except Exception as _oe:
                    logger.debug("Overnight protection error: %s", _oe)

            # Dual mode balance re-check (every 30 min during session)
            if _DUAL_MODE_AVAILABLE and self._dual_engine:
                try:
                    _dual_status = self._dual_engine.run_balance_check()
                    if _dual_status.get("live_enabled"):
                        self._apply_order_block(False, "dual_mode_periodic_funded")
                        self._sync_runtime_capital(float(_dual_status.get("balance", 0) or 0))
                        self.runtime_state.mode = "LIVE"
                    else:
                        self._apply_order_block(True, "dual_mode_periodic_unfunded")
                        self.runtime_state.mode = "PAPER"
                    # Keep live_signal_engine in sync
                    if hasattr(self.live_engine, "_dual_engine"):
                        self.live_engine._dual_engine = self._dual_engine
                except Exception: pass

            # Auto mode re-evaluation (every 30 min)
            if _AUTOMODE_AVAILABLE and self.auto_mode:
                if self.runtime_state.heartbeat_count % 60 == 15:
                    try:
                        _open = len(self.live_engine.trade_manager.get_open_positions())
                        _result = self.auto_mode.evaluate(open_positions=_open)
                        if _result.get("switched"):
                            import config as _cfg_live
                            if _result.get("is_live"):
                                self._apply_order_block(False, "auto_mode_periodic_live")
                                self.runtime_state.mode  = "LIVE"
                            else:
                                if (
                                    getattr(self, "_dual_engine", None)
                                    and self._dual_engine.is_live_enabled()
                                ):
                                    self._apply_order_block(False, "dual_mode_overrides_auto_periodic")
                                    self.runtime_state.mode = "LIVE"
                                else:
                                    # _cfg_live.PAPER_TRADING = True  # DISABLED: paper_trade kills data fetch

                                    _cfg_pm = __import__("config")

                                    self._apply_order_block(True, "auto_mode_periodic_paper")
                                    # _cfg_live.PAPER_TRADE   = True  # DISABLED: use PAPER_ORDERS_ONLY instead
                                    self.runtime_state.mode  = "PAPER"
                                    _paper_cap = float(getattr(_cfg_pm, "PAPER_CAPITAL",
                                                       getattr(_cfg_pm, "CAPITAL", 100000)))
                                    if _paper_cap > 0:
                                        self._sync_runtime_capital(_paper_cap)
                            logger.info(
                                "Auto mode switched to %s | balance=₹%.0f",
                                _result["mode"], _result["balance"],
                            )
                    except Exception as _e:
                        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Memory check (hourly)
            if self.runtime_state.heartbeat_count % 120 == 0:
                self._check_memory_usage()

            # Check theta decay on open option positions
            self._check_option_theta_exits()

            # Track every qualified generated signal against cached 1-minute
            # bars and publish target/SL lifecycle updates every ~5 minutes.
            if self.runtime_state.heartbeat_count % 10 == 0:
                try:
                    from autonomous_signal_lifecycle import (
                        update_generated_signal_lifecycle, send_lifecycle_digest)
                    send_lifecycle_digest(update_generated_signal_lifecycle())
                except Exception as _siglife_exc:
                    logger.debug("generated signal lifecycle: %s", _siglife_exc)

            # SL hunt protection: detect wick stops
            self._run_sl_hunt_cycle()

            # Friday weekend protection
            self._run_friday_protection()

            # 15-minute status broadcast
            self._send_15min_status_if_due()

            # Hourly P&L update during session
            self._send_hourly_update_if_due()

            # Log live data feed status every hour
            if self.runtime_state.heartbeat_count % 120 == 60:  # offset from memory check
                self._log_live_data_status()
                self._check_learning_loop_health()   # alert if signal_log.db stalled

            # Capital compounding checks
            self._check_capital_milestone()
            self._check_drawdown_alert()
            self._check_profit_lock()

            # High-impact event day: apply conservative overrides
            self._apply_high_impact_day_overrides()

            # EOD square-off: force-close all options positions before close
            if window.get("in_eod_exit_window") and not self._eod_squared_off:
                self._trigger_eod_squareoff()

            # Only run new signal evaluation if NOT in EOD window
            if not window.get("in_eod_exit_window"):
                # If live balance is unknown, keep scanning and paper logging.
                # Capital failure must block real orders, not signal generation.
                _cap_now = getattr(self.live_engine.trade_manager, "capital", 1)
                if (not bool(getattr(__import__("config"), "PAPER_TRADING", True)) and
                        _cap_now <= 0):
                    _cfg_scan = __import__("config")
                    _paper_cap = float(getattr(_cfg_scan, "PAPER_CAPITAL",
                                       getattr(_cfg_scan, "CAPITAL", 100000)))
                    _cfg_scan.PAPER_ORDERS_ONLY = True
                    self.runtime_state.mode = "PAPER"
                    self.live_engine.total_capital = _paper_cap
                    self.live_engine.trade_manager.capital = _paper_cap
                    self.live_engine.risk_manager.capital = _paper_cap
                    self.live_engine.capital_allocator.update_total(_paper_cap)
                    logger.warning(
                        "Live balance is ₹0/unknown — continuing scan in "
                        "PAPER_ORDERS_ONLY with PAPER_CAPITAL ₹%.0f.",
                        _paper_cap,
                    )
                logger.info("Market open — running live engine cycle")
                self.live_engine._run_cycle()

                # scanned=0 alarm — turn the otherwise silent ERROR log into a
                # Telegram ping when the scan comes up empty several cycles in a
                # row (Angel session / data feed down). Alert once per outage,
                # then confirm recovery. State is lazily initialised via getattr.
                _scanned = getattr(self.live_engine, "_last_scan_count", 0)
                if _scanned <= 0:
                    self._zero_scan_streak = getattr(self, "_zero_scan_streak", 0) + 1
                    if (self._zero_scan_streak >= 3 and
                            not getattr(self, "_zero_scan_alerted", False)):
                        self._zero_scan_alerted = True
                        try:
                            self.alerts.critical(
                                "🚨 SCANNED: 0 for %d cycles — no market data.\n"
                                "Likely Angel session / data feed down. Auto-recovery "
                                "is retrying. Run /diagscan or /fixangel."
                                % self._zero_scan_streak
                            )
                        except Exception:
                            logger.exception("zero-scan alert failed")
                else:
                    if getattr(self, "_zero_scan_alerted", False):
                        try:
                            self.alerts.send(
                                "✅ Scan recovered — market data flowing again "
                                "(%d symbols)." % _scanned)
                        except Exception:
                            pass
                    self._zero_scan_streak = 0
                    self._zero_scan_alerted = False

                # degraded-scan alarm — scanning is alive but resolving far
                # fewer symbols than the ~190 universe (partial token-resolution
                # / rate-limit failure, as in the 2026-06-15 storm where the
                # count was low, not strictly 0). Same 3-cycle debounce +
                # recovery pattern as the zero-scan alarm. Tune via env
                # SCAN_LOW_WATERMARK (default 20 ≈ 10% of the universe).
                _low_wm = int(getattr(cfg, "SCAN_LOW_WATERMARK", 20))
                if 0 < _scanned < _low_wm:
                    self._low_scan_streak = getattr(self, "_low_scan_streak", 0) + 1
                    if (self._low_scan_streak >= 3 and
                            not getattr(self, "_low_scan_alerted", False)):
                        self._low_scan_alerted = True
                        try:
                            self.alerts.critical(
                                "⚠️ Scan DEGRADED — only %d symbols scanned for %d "
                                "cycles (healthy ≈ universe size). Suspect token-"
                                "resolution / rate-limit. Run /diagscan."
                                % (_scanned, self._low_scan_streak)
                            )
                        except Exception:
                            logger.exception("low-scan alert failed")
                elif _scanned >= _low_wm:
                    if getattr(self, "_low_scan_alerted", False):
                        try:
                            self.alerts.send(
                                "✅ Scan back to healthy breadth (%d symbols)."
                                % _scanned)
                        except Exception:
                            pass
                    self._low_scan_streak = 0
                    self._low_scan_alerted = False

                # Free intraday OI accrual — throttled (default 15min), market-hours
                # only, best-effort. Stores per-strike OI/IV snapshots so the
                # intraday OI-flow hypothesis can be validated later (no paid feed).
                try:
                    import intraday_oi_logger
                    intraday_oi_logger.maybe_snapshot("NIFTY")
                except Exception:
                    pass

                # Option-scalp SIGNAL — throttled, market-hours only, best-effort.
                # Emits ATM momentum-breakout scalp setups on the indices and
                # journals them (-> option Telegram card + accrues for measurement).
                # Signal-only, places no orders; disabled unless OPTION_SCALPER_ENABLED.
                try:
                    import option_scalper
                    option_scalper.maybe_scan()
                except Exception:
                    pass

            time.sleep(max(5, int(getattr(cfg, "MAIN_LOOP_SLEEP_SEC", 30))))

        elif window["in_opening_window"]:
            # Market is open but we are in the no-trade window (09:15-09:20).
            # Heartbeat but do not place new orders.
            self.runtime_state.market_phase = "OPENING_WAIT"
            self._notify_mode_change("OPENING_WAIT")
            self._heartbeat("OPENING_WAIT")
            # Pre-market gap risk check for swing trades
            # Get GIFT Nifty gap before gap check
            _gift_gap = self._get_gift_nifty_gap()
            if abs(_gift_gap) > 0.003:  # > 0.3% gap
                logger.info("GIFT Nifty gap: %.2f%% — gap risk check heightened",
                            _gift_gap * 100)
            if self.gap_risk_manager and self.gap_risk_manager.should_run_gap_check():
                closed = self.gap_risk_manager.run_gap_check()
                # Also run SwingProtectionEngine gap analysis for finer-grained decisions
                if _SLHG_MA:
                    try:
                        sp      = _get_swing_protect_ma(
                            trade_manager = self.live_engine.trade_manager,
                            alerts        = self.alerts,
                        )
                        gap_pct = _gift_gap if abs(_gift_gap) > 0 else 0.0
                        gift_px = self.live_engine._feeds.gift_nifty.get_price() if (
                            self.live_engine._feeds) else 0.0
                        for trade in self.live_engine.trade_manager.get_open_positions():
                            meta = trade.get("metadata",{}) or {}
                            if not meta.get("style") == "swing": continue
                            decision = sp.handle_gap_open(
                                trade_id    = trade.get("trade_id",""),
                                symbol      = trade.get("symbol",""),
                                side        = trade.get("side","BUY"),
                                entry_price = float(trade.get("entry_price",0)),
                                stop_loss   = float(trade.get("stop_loss",0)),
                                current_ltp = float(trade.get("entry_price",0)),
                                gap_pct     = gap_pct,
                                gift_nifty  = gift_px,
                            )
                            if decision.get("action") == "CLOSE_FULL":
                                self.live_engine.trade_manager._close_trade_internal(
                                    trade.get("trade_id",""), 0.0,
                                    decision.get("reason","gap_protection"))
                    except Exception as _e:
                        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                if closed:
                    logger.info("Gap risk check closed %d swing trades", len(closed))
            logger.info(
                "Opening window — holding new entries until %s",
                window.get("trade_start"),
            )
            time.sleep(10)

        elif window["in_market"]:
            # Market is open but outside the trade window (pre-open or buffer)
            self.runtime_state.market_phase = "MARKET_BUFFER"
            self._notify_mode_change("MARKET_BUFFER")
            self._heartbeat("MARKET_BUFFER")
            time.sleep(30)

        else:
            # Outside market hours
            # Heartbeat thread during after-hours tasks
            import threading as _hbt2, pathlib as _hbp2, json as _hbj2, time as _hbs2
            _hb2_active = [True]
            def _keep_alive2():
                while _hb2_active[0]:
                    try:
                        _hbp2.Path("heartbeat.json").write_text(
                            _hbj2.dumps({"ts": _hbs2.time(), "phase": "AFTER_HOURS"}))
                    except Exception: pass
                    _hbs2.sleep(30)
            _hb2t = _hbt2.Thread(target=_keep_alive2, daemon=True, name="AfterHrsHB")
            _hb2t.start()
            try:
                self._after_hours_tasks()
            finally:
                _hb2_active[0] = False
            self._heartbeat("AFTER_HOURS")
            time.sleep(300)

    # ------------------------------------------------------------------
    # Main run — outer restart wrapper
    # ------------------------------------------------------------------
    def run(self) -> None:
        self._startup()
        self._startup_position_safety_check()
        self.runtime_state.running = True
        self._save_runtime_state()
        logger.info("Autonomous system started | mode=%s", self.runtime_state.mode)
        self._start_watchdog_heartbeat()
        # ── Start idle engine ────────────────────────────────────────────
        # Startup heartbeat thread — keeps watchdog calm during long init
        try:
            import threading as _hbt, pathlib as _hbp2, json as _hbj2, time as _hbs
            def _startup_heartbeat():
                for _ in range(90):  # 90 × 60s = 90 min max
                    try:
                        _hbp2.Path("heartbeat.json").write_text(
                            _hbj2.dumps({"ts": _hbs.time(), "phase": "STARTUP"}))
                    except Exception: pass
                    _hbs.sleep(60)
            _hbt.Thread(target=_startup_heartbeat, daemon=True, name="StartupHB").start()
        except Exception: pass
        if _IDLE_AVAIL:
            try:
                _idle = _get_idle(alerts=self.alerts)
                _idle.start()
                logger.info("IdleEngine started")
            except Exception as _ie:
                logger.debug("IdleEngine: %s", _ie)
        from datetime import time as _dtoih
        # Google Drive pull/auto-deploy is intentionally disabled. Backups may
        # still push to Drive, but deployment is manual to prevent code overwrite.
        # 2026-07-10: this was gated on BOOT time being 08:45-15:45 — but the
        # bot restarts in the evening, so the tracker never started and
        # oi_tracker_state.json sat frozen for a month (last daytime boot,
        # 06-09). The tracker's own loop already sleeps outside 09:14-15:32,
        # so the boot-time gate was redundant and harmful. Start always.
        if _OI_TRACKER_AVAIL:
            try:
                _oit = _get_oi_tracker(alerts=self.alerts)
                _oit.start()
                logger.info("OITracker started (sleeps until market hours)")
            except Exception as _oite:
                logger.debug("OITracker: %s", _oite)

        # 2026-07-13: the 5-min option snapshot recorder had NO runner (no
        # systemd unit, no cron — run_option_snapshot_recorder.sh was wired
        # to nothing). The only snapshots came from the live-engine hook at
        # scan pace (~26 min), so "5-min" OI-flow deltas were computed over
        # 26-minute gaps all along. Run the loop in-process like OITracker;
        # it self-sleeps outside market hours.
        try:
            import os as _snap_os
            import threading as _snap_thr
            from option_chain_recorder import run_snapshot_loop as _snap_loop
            _snap_syms = [s.strip().upper() for s in _snap_os.getenv(
                "SNAPSHOT_OPTION_UNDERLYINGS", "NIFTY,BANKNIFTY,FINNIFTY"
            ).split(",") if s.strip()]
            _snap_thr.Thread(
                target=_snap_loop, args=(_snap_syms,),
                kwargs={"interval_sec": int(_snap_os.getenv(
                    "OPTION_CHAIN_SNAPSHOT_INTERVAL_SEC", "300"))},
                daemon=True, name="OptionSnapshotRecorder",
            ).start()
            logger.info("Option snapshot recorder started (5-min, in-process)")
        except Exception as _sre:
            logger.debug("snapshot recorder: %s", _sre)

        # Init LLM trading agent
        self._agent = None
        if _AGENT_AVAILABLE:
            try:
                self._agent = _get_agent(alerts=self.alerts)
                if self._agent.is_available():
                    logger.info("LLM Trading Agent active (Claude)")
                else:
                    logger.info("LLM Agent loaded but no ANTHROPIC_API_KEY — agent disabled")
            except Exception as _ae:
                logger.debug("Agent init: %s", _ae)

        # Load per-symbol backtest params into signal engine
        try:
            from pathlib import Path as _P
            _sp = _P("symbol_params.json")
            if _sp.exists():
                import json as _j
                _params = _j.loads(_sp.read_text())
                # Make params available to signal_engine via module-level var
                import signal_engine as _se
                _se._SYMBOL_PARAMS = _params
                logger.info("Loaded per-symbol params for %d symbols", len(_params))
        except Exception: pass

        # ── SAFETY: warn loudly if live mode with no real balance ─────────
        try:
            import config as _sc
            _paper = bool(getattr(_sc, "PAPER_TRADING",      True))
            _paper_orders_only = bool(getattr(_sc, "PAPER_ORDERS_ONLY", False))
            _live  = bool(getattr(_sc, "ENABLE_REAL_TRADING", False))
            if not _paper and not _paper_orders_only and _live:
                # Check actual balance
                _broker = self.live_engine.broker_manager.get_execution_broker()
                _real_bal = 0.0
                if _broker and hasattr(_broker, "angel") and _broker.angel:
                    _orig = _broker.angel.paper_trade
                    _broker.angel.paper_trade = False
                    try:
                        _real_bal = float(_broker.angel.get_balance(force_real=True) or 0)
                    finally:
                        _broker.angel.paper_trade = _orig
                if _real_bal <= 0 and getattr(self, "_dual_engine", None):
                    try:
                        _dual_status = self._dual_engine.get_status()
                        if _dual_status.get("live_enabled") and float(_dual_status.get("balance", 0) or 0) > 0:
                            _real_bal = float(_dual_status.get("balance", 0) or 0)
                    except Exception:
                        pass
                # Only warn if both Angel returns 0 AND .env REAL_CAPITAL is also 0
                import os as _ose
                _env_real = float(_ose.getenv("REAL_CAPITAL","0") or 0)
                _no_balance = (_real_bal in (0.0,1_000_000.0,100_000.0) or _real_bal < 100) and _env_real < 1000
                if _no_balance:
                    logger.critical(
                        "⚠️  LIVE MODE ENABLED BUT NO REAL BALANCE DETECTED!\n"
                        "   PAPER_TRADING=false + ENABLE_REAL_TRADING=true\n"
                        "   but Angel One balance = ₹%.0f\n"
                        "   If you have no funds, set PAPER_TRADING=true in .env",
                        _real_bal
                    )
                    try:
                        self.alerts.send(
                            "🚨 <b>WARNING: LIVE MODE WITH NO BALANCE</b>\n"
                            "PAPER_TRADING=false but Angel One balance is ₹0\n"
                            "Set PAPER_TRADING=true in .env to run safely\n"
                            "Run: ./bot.sh restart after fixing .env",
                            dedup_key="live_no_balance_warning"
                        )
                    except Exception as _e:
                        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                else:
                    logger.info("Live mode: Angel One balance = ₹%.0f", _real_bal)
        except Exception as _se:
            logger.debug("Startup safety check: %s", _se)

        consecutive_failures = 0
        backoff = RESTART_BACKOFF_BASE

        # Set phase immediately so watchdog uses correct limit
        try:
            from connection_monitor import get_monitor as _gm_ph
            _cm_ph = _gm_ph()
            if hasattr(_cm_ph, "is_market_hours") and not _cm_ph.is_market_hours():
                self.runtime_state.market_phase = "HOLIDAY"
                self._save_runtime_state()
        except Exception: pass

        while True:
            try:
                # Always update heartbeat — prevents watchdog killing on holiday
                try:
                    import pathlib as _hbp, json as _hbj, time as _hbt
                    _hbp.Path("heartbeat.json").write_text(
                        _hbj.dumps({"ts": _hbt.time(),
                                    "phase": self.runtime_state.market_phase}))
                except Exception: pass
                self._run_one_iteration()
                # Successful iteration — reset failure counter
                consecutive_failures = 0
                backoff = RESTART_BACKOFF_BASE

            except KeyboardInterrupt:
                logger.info("Stopped by user (KeyboardInterrupt)")
                self.db.log_event("INFO", "STOP", "Stopped by user")
                break

            except Exception as exc:
                consecutive_failures += 1
                self.runtime_state.restart_count += 1

                logger.exception(
                    "Unhandled error in main loop (attempt %d/%d): %s",
                    consecutive_failures, MAX_RESTART_ATTEMPTS, exc,
                )
                self.db.log_event("ERROR", "LOOP_ERROR", str(exc))
                _append_jsonl(
                    MAIN_LIVE_LOG,
                    {
                        "timestamp": datetime.now().isoformat(),
                        "event":     "LOOP_ERROR",
                        "attempt":   consecutive_failures,
                        "error":     str(exc),
                    },
                )

                if consecutive_failures >= MAX_RESTART_ATTEMPTS:
                    logger.critical(
                        "Too many consecutive failures (%d) — exiting for supervisor restart",
                        consecutive_failures,
                    )
                    self.db.log_event(
                        "CRITICAL", "MAX_FAILURES",
                        f"Exiting after {consecutive_failures} consecutive failures",
                    )
                    self.runtime_state.running      = False
                    self.runtime_state.market_phase = "CRASHED"
                    self._save_runtime_state()
                    sys.exit(1)

                logger.info(
                    "Restarting loop in %ds (attempt %d/%d) ...",
                    backoff, consecutive_failures, MAX_RESTART_ATTEMPTS,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, RESTART_BACKOFF_MAX)

        # Clean shutdown
        self.runtime_state.running      = False
        self.runtime_state.market_phase = "STOPPED"
        self._save_runtime_state()
        _append_jsonl(
            MAIN_LIVE_LOG,
            {"timestamp": datetime.now().isoformat(), "event": "SHUTDOWN"},
        )


# =============================================================================
# ENTRY
# =============================================================================

def _setup_rotating_logger() -> None:
    """Configure rotating file handler — max 50MB, keep 5 files."""
    import logging.handlers, os
    log_file = os.getenv('LOG_FILE', 'trading_bot.log')
    handler  = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=50*1024*1024, backupCount=5, encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
    ))
    root = logging.getLogger()
    root.addHandler(handler)
    install_secret_redaction(root.handlers)
    root.setLevel(getattr(logging, os.getenv('LOG_LEVEL','INFO').upper(), logging.INFO))
    logging.getLogger(__name__).info('Rotating log: %s (50MB x 5)', log_file)


if __name__ == "__main__":
    _setup_rotating_logger()
    system = AutonomousTradingSystem()
    system.run()
