"""Persist and rank per-strike CE/PE flow signals from option snapshots."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from option_quality import extract_contract_liquidity

logger = logging.getLogger(__name__)


EDGE_MODEL_FILE = "option_multistrike_edge.json"


@dataclass(frozen=True)
class StrikeFlowSignal:
    underlying: str
    strike: float
    option_type: str
    flow: str
    signal: str
    direction: str
    score: float
    tradable: bool
    price: float
    price_change_pct: float
    oi: float
    oi_change_pct: float
    volume: float
    volume_change_pct: float
    spread_pct: Optional[float]
    reason: str
    source: str = ""
    market_regime: str = "UNKNOWN"
    market_bias: str = "UNKNOWN"
    regime_aligned: Optional[bool] = None
    score_rank: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    edge_policy: str = "VALIDATING"
    edge_outcomes: int = 0
    edge_avg_r: float = 0.0
    edge_profit_factor: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def ensure_multistrike_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS option_strike_signals (
            ts REAL NOT NULL,
            snapshot_time TEXT NOT NULL,
            underlying TEXT NOT NULL,
            expiry TEXT DEFAULT '',
            strike REAL NOT NULL,
            option_type TEXT NOT NULL,
            flow TEXT NOT NULL,
            signal TEXT NOT NULL,
            direction TEXT NOT NULL,
            score REAL DEFAULT 0,
            tradable INTEGER DEFAULT 0,
            price REAL DEFAULT 0,
            price_change_pct REAL DEFAULT 0,
            oi REAL DEFAULT 0,
            oi_change_pct REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            volume_change_pct REAL DEFAULT 0,
            spread_pct REAL,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT ''
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(option_strike_signals)")}
    migrations = {
        "outcome_label": "INTEGER DEFAULT -99",
        "exit_price": "REAL DEFAULT 0",
        "gross_pnl": "REAL DEFAULT 0",
        "estimated_costs": "REAL DEFAULT 0",
        "net_pnl": "REAL DEFAULT 0",
        "labelled_at": "TEXT DEFAULT ''",
        "market_regime": "TEXT DEFAULT 'UNKNOWN'",
        "market_bias": "TEXT DEFAULT 'UNKNOWN'",
        "regime_aligned": "INTEGER DEFAULT -1",
        "score_rank": "INTEGER DEFAULT 0",
        "execution_status": "TEXT DEFAULT ''",
        "fill_latency_sec": "REAL DEFAULT 0",
        "fill_probability": "REAL DEFAULT 0",
        "capital_at_risk": "REAL DEFAULT 0",
        "net_r": "REAL DEFAULT 0",
        "entry_price": "REAL DEFAULT 0",
        "stop_loss": "REAL DEFAULT 0",
        "target_1": "REAL DEFAULT 0",
        "target_2": "REAL DEFAULT 0",
        "lifecycle_status": "TEXT DEFAULT ''",
        "status_updated_at": "TEXT DEFAULT ''",
        "edge_policy": "TEXT DEFAULT 'VALIDATING'",
        "edge_outcomes": "INTEGER DEFAULT 0",
        "edge_avg_r": "REAL DEFAULT 0",
        "edge_profit_factor": "REAL DEFAULT 0",
        # 2026-07-16: added so the multi-strategy option catalog
        # (option_strategy_registry.py / option_core_strategies.py) can
        # write shadow-only legs into this same table and have
        # option_live_edge_policy.cohort_policy() accrue evidence per
        # strategy, not just per (flow, direction, score, dte). `side`
        # distinguishes short (SELL) legs -- every row before this migration
        # was BUY-only, hence the backfill below.
        "strategy": "TEXT DEFAULT ''",
        "combo_id": "TEXT DEFAULT ''",
        "side": "TEXT DEFAULT 'BUY'",
    }
    for name, declaration in migrations.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE option_strike_signals ADD COLUMN {name} {declaration}")
    conn.execute(
        """UPDATE option_strike_signals
              SET strategy='single_strike_flow'
            WHERE COALESCE(strategy,'')=''"""
    )
    conn.execute(
        """UPDATE option_strike_signals
              SET side='BUY'
            WHERE COALESCE(side,'')=''"""
    )
    conn.execute(
        """UPDATE option_strike_signals
              SET lifecycle_status=CASE WHEN tradable=1 THEN 'OPEN' ELSE 'WATCH' END,
                  status_updated_at=snapshot_time
            WHERE COALESCE(lifecycle_status,'')=''"""
    )
    conn.execute(
        """UPDATE option_strike_signals
              SET entry_price=ROUND(price*(1+MAX(COALESCE(spread_pct,0),0)/2),2),
                  stop_loss=ROUND(price*(1+MAX(COALESCE(spread_pct,0),0)/2)*0.85,2),
                  target_1=ROUND(price*(1+MAX(COALESCE(spread_pct,0),0)/2)*1.20,2),
                  target_2=ROUND(price*(1+MAX(COALESCE(spread_pct,0),0)/2)*1.35,2)
            WHERE price>0 AND COALESCE(entry_price,0)<=0"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strike_signal_lookup "
        "ON option_strike_signals(underlying, option_type, ts DESC, score DESC)"
    )
    # 2026-07-16: widened to include `strategy`. The old 4-column unique key
    # (snapshot_time, underlying, strike, option_type) would otherwise let a
    # new strategy's shadow leg silently collide with (INSERT OR REPLACE
    # clobber) the single-strike-flow system's row for the exact same
    # strike/snapshot -- a real risk since ATM/near-ATM strikes are exactly
    # what both systems track. Recreating (not just adding a second index)
    # because the old unique constraint must stop being enforced on its own.
    conn.execute("DROP INDEX IF EXISTS idx_strike_signal_unique")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_strike_signal_unique "
        "ON option_strike_signals(snapshot_time, underlying, strike, option_type, strategy)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strike_signal_strategy "
        "ON option_strike_signals(strategy, ts DESC)"
    )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _cfg_bool(name: str, default: bool) -> bool:
    try:
        import config as cfg
        default = bool(getattr(cfg, name, default))
    except Exception as exc:
        logger.debug("_cfg_bool %s: %s", name, exc)
    return _env_bool(name, default)


def _cfg_float(name: str, default: float) -> float:
    try:
        import config as cfg
        default = float(getattr(cfg, name, default) or default)
    except Exception as exc:
        logger.debug("_cfg_float %s: %s", name, exc)
    raw = os.getenv(name)
    return _f(raw, default) if raw is not None else float(default)


def _pct(current: float, previous: float) -> float:
    if previous <= 0:
        return 0.0
    return (current - previous) / previous * 100.0


def _snapshot_epoch(snapshot_time: str) -> float:
    text = str(snapshot_time or "").strip()
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        try:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except Exception:
            return time.time()


def _strikes(rows: Iterable[Dict[str, Any]]) -> List[float]:
    out = set()
    for row in rows:
        try:
            value = float(row.get("strikePrice", row.get("strike", 0)) or 0)
            if value > 0:
                out.add(value)
        except Exception:
            continue
    return sorted(out)


def _classify(price_change_pct: float, oi_change_pct: float) -> tuple[str, str]:
    price_up = price_change_pct > 0.05
    price_down = price_change_pct < -0.05
    oi_up = oi_change_pct > 0.05
    oi_down = oi_change_pct < -0.05
    if price_up and oi_up:
        return "LONG_BUILDUP", "BUY"
    if price_up and oi_down:
        return "SHORT_COVERING", "BUY"
    if price_down and oi_up:
        return "SHORT_BUILDUP", "AVOID_BUY"
    if price_down and oi_down:
        return "LONG_UNWINDING", "AVOID_BUY"
    return "NEUTRAL", "WATCH"


def _normalise_bias(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "UPTREND", "BULLISH_TREND"}:
        return "BULLISH"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWNTREND", "BEARISH_TREND"}:
        return "BEARISH"
    return "UNKNOWN"


def _calibrated_score(
    *,
    price_change_pct: float,
    oi_change_pct: float,
    volume_change_pct: float,
    oi: float,
    volume: float,
    spread_pct: Optional[float],
    min_oi: float,
    min_volume: float,
    max_spread_pct: float,
    flow: str,
    regime_aligned: Optional[bool],
) -> float:
    """Continuous 0-100 score that does not saturate on ordinary moves."""
    momentum = 28.0 * math.tanh(abs(price_change_pct) / 12.0)
    oi_strength = 18.0 * math.tanh(abs(oi_change_pct) / 25.0)
    volume_strength = 10.0 * math.tanh(max(0.0, volume_change_pct) / 60.0)
    oi_quality = 8.0 * min(1.0, math.log1p(max(0.0, oi) / max(min_oi, 1.0)) / math.log(4.0))
    volume_quality = 8.0 * min(1.0, math.log1p(max(0.0, volume) / max(min_volume, 1.0)) / math.log(4.0))
    if spread_pct is None or max_spread_pct <= 0:
        spread_quality = 0.0
    else:
        spread_quality = 12.0 * max(0.0, 1.0 - float(spread_pct) / max_spread_pct)
    flow_quality = {
        "SHORT_COVERING": 12.0,
        "LONG_BUILDUP": 10.0,
        "SHORT_BUILDUP": 4.0,
        "LONG_UNWINDING": 2.0,
    }.get(flow, 0.0)
    alignment = 4.0 if regime_aligned is True else -28.0 if regime_aligned is False else 0.0
    return max(0.0, min(100.0, momentum + oi_strength + volume_strength + oi_quality
                        + volume_quality + spread_quality + flow_quality + alignment))


def build_multistrike_signals(
    *,
    underlying: str,
    current_rows: List[Dict[str, Any]],
    previous_rows: Optional[List[Dict[str, Any]]] = None,
    source: str = "",
    min_oi: float = 100.0,
    min_volume: float = 100.0,
    max_spread_pct: float = 0.03,
    min_score: float = 60.0,
    max_score: float = 85.0,
    top_n_per_side: int = 5,
    market_regime: str = "UNKNOWN",
    market_bias: str = "UNKNOWN",
    max_tradable_per_snapshot: int = 2,
) -> List[StrikeFlowSignal]:
    previous_rows = previous_rows or []
    current_data = {"chain": current_rows, "source": source}
    previous_data = {"chain": previous_rows}
    ranked: List[StrikeFlowSignal] = []
    normalised_bias = _normalise_bias(market_bias)
    for strike in _strikes(current_rows):
        for option_type in ("CE", "PE"):
            current = extract_contract_liquidity(
                current_data, strike=strike, option_type=option_type
            )
            previous = extract_contract_liquidity(
                previous_data, strike=strike, option_type=option_type
            )
            price = _f(current.get("last_price"))
            oi = _f(current.get("oi"))
            volume = _f(current.get("volume"))
            spread = current.get("spread_pct")
            price_change = _pct(price, _f(previous.get("last_price")))
            oi_change = _pct(oi, _f(previous.get("oi")))
            volume_change = _pct(volume, _f(previous.get("volume")))
            flow, action = _classify(price_change, oi_change)
            direction = "BULLISH" if option_type == "CE" else "BEARISH"
            regime_aligned = None if normalised_bias == "UNKNOWN" else direction == normalised_bias

            liquidity_ok = oi >= min_oi and volume >= min_volume
            spread_ok = spread is not None and 0 <= float(spread) <= max_spread_pct
            history_ok = bool(previous_rows and _f(previous.get("last_price")) > 0)
            score = _calibrated_score(
                price_change_pct=price_change, oi_change_pct=oi_change,
                volume_change_pct=volume_change, oi=oi, volume=volume,
                spread_pct=None if spread is None else float(spread),
                min_oi=min_oi, min_volume=min_volume, max_spread_pct=max_spread_pct,
                flow=flow, regime_aligned=regime_aligned,
            )
            tradable = bool(
                history_ok and liquidity_ok and spread_ok
                and action == "BUY" and min_score <= score < max_score
                and regime_aligned is not False
            )
            reason_parts = [flow.lower()]
            if not history_ok:
                reason_parts.append("warmup_no_previous_snapshot")
            if not liquidity_ok:
                reason_parts.append("liquidity_below_minimum")
            if not spread_ok:
                reason_parts.append("spread_missing_or_wide")
            if action != "BUY":
                reason_parts.append("flow_not_buyable")
            if regime_aligned is False:
                reason_parts.append("counter_regime_direction")
            elif regime_aligned is True:
                reason_parts.append("regime_aligned")
            if score < min_score:
                reason_parts.append("score_below_minimum")
            if score >= max_score:
                reason_parts.append("score_exhaustion_cap")
            signal = f"BUY_{option_type}" if action == "BUY" else "WATCH"
            # Premium-risk plan shown with every generated signal. Execution
            # still requires the tradable gate; these levels are not a fill.
            half_spread = max(0.0, float(spread or 0.0)) / 2.0
            entry_price = price * (1.0 + half_spread)
            stop_loss = entry_price * 0.85
            target_1 = entry_price * 1.20
            target_2 = entry_price * 1.35
            ranked.append(StrikeFlowSignal(
                underlying=str(underlying).upper(), strike=strike,
                option_type=option_type, flow=flow, signal=signal,
                direction=direction, score=round(score, 2), tradable=tradable,
                price=round(price, 4), price_change_pct=round(price_change, 4),
                oi=round(oi, 4), oi_change_pct=round(oi_change, 4),
                volume=round(volume, 4), volume_change_pct=round(volume_change, 4),
                spread_pct=None if spread is None else round(float(spread), 6),
                reason=",".join(reason_parts), source=str(source or ""),
                market_regime=str(market_regime or "UNKNOWN").upper(),
                market_bias=normalised_bias, regime_aligned=regime_aligned,
                entry_price=round(entry_price, 2), stop_loss=round(stop_loss, 2),
                target_1=round(target_1, 2), target_2=round(target_2, 2),
            ))

    output: List[StrikeFlowSignal] = []
    limit = max(1, int(top_n_per_side or 1))
    for option_type in ("CE", "PE"):
        side_rows = [row for row in ranked if row.option_type == option_type]
        side_rows.sort(key=lambda row: (row.tradable, row.score), reverse=True)
        output.extend(side_rows[:limit])

    # Keep all research candidates, but permit at most one live candidate for a
    # correlated (direction, flow) cluster and cap the whole snapshot.
    tradable_seen = set()
    tradable_count = 0
    final: List[StrikeFlowSignal] = []
    for rank, row in enumerate(sorted(output, key=lambda item: item.score, reverse=True), start=1):
        cluster = (row.direction, row.flow)
        allow = row.tradable and cluster not in tradable_seen and tradable_count < max(1, int(max_tradable_per_snapshot))
        reason = row.reason
        if row.tradable and not allow:
            reason = f"{reason},correlated_strike_deduplicated"
        if allow:
            tradable_seen.add(cluster)
            tradable_count += 1
        final.append(replace(row, tradable=allow, reason=reason, score_rank=rank))
    return sorted(final, key=lambda item: (item.option_type, item.score_rank))


def persist_multistrike_signals(
    *,
    conn: sqlite3.Connection,
    snapshot_time: str,
    underlying: str,
    expiry: str,
    current_rows: List[Dict[str, Any]],
    source: str,
    top_n_per_side: int = 5,
    market_regime: str = "UNKNOWN",
    market_bias: str = "UNKNOWN",
) -> Dict[str, Any]:
    ensure_multistrike_schema(conn)
    previous = conn.execute(
        """
        SELECT rows_json FROM option_chain_snapshots
         WHERE upper(underlying)=upper(?) AND ok=1 AND snapshot_time < ?
         ORDER BY ts DESC LIMIT 1
        """,
        (underlying, snapshot_time),
    ).fetchone()
    try:
        previous_rows = json.loads(previous[0] or "[]") if previous else []
    except Exception:
        previous_rows = []
    signals = build_multistrike_signals(
        underlying=underlying,
        current_rows=current_rows,
        previous_rows=previous_rows,
        source=source,
        top_n_per_side=top_n_per_side,
        market_regime=market_regime,
        market_bias=market_bias,
    )
    edge_gate = _execution_edge_gate(conn)
    from option_live_edge_policy import cohort_policy, dte_bucket
    block_quarantined = _cfg_bool("OPTION_EDGE_POLICY_BLOCK_QUARANTINED", True)
    require_promising = _cfg_bool("OPTION_EDGE_POLICY_REQUIRE_PROMISING", True)
    promising_bonus = max(0.0, _cfg_float("OPTION_EDGE_POLICY_PROMISING_SCORE_BONUS", 3.0))
    # 2026-07-15: dte:8plus is the single strongest, most holdout-confirmed
    # losing cohort option_cohort_edge_miner.py has found (train t=-24.24,
    # holdout n=5450 R=-0.023, worse than train) — the gate below previously
    # had no DTE resolution at all, so far-dated signals got no extra
    # scrutiny despite being the clearest measured loser in this system.
    _dte = dte_bucket(expiry, snapshot_time)
    policy_adjusted = []
    for item in signals:
        policy = cohort_policy(conn, flow=item.flow, direction=item.direction,
                                score=item.score, dte=_dte)
        status = str(policy["status"])
        reason = item.reason
        tradable = item.tradable
        score = item.score
        if tradable and block_quarantined and status == "QUARANTINED":
            tradable = False
            reason = f"{reason},quarantined_negative_forward_edge"
        elif tradable and require_promising and status not in {"PAPER_PROMISING", "LIVE_EVIDENCE_READY"}:
            tradable = False
            reason = f"{reason},edge_still_validating"
        elif tradable and status in {"PAPER_PROMISING", "LIVE_EVIDENCE_READY"}:
            score = round(min(100.0, float(score) + promising_bonus), 2)
            reason = f"{reason},positive_forward_edge"
        policy_adjusted.append(
            replace(
                item,
                score=score,
                tradable=tradable,
                reason=reason,
                edge_policy=status,
                edge_outcomes=policy["outcomes"],
                edge_avg_r=policy["avg_net_r"],
                edge_profit_factor=policy["profit_factor"],
            )
        )
    signals = policy_adjusted
    if not edge_gate["allow_actionable"]:
        signals = [
            replace(item, tradable=False, reason=f"{item.reason},negative_forward_edge_circuit_breaker")
            if item.tradable else item
            for item in signals
        ]
    snapshot_ts = _snapshot_epoch(snapshot_time)
    for item in signals:
        row = item.to_dict()
        conn.execute(
            """
            INSERT OR REPLACE INTO option_strike_signals
            (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,
             direction,score,tradable,price,price_change_pct,oi,oi_change_pct,
             volume,volume_change_pct,spread_pct,reason,source,market_regime,
             market_bias,regime_aligned,score_rank,entry_price,stop_loss,target_1,target_2,
             lifecycle_status,status_updated_at,edge_policy,edge_outcomes,edge_avg_r,edge_profit_factor,
             strategy,side)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_ts, snapshot_time, underlying, expiry, row["strike"], row["option_type"],
                row["flow"], row["signal"], row["direction"], row["score"],
                1 if row["tradable"] else 0, row["price"], row["price_change_pct"],
                row["oi"], row["oi_change_pct"], row["volume"],
                row["volume_change_pct"], row["spread_pct"], row["reason"], source,
                row["market_regime"], row["market_bias"],
                -1 if row["regime_aligned"] is None else int(row["regime_aligned"]),
                row["score_rank"],
                row["entry_price"], row["stop_loss"], row["target_1"], row["target_2"],
                "OPEN" if row["tradable"] else "WATCH", snapshot_time,
                row["edge_policy"], row["edge_outcomes"], row["edge_avg_r"], row["edge_profit_factor"],
                "single_strike_flow", "BUY",
            ),
        )
    lifecycle_events = update_signal_lifecycle(conn, underlying=underlying, snapshot_time=snapshot_time)
    return {
        "written": len(signals),
        "tradable": sum(1 for item in signals if item.tradable),
        "signals": [item.to_dict() for item in signals],
        "lifecycle_events": lifecycle_events,
        "execution_edge_gate": edge_gate,
    }


def _execution_edge_gate(conn: sqlite3.Connection, min_outcomes: int = 20) -> Dict[str, Any]:
    """Block new actionable signals after a sufficiently large negative live-source sample."""
    ensure_multistrike_schema(conn)
    live_sources = ("angel", "angel_eod", "angel_fallback", "nse_live", "resilience_nse", "sensibull", "bse", "bse_oc")
    placeholders = ",".join("?" for _ in live_sources)
    row = conn.execute(
        f"""SELECT COUNT(*),AVG(net_r),AVG(CASE WHEN outcome_label=1 THEN 1.0 ELSE 0.0 END),AVG(net_pnl)
               FROM option_strike_signals
              WHERE tradable=1 AND source IN ({placeholders}) AND outcome_label IN (-1,0,1)""",
        live_sources,
    ).fetchone()
    n = int(row[0] or 0)
    avg_r = _f(row[1])
    win_rate = _f(row[2])
    avg_net = _f(row[3])
    negative = n >= int(min_outcomes) and (avg_r <= 0 or avg_net <= 0)
    return {
        "allow_actionable": not negative,
        "verified_outcomes": n,
        "avg_net_r": round(avg_r, 4),
        "win_rate": round(win_rate, 4),
        "avg_net_pnl": round(avg_net, 2),
        "reason": "negative_forward_edge" if negative else "insufficient_or_nonnegative_evidence",
    }


def update_signal_lifecycle(
    conn: sqlite3.Connection, *, underlying: str, snapshot_time: str
) -> List[Dict[str, Any]]:
    """Update prior actionable signals from the newest observed option premiums."""
    ensure_multistrike_schema(conn)
    conn.row_factory = sqlite3.Row
    prior = conn.execute(
        """SELECT rowid,* FROM option_strike_signals
             WHERE upper(underlying)=upper(?) AND snapshot_time<? AND tradable=1
               AND lifecycle_status IN ('OPEN','TARGET1_HIT')
             ORDER BY ts""", (underlying, snapshot_time),
    ).fetchall()
    events: List[Dict[str, Any]] = []
    for row in prior:
        latest = conn.execute(
            """SELECT price FROM option_strike_signals
                 WHERE upper(underlying)=upper(?) AND expiry=? AND strike=?
                   AND option_type=? AND snapshot_time=? AND price>0 LIMIT 1""",
            (underlying, row["expiry"], row["strike"], row["option_type"], snapshot_time),
        ).fetchone()
        if not latest:
            continue
        price = _f(latest["price"])
        old = str(row["lifecycle_status"] or "OPEN")
        status = ""
        if price >= _f(row["target_2"]):
            status = "TARGET2_HIT"
        elif old == "OPEN" and price >= _f(row["target_1"]):
            status = "TARGET1_HIT"
        elif price <= _f(row["stop_loss"]):
            status = "EXIT_AFTER_T1" if old == "TARGET1_HIT" else "STOP_LOSS_HIT"
        if not status:
            continue
        new_stop = _f(row["entry_price"]) if status == "TARGET1_HIT" else _f(row["stop_loss"])
        conn.execute(
            "UPDATE option_strike_signals SET lifecycle_status=?,status_updated_at=?,stop_loss=? WHERE rowid=?",
            (status, snapshot_time, new_stop, row["rowid"]),
        )
        events.append({
            "underlying": row["underlying"], "strike": row["strike"],
            "option_type": row["option_type"], "entry_price": row["entry_price"],
            "stop_loss": new_stop, "target_1": row["target_1"], "target_2": row["target_2"],
            "status": status, "current_price": price, "snapshot_time": snapshot_time,
            "signal_time": row["snapshot_time"],
        })
    return events


UNLABELABLE_OUTCOME = -3  # distinct from -99 (pending/retry) and -2 (journal "unfilled")


def label_multistrike_outcomes(
    *,
    db_path: str = "option_chain_snapshots.db",
    min_horizon_sec: int = 900,
    max_horizon_sec: int = 3600,
    extended_horizon_sec: int = 6 * 3600,
    stale_after_sec: int = 6 * 3600,
    limit: int = 20000,
) -> Dict[str, Any]:
    """Label every generated strike-flow signal from a later premium observation.

    Two-tier forward search: the intended short window first (min_horizon_sec
    to max_horizon_sec), then a same-day extended window if the recorder had
    a coverage gap over the short one (the recorder tracks a moving ATM±N
    strike band, so a specific strike can silently drop out of capture for
    hours — a rigid single window then loses that signal's evidence forever).
    Rows that still have no match AND are older than stale_after_sec are
    marked UNLABELABLE_OUTCOME instead of left pending: this both stops an
    unbounded nightly rescan of permanently-dead rows (2026-07-13: last
    night's run rescanned 3,342 pending rows and labelled ZERO of them) and
    makes the gap auditable instead of an invisible growing "pending" count.
    """
    from shadow_execution import simulate_option_round_trip

    labelled = 0
    labelled_extended = 0
    skipped = 0
    marked_unlabelable = 0
    verified = 0
    now_ts = time.time()
    live_sources = {"nse_live", "resilience_nse", "angel", "angel_eod", "angel_fallback", "sensibull", "bse", "bse_oc"}
    with sqlite3.connect(db_path) as conn:
        ensure_multistrike_schema(conn)
        conn.row_factory = sqlite3.Row
        pending = conn.execute(
            "SELECT rowid,* FROM option_strike_signals "
            "WHERE outcome_label=-99 AND price>0 ORDER BY ts LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        for row in pending:
            entry_ts = float(row["ts"])
            future = conn.execute(
                """
                SELECT price,ts,spread_pct,volume FROM option_strike_signals
                 WHERE upper(underlying)=upper(?) AND expiry=? AND strike=?
                   AND upper(option_type)=upper(?) AND price>0
                   AND ts>=? AND ts<=?
                 ORDER BY ts LIMIT 1
                """,
                (
                    row["underlying"], row["expiry"], row["strike"], row["option_type"],
                    entry_ts + max(60, int(min_horizon_sec)),
                    entry_ts + max(int(min_horizon_sec), int(max_horizon_sec)),
                ),
            ).fetchone()
            extended = False
            if not future and extended_horizon_sec > max_horizon_sec:
                future = conn.execute(
                    """
                    SELECT price,ts,spread_pct,volume FROM option_strike_signals
                     WHERE upper(underlying)=upper(?) AND expiry=? AND strike=?
                       AND upper(option_type)=upper(?) AND price>0
                       AND ts>?  AND ts<=?
                     ORDER BY ts LIMIT 1
                    """,
                    (
                        row["underlying"], row["expiry"], row["strike"], row["option_type"],
                        entry_ts + int(max_horizon_sec),
                        entry_ts + max(int(max_horizon_sec), int(extended_horizon_sec)),
                    ),
                ).fetchone()
                extended = bool(future)
            if not future:
                skipped += 1
                if now_ts - entry_ts > max(0, int(stale_after_sec)):
                    conn.execute(
                        "UPDATE option_strike_signals SET outcome_label=?,"
                        "execution_status=?,labelled_at=? WHERE rowid=?",
                        (UNLABELABLE_OUTCOME, "no_future_price_data",
                         time.strftime("%Y-%m-%dT%H:%M:%S%z"), row["rowid"]),
                    )
                    marked_unlabelable += 1
                continue
            execution = simulate_option_round_trip(
                row["price"], future["price"], side="BUY",
                entry_spread_pct=row["spread_pct"], exit_spread_pct=future["spread_pct"],
                observed_volume=row["volume"],
            )
            status = execution["execution_status"]
            if extended:
                labelled_extended += 1
                status = f"{status},extended_horizon"
            conn.execute(
                """
                UPDATE option_strike_signals
                   SET outcome_label=?,exit_price=?,gross_pnl=?,estimated_costs=?,
                       net_pnl=?,labelled_at=?,execution_status=?,fill_latency_sec=?,
                       fill_probability=?,capital_at_risk=?,net_r=?
                 WHERE rowid=?
                """,
                (
                    execution["label"], execution["fill_exit_price"], execution["gross_pnl"],
                    execution["estimated_costs"], execution["net_pnl"],
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"), status,
                    execution["fill_latency_sec"], execution["fill_probability"],
                    execution["capital_at_risk"], execution["net_r"], row["rowid"],
                ),
            )
            labelled += 1
            if str(row["source"] or "").lower() in live_sources:
                verified += 1
        conn.commit()
    return {
        "ok": True,
        "labelled": labelled,
        "labelled_extended_horizon": labelled_extended,
        "verified_labelled": verified,
        "skipped_no_future_price": skipped,
        "marked_unlabelable": marked_unlabelable,
        "pending_seen": len(pending),
    }


def build_multistrike_edge_model(
    *,
    db_path: str = "option_chain_snapshots.db",
    output_file: str = EDGE_MODEL_FILE,
    min_samples: int = 100,
    min_days: int = 15,
) -> Dict[str, Any]:
    """Learn bounded strike-flow weights from verified generated outcomes."""
    live_sources = ("nse_live", "resilience_nse", "angel", "angel_eod", "angel_fallback", "sensibull", "bse", "bse_oc")
    groups: Dict[str, Dict[str, float]] = {}
    with sqlite3.connect(db_path) as conn:
        ensure_multistrike_schema(conn)
        placeholders = ",".join("?" for _ in live_sources)
        rows = conn.execute(
            f"SELECT underlying,option_type,flow,score,outcome_label,net_pnl,net_r,substr(snapshot_time,1,10) "
            f"FROM option_strike_signals "
            f"WHERE outcome_label IN (-1,0,1) AND lower(COALESCE(source,'')) IN ({placeholders})",
            live_sources,
        ).fetchall()
    for underlying, option_type, flow, score, label, net_pnl, net_r, signal_day in rows:
        bucket = "high" if float(score or 0) >= 75 else "mid" if float(score or 0) >= 60 else "low"
        key = f"{str(underlying).upper()}:{str(option_type).upper()}:{str(flow).upper()}:{bucket}"
        stat = groups.setdefault(key, {"samples": 0, "wins": 0, "net_pnl": 0.0,
                                       "net_r": 0.0, "abs_r": 0.0, "days": set()})
        stat["samples"] += 1
        stat["wins"] += 1 if int(label) > 0 else 0
        stat["net_pnl"] += float(net_pnl or 0)
        stat["net_r"] += float(net_r or 0)
        stat["abs_r"] += abs(float(net_r or 0))
        if signal_day:
            stat["days"].add(str(signal_day))
    weights = {}
    for key, stat in groups.items():
        n = int(stat["samples"])
        day_count = len(stat["days"])
        if n < max(1, int(min_samples)) or day_count < max(1, int(min_days)):
            weights[key] = 1.0
            continue
        win_rate = stat["wins"] / n
        expectancy = stat["net_r"] / max(stat["abs_r"], 1e-9)
        confidence = min(1.0, n / max(2 * min_samples, 1))
        weights[key] = round(max(0.75, min(1.25, 1 + confidence * (0.25 * (win_rate - 0.5) * 2 + 0.15 * math.tanh(expectancy)))), 4)
    serializable_groups = {
        key: {**stat, "days": len(stat["days"])} for key, stat in groups.items()
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verified_outcomes": len(rows), "min_samples": int(min_samples),
        "min_days": int(min_days), "weights": weights, "groups": serializable_groups,
    }
    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return payload


def _learned_flow_multiplier(underlying: str, option_type: str, flow: str, score: float) -> float:
    try:
        payload = json.loads(open(EDGE_MODEL_FILE, encoding="utf-8").read())
        # Never consume a legacy/single-session weight file. Such a model is a
        # description of yesterday, not evidence of repeatable edge.
        if int(payload.get("min_samples", 0) or 0) < 100 or int(payload.get("min_days", 0) or 0) < 15:
            return 1.0
        bucket = "high" if score >= 75 else "mid" if score >= 60 else "low"
        key = f"{underlying.upper()}:{option_type.upper()}:{flow.upper()}:{bucket}"
        group = (payload.get("groups") or {}).get(key, {})
        if int(group.get("samples", 0) or 0) < 100 or int(group.get("days", 0) or 0) < 15:
            return 1.0
        return float((payload.get("weights") or {}).get(key, 1.0))
    except Exception:
        return 1.0


def latest_flow_scores(
    underlying: str,
    option_type: str,
    *,
    db_path: str = "option_chain_snapshots.db",
    max_age_sec: int = 600,
) -> Dict[float, Dict[str, Any]]:
    try:
        with sqlite3.connect(db_path) as conn:
            ensure_multistrike_schema(conn)
            rows = conn.execute(
                """
                SELECT strike,score,tradable,flow,signal,ts,reason,oi,volume,spread_pct,price
                  FROM option_strike_signals
                 WHERE upper(underlying)=upper(?) AND upper(option_type)=upper(?)
                   AND ts >= ?
                 ORDER BY ts DESC, score DESC
                """,
                (underlying, option_type, time.time() - max(1, int(max_age_sec))),
            ).fetchall()
    except Exception:
        return {}
    output: Dict[float, Dict[str, Any]] = {}
    for strike, score, tradable, flow, signal, ts, reason, oi, volume, spread_pct, price in rows:
        key = float(strike)
        if key not in output:
            raw_score = float(score or 0)
            multiplier = _learned_flow_multiplier(underlying, option_type, str(flow or ""), raw_score)
            output[key] = {
                "score": round(max(0.0, min(100.0, raw_score * multiplier)), 4),
                "raw_score": raw_score, "learned_multiplier": multiplier,
                "tradable": bool(tradable),
                "flow": flow, "signal": signal, "ts": float(ts or 0),
                "reason": reason,
                "oi": float(oi or 0), "volume": float(volume or 0),
                "spread_pct": None if spread_pct is None else float(spread_pct),
                "observed_price": float(price or 0),
            }
    return output


def backfill_multistrike_signals(
    *,
    db_path: str = "option_chain_snapshots.db",
    limit: int = 0,
    reset: bool = True,
) -> Dict[str, Any]:
    with sqlite3.connect(db_path) as conn:
        ensure_multistrike_schema(conn)
        if reset:
            conn.execute("DELETE FROM option_strike_signals")
        sql = (
            "SELECT snapshot_time,underlying,expiry,rows_json,source "
            "FROM option_chain_snapshots WHERE ok=1 ORDER BY ts"
        )
        rows = conn.execute(sql).fetchall()
        if limit > 0:
            rows = rows[-int(limit):]
        written = 0
        tradable = 0
        processed = 0
        for snapshot_time, underlying, expiry, rows_json, source in rows:
            try:
                chain_rows = json.loads(rows_json or "[]")
            except Exception:
                chain_rows = []
            if not isinstance(chain_rows, list) or not chain_rows:
                continue
            result = persist_multistrike_signals(
                conn=conn,
                snapshot_time=str(snapshot_time),
                underlying=str(underlying),
                expiry=str(expiry or ""),
                current_rows=chain_rows,
                source=str(source or "unknown"),
            )
            processed += 1
            written += int(result.get("written", 0) or 0)
            tradable += int(result.get("tradable", 0) or 0)
        conn.commit()
    return {
        "ok": True,
        "processed_snapshots": processed,
        "written": written,
        "tradable": tradable,
        "reset": bool(reset),
    }


if __name__ == "__main__":
    print(json.dumps(backfill_multistrike_signals(), indent=2))
