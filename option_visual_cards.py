"""Telegram visuals built only from supplied or stored observations.

Pure renderers never send messages or change trading state. Unknown values
stay unknown; a generated setup is never presented as an executed trade.
"""
from __future__ import annotations

import hashlib
import html
import math
import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import chart_theme as ct

IST = ZoneInfo("Asia/Kolkata")


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def positive(*values: Any) -> float | None:
    return next((parsed for value in values if (parsed := number(value)) is not None and parsed > 0), None)


def money(value: Any) -> str:
    parsed = number(value)
    return f"₹{parsed:,.2f}" if parsed is not None else "—"


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
    except (TypeError, ValueError):
        return None


def signal_view(payload: dict) -> dict:
    """Normalize journal decisions and persisted strike-flow rows for display."""
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else payload
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    legs = selected.get("legs") if isinstance(selected.get("legs"), list) else []
    legs = [leg for leg in legs if isinstance(leg, dict)]
    symbol = str(payload.get("underlying") or payload.get("symbol") or "OPTION")
    strike = positive(selected.get("strike"))
    ot = str(selected.get("option_type") or "").upper()
    side = str(selected.get("trade_side") or selected.get("side") or payload.get("side") or
               ("BUY" if str(payload.get("signal", "")).startswith("BUY_") else "")).upper()
    direction = str(payload.get("direction") or selected.get("underlying_direction") or "").upper()
    if legs:
        direction = "MULTI-LEG"
    elif direction not in {"BULLISH", "BEARISH"}:
        direction = ({("BUY", "CE"): "BULLISH", ("BUY", "PE"): "BEARISH",
                      ("SELL", "CE"): "BEARISH", ("SELL", "PE"): "BULLISH"}
                     .get((side, ot), "NOT PROVIDED"))
    research = bool(metadata.get("unvalidated") or metadata.get("research_only") or
                    metadata.get("shadow_only") or "RESEARCH" in str(payload.get("evidence_class", "")))
    lifecycle = str(payload.get("lifecycle_status") or "SETUP")
    if payload.get("demo"):
        status = "DEMO / SYNTHETIC"
    elif research:
        status = "SHADOW RESEARCH"
    elif lifecycle in {"STOP_LOSS_HIT", "TARGET2_HIT", "EXIT_AFTER_T1"}:
        status = "SIGNAL CLOSED"
    elif "tradable" in payload and not payload.get("tradable"):
        status = "WATCH / BLOCKED"
    elif payload.get("focused_paper") or str(payload.get("broker_name", "")).upper() == "PAPER":
        status = "PAPER SETUP"
    elif payload.get("trade_id"):
        status = "RECORDED TRADE"
    else:
        status = "SELECTED SETUP"
    contract = (f"{symbol} {strike:,.0f} {ot}" if strike and ot
                else str(selected.get("symbol") or symbol))
    if legs:
        contract = f"{symbol} · {payload.get('strategy') or 'MULTI-LEG'}"
    quality = payload.get("quality") or {}
    if isinstance(quality, dict) and isinstance(quality.get("chain_quality"), dict):
        quality = quality["chain_quality"]
    if not isinstance(quality, dict):
        quality = {}
    spread = number(selected.get("spread_pct"))
    if spread is None:
        spread = number(quality.get("ce_spread_pct" if ot == "CE" else "pe_spread_pct"))
    entry = positive(selected.get("entry_price"), selected.get("entry"), selected.get("premium"), selected.get("ltp"))
    stop = positive(selected.get("stop_loss"), selected.get("sl"))
    target = positive(selected.get("target_1"), selected.get("target_price"), selected.get("target"))
    target2 = positive(selected.get("target_2"))
    risk = abs(entry - stop) if entry and stop and entry != stop else None
    valid_plan = bool(entry and stop and target and (
        (side == "BUY" and stop < entry < target) or
        (side == "SELL" and target < entry < stop)))
    rr = abs(target - entry) / risk if valid_plan and risk else None
    score = number(payload.get("score"))  # Never rescale unrelated setup scores.
    clock = _time(payload.get("snapshot_time") or payload.get("time"))
    return dict(contract=contract, symbol=symbol, strike=strike, option_type=ot,
                side=side or "NOT PROVIDED", direction=direction, status=status,
                entry=entry, stop=stop, target=target, target2=target2,
                current=positive(payload.get("current_price"), payload.get("price"), selected.get("ltp")),
                risk=risk, rr=rr, valid_plan=valid_plan, score=score,
                setup_score=number(payload.get("setup_score")),
                expiry=str(selected.get("expiry") or selected.get("option_expiry") or payload.get("expiry") or
                           (metadata.get("regime") or {}).get("next_expiry") or "Not provided"),
                timestamp=clock.strftime("%d %b · %H:%M IST") if clock else "Quote time unavailable",
                snapshot_time=str(payload.get("snapshot_time") or payload.get("time") or ""),
                spread=spread, volume=number(selected.get("volume")),
                edge=str(payload.get("edge_policy") or metadata.get("edge_status") or "UNVERIFIED"),
                outcomes=number(payload.get("edge_outcomes")),
                pf=number(payload.get("edge_profit_factor")),
                reason=str(payload.get("reason") or "No additional context recorded").replace("_", " "),
                lifecycle=lifecycle.replace("TARGET1", "TARGET 1").replace("TARGET2", "TARGET 2").replace("_", " "),
                source=str(payload.get("source") or payload.get("data_source") or quality.get("source") or "Unknown"),
                strategy=str(payload.get("strategy") or payload.get("flow") or "Option setup"),
                legs=legs, net_credit=number(selected.get("net_credit")),
                net_debit=number(selected.get("net_debit")), max_loss=number(selected.get("max_loss")))


def option_navigation() -> dict:
    return {"inline_keyboard": [
        [{"text": "📊 Dashboard", "callback_data": "cmd:report"},
         {"text": "🎯 Signals", "callback_data": "cmd:signals"}],
        [{"text": "📈 OI chart", "callback_data": "cmd:oichart"},
         {"text": "📌 Positions", "callback_data": "cmd:positions"}],
    ]}


def signal_caption(payload: dict) -> str:
    v = signal_view(payload)
    escape = lambda value, length=65: html.escape(str(value)[:length], quote=False)
    lines = [f"🎯 <b>{escape(v['contract'])}</b>",
             f"<b>{escape(v['status'])}</b> · {escape(v['direction'])}",
             f"{escape(v['side'])} · Expiry {escape(v['expiry'], 24)}"]
    if v["entry"]:
        lines.append(f"Entry {money(v['entry'])} · SL {money(v['stop'])}")
        lines.append(f"T1 {money(v['target'])} · T2 {money(v['target2'])}")
    if v["legs"]:
        lines.append(f"{len(v['legs'])} legs · {escape(v['strategy'], 35)}")
    lines.extend([f"{escape(v['timestamp'])}", "<i>Setup view · execution is tracked separately.</i>"])
    return "\n".join(lines)


def premium_history(payload: dict, db_path: str = "option_chain_snapshots.db", limit: int = 60) -> list:
    """Read this contract's same-session recorded premiums, never a proxy series."""
    view = signal_view(payload)
    stamp = _time(view["snapshot_time"])
    if not stamp or not view["strike"] or not view["option_type"] or not Path(db_path).exists():
        return []
    from option_quality import normalize_expiry
    expiry = normalize_expiry(view["expiry"])
    if not expiry:
        return []
    points = []
    try:
        from contextlib import closing
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
            rows = conn.execute(
                "SELECT snapshot_time,expiry,price FROM option_strike_signals "
                "WHERE upper(underlying)=upper(?) AND strike=? AND option_type=? "
                "AND snapshot_time LIKE ? AND price>0 "
                "AND source IN ('angel','angel_eod','angel_fallback','nse_live',"
                "'resilience_nse','sensibull','bse','bse_oc') ORDER BY ts DESC LIMIT ?",
                (view["symbol"], view["strike"], view["option_type"], stamp.date().isoformat() + "%", max(1, min(limit, 240))),
            ).fetchall()
        for raw_time, raw_expiry, raw_price in rows:
            clock, price = _time(raw_time), positive(raw_price)
            if clock and price and clock <= stamp and normalize_expiry(raw_expiry) == expiry:
                points.append((clock, price))
    except (sqlite3.Error, OSError):
        return []
    return sorted(dict(points).items())[-limit:]


def _canvas(width=8, height=10):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    fig = Figure(figsize=(width, height), dpi=140, facecolor=ct.BG)
    FigureCanvasAgg(fig)
    return fig


def _panel(fig, bounds):
    ax = fig.add_axes(bounds)
    ax.set_facecolor(ct.PANEL)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(ct.GRID)
    return ax


def _text(ax, x, y, value, size=11, color=ct.TEXT_SECONDARY, weight="normal", **kw):
    kw.setdefault("parse_math", False)
    return ax.text(x, y, str(value), fontsize=size, color=color, fontweight=weight,
                   transform=ax.transAxes, va="top", **kw)


def _status_color(v):
    if v["status"] == "WATCH / BLOCKED":
        return ct.WARNING
    if "RESEARCH" in v["status"] or "DEMO" in v["status"]:
        return ct.INFO
    return ct.TEXT_PRIMARY


def _levels(ax, v, y=.30):
    levels = [("SL", v["stop"], ct.BEARISH), ("ENTRY", v["entry"], ct.INFO),
              ("T1", v["target"], ct.BULLISH), ("T2", v["target2"], ct.CATEGORICAL[2])]
    usable = [(label, price, color) for label, price, color in levels if price]
    if len(usable) < 2 or not v["valid_plan"]:
        label = ("Risk / reward cannot be calculated from these levels" if v["entry"] and v["stop"] and v["target"]
                 else "Complete risk levels not recorded")
        _text(ax, .04, y + .08, label, color=ct.TEXT_MUTED, size=10)
        return
    low, high = min(p for _, p, _ in usable), max(p for _, p, _ in usable)
    span = high - low or 1
    x = lambda price: .09 + .82 * (price - low) / span
    ax.plot([.09, .91], [y, y], color=ct.GRID, linewidth=4)
    entry_x, stop_x, target_x = x(v["entry"]), x(v["stop"]), x(v["target"])
    ax.plot([stop_x, entry_x], [y, y], color=ct.BEARISH, linewidth=5, alpha=.75)
    ax.plot([entry_x, target_x], [y, y], color=ct.BULLISH, linewidth=5, alpha=.75)
    for i, (label, price, color) in enumerate(usable):
        ax.plot(x(price), y, "o", markersize=7, color=color, markeredgecolor=ct.BG)
        # Stagger entry and targets to avoid merged labels on narrow spreads.
        _text(ax, x(price), y + (.13 if i % 2 == 0 else -.07), label,
              color=color, size=9, weight="bold", ha="center")
    if v["current"]:
        cur_x = min(.95, max(.05, x(v["current"])))
        ax.plot(cur_x, y + .035, "v", color=ct.TEXT_PRIMARY, markersize=7)
        _text(ax, cur_x, y + .27, "LAST", color=ct.TEXT_PRIMARY, size=8, ha="center")


def _save(fig, output_dir, prefix, fingerprint) -> str:
    out = Path(output_dir or "reports/telegram_visuals")
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(repr(fingerprint).encode()).hexdigest()[:16]
    path = out / f"{prefix}_{digest}.png"
    fig.savefig(path, facecolor=ct.BG, dpi=140)
    return str(path.resolve())


def render_option_signal_card(payload: dict, *, history: list | None = None, output_dir: str | None = None) -> str:
    """A portrait card with readable levels, quote history and evidence status."""
    v = signal_view(payload)
    fig = _canvas(8, 10.5)
    header = _panel(fig, [.045, .83, .91, .13])
    _text(header, .04, .86, "OPTION DESK", size=10, color=ct.TEXT_MUTED, weight="bold")
    _text(header, .96, .86, v["status"], size=10, color=_status_color(v), weight="bold", ha="right")
    _text(header, .04, .58, textwrap.shorten(v["contract"], width=43, placeholder="…"), size=23, color=ct.TEXT_PRIMARY, weight="bold")
    direction_color = ct.BULLISH if v["direction"] == "BULLISH" else ct.BEARISH if v["direction"] == "BEARISH" else ct.INFO
    _text(header, .04, .23, f"{v['side']}  /  {v['direction']}  ·  {v['lifecycle']}", size=11, color=direction_color)

    metric_fields = [
        ("ENTRY", v["entry"], ct.INFO), ("STOP LOSS", v["stop"], ct.BEARISH),
        ("TARGET 1", v["target"], ct.BULLISH), ("TARGET 2", v["target2"], ct.CATEGORICAL[2])]
    if v["legs"]:
        metric_fields = [("NET CREDIT / UNIT", v["net_credit"], ct.INFO),
                         ("NET DEBIT / UNIT", v["net_debit"], ct.INFO),
                         ("PAYOFF LOSS / UNIT", v["max_loss"], ct.WARNING),
                         ("LEGS", len(v["legs"]), ct.TEXT_MUTED)]
    for index, (label, price, color) in enumerate(metric_fields):
        ax = _panel(fig, [.045 + index * .233, .727, .211, .079])
        _text(ax, .09, .81, label, color=color, size=9, weight="bold")
        _text(ax, .09, .48, str(int(price)) if label == "LEGS" else money(price), color=ct.TEXT_PRIMARY, size=16, weight="bold")

    chart = fig.add_axes([.105, .405, .81, .275])
    ct.apply_theme(fig, chart)
    chart.set_title("RECORDED OPTION PREMIUM", loc="left", color=ct.TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=13)
    observed = []
    for clock, value in history or []:
        stamp = _time(clock)
        price = positive(value)
        if stamp and price:
            observed.append((stamp, price))
    observed = sorted(dict(observed).items())
    if v["legs"]:
        chart.set_title("STRATEGY LEGS / QUOTED PREMIUM", loc="left", color=ct.TEXT_PRIMARY, fontsize=12, fontweight="bold", pad=13)
        chart.set_axis_off()
        for index, leg in enumerate(v["legs"][:6]):
            action = str(leg.get("side") or v["side"])
            ratio = positive(leg.get("ratio"))
            if ratio and ratio != 1:
                action += f" ×{ratio:g}"
            ot = str(leg.get("option_type") or "?")
            strike = positive(leg.get("strike"))
            strike_label = f"{strike:,.0f}" if strike else "—"
            color = ct.CALL if ot == "CE" else ct.PUT
            _text(chart, .02, .94 - index * .16, f"{action}  {strike_label} {ot}", size=14, color=color, weight="bold")
            _text(chart, .70, .94 - index * .16, money(positive(leg.get("price"))), size=14, ha="right", color=ct.TEXT_PRIMARY)
            _text(chart, .98, .94 - index * .16, str(leg.get("oi_direction") or "OI unknown"), size=9, ha="right", color=ct.TEXT_MUTED)
    elif len(observed) >= 2:
        import matplotlib.dates as mdates
        clocks, prices = zip(*observed)
        chart.plot(clocks, prices, color=ct.INFO, linewidth=2.5, marker="o", markersize=3)
        chart.scatter([clocks[-1]], [prices[-1]], color=ct.TEXT_PRIMARY, s=30, zorder=4)
        chart.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=IST))
        chart.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=5))
        chart.set_ylabel("Premium ₹", color=ct.TEXT_MUTED, fontsize=10)
        for label, price, color in [("SL", v["stop"], ct.BEARISH), ("Entry", v["entry"], ct.INFO),
                                     ("T1", v["target"], ct.BULLISH)]:
            if price and v["valid_plan"]:
                chart.axhline(price, color=color, linestyle="--", alpha=.6, linewidth=1)
                chart.text(.01, price, f" {label} {price:,.2f}", color=color, fontsize=9,
                           transform=chart.get_yaxis_transform(), va="bottom")
        chart.text(1, 1.02, f"{len(observed)} observations · IST", transform=chart.transAxes,
                   ha="right", fontsize=8, color=ct.TEXT_MUTED)
    else:
        chart.set_xticks([])
        chart.set_yticks([])
        chart.text(.5, .58, "No premium history recorded", transform=chart.transAxes,
                   ha="center", color=ct.TEXT_PRIMARY, fontsize=14)
        chart.text(.5, .40, "Levels shown below are the recorded setup.", transform=chart.transAxes,
                   ha="center", color=ct.TEXT_MUTED, fontsize=10)

    risk = _panel(fig, [.045, .245, .91, .12])
    _text(risk, .04, .88, "RISK / REWARD MAP", size=10, weight="bold", color=ct.TEXT_PRIMARY)
    rr = f"T1 reward / risk  {v['rr']:.2f}R gross" if v["rr"] is not None else "R:R not available"
    _text(risk, .96, .88, rr, size=10, color=ct.TEXT_MUTED, ha="right")
    _levels(risk, v, .42)
    if v["legs"]:
        _text(risk, .04, .17, "Payoff values are per unit, before fees. No account risk estimate.", size=9, color=ct.TEXT_MUTED)

    evidence = _panel(fig, [.045, .065, .91, .155])
    _text(evidence, .04, .90, "EVIDENCE & CONTEXT", size=10, weight="bold", color=ct.TEXT_PRIMARY)
    edge = v["edge"].replace("_", " ")
    n = f"n={int(v['outcomes'])}" if v["outcomes"] is not None else "sample not recorded"
    _text(evidence, .04, .68, f"{edge}  ·  {n}", size=11, color=ct.WARNING)
    score = f"Flow score {v['score']:.1f}/100" if v["score"] is not None else (
        f"Setup score {v['setup_score']:g} (native scale)" if v["setup_score"] is not None else "Score not recorded")
    spread = f"Spread {v['spread'] * 100:.2f}%" if v["spread"] is not None else "Spread unknown"
    _text(evidence, .04, .47, f"{score}   ·   {spread}", size=10)
    if v["score"] is not None and 0 <= v["score"] <= 100:
        for start, end, color in [(0, 60, ct.BEARISH), (60, 85, ct.BULLISH), (85, 100, ct.WARNING)]:
            evidence.plot([.04 + .92 * start / 100, .04 + .92 * end / 100], [.35, .35],
                          color=color, linewidth=4, alpha=.7)
        evidence.plot(.04 + .92 * v["score"] / 100, .35, "o", color=ct.TEXT_PRIMARY, markersize=6)
    reason = textwrap.fill(textwrap.shorten(v["reason"], width=105, placeholder="…"), width=76)
    _text(evidence, .04, .25, reason, size=9, color=ct.TEXT_MUTED)
    source = textwrap.shorten(v["source"], width=18, placeholder="…")
    fig.text(.045, .033, f"{v['timestamp']}  ·  Expiry {v['expiry']}  ·  {source}", color=ct.TEXT_MUTED, fontsize=9)
    fig.text(.955, .014, "Setup ≠ fill. Stored observations, not a live quote.", ha="right", color=ct.TEXT_MUTED, fontsize=8)
    return _save(fig, output_dir, "option_signal", (v, observed))


def render_option_board(rows: list, *, day: str, output_dir: str | None = None) -> str:
    """Four full-width contract tiles instead of a tiny monospace table."""
    shown = rows[:4]
    views = [signal_view(dict(row)) for row in shown]
    fig = _canvas(8, 10.5)
    header = _panel(fig, [.045, .83, .91, .13])
    _text(header, .04, .87, "OPTION DESK / SIGNAL BOARD", size=11, color=ct.INFO, weight="bold")
    _text(header, .04, .61, day, size=25, color=ct.TEXT_PRIMARY, weight="bold")
    setups = sum(bool(row.get("tradable")) for row in shown)
    _text(header, .04, .22, f"Showing {len(shown)} contracts  ·  {setups} gate-passed  ·  {len(shown) - setups} watch", size=11)
    if not views:
        ax = _panel(fig, [.045, .25, .91, .48])
        _text(ax, .5, .60, "No option setups recorded", size=20, color=ct.TEXT_PRIMARY, ha="center")
        _text(ax, .5, .43, "The board updates as stored signals arrive.", size=12, color=ct.TEXT_MUTED, ha="center")
    for index, v in enumerate(views):
        ax = _panel(fig, [.045, .637 - index * .184, .91, .164])
        _text(ax, .035, .89, textwrap.shorten(v["contract"], width=37, placeholder="…"), size=17, color=ct.TEXT_PRIMARY, weight="bold")
        _text(ax, .965, .87, v["status"], size=9, color=_status_color(v), ha="right", weight="bold")
        _text(ax, .035, .69, f"{v['direction']}  ·  {v['lifecycle']}  ·  Expiry {v['expiry']}", size=9, color=ct.TEXT_MUTED)
        for col, (label, value, color) in enumerate([
            ("ENTRY", v["entry"], ct.INFO), ("SL", v["stop"], ct.BEARISH),
            ("T1", v["target"], ct.BULLISH), ("T2", v["target2"], ct.CATEGORICAL[2])]):
            _text(ax, .035 + col * .245, .52, label, size=8, color=color, weight="bold")
            _text(ax, .035 + col * .245, .41, money(value), size=14, color=ct.TEXT_PRIMARY, weight="bold")
        edge = textwrap.shorten(v["edge"].replace("_", " "), width=26, placeholder="…")
        score = f"Score {v['score']:.1f}" if v["score"] is not None else "Score —"
        _text(ax, .035, .17, f"{score}  ·  {edge}", size=9, color=ct.WARNING)
        stamp = _time(v["snapshot_time"])
        _text(ax, .965, .17, f"Snapshot {stamp.strftime('%H:%M IST')}" if stamp else "Time unknown",
              size=9, color=ct.TEXT_MUTED, ha="right")
    fig.text(.045, .039, "WATCH = blocked / observation only. Gate passed ≠ order placed.", color=ct.TEXT_MUTED, fontsize=9)
    fig.text(.045, .018, "Use Dashboard · Signals · OI chart · Positions in the buttons below.", color=ct.TEXT_MUTED, fontsize=9)
    return _save(fig, output_dir, "option_board", (day, views))
