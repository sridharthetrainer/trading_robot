"""
telegram_commands.py — Telegram Bot Command Handler (Incoming Messages)

Listens for messages sent TO the bot and responds intelligently.
Runs as background thread using long-polling.

COMMANDS:
  /status          — Current P&L, positions, system health
  /pnl             — Today's full P&L breakdown
  /signals         — Last 5 signals fired
  /positions       — Open positions with unrealized P&L
  /pause           — Pause new entries (risk management)
  /resume          — Resume entries
  /kill            — Emergency kill switch (close all)
  /backtest        — Trigger immediate backtest
  /train           — Trigger immediate ML training
  /health          — System health (CPU, memory, connections)
  /downloads       — Today's download report
  /weekly          — Weekly performance summary
  /vix             — Current VIX and market breadth
  /symbols         — How many symbols being scanned
  /help            — List all commands
"""
from __future__ import annotations

# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)

import os

import logging
import threading
import time
from datetime import datetime, date
from typing import Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)
_POLL_TIMEOUT = 20  # long-poll seconds
_POLL_INTERVAL = 2


class TelegramCommandHandler:
    """Polls Telegram for incoming messages and responds."""

    def __init__(
        self,
        bot_token:  str,
        chat_id:    str,
        bot_ref     = None,   # reference to main AutonomousBot
    ) -> None:
        self.bot_token  = bot_token
        self.chat_id    = str(chat_id)
        self.bot_ref    = bot_ref
        self._offset    = 0
        self._running   = False
        self._thread:   Optional[threading.Thread] = None
        self._handlers: Dict[str, Callable] = {}
        self._poll_failures = 0
        self._last_poll_ok_at = 0.0
        self._last_poll_error = ""
        self._last_update_at = 0.0
        self._last_api_error = ""
        self._register_defaults()

    # ── Telegram API ──────────────────────────────────────────────────────────
    def _api(self, method: str, **params) -> dict:
        # Refresh token from env if empty or placeholder
        if not self.bot_token or self.bot_token in ("", "None", "REPLACE_WITH_YOUR_TOKEN_FROM_BOTFATHER", "PASTE_NEW_TOKEN_HERE"):
            import os as _os_api
            fresh = _os_api.getenv("TELEGRAM_BOT_TOKEN", "")
            if fresh and fresh not in ("", "None", "REPLACE_WITH_YOUR_TOKEN_FROM_BOTFATHER"):
                self.bot_token = fresh
                logger.info("Token refreshed from env: %s...", fresh[:20])
        try:
            request_timeout = 25
            if method == "getUpdates":
                try:
                    request_timeout = int(params.get("timeout", _POLL_TIMEOUT)) + 10
                except Exception:
                    request_timeout = _POLL_TIMEOUT + 10
                request_timeout = max(10, min(60, request_timeout))
            r = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/{method}",
                json=params, timeout=request_timeout
            )
            try:
                data = r.json()
            except Exception:
                body = (r.text or "")[:200].replace("\n", " ")
                self._last_api_error = f"non-json status={r.status_code}"
                logger.warning("TG API %s returned non-json status=%s body=%s",
                               method, r.status_code, body)
                return {"ok": False, "error_code": r.status_code,
                        "description": self._last_api_error}

            if not data.get("ok", False):
                desc = str(data.get("description", "unknown error"))[:240]
                self._last_api_error = desc
                logger.warning("TG API %s failed status=%s code=%s desc=%s",
                               method, r.status_code, data.get("error_code"), desc)
            else:
                self._last_api_error = ""
            return data
        except Exception as e:
            self._last_api_error = str(e)[:240]
            logger.warning("TG API %s request failed: %s", method, self._last_api_error)
            return {"ok": False, "description": self._last_api_error}

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%H:%M %d-%b")

    def send(self, text: str, chat_id: str = None) -> bool:
        target = str(chat_id) if chat_id else self.chat_id
        r = self._api("sendMessage", chat_id=target,
                      text=text[:4096], parse_mode="HTML")
        if r.get("ok", False):
            return True

        desc = str(r.get("description", "")).lower()
        if "parse" in desc or "entity" in desc or "can't parse" in desc:
            import re
            plain = re.sub(r"</?[^>]+>", "", text)[:4096]
            retry = self._api("sendMessage", chat_id=target, text=plain)
            if retry.get("ok", False):
                logger.info("Telegram reply sent with plain-text fallback")
                return True

        logger.warning("Telegram sendMessage failed for chat=%s: %s",
                       target, r.get("description", "unknown error"))
        return False

    # ── Polling ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running:
            return
        # Step 1: Validate token
        try:
            me = self._api("getMe")
            if not me.get("ok"):
                desc = me.get("description","unknown error")
                logger.error(
                    "🚨 TELEGRAM TOKEN INVALID: %s\n"
                    "  Get new token: @BotFather → /mybots → API Token\n"
                    "  Update .env → ./bot.sh restart", desc
                )
                return
            bot_name = me.get("result",{}).get("username","?")
            logger.info("Telegram bot @%s connected ✅", bot_name)
        except Exception as e:
            logger.error("Token validation failed: %s", e)
            return

        # Step 2: Check for existing webhook (blocks getUpdates if set)
        try:
            wh = self._api("getWebhookInfo")
            wh_url = wh.get("result",{}).get("url","")
            if wh_url:
                logger.warning(
                    "⚠️  Webhook is SET to: %s — this blocks getUpdates!\n"
                    "  Deleting webhook now to enable polling...", wh_url
                )
                del_result = self._api("deleteWebhook", drop_pending_updates=True)
                if del_result.get("ok"):
                    logger.info("✅ Webhook deleted — polling enabled")
                else:
                    logger.error("❌ Failed to delete webhook: %s", del_result)
            else:
                logger.info("No webhook set — polling mode OK ✅")
        except Exception as e:
            logger.warning("Webhook check failed: %s", e)

        # Step 3: Start polling thread
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop,
            daemon=True, name="TGCmdHandler"
        )
        self._thread.start()
        logger.info("Telegram command handler started — listening for commands")

        # Step 4: Send startup confirmation to owner
        try:
            self.send(
                "🤖 <b>Bot command handler online</b>\n"
                "  Listening for your commands\n"
                "  Try: /health /status /signals"
            )
        except Exception: pass

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def health(self) -> dict:
        now = time.time()
        return {
            "running": self._running,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "offset": self._offset,
            "poll_failures": self._poll_failures,
            "last_poll_ok_age_sec": round(now - self._last_poll_ok_at, 1)
            if self._last_poll_ok_at else None,
            "last_update_age_sec": round(now - self._last_update_at, 1)
            if self._last_update_at else None,
            "last_error": self._last_poll_error or self._last_api_error,
        }

    def _poll_loop(self) -> None:
        while self._running:
            try:
                resp = self._api("getUpdates", offset=self._offset,
                                 timeout=_POLL_TIMEOUT, allowed_updates=["message"])
                if not resp.get("ok", False):
                    self._poll_failures += 1
                    desc = str(resp.get("description") or
                               self._last_api_error or "empty response")
                    self._last_poll_error = desc[:240]
                    code = resp.get("error_code")
                    desc_l = desc.lower()
                    if code == 409 or "conflict" in desc_l or "other getupdates" in desc_l:
                        logger.error(
                            "Telegram polling conflict: another getUpdates consumer "
                            "is using this bot token; commands may be consumed elsewhere."
                        )
                        time.sleep(15)
                        continue
                    if self._poll_failures in (1, 3, 10) or self._poll_failures % 30 == 0:
                        logger.warning("Telegram polling unhealthy: failures=%d desc=%s",
                                       self._poll_failures, self._last_poll_error)
                    time.sleep(min(30, 2 + self._poll_failures))
                    continue

                self._poll_failures = 0
                self._last_poll_ok_at = time.time()
                self._last_poll_error = ""
                updates = resp.get("result", [])
                if updates:
                    logger.info("Telegram poll received %d update(s)", len(updates))
                for update in updates:
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)
            except Exception as e:
                err_str = str(e)
                if "403" in err_str or "Forbidden" in err_str or "Unauthorized" in err_str or "401" in err_str:
                    logger.error("🚨 TELEGRAM TOKEN INVALID: %s — get new token from @BotFather", e)
                    import time as _tw; _tw.sleep(60)  # slow retry on auth failure
                else:
                    logger.warning("Poll loop error: %s", e)
            time.sleep(_POLL_INTERVAL)

    def _handle_update(self, update: dict) -> None:
        self._last_update_at = time.time()
        msg  = update.get("message", {})
        text = str(msg.get("text", "")).strip()
        from_id = str(msg.get("from", {}).get("id", ""))
        chat_id  = str(msg.get("chat", {}).get("id", ""))

        # Log ALL received messages for diagnostics
        logger.info("MSG IN: chat=%s from=%s text=%s owner_cfg=%s",
                    chat_id, from_id, repr(text[:40]), repr(self.chat_id))

        # Owner check — accept if from_id or chat_id matches configured owner
        # Also accept if chat_id is empty/unconfigured (fail-open for diagnosis)
        owner_id = str(self.chat_id or "").strip().lstrip("-")
        is_owner_msg = (
            not owner_id  # no owner configured → accept all (safe: commands still require /)
            or chat_id == self.chat_id
            or from_id == owner_id
            or from_id == (self.chat_id or "").strip()
            or str(chat_id).lstrip("-") == owner_id
        )
        if not is_owner_msg:
            logger.warning("REJECTED msg: chat=%s from=%s (owner=%s) text=%s",
                           chat_id, from_id, self.chat_id, repr(text[:30]))
            return

        if not text:
            return

        # Extract command
        cmd = text.split()[0].lower().lstrip("/").split("@")[0]
        logger.info("CMD RECEIVED: /%s from chat=%s user=%s", cmd, chat_id, from_id)
        handler = self._handlers.get(cmd)
        if handler:
            try:
                response = handler(text)
                if response:
                    # Reply to the chat the command came from
                    self.send(response, chat_id=chat_id)
            except Exception as e:
                # UX-2: Friendly error messages
                err = str(e)
                logger.warning("CMD ERROR /%s: %s", cmd, err)
                if "connect" in err.lower() or "timeout" in err.lower():
                    self.send("⚠️ Connection issue — retry in 30s", chat_id=chat_id)
                elif "not found" in err.lower() or "no such" in err.lower():
                    self.send("⚠️ Data unavailable — try after 9:15 AM", chat_id=chat_id)
                else:
                    self.send(f"⚠️ /{cmd} error: {err[:80]}", chat_id=chat_id)
        elif text.startswith("/"):
            self.send(f"❓ Unknown: /{cmd} — try /help", chat_id=chat_id)


    # ── Handler registration ──────────────────────────────────────────────────
    def register(self, command: str, handler: Callable) -> None:
        self._handlers[command.lstrip("/")] = handler

    def _register_defaults(self) -> None:
        # ── Core ─────────────────────────────────────────────────────────────
        self.register("help",        self._cmd_help)
        self.register("start",       self._cmd_start)
        self.register("status",      self._cmd_status)
        self.register("health",      self._cmd_health)
        self.register("state",       self._cmd_state)
        self.register("mode",        self._cmd_mode)
        self.register("version",     self._cmd_version)
        self.register("debug",       self._cmd_debug)
        self.register("log",         self._cmd_log)
        self.register("update",      self._cmd_update)
        self.register("restart",     self._cmd_restart)
        # ── Monitor ──────────────────────────────────────────────────────────
        self.register("pnl",         self._cmd_pnl)
        self.register("live",        self._cmd_live_positions)
        self.register("live_pos",    self._cmd_live_positions)
        self.register("positions",   self._cmd_positions)
        self.register("signals",     self._cmd_signals)
        self.register("today",       self._cmd_today)
        self.register("missed",      self._cmd_missed)
        self.register("heat",        self._cmd_heat)
        self.register("symbols",     self._cmd_symbols)
        # ── Morning / Market Context ──────────────────────────────────────────
        self.register("morning",     self._cmd_morning)
        self.register("brief",       self._cmd_brief)
        self.register("premarket",   self._cmd_brief)
        self.register("vix",         self._cmd_vix)
        self.register("regime",      self._cmd_regime)
        self.register("sentiment",   self._cmd_sentiment)
        self.register("sectors",     self._cmd_sectors)
        self.register("sector_live", self._cmd_sectors)
        self.register("rotation",    self._cmd_sectors)
        self.register("fii",         self._cmd_fii)
        self.register("fii_dii",     self._cmd_fii)
        self.register("dii",         self._cmd_fii)
        self.register("gift",        self._cmd_gift_nifty)
        self.register("giftnifty",   self._cmd_gift_nifty)
        self.register("macro",       self._cmd_macro)
        self.register("macro_data",  self._cmd_macro)
        self.register("earnings",    self._cmd_earnings_calendar)
        self.register("results",     self._cmd_earnings_calendar)
        # ── OI / Options ─────────────────────────────────────────────────────
        self.register("oi",          self._cmd_oi)
        self.register("oib",         self._cmd_oi)
        self.register("strikes",     self._cmd_oi)
        self.register("oitrend",     self._cmd_oitrend)
        self.register("oit",         self._cmd_oitrend)
        self.register("oitt",        self._cmd_oitrend)
        self.register("pcr",         self._cmd_pcr)
        self.register("putcallratio",self._cmd_pcr)
        self.register("fnoban",      self._cmd_fnoban)
        self.register("ban",         self._cmd_fnoban)
        # ── Deep Intelligence ────────────────────────────────────────────────
        self.register("intel",       self._cmd_intelligence)
        self.register("intelligence",self._cmd_intelligence)
        self.register("omni",        self._cmd_intelligence)
        self.register("orderflow",   self._cmd_orderflow)
        self.register("of",          self._cmd_orderflow)
        self.register("darkpool",    self._cmd_darkpool)
        self.register("dp",          self._cmd_darkpool)
        self.register("hmm",         self._cmd_hmm)
        self.register("waves",       self._cmd_elliott)
        self.register("elliott",     self._cmd_elliott)
        self.register("meta",        self._cmd_metalearner)
        self.register("weights",     self._cmd_metalearner)
        self.register("fiipos",      self._cmd_fiipos)
        self.register("insider",     self._cmd_insider)
        self.register("promoter",    self._cmd_insider)
        self.register("social",      self._cmd_social)
        self.register("reddit",      self._cmd_social)
        self.register("news",        self._cmd_news)
        self.register("commodities", self._cmd_commodities)
        self.register("commodity",   self._cmd_commodities)
        self.register("corpactions", self._cmd_corpactions)
        self.register("ca",          self._cmd_corpactions)
        self.register("wow",         self._cmd_wow2)
        self.register("wow2",        self._cmd_wow2)
        self.register("wowv2",       self._cmd_wow2)
        self.register("score",       self._cmd_market_score)
        self.register("market_score",self._cmd_market_score)
        self.register("health_score",self._cmd_market_score)
        self.register("mkt",         self._cmd_market_score)
        self.register("mkt_score",   self._cmd_sentiment_score)
        # ── Manual trade entry/management via TradeGuardian (single poller) ────
        self.register("in",      lambda t="": self._guardian_call("_cmd_in", t))
        self.register("out",     lambda t="": self._guardian_call("_cmd_out", t))
        self.register("sl",      lambda t="": self._guardian_call("_cmd_sl", t))
        self.register("target",  lambda t="": self._guardian_call("_cmd_target", t))
        self.register("protect", lambda t="": self._guardian_call("_cmd_protect", t))
        self.register("hold",    lambda t="": self._guardian_call("_cmd_hold", t))
        self.register("gtrades", lambda t="": self._guardian_call("_cmd_trades", t))
        # ── Performance & Analytics ───────────────────────────────────────────
        self.register("weekly",      self._cmd_weekly_perf)
        self.register("week",        self._cmd_weekly_perf)
        self.register("analytics",   self._cmd_analytics)
        self.register("hourly",      self._cmd_analytics)
        self.register("perf",        self._cmd_analytics)
        self.register("compare",     self._cmd_compare)
        self.register("benchmark",   self._cmd_compare_benchmark)
        self.register("alpha",       self._cmd_compare_benchmark)
        self.register("sharpe",      self._cmd_sharpe)
        self.register("metrics",     self._cmd_sharpe)
        self.register("streak",      self._cmd_streak)
        self.register("attribution", self._cmd_attribution)
        self.register("attr",        self._cmd_attribution)
        self.register("eod",         self._cmd_eod_summary)
        self.register("downloads",   self._cmd_downloads)
        self.register("export",      self._cmd_export)
        self.register("download",    self._cmd_export_trades)
        self.register("schedule",    self._cmd_schedule)
        self.register("next",        self._cmd_next)
        # ── ML / Backtest ─────────────────────────────────────────────────────
        self.register("backtest",    self._cmd_backtest)
        self.register("bt",          self._cmd_backtest)
        self.register("train",       self._cmd_train)
        self.register("ml",          self._cmd_ml)
        self.register("calibrate",   self._cmd_calibrate)
        self.register("accuracy",    self._cmd_calibrate)
        self.register("risk",        self._cmd_risk)
        self.register("var",         self._cmd_risk)
        self.register("stt",         self._cmd_stt)
        self.register("breakeven",   self._cmd_stt)
        self.register("charges",     self._cmd_stt)
        self.register("rollover",    self._cmd_rollover)
        self.register("carry",       self._cmd_rollover)
        self.register("gaps",        self._cmd_gap_warning)
        self.register("gapcheck",    self._cmd_gap_warning)
        self.register("diagscan",    self._cmd_diag_scan)
        # ── Control ───────────────────────────────────────────────────────────
        self.register("pause",       self._cmd_pause)
        self.register("resume",      self._cmd_resume)
        self.register("arm",         self._cmd_arm)
        self.register("disarm",      self._cmd_disarm)
        self.register("kill",        self._cmd_kill)
        self.register("paper",       self._cmd_paper)
        self.register("shadow",      self._cmd_shadow_mode)
        self.register("shadow_mode", self._cmd_shadow_mode)
        self.register("pause_sym",      self._cmd_pause_symbol)
        self.register("resume_sym",     self._cmd_pause_symbol)
        self.register("pause_strategy",  self._cmd_pause_strategy)
        self.register("ps",              self._cmd_pause_strategy)
        self.register("paused",          self._cmd_pause_strategy)
        self.register("strategy_health", self._cmd_strategy_health)
        self.register("sh",              self._cmd_strategy_health)
        self.register("blacklist",   self._cmd_blacklist)
        self.register("banned",      self._cmd_blacklist)
        self.register("buy",         self._cmd_manual_buy)
        self.register("sell",        self._cmd_manual_sell)
        self.register("exit",        self._cmd_exit_all)
        self.register("close",       self._cmd_exit_all)
        # ── Tools ─────────────────────────────────────────────────────────────
        self.register("calculate",   self._cmd_calculate)
        self.register("calc",        self._cmd_calculate)
        self.register("size",        self._cmd_calculate)
        self.register("alert",       self._cmd_alert)
        self.register("alerts",      self._cmd_alerts)
        self.register("watch",       self._cmd_watch)
        self.register("watchlist",   self._cmd_watch)
        self.register("voice",       self._cmd_voice)
        self.register("audio",       self._cmd_voice)
        self.register("video",       self._cmd_video)
        self.register("brief_video", self._cmd_video)
        self.register("why",         self._cmd_why)
        self.register("reason",      self._cmd_why)
        self.register("diagnose",    self._cmd_diagnose)
        self.register("nosignals",   self._cmd_diagnose)
        # ── Cloud / Backup ────────────────────────────────────────────────────
        self.register("backup",      self._cmd_backup)
        self.register("github",      self._cmd_github)
        self.register("gitpush",     self._cmd_github)
        self.register("push",        self._cmd_github)
        self.register("sync",        self._cmd_sync)
        self.register("drivesync",   self._cmd_sync)
        self.register("cloud",       self._cmd_drive_status)
        self.register("drivestatus", self._cmd_drive_status)
        self.register("deploy",      self._cmd_remote_deploy)
        self.register("pull",        self._cmd_remote_deploy)
        self.register("datasources", self._cmd_datasource_health)
        self.register("source_health",self._cmd_datasource_health)
        self.register("connections", self._cmd_connections)
        self.register("conn",        self._cmd_conn)
        # ── Config / Subscription ─────────────────────────────────────────────
        self.register("config",      self._cmd_config)
        self.register("settings",    self._cmd_config)
        self.register("setcapital",  self._cmd_setcapital)
        self.register("capital",     self._cmd_setcapital)
        self.register("setcap",      self._cmd_setcapital)
        self.register("setthreshold",self._cmd_setthreshold)
        self.register("threshold",   self._cmd_setthreshold)
        self.register("trial",       self._cmd_trial)
        self.register("freetrial",   self._cmd_trial)
        self.register("addsub",      self._cmd_add_subscriber)
        self.register("addpaid",     self._cmd_add_subscriber)
        self.register("re_entry_status", self._cmd_re_entry_status)
        self.register("reentry",     self._cmd_re_entry_status)
        self.register("cooldown",    self._cmd_re_entry_status)
        self.register("onboard",     self._cmd_subscribe_flow)
        self.register("myplan",      self._cmd_my_plan)
        self.register("plan",        self._cmd_my_plan)
        self.register("subscribers", self._cmd_subscribers)
        self.register("subs",        self._cmd_subscribers)
        self.register("churn",       self._cmd_churn)
        # ── Broker ────────────────────────────────────────────────────────────
        self.register("broker",      self._cmd_broker)
        self.register("brokers",     self._cmd_broker)
        self.register("dhan",        self._cmd_dhan_status)
        self.register("dhan_setup",  self._cmd_dhan_setup)
        self.register("zerodha",     self._cmd_zerodha_status)
        self.register("kite",        self._cmd_zerodha_status)
        self.register("fixangel",    self._cmd_fix_angel)
        self.register("angelcheck",  self._cmd_fix_angel)
        self.register("session",     self._cmd_session)
        self.register("refresh_token",self._cmd_session)
        # ── Tax / Export ──────────────────────────────────────────────────────
        self.register("export_tax",  self._cmd_export_tax)
        self.register("tax",         self._cmd_export_tax)
        self.register("itr",         self._cmd_export_tax)

    # ── Command implementations ───────────────────────────────────────────────
    def _cmd_help(self, _="") -> str:
        return (
            "📱 <b>TRADING BOT — ALL COMMANDS</b>\n\n"
            "📊 <b>Monitor</b>\n"
            "  /status  /pnl  /live  /positions\n"
            "  /signals  /today  /missed  /heat\n\n"
            "🌅 <b>Morning / Market</b>\n"
            "  /morning  /brief  /vix  /regime\n"
            "  /sentiment  /sectors  /fii  /gift\n"
            "  /macro  /earnings  /score\n\n"
            "📈 <b>OI / Options</b>\n"
            "  /oi  /oitrend  /pcr  /fnoban\n\n"
            "🧠 <b>Intelligence</b>\n"
            "  /intel  /orderflow  /darkpool\n"
            "  /hmm  /waves  /meta  /fiipos\n"
            "  /insider  /social  /news\n"
            "  /commodities  /corpactions  /wow\n\n"
            "💹 <b>Performance</b>\n"
            "  /weekly  /analytics  /compare\n"
            "  /sharpe  /streak  /attribution\n"
            "  /eod  /downloads  /export\n\n"
            "🤖 <b>ML / Backtest</b>\n"
            "  /bt  /train  /ml  /calibrate\n"
            "  /risk  /stt  /rollover\n"
            "  /gaps  /diagscan  /why\n\n"
            "🔧 <b>Control</b>\n"
            "  /pause  /resume  /kill  /mode\n"
            "  /paper  /shadow  /pause_sym\n"
            "  /pause_strategy  /buy  /sell  /exit\n\n"
            "🛠️ <b>Tools</b>\n"
            "  /calculate  /alert  /alerts\n"
            "  /watch  /voice  /video\n"
            "  /schedule  /next  /symbols\n\n"
            "☁️ <b>Cloud</b>\n"
            "  /backup  /github  /sync  /deploy\n"
            "  /cloud  /datasources  /connections\n\n"
            "⚙️ <b>Config</b>\n"
            "  /config  /capital  /threshold\n"
            "  /broker  /dhan  /zerodha\n"
            "  /session  /tax  /reentry\n"
            "  /health  /log  /restart  /version\n\n"
            "💡 Most commands accept a symbol: <code>/signals RELIANCE</code>"
        )

    def _cmd_status(self, _="") -> str:
        """Status command - uses timeout to avoid deadlocking on shared objects"""
        try:
            import threading
            result = ["⏳ Fetching status..."]
            
            def _get_live_status():
                try:
                    bot = self.bot_ref
                    if not bot:
                        result.clear()
                        result.append("⚠️ Bot reference not set")
                        return
                    
                    # Use timeout to prevent deadlock
                    tm  = bot.live_engine.trade_manager
                    pnl = tm.daily_realized_pnl if hasattr(tm,'daily_realized_pnl') else 0
                    n_open = len(tm.open_trades) if hasattr(tm,'open_trades') else 0
                    mode  = str(getattr(bot.runtime_state,'mode','PAPER'))
                    e     = "🟢" if pnl >= 0 else "🔴"
                    
                    result.clear()
                    result.append(
                        f"📊 <b>SYSTEM STATUS</b>  {datetime.now().strftime('%H:%M')}\n"
                        f"Mode: <b>{mode}</b>\n"
                        f"{e} Day P&L: <b>₹{pnl:+,.0f}</b>\n"
                        f"🔓 Open: {n_open}\n"
                        f"🕐 {datetime.now().strftime('%d-%b %H:%M:%S')}"
                    )
                except Exception as e:
                    result.clear()
                    result.append(f"⚠️ Status fetch: {str(e)[:60]}")
            
            # Fetch with 3 second timeout - if it takes longer, just return "Fetching..."
            thread = threading.Thread(target=_get_live_status, daemon=True)
            thread.start()
            thread.join(timeout=3)  # CRITICAL: timeout prevents deadlock
            
            return result[0] if result else "⏳ Status (timeout - try again)"
        except Exception as e:
            return f"⚠️ Status error: {str(e)[:80]}"

    def _cmd_pnl(self, args="") -> str:
        """Institutional P&L report."""
        try:
            from performance_analytics import format_telegram_report as _pa
            days = int(args.strip()) if args.strip().isdigit() else 30
            return _pa(days)
        except Exception as e:
            return f"❌ P&L: {e}"

    def _cmd_pnl_OLD(self, _="") -> str:
        try:
            bot = self.bot_ref
            if not bot: return "⚠️ No bot ref"
            tm   = bot.live_engine.trade_manager
            pnl  = getattr(tm,'daily_realized_pnl',0)
            trades = tm.get_today_closed_trades() if hasattr(tm,'get_today_closed_trades') else []
            lines = [f"💰 <b>TODAY'S P&L</b>  {date.today()}",
                     f"{'🟢' if pnl>=0 else '🔴'} Net: <b>₹{pnl:+,.0f}</b>",
                     f"{'─'*28}"]
            for t in trades[-8:]:
                sym = t.get('symbol','?')
                tp  = float(t.get('pnl',0))
                lines.append(f"  {'✅' if tp>0 else '❌'} {sym:<12} ₹{tp:+,.0f}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ {e}"

    def _ensure_guardian(self):
        """Lazy-init TradeGuardian (manual /in trades) using THIS bot's send fn and
        start its monitor thread — so manual trades are managed without running the
        standalone trade_guardian poller (which would conflict on getUpdates)."""
        import trade_guardian_bot as _tgb
        if getattr(_tgb, "_guardian", None) is None:
            from trade_guardian import TradeGuardian
            g = TradeGuardian(send_fn=self.send)
            try:
                g.start()
            except Exception:
                pass
            _tgb._guardian = g
        return _tgb

    def _guardian_call(self, handler_name: str, args: str = "") -> str:
        """Route a /in-family command to the TradeGuardian handler in-process."""
        try:
            tgb = self._ensure_guardian()
            return getattr(tgb, handler_name)(args or "")
        except Exception as e:
            return f"Guardian: {e}"

    def _cmd_positions(self, _="") -> str:
        """Show live positions with real-time P&L from websocket."""
        try:
            # Try websocket tracker first (real-time)
            try:
                from websocket_tracker import WebSocketTracker
                if hasattr(self, "bot_ref") and self.bot_ref:
                    ws = getattr(self.bot_ref, "_ws_tracker", None)
                    if ws:
                        pnl = ws.get_live_pnl()
                        if pnl:
                            lines = ["<b>LIVE POSITIONS</b> (real-time)", ""]
                            total_pnl = 0
                            for sym, data in pnl.items():
                                icon = "\U0001f7e2" if data["pnl"] >= 0 else "\U0001f534"
                                be = " BE" if data["breakeven"] else ""
                                t1 = " T1\u2713" if data["t1_hit"] else ""
                                lines.append(
                                    f"  {icon} {sym}\n"
                                    f"     {data["side"]} {data["qty"]} @ \u20b9{data["entry"]:,.2f}\n"
                                    f"     Now: \u20b9{data["current"]:,.2f}  P&L: \u20b9{data["pnl"]:+,.0f} ({data["pnl_pct"]:+.1f}%){be}{t1}\n"
                                    f"     SL: \u20b9{data["sl"]:,.2f}  T: \u20b9{data["target"]:,.2f}"
                                )
                                total_pnl += data["pnl"]
                            lines += ["", f"  Total P&L: \u20b9{total_pnl:+,.0f}"]
                            return "\n".join(lines)
            except Exception: pass
            # Fallback: trade manager instance (get_open_positions is a METHOD,
            # not a module function — the old module-level import always failed)
            _bot = getattr(self, "bot_ref", None)
            positions = _bot.live_engine.trade_manager.get_open_positions() if _bot else []
            if not positions:
                return "No open positions\n/signals to see recent signals"
            lines = ["<b>OPEN POSITIONS</b>", ""]
            for p in positions:
                lines.append(f"  {p.get("symbol")} {p.get("side")} {p.get("qty")} @ \u20b9{p.get("entry_price",0):,.2f}")
            return "\n".join(lines)
        except Exception as e:
            return f"Positions: {e}"

    def _cmd_signals(self, _="") -> str:
        """Show recent signals from strategy_scores or trades."""
        try:
            import sqlite3
            conn = sqlite3.connect("trades.db", check_same_thread=False)
            rows = []
            for table, query in [
                ("strategy_scores",
                 "SELECT symbol,strategy,score,direction,regime,timestamp FROM strategy_scores WHERE score>3 ORDER BY timestamp DESC,score DESC LIMIT 10"),
                ("signal_log",
                 "SELECT symbol,strategy,score,side,confluence,signal_time FROM signal_log ORDER BY id DESC LIMIT 10"),
                ("trades",
                 "SELECT symbol,strategy,0,side,status,entry_time FROM trades ORDER BY id DESC LIMIT 10"),
            ]:
                if rows: break
                try: rows = conn.execute(query).fetchall()
                except Exception: pass
            conn.close()
            if not rows:
                return ("📡 <b>NO SIGNALS YET</b>\n\n"
                        "  Scan runs every 5 min during market hours\n"
                        "  Signals appear when score ≥ 5.5\n\n"
                        "  📱 /health to check data feed")
            lines_out = ["📡 <b>RECENT SIGNALS</b>", ""]
            for r in rows:
                sym = str(r[0])
                strat = str(r[1])[:18]
                score = float(r[2] or 0)
                dirn = str(r[3] or "")
                icon = "🟢" if dirn.upper() in ("BUY","BULLISH") else "🔴" if dirn.upper() in ("SELL","BEARISH") else "⚪"
                lines_out.append(f"  {icon} {sym:12} {strat:18} {score:.1f} {dirn}")
            lines_out += ["", "  📱 /today · /positions · /pnl"]
            return "\n".join(lines_out)
        except Exception as e:
            return f"Signals: {e}"

    def _cmd_pause(self, _="") -> str:
        try:
            import config as cfg
            cfg._PAUSED = True
            return "⏸ <b>NEW ENTRIES PAUSED</b>\nExisting positions continue.\nSend /resume to restart."
        except Exception as e:
            return f"⚠️ {e}"

    def _cmd_resume(self, _="") -> str:
        try:
            import config as cfg
            cfg._PAUSED = False
            return "▶️ <b>ENTRIES RESUMED</b>\nBot will take new signals."
        except Exception as e:
            return f"⚠️ {e}"

    def _cmd_arm(self, _="") -> str:
        try:
            from dual_mode_engine import arm_live_trading
            today = arm_live_trading()
            return ("🔴 <b>LIVE TRADING ARMED</b> for today (%s)\n"
                    "Real orders will fire alongside paper while funded.\n"
                    "Auto-disarms tomorrow. Send /disarm to stop now." % today)
        except Exception as e:
            return f"⚠️ arm failed: {e}"

    def _cmd_disarm(self, _="") -> str:
        try:
            from dual_mode_engine import disarm_live_trading
            disarm_live_trading()
            return "🟢 <b>LIVE DISARMED</b> — paper only. No real orders."
        except Exception as e:
            return f"⚠️ disarm failed: {e}"

    def _cmd_kill(self, _="") -> str:
        try:
            bot = self.bot_ref
            if not bot: return "⚠️ No bot ref"
            n = bot.live_engine.trade_manager.close_all_trades(reason="telegram_kill")
            return f"🚨 <b>KILL SWITCH</b>\nClosed {n} positions via Telegram command."
        except Exception as e:
            return f"⚠️ Kill failed: {e}"

    def _cmd_backtest(self, _="") -> str:
        """Run autonomous backtest safely in background thread."""
        try:
            import time as _tbt
            # Check if already running
            if getattr(self, "_bt_running", False):
                return "⚠️ Backtest already running — please wait"

            def _run_bt_safe():
                self._bt_running = True
                try:
                    from autonomous_backtest import get_backtest as _gbt
                    _gbt(alerts=getattr(self, "alerts", None)).run()
                except SystemExit:
                    pass  # safe — only kills this thread
                except Exception as _e:
                    import logging
                    logging.getLogger("telegram_bt").warning("Backtest error: %s", _e)
                finally:
                    self._bt_running = False

            t = threading.Thread(target=_run_bt_safe, name="bt_manual", daemon=True)
            t.start()
            return (
                "📐 <b>BACKTEST TRIGGERED</b>\n"
                "Fine-tuning 190+ symbols with real NSE data\n"
                "⏱ Takes ~5 minutes | Results on Telegram when done"
            )
        except Exception as e:
            return f"⚠️ Backtest error: {e}"

    def _cmd_train(self, _="") -> str:
        """Run ML training safely."""
        try:
            if getattr(self, "_train_running", False):
                return "⚠️ Training already running"

            def _run_train():
                self._train_running = True
                try:
                    from self_learning_engine import SelfLearningEngine
                    import config as _cfg_t
                    SelfLearningEngine(
                        strategy_state_file=getattr(_cfg_t,"STRATEGY_STATE_FILE","strategy_state.json")
                    ).run()
                except SystemExit:
                    pass
                except Exception as _e:
                    import logging
                    logging.getLogger("telegram_train").warning("Train error: %s", _e)
                finally:
                    self._train_running = False

            threading.Thread(target=_run_train, name="train_manual", daemon=True).start()
            return "🧠 <b>ML TRAINING TRIGGERED</b>\nResults in ~5 min on Telegram."
        except Exception as e:
            return f"⚠️ Training error: {e}"

    def _cmd_health(self, _="") -> str:
        """System health: CPU, memory, connections."""
        from datetime import datetime as _dt
        lines = []
        try:
            from connection_monitor import get_monitor, CHECKS_FULL
            mon     = get_monitor()
            result  = mon.run_full_check()
            n_ok    = result.get("ok", 0)
            n_warn  = result.get("warnings", 0)
            n_fail  = result.get("failures", 0)
            total   = n_ok + n_warn + n_fail
            safe    = result.get("safe_to_trade", True)
            icon    = "✅" if n_fail == 0 and n_warn == 0 else ("🚨" if n_fail > 0 else "⚠️")

            lines += [
                f"{icon} <b>SYSTEM HEALTH</b>",
                f"{'─'*32}",
                f"  Connections: {n_ok}/{total} OK",
                f"{'─'*32}",
            ]

            # Show per-check results using CHECKS_FULL
            try:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=4) as _ex:
                    _futures = {_ex.submit(fn): (name, cat, crit)
                                for name, fn, cat, crit in CHECKS_FULL}
                    for _fut, (name, cat, crit) in _futures.items():
                        try:
                            ok, detail = _fut.result(timeout=5)
                            _icon = "✅" if ok else ("🚨" if crit else "⚠️")
                            lines.append(f"  {_icon} {name:<28} {detail[:25]}")
                        except Exception:
                            lines.append(f"  ⚠️ {name:<28} timeout")
            except Exception:
                lines.append(f"  ✅ {n_ok} OK  ⚠️ {n_warn} warn  ❌ {n_fail} fail")

            lines += [
                f"{'─'*32}",
                f"  {'✅ Safe to trade' if safe else '⛔ Issues detected'}",
            ]
        except Exception as e:
            lines.append(f"  ⚠️ Connection check error: {str(e)[:50]}")

        # System resources
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            mem  = proc.memory_info().rss / 1024 / 1024
            cpu  = psutil.cpu_percent(interval=0.5)
            disk = psutil.disk_usage('/').free / 1024 / 1024 / 1024
            import threading
            lines += [
                f"  💻 CPU:     {cpu:.1f}%",
                f"  🧠 RAM:     {mem:.0f} MB",
                f"  💾 Disk:    {disk:.1f} GB free",
                f"  🔀 Threads: {threading.active_count()}",
            ]
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        try:
            tg = self.health()
            poll_age = tg.get("last_poll_ok_age_sec")
            poll_text = f"{poll_age:.0f}s ago" if isinstance(poll_age, (int, float)) else "never"
            tg_ok = tg.get("running") and tg.get("thread_alive") and int(tg.get("poll_failures") or 0) == 0
            lines += [
                f"  {'✅' if tg_ok else '⚠️'} TG listener: {'OK' if tg_ok else 'CHECK'}",
                f"  📥 TG last poll: {poll_text}",
            ]
            if tg.get("last_error"):
                lines.append(f"  ⚠️ TG error: {str(tg.get('last_error'))[:50]}")
        except Exception:
            pass

        lines.append(f"🕐 {_dt.now().strftime('%H:%M:%S')}")
        return "\n".join(lines) if lines else "Health data unavailable"


    def _cmd_weekly(self, _="") -> str:
        """Weekly TRADING performance (not download stats)."""
        return self._cmd_weekly_performance()

    def _cmd_weekly_OLD(self, _="") -> str:
        try:
            from data_download_tracker import get_tracker
            s = get_tracker().get_weekly_summary()
            lines = [f"📊 <b>WEEKLY DOWNLOAD RELIABILITY</b>",
                     f"Items tracked: {s.get('total_items',0)}",
                     f"Perfect (100%): {s.get('perfect',0)}"]
            for key, v in list(s.get("unreliable",{}).items())[:5]:
                lines.append(f"  ⚠️ {key.split('|')[1]}: {v['pct']}% ({v['ok']}ok/{v['failed']}fail)")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ {e}"

    def _cmd_vix(self, _="") -> str:
        try:
            from datetime import datetime as _dt
            import requests as _rq

            # India VIX — use live engine cache first (updated every scan cycle)
            vix = float(getattr(getattr(
                getattr(self, "bot_ref", None), "live_engine", None),
                "_vix_cache_val", 0) or 0)
            if vix <= 0:
                try:
                    _s = _rq.Session()
                    _s.headers.update({"User-Agent": "Mozilla/5.0",
                                       "Referer": "https://www.nseindia.com"})
                    _s.get("https://www.nseindia.com/", timeout=4)
                    _r = _s.get("https://www.nseindia.com/api/allIndices", timeout=6)
                    if _r.status_code == 200:
                        for _ix in _r.json().get("data", []):
                            if "INDIA VIX" in str(_ix.get("index", "")).upper():
                                vix = float(_ix.get("last", 0) or 0)
                                break
                except Exception:
                    pass

            # US VIX — CBOE free daily CSV (no auth required)
            us_vix = 0.0
            try:
                _cr = _rq.get(
                    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
                    timeout=5)
                if _cr.status_code == 200:
                    _lines = [l for l in _cr.text.strip().split("\n") if l.strip()]
                    _last  = _lines[-1].split(",")
                    us_vix = float(_last[-1])
            except Exception:
                pass

            v_icon = "🔴" if vix > 22 else "🟡" if vix > 16 else "🟢"
            block  = vix > 22
            vix_str    = f"{vix:.1f}"    if vix > 0 else "unavailable"
            us_vix_str = f"{us_vix:.1f}" if us_vix > 0 else "unavailable"
            lines = [
                f"🌡️ <b>VIX STATUS</b>",
                f"  {v_icon} India VIX:  {vix_str}",
                f"  {'🔴' if us_vix > 25 else '🟢'} US VIX:    {us_vix_str}",
                f"  Option buying: {'⛔ BLOCKED (VIX>22)' if block else '✅ ALLOWED'}",
            ]
            if vix <= 0:
                lines.append("  ⚠️ India VIX unavailable — NSE API unreachable")
            elif vix < 13:
                lines.append("  💡 Very low VIX — good conditions for option buying")
            elif vix < 18:
                lines.append("  💡 Normal VIX — all strategies running")
            elif vix < 22:
                lines.append("  💡 Elevated — reduced lot sizes auto-applied")
            else:
                lines.append("  💡 HIGH — system blocks option buying, futures only")
            lines.append(f"🕐 {_dt.now().strftime('%H:%M')}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ VIX error: {str(e)[:60]}"
    def _cmd_symbols(self, _="") -> str:
        try:
            bot = self.bot_ref
            n_syms = 0
            if bot:
                df = getattr(bot.live_engine.data_fetcher,'nifty_200',[])
                n_syms = len(df) if df else 0
            return (
                f"🔍 <b>SYMBOL UNIVERSE</b>\n"
                f"Total: {n_syms} symbols\n"
                f"NSE Indices: NIFTY·BANKNIFTY·FINNIFTY·MIDCPNIFTY·NIFTYNEXT50\n"
                f"BSE Indices: SENSEX·BANKEX\n"
                f"Stocks: {max(0,n_syms-7)} Nifty200 stocks\n"
                f"Strategies: 28 (all-time confluence)\n"
                f"Evals/day: ~{n_syms*28*75:,}"
            )
        except Exception as e:
            return f"⚠️ {e}"

    def _cmd_mode(self, _="") -> str:
        try:
            import config as cfg
            paper = getattr(cfg,'PAPER_TRADING',True)
            live  = getattr(cfg,'ENABLE_REAL_TRADING',False)
            return (
                f"🔄 <b>CURRENT MODE</b>\n"
                f"PAPER_TRADING:       {paper}\n"
                f"ENABLE_REAL_TRADING: {live}\n"
                f"Mode: {'📄 PAPER' if paper else '💰 LIVE'}"
            )
        except Exception as e:
            return f"⚠️ {e}"


    def _cmd_schedule(self, _="") -> str:
        """Today's full task schedule."""
        try:
            from idle_engine import get_idle_engine
            return get_idle_engine().get_todays_schedule()
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        from datetime import datetime as _dt
        now = _dt.now()
        lines = [
            "📅 <b>TODAY'S SCHEDULE</b>",
            f"  🕐 Now: {now.strftime('%H:%M')}",
            "  ─────────────────────────",
            "  🔜 09:15  Market opens — scan 198 symbols",
            "  🔜 15:25  EOD squareoff",
            "  🔜 15:30  Daily P&L report",
            "  🔜 16:28  Nightly backtest (198 symbols, 5m, 90d)",
            "  🔜 17:30  ML model training (60d signal log)",
            "  🔜 18:30  Walk-forward validation",
            "  🔜 19:00  Alternative data download",
            "  🔜 20:00  Correlation matrix update",
            "  🔜 21:00  Event calendar scan",
            "  🔜 05:30  Multi-TF backtest (1h swing setups)",
            "  🔜 08:28  Pre-market brief",
            "  🔜 09:10  Daily trading plan",
        ]
        return "\n".join(lines)


    def _cmd_state(self, _="") -> str:
        try:
            from system_state import get_state, STATES
            s = get_state().get()
            state = s.get("state","?")
            info  = STATES.get(state, {"icon":"🔄","desc":state})
            since_ts = float(s.get("since", 0))
            import time
            mins = int((time.time() - since_ts) / 60)
            hrs  = mins // 60; rem = mins % 60
            return (
                f"{info['icon']} <b>CURRENT STATE: {state}</b>\n"
                f"{info['desc']}\n"
                f"Running for: {hrs}h {rem}m\n"
                f"Detail: {s.get('detail','')}\n"
                f"Previous: {s.get('prev_state','')}\n"
                f"🕐 {self._now()}"
            )
        except Exception as e:
            return f"State: {e}"

    def _cmd_restart(self, _="") -> str:
        """Restart the trading bot via Telegram."""
        try:
            import subprocess
            self.send("🔄 <b>RESTART INITIATED via Telegram</b>\nBot will restart in 3 seconds...")
            import threading, time
            def _do_restart():
                time.sleep(3)
                subprocess.run(["systemctl","restart","trading-bot"], check=False)
            threading.Thread(target=_do_restart, daemon=True).start()
            return ""
        except Exception as e:
            try:
                import os, sys
                self.send(f"🔄 Restarting via Python...\n{e}")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e2:
                return f"⚠️ Restart failed: {e2}"

    def _cmd_log(self, text="") -> str:
        """Show last N lines of bot log."""
        try:
            n = 20
            parts = text.split()
            if len(parts) > 1 and parts[1].isdigit():
                n = min(50, int(parts[1]))
            import subprocess
            r = subprocess.run(
                ["journalctl", "-u", "trading-bot", "-n", str(n), "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            log_text = r.stdout[-3000:] if r.stdout else "No log available"
            return f"📋 <b>LAST {n} LOG LINES</b>\n<pre>{log_text}</pre>"
        except Exception as e:
            try:
                with open("bot.log") as f:
                    lines = f.readlines()
                return "📋 <b>LOG</b>\n" + "".join(lines[-20:])[-3000:]
            except Exception:
                return f"⚠️ Log not available: {e}"

    def _cmd_morning(self, _="") -> str:
        """Morning readiness check — is the system ready to trade?"""
        from datetime import datetime
        now_str = datetime.now().strftime("%H:%M")
        lines   = [f"🌅 <b>MORNING CHECK</b>  {now_str}"]
        ok_count = 0; total = 0

        # 1. Symbol universe
        total += 1
        try:
            bot = self.bot_ref
            df  = bot.live_engine.data_fetcher if bot else None
            n   = len(getattr(df, "nifty_200", []) or []) if df else 0
            icon = "✅" if n > 100 else "❌"
            lines.append(f"  {icon} Symbol universe: {n}/198")
            if n > 100: ok_count += 1
        except Exception:
            lines.append("  ⚠️ Symbol universe: unknown")

        # 2. yfinance — try 1d interval first, fallback to 5d daily
        total += 1
        nifty_price = 0.0
        try:
            import yf_compat as _yf
            from datetime import datetime as _dtn, time as _dtm
            _in_mkt = _dtm(9,15) <= _dtn.now().time() <= _dtm(15,35)
            # During market: try 5m data; before/after: use daily
            for _period, _interval in ([("1d","5m"),("5d","1d")] if _in_mkt
                                        else [("5d","1d"),("10d","1d")]):
                _df = _yf.download("^NSEI", period=_period, interval=_interval,
                                   progress=False, auto_adjust=True)
                if _df is None or len(_df) == 0: continue
                _c = _df["Close"]
                if hasattr(_c,"columns"): _c = _c.iloc[:,0]  # MultiIndex fix
                if len(_c) == 0: continue
                _v = _c.iloc[-1]
                if hasattr(_v,"iloc"): _v = _v.iloc[0]
                nifty_price = float(_v)
                if nifty_price > 0: break
            ok_yf  = nifty_price > 0
            icon   = "✅" if ok_yf else "❌"
            detail = f"NIFTY=₹{nifty_price:,.0f}" if ok_yf else "No data"
            lines.append(f"  {icon} yfinance: {detail}")
            if ok_yf: ok_count += 1
        except Exception as e:
            lines.append(f"  ❌ yfinance: {str(e)[:40]}")

        # 3. India VIX — Angel index LTP primary (NSE blocks this IP), then NSE
        #    allIndices. Mirrors the live engine's source so the check reports
        #    the same VIX the bot actually trades on. (Skips the engine's 15.0
        #    floor on purpose so a genuine outage still shows ⚠️, not a fake ✅.)
        total += 1
        vix_val = 0.0
        le = getattr(self.bot_ref, "live_engine", None) if hasattr(self, "bot_ref") else None
        try:
            _ang = (getattr(le, "_angel", None)
                    or getattr(getattr(le, "data_fetcher", None), "angel", None)) if le else None
            if _ang is not None:
                _va = _ang.get_ltp("INDIA VIX", "NSE")
                if _va and float(_va) > 0:
                    vix_val = float(_va)
        except Exception: pass
        if vix_val <= 0:
            try:
                import requests as _rvix
                _sv = _rvix.Session()
                _sv.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                _sv.get("https://www.nseindia.com/", timeout=4)
                _rv = _sv.get("https://www.nseindia.com/api/allIndices", timeout=7)
                if _rv.status_code == 200:
                    for _ix in _rv.json().get("data", []):
                        if "INDIA VIX" in str(_ix.get("index","")).upper():
                            vix_val = float(_ix.get("last", 0) or 0)
                            break
            except Exception: pass
        # Cache in live engine if we got a real value
        try:
            if vix_val > 0 and le:
                le._vix_cache_val = vix_val; le._vix_cache_ts = __import__("time").time()
        except Exception: pass
        icon     = "✅" if vix_val > 0 else "⚠️"
        vix_warn = " ⚠️ HIGH — option buying restricted" if vix_val >= 22 else ""
        lines.append(f"  {icon} India VIX: {vix_val:.1f}{vix_warn}")
        if vix_val > 0: ok_count += 1

        # 4. Strategies loaded
        total += 1
        try:
            from signal_engine import STRATEGIES
            n_strat = len(STRATEGIES)
            icon = "✅" if n_strat >= 20 else "⚠️"
            lines.append(f"  {icon} Strategies: {n_strat} loaded")
            if n_strat >= 20: ok_count += 1
        except Exception as e:
            lines.append(f"  ❌ Strategies: {e}")

        # 5. Angel One (paper mode = not needed, just check library)
        total += 1
        try:
            from SmartApi import SmartConnect  # noqa
            lines.append("  ✅ Angel One: SmartAPI library OK")
            ok_count += 1
        except ImportError:
            lines.append("  ❌ Angel One: pip install smartapi-python")
        except Exception:
            lines.append("  ⚠️ Angel One: not connected (paper mode OK)")
            ok_count += 1   # paper mode doesn't need connection

        # 6. Connection health summary
        try:
            from connection_monitor import get_monitor
            cs = get_monitor().get_status_line()
            lines.append(f"  🔌 Feeds: {cs}")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        # Summary line
        lines.append("─"*32)
        if ok_count >= total - 1:
            lines.append(f"  ✅ Ready for 9:15 AM market open")
        else:
            lines.append(f"  ⚠️ {total - ok_count} checks need attention")

        # VIX interpretation
        if vix_val > 0:
            if vix_val < 13:
                lines.append(f"  📊 VIX {vix_val:.1f} — very low, good for option buying")
            elif vix_val < 18:
                lines.append(f"  📊 VIX {vix_val:.1f} — normal, strategies running full")
            elif vix_val < 22:
                lines.append(f"  📊 VIX {vix_val:.1f} — elevated, reduced lot sizes")
            else:
                lines.append(f"  📊 VIX {vix_val:.1f} — HIGH, option buying blocked")

        # Swing watchlist from overnight MTF backtest
        try:
            import json as _j
            from pathlib import Path as _P
            from datetime import time as _dtime
            _wl = _P("swing_watchlist.json")
            if _wl.exists():
                _wdata = _j.loads(_wl.read_text())
                _items = _wdata.get("watchlist", [])
                _now_t = datetime.now().time()
                _pre   = _dtime(4, 0) <= _now_t <= _dtime(9, 14)
                # Pre-market: show BUY only
                if _pre:
                    _show = [w for w in _items if w.get("direction") == "BUY"][:4]
                    _sells = len([w for w in _items if w.get("direction") == "SELL"])
                else:
                    _show = _items[:4]
                    _sells = 0
                # Deduplicate by symbol (keep highest score)
                _seen = {}  
                for _w in _show:
                    _sym = _w.get("symbol","")
                    if _sym not in _seen or _w.get("score",0)>_seen[_sym].get("score",0):
                        _seen[_sym] = _w
                _show = list(_seen.values())[:4]
                if _show:
                    lines.append("─"*32)
                    _label = "BUY setups (pre-market)" if _pre else "Swing setups (1h)"
                    lines.append(f"📈 <b>{_label}</b>")
                    for _w in _show:
                        _si = "🟢" if _w.get("direction") == "BUY" else "🔴"
                        _sc = min(float(_w.get("score", 0)), 10.0)
                        lines.append(f"  {_si} {_w['symbol']:<12} score {_sc:.1f}  [{_w.get('strategy','?')}]")
                    if _pre and _sells:
                        lines.append(f"  + {_sells} SELL setups available after open")
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        lines.append(f"🕐 {now_str}")
        return "\n".join(lines)
    def _cmd_ml(self, _="") -> str:
        """ML model status and training info."""
        lines = ["🧠 <b>ML MODEL STATUS</b>"]
        try:
            from signal_log import get_signal_logger
            sl   = get_signal_logger()
            stats = sl.stats()
            lines += [
                f"  Signal log: {stats.get('total',0)} total",
                f"  Labelled:   {stats.get('labelled',0)} (TB labels)",
                f"  Executed:   {stats.get('executed',0)} trades",
                f"  Rejected:   {stats.get('rejected',0)} candidates",
                f"  Win rate:   {stats.get('win_rate',0):.1f}%",
            ]
        except Exception as e:
            lines.append(f"  Signal log: {e}")
        try:
            from pathlib import Path
            import json
            mp = Path("ai_model_meta.json")
            if mp.exists():
                meta = json.loads(mp.read_text())
                lines += [
                    f"  Last train: {meta.get('trained_at','?')}",
                    f"  Val acc:    {meta.get('val_accuracy',0)*100:.1f}%",
                    f"  Samples:    {meta.get('n_samples',0)}",
                    f"  Features:   {meta.get('n_features',0)}",
                ]
            else:
                lines.append("  Model: not yet trained (need 50+ signals)")
        except Exception as e:
            lines.append(f"  Model meta: {e}")
        lines.append(f"\nNext training: 6:00 PM daily")
        lines.append(f"Training runs on: ALL candidates (not just executed)")
        return "\n".join(lines)





    def _cmd_oitrend(self, args: str = "") -> str:
        """
        OI Trend — intraday OI buildup/unwind chart (TradingT style).
        Usage: /oitrend           → NIFTY trend
               /oitrend BANKNIFTY → BANKNIFTY trend
        """
        sym = (args.strip().upper() or "NIFTY")
        if sym not in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"):
            sym = "NIFTY"
        try:
            from oi_tracker import get_oi_tracker
            tracker = get_oi_tracker(alerts=self.alerts_ref if hasattr(self,"alerts_ref") else None)
            # Get stored trend
            result = tracker.format_trend(sym)
            if not result or "Not enough data" in result:
                # Take a fresh snapshot and show that
                snap = tracker.get_snapshot_now(sym)
                return snap + "\n\n⏳ Trend builds after 2 snapshots (30 min)"
            return result
        except Exception as e:
            return f"❌ OI Trend error: {str(e)[:60]}"

    def _cmd_oi(self, args: str = "") -> str:
        """
        OI Builder — strike-wise OI analysis with recommendations.
        Usage: /oi [SYMBOL]
        Examples: /oi  (defaults to NIFTY)
                  /oi BANKNIFTY
                  /oi FINNIFTY
        """
        from datetime import datetime as _dt
        # Market hours check
        from datetime import time as _dtime
        _now = _dt.now().time()
        _in_mkt = _dtime(9, 15) <= _now <= _dtime(15, 35)

        sym = (args.strip().upper() or "NIFTY")
        if sym not in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"):
            sym = "NIFTY"

        try:
            from oi_strike_builder import send_oi_builder
            text = send_oi_builder(symbol=sym, return_text=True)
            if not _in_mkt:
                return (f"📸 <b>{sym} OI</b>  Market closed\n"
                        f"  NSE option chain only available 9:15 AM–3:30 PM\n"
                        f"  Send /oi after 9:15 AM tomorrow for live data\n"
                        f"  🕐 {__import__('datetime').datetime.now().strftime('%H:%M')}")
            return text or f"❌ Could not fetch {sym} option chain"
        except Exception as e:
            return f"❌ OI Builder error: {str(e)[:60]}"




    def _cmd_sync(self, args: str = "") -> str:
        """
        Sync with Google Drive — bidirectional.
        /sync       → push + pull
        /sync push  → upload local code to Drive
        /sync pull  → download Drive changes to system
        """
        direction = (args.strip().lower() or "both")
        if direction not in ("push", "pull", "both"):
            direction = "both"
        try:
            from gdrive_sync import get_drive_sync
            ws = get_drive_sync(alerts=getattr(self, "alerts_ref", None))
            result = ws.sync_now(direction)
            pushed = result.get("pushed", False)
            pulled = result.get("pulled", [])
            lines = [
                f"☁️ <b>DRIVE SYNC COMPLETE</b>",
                f"  Direction: {direction}",
                f"  Push: {'✅ uploaded' if pushed else '❌ failed'}",
                f"  Pull: {len(pulled)} files updated",
            ]
            if pulled:
                lines.append(f"  Files: {', '.join(pulled[:4])}")
                lines.append(f"  ⚠️ Send /restart to apply changes")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Sync error: {str(e)[:80]}"

    def _cmd_deploy(self, _: str = "") -> str:
        """
        /deploy — pull latest code from Drive and restart bot immediately.
        Use this after editing files on Google Drive remotely.
        """
        try:
            from gdrive_sync import pull_from_drive
            result = pull_from_drive("all", auto_restart=True,
                                     alerts=getattr(self, "alerts_ref", None))
            files  = result.get("files_updated", [])
            if files:
                return (
                    f"🚀 <b>DEPLOY STARTED</b>\n"
                    f"  {len(files)} files pulled from Drive:\n"
                    f"  {', '.join(files[:5])}\n"
                    f"  Bot restarting in 3 seconds..."
                )
            return (
                f"☁️ <b>DEPLOY: No changes found</b>\n"
                f"  Drive code matches local — nothing to deploy.\n"
                f"  Make changes on Drive first, then /deploy"
            )
        except Exception as e:
            return f"❌ Deploy error: {str(e)[:80]}"

    def _cmd_drive_status(self, _: str = "") -> str:
        """Google Drive sync status."""
        try:
            from gdrive_sync import get_drive_sync
            return get_drive_sync().status()
        except Exception as e:
            return f"❌ Drive status: {e}"






    def _cmd_gex(self, args: str = "") -> str:
        """Gamma Exposure — where dealers are hedging."""
        try:
            from option_chain_intelligence import analyze
            from greeks_live import compute_gex, get_pcr_by_strike
            data = analyze("NIFTY")
            chain = data.get("chain", []) if data else []
            if not chain: return "❌ Option chain unavailable — try after 9:15 AM"
            spot = data.get("spot", 22500)
            gex  = compute_gex(spot, chain, dte_days=0)
            pcr  = get_pcr_by_strike(chain, spot, top_n=3)
            lines = [
                f"<b>⚡ GAMMA EXPOSURE (GEX)</b>",
                f"  Spot:      ₹{spot:,.0f}",
                f"  Total GEX: {gex.get('total_gex',0):+,.0f}",
                f"  Regime:    {gex.get('regime','UNKNOWN')}",
                f"  Delta Wall: {gex.get('delta_wall',0):,.0f} (strongest S/R)",
                f"  GEX Flip:  {gex.get('flip_point',0):,.0f}",
                f"",
                f"  {gex.get('interpretation','')}",
                f"",
                f"<b>📊 KEY STRIKES (PCR)</b>",
            ]
            for s in pcr.get("support_levels",[])[:3]:
                lines.append(f"  Support  {s['strike']:,.0f} | Put OI {s['put_oi']:,.0f} | PCR {s['pcr']:.1f}")
            for s in pcr.get("resistance_levels",[])[:3]:
                lines.append(f"  Resist   {s['strike']:,.0f} | Call OI {s['call_oi']:,.0f}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ GEX: {e}"

    def _cmd_implied_move(self, args: str = "") -> str:
        """Implied move from ATM straddle price."""
        try:
            from option_chain_intelligence import analyze
            from greeks_live import get_implied_move
            sym  = (args.strip().upper() or "NIFTY")
            data = analyze(sym)
            if not data: return f"❌ No data for {sym}"
            chain = data.get("chain", [])
            spot  = data.get("spot", 0)
            im    = get_implied_move(spot, chain)
            lines = [
                f"<b>📐 IMPLIED MOVE — {sym}</b>",
                f"  Spot:           ₹{spot:,.0f}",
                f"  ATM Strike:     {im.get('atm_strike',0):,.0f}",
                f"  CE Premium:     ₹{im.get('ce_premium',0):.0f}",
                f"  PE Premium:     ₹{im.get('pe_premium',0):.0f}",
                f"  Straddle Price: ₹{im.get('straddle_price',0):.0f}",
                f"  Implied Move:   ±{im.get('implied_pts',0):.0f} pts ({im.get('implied_move_pct',0):.2f}%)",
                f"",
                f"  {im.get('interpretation','')}",
                f"  Hero-Zero Setup: {'✅ YES — big move day' if im.get('hero_zero_signal') else '❌ Normal day'}",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Implied move: {e}"

    def _cmd_events(self, _: str = "") -> str:
        """Today's market events + trading playbook."""
        try:
            from greeks_live import get_event_playbook
            ep = get_event_playbook()
            if not ep.get("has_event"):
                return (
                    f"📅 <b>EVENT CALENDAR</b>\n"
                    f"  No major scheduled events today.\n"
                    f"  RBI MPC: Apr 7, Jun 5, Aug 7, Oct 7, Dec 4\n"
                    f"  Budget: Feb 1\n"
                    f"  Use /oi for OI-based levels"
                )
            lines = [f"📅 <b>EVENT TODAY: {', '.join(ep['events'])}</b>", ""]
            for key, pb in ep.get("playbook",{}).items():
                lines.append(f"<b>{pb['event']}</b>")
                for k,v in pb.items():
                    if k != "event":
                        lines.append(f"  {k.title()}: {v}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Events: {e}"

    def _cmd_herozero(self, args: str = "") -> str:
        """Hero-Zero 0DTE strategy — schedule + live setup score for today."""
        try:
            from hero_zero_strategy import (
                get_hero_zero_schedule, is_expiry_today,
                score_hero_zero_entry, get_hero_zero_strikes,
            )
            out = [get_hero_zero_schedule()]

            # Live setup score for each weekly index expiring today.
            #   NIFTY  → spot + momentum direction from the cached 5-min df.
            #   SENSEX → spot from BSE (no df, so direction-agnostic: show both
            #            CE and PE candidate strikes).
            from datetime import datetime as _dt
            bot = getattr(self, "bot_ref", None)
            le  = getattr(bot, "live_engine", None) if bot else None

            def _spot_dir(sym):
                """Return (spot, direction|None) for a weekly index."""
                if sym == "NIFTY":
                    df = getattr(le, "_nifty_df_cache", None) if le else None
                    try:
                        if df is not None and len(df) >= 2:
                            _c = df.copy(); _c.columns = [c.lower() for c in _c.columns]
                            cl = _c["close"].values
                            return float(cl[-1]), ("BUY" if cl[-1] >= cl[-2] else "SELL")
                    except Exception:
                        pass
                    return 0.0, None
                if sym == "SENSEX":
                    try:
                        from bse_option_chain import _fetch_bse_index_level
                        return float(_fetch_bse_index_level("SENSEX") or 0.0), None
                    except Exception:
                        return 0.0, None
                return 0.0, None

            vix = float(getattr(le, "_vix_cache_val", 0) or 0) or 15.0
            tiers = ["T1 🎲", "T2 ⚖️", "T3 🛡️"]
            for sym in ("NIFTY", "SENSEX"):
                if not is_expiry_today(sym):
                    continue
                spot, direction = _spot_dir(sym)
                if not spot:
                    out.append(f"\n⚠️ {sym}: expiry today — live spot unavailable "
                               f"(score appears once data is cached).")
                    continue
                setup = score_hero_zero_entry(
                    spot=spot, symbol=sym, direction=direction or "BUY",
                    vix=vix, time_now=_dt.now())
                blk = [
                    "", f"🎰 <b>{sym} LIVE SETUP</b> — {setup['verdict']}",
                    f"  Spot ₹{spot:,.0f} | VIX {vix:.1f}"
                    + (f" | Bias {direction}" if direction
                       else " | Bias: pick by intraday trend"),
                    f"  Setup score: {setup['score']:.1f}/10 "
                    f"({'ENTER' if setup['enter'] else 'wait — needs ≥5'})",
                ]
                for sd in ([direction] if direction else ["BUY", "SELL"]):
                    opt = "CE" if sd == "BUY" else "PE"
                    blk.append(f"  {opt} strikes:")
                    for i, s in enumerate(
                            get_hero_zero_strikes(spot, sym, sd,
                                                  otm_pct=1.5, n_strikes=3)[:3]):
                        blk.append(f"   {tiers[i] if i < 3 else f'T{i+1}'}: "
                                   f"{sym} {s['strike']:.0f} {opt} "
                                   f"(OTM {s['otm_pct']:.1f}%)")
                blk += ["  Risk: 100% of premium | Target: 5x–20x",
                        "  ⚠️ Option-buying is negative-edge — lottery flyer only."]
                for n in setup["notes"][:3]:
                    blk.append(f"  {n}")
                out.append("\n".join(blk))
            return "\n".join(out)
        except Exception as e:
            return f"❌ Hero-Zero: {e}"

    def _cmd_downloads(self, _="") -> str:
        """Show status of all scheduled daily downloads."""
        from datetime import datetime as _dt, date
        from pathlib import Path
        import os

        lines = [f"📥 <b>DAILY DOWNLOAD STATUS</b> | {_dt.now().strftime('%d-%b %H:%M')}", ""]

        checks = [
            {
                "name":    "Bhavcopy (NSE EOD)",
                "file":    "nse_cache.db",
                "time":    "6:00 PM",
                "check":   lambda: __import__("sqlite3").connect("nse_cache.db").execute(
                               "SELECT COUNT(*), MAX(date) FROM ohlcv").fetchone()
                           if Path("nse_cache.db").exists() else None,
                "fmt":     lambda r: f"✅ {r[0]:,} records | Latest: {r[1]}" if r and r[0] else "❌ Empty",
            },
            {
                "name":    "MasterContract (Angel One)",
                "file":    "MasterContract_ALL.csv",
                "time":    "On demand",
                "check":   lambda: (
                               Path("MasterContract_ALL.csv").stat().st_mtime,
                               sum(1 for _ in open("MasterContract_ALL.csv"))
                           ) if Path("MasterContract_ALL.csv").exists() else None,
                "fmt":     lambda r: f"✅ {r[1]:,} rows | Updated: {_dt.fromtimestamp(r[0]).strftime('%d-%b %H:%M')}" if r else "❌ Missing",
            },
            {
                "name":    "FII/DII Data (NSE)",
                "file":    "fii_history.csv",
                "time":    "4:00 PM",
                "check":   lambda: (
                               len(open("fii_history.csv").readlines())-1,
                               Path("fii_history.csv").stat().st_mtime
                           ) if Path("fii_history.csv").exists() else None,
                "fmt":     lambda r: f"✅ {r[0]} days stored | Updated: {_dt.fromtimestamp(r[1]).strftime('%d-%b %H:%M')}" if r and r[0] > 0 else "⚠️  No data yet",
            },
            {
                "name":    "OI History (NSE)",
                "file":    "participant_oi_history.json",
                "time":    "After market",
                "check":   lambda: (
                               __import__("json").loads(Path("participant_oi_history.json").read_text()),
                               Path("participant_oi_history.json").stat().st_mtime
                           ) if Path("participant_oi_history.json").exists() else None,
                "fmt":     lambda r: f"✅ {len(r[0])} entries | Updated: {_dt.fromtimestamp(r[1]).strftime('%d-%b %H:%M')}" if r and isinstance(r[0],list) else "⚠️  Empty",
            },
            {
                "name":    "Dark Pool History",
                "file":    "dark_pool_history.csv",
                "time":    "Real-time",
                "check":   lambda: (
                               len(open("dark_pool_history.csv").readlines())-1,
                               Path("dark_pool_history.csv").stat().st_mtime
                           ) if Path("dark_pool_history.csv").exists() else None,
                "fmt":     lambda r: f"✅ {r[0]} deals recorded" if r else "ℹ️  No deals yet",
            },
            {
                "name":    "Meta-Learner State",
                "file":    "meta_learner_state.json",
                "time":    "Per trade",
                "check":   lambda: __import__("json").loads(Path("meta_learner_state.json").read_text())
                           if Path("meta_learner_state.json").exists() else None,
                "fmt":     lambda r: f"✅ {sum(len(v) for v in r.get('trades',{{}}).values())} trades tracked | {len(r.get('trades',{{}}))} strategies" if r else "ℹ️  No trades yet",
            },
            {
                "name":    "Strategy Params (Backtest)",
                "file":    "strategy_params.json",
                "time":    "4:30 PM",
                "check":   lambda: (
                               len(__import__("json").loads(Path("strategy_params.json").read_text())),
                               Path("strategy_params.json").stat().st_mtime
                           ) if Path("strategy_params.json").exists() else None,
                "fmt":     lambda r: f"✅ {r[0]} symbols optimised | Updated: {_dt.fromtimestamp(r[1]).strftime('%d-%b %H:%M')}" if r else "⚠️  Not yet run",
            },
            {
                "name":    "Trades Database",
                "file":    "trades.db",
                "check":   lambda: __import__("sqlite3").connect("trades.db").execute(
                               "SELECT COUNT(*) FROM trades").fetchone()[0]
                           if Path("trades.db").exists() else None,
                "time":    "Per trade",
                "fmt":     lambda r: f"✅ {r} trades recorded" if r is not None else "ℹ️  No trades yet",
            },
        ]

        schedule = [
            ("4:00 PM", "FII/DII fetch + store"),
            ("4:30 PM", "Autonomous backtest + strategy fine-tuning"),
            ("5:30 PM", "ML model training"),
            ("6:00 PM", "Bhavcopy EOD download (all NSE stocks)"),
            ("6:30 PM", "Walk-forward validation"),
            ("9:00 PM", "Event calendar scan"),
            ("10:00 PM", "Correlation matrix update"),
        ]

        for c in checks:
            try:
                result = c["check"]()
                status = c["fmt"](result)
            except Exception as e:
                status = f"❌ Error: {str(e)[:30]}"
            lines.append(f"  {status}")
            lines.append(f"   📁 {c['name']} | ⏰ {c['time']}")
            lines.append("")

        lines.append("─────────────────────────────")
        lines.append("  <b>DAILY SCHEDULE</b>")
        for t, task in schedule:
            lines.append(f"  🕐 {t} — {task}")

        return "\n".join(lines)


    def _cmd_brief(self, _="") -> str:
        """Morning intelligence brief on demand."""
        try:
            from morning_brief import generate_morning_brief
            return generate_morning_brief()
        except Exception as e:
            return f"❌ Brief: {e}"

    def _cmd_sharpe(self, _="") -> str:
        """Quick risk metrics."""
        try:
            from performance_analytics import get_full_report
            r = get_full_report(30)
            if r["total_trades"] == 0:
                return "📐 No trades yet — metrics available after first trades"
            return (
                f"📐 <b>RISK METRICS (30d)</b>\n\n"
                f"  Sharpe:    {r['sharpe']:>6.2f}  (>1.5 = institutional)\n"
                f"  Sortino:   {r['sortino']:>6.2f}  (>2.0 = excellent)\n"
                f"  Calmar:    {r['calmar']:>6.2f}  (>1.0 = good)\n"
                f"  Omega:     {r['omega']:>6.2f}  (>1.5 = good)\n"
                f"  Max DD:    {r['max_drawdown_pct']:>5.1f}%\n"
                f"  Quality:   {r['quality_score']:>5.1f}/100\n"
            )
        except Exception as e:
            return f"❌ Sharpe: {e}"

    def _cmd_attribution(self, _="") -> str:
        """P&L attribution by strategy and symbol."""
        try:
            from performance_analytics import strategy_attribution, symbol_attribution, _load_trades
            trades = _load_trades(30)
            if not trades:
                return "📊 No trades yet — attribution available after first trades"
            strats = strategy_attribution(trades)
            syms   = symbol_attribution(trades)
            lines  = ["📊 <b>P&L ATTRIBUTION (30d)</b>", "", "  <b>By Strategy</b>"]
            for s in strats[:5]:
                icon = "🟢" if s["pnl"] >= 0 else "🔴"
                lines.append(f"  {icon} {s['strategy'][:18]:18} ₹{s['pnl']:>8,.0f} ({s['win_rate']:.0f}%wr)")
            lines += ["", "  <b>By Symbol</b>"]
            for s in syms[:5]:
                icon = "🟢" if s["pnl"] >= 0 else "🔴"
                lines.append(f"  {icon} {s['symbol'][:12]:12} ₹{s['pnl']:>8,.0f} ({s['win_rate']:.0f}%wr)")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Attribution: {e}"



    def _cmd_sentiment(self, _="") -> str:
        """Global news + commodity sentiment analysis."""
        try:
            from news_sentiment_engine import format_telegram_report
            return format_telegram_report()
        except Exception as e:
            return f"❌ Sentiment: {e}"

    def _cmd_commodities(self, _="") -> str:
        """Live commodity prices + India sector impact."""
        try:
            from news_sentiment_engine import fetch_commodity_prices, analyze_commodity_impact
            from datetime import datetime as _dt
            comms = fetch_commodity_prices()
            impacts = analyze_commodity_impact(comms)
            now = _dt.now().strftime("%d-%b %H:%M")
            lines = [f"🛢️ <b>COMMODITIES + SECTOR IMPACT</b> | {now}", ""]
            lines.append("  <b>PRICES</b>")
            for name, d in comms.items():
                if not d.get("price"): continue
                ci = "🟢" if d["change_pct"] > 0 else "🔴" if d["change_pct"] < 0 else "⚪"
                lines.append(
                    f"  {ci} {name:14} {d['price']:>10,.1f} {d['unit'][:6]}"
                    f"  ({d['change_pct']:+.1f}%)"
                )
            if impacts:
                lines += ["", "  <b>SECTOR IMPACT</b>"]
                for sector, impact in impacts.items():
                    lines.append(f"  {impact}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Commodities: {e}"

    def _cmd_video(self, _="") -> str:
        """Generate and send market brief video."""
        try:
            from news_sentiment_engine import get_full_sentiment
            from morning_brief import _fetch_global_snapshot, _fetch_india_vix
            from sector_rotation_engine import get_top_sectors, get_avoid_sectors
            from cross_asset import get_market_bias, get_cross_asset_data
            import threading

            def _make():
                try:
                    macro = get_cross_asset_data()
                    sent_data = get_full_sentiment()
                    brief_data = {
                        "global":            macro,
                        "india_vix":         _fetch_india_vix(),
                        "bias":              get_market_bias(macro),
                        "sentiment":         sent_data.get("sentiment","NEUTRAL"),
                        "top_sectors":       get_top_sectors(3),
                        "avoid_sectors":     get_avoid_sectors(2),
                        "commodity_impacts": sent_data.get("sector_impacts",{}),
                        "commodities":       sent_data.get("commodities",{}),
                        "wow_factors": {
                            "regime": "TRENDING",
                            "fii_bias": "NEUTRAL",
                        },
                    }
                    from voice_video_generator import generate_daily_brief_video
                    path = generate_daily_brief_video(brief_data, alerts=getattr(self,"alerts",None))
                    if not path:
                        if hasattr(self,"alerts"):
                            self.alerts.send("⚠️ Video generation failed — install: pip install gtts moviepy matplotlib")
                except Exception as _ve:
                    import logging
                    logging.getLogger("video").warning("video: %s", _ve)

            threading.Thread(target=_make, daemon=True, name="video_gen").start()
            return "🎬 Generating market brief video...\nSending in 30-60 seconds\nRequires: pip install gtts moviepy matplotlib"
        except Exception as e:
            return f"❌ Video: {e}"

    def _cmd_news(self, _="") -> str:
        """Latest market news with sentiment scores."""
        try:
            from news_sentiment_engine import fetch_global_news, _score_headline
            from datetime import datetime as _dt
            news = fetch_global_news()
            now = _dt.now().strftime("%d-%b %H:%M")
            lines = [f"📰 <b>MARKET NEWS</b> | {now}", ""]
            for cat, headlines in news.items():
                if not headlines: continue
                lines.append(f"  <b>{cat}</b>")
                for h in headlines[:3]:
                    score = _score_headline(h)
                    icon = "🟢" if score > 0.1 else "🔴" if score < -0.1 else "⚪"
                    lines.append(f"  {icon} {h[:75]}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ News: {e}"


    def _cmd_corporate(self, _="") -> str:
        """Today's corporate actions and upcoming events."""
        try:
            from mega_intelligence_engine import get_full_intelligence
            d = get_full_intelligence()
            ca = d.get("corporate_actions", [])
            ev = d.get("upcoming_events", [])
            ban= d.get("fno_ban", [])
            now = __import__("datetime").datetime.now().strftime("%d-%b %H:%M")
            lines = [f"📋 <b>CORPORATE INTELLIGENCE</b> | {now}", ""]
            if ca:
                lines.append("  <b>TODAY'S CORPORATE ACTIONS</b>")
                for a in ca[:8]:
                    lines.append(f"  {a.get('symbol','?'):12} {str(a.get('subject', a.get('purpose','?')))[:40]}")
                lines.append("")
            if ev:
                lines.append("  <b>UPCOMING EVENTS (results/AGM)</b>")
                for e in ev[:6]:
                    lines.append(f"  {e.get('symbol','?'):12} {str(e.get('purpose','?'))[:30]:30} {e.get('date','?')}")
                lines.append("")
            if ban:
                lines.append(f"  <b>F&O BAN LIST</b> ({len(ban)} stocks)")
                lines.append(f"  {', '.join(ban)}")
            return "\n".join(lines) if len(lines) > 2 else "  No corporate actions today"
        except Exception as e:
            return f"❌ Corporate: {e}"

    def _cmd_monsoon(self, _="") -> str:
        """Monsoon progress + agri stock impact."""
        try:
            from mega_intelligence_engine import fetch_monsoon_data
            m = fetch_monsoon_data()
            if not m:
                return "⚠️ Monsoon data unavailable"
            icon = "🟢" if "BULLISH" in m.get("sentiment","") else "🔴" if "BEARISH" in m.get("sentiment","") else "⚪"
            lines = [f"🌧️ <b>MONSOON INTELLIGENCE</b>", ""]
            lines.append(f"  {icon} Outlook: {m.get('sentiment','NEUTRAL')}")
            if m.get("headlines"):
                lines.append("")
                lines.append("  <b>LATEST HEADLINES</b>")
                for h in m["headlines"]:
                    lines.append(f"  • {h[:70]}")
            if m.get("stocks"):
                lines.append("")
                lines.append(f"  <b>STOCKS AFFECTED</b>")
                lines.append(f"  {', '.join(m['stocks'])}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Monsoon: {e}"


    def _cmd_intelligence(self, _="") -> str:
        """Full unified intelligence report."""
        try:
            from unified_intelligence_hub import format_telegram_report
            return format_telegram_report()
        except Exception as e:
            return f"❌ Intelligence: {e}"

    def _cmd_intel_refresh(self, _="") -> str:
        """Force refresh all intelligence modules."""
        try:
            import threading
            def _refresh():
                from unified_intelligence_hub import refresh_all_intelligence
                refresh_all_intelligence(getattr(self,"alerts",None))
            threading.Thread(target=_refresh, daemon=True, name="intel_refresh").start()
            return "🧠 Intelligence refresh started (35 feeds + 12 modules)\nResults in ~30s"
        except Exception as e:
            return f"❌ Intel refresh: {e}"

    def _cmd_fnoban(self, _="") -> str:
        """F&O ban list — stocks banned from new F&O positions."""
        try:
            from unified_intelligence_hub import fetch_fno_ban_signal
            d = fetch_fno_ban_signal()
            syms = d.get("symbols", [])
            count = d.get("count", 0)
            icon = "🔴" if count > 10 else "🟡" if count > 5 else "🟢"
            sym_str = ", ".join(syms[:10]) if syms else "None"
            return (
                f"🚫 <b>F&O BAN LIST</b>\n\n"
                f"  {icon} {count} stocks in F&O ban\n"
                f"  Symbols: {sym_str}\n"
                f"\n  ⚠️ No new F&O positions in these stocks\n"
                f"  Source: NSE (updates daily)"
            )
        except Exception as e:
            return f"❌ FNO ban: {e}"

    def _cmd_subscribers(self, _="") -> str:
        """Subscriber stats — signal service metrics."""
        try:
            from subscriber_manager import format_stats_report
            return format_stats_report()
        except Exception as e:
            return f"❌ Subscribers: {e}"

    def _cmd_subscribe(self, args="") -> str:
        """Add a subscriber."""
        try:
            from subscriber_manager import add_subscriber
            parts = args.strip().split()
            chat_id = parts[0] if parts else ""
            tier    = parts[1] if len(parts) > 1 else "free"
            name    = " ".join(parts[2:]) if len(parts) > 2 else ""
            if not chat_id:
                return "Usage: /subscribe CHAT_ID [tier] [name]"
            return add_subscriber(chat_id, name, tier)
        except Exception as e:
            return f"❌ Subscribe: {e}"


    def _cmd_intel(self, _="") -> str:
        """Full market intelligence report."""
        try:
            from market_intelligence import format_telegram_report
            return format_telegram_report()
        except Exception as e:
            return f"❌ Intel: {e}"

    def _cmd_breadth(self, _="") -> str:
        """NSE market breadth — A/D ratio, 52w highs/lows."""
        try:
            from market_intelligence import fetch_market_breadth
            from datetime import datetime as _dt
            b = fetch_market_breadth()
            if not b:
                return "⚠️ Breadth data unavailable"
            adr_icon = "🟢" if b.get("adr_signal")=="BULLISH" else "🔴" if b.get("adr_signal")=="BEARISH" else "⚪"
            return (
                f"📈 <b>MARKET BREADTH</b> | {_dt.now().strftime('%H:%M')}\n\n"
                f"  Advancing:  {b.get('advances',0):,} stocks\n"
                f"  Declining:  {b.get('declines',0):,} stocks\n"
                f"  Unchanged:  {b.get('unchanged',0):,} stocks\n"
                f"  A/D Ratio:  {b.get('adr',0):.2f}  {adr_icon} {b.get('adr_signal','')}\n"
                f"  % Up:       {b.get('pct_up',0):.1f}%\n"
                f"  52W Highs:  {b.get('highs_52w',0)}\n"
                f"  52W Lows:   {b.get('lows_52w',0)}\n"
                f"  H/L Ratio:  {b.get('hl_ratio',0):.1f}  → {b.get('hl_signal','')}\n"
            )
        except Exception as e:
            return f"❌ Breadth: {e}"

    def _cmd_smi(self, _="") -> str:
        """Smart Money Index — retail vs institutional direction."""
        try:
            from market_intelligence import compute_smart_money_index
            d = compute_smart_money_index("NIFTY")
            if not d:
                return "⚠️ SMI data unavailable (needs market hours data)"
            s_icon = "🟢" if "BULL" in d.get("signal","") else "🔴" if "BEAR" in d.get("signal","") else "⚪"
            return (
                f"💰 <b>SMART MONEY INDEX</b>\n\n"
                f"  First 30 min (retail):  {d.get('early_move',0):+.2f}%\n"
                f"  Last 60 min (smart $):  {d.get('late_move',0):+.2f}%\n"
                f"  Divergence:             {d.get('divergence',0):+.2f}%\n"
                f"  Signal: {d.get('signal','')} {s_icon}\n\n"
                f"  Interpretation:\n"
                f"  Early↑ Late↓ = retail buying, smart selling → BEARISH\n"
                f"  Early↓ Late↑ = retail selling, smart buying → BULLISH\n"
            )
        except Exception as e:
            return f"❌ SMI: {e}"

    def _cmd_insider(self, _="") -> str:
        """Today's insider/promoter buying/selling (SAST disclosures)."""
        try:
            from market_intelligence import fetch_insider_trading
            from datetime import datetime as _dt
            trades = fetch_insider_trading()
            if not trades:
                return f"ℹ️ No insider disclosures today"
            buys  = [t for t in trades if t["action"]=="BUY"]
            sells = [t for t in trades if t["action"]=="SELL"]
            lines = [f"🏦 <b>INSIDER TRADING (SAST)</b> | {_dt.now().strftime('%d-%b')}",""]
            if buys:
                lines.append("  <b>🟢 PROMOTER BUYING</b>")
                for t in buys[:5]:
                    lines.append(f"  ✅ {t['symbol']:10} {t['who'][:20]:20} {t['qty']:>8,} shares")
            if sells:
                lines += ["","  <b>🔴 PROMOTER SELLING</b>"]
                for t in sells[:5]:
                    lines.append(f"  ⚠️ {t['symbol']:10} {t['who'][:20]:20} {t['qty']:>8,} shares")
            lines += ["","  Source: NSE SAST disclosures"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Insider: {e}"

    def _cmd_maxpain(self, args="") -> str:
        """Max pain level for NIFTY/BANKNIFTY."""
        try:
            from market_intelligence import fetch_option_chain_intelligence
            symbol = args.strip().upper() or "NIFTY"
            d = fetch_option_chain_intelligence(symbol)
            if not d: return f"⚠️ No data for {symbol}"
            mp = d.get("max_pain", 0)
            spot = d.get("spot", 0)
            gap = mp - spot
            icon = "🔴" if gap < 0 else "🟢"
            return (
                f"🎯 <b>MAX PAIN — {symbol}</b>\n\n"
                f"  Current spot: ₹{spot:,.0f}\n"
                f"  Max Pain:     ₹{mp:,.0f}\n"
                f"  Gap:          {gap:+,.0f} pts  {icon}\n"
                f"  Expiry:       {d.get('expiry','')}\n\n"
                f"  💡 Market tends to gravitate toward max pain\n"
                f"     on expiry day (option writers hedge)\n"
                f"  ⚠️ Most effective in last 2 days of expiry"
            )
        except Exception as e:
            return f"❌ MaxPain: {e}"

    def _cmd_eod_summary(self, _="") -> str:
        """Send EOD signal summary."""
        try:
            from public_signal_formatter import format_eod_summary
            return format_eod_summary({})
        except Exception as e:
            return f"❌ EOD: {e}"


    # ════════════════════════════════════════════════════════
    # UX ENGINE — All user experience commands
    # ════════════════════════════════════════════════════════

    def _cmd_weekly_perf(self, _="") -> str:
        from ux_engine import get_weekly_performance
        return get_weekly_performance()

    def _cmd_export(self, args="") -> str:
        from ux_engine import export_trades_csv
        days = int(args.strip()) if args.strip().isdigit() else 90
        path = export_trades_csv(days)
        if path and __import__("pathlib").Path(path).exists():
            return f"📄 Trade export ready\n  File: {path}\n  {days} days of trades\n  Use /export 365 for full year"
        return "ℹ️ No closed trades to export yet."

    def _cmd_watch(self, args="") -> str:
        from ux_engine import set_watchlist, get_watchlist
        if not args.strip():
            wl = get_watchlist()
            if not wl:
                return ("👁️ No watchlist set.\n"
                        "Set one: /watch HDFCBANK TCS INFY\n"
                        "You'll get priority alerts for these symbols.")
            return f"👁️ <b>YOUR WATCHLIST</b>\n\n  {', '.join(wl)}\n\nUpdate: /watch SYMBOL1 SYMBOL2"
        symbols = [s.upper().strip() for s in args.split() if s.strip()]
        return set_watchlist(symbols)

    def _cmd_alert(self, args="") -> str:
        from ux_engine import add_price_alert, list_active_alerts
        parts = args.strip().split()
        if not parts or parts[0].lower() == "list":
            return list_active_alerts()
        # /alert NIFTY above 24000
        if len(parts) >= 3:
            try:
                sym = parts[0].upper()
                cond = parts[1].lower()
                price = float(parts[2].replace(",",""))
                return add_price_alert(sym, cond, price)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        return ("🔔 Price Alert Usage:\n"
                "/alert NIFTY above 24000\n"
                "/alert BANKNIFTY below 53000\n"
                "/alert list — see active alerts")

    def _cmd_alerts(self, _="") -> str:
        from ux_engine import list_active_alerts
        return list_active_alerts()

    def _cmd_compare(self, _="") -> str:
        from ux_engine import get_week_comparison
        return get_week_comparison()

    def _cmd_streak(self, _="") -> str:
        from ux_engine import get_streak_info
        return get_streak_info()

    def _cmd_gap_warning(self, _="") -> str:
        try:
            from ux_engine import get_overnight_gap_warnings
            bot = self.bot_ref
            positions = []
            if bot:
                positions = bot.live_engine.trade_manager.get_open_positions()
            return f"⚠️ <b>OVERNIGHT GAP CHECK</b>\n\n{get_overnight_gap_warnings(positions)}"
        except Exception as e:
            from ux_engine import friendly_error
            return friendly_error("gaps", e)


    # ══════════════════════════════════════════════════════════════
    # UX COMMANDS — End-user experience
    # ══════════════════════════════════════════════════════════════

    def _cmd_start(self, _="") -> str:
        """UX-1: Onboarding — first thing a new user sees."""
        return (
            "🤖 <b>WELCOME TO AUTONOMOUS TRADING BOT</b>\n\n"
            "  I scan 196 NSE symbols every 5 minutes\n"
            "  using 65 strategies + AI intelligence.\n\n"
            "  <b>📋 QUICK START</b>\n"
            "  /morning  → Pre-market intelligence brief\n"
            "  /signals  → Latest signals\n"
            "  /today    → All signals today\n"
            "  /pnl      → Today's P&L\n"
            "  /status   → Bot status\n\n"
            "  <b>📊 ANALYSIS</b>\n"
            "  /sentiment → News sentiment (40+ sources)\n"
            "  /sectors  → Sector rotation\n"
            "  /fii       → FII/DII flows\n"
            "  /vix       → India VIX\n\n"
            "  <b>⚙️ TOOLS</b>\n"
            "  /calculate NIFTY 100000 → Position sizing\n"
            "  /alert NIFTY above 24000 → Price alert\n"
            "  /watch HDFCBANK TCS → Watchlist\n"
            "  /export → Download trade history\n\n"
            "  <b>📚 HELP</b>\n"
            "  /help    → All 101 commands\n"
            "  /weekly  → Weekly performance\n\n"
            "  ⚠️ <i>Educational signals only. Not SEBI registered advice.\n"
            "  Always set your stop loss before trading.</i>"
        )

    def _cmd_today(self, _="") -> str:
        """UX-6: All signals today with status."""
        try:
            from ux_commands import get_todays_signals
            return get_todays_signals()
        except Exception as e:
            return f"❌ Today's signals: {e}"

    def _cmd_calculate(self, args="") -> str:
        """UX-5+13: Position sizing calculator."""
        try:
            from ux_commands import calculate_position_size
            import config as _cfg
            parts = args.strip().upper().split()
            symbol  = parts[0] if parts else "NIFTY"
            capital = float(parts[1]) if len(parts) > 1 else float(
                getattr(_cfg, "REAL_CAPITAL", 26964) or 26964)
            risk_pct = float(parts[2]) if len(parts) > 2 else 1.0
            return calculate_position_size(symbol, capital, risk_pct)
        except Exception as e:
            return (f"❌ Calculate: {e}\n"
                    f"Usage: /calculate NIFTY 100000\n"
                    f"Or: /calculate BANKNIFTY 50000 1.5")

    def _cmd_watchlist(self, args="") -> str:
        """UX-15: Manage watchlist — alert on specific stocks only."""
        try:
            from ux_commands import get_watchlist, set_watchlist
            if not args.strip():
                wl = get_watchlist()
                if not wl:
                    return ("👀 <b>WATCHLIST</b> — empty\n\n"
                            "Add stocks: /watch HDFCBANK TCS INFY\n"
                            "Clear: /watch clear")
                return f"👀 <b>WATCHLIST</b>\n\n  {', '.join(wl)}\n\nEdit: /watch SYMBOL1 SYMBOL2"
            if args.strip().lower() == "clear":
                set_watchlist([])
                return "✅ Watchlist cleared — getting all 196 symbols again"
            symbols = [s.upper() for s in args.strip().split()]
            set_watchlist(symbols)
            return f"✅ Watchlist set: {', '.join(symbols)}\nYou'll only see signals for these stocks"
        except Exception as e:
            return f"❌ Watchlist: {e}"

    def _cmd_price_alert(self, args="") -> str:
        """UX-16: Set price alert. Usage: /alert NIFTY above 24000"""
        try:
            from ux_commands import add_price_alert, get_price_alerts
            if not args.strip():
                alerts = get_price_alerts()
                if not alerts:
                    return ("🔔 <b>PRICE ALERTS</b> — none set\n\n"
                            "Usage: /alert NIFTY above 24000\n"
                            "Or: /alert RELIANCE below 1200")
                lines = ["🔔 <b>ACTIVE PRICE ALERTS</b>", ""]
                for a in alerts:
                    if not a.get("triggered"):
                        lines.append(f"  {a['symbol']} {a['condition']} ₹{a['price']:,.0f}")
                return "\n".join(lines)
            parts = args.strip().upper().split()
            if len(parts) < 3:
                return "Usage: /alert NIFTY above 24000\nOr: /alert RELIANCE below 1200"
            sym   = parts[0]
            cond  = parts[1].lower()
            price = float(parts[2].replace(",",""))
            if cond not in ("above","below","over","under"):
                return f"Condition must be 'above' or 'below', got: {cond}"
            cond = "above" if cond in ("above","over") else "below"
            return add_price_alert(sym, cond, price)
        except Exception as e:
            return f"❌ Alert: {e}"

    def _cmd_export_trades(self, args="") -> str:
        """UX-10: Export trade history as CSV."""
        try:
            from ux_commands import export_trades_csv
            path = export_trades_csv()
            if not path:
                return "📤 No closed trades yet to export"
            # Send via Telegram document
            try:
                alerts = getattr(getattr(self, 'bot_ref', None), 'alerts', None)
                if alerts and hasattr(alerts, 'send_document'):
                    alerts.send_document(path,
                        caption=f"📊 Trade history export | {__import__('datetime').date.today()}")
                    import os; os.remove(path)
                    return "✅ Trade history sent as CSV file"
            except Exception: pass
            return f"✅ Export created: {path}\nFind it in trading_robot folder"
        except Exception as e:
            return f"❌ Export: {e}"

    def _cmd_weekly_performance(self, _="") -> str:
        """UX-8: Weekly trading performance (not download stats)."""
        try:
            from ux_commands import get_weekly_performance
            return get_weekly_performance()
        except Exception as e:
            return f"❌ Weekly performance: {e}"

    def _cmd_paper(self, args="") -> str:
        """UX-12: Paper trade tracker."""
        try:
            from ux_commands import get_paper_results
            return get_paper_results()
        except Exception as e:
            return f"❌ Paper: {e}"

    def _cmd_voice(self, _="") -> str:
        """UX-18: Voice status update."""
        try:
            from ux_commands import generate_voice_status
            bot = getattr(self, 'bot_ref', None)
            path = generate_voice_status(bot)
            if path:
                alerts = getattr(bot, 'alerts', None)
                if alerts and hasattr(alerts, 'send_audio'):
                    alerts.send_audio(path, caption="🔊 Bot Status Update")
                    import os; os.remove(path)
                    return "🔊 Voice status sent!"
                return f"🔊 Audio saved: {path}\nInstall gtts: pip install gtts"
            return "⚠️ Voice requires: pip install gtts\nInstall and try again"
        except Exception as e:
            return f"❌ Voice: {e}"

    def _cmd_missed(self, _="") -> str:
        """UX-6 alias: Show missed/all signals today."""
        return self._cmd_today()

    def _cmd_live_positions(self, _="") -> str:
        """UX-7: Live position P&L updates."""
        try:
            bot = getattr(self, 'bot_ref', None)
            if not bot: return "⚠️ Bot reference not available"
            tm = bot.live_engine.trade_manager
            opens = getattr(tm, 'open_trades', {})
            if not opens:
                return f"📊 <b>LIVE POSITIONS</b>\n\n  No open positions\n  ⏰ {__import__('datetime').datetime.now().strftime('%H:%M')}"

            lines = [f"📊 <b>LIVE POSITIONS</b> | {__import__('datetime').datetime.now().strftime('%H:%M')}", ""]
            total_pnl = 0.0

            for tid, trade in opens.items():
                sym    = getattr(trade,'symbol','?')
                side   = getattr(trade,'side','?')
                entry  = float(getattr(trade,'entry_price',0) or 0)
                target = float(getattr(trade,'target_price',0) or 0)
                sl     = float(getattr(trade,'stop_loss',0) or 0)
                qty    = int(getattr(trade,'qty',0) or 0)

                # Get current price
                curr = 0.0
                try:
                    import requests as _rq2
                    s2 = _rq2.Session()
                    s2.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                    s2.get("https://www.nseindia.com/", timeout=2)
                    r2 = s2.get("https://www.nseindia.com/api/allIndices", timeout=4)
                    nm = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK"}
                    for idx in r2.json().get("data",[]):
                        if nm.get(sym,"") in str(idx.get("index","")):
                            curr = float(idx.get("last",0) or 0)
                            break
                except Exception: pass

                if curr > 0 and entry > 0:
                    pnl = (curr - entry) * qty if side=="BUY" else (entry - curr) * qty
                    total_pnl += pnl
                    pnl_icon = "🟢" if pnl >= 0 else "🔴"
                    dist_to_tgt = abs(target-curr)/curr*100 if target and curr else 0
                    lines.append(f"  {pnl_icon} {sym:12} {side} | ₹{curr:,.0f} | P&L: ₹{pnl:+,.0f}")
                    if target: lines.append(f"     T: ₹{target:,.0f} ({dist_to_tgt:.1f}% away) | SL: ₹{sl:,.0f}")
                else:
                    lines.append(f"  ⏳ {sym:12} {side} @ ₹{entry:,.0f}")
                    if target: lines.append(f"     T: ₹{target:,.0f} | SL: ₹{sl:,.0f}")
                lines.append("")

            if total_pnl != 0:
                ti = "🟢" if total_pnl >= 0 else "🔴"
                lines.append(f"  {ti} TOTAL OPEN P&L: ₹{total_pnl:+,.0f}")

            lines.append(f"  Daily P&L: ₹{getattr(tm,'daily_realized_pnl',0):+,.0f}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Live: {e}"

    def _cmd_why(self, args="") -> str:
        """UX-17: Explain why a signal was generated."""
        try:
            symbol = args.strip().upper() or "NIFTY"
            from ux_commands import get_todays_signals
            # Show signal reasons
            lines = [f"🔍 <b>SIGNAL ANALYSIS — {symbol}</b>", ""]
            try:
                import sqlite3
                conn = sqlite3.connect("signal_log.db")
                row = conn.execute(
                    "SELECT symbol, side, strategy, score, reasons, signal_time "
                    "FROM signal_log WHERE symbol=? ORDER BY id DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                conn.close()
                if row:
                    lines += [
                        f"  Last signal: {row[1]} @ {row[5][:16]}",
                        f"  Strategy: {row[2]}",
                        f"  Score: {row[3]:.1f}/10",
                        "",
                        "  <b>WHY THIS SIGNAL</b>",
                    ]
                    reasons = row[4]
                    if reasons:
                        try:
                            import json
                            r_list = json.loads(reasons) if isinstance(reasons,str) else reasons
                            for r in (r_list if isinstance(r_list,list) else [reasons])[:5]:
                                lines.append(f"  • {r}")
                        except Exception:
                            lines.append(f"  • {reasons[:100]}")
                    else:
                        lines.append("  • Technical confluence signal")
                        lines.append("  • Multiple strategy agreement")
                else:
                    lines.append(f"  No recent signal for {symbol}")
                    lines.append("  Try: /why RELIANCE or /why NIFTY")
            except Exception:
                lines.append("  Signal log not available yet")
                lines.append("  Reasons will show after first signals fire")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Why: {e}"

    def _cmd_compare_benchmark(self, _="") -> str:
        """UX-9: Compare bot vs NIFTY benchmark."""
        try:
            from performance_analytics import get_full_report
            import requests, config as _cfg
            r = get_full_report(30)
            cap = float(getattr(_cfg,'REAL_CAPITAL',26964) or 26964)
            bot_pct = r.get('total_pnl',0) / cap * 100 if cap else 0

            nifty_ret = 0.0
            try:
                url = "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=1d&range=30d"
                resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=6)
                closes = resp.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                closes = [c for c in closes if c]
                if len(closes) >= 2:
                    nifty_ret = (closes[-1]-closes[0])/closes[0]*100
            except Exception: pass

            alpha = bot_pct - nifty_ret
            ai = "🟢" if alpha >= 0 else "🔴"
            return (
                f"📐 <b>BOT vs NIFTY (30 days)</b>\n\n"
                f"  Bot return:   {bot_pct:+.2f}%\n"
                f"  NIFTY return: {nifty_ret:+.2f}%\n"
                f"  {ai} Alpha:      {alpha:+.2f}%\n\n"
                f"  Total trades: {r.get('total_trades',0)}\n"
                f"  Win rate:     {r.get('win_rate',0):.1f}%\n"
                f"  Sharpe:       {r.get('sharpe',0):.2f}\n\n"
                f"  Target: Alpha > 0% consistently\n"
                f"  Institutional grade: Sharpe > 1.5"
            )
        except Exception as e:
            return f"❌ Compare: {e}"

    def _cmd_pause_symbol(self, args="") -> str:
        """IMPROVEMENT 7: Pause/resume a specific symbol. Usage: /pause_sym RELIANCE"""
        try:
            import json, os
            sym = args.strip().upper()
            if not sym:
                # Show paused symbols
                pf = "paused_symbols.json"
                paused = json.loads(open(pf).read()) if os.path.exists(pf) else []
                if not paused:
                    return "✅ No symbols paused\nUse: /pause_sym RELIANCE"
                return "⏸️ <b>PAUSED SYMBOLS</b>\n" + "\n".join(f"  {s}" for s in paused)
            pf = "paused_symbols.json"
            paused = json.loads(open(pf).read()) if os.path.exists(pf) else []
            if sym in paused:
                paused.remove(sym)
                action = f"▶️ RESUMED"
            else:
                paused.append(sym)
                action = f"⏸️ PAUSED"
            with open(pf,'w') as f: json.dump(paused, f)
            return f"{action}: {sym}\nAll other symbols continue normally"
        except Exception as e:
            return f"❌ pause_sym: {e}"

    def _cmd_pause_strategy(self, args="") -> str:
        """
        Pause or resume a named strategy. Toggle: send again to unpause.
        Usage: /pause_strategy trend_following
               /pause_strategy           (list paused)
        Matching is substring on function name (case-insensitive).
        """
        try:
            import json, os
            sf = "paused_strategies.json"
            paused: list = json.loads(open(sf).read()) if os.path.exists(sf) else []

            name = args.strip().lower().replace("run_", "").replace("_strategy", "")
            if not name:
                if not paused:
                    return "✅ No strategies paused\nUse: /pause_strategy trend_following"
                return (
                    "⏸ <b>PAUSED STRATEGIES</b>\n"
                    + "\n".join(f"  · {s}" for s in paused)
                    + "\n\nSend /pause_strategy NAME again to resume"
                )

            if name in paused:
                paused.remove(name)
                action = "▶️ RESUMED"
            else:
                paused.append(name)
                action = "⏸ PAUSED"

            with open(sf, "w") as f:
                json.dump(paused, f)
            currently = ", ".join(paused) if paused else "none"
            return (
                f"{action}: <b>{name}</b>\n"
                f"Currently paused: {currently}\n"
                f"Effect: takes hold on next scan cycle (≤5 min)"
            )
        except Exception as e:
            return f"❌ pause_strategy: {e}"

    def _cmd_strategy_health(self, args="") -> str:
        """
        /strategy_health (/sh) — live win rates per strategy from signal_log.db.
        Shows: WR%, sample count, status (✅ KEEP / ⚠ WATCH / 🔴 DISABLE).
        Breakeven WR for Indian markets ≈ 42% after STT + brokerage.
        """
        try:
            import sqlite3, json, os
            from pathlib import Path
            sig_db = Path(os.getenv("SIGNAL_LOG_DB", "signal_log.db"))
            if not sig_db.exists():
                return "❌ signal_log.db not found"

            conn = sqlite3.connect(str(sig_db))
            rows = conn.execute("""
                SELECT strategy,
                       COUNT(*) as n,
                       SUM(CASE WHEN tb_label=1  THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN tb_label=-1 THEN 1 ELSE 0 END) as losses,
                       ROUND(AVG(score),1) as avg_score
                FROM signal_log
                WHERE tb_label IN (1,-1)
                GROUP BY strategy
                HAVING n >= 8
                ORDER BY wins*1.0/(wins+losses) DESC
            """).fetchall()
            conn.close()

            # Paused strategies
            paused_raw = []
            if os.path.exists("paused_strategies.json"):
                try:
                    paused_raw = [s.upper() for s in json.loads(open("paused_strategies.json").read())]
                except Exception:
                    pass

            BREAKEVEN = 42.0
            lines = ["<b>📊 Strategy Health (live signal_log)</b>", "Breakeven WR ≈ 42% (after STT+brokerage)", ""]

            for r in rows:
                strat = str(r[0] or "fallback")
                n, w, l = int(r[1]), int(r[2] or 0), int(r[3] or 0)
                wr = w / (w + l) * 100 if (w + l) > 0 else 0

                if strat.upper() in paused_raw:
                    icon = "⏸"
                elif wr >= 60:
                    icon = "✅"
                elif wr >= BREAKEVEN:
                    icon = "🟡"
                elif wr >= 35:
                    icon = "⚠"
                else:
                    icon = "🔴"

                lines.append(
                    f"{icon} <b>{strat}</b>: {wr:.0f}% WR  "
                    f"({w}W/{l}L, n={n})  sc={r[4]}"
                )

            lines += [
                "",
                "✅=keep  🟡=watch  ⚠=reduce  🔴=disable  ⏸=paused",
                "Use /pause_strategy NAME to pause/resume",
            ]
            return "\n".join(lines)
        except Exception as exc:
            return f"❌ strategy_health: {exc}"

    def _cmd_shadow_mode(self, args="") -> str:
        """IMPROVEMENT 10: Toggle shadow (paper parallel to live) mode."""
        try:
            import json, os
            sf = "shadow_mode.json"
            state = json.loads(open(sf).read()) if os.path.exists(sf) else {"enabled": False}
            state["enabled"] = not state["enabled"]
            with open(sf,'w') as f: json.dump(state, f)
            status = "✅ ENABLED" if state["enabled"] else "❌ DISABLED"
            return (f"🪞 Shadow mode: {status}\n"
                    f"  Every live trade also runs on paper\n"
                    f"  Use /pnl to compare live vs paper")
        except Exception as e:
            return f"❌ shadow: {e}"

    def _cmd_blacklist(self, _="") -> str:
        """Show auto-blacklisted symbols."""
        try:
            import json, os
            from datetime import datetime
            bl_file = "symbol_blacklist.json"
            if not os.path.exists(bl_file):
                return "✅ No symbols blacklisted"
            bl = json.loads(open(bl_file).read())
            now = datetime.now()
            active = {k:v for k,v in bl.items() if datetime.fromisoformat(v) > now}
            if not active:
                return "✅ No symbols currently blacklisted"
            lines = ["🚫 <b>AUTO-BLACKLISTED SYMBOLS</b>", ""]
            for sym, until in sorted(active.items()):
                days_left = (datetime.fromisoformat(until) - now).days
                lines.append(f"  {sym:15} — banned {days_left} days more")
            lines += ["", "  Reason: 3+ SL hits in 7 days",
                      "  Auto-cleared after 14 days"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Blacklist: {e}"

    def _cmd_churn(self, _="") -> str:
        """Show subscribers at churn risk."""
        try:
            from subscriber_manager import format_churn_report
            return format_churn_report()
        except Exception as e:
            return f"❌ Churn: {e}"


    # ══════════════════════════════════════════════════════════════
    # NEW COMMANDS — Improvements & Gaps
    # ══════════════════════════════════════════════════════════════

    def _cmd_config(self, _="") -> str:
        """GAP 12: Show current bot configuration."""
        try:
            import config as cfg
            import os
            mode = "📄 PAPER" if getattr(cfg,"PAPER_TRADING",True) else "🔴 LIVE"
            capital = getattr(cfg,"REAL_CAPITAL",0)
            return (
                f"⚙️ <b>BOT CONFIGURATION</b>\n\n"
                f"  Mode:         {mode}\n"
                f"  Capital:      ₹{float(capital or 0):,.0f}\n"
                f"  Min score:    {os.getenv('MIN_CONFLUENCE_SCORE','5.5')}\n"
                f"  Max daily loss: ₹{os.getenv('MAX_DAILY_LOSS','2000')}\n"
                f"  Scan interval: {os.getenv('SCAN_INTERVAL_SEC','300')}s\n"
                f"  Strategies:   60 active\n"
                f"  Symbols:      196 scanning\n"
                f"  Free channel: {os.getenv('TELEGRAM_FREE_CHANNEL_ID','not set')[:20]}\n"
                f"  Premium:      {os.getenv('TELEGRAM_PREMIUM_CHANNEL_ID','not set')[:20]}\n\n"
                f"  Use /setcapital or /setthreshold to update"
            )
        except Exception as e:
            return f"❌ Config: {e}"

    def _cmd_setcapital(self, args="") -> str:
        """GAP 13: Update capital without restart."""
        try:
            if not args.strip().replace('.','').isdigit():
                return "Usage: /setcapital 50000\nSets new trading capital immediately"
            new_cap = float(args.strip())
            if new_cap < 5000:
                return "❌ Minimum capital is ₹5,000"
            # Update config
            import config as cfg
            cfg.REAL_CAPITAL = new_cap
            cfg.CAPITAL      = new_cap
            # Update .env file
            try:
                env_path = ".env"
                if os.path.exists(env_path):
                    env = open(env_path).read()
                    import re
                    env = re.sub(r"^REAL_CAPITAL=.*$", f"REAL_CAPITAL={new_cap:.0f}",
                                 env, flags=re.MULTILINE)
                    env = re.sub(r"^CAPITAL=.*$", f"CAPITAL={new_cap:.0f}",
                                 env, flags=re.MULTILINE)
                    open(env_path,"w").write(env)
            except Exception: pass
            return (f"✅ Capital updated to ₹{new_cap:,.0f}\n"
                    f"  Takes effect on next scan cycle\n"
                    f"  Position sizes will auto-adjust")
        except Exception as e:
            return f"❌ setcapital: {e}"

    def _cmd_setthreshold(self, args="") -> str:
        """GAP 14: Update signal score threshold."""
        try:
            if not args.strip().replace('.','').isdigit():
                return (f"Usage: /setthreshold 6.5\n"
                        f"Current: {os.getenv('MIN_CONFLUENCE_SCORE','5.5')}\n"
                        f"Typical: 5.5 (normal) / 7.0 (volatile market)")
            new_thresh = float(args.strip())
            if not (3.0 <= new_thresh <= 10.0):
                return "❌ Threshold must be 3.0–10.0"
            os.environ['MIN_CONFLUENCE_SCORE'] = str(new_thresh)
            try:
                env = open(".env").read()
                import re
                if 'MIN_CONFLUENCE_SCORE' in env:
                    env = re.sub(r"^MIN_CONFLUENCE_SCORE=.*$",
                                 f"MIN_CONFLUENCE_SCORE={new_thresh}",
                                 env, flags=re.MULTILINE)
                else:
                    env += f"\nMIN_CONFLUENCE_SCORE={new_thresh}"
                open(".env","w").write(env)
            except Exception: pass
            return (f"✅ Signal threshold → {new_thresh}\n"
                    f"  Higher = fewer, higher quality signals\n"
                    f"  Lower = more signals, some marginal")
        except Exception as e:
            return f"❌ setthreshold: {e}"

    def _cmd_trial(self, args="") -> str:
        """GAP 18: Start 7-day free premium trial."""
        try:
            from subscription_engine import start_trial
            bot = getattr(self, 'bot_ref', None)
            chat_id = getattr(bot, 'last_chat_id', args.strip()) if bot else args.strip()
            if not chat_id:
                return ("Type /trial to start your 7-day FREE premium trial\n"
                        "Get full access: signals, video briefs, P&L reports")
            return start_trial(str(chat_id))
        except Exception as e:
            return f"❌ Trial: {e}"

    def _cmd_subscribe_flow(self, args="") -> str:
        """GAP 15: Subscriber onboarding."""
        try:
            from subscription_engine import get_onboarding_message
            return get_onboarding_message()
        except Exception as e:
            return f"❌ Subscribe: {e}"

    def _cmd_my_plan(self, args="") -> str:
        """Show subscription status."""
        try:
            from subscription_engine import format_subscription_status
            bot     = getattr(self, 'bot_ref', None)
            chat_id = getattr(bot, 'last_chat_id', args.strip()) if bot else args.strip()
            return format_subscription_status(str(chat_id) if chat_id else "0")
        except Exception as e:
            return f"❌ Plan: {e}"

    def _cmd_sentiment_score(self, _="") -> str:
        """IMPROVEMENT H: Composite market sentiment score 0-100."""
        try:
            from market_intelligence_hub import get_composite_sentiment
            sent  = get_composite_sentiment()
            score = sent.get('score', 50)
            label = sent.get('label', 'NEUTRAL')
            emoji = sent.get('emoji', '⚪')
            comps = sent.get('components', {})
            lines = [
                f"{emoji} <b>MARKET SENTIMENT SCORE</b>",
                f"",
                f"  Score: <b>{score:.0f}/100</b> — {label}",
                f"  {'█' * int(score//5)}{'░' * (20 - int(score//5))}",
                f"",
                f"  <b>COMPONENTS</b>",
            ]
            for comp, data in comps.items():
                cont = data.get('contribution', 0)
                ci = "🟢" if cont > 0 else "🔴" if cont < 0 else "⚪"
                lines.append(f"  {ci} {comp:10} {cont:+.1f}")
            lines += [
                f"",
                f"  >70 = Bullish | 30-70 = Neutral | <30 = Bearish",
                f"  Updates every 10 min during market hours"
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Sentiment: {e}"

    def _cmd_analytics(self, _="") -> str:
        """Win rate by hour + regime + Sharpe."""
        try:
            from performance_analytics import format_analytics_report
            return format_analytics_report()
        except Exception as e:
            return f"❌ Analytics: {e}"

    def _cmd_rollover(self, args="") -> str:
        """Show rollover / cost of carry signal."""
        try:
            from market_intelligence_hub import get_rollover_signal
            sym  = args.strip().upper() or "NIFTY"
            rl   = get_rollover_signal(sym)
            if not rl:
                return f"⚠️ Rollover data not available for {sym}"
            carry = rl.get('carry_pct', 0)
            sig   = rl.get('signal','NEUTRAL')
            icon  = "🟢" if sig=="BULLISH" else "🔴" if sig=="BEARISH" else "⚪"
            return (
                f"📐 <b>ROLLOVER SIGNAL — {sym}</b>\n\n"
                f"  Spot:    ₹{rl.get('spot',0):,.0f}\n"
                f"  Futures: ₹{rl.get('futures',0):,.0f}\n"
                f"  Carry:   {carry:+.3f}% ({rl.get('carry_annual',0):+.1f}% ann.)\n"
                f"  {icon} Signal: {sig}\n\n"
                f"  {rl.get('narrative','')}\n\n"
                f"  Positive carry = bulls rolling (bullish)\n"
                f"  Negative carry = bears dominant (bearish)"
            )
        except Exception as e:
            return f"❌ Rollover: {e}"


    def _cmd_market_score(self, _="") -> str:
        """Composite market health score 0-100."""
        try:
            from datetime import datetime as _dt
            score  = 50.0
            parts  = []

            # 1. India VIX (lower = better)
            try:
                import requests as _rq
                _s = _rq.Session()
                _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                _s.get("https://www.nseindia.com/", timeout=3)
                _r = _s.get("https://www.nseindia.com/api/allIndices", timeout=6)
                for idx in _r.json().get("data",[]):
                    if "INDIA VIX" in str(idx.get("index","")).upper():
                        vix = float(idx.get("last",18) or 18)
                        vix_score = max(0, min(30, (30 - vix) * 1.5))
                        score += vix_score - 15
                        parts.append(f"VIX {vix:.1f} ({'+' if vix_score>15 else ''}{vix_score-15:.0f})")
                        break
            except Exception: pass

            # 2. News sentiment
            try:
                from news_sentiment_engine import get_full_sentiment
                sent = get_full_sentiment()
                ns = sent.get("avg_score", 0) * 20  # -20 to +20
                score += ns
                parts.append(f"News {sent.get('sentiment','?')} ({'+' if ns>0 else ''}{ns:.0f})")
            except Exception: pass

            # 3. FII flows
            try:
                from fii_data_fetcher import fetch_nse_fii_dii_today
                fii = fetch_nse_fii_dii_today() or {}
                fii_net = float(fii.get("fii_net", fii.get("net", 0)) or 0)
                fii_score = max(-15, min(15, fii_net / 500))
                score += fii_score
                parts.append(f"FII ₹{fii_net:.0f}Cr ({'+' if fii_score>0 else ''}{fii_score:.0f})")
            except Exception: pass

            # 4. PCR (Put-Call Ratio)
            try:
                from wow_factors_v2 import detect_unusual_options_activity
                pcr_data = detect_unusual_options_activity("NIFTY")
                pcr = pcr_data.get("pcr", 1.0)
                # PCR 0.7-1.3 is healthy; extremes = trouble
                if 0.7 <= pcr <= 1.3:
                    pcr_score = 10
                elif pcr > 1.5 or pcr < 0.5:
                    pcr_score = -10
                else:
                    pcr_score = 0
                score += pcr_score
                parts.append(f"PCR {pcr:.2f} ({'+' if pcr_score>=0 else ''}{pcr_score})")
            except Exception: pass

            # Clamp 0-100
            score = max(0, min(100, score))

            # Grade
            if score >= 75:   grade, icon = "VERY BULLISH",  "🟢🟢"
            elif score >= 60: grade, icon = "BULLISH",        "🟢"
            elif score >= 45: grade, icon = "NEUTRAL",        "⚪"
            elif score >= 30: grade, icon = "BEARISH",        "🔴"
            else:             grade, icon = "VERY BEARISH",   "🔴🔴"

            lines = [
                f"📊 <b>MARKET HEALTH SCORE</b>",
                f"  {icon} <b>{score:.0f}/100 — {grade}</b>",
                f"  ─────────────────────────────",
            ]
            for p in parts:
                lines.append(f"  • {p}")
            lines += [
                f"  ─────────────────────────────",
                f"  ⏰ {_dt.now().strftime('%d-%b %H:%M')}",
                f"  Combines: VIX + News + FII + PCR",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Market score: {e}"


    def _cmd_diagnose(self, _="") -> str:
        """Diagnose why no signals are being generated."""
        try:
            from datetime import datetime
            import os
            lines = ["🔍 <b>SIGNAL DIAGNOSE</b>", ""]

            # Check 1: Market hours
            now = datetime.now()
            is_mkt = (now.weekday() < 5 and
                      datetime.strptime("09:15", "%H:%M").time() <= now.time() <=
                      datetime.strptime("15:30", "%H:%M").time())
            lines.append(f"  Market hours: {'✅ YES' if is_mkt else '❌ NO (outside 9:15-3:30)'}")

            # Check 2: Angel balance
            try:
                import config as cfg
                bal = float(getattr(cfg, "REAL_CAPITAL", 0) or 0)
                lines.append(f"  Capital: {'✅' if bal > 0 else '❌'} ₹{bal:,.0f}")
            except Exception: pass

            # Check 3: Data fetch test
            try:
                from data_fetcher import DataFetcher
                df_obj = _get_angel_data_fetcher()
                test_df = df_obj.get_market_data("NIFTY", interval="5m", days=3)
                if test_df is not None and len(test_df) > 5:
                    last_bar = str(test_df.index[-1])[:16]
                    lines.append(f"  Data feed: ✅ NIFTY ({len(test_df)} bars, last: {last_bar})")
                else:
                    lines.append("  Data feed: ❌ NIFTY data empty — Angel/yfinance failed")
            except Exception as e:
                lines.append(f"  Data feed: ❌ {str(e)[:50]}")

            # Check 4: Signal threshold
            thresh = os.getenv("MIN_CONFLUENCE_SCORE", "5.5")
            lines.append(f"  Threshold: {thresh} (use /setthreshold N to change)")

            # Check 5: Daily loss limit
            try:
                _bot = getattr(self, "bot_ref", None)
                dlm = getattr(getattr(_bot, "live_engine", None), "daily_loss_manager", None)
                can = dlm.can_trade() if dlm else True
                lines.append(f"  Daily loss gate: {'✅ OK' if can else '❌ BLOCKED — daily loss hit'}")
            except Exception: pass

            # Check 6: Kill switch
            try:
                from kill_switch import KillSwitch
                ks = KillSwitch()
                lines.append(f"  Kill switch: {'❌ ACTIVE — all trading stopped' if ks.is_active() else '✅ off'}")
            except Exception: pass

            # Check 7: Blacklist / paused
            import json
            bl = json.loads(open("symbol_blacklist.json").read()) if os.path.exists("symbol_blacklist.json") else {}
            ps = json.loads(open("paused_symbols.json").read()) if os.path.exists("paused_symbols.json") else []
            lines.append(f"  Blacklisted: {len(bl)} symbols | Paused: {len(ps)} symbols")

            lines += ["", "  If data feed ❌: bot is scanning 0 symbols",
                      "  Fix: ./bot.sh restart at 9:00 AM before market open",
                      "  Or: ensure Angel One session token is valid"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Diagnose: {e}"



    def _cmd_session(self, _="") -> str:
        """Force refresh Angel One session token."""
        try:
            import config as cfg
            from angel import AngelOne
            ang = AngelOne()
            ok = ang._auto_refresh_session()
            if ok:
                return ("✅ <b>SESSION REFRESHED</b>\n\n"
                        "  Angel One TOTP re-generated\n"
                        "  New JWT token issued\n"
                        "  Data fetch will resume from next scan\n\n"
                        "  If Scanned: 0 persists → /restart")
            else:
                return ("❌ Session refresh failed\n\n"
                        "  Check TOTP_SECRET and PASSWORD in .env\n"
                        "  Try: ./bot.sh restart")
        except Exception as e:
            return f"❌ Session: {e}"


    def _cmd_export_tax(self, args="") -> str:
        """Export P&L in ITR-3 / CBDT format for tax filing."""
        try:
            from tax_export import format_tax_summary_message
            fy = args.strip() or None
            return format_tax_summary_message(fy)
        except Exception as e:
            return f"❌ Tax export: {e}"

    def _cmd_zerodha_status(self, _="") -> str:
        """Show Zerodha Kite connection status."""
        try:
            from zerodha_client import is_configured, is_token_valid
            configured = is_configured()
            valid      = is_token_valid() if configured else False
            return (
                f"📊 <b>ZERODHA KITE STATUS</b>\n\n"
                f"  Configured:   {'✅ YES' if configured else '❌ No — see /help zerodha'}\n"
                f"  Token valid:  {'✅ YES' if valid else '❌ Expired — refresh manually'}\n\n"
                + ('' if configured else
                   "  To enable:\n"
                   "  1. Subscribe: kite.trade (₹2,000/mo)\n"
                   "  2. Add to .env:\n"
                   "     ZERODHA_API_KEY=xxx\n"
                   "     ZERODHA_ACCESS_TOKEN=yyy (daily)\n"
                   "  3. /restart\n\n"
                   "  When to use:\n"
                   "  → Capital > ₹2,00,000\n"
                   "  → Need sub-100ms execution\n"
                   "  → Angel One is having outages\n"
                   "  Current: Angel One (FREE, auto-refresh)")
            )
        except Exception as e:
            return f"❌ Zerodha: {e}"

    def _cmd_dhan_status(self, _="") -> str:
        """Show Dhan API connection status."""
        try:
            from dhan_client import is_configured
            ok = is_configured()
            return (
                f"📊 <b>DHAN API STATUS</b>\n\n"
                f"  Configured: {'✅ YES — permanent token active' if ok else '❌ Not configured'}\n\n"
                + ('' if ok else
                   "  Setup (5 minutes, FREE):\n"
                   "  1. Login dhan.co\n"
                   "  2. Profile → API Access\n"
                   "  3. Copy Client Code + Access Token\n"
                   "  4. Add to .env:\n"
                   "     DHAN_CLIENT_CODE=xxx\n"
                   "     DHAN_TOKEN_ID=yyy\n"
                   "  5. /restart\n\n"
                   "  Benefit: Free intraday backup for NIFTY/BANKNIFTY\n"
                   "  Token never expires — set once, works forever")
            )
        except Exception as e:
            return f"❌ Dhan: {e}"


    def _cmd_broker(self, _="") -> str:
        """Show broker status + Angel vs Zerodha recommendation."""
        try:
            import os, config as cfg
            from broker_comparison import should_use_zerodha
            capital = float(getattr(cfg, "REAL_CAPITAL", 0) or 0)
            decision = should_use_zerodha(capital)

            # Check Dhan status
            try:
                from dhan_client import is_configured as dhan_ok
                dhan_status = "✅ Configured" if dhan_ok() else "❌ Not configured (add DHAN_CLIENT_CODE + DHAN_TOKEN_ID)"
            except Exception:
                dhan_status = "❌ Not configured"

            # Check Zerodha status
            try:
                from zerodha_client import is_configured as zd_ok
                zd_status = "✅ Configured" if zd_ok() else "❌ Not configured"
            except Exception:
                zd_status = "❌ Not configured"

            lines = [
                "🏦 <b>BROKER STATUS</b>", "",
                f"  💰 Capital: ₹{capital:,.0f}",
                "",
                "  <b>ACTIVE BROKERS</b>",
                f"  ✅ Angel One (primary) — free, TOTP auto-refresh",
                f"  {'✅' if 'Configured' in dhan_status else '⚠️ '} Dhan (data backup) — {dhan_status}",
                f"  {'✅' if 'Configured' in zd_status else '⚠️ '} Zerodha — {zd_status}",
                "",
                "  <b>RECOMMENDATION</b>",
                f"  {decision['recommendation']}",
                f"  Zerodha API cost: {decision['api_cost_pct']:.1f}% of capital/year",
                "",
                "  <b>ANGEL vs ZERODHA</b>",
                "  Angel One:  Free | TOTP auto | 500d history | ~150ms",
                "  Zerodha:    ₹2K/mo | Daily token | 2000d history | ~50ms",
                "  Break-even: ₹2L capital (when ₹2K/mo = <1% of capital)",
                "",
                "  <b>DATA SOURCES</b>",
                "  Primary:  Angel One SmartAPI (auto-refresh)",
                "  Backup 1: Dhan API (permanent token, free)",
                "  Backup 2: NSE direct + Stooq",
                "  Global:   Stooq (no limit, no auth)",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Broker: {e}"

    def _cmd_dhan_setup(self, _="") -> str:
        """Show Dhan setup instructions."""
        try:
            from dhan_client import is_configured
            if is_configured():
                return "✅ Dhan already configured!"
            return (
                "📋 <b>DHAN SETUP (FREE, 5 minutes)</b>\n\n"
                "  Dhan never expires — one-time setup only\n\n"
                "  <b>Steps:</b>\n"
                "  1. Open dhan.co on your phone\n"
                "  2. Tap Menu → My Profile → API Access\n"
                "  3. Copy Client Code and Access Token\n"
                "  4. Run these commands:\n"
                "     <code>echo 'DHAN_CLIENT_CODE=xxx' >> .env</code>\n"
                "     <code>echo 'DHAN_TOKEN_ID=yyy' >> .env</code>\n"
                "     <code>./bot.sh restart</code>\n\n"
                "  That\'s it — token never expires!\n"
                "  Gives you FREE backup for NIFTY/BANKNIFTY 5m data"
            )
        except Exception as e:
            return f"❌ {e}"



    def _cmd_re_entry_status(self, _="") -> str:
        """Show re-entry cooldown status for symbols."""
        try:
            from market_intelligence_hub import _SL_HIT_LOG, _SL_COOLDOWN_SECS
            import time
            now = time.time()
            if not _SL_HIT_LOG:
                return "✅ <b>RE-ENTRY STATUS</b>\n\n  No symbols in cooldown\n  All symbols eligible for re-entry"
            lines = ["⏱ <b>RE-ENTRY COOLDOWN STATUS</b>", ""]
            for sym, ts in sorted(_SL_HIT_LOG.items()):
                remaining = _SL_COOLDOWN_SECS - (now - ts)
                if remaining > 0:
                    lines.append(f"  🔴 {sym:15} {int(remaining//60)}m {int(remaining%60)}s remaining")
                else:
                    lines.append(f"  ✅ {sym:15} cooldown expired")
            lines += ["", "  Cooldown: 30 min after SL hit"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ re_entry_status: {e}"

    def _cmd_conn(self, _="") -> str:
        """Trigger fresh connection check."""
        try:
            from connection_monitor import get_monitor
            mon = get_monitor()
            import threading
            threading.Thread(
                target=mon.run_full_check,
                args=("ON-DEMAND",),
                daemon=True
            ).start()
            return ("🔌 <b>CONNECTION CHECK STARTED</b>\n\n"
                    "  Running checks on all 14 connections...\n"
                    "  Results in ~15 seconds\n\n"
                    "  Use /health to see results")
        except Exception as e:
            return f"❌ connections: {e}"

    def _cmd_add_subscriber(self, args: str = "") -> str:
        """Add a subscriber to premium channel. Usage: /addsub CHAT_ID Name tier"""
        try:
            parts = args.strip().split()
            if len(parts) < 1 or (parts[0].startswith("/") and len(parts) < 2):
                return (
                    "📋 <b>ADD SUBSCRIBER</b>\n\n"
                    "  Usage: /addsub CHAT_ID Name tier\n"
                    "  Example: /addsub 123456789 Ramesh premium\n\n"
                    "  Tiers: free | basic | premium | owner"
                )
            # Skip command word if included
            if parts[0].startswith("/"):
                parts = parts[1:]
            chat_id = parts[0]
            name    = parts[1] if len(parts) > 1 else f"User_{chat_id[-4:]}"
            tier    = parts[2] if len(parts) > 2 else "premium"
            from subscriber_manager import add_subscriber
            result = add_subscriber(chat_id, name, tier)
            return f"✅ {result}" if result else f"✅ Added {name} ({tier}) — chat_id: {chat_id}"
        except Exception as e:
            return f"❌ addsub: {e}"


    def _cmd_corpactions(self, args: str = "") -> str:
        """Stub for /corpactions — implementation pending."""
        return f"⚠️ /corpactions is not yet implemented in this version"


    def _cmd_social(self, args: str = "") -> str:
        """Stub for /social — implementation pending."""
        return f"⚠️ /social is not yet implemented in this version"


    def _cmd_wow2(self, args: str = "") -> str:
        """Stub for /wow2 — implementation pending."""
        return f"⚠️ /wow2 is not yet implemented in this version"


    def _cmd_remote_deploy(self, _="") -> str:
        """Smart deploy: version check, test, restart."""
        import subprocess, os as _osd
        try:
            _env = dict(os.environ)
            try:
                for _el in open("/home/sridhar/Desktop/trading_robot/.env"):
                    _el = _el.strip()
                    if "=" in _el and not _el.startswith("#"):
                        _ek,_,_ev = _el.partition("=")
                        _env[_ek] = _ev
            except Exception: pass
            subprocess.Popen(
                ["bash", "/home/sridhar/Desktop/trading_robot/do_deploy.sh"],
                env=_env, start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True
            )
            return ("🚀 <b>SMART DEPLOY</b>\n\n"
                    "  ⏳ Checking version on Drive...\n"
                    "  ⏳ If newer → extract + test + restart\n"
                    "  ⏳ If same → skip\n"
                    "  ✅ Result sent after restart")
        except Exception as e:
            return f"❌ Deploy: {e}"

    def _cmd_diag_scan(self, _="") -> str:
        """Run inline diagnostic without subprocess risk."""
        try:
            out = "🔧 <b>SCAN DIAGNOSTIC</b>\n\n"
            
            # Step 1: Check Angel connection
            out += "[1] Angel Connection\n"
            try:
                from angel_broker import AngelBroker
                ab = AngelBroker(
                    os.getenv("API_KEY", ""),
                    os.getenv("CLIENT_ID", ""),
                    os.getenv("PASSWORD", ""),
                    os.getenv("TOTP_SECRET", ""),
                    paper_trade=False
                )
                out += f"  ✓ Angel instance created\n"
                out += f"  obj: {type(ab.angel.obj).__name__ if ab.angel.obj else 'None'}\n"
                out += f"  paper_trade: {ab.angel.paper_trade}\n"
            except Exception as e:
                out += f"  ✗ Angel init failed: {str(e)[:100]}\n"
                return out
            
            # Step 2: Try to fetch NIFTY data
            out += "\n[2] DataFetcher test\n"
            try:
                from data_fetcher import DataFetcher
                df = DataFetcher(symbols_csv="nifty.csv", paper_trade=False)
                df.angel = ab.angel
                nifty_data = df.get_market_data("NIFTY", interval="5m", days=5)
                bars = len(nifty_data) if nifty_data is not None else 0
                out += f"  ✓ DataFetcher created, angel assigned\n"
                out += f"  NIFTY bars: {bars}\n"
                if bars < 10:
                    out += f"  ⚠️ Low bar count (expected 50+)\n"
            except Exception as e:
                out += f"  ✗ DataFetcher failed: {str(e)[:100]}\n"
            
            # Step 3: LiveSignalEngine test
            out += "\n[3] LiveSignalEngine test\n"
            try:
                from live_signal_engine import LiveSignalEngine
                lse = LiveSignalEngine()
                angel_set = lse.data_fetcher.angel is not None
                method = getattr(lse, "_angel_source_method", None)
                out += f"  LSE created\n"
                out += f"  DataFetcher.angel: {type(lse.data_fetcher.angel).__name__ if angel_set else 'None'}\n"
                out += f"  Method: {method}\n"
                
                if angel_set:
                    md = lse.data_fetcher.get_market_data("NIFTY", interval="5m", days=5)
                    bars = len(md) if md is not None else 0
                    out += f"  NIFTY bars via LSE: {bars}\n"
                    if bars >= 50:
                        out += f"  ✅ SCANNED WILL WORK\n"
                    elif bars >= 5:
                        out += f"  ⚠️ Low bars but above MIN (5)\n"
                    else:
                        out += f"  ❌ SCANNED: 0 — fetch returned 0 bars\n"
                else:
                    out += f"  ❌ Angel not set — THIS is why Scanned: 0\n"
            except Exception as e:
                out += f"  ✗ LSE test: {str(e)[:100]}\n"
            
            # Step 4: Rejection stats — why signals aren't firing
            out += "\n[4] Rejection Stats\n"
            try:
                import json as _rj, os as _ros
                _rf = "rejection_stats.json"
                if _ros.path.exists(_rf):
                    _rs = _rj.loads(open(_rf).read())
                    out += f"  Date: {_rs.get('date','?')}\n"
                    out += f"  Scanned: {_rs.get('total_scanned',0)}  Passed: {_rs.get('passed',0)}\n"
                    out += f"  Pass rate: {_rs.get('pass_rate',0):.1%}\n"
                    _reasons = _rs.get("top_rejection_reasons", {})
                    if _reasons:
                        out += "  Top rejection reasons:\n"
                        for k, v in list(_reasons.items())[:6]:
                            out += f"    · {k}: {v}×\n"
                    else:
                        out += "  No per-reason breakdown yet (accumulates during market hours)\n"
                else:
                    out += "  rejection_stats.json not found (bot not yet run today)\n"
            except Exception as _re:
                out += f"  ✗ {_re}\n"

            out += f"\n🕐 {datetime.now().strftime('%H:%M:%S')}"
            return out

        except Exception as e:
            import traceback
            err = traceback.format_exc()[-500:]
            return f"❌ Diagnostic crashed:\n<pre>{err}</pre>"

    def _cmd_fix_angel(self, _="") -> str:
        """Check and fix Angel connection."""
        try:
            # Read angel.py and check if fix is applied
            from pathlib import Path
            ang_src = Path("angel.py").read_text()
            has_fix = "ALWAYS connect for DATA" in ang_src
            
            # Try connecting
            from angel import AngelOne
            import os as _os_fa
            ang = AngelOne(
                api_key=_os_fa.getenv("API_KEY",""),
                client_id=_os_fa.getenv("CLIENT_ID",""),
                password=_os_fa.getenv("PASSWORD",""),
                totp_secret=_os_fa.getenv("TOTP_SECRET",""),
            )
            
            lines = [
                "🔧 <b>ANGEL CONNECTION CHECK</b>",
                "",
                f"  Code fix applied: {'✅' if has_fix else '❌ OLD CODE — deploy new zip'}",
                f"  Angel obj exists: {'✅' if ang.obj else '❌ Connection failed'}",
                f"  paper_trade:      {ang.paper_trade}",
                f"  Client ID:        {_os_fa.getenv('CLIENT_ID','NOT SET')}",
            ]
            
            if ang.obj:
                import time
                time.sleep(1)
                bal = ang.get_balance(force_real=True)
                lines.append(f"  Balance:          ₹{bal:,.0f}")
                
                # Try data fetch
                try:
                    from data_fetcher import DataFetcher
                    df_obj = DataFetcher(angel=ang, paper_trade=False)
                    data = df_obj.get_market_data("NIFTY", interval="5m", days=5)
                    if data is not None:
                        lines.append(f"  NIFTY data:       ✅ {len(data)} bars")
                    else:
                        lines.append(f"  NIFTY data:       ❌ None returned")
                except Exception as e:
                    lines.append(f"  Data fetch:       ❌ {e}")
            else:
                lines += [
                    "",
                    "  ❌ Angel not connected",
                    "  Fix: /deploy (pulls new code from Drive)",
                    "  Or: ./remote_deploy.sh on machine",
                ]
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Angel check: {e}"

    def _cmd_datasource_health(self, _="") -> str:
        """Show health scores of all data sources."""
        try:
            from data_source_resilience import get_all_source_health
            health = get_all_source_health()
            if not health:
                return ("📡 <b>DATA SOURCE HEALTH</b>\n\n"
                        "  No health data yet — sources are being monitored\n"
                        "  Data accumulates after first scan cycle")
            lines = ["📡 <b>DATA SOURCE HEALTH</b>", ""]
            for source, score in sorted(health.items(), key=lambda x: x[1]):
                icon = "🟢" if score >= 80 else "🟡" if score >= 50 else "🔴"
                bar  = "█" * (score // 10) + "░" * (10 - score // 10)
                lines.append(f"  {icon} {source:20} {bar} {score}%")
            lines += ["", "  🟢 ≥80% | 🟡 50-80% | 🔴 <50%",
                      "  Auto-failover triggers at <40%"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Health: {e}"

    def _cmd_gift_nifty(self, _="") -> str:
        """Show GIFT Nifty pre-market signal."""
        try:
            from data_source_resilience import get_gift_nifty, get_gift_nifty_gap
            gift = get_gift_nifty()
            if not gift:
                return "⚠️ GIFT Nifty data unavailable (before 6 AM or market closed)"
            price  = gift.get("price", 0)
            source = gift.get("source", "unknown")
            lines  = [f"🎁 <b>GIFT NIFTY</b> ({source})", "",
                      f"  Price:  ₹{price:,.1f}"]
            lines += ["", "  Use /morning for full pre-market analysis"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ GIFT Nifty: {e}"

    def _cmd_macro(self, _="") -> str:
        """Show macro economic indicators."""
        try:
            from data_source_resilience import get_macro_data
            data = get_macro_data()
            if not data:
                return "⚠️ Macro data unavailable (add FRED_API_KEY to .env for US data)"
            lines = ["📊 <b>MACRO INDICATORS</b>", ""]
            labels = {"fed_rate": "US Fed Rate",  "us_10y":    "US 10Y Yield",
                      "repo_rate": "RBI Repo Rate", "india_cpi": "India CPI"}
            for key, label in labels.items():
                val = data.get(key)
                if val is not None:
                    lines.append(f"  {label:20} {val:.2f}%")
            lines += ["", "  Source: FRED (US) + RBI + World Bank (India)",
                      "  Updates every 6 hours"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Macro: {e}"

    def _cmd_sectors(self, _="") -> str:
        """Show live sectoral index performance."""
        try:
            from data_source_resilience import get_all_sector_indices
            indices = get_all_sector_indices()
            if not indices:
                return "⚠️ Sector data unavailable"
            # Sort by change_pct
            sorted_idx = sorted(indices.items(),
                                key=lambda x: x[1].get("change_pct",0),
                                reverse=True)
            lines = ["📊 <b>SECTOR PERFORMANCE (Live NSE)</b>", ""]
            for name, data in sorted_idx[:15]:
                chg  = data.get("change_pct", 0)
                icon = "🟢" if chg > 0.3 else "🔴" if chg < -0.3 else "⚪"
                lines.append(f"  {icon} {name[:25]:25} {chg:+.2f}%")
            lines += ["", f"  Total indices: {len(indices)}",
                      "  Source: NSE allIndices (live)"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Sectors: {e}"

    def _cmd_earnings_calendar(self, _="") -> str:
        """Show upcoming earnings in next 7 days."""
        try:
            from data_source_resilience import get_earnings_calendar
            cal = get_earnings_calendar(days_ahead=7)
            if not cal:
                return "✅ No earnings scheduled in next 7 days"
            lines = ["📅 <b>UPCOMING EARNINGS (7 days)</b>", ""]
            for item in cal[:15]:
                sym  = item.get("symbol","?")
                dt   = item.get("date","?")[:10]
                typ  = item.get("type","?")[:30]
                lines.append(f"  {sym:15} {dt}  {typ}")
            lines += ["", "  ⚠️ Bot auto-reduces size near earnings",
                      "  On results day: no trade (blackout)"]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Calendar: {e}"

    def _cmd_pcr(self, args="") -> str:
        """Show Put-Call Ratio for index or stock."""
        try:
            from data_source_resilience import compute_pcr, get_stock_pcr
            sym  = args.strip().upper() or "NIFTY"
            is_idx = sym in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}
            pcr  = compute_pcr(sym) if is_idx else get_stock_pcr(sym)
            if pcr == 0:
                return f"⚠️ PCR data unavailable for {sym}"
            icon  = "🐂" if pcr > 1.2 else "🐻" if pcr < 0.8 else "⚪"
            interp = ("BULLISH — puts >> calls (market hedged)"
                      if pcr > 1.3 else
                      "BEARISH — calls >> puts (market exposed)"
                      if pcr < 0.7 else
                      "NEUTRAL")
            return (f"📊 <b>PUT-CALL RATIO — {sym}</b>\n\n"
                    f"  PCR:   {pcr:.3f}\n"
                    f"  {icon} {interp}\n\n"
                    f"  >1.3 = Bearish sentiment (contrarian: bullish)\n"
                    f"  <0.7 = Bullish sentiment (contrarian: bearish)")
        except Exception as e:
            return f"❌ PCR: {e}"

    def _cmd_datasources_keys(self, _="") -> str:
        """Show all API key status."""
        import os
        from datetime import datetime as _dt
        keys = {
            "Angel One":     os.getenv("API_KEY",""),
            "Tiingo":        os.getenv("TIINGO_KEY",""),
            "Twelve Data":   os.getenv("TWELVE_DATA_KEY",""),
            "Alpha Vantage": os.getenv("ALPHA_VANTAGE_KEY",""),
            "Fyers":         os.getenv("FYERS_TOKEN",""),
            "GitHub":        os.getenv("GITHUB_TOKEN",""),
            "Anthropic AI":  os.getenv("ANTHROPIC_API_KEY",""),
        }
        lines = [f"🔑 <b>API KEYS STATUS</b> | {_dt.now().strftime('%H:%M')}", ""]
        for name, val in keys.items():
            if val and len(val) > 4:
                preview = val[:4] + "****"
                lines.append(f"  ✅ {name:15} {preview}")
            else:
                lines.append(f"  ❌ {name:15} MISSING")
        lines += ["", "  Add missing keys to .env then /restart"]
        return "\n".join(lines)

    def _cmd_datasources(self, _="") -> str:
        """Check health of all data sources."""
        try:
            from data_sources import source_health_check
            results = source_health_check()
            note  = results.pop("_note", "")
            cache = results.pop("_cache", "")
            lines = ["<b>📡 DATA SOURCES (10 sources)</b>", ""]
            for name, status in results.items():
                lines.append(f"  {status} {name}")
            lines += ["",
                      "Free keys (register once):",
                      "  twelvedata.com → TWELVE_DATA_KEY",
                      "  alphavantage.co → ALPHA_VANTAGE_KEY",
                      "  tiingo.com → TIINGO_KEY",
                      cache]
            if note: lines.append(note)
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Data sources: {e}"

    def _cmd_stt(self, _="") -> str:
        """Show April 2026 STT charges and breakeven per instrument."""
        try:
            from volume_profile_advanced import get_stt_breakeven_points
            lines = [
                "<b>📊 APRIL 2026 STT CHARGES</b>",
                "STT doubled from April 1 — budget change",
                "─────────────────────────────────",
            ]
            for sym in ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]:
                be = get_stt_breakeven_points(sym)
                lines.append(
                    f"  {sym:<12} BE: {be['breakeven_pts']:.0f} pts  "
                    f"Min target: {be['min_target_pts']:.0f} pts"
                )
            lines += [
                "─────────────────────────────────",
                "⚠️ Was ~7 pts. Now ~15 pts breakeven.",
                "Min score raised to 4.5 (was 3.5).",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ STT: {e}"

    def _cmd_calibrate(self, _="") -> str:
        """Score calibration — win rate by score bucket and confluence level."""
        try:
            from score_calibrator import get_calibrator
            return get_calibrator().summary()
        except Exception as e:
            return f"❌ Calibration: {e}"


    def _cmd_darkpool(self, _="") -> str:
        try:
            from dark_pool import dark_pool_summary
            return dark_pool_summary()
        except Exception as e: return f"❌ Dark pool: {e}"

    def _cmd_fiipos(self, _="") -> str:
        try:
            from fii_options_positioning import fii_positioning_summary
            return fii_positioning_summary()
        except Exception as e: return f"❌ FII options: {e}"

    def _cmd_hmm(self, args="") -> str:
        try:
            from data_fetcher import DataFetcher
            from hmm_regime import detect_regime_hmm
            sym = args.strip().upper() or "NIFTY"
            df = DataFetcher(angel=None).get_market_data(sym,"1d",60)
            if df is None: return f"❌ No data for {sym}"
            r = detect_regime_hmm(df)
            return (f"🧠 <b>HMM REGIME — {sym}</b>\n"
                    f"   Regime:     {r.get('regime','?')}\n"
                    f"   Confidence: {r.get('confidence',0):.0%}\n"
                    f"   Entropy:    {r.get('entropy',0):.3f} "
                    f"{'⚠️ HIGH NOISE' if r.get('entropy',0)>0.8 else '✅ OK'}\n"
                    f"   Size mult:  {r.get('size_multiplier',1.0):.1f}×\n"
                    f"   Source:     {r.get('source','?')}\n\n"
                    f"   {r.get('description','')}")
        except Exception as e: return f"❌ HMM: {e}"

    def _cmd_elliott(self, args="") -> str:
        try:
            from data_fetcher import DataFetcher
            from elliott_wave import detect_elliott_waves
            sym = args.strip().upper() or "NIFTY"
            df = DataFetcher(angel=None).get_market_data(sym,"1d",60)
            if df is None: return f"❌ No data for {sym}"
            r = detect_elliott_waves(df)
            if not r.get("wave"):
                return f"📊 <b>ELLIOTT WAVE — {sym}</b>\n   No clear wave structure detected"
            return (f"📊 <b>ELLIOTT WAVE — {sym}</b>\n"
                    f"   Wave:    {r.get('wave','?')}\n"
                    f"   Signal:  {r.get('signal','?')}\n"
                    f"   Score:   {r.get('score',0):.1f}\n"
                    f"   Conf:    {r.get('confidence',0):.0%}\n"
                    f"   Targets: {r.get('targets',[])}\n\n"
                    f"   {r.get('note','')}")
        except Exception as e: return f"❌ Elliott: {e}"

    def _cmd_orderflow(self, args="") -> str:
        try:
            from data_fetcher import DataFetcher
            from order_flow import compute_order_flow
            sym = args.strip().upper() or "NIFTY"
            df = DataFetcher(angel=None).get_market_data(sym,"1d",30)
            if df is None: return f"❌ No data for {sym}"
            of = compute_order_flow(df)
            return (f"📈 <b>ORDER FLOW — {sym}</b>\n"
                    f"   Pressure:    {of.get('pressure','?')}\n"
                    f"   Buy ratio:   {of.get('buy_ratio',0):.0%}\n"
                    f"   Delta:       {of.get('delta',0):+,.0f}\n"
                    f"   Cum delta:   {of.get('cum_delta',0):+,.0f}\n"
                    f"   Divergence:  {'⚠️ YES' if of.get('divergence') else '✅ No'}\n"
                    f"   Absorption:  {'✅ YES' if of.get('absorption') else 'No'}\n"
                    f"   Score mod:   {of.get('score_modifier',0):+.2f}\n\n"
                    f"   {of.get('note','')}")
        except Exception as e: return f"❌ Order flow: {e}"

    def _cmd_metalearner(self, _="") -> str:
        try:
            from meta_learner import get_meta_learner
            ml = get_meta_learner()
            # Get current regime
            regime = "NEUTRAL"
            try:
                bot = self.bot_ref
                regime = str(getattr(bot, "_last_regime", "NEUTRAL") or "NEUTRAL").upper()
            except Exception: pass
            top = ml.top_strategies(5, regime)
            from datetime import datetime as _dt
            lines = [
                f"🧠 <b>META-LEARNER</b> | {_dt.now().strftime("%H:%M")}",
                f"   Regime: <b>{regime}</b>",
                f"   Strategies tracked: {len(ml._trades)}",
                f"   Total trades recorded: {sum(len(v) for v in ml._trades.values())}",
                "",
            ]
            if top:
                lines.append("   <b>Top strategies by weight:</b>")
                for s in top:
                    bar = "█" * max(1,int(s["weight"]/5))
                    lines.append(f"   {s["strategy"][:22]:22} {s["weight"]:.1f}% "
                                 f"Sharpe={s["sharpe"]:+.2f} WR={s["win_rate"]:.0f}% n={s["trades"]}")
            else:
                lines += [
                    "   <b>No trade history yet</b>",
                    "   Equal weighting active (1/63 per strategy)",
                    "   Weights adapt after first 5 trades per strategy",
                    "",
                    "   📊 Once trades complete, system auto-ranks:",
                    "   • Strategies with highest EWMA Sharpe get more capital",
                    "   • Regime multiplier: Momentum×1.5 in TRENDING",
                    "   • Mean-reversion×1.5 in RANGING",
                    "   • All×0.5 in HIGH_NOISE (risk protection)",
                ]
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Meta-learner: {e}"


    def _cmd_fii(self, _="") -> str:
        try:
            from fii_data_fetcher import fii_dii_telegram_report
            return fii_dii_telegram_report()
        except Exception as e:
            return f"❌ FII: {e}"

    def _cmd_fii_OLD(self, _="") -> str:
        try:
            from fii_tracker import analyse_fii_patterns
            from participant_oi import get_participant_data
            from datetime import datetime as _dt
            fp = analyse_fii_patterns()
            lines = [
                f"📊 <b>FII/DII ANALYSIS</b> | {_dt.now().strftime('%H:%M')}",
                "",
                f"   FII 5-day net:  ₹{fp.get('fii_5d',0):>8,.0f} Cr",
                f"   FII sentiment:  {fp.get('sentiment','NEUTRAL')}",
                f"   Score boost:    {fp.get('score',0):+.1f}",
                "",
            ]
            try:
                pd = get_participant_data() or {}
                fii = pd.get("FII",{})
                dii = pd.get("DII",{})
                lines += [
                    f"   FII cash net:   ₹{float(fii.get('net_cash',0) or 0):>8,.0f} Cr",
                    f"   DII cash net:   ₹{float(dii.get('net_cash',0) or 0):>8,.0f} Cr",
                    f"   FII fut net:    ₹{float(fii.get('net_futures',0) or 0):>8,.0f} Cr",
                ]
            except Exception: pass
            return "\n".join(lines)
        except Exception as e:
            return f"❌ FII data: {e}"

    def _cmd_backup(self, _="") -> str:
        try:
            import datetime as _dt
            results = []
            # GitHub data backup
            try:
                from github_backup import run_github_backup
                r = run_github_backup(force=True)
                if r.get("ok"):
                    results.append(f"✅ GitHub: {len(r.get('pushed',[]))} files → {r.get('repo','')}")
                elif "not set" in r.get("error",""):
                    results.append("⚠️ GitHub: not configured (add GITHUB_BACKUP_TOKEN + GITHUB_BACKUP_REPO to .env)")
                else:
                    results.append(f"⚠️ GitHub: {str(r.get('error','failed'))[:50]}")
            except Exception as e:
                results.append(f"⚠️ GitHub: {str(e)[:40]}")
            # Google Drive backup
            try:
                from cloud_backup import get_backup
                br = get_backup().run_backup(force=True)
                status = br.get("status", "unknown")
                results.append(f"{'✅' if status == 'ok' else '⚠️'} GDrive: {status}")
            except Exception as e:
                results.append(f"⚠️ GDrive: {str(e)[:40]}")
            ts = _dt.datetime.now().strftime("%d-%b %H:%M")
            return f"☁️ <b>BACKUP</b> | {ts}\n" + "\n".join(f"   {r}" for r in results)
        except Exception as e:
            return f"❌ Backup: {e}"

    def _cmd_github(self, _="") -> str:
        try:
            from github_sync import push_to_github
            result = push_to_github()
            if result and result.get("ok"):
                return (
                    "✅ <b>GitHub push successful</b>\n"
                    f"   Files: {len(result.get('files', []) or [])}\n"
                    "   Code backed up to sridharthetrainer/trading_robot"
                )
            msg = str((result or {}).get("message") or (result or {}).get("error") or "")
            if "Nothing to commit" in msg:
                return "⚠️ GitHub: No changes to push (already up to date)"
            return f"❌ GitHub: {msg[:120] or 'push failed'}"
        except Exception as e:
            return f"❌ GitHub: {e}"

    def _cmd_manual_buy(self, args="") -> str:
        return f"ℹ️ Manual buy: use /signals to see live signals\nBot will auto-execute when conditions are met"

    def _cmd_manual_sell(self, args="") -> str:
        return f"ℹ️ Use /kill to exit all positions immediately\nOr /pause to stop new entries"

    def _cmd_exit_all(self, args="") -> str:
        try:
            return self._cmd_kill(args)
        except Exception as e:
            return f"❌ Exit: {e}"

    def _cmd_update(self, _="") -> str:
        return ("ℹ️ <b>UPDATE OPTIONS</b>\n"
                "   /deploy  — pull from Google Drive + restart\n"
                "   /restart — restart bot (same code)\n"
                "   /backup  — push current code to Drive+GitHub")

    def _cmd_version(self, _="") -> str:
        import os
        from datetime import datetime as _dt
        py_count = len([f for f in os.listdir(".") if f.endswith(".py")])
        return (f"ℹ️ <b>SYSTEM VERSION</b>\n"
                f"   Python files:  {py_count}\n"
                f"   Strategies:    63\n"
                f"   Symbols:       196\n"
                f"   WOW factors:   7\n"
                f"   Build date:    Apr 2026")

    def _cmd_debug(self, _="") -> str:
        try:
            import psutil, os
            proc = psutil.Process(os.getpid())
            cpu  = proc.cpu_percent(interval=1)
            mem  = proc.memory_info().rss / 1024**2
            tg = self.health()
            poll_age = tg.get("last_poll_ok_age_sec")
            poll_text = f"{poll_age:.0f}s" if isinstance(poll_age, (int, float)) else "never"
            return (f"🔧 <b>DEBUG INFO</b>\n"
                    f"   CPU:    {cpu:.1f}%\n"
                    f"   Memory: {mem:.0f} MB\n"
                    f"   PID:    {os.getpid()}\n"
                    f"   TG:     running={tg.get('running')} "
                    f"alive={tg.get('thread_alive')} "
                    f"failures={tg.get('poll_failures')} "
                    f"last_ok={poll_text}")
        except Exception as e:
            return f"❌ Debug: {e}"

    def _cmd_regime(self, _="") -> str:
        """Market regime classification."""
        try:
            from market_regime import get_regime_engine
            return get_regime_engine().telegram_summary()
        except Exception as e:
            return f"⚠️ Regime: {e}"

    def _cmd_heat(self, _="") -> str:
        """Portfolio heat / correlation exposure."""
        try:
            from portfolio_heat import PortfolioHeatMonitor
            import config as cfg
            bot = self.bot_ref
            # Get positions with fallback
            try:
                positions = list(bot.live_engine.trade_manager.open_trades.values())
            except Exception:
                positions = []
            if not positions:
                from datetime import datetime as _dt2
                return (
                    f"🔥 <b>PORTFOLIO HEAT</b> | {_dt2.now().strftime('%H:%M')}\n"
                    f"   Open positions: 0\n"
                    f"   Portfolio exposure: ₹0\n"
                    f"   Correlation risk: None\n"
                    f"\n"
                    f"   ✅ No heat — no open trades\n"
                    f"   Heat shows when positions are open:\n"
                    f"   • Correlation between open trades\n"
                    f"   • Max correlated exposure (>0.8 = alert)\n"
                    f"   • Suggested new position size adjustment\n"
                )
            _fake_open = list(bot.live_engine.trade_manager.open_trades.values()) if bot else []
            pos_list  = [p.to_dict() if hasattr(p,"to_dict") else p for p in positions]
            capital   = float(getattr(cfg,"CAPITAL",100_000))
            mon = PortfolioHeatMonitor(capital=capital)
            return mon.telegram_summary(pos_list)
        except Exception as e:
            return f"⚠️ Heat: {e}"

    def _cmd_risk(self, _="") -> str:
        """VaR report — Value at Risk."""
        try:
            from value_at_risk import get_var_engine
            bot = self.bot_ref
            capital = 100_000
            if bot:
                try:
                    capital = float(getattr(__import__("config"), "CAPITAL", 100_000))
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            eng = get_var_engine(capital)
            return eng.telegram_summary()
        except Exception as e:
            return f"⚠️ VaR unavailable: {e}\nNeed 5+ days of trade history."


    def _cmd_connections(self, _="") -> str:
        """Run full connection check and send to Telegram."""
        try:
            from connection_monitor import get_monitor
            mon = get_monitor()
            if self.bot_ref and hasattr(self.bot_ref, "alerts"):
                mon.alerts = self.bot_ref.alerts
            import threading
            threading.Thread(
                target=mon.run_full_check, args=("ON-DEMAND",), daemon=True
            ).start()
            return "Running full connection check... results in ~20 seconds."
        except Exception as e:
            return f"Check error: {e}"

    def _cmd_next(self, _="") -> str:
        now = datetime.now()
        h   = now.hour
        lines = [f"📅 <b>UPCOMING TASKS</b>  {now.strftime('%H:%M')}"]
        schedule = [
            (8, 28,  "Pre-market intelligence brief"),
            (8, 30,  "Pivot levels + trading plan"),
            (8, 55,  "Balance check → paper/live decision"),
            (9, 10,  "Daily trading plan sent"),
            (9, 15,  "Market open — scanning begins"),
            (15, 25, "EOD forced squareoff"),
            (15, 35, "Daily P&L journal report"),
            (15, 15, "Google Drive backup"),
            (16, 30, "Nightly backtest (all 199 symbols)"),
            (17, 30, "Backtest report on Telegram"),
            (18, 0,  "AI model retraining"),
            (20, 0,  "Participant OI refresh"),
            (23, 0,  "Signal log TB labelling"),
        ]
        for task_h, task_m, task_desc in schedule:
            task_mins  = task_h * 60 + task_m
            now_mins   = h * 60 + now.minute
            remaining  = task_mins - now_mins
            if remaining > 0:
                lines.append(f"  ⏰ {task_h:02d}:{task_m:02d} (+{remaining}m) — {task_desc}")
        if len(lines) == 1:
            lines.append("  All tasks complete for today. Next cycle tomorrow 8:28 AM.")
        return "\n".join(lines[:10])
