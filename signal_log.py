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
    stop_loss        REAL DEFAULT 0,
    target           REAL DEFAULT 0,
    rr               REAL DEFAULT 0,

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
    structure_mod    REAL DEFAULT 0,
    market_quality_mod REAL DEFAULT 0,
    market_profile_mod REAL DEFAULT 0,
    candidate_quality_mod REAL DEFAULT 0,
    time_bucket_wt   REAL DEFAULT 1,
    ai_score         REAL DEFAULT 0,
    rl_bias          REAL DEFAULT 0,
    weinstein_mod    REAL DEFAULT 0,
    sector_mod       REAL DEFAULT 0,
    crsi_mod         REAL DEFAULT 0,
    nr_mod           REAL DEFAULT 0,
    volume_mod       REAL DEFAULT 0,
    volume_ratio     REAL DEFAULT 0,

    -- Market structure context
    structure_label      TEXT DEFAULT '',
    structure_direction  TEXT DEFAULT '',
    structure_bos        TEXT DEFAULT '',
    structure_choch      TEXT DEFAULT '',
    structure_score      REAL DEFAULT 0,
    structure_aligned    INTEGER DEFAULT 0,
    structure_retest     INTEGER DEFAULT 0,
    indicator_coverage   REAL DEFAULT 0,
    candidate_confirmations INTEGER DEFAULT 0,
    bar_return_1_atr REAL DEFAULT 0,
    line_slope_atr REAL DEFAULT 0,
    line_turn REAL DEFAULT 0,
    step_direction REAL DEFAULT 0,
    baseline_distance_atr REAL DEFAULT 0,
    hollow_state REAL DEFAULT 0,
    hollow_run REAL DEFAULT 0,
    volume_candle_strength REAL DEFAULT 0,
    line_break_direction REAL DEFAULT 0,
    line_break_run REAL DEFAULT 0,
    line_break_event REAL DEFAULT 0,
    kagi_direction REAL DEFAULT 0,
    kagi_reversal REAL DEFAULT 0,
    kagi_distance_atr REAL DEFAULT 0,
    pnf_direction REAL DEFAULT 0,
    pnf_boxes REAL DEFAULT 0,
    pnf_reversal REAL DEFAULT 0,
    range_direction REAL DEFAULT 0,
    range_run REAL DEFAULT 0,
    range_event REAL DEFAULT 0,
    footprint_delta_proxy REAL DEFAULT 0,
    footprint_available REAL DEFAULT 0,
    ichimoku_position REAL DEFAULT 0,
    ichimoku_tk REAL DEFAULT 0,
    representation_coverage REAL DEFAULT 0,
    tick_oim REAL DEFAULT 0,
    tick_velocity REAL DEFAULT 0,
    tick_momentum REAL DEFAULT 0,
    tick_sample_count INTEGER DEFAULT 0,
    tick_flow_available INTEGER DEFAULT 0,
    profile_poc        REAL DEFAULT 0,
    profile_vah        REAL DEFAULT 0,
    profile_val        REAL DEFAULT 0,
    profile_poc_distance_pct REAL DEFAULT 0,
    profile_value_width_pct REAL DEFAULT 0,
    profile_bias       TEXT DEFAULT '',
    profile_position   TEXT DEFAULT '',
    profile_acceptance TEXT DEFAULT '',

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

    -- Option metadata
    option_type      TEXT DEFAULT '',
    option_strike    INTEGER DEFAULT 0,
    option_expiry    TEXT DEFAULT '',
    option_dte       INTEGER DEFAULT 0,
    option_style     TEXT DEFAULT '',
    option_premium   REAL DEFAULT 0,
    option_symbol    TEXT DEFAULT '',

    -- Time context
    hour_of_day      INTEGER DEFAULT 10,
    day_of_week      INTEGER DEFAULT 0,
    trade_num_today  INTEGER DEFAULT 0,
    daily_pnl_before REAL DEFAULT 0,

    -- Outcome (filled post-market)
    tb_label             INTEGER DEFAULT -99,    -- +1 win, -1 loss, 0 timeout, -99 pending
    outcome_price        REAL DEFAULT 0,
    outcome_time         REAL DEFAULT 0,
    tb_target            REAL DEFAULT 0,
    tb_stop              REAL DEFAULT 0,
    tb_rr                REAL DEFAULT 0,
    tb_r_multiple        REAL DEFAULT 0,         -- PRE-COST (gross) directional R
    tb_r_multiple_net    REAL DEFAULT 0,         -- net of round-trip cost + slippage
    tb_used_custom_barrier INTEGER DEFAULT 0,
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


def _append_reason(existing: str, reason: str) -> str:
    parts = [p for p in str(existing or "").split(",") if p]
    if reason not in parts:
        parts.append(reason)
    return ",".join(parts)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _risk_reward(entry_price: float, target_price: float, stop_price: float) -> float:
    risk = abs(_safe_float(entry_price) - _safe_float(stop_price))
    reward = abs(_safe_float(target_price) - _safe_float(entry_price))
    if risk <= 0 or reward <= 0:
        return 0.0
    return reward / risk


def _signal_risk_levels(signal: dict, entry: float, side: str) -> tuple[float, float, float, str]:
    """Return signal-time risk levels for every generated candidate."""
    stop = _safe_float(signal.get("stop_loss", signal.get("sl", signal.get("stop", 0))), 0.0)
    target = _safe_float(
        signal.get("target", signal.get("target_price", signal.get("take_profit", 0))), 0.0
    )
    rr = _safe_float(signal.get("rr", signal.get("risk_reward", 0)), 0.0)
    source = str(signal.get("risk_level_source", "") or "")
    side_u = str(side or "").upper()
    valid = (
        side_u == "BUY" and 0 < stop < entry < target
    ) or (
        side_u == "SELL" and 0 < target < entry < stop
    )
    if entry > 0 and side_u in {"BUY", "SELL"} and not valid:
        metadata = signal.get("metadata", {}) if isinstance(signal.get("metadata"), dict) else {}
        atr = _safe_float(signal.get("atr", metadata.get("atr", 0)), 0.0)
        style = str(signal.get("option_style", signal.get("style", "intraday")) or "intraday").lower()
        fallback_pct = 0.005 if "scalp" in style else 0.02 if "swing" in style else 0.01
        risk = atr if 0 < atr < entry * 0.10 else entry * fallback_pct
        reward = risk * 1.5
        if side_u == "BUY":
            stop, target = entry - risk, entry + reward
        else:
            stop, target = entry + risk, entry - reward
        source = "signal_atr" if atr > 0 else f"signal_policy_{style}"
    if rr <= 0 and entry > 0 and stop > 0 and target > 0:
        rr = _risk_reward(entry, target, stop)
    return stop, target, rr, source


def _parse_expiry_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d%b%Y", "%d%b%y"):
        try:
            return datetime.strptime(text.upper(), fmt).date()
        except Exception:
            continue
    return None


def _trade_exists(trade_id: str, db_path: str = "trades.db") -> bool:
    trade_id = str(trade_id or "").strip()
    if not trade_id or not Path(db_path).exists():
        return False
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute(
                "SELECT 1 FROM trades WHERE trade_id = ? LIMIT 1",
                (trade_id,),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


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
                    "structure_mod":  "REAL DEFAULT 0",
                    "market_quality_mod": "REAL DEFAULT 0",
                    "market_profile_mod": "REAL DEFAULT 0",
                    "candidate_quality_mod": "REAL DEFAULT 0",
                    "weinstein_mod":  "REAL DEFAULT 0",
                    "sector_mod":     "REAL DEFAULT 0",
                    "crsi_mod":       "REAL DEFAULT 0",
                    "nr_mod":         "REAL DEFAULT 0",
                    "volume_mod":     "REAL DEFAULT 0",
                    "structure_label": "TEXT DEFAULT ''",
                    "structure_direction": "TEXT DEFAULT ''",
                    "structure_bos": "TEXT DEFAULT ''",
                    "structure_choch": "TEXT DEFAULT ''",
                    "structure_score": "REAL DEFAULT 0",
                    "structure_aligned": "INTEGER DEFAULT 0",
                    "structure_retest": "INTEGER DEFAULT 0",
                    "indicator_coverage": "REAL DEFAULT 0",
                    "candidate_confirmations": "INTEGER DEFAULT 0",
                    "bar_return_1_atr": "REAL DEFAULT 0",
                    "line_slope_atr": "REAL DEFAULT 0",
                    "line_turn": "REAL DEFAULT 0",
                    "step_direction": "REAL DEFAULT 0",
                    "baseline_distance_atr": "REAL DEFAULT 0",
                    "hollow_state": "REAL DEFAULT 0",
                    "hollow_run": "REAL DEFAULT 0",
                    "volume_candle_strength": "REAL DEFAULT 0",
                    "line_break_direction": "REAL DEFAULT 0",
                    "line_break_run": "REAL DEFAULT 0",
                    "line_break_event": "REAL DEFAULT 0",
                    "kagi_direction": "REAL DEFAULT 0",
                    "kagi_reversal": "REAL DEFAULT 0",
                    "kagi_distance_atr": "REAL DEFAULT 0",
                    "pnf_direction": "REAL DEFAULT 0",
                    "pnf_boxes": "REAL DEFAULT 0",
                    "pnf_reversal": "REAL DEFAULT 0",
                    "range_direction": "REAL DEFAULT 0",
                    "range_run": "REAL DEFAULT 0",
                    "range_event": "REAL DEFAULT 0",
                    "footprint_delta_proxy": "REAL DEFAULT 0",
                    "footprint_available": "REAL DEFAULT 0",
                    "ichimoku_position": "REAL DEFAULT 0",
                    "ichimoku_tk": "REAL DEFAULT 0",
                    "representation_coverage": "REAL DEFAULT 0",
                    "tick_oim": "REAL DEFAULT 0",
                    "tick_velocity": "REAL DEFAULT 0",
                    "tick_momentum": "REAL DEFAULT 0",
                    "tick_sample_count": "INTEGER DEFAULT 0",
                    "tick_flow_available": "INTEGER DEFAULT 0",
                    "profile_poc": "REAL DEFAULT 0",
                    "profile_vah": "REAL DEFAULT 0",
                    "profile_val": "REAL DEFAULT 0",
                    "profile_poc_distance_pct": "REAL DEFAULT 0",
                    "profile_value_width_pct": "REAL DEFAULT 0",
                    "profile_bias": "TEXT DEFAULT ''",
                    "profile_position": "TEXT DEFAULT ''",
                    "profile_acceptance": "TEXT DEFAULT ''",
                    # Per-signal risk levels — let the triple-barrier labeller score
                    # each signal against ITS OWN stop/target (not a generic 1.5%/1%),
                    # so the ML weighting learns from realistic R outcomes.
                    "stop_loss":      "REAL DEFAULT 0",
                    "target":         "REAL DEFAULT 0",
                    "rr":             "REAL DEFAULT 0",
                    "tb_target":      "REAL DEFAULT 0",
                    "tb_stop":        "REAL DEFAULT 0",
                    "tb_rr":          "REAL DEFAULT 0",
                    "tb_r_multiple":  "REAL DEFAULT 0",
                    "tb_r_multiple_net": "REAL DEFAULT 0",
                    "tb_used_custom_barrier": "INTEGER DEFAULT 0",
                    "risk_level_source": "TEXT DEFAULT ''",
                    "training_eligible": "INTEGER DEFAULT 0",
                    "training_exclusion_reason": "TEXT DEFAULT ''",
                    "lifecycle_status": "TEXT DEFAULT 'OPEN'",
                    "lifecycle_updated_at": "TEXT DEFAULT ''",
                    "lifecycle_price": "REAL DEFAULT 0",
                    # Real bid/ask spread at signal time (2026-07-22, external-review
                    # follow-up): the equity-side cost model (cost_aware_r_multiple)
                    # has always used a flat slippage assumption because no historical
                    # spread data existed to condition it on -- the real depth-fetch
                    # path only ever ran at order-routing time, which almost never
                    # fires. This captures a REAL market-depth reading per signal so a
                    # genuine time-of-day cost model becomes buildable after enough
                    # days accrue. signal_spread_source records how it was obtained
                    # ("live_depth"/"unavailable") so unavailable rows are distinguishable
                    # from a genuine zero spread.
                    "signal_spread_pct": "REAL DEFAULT 0",
                    "signal_spread_source": "TEXT DEFAULT ''",
                    # Prospective-holdout freeze (2026-07-22, external-review
                    # follow-up): CPCV protects against overfitting one partition
                    # of a fixed historical dataset, it does NOT protect against
                    # the cumulative effect of the research process that shaped
                    # which features/models to try. The only real fix is scoring
                    # a genuinely FROZEN model (see prospective_freeze.py) against
                    # data that didn't exist when it was built. NULL here (not 0)
                    # means "not yet scored" -- distinguishable from a genuine
                    # P(win)=0 reading.
                    "frozen_regime_pwin": "REAL",
                    "frozen_full_pwin": "REAL",
                    "frozen_model_version": "TEXT DEFAULT ''",
                    # 'historical_backfill' (signal_date <= freeze date, scored
                    # retroactively so the scoring code path itself is exercised)
                    # vs 'live_prospective' (signal_date > freeze date, genuinely
                    # unseen when the model was built) -- an external review
                    # correctly flagged that without a mechanical marker, someone
                    # evaluating "prospective" performance later could accidentally
                    # include already-seen historical rows as if they were fresh
                    # evidence. This must be assigned at write time, not
                    # reconstructed at analysis time.
                    "prediction_origin": "TEXT DEFAULT ''",
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
        signal_spread_pct: float = 0.0,
        signal_spread_source: str = "",
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
            side_value = str(signal.get("side", signal.get("direction", "")) or "").upper()
            stop_loss, target, rr, risk_level_source = _signal_risk_levels(
                signal, ep, side_value
            )
            if side_value == "BUY":
                risk_levels_valid = bool(ep > 0 and 0 < stop_loss < ep < target and rr > 0)
            elif side_value == "SELL":
                risk_levels_valid = bool(ep > 0 and 0 < target < ep < stop_loss and rr > 0)
            else:
                risk_levels_valid = False
            try:
                from trading_calendar import is_trading_day
                session_valid = is_trading_day(now)
            except Exception:
                session_valid = now.weekday() < 5
            training_eligible = bool(risk_levels_valid and session_valid)
            exclusion_reasons = []
            if not session_valid:
                exclusion_reasons.append("non_trading_session")
            if not risk_levels_valid:
                exclusion_reasons.append("missing_or_invalid_risk_levels")
            executed_flag = bool(executed)
            cleaned_reason = str(rejection_reason or "")
            cleaned_trade_id = str(trade_id or "")

            option_expiry_raw = signal.get("option_expiry", "")
            option_dte_raw = int(signal.get("option_dte", 0) or 0)
            option_symbol_raw = str(signal.get("option_symbol", "") or "")
            exp_date = _parse_expiry_date(option_expiry_raw)
            if executed_flag:
                if not cleaned_trade_id:
                    executed_flag = False
                    cleaned_reason = _append_reason(cleaned_reason, "invalid_executed_missing_trade_id")
                elif exp_date and exp_date < now.date():
                    executed_flag = False
                    cleaned_reason = _append_reason(cleaned_reason, "invalid_executed_expired_option")
                elif option_symbol_raw and option_dte_raw < 0:
                    executed_flag = False
                    cleaned_reason = _append_reason(cleaned_reason, "invalid_executed_negative_dte")

            # Pivot position flags
            above_wpvt = 1 if (weekly_pivot  > 0 and ep > weekly_pivot)  else 0
            above_mpvt = 1 if (monthly_pivot > 0 and ep > monthly_pivot) else 0
            pct_w_r1   = (ep - weekly_r1)  / weekly_r1  * 100 if weekly_r1  > 0 else 0
            pct_m_r1   = (ep - monthly_r1) / monthly_r1 * 100 if monthly_r1 > 0 else 0

            # Score modifiers from signal metadata
            meta = signal.get("metadata", {}) or {}
            mods = meta.get("score_modifiers", {}) or {}
            structure_ctx = (
                meta.get("structure_context")
                or signal.get("structure_context")
                or (meta.get("decision_inputs", {}) or {}).get("structure_context")
                or {}
            )
            if not isinstance(structure_ctx, dict):
                structure_ctx = {}
            market_quality = meta.get("market_quality") or signal.get("market_quality") or {}
            if not isinstance(market_quality, dict):
                market_quality = {}
            candidate_quality = meta.get("candidate_quality") or signal.get("candidate_quality") or {}
            if not isinstance(candidate_quality, dict):
                candidate_quality = {}
            market_profile = (
                meta.get("market_profile")
                or signal.get("market_profile")
                or (meta.get("decision_inputs", {}) or {}).get("market_profile")
                or {}
            )
            if not isinstance(market_profile, dict):
                market_profile = {}
            representation_ctx = (
                meta.get("representation_features")
                or signal.get("representation_features")
                or {}
            )
            if not isinstance(representation_ctx, dict):
                representation_ctx = {}
            tick_flow_ctx = meta.get("tick_order_flow") or signal.get("tick_order_flow") or {}
            if not isinstance(tick_flow_ctx, dict):
                tick_flow_ctx = {}

            row = {
                "signal_date":     now.strftime("%Y-%m-%d"),
                "signal_time":     now.strftime("%H:%M:%S"),
                "symbol":          sym,
                "side":            side_value,
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
                "stop_loss":       stop_loss,
                "target":          target,
                "rr":              round(rr, 4),
                "risk_level_source": risk_level_source,
                "training_eligible": 1 if training_eligible else 0,
                "training_exclusion_reason": ",".join(exclusion_reasons),
                "executed":        1 if executed_flag else 0,
                "rejection_reason":cleaned_reason,
                "trade_id":        cleaned_trade_id if executed_flag else "",
                "option_type":     str(signal.get("option_type", "") or ""),
                "option_strike":   int(signal.get("option_strike", 0) or 0),
                "option_expiry":   str(signal.get("option_expiry", "") or ""),
                "option_dte":      int(signal.get("option_dte", 0) or 0),
                "option_style":    str(signal.get("option_style", "") or ""),
                "option_premium":  float(signal.get("option_premium", 0) or 0),
                "option_symbol":   str(signal.get("option_symbol", "") or ""),
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
                "structure_mod":   float(mods.get("structure_mod", meta.get("structure_mod", 0)) or 0),
                "market_quality_mod": float(mods.get("market_quality_mod", meta.get("market_quality_mod", 0)) or 0),
                "market_profile_mod": float(mods.get("market_profile_mod", meta.get("market_profile_mod", 0)) or 0),
                "candidate_quality_mod": float(mods.get("candidate_quality_mod", meta.get("candidate_quality_mod", 0)) or 0),
                "weinstein_mod":   float(mods.get("weinstein_mod", 0) or 0),
                "sector_mod":      float(mods.get("sector_mod", 0) or 0),
                "crsi_mod":        float(mods.get("crsi_mod", 0) or 0),
                "nr_mod":          float(mods.get("nr_mod", 0) or 0),
                "volume_mod":      float(mods.get("volume_mod", 0) or 0),
                "time_bucket_wt":  float(mods.get("time_bucket", 1) or 1),
                "ai_score":        float(signal.get("confidence", 0) or 0),
                "volume_ratio":    float(signal.get("volume_ratio", 0) or 0),
                # Market structure context
                "structure_label": str(
                    structure_ctx.get("structure_label")
                    or meta.get("structure_label")
                    or ""
                ),
                "structure_direction": str(
                    structure_ctx.get("structure_direction")
                    or meta.get("structure_direction")
                    or ""
                ),
                "structure_bos": str(
                    structure_ctx.get("last_bos")
                    or meta.get("structure_bos")
                    or ""
                ),
                "structure_choch": str(
                    structure_ctx.get("choch_direction")
                    or meta.get("structure_choch")
                    or ""
                ),
                "structure_score": float(
                    structure_ctx.get("structure_score", meta.get("structure_score", 0)) or 0
                ),
                "structure_aligned": 1 if bool(structure_ctx.get("aligned", False)) else 0,
                "structure_retest": 1 if bool(structure_ctx.get("retest_confirmed", False)) else 0,
                "indicator_coverage": float(
                    (market_quality.get("metrics") or {}).get("indicator_coverage", 0) or 0
                ),
                "candidate_confirmations": int(candidate_quality.get("confirmation_count", 0) or 0),
                "tick_oim": float(tick_flow_ctx.get("oim", 0) or 0),
                "tick_velocity": float(tick_flow_ctx.get("velocity", 0) or 0),
                "tick_momentum": float(tick_flow_ctx.get("momentum", 0) or 0),
                "tick_sample_count": int(tick_flow_ctx.get("total", 0) or 0),
                "tick_flow_available": 1 if int(tick_flow_ctx.get("total", 0) or 0) > 0 else 0,
                "bar_return_1_atr": float(representation_ctx.get("bar_return_1_atr", 0) or 0),
                "line_slope_atr": float(representation_ctx.get("line_slope_atr", 0) or 0),
                "line_turn": float(representation_ctx.get("line_turn", 0) or 0),
                "step_direction": float(representation_ctx.get("step_direction", 0) or 0),
                "baseline_distance_atr": float(representation_ctx.get("baseline_distance_atr", 0) or 0),
                "hollow_state": float(representation_ctx.get("hollow_state", 0) or 0),
                "hollow_run": float(representation_ctx.get("hollow_run", 0) or 0),
                "volume_candle_strength": float(representation_ctx.get("volume_candle_strength", 0) or 0),
                "line_break_direction": float(representation_ctx.get("line_break_direction", 0) or 0),
                "line_break_run": float(representation_ctx.get("line_break_run", 0) or 0),
                "line_break_event": float(representation_ctx.get("line_break_event", 0) or 0),
                "kagi_direction": float(representation_ctx.get("kagi_direction", 0) or 0),
                "kagi_reversal": float(representation_ctx.get("kagi_reversal", 0) or 0),
                "kagi_distance_atr": float(representation_ctx.get("kagi_distance_atr", 0) or 0),
                "pnf_direction": float(representation_ctx.get("pnf_direction", 0) or 0),
                "pnf_boxes": float(representation_ctx.get("pnf_boxes", 0) or 0),
                "pnf_reversal": float(representation_ctx.get("pnf_reversal", 0) or 0),
                "range_direction": float(representation_ctx.get("range_direction", 0) or 0),
                "range_run": float(representation_ctx.get("range_run", 0) or 0),
                "range_event": float(representation_ctx.get("range_event", 0) or 0),
                "footprint_delta_proxy": float(representation_ctx.get("footprint_delta_proxy", 0) or 0),
                "footprint_available": float(representation_ctx.get("footprint_available", 0) or 0),
                "ichimoku_position": float(representation_ctx.get("ichimoku_position", 0) or 0),
                "ichimoku_tk": float(representation_ctx.get("ichimoku_tk", 0) or 0),
                "representation_coverage": float(representation_ctx.get("representation_coverage", 0) or 0),
                "profile_poc": float(market_profile.get("poc", meta.get("profile_poc", 0)) or 0),
                "profile_vah": float(market_profile.get("vah", meta.get("profile_vah", 0)) or 0),
                "profile_val": float(market_profile.get("val", meta.get("profile_val", 0)) or 0),
                "profile_poc_distance_pct": float(
                    market_profile.get("poc_distance_pct", meta.get("profile_poc_distance_pct", 0)) or 0
                ),
                "profile_value_width_pct": float(
                    market_profile.get("value_width_pct", meta.get("profile_value_width_pct", 0)) or 0
                ),
                "profile_bias": str(market_profile.get("profile_bias", meta.get("profile_bias", "")) or ""),
                "profile_position": str(market_profile.get("profile_position", meta.get("profile_position", "")) or ""),
                "profile_acceptance": str(
                    market_profile.get("acceptance_state", meta.get("profile_acceptance", "")) or ""
                ),
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
                "signal_spread_pct": signal_spread_pct,
                "signal_spread_source": signal_spread_source,
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
                      strategy: str = "",
                      option_metadata: Optional[Dict[str, Any]] = None,
                      require_trade_row: bool = True) -> bool:
        """
        Flip executed=0 -> 1 on the most recent candidate row for this
        symbol today (matching strategy when given) and attach the trade_id.
        Optionally update option metadata fields for options trades.

        Candidates are always logged with executed=False before the execution
        gates run ("updated later if executed") — this is the missing other
        half: until 2026-06-12 nothing ever updated it, so all 12k+ logged
        signals showed executed=0 and the ML pipeline had no positive class.
        
        Parameters
        ----------
        option_metadata : dict, optional
            Option contract details: option_type, option_strike, option_expiry,
            option_dte, option_style, option_premium, option_symbol
        """
        try:
            trade_id = str(trade_id or "").strip()
            if require_trade_row and not _trade_exists(trade_id):
                logger.warning(
                    "signal_log.mark_executed blocked: trade_id=%s missing in trades.db",
                    trade_id or "<empty>",
                )
                return False

            if option_metadata and isinstance(option_metadata, dict):
                exp_date = _parse_expiry_date(option_metadata.get("option_expiry"))
                if exp_date and exp_date < date.today():
                    logger.warning(
                        "signal_log.mark_executed blocked: expired option expiry=%s trade_id=%s",
                        option_metadata.get("option_expiry"), trade_id,
                    )
                    return False
                try:
                    if int(option_metadata.get("option_dte", 0) or 0) < 0:
                        logger.warning(
                            "signal_log.mark_executed blocked: negative DTE trade_id=%s",
                            trade_id,
                        )
                        return False
                except Exception:
                    pass

            with self._conn() as conn:
                # 2-day window handles late-evening executions that roll past
                # midnight before mark_executed is called.
                base = (f"SELECT id, entry_price, stop_loss, target FROM {_TBL} WHERE symbol = ? "
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
                    # Build UPDATE statement with option metadata if provided
                    if option_metadata and isinstance(option_metadata, dict):
                        cols = [
                            "executed = 1",
                            "trade_id = ?",
                            "rejection_reason = CASE "
                            "WHEN COALESCE(rejection_reason,'') != '' "
                            "THEN 'shadow_executed|' || rejection_reason "
                            "ELSE rejection_reason END",
                        ]
                        vals = [str(trade_id or "")]
                        if "option_type" in option_metadata:
                            cols.append("option_type = ?")
                            vals.append(str(option_metadata.get("option_type", "")))
                        if "option_strike" in option_metadata:
                            cols.append("option_strike = ?")
                            vals.append(int(option_metadata.get("option_strike", 0)))
                        if "option_expiry" in option_metadata:
                            cols.append("option_expiry = ?")
                            vals.append(str(option_metadata.get("option_expiry", "")))
                        if "option_dte" in option_metadata:
                            cols.append("option_dte = ?")
                            vals.append(int(option_metadata.get("option_dte", 0)))
                        if "option_style" in option_metadata:
                            cols.append("option_style = ?")
                            vals.append(str(option_metadata.get("option_style", "")))
                        if "option_premium" in option_metadata:
                            cols.append("option_premium = ?")
                            vals.append(float(option_metadata.get("option_premium", 0)))
                        if "option_symbol" in option_metadata:
                            cols.append("option_symbol = ?")
                            vals.append(str(option_metadata.get("option_symbol", "")))
                        stop_loss = _safe_float(
                            option_metadata.get("stop_loss", option_metadata.get("sl", 0)),
                            0.0,
                        )
                        target = _safe_float(
                            option_metadata.get(
                                "target",
                                option_metadata.get("target_price", option_metadata.get("take_profit", 0)),
                            ),
                            0.0,
                        )
                        rr = _safe_float(option_metadata.get("rr", option_metadata.get("risk_reward", 0)), 0.0)
                        entry_price = _safe_float(row["entry_price"] if hasattr(row, "keys") else row[1], 0.0)
                        if stop_loss > 0:
                            cols.append("stop_loss = ?")
                            vals.append(stop_loss)
                        else:
                            stop_loss = _safe_float(row["stop_loss"] if hasattr(row, "keys") else row[2], 0.0)
                        if target > 0:
                            cols.append("target = ?")
                            vals.append(target)
                        else:
                            target = _safe_float(row["target"] if hasattr(row, "keys") else row[3], 0.0)
                        if rr <= 0 and entry_price > 0 and stop_loss > 0 and target > 0:
                            rr = _risk_reward(entry_price, target, stop_loss)
                        if rr > 0:
                            cols.append("rr = ?")
                            vals.append(round(rr, 4))
                        vals.append(row[0])
                        update_sql = f"UPDATE {_TBL} SET {', '.join(cols)} WHERE id = ?"
                        conn.execute(update_sql, vals)
                    else:
                        conn.execute(
                            f"UPDATE {_TBL} SET executed = 1, trade_id = ?, "
                            "rejection_reason = CASE "
                            "WHEN COALESCE(rejection_reason,'') != '' "
                            "THEN 'shadow_executed|' || rejection_reason "
                            "ELSE rejection_reason END "
                            "WHERE id = ?",
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
            from triple_barrier import (
                cost_aware_r_multiple,
                get_dynamic_barriers,
                label_triple_barrier,
                r_multiple_for_outcome,
                resolve_barrier_levels,
                risk_reward_from_levels,
            )
        except ImportError:
            logger.warning("triple_barrier not available for labelling")
            return 0

        labelled = 0
        try:
            from signal_quality import quarantine_signal_log
            quarantine_signal_log(self.db_path)
        except Exception as exc:
            logger.warning("Pre-label signal quality quarantine failed: %s", exc)
        try:
            with self._conn() as conn:
                pending = conn.execute(
                    f"SELECT id, symbol, side, entry_price, signal_time, "
                    f"signal_date, stop_loss, target, rr "
                    f"FROM {_TBL} WHERE tb_label = -99 "
                    f"AND training_eligible = 1 "
                    f"AND signal_date <= date('now','localtime')"
                ).fetchall()
                conn.execute(
                    f"UPDATE {_TBL} SET tb_label=-2 "
                    f"WHERE tb_label=-99 AND training_eligible=0 "
                    f"AND signal_date < date('now','localtime')"
                )

            for row in pending:
                sig_id = row["id"]
                sym    = row["symbol"]
                side   = str(row["side"] or "").upper()
                ep     = float(row["entry_price"] or 0)
                stored_stop = _safe_float(row["stop_loss"], 0.0)
                stored_target = _safe_float(row["target"], 0.0)
                stored_rr = _safe_float(row["rr"], 0.0)
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
                        history = df.iloc[: entry_idx + 1]
                        high = history["high"] if "high" in history.columns else history.iloc[:, 1]
                        low  = history["low"]  if "low"  in history.columns else history.iloc[:, 2]
                        atr  = float((high - low).tail(14).mean())

                    t_pct, s_pct, max_b = get_dynamic_barriers(atr, ep)
                    tb_target, tb_stop, used_custom = resolve_barrier_levels(
                        ep,
                        side=side,
                        target_pct=t_pct,
                        stop_pct=s_pct,
                        target_price=stored_target,
                        stop_price=stored_stop,
                    )
                    tb_rr = stored_rr if stored_rr > 0 else risk_reward_from_levels(ep, tb_target, tb_stop)
                    label = label_triple_barrier(
                        df,
                        entry_idx,
                        ep,
                        t_pct,
                        s_pct,
                        max_b,
                        side,
                        target_price=stored_target,
                        stop_price=stored_stop,
                    )

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
                        touch_idx = None
                        for i in range(len(future)):
                            high_i = float(highs.iloc[i])
                            low_i = float(lows.iloc[i])
                            if side == "BUY":
                                touched = high_i >= tb_target or low_i <= tb_stop
                            else:
                                touched = low_i <= tb_target or high_i >= tb_stop
                            if touched:
                                touch_idx = i
                                break
                        ob = future.index[touch_idx if touch_idx is not None else -1]
                        outcome_price = float(closes.iloc[touch_idx]
                                              if touch_idx is not None else closes.iloc[-1])
                        outcome_time = str(ob)
                    else:
                        peak = ep; max_adv = 0.0; max_fav = 0.0
                    tb_r_multiple = r_multiple_for_outcome(ep, outcome_price, side, tb_stop)
                    # Net-of-cost directional R (corrected cost model + slippage):
                    # removes the PRE-COST / asymmetric-barrier positive bias.
                    tb_r_net = cost_aware_r_multiple(ep, outcome_price, side, tb_stop)

                    # Never persist a decided training label when entry and
                    # outcome are from different price scales/instruments.
                    # Keep the row for auditability, but quarantine it from all
                    # learners that honor training_eligible.
                    try:
                        from signal_quality import price_row_ok
                        price_ok, price_reason = price_row_ok(ep, outcome_price)
                    except Exception:
                        price_ok, price_reason = True, ""
                    if not price_ok:
                        with self._conn() as conn:
                            old_reason = conn.execute(
                                f"SELECT training_exclusion_reason FROM {_TBL} WHERE id=?",
                                (sig_id,),
                            ).fetchone()
                            reason = _append_reason(
                                old_reason[0] if old_reason else "",
                                f"data_quality_{price_reason}",
                            )
                            conn.execute(
                                f"UPDATE {_TBL} SET tb_label=-2, outcome_price=?, "
                                f"outcome_time=?, tb_target=?, tb_stop=?, tb_rr=?, "
                                f"tb_r_multiple=0, tb_r_multiple_net=0, "
                                f"training_eligible=0, training_exclusion_reason=? "
                                f"WHERE id=?",
                                (round(outcome_price, 4), outcome_time,
                                 round(tb_target, 4), round(tb_stop, 4), round(tb_rr, 4),
                                 reason, sig_id),
                            )
                        logger.error(
                            "Quarantined corrupt TB label id=%s %s: entry=%.4f outcome=%.4f (%s)",
                            sig_id, sym, ep, outcome_price, price_reason,
                        )
                        continue

                    with self._conn() as conn:
                        conn.execute(
                            f"UPDATE {_TBL} SET tb_label=?, peak_price=?, "
                            f"max_adverse_move=?, max_favorable_move=?, "
                            f"outcome_price=?, outcome_time=?, "
                            f"tb_target=?, tb_stop=?, tb_rr=?, tb_r_multiple=?, "
                            f"tb_r_multiple_net=?, "
                            f"tb_used_custom_barrier=? "
                            f"WHERE id=?",
                            (label, peak, round(max_adv, 3), round(max_fav, 3),
                             round(outcome_price, 4), outcome_time,
                             round(tb_target, 4), round(tb_stop, 4), round(tb_rr, 4),
                             round(tb_r_multiple, 4), round(tb_r_net, 4),
                             1 if used_custom else 0, sig_id)
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
                  AND tb_label IN (-1, 0, 1)
                  AND training_eligible = 1
                  AND stop_loss > 0 AND target > 0 AND rr > 0
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


# Confirmation gate: keep/prune verdicts need this many DISTINCT strict days.
EDGE_GATE_DAYS = 8


def usable_edge_days(db_path: str = str(_DB_PATH)) -> int:
    """Canonical STRICT count of distinct trading days usable for edge
    confirmation — the single source of truth for every status surface.

    Strict = labelled + training_eligible + real risk levels (same basis as
    nightly_edge_monitor's CONFIRMED gate). A looser count (tb_stop>0 only)
    includes early rows that predate the risk-level columns and once overstated
    readiness by 2 days (2026-07-06 review). Quote THIS number everywhere."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        n = conn.execute(
            f"SELECT COUNT(DISTINCT signal_date) FROM {_TBL} "
            "WHERE tb_label IN (-1,0,1) AND training_eligible=1 "
            "AND stop_loss>0 AND target>0 AND rr>0"
        ).fetchone()[0]
        conn.close()
        return int(n or 0)
    except Exception as e:
        logger.debug("usable_edge_days: %s", e)
        return 0


def worthiness_summary(db_path: str = str(_DB_PATH), days: int = 30,
                       min_n: int = 20, top: int = 5) -> dict:
    """
    Signal worthiness from triple-barrier shadow labels — GROSS vs NET-of-cost R.

    Decides edge from every generated signal (not just executed trades). Net R
    subtracts the corrected round-trip cost + slippage, removing the PRE-COST /
    asymmetric-barrier positive bias. Only rows with a real barrier (tb_stop>0)
    are scored; net is computed on the fly where the column isn't populated yet.
    Reports meta-labeler readiness (needs >=10 distinct trading days).
    """
    import sqlite3 as _sql
    out = {"ok": False, "days": days}
    try:
        from triple_barrier import cost_aware_r_multiple, r_multiple_for_outcome
        conn = _sql.connect(db_path)
        conn.row_factory = _sql.Row
        rows = conn.execute(
            f"SELECT strategy, side, entry_price, outcome_price, tb_stop, tb_label, "
            f"tb_r_multiple, tb_r_multiple_net, signal_date "
            f"FROM {_TBL} WHERE tb_label IN (1,0,-1) AND training_eligible=1 "
            f"AND stop_loss>0 AND target>0 AND rr>0 AND tb_stop > 0 "
            f"AND signal_date >= date('now', ?, 'localtime')",
            (f'-{int(days)} days',),
        ).fetchall()
        conn.close()
    except Exception as e:
        out["error"] = str(e)
        return out

    if not rows:
        out["error"] = "no_barriered_labels_in_window"
        return out

    days_seen, by_strat = set(), {}
    n = wins = net_pos = 0
    g_sum = nt_sum = 0.0
    for r in rows:
        ep = r["entry_price"] or 0; op = r["outcome_price"] or 0
        st = r["tb_stop"] or 0; side = r["side"] or "BUY"
        if ep <= 0 or op <= 0 or st <= 0:
            continue
        g = r["tb_r_multiple"] or r_multiple_for_outcome(ep, op, side, st)
        nt = r["tb_r_multiple_net"] or cost_aware_r_multiple(ep, op, side, st)
        n += 1
        if r["tb_label"] == 1: wins += 1
        if nt > 0: net_pos += 1
        g_sum += g; nt_sum += nt
        if r["signal_date"]: days_seen.add(str(r["signal_date"]))
        s = (r["strategy"] or "?")
        d = by_strat.setdefault(s, {"n": 0, "g": 0.0, "nt": 0.0})
        d["n"] += 1; d["g"] += g; d["nt"] += nt

    if n == 0:
        out["error"] = "no_valid_rows"
        return out

    ranked = sorted(
        ({"strategy": k, "n": v["n"], "avg_gross_R": round(v["g"]/v["n"], 3),
          "avg_net_R": round(v["nt"]/v["n"], 3)} for k, v in by_strat.items() if v["n"] >= min_n),
        key=lambda x: x["avg_net_R"], reverse=True,
    )
    dd = len(days_seen)
    strict_days = usable_edge_days(db_path)
    out.update({
        "ok": True, "n_scored": n, "distinct_days": dd,
        # STRICT canonical day count — quote THIS for gate-readiness everywhere
        "usable_days_strict": strict_days,
        "edge_gate_days": EDGE_GATE_DAYS,
        "edge_gate_ready": strict_days >= EDGE_GATE_DAYS,
        "win_rate": round(100.0*wins/n, 1),
        "avg_gross_R": round(g_sum/n, 3), "avg_net_R": round(nt_sum/n, 3),
        "pct_net_positive": round(100.0*net_pos/n, 1),
        "meta_labeler_ready": dd >= 10,
        "best": ranked[:top], "worst": ranked[-top:][::-1] if len(ranked) > top else [],
    })
    return out


def strategy_selection_pbo(db_path: str = str(_DB_PATH), days: int = 3650,
                           n_splits: int = 8, min_days: int = 8) -> dict:
    """Probability of Backtest Overfitting for STRATEGY selection.

    Builds a (trading-day × strategy) matrix of mean net-of-cost R from the
    triple-barrier shadow labels and asks, via CSCV: if you pick the best strategy
    in-sample, how often is it below median out-of-sample? High PBO = "the best
    pocket is probably luck" (exactly the risk when chasing positive sub-groups on
    few days). Needs >= min_days distinct days; returns {ok:False} otherwise.
    """
    import sqlite3 as _sql
    out = {"ok": False}
    try:
        import numpy as _np
        from triple_barrier import cost_aware_r_multiple, r_multiple_for_outcome
        from pbo import probability_of_backtest_overfitting
        conn = _sql.connect(db_path)
        conn.row_factory = _sql.Row
        rows = conn.execute(
            f"SELECT strategy, side, entry_price, outcome_price, tb_stop, "
            f"tb_r_multiple, tb_r_multiple_net, signal_date "
            f"FROM {_TBL} WHERE tb_label IN (1,0,-1) AND training_eligible=1 "
            f"AND stop_loss>0 AND target>0 AND rr>0 AND tb_stop > 0 "
            f"AND signal_date >= date('now', ?, 'localtime')",
            (f'-{int(days)} days',),
        ).fetchall()
        conn.close()
    except Exception as e:
        out["error"] = str(e)
        return out

    cell = {}  # (day, strategy) -> [net_r,...]
    strategies, dayset = set(), set()
    for r in rows:
        ep, op, st = r["entry_price"] or 0, r["outcome_price"] or 0, r["tb_stop"] or 0
        if ep <= 0 or op <= 0 or st <= 0 or not r["signal_date"]:
            continue
        nt = r["tb_r_multiple_net"] or cost_aware_r_multiple(ep, op, r["side"] or "BUY", st)
        d, s = str(r["signal_date"]), (r["strategy"] or "?")
        cell.setdefault((d, s), []).append(nt)
        strategies.add(s); dayset.add(d)

    dd = len(dayset)
    strategies = sorted(strategies)
    if dd < int(min_days) or len(strategies) < 2:
        out.update({"ok": False, "distinct_days": dd, "n_strategies": len(strategies),
                    "reason": f"need >= {min_days} days and >= 2 strategies "
                              f"(have {dd} days, {len(strategies)} strategies)"})
        return out

    day_list = sorted(dayset)
    M = _np.zeros((len(day_list), len(strategies)), dtype=float)
    for i, d in enumerate(day_list):
        for j, s in enumerate(strategies):
            v = cell.get((d, s))
            M[i, j] = float(_np.mean(v)) if v else 0.0
    res = probability_of_backtest_overfitting(M, n_splits=min(int(n_splits), len(day_list)))
    res.update({"ok": True, "distinct_days": dd, "n_strategies": len(strategies)})
    return res
