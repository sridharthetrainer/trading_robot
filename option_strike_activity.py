#!/usr/bin/env python3
"""CE/PE strike activity report for Telegram and audits."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from option_chain_intelligence import OptionChainIntelligence
from option_bot_views import score_gauge


DB_PATH = "option_chain_snapshots.db"
SUPPORTED_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}


@dataclass
class StrikeActivityReport:
    ok: bool
    text: str
    source: str = ""
    reason: str = ""


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return float(default)


def _fmt_num(value: Any) -> str:
    n = _safe_float(value)
    if abs(n) >= 10000000:
        return f"{n / 10000000:.2f}Cr"
    if abs(n) >= 100000:
        return f"{n / 100000:.2f}L"
    if abs(n) >= 1000:
        return f"{n / 1000:.1f}K"
    return f"{n:.0f}"


def _fmt_strike(value: Any) -> str:
    n = _safe_float(value)
    return f"{n:.0f}" if n else "-"


def _normalise_symbol(symbol: str) -> str:
    sym = str(symbol or "NIFTY").strip().upper()
    return sym if sym in SUPPORTED_UNDERLYINGS else "NIFTY"


def _latest_actionable_signals(underlying: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            from option_multistrike_signals import ensure_multistrike_schema
            ensure_multistrike_schema(conn)
            conn.row_factory = sqlite3.Row
            latest = conn.execute(
                "SELECT MAX(snapshot_time) FROM option_strike_signals WHERE upper(underlying)=?",
                (underlying.upper(),),
            ).fetchone()[0]
            if not latest:
                return []
            rows = conn.execute(
                """SELECT strike,option_type,signal,score,tradable,entry_price,stop_loss,target_1,target_2,reason
                     FROM option_strike_signals
                    WHERE upper(underlying)=? AND snapshot_time=? AND signal LIKE 'BUY_%'
                    ORDER BY tradable DESC,score DESC LIMIT 4""",
                (underlying.upper(), latest),
            ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _latest_snapshot_dataframe(
    *,
    underlying: str,
    db_path: str = DB_PATH,
) -> Tuple[pd.DataFrame, float, str]:
    db = Path(db_path)
    if not db.exists():
        return pd.DataFrame(), 0.0, ""
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(option_chain_snapshots)").fetchall()}
        ok_filter = "AND ok=1" if "ok" in cols else ""
        row = conn.execute(
            f"""
            SELECT snapshot_time, spot, rows_json
            FROM option_chain_snapshots
            WHERE upper(underlying)=? {ok_filter}
            ORDER BY ts DESC
            LIMIT 1
            """,
            (underlying.upper(),),
        ).fetchone()
    if not row:
        return pd.DataFrame(), 0.0, ""
    try:
        rows = json.loads(row["rows_json"] or "[]")
    except Exception:
        rows = []
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame(), 0.0, str(row["snapshot_time"] or "")
    return pd.DataFrame([r for r in rows if isinstance(r, dict)]), _safe_float(row["spot"]), str(row["snapshot_time"] or "")


def _fetch_live_dataframe(underlying: str) -> Tuple[pd.DataFrame, float, str]:
    try:
        from option_chain_fetcher import OptionChainFetcher

        result = OptionChainFetcher(underlying=underlying, strike_count_each_side=10).fetch_and_analyze()
        if result is None:
            return pd.DataFrame(), 0.0, ""
        df = getattr(result, "dataframe", pd.DataFrame())
        spot = _safe_float(getattr(result, "spot", 0.0))
        expiry = str(getattr(result, "expiry", "") or "")
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame(), spot, expiry
    except Exception:
        return pd.DataFrame(), 0.0, ""


def _activity_rows(items: List[Dict[str, Any]], side: str, limit: int) -> List[str]:
    label = "CE" if side == "CE" else "PE"
    rows = []
    for item in items[:limit]:
        rows.append(
            f"  {label} {_fmt_strike(item.get('strike'))}: "
            f"Vol {_fmt_num(item.get('volume'))} | "
            f"OI {_fmt_num(item.get('oi'))} | "
            f"Chg {_fmt_num(item.get('change_oi'))} | "
            f"Score {_safe_float(item.get('score')):.2f}"
        )
    return rows


def _quality_verdict(summary: Dict[str, Any], signal: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    sentiment = str((summary.get("activity_sentiment") or {}).get("sentiment", "NEUTRAL"))
    net_bias = str(summary.get("net_bias", "NEUTRAL"))
    pcr_oi = _safe_float(summary.get("pcr_oi"))
    pcr_chg = _safe_float(summary.get("pcr_change_oi"))
    pcr_vol = _safe_float(summary.get("pcr_volume"))
    bull = _safe_float(summary.get("bullish_score"))
    bear = _safe_float(summary.get("bearish_score"))
    reasons = []

    aligned = sentiment == net_bias and net_bias in {"BULLISH", "BEARISH"}
    if aligned:
        reasons.append(f"activity agrees with {net_bias}")
    else:
        reasons.append(f"activity={sentiment}, bias={net_bias}")

    if net_bias == "BULLISH":
        if pcr_oi >= 1.0:
            reasons.append("PCR OI supports put-side support")
        if pcr_chg >= 1.0:
            reasons.append("fresh put OI stronger than call OI")
        if pcr_vol >= 1.0:
            reasons.append("put volume stronger")
    elif net_bias == "BEARISH":
        if pcr_oi <= 1.0:
            reasons.append("PCR OI supports call-side pressure")
        if pcr_chg <= 1.0:
            reasons.append("fresh call OI stronger than put OI")
        if pcr_vol <= 1.0:
            reasons.append("call volume stronger")

    strength = abs(bull - bear)
    if signal:
        verdict = f"TRADEABLE WATCH: {signal.get('signal', net_bias)}"
    elif aligned and strength >= 0.8:
        verdict = f"HIGH WATCH: {net_bias}"
    elif aligned:
        verdict = f"MEDIUM WATCH: {net_bias}"
    else:
        verdict = "NO-TRADE / WAIT"
    return verdict, reasons[:4]


def build_strike_activity_report(
    *,
    underlying: str = "NIFTY",
    top_n: int = 5,
    prefer_live: bool = True,
    db_path: str = DB_PATH,
) -> StrikeActivityReport:
    symbol = _normalise_symbol(underlying)
    limit = max(2, min(8, int(top_n or 5)))
    source = "live"

    df = pd.DataFrame()
    spot = 0.0
    source_detail = ""
    if prefer_live:
        df, spot, source_detail = _fetch_live_dataframe(symbol)

    if df.empty:
        df, spot, source_detail = _latest_snapshot_dataframe(underlying=symbol, db_path=db_path)
        source = "snapshot"

    if df.empty:
        return StrikeActivityReport(
            ok=False,
            text=f"📊 <b>{symbol} strike activity unavailable</b>\nNo live chain or stored successful snapshot found.",
            source=source,
            reason="no_option_chain_data",
        )

    try:
        intel = OptionChainIntelligence(underlying=symbol, strike_window=10)
        summary_obj = intel.analyze(df, spot_price=spot)
        signal = intel.build_trade_signal(summary_obj)
        summary = {
            "spot": summary_obj.spot,
            "atm_strike": summary_obj.atm_strike,
            "pcr_oi": summary_obj.pcr_oi,
            "pcr_change_oi": summary_obj.pcr_change_oi,
            "pcr_volume": summary_obj.pcr_volume,
            "bullish_score": summary_obj.bullish_score,
            "bearish_score": summary_obj.bearish_score,
            "net_bias": summary_obj.net_bias,
            "regime": summary_obj.regime,
            "call_wall": summary_obj.call_wall,
            "put_wall": summary_obj.put_wall,
            "activity_sentiment": summary_obj.activity_sentiment,
        }
    except Exception as exc:
        return StrikeActivityReport(
            ok=False,
            text=f"📊 <b>{symbol} strike activity error</b>\n{str(exc)[:120]}",
            source=source,
            reason="analysis_error",
        )

    activity = summary_obj.activity_sentiment or {}
    top_calls = summary_obj.most_active_call_strikes or []
    top_puts = summary_obj.most_active_put_strikes or []
    verdict, reasons = _quality_verdict(summary, signal)

    source_line = "Live option chain"
    if source == "snapshot":
        source_line = f"Stored snapshot {source_detail or 'latest'}"
    elif source_detail:
        source_line = f"Live option chain exp {source_detail}"

    lines = [
        f"📊 <b>{symbol} CE/PE STRIKE FLOW</b>",
        f"  Spot {_fmt_num(summary_obj.spot)} | ATM {_fmt_strike(summary_obj.atm_strike)}",
        f"  Source: {source_line}",
        f"  Bias: {summary_obj.net_bias} | Activity: {activity.get('sentiment', 'NEUTRAL')} | Regime: {summary_obj.regime}",
        f"  Quality: <b>{verdict}</b>",
        f"  PCR OI {summary_obj.pcr_oi:.2f} | Chg {summary_obj.pcr_change_oi:.2f} | Vol {summary_obj.pcr_volume:.2f}",
        f"  Support {_fmt_strike(summary_obj.put_wall)} | Resistance {_fmt_strike(summary_obj.call_wall)}",
        "",
        f"🔥 <b>Top CE traded/OI strikes</b>",
    ]
    lines.extend(_activity_rows(top_calls, "CE", limit) or ["  No CE activity rows"])
    lines.append("")
    lines.append("🔥 <b>Top PE traded/OI strikes</b>")
    lines.extend(_activity_rows(top_puts, "PE", limit) or ["  No PE activity rows"])
    if reasons:
        lines.append("")
        lines.append("✅ <b>Quality notes</b>")
        lines.extend(f"  {r}" for r in reasons)
    if signal:
        lines.append("")
        lines.append(f"🎯 Signal: {signal.get('signal')} | Confidence {_safe_float(signal.get('confidence')):.2f}")
        if signal.get("reason"):
            lines.append(f"  {str(signal.get('reason'))[:140]}")
    actionable = _latest_actionable_signals(symbol, db_path)
    if actionable:
        lines.extend(["", "🎯 <b>Premium entry plans</b>"])
        # 2026-07-08: this list is the top-4 BUY_% rows by score regardless of
        # CE/PE side — on a day with zero tradable signals it can show a CE row
        # for one index next to a PE row for another with nothing tying either
        # back to the index's own stated bias above, reading as a directional
        # call it isn't. CE implies BULLISH, PE implies BEARISH (same mapping
        # option_multistrike_signals uses); tag each row's agreement with the
        # bias already printed in this card so a WATCH flow flag can't be
        # mistaken for a signal that agrees with the system's own view.
        net_bias = str(summary_obj.net_bias or "NEUTRAL").upper()
        for row in actionable:
            status = "ACTIONABLE" if row.get("tradable") else "WATCH"
            opt = row["option_type"]
            row_dir = "BULLISH" if opt == "CE" else "BEARISH"
            if net_bias in ("BULLISH", "BEARISH"):
                agree_tag = " [agrees with bias]" if row_dir == net_bias else " [AGAINST bias]"
            else:
                agree_tag = ""
            lines.append(
                f"  {status} {opt} {_fmt_strike(row['strike'])} | "
                f"Entry ₹{_safe_float(row['entry_price']):.2f} | SL ₹{_safe_float(row['stop_loss']):.2f} | "
                f"T1 ₹{_safe_float(row['target_1']):.2f} | T2 ₹{_safe_float(row['target_2']):.2f}"
                f"{agree_tag}\n"
                f"    {score_gauge(row.get('score'))}"
            )
        lines.append("  <i>Levels are premium plans; ACTIONABLE still requires liquidity/regime gates. "
                      "WATCH = flow flag only, not a trade call.</i>")
    lines.append(f"🕐 {datetime.now().strftime('%H:%M')}")
    return StrikeActivityReport(ok=True, text="\n".join(lines), source=source)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", nargs="?", default="NIFTY")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--snapshot", action="store_true")
    ns = parser.parse_args()
    print(build_strike_activity_report(
        underlying=ns.symbol,
        top_n=ns.top,
        prefer_live=not ns.snapshot,
    ).text)
