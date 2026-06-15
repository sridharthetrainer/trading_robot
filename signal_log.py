"""
signal_log.py  —  Candidate Signal Logger

Saves ALL candidate signals (executed AND rejected) with full feature
vector + market context to signal_log.db.

After market close, Triple Barrier labels are applied using actual
OHLCV data.  This gives 8× more training data than executed-only.

SCHEMA (signal_log.db → table: signal_log):
  Core signal fields   : symbol, side, strategy, score, confluence...
  All 25 score modifiers: bhav, cross_asset, participant_oi, ...
  Market context       : vix, ivp, pcr, fii_net, sensex_div...
  Pivot context        : above/below weekly/monthly pivots
  Outcome (set later)  : tb_label, outcome_price, outcome_time
  Execution flag       : executed (0=rejected, 1=took the trade)
"""
from __future__ import annotations

import json
import logging
import sqlite3

_WAL_ENABLED = set()

def _enable_wal(conn, path=""):
    """Enable WAL mode for better concurrent access."""
    if path not in _WAL_ENABLED:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            if path: _WAL_ENABLED.add(path)
        except Exception: pass
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_PATH   = Path("signal_log.db")
_TBL       = "signal_log"


# ── Schema ────────────────────────────────────────────────────────────────────
_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TBL} (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    log_time         REAL    DEFAULT (strftime('%s','now')),
    signal_date      TEXT,
    signal_time      TEXT,

    -- Core signal
    symbol           TEXT,
    side             TEXT,
    strategy         TEXT,
    confluence       TEXT,
    n_agree          INTEGER DEFAULT 1,
    n_conflict       INTEGER DEFAULT 0,
    agreeing_strats  TEXT,       -- JSON list
    score            REAL,
    raw_score        REAL,
    regime           TEXT,
    htf_bias         TEXT,
    entry_price      REAL,

    -- Execution
    executed         INTEGER DEFAULT 0,   -- 1=traded, 0=rejected
    rejection_reason TEXT,
    trade_id         TEXT,

    -- Score modifier features (all 25)
    bhav_delivery    REAL DEFAULT 0,
    cross_asset_mod  REAL DEFAULT 0,
    participant_mod  REAL DEFAULT 0,
    expiry_mod       REAL DEFAULT 0,
    sip_boost        REAL DEFAULT 0,
    bulk_deal_mod    REAL DEFAULT 0,
    theta_mod        REAL DEFAULT 0,
    rebal_mod        REAL DEFAULT 0,
    news_mod         REAL DEFAULT 0,
    mtf_pivot_mod    REAL DEFAULT 0,
    gex_mod          REAL DEFAULT 0,
    skew_mod         REAL DEFAULT 0,
    whale_mod        REAL DEFAULT 0,
    sr_level_mod     REAL DEFAULT 0,
    pivot_boss_mod   REAL DEFAULT 0,
    oi_mod           REAL DEFAULT 0,
    time_bucket_wt   REAL DEFAULT 1,
    ai_score         REAL DEFAULT 0,
    rl_bias          REAL DEFAULT 0,
    weinstein_mod    REAL DEFAULT 0,
    volume_ratio     REAL DEFAULT 0,

    -- Market context at signal time
    india_vix        REAL DEFAULT 0,
    iv_percentile    REAL DEFAULT 50,
    pcr_atm          REAL DEFAULT 1,
    fii_net_cash     REAL DEFAULT 0,
    fii_fut_ratio    REAL DEFAULT 1,
    fii_cum_5d       REAL DEFAULT 0,
    client_ce_pct    REAL DEFAULT 0,
    cross_asset_bias TEXT DEFAULT 'NEUTRAL',
    sensex_nifty_div REAL DEFAULT 0,

    -- Pivot context
    above_weekly_pvt INTEGER DEFAULT 0,
    above_monthly_pvt INTEGER DEFAULT 0,
    pct_from_w_r1    REAL DEFAULT 0,
    pct_from_m_r1    REAL DEFAULT 0,
    expiry_dte       INTEGER DEFAULT 5,
    expiry_regime    TEXT DEFAULT 'NORMAL',

    -- Symbol context
    symbol_type      TEXT DEFAULT 'INDEX',   -- INDEX, BANKING, IT, FMCG, ...
    sector_code      INTEGER DEFAULT 0,

    -- Time context
    hour_of_day      INTEGER DEFAULT 10,
    day_of_week      INTEGER DEFAULT 0,
    trade_num_today  INTEGER DEFAULT 0,
    daily_pnl_before REAL DEFAULT 0,

    -- Outcome (filled post-market)
    tb_label             INTEGER DEFAULT -99,    -- +1 win, -1 loss, 0 timeout, -99 pending
    outcome_price        REAL DEFAULT 0,
    outcome_time         REAL DEFAULT 0,
    peak_price           REAL DEFAULT 0,
    max_adverse_move     REAL DEFAULT 0,
    max_favorable_move   REAL DEFAULT 0        -- MFE %: best unrealised gain during hold
);

CREATE INDEX IF NOT EXISTS idx_signal_date ON {_TBL}(signal_date);
CREATE INDEX IF NOT EXISTS idx_symbol      ON {_TBL}(symbol);
CREATE INDEX IF NOT EXISTS idx_executed    ON {_TBL}(executed);
CREATE INDEX IF NOT EXISTS idx_tb_label    ON {_TBL}(tb_label);
"""

_SECTOR_MAP = {
    "NIFTY":0,"BANKNIFTY":0,"FINNIFTY":0,"MIDCPNIFTY":0,"SENSEX":0,
    "HDFCBANK":1,"ICICIBANK":1,"SBIN":1,"AXISBANK":1,"KOTAKBANK":1,
    "BANKBARODA":1,"INDUSINDBK":1,"BANDHANBNK":1,"FEDERALBNK":1,
    "INFY":2,"TCS":2,"WIPRO":2,"HCLTECH":2,"TECHM":2,"MPHASIS":2,
    "ITC":3,"HINDUNILVR":3,"NESTLEIND":3,"BRITANNIA":3,"DABUR":3,
    "RELIANCE":4,"ONGC":4,"BPCL":4,"GAIL":4,"HPCL":4,
    "BAJFINANCE":5,"HDFCLIFE":5,"SBILIFE":5,"BAJAJFINSV":5,
    "SUNPHARMA":6,"DRREDDY":6,"CIPLA":6,"DIVISLAB":6,"LUPIN":6,
    "TATAMOTORS":7,"MARUTI":7,"M&M":7,"EICHERMOT":7,
    "TATASTEEL":8,"JSWSTEEL":8,"HINDALCO":8,"VEDL":8,
    "LT":9,"ADANIENT":9,"NTPC":9,"POWERGRID":9,
    "ZOMATO":10,"IRCTC":10,"DMART":10,"TRENT":10,
    "BHARTIARTL":11,"DLF":12,"APOLLOHOSP":13,"TITAN":14,
}

_TYPE_MAP = {
    "NIFTY":"INDEX","BANKNIFTY":"INDEX","FINNIFTY":"INDEX",
    "MIDCPNIFTY":"INDEX","SENSEX":"INDEX","BANKEX":"INDEX",
}

def _get_type(symbol: str) -> str:
    return _TYPE_MAP.get(symbol.upper(), "STOCK")

def _get_sector(symbol: str) -> int:
    return _SECTOR_MAP.get(symbol.upper(), 99)


class SignalLogger:
    """Thread-safe logger for all candidate signals."""

    def __init__(self, db_path: str = str(_DB_PATH)) -> None:
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._conn() as conn:
                conn.executescript(_CREATE_SQL)
                # Migration: add columns missing on existing DBs (idempotent)
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({_TBL})").fetchall()]
                _new_cols = {
                    "max_favorable_move": "REAL DEFAULT 0",
                    "gex_mod":        "REAL DEFAULT 0",
                    "skew_mod":       "REAL DEFAULT 0",
                    "whale_mod":      "REAL DEFAULT 0",
                    "sr_level_mod":   "REAL DEFAULT 0",
                    "pivot_boss_mod": "REAL DEFAULT 0",
                    "oi_mod":         "REAL DEFAULT 0",
                }
                for _c, _decl in _new_cols.items():
                    if _c not in cols:
                        conn.execute(f"ALTER TABLE {_TBL} ADD COLUMN {_c} {_decl}")
        except Exception as e:
            logger.error("SignalLogger init_db: %s", e)

    def log_candidate(
        self,
        signal:           dict,
        executed:         bool  = False,
        rejection_reason: str   = "",
        trade_id:         str   = "",
        # Market context (pass from live_signal_engine)
        india_vix:        float = 0.0,
        iv_percentile:    float = 50.0,
        pcr_atm:          float = 1.0,
        fii_net_cash:     float = 0.0,
        fii_fut_ratio:    float = 1.0,
        fii_cum_5d:       float = 0.0,
        client_ce_pct:    float = 0.0,
        cross_asset_bias: str   = "NEUTRAL",
        sensex_nifty_div: float = 0.0,
        # Pivot context
        weekly_pivot:     float = 0.0,
        monthly_pivot:    float = 0.0,
        weekly_r1:        float = 0.0,
        monthly_r1:       float = 0.0,
        expiry_dte:       int   = 5,
        expiry_regime:    str   = "NORMAL",
        # Session context
        trade_num_today:  int   = 0,
        daily_pnl_before: float = 0.0,
    ) -> Optional[int]:
        """Log a candidate signal (executed or rejected) with full context."""
        try:
            now  = datetime.now()
            sym  = str(signal.get("symbol", "?")).upper()
            ep   = float(signal.get("entry_price", signal.get("price", 0)) or 0)

            # Pivot position flags
            above_wpvt = 1 if (weekly_pivot  > 0 and ep > weekly_pivot)  else 0
            above_mpvt = 1 if (monthly_pivot > 0 and ep > monthly_pivot) else 0
            pct_w_r1   = (ep - weekly_r1)  / weekly_r1  * 100 if weekly_r1  > 0 else 0
            pct_m_r1   = (ep - monthly_r1) / monthly_r1 * 100 if monthly_r1 > 0 else 0

            # Score modifiers from signal metadata
            meta = signal.get("metadata", {}) or {}
            mods = meta.get("score_modifiers", {}) or {}

            row = {
                "signal_date":     now.strftime("%Y-%m-%d"),
                "signal_time":     now.strftime("%H:%M:%S"),
                "symbol":          sym,
                "side":            str(signal.get("side", signal.get("direction","")) or ""),
                "strategy":        str(signal.get("strategy","") or ""),
                "confluence":      str(signal.get("confluence","SINGLE") or "SINGLE"),
                "n_agree":         int(signal.get("n_agree", 1) or 1),
                "n_conflict":      int(signal.get("n_conflict", 0) or 0),
                "agreeing_strats": json.dumps(signal.get("agreeing", []) or []),
                "score":           float(signal.get("score", 0) or 0),
                "raw_score":       float(signal.get("raw_score", 0) or 0),
                "regime":          str(signal.get("regime","") or ""),
                "htf_bias":        str(signal.get("htf_bias","") or ""),
                "entry_price":     ep,
                "executed":        1 if executed else 0,
                "rejection_reason":rejection_reason,
                "trade_id":        trade_id,
                # Score modifiers
                "bhav_delivery":   float(mods.get("bhav_delivery", 0) or 0),
                "cross_asset_mod": float(mods.get("cross_asset", 0) or 0),
                "participant_mod": float(mods.get("participant_oi", 0) or 0),
                "expiry_mod":      float(mods.get("expiry_regime", 0) or 0),
                "sip_boost":       float(mods.get("sip_boost", 0) or 0),
                "bulk_deal_mod":   float(mods.get("bulk_deal", 0) or 0),
                "theta_mod":       float(mods.get("theta", 0) or 0),
                "rebal_mod":       float(mods.get("rebalancing", 0) or 0),
                "news_mod":        float(mods.get("news", 0) or 0),
                "mtf_pivot_mod":   float(mods.get("mtf_pivot", 0) or 0),
                "gex_mod":         float(mods.get("gex_mod", 0) or 0),
                "skew_mod":        float(mods.get("skew_mod", 0) or 0),
                "whale_mod":       float(mods.get("whale_mod", 0) or 0),
                "sr_level_mod":    float(mods.get("sr_level_mod", 0) or 0),
                "pivot_boss_mod":  float(mods.get("pivot_boss_mod", 0) or 0),
                "oi_mod":          float(mods.get("oi_mod", 0) or 0),
                "time_bucket_wt":  float(mods.get("time_bucket", 1) or 1),
                "ai_score":        float(signal.get("confidence", 0) or 0),
                "volume_ratio":    float(signal.get("volume_ratio", 0) or 0),
                # Market context
                "india_vix":       india_vix,
                "iv_percentile":   iv_percentile,
                "pcr_atm":         pcr_atm,
                "fii_net_cash":    fii_net_cash,
                "fii_fut_ratio":   fii_fut_ratio,
                "fii_cum_5d":      fii_cum_5d,
                "client_ce_pct":   client_ce_pct,
                "cross_asset_bias":cross_asset_bias,
                "sensex_nifty_div":sensex_nifty_div,
                # Pivot context
                "above_weekly_pvt": above_wpvt,
                "above_monthly_pvt":above_mpvt,
                "pct_from_w_r1":   round(pct_w_r1, 3),
                "pct_from_m_r1":   round(pct_m_r1, 3),
                "expiry_dte":      expiry_dte,
                "expiry_regime":   expiry_regime,
                # Symbol context
                "symbol_type":     _get_type(sym),
                "sector_code":     _get_sector(sym),
                # Time context
                "hour_of_day":     now.hour,
                "day_of_week":     now.weekday(),
                "trade_num_today": trade_num_today,
                "daily_pnl_before":daily_pnl_before,
                # Outcome — pending until post-market labelling
                "tb_label":        -99,
            }

            cols  = ", ".join(row.keys())
            phs   = ", ".join("?" * len(row))
            sql   = f"INSERT INTO {_TBL} ({cols}) VALUES ({phs})"
            with self._conn() as conn:
                cur = conn.execute(sql, list(row.values()))
                return cur.lastrowid
        except Exception as e:
            logger.debug("SignalLogger.log_candidate: %s", e)
            return None

    def mark_executed(self, symbol: str, trade_id: str = "",
                      strategy: str = "") -> bool:
        """
        Flip executed=0 -> 1 on the most recent candidate row for this
        symbol today (matching strategy when given) and attach the trade_id.

        Candidates are always logged with executed=False before the execution
        gates run ("updated later if executed") — this is the missing other
        half: until 2026-06-12 nothing ever updated it, so all 12k+ logged
        signals showed executed=0 and the ML pipeline had no positive class.
        """
        try:
            with self._conn() as conn:
                # 2-day window handles late-evening executions that roll past
                # midnight before mark_executed is called.
                base = (f"SELECT id FROM {_TBL} WHERE symbol = ? "
                        f"AND signal_date >= date('now','localtime','-1 day') "
                        f"AND executed = 0 {{strat}} ORDER BY id DESC LIMIT 1")
                row = None
                # 1) prefer the matching strategy (case-insensitive — trade records
                #    upper-case the strategy while candidates are logged raw).
                if strategy:
                    row = conn.execute(
                        base.format(strat="AND UPPER(strategy) = UPPER(?)"),
                        (symbol, strategy)).fetchone()
                # 2) fall back to symbol-only so a trade still links when the
                #    strategy name diverges or wasn't logged (was a permanent miss).
                if not row:
                    row = conn.execute(base.format(strat=""), (symbol,)).fetchone()
                if row:
                    conn.execute(
                        f"UPDATE {_TBL} SET executed = 1, trade_id = ? WHERE id = ?",
                        (str(trade_id or ""), row[0]))
                    logger.info("signal_log: linked trade %s -> candidate id=%s (%s/%s)",
                                trade_id, row[0], symbol, strategy or "any")
                    return True
            logger.warning("signal_log.mark_executed: no recent candidate row for %s "
                           "(strategy=%s) — trade %s not linked", symbol,
                           strategy or "any", trade_id)
            return False
        except Exception as e:
            logger.debug("SignalLogger.mark_executed: %s", e)
            return False

    def apply_triple_barrier_labels(self, df_map: dict) -> int:
        """
        Post-market: apply Triple Barrier labels to all unlabelled signals.

        df_map: {symbol: pd.DataFrame with OHLCV}  — use EOD data fetch

        Returns count of signals labelled.
        """
        try:
            from triple_barrier import label_triple_barrier, get_dynamic_barriers
        except ImportError:
            logger.warning("triple_barrier not available for labelling")
            return 0

        labelled = 0
        try:
            with self._conn() as conn:
                pending = conn.execute(
                    f"SELECT id, symbol, side, entry_price, signal_time, "
                    f"signal_date "
                    f"FROM {_TBL} WHERE tb_label = -99 "
                    f"AND signal_date <= date('now','-1 day')"
                ).fetchall()

            for row in pending:
                sig_id = row["id"]
                sym    = row["symbol"]
                side   = row["side"]
                ep     = float(row["entry_price"] or 0)
                df     = df_map.get(sym)

                if ep <= 0 or not (row["side"] or ""):
                    # Unlabellable forever (junk row from early logging) —
                    # retire with sentinel -2 so it stops clogging the
                    # nightly pending queue. -2 is excluded everywhere
                    # (training/reporting filter on tb_label IN (1,0,-1)).
                    with self._conn() as conn:
                        conn.execute(
                            f"UPDATE {_TBL} SET tb_label = -2 WHERE id = ?",
                            (sig_id,))
                    continue
                if df is None:
                    continue

                try:
                    # Find bar index closest to signal time.
                    # BUG FIX 2026-06-12: was hardcoded to YESTERDAY's date,
                    # so any signal older than one day matched the wrong bar
                    # and was labelled against the wrong entry window.
                    sig_ts = datetime.strptime(
                        f"{row['signal_date']} {row['signal_time']}",
                        "%Y-%m-%d %H:%M:%S"
                    ).timestamp()
                    entry_idx = 0
                    if hasattr(df.index, 'to_pydatetime'):
                        times = [t.timestamp() for t in df.index.to_pydatetime()]
                        diffs = [abs(t - sig_ts) for t in times]
                        entry_idx = diffs.index(min(diffs))

                    # Compute ATR for dynamic barriers
                    if "atr" in df.columns:
                        atr = float(df["atr"].iloc[entry_idx])
                    else:
                        high = df["high"] if "high" in df.columns else df.iloc[:, 1]
                        low  = df["low"]  if "low"  in df.columns else df.iloc[:, 2]
                        atr  = float((high - low).tail(14).mean())

                    t_pct, s_pct, max_b = get_dynamic_barriers(atr, ep)
                    label = label_triple_barrier(df, entry_idx, ep, t_pct, s_pct, max_b, side)

                    # Find peak, max adverse, and the outcome bar (first
                    # barrier touch, else the timeout bar) so outcome_price/
                    # outcome_time are recorded — they were dead columns.
                    outcome_price, outcome_time = ep, ""
                    if entry_idx < len(df) - 1:
                        future = df.iloc[entry_idx: entry_idx + max_b + 1]
                        highs  = future["high"] if "high" in df.columns else future.iloc[:, 1]
                        lows   = future["low"]  if "low"  in df.columns else future.iloc[:, 2]
                        closes = future["close"] if "close" in df.columns else future.iloc[:, 3]
                        peak, trough = float(highs.max()), float(lows.min())
                        max_adv = (abs(ep - trough) / ep * 100 if side == "BUY"
                                   else abs(peak - ep) / ep * 100)
                        max_fav = (abs(peak - ep) / ep * 100 if side == "BUY"
                                   else abs(ep - trough) / ep * 100)
                        up_lvl = ep * (1 + (t_pct if side == "BUY" else s_pct))
                        dn_lvl = ep * (1 - (s_pct if side == "BUY" else t_pct))
                        touch_idx = None
                        for i in range(len(future)):
                            if float(highs.iloc[i]) >= up_lvl or float(lows.iloc[i]) <= dn_lvl:
                                touch_idx = i
                                break
                        ob = future.index[touch_idx if touch_idx is not None else -1]
                        outcome_price = float(closes.iloc[touch_idx]
                                              if touch_idx is not None else closes.iloc[-1])
                        outcome_time = str(ob)
                    else:
                        peak = ep; max_adv = 0.0; max_fav = 0.0

                    with self._conn() as conn:
                        conn.execute(
                            f"UPDATE {_TBL} SET tb_label=?, peak_price=?, "
                            f"max_adverse_move=?, max_favorable_move=?, "
                            f"outcome_price=?, outcome_time=? "
                            f"WHERE id=?",
                            (label, peak, round(max_adv, 3), round(max_fav, 3),
                             round(outcome_price, 4), outcome_time, sig_id)
                        )
                    labelled += 1
                except Exception as e:
                    logger.debug("TB label row %d: %s", sig_id, e)

        except Exception as e:
            logger.warning("apply_triple_barrier_labels: %s", e)

        if labelled:
            logger.info("Triple Barrier labelled %d signals", labelled)
        return labelled

    def get_training_data(
        self,
        days_back:  int  = 60,
        min_labels: int  = 50,
        include_rejected: bool = True,
    ) -> List[dict]:
        """
        Fetch labelled signals for model training.
        Returns list of dicts with all features + tb_label.

        include_rejected=True: 8× more data, teaches model what NOT to take
        include_rejected=False: executed trades only (old behaviour)
        """
        try:
            cutoff = (date.today() - __import__('datetime').timedelta(days=days_back)).isoformat()
            exec_filter = "" if include_rejected else "AND executed = 1"
            sql = f"""
                SELECT * FROM {_TBL}
                WHERE signal_date >= ?
                  AND tb_label != -99
                  {exec_filter}
                ORDER BY signal_date, signal_time
            """
            with self._conn() as conn:
                rows = conn.execute(sql, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("get_training_data: %s", e)
            return []

    def stats(self) -> dict:
        """Summary stats for Telegram report."""
        try:
            with self._conn() as conn:
                total   = conn.execute(f"SELECT COUNT(*) FROM {_TBL}").fetchone()[0]
                labelled= conn.execute(f"SELECT COUNT(*) FROM {_TBL} WHERE tb_label != -99").fetchone()[0]
                wins    = conn.execute(f"SELECT COUNT(*) FROM {_TBL} WHERE tb_label = 1").fetchone()[0]
                losses  = conn.execute(f"SELECT COUNT(*) FROM {_TBL} WHERE tb_label = -1").fetchone()[0]
                executed= conn.execute(f"SELECT COUNT(*) FROM {_TBL} WHERE executed = 1").fetchone()[0]
            win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0
            return {
                "total":    total,
                "labelled": labelled,
                "executed": executed,
                "rejected": total - executed,
                "wins":     wins,
                "losses":   losses,
                "win_rate": round(win_rate, 1),
            }
        except Exception as e:
            logger.debug("SignalLogger.stats: %s", e)
            return {}


# Singleton
_logger_instance: Optional[SignalLogger] = None

def get_signal_logger(db_path: str = str(_DB_PATH)) -> SignalLogger:
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = SignalLogger(db_path)
    return _logger_instance
