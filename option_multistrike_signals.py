"""Persist and rank per-strike CE/PE flow signals from option snapshots."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from option_quality import extract_contract_liquidity


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
    }
    for name, declaration in migrations.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE option_strike_signals ADD COLUMN {name} {declaration}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_strike_signal_lookup "
        "ON option_strike_signals(underlying, option_type, ts DESC, score DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_strike_signal_unique "
        "ON option_strike_signals(snapshot_time, underlying, strike, option_type)"
    )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except Exception:
        return float(default)


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
    top_n_per_side: int = 5,
) -> List[StrikeFlowSignal]:
    previous_rows = previous_rows or []
    current_data = {"chain": current_rows, "source": source}
    previous_data = {"chain": previous_rows}
    ranked: List[StrikeFlowSignal] = []
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

            liquidity_ok = oi >= min_oi and volume >= min_volume
            spread_ok = spread is not None and 0 <= float(spread) <= max_spread_pct
            history_ok = bool(previous_rows and _f(previous.get("last_price")) > 0)
            momentum = min(35.0, abs(price_change) * 4.0)
            oi_strength = min(35.0, abs(oi_change) * 2.0)
            volume_strength = min(15.0, max(0.0, volume_change) * 0.3)
            liquidity_score = 7.5 if liquidity_ok else 0.0
            spread_score = 7.5 if spread_ok else 0.0
            score = min(100.0, momentum + oi_strength + volume_strength + liquidity_score + spread_score)
            tradable = bool(
                history_ok and liquidity_ok and spread_ok
                and action == "BUY" and score >= min_score
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
            if score < min_score:
                reason_parts.append("score_below_minimum")
            signal = f"BUY_{option_type}" if action == "BUY" else "WATCH"
            ranked.append(StrikeFlowSignal(
                underlying=str(underlying).upper(), strike=strike,
                option_type=option_type, flow=flow, signal=signal,
                direction=direction, score=round(score, 2), tradable=tradable,
                price=round(price, 4), price_change_pct=round(price_change, 4),
                oi=round(oi, 4), oi_change_pct=round(oi_change, 4),
                volume=round(volume, 4), volume_change_pct=round(volume_change, 4),
                spread_pct=None if spread is None else round(float(spread), 6),
                reason=",".join(reason_parts), source=str(source or ""),
            ))

    output: List[StrikeFlowSignal] = []
    limit = max(1, int(top_n_per_side or 1))
    for option_type in ("CE", "PE"):
        side_rows = [row for row in ranked if row.option_type == option_type]
        side_rows.sort(key=lambda row: (row.tradable, row.score), reverse=True)
        output.extend(side_rows[:limit])
    return output


def persist_multistrike_signals(
    *,
    conn: sqlite3.Connection,
    snapshot_time: str,
    underlying: str,
    expiry: str,
    current_rows: List[Dict[str, Any]],
    source: str,
    top_n_per_side: int = 5,
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
    )
    snapshot_ts = _snapshot_epoch(snapshot_time)
    for item in signals:
        row = item.to_dict()
        conn.execute(
            """
            INSERT OR REPLACE INTO option_strike_signals
            (ts,snapshot_time,underlying,expiry,strike,option_type,flow,signal,
             direction,score,tradable,price,price_change_pct,oi,oi_change_pct,
             volume,volume_change_pct,spread_pct,reason,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_ts, snapshot_time, underlying, expiry, row["strike"], row["option_type"],
                row["flow"], row["signal"], row["direction"], row["score"],
                1 if row["tradable"] else 0, row["price"], row["price_change_pct"],
                row["oi"], row["oi_change_pct"], row["volume"],
                row["volume_change_pct"], row["spread_pct"], row["reason"], source,
            ),
        )
    return {
        "written": len(signals),
        "tradable": sum(1 for item in signals if item.tradable),
        "signals": [item.to_dict() for item in signals],
    }


def label_multistrike_outcomes(
    *,
    db_path: str = "option_chain_snapshots.db",
    min_horizon_sec: int = 900,
    max_horizon_sec: int = 3600,
    limit: int = 20000,
) -> Dict[str, Any]:
    """Label every generated strike-flow signal from a later premium observation."""
    from shadow_execution import simulate_option_round_trip

    labelled = 0
    skipped = 0
    verified = 0
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
            future = conn.execute(
                """
                SELECT price,ts FROM option_strike_signals
                 WHERE upper(underlying)=upper(?) AND expiry=? AND strike=?
                   AND upper(option_type)=upper(?) AND price>0
                   AND ts>=? AND ts<=?
                 ORDER BY ts LIMIT 1
                """,
                (
                    row["underlying"], row["expiry"], row["strike"], row["option_type"],
                    float(row["ts"]) + max(60, int(min_horizon_sec)),
                    float(row["ts"]) + max(int(min_horizon_sec), int(max_horizon_sec)),
                ),
            ).fetchone()
            if not future:
                skipped += 1
                continue
            execution = simulate_option_round_trip(row["price"], future["price"], side="BUY")
            conn.execute(
                """
                UPDATE option_strike_signals
                   SET outcome_label=?,exit_price=?,gross_pnl=?,estimated_costs=?,
                       net_pnl=?,labelled_at=?
                 WHERE rowid=?
                """,
                (
                    execution["label"], execution["fill_exit_price"], execution["gross_pnl"],
                    execution["estimated_costs"], execution["net_pnl"],
                    time.strftime("%Y-%m-%dT%H:%M:%S%z"), row["rowid"],
                ),
            )
            labelled += 1
            if str(row["source"] or "").lower() in live_sources:
                verified += 1
        conn.commit()
    return {
        "ok": True,
        "labelled": labelled,
        "verified_labelled": verified,
        "skipped_no_future_price": skipped,
        "pending_seen": len(pending),
    }


def build_multistrike_edge_model(
    *,
    db_path: str = "option_chain_snapshots.db",
    output_file: str = EDGE_MODEL_FILE,
    min_samples: int = 30,
) -> Dict[str, Any]:
    """Learn bounded strike-flow weights from verified generated outcomes."""
    live_sources = ("nse_live", "resilience_nse", "angel", "angel_eod", "angel_fallback", "sensibull", "bse", "bse_oc")
    groups: Dict[str, Dict[str, float]] = {}
    with sqlite3.connect(db_path) as conn:
        ensure_multistrike_schema(conn)
        placeholders = ",".join("?" for _ in live_sources)
        rows = conn.execute(
            f"SELECT option_type,flow,score,outcome_label,net_pnl FROM option_strike_signals "
            f"WHERE outcome_label IN (-1,0,1) AND lower(COALESCE(source,'')) IN ({placeholders})",
            live_sources,
        ).fetchall()
    for option_type, flow, score, label, net_pnl in rows:
        bucket = "high" if float(score or 0) >= 75 else "mid" if float(score or 0) >= 60 else "low"
        key = f"{str(option_type).upper()}:{str(flow).upper()}:{bucket}"
        stat = groups.setdefault(key, {"samples": 0, "wins": 0, "net_pnl": 0.0, "abs_pnl": 0.0})
        stat["samples"] += 1
        stat["wins"] += 1 if int(label) > 0 else 0
        stat["net_pnl"] += float(net_pnl or 0)
        stat["abs_pnl"] += abs(float(net_pnl or 0))
    weights = {}
    for key, stat in groups.items():
        n = int(stat["samples"])
        if n < max(1, int(min_samples)):
            weights[key] = 1.0
            continue
        win_rate = stat["wins"] / n
        expectancy = stat["net_pnl"] / max(stat["abs_pnl"], 1.0)
        confidence = min(1.0, n / max(2 * min_samples, 1))
        weights[key] = round(max(0.75, min(1.25, 1 + confidence * (0.25 * (win_rate - 0.5) * 2 + 0.15 * math.tanh(expectancy)))), 4)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verified_outcomes": len(rows), "min_samples": int(min_samples),
        "weights": weights, "groups": groups,
    }
    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return payload


def _learned_flow_multiplier(option_type: str, flow: str, score: float) -> float:
    try:
        payload = json.loads(open(EDGE_MODEL_FILE, encoding="utf-8").read())
        bucket = "high" if score >= 75 else "mid" if score >= 60 else "low"
        return float((payload.get("weights") or {}).get(f"{option_type.upper()}:{flow.upper()}:{bucket}", 1.0))
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
                SELECT strike,score,tradable,flow,signal,ts,reason
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
    for strike, score, tradable, flow, signal, ts, reason in rows:
        key = float(strike)
        if key not in output:
            raw_score = float(score or 0)
            multiplier = _learned_flow_multiplier(option_type, str(flow or ""), raw_score)
            output[key] = {
                "score": round(max(0.0, min(100.0, raw_score * multiplier)), 4),
                "raw_score": raw_score, "learned_multiplier": multiplier,
                "tradable": bool(tradable),
                "flow": flow, "signal": signal, "ts": float(ts or 0),
                "reason": reason,
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
