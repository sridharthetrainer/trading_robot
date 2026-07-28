"""
alerts.py — Telegram Alert Manager

All messages sent to Telegram.
Dedup persisted to disk — survives restarts.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_TG_PARSE_HTML = "HTML"
_TG_MAX_LEN    = 4096
_B = "█"; _E = "░"

# ── Constants for April 2026 ──────────────────────────────────────────────────
COMMANDS_BLOCK = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📱 <b>TELEGRAM COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📊 <b>Monitor</b>
    /status    — P&L + open positions
    /pnl       — trade-by-trade detail
    /positions — live positions
    /signals   — last 5 signals
    /risk      — VaR + exposure
    /heat      — portfolio correlation

  🌅 <b>Morning</b>
    /morning   — system readiness + swing setups
    /vix       — VIX + option filter
    /schedule  — today's timeline
    /regime    — market regime + strategy weights

  🔧 <b>Control</b>
    /pause     — stop new entries
    /resume    — resume entries
    /restart   — restart bot
    /kill      — emergency exit ALL
    /mode      — current mode (paper/live)

  📈 <b>OI & Options</b>
    /oi        — OI Builder: strike snapshot + guide
    /oi BANKNIFTY — OI for BANKNIFTY
    /oitrend   — Intraday OI buildup chart
    /oitrend BANKNIFTY

  🧠 <b>Analytics</b>
    /bt        — run backtest now
    /train     — run ML training
    /ml        — ML model performance
    /weekly    — weekly P&L report
    /calibrate — score→win rate calibration
    /stt       — April 2026 STT breakeven table

  🔌 <b>System</b>
    /health    — all 14 connection feeds
    /connections — trigger fresh connection check
    /log       — last 20 system log lines
    /state     — system state machine

  ☁️ <b>Drive Sync</b>
    /sync      — sync with Google Drive
    /deploy    — pull from Drive + restart
    /cloud     — Drive sync status
    /github    — push code to GitHub
    /backup    — full backup (Drive+GitHub+Telegram)
    /fii       — FII/DII pattern analysis
    /datasources — all data source health

  ℹ️ /help — full command list
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


class AlertManager:
    def __init__(
        self,
        bot_token:  str  = "",
        chat_id:    str  = "",
        enabled:    bool = True,
        parse_mode: str  = _TG_PARSE_HTML,
        dedup_ttl:  int  = 300,
        name:       str  = "",
    ) -> None:
        self.bot_token   = bot_token
        self.chat_id     = chat_id
        self.enabled     = enabled and bool(bot_token) and bool(chat_id)
        self.parse_mode  = parse_mode
        self.dedup_ttl   = dedup_ttl
        # `name` isolates dedup/spool state so a second channel (e.g. a separate
        # option-bot token/chat) does not share — and cross-deliver — the main
        # channel's spool. Default "" keeps the original filenames unchanged.
        _sfx = f"_{name}" if name else ""
        self._dedup_file = Path(f"dedup_state{_sfx}.json")
        self._dedup_sent: Dict[str, float] = self._load_dedup()
        self._spool_file = Path(f"telegram_spool{_sfx}.jsonl")
        self._media_spool_dir = Path(f".telegram_media_spool{_sfx}")
        self._last_spool_flush = 0.0

    # ── Dedup (persisted to disk) ─────────────────────────────────────────────
    def _load_dedup(self) -> Dict[str, float]:
        try:
            if self._dedup_file.exists():
                import json as _j
                data = _j.loads(self._dedup_file.read_text())
                cutoff = time.time() - 86400
                return {k: v for k, v in data.items()
                        if isinstance(v, (int, float)) and v > cutoff}
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        return {}

    def _save_dedup(self) -> None:
        try:
            import json as _j
            self._dedup_file.write_text(_j.dumps(self._dedup_sent))
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    def _is_dedup_blocked(self, key: str, cooldown: int) -> bool:
        return (time.time() - self._dedup_sent.get(key, 0)) < cooldown

    def _mark_dedup_sent(self, key: str) -> None:
        self._dedup_sent[key] = time.time()
        if len(self._dedup_sent) > 500:
            cutoff = time.time() - 86400
            self._dedup_sent = {k: v for k, v in self._dedup_sent.items() if v > cutoff}
        self._save_dedup()

    def _post_telegram(self, data: Dict[str, Any], timeout: int = 10) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for attempt in range(3):
            # Run the HTTP call in a daemon thread joined with a HARD wall-clock
            # bound. requests' own `timeout` does NOT cover DNS resolution
            # (getaddrinfo), so a broken resolver — e.g. just after a
            # suspend/resume — can hang requests.post() for many minutes despite
            # timeout=10 and stall whatever called alerts.send() (this once hung
            # bot startup ~70 min until DNS recovered). The join caps it
            # regardless of cause (DNS, connect, TLS, pool).
            box: Dict[str, Any] = {}

            def _do() -> None:
                try:
                    box["resp"] = requests.post(url, data=data, timeout=timeout)
                except Exception as exc:  # best-effort sender
                    box["err"] = exc

            worker = threading.Thread(target=_do, daemon=True)
            worker.start()
            worker.join(timeout + 2)
            if worker.is_alive():
                logger.debug("Telegram send attempt %d hard-timed out after %ds",
                             attempt + 1, timeout + 2)
            else:
                resp = box.get("resp")
                if resp is not None:
                    if resp.status_code == 200:
                        return True
                    if resp.status_code not in (408, 429, 500, 502, 503, 504):
                        logger.debug("Telegram non-retryable status=%s body=%s",
                                     resp.status_code, resp.text[:120])
                        return False
                else:
                    logger.debug("Telegram send attempt %d failed: %s",
                                 attempt + 1, box.get("err"))
            time.sleep(0.8 * (2 ** attempt))
        return False

    def _spool_message(self, data: Dict[str, Any], dedup_key: Optional[str], cooldown: int) -> None:
        try:
            import json as _j
            item = {
                "ts": time.time(),
                "data": data,
                "dedup_key": dedup_key,
                "cooldown": int(cooldown),
            }
            with self._spool_file.open("a", encoding="utf-8") as f:
                f.write(_j.dumps(item, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.debug("Telegram spool write failed: %s", exc)

    def _post_photo(self, photo_path: str, caption: str, timeout: int = 30) -> bool:
        try:
            with Path(photo_path).open("rb") as handle:
                response = requests.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendPhoto",
                    data={
                        "chat_id": self.chat_id,
                        "caption": str(caption or "")[:1024],
                        "parse_mode": self.parse_mode,
                    },
                    files={"photo": handle},
                    timeout=timeout,
                )
            return response.status_code == 200
        except Exception as exc:
            logger.debug("Telegram photo post failed: %s", exc)
            return False

    def _spool_photo(self, photo_path: str, caption: str) -> None:
        try:
            source = Path(photo_path)
            if not source.exists():
                return
            self._media_spool_dir.mkdir(parents=True, exist_ok=True)
            digest = __import__("hashlib").sha256(source.read_bytes()).hexdigest()[:16]
            saved = self._media_spool_dir / f"{int(time.time())}_{digest}{source.suffix or '.png'}"
            shutil.copy2(source, saved)
            import json as _j
            item = {
                "ts": time.time(), "kind": "photo",
                "photo_path": str(saved), "caption": str(caption or "")[:1024],
            }
            with self._spool_file.open("a", encoding="utf-8") as handle:
                handle.write(_j.dumps(item, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("Telegram photo spool write failed: %s", exc)

    def flush_spool(self, limit: int = 5) -> int:
        if not self.enabled or not self._spool_file.exists():
            return 0
        if time.time() - self._last_spool_flush < 30:
            return 0
        self._last_spool_flush = time.time()
        sent = 0
        kept: List[str] = []
        try:
            import json as _j
            lines = self._spool_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for raw in lines:
                if not raw.strip():
                    continue
                if sent >= limit:
                    kept.append(raw)
                    continue
                try:
                    item = _j.loads(raw)
                    data = item.get("data") or {}
                    if item.get("kind") == "photo":
                        photo_path = str(item.get("photo_path") or "")
                        if photo_path and self._post_photo(
                            photo_path, str(item.get("caption") or "")
                        ):
                            sent += 1
                            try:
                                Path(photo_path).unlink(missing_ok=True)
                            except Exception:
                                pass
                        else:
                            kept.append(raw)
                        continue
                    dedup_key = item.get("dedup_key")
                    cooldown = int(item.get("cooldown", self.dedup_ttl) or self.dedup_ttl)
                    if dedup_key and self._is_dedup_blocked(dedup_key, cooldown):
                        continue
                    if self._post_telegram(data, timeout=10):
                        sent += 1
                        if dedup_key:
                            self._mark_dedup_sent(dedup_key)
                    else:
                        kept.append(raw)
                except Exception:
                    kept.append(raw)
            self._spool_file.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except Exception as exc:
            logger.debug("Telegram spool flush failed: %s", exc)
        return sent

    # ── Core sender ───────────────────────────────────────────────────────────
    def send(
        self,
        message:                 str,
        dedup_key:               Optional[str] = None,
        dedup_cooldown_override: Optional[int] = None,
        reply_markup:            Optional[dict] = None,
    ) -> bool:
        text = str(message or "").strip()
        if not text or not self.enabled:
            return False
        cooldown = dedup_cooldown_override if dedup_cooldown_override is not None else self.dedup_ttl
        if dedup_key and self._is_dedup_blocked(dedup_key, cooldown):
            return False
        if len(text) > _TG_MAX_LEN:
            text = text[:_TG_MAX_LEN - 40] + "\n\n<i>…truncated</i>"
        data: Dict[str, Any] = {
            "chat_id":    self.chat_id,
            "text":       text,
            "parse_mode": self.parse_mode,
        }
        if reply_markup:
            import json
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            self.flush_spool(limit=3)
        except Exception:
            pass
        try:
            ok = self._post_telegram(data, timeout=10)
            if ok and dedup_key:
                self._mark_dedup_sent(dedup_key)
            if not ok:
                self._spool_message(data, dedup_key, cooldown)
            return ok
        except Exception as exc:
            logger.debug("Telegram send failed: %s", exc)
            self._spool_message(data, dedup_key, cooldown)
            return False

    # ── Helpers ───────────────────────────────────────────────────────────────
    def critical(self, message: str) -> bool:
        return self.send(f"🚨 <b>CRITICAL</b>\n{message}",
                         dedup_key=f"crit:{message[:40]}", dedup_cooldown_override=300)

    def warning(self, message: str, **kwargs) -> bool:
        return self.send(f"⚠️ {message}",
                         dedup_key=f"warn:{message[:30]}", dedup_cooldown_override=300)

    def _bar(self, val: float, limit: float, width: int = 10) -> str:
        if limit <= 0: return _B * width
        pct    = min(abs(val) / abs(limit), 1.0)
        filled = round(pct * width)
        return _B * filled + _E * (width - filled)

    def _wr_bar(self, wr: float, width: int = 8) -> str:
        return _B * round(wr / 100 * width) + _E * (width - round(wr / 100 * width))

    def _inr(self, v: float) -> str:
        return f"{abs(v):,.0f}"

    def _today(self) -> str:
        return date.today().isoformat()

    def _now(self) -> str:
        return datetime.now().strftime("%I:%M %p")

    def _now_short(self) -> str:
        return datetime.now().strftime("%H:%M")

    def _f(self, v: float, dp: int = 2) -> str:
        return f"{v:.{dp}f}"

    def _conf_label(self, n: int) -> str:
        if n >= 6: return "⚡⚡ VERY STRONG"
        if n >= 4: return "⚡ STRONG"
        if n >= 3: return "〰️ MEDIUM"
        if n >= 2: return "· WEAK"
        return "· SINGLE"

    def _streak_emoji(self, s: int) -> str:
        if s >= 3:  return "🔥"
        if s >= 2:  return "✅"
        if s <= -3: return "🥶"
        if s <= -2: return "❌"
        return "➡️"

    def _level_tag(self, price: float, level: float, label: str, tol: float = 0.002) -> str:
        if level <= 0: return ""
        if abs(price - level) / level < tol: return f"≈{label}"
        if price > level: return f"↑{label}"
        return f"↓{label}"

    # ─────────────────────────────────────────────────────────────────────────
    # STARTUP — fires on every restart
    # ─────────────────────────────────────────────────────────────────────────
    def startup(
        self, mode="PAPER", capital=None, bot_name="Trading Bot",
        symbols=0, strategies=0, version=None, lot_sizes=None,
        vix=None, active_strategy=None, **kwargs,
    ) -> bool:
        live = "PAPER" not in str(mode).upper()
        mode_icon = "💰 <b>LIVE TRADING</b>" if live else "📄 <b>PAPER MODE</b>"
        ts   = datetime.now().strftime("%d %b %Y, %I:%M %p")

        L = [
            f"{'━'*34}",
            f"🤖 <b>{bot_name} — STARTED</b>",
            f"  {mode_icon}",
            f"  🕐 {ts}",
            f"{'━'*34}",
            "",
        ]

        if capital:
            L.append(f"💵 Capital      ₹{self._inr(capital)}")
        if symbols:
            L += [
                f"🔍 Universe     {symbols} symbols",
                f"   NSE: NIFTY · BANKNIFTY · FINNIFTY · MIDCPNIFTY",
                f"   BSE: SENSEX · BANKEX",
                f"   + Nifty200 stocks (all sectors)",
            ]
        L.append(f"📐 Strategies   {strategies or 51} active (confluence engine)")

        if vix:
            v_icon = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            L.append(f"📊 India VIX    {v_icon} {vix:.1f}")

        # Lot size reminder post April 2026
        if date.today() >= date(2026, 4, 1):
            L += [
                "",
                f"📦 <b>Lot sizes (Apr 2026)</b>",
                f"   NIFTY=65  BANKNIFTY=30  FINNIFTY=65",
                f"   SENSEX=20  MIDCPNIFTY=120",
            ]

        if not live:
            L += [
                "",
                f"📋 <b>PAPER ONLY MODE</b>",
                f"   Trades are simulated — no real orders",
                f"   Switch: set PAPER_TRADING=false in .env",
            ]
        # April 2026 STT reminder
        L += [
            "",
            f"⚠️ <b>Apr 2026 STT</b>: Breakeven ~15 pts (was 7)",
            f"   Min score raised to 4.5  /stt for details",
        ]
        # Drive sync status
        try:
            import subprocess as _sp
            _rc = _sp.run(["which","rclone"], capture_output=True, timeout=2)
            if _rc.returncode == 0:
                L.append(f"☁️ Drive sync: ✅ active  /cloud for status")
        except Exception: pass

        # Always show commands on restart
        L += ["", COMMANDS_BLOCK]
        L.append(f"{'━'*34}")

        # Startup: unique per-restart, never dedup-blocked
        dk = f"startup:{int(time.time()//60)}"
        return self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=0)

    # ─────────────────────────────────────────────────────────────────────────
    # MODE CHANGE — every state transition
    # ─────────────────────────────────────────────────────────────────────────
    def mode_change(
        self, from_mode="", to_mode="", reason="",
        daily_pnl=None, trades_today=0, **kwargs,
    ) -> bool:
        icons = {
            "LIVE":"📈","PAPER":"📄","LEARNING":"📚","BACKTEST":"📐",
            "ML_TRAINING":"🧠","HOLIDAY":"🎉","AFTER_HOURS":"🌙",
            "WEEKEND":"🏖️","STOPPED":"🛑","CRASHED":"💥",
            "KILL_SWITCH":"🚨","BACKUP":"💾","BOOT":"🚀",
        }
        fi = icons.get(from_mode.upper(), "🔄")
        ti = icons.get(to_mode.upper(),   "🔄")

        L = [
            f"{fi}→{ti} <b>MODE: {to_mode.upper()}</b>",
        ]
        if reason:
            L.append(f"  {reason}")
        if daily_pnl is not None:
            e = "🟢" if daily_pnl >= 0 else "🔴"
            L.append(f"  {e} Day P&L:  ₹{daily_pnl:+,.0f}")
        if trades_today:
            L.append(f"  📊 Trades:   {trades_today}")

        cmd_map = {
            "LIVE":        "📱 /status · /pnl · /risk · /pause",
            "PAPER":       "📱 /status · /pnl · /risk · /health",
            "BACKTEST":    "📱 /bt — backtest status · takes ~40 min",
            "ML_TRAINING": "📱 /ml — model status · /train to force",
            "HOLIDAY":     "📱 /bt now · /train ML · /health",
            "LEARNING":    "📱 /health · /downloads · /schedule",
            "AFTER_HOURS": "📱 /schedule — what runs next",
        }
        hint = cmd_map.get(to_mode.upper(), "")
        if hint:
            L.append(f"  {hint}")
        L.append(f"🕐 {self._now()}")

        dk = f"mode:{from_mode}:{to_mode}:{self._today()}"
        return self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=3600)

    def alert_mode_switch(self, from_mode="", to_mode="", reason="", **kwargs) -> bool:
        return self.mode_change(from_mode=from_mode, to_mode=to_mode, reason=reason)

    # ─────────────────────────────────────────────────────────────────────────
    # MARKET OPEN
    # ─────────────────────────────────────────────────────────────────────────
    def market_open(
        self, date_str="", is_high_impact=False, vix=None,
        gift_nifty_gap=None, breadth_signal=None, fii_bias=None,
        day_type=None, expiry_info=None,
        nifty_prev_close=0.0, banknifty_prev_close=0.0,
        sensex_prev_close=0.0, finnifty_prev_close=0.0,
        midcp_prev_close=0.0, symbols_universe=200, **kwargs,
    ) -> bool:
        weekday = datetime.now().strftime("%A")
        L = [f"🔔 <b>MARKET OPEN</b>  {weekday} {date_str or ''}"]

        if is_high_impact:
            L.append("⚠️ <b>HIGH-IMPACT EVENT — reduced position sizing</b>")

        if gift_nifty_gap:
            g = "🟢" if gift_nifty_gap > 0 else "🔴"
            L.append(f"  {g} GIFT Nifty gap  {gift_nifty_gap*100:+.2f}%")

        if vix:
            vi = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            L.append(f"  {vi} India VIX       {vix:.1f}")

        if fii_bias:
            fb = "🟢" if "bull" in fii_bias.lower() else "🔴" if "bear" in fii_bias.lower() else "⚪"
            L.append(f"  {fb} FII bias        {fii_bias}")

        if day_type:
            L.append(f"  📅 Day type       {day_type}")

        if expiry_info:
            L.append(f"  📆 {expiry_info}")

        if nifty_prev_close:
            L += [
                "",
                f"📊 <b>Previous closes</b>",
                f"  NIFTY:     ₹{nifty_prev_close:,.0f}",
            ]
            if banknifty_prev_close:
                L.append(f"  BANKNIFTY: ₹{banknifty_prev_close:,.0f}")
            if sensex_prev_close:
                L.append(f"  SENSEX:    ₹{sensex_prev_close:,.0f}")

        L += [
            "",
            f"🔍 Scanning {symbols_universe} symbols with 31 strategies",
            f"📱 /signals · /vix · /status",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"mktopen:{date_str or self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # MARKET CLOSE
    # ─────────────────────────────────────────────────────────────────────────
    def market_close(
        self, date_str="", trades_today=0, wins=0, losses=0,
        daily_pnl=0.0, gross_pnl=0.0, total_costs=0.0,
        daily_loss_limit=3000.0, best_trade=0.0, worst_trade=0.0,
        capital_start=None, capital_end=None, **kwargs,
    ) -> bool:
        pnl_icon = "🟢" if daily_pnl >= 0 else "🔴"
        wr = wins / trades_today * 100 if trades_today else 0

        L = [
            f"🔕 <b>MARKET CLOSED</b>  {date_str or self._today()}",
            f"{'─'*32}",
            f"  {pnl_icon} P&L      ₹{daily_pnl:+,.0f}",
        ]
        if gross_pnl and total_costs:
            L.append(f"  📊 Gross    ₹{gross_pnl:+,.0f}   Costs ₹{total_costs:,.0f}")
        if trades_today:
            L += [
                f"  🎯 Trades   {trades_today}  ({wins}W · {losses}L)",
                f"  📈 Win rate {wr:.0f}%  {self._wr_bar(wr)}",
            ]
        if best_trade:
            L.append(f"  🏆 Best     ₹{best_trade:+,.0f}")
        if worst_trade:
            L.append(f"  👎 Worst    ₹{worst_trade:+,.0f}")
        if capital_start and capital_end:
            chg = capital_end - capital_start
            ci  = "🟢" if chg >= 0 else "🔴"
            L.append(f"  {ci} Capital   ₹{capital_end:,.0f}  ({chg:+,.0f})")

        L += [
            f"{'─'*32}",
            f"  🔄 Backtest starts at 4:30 PM",
            f"  🧠 ML training at 5:30 PM",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"mktclose:{date_str or self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # TRADE ENTRY
    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────
    # SAHI-STYLE TRADE ALERT
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sahi_alert(
        self,
        symbol, side, entry_price, stop_loss, target_price,
        agreeing_strategies, score, confluence,
        fii_bias_str, whale_mod, oi_direction, pcr, sr_ctx,
        vix, regime, paper, trade_id, **kwargs
    ) -> str:
        """Generate SAHI Research style trade alert with rationale."""
        try:
            from trade_rationale import build_rationale, format_sahi_alert
            entry_low  = float(entry_price or 0)
            entry_high = entry_low * 1.001  # 0.1% range
            rationale  = build_rationale(
                symbol=symbol, side=side,
                agreeing_strategies=agreeing_strategies or [],
                score=score or 0,
                confluence=confluence or "SINGLE",
                fii_bias=fii_bias_str or "",
                whale_mod=float(kwargs.get("whale_mod", 0) or 0),
                oi_direction=oi_direction or "",
                pcr=float(kwargs.get("pcr", 0) or 0),
                sr_ctx=sr_ctx or "",
                vix=float(vix or 0),
                regime=regime or "",
                entry_price=entry_low,
                stop_loss=float(stop_loss or 0),
                target_price=float(target_price or 0),
            )
            return format_sahi_alert(
                symbol=symbol,
                side=side,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=float(stop_loss or 0),
                target=float(target_price or 0),
                rationale=rationale,
                paper=paper,
                trade_id=trade_id or "",
                score=float(score or 0),
                confluence=confluence or "",
            )
        except Exception as _e:
            import logging
            logging.getLogger(__name__).debug("SAHI alert: %s", _e)
            return ""

    def trade_entry(
        self,
        symbol="", side="BUY", qty=0, entry_price=0.0,
        stop_loss=None, target_price=None,
        strategy=None, regime=None, score=None,
        option_strike=None, option_type=None, option_expiry=None, dte=None,
        confidence=None, alpha_factors=None,
        capital_deployed=None, total_capital=None,
        daily_pnl=None, daily_limit=3000.0,
        wins_today=0, losses_today=0,
        trade_id=None, paper=True,
        n_agree=1, confluence="SINGLE",
        agreeing_strategies=None, htf_bias="",
        weekly_pivot=0.0, weekly_r1=0.0, weekly_s1=0.0,
        monthly_pivot=0.0, monthly_r1=0.0,
        fii_net=0.0, fii_bias_str="", day_type="",
        momentum_override=False, tranche="",
        **kwargs,
    ) -> bool:
        side_u     = side.upper()
        side_icon  = "🟢 BUY" if side_u == "BUY" else "🔴 SELL"
        opt_icon   = "📞" if option_type == "CE" else "📟" if option_type == "PE" else "📌"
        mode_tag   = "PAPER" if paper else "🔴 LIVE"
        conf_label = self._conf_label(n_agree)
        trades_now = wins_today + losses_today
        wr         = wins_today / trades_now * 100 if trades_now else 0.0
        risk       = abs(entry_price - stop_loss)    if stop_loss    else 0.0
        reward     = abs(target_price - entry_price) if target_price else 0.0
        rr         = f"1:{reward/risk:.1f}" if risk > 0 and reward > 0 else "—"
        notional   = entry_price * qty

        # Sector tag for stocks
        _INDICES = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY",
                    "NIFTYNEXT50","SENSEX","BANKEX"}
        sector_tag = ""
        exchange_tag = " [BSE]" if symbol.upper() in ("SENSEX","BANKEX") else \
                       "" if symbol.upper() in _INDICES else " [NSE]"
        try:
            import pandas as _pd, os as _os
            for _csv in ["nifty200.csv"]:
                if _os.path.exists(_csv):
                    _df2 = _pd.read_csv(_csv)
                    _col = [c for c in _df2.columns if c.lower() in ("sector","industry")][0]
                    _sym_col = [c for c in _df2.columns if c.lower() in ("symbol","ticker")][0]
                    _row = _df2[_df2[_sym_col] == symbol.upper()]
                    if not _row.empty:
                        sector_tag = f" [{_row[_col].values[0]}]"
                    break
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # S/R level context
        pdh = float(kwargs.get("PDH", 0) or 0)
        pdl = float(kwargs.get("PDL", 0) or 0)
        pwh = float(kwargs.get("PWH", 0) or 0)
        pwl = float(kwargs.get("PWL", 0) or 0)
        pmh = float(kwargs.get("PMH", 0) or 0)
        pml = float(kwargs.get("PML", 0) or 0)
        sr_ctx = kwargs.get("sr_level_ctx","") or kwargs.get("levels_ctx","")

        L = [
            f"{'━'*34}",
            f"  {conf_label}  ({n_agree} agree)",
            f"  {side_icon}  {opt_icon} {symbol}{exchange_tag}{sector_tag}  [{mode_tag}]",
            f"{'━'*34}",
        ]

        # Option details
        if option_strike and option_type:
            expiry_str = f" exp {option_expiry}" if option_expiry else ""
            dte_str    = f" ({dte}DTE)" if dte else ""
            L.append(f"  📌 {symbol} {option_strike} {option_type}{expiry_str}{dte_str}")

        # Entry / Stop / Target
        L.append(f"  🎯 Entry   ₹{entry_price:,.2f}  ×{qty} = ₹{notional:,.0f}")
        if stop_loss:
            L.append(f"  🛑 Stop    ₹{stop_loss:,.2f}  Risk ₹{risk*qty:,.0f}")
        if target_price:
            L.append(f"  🏆 Target  ₹{target_price:,.2f}  Rwd  ₹{reward*qty:,.0f}")
        if rr != "—":
            score_icon = "⚡" if score and score >= 8 else "●"
            L.append(f"  📐 R:R {rr}   {score_icon} Score {score:.1f}" if score else f"  📐 R:R {rr}")

        # Key S/R levels
        sr_parts = []
        if pdh: sr_parts.append(f"PDH:{pdh:,.0f}")
        if pdl: sr_parts.append(f"PDL:{pdl:,.0f}")
        if pwh: sr_parts.append(f"PWH:{pwh:,.0f}")
        if pwl: sr_parts.append(f"PWL:{pwl:,.0f}")
        if pmh: sr_parts.append(f"PMH:{pmh:,.0f}")
        if pml: sr_parts.append(f"PML:{pml:,.0f}")
        if sr_parts:
            L.append(f"  📏 {' · '.join(sr_parts[:4])}")
        if sr_ctx:
            L.append(f"  🎯 {sr_ctx[:55]}")

        # Agreeing strategies
        if agreeing_strategies:
            strat_str = " · ".join(str(s) for s in agreeing_strategies[:5])
            L.append(f"  📋 {strat_str}")

        # HTF bias
        if htf_bias:
            hb = "⬆️" if "bull" in htf_bias.lower() else "⬇️" if "bear" in htf_bias.lower() else "↔️"
            L.append(f"  {hb} HTF: {htf_bias}")

        # FII context
        if fii_bias_str:
            fb = "🟢" if "bull" in fii_bias_str.lower() else "🔴" if "bear" in fii_bias_str.lower() else "⚪"
            net_str = f"  Net ₹{fii_net/1e7:+.0f}Cr" if fii_net else ""
            L.append(f"  🏦 FII: {fb} {fii_bias_str}{net_str}")

        # Whale / institutional modifier
        whale_mod = kwargs.get("whale_mod", 0)
        if abs(whale_mod) >= 1.0:
            wm = "🐳 Whale: bullish" if whale_mod > 0 else "🐳 Whale: bearish"
            L.append(f"  {wm}")

        # Day P&L bar
        if daily_pnl is not None and daily_limit:
            bar = self._bar(abs(daily_pnl), daily_limit)
            pi  = "🟢" if daily_pnl >= 0 else "🔴"
            L.append(f"  {pi} Day P&L  ₹{daily_pnl:+,.0f}  {bar}")

        # Today's record
        if trades_now:
            L.append(f"  📊 Today  {trades_now} trades  WR {wr:.0f}%  "
                     f"({wins_today}W·{losses_today}L)")

        if trade_id:
            L.append(f"  🔖 {trade_id}")

        L.append(f"{'━'*34}")
        L.append(f"🕐 {self._now()}")

        dk = f"entry:{trade_id or symbol}:{int(time.time()//60)}"
        sent = self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=55)

        # Also send SAHI-style alert as separate message
        try:
            _sahi = self._build_sahi_alert(
                symbol=symbol, side=side,
                entry_price=entry_price, stop_loss=stop_loss,
                target_price=target_price,
                agreeing_strategies=agreeing_strategies,
                score=score, confluence=confluence,
                fii_bias_str=fii_bias_str or '',
                whale_mod=float(whale_mod or 0),
                oi_direction=htf_bias or '', pcr=0, sr_ctx=sr_ctx or '',
                vix=0,
                regime=regime or '', paper=paper, trade_id=trade_id or '',
                **kwargs
            )
            if _sahi:
                import time as _t2
                self.send(_sahi,
                    dedup_key=f"sahi:{trade_id or symbol}:{int(_t2.time()//60)}",
                    dedup_cooldown_override=55)
        except Exception: pass

        return sent

    # ─────────────────────────────────────────────────────────────────────────
    # TRADE EXIT
    # ─────────────────────────────────────────────────────────────────────────

    def send_photo(self, photo_path: str, caption: str = "") -> bool:
        """Send a photo/chart, persisting a retry copy when delivery fails."""
        if not self.enabled: return False
        try:
            self.flush_spool(limit=3)
            ok = self._post_photo(photo_path, caption)
            if not ok:
                self._spool_photo(photo_path, caption)
            return ok
        except Exception as e:
            logger.debug("send_photo: %s", e)
            self._spool_photo(photo_path, caption)
            return False

    def send_video(self, video_path: str, caption: str = "") -> bool:
        """Send a video to Telegram."""
        if not self.enabled: return False
        try:
            import requests as _rq
            url = f"https://api.telegram.org/bot{self.bot_token}/sendVideo"
            with open(video_path, 'rb') as f:
                resp = _rq.post(url, data={
                    "chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "parse_mode": self.parse_mode,
                    "supports_streaming": True,
                }, files={"video": f}, timeout=60)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("send_video: %s", e)
            return False

    def send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a document/file to Telegram."""
        if not self.enabled: return False
        try:
            import requests as _rq
            url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
            with open(file_path, 'rb') as f:
                resp = _rq.post(url, data={
                    "chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "parse_mode": self.parse_mode,
                }, files={"document": f}, timeout=30)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("send_document: %s", e)
            return False

    def send_to_channel(self, channel_id: str, message: str) -> bool:
        """Send message to a specific Telegram channel/group."""
        if not self.enabled or not channel_id: return False
        try:
            import requests as _rq
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = _rq.post(url, json={
                "chat_id": channel_id,
                "text": message[:4096],
                "parse_mode": self.parse_mode,
            }, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("send_to_channel: %s", e)
            return False

    def send_audio(self, audio_path: str, caption: str = "") -> bool:
        """Send audio/voice message to Telegram."""
        if not self.enabled: return False
        try:
            import requests as _rq
            url = f"https://api.telegram.org/bot{self.bot_token}/sendAudio"
            with open(audio_path, 'rb') as f:
                resp = _rq.post(url, data={
                    "chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "parse_mode": self.parse_mode,
                    "title": caption[:60] or "Market Brief",
                }, files={"audio": f}, timeout=30)
            return resp.status_code == 200
        except Exception as e:
            logger.debug("send_audio: %s", e)
            return False

    def trade_exit(
        self,
        symbol="", side="BUY", qty=0, exit_price=0.0,
        pnl=0.0, exit_reason="",
        *, entry_price=0.0, charges=0.0, net_pnl=None,
        hold_minutes=None,
        wins_today=0, losses_today=0, daily_pnl=0.0,
        daily_limit=3000.0, trade_id=None, paper=True,
        option_strike=None, option_type=None,
        trade=None, reason=None, **kwargs,
    ) -> bool:
        if "hold_seconds" in kwargs and hold_minutes is None:
            try:
                hold_minutes = float(kwargs["hold_seconds"]) / 60.0
            except Exception:
                pass

        if net_pnl is None:
            net_pnl = pnl

        # Accept ManagedTrade object directly
        if trade is not None:
            symbol      = getattr(trade, "symbol", symbol)
            side        = getattr(trade, "side", side)
            qty         = getattr(trade, "qty", qty)
            entry_price = float(getattr(trade, "entry_price", entry_price) or 0)
            exit_price  = float(getattr(trade, "exit_price", exit_price) or entry_price)
            net_pnl     = float(getattr(trade, "realized_pnl", pnl) or pnl)
            pnl         = net_pnl
            trade_id    = getattr(trade, "trade_id", trade_id)
            t_in        = getattr(trade, "entry_time", None)
            t_out       = getattr(trade, "exit_time", None)
            if t_in and t_out:
                hold_minutes = (t_out - t_in) / 60
        if reason and not exit_reason:
            exit_reason = reason
        net_pnl = float(net_pnl or 0.0)
        won    = net_pnl >= 0
        icon   = "✅ WIN" if won else "❌ LOSS"
        pct    = abs(exit_price - entry_price) / entry_price * 100 if entry_price else 0
        trades = wins_today + losses_today
        wr     = wins_today / trades * 100 if trades else 0

        hold_str = f"{hold_minutes:.0f}m" if hold_minutes else ""
        opt_str  = f" {option_strike}{option_type}" if option_strike and option_type else ""
        mode_tag = "PAPER" if paper else "LIVE"

        L = [
            f"{'━'*34}",
            f"  {icon}  {symbol}{opt_str}  [{mode_tag}]",
            f"{'━'*34}",
            f"  Entry  ₹{entry_price:,.2f}  →  Exit ₹{exit_price:,.2f}  ({pct:+.1f}%)",
            f"  P&L    ₹{net_pnl:+,.0f}  (charges ₹{charges:,.0f})",
        ]
        if exit_reason:
            reasons = {
                "STOP_LOSS": "🛑 Stop loss hit",
                "TARGET":    "🏆 Target reached",
                "EOD":       "🔕 EOD squareoff",
                "MANUAL":    "✋ Manual close",
                "TRAIL":     "📈 Trailing stop",
            }
            L.append(f"  {reasons.get(exit_reason.upper(), exit_reason)}")
        if hold_str:
            L.append(f"  ⏱️ Held {hold_str}")

        pi  = "🟢" if daily_pnl >= 0 else "🔴"
        bar = self._bar(abs(daily_pnl), daily_limit)
        L += [
            f"{'─'*32}",
            f"  {pi} Day P&L  ₹{daily_pnl:+,.0f}  {bar}",
            f"  📊 Today    {trades} trades  WR {wr:.0f}%",
        ]
        if trade_id:
            L.append(f"  🔖 {trade_id}")
        L.append(f"🕐 {self._now()}")

        dk = f"exit:{trade_id or symbol}:{int(time.time()//60)}"
        return self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=55)

    # ─────────────────────────────────────────────────────────────────────────
    # DAILY SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    def daily_summary(
        self,
        date_str="", total_trades=0, wins=0, losses=0,
        daily_realized_pnl=0.0, gross_pnl=0.0, total_costs=0.0,
        daily_loss_limit=3000.0, strategy_breakdown=None,
        best_trade=0.0, worst_trade=0.0,
        avg_hold_min=None, avg_rr=None,
        capital_start=None, capital_end=None,
        alpha_breakdown=None, trade_list=None,
        index_pnl=None, sector_pnl=None,
        sharpe_30d=None, ewma_win_rate=None, max_drawdown=None,
        **kwargs,
    ) -> bool:
        wr      = wins / total_trades * 100 if total_trades else 0
        pi      = "🟢" if daily_realized_pnl >= 0 else "🔴"
        day_str = date_str or self._today()
        limit_pct = abs(daily_realized_pnl) / daily_loss_limit * 100 if daily_loss_limit else 0

        L = [
            f"{'━'*34}",
            f"  📊 <b>DAILY SUMMARY — {day_str}</b>",
            f"{'━'*34}",
            f"  {pi} P&L     ₹{daily_realized_pnl:+,.0f}",
        ]
        if gross_pnl and total_costs:
            L.append(f"     Gross ₹{gross_pnl:+,.0f}  Costs ₹{total_costs:,.0f}")
        if total_trades:
            L += [
                f"  🎯 Trades  {total_trades}  ({wins}W · {losses}L)",
                f"  📈 WR      {wr:.0f}%  {self._wr_bar(wr)}",
            ]
            if limit_pct:
                L.append(f"  ⚠️ Limit   {self._bar(abs(daily_realized_pnl), daily_loss_limit)} {limit_pct:.0f}%")
        opened_today = int(kwargs.get("opened_today", 0) or 0)
        closed_today = int(kwargs.get("closed_today", total_trades) or 0)
        open_positions = int(kwargs.get("open_positions", 0) or 0)
        if opened_today or open_positions:
            L.append(
                f"  📒 Flow    opened {opened_today} · closed {closed_today} · open {open_positions}"
            )
        if best_trade:
            L.append(f"  🏆 Best    ₹{best_trade:+,.0f}")
        if worst_trade:
            L.append(f"  👎 Worst   ₹{worst_trade:+,.0f}")
        if avg_rr:
            L.append(f"  📐 Avg R:R {avg_rr:.2f}")
        if avg_hold_min:
            L.append(f"  ⏱️ Avg hold {avg_hold_min:.0f} min")

        # Capital change
        if capital_start and capital_end:
            chg = capital_end - capital_start
            ci  = "🟢" if chg >= 0 else "🔴"
            L += [
                f"{'─'*32}",
                f"  {ci} Capital  ₹{capital_end:,.0f}  ({chg:+,.0f})",
            ]

        # Live metrics
        if sharpe_30d is not None:
            L.append(f"  📉 Sharpe(30d)  {sharpe_30d:.2f}")
        if ewma_win_rate is not None:
            L.append(f"  🎯 EWMA WR     {ewma_win_rate*100:.0f}%")
        if max_drawdown:
            L.append(f"  📉 Max DD      ₹{max_drawdown:,.0f}")

        # Strategy breakdown
        if strategy_breakdown:
            top = sorted(strategy_breakdown.items(),
                         key=lambda x: x[1].get("pnl", 0), reverse=True)[:3]
            if top:
                L.append(f"{'─'*32}")
                L.append("  📋 <b>Top strategies</b>")
                for strat, d in top:
                    p = d.get("pnl", 0)
                    t = d.get("trades", 0)
                    si = "🟢" if p >= 0 else "🔴"
                    L.append(f"    {si} {strat:<18} ₹{p:+,.0f}  ({t}t)")

        # VaR
        try:
            from value_at_risk import get_var_engine
            _var = get_var_engine(capital_start or 100_000).compute()
            vc   = "🟢" if _var.safe_to_trade else "🔴"
            L.append(f"{'─'*32}")
            L.append(f"  {vc} VaR(95%)  ₹{_var.total_var:,.0f}  ({_var.var_pct:.1f}%)")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Connection status
        try:
            from connection_monitor import get_monitor
            cs = get_monitor().get_status_line()
            L.append(f"  🔌 {cs}")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        L += [
            f"{'─'*32}",
            f"  🔄 Backtest running at 4:30 PM",
            f"  📱 /pnl · /risk · /health",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"dsum:{day_str}")

    def daily_summary_compat(self, **kwargs) -> bool:
        return self.daily_summary(**kwargs)


    # ─────────────────────────────────────────────────────────────────────────
    # NIFTY INTRADAY BRIEF — SAHI Research style card
    # ─────────────────────────────────────────────────────────────────────────
    def nifty_intraday_brief(
        self,
        date_str:         str   = "",
        # Price levels
        spot:             float = 0.0,
        range_high:       float = 0.0,   # top of consolidation range
        range_low:        float = 0.0,   # bottom of range
        demand_zone_high: float = 0.0,   # demand zone top (≈ put wall)
        demand_zone_low:  float = 0.0,   # demand zone bottom
        resistance:       float = 0.0,   # key resistance (≈ call wall / max pain)
        support:          float = 0.0,   # key support
        # OI snapshot
        total_call_oi:    float = 0.0,   # in lakhs
        call_oi_chg:      float = 0.0,   # change in lakhs
        max_call_strike:  float = 0.0,   # max call OI strike (ceiling)
        total_put_oi:     float = 0.0,
        put_oi_chg:       float = 0.0,
        max_put_strike:   float = 0.0,   # max put OI strike (floor)
        future_oi:        int   = 0,
        future_oi_chg_pct:float = 0.0,
        pcr:              float = 0.0,
        iv:               float = 0.0,   # India VIX or option IV
        ivp:              float = 0.0,   # IV percentile 0-100
        # Market sentiment
        put_writer_low:   float = 0.0,   # put writers' zone bottom
        put_writer_high:  float = 0.0,   # put writers' zone top
        call_writer:      float = 0.0,   # call writers' strike
        # Context
        fii_bias:         str   = "",
        day_type:         str   = "",
        expiry_info:      str   = "",
        regime:           str   = "",
        **kwargs,
    ) -> bool:
        """
        SAHI-Research style intraday brief.
        Sent at 8:28 AM pre-market + updated at 9:05 AM after option chain loads.
        """
        ds    = date_str or datetime.now().strftime("%d %b %Y")
        today = datetime.now().strftime("%A")

        # ── Outlook narrative (auto-generated from levels) ────────────────
        outlook = []
        if range_high and range_low:
            outlook.append(f"Nifty range: {range_low:,.0f} – {range_high:,.0f}")
        if support:
            outlook.append(f"Hold {support:,.0f} → consolidation intact")
        if demand_zone_low and demand_zone_high:
            outlook.append(f"Demand zone: {demand_zone_low:,.0f}–{demand_zone_high:,.0f}")
        if resistance:
            outlook.append(f"Break {resistance:,.0f} → next leg up")

        # ── PCR interpretation ────────────────────────────────────────────
        if pcr >= 1.3:
            pcr_view = "🟢 Bullish (heavy put writing)"
        elif pcr >= 0.9:
            pcr_view = "🟡 Neutral"
        elif pcr >= 0.7:
            pcr_view = "🟠 Slightly bearish"
        else:
            pcr_view = "🔴 Bearish (call writing dominates)"

        # ── IV interpretation ─────────────────────────────────────────────
        vix_val  = iv or 0
        if vix_val > 22:
            iv_view  = f"🔴 HIGH {vix_val:.1f}%"
            iv_note  = "Option buying blocked — premiums expensive"
        elif vix_val > 16:
            iv_view  = f"🟡 ELEVATED {vix_val:.1f}%"
            iv_note  = "Reduced lot sizes applied"
        else:
            iv_view  = f"🟢 NORMAL {vix_val:.1f}%"
            iv_note  = "All strategies active"

        # ── Direction bias ────────────────────────────────────────────────
        bias_icon = "⚖️ NEUTRAL"
        if pcr >= 1.1 and support and spot >= support:
            bias_icon = "📈 MILDLY BULLISH"
        elif pcr >= 1.3:
            bias_icon = "📈 BULLISH"
        elif pcr <= 0.7:
            bias_icon = "📉 BEARISH"
        elif pcr <= 0.9 and resistance and spot >= resistance * 0.995:
            bias_icon = "📉 MILDLY BEARISH"

        # Format OI in lakhs
        def _lakh(v):
            if v >= 100: return f"{v/100:.2f}Cr"
            return f"{v:.2f}L"

        L = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  📊 <b>NIFTY INTRADAY BRIEF</b>",
            f"  {today}, {ds}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        # ── Outlook ───────────────────────────────────────────────────────
        L.append(f"<b>📋 OUTLOOK</b>  {bias_icon}")
        for point in outlook[:4]:
            L.append(f"  • {point}")
        if expiry_info:
            L.append(f"  • {expiry_info}")
        if day_type:
            L.append(f"  • {day_type}")

        # ── Index activity ────────────────────────────────────────────────
        L += ["", f"<b>📈 INDEX ACTIVITY</b>"]
        if spot:
            L.append(f"  LTP:       ₹{spot:,.0f}")
        if resistance:
            L.append(f"  Resistance: ₹{resistance:,.0f}  🔴 (Call wall)")
        if demand_zone_low:
            L.append(f"  Demand:     ₹{demand_zone_low:,.0f}–{demand_zone_high:,.0f}  🟢")
        if support:
            L.append(f"  Support:    ₹{support:,.0f}  🟢 (Put wall)")

        # ── OI snapshot ───────────────────────────────────────────────────
        L += ["", f"<b>🔢 OI SNAPSHOT</b>"]
        if future_oi:
            chg_icon = "🟢" if future_oi_chg_pct >= 0 else "🔴"
            L.append(f"  Future OI:  {future_oi:,}  {chg_icon}{future_oi_chg_pct:+.1f}%")

        # Call OI row
        if total_call_oi:
            coi_str = f"{_lakh(total_call_oi)}"
            chg_str = f" +{_lakh(call_oi_chg)}" if call_oi_chg > 0 else f" {_lakh(call_oi_chg)}"
            pin_str = f"  Pin: {max_call_strike:,.0f}" if max_call_strike else ""
            L.append(f"  Call OI:    {coi_str}{chg_str}{pin_str}")

        # Put OI row
        if total_put_oi:
            poi_str = f"{_lakh(total_put_oi)}"
            pchg_str= f" +{_lakh(put_oi_chg)}" if put_oi_chg > 0 else f" {_lakh(put_oi_chg)}"
            ppin_str= f"  Pin: {max_put_strike:,.0f}" if max_put_strike else ""
            L.append(f"  Put OI:     {poi_str}{pchg_str}{ppin_str}")

        # PCR + IV side by side
        if pcr:
            L.append(f"  PCR: {pcr:.2f}  {pcr_view}")
        if vix_val:
            L.append(f"  IV:  {iv_view}")
            L.append(f"  ↳   {iv_note}")

        # ── Market sentiment ──────────────────────────────────────────────
        L += ["", f"<b>🐂🐻 MARKET SENTIMENT</b>"]
        if put_writer_low and put_writer_high:
            L.append(f"  Put writers:  🟢 {put_writer_low:,.0f}–{put_writer_high:,.0f}")
        elif max_put_strike:
            L.append(f"  Put writers:  🟢 {max_put_strike:,.0f} zone")
        if call_writer:
            L.append(f"  Call writers: 🔴 {call_writer:,.0f}")
        elif max_call_strike:
            L.append(f"  Call writers: 🔴 {max_call_strike:,.0f}")
        if fii_bias:
            fb = "🟢" if "bull" in fii_bias.lower() else "🔴" if "bear" in fii_bias.lower() else "⚪"
            L.append(f"  FII stance:   {fb} {fii_bias}")
        if regime:
            L.append(f"  Regime:       {regime}")

        # ── Technical levels ──────────────────────────────────────────────
        L += ["", f"<b>📏 TECHNICAL LEVELS</b>"]
        if support:
            L.append(f"  Support:    ▲ ₹{support:,.0f}")
        if demand_zone_low:
            L.append(f"  Demand:     ▲ ₹{demand_zone_low:,.0f}–{demand_zone_high:,.0f}")
        if resistance:
            L.append(f"  Resistance: ▼ ₹{resistance:,.0f}")
        if range_high:
            L.append(f"  Range:      ₹{range_low:,.0f} – ₹{range_high:,.0f}")

        L += [
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  📱 /signals  /vix  /regime  /status",
            f"🕐 {self._now()}",
        ]

        return self.send("\n".join(L),
                         dedup_key=f"intraday_brief:{ds}:{self._now_short()[:2]}",
                         dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # PRE-MARKET BRIEF
    # ─────────────────────────────────────────────────────────────────────────
    def pre_market_brief(
        self, date_str="", vix=None, fii_net=0.0, fii_bias="",
        gift_nifty_gap=None, us_close_pct=None, sgx_nifty=None,
        crude_price=None, usdinr=None, day_type=None,
        key_events=None, expiry_info=None,
        nifty_pdh=0.0, nifty_pdl=0.0, nifty_pdc=0.0,
        banknifty_pdh=0.0, banknifty_pdl=0.0,
        support_levels=None, resistance_levels=None,
        **kwargs,
    ) -> bool:
        L = [
            f"🌅 <b>PRE-MARKET BRIEF</b>  {date_str or self._today()}",
            f"{'─'*32}",
        ]
        # GIFT Nifty
        if gift_nifty_gap is not None:
            gi = "🟢" if gift_nifty_gap > 0.3 else "🔴" if gift_nifty_gap < -0.3 else "⚪"
            L.append(f"  {gi} GIFT Nifty gap  {gift_nifty_gap:+.2f}%")
        # US market
        if us_close_pct is not None:
            ui = "🟢" if us_close_pct > 0 else "🔴"
            L.append(f"  {ui} US close        {us_close_pct:+.2f}%")
        # VIX
        if vix:
            vi = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            block = "⚠️ Option buying blocked" if vix > 22 else ""
            L.append(f"  {vi} India VIX       {vix:.1f}  {block}")
        # FII
        if fii_bias or fii_net:
            fb = "🟢" if "bull" in str(fii_bias).lower() else "🔴" if "bear" in str(fii_bias).lower() else "⚪"
            net = f"  ₹{fii_net/1e7:+.0f}Cr" if fii_net else ""
            L.append(f"  {fb} FII bias        {fii_bias}{net}")
        # Cross-asset
        if crude_price:
            L.append(f"  🛢️ Brent crude     ${crude_price:.1f}")
        if usdinr:
            L.append(f"  💱 USD/INR         ₹{usdinr:.2f}")

        # Key levels
        if nifty_pdh or nifty_pdl:
            L += [
                f"{'─'*32}",
                f"  📏 <b>Key levels (NIFTY)</b>",
            ]
            if nifty_pdh: L.append(f"    PDH: ₹{nifty_pdh:,.0f}")
            if nifty_pdl: L.append(f"    PDL: ₹{nifty_pdl:,.0f}")
            if nifty_pdc: L.append(f"    PDC: ₹{nifty_pdc:,.0f}")
        if banknifty_pdh or banknifty_pdl:
            L.append(f"  📏 <b>Key levels (BANKNIFTY)</b>")
            if banknifty_pdh: L.append(f"    PDH: ₹{banknifty_pdh:,.0f}")
            if banknifty_pdl: L.append(f"    PDL: ₹{banknifty_pdl:,.0f}")

        # Events
        if key_events:
            L += [f"{'─'*32}", "  📅 <b>Events today</b>"]
            for ev in key_events[:3]:
                L.append(f"    • {ev}")
        if expiry_info:
            L.append(f"  📆 {expiry_info}")

        if day_type:
            L.append(f"{'─'*32}")
            L.append(f"  📊 Day type: {day_type}")

        L += [
            f"{'─'*32}",
            f"  📱 /health — verify all feeds",
            f"  📱 /vix · /status · /signals",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"premarket:{date_str or self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # DAILY PLAN
    # ─────────────────────────────────────────────────────────────────────────
    def daily_plan(
        self, date_str="", strategy="", regime="",
        nifty_levels=None, banknifty_levels=None,
        key_events=None, expiry_info=None,
        bias="", day_type="", symbols_watchlist=None,
        **kwargs,
    ) -> bool:
        L = [
            f"🗓️ <b>TODAY'S PLAN</b>  {date_str or self._today()}",
            f"{'─'*32}",
        ]
        if regime:
            L.append(f"  📊 Regime:    {regime}")
        if strategy:
            L.append(f"  ⚡ Strategy:  {strategy}")
        if bias:
            bi = "🟢" if "bull" in bias.lower() else "🔴" if "bear" in bias.lower() else "⚪"
            L.append(f"  {bi} Bias:      {bias}")
        if day_type:
            L.append(f"  📅 Day type:  {day_type}")
        if expiry_info:
            L.append(f"  📆 Expiry:    {expiry_info}")

        if nifty_levels:
            L += [f"{'─'*32}", "  📏 <b>NIFTY levels</b>"]
            for label, val in nifty_levels.items():
                if val:
                    L.append(f"    {label}: ₹{val:,.0f}")
        if banknifty_levels:
            L += ["  📏 <b>BANKNIFTY levels</b>"]
            for label, val in banknifty_levels.items():
                if val:
                    L.append(f"    {label}: ₹{val:,.0f}")

        if symbols_watchlist:
            L += [f"{'─'*32}", "  👁️ <b>Watchlist</b>"]
            for s in symbols_watchlist[:6]:
                L.append(f"    • {s}")

        if key_events:
            L += [f"{'─'*32}", "  📅 <b>Events</b>"]
            for ev in key_events[:3]:
                L.append(f"    • {ev}")

        L += [
            f"{'─'*32}",
            f"  📱 /signals · /status · /risk",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"dailyplan:{date_str or self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS 15-MIN
    # ─────────────────────────────────────────────────────────────────────────
    def status_15min(
        self,
        symbols_scanned=0, signals_found=0, tier1_signals=0,
        top_signal=None, open_positions=0, open_pos_list=None,
        daily_pnl=None, unrealized_pnl=None, daily_limit=3000.0,
        trades_today=0, wins_today=0, market_phase="LIVE",
        current_strategy=None, circuit_breaker=False, vix=None,
        vix_change=None, breadth_signal=None, fii_bias=None,
        day_type=None, expiry_day=False,
        index_signals=None, index_ltp=None,
        sensex_nifty_divergence=0.0, symbols_universe=200,
        **kwargs,
    ) -> bool:
        if circuit_breaker:
            return self.circuit_breaker(daily_pnl=daily_pnl or 0, daily_limit=daily_limit)

        losses_today = trades_today - wins_today
        wr  = wins_today / trades_today * 100 if trades_today else 0
        pi  = "🟢" if (daily_pnl or 0) >= 0 else "🔴"
        ts  = self._now_short()

        # ── Rich dashboard ────────────────────────────────────────────────
        L = [f"📡 <b>LIVE DASHBOARD</b>  ·  {ts}"]

        # Index LTPs
        if index_ltp:
            parts = [f"{k} ₹{v:,.0f}" for k, v in list(index_ltp.items())[:4]]
            L.append("  " + "   ".join(parts))
        L.append("  ────────────────")

        # Scan line (warm-up grace right after a restart — cache still cold)
        if kwargs.get("warming_up"):
            L.append("  ⏳ Warming up — scan starting (just restarted)")
        else:
            sp = [f"🔍 Scanned <b>{symbols_scanned}</b>"]
            if signals_found:  sp.append(f"Signals <b>{signals_found}</b>")
            if tier1_signals:  sp.append(f"⚡{tier1_signals} strong")
            L.append("  " + "   ·   ".join(sp))

        # P&L — value + bar + % of daily limit
        if daily_pnl is not None:
            bar = self._bar(abs(daily_pnl), daily_limit)
            pct = (abs(daily_pnl) / daily_limit * 100) if daily_limit else 0
            ur  = f"  (unreal ₹{unrealized_pnl:+,.0f})" if unrealized_pnl else ""
            L.append(f"  {pi} <b>P&amp;L ₹{daily_pnl:+,.0f}</b>{ur}")
            L.append(f"     {bar} {pct:.0f}% of ₹{daily_limit:,.0f} limit")

        # Win-rate bar (only once there are closed trades)
        if trades_today:
            L.append(f"  🎯 WR {wr:.0f}%  {self._wr_bar(wr)}  "
                     f"({wins_today}W/{losses_today}L · {trades_today} trades)")

        # Open positions
        if open_positions:
            L.append(f"  📂 <b>{open_positions} open</b>")
            for pos in (open_pos_list or [])[:3]:
                sym  = pos.get("symbol", "?")
                upnl = pos.get("unrealized_pnl", 0)
                pi2  = "🟢" if upnl >= 0 else "🔴"
                L.append(f"     {pi2} {sym}  ₹{upnl:+,.0f}")

        # Top signal
        if top_signal:
            ts_sym  = top_signal.get("symbol", "?")
            ts_side = top_signal.get("side", "?")
            ts_sc   = top_signal.get("score", 0)
            si = "🟢" if ts_side == "BUY" else "🔴"
            L.append(f"  ⚡ Best  {si} {ts_sym}  score {ts_sc:.1f}")

        # VIX gauge
        if vix:
            vlabel = "calm" if vix < 13 else "normal" if vix < 16 else "elevated" if vix < 20 else "high"
            vi = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            L.append(f"  {vi} VIX {vix:.1f}  {self._bar(min(vix,30),30,width=6)} {vlabel}")

        if expiry_day:
            L.append("  📆 EXPIRY DAY — 0DTE active")

        L.append("  ────────────────")
        L.append("  📱 /pnl · /positions · /signals · /oisr")

        dedup_window = 60 * 15
        return self.send("\n".join(L),
                         dedup_key=f"scan15:{ts}",
                         dedup_cooldown_override=dedup_window)

    # ─────────────────────────────────────────────────────────────────────────
    # HOURLY UPDATE
    # ─────────────────────────────────────────────────────────────────────────
    def hourly_update(
        self, hour_label="", daily_pnl=0.0, unrealized=0.0,
        trades=0, wins=0, vix=None, breadth=None,
        top_alpha=None, daily_limit=3000.0, **kwargs,
    ) -> bool:
        pi  = "🟢" if daily_pnl >= 0 else "🔴"
        bar = self._bar(abs(daily_pnl), daily_limit)
        wr  = wins / trades * 100 if trades else 0

        L = [
            f"⏰ <b>HOURLY</b>  {hour_label or self._now_short()}",
            f"  {pi} P&L    ₹{daily_pnl:+,.0f}  {bar}",
        ]
        if unrealized:
            L.append(f"  📂 Unreal  ₹{unrealized:+,.0f}")
        if trades:
            L.append(f"  🎯 Trades  {trades}  WR {wr:.0f}%")
        if vix:
            vi = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            L.append(f"  {vi} VIX     {vix:.1f}")
        if breadth:
            L.append(f"  📈 Breadth {breadth}")
        if top_alpha:
            L.append(f"  ⚡ Top     {top_alpha}")

        # Connection status
        try:
            from connection_monitor import get_monitor
            cs = get_monitor().get_status_line()
            L.append(f"  🔌 {cs}")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        L.append(f"🕐 {self._now()}")
        return self.send("\n".join(L), dedup_key=f"hourly:{hour_label}",
                         dedup_cooldown_override=3300)

    # ─────────────────────────────────────────────────────────────────────────
    # PARTIAL EXIT
    # ─────────────────────────────────────────────────────────────────────────
    def partial_exit(
        self, symbol="", side="", qty_closed=0, qty_remaining=0,
        exit_price=0.0, new_stop=0.0, pnl_so_far=0.0,
        trade_id=None, **kwargs,
    ) -> bool:
        pi = "🟢" if pnl_so_far >= 0 else "🔴"
        L  = [
            f"✂️ <b>PARTIAL EXIT</b>  {symbol}",
            f"  Closed {qty_closed} @ ₹{exit_price:,.2f}",
            f"  Remaining: {qty_remaining} units",
            f"  {pi} P&L so far: ₹{pnl_so_far:+,.0f}",
        ]
        if new_stop:
            L.append(f"  🛑 New stop → ₹{new_stop:,.2f}")
        if trade_id:
            L.append(f"  🔖 {trade_id}")
        L.append(f"🕐 {self._now()}")
        return self.send("\n".join(L), dedup_key=f"partial:{trade_id or symbol}:{int(time.time()//60)}")

    # ─────────────────────────────────────────────────────────────────────────
    # KILL SWITCH
    # ─────────────────────────────────────────────────────────────────────────
    def kill_switch_triggered(
        self, source="", reason="", positions_closed=0,
        daily_pnl=0.0, **kwargs,
    ) -> bool:
        pi = "🟢" if daily_pnl >= 0 else "🔴"
        L  = [
            f"🚨 <b>KILL SWITCH ACTIVATED</b>",
            f"  Source: {source or 'Manual'}",
            f"  Reason: {reason}",
            f"  Closed: {positions_closed} positions",
            f"  {pi} P&L: ₹{daily_pnl:+,.0f}",
            f"  📱 Send /resume to restart trading",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key="kill_switch", dedup_cooldown_override=60)

    # ─────────────────────────────────────────────────────────────────────────
    # CIRCUIT BREAKER
    # ─────────────────────────────────────────────────────────────────────────
    def circuit_breaker(
        self, tripped=True, consecutive_fails=0,
        daily_pnl=0.0, daily_limit=3000.0, **kwargs,
    ) -> bool:
        pct = abs(daily_pnl) / daily_limit * 100 if daily_limit else 0
        L   = [
            f"⚡ <b>CIRCUIT BREAKER</b>  {'TRIPPED' if tripped else 'RESET'}",
            f"  P&L: ₹{daily_pnl:+,.0f}  ({pct:.0f}% of limit)",
            f"  Limit: ₹{daily_limit:,.0f}",
        ]
        if consecutive_fails:
            L.append(f"  Consecutive losses: {consecutive_fails}")
        if tripped:
            L.append(f"  ⛔ No new entries until tomorrow")
            L.append(f"  📱 /resume to override")
        L.append(f"🕐 {self._now()}")
        return self.send("\n".join(L), dedup_key=f"cb:{self._today()}", dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # EOD SQUAREOFF
    # ─────────────────────────────────────────────────────────────────────────
    def eod_squareoff(self, positions_closed=0, total_pnl=0.0, **kwargs) -> bool:
        pi = "🟢" if total_pnl >= 0 else "🔴"
        L  = [
            f"🔕 <b>EOD SQUAREOFF</b>",
            f"  Closed: {positions_closed} positions",
            f"  {pi} P&L: ₹{total_pnl:+,.0f}",
            f"  🔄 Backtest starts at 4:30 PM",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"eod:{self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # VIX SPIKE
    # ─────────────────────────────────────────────────────────────────────────
    def vix_spike_alert(self, vix=0.0, **kwargs) -> bool:
        block = vix > 22
        L = [
            f"🌡️ <b>VIX SPIKE ALERT</b>",
            f"  India VIX: {vix:.1f}",
            f"  {'⛔ Option buying BLOCKED' if block else '⚠️ Reduced position sizing'}",
            f"  📱 /vix for details",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"vix:{int(vix)}:{self._today()}", dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # DAILY LOSS WARNING
    # ─────────────────────────────────────────────────────────────────────────
    def daily_loss_warning(
        self, daily_pnl=0.0, daily_limit=3000.0, pct=None, **kwargs,
    ) -> bool:
        pct = pct or (abs(daily_pnl) / daily_limit * 100 if daily_limit else 0)
        bar = self._bar(abs(daily_pnl), daily_limit)
        L   = [
            f"⚠️ <b>LOSS WARNING</b>",
            f"  P&L:   ₹{daily_pnl:+,.0f}",
            f"  Limit: ₹{daily_limit:,.0f}",
            f"  Used:  {bar} {pct:.0f}%",
            f"  📱 /pause to stop new entries",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"loss_warn:{int(pct//10)}:{self._today()}", dedup_cooldown_override=1800)

    # ─────────────────────────────────────────────────────────────────────────
    # DRAWDOWN ALERT
    # ─────────────────────────────────────────────────────────────────────────
    def drawdown_alert(self, win_rate=0.0, max_lots=1, **kwargs) -> bool:
        L = [
            f"📉 <b>DRAWDOWN ALERT</b>",
            f"  Win rate: {win_rate*100:.0f}%",
            f"  Lots: {max_lots} (auto-reduced)",
            f"  System adapting position size",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"dd:{self._today()}", dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # LEARNING CYCLE
    # ─────────────────────────────────────────────────────────────────────────
    def learning_cycle(
        self, cycle=0, win_rate=0.0, model_score=0.0, **kwargs,
    ) -> bool:
        L = [
            f"🧠 <b>ML TRAINING COMPLETE</b>",
            f"  Cycle:      {cycle}",
            f"  Win rate:   {win_rate*100:.1f}%",
            f"  Val score:  {model_score:.3f}",
            f"  📱 /ml for model details",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"ml:{self._today()}", dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # BACKTEST REPORT
    # ─────────────────────────────────────────────────────────────────────────
    def backtest_report(self, results: dict = None, **kwargs) -> bool:
        r = results or {}
        status = r.get("status", "complete")
        L = [f"📐 <b>BACKTEST {'COMPLETE' if status != 'failed' else 'FAILED'}</b>"]
        if status == "failed":
            L.append(f"  Error: {r.get('error','?')[:60]}")
        else:
            if r.get("symbols_tested"):
                L.append(f"  Symbols:    {r['symbols_tested']}")
            if r.get("total_signals"):
                L.append(f"  Signals:    {r['total_signals']:,}")
            if r.get("overall_win_rate"):
                L.append(f"  Win rate:   {r['overall_win_rate']*100:.1f}%")
            if r.get("best_strategy"):
                L.append(f"  Best strat: {r['best_strategy']}")
            if r.get("params_updated"):
                L.append(f"  Params:     {r['params_updated']} updated")
            L.append(f"  📱 /ml for model training next")
        L.append(f"🕐 {self._now()}")
        return self.send("\n".join(L), dedup_key=f"bt:{self._today()}", dedup_cooldown_override=3600)

    # ─────────────────────────────────────────────────────────────────────────
    # WEEKLY REPORT
    # ─────────────────────────────────────────────────────────────────────────
    def weekly_report(
        self, week_str="", total_trades=0, wins=0, losses=0,
        weekly_pnl=0.0, weekly_limit=15000.0,
        best_strategy=None, worst_strategy=None,
        strategy_breakdown=None, download_success_rate=None,
        **kwargs,
    ) -> bool:
        wr = wins / total_trades * 100 if total_trades else 0
        pi = "🟢" if weekly_pnl >= 0 else "🔴"
        L  = [
            f"{'━'*34}",
            f"  📊 <b>WEEKLY SUMMARY</b>  {week_str or self._today()}",
            f"{'━'*34}",
            f"  {pi} P&L     ₹{weekly_pnl:+,.0f}",
            f"  🎯 Trades   {total_trades}  ({wins}W · {losses}L)",
            f"  📈 Win rate {wr:.0f}%  {self._wr_bar(wr)}",
        ]
        if best_strategy:
            L.append(f"  🏆 Best:    {best_strategy}")
        if worst_strategy:
            L.append(f"  👎 Worst:   {worst_strategy}")
        if download_success_rate is not None:
            icon = "🟢" if download_success_rate > 90 else "🟡" if download_success_rate > 70 else "🔴"
            L.append(f"  {icon} Data:    {download_success_rate:.0f}% downloaded OK")
        L += [
            f"{'─'*32}",
            f"  📱 /ml · /bt · /risk · /health",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"weekly:{week_str or self._today()}")

    # ─────────────────────────────────────────────────────────────────────────
    # SIGNAL REJECTION SUMMARY
    # ─────────────────────────────────────────────────────────────────────────
    def signal_rejection_summary(
        self, total_scanned=0, total_signals=0, rejected=0,
        rejection_reasons=None, top_rejections=None, **kwargs,
    ) -> bool:
        pass_rate = total_signals / total_scanned * 100 if total_scanned else 0
        L = [
            f"🔍 <b>SIGNAL SUMMARY</b>  {self._now_short()}",
            f"  Scanned:  {total_scanned}",
            f"  Signals:  {total_signals}  ({pass_rate:.1f}% pass rate)",
            f"  Rejected: {rejected}",
        ]
        if top_rejections:
            L.append("  Top reasons:")
            for r in top_rejections[:3]:
                L.append(f"    • {r}")
        L.append(f"🕐 {self._now()}")
        return self.send("\n".join(L), dedup_key=f"rejsum:{self._now_short()}:{self._today()}", dedup_cooldown_override=900)

    # ─────────────────────────────────────────────────────────────────────────
    # SHUTDOWN
    # ─────────────────────────────────────────────────────────────────────────
    def shutdown(self, reason="", bot_name="Bot") -> bool:
        L = [
            f"🛑 <b>{bot_name} STOPPED</b>",
            f"  Reason: {reason or 'Unknown'}",
            f"  📱 Restart: ./bot.sh start",
            f"🕐 {self._now()}",
        ]
        return self.send("\n".join(L), dedup_key=f"shutdown:{int(time.time()//60)}", dedup_cooldown_override=55)

    # ─────────────────────────────────────────────────────────────────────────
    # CONNECTION HEALTH
    # ─────────────────────────────────────────────────────────────────────────
    def connection_health(
        self, results: list = None, context: str = "", **kwargs,
    ) -> bool:
        results = results or []
        ok    = sum(1 for r in results if getattr(r, "ok", True))
        fails = [r for r in results if not getattr(r, "ok", True) and getattr(r, "critical", False)]
        warns = [r for r in results if not getattr(r, "ok", True) and not getattr(r, "critical", False)]
        total = len(results)
        n_f   = len(fails); n_w = len(warns)
        from datetime import time as _dtt
        _in_mkt = _dtt(9,15) <= datetime.now().time() <= _dtt(15,35)

        if n_f == 0 and n_w == 0:
            return self.send(
                f"✅ <b>ALL SYSTEMS OK</b>  {self._now()}\n   {ok}/{total} checks passed",
                dedup_key=f"health_ok:{self._today()}:{context}",
                dedup_cooldown_override=3600,
            )

        icon = "🚨" if fails else "⚠️"
        L    = [f"{icon} <b>HEALTH CHECK</b>  {context}"]
        for r in fails:
            L.append(f"  ❌ <b>{r.name}</b>: {r.detail}")
        for r in warns:
            L.append(f"  ⚠️ {r.name}: {r.detail}")
        if ok:
            L.append(f"  ✅ {ok}/{total} feeds OK")

        if n_f > 0:
            if _in_mkt:
                L += [f"  ⛔ Scanning paused — fix critical issues",
                      f"  📱 /health to recheck"]
            else:
                L += [f"  ⚠️ Fix before 9:15 AM market open",
                      f"  📱 /health to recheck anytime"]
        else:
            L.append(f"  ℹ️ Non-critical — trading continues")

        L.append(f"🕐 {self._now()}")
        dk = f"health:{n_f}:{n_w}:{self._today()}"
        return self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=1800)

    def connection_alert(
        self, check_type="STARTUP", n_ok=0, n_warn=0, n_fail=0,
        failures=None, warnings=None, safe_to_trade=True, **kwargs,
    ) -> bool:
        from datetime import time as _dtt
        _in_mkt = _dtt(9,15) <= datetime.now().time() <= _dtt(15,35)
        icon = "🟢" if n_fail==0 and n_warn==0 else "🟡" if n_fail==0 else "🔴"
        status = ("ALL SYSTEMS GO" if n_fail==0 and n_warn==0
                  else f"{n_warn} WARNING(S)" if n_fail==0
                  else f"{n_fail} CRITICAL FAILURE(S)")

        L = [
            f"{icon} <b>{check_type} CHECK — {status}</b>",
            f"  OK: {n_ok}   Warn: {n_warn}   Failed: {n_fail}",
        ]
        if failures:
            L.append("  ❌ <b>ACTION REQUIRED:</b>")
            for item in (failures or [])[:4]:
                n = item[0] if isinstance(item,(list,tuple)) else item
                d = item[1] if isinstance(item,(list,tuple)) and len(item)>1 else ""
                L.append(f"    ❌ {n}")
                if d: L.append(f"       {d[:55]}")
        if warnings:
            L.append("  ⚠️ Warnings (non-critical):")
            for item in (warnings or [])[:3]:
                n = item[0] if isinstance(item,(list,tuple)) else item
                L.append(f"    ⚠️ {n}")

        if safe_to_trade:
            L += ["  ✅ Ready for 9:15 AM market open",
                  "  📱 /health · /signals · /status"]
        else:
            if _in_mkt:
                L += ["  ⛔ Scanning paused until fixed",
                      "  📱 /connections to recheck"]
            else:
                L += ["  ⚠️ Fix before market opens",
                      "  📱 /connections to recheck"]
        L.append(f"🕐 {self._now()}")

        cooldown = 3600 if check_type == "STARTUP" else 1800
        dk = f"conn_{check_type}_{self._today()}_{n_fail}_{n_warn}"
        return self.send("\n".join(L), dedup_key=dk, dedup_cooldown_override=cooldown)

    def data_feed_restored(self, feed_name: str = "") -> bool:
        return self.send(
            f"✅ <b>FEED RESTORED</b>  {feed_name}\n"
            f"  Resuming normal operation\n🕐 {self._now()}",
            dedup_key=f"restored:{feed_name}:{self._today()}",
            dedup_cooldown_override=300,
        )

    def pre_market_ready(self, feeds_ok=0, feeds_total=0, **kwargs) -> bool:
        return self.send(
            f"✅ <b>PRE-MARKET READY</b>  {feeds_ok}/{feeds_total} feeds OK\n"
            f"  📈 Ready for 9:15 AM market open\n"
            f"  📱 /morning · /vix · /schedule\n🕐 {self._now()}",
            dedup_key=f"premkt_ready:{self._today()}",
            dedup_cooldown_override=0,
        )

    def regulatory_update(
        self, changes: list = None, effective_date: str = "01-Apr-2026", **kwargs,
    ) -> bool:
        L = [f"📋 <b>REGULATORY CHANGES — {effective_date}</b>"]
        for c in (changes or []):
            L.append(f"  {c}")
        L += [f"  ✅ System parameters updated automatically.", f"🕐 {self._now()}"]
        yrmo = f"{date.today().year}-{date.today().month:02d}"
        return self.send("\n".join(L),
                         dedup_key=f"reg:{effective_date}:{yrmo}",
                         dedup_cooldown_override=86400*30)

    def mode_change_backtest(self) -> bool:
        return self.mode_change("LIVE","BACKTEST","Market closed — nightly backtest starting")

    def high_impact_day(self, event_name="", vix=None) -> bool:
        L = [f"⚠️ <b>HIGH-IMPACT EVENT</b>  {event_name}"]
        if vix: L.append(f"  VIX: {vix:.1f}")
        L += [f"  Position sizing reduced", f"🕐 {self._now()}"]
        return self.send("\n".join(L), dedup_key=f"high_impact:{self._today()}", dedup_cooldown_override=3600)

    def silent_close_detected(self, symbol="", trade_id="", **kwargs) -> bool:
        return self.send(
            f"👻 <b>SILENT CLOSE</b>  {symbol}\n"
            f"  Position closed externally (Angel One app?)\n"
            f"  Trade: {trade_id}\n🕐 {self._now()}",
            dedup_key=f"silent:{trade_id}",
        )

    def after_hours_report(self, date_str="", **kwargs) -> bool:
        return self.send(
            f"🌙 <b>AFTER-HOURS</b>  {date_str or self._today()}\n"
            f"  Backtest: 4:30 PM\n  ML:       5:30 PM\n"
            f"  📱 /schedule · /health\n🕐 {self._now()}",
            dedup_key=f"afterhours:{date_str or self._today()}", dedup_cooldown_override=3600,
        )

    def capital_milestone(self, capital=0.0, milestone=0.0, growth_pct=0.0, **kwargs) -> bool:
        return self.send(
            f"🎉 <b>CAPITAL MILESTONE</b>\n"
            f"  Capital: ₹{self._inr(capital)}\n"
            f"  Growth:  {growth_pct:+.1f}%\n🕐 {self._now()}",
            dedup_key=f"milestone:{int(milestone//1000)}",
        )

    def margin_warning(self, margin_used=0.0, margin_available=0.0, **kwargs) -> bool:
        pct = margin_used/(margin_used+margin_available)*100 if margin_available else 0
        return self.send(
            f"⚠️ <b>MARGIN WARNING</b>\n"
            f"  Used:  ₹{self._inr(margin_used)}  ({pct:.0f}%)\n"
            f"  Free:  ₹{self._inr(margin_available)}\n🕐 {self._now()}",
            dedup_key=f"margin:{int(pct//10)}:{self._today()}", dedup_cooldown_override=1800,
        )

    def system_status(self, pid=0, memory_mb=0.0, disk_gb=0.0, uptime_h=0.0, **kwargs) -> bool:
        return self.send(
            f"💻 <b>SYSTEM STATUS</b>\n"
            f"  PID:    {pid}\n"
            f"  RAM:    {memory_mb:.0f} MB\n"
            f"  Disk:   {disk_gb:.1f} GB free\n"
            f"  Uptime: {uptime_h:.1f}h\n🕐 {self._now()}",
            dedup_key=f"sysstat:{self._today()}:{int(uptime_h//1)}", dedup_cooldown_override=3600,
        )

    def data_recovery_alert(self, kind="", name="", detail="") -> bool:
        return self.data_feed_restored(name)
