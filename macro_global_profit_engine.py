#!/usr/bin/env python3
"""
macro_global_profit_engine.py — global/macro sentiment CONTEXT for NIFTY option
selection.  REPORT-ONLY, additive, honest.

WHAT IT IS:
  A thin orchestrator that summarises the EXISTING macro signals into one view,
  maps macro → Indian sectors with DETERMINISTIC rules, and logs a daily snapshot
  to the `macro_global_sentiment` table so the effect can be VALIDATED later.

WHAT IT IS NOT (deliberately — CLAUDE.md rules 4 & 5):
  - Not a predictor and not a profit claim. The macro signal it surfaces
    (`cross_asset_mod`) currently measures as NOISE on the available data
    (nightly modifier_edge_analyzer), and the broader edge search is exhausted.
  - No fabricated numbers. Every value is a transparent transform of a REAL
    input or is omitted. Gap-probability and a `profit_quality_score` GATE are
    DEFERRED until backtest_macro_sentiment.py can calibrate them on accrued data.
  - Does not place trades or gate the live engine. It returns advisory CONTEXT
    only; wiring it as a live confidence-adjuster requires a passing backtest.

REUSES (does not duplicate):
  - cross_asset.get_cross_asset_data()        — multi-asset prices/changes
  - global_market_filter.get_global_filter()  — GIFT-NIFTY bias

The composite `global_score` is an UNVALIDATED heuristic: a weighted average of
real per-asset % changes, sign-mapped to NIFTY direction. It is transparent
context, not a calibrated probability.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

DB_PATH = "signal_log.db"   # reuse the main DB; table is namespaced

# Per-asset directional effect on NIFTY (sign) + heuristic weight.
# +1 = asset up is BULLISH for NIFTY; -1 = asset up is BEARISH for NIFTY.
# Weights are a transparent heuristic (NOT validated) and are renormalised over
# whatever assets are actually available at runtime.
_ASSET_EFFECT = {
    "GIFT":    (+1, 0.30),   # GIFT/SGX NIFTY — the overnight lead
    "SP500":   (+1, 0.18),   # US risk appetite
    "NIKKEI":  (+1, 0.12),   # Asia
    "BRENT":   (-1, 0.10),   # crude up = importer headwind
    "USDINR":  (-1, 0.08),   # INR weakness = FII headwind
    "DXY":     (-1, 0.06),   # dollar strength = EM headwind
    "GOLD":    (-1, 0.05),   # risk-off
    "USVIX":   (-1, 0.06),   # risk-off / fear
    "US10Y":   (-1, 0.05),   # yields up = valuation headwind
}


def _sub_score(change_pct: float) -> float:
    """Map a % change to a [-100, 100] sub-score (2% move ≈ full scale). Linear,
    transparent — NOT a probability."""
    return max(-100.0, min(100.0, float(change_pct) * 50.0))


def get_macro_context(force: bool = False) -> Dict[str, Any]:
    """Assemble macro context from the existing modules. Report-only."""
    assets: Dict[str, Dict[str, Any]] = {}
    try:
        from cross_asset import get_cross_asset_data
        assets = get_cross_asset_data(force=force) or {}
    except Exception as exc:
        assets = {"_error": str(exc)}

    # GIFT-NIFTY bias from the dedicated filter (more reliable than the
    # SGXNIFTY proxy inside cross_asset, which is mapped to a bank index).
    gift_chg = 0.0
    try:
        from global_market_filter import get_global_filter
        gb = get_global_filter().get_global_bias()
        gift_chg = float(gb.get("change_pct", 0.0)) * 100.0  # to %
    except Exception:
        gb = {}

    # Build the available-asset → change map (GIFT from the filter above).
    changes: Dict[str, float] = {"GIFT": gift_chg}
    for key in ("SP500", "NIKKEI", "BRENT", "USDINR", "DXY", "GOLD", "USVIX", "US10Y"):
        a = assets.get(key) if isinstance(assets, dict) else None
        if isinstance(a, dict) and a.get("change_pct") is not None:
            changes[key] = float(a.get("change_pct", 0.0))

    # Weighted composite over AVAILABLE assets (renormalise weights).
    num = den = 0.0
    contribs: List[tuple] = []
    for key, chg in changes.items():
        if key not in _ASSET_EFFECT:
            continue
        sign, w = _ASSET_EFFECT[key]
        c = sign * _sub_score(chg)
        num += w * c
        den += w
        contribs.append((abs(w * c), key, chg, sign))
    score = round(num / den, 1) if den > 0 else 0.0

    # Risk-off override → HIGH_RISK / NO_TRADE_ZONE
    us_vix = float((assets.get("USVIX") or {}).get("price", 0) or 0) if isinstance(assets, dict) else 0
    label = _bias_label(score, us_vix)

    # Top reasons = largest absolute contributors (real changes)
    contribs.sort(reverse=True)
    reasons = [
        f"{k} {('+' if chg >= 0 else '')}{chg:.2f}% "
        f"({'bullish' if (sign * chg) > 0 else 'bearish'} for NIFTY)"
        for _, k, chg, sign in contribs[:5]
    ]

    sectors = sector_impact_map(changes)

    return {
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
        "global_score":   score,            # -100..+100 heuristic composite (UNVALIDATED)
        "bias":           label,            # BULLISH/BEARISH/NEUTRAL/HIGH_RISK/NO_TRADE_ZONE
        "gift_change_pct": round(gift_chg, 3),
        "us_vix":         us_vix,
        "changes":        {k: round(v, 3) for k, v in changes.items()},
        "sectors":        sectors,
        "reasons":        reasons,
        "note":           "CONTEXT ONLY — unvalidated heuristic; not a prediction or profit claim.",
    }


def _bias_label(score: float, us_vix: float) -> str:
    if us_vix and us_vix >= 28:
        return "HIGH_RISK"            # global fear — buying-only system should be cautious
    if -20 <= score <= 20:
        return "NO_TRADE_ZONE"        # conflicting/weak macro — premium-decay risk
    if score > 35:
        return "BULLISH"
    if score < -35:
        return "BEARISH"
    return "NEUTRAL"


def sector_impact_map(changes: Dict[str, float]) -> Dict[str, Any]:
    """Deterministic macro → Indian-sector mapping (rules, not prediction)."""
    pos: List[str] = []
    neg: List[str] = []
    why: List[str] = []
    brent = changes.get("BRENT", 0.0)
    usdinr = changes.get("USDINR", 0.0)
    sp500 = changes.get("SP500", 0.0)
    if brent >= 1.5:
        neg += ["NIFTY", "OMC", "Paints", "Aviation"]; pos += ["Oil&Gas Upstream"]
        why.append(f"Brent +{brent:.1f}% (importer headwind)")
    elif brent <= -1.5:
        pos += ["OMC", "Aviation"]; why.append(f"Brent {brent:.1f}% (margin tailwind)")
    if usdinr >= 0.3:
        pos += ["IT", "Pharma"]; neg += ["Banks", "Importers"]
        why.append(f"USDINR +{usdinr:.2f}% (exporters benefit, importers hurt)")
    elif usdinr <= -0.3:
        neg += ["IT", "Pharma"]; why.append(f"USDINR {usdinr:.2f}% (exporter headwind)")
    if sp500 >= 0.5:
        pos += ["IT"]; why.append(f"US strong +{sp500:.2f}% (IT proxy)")
    elif sp500 <= -0.5:
        neg += ["IT"]; why.append(f"US weak {sp500:.2f}%")
    # de-dup preserving order
    dedup = lambda xs: list(dict.fromkeys(xs))
    return {
        "positive_sectors": dedup(pos),
        "negative_sectors": dedup(neg),
        "reason": "; ".join(why) if why else "no strong macro→sector signal",
    }


# ── Persistence (audit table) ────────────────────────────────────────────────

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_global_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT,
            gift_change_pct  REAL,
            global_score     REAL,
            bias             TEXT,
            us_vix           REAL,
            positive_sectors TEXT,
            negative_sectors TEXT,
            reasons_json     TEXT
        )
        """
    )
    conn.commit()


def log_sentiment(ctx: Optional[Dict[str, Any]] = None, db_path: str = DB_PATH) -> bool:
    """Append one snapshot row. Best-effort; returns success. Idempotent-ish:
    skips if a row already exists for today (one snapshot/day is enough for the
    daily macro→next-day study)."""
    try:
        ctx = ctx or get_macro_context()
        today = ctx["timestamp"][:10]
        conn = sqlite3.connect(db_path)
        try:
            _ensure_table(conn)
            exists = conn.execute(
                "SELECT 1 FROM macro_global_sentiment WHERE substr(timestamp,1,10)=?",
                (today,),
            ).fetchone()
            if exists:
                return False
            conn.execute(
                """INSERT INTO macro_global_sentiment
                   (timestamp, gift_change_pct, global_score, bias, us_vix,
                    positive_sectors, negative_sectors, reasons_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    ctx["timestamp"], ctx["gift_change_pct"], ctx["global_score"],
                    ctx["bias"], ctx["us_vix"],
                    json.dumps(ctx["sectors"]["positive_sectors"]),
                    json.dumps(ctx["sectors"]["negative_sectors"]),
                    json.dumps(ctx["reasons"]),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


# ── Telegram / CLI presentation ──────────────────────────────────────────────

def format_globalbias(ctx: Optional[Dict[str, Any]] = None) -> str:
    ctx = ctx or get_macro_context()
    s = ctx["sectors"]
    lines = [
        f"🌍 Global Macro Context: <b>{ctx['bias']}</b>",
        f"📊 Composite score: {ctx['global_score']:+.0f}/100  (heuristic, unvalidated)",
        f"📈 GIFT NIFTY: {ctx['gift_change_pct']:+.2f}%   US VIX: {ctx['us_vix'] or 'n/a'}",
        f"🏭 Sectors +: {', '.join(s['positive_sectors']) or '—'}",
        f"🏭 Sectors −: {', '.join(s['negative_sectors']) or '—'}",
        "",
        "<b>Top macro reasons:</b>",
    ] + [f"  • {r}" for r in ctx["reasons"]] + [
        "",
        "⚠️ CONTEXT ONLY — not a signal, prediction, or profit claim. The macro",
        "modifier currently measures as noise; do not trade on this alone.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Macro/global sentiment context (report-only)")
    ap.add_argument("--log", action="store_true", help="Persist a snapshot to macro_global_sentiment")
    ap.add_argument("--json", action="store_true", help="Print raw JSON context")
    args = ap.parse_args()
    ctx = get_macro_context()
    if args.json:
        print(json.dumps(ctx, indent=2))
    else:
        print(format_globalbias(ctx).replace("<b>", "").replace("</b>", ""))
    if args.log:
        print("\nlogged:" , log_sentiment(ctx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
