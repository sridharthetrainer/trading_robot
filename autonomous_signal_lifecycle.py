"""Track generated autonomous signals against cached one-minute bars."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _ensure(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signal_log)")}
    for name, decl in {
        "lifecycle_status": "TEXT DEFAULT 'OPEN'",
        "lifecycle_updated_at": "TEXT DEFAULT ''",
        "lifecycle_price": "REAL DEFAULT 0",
    }.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE signal_log ADD COLUMN {name} {decl}")
    conn.execute("UPDATE signal_log SET lifecycle_status='OPEN' WHERE COALESCE(lifecycle_status,'')='' AND tb_label=-99")


def active_generated_symbols(
    session_date: str | None = None, signal_db: str = "signal_log.db"
) -> set[str]:
    """Return qualified symbols that already have an unresolved signal."""
    day = session_date or date.today().isoformat()
    if not Path(signal_db).exists():
        return set()
    try:
        conn = sqlite3.connect(signal_db)
        _ensure(conn)
        rows = conn.execute(
            """SELECT DISTINCT upper(symbol) FROM signal_log
                WHERE signal_date=? AND COALESCE(rejection_reason,'')=''
                  AND COALESCE(lifecycle_status,'OPEN')='OPEN'""",
            (day,),
        ).fetchall()
        conn.close()
        return {str(row[0]) for row in rows if row and row[0]}
    except sqlite3.Error:
        return set()


def update_generated_signal_lifecycle(
    *, signal_db: str = "signal_log.db", candle_db: str = "candle_cache.db",
    session_date: str | None = None,
    price_frames: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    day = session_date or date.today().isoformat()
    if not Path(signal_db).exists() or (not price_frames and not Path(candle_db).exists()):
        return {"ok": False, "reason": "database_missing", "events": []}
    signals = sqlite3.connect(signal_db); signals.row_factory = sqlite3.Row
    candles = None
    if not price_frames:
        candles = sqlite3.connect(candle_db); candles.row_factory = sqlite3.Row
    _ensure(signals)
    rows = signals.execute(
        """SELECT id,signal_date,signal_time,symbol,side,strategy,score,entry_price,
                  stop_loss,target,lifecycle_status
             FROM signal_log
            WHERE signal_date=? AND tb_label=-99
              AND (executed=1 OR COALESCE(rejection_reason,'')='')
              AND lifecycle_status IN ('OPEN','TARGET_HIT','STOP_LOSS_HIT')
            ORDER BY id""", (day,),
    ).fetchall()
    symbols = {str(row["symbol"] or "").upper() for row in rows}
    bars_by_symbol: Dict[str, List[Any]] = {symbol: [] for symbol in symbols}
    if price_frames:
        for symbol, entry in price_frames.items():
            frame = entry.get("df") if isinstance(entry, dict) else entry
            if frame is None or not hasattr(frame, "iterrows"):
                continue
            cols = {str(col).lower(): col for col in frame.columns}
            if not {"high", "low", "close"} <= set(cols):
                continue
            for index, bar in frame.iterrows():
                if str(index)[:10] != day:
                    continue
                bars_by_symbol.setdefault(str(symbol).upper(), []).append({
                    "timestamp": str(index), "high": bar[cols["high"]],
                    "low": bar[cols["low"]], "close": bar[cols["close"]],
                })
    elif symbols and candles is not None:
        placeholders = ",".join("?" for _ in symbols)
        all_bars = candles.execute(
            f"""SELECT symbol,timestamp,high,low,close FROM candles
                  WHERE interval='1m' AND substr(timestamp,1,10)=?
                    AND upper(symbol) IN ({placeholders}) ORDER BY symbol,timestamp""",
            (day, *sorted(symbols)),
        ).fetchall()
        for bar in all_bars:
            bars_by_symbol.setdefault(str(bar["symbol"] or "").upper(), []).append(bar)
    events: List[Dict[str, Any]] = []
    for row in rows:
        if row["lifecycle_status"] in ("TARGET_HIT", "STOP_LOSS_HIT"):
            continue
        start = _dt(f"{row['signal_date']}T{row['signal_time']}+05:30")
        bars = bars_by_symbol.get(str(row["symbol"] or "").upper(), [])
        status = ""
        current = 0.0
        for bar in bars:
            when = _dt(bar["timestamp"])
            if start and when:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=start.tzinfo)
                if when < start:
                    continue
            high, low, current = float(bar["high"] or 0), float(bar["low"] or 0), float(bar["close"] or 0)
            side = str(row["side"] or "").upper()
            # Conservative ordering when both barriers touch in one cached bar.
            if side == "BUY":
                status = "STOP_LOSS_HIT" if low <= float(row["stop_loss"] or 0) else "TARGET_HIT" if high >= float(row["target"] or 0) else ""
            else:
                status = "STOP_LOSS_HIT" if high >= float(row["stop_loss"] or 0) else "TARGET_HIT" if low <= float(row["target"] or 0) else ""
            if status:
                break
        if current > 0:
            signals.execute(
                "UPDATE signal_log SET lifecycle_price=?,lifecycle_updated_at=? WHERE id=?",
                (current, datetime.now().astimezone().isoformat(timespec="seconds"), row["id"]),
            )
        if status:
            signals.execute(
                "UPDATE signal_log SET lifecycle_status=?,lifecycle_price=?,lifecycle_updated_at=? WHERE id=?",
                (status, current, datetime.now().astimezone().isoformat(timespec="seconds"), row["id"]),
            )
            events.append({**dict(row), "status": status, "current_price": current})
    signals.commit(); signals.close()
    if candles is not None:
        candles.close()
    return {"ok": True, "day": day, "tracked": len(rows), "events": events}


def send_lifecycle_digest(result: Dict[str, Any]) -> bool:
    events = result.get("events", []) or []
    if not events:
        return False
    from alerts import AlertManager
    lines = [f"🔔 <b>AUTONOMOUS SIGNAL UPDATES</b> ({len(events)})"]
    for event in events[:10]:
        icon = "✅" if event["status"] == "TARGET_HIT" else "🛑"
        lines.append(
            f"{icon} {event['symbol']} {event['side']} · {event['status'].replace('_',' ')}\n"
            f"   Entry ₹{float(event['entry_price']):.2f} | SL ₹{float(event['stop_loss']):.2f} | "
            f"Target ₹{float(event['target']):.2f} | Now ₹{float(event['current_price']):.2f}"
        )
    key = "autolife_" + "_".join(str(event["id"]) + event["status"] for event in events[:10])
    return bool(AlertManager().send("\n".join(lines), dedup_key=key, dedup_cooldown_override=172_800))
