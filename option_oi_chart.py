#!/usr/bin/env python3
"""
option_oi_chart.py

Generate intraday option OI line charts from option_chain_snapshots.db.

Examples:
    python option_oi_chart.py --underlying NIFTY --date 2026-06-18
    python option_oi_chart.py --underlying NIFTY --date 2026-06-18 --strike 23500
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DB_PATH = "option_chain_snapshots.db"


@dataclass
class OIChartResult:
    ok: bool
    path: str = ""
    caption: str = ""
    reason: str = ""
    points: int = 0


def _safe_float(row: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            value = float(row.get(key, 0) or 0)
            return value
        except Exception:
            continue
    return 0.0


def _parse_time(value: str) -> datetime:
    raw = str(value or "")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass
    return datetime.fromtimestamp(0)


def _load_snapshot_rows(
    *,
    db_path: str,
    underlying: str,
    day: str,
) -> List[Dict[str, Any]]:
    db = Path(db_path)
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(option_chain_snapshots)").fetchall()}
        ok_filter = "AND ok=1" if "ok" in cols else ""
        rows = conn.execute(
            f"""
            SELECT snapshot_time, underlying, spot, atm_strike, rows_json, summary_json
            FROM option_chain_snapshots
            WHERE upper(underlying)=? AND substr(snapshot_time,1,10)=? {ok_filter}
            ORDER BY ts ASC
            """,
            (underlying.upper(), day),
        ).fetchall()
    return [dict(r) for r in rows]


def _series_from_snapshots(
    snapshots: Iterable[Dict[str, Any]],
    *,
    strike: Optional[float] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for snap in snapshots:
        try:
            rows = json.loads(snap.get("rows_json") or "[]")
        except Exception:
            rows = []
        if not isinstance(rows, list) or not rows:
            continue

        selected: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if strike is not None:
                row_strike = _safe_float(row, "strikePrice", "strike", "strike_price")
                if abs(row_strike - float(strike)) > 0.01:
                    continue
            selected.append(row)
        if not selected:
            continue

        ce_oi = sum(_safe_float(r, "CE_openInterest", "CE_OI", "ce_oi") for r in selected)
        pe_oi = sum(_safe_float(r, "PE_openInterest", "PE_OI", "pe_oi") for r in selected)
        ce_chg = sum(_safe_float(r, "CE_changeinOpenInterest", "CE_CHG_OI", "ce_change_oi") for r in selected)
        pe_chg = sum(_safe_float(r, "PE_changeinOpenInterest", "PE_CHG_OI", "pe_change_oi") for r in selected)
        out.append({
            "time": _parse_time(str(snap.get("snapshot_time") or "")),
            "label": _parse_time(str(snap.get("snapshot_time") or "")).strftime("%H:%M"),
            "spot": float(snap.get("spot") or 0),
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "ce_change_oi": ce_chg,
            "pe_change_oi": pe_chg,
        })
    return out


def _snapshot_rows(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(snap.get("rows_json") or "[]")
    except Exception:
        rows = []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _row_strike(row: Dict[str, Any]) -> float:
    return _safe_float(row, "strikePrice", "strike", "strike_price")


def _activity_score(row: Dict[str, Any], side: str, maxes: Dict[str, float]) -> float:
    prefix = "CE" if side == "CE" else "PE"
    oi = _safe_float(row, f"{prefix}_openInterest", f"{prefix}_OI", f"{prefix.lower()}_oi")
    chg = max(0.0, _safe_float(row, f"{prefix}_changeinOpenInterest", f"{prefix}_CHG_OI", f"{prefix.lower()}_change_oi"))
    vol = _safe_float(row, f"{prefix}_totalTradedVolume", f"{prefix}_VOLUME", f"{prefix.lower()}_volume")
    return (
        0.45 * oi / max(maxes["oi"], 1.0)
        + 0.35 * chg / max(maxes["chg"], 1.0)
        + 0.20 * vol / max(maxes["vol"], 1.0)
    )


def _latest_chain_context(
    snapshots: List[Dict[str, Any]],
    *,
    top_n: int = 5,
) -> Dict[str, Any]:
    latest_rows: List[Dict[str, Any]] = []
    latest = snapshots[-1] if snapshots else {}
    for snap in reversed(snapshots):
        latest_rows = _snapshot_rows(snap)
        if latest_rows:
            latest = snap
            break
    if not latest_rows:
        return {"top_strikes": [], "support": None, "resistance": None, "spot": 0.0}

    maxes = {
        "oi": max(
            max(_safe_float(r, "CE_openInterest", "CE_OI") for r in latest_rows),
            max(_safe_float(r, "PE_openInterest", "PE_OI") for r in latest_rows),
            1.0,
        ),
        "chg": max(
            max(max(0.0, _safe_float(r, "CE_changeinOpenInterest", "CE_CHG_OI")) for r in latest_rows),
            max(max(0.0, _safe_float(r, "PE_changeinOpenInterest", "PE_CHG_OI")) for r in latest_rows),
            1.0,
        ),
        "vol": max(
            max(_safe_float(r, "CE_totalTradedVolume", "CE_VOLUME") for r in latest_rows),
            max(_safe_float(r, "PE_totalTradedVolume", "PE_VOLUME") for r in latest_rows),
            1.0,
        ),
    }

    scored: Dict[float, Dict[str, Any]] = {}
    for row in latest_rows:
        strike = _row_strike(row)
        if strike <= 0:
            continue
        ce_score = _activity_score(row, "CE", maxes)
        pe_score = _activity_score(row, "PE", maxes)
        scored[strike] = {
            "strike": strike,
            "score": round(max(ce_score, pe_score), 4),
            "ce_score": round(ce_score, 4),
            "pe_score": round(pe_score, 4),
            "ce_oi": _safe_float(row, "CE_openInterest", "CE_OI"),
            "pe_oi": _safe_float(row, "PE_openInterest", "PE_OI"),
            "ce_change_oi": _safe_float(row, "CE_changeinOpenInterest", "CE_CHG_OI"),
            "pe_change_oi": _safe_float(row, "PE_changeinOpenInterest", "PE_CHG_OI"),
        }

    top_strikes = sorted(scored.values(), key=lambda x: x["score"], reverse=True)[:max(1, int(top_n))]
    support_row = max(latest_rows, key=lambda r: _safe_float(r, "PE_openInterest", "PE_OI"))
    resistance_row = max(latest_rows, key=lambda r: _safe_float(r, "CE_openInterest", "CE_OI"))
    return {
        "top_strikes": top_strikes,
        "support": _row_strike(support_row),
        "resistance": _row_strike(resistance_row),
        "spot": float(latest.get("spot") or 0),
    }


def _multi_strike_series(
    snapshots: Iterable[Dict[str, Any]],
    strikes: List[float],
) -> Dict[float, List[Dict[str, Any]]]:
    return {float(strike): _series_from_snapshots(snapshots, strike=float(strike)) for strike in strikes}


def generate_option_oi_chart(
    *,
    underlying: str = "NIFTY",
    day: Optional[str] = None,
    strike: Optional[float] = None,
    db_path: str = DB_PATH,
    output_dir: Optional[str] = None,
    compare_top: int = 0,
    strikes: Optional[List[float]] = None,
) -> OIChartResult:
    underlying = str(underlying or "NIFTY").upper()
    day = str(day or datetime.now().strftime("%Y-%m-%d"))
    snapshots = _load_snapshot_rows(db_path=db_path, underlying=underlying, day=day)
    if not snapshots:
        return OIChartResult(False, reason=f"no_snapshots_for_{underlying}_{day}")

    explicit_strikes = [float(s) for s in (strikes or []) if float(s or 0) > 0]
    if strike is not None:
        explicit_strikes.append(float(strike))
    compare_mode = bool(compare_top or len(explicit_strikes) > 1)
    context = _latest_chain_context(snapshots, top_n=max(compare_top, len(explicit_strikes), 5))
    if compare_top and not explicit_strikes:
        explicit_strikes = [float(item["strike"]) for item in context.get("top_strikes", [])[:compare_top]]

    if compare_mode:
        multi = _multi_strike_series(snapshots, explicit_strikes)
        multi = {k: v for k, v in multi.items() if len(v) >= 2}
        if not multi:
            return OIChartResult(False, reason=f"not_enough_multi_strike_points_for_{underlying}_{day}")
        return _render_multi_strike_chart(
            underlying=underlying,
            day=day,
            multi=multi,
            context=context,
            output_dir=output_dir,
        )

    series = _series_from_snapshots(snapshots, strike=strike)
    if len(series) < 2:
        suffix = f" strike {int(strike)}" if strike is not None else ""
        return OIChartResult(False, reason=f"not_enough_oi_points_for_{underlying}_{day}{suffix}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [p["label"] for p in series]
    ce_oi = [p["ce_oi"] for p in series]
    pe_oi = [p["pe_oi"] for p in series]
    ce_chg = [p["ce_change_oi"] for p in series]
    pe_chg = [p["pe_change_oi"] for p in series]
    spot = [p["spot"] for p in series]

    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, dpi=130)
    fig.patch.set_facecolor("#0d1117")
    title_strike = f" | Strike {int(strike)}" if strike is not None else " | All Snapshot Strikes"
    fig.suptitle(f"{underlying} OI Trend | {day}{title_strike}", color="white", fontsize=14, fontweight="bold")

    for ax in axes:
        ax.set_facecolor("#0d1117")
        ax.grid(True, color="#30363d", alpha=0.55)
        ax.tick_params(colors="#c9d1d9", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    axes[0].plot(labels, ce_oi, color="#ff6b6b", linewidth=2.2, label="CE OI")
    axes[0].plot(labels, pe_oi, color="#4dabf7", linewidth=2.2, label="PE OI")
    axes[0].set_ylabel("Open Interest", color="#c9d1d9")
    axes[0].legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

    axes[1].plot(labels, ce_chg, color="#ffa8a8", linewidth=2.0, label="CE Change OI")
    axes[1].plot(labels, pe_chg, color="#91caff", linewidth=2.0, label="PE Change OI")
    axes[1].axhline(0, color="#8b949e", linewidth=0.8)
    axes[1].set_ylabel("Change OI", color="#c9d1d9")
    axes[1].legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

    axes[2].plot(labels, spot, color="#ffd43b", linewidth=1.8, label="Spot")
    axes[2].set_ylabel("Spot", color="#c9d1d9")
    axes[2].set_xlabel("Time", color="#c9d1d9")
    axes[2].legend(loc="upper left", facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    step = max(1, len(labels) // 8)
    axes[2].set_xticks(range(0, len(labels), step))
    axes[2].set_xticklabels(labels[::step], rotation=35, ha="right")

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    out_dir = Path(output_dir or tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    strike_part = f"_{int(strike)}" if strike is not None else ""
    out_path = out_dir / f"oi_chart_{underlying}_{day}{strike_part}.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    last = series[-1]
    sentiment = "BULLISH" if last["pe_change_oi"] > last["ce_change_oi"] else "BEARISH" if last["ce_change_oi"] > last["pe_change_oi"] else "NEUTRAL"
    caption = (
        f"{underlying} OI chart {day}{title_strike}\n"
        f"Last CE OI: {last['ce_oi']:,.0f} | PE OI: {last['pe_oi']:,.0f}\n"
        f"Last CE Chg: {last['ce_change_oi']:,.0f} | PE Chg: {last['pe_change_oi']:,.0f}\n"
        f"Change-OI sentiment: {sentiment}"
    )
    return OIChartResult(True, path=str(out_path), caption=caption, points=len(series))


def _render_multi_strike_chart(
    *,
    underlying: str,
    day: str,
    multi: Dict[float, List[Dict[str, Any]]],
    context: Dict[str, Any],
    output_dir: Optional[str],
) -> OIChartResult:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    strikes = sorted(multi.keys())
    first_series = next(iter(multi.values()))
    labels = [p["label"] for p in first_series]
    colors = ["#4dabf7", "#ff6b6b", "#51cf66", "#ffd43b", "#b197fc", "#20c997", "#ff922b"]

    fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=False, dpi=130)
    fig.patch.set_facecolor("#0d1117")
    fig.suptitle(f"{underlying} Multi-Strike OI Comparison | {day}", color="white", fontsize=14, fontweight="bold")
    for ax in axes:
        ax.set_facecolor("#0d1117")
        ax.grid(True, color="#30363d", alpha=0.55)
        ax.tick_params(colors="#c9d1d9", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    for idx, strike_val in enumerate(strikes):
        data = multi[strike_val]
        color = colors[idx % len(colors)]
        axes[0].plot([p["label"] for p in data], [p["ce_oi"] for p in data],
                     color=color, linewidth=1.8, linestyle="-", label=f"{int(strike_val)} CE")
        axes[0].plot([p["label"] for p in data], [p["pe_oi"] for p in data],
                     color=color, linewidth=1.8, linestyle="--", label=f"{int(strike_val)} PE")
        axes[1].plot([p["label"] for p in data], [p["ce_change_oi"] for p in data],
                     color=color, linewidth=1.6, linestyle="-", label=f"{int(strike_val)} CE chg")
        axes[1].plot([p["label"] for p in data], [p["pe_change_oi"] for p in data],
                     color=color, linewidth=1.6, linestyle="--", label=f"{int(strike_val)} PE chg")

    axes[0].set_ylabel("OI", color="#c9d1d9")
    axes[1].set_ylabel("Change OI", color="#c9d1d9")
    axes[1].axhline(0, color="#8b949e", linewidth=0.8)
    for ax in axes[:2]:
        ax.legend(loc="upper left", ncol=2, fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")

    top = context.get("top_strikes", [])
    support = context.get("support")
    resistance = context.get("resistance")
    spot = context.get("spot") or 0
    y_labels = [str(int(item["strike"])) for item in top]
    ce_scores = [float(item.get("ce_score", 0)) for item in top]
    pe_scores = [float(item.get("pe_score", 0)) for item in top]
    y_pos = list(range(len(top)))
    axes[2].barh([y - 0.18 for y in y_pos], ce_scores, height=0.32, color="#ff6b6b", label="CE activity")
    axes[2].barh([y + 0.18 for y in y_pos], pe_scores, height=0.32, color="#4dabf7", label="PE activity")
    axes[2].set_yticks(y_pos)
    axes[2].set_yticklabels(y_labels, color="#c9d1d9")
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Latest activity score", color="#c9d1d9")
    axes[2].legend(loc="lower right", facecolor="#161b22", edgecolor="#30363d", labelcolor="#c9d1d9")
    sr_text = f"Support: {int(support) if support else 'NA'} | Resistance: {int(resistance) if resistance else 'NA'} | Spot: {spot:,.0f}"
    axes[2].text(0.01, 1.04, sr_text, transform=axes[2].transAxes, color="#ffd43b", fontsize=10, fontweight="bold")

    step = max(1, len(labels) // 8)
    axes[1].set_xticks(range(0, len(labels), step))
    axes[1].set_xticklabels(labels[::step], rotation=35, ha="right")
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])

    out_dir = Path(output_dir or tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"oi_multistrike_{underlying}_{day}.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    top_txt = ", ".join(str(int(s)) for s in strikes[:6])
    caption = (
        f"{underlying} multi-strike OI comparison {day}\n"
        f"Compared: {top_txt}\n"
        f"Key support: {int(support) if support else 'NA'} | Key resistance: {int(resistance) if resistance else 'NA'}\n"
        f"Top trading strikes shown by latest OI/change/volume activity"
    )
    return OIChartResult(True, path=str(out_path), caption=caption, points=min(len(v) for v in multi.values()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--underlying", default="NIFTY")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--strike", type=float, default=None)
    parser.add_argument("--strikes", default="", help="comma-separated strikes to compare")
    parser.add_argument("--top", type=int, default=0, help="compare top N active strikes from latest snapshot")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    result = generate_option_oi_chart(
        underlying=args.underlying,
        day=args.date,
        strike=args.strike,
        strikes=[float(x) for x in args.strikes.split(",") if x.strip()] if args.strikes else None,
        compare_top=args.top,
        db_path=args.db,
        output_dir=args.output_dir,
    )
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
