"""
trade_guardian_bot.py — Telegram Bot for Trade Guardian

SEPARATE bot from main system bot. Get a new token from @BotFather.
Set in trade_guardian.yaml:
  telegram:
    bot_token: "YOUR_TOKEN"
    chat_id: "YOUR_CHAT_ID"

OR set environment variables:
  GUARDIAN_BOT_TOKEN=...
  GUARDIAN_CHAT_ID=...

─────────────────────────────────────────────────────────────────────────────
COMMANDS:
  /in <symbol> <type> <side> <price> <qty> [options]
      Register a new trade.
      Examples:
        /in NIFTY OPTIONS BUY 150 1 CE 22200 weekly
        /in BANKNIFTY OPTIONS SELL 200 1 PE 48000 weekly
        /in NIFTY FUTURES BUY 22250 75
        /in RELIANCE STOCK BUY 2890 50
        /in INFY STOCK BUY 1450 100 positional

  /out [trade_id] [price]
      Close a trade (mental note — does not place an order).
        /out                    (if only one trade open)
        /out NIFTY_142305 2890

  /sl <value> [trade_id]
      Manually override stop loss.
        /sl 120                 (if one trade open)
        /sl 22000 NIFTY_142305

  /target <value> [trade_id] [level]
      Override target level.
        /target 250             (sets T1)
        /target 300 NIFTY_142305 2    (sets T2)

  /protect [pct] [trade_id]
      Move SL to protect pct% of current profit.
        /protect                → protect 50% of profit
        /protect 70             → protect 70%

  /status
      Show all open trades with live P&L, SL, targets, regime.

  /trades [n]
      Show last n closed trades (default 5).

  /performance
      Today's P&L summary across all trades.

  /hold [trade_id]
      Tell system you're holding — suppress exit suggestions for 15 min.

  /signal [symbol]
      Get current signal score and regime for a symbol.

  /settings
      Show current trade_guardian.yaml settings summary.

  /help
      Show this help.
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_YAML_PATH = Path("trade_guardian.yaml")

# ── Globals (set at startup) ──────────────────────────────────────────────────
_guardian = None     # TradeGuardian instance
_bot_token = ""
_chat_id   = ""
_running   = False
_offset    = 0
_hold_until: Dict[str, float] = {}   # {trade_id: timestamp until hold expires}


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_cfg() -> Dict:
    try:
        return yaml.safe_load(_YAML_PATH.read_text()) or {}
    except Exception:
        return {}


def _token() -> str:
    t = os.getenv("GUARDIAN_BOT_TOKEN", "")
    if not t:
        t = _get_cfg().get("telegram", {}).get("bot_token", "")
    return t


def _chat() -> str:
    c = os.getenv("GUARDIAN_CHAT_ID", "")
    if not c:
        c = _get_cfg().get("telegram", {}).get("chat_id", "")
    return str(c)


def _is_authorized_message(message: Dict) -> bool:
    """Allow Guardian commands only from its configured Telegram owner.

    A Guardian command can change the tracked stop, target, or status of a
    manual position.  Receiving a Telegram update is therefore never itself
    sufficient authority.  Fail closed when the owner chat has not been
    configured; this avoids turning initial bot setup into an open command
    channel.
    """
    configured_chat = _chat().strip()
    if not configured_chat:
        logger.warning("Rejected Guardian command: GUARDIAN_CHAT_ID is not configured")
        return False

    chat_id = str((message.get("chat") or {}).get("id", "")).strip()
    from_id = str((message.get("from") or {}).get("id", "")).strip()
    owner_id = configured_chat.lstrip("-")
    return (
        chat_id == configured_chat
        or from_id == configured_chat
        or from_id == owner_id
        or chat_id.lstrip("-") == owner_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Telegram API
# ─────────────────────────────────────────────────────────────────────────────

def _api(method: str, **params) -> Dict:
    token = _token()
    if not token:
        return {}
    url  = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug("Telegram API %s: %s", method, e)
        return {}


def send(text: str, chat_id: str = "") -> bool:
    """Send a message to the guardian channel."""
    cid = chat_id or _chat()
    if not cid:
        logger.warning("Guardian bot: no chat_id configured")
        return False
    result = _api("sendMessage", chat_id=cid, text=text,
                  parse_mode="HTML", disable_web_page_preview=True)
    return bool(result.get("ok"))


def send_photo(path: str, caption: str = "", chat_id: str = "") -> bool:
    """Send an image (sendPhoto, multipart) to the guardian channel."""
    cid = chat_id or _chat()
    token = _token()
    if not cid or not token:
        return False
    try:
        import requests
        with open(path, "rb") as fh:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
                files={"photo": fh}, timeout=20)
        return bool(r.ok)
    except Exception as e:
        logger.debug("send_photo: %s", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Command parsers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_trade(t) -> str:
    """Format a GuardedTrade for Telegram display."""
    pnl_emoji = "🟢" if t.pnl >= 0 else "🔴"
    be_icon   = "🔒" if t.breakeven_activated else ""
    t1_icon   = "✅" if t.t1_hit else ""
    lot_info  = f"{t.qty}L×{t.lot_size}" if t.lot_size > 1 else f"{t.qty}"

    wow_icon = "🟢" if t.wow_score > 0.5 else "🔴" if t.wow_score < -0.5 else "⚪"
    lines = [
        f"<b>{t.symbol}</b> {t.option_type or ''} {t.strike or ''} "
        f"| {t.side} {lot_info} @ ₹{t.entry_price:.2f}",
        f"CMP: ₹{t.current_price:.2f} {pnl_emoji} ₹{t.pnl:+,.0f} ({t.pnl_pct:+.1f}%) "
        f"R={t.r_multiple:.2f}",
        f"SL: ₹{t.trailing_sl:.2f}{be_icon}  |  T1: ₹{t.target_1:.2f}{t1_icon}  "
        f"|  T2: ₹{t.target_2:.2f}",
        f"Signal: {t.signal_score:.1f}/10 {t.signal_direction or ''}  |  "
        f"WOW: {wow_icon}{t.wow_score:+.2f} {t.wow_verdict}  |  {t.regime}",
    ]
    if t.wow_reasons:
        top_wow = t.wow_reasons.split(" | ")[0]
        lines.append(f"<i>WOW: {top_wow[:80]}</i>")
    elif t.narrative:
        lines.append(f"<i>{t.narrative[:100]}</i>")
    if t.notes:
        lines.append(f"📝 {t.notes}")
    return "\n".join(lines)


def _cmd_in(args: str) -> str:
    """
    /in <symbol> <instrument> <side> <price> <qty> [option_type] [strike] [expiry] [positional]

    Examples:
      /in NIFTY OPTIONS BUY 150 1 CE 22200 weekly
      /in NIFTY FUTURES BUY 22250 75
      /in RELIANCE STOCK BUY 2890 50
      /in INFY STOCK BUY 1450 100 positional
    """
    if not _guardian:
        return "Guardian not initialised."
    parts = args.split()
    if len(parts) < 5:
        return (
            "Usage: /in <symbol> <instrument> <side> <price> <qty> [CE/PE] [strike] [expiry]\n"
            "Example: /in NIFTY OPTIONS BUY 150 1 CE 22200 weekly\n"
            "         /in NIFTY FUTURES BUY 22250 75\n"
            "         /in TCS STOCK BUY 3500 10"
        )
    try:
        symbol     = parts[0].upper()
        instrument = parts[1].upper()
        side       = parts[2].upper()
        price      = float(parts[3])
        qty        = int(parts[4])

        option_type = ""
        strike      = 0.0
        expiry      = "weekly"
        is_pos      = False
        manual_sl   = 0.0
        manual_tgt  = 0.0

        # Parse optional fields
        remaining = parts[5:]
        i = 0
        while i < len(remaining):
            tok = remaining[i].upper()
            if tok in ("CE", "PE"):
                option_type = tok
            elif tok == "POSITIONAL":
                is_pos = True
            elif tok in ("WEEKLY", "MONTHLY"):
                expiry = tok.lower()
            elif tok.startswith("SL="):
                manual_sl = float(tok[3:])
            elif tok.startswith("T1="):
                manual_tgt = float(tok[3:])
            else:
                try:
                    # Could be strike price
                    v = float(tok)
                    if v > 1000 and not strike:
                        strike = v
                    elif v < 1000 and not manual_sl:
                        manual_sl = v
                except ValueError:
                    expiry = tok.lower()
            i += 1

        result = _guardian.register_trade(
            symbol=symbol, instrument=instrument, side=side,
            entry_price=price, qty=qty, option_type=option_type,
            strike=strike, expiry=expiry, is_positional=is_pos,
            manual_sl=manual_sl, manual_target=manual_tgt,
        )

        trade = result["trade"]
        intel = result["signal"]
        fomo  = result.get("fomo_warn")

        lot_sz   = trade.lot_size
        units    = qty * lot_sz
        risk_inr = trade.total_risk_inr()
        t1_inr   = abs(trade.target_1 - price) * units
        t2_inr   = abs(trade.target_2 - price) * units

        # Partial booking message that works for qty=1
        if qty == 1:
            partial_note = "1 lot — can't split; trail SL at T1 instead of partial exit"
        else:
            partial_note = f"Book {max(1,qty//2)} at T1, hold {qty - max(1,qty//2)} for T2"

        msg = [
            f"✅ <b>TRADE REGISTERED</b>",
            f"",
            f"<b>{symbol}</b> {option_type or ''} {strike or ''} | "
            f"{side} {qty} lot{'s' if qty>1 else ''} @ ₹{price:.2f}",
            f"Instrument: {instrument} | Expiry: {expiry}",
            f"Lot size: {lot_sz} units/lot | Total units: {units}",
            f"",
            f"📊 <b>Computed Levels:</b>",
            f"  🛑 Stop Loss:  ₹{trade.stop_loss:.2f}  (max loss ₹{risk_inr:,.0f})",
            f"  🎯 Target 1:  ₹{trade.target_1:.2f}  (profit ₹{t1_inr:,.0f})",
            f"  🎯 Target 2:  ₹{trade.target_2:.2f}  (profit ₹{t2_inr:,.0f})",
            f"  🎯 Target 3:  ₹{trade.target_3:.2f}",
            f"  📍 Breakeven: ₹{trade.breakeven_price:.2f}",
            f"  R:R = 1:{t1_inr/risk_inr:.1f} (T1)  |  1:{t2_inr/risk_inr:.1f} (T2)",
            f"",
            f"💡 Partial booking: {partial_note}",
            f"",
            f"🧠 <b>Market Intelligence:</b>",
            f"  Regime:  {intel.get('regime','?')}",
            f"  Signal:  {intel.get('score',0):.1f}/10  {intel.get('direction','')}",
            f"  VIX:     {trade.vix:.1f}",
        ]
        # WOW factors block
        wow = result.get("wow", {})
        if wow:
            ws  = float(wow.get("wow_score", 0))
            wv  = str(wow.get("verdict", "NEUTRAL"))
            wr  = wow.get("reasons", [])[:3]
            wow_icon = "🟢" if ws > 0.5 else "🔴" if ws < -0.5 else "⚪"
            msg += [
                f"",
                f"⚡ <b>WOW Factors:</b> {wow_icon} {ws:+.2f}  [{wv}]",
            ]
            for r in wr:
                msg += [f"  • {r[:70]}"]
        if trade.narrative:
            msg += ["", f"<i>{trade.narrative[:150]}</i>"]
        if fomo:
            msg += ["", f"⚠️ <b>SYSTEM WARNING:</b> {fomo}"]
        msg += [f"", f"ID: <code>{trade.trade_id}</code>"]
        return "\n".join(msg)

    except Exception as e:
        logger.exception("_cmd_in: %s", e)
        return f"Error registering trade: {e}"


def _cmd_out(args: str) -> str:
    """Close a trade. /out [trade_id] [price]"""
    if not _guardian:
        return "Guardian not initialised."
    parts    = args.split()
    trades   = _guardian.get_open_trades()

    if not trades:
        return "No open trades."

    # Resolve trade_id
    trade_id = None
    price    = 0.0
    if len(trades) == 1 and (not parts or not any("_" in p for p in parts)):
        trade_id = trades[0].trade_id
        price    = trades[0].current_price
    else:
        for p in parts:
            if "_" in p:
                trade_id = p
            else:
                try: price = float(p)
                except ValueError: pass

    if not trade_id:
        lines = ["Multiple trades open. Specify trade ID:"]
        for t in trades:
            lines.append(f"  <code>{t.trade_id}</code> — {t.symbol} {t.side} "
                         f"₹{t.pnl:+,.0f} ({t.pnl_pct:+.1f}%)")
        return "\n".join(lines)

    t = next((x for x in trades if x.trade_id == trade_id), None)
    if not t:
        return f"Trade {trade_id} not found."

    ep = price or t.current_price or t.entry_price
    _guardian.close_trade(trade_id, ep, reason="manual_close")
    pnl = (ep - t.entry_price) * t.qty if t.side == "SELL" else (ep - t.entry_price) * t.qty
    return (
        f"{'🟢' if pnl >= 0 else '🔴'} <b>TRADE CLOSED</b>\n"
        f"{t.symbol} {t.side} @ ₹{ep:.2f}\n"
        f"P&L: ₹{pnl:+,.0f}"
    )


def _cmd_sl(args: str) -> str:
    """Override stop loss. /sl <value> [trade_id]"""
    if not _guardian:
        return "Guardian not initialised."
    parts  = args.split()
    trades = _guardian.get_open_trades()
    if not trades:
        return "No open trades."

    sl_val   = None
    trade_id = None
    for p in parts:
        if "_" in p:
            trade_id = p
        else:
            try: sl_val = float(p)
            except ValueError: pass

    if sl_val is None:
        return "Usage: /sl <value> [trade_id]"

    if not trade_id and len(trades) == 1:
        trade_id = trades[0].trade_id
    elif not trade_id:
        return "Multiple trades open. Specify: /sl 120 <trade_id>"

    if _guardian.update_sl(trade_id, sl_val):
        t = next((x for x in trades if x.trade_id == trade_id), None)
        return (
            f"✅ Stop loss updated\n"
            f"{t.symbol if t else trade_id}: SL → ₹{sl_val:.2f}"
        )
    return "Trade not found."


def _cmd_target(args: str) -> str:
    """Override target. /target <value> [trade_id] [level]"""
    if not _guardian:
        return "Guardian not initialised."
    parts  = args.split()
    trades = _guardian.get_open_trades()
    if not trades:
        return "No open trades."

    tgt_val  = None
    trade_id = None
    level    = 1
    for p in parts:
        if "_" in p:
            trade_id = p
        elif p.isdigit() and int(p) in (1, 2, 3):
            level = int(p)
        else:
            try: tgt_val = float(p)
            except ValueError: pass

    if tgt_val is None:
        return "Usage: /target <value> [trade_id] [1/2/3]"

    if not trade_id and len(trades) == 1:
        trade_id = trades[0].trade_id
    elif not trade_id:
        return "Multiple trades open. Specify: /target 250 <trade_id>"

    if _guardian.update_target(trade_id, tgt_val, level):
        t = next((x for x in trades if x.trade_id == trade_id), None)
        return f"✅ T{level} updated\n{t.symbol if t else trade_id}: T{level} → ₹{tgt_val:.2f}"
    return "Trade not found."


def _cmd_protect(args: str) -> str:
    """/protect [pct] [trade_id] — protect pct% of current profit"""
    if not _guardian:
        return "Guardian not initialised."
    parts  = args.split()
    trades = _guardian.get_open_trades()
    if not trades:
        return "No open trades."

    pct      = 50.0
    trade_id = None
    for p in parts:
        if "_" in p:
            trade_id = p
        else:
            try:
                v = float(p.rstrip("%"))
                if 1 <= v <= 100:
                    pct = v
            except ValueError: pass

    if not trade_id and len(trades) == 1:
        trade_id = trades[0].trade_id
    elif not trade_id:
        return "Multiple trades open. Specify: /protect 70 <trade_id>"

    msg = _guardian.protect_profit(trade_id, pct)
    return f"🔒 {msg}" if msg else "Error."


def _cmd_status(args: str = "") -> str:
    """Show all open trades with live P&L."""
    if not _guardian:
        return "Guardian not initialised."
    trades = _guardian.get_open_trades()
    if not trades:
        return "📭 No open trades being monitored.\nUse /in to register a trade."

    lines = [f"📊 <b>OPEN TRADES ({len(trades)})</b>\n"]
    total_pnl = 0
    for t in trades:
        lines.append(_fmt_trade(t))
        lines.append("")
        total_pnl += t.pnl

    lines.append(f"<b>Total P&L: ₹{total_pnl:+,.0f}</b>")
    return "\n".join(lines)


def _cmd_trades(args: str = "") -> str:
    """Show last N closed trades."""
    n = 5
    try:
        n = int(args.strip()) if args.strip() else 5
    except ValueError:
        pass
    try:
        conn = sqlite3.connect("trade_guardian.db")
        rows = conn.execute(
            "SELECT symbol, instrument, side, entry_price, exit_price, pnl, pnl_pct, "
            "exit_reason, exit_time FROM guarded_trades WHERE status='CLOSED' "
            "ORDER BY exit_time DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
    except Exception:
        return "No trade history available."

    if not rows:
        return "No closed trades yet."

    lines = [f"📜 <b>Last {len(rows)} Closed Trades</b>\n"]
    total = 0
    for r in rows:
        sym, instr, side, ep, xp, pnl, pct, reason, ts = r
        em = "🟢" if (pnl or 0) >= 0 else "🔴"
        lines.append(
            f"{em} {sym} {side} @ ₹{ep:.2f} → ₹{xp:.2f} "
            f"| ₹{(pnl or 0):+,.0f} ({(pct or 0):+.1f}%) | {reason or '—'}"
        )
        total += (pnl or 0)
    lines.append(f"\n<b>Total: ₹{total:+,.0f}</b>")
    return "\n".join(lines)


def _cmd_performance(args: str = "") -> str:
    """Today's performance summary."""
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect("trade_guardian.db")
        rows = conn.execute(
            "SELECT pnl, pnl_pct, symbol, side FROM guarded_trades "
            "WHERE status='CLOSED' AND date(exit_time)=?", (today,)
        ).fetchall()
        open_rows = conn.execute(
            "SELECT pnl, symbol, side FROM guarded_trades WHERE status='OPEN'"
        ).fetchall()
        conn.close()
    except Exception:
        return "Performance data unavailable."

    closed_pnl = sum(r[0] or 0 for r in rows)
    open_pnl   = sum(r[0] or 0 for r in open_rows)
    wins  = sum(1 for r in rows if (r[0] or 0) > 0)
    total = len(rows)

    lines = [
        f"📈 <b>Today's Performance — {today}</b>\n",
        f"Closed trades: {total} | Wins: {wins} | "
        f"Win rate: {wins/total*100:.0f}%" if total else "Closed: 0",
        f"Closed P&L:  ₹{closed_pnl:+,.0f}",
        f"Open P&L:    ₹{open_pnl:+,.0f}",
        f"Total P&L:   ₹{closed_pnl + open_pnl:+,.0f}",
    ]
    if rows:
        lines.append("\nClosed:")
        for r in rows:
            em = "🟢" if (r[0] or 0) >= 0 else "🔴"
            lines.append(f"  {em} {r[2]} {r[3]}: ₹{(r[0] or 0):+,.0f}")
    return "\n".join(lines)


def _cmd_signal(args: str = "") -> str:
    """Get live signal for a symbol. /signal NIFTY"""
    from trade_guardian import _get_signal_intelligence
    symbol = args.strip().upper() or "NIFTY"
    intel  = _get_signal_intelligence(symbol)
    score  = float(intel.get("score", 0))
    regime = intel.get("regime", "UNKNOWN")
    direction = intel.get("direction", "—")
    narrative = intel.get("narrative", "")

    score_bar = "█" * int(score) + "░" * (10 - int(score))
    return (
        f"🔍 <b>Signal: {symbol}</b>\n"
        f"Score: {score:.1f}/10 [{score_bar}]\n"
        f"Direction: {direction}\n"
        f"Regime: {regime}\n"
        f"VIX: {intel.get('vix', '—')}\n"
        + (f"\n<i>{narrative}</i>" if narrative else "")
    )


def _cmd_wow(args: str = "") -> str:
    """
    Full WOW factor breakdown for a symbol or active trade.
    /wow              → run on all open trade symbols
    /wow NIFTY        → run on NIFTY
    /wow BANKNIFTY BUY → run on BANKNIFTY with BUY direction
    """
    from trade_guardian import _get_wow_intelligence

    # Determine symbol + direction
    parts  = args.split()
    trades = _guardian.get_open_trades() if _guardian else []

    targets = []  # [(symbol, direction)]
    if parts:
        sym  = parts[0].upper()
        dirn = parts[1].upper() if len(parts) > 1 else "BUY"
        targets = [(sym, dirn)]
    elif trades:
        targets = [(t.symbol, t.side) for t in trades]
    else:
        targets = [("NIFTY", "BUY")]

    lines = []
    for sym, dirn in targets[:3]:   # max 3 symbols per call
        wow = _get_wow_intelligence(sym, dirn)
        ws  = float(wow.get("wow_score", 0))
        wv  = wow.get("verdict", "NEUTRAL")
        pcr = float(wow.get("pcr", 1.0))
        reasons = wow.get("reasons", [])
        factors = wow.get("factors", {})
        wow_icon = "🟢" if ws > 0.5 else "🔴" if ws < -0.5 else "⚪"

        lines += [
            f"⚡ <b>WOW FACTORS — {sym} {dirn}</b>",
            f"  {wow_icon} Total Score: <b>{ws:+.2f}</b>  [{wv}]",
            f"  PCR: {pcr:.2f}  |  OI Signal: {wow.get('oi_signal','—')}",
            "",
        ]
        # Individual factors
        factor_lines = []
        for fname, fval in sorted(factors.items(), key=lambda x: abs(x[1]), reverse=True)[:8]:
            fval = float(fval)
            icon = "🟢" if fval > 0.1 else "🔴" if fval < -0.1 else "⚪"
            factor_lines.append(f"  {icon} {fname.replace('_',' ').title()}: {fval:+.3f}")
        lines += factor_lines

        if reasons:
            lines += ["", "  <b>Key Signals:</b>"]
            for r in reasons[:5]:
                lines.append(f"  • {r[:75]}")
        lines += [""]

    return "\n".join(lines).strip() if lines else "No WOW data available."


def _cmd_hold(args: str = "") -> str:
    """Suppress exit suggestions for 15 minutes. /hold [trade_id]"""
    global _hold_until
    trades = _guardian.get_open_trades() if _guardian else []
    trade_id = args.strip() or (trades[0].trade_id if len(trades) == 1 else None)
    if not trade_id:
        return "Specify trade ID: /hold <trade_id>"
    _hold_until[trade_id] = time.time() + 900  # 15 min
    return f"⏸ Hold mode: exit suggestions suppressed for 15 min on {trade_id}"


def _cmd_settings(args: str = "") -> str:
    """Show current YAML settings summary."""
    cfg = _get_cfg()
    opt = cfg.get("options", {})
    fut = cfg.get("futures", {})
    se  = cfg.get("signal_engine", {})
    fg  = cfg.get("fomo_guard", {})

    return (
        f"⚙️ <b>Trade Guardian Settings</b>\n\n"
        f"<b>Options:</b>\n"
        f"  SL: {opt.get('initial_sl_pct',30)}% | "
        f"T1: +{opt.get('target_1_pct',50)}% | "
        f"T2: +{opt.get('target_2_pct',100)}%\n"
        f"  Spike alert: +{opt.get('spike_alert_pct',35)}% in 5 min\n"
        f"  Break-even at: +{opt.get('breakeven_at_pct',20)}%\n\n"
        f"<b>Futures:</b>\n"
        f"  SL: {fut.get('initial_sl_atr_mult',1.5)}×ATR | "
        f"T1: {fut.get('target_1_rr',1.5)}R | T2: {fut.get('target_2_rr',3.0)}R\n\n"
        f"<b>Signal Engine:</b>\n"
        f"  Exit if score < {se.get('suggest_exit_score_below',3.0)}\n"
        f"  Hold if score > {se.get('hold_if_score_above',5.0)}\n"
        f"  Extend target if score > {se.get('extend_target_score_above',7.5)}\n\n"
        f"<b>FOMO Guard:</b> {'ON' if fg.get('enabled',True) else 'OFF'}\n"
        f"  Warn if score < {fg.get('warn_if_score_below',4.0)}\n\n"
        f"Edit <code>trade_guardian.yaml</code> to change settings."
    )


def _cmd_help(args: str = "") -> str:
    return (
        "🤖 <b>Trade Guardian Commands</b>\n\n"
        "<b>Register trade:</b>\n"
        "  /in NIFTY OPTIONS BUY 150 1 CE 22200 weekly\n"
        "  /in NIFTY FUTURES BUY 22250 75\n"
        "  /in TCS STOCK BUY 3500 10\n\n"
        "<b>Manage trades:</b>\n"
        "  /out [trade_id] [price] — close trade\n"
        "  /sl 120 — override stop loss\n"
        "  /target 250 — override target\n"
        "  /protect 70 — lock 70% of profit\n"
        "  /hold [trade_id] — suppress alerts 15 min\n\n"
        "<b>Information:</b>\n"
        "  /manual — auto-detected manual trades (image cards)\n"
        "  /status — open trades + live P&L + WOW score\n"
        "  /trades [n] — last n closed trades\n"
        "  /performance — today's P&L\n"
        "  /signal NIFTY — live signal score + regime\n"
        "  /wow [SYMBOL] — full WOW factor breakdown (22 factors)\n"
        "  /settings — current config\n\n"
        "<b>YAML config:</b> trade_guardian.yaml\n"
        "<b>Set tokens:</b> GUARDIAN_BOT_TOKEN + GUARDIAN_CHAT_ID in .env"
    )


def _cmd_manual(args: str = "") -> Optional[str]:
    """Show auto-detected manual trades (from the tracker) as image cards."""
    import sqlite3
    try:
        conn = sqlite3.connect("manual_trades.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM manual_trades WHERE status='OPEN' "
            "ORDER BY id DESC").fetchall()
        conn.close()
    except Exception as e:
        return f"Could not read manual trades: {e}"
    if not rows:
        return "📭 No open manual trades detected."

    try:
        from trade_card import render_trade_card
    except Exception as e:
        return f"Card renderer unavailable: {e}"

    sent = 0
    for i, r in enumerate(rows):
        d = dict(r)
        entry = float(d.get("entry_price") or 0)
        ltp   = float(d.get("current_price") or 0) or entry
        try:
            path = render_trade_card(
                symbol=d["symbol"], side=d.get("side", "BUY"),
                qty=int(d.get("qty") or 0), entry=entry, ltp=ltp,
                sl=float(d.get("stop_loss") or 0),
                target=float(d.get("target_1") or 0),
                pnl=float(d.get("pnl") or 0),
                pnl_pct=float(d.get("pnl_pct") or 0),
                out_path=f"guardian_manual_{i}.png",
                extra="GTT SL+target at broker" if d.get("sl_gtt_id") else "")
            send_photo(path, f"📊 <b>{d['symbol']}</b> — {d.get('side')} {d.get('qty')}")
            sent += 1
        except Exception as e:
            logger.debug("manual card %s: %s", d.get("symbol"), e)
    return None if sent else "Could not render manual trade cards."


# ─────────────────────────────────────────────────────────────────────────────
# Command dispatcher
# ─────────────────────────────────────────────────────────────────────────────

_COMMANDS = {
    "manual":      _cmd_manual,
    "m":           _cmd_manual,
    "in":          _cmd_in,
    "out":         _cmd_out,
    "sl":          _cmd_sl,
    "target":      _cmd_target,
    "protect":     _cmd_protect,
    "status":      _cmd_status,
    "trades":      _cmd_trades,
    "performance": _cmd_performance,
    "perf":        _cmd_performance,
    "signal":      _cmd_signal,
    "wow":         _cmd_wow,
    "hold":        _cmd_hold,
    "settings":    _cmd_settings,
    "help":        _cmd_help,
    "start":       _cmd_help,
}


def _dispatch(text: str) -> Optional[str]:
    """Parse and dispatch a Telegram command. Returns response string or None."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts   = text[1:].split(None, 1)
    cmd     = parts[0].lower().split("@")[0]  # strip bot username if present
    args    = parts[1] if len(parts) > 1 else ""
    handler = _COMMANDS.get(cmd)
    if not handler:
        return f"Unknown command: /{cmd}\nSend /help for available commands."
    try:
        return handler(args)
    except Exception as e:
        logger.exception("Command /%s error: %s", cmd, e)
        return f"Error in /{cmd}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Poll loop
# ─────────────────────────────────────────────────────────────────────────────

def _poll_loop() -> None:
    global _offset
    while _running:
        token = _token()
        if not token:
            time.sleep(10)
            continue
        try:
            resp = _api("getUpdates", offset=_offset, timeout=20,
                        allowed_updates=["message"])
            updates = resp.get("result", [])
            for update in updates:
                _offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                if text and _is_authorized_message(msg):
                    response = _dispatch(text)
                    if response:
                        send(response)
                elif text:
                    logger.warning(
                        "Rejected Guardian command from chat=%s user=%s",
                        (msg.get("chat") or {}).get("id", ""),
                        (msg.get("from") or {}).get("id", ""),
                    )
        except Exception as e:
            logger.debug("poll_loop: %s", e)
            time.sleep(5)
        time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    """Start the Trade Guardian bot. Runs until KeyboardInterrupt."""
    global _guardian, _running

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    logger.info("Trade Guardian Bot starting...")

    # Check config
    token = _token()
    chat  = _chat()
    if not token:
        logger.error(
            "No GUARDIAN_BOT_TOKEN found.\n"
            "1. Create a new bot via @BotFather\n"
            "2. Set GUARDIAN_BOT_TOKEN in .env\n"
            "   OR set telegram.bot_token in trade_guardian.yaml"
        )
        return
    if not chat:
        logger.warning(
            "No GUARDIAN_CHAT_ID found. Send any message to your bot, "
            "then check /getUpdates to find your chat_id."
        )

    # Initialise guardian with send function
    from trade_guardian import TradeGuardian
    _guardian = TradeGuardian(send_fn=send)
    _guardian.start()

    # Start polling
    _running = True
    poll_thread = threading.Thread(target=_poll_loop, daemon=True, name="GuardianPoll")
    poll_thread.start()

    send(
        "🤖 <b>Trade Guardian Online</b>\n"
        "Ready to manage your manual trades.\n"
        "Send /help for commands."
    )
    logger.info("Trade Guardian Bot running. Token: %s...%s", token[:8], token[-4:])

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down Trade Guardian Bot...")
        _running = False
        if _guardian:
            _guardian.stop()


if __name__ == "__main__":
    run()
