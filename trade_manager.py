"""
trade_manager.py

Trade lifecycle manager with persistent journaling.

Fixes applied
-------------
1. Missing lock_trading() / unlock_trading() methods
   DailyLossLimitManager.lock() calls self.trade_manager.lock_trading(reason)
   KillSwitch.trigger() does the same.
   Neither method existed — both callers would raise AttributeError the
   moment any limit was breached, silently swallowing the lock.

2. Risk-approved quantity was ignored
   LiveSignalEngine._execute_candidate():
       risk_decision = risk_manager.evaluate_new_trade(...)
       final_qty = risk_decision.approved_quantity   # e.g. 50
       trade_manager.open_trade(..., qty=final_qty)  # passed but NOT used

   Inside open_trade(), AdaptivePositionSizer runs again independently
   and its result was used as the final quantity, discarding the
   PortfolioRiskManager approval entirely.

   Fix: open_trade() accepts an optional qty_override parameter. When
   provided (always from _execute_candidate), that quantity is used and
   the internal sizer is skipped. When None, the sizer runs as before
   (backwards-compatible for direct callers).

3. get_closed_trades() returned "trade_id" but SelfLearningEngine
   watermark logic reads "id"
   self.rl_state.get("__last_processed_trade_id__", 0) checks
   int(t.get("id", 0)) on every trade. With the key named "trade_id",
   this always returned 0 and ALL trades were reprocessed every learning
   cycle (compounding the original RL double-counting bug).
   Fixed: both "id" and "trade_id" are now included in the returned dict.

4. _place_order_via_broker() hardcoded exchange="NFO"
   Cash equity trades (ENABLE_CASH_EQUITY_EXECUTION=True) were being
   routed to NFO exchange, causing broker rejections.
   Fixed: accepts an exchange parameter, defaults to "NFO" only when
   not provided — consistent with prior behaviour for options.

5. _calculate_today_realized_pnl() called time.strftime()/localtime()
   on every iteration. Moved date string computation outside the loop.
"""

from __future__ import annotations
import threading
try:
    from capital_recycler import get_recycler as _get_recycler
    _RECYCLER_AVAIL = True
except ImportError:
    _RECYCLER_AVAIL = False
try:
    from feature_importance import get_tracker as _get_fi
    _FI_AVAIL = True
except ImportError:
    _FI_AVAIL = False

try:
    from dual_mode_engine import get_dual_engine as _get_dual
    _DUAL_AVAILABLE = True
except ImportError:
    _DUAL_AVAILABLE = False

import json
import logging
import os
import sqlite3

def _wal_connect(db_path: str, **kwargs):
    """Open SQLite connection with WAL mode for better concurrent access."""
    conn = sqlite3.connect(db_path, **kwargs, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
    except Exception as _e:
        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
    return conn

import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from alerts import AlertManager
from broker_manager import BrokerManager
from adaptive_position_sizer import AdaptivePositionSizer

# Accurate NSE cost model. _calculate_pnl() referenced _CC_AVAILABLE +
# calculate_net_pnl without importing them (NameError when it ran); import them
# here with the same guarded pattern as main_autonomous / live_signal_engine.
try:
    from capital_compounder import calculate_net_pnl
    _CC_AVAILABLE = True
except Exception:
    _CC_AVAILABLE = False



def estimate_slippage(symbol: str, price: float, side: str) -> float:
    """Estimate realistic slippage for fill quality tracking."""
    indices = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}
    nifty50 = {"RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR",
               "SBIN","BHARTIARTL","KOTAKBANK","LT"}
    if symbol.upper() in indices:
        pct = 0.0003
    elif symbol.upper() in nifty50:
        pct = 0.0005
    else:
        pct = 0.001
    direction = 1 if side == "BUY" else -1
    return round(price * pct * direction, 2)


logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

def check_and_blacklist_symbol(symbol: str, db_path: str = "trades.db") -> bool:
    """
    IMPROVEMENT 3: Auto-blacklist symbols with 3+ SL hits in 7 days.
    Returns True if symbol is blacklisted (skip trading).
    """
    import sqlite3, json, os
    from datetime import datetime, timedelta

    _BL_FILE = "symbol_blacklist.json"
    # Load existing blacklist
    try:
        bl = json.loads(open(_BL_FILE).read()) if os.path.exists(_BL_FILE) else {}
    except Exception:
        bl = {}

    # Expire old entries (2 weeks)
    now = datetime.now()
    bl = {k:v for k,v in bl.items() if datetime.fromisoformat(v) > now}

    if symbol.upper() in bl:
        return True  # still blacklisted

    # Check recent SL hits
    try:
        conn  = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute('PRAGMA journal_mode=WAL')
        cutoff_ts = (now - timedelta(days=7)).timestamp()
        rows  = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE symbol=? AND exit_reason LIKE '%stop%' "
            "AND entry_time > ? AND status='CLOSED'",
            (symbol.upper(), cutoff_ts)
        ).fetchone()
        conn.close()
        sl_hits = rows[0] if rows else 0
        if sl_hits >= 3:
            bl[symbol.upper()] = (now + timedelta(days=14)).isoformat()
            with open(_BL_FILE,'w') as f: json.dump(bl, f, indent=2)
            import logging
            logging.getLogger(__name__).warning(
                "BLACKLISTED %s — %d SL hits in 7 days (2-week ban)", symbol, sl_hits)
            return True
    except Exception as _e:
        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
    return False


@dataclass
class ManagedTrade:
    trade_id:          str
    symbol:            str
    side:              str
    qty:               int
    strategy:          str
    broker_name:       str
    order_id:          str
    entry_price:       float
    entry_time:        float
    stop_loss:         Optional[float]        = None
    target_price:      Optional[float]        = None
    trail_stop:        Optional[float]        = None
    entry_atr:         Optional[float]        = None
    highest_price:     float                  = field(init=False)
    lowest_price:      float                  = field(init=False)
    status:            str                    = "OPEN"
    exit_price:        Optional[float]        = None
    exit_time:         Optional[float]        = None
    exit_reason:       Optional[str]          = None
    realized_pnl:      float                  = 0.0
    mode:              str                    = "PAPER"  # PAPER or LIVE
    trade_type:        str                    = "PAPER"
    confidence:        Optional[float]        = None
    regime:            Optional[str]          = None
    score:             Optional[float]        = None
    correlation_group: Optional[str]          = None
    sl_order_id:       Optional[str]          = None   # broker-side SL-M order
    metadata:          Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.highest_price = float(self.entry_price)
        self.lowest_price  = float(self.entry_price)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ManagedTrade":
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {"raw_metadata": metadata}

        trade = cls(
            trade_id         = str(row.get("trade_id")),
            symbol           = str(row.get("symbol")),
            side             = str(row.get("side")),
            qty              = int(row.get("qty", 0)),
            strategy         = str(row.get("strategy") or "AUTO"),
            broker_name      = str(row.get("broker_name") or "UNKNOWN"),
            order_id         = str(row.get("order_id") or ""),
            entry_price      = float(row.get("entry_price", 0.0)),
            entry_time       = float(row.get("entry_time", time.time())),
            stop_loss        = _safe_float(row.get("stop_loss")),
            target_price     = _safe_float(row.get("target_price")),
            trail_stop       = _safe_float(row.get("trail_stop")),
            entry_atr        = _safe_float(row.get("entry_atr")),
            status           = str(row.get("status") or "OPEN"),
            exit_price       = _safe_float(row.get("exit_price")),
            exit_time        = _safe_float(row.get("exit_time")),
            exit_reason      = row.get("exit_reason"),
            realized_pnl     = float(row.get("realized_pnl") or 0.0),
            mode             = str(row.get("mode") or "PAPER"),
            trade_type       = str(row.get("trade_type") or row.get("mode") or "PAPER"),
            confidence       = _safe_float(row.get("confidence")),
            regime           = row.get("regime"),
            score            = _safe_float(row.get("score")),
            correlation_group= row.get("correlation_group"),
            metadata         = metadata if isinstance(metadata, dict) else None,
        )
        trade.highest_price = float(row.get("highest_price") or trade.entry_price)
        trade.lowest_price  = float(row.get("lowest_price")  or trade.entry_price)
        return trade


def _safe_float(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


# =============================================================================
# PERSISTENT JOURNAL STORE
# =============================================================================

class TradeJournalStore:
    _trade_lock = threading.Lock()
    MAX_SECTOR_POSITIONS = 2  # max positions in same sector

    def __init__(self, db_path: str = "trades.db") -> None:
        self.db_path = str(db_path)
        self._ensure_parent_dir()
        self._init_db()

    def _ensure_parent_dir(self) -> None:
        Path(self.db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + writes
        conn.execute("PRAGMA synchronous=NORMAL") # faster, still safe
        conn.execute("PRAGMA cache_size=-32000")  # 32MB page cache
        return conn

    def _get_columns(self, conn: sqlite3.Connection, table_name: str) -> List[str]:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cur.fetchall()]

    def _add_column_if_missing(
        self, conn: sqlite3.Connection, table_name: str,
        column_name: str, column_def: str,
    ) -> None:
        if column_name not in self._get_columns(conn, table_name):
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

    def _init_db(self) -> None:
        conn = self._connect()
        cur  = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                qty INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                broker_name TEXT,
                order_id TEXT,
                entry_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                stop_loss REAL,
                target_price REAL,
                trail_stop REAL,
                entry_atr REAL,
                highest_price REAL,
                lowest_price REAL,
                status TEXT NOT NULL,
                exit_price REAL,
                exit_time REAL,
                exit_reason TEXT,
                realized_pnl REAL DEFAULT 0,
                confidence REAL,
                regime TEXT,
                score REAL,
                correlation_group TEXT,
                sl_order_id TEXT,
                metadata TEXT,
                mode TEXT DEFAULT 'PAPER',
                trade_type TEXT DEFAULT 'PAPER',
                gross_pnl REAL DEFAULT 0,
                brokerage REAL DEFAULT 0,
                stt REAL DEFAULT 0,
                exchange_charge REAL DEFAULT 0,
                sebi_levy REAL DEFAULT 0,
                gst REAL DEFAULT 0,
                stamp_duty REAL DEFAULT 0,
                total_charges REAL DEFAULT 0,
                cumulative_pnl REAL DEFAULT 0,
                holding_minutes INTEGER DEFAULT 0,
                r_multiple REAL DEFAULT 0,
                paper_pnl REAL DEFAULT 0,
                live_pnl REAL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                signal_metadata TEXT DEFAULT '{}',
                entry_reason TEXT DEFAULT '',
                win_rate_at_entry REAL DEFAULT 0
            )
        """)
        conn.commit()

        required_columns = {
            "trail_stop":        "REAL",
            "mode":              "TEXT",
            "entry_atr":         "REAL",
            "trade_type":        "TEXT DEFAULT 'PAPER'",
            "gross_pnl":         "REAL DEFAULT 0",
            "brokerage":         "REAL DEFAULT 0",
            "stt":               "REAL DEFAULT 0",
            "exchange_charge":   "REAL DEFAULT 0",
            "sebi_levy":         "REAL DEFAULT 0",
            "gst":               "REAL DEFAULT 0",
            "stamp_duty":        "REAL DEFAULT 0",
            "total_charges":     "REAL DEFAULT 0",
            "cumulative_pnl":    "REAL DEFAULT 0",
            "holding_minutes":   "INTEGER DEFAULT 0",
            "r_multiple":        "REAL DEFAULT 0",
            "paper_pnl":         "REAL DEFAULT 0",
            "live_pnl":          "REAL DEFAULT 0",
            "highest_price":     "REAL",
            "lowest_price":      "REAL",
            "confidence":        "REAL",
            "regime":            "TEXT",
            "score":             "REAL",
            "correlation_group": "TEXT",
            "metadata":          "TEXT",
            "created_at":        "REAL DEFAULT 0",
            "updated_at":        "REAL DEFAULT 0",
            "sl_order_id":       "TEXT",
        }
        for col_name, col_def in required_columns.items():
            self._add_column_if_missing(conn, "trades", col_name, col_def)
        conn.commit()

        now_ts = time.time()
        for col in ("entry_time", "created_at", "updated_at"):
            conn.execute(
                f"UPDATE trades SET {col} = ? WHERE {col} IS NULL OR {col} = 0", (now_ts,)
            )
        conn.execute(
            "UPDATE trades SET highest_price = COALESCE(highest_price, entry_price) WHERE highest_price IS NULL"
        )
        conn.execute(
            "UPDATE trades SET lowest_price = COALESCE(lowest_price, entry_price) WHERE lowest_price IS NULL"
        )
        conn.commit()

        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time  ON trades(exit_time)")
        conn.commit()
        conn.close()

    def upsert_trade(self, trade: ManagedTrade) -> None:
        now_ts        = time.time()
        metadata_json = json.dumps(trade.metadata or {}, default=str)

        # ── Derive new analysis columns ───────────────────────────────────────
        meta        = trade.metadata or {}
        cost        = getattr(trade, "cost_breakdown", meta.get("costs", {}))
        is_dual     = meta.get("dual_mode", False)
        is_live     = str(meta.get("live_order_id", "")).strip() not in ("", "None")

        # trade_type: PAPER / LIVE / DUAL_PAPER / DUAL_LIVE
        if is_dual:
            trade_type = "DUAL_LIVE" if is_live else "DUAL_PAPER"
        elif trade.mode == "LIVE" or is_live:
            trade_type = "LIVE"
        else:
            trade_type = "PAPER"
        trade.trade_type = trade_type

        gross_pnl       = getattr(trade, "gross_pnl", meta.get("gross_pnl", 0.0))
        brokerage       = float(cost.get("brokerage",       0))
        stt             = float(cost.get("stt",             0))
        exchange_charge = float(cost.get("exchange_charge", 0))
        sebi_levy       = float(cost.get("sebi_levy",       0))
        gst             = float(cost.get("gst",             0))
        stamp_duty      = float(cost.get("stamp_duty",      0))
        total_charges   = float(cost.get("total",           brokerage+stt+exchange_charge+sebi_levy+gst+stamp_duty))

        # holding_minutes
        holding_minutes = 0
        if trade.exit_time and trade.entry_time:
            holding_minutes = max(0, int((trade.exit_time - trade.entry_time) / 60))

        # r_multiple: how many R did we actually make/lose?
        r_multiple = 0.0
        if trade.stop_loss and trade.entry_price and trade.exit_price:
            planned_risk = abs(float(trade.entry_price) - float(trade.stop_loss))
            if planned_risk > 0:
                actual_pnl_per_unit = (float(trade.exit_price) - float(trade.entry_price))                     if trade.side == "BUY" else                     (float(trade.entry_price) - float(trade.exit_price))
                r_multiple = round(actual_pnl_per_unit / planned_risk, 2)

        # cumulative_pnl: sum of all closed realized_pnl up to and including this trade
        cumulative_pnl = 0.0
        try:
            conn_c = self._connect()
            row = conn_c.cursor().execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM trades WHERE status='CLOSED'"
            ).fetchone()
            conn_c.close()
            cumulative_pnl = float(row[0] or 0) + float(trade.realized_pnl or 0)
        except Exception:
            cumulative_pnl = float(trade.realized_pnl or 0)

        # paper_pnl vs live_pnl (dual mode)
        paper_pnl = float(trade.realized_pnl or 0) if trade_type in ("PAPER","DUAL_PAPER") else 0.0
        live_pnl  = float(trade.realized_pnl or 0) if trade_type in ("LIVE","DUAL_LIVE")  else 0.0

        conn = self._connect()
        conn.cursor().execute("""
            INSERT INTO trades (
                trade_id, symbol, side, qty, strategy, broker_name, order_id,
                entry_price, entry_time, stop_loss, target_price, trail_stop,
                entry_atr, highest_price, lowest_price, status, exit_price,
                exit_time, exit_reason, realized_pnl, confidence, regime, score,
                correlation_group, metadata, mode,
                trade_type, gross_pnl, brokerage, stt, exchange_charge,
                sebi_levy, gst, stamp_duty, total_charges, cumulative_pnl,
                holding_minutes, r_multiple, paper_pnl, live_pnl,
                created_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(trade_id) DO UPDATE SET
                symbol=excluded.symbol, side=excluded.side, qty=excluded.qty,
                strategy=excluded.strategy, broker_name=excluded.broker_name,
                order_id=excluded.order_id, entry_price=excluded.entry_price,
                entry_time=excluded.entry_time, stop_loss=excluded.stop_loss,
                target_price=excluded.target_price, trail_stop=excluded.trail_stop,
                entry_atr=excluded.entry_atr, highest_price=excluded.highest_price,
                lowest_price=excluded.lowest_price, status=excluded.status,
                exit_price=excluded.exit_price, exit_time=excluded.exit_time,
                exit_reason=excluded.exit_reason, realized_pnl=excluded.realized_pnl,
                confidence=excluded.confidence, regime=excluded.regime,
                score=excluded.score, correlation_group=excluded.correlation_group,
                metadata=excluded.metadata, mode=excluded.mode,
                trade_type=excluded.trade_type, gross_pnl=excluded.gross_pnl,
                brokerage=excluded.brokerage, stt=excluded.stt,
                exchange_charge=excluded.exchange_charge, sebi_levy=excluded.sebi_levy,
                gst=excluded.gst, stamp_duty=excluded.stamp_duty,
                total_charges=excluded.total_charges, cumulative_pnl=excluded.cumulative_pnl,
                holding_minutes=excluded.holding_minutes, r_multiple=excluded.r_multiple,
                paper_pnl=excluded.paper_pnl, live_pnl=excluded.live_pnl,
                updated_at=excluded.updated_at
        """, (
            trade.trade_id, trade.symbol, trade.side, trade.qty,
            trade.strategy, trade.broker_name, trade.order_id,
            trade.entry_price, trade.entry_time, trade.stop_loss,
            trade.target_price, trade.trail_stop, trade.entry_atr,
            trade.highest_price, trade.lowest_price, trade.status,
            trade.exit_price, trade.exit_time, trade.exit_reason,
            trade.realized_pnl, trade.confidence, trade.regime,
            trade.score, trade.correlation_group, metadata_json,
            trade.mode,
            # new columns
            trade_type, round(float(gross_pnl), 2),
            round(brokerage, 2), round(stt, 2), round(exchange_charge, 2),
            round(sebi_levy, 4), round(gst, 2), round(stamp_duty, 4),
            round(total_charges, 2), round(cumulative_pnl, 2),
            holding_minutes, r_multiple,
            round(paper_pnl, 2), round(live_pnl, 2),
            now_ts, now_ts,
        ))
        conn.commit()
        conn.close()

    def load_all_trades(self) -> List[ManagedTrade]:
        conn = self._connect()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM trades ORDER BY entry_time ASC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return [ManagedTrade.from_row(r) for r in rows]

    def load_open_trades(self) -> List[ManagedTrade]:
        conn = self._connect()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_time ASC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return [ManagedTrade.from_row(r) for r in rows]

    def _send_close_notification(self, trade: dict) -> None:
        """Send Telegram notification when trade closes."""
        try:
            from ux_engine import format_trade_close_notification
            msg = format_trade_close_notification(trade)
            alerts = getattr(self, "_alerts", None)
            if alerts:
                alerts.send(msg)
        except Exception as e:
            import logging
            logging.getLogger("trade_manager").debug("close_notify: %s", e)

    def load_closed_trades(self) -> List[ManagedTrade]:
        conn = self._connect()
        cur  = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE status = 'CLOSED' "
            "ORDER BY COALESCE(exit_time, entry_time) ASC"
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return [ManagedTrade.from_row(r) for r in rows]


# =============================================================================
# TRADE MANAGER
# =============================================================================

class TradeManager:
    """
    Trade lifecycle manager with persistent journaling, adaptive sizing,
    daily loss lock, and ML-ready closed trade export.
    """

    def __init__(
        self,
        broker_manager:     Optional[BrokerManager]  = None,
        alert_manager:      Optional[AlertManager]   = None,
        capital:            float                    = 100_000,
        max_open_positions: int                      = 1,
        daily_loss_limit:   float                    = 3000.0,
        brokerage_per_order: float                   = 20.0,
        stt_rate: float                              = 0.0015,   # Budget 2026: 0.15% options STT from Apr 1 2026 STT
        exchange_charges_rate: float                 = 0.0,      # set to ~0.00019 for full model
        enable_trailing:    bool                     = True,
        db_path:            str                      = "trades.db",
        restore_state:      bool                     = True,
    ) -> None:
        self.broker_manager      = broker_manager
        self.alerts              = alert_manager
        self.capital             = float(capital)
        self.max_open_positions  = int(max_open_positions)
        self.daily_loss_limit    = float(daily_loss_limit)
        self.brokerage_per_order      = float(brokerage_per_order)
        self.stt_rate                 = float(stt_rate)
        self.exchange_charges_rate    = float(exchange_charges_rate)
        self.enable_trailing     = bool(enable_trailing)

        self.position_sizer = AdaptivePositionSizer()
        self.store          = TradeJournalStore(db_path=db_path)

        self.open_trades:   Dict[str, ManagedTrade] = {}
        self.closed_trades: List[ManagedTrade]      = []

        self._trade_lock      = threading.Lock()
        self._trade_counter    = 1
        self.daily_realized_pnl = 0.0
        self.trading_locked    = False
        self.lock_reason: Optional[str] = None

        if restore_state:
            self._restore_from_store()

    # ------------------------------------------------------------------
    # State restore
    # ------------------------------------------------------------------
    def _restore_from_store(self) -> None:
        """
        Restore trades from SQLite, then reconcile open positions against
        the broker to catch fills or rejections that occurred while the
        process was down.

        Reconciliation rules
        --------------------
        SIM / paper orders  → accepted as-is (no broker to query)
        Real order_id       → query broker.get_order_status()
          FILLED / COMPLETE → keep as OPEN (normal)
          REJECTED / CANCELLED → mark ORPHANED in metadata, remove from open_trades
          None / timeout    → keep as OPEN with a WARNING (broker may be slow)
        """
        try:
            all_trades  = self.store.load_all_trades()
            max_counter = 0

            for trade in all_trades:
                try:
                    if trade.trade_id.startswith("T"):
                        max_counter = max(max_counter, int(trade.trade_id[1:]))
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

                if trade.status == "OPEN":
                    self.open_trades[trade.trade_id] = trade
                else:
                    self.closed_trades.append(trade)

            self._trade_counter     = max_counter + 1 if max_counter > 0 else 1
            self.daily_realized_pnl = self._calculate_today_realized_pnl()

            if self.daily_realized_pnl <= -abs(self.daily_loss_limit):
                self.trading_locked = True
                self.lock_reason    = f"Daily loss limit breached: {self.daily_realized_pnl:.2f}"

            logger.info(
                "TradeManager restored | open=%s closed=%s daily_pnl=%.2f",
                len(self.open_trades), len(self.closed_trades), self.daily_realized_pnl,
            )

            # Reconcile open trades against broker
            self._reconcile_open_trades_with_broker()

        except Exception:
            logger.exception("Failed to restore trade manager state")

    def _reconcile_open_trades_with_broker(self) -> None:
        """
        Verify each open trade's order_id against the broker.

        Called once at startup after _restore_from_store(). Safe to call
        when broker_manager is None (paper trading, no broker available).
        """
        if not self.open_trades:
            return

        if self.broker_manager is None:
            logger.info("Reconciliation skipped — no broker manager")
            return

        if not hasattr(self.broker_manager, "get_order_status"):
            logger.info("Reconciliation skipped — broker has no get_order_status")
            return

        logger.info("Reconciling %d open trade(s) against broker...", len(self.open_trades))
        orphaned: List[str] = []

        for trade_id, trade in list(self.open_trades.items()):
            order_id = str(trade.order_id or "").strip()

            # Skip simulated or paper orders — nothing to verify
            if not order_id or order_id.startswith(("SIM", "PAPER", "PAPER_SL")):
                logger.debug("Reconcile skipped (simulated) | trade_id=%s", trade_id)
                continue

            try:
                status_raw = self.broker_manager.get_order_status(
                    order_id=order_id,
                    exchange=trade.metadata.get("exchange", "NFO")
                    if isinstance(trade.metadata, dict) else "NFO",
                )
            except Exception as exc:
                logger.warning(
                    "Reconcile: broker query failed | trade_id=%s order_id=%s error=%s",
                    trade_id, order_id, exc,
                )
                continue

            if status_raw is None:
                logger.warning(
                    "Reconcile: no status returned | trade_id=%s order_id=%s "                    "— keeping as OPEN (broker may be slow)",
                    trade_id, order_id,
                )
                continue

            status_str = str(status_raw).upper()
            if isinstance(status_raw, dict):
                status_str = str(status_raw.get("status", "")).upper()

            if status_str in ("FILLED", "COMPLETE", "EXECUTED", "OPEN", "PENDING", "TRIGGER PENDING"):
                logger.info(
                    "Reconcile OK | trade_id=%s order_id=%s broker_status=%s",
                    trade_id, order_id, status_str,
                )
            elif status_str in ("REJECTED", "CANCELLED", "CANCELED", "FAILED"):
                logger.critical(
                    "Reconcile MISMATCH: order was %s at broker but DB shows OPEN | "                    "trade_id=%s order_id=%s — marking orphaned",
                    status_str, trade_id, order_id,
                )
                orphaned.append(trade_id)

                # Update metadata to record the discrepancy
                meta = trade.metadata or {}
                meta["reconciliation_status"] = status_str
                meta["reconciliation_at"]     = time.time()
                trade.metadata = meta
                self._persist_trade(trade)
            else:
                logger.warning(
                    "Reconcile: unexpected broker status=%s | trade_id=%s — keeping as OPEN",
                    status_str, trade_id,
                )

        # Remove orphaned trades from the active open_trades dict
        # They remain in the DB with their metadata flag for audit purposes
        for trade_id in orphaned:
            trade = self.open_trades.pop(trade_id, None)
            if trade:
                trade.status      = "ORPHANED"
                trade.exit_reason = f"reconciliation_{trade.metadata.get('reconciliation_status', 'unknown')}".lower()
                trade.exit_time   = time.time()
                self.closed_trades.append(trade)
                self._persist_trade(trade)
                logger.critical(
                    "Orphaned trade removed from active positions | trade_id=%s", trade_id
                )

        if orphaned:
            logger.critical(
                "Reconciliation complete: %d orphaned trade(s) removed from active positions. "                "Review trades.db for records.",
                len(orphaned),
            )
        else:
            logger.info("Reconciliation complete: all open trades verified with broker")

    # ------------------------------------------------------------------
    # Lock control — called by DailyLossLimitManager and KillSwitch
    # ------------------------------------------------------------------
    def lock_trading(self, reason: str) -> None:
        """
        Lock all new trade entry.
        Called by DailyLossLimitManager.lock() and KillSwitch.trigger().
        """
        self.trading_locked = True
        self.lock_reason    = str(reason)
        logger.warning("Trading locked by TradeManager | reason=%s", reason)

    def unlock_trading(self) -> None:
        """
        Unlock trading (e.g. new trading day reset or kill-switch reset).
        Called by DailyLossLimitManager.reset_day() and KillSwitch.reset().
        """
        self.trading_locked = False
        self.lock_reason    = None
        logger.info("Trading unlocked by TradeManager")

    def reset_daily_state(self) -> None:
        """
        Reset day-scoped accumulators. Called by main_autonomous on new trading day.
        """
        self.daily_realized_pnl = 0.0
        self.trading_locked     = False
        self.lock_reason        = None
        logger.info("TradeManager daily state reset | date=%s", date.today().isoformat())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _cancel_stuck_orders(self) -> None:
        """Auto-cancel orders pending >90 seconds and re-place as market order."""
        import time
        now = time.time()
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT trade_id, symbol, side, qty, entry_price, strategy "
                "FROM trades WHERE status='PENDING' AND created_at < ?",
                (now - 90,)
            ).fetchall()
            conn.close()
            for row in rows:
                tid, sym, side, qty, ep, strat = row
                logger.warning("Stuck order detected: %s %s — cancelling", sym, tid)
                try:
                    if hasattr(self, 'broker_manager') and self.broker_manager:
                        self.broker_manager.cancel_order(tid)
                except Exception: pass
                # Mark as failed
                try:
                    conn2 = self._connect()
                    conn2.execute(
                        "UPDATE trades SET status='FAILED', exit_reason='stuck_order_timeout' "
                        "WHERE trade_id=?", (tid,))
                    conn2.commit(); conn2.close()
                except Exception: pass
                if hasattr(self, 'alerts') and self.alerts:
                    self.alerts.send(
                        f"⚠️ Stuck order cancelled: {sym} {side}\n"
                        f"  Order pending >90s — check broker app",
                        dedup_key=f"stuck_{tid}", dedup_cooldown_override=3600
                    )
        except Exception as e:
            logger.debug("cancel_stuck_orders: %s", e)


    def _persist_trade(self, trade: ManagedTrade) -> None:
        with self._trade_lock:
            return self.__persist_trade_inner(trade)

    def __persist_trade_inner(self, trade: ManagedTrade) -> None:
        try:
            self.store.upsert_trade(trade)
        except Exception:
            logger.exception("Failed to persist trade | trade_id=%s", trade.trade_id)

    def _next_trade_id(self) -> str:
        trade_id = f"T{self._trade_counter:06d}"
        self._trade_counter += 1
        return trade_id

    # ------------------------------------------------------------------
    # P&L helpers
    # ------------------------------------------------------------------
    def _calculate_pnl(
        self,
        trade: ManagedTrade,
        exit_price: float,
        is_options: bool = True,
    ) -> float:
        """
        Net P&L after ALL NSE transaction costs.
        Also populates trade.gross_pnl and trade.cost_breakdown dict
        so upsert_trade() can write individual charge columns.
        """
        gross_pnl   = 0.0
        net_pnl     = 0.0
        cost_dict   = {}

        if _CC_AVAILABLE:
            gross_pnl, net_pnl, costs = calculate_net_pnl(
                entry_price       = float(trade.entry_price),
                exit_price        = float(exit_price),
                qty               = int(trade.qty),
                side              = trade.side,
                brokerage_per_leg = self.brokerage_per_order,
                is_options        = is_options,
            )
            cost_dict = costs.to_dict()
        else:
            # Legacy fallback
            if trade.side == "BUY":
                gross_pnl = (float(exit_price) - float(trade.entry_price)) * int(trade.qty)
            else:
                gross_pnl = (float(trade.entry_price) - float(exit_price)) * int(trade.qty)
            brokerage   = 2.0 * self.brokerage_per_order
            stt         = float(exit_price) * int(trade.qty) * self.stt_rate if is_options else 0
            exchange    = float(exit_price) * int(trade.qty) * self.exchange_charges_rate
            cost_dict   = {
                "brokerage":       brokerage,
                "stt":             stt,
                "exchange_charge": exchange,
                "sebi_levy":       0.0,
                "gst":             0.0,
                "stamp_duty":      0.0,
                "total":           brokerage + stt + exchange,
            }
            net_pnl = gross_pnl - cost_dict["total"]

        # Attach to trade object for upsert_trade() to write to DB columns
        trade.gross_pnl    = round(float(gross_pnl), 2)
        trade.cost_breakdown = cost_dict

        # Also store in metadata for backward compatibility
        if hasattr(trade, "metadata") and isinstance(trade.metadata, dict):
            trade.metadata["costs"]     = cost_dict
            trade.metadata["gross_pnl"] = round(float(gross_pnl), 2)

        return round(float(net_pnl), 2)

    def _is_option_trade(self, trade: ManagedTrade) -> bool:
        """Best-effort asset classifier used for exchange and cost selection."""
        meta = trade.metadata if isinstance(trade.metadata, dict) else {}
        asset_type = str(meta.get("asset_type", "")).upper()
        symbol = str(getattr(trade, "symbol", "") or "").upper()
        return (
            asset_type == "OPTION"
            or symbol.endswith(("CE", "PE"))
            or " CE" in symbol
            or " PE" in symbol
        )

    def _trade_exchange(self, trade: ManagedTrade) -> str:
        return "NFO" if self._is_option_trade(trade) else "NSE"

    def _shadow_option_outcomes(
        self,
        trade: ManagedTrade,
        *,
        selected_exit_price: float,
        exit_reason: str,
    ) -> list:
        meta = trade.metadata if isinstance(trade.metadata, dict) else {}
        candidates = meta.get("shadow_candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return []

        broker = None
        try:
            if self.broker_manager:
                broker = self.broker_manager.get_execution_broker()
        except Exception:
            broker = None

        outcomes = []
        selected_symbol = str(getattr(trade, "symbol", "") or "")
        qty = int(getattr(trade, "qty", 0) or 0)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("symbol", "") or "")
            entry = float(candidate.get("premium", 0.0) or 0.0)
            if not symbol or entry <= 0 or qty <= 0:
                continue
            exit_price = 0.0
            if symbol == selected_symbol:
                exit_price = float(selected_exit_price or 0.0)
            elif broker and hasattr(broker, "get_ltp"):
                try:
                    exit_price = float(broker.get_ltp(symbol, exchange="NFO") or 0.0)
                except TypeError:
                    try:
                        exit_price = float(broker.get_ltp(symbol) or 0.0)
                    except Exception:
                        exit_price = 0.0
                except Exception:
                    exit_price = 0.0
            if exit_price <= 0:
                continue
            pnl = round((exit_price - entry) * qty, 2)
            outcomes.append({
                "symbol": symbol,
                "strike": candidate.get("strike", 0),
                "option_type": candidate.get("option_type", ""),
                "label": 1 if pnl > 0 else -1 if pnl < 0 else 0,
                "pnl": pnl,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
            })
        return outcomes

    def _is_swing_trade(self, trade: ManagedTrade) -> bool:
        meta = trade.metadata if isinstance(trade.metadata, dict) else {}
        return str(meta.get("style", "intraday")).lower() == "swing"

    def _cap_live_qty_to_balance(
        self,
        requested_qty: int,
        entry_price: float,
        balance: float,
        metadata: Optional[Dict[str, Any]],
        side: str,
    ) -> int:
        """
        Return the largest live quantity that can fit in current cash.

        Paper trades keep the strategy-requested size; only the real linked leg
        is capped. For options, quantity must be a full lot.
        """
        qty = int(requested_qty or 0)
        price = float(entry_price or 0.0)
        cash = float(balance or 0.0)
        if qty <= 0 or price <= 0 or cash <= 0:
            return 0

        try:
            import config as _cfg_live_qty
            use_pct = float(getattr(_cfg_live_qty, "LIVE_BALANCE_USE_PCT", 0.95))
            default_lot = int(getattr(_cfg_live_qty, "OPTION_LOT_SIZE", 65))
        except Exception:
            use_pct = 0.95
            default_lot = 65
        use_pct = max(0.05, min(1.0, use_pct))

        meta = metadata if isinstance(metadata, dict) else {}
        is_option = str(meta.get("asset_type", "")).upper() == "OPTION"
        lot_size = int(meta.get("lot_size") or default_lot or 1)
        lot_size = max(1, lot_size)

        usable_cash = cash * use_pct
        affordable_units = int(usable_cash // price)
        if affordable_units <= 0:
            return 0

        capped_qty = min(qty, affordable_units)
        if is_option:
            capped_qty = (capped_qty // lot_size) * lot_size
            if capped_qty < lot_size:
                return 0

        return max(0, int(capped_qty))

    def _calculate_today_realized_pnl(self) -> float:
        today = date.today().isoformat()   # compute once outside loop
        try:
            conn = self.store._connect()
            row = conn.cursor().execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0)
                FROM trades
                WHERE status='CLOSED'
                  AND date(exit_time,'unixepoch','localtime') = ?
                """,
                (today,),
            ).fetchone()
            conn.close()
            return round(float(row[0] or 0.0), 2)
        except Exception:
            total = 0.0
            for trade in self.closed_trades:
                if not trade.exit_time:
                    continue
                trade_day = time.strftime("%Y-%m-%d", time.localtime(trade.exit_time))
                if trade_day == today:
                    total += float(trade.realized_pnl)
            return round(total, 2)

    def _today_trade_counts(self) -> Dict[str, int]:
        today = date.today().isoformat()
        try:
            conn = self.store._connect()
            row = conn.cursor().execute(
                """
                SELECT
                    SUM(CASE WHEN date(entry_time,'unixepoch','localtime') = ? THEN 1 ELSE 0 END) AS opened_today,
                    SUM(CASE WHEN status='CLOSED'
                              AND date(exit_time,'unixepoch','localtime') = ?
                             THEN 1 ELSE 0 END) AS closed_today
                FROM trades
                """,
                (today, today),
            ).fetchone()
            conn.close()
            return {
                "opened_today": int(row[0] or 0),
                "closed_today": int(row[1] or 0),
            }
        except Exception:
            return {"opened_today": 0, "closed_today": 0}

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def _can_open_new_trade(self, symbol: Optional[str] = None, side: Optional[str] = None) -> bool:
        if self.trading_locked:
            logger.warning("Trading locked | reason=%s", self.lock_reason)
            return False
        if len(self.open_trades) >= self.max_open_positions:
            logger.warning("Max open positions reached (%d)", self.max_open_positions)
            return False
        if symbol and side:
            for trade in self.open_trades.values():
                if trade.symbol == symbol and trade.side == side and trade.status == "OPEN":
                    logger.warning("Duplicate same-side trade blocked | symbol=%s side=%s", symbol, side)
                    return False
        return True

    # ------------------------------------------------------------------
    # Broker order placement
    # ------------------------------------------------------------------

    def _place_broker_sl_order(
        self, trade: "ManagedTrade", exchange: str = "NFO"
    ) -> Optional[str]:
        """
        Place a Stop-Loss Market (SL-M) order at the broker as a safety net.

        For a BUY trade: we place a SELL SL-M at the stop_loss price.
        For a SELL trade: we place a BUY SL-M at the stop_loss price.

        The broker will execute this if price reaches the trigger — even
        if our system is offline. This is critical for live trading safety.
        """
        if not trade.stop_loss or not self.broker_manager:
            return None

        meta = trade.metadata or {}
        live_order_id = str(meta.get("live_order_id", "")).strip()

        # Skip for pure simulated/paper orders.  A paper trade may still have a
        # linked live leg in dual mode; only that case needs broker-side SL.
        if str(trade.order_id or "").startswith(("SIM", "PAPER")) and not live_order_id:
            return None

        try:
            broker = self.broker_manager.get_execution_broker()
            if not broker or not hasattr(broker, "place_sl_order"):
                return None

            close_side = "SELL" if trade.side == "BUY" else "BUY"
            sl_order_id = broker.place_sl_order(
                symbol        = trade.symbol,
                qty           = trade.qty,
                buy_sell      = close_side,
                trigger_price = float(trade.stop_loss),
                exchange      = exchange,
            )
            if sl_order_id:
                logger.info(
                    "Broker SL-M order placed | trade_id=%s symbol=%s "
                    "trigger=%.2f sl_order_id=%s",
                    trade.trade_id, trade.symbol,
                    trade.stop_loss, sl_order_id,
                )
            else:
                logger.warning(
                    "Broker SL-M order FAILED | trade_id=%s symbol=%s — "
                    "using software stop only",
                    trade.trade_id, trade.symbol,
                )
            return sl_order_id
        except Exception as exc:
            logger.warning(
                "_place_broker_sl_order error for %s: %s", trade.symbol, exc
            )
            return None

    def _update_broker_sl_order(
        self,
        trade:         "ManagedTrade",
        new_stop:      float,
        exchange:      str = "NFO",
    ) -> None:
        """
        Cancel the old broker SL-M order and place a new one at new_stop.
        Called by the trailing stop engine when stop level improves.
        """
        if not self.broker_manager or not new_stop:
            return

        try:
            broker = self.broker_manager.get_execution_broker()
            if not broker or not hasattr(broker, "place_sl_order"):
                return

            # Cancel existing SL order
            old_sl_id = trade.sl_order_id
            if old_sl_id and hasattr(broker, "cancel_order"):
                cancelled = broker.cancel_order(old_sl_id)
                if not cancelled:
                    logger.debug(
                        "Failed to cancel old SL order %s — may already be expired",
                        old_sl_id,
                    )

            # Place new SL order at improved stop level
            close_side  = "SELL" if trade.side == "BUY" else "BUY"
            new_sl_id   = broker.place_sl_order(
                symbol        = trade.symbol,
                qty           = trade.qty,
                buy_sell      = close_side,
                trigger_price = float(new_stop),
                exchange      = exchange,
            )

            trade.sl_order_id = new_sl_id
            if isinstance(trade.metadata, dict):
                trade.metadata["sl_order_id"] = new_sl_id

            if new_sl_id:
                logger.debug(
                    "Broker SL updated | trade_id=%s new_stop=%.2f new_sl_id=%s",
                    trade.trade_id, new_stop, new_sl_id,
                )
        except Exception as exc:
            logger.debug("_update_broker_sl_order error: %s", exc)

    def _cancel_broker_sl_order(
        self, trade: "ManagedTrade"
    ) -> None:
        """Cancel broker SL-M order when closing position normally."""
        sl_id = getattr(trade, "sl_order_id", None)
        if not sl_id or not self.broker_manager:
            return
        try:
            broker = self.broker_manager.get_execution_broker()
            if broker and hasattr(broker, "cancel_order"):
                broker.cancel_order(sl_id)
                logger.debug("Broker SL cancelled on close: %s", sl_id)
        except Exception as exc:
            logger.debug("_cancel_broker_sl_order error: %s", exc)

    # SEBI Apr-2026: market orders prohibited for algo trading.
    # All orders must be LIMIT. Tolerance is configurable; 0.3% default is
    # wide enough to fill in normal conditions while staying SEBI-compliant.
    _LIMIT_TOL = float(os.getenv("LIMIT_ORDER_TOLERANCE", "0.003"))

    # SEBI Apr-2026: every automated order must carry an audit tag identifying
    # it as algo-originated. Angel sanitizes/truncates to <=20 alphanumeric chars.
    _ALGO_TAG_PREFIX = os.getenv("ALGO_ORDER_TAG_PREFIX", "ALGO")

    @classmethod
    def _build_order_tag(cls, strategy: str = "") -> str:
        """Build the SEBI algo-order audit tag from a strategy name."""
        return f"{cls._ALGO_TAG_PREFIX}{strategy or 'BOT'}"

    def _place_order_via_broker(
        self, symbol: str, qty: int, buy_sell: str,
        exchange: str = "NFO",
        ref_price: float = 0.0,
        order_tag: str = "",
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (broker_name, order_id).
        Places a LIMIT order at ref_price ± LIMIT_ORDER_TOLERANCE (SEBI Apr-2026).
        If ref_price is not provided, attempts to fetch LTP from the broker.
        Falls back to MARKET with a warning only when no price is available.
        """
        if self.broker_manager is None:
            logger.warning("Broker manager unavailable; returning simulated order")
            return "SIM", f"SIM-{int(time.time())}"

        # Resolve limit price
        _ref = float(ref_price or 0.0)
        if _ref <= 0:
            try:
                _br = self.broker_manager.get_execution_broker()
                if _br and hasattr(_br, "get_ltp"):
                    _ref = float(_br.get_ltp(symbol) or 0)
            except Exception:
                pass

        if _ref > 0:
            tol   = self._LIMIT_TOL
            lp    = _ref * (1.0 + tol) if buy_sell.upper() == "BUY" else _ref * (1.0 - tol)
            lp    = round(lp, 2)
            otype = "LIMIT"
        else:
            logger.warning(
                "SEBI-LIMIT: no ref_price for %s %s — falling back to MARKET",
                buy_sell, symbol,
            )
            lp    = 0
            otype = "MARKET"

        _tag = order_tag or self._build_order_tag()

        if hasattr(self.broker_manager, "place_order"):
            try:
                result = self.broker_manager.place_order(
                    symbol=symbol, qty=qty, buy_sell=buy_sell,
                    order_type=otype, price=lp, exchange=exchange,
                    order_tag=_tag,
                )
                if isinstance(result, tuple) and len(result) == 2:
                    return result[0], result[1]
            except Exception:
                logger.exception("broker_manager.place_order failed")

        if hasattr(self.broker_manager, "place_order_with_fallback"):
            try:
                result = self.broker_manager.place_order_with_fallback(
                    symbol=symbol, qty=qty, buy_sell=buy_sell,
                    required_balance=0, order_type=otype, price=lp, exchange=exchange,
                    order_tag=_tag,
                )
                if isinstance(result, tuple) and len(result) == 2:
                    order_id, broker_name = result
                    return broker_name, order_id
            except Exception:
                logger.exception("broker_manager.place_order_with_fallback failed")

        logger.warning("No compatible broker order method found; returning simulated order")
        return "SIM", f"SIM-{int(time.time())}"

    # ------------------------------------------------------------------
    # Public position accessors
    # ------------------------------------------------------------------
    def get_open_positions(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.open_trades.values()]

    def get_closed_positions(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self.closed_trades]

    def get_closed_trades(self) -> List[Dict[str, Any]]:
        """
        Export closed trades for SelfLearningEngine.

        IMPORTANT: includes both "id" (integer for RL watermark) and
        "trade_id" (string identifier) so the watermark logic in
        SelfLearningEngine works correctly.
        """
        rows: List[Dict[str, Any]] = []
        for idx, t in enumerate(self.closed_trades):
            metadata    = t.metadata or {}
            signal_data = metadata.get("signal_data", {}) if isinstance(metadata, dict) else {}

            rows.append({
                "id":          idx + 1,        # sequential int for RL watermark
                "trade_id":    t.trade_id,     # string identifier
                "symbol":      t.symbol,
                "side":        t.side,
                "strategy":    t.strategy,
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "pnl":         t.realized_pnl,
                "mode":        getattr(t, "mode", "PAPER"),
                "trade_type":  getattr(t, "trade_type", getattr(t, "mode", "PAPER")),
                "confidence":  float(t.confidence or 0.0),
                "score":       float(t.score or 0.0),
                "regime":      t.regime or "UNKNOWN",
                "regime_score": float(signal_data.get("directional_score", t.score or 0.0) or 0.0),
                "volatility":  float(metadata.get("volatility", 0.0) if isinstance(metadata, dict) else 0.0),
                "entry_atr":   float(t.entry_atr or 0.0),
                "exit_reason": t.exit_reason,
                "entry_time":  t.entry_time,
                "exit_time":   t.exit_time,
                "metadata":    metadata,
            })
        return rows

    def get_daily_pnl(self) -> float:
        self.daily_realized_pnl = self._calculate_today_realized_pnl()
        return float(self.daily_realized_pnl)

    # ------------------------------------------------------------------
    # Open trade
    # ------------------------------------------------------------------
    def _get_trading_mode(self) -> str:
        """Returns PAPER or LIVE based on broker connection state."""
        try:
            if self.broker_manager:
                broker = self.broker_manager.get_execution_broker()
                if broker and hasattr(broker, "paper_trade"):
                    return "PAPER" if broker.paper_trade else "LIVE"
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        return "PAPER"

    def open_trade(
        self,
        # force_paper_if_insufficient: if capital < position size,
        # trade is logged as PAPER instead of being rejected

        symbol:            str,
        side:              str,
        strategy:          str,
        entry_price:       float,
        stop_loss:         float,
        target_price:      float,
        score:             float,
        regime:            str,
        atr:               float,
        confidence:        Optional[float]        = None,
        correlation_group: Optional[str]          = None,
        metadata:          Optional[Dict[str, Any]] = None,
        qty_override:      Optional[int]          = None,
        exchange:          str                    = "NFO",
    ) -> Optional[str]:
        """
        Open a new trade.

        qty_override: when provided (e.g. from PortfolioRiskManager
        approval), use this quantity directly and skip the internal
        AdaptivePositionSizer.  This ensures the risk-manager's decision
        is actually honoured rather than silently overridden.
        """
        side = str(side).upper().strip()
        if side not in {"BUY", "SELL"}:
            logger.error("Invalid side: %s", side)
            return None

        if not stop_loss or float(stop_loss) <= 0:
            logger.error("open_trade: stop_loss must be set and > 0 for %s — trade rejected", symbol)
            return None

        if not self._can_open_new_trade(symbol=symbol, side=side):
            return None

        # Quantity determination
        if qty_override is not None and int(qty_override) > 0:
            qty = int(qty_override)
            sizing_reason = f"qty_override={qty}"
        else:
            sizing = self.position_sizer.size_position(
                capital=self.capital,
                entry_price=entry_price,
                stop_loss=stop_loss,
                score=score,
                regime=regime,
                strategy=strategy,
                atr=atr,
                confidence=confidence,
            )
            qty = int(getattr(sizing, "quantity", 0))
            sizing_reason = getattr(sizing, "reason", "sizer")

        if qty <= 0:
            logger.info("Rejected trade — zero quantity | reason=%s", sizing_reason)
            return None

        # ── PAPER-FIRST MODE ─────────────────────────────────────────────
        # Every accepted signal becomes a paper trade for ML/training even
        # when Angel has no usable funds.  Live execution is an optional
        # linked leg below, never a prerequisite for recording the trade.
        broker_name = "PAPER"
        order_id = f"PAPER-{int(time.time() * 1000)}"

        # ── DUAL MODE: live order if balance sufficient ───────────────────
        _live_order_id   = None
        _live_broker_name = None
        _meta_in = metadata or {}
        _signal_data = _meta_in.get("signal_data") if isinstance(_meta_in, dict) else {}
        try:
            import config as _cfg_live_gate
            _allow_validation_blocked_live = bool(
                getattr(_cfg_live_gate, "ALLOW_VALIDATION_BLOCKED_LIVE", False)
            )
        except Exception:
            _allow_validation_blocked_live = False
        _force_paper = bool(
            isinstance(_meta_in, dict)
            and (
                _meta_in.get("force_paper")
                or _meta_in.get("paper_training_mode")
                or (
                    isinstance(_signal_data, dict)
                    and (
                        _signal_data.get("paper_training_mode")
                        or _signal_data.get("paper_training_only")
                        or (
                            _signal_data.get("live_ready") is False
                            and not _allow_validation_blocked_live
                        )
                    )
                )
            )
        )
        _probation_decision = None
        _probation_live = False
        if _force_paper:
            try:
                from live_probation import evaluate_probation
                _probation_decision = evaluate_probation(
                    symbol=symbol,
                    strategy=strategy,
                    requested_qty=qty,
                    entry_price=float(entry_price),
                    score=float(score or 0.0),
                    confidence=confidence,
                    metadata=_meta_in if isinstance(_meta_in, dict) else {},
                )
                if _probation_decision.allowed:
                    _force_paper = False
                    _probation_live = True
                    logger.info(
                        "Live probation approved | symbol=%s strategy=%s live_qty=%s reason=%s",
                        symbol, strategy, _probation_decision.live_qty,
                        _probation_decision.reason,
                    )
                else:
                    logger.info(
                        "Live probation blocked | symbol=%s strategy=%s reason=%s",
                        symbol, strategy, _probation_decision.reason,
                    )
            except Exception as _lp_e:
                logger.debug("live probation evaluation failed: %s", _lp_e)
        if _force_paper:
            logger.info(
                "Live leg skipped: paper-training strategy state | symbol=%s strategy=%s",
                symbol, strategy,
            )
        if _DUAL_AVAILABLE and not _force_paper:
            try:
                _dual = _get_dual(
                    broker_manager=self.broker_manager,
                    alerts=self.alerts,
                )
                if _dual.should_place_live_order():
                    _dual_status = _dual.get_status() if hasattr(_dual, "get_status") else {}
                    _live_balance = float(_dual_status.get("balance", 0.0) or 0.0)
                    _live_qty = self._cap_live_qty_to_balance(
                        requested_qty=qty,
                        entry_price=entry_price,
                        balance=_live_balance,
                        metadata=_meta_in if isinstance(_meta_in, dict) else {},
                        side=side,
                    )
                    if _probation_live and _probation_decision is not None:
                        _live_qty = min(int(_live_qty or 0), int(_probation_decision.live_qty or 0))
                    if _live_qty <= 0:
                        logger.info(
                            "Live leg skipped: balance ₹%.2f cannot fit minimum order | "
                            "symbol=%s requested_qty=%s entry=%.2f",
                            _live_balance, symbol, qty, float(entry_price),
                        )
                    elif _live_qty < qty:
                        logger.info(
                            "Live leg downsized for balance | symbol=%s requested_qty=%s "
                            "live_qty=%s balance=₹%.2f entry=%.2f",
                            symbol, qty, _live_qty, _live_balance, float(entry_price),
                        )
                    # Temporarily switch broker to live mode
                    _broker = self.broker_manager.get_execution_broker()
                    if _live_qty > 0 and _broker and hasattr(_broker, "angel"):
                        _orig_paper = _broker.angel.paper_trade
                        _broker.angel.paper_trade = False
                        try:
                            _live_broker_name, _live_order_id = \
                                self._place_order_via_broker(
                                    symbol=symbol, qty=_live_qty,
                                    buy_sell=side, exchange=exchange,
                                    ref_price=float(entry_price),
                                    order_tag=self._build_order_tag(strategy),
                                )
                            if _live_order_id:
                                logger.info(
                                    "DUAL MODE: live order placed | "
                                    "paper=%s live=%s symbol=%s live_qty=%s",
                                    order_id, _live_order_id, symbol, _live_qty,
                                )
                        finally:
                            _broker.angel.paper_trade = _orig_paper
            except Exception as _de:
                logger.warning("Dual mode live order failed: %s", _de)

        if not order_id:
            logger.error("Order placement failed | symbol=%s side=%s qty=%s", symbol, side, qty)
            return None

        # FIX 3: Poll order fill confirmation (10-second window)
        fill_confirmed = False
        if not str(order_id).startswith(("SIM", "PAPER")):
            try:
                broker = self.broker_manager.get_execution_broker()
                if broker and hasattr(broker, "poll_order_fill"):
                    fill_data = broker.poll_order_fill(order_id, timeout_sec=10.0)
                    if fill_data is None:
                        logger.critical(
                            "ORDER NOT CONFIRMED FILLED in 10s | "
                            "symbol=%s order_id=%s — cancelling",
                            symbol, order_id,
                        )
                        if hasattr(broker, "cancel_order"):
                            broker.cancel_order(order_id)
                        if self.alerts:
                            try:
                                self.alerts.send(
                                    f"⚠️ ORDER FILL NOT CONFIRMED\n"
                                    f"Symbol: {symbol}\nOrder: {order_id}\n"
                                    f"Order cancelled — no trade opened"
                                )
                            except Exception as _e:
                                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
                        return None
                    else:
                        fill_confirmed = True
                        avg_price = float(fill_data.get("averageprice") or entry_price)
                        if avg_price > 0:
                            entry_price = avg_price   # use actual fill price
            except Exception as exc:
                logger.warning("Fill confirmation check failed: %s", exc)

        trade_id = self._next_trade_id()
        trade = ManagedTrade(
            trade_id          = trade_id,
            symbol            = symbol,
            side              = side,
            qty               = qty,
            strategy          = str(strategy).upper(),
            broker_name       = str(broker_name or "UNKNOWN"),
            order_id          = str(order_id),
            entry_price       = float(entry_price),
            entry_time        = time.time(),
            stop_loss         = float(stop_loss)    if stop_loss    is not None else None,
            target_price      = float(target_price) if target_price is not None else None,
            trail_stop        = float(stop_loss)    if stop_loss    is not None else None,
            entry_atr         = float(atr)          if atr          is not None else None,
            confidence        = confidence,
            regime            = regime,
            score             = score,
            correlation_group = correlation_group,
            metadata          = {**(metadata or {}),
                                  "sizing_reason": sizing_reason,
                                  "paper_order_id": str(order_id),
                                  "live_order_id":  str(_live_order_id or ""),
                                  "live_requested_qty": int(qty),
                                  "live_qty": int(_live_qty) if "_live_qty" in locals() else 0,
                                  "dual_mode":      bool(_live_order_id),
                                  "live_probation": bool(_probation_live and _live_order_id),
                                  "live_probation_decision": (
                                      _probation_decision.to_dict()
                                      if _probation_decision is not None else {}
                                  )},
        )

        self.open_trades[trade_id] = trade
        self._persist_trade(trade)
        if _probation_live and _live_order_id:
            try:
                from live_probation import record_probation_entry
                record_probation_entry(
                    trade_id=trade_id,
                    symbol=symbol,
                    strategy=strategy,
                    live_qty=int(_live_qty) if "_live_qty" in locals() else 0,
                    entry_price=float(entry_price),
                    decision=_probation_decision,
                )
            except Exception as _lp_e:
                logger.debug("live probation entry record failed: %s", _lp_e)

        # ── BRACKET LOGIC: atomic entry + SL verification ───────────────
        # Angel One F&O doesn't support native bracket orders.
        # We simulate bracket behaviour:
        #   1. Entry already placed above
        #   2. Immediately place SL-M (target: <3 seconds after fill)
        #   3. If SL-M fails → cancel/close entry within 10 seconds
        # This closes the unprotected position window.
        sl_order_id = self._place_broker_sl_order(
            trade=trade, exchange=exchange
        )
        if sl_order_id:
            trade.sl_order_id = sl_order_id
            if isinstance(trade.metadata, dict):
                trade.metadata["sl_order_id"] = sl_order_id
            self._persist_trade(trade)
            logger.info(
                "Bracket complete: entry=%s SL-M=%s symbol=%s",
                order_id, sl_order_id, symbol,
            )
        else:
            # SL placement failed — this is the race condition we are closing
            # For live trades: cancel entry immediately to avoid unprotected position
            is_live = bool(_live_order_id) or not str(order_id).startswith(("PAPER","SIM","FAKE"))
            if is_live and stop_loss:
                logger.critical(
                    "BRACKET FAILURE: SL-M failed after entry fill | "
                    "trade_id=%s symbol=%s — attempting entry reversal",
                    trade_id, symbol,
                )
                # Retry SL placement once more before reversing
                import time as _bt
                _bt.sleep(1.0)
                sl_order_id = self._place_broker_sl_order(trade=trade, exchange=exchange)
                if sl_order_id:
                    trade.sl_order_id = sl_order_id
                    if isinstance(trade.metadata, dict):
                        trade.metadata["sl_order_id"] = sl_order_id
                    self._persist_trade(trade)
                    logger.info("Bracket SL retry succeeded: %s", sl_order_id)
                else:
                    # SL failed twice — close position immediately
                    logger.critical(
                        "SL retry failed — closing entry position to avoid "
                        "unprotected exposure | trade_id=%s", trade_id
                    )
                    try:
                        close_side = "SELL" if side == "BUY" else "BUY"
                        broker = self.broker_manager.get_execution_broker()
                        if broker:
                            # Emergency reversal: LIMIT at 1% tolerance (SEBI: no MARKET orders)
                            _emg_ref = float(entry_price or 0)
                            if _emg_ref > 0:
                                _emg_tol = 0.01   # 1% — wider tolerance for emergency fill
                                _emg_price = round(
                                    _emg_ref * (1.0 + _emg_tol) if close_side == "BUY"
                                    else _emg_ref * (1.0 - _emg_tol), 2
                                )
                                _emg_otype = "LIMIT"
                            else:
                                _emg_price = 0
                                _emg_otype = "MARKET"
                            broker.place_order(
                                symbol=symbol, qty=qty,
                                buy_sell=close_side,
                                order_type=_emg_otype,
                                price=_emg_price,
                                exchange=exchange,
                            )
                        # Remove from open trades
                        self.open_trades.pop(trade_id, None)
                        if self.alerts:
                            self.alerts.send(
                                f"🚨 BRACKET FAILURE — position closed\n"
                                f"SL placement failed twice for {symbol}\n"
                                f"Entry reversed for safety.",
                                dedup_key=f"bracket_fail_{trade_id}"
                            )
                        return None
                    except Exception as _be:
                        logger.critical("Entry reversal also failed: %s", _be)
            elif not is_live:
                logger.debug("Paper trade: SL-M not placed (normal)")


        # ── GTT order for swing positions (survives bot crash/restart) ────────
        # Swing positions held overnight MUST have a GTT at broker level.
        # GTT persists even if bot is down, internet drops, or PC restarts.
        trade_style = str((metadata or {}).get("style", "intraday")).lower()
        if trade_style == "swing" and stop_loss and stop_loss > 0:
            try:
                broker = self.broker_manager.get_execution_broker()
                if broker and hasattr(broker, "angel") and hasattr(broker.angel, "place_gtt_order"):
                    # For options: limit price = 20% below trigger (ensure fill)
                    is_option  = "CE" in symbol.upper() or "PE" in symbol.upper()
                    limit_px   = round(stop_loss * 0.80, 2) if is_option else round(stop_loss * 0.995, 2)
                    close_side = "SELL" if side == "BUY" else "BUY"
                    gtt_id = broker.angel.place_gtt_order(
                        symbol           = symbol,
                        qty              = qty,
                        trigger_price    = float(stop_loss),
                        limit_price      = limit_px,
                        transaction_type = close_side,
                        exchange         = exchange,
                    )
                    if gtt_id:
                        if isinstance(trade.metadata, dict):
                            trade.metadata["gtt_id"] = gtt_id
                        self._persist_trade(trade)
                        logger.info(
                            "GTT SL placed for swing | trade_id=%s symbol=%s "
                            "trigger=%.2f gtt_id=%s",
                            trade_id, symbol, stop_loss, gtt_id,
                        )
                    else:
                        logger.warning(
                            "GTT placement failed for swing trade %s — "
                            "broker SL-M still active as fallback",
                            trade_id,
                        )
            except Exception as _gtt_e:
                logger.warning("GTT error for swing %s: %s", trade_id, _gtt_e)

        logger.info(
            "Trade opened | trade_id=%s symbol=%s side=%s qty=%s entry=%.2f exchange=%s",
            trade_id, symbol, side, qty, entry_price, exchange,
        )

        # Telegram alert
        if self.alerts:
            try:
                self.alerts.trade_entry(
                    symbol           = symbol,
                    side             = side,
                    qty              = qty,
                    entry_price      = float(entry_price),
                    trade_id         = trade_id,
                    strategy         = str(metadata.get("strategy", "")) if metadata else None,
                    regime           = str(metadata.get("regime", "")) if metadata else None,
                    confidence       = float(metadata["confidence"]) if metadata and "confidence" in metadata else None,
                    score            = float(metadata["score"]) if metadata and "score" in metadata else None,
                    stop_loss        = float(stop_loss) if stop_loss else None,
                    target_price     = float(target_price) if target_price else None,
                    option_expiry    = str(metadata.get("expiry", "")) if metadata else None,
                    option_strike    = int(metadata["strike"]) if metadata and "strike" in metadata else None,
                    capital_deployed = float(entry_price) * int(qty),
                    total_capital    = float(self.capital),
                    daily_pnl        = float(self.daily_realized_pnl),
                    daily_limit      = float(self.daily_loss_limit),
                )
            except Exception:
                logger.debug("trade_entry alert failed", exc_info=True)

        return trade_id


    def _close_trade_internal(
        self,
        trade_id:    str,
        exit_price:  float,
        exit_reason: str = "internal",
        exchange:    str = "NFO",
    ) -> bool:
        """
        Close a single trade by trade_id.
        GA-1 fix: websocket_engine calls this method when trailing stop triggers.
        Alias-safe: delegates to _close_single_trade_by_id().
        """
        return self._close_single_trade_by_id(trade_id, exit_price, exit_reason, exchange)

    def _close_single_trade_by_id(
        self,
        trade_id:    str,
        exit_price:  float,
        exit_reason: str = "trailing_stop",
        exchange:    str = "NFO",
    ) -> bool:
        """Close a single named trade at the given exit_price."""
        trade = self.open_trades.get(trade_id)
        if not trade:
            logger.warning("_close_single_trade_by_id: trade_id=%s not found", trade_id)
            return False
        try:
            close_exchange = exchange or self._trade_exchange(trade)
            if close_exchange == "NFO" and not self._is_option_trade(trade):
                close_exchange = "NSE"
            self._cancel_broker_sl_order(trade)
            meta = trade.metadata or {}
            live_order_id = str(meta.get("live_order_id", "")).strip()
            if live_order_id:
                broker_name, order_id = self._place_order_via_broker(
                    symbol    = trade.symbol,
                    qty       = trade.qty,
                    buy_sell  = "SELL" if trade.side == "BUY" else "BUY",
                    exchange  = close_exchange,
                    ref_price = float(exit_price or trade.entry_price),
                    order_tag = self._build_order_tag(getattr(trade, "strategy", "")),
                )
                if isinstance(trade.metadata, dict):
                    trade.metadata["live_exit_order_id"] = str(order_id or "")
            else:
                broker_name, order_id = "PAPER", f"PAPER-EXIT-{int(time.time() * 1000)}"
                if isinstance(trade.metadata, dict):
                    trade.metadata["paper_exit_order_id"] = order_id
            real_exit = float(exit_price) if exit_price else float(trade.entry_price)
            pnl = self._calculate_pnl(trade, real_exit, is_options=self._is_option_trade(trade))
            trade.status       = "CLOSED"
            trade.exit_price   = real_exit
            trade.exit_time    = time.time()
            trade.exit_reason  = exit_reason
            trade.realized_pnl = pnl
            self.closed_trades.append(trade)
            del self.open_trades[trade_id]
            self._persist_trade(trade)
            self.daily_realized_pnl += pnl
            # ── Update strategy performance matrix ─────────────────────────
            try:
                from strategy_performance_matrix import get_strategy_matrix
                _mat = get_strategy_matrix()
                _strat = getattr(trade, "strategy", "") or ""
                _sym   = getattr(trade, "symbol", "") or ""
                _won   = pnl >= 0
                _dur   = int((getattr(trade,"exit_time",0) or 0) -
                             (getattr(trade,"entry_time",0) or 0)) // 60
                if _strat:
                    _mat.record_result(_strat, _sym, _won, pnl, _dur)
            except Exception as _me:
                logger.debug("matrix_update: %s", _me)
            # ── SL cooldown registration (IMPROVEMENT G) ──────────────────
            try:
                if "stop" in str(exit_reason).lower() or "sl" in str(exit_reason).lower():
                    from market_intelligence_hub import register_sl_hit
                    register_sl_hit(getattr(trade,"symbol",""))
            except Exception: pass

            # ── RL agent learning ──────────────────────────────────────────
            try:
                from rl_agent import rl_record_outcome
                import datetime as _dt
                _hour = _dt.datetime.now().hour
                _regime = str(getattr(trade,"metadata",{}).get("regime","UNKNOWN"))
                _vix   = float(getattr(trade,"metadata",{}).get("vix",15))
                _risk  = abs(float(getattr(trade,"entry_price",1) or 1) -
                             float(getattr(trade,"stop_loss",0) or 0)) * float(getattr(trade,"qty",1) or 1)
                rl_record_outcome(_regime, _vix, _hour, _strat, 1.0, pnl, _risk)
            except Exception: pass
            # ── Score calibrator outcome recording ────────────────────────────
            try:
                _score = float(getattr(trade, "score", None) or 0.0)
                _conf  = str((getattr(trade, "metadata", None) or {}).get("confluence", ""))
                if _score > 0 and _strat:
                    from score_calibrator import get_calibrator
                    get_calibrator().record(
                        score      = _score,
                        confluence = _conf,
                        strategy   = _strat,
                        won        = pnl >= 0,
                        pnl        = pnl,
                        regime     = str(getattr(trade, "regime", "") or ""),
                    )
            except Exception: pass
            logger.info(
                "Trade closed | trade_id=%s reason=%s exit=%.2f pnl=%.2f",
                trade_id, exit_reason, real_exit, pnl,
            )
            # Send exit alert
            # Partial fill reconciliation
            try:
                _filled = int(getattr(trade, 'filled_qty', 0) or 0)
                _intended = int(getattr(trade, 'qty', 0) or 0)
                if 0 < _filled < _intended:
                    logger.warning('Partial fill %s: intended=%d filled=%d',
                                   getattr(trade,'symbol','?'), _intended, _filled)
                    _ep = float(getattr(trade,'entry_price',0) or 0)
                    _xp = float(exit_price or 0)
                    _side = getattr(trade,'side','BUY')
                    pnl = (_xp-_ep)*_filled if _side=='BUY' else (_ep-_xp)*_filled
                    trade.qty = _filled
            except Exception as _pf_e:
                logger.debug('partial_fill: %s', _pf_e)

            # Feed closed option outcomes back into the strike-selection journal.
            try:
                if self._is_option_trade(trade):
                    from option_decision_journal import label_option_decision
                    _option_rows = label_option_decision(
                        str(trade_id),
                        outcome_label=(1 if float(pnl or 0) > 0 else -1 if float(pnl or 0) < 0 else 0),
                        pnl=float(pnl or 0.0),
                        exit_reason=str(exit_reason or ""),
                    )
                    from option_decision_journal import label_option_shadow_decisions
                    _shadow_rows = label_option_shadow_decisions(
                        str(trade_id),
                        self._shadow_option_outcomes(
                            trade,
                            selected_exit_price=float(real_exit or 0.0),
                            exit_reason=str(exit_reason or ""),
                        ),
                    )
                    if _option_rows or _shadow_rows:
                        from option_strike_autotune import build_strike_autotune
                        _tune = build_strike_autotune()
                        logger.info(
                            "Option strike autotune refreshed | labelled_selected=%s labelled_shadow=%s features=%s",
                            _tune.get("labelled_selected", 0),
                            _tune.get("labelled_shadow", 0),
                            len(_tune.get("feature_weights", {}) or {}),
                        )
            except Exception as _oj_e:
                logger.debug("option decision journal label failed: %s", _oj_e)

            try:
                meta = trade.metadata if isinstance(trade.metadata, dict) else {}
                if meta.get("live_probation"):
                    live_qty = int(meta.get("live_qty", 0) or 0)
                    if live_qty > 0:
                        if str(getattr(trade, "side", "")).upper() == "SELL":
                            live_pnl = (float(trade.entry_price) - float(real_exit)) * live_qty
                        else:
                            live_pnl = (float(real_exit) - float(trade.entry_price)) * live_qty
                        from live_probation import record_probation_exit
                        record_probation_exit(str(trade_id), pnl=round(float(live_pnl), 2))
            except Exception as _lp_e:
                logger.debug("live probation exit record failed: %s", _lp_e)

            if self.alerts:
                try:
                    self.alerts.trade_exit(
                        trade    = trade,
                        pnl      = pnl,
                        reason   = exit_reason,
                        paper    = bool(getattr(trade, "mode","PAPER") == "PAPER"),
                    )
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            return True
        except Exception as exc:
            logger.exception("_close_single_trade_by_id failed: %s", exc)
            return False

    def close_all_trades(
        self,
        reason: str = "manual",
        exchange: Optional[str] = None,
    ) -> int:
        """
        Close all open trades at market. Returns count of trades closed.
        Called by DailyLossLimitManager and KillSwitch.
        """
        closed = 0
        for trade_id, trade in list(self.open_trades.items()):
            try:
                # Cancel broker SL order before placing exit order
                self._cancel_broker_sl_order(trade)

                # GA-10: fetch real LTP before placing exit order
                live_ltp = None
                try:
                    if self.broker_manager:
                        broker = self.broker_manager.get_execution_broker()
                        if broker and hasattr(broker, 'get_ltp'):
                            live_ltp = broker.get_ltp(trade.symbol)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

                close_exchange = exchange or self._trade_exchange(trade)
                if close_exchange == "NFO" and not self._is_option_trade(trade):
                    close_exchange = "NSE"

                meta = trade.metadata or {}
                live_order_id = str(meta.get("live_order_id", "")).strip()
                if live_order_id:
                    broker_name, order_id = self._place_order_via_broker(
                        symbol    = trade.symbol,
                        qty       = trade.qty,
                        buy_sell  = "SELL" if trade.side == "BUY" else "BUY",
                        exchange  = close_exchange,
                        ref_price = float(live_ltp or trade.entry_price),
                        order_tag = self._build_order_tag(getattr(trade, "strategy", "")),
                    )
                    if isinstance(trade.metadata, dict):
                        trade.metadata["live_exit_order_id"] = str(order_id or "")
                else:
                    broker_name, order_id = "PAPER", f"PAPER-EXIT-{int(time.time() * 1000)}"
                    if isinstance(trade.metadata, dict):
                        trade.metadata["paper_exit_order_id"] = order_id
                exit_price = float(live_ltp or trade.entry_price)
                pnl        = self._calculate_pnl(trade, exit_price)

                trade.status      = "CLOSED"
                trade.exit_price  = exit_price
                trade.exit_time   = time.time()
                trade.exit_reason = reason
                trade.realized_pnl = pnl

                self.closed_trades.append(trade)
                del self.open_trades[trade_id]
                self._persist_trade(trade)
                self.daily_realized_pnl += pnl
                closed += 1

            except Exception:
                logger.exception("Failed to close trade_id=%s during %s", trade_id, reason)

        logger.info("close_all_trades | reason=%s closed=%d", reason, closed)
        return closed


    def close_positions_at_eod(
        self,
        ltp_getter=None,
        *,
        include_swing: bool = False,
        only_options: bool = False,
        reason: str = "eod_squareoff",
    ) -> int:
        """
        Force-close open EOD positions at current market price.

        By default this closes all non-swing positions, including CASH paper
        trades. This keeps intraday paper trades useful for ML training and
        prevents stale positions from surviving after market close.

        Parameters
        ----------
        ltp_getter : callable, optional
            Function that takes (symbol, exchange) and returns current LTP.
            If provided, used to get a realistic exit price.
            Falls back to entry_price if unavailable.

        Returns number of trades closed.
        """
        candidates = {
            tid: t
            for tid, t in list(self.open_trades.items())
            if (include_swing or not self._is_swing_trade(t))
            and (not only_options or self._is_option_trade(t))
        }

        if not candidates:
            return 0

        logger.warning(
            "EOD square-off: force-closing %d position(s) | only_options=%s include_swing=%s",
            len(candidates), only_options, include_swing,
        )
        closed = 0

        for trade_id, trade in candidates.items():
            try:
                exchange = self._trade_exchange(trade)
                # Get live LTP if possible, else use entry price as best effort
                exit_price = float(trade.entry_price)
                if ltp_getter is not None:
                    try:
                        ltp = ltp_getter(trade.symbol, exchange)
                        if ltp and float(ltp) > 0:
                            candidate_exit = float(ltp)
                            entry = max(float(trade.entry_price or 0), 0.01)
                            if (
                                not self._is_option_trade(trade)
                                and not (entry * 0.50 <= candidate_exit <= entry * 1.50)
                            ):
                                logger.warning(
                                    "Ignoring implausible EOD LTP | trade_id=%s symbol=%s "
                                    "entry=%.2f ltp=%.2f exchange=%s",
                                    trade_id, trade.symbol, entry, candidate_exit, exchange,
                                )
                            else:
                                exit_price = candidate_exit
                    except Exception as _e:
                        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

                if self._close_trade_internal(trade_id, exit_price, reason, exchange):
                    closed += 1

            except Exception:
                logger.exception("EOD square-off failed | trade_id=%s", trade_id)

        return closed

    def close_options_at_eod(self, ltp_getter=None) -> int:
        """
        Compatibility wrapper for callers that explicitly want only options.
        """
        return self.close_positions_at_eod(
            ltp_getter=ltp_getter,
            only_options=True,
            reason="eod_squareoff",
        )

    # ------------------------------------------------------------------
    # Position tick hooks — called each REST poll cycle
    # ------------------------------------------------------------------

    def tick_trailing_stops(
        self,
        ltp_map:   Dict[str, float],
        atr_pct:   float = 0.005,  # 0.5% of price as default trail step
        exchange:  str   = "NFO",
    ) -> int:
        """
        ATR-based trailing stop updater. Called every REST-poll cycle.

        For each open trade, if LTP has moved in favour by at least 1 ATR
        from the last known trail_stop level, ratchet the stop up (for BUY)
        or down (for SELL) by one ATR.

        Returns the number of stops that were updated this tick.
        """
        updated = 0
        for trade_id, trade in list(self.open_trades.items()):
            if trade.status != "OPEN":
                continue
            ltp = ltp_map.get(trade.symbol)
            if not ltp or ltp <= 0:
                continue
            try:
                atr = float(trade.entry_atr or ltp * atr_pct)
                trail = float(trade.trail_stop or trade.stop_loss or 0)
                if not trail:
                    continue

                _ex = self._trade_exchange(trade)  # NSE for equities, NFO for options
                if trade.side == "BUY":
                    new_trail = ltp - atr
                    if new_trail > trail + (atr * 0.5):  # require ≥0.5 ATR improvement
                        trade.trail_stop = new_trail
                        trade.stop_loss  = new_trail
                        self._update_broker_sl_order(trade, new_trail, _ex)
                        updated += 1
                        logger.debug(
                            "Trail updated BUY | %s trail=%.2f ltp=%.2f",
                            trade.symbol, new_trail, ltp,
                        )
                elif trade.side == "SELL":
                    new_trail = ltp + atr
                    if new_trail < trail - (atr * 0.5):
                        trade.trail_stop = new_trail
                        trade.stop_loss  = new_trail
                        self._update_broker_sl_order(trade, new_trail, _ex)
                        updated += 1
                        logger.debug(
                            "Trail updated SELL | %s trail=%.2f ltp=%.2f",
                            trade.symbol, new_trail, ltp,
                        )
            except Exception as exc:
                logger.debug("tick_trailing_stops error %s: %s", trade_id, exc)
        return updated

    def tick_partial_exits(
        self,
        ltp_map:      Dict[str, float],
        t1_fraction:  float = 0.50,   # fraction of target to trigger T1
        exit_pct:     float = 0.50,   # fraction of position to exit at T1
        exchange:     str   = "NFO",
    ) -> int:
        """
        T1 partial exit. When price reaches t1_fraction of target distance
        from entry, close exit_pct of the position (default: 50% at 50% target).
        Sets a metadata flag so the same trade is not double-exited.

        Returns number of partial exits executed this tick.
        """
        executed = 0
        for trade_id, trade in list(self.open_trades.items()):
            if trade.status != "OPEN":
                continue
            meta = trade.metadata or {}
            if meta.get("t1_done"):
                continue  # already taken partial profit on this trade
            if not trade.target_price:
                continue
            ltp = ltp_map.get(trade.symbol)
            if not ltp or ltp <= 0:
                continue
            try:
                entry  = float(trade.entry_price)
                target = float(trade.target_price)
                dist   = abs(target - entry)
                if dist == 0:
                    continue

                t1_level = entry + dist * t1_fraction if trade.side == "BUY" else entry - dist * t1_fraction
                triggered = (trade.side == "BUY" and ltp >= t1_level) or \
                            (trade.side == "SELL" and ltp <= t1_level)

                if not triggered:
                    continue

                exit_qty = max(1, round(trade.qty * exit_pct))
                if exit_qty >= trade.qty:
                    # Would exit the whole thing — let normal target handle it
                    continue

                pnl_partial = self._calculate_pnl(trade, ltp, is_options=self._is_option_trade(trade))
                pnl_partial = pnl_partial * exit_pct  # approximate

                # Place broker order for LIVE trades
                live_order_id = str((trade.metadata or {}).get("live_order_id", "")).strip()
                _close_side   = "SELL" if trade.side == "BUY" else "BUY"
                _close_ex     = self._trade_exchange(trade)
                if live_order_id:
                    t1_broker, t1_order = self._place_order_via_broker(
                        trade.symbol, exit_qty, _close_side, _close_ex,
                        ref_price=float(ltp),
                        order_tag=self._build_order_tag(getattr(trade, "strategy", "")),
                    )
                else:
                    t1_broker, t1_order = "PAPER", f"PAPER-T1-{int(time.time())}"

                # Mark partial done and reduce qty in-memory
                if isinstance(trade.metadata, dict):
                    trade.metadata["t1_done"]       = True
                    trade.metadata["t1_price"]       = ltp
                    trade.metadata["t1_qty"]         = exit_qty
                    trade.metadata["t1_pnl_est"]     = round(pnl_partial, 2)
                    trade.metadata["t1_order_id"]    = t1_order
                else:
                    trade.metadata = {
                        "t1_done": True, "t1_price": ltp,
                        "t1_qty": exit_qty, "t1_order_id": t1_order,
                    }

                trade.qty = trade.qty - exit_qty
                self.daily_realized_pnl += pnl_partial
                executed += 1
                logger.info(
                    "T1 partial exit | %s side=%s ltp=%.2f t1_level=%.2f "
                    "exit_qty=%d remain_qty=%d order=%s",
                    trade.symbol, trade.side, ltp, t1_level,
                    exit_qty, trade.qty, t1_order,
                )
            except Exception as exc:
                logger.debug("tick_partial_exits error %s: %s", trade_id, exc)
        return executed

    def tick_time_exits(
        self,
        ltp_map:          Dict[str, float],
        max_hold_minutes: int   = 90,
        max_loss_pct:     float = -0.005,  # only force-close if P&L < -0.5%
        exchange:         str   = "NFO",
    ) -> int:
        """
        Time-based forced exit. Prevents zombie positions by closing any trade
        that has been open longer than max_hold_minutes and is below the
        max_loss_pct threshold (avoids exiting a quietly winning trade).

        max_hold_minutes: configurable via MAX_HOLD_MINUTES in .env (default 90).
        Returns number of trades force-closed this tick.
        """
        import os as _os
        _max_mins = int(_os.environ.get("MAX_HOLD_MINUTES", max_hold_minutes))
        _max_loss = float(_os.environ.get("MAX_HOLD_LOSS_PCT", max_loss_pct))

        closed = 0
        for trade_id, trade in list(self.open_trades.items()):
            if trade.status != "OPEN":
                continue
            try:
                age_min = (time.time() - float(trade.entry_time)) / 60.0
                if age_min < _max_mins:
                    continue

                ltp = ltp_map.get(trade.symbol, 0.0)
                if not ltp:
                    ltp = float(trade.entry_price)

                entry     = float(trade.entry_price)
                pnl_pct   = ((ltp - entry) / entry) if trade.side == "BUY" else ((entry - ltp) / entry)

                if pnl_pct >= 0:
                    # Gross non-negative, but charges can still make net P&L
                    # negative. Estimate before naming the exit.
                    try:
                        projected_net = self._calculate_pnl(
                            trade, ltp, is_options=self._is_option_trade(trade)
                        )
                    except Exception:
                        projected_net = 0.0
                    reason = "time_exit_profit" if projected_net >= 0 else "time_exit_cost_loss"
                elif pnl_pct >= _max_loss:
                    # Small loss within tolerance — close to free capital
                    reason = "time_exit_small_loss"
                else:
                    # Beyond max_loss_pct — close as zombie stop
                    reason = "time_exit_zombie_stop"

                logger.info(
                    "TIME EXIT | %s age=%.0fmin pnl_pct=%.2f%% reason=%s",
                    trade.symbol, age_min, pnl_pct * 100, reason,
                )
                self._cancel_broker_sl_order(trade)
                self._close_single_trade_by_id(trade_id, ltp, reason, exchange)
                closed += 1

            except Exception as exc:
                logger.debug("tick_time_exits error %s: %s", trade_id, exc)
        return closed

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        counts = self._today_trade_counts()
        return {
            "open_positions":    len(self.open_trades),
            "closed_positions":  len(self.closed_trades),
            "opened_today":      counts.get("opened_today", 0),
            "closed_today":      counts.get("closed_today", 0),
            "daily_realized_pnl": round(self.get_daily_pnl(), 2),
            "trading_locked":    self.trading_locked,
            "lock_reason":       self.lock_reason,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE JOURNAL — SQL analysis helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TradeJournal:
    """
    Query helper for trades.db.
    All P&L numbers are NET (after charges) unless specified.
    All charge numbers are queryable individual SQL columns.

    Usage:
        journal = TradeJournal()
        print(journal.summary_today())
        print(journal.charges_this_month())
        print(journal.paper_vs_live())
        print(journal.strategy_report())
    """

    _trade_lock = threading.Lock()
    MAX_SECTOR_POSITIONS = 2  # max positions in same sector

    def __init__(self, db_path: str = "") -> None:
        import config as _cfg
        self._db = db_path or getattr(_cfg, "TRADES_DB", "trades.db")

    def _q(self, sql: str, params: tuple = ()) -> list:
        import sqlite3
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _one(self, sql: str, params: tuple = ()) -> dict:
        rows = self._q(sql, params)
        return rows[0] if rows else {}

    # ── Daily summary ─────────────────────────────────────────────────────────
    def summary_today(self) -> dict:
        """Today's complete trade summary."""
        from datetime import date
        today = date.today().isoformat()
        r = self._one("""
            SELECT
                COUNT(*)                              AS total_trades,
                SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN realized_pnl<0 THEN 1 ELSE 0 END) AS losses,
                ROUND(SUM(realized_pnl),2)            AS net_pnl,
                ROUND(SUM(gross_pnl),2)               AS gross_pnl,
                ROUND(SUM(total_charges),2)           AS total_charges,
                ROUND(SUM(brokerage),2)               AS brokerage,
                ROUND(SUM(stt),2)                     AS stt,
                ROUND(SUM(exchange_charge),2)         AS exchange_charge,
                ROUND(SUM(gst),2)                     AS gst,
                ROUND(SUM(stamp_duty),4)              AS stamp_duty,
                ROUND(AVG(holding_minutes),0)         AS avg_hold_min,
                ROUND(AVG(r_multiple),2)              AS avg_r,
                MAX(cumulative_pnl)                   AS cumulative_pnl
            FROM trades
            WHERE status='CLOSED'
              AND date(exit_time,'unixepoch','localtime') = ?
        """, (today,))
        wins   = r.get("wins",0) or 0
        total  = r.get("total_trades",0) or 0
        r["win_rate_pct"] = round(wins/total*100, 1) if total > 0 else 0
        return r

    # ── Period summary ────────────────────────────────────────────────────────
    def summary_period(self, days: int = 30) -> dict:
        """Summary for last N days."""
        r = self._one("""
            SELECT
                COUNT(*)                              AS total_trades,
                SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                ROUND(SUM(realized_pnl),2)            AS net_pnl,
                ROUND(SUM(gross_pnl),2)               AS gross_pnl,
                ROUND(SUM(total_charges),2)           AS total_charges,
                ROUND(SUM(brokerage),2)               AS brokerage,
                ROUND(SUM(stt),2)                     AS stt,
                ROUND(SUM(gst),2)                     AS gst,
                ROUND(AVG(r_multiple),2)              AS avg_r,
                MAX(cumulative_pnl)                   AS cumulative_pnl,
                ROUND(AVG(holding_minutes),0)         AS avg_hold_min
            FROM trades
            WHERE status='CLOSED'
              AND exit_time >= strftime('%s','now',?)
        """, (f"-{days} days",))
        wins  = r.get("wins",0) or 0
        total = r.get("total_trades",0) or 0
        r["win_rate_pct"] = round(wins/total*100, 1) if total > 0 else 0
        return r

    # ── Charges breakdown ─────────────────────────────────────────────────────
    def charges_this_month(self) -> dict:
        """All charges paid this calendar month — queryable individually."""
        r = self._one("""
            SELECT
                ROUND(SUM(brokerage),2)       AS brokerage,
                ROUND(SUM(stt),2)             AS stt,
                ROUND(SUM(exchange_charge),2) AS exchange_charge,
                ROUND(SUM(sebi_levy),4)       AS sebi_levy,
                ROUND(SUM(gst),2)             AS gst,
                ROUND(SUM(stamp_duty),4)      AS stamp_duty,
                ROUND(SUM(total_charges),2)   AS total_charges,
                COUNT(*)                      AS trade_count,
                ROUND(AVG(total_charges),2)   AS avg_charge_per_trade
            FROM trades
            WHERE status='CLOSED'
              AND strftime('%Y-%m', exit_time,'unixepoch','localtime')
                = strftime('%Y-%m','now')
        """)
        return r

    # ── Paper vs Live comparison ──────────────────────────────────────────────
    def paper_vs_live(self) -> dict:
        """Compare paper and live P&L side by side."""
        rows = self._q("""
            SELECT
                trade_type,
                COUNT(*)                              AS trades,
                SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                ROUND(SUM(realized_pnl),2)            AS net_pnl,
                ROUND(SUM(gross_pnl),2)               AS gross_pnl,
                ROUND(SUM(total_charges),2)           AS charges,
                ROUND(AVG(r_multiple),2)              AS avg_r
            FROM trades
            WHERE status='CLOSED'
            GROUP BY trade_type
            ORDER BY trade_type
        """)
        result = {}
        for r in rows:
            tt    = r["trade_type"]
            wins  = r.get("wins",0) or 0
            total = r.get("trades",0) or 0
            r["win_rate_pct"] = round(wins/total*100,1) if total > 0 else 0
            result[tt] = r
        return result

    # ── Strategy performance ──────────────────────────────────────────────────
    def strategy_report(self, days: int = 30) -> list:
        """Per-strategy P&L, win rate, charges, avg R."""
        return self._q("""
            SELECT
                strategy,
                COUNT(*)                              AS trades,
                SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins,
                ROUND(SUM(realized_pnl),2)            AS net_pnl,
                ROUND(SUM(total_charges),2)           AS charges,
                ROUND(AVG(r_multiple),2)              AS avg_r,
                ROUND(AVG(holding_minutes),0)         AS avg_hold_min,
                ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),1)
                                                      AS win_rate_pct
            FROM trades
            WHERE status='CLOSED'
              AND exit_time >= strftime('%s','now',?)
            GROUP BY strategy
            ORDER BY net_pnl DESC
        """, (f"-{days} days",))

    # ── Cumulative P&L equity curve ───────────────────────────────────────────
    def equity_curve(self, trade_type: str = "") -> list:
        """Daily cumulative P&L for equity curve chart."""
        where = "WHERE status='CLOSED'"
        params = []
        if trade_type:
            where += " AND trade_type=?"
            params.append(trade_type)
        return self._q(f"""
            SELECT
                date(exit_time,'unixepoch','localtime') AS trade_date,
                ROUND(SUM(realized_pnl),2)              AS daily_pnl,
                ROUND(SUM(total_charges),2)             AS daily_charges,
                COUNT(*)                                AS trade_count,
                MAX(cumulative_pnl)                     AS cumulative_pnl
            FROM trades {where}
            GROUP BY trade_date
            ORDER BY trade_date
        """, tuple(params))

    # ── Best/worst trades ────────────────────────────────────────────────────
    def best_worst(self, n: int = 5) -> dict:
        return {
            "best":  self._q("SELECT trade_id,symbol,strategy,realized_pnl,r_multiple,holding_minutes "
                             "FROM trades WHERE status='CLOSED' ORDER BY realized_pnl DESC LIMIT ?", (n,)),
            "worst": self._q("SELECT trade_id,symbol,strategy,realized_pnl,r_multiple,holding_minutes "
                             "FROM trades WHERE status='CLOSED' ORDER BY realized_pnl ASC  LIMIT ?", (n,)),
        }

    # ── Full summary string for Telegram ─────────────────────────────────────
    def format_daily_telegram(self) -> str:
        d  = self.summary_today()
        ch = d.get("total_charges", 0) or 0
        lines = [
            "📊 <b>DAILY TRADE JOURNAL</b>",
            "─" * 28,
            f"Trades:      {d.get('total_trades',0)} ({d.get('wins',0)}W / {d.get('losses',0)}L)  {d.get('win_rate_pct',0):.0f}%",
            f"Gross P&L:   ₹{d.get('gross_pnl',0):+,.0f}",
            f"Charges:     ₹{ch:,.0f}",
            f"  Brokerage: ₹{d.get('brokerage',0):.0f}",
            f"  STT:       ₹{d.get('stt',0):.2f}",
            f"  GST:       ₹{d.get('gst',0):.2f}",
            f"Net P&L:     ₹{d.get('net_pnl',0):+,.0f}",
            f"Avg hold:    {d.get('avg_hold_min',0):.0f} min",
            f"Avg R:       {d.get('avg_r',0):.2f}R",
            f"Cumulative:  ₹{d.get('cumulative_pnl',0):+,.0f}",
        ]
        return "\n".join(lines)


# Singleton
_journal: "TradeJournal | None" = None
def get_journal() -> "TradeJournal":
    global _journal
    if _journal is None:
        _journal = TradeJournal()
    return _journal
