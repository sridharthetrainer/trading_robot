"""
option_decision_journal.py

Append-only JSONL journal for option-bot decisions.

This is separate from signal_log.db because it captures option-specific
pre-signal decisions: chain health, expected move, strike quality, selected
candidate, and block reasons.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception as exc:
    logger.debug("option journal .env load failed: %s", exc)


DEFAULT_JOURNAL_FILE = "option_decision_journal.jsonl"
MAX_FIELD_CHARS = 2000
AUTO_SHADOW_CANDIDATES = os.getenv("OPTION_JOURNAL_AUTO_SHADOWS", "true").lower() == "true"
AUTO_SHADOW_WINGS = max(1, int(os.getenv("OPTION_JOURNAL_SHADOW_WINGS", "4")))
_LIVE_DATA_SOURCES = {
    "nse_live", "resilience_nse", "angel", "angel_fallback",
    "sensibull", "bse", "bse_oc",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _underlying_root(symbol: str) -> str:
    m = re.match(r"^([A-Z]+)", str(symbol or "").upper())
    return m.group(1) if m else ""


def _strike_step(root: str, strike: float) -> int:
    root = str(root or "").upper()
    if root in {"BANKNIFTY", "SENSEX", "BANKEX"}:
        return 100
    if root in {"NIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}:
        return 50
    if strike >= 10000:
        return 100
    if strike >= 1000:
        return 50
    return 10


def _build_symbol_like(selected_symbol: str, strike: int, option_type: str) -> str:
    raw = str(selected_symbol or "").upper()
    if not raw:
        return ""
    return re.sub(r"\d{4,6}(CE|PE)$", f"{int(strike)}{option_type}", raw)


def synthesize_shadow_candidates(
    selected: Optional[Dict[str, Any]],
    *,
    spot: float = 0.0,
    side: str = "",
    wings: int = AUTO_SHADOW_WINGS,
) -> list:
    """
    Build a small comparison ladder when live option-chain candidates are absent.

    The candidates are explicitly marked synthetic; they are a coverage fallback
    for journaling and EOD comparison, not a replacement for real chain rows.
    """
    if not isinstance(selected, dict):
        return []
    strike = _safe_int(selected.get("strike"), 0)
    option_type = str(selected.get("option_type") or "").upper()
    if strike <= 0 or option_type not in {"CE", "PE"}:
        return []
    symbol = str(selected.get("symbol") or "")
    root = _underlying_root(symbol) or str(selected.get("underlying") or "")
    step = _strike_step(root, strike)
    premium = _safe_float(selected.get("premium") or selected.get("entry_price"), 0.0)
    out = []
    for offset in range(-int(wings), int(wings) + 1):
        cand_strike = int(strike + offset * step)
        if cand_strike <= 0:
            continue
        out.append({
            "symbol": _build_symbol_like(symbol, cand_strike, option_type),
            "strike": cand_strike,
            "option_type": option_type,
            "premium": premium,
            "spot": _safe_float(selected.get("spot"), _safe_float(spot, 0.0)),
            "dte": _safe_int(selected.get("dte"), 0),
            "expiry": selected.get("expiry") or selected.get("option_expiry") or "",
            "strike_type": "SELECTED" if offset == 0 else f"{offset:+d}STEP",
            "shadow": offset != 0,
            "synthetic_shadow": True,
            "entry_source": "selected_premium_fallback",
            "side": str(side or ""),
        })
    return out


def ensure_option_journal(path: Optional[str] = None) -> str:
    out_path = Path(path or os.getenv("OPTION_DECISION_JOURNAL", DEFAULT_JOURNAL_FILE))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.touch(exist_ok=True)
    return str(out_path)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            return value[:MAX_FIELD_CHARS] + "...[truncated]"
        return value
    try:
        return float(value)
    except Exception:
        return str(value)[:MAX_FIELD_CHARS]


# ── Separate Telegram channel for SELECTED option trades ──────────────────────
# The autonomous NIFTY option logic already journals every decision here; this
# additionally pushes a trade card to a DEDICATED option Telegram channel when a
# trade is actually selected (decision starts with "selected"). Blocked/rejected
# decisions are NOT alerted. No-op until OPTION_BOT_TOKEN + OPTION_CHAT_ID are set
# (create the bot via @BotFather, get the chat id) — it never falls back to the
# main channel, so option alerts stay separate. Best-effort; never raises.
_OPTION_ALERTS = None
_OPTION_ALERTS_READY = False


def _option_alerts():
    global _OPTION_ALERTS, _OPTION_ALERTS_READY
    if _OPTION_ALERTS_READY:
        return _OPTION_ALERTS
    _OPTION_ALERTS_READY = True
    token = (os.getenv("OPTION_BOT_TOKEN") or "").strip()
    chat = (os.getenv("OPTION_CHAT_ID") or "").strip()
    if not token or not chat:
        logger.info("Option Telegram channel not configured "
                    "(set OPTION_BOT_TOKEN + OPTION_CHAT_ID) — selection alerts off")
        return None
    try:
        from alerts import AlertManager
        _OPTION_ALERTS = AlertManager(bot_token=token, chat_id=chat, name="option")
    except Exception as exc:
        logger.debug("option alerts init: %s", exc)
        _OPTION_ALERTS = None
    return _OPTION_ALERTS


def _fmt_oi(value: Any) -> str:
    v = _safe_float(value)
    if v >= 1e7:  return f"{v/1e7:.1f}Cr"
    if v >= 1e5:  return f"{v/1e5:.1f}L"
    if v >= 1e3:  return f"{v/1e3:.0f}K"
    return f"{v:.0f}" if v else "—"


def _format_option_card(p: Dict[str, Any]) -> str:
    """Rich option-selection card for the separate option Telegram channel.
    Uses only fields actually present in the decision payload; each line is
    omitted gracefully when its data is missing."""
    sel = p.get("selected") or {}
    q = p.get("quality") or {}
    if isinstance(q.get("chain_quality"), dict):   # tolerate nested shape
        q = q["chain_quality"]

    sym = p.get("symbol", "")
    strike = sel.get("strike") or ""
    otype = str(sel.get("option_type") or "").upper()
    leg = str(sel.get("symbol") or "").strip()
    if not leg and strike and otype:
        leg = f"{sym} {int(_safe_float(strike))}{otype}".strip()
    stype = str(sel.get("strike_type") or "").upper()          # ATM / OTM / ITM
    side = str(p.get("side") or "").upper()
    arrow = "📈" if side == "BUY" else ("📉" if side == "SELL" else "•")
    style = str(sel.get("style") or "").strip()

    hdr = f"🎯 <b>OPTION SELECTED — {sym}</b>"
    if style:
        hdr += f"  <i>{style}</i>"
    lines = [hdr, "━━━━━━━━━━━━━━━━━━"]

    legline = f"{arrow} <b>{side} {leg or sym}</b>"
    if stype:
        legline += f"  ({stype})"
    lines.append(legline)

    exp_bits = []
    if str(sel.get("expiry") or "").strip():
        exp_bits.append(str(sel["expiry"]).strip())
    if sel.get("dte") not in (None, ""):
        exp_bits.append(f"{int(_safe_float(sel.get('dte')))}DTE")
    if exp_bits:
        lines.append("🗓 " + " · ".join(exp_bits))

    entry = _safe_float(sel.get("entry_price") or sel.get("entry")
                        or sel.get("premium") or sel.get("ltp"))
    money = []
    if entry > 0:
        money.append(f"Entry ≈ ₹{entry:.1f}")
    if sel.get("oi") not in (None, "", 0):
        money.append(f"OI {_fmt_oi(sel.get('oi'))}")
    if money:
        lines.append("💰 " + "  ·  ".join(money))

    # OI-direction color indicator, one line per leg, for multi-leg
    # structures (C1/C3/...). 2026-07-20, operator: "include some image
    # or color indicator of OI direction." Observed context only -- see
    # option_core_strategies.py's oi_context_unvalidated note; this is
    # display, not a claim that OI direction predicts anything here.
    legs = sel.get("legs")
    if isinstance(legs, list) and legs:
        oi_bits = []
        for leg in legs:
            if not isinstance(leg, dict) or not leg.get("oi_emoji"):
                continue
            leg_lbl = f"{leg.get('option_type', '')}{int(_safe_float(leg.get('strike')))}"
            oi_bits.append(f"{leg.get('oi_emoji')}{leg_lbl}")
        if oi_bits:
            lines.append("OI " + " ".join(oi_bits)
                         + "  (🟢buildup 🔴unwinding ⚪flat)")

    ctx = []
    spot = _safe_float(p.get("spot") or sel.get("spot"))
    if spot > 0:
        ctx.append(f"Spot {spot:,.0f}")
    if _safe_float(p.get("setup_score")) > 0:
        ctx.append(f"Score {_safe_float(p.get('setup_score')):.0f}")
    if p.get("strategy"):
        ctx.append(str(p.get("strategy")))
    if ctx:
        lines.append("📊 " + " · ".join(ctx))

    chain = []
    if _safe_float(q.get("implied_move_pct")) > 0:
        chain.append(f"impl move {_safe_float(q.get('implied_move_pct')):.2f}%")
    if _safe_float(q.get("move_used_ratio")) > 0:
        chain.append(f"used {_safe_float(q.get('move_used_ratio'))*100:.0f}%")
    spr = q.get("ce_spread_pct") if otype == "CE" else q.get("pe_spread_pct")
    if spr is not None and _safe_float(spr) > 0:
        chain.append(f"spread {_safe_float(spr)*100:.1f}%")
    if chain:
        lines.append("🔎 " + " · ".join(chain))

    if p.get("reason"):
        lines.append("📝 " + str(p.get("reason")))
    return "\n".join(lines)


# Research/replay strategies journal decision="selected" for backtest labelling,
# NOT live trades — they must not reach the live option Telegram channel.
_RESEARCH_STRAT_MARKERS = ("historical_replay", "replay", "snapshot", "backtest", "shadow")


def _is_research_strategy(strategy: str) -> bool:
    s = str(strategy or "").lower()
    return any(m in s for m in _RESEARCH_STRAT_MARKERS)


def option_performance_summary(days: int = 400, min_n: int = 10, top: int = 5,
                               path: Optional[str] = None) -> Dict[str, Any]:
    """Option worthiness from labelled option decisions (shadow/replay + live).

    Reads the option journal, aggregates outcome_label/pnl over the window, and
    ranks strategies by avg P&L. Also counts LIVE (non-research) selections so the
    digest separates measured edge from live activity. Best-effort; never raises.
    """
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    out: Dict[str, Any] = {"ok": False, "days": days}
    try:
        jpath = ensure_option_journal(path)
        cutoff = (_dt.now() - _td(days=int(days))).strftime("%Y-%m-%d")
        rows = []
        live_sel = 0
        with open(jpath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                t = str(d.get("time", ""))[:10]
                if t and t < cutoff:
                    continue
                dec = str(d.get("decision", ""))
                if dec.startswith("selected") and not _is_research_strategy(d.get("strategy")):
                    live_sel += 1
                if d.get("outcome_label") in (1, 0, -1):
                    rows.append(d)
    except FileNotFoundError:
        out["error"] = "no_option_journal"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out

    if not rows:
        out.update({"ok": True, "n_labelled": 0, "live_selected": live_sel})
        return out

    wins = sum(1 for d in rows if d.get("outcome_label") == 1)
    losses = sum(1 for d in rows if d.get("outcome_label") == -1)
    timeouts = sum(1 for d in rows if d.get("outcome_label") == 0)
    pnls = [_safe_float(d.get("pnl")) for d in rows if d.get("pnl") not in (None, "")]
    live_rows = [d for d in rows if not _is_research_strategy(d.get("strategy"))]
    median_pnl = 0.0
    if pnls:
        _sp = sorted(pnls); _m = len(_sp)
        median_pnl = _sp[_m // 2] if _m % 2 else (_sp[_m // 2 - 1] + _sp[_m // 2]) / 2.0
    by: Dict[str, Dict[str, float]] = {}
    for d in rows:
        s = str(d.get("strategy") or "?")
        b = by.setdefault(s, {"n": 0, "w": 0, "pnl": 0.0})
        b["n"] += 1
        if d.get("outcome_label") == 1:
            b["w"] += 1
        b["pnl"] += _safe_float(d.get("pnl"))
    ranked = sorted(
        ({"strategy": k, "n": int(v["n"]), "win_rate": round(100.0*v["w"]/v["n"], 1),
          "avg_pnl": round(v["pnl"]/v["n"], 1)} for k, v in by.items() if v["n"] >= min_n),
        key=lambda x: x["avg_pnl"], reverse=True,
    )
    n = len(rows)
    out.update({
        "ok": True, "n_labelled": n, "live_selected": live_sel,
        "live_labelled": len(live_rows),
        "wins": wins, "losses": losses, "timeouts": timeouts,
        "win_rate": round(100.0*wins/n, 1) if n else 0.0,
        "total_pnl": round(sum(pnls), 1), "avg_pnl": round(sum(pnls)/len(pnls), 1) if pnls else 0.0,
        "median_pnl": round(median_pnl, 1),
        "sample_quality": "ROBUST" if n >= 200 else "DEVELOPING" if n >= 50 else "LOW_SAMPLE",
        "outlier_skew_warning": bool(pnls and abs(sum(pnls)/len(pnls)-median_pnl) > max(100.0, abs(median_pnl)*2)),
        "best": ranked[:top], "worst": ranked[-top:][::-1] if len(ranked) > top else [],
    })
    return out


def format_option_summary(d: Dict[str, Any]) -> str:
    """Render option_performance_summary() as an HTML Telegram digest."""
    if not d.get("ok"):
        return f"📊 Option summary unavailable ({d.get('error', '-')})"
    if not d.get("n_labelled"):
        return (f"📊 <b>Option Edge</b> — last {d['days']}d\n"
                f"live selections: {d.get('live_selected', 0)}\n"
                f"no labelled outcomes yet (need EOD/replay labelling)")
    # Flag on MEDIAN (avg is outlier-skewed for option buying): a low win rate
    # with positive avg is the classic "few big wins" profile — don't call it a win.
    flag = "✅" if d.get("median_pnl", 0) > 0 else "🔴"
    lines = [
        f"📊 <b>Option Edge</b> — last {d['days']}d",
        f"labelled {d['n_labelled']} · live selections {d.get('live_selected', 0)}",
        f"sample: <b>{d.get('sample_quality','LOW_SAMPLE')}</b> · verified live labels {d.get('live_labelled',0)}",
        f"W/L/T: {d['wins']}/{d['losses']}/{d['timeouts']} · win {d['win_rate']}%",
        f"P&L: {flag} median ₹{d.get('median_pnl', 0):+.0f} · avg ₹{d['avg_pnl']:+.0f} · total ₹{d['total_pnl']:+.0f}",
        "<i>shadow/replay-labelled — live N still small; avg is outlier-skewed</i>",
    ]
    if d.get("outlier_skew_warning"):
        lines.append("⚠️ Mean is skewed by outliers; prioritize median and verified-live results.")
    if d.get("best"):
        lines.append("\n🏆 <b>best</b> (n≥10):")
        for b in d["best"]:
            lines.append(f"  {b['strategy'][:24]}: ₹{b['avg_pnl']:+.0f} (n={b['n']}, win {b['win_rate']}%)")
    if d.get("worst"):
        lines.append("\n⚠️ <b>worst</b>:")
        for b in d["worst"]:
            lines.append(f"  {b['strategy'][:24]}: ₹{b['avg_pnl']:+.0f} (n={b['n']})")
    return "\n".join(lines)


def option_scalp_performance(days: int = 400, path: Optional[str] = None) -> Dict[str, Any]:
    """Option SCALPING P&L from labelled journal outcomes (strategy contains
    'scalp'). Splits LIVE vs replay-research so the live picture is honest, with
    per-strategy + today breakdowns. Best-effort; never raises."""
    import json as _json
    from datetime import datetime as _dt, timedelta as _td
    out: Dict[str, Any] = {"ok": False, "days": days}
    try:
        jpath = ensure_option_journal(path)
        cutoff = (_dt.now() - _td(days=int(days))).strftime("%Y-%m-%d")
        today = _dt.now().strftime("%Y-%m-%d")
        rows = []
        with open(jpath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except Exception:
                    continue
                if d.get("outcome_label") not in (1, 0, -1):
                    continue
                if "scalp" not in str(d.get("strategy", "")).lower():
                    continue
                if str(d.get("time", ""))[:10] < cutoff:
                    continue
                rows.append(d)
    except FileNotFoundError:
        out["error"] = "no_option_journal"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out

    if not rows:
        out.update({"ok": True, "n": 0})
        return out

    def _agg(items):
        n = len(items)
        wins = sum(1 for d in items if d.get("outcome_label") == 1)
        losses = sum(1 for d in items if d.get("outcome_label") == -1)
        pnls = [_safe_float(d.get("pnl")) for d in items if d.get("pnl") not in (None, "")]
        med = 0.0
        if pnls:
            sp = sorted(pnls); m = len(sp)
            med = sp[m // 2] if m % 2 else (sp[m // 2 - 1] + sp[m // 2]) / 2.0
        return {"n": n, "wins": wins, "losses": losses,
                "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
                "total_pnl": round(sum(pnls), 1),
                "avg_pnl": round(sum(pnls) / len(pnls), 1) if pnls else 0.0,
                "median_pnl": round(med, 1)}

    live = [d for d in rows if not _is_research_strategy(d.get("strategy"))]
    by: Dict[str, list] = {}
    for d in rows:
        by.setdefault(str(d.get("strategy") or "?"), []).append(d)
    today_rows = [d for d in rows if str(d.get("time", ""))[:10] == today]

    out.update({
        "ok": True, "n": len(rows), "all": _agg(rows), "live": _agg(live),
        "today": _agg(today_rows),
        "by_strategy": {k: _agg(v) for k, v in sorted(by.items(), key=lambda x: -len(x[1]))},
    })
    return out


def format_scalp_performance(d: Dict[str, Any]) -> str:
    """Render option_scalp_performance() as a Telegram P&L digest."""
    if not d.get("ok"):
        return f"📊 Scalp P&L unavailable ({d.get('error', '-')})"
    if not d.get("n"):
        return f"⚡ <b>Option Scalp P&L</b> — last {d['days']}d\n  no labelled scalp outcomes yet"
    a, lv, td = d["all"], d["live"], d["today"]
    flag = "✅" if a["median_pnl"] > 0 else "🔴"
    lines = [
        f"⚡ <b>Option Scalp P&L</b> — last {d['days']}d",
        f"all: {a['n']} · W/L {a['wins']}/{a['losses']} · win {a['win_rate']}%",
        f"P&L: {flag} median ₹{a['median_pnl']:+.0f} · avg ₹{a['avg_pnl']:+.0f} · total ₹{a['total_pnl']:+.0f}",
        f"today: {td['n']} scalps · ₹{td['total_pnl']:+.0f}  |  live: {lv['n']} · ₹{lv['total_pnl']:+.0f}",
        "<i>incl. replay-labelled; option scalping is measured net-negative</i>",
    ]
    lines.append("\nby strategy:")
    for k, v in list(d.get("by_strategy", {}).items())[:6]:
        lines.append(f"  {k[:26]}: n={v['n']} win {v['win_rate']}% ₹{v['avg_pnl']:+.0f}/trade")
    return "\n".join(lines)


def _alert_option_selection(payload: Dict[str, Any]) -> bool:
    try:
        if not str(payload.get("decision") or "").startswith("selected"):
            return False
        # Live trades only — exclude historical_replay/snapshot research strategies
        # (320 of 324 'selected' in the journal are replay labelling, not trades).
        if _is_research_strategy(payload.get("strategy")):
            return False
        am = _option_alerts()
        if am is None:
            return False
        sel = payload.get("selected") or {}
        key = (payload.get("trade_id") or payload.get("source_id")
               or f"{payload.get('symbol')}_{sel.get('strike')}_{sel.get('option_type')}_{payload.get('side')}")
        return bool(am.send(
            _format_option_card(payload),
            dedup_key=f"optsel_{key}", dedup_cooldown_override=3600,
        ))
    except Exception as exc:
        logger.debug("option selection alert: %s", exc)
        return False


def alert_option_scalp_report(payload: Dict[str, Any], df) -> bool:
    """Send the evidence image after the OPTION_SCALP text selection card."""
    try:
        if str(payload.get("strategy") or "").upper() != "OPTION_SCALP":
            return False
        am = _option_alerts()
        if am is None:
            return False
        from scalp_signal_report import render_scalp_signal_report
        path = render_scalp_signal_report(df, payload)
        sel = payload.get("selected") or {}
        leg = str(sel.get("symbol") or payload.get("symbol") or "OPTION SCALP")
        caption = (
            f"📊 <b>{leg}</b> — supporting evidence\n"
            "Underlying levels shown; verify live option premium and liquidity."
        )
        if payload.get("_selection_alert_sent") is False:
            # The text card was queued first. Queue the image behind it instead
            # of sending it immediately and reversing the conversation order.
            am._spool_photo(path, caption)
            return False
        return bool(am.send_photo(path, caption))
    except Exception as exc:
        logger.debug("option scalp evidence report: %s", exc)
        return False


def alert_generated_option_signals(
    *, underlying: str, snapshot_time: str, expiry: str, signals: list[Dict[str, Any]]
) -> int:
    """Route persisted actionable strike-flow signals to the option channel."""
    actionable = [row for row in (signals or []) if bool(row.get("tradable"))]
    rows_to_send = actionable
    if not rows_to_send:
        watches = [row for row in (signals or []) if str(row.get("signal") or "").startswith("BUY_")]
        rows_to_send = sorted(watches, key=lambda row: _safe_float(row.get("score")), reverse=True)[:1]
    if not rows_to_send:
        return 0
    am = _option_alerts()
    if am is None:
        return 0
    sent = 0
    try:
        from option_bot_views import score_gauge
    except Exception:
        score_gauge = None
    for row in rows_to_send:
        strike = _safe_float(row.get("strike"))
        otype = str(row.get("option_type") or "").upper()
        signal = str(row.get("signal") or f"BUY_{otype}").upper()
        status = "ACTIONABLE" if row.get("tradable") else "WATCH — NOT ACTIONABLE"
        lines = [
            f"🎯 <b>{underlying} OPTION SIGNAL</b> · {status}",
            f"<b>{signal} {strike:.0f}{otype}</b>",
        ]
        if score_gauge is not None:
            lines.append(score_gauge(row.get("score")))
        else:
            lines.append(f"Score {_safe_float(row.get('score')):.1f}")
        lines += [
            f"Entry ₹{_safe_float(row.get('entry_price')):.2f} | SL ₹{_safe_float(row.get('stop_loss')):.2f}",
            f"T1 ₹{_safe_float(row.get('target_1')):.2f} | T2 ₹{_safe_float(row.get('target_2')):.2f}",
            f"Expiry {expiry or '-'} | Flow {row.get('flow','-')} | Source {row.get('source','-')}",
            f"Edge {row.get('edge_policy','VALIDATING')} | n={int(_safe_float(row.get('edge_outcomes')))} | PF {_safe_float(row.get('edge_profit_factor')):.2f}",
            f"Snapshot {str(snapshot_time)[11:19]}",
            f"<i>{str(row.get('reason') or '')[:180]}</i>",
        ]
        key = f"optflow_{snapshot_time}_{underlying}_{strike:.0f}_{otype}"
        if am.send("\n".join(lines), dedup_key=key, dedup_cooldown_override=86_400):
            sent += 1
    return sent


def alert_option_lifecycle_events(events: list[Dict[str, Any]]) -> int:
    """Send target/stop updates produced by later verified option snapshots."""
    am = _option_alerts()
    if am is None:
        return 0
    sent = 0
    icons = {"TARGET1_HIT": "✅", "TARGET2_HIT": "🏆", "STOP_LOSS_HIT": "🛑", "EXIT_AFTER_T1": "🔒"}
    for event in events or []:
        status = str(event.get("status") or "UPDATED")
        leg = f"{event.get('underlying')} {float(event.get('strike') or 0):.0f}{event.get('option_type')}"
        lines = [
            f"{icons.get(status, '🔔')} <b>{status.replace('_', ' ')} — {leg}</b>",
            f"Current ₹{_safe_float(event.get('current_price')):.2f} | Entry ₹{_safe_float(event.get('entry_price')):.2f}",
            f"SL ₹{_safe_float(event.get('stop_loss')):.2f} | T1 ₹{_safe_float(event.get('target_1')):.2f} | T2 ₹{_safe_float(event.get('target_2')):.2f}",
        ]
        if status == "TARGET1_HIT":
            lines.append("SL revised to cost-to-cost after T1.")
        key = f"optlife_{event.get('signal_time')}_{leg}_{status}"
        if am.send("\n".join(lines), dedup_key=key, dedup_cooldown_override=172_800):
            sent += 1
    return sent


def _alert_option_result(payload: Dict[str, Any]) -> None:
    """Lifecycle/result alert to the option channel when a LIVE option decision
    gets its outcome recorded (win/loss/timeout + P&L). Research/replay outcomes
    are excluded. NOTE: this fires when the OUTCOME is journaled — autonomous
    options aren't tracked as live intraday positions, so real-time SL/target
    hits are not emitted (no option position tracker on that path)."""
    try:
        if payload.get("outcome_label") not in (1, 0, -1):
            return
        if _is_research_strategy(payload.get("strategy")):
            return
        am = _option_alerts()
        if am is None:
            return
        sel = payload.get("selected") or {}
        leg = str(sel.get("symbol") or "").strip()
        if not leg:
            leg = (f"{payload.get('symbol','')} {sel.get('strike','')}"
                   f"{(sel.get('option_type') or '')}").strip()
        lbl = payload.get("outcome_label")
        verdict = "✅ WIN" if lbl == 1 else ("🔴 LOSS" if lbl == -1 else "⏱ TIMEOUT")
        lines = [f"🏁 <b>OPTION RESULT — {payload.get('symbol','')}</b>",
                 f"{verdict}  {leg}"]
        if payload.get("pnl") is not None:
            lines.append(f"P&L: ₹{_safe_float(payload.get('pnl')):+.0f}")
        oc = payload.get("outcome") or {}
        if isinstance(oc, dict) and oc.get("exit_reason"):
            lines.append(f"exit: {oc.get('exit_reason')}")
        key = (payload.get("trade_id") or payload.get("source_id")
               or f"{leg}_{payload.get('ts','')}")
        am.send("\n".join(lines), dedup_key=f"optres_{key}", dedup_cooldown_override=3600)
    except Exception as exc:
        logger.debug("option result alert: %s", exc)


def send_option_daily_summary(days: int = 1) -> bool:
    """Post the option edge digest to the option channel (once/day via dedup).
    Best-effort; returns True if sent. Call from the EOD/post-market path."""
    try:
        am = _option_alerts()
        if am is None:
            return False
        import time as _t
        text = format_option_summary(option_performance_summary(days=days))
        am.send(text, dedup_key=f"optsum_{_t.strftime('%Y-%m-%d')}",
                dedup_cooldown_override=72_000)   # ~20h: once per trading day
        return True
    except Exception as exc:
        logger.debug("send_option_daily_summary: %s", exc)
        return False


def record_option_decision(
    *,
    strategy: str,
    symbol: str,
    decision: str,
    reason: str = "",
    side: str = "",
    spot: float = 0.0,
    setup_score: float = 0.0,
    quality: Optional[Dict[str, Any]] = None,
    selected: Optional[Dict[str, Any]] = None,
    strikes: Optional[Any] = None,
    trade_id: str = "",
    source_id: str = "",
    outcome: Optional[Dict[str, Any]] = None,
    outcome_label: Optional[int] = None,
    pnl: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append one option decision to JSONL and return the payload.

    decision examples: selected, blocked_quality, blocked_no_tradeable_strike.
    """
    out_path = Path(ensure_option_journal(path))
    strike_rows = strikes or []
    if (
        AUTO_SHADOW_CANDIDATES
        and str(decision or "").startswith("selected")
        and not strike_rows
    ):
        strike_rows = synthesize_shadow_candidates(selected, spot=spot, side=side)
    selected_row = selected if isinstance(selected, dict) else {}
    metadata_row = metadata if isinstance(metadata, dict) else {}
    quality_row = quality if isinstance(quality, dict) else {}
    chain_quality = (
        quality_row.get("chain_quality")
        if isinstance(quality_row.get("chain_quality"), dict)
        else {}
    )
    source = str(
        metadata_row.get("data_source")
        or metadata_row.get("source")
        or selected_row.get("source")
        or quality_row.get("source")
        or chain_quality.get("source")
        or ""
    ).lower()
    selected_synthetic = bool(selected_row.get("synthetic_shadow"))
    synthetic_shadow_count = sum(
        1 for row in strike_rows
        if isinstance(row, dict) and row.get("synthetic_shadow")
    )
    research = (
        _is_research_strategy(strategy)
        or "backfill" in str(reason or "").lower()
        or "replay" in str(source_id or "").lower()
    )
    valid_contract = bool(
        float(spot or 0) > 0
        and _safe_float(selected_row.get("strike"), 0) > 0
        and str(selected_row.get("option_type") or "").upper() in {"CE", "PE"}
        and _safe_float(
            selected_row.get("premium") or selected_row.get("entry_price") or selected_row.get("ltp"),
            0,
        ) > 0
    )
    is_live_data = bool(source in _LIVE_DATA_SOURCES and valid_contract and not selected_synthetic and not research)
    if research or selected_synthetic:
        evidence_class = "RESEARCH_SYNTHETIC"
    elif is_live_data and trade_id:
        evidence_class = "LIVE_EXECUTION"
    elif is_live_data:
        evidence_class = "LIVE_OBSERVATION"
    else:
        evidence_class = "UNVERIFIED"
    payload = {
        "ts": time.time(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "strategy": str(strategy or ""),
        "symbol": str(symbol or ""),
        "decision": str(decision or ""),
        "reason": str(reason or ""),
        "side": str(side or ""),
        "spot": float(spot or 0.0),
        "setup_score": float(setup_score or 0.0),
        "quality": _clean(quality or {}),
        "selected": _clean(selected or {}),
        "strikes": _clean(strike_rows),
        "trade_id": str(trade_id or ""),
        "source_id": str(source_id or ""),
        "data_source": source,
        "is_live_data": is_live_data,
        "evidence_class": evidence_class,
        "selected_synthetic": selected_synthetic,
        "synthetic_shadow_count": synthetic_shadow_count,
    }
    if metadata:
        payload["metadata"] = _clean(metadata)
    if outcome:
        payload["outcome"] = _clean(outcome)
    if outcome_label is not None:
        payload["outcome_label"] = int(outcome_label)
    if pnl is not None:
        payload["pnl"] = float(pnl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
    payload["_selection_alert_sent"] = _alert_option_selection(payload)
    _alert_option_result(payload)
    return payload


def load_recent_option_decisions(
    path: str = DEFAULT_JOURNAL_FILE,
    limit: int = 100,
) -> list:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-int(limit):]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def label_option_decision(
    trade_id: str,
    *,
    outcome_label: int,
    pnl: float,
    exit_reason: str = "",
    path: str = DEFAULT_JOURNAL_FILE,
) -> int:
    """
    Attach outcome data to selected journal rows matching trade_id.

    Returns number of rows updated. The rewrite is idempotent and preserves
    unrelated rows. If no matching row exists, returns 0.
    """
    trade_id = str(trade_id or "").strip()
    if not trade_id:
        return 0
    p = Path(path)
    if not p.exists():
        return 0

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    updated = 0
    new_lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if (
            str(row.get("trade_id", "") or "") == trade_id
            and str(row.get("decision", "")) == "selected"
        ):
            row["outcome"] = {
                "label": int(outcome_label),
                "pnl": float(pnl),
                "exit_reason": str(exit_reason or ""),
                "labelled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            row["outcome_label"] = int(outcome_label)
            row["pnl"] = float(pnl)
            updated += 1
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))

    if updated:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


def label_option_shadow_decisions(
    trade_id: str,
    shadow_outcomes: Any,
    *,
    path: str = DEFAULT_JOURNAL_FILE,
) -> int:
    """
    Attach outcome data to shadow strike candidates for a selected trade row.

    shadow_outcomes may be a list of dicts keyed by symbol/strike/option_type, or
    a dict keyed by symbol. Each outcome can include pnl, label, exit_price, and
    exit_reason. Returns number of shadow candidates updated.
    """
    trade_id = str(trade_id or "").strip()
    if not trade_id:
        return 0
    p = Path(path)
    if not p.exists():
        return 0

    if isinstance(shadow_outcomes, dict):
        outcomes = []
        for key, value in shadow_outcomes.items():
            item = dict(value) if isinstance(value, dict) else {"pnl": value}
            item.setdefault("symbol", key)
            outcomes.append(item)
    elif isinstance(shadow_outcomes, list):
        outcomes = [o for o in shadow_outcomes if isinstance(o, dict)]
    else:
        outcomes = []
    if not outcomes:
        return 0

    def _match(candidate: Dict[str, Any], outcome: Dict[str, Any]) -> bool:
        c_sym = str(candidate.get("symbol", "") or "")
        o_sym = str(outcome.get("symbol", "") or "")
        if c_sym and o_sym and c_sym == o_sym:
            return True
        try:
            c_strike = float(candidate.get("strike", 0) or 0)
            o_strike = float(outcome.get("strike", 0) or 0)
        except Exception:
            c_strike = o_strike = 0.0
        c_type = str(candidate.get("option_type", "") or "")
        o_type = str(outcome.get("option_type", "") or "")
        return bool(c_strike and o_strike and c_strike == o_strike and c_type == o_type)

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    updated = 0
    new_lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        row_trade_id = str(row.get("trade_id", "") or "").strip()
        row_source_id = str(row.get("source_id", "") or "").strip()
        if (
            (row_trade_id == trade_id or row_source_id == trade_id)
            and str(row.get("decision", "")) == "selected"
            and isinstance(row.get("strikes"), list)
        ):
            for candidate in row["strikes"]:
                if not isinstance(candidate, dict):
                    continue
                for outcome in outcomes:
                    if not _match(candidate, outcome):
                        continue
                    pnl = float(outcome.get("pnl", 0.0) or 0.0)
                    label = int(outcome.get("label", 1 if pnl > 0 else -1 if pnl < 0 else 0))
                    candidate["shadow_outcome"] = {
                        "label": label,
                        "pnl": pnl,
                        "exit_price": outcome.get("exit_price"),
                        "exit_reason": str(outcome.get("exit_reason", "") or ""),
                        "labelled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    }
                    updated += 1
                    break
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))

    if updated:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


def repair_missing_shadow_candidates(
    *,
    path: str = DEFAULT_JOURNAL_FILE,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Enrich existing selected rows that have no strike comparison ladder.

    Returns counts and rewrites the journal only when rows are changed.
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "reason": "journal_missing", "updated": 0}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = []
    updated = 0
    selected_seen = 0
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if str(row.get("decision", "")).startswith("selected"):
            selected_seen += 1
            strikes = row.get("strikes")
            needs_synthetic_refresh = (
                isinstance(strikes, list)
                and strikes
                and len(strikes) < (AUTO_SHADOW_WINGS * 2 + 1)
                and all(isinstance(s, dict) and s.get("synthetic_shadow") for s in strikes)
            )
            if not isinstance(strikes, list) or not strikes or needs_synthetic_refresh:
                existing_outcomes = {}
                if isinstance(strikes, list):
                    for item in strikes:
                        if isinstance(item, dict) and isinstance(item.get("shadow_outcome"), dict):
                            key = (
                                str(item.get("symbol") or ""),
                                str(item.get("strike") or ""),
                                str(item.get("option_type") or ""),
                            )
                            existing_outcomes[key] = item["shadow_outcome"]
                synth = synthesize_shadow_candidates(
                    row.get("selected") if isinstance(row.get("selected"), dict) else {},
                    spot=_safe_float(row.get("spot"), 0.0),
                    side=str(row.get("side") or ""),
                )
                if synth:
                    for item in synth:
                        key = (
                            str(item.get("symbol") or ""),
                            str(item.get("strike") or ""),
                            str(item.get("option_type") or ""),
                        )
                        if key in existing_outcomes:
                            item["shadow_outcome"] = existing_outcomes[key]
                    row["strikes"] = synth
                    row["shadow_repaired_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    updated += 1
        new_lines.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
    if updated and not dry_run:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "journal_file": str(p),
        "selected_seen": selected_seen,
        "updated": updated,
        "dry_run": dry_run,
    }
