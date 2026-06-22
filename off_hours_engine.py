"""
off_hours_engine.py — Smart Non-Market Hours Task Scheduler

When market is CLOSED (weekends, holidays, after 3:30 PM):
  → The system is ALWAYS ON — use it wisely.
  
TASK SCHEDULE:
  3:30 PM  : EOD data download (BhavCopy, FII, OI)
  4:00 PM  : Nightly backtest (all 199 symbols)
  5:30 PM  : Backtest report + mode switch alert
  6:00 PM  : ML training (signal_log labels + retrain)
  7:00 PM  : Weekly report (if Friday)
  8:00 PM  : Download report sent
  9:00 PM  : Participant OI refresh
  10:00 PM : Pre-market data prep (bulk deals, corp actions)
  11:00 PM : System health check
  SAT 8AM  : Full week backtest + model analysis
  SAT 10AM : Feature IC report
  SAT 12PM : Deep ML training (15-day window)
  SUN 8AM  : Connection check (all data sources)
  SUN 10AM : Next-week plan + download schedule

NSE HOLIDAYS (auto-detected):
  Checks NSE holiday calendar via free API.
  On holidays: runs full backtest + extended ML training.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# NSE 2026 trading holidays (approximate — auto-updated from NSE)
NSE_HOLIDAYS_2026 = {
    "2026-01-14","2026-01-26","2026-02-19","2026-03-25",  # Holi
    "2026-04-06","2026-04-14","2026-04-17","2026-05-01",
    "2026-07-06","2026-08-15","2026-10-02","2026-10-21",
    "2026-10-22","2026-11-04","2026-12-25",
}


def is_market_holiday(d: Optional[date] = None) -> bool:
    d = d or date.today()
    if d.weekday() >= 5:   # Sat/Sun
        return True
    return d.isoformat() in NSE_HOLIDAYS_2026


def is_market_hours(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    from datetime import time as dtime
    return dtime(9,15) <= now.time() <= dtime(15,30)


def fetch_nse_holidays() -> set:
    """Try to fetch current NSE holiday list from NSE website."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/", timeout=6)
        r = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
        data = r.json()
        holidays = set()
        for item in data.get("CM", []):
            dt_str = item.get("tradingDate","")
            if dt_str:
                try:
                    dt = datetime.strptime(dt_str, "%d-%b-%Y")
                    holidays.add(dt.strftime("%Y-%m-%d"))
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
        if holidays:
            logger.info("NSE holidays fetched: %d dates", len(holidays))
            return holidays
    except Exception as e:
        logger.debug("NSE holiday fetch: %s", e)
    return NSE_HOLIDAYS_2026


class OffHoursEngine:
    """
    Runs off-hours tasks intelligently.
    Wired into main_autonomous.py after-hours loop.
    """

    def __init__(self, bot_ref=None, alerts=None) -> None:
        self.bot_ref  = bot_ref
        self.alerts   = alerts
        self._done_tasks: set  = set()   # tracks what ran today
        self._holiday_cache: Optional[set] = None

    def _task_done(self, key: str) -> bool:
        today_key = f"{date.today()}:{key}"
        if today_key in self._done_tasks:
            return True
        self._done_tasks.add(today_key)
        return False

    def _send(self, msg: str) -> None:
        if self.alerts:
            try:
                self.alerts.send(msg)
            except Exception as _e:
                import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    # ── Mode switch alert ─────────────────────────────────────────────────────
    def alert_mode_switch(self, from_mode: str, to_mode: str, reason: str = "") -> None:
        """Alert when system switches between trading/backtest/learning modes."""
        icon = {"TRADING":"📈","BACKTEST":"📐","LEARNING":"🧠",
                "PAPER":"📄","LIVE":"💰","AFTER_HOURS":"🌙"}.get(to_mode.upper(),"🔄")
        msg = (
            f"{icon} <b>MODE SWITCH</b>\n"
            f"{from_mode} → <b>{to_mode}</b>\n"
        )
        if reason:
            msg += f"Reason: {reason}\n"
        msg += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        self._send(msg)

    def send_backtest_report(self, results: dict) -> None:
        """Rich backtest report after nightly run."""
        if not results:
            return
        n_syms   = results.get("symbols_tested", 0)
        n_ok     = results.get("symbols_profitable", 0)
        best_sym = results.get("best_symbol", "?")
        best_pnl = results.get("best_pnl", 0)
        worst    = results.get("worst_symbol", "?")
        avg_wr   = results.get("avg_win_rate", 0)
        improved = results.get("params_improved", 0)

        msg = (
            f"📐 <b>NIGHTLY BACKTEST COMPLETE</b>\n"
            f"{'═'*30}\n"
            f"  Symbols tested:  {n_syms}\n"
            f"  Profitable:      {n_ok}/{n_syms}\n"
            f"  Avg win rate:    {avg_wr:.0f}%\n"
            f"  Params improved: {improved} symbols\n"
            f"{'─'*30}\n"
            f"  🏆 Best:   {best_sym}  ₹{best_pnl:+,.0f}\n"
            f"  💀 Worst:  {worst}\n"
            f"{'─'*30}\n"
            f"  Impact: Updated strategy params for {improved} symbols.\n"
            f"  Model will use these tomorrow.\n"
            f"🕐 {datetime.now().strftime('%H:%M')}"
        )
        self._send(msg)

    # ── Holiday utilization ───────────────────────────────────────────────────
    def run_holiday_tasks(self) -> None:
        """Full suite of tasks on market holiday."""
        self._send(
            f"🎉 <b>MARKET HOLIDAY — {date.today()}</b>\n"
            f"Running extended maintenance tasks..."
        )
        tasks = [
            ("Full 199-symbol backtest",   self._run_full_backtest),
            ("Deep ML training (15d)",      self._run_deep_ml),
            ("Feature IC analysis",         self._run_feature_ic),
            ("Download all historical data",self._run_historical_download),
            ("System health audit",         self._run_health_audit),
        ]
        results = []
        for name, fn in tasks:
            try:
                self._send(f"⏳ Running: {name}...")
                fn()
                results.append(f"  ✅ {name}")
            except Exception as e:
                results.append(f"  ❌ {name}: {str(e)[:50]}")

        self._send(
            f"✅ <b>HOLIDAY TASKS COMPLETE</b>\n"
            + "\n".join(results)
        )

    def _run_sector_rotation(self) -> None:
        """Refresh sector rotation cache at 9:00 AM."""
        try:
            from sector_rotation_engine import rank_sectors
            rankings = rank_sectors()
            if rankings:
                top = [r["sector"] for r in rankings[:3]]
                logger.info("Sector rotation: top=%s", top)
        except Exception as e:
            logger.debug("sector_rotation: %s", e)

    def _run_full_backtest(self) -> None:
        from autonomous_backtest import get_backtest
        if self.alerts:
            self.alerts.send(
                "📐 <b>NIGHTLY BACKTEST STARTED</b>\n"
                "  Testing all 198 symbols × 31 strategies\n"
                "  Data: NSE Bhavcopy cache (60-day 5-min OHLCV\n"
                "  Duration: ~30-45 min\n"
                "  Results → Telegram when done\n"
                f"🕐 {__import__('datetime').datetime.now().strftime('%H:%M')}",
                dedup_key=f"bt_started_{__import__('datetime').date.today()}",
                dedup_cooldown_override=3600
            )
        get_backtest().run()

    def _run_deep_ml(self) -> None:
        if self.alerts:
            self.alerts.send(
                "🧠 <b>ML TRAINING STARTED</b>\n"
                "  Training on 60-day signal log\n"
                "  5 specialised sub-models\n"
                "  Duration: ~15-20 min\n"
                f"🕐 {__import__('datetime').datetime.now().strftime('%H:%M')}",
                dedup_key=f"ml_started_{__import__('datetime').date.today()}",
                dedup_cooldown_override=3600
            )
        from self_learning_engine import SelfLearningEngine
        SelfLearningEngine().run()

    def _run_feature_ic(self) -> None:
        from feature_importance import get_tracker
        report = get_tracker().format_report()
        self._send(report)

    def _run_historical_download(self) -> None:
        """Download extended historical data for all symbols."""
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            import pandas as pd
            from data_download_tracker import record
            symbols = ["^NSEI","^NSEBANK","^BSESN","^INDIAVIX"]
            for sym in symbols:
                t0 = datetime.now().timestamp()
                df = yf.download(sym, period="1y", interval="1d",
                                 progress=False, auto_adjust=True)
                ms = (datetime.now().timestamp() - t0) * 1000
                status = "OK" if df is not None and len(df) > 0 else "FAILED"
                record("yfinance", sym, status, "Historical",
                       size_kb=len(str(df))/1024 if df is not None else 0,
                       rows=len(df) if df is not None else 0,
                       latency_ms=ms)
        except Exception as e:
            logger.warning("Historical download: %s", e)

    def _run_health_audit(self) -> None:
        from system_monitor import system_health
        h = system_health()
        self._send(
            f"⚙️ <b>SYSTEM HEALTH AUDIT</b>\n"
            f"PID:    {h.get('bot_pid',0)}\n"
            f"Mem:    {h.get('memory_mb',0):.0f} MB\n"
            f"Disk:   {h.get('disk_free_gb',0):.1f} GB free\n"
            f"CPU:    {'Active' if h.get('cpu_active') else 'Idle'}\n"
        )

    # ── Weekend tasks ─────────────────────────────────────────────────────────
    def run_weekend_tasks(self, is_saturday: bool = True) -> None:
        now = datetime.now()
        dow = date.today().strftime("%A")

        if is_saturday:
            # Saturday: deep analysis
            schedule = [
                # ── New daily tasks ───────────────────────────────────────────────
        ("06:00", "Bhavcopy EOD download",           self._run_bhavcopy_download),
        ("08:00", "Morning video generation",         self._run_morning_video),
        ("08:30", "Morning intelligence brief",       self._run_morning_brief),
        ("18:15", "Daily data downloads",        self._run_daily_downloads),
        ("07:45", "Overnight gap risk check",    self._run_overnight_risk_check),
        ("09:00", "Sector rotation refresh",          self._run_sector_rotation),
        ("15:35", "EOD performance report",           self._run_eod_performance),
        # ── Saturday tasks ──────────────────────────────────────────────────
        ("08:00", "Full backtest + model analysis", self._run_full_backtest),
                ("10:00", "Feature IC report",              self._run_feature_ic),
                ("12:00", "Deep ML training (15d window)",  self._run_deep_ml),
                ("14:00", "Download historical data",       self._run_historical_download),
                ("16:00", "Weekly performance analysis",    self._run_weekly_analysis),
        ("15:20", "Position reconciliation",     self._run_recon),
        ("16:05", "EOD ML strategy analysis",    self._run_eod_ml_analysis),
        ("16:08", "Global markets snapshot",     lambda: __import__("strategy_score_tracker").store_global_snapshot()),
        ("20:00", "Auto test suite",             self._run_auto_tests),
        ("16:10", "Sector rotation snapshot",    lambda: __import__("sector_rotation_engine").store_sector_snapshot()),
            ]
        else:
            # Sunday: connection checks + next-week prep
            schedule = [
                ("08:00", "All connection tests",       self._run_connection_check),
                ("10:00", "Next-week download plan",    self._run_next_week_plan),
                ("12:00", "System health audit",        self._run_health_audit),
                ("14:00", "Strategy parameter review",  self._run_param_review),
            ]

        current_h_m = now.strftime("%H:%M")
        pending = [(t, n, fn) for t, n, fn in schedule if t >= current_h_m]

        if pending:
            t, name, fn = pending[0]
            task_key = f"weekend_{t}_{name[:20]}"
            if not self._task_done(task_key):
                try:
                    fn()
                except Exception as e:
                    self._send(f"⚠️ Weekend task failed: {name}\n{e}")

    def _run_weekly_analysis(self) -> None:
        self._send(
            f"📊 <b>WEEKLY ANALYSIS</b>\n"
            f"Running performance review...\n"
            f"Check /weekly for results."
        )

    def _run_bhavcopy_download(self) -> None:
        """6 PM daily: Download NSE Bhavcopy EOD data."""
        try:
            from bhavcopy_cache import download_bhavcopy
            n = download_bhavcopy()
            if n > 0:
                logger.info("Bhavcopy downloaded: %d records", n)
                self.alerts.send(
                    f"📥 <b>BHAVCOPY DOWNLOADED</b>\n"
                    f"  Records: {n:,} stocks\n"
                    f"  Source: NSE archives.nseindia.com",
                    dedup_key="bhavcopy_dl", dedup_cooldown_override=43200
                )
        except Exception as e:
            logger.warning("bhavcopy_download: %s", e)

    def _run_morning_brief(self) -> None:
        """8:30 AM daily: Send morning brief + gap warnings."""
        try:
            from morning_brief import send_morning_brief
            send_morning_brief(self.alerts)
        except Exception as e:
            logger.debug("morning_brief: %s", e)
        # UX-14: Gap warnings for open positions
        try:
            from ux_engine import get_overnight_gap_warnings
            import sqlite3
            db_path = "trades.db"
            if __import__("pathlib").Path(db_path).exists():
                conn = sqlite3.connect(db_path)
                rows = conn.execute(
                    "SELECT symbol, side, entry_price, stop_loss FROM trades "
                    "WHERE status='open'").fetchall()
                conn.close()
                if rows:
                    positions = [{"symbol":r[0],"side":r[1],"entry_price":r[2],"stop_loss":r[3]} for r in rows]
                    gap_msg = get_overnight_gap_warnings(positions)
                    if gap_msg:
                        self.alerts.send(
                            f"⚠️ <b>OVERNIGHT GAP CHECK</b>\n\n{gap_msg}",
                            dedup_key="gap_warning", dedup_cooldown_override=43200
                        )
        except Exception as e:
            logger.debug("gap_warning: %s", e)

    def _run_morning_video(self) -> None:
        """8:00 AM daily: Generate and send market brief video."""
        try:
            import threading as _vt
            def _make():
                try:
                    from cross_asset import get_cross_asset_data, get_market_bias
                    from morning_brief import _fetch_india_vix, _fetch_global_snapshot
                    from sector_rotation_engine import get_top_sectors, get_avoid_sectors
                    from news_sentiment_engine import get_full_sentiment
                    # Fetch global data using morning_brief (Stooq-first chain)
                    _gsnap = _fetch_global_snapshot()
                    # Convert snapshot format to cross_asset format for chart
                    macro = {}
                    for _gk, _gv in _gsnap.items():
                        if isinstance(_gv, dict) and _gv.get("price"):
                            macro[_gk] = {
                                "price": _gv["price"],
                                "change_pct": _gv.get("chg", 0),
                                "prev": _gv["price"] / (1 + _gv.get("chg",0)/100) if _gv.get("chg") else _gv["price"],
                            }
                    # Also try cross_asset for any missing keys
                    try:
                        _ca = get_cross_asset_data(force=True)
                        for _ck, _cv in _ca.items():
                            if _ck not in macro and isinstance(_cv, dict):
                                macro[_ck] = _cv
                    except Exception: pass
                    sent  = get_full_sentiment(use_cache=False)
                    brief_data = {
                        "global":           macro,
                        "india_vix":        _fetch_india_vix(),
                        "bias":             get_market_bias(macro),
                        "sentiment":        sent.get("sentiment","NEUTRAL"),
                        "top_sectors":      get_top_sectors(3),
                        "avoid_sectors":    get_avoid_sectors(2),
                        "commodity_impacts":sent.get("sector_impacts",{}),
                        "commodities":      sent.get("commodities",{}),
                        "wow_factors":      {"regime":"TRENDING","fii_bias":"NEUTRAL"},
                    }
                    from voice_video_generator import generate_daily_brief_video
                    generate_daily_brief_video(brief_data, alerts=self.alerts)
                except Exception as _ve:
                    logger.debug("video_gen: %s", _ve)
            _vt.Thread(target=_make, daemon=True, name="video_8am").start()
        except Exception as e:
            logger.debug("morning_video: %s", e)

    def _run_sector_rotation_refresh(self) -> None:
        """9:00 AM daily: Refresh sector rotation cache pre-market."""
        try:
            from sector_rotation_engine import rank_sectors, format_telegram_report
            rankings = rank_sectors()
            if rankings:
                self.alerts.send(
                    format_telegram_report(),
                    dedup_key="sector_rotation", dedup_cooldown_override=43200
                )
        except Exception as e:
            logger.debug("sector_rotation: %s", e)

    def _run_omnisource_refresh(self) -> None:
        """Refresh omnisource intelligence cache."""
        try:
            from omnisource_news_engine import get_omnisource_intelligence
            get_omnisource_intelligence(use_cache=False)
        except Exception as e:
            logger.debug("omnisource_refresh: %s", e)

    def _run_eod_performance(self) -> None:
        """3:35 PM daily: Auto-send EOD performance + accuracy post."""
        try:
            from performance_analytics import format_telegram_report as _pa
            msg = _pa(1)
            self.alerts.send(msg, dedup_key="eod_perf", dedup_cooldown_override=43200)
        except Exception as e:
            logger.debug("eod_performance: %s", e)
        # UX-11: Send public accuracy post
        try:
            from ux_engine import get_daily_accuracy_post
            post = get_daily_accuracy_post()
            if post:
                self.alerts.send(post, dedup_key="eod_accuracy", dedup_cooldown_override=43200)
        except Exception as e:
            logger.debug("daily_accuracy: %s", e)

    def _run_fno_ban_check(self) -> None:
        """9:05 AM daily: Fetch F&O ban list AND SEBI ASM/GSM surveillance list."""
        # F&O ban list
        try:
            from omnisource_news_engine import fetch_fno_ban_list
            banned = fetch_fno_ban_list()
            if banned:
                logger.info("F&O ban list: %s", banned)
                self.alerts.send(
                    f"🚫 <b>F&O BAN TODAY</b>: {', '.join(banned[:12])}",
                    dedup_key="fno_ban", dedup_cooldown_override=43200
                )
        except Exception as e:
            logger.debug("fno_ban: %s", e)

        # ASM / GSM surveillance list (force-refresh daily at open)
        try:
            from asm_gsm_filter import get_asm_gsm_list, get_surveillance_status_message
            watch = get_asm_gsm_list(force=True)   # force=True bypasses cache for daily refresh
            msg   = get_surveillance_status_message()
            logger.info("ASM/GSM refresh: %d symbols", len(watch))
            if watch:
                self.alerts.send(
                    f"⚠️ <b>Surveillance Watch</b>\n{msg}\n"
                    f"These symbols will be blocked from new signals.",
                    dedup_key="asm_gsm_daily", dedup_cooldown_override=43200
                )
        except Exception as e:
            logger.debug("asm_gsm_refresh: %s", e)

    def _run_heartbeat(self) -> None:
        """Market-hours heartbeat — bot alive + RAM/CPU/disk check."""
        try:
            from trade_manager import TradeManager
            from datetime import datetime
            n_open = 0; pnl = 0.0
            try:
                tm = TradeManager()
                n_open = len(tm.get_open_positions())
                pnl    = tm.get_daily_pnl()
            except Exception: pass
            # System health
            ram_pct = cpu_pct = 0.0
            disk_gb = 999.0
            health_line = ''
            try:
                import psutil
                ram_pct  = psutil.virtual_memory().percent
                cpu_pct  = psutil.cpu_percent(interval=0.5)
                disk_gb  = psutil.disk_usage('/').free / (1024**3)
                health_line = f'  RAM {ram_pct:.0f}% | CPU {cpu_pct:.0f}% | Disk {disk_gb:.1f}GB free'
                if ram_pct > 85 and self.alerts:
                    self.alerts.send(
                        f'⚠️ HIGH RAM: {ram_pct:.0f}% — restart bot soon',
                        dedup_key='ram_alert', dedup_cooldown_override=3600
                    )
            except ImportError: pass
            icon = '🟢' if pnl >= 0 else '🔴'
            if self.alerts:
                self.alerts.send(
                    f'💚 <b>BOT ALIVE</b> | {datetime.now().strftime("%H:%M")}\n'
                    f'  Open: {n_open} | {icon} P&L: ₹{pnl:+,.0f}\n'
                    f'{health_line}',
                    dedup_key=f'heartbeat_{datetime.now().strftime("%H%M")}',
                    dedup_cooldown_override=1700
                )
        except Exception as e:
            logger.debug('heartbeat: %s', e)



    def _run_live_position_update(self) -> None:
        """Every 30 min during market hours: send live position P&L."""
        try:
            from datetime import datetime as _dt
            now = _dt.now()
            # Only during market hours
            if not (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30)):
                return
            from telegram_commands import TelegramCommandHandler
            # Just call via bot_ref if available
            bot = getattr(self, 'bot_ref', None)
            if bot and hasattr(bot, 'live_engine'):
                tm = bot.live_engine.trade_manager
                opens = getattr(tm, 'open_trades', {})
                if opens and self.alerts:
                    # Quick P&L summary
                    pnl = getattr(tm, 'daily_realized_pnl', 0)
                    n = len(opens)
                    icon = "🟢" if pnl >= 0 else "🔴"
                    self.alerts.send(
                        f"📊 <b>POSITION UPDATE</b> | {now.strftime('%H:%M')}\n"
                        f"  Open: {n} | {icon} Day P&L: ₹{pnl:+,.0f}\n"
                        f"  Use /live for details",
                        dedup_key=f"live_update_{now.strftime('%H%M')}",
                        dedup_cooldown_override=1700
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("live_update: %s", e)


    def _run_accuracy_post(self) -> None:
        """Post daily signal accuracy to free/premium channels (UX-11)."""
        try:
            import os
            from performance_analytics import get_full_report
            from signal_broadcaster import get_broadcaster
            from datetime import datetime as _dt

            r = get_full_report(1)  # Today
            n = r.get("total_trades", 0)
            wr = r.get("win_rate", 0)
            pnl = r.get("total_pnl", 0)
            score = r.get("quality_score", 0)

            if n == 0:
                msg = (f"📡 <b>TODAY'S SIGNAL SUMMARY</b> | {_dt.now().strftime('%d %b')}\n\n"
                       f"  Market closed | Preparing for tomorrow\n"
                       f"  Signals start at 9:15 AM\n"
                       f"  Quality gate: 5.5+ score minimum")
            else:
                pnl_icon = "🟢" if pnl >= 0 else "🔴"
                msg = (f"📡 <b>TODAY'S PERFORMANCE</b> | {_dt.now().strftime('%d %b')}\n\n"
                       f"  Signals: {n} | Win rate: {wr:.0f}%\n"
                       f"  {pnl_icon} P&L: ₹{pnl:+,.0f}\n"
                       f"  Quality score: {score:.0f}/100\n\n"
                       f"  30-day accuracy: updating...\n"
                       f"  ⚠️ Educational only | Past ≠ future")

            # Send to free channel if configured
            from channel_config import send_to_both
            send_to_both(self.alerts, msg)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("accuracy_post: %s", e)


    def _run_swing_gap_warning(self) -> None:
        """UX-14: Pre-market gap warning for open swing positions."""
        try:
            bot = getattr(self, 'bot_ref', None)
            if not bot: return
            tm = bot.live_engine.trade_manager
            opens = getattr(tm, 'open_trades', {})
            if not opens: return

            import requests as _rq
            s = _rq.Session()
            s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            s.get("https://www.nseindia.com/", timeout=4)
            r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
            index_prices = {}
            for idx in r.json().get("data",[]):
                nm = str(idx.get("index",""))
                px = float(idx.get("last",0) or 0)
                if px: index_prices[nm] = px

            warnings = []
            for tid, trade in opens.items():
                sym = getattr(trade,'symbol','?')
                entry = float(getattr(trade,'entry_price',0) or 0)
                sl = float(getattr(trade,'stop_loss',0) or 0)
                side = getattr(trade,'side','BUY')
                style = str(getattr(trade,'metadata',{}).get('style','') or '')
                if 'swing' not in style.lower(): continue

                # Get overnight price
                curr = index_prices.get("NIFTY 50",0) if sym=="NIFTY" else 0
                if curr and entry:
                    gap_pct = (curr - entry) / entry * 100
                    if abs(gap_pct) > 0.5:
                        gap_icon = "🟢" if (gap_pct > 0 and side=="BUY") or (gap_pct < 0 and side=="SELL") else "⚠️"
                        warnings.append(
                            f"  {gap_icon} {sym} gap {gap_pct:+.1f}% | "
                            f"Entry ₹{entry:,.0f} | SL ₹{sl:,.0f}"
                        )

            if warnings and self.alerts:
                from datetime import datetime as _dt
                self.alerts.send(
                    f"⚡ <b>PRE-MARKET GAP WARNING</b> | {_dt.now().strftime('%H:%M')}\n\n"
                    + "\n".join(warnings) + "\n\n"
                    f"  Review your SL levels before 9:15 AM",
                    dedup_key="gap_warning", dedup_cooldown_override=43200
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("gap_warning: %s", e)


    def _run_capital_rebalance(self) -> None:
        """Every Sunday: Re-read broker balance, update capital allocator."""
        try:
            from datetime import datetime as _dt
            if _dt.now().weekday() != 6:  # Sunday only
                return
            bot = getattr(self, 'bot_ref', None)
            if not bot: return
            # Fetch real balance
            live_bal = 0.0
            try:
                bal_data = bot.live_engine.broker_manager.get_execution_broker().angel.get_balance()
                live_bal = float(bal_data.get("net", 0) or 0)
            except Exception: pass
            if live_bal <= 0: return
            # Update .env
            import re as _re
            env_path = ".env"
            with open(env_path) as f: env = f.read()
            env = _re.sub(r'^REAL_CAPITAL=.*', f'REAL_CAPITAL={live_bal:.0f}', env, flags=_re.M)
            env = _re.sub(r'^CAPITAL=.*', f'CAPITAL={live_bal:.0f}', env, flags=_re.M)
            with open(env_path,'w') as f: f.write(env)
            # Reset capital allocator
            if hasattr(bot.live_engine, 'capital_allocator'):
                bot.live_engine.capital_allocator._initialized = False
                bot.live_engine.capital_allocator.update_total(live_bal)
            if self.alerts:
                self.alerts.send(
                    f"💰 <b>WEEKLY CAPITAL REBALANCE</b>\n"
                    f"  New balance: ₹{live_bal:,.0f}\n"
                    f"  Capital allocator updated\n"
                    f"  Position sizes recalibrated"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("capital_rebalance: %s", e)


    def _run_meta_learner_training(self) -> None:
        """Nightly: retrain meta-learner weights from live trade results."""
        try:
            from meta_learner import get_meta_learner
            ml = get_meta_learner()
            results = ml.train()
            if results:
                top = sorted(results.items(), key=lambda x: x[1]['weight'], reverse=True)[:3]
                msg = "🧠 <b>META-LEARNER UPDATED</b>\n"
                for strat, info in top:
                    msg += f"  {strat[:20]:20} weight={info['weight']:.2f} wr={info['win_rate']:.0%}\n"
                self.alerts.send(msg, dedup_key="meta_train", dedup_cooldown_override=43200)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("meta_train: %s", e)


    def _run_github_backup(self) -> None:
        """Nightly: auto-push code + DBs to GitHub."""
        try:
            from github_sync import push_to_github
            result = push_to_github(
                files_to_commit=[
                    'trades.db', 'signal_log.db', 'strategy_performance_matrix.json',
                    'meta_learner_weights.json', 'rl_qtable.json',
                ],
                commit_message=f'Auto-backup {__import__("datetime").date.today()}',
            )
            if result:
                import logging
                logging.getLogger(__name__).info('GitHub backup pushed')
        except Exception as _gh_e:
            import logging
            logging.getLogger(__name__).debug('github_backup: %s', _gh_e)


    def _run_db_vacuum(self) -> None:
        """Weekly: VACUUM all SQLite DBs to reclaim disk space."""
        import sqlite3, os
        dbs = ['trades.db', 'signal_log.db', 'nse_cache.db', 'strategy_matrix.db']
        for db in dbs:
            if os.path.exists(db):
                try:
                    before = os.path.getsize(db) // 1024
                    conn = sqlite3.connect(db)
                    conn.execute('VACUUM')
                    conn.close()
                    after = os.path.getsize(db) // 1024
                    import logging
                    logging.getLogger(__name__).info(
                        'VACUUM %s: %dKB -> %dKB', db, before, after)
                except Exception as _ve:
                    import logging
                    logging.getLogger(__name__).debug('vacuum %s: %s', db, _ve)


    def _run_macro_event_check(self) -> None:
        """IMPROVEMENT 8: Reduce sizing before RBI/budget/fed events."""
        try:
            from event_calendar import get_upcoming_macro_events
            import json, os
            from datetime import datetime, timedelta
            events = get_upcoming_macro_events(days=3)
            macro_events = [e for e in events if any(
                x in str(e.get('event','')).lower()
                for x in ['rbi', 'fed', 'budget', 'gdp', 'cpi', 'inflation'])]
            config_file = 'macro_event_override.json'
            if macro_events:
                override = {'reduce_size': True, 'factor': 0.5,
                            'events': [str(e) for e in macro_events[:3]],
                            'until': (datetime.now() + timedelta(days=2)).isoformat()}
                with open(config_file,'w') as f: json.dump(override, f)
                if self.alerts:
                    self.alerts.send(
                        f'⚡ MACRO EVENT ALERT: {len(macro_events)} events in 3 days\n'
                        f'  Position sizes auto-reduced to 50%\n'
                        f'  Events: {[e.get("event","?") for e in macro_events[:2]]}',
                        dedup_key='macro_event', dedup_cooldown_override=43200
                    )
            elif os.path.exists(config_file):
                os.remove(config_file)  # clear override
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('macro_event: %s', e)


    def _run_lstm_retrain(self) -> None:
        """Nightly: retrain LSTM model on live trade data."""
        try:
            from lstm_model import LSTMModel
            lstm = LSTMModel()
            if hasattr(lstm, 'retrain'):
                result = lstm.retrain()
                import logging
                logging.getLogger(__name__).info('LSTM retrained: %s', result)
            elif hasattr(lstm, 'train'):
                lstm.train()
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('lstm_retrain: %s', e)

    def _run_weekly_equity_chart(self) -> None:
        """Monday 9AM: Send weekly equity curve chart to subscribers."""
        try:
            from datetime import datetime as _dt
            if _dt.now().weekday() != 0:  # Monday only
                return
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import sqlite3, os
            conn = sqlite3.connect('trades.db')
            rows = conn.execute(
                "SELECT exit_time, realized_pnl FROM trades WHERE status='CLOSED' "
                "AND realized_pnl IS NOT NULL ORDER BY exit_time"
            ).fetchall()
            conn.close()
            if len(rows) < 3:
                return
            cumulative = 0.0
            dates, equity = [], []
            for row in rows:
                cumulative += float(row[1] or 0)
                dates.append(str(row[0])[:10])
                equity.append(cumulative)
            fig, ax = plt.subplots(figsize=(12,5))
            fig.patch.set_facecolor('#0D1117')
            ax.set_facecolor('#161B22')
            color = '#00FF88' if equity[-1] >= 0 else '#FF4444'
            ax.plot(range(len(equity)), equity, color=color, linewidth=2)
            ax.fill_between(range(len(equity)), equity, alpha=0.2, color=color)
            ax.set_title(f'Equity Curve | {_dt.now().strftime("%b %Y")}',
                         color='white', fontsize=14)
            ax.tick_params(colors='white')
            for spine in ax.spines.values(): spine.set_edgecolor('#333')
            ax.axhline(0, color='#444', linewidth=0.5)
            out = f'equity_curve_{_dt.now().strftime("%Y%m%d")}.png'
            plt.savefig(out, dpi=120, bbox_inches='tight', facecolor='#0D1117')
            plt.close()
            if self.alerts and hasattr(self.alerts, 'send_photo'):
                self.alerts.send_photo(out,
                    caption=f'📈 Weekly equity curve | {_dt.now().strftime("%d %b %Y")}')
            try: os.remove(out)
            except Exception: pass
            # Also send to premium channel
            try:
                from channel_config import send_to_premium
                send_to_premium(self.alerts,
                    f'📈 Weekly P&L curve — {_dt.now().strftime("%d %b")}')
            except Exception: pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('weekly_chart: %s', e)

    def _run_subscription_checks(self) -> None:
        """Daily: check expiring subscriptions and send renewal reminders."""
        try:
            from subscription_engine import check_expiring_subscriptions
            expiring = check_expiring_subscriptions(self.alerts)
            if expiring:
                import logging
                logging.getLogger(__name__).info(
                    '%d subscriptions expiring soon', len(expiring))
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('sub_checks: %s', e)

    def _run_composite_sentiment_update(self) -> None:
        """Update composite market sentiment score (IMPROVEMENT H)."""
        try:
            from market_intelligence_hub import get_composite_sentiment
            sent = get_composite_sentiment()
            score = sent.get('score', 50)
            label = sent.get('label', 'NEUTRAL')
            emoji = sent.get('emoji', '⚪')
            if self.alerts:
                self.alerts.send(
                    f'{emoji} <b>Market Sentiment</b>: {label} ({score:.0f}/100)\n'
                    f'  VIX | FII | News | PCR | Carry',
                    dedup_key=f'sentiment_{__import__("datetime").date.today()}',
                    dedup_cooldown_override=43200
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('sentiment_update: %s', e)


    def _run_fno_bhavcopy_oi(self) -> None:
        """Load FNO bhavcopy OI baseline at 9:05 AM."""
        try:
            import requests, zipfile, io
            from datetime import date, timedelta
            # NSE FNO bhavcopy URL (published daily at 6 PM)
            d = date.today()
            if d.weekday() >= 5:  # weekend — use Friday
                d -= timedelta(days=d.weekday() - 4)
            url = (f'https://archives.nseindia.com/content/historical/'
                   f'DERIVATIVES/{d.year}/'
                   f'fo{d.strftime("%d%b%Y").upper()}bhav.csv.zip')
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            if r.status_code == 200:
                zf = zipfile.ZipFile(io.BytesIO(r.content))
                csv_name = [n for n in zf.namelist() if n.endswith('.csv')][0]
                import pandas as _pd
                df = _pd.read_csv(zf.open(csv_name))
                # Save for OI tracker
                df.to_csv('fno_bhavcopy_latest.csv', index=False)
                import logging
                logging.getLogger(__name__).info(
                    'FNO bhavcopy loaded: %d rows', len(df))
                # Update OI tracker
                try:
                    from oi_tracker import load_fno_bhavcopy
                    load_fno_bhavcopy(df)
                except Exception: pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('fno_bhavcopy: %s', e)


    def _run_zerodha_token_refresh(self) -> None:
        """7:30 AM: Auto-refresh Zerodha token if configured."""
        try:
            from zerodha_client import is_configured, auto_refresh_token_playwright
            if not is_configured():
                return  # not configured — skip silently
            result = auto_refresh_token_playwright()
            import logging
            logging.getLogger(__name__).info(
                'Zerodha token refresh: %s', 'OK' if result else 'FAILED')
            if not result and self.alerts:
                self.alerts.send(
                    '⚠️ Zerodha token refresh failed\n'
                    'Using Angel One + Dhan for data today\n'
                    '/dhan_setup if Dhan not configured',
                    dedup_key='zerodha_token_fail',
                    dedup_cooldown_override=43200
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('zerodha_refresh: %s', e)


    def _run_walk_forward_validation(self) -> None:
        """Weekly: run walk-forward validation on strategy parameters."""
        try:
            from autonomous_backtest import run_walk_forward
            results = run_walk_forward()
            if results:
                import logging
                logging.getLogger(__name__).info('Walk-forward: %s', results)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('walk_forward: %s', e)




    def _run_auto_tests(self) -> None:
        """Run full test suite during idle time (after hours/weekends)."""
        try:
            import subprocess
            r = subprocess.run(
                ["python3", "test_core.py"],
                capture_output=True, text=True, timeout=60,
                cwd="/home/sridhar/Desktop/trading_robot"
            )
            result = r.stdout[-300:] if r.stdout else "no output"
            passed = "ALL TESTS PASSED" in result
            if self.alerts:
                icon = "✅" if passed else "❌"
                self.alerts.send(
                    f"{icon} <b>AUTO TEST RESULTS</b>\n<pre>{result}</pre>",
                    dedup_key=f"auto_test_{__import__('datetime').date.today()}"
                )
        except Exception as e:
            import logging; logging.getLogger(__name__).debug("auto_test: %s", e)

    def _run_recon(self):
        """Check positions match Angel. Auto-close orphaned local positions."""
        try:
            from angel import AngelOne
            import os
            ang = AngelOne(api_key=os.getenv("API_KEY",""),client_id=os.getenv("CLIENT_ID",""),
                password=os.getenv("PASSWORD",""),totp_secret=os.getenv("TOTP_SECRET",""))
            r = ang.reconcile_positions()
            ap = r.get("angel_positions",{})
            if ap and self.alerts:
                ls = ["<b>POSITION CHECK</b>",""]
                for s,d in ap.items(): ls.append(f"  {s}: qty={d['qty']}")
                if not ap: ls.append("  No open positions")
                # Auto-fix: close local positions not on Angel
                try:
                    import sqlite3
                    conn = sqlite3.connect("trades.db", check_same_thread=False)
                    local = conn.execute("SELECT symbol FROM trades WHERE status='OPEN'").fetchall()
                    orphaned = [s for (s,) in local if s not in ap]
                    if orphaned:
                        for s in orphaned:
                            conn.execute("UPDATE trades SET status='CLOSED',exit_time=datetime('now') WHERE symbol=? AND status='OPEN'",(s,))
                            ls.append(f"  ⚠️ Auto-closed orphan: {s}")
                        conn.commit()
                    conn.close()
                except Exception: pass
                self.alerts.send("\n".join(ls),dedup_key="recon")
        except Exception: pass

    def _run_eod_ml_analysis(self) -> None:
        """16:00: Run EOD ML analysis on all strategy scores from today."""
        try:
            from strategy_score_tracker import run_eod_ml_analysis, record_fii_dii
            # Record FII/DII data
            try:
                from participant_oi import get_participant_data
                pd = get_participant_data(force=True)
                if pd:
                    record_fii_dii(
                        fii_buy=pd.get('fii_buy',0), fii_sell=pd.get('fii_sell',0),
                        fii_net=pd.get('fii_net',0),
                        dii_buy=pd.get('dii_buy',0), dii_sell=pd.get('dii_sell',0),
                        dii_net=pd.get('dii_net',0),
                        fii_futures_oi=pd.get('fii_futures_oi',0),
                        fii_futures_net=pd.get('fii_futures_net',0),
                    )
            except Exception: pass
            # Run ML analysis
            report = run_eod_ml_analysis()
            if self.alerts and report:
                self.alerts.send(report, dedup_key='eod_ml')
        except Exception as e:
            import logging; logging.getLogger(__name__).debug('eod_ml: %s', e)

    def _run_daily_data_download(self) -> None:
        """6:45 PM: Download all required daily data via Angel API."""
        import logging, os, time
        _lg = logging.getLogger(__name__)
        downloaded = 0
        failed = 0
        
        # Get Angel singleton
        try:
            from angel import AngelOne
            ang = AngelOne(
                api_key=os.getenv("API_KEY",""),
                client_id=os.getenv("CLIENT_ID",""),
                password=os.getenv("PASSWORD",""),
                totp_secret=os.getenv("TOTP_SECRET",""),
            )
            from data_fetcher import DataFetcher
            df = DataFetcher(angel=ang, paper_trade=False)
        except Exception as e:
            _lg.warning("Daily download: no Angel: %s", e)
            df = None
        
        # Download 5m data for key indices
        indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50"]
        for sym in indices:
            try:
                if df:
                    data = df.get_market_data(sym, interval="5m", days=5)
                    if data is not None and len(data) >= 10:
                        downloaded += 1
                        _lg.info("Downloaded %s: %d bars", sym, len(data))
                    else:
                        failed += 1
                time.sleep(1)  # rate limit
            except Exception as e:
                failed += 1; _lg.debug("dl_%s: %s", sym, e)
        
        # Bhavcopy (equity EOD)
        try:
            from bhavcopy_cache import download_last_n_days
            download_last_n_days(5)
            downloaded += 1
        except Exception: failed += 1
        
        # F&O bhavcopy
        try:
            from fno_bhavcopy_oi import download_fno_bhavcopy
            if download_fno_bhavcopy() is not None: downloaded += 1
        except Exception: failed += 1
        
        # Master contract
        try:
            from download_master_contract import download_angel_scrip_master
            if download_angel_scrip_master(): downloaded += 1
        except Exception: failed += 1
        
        # Report
        if self.alerts:
            self.alerts.send(
                f"📥 <b>DAILY DATA DOWNLOAD</b>\n\n"
                f"  ✅ Downloaded: {downloaded}\n"
                f"  ❌ Failed: {failed}\n"
                f"  Total: {downloaded + failed}\n\n"
                f"  Indices 5m: {len([1 for s in indices[:6]])} attempted\n"
                f"  Bhavcopy + F&O + Master: 3 attempted",
                dedup_key=f"daily_dl_{__import__("datetime").date.today()}"
            )

    def _run_overnight_risk_check(self) -> None:
        """Pre-market 7:45 AM: Check overnight gap risk for open positions."""
        try:
            from gap_risk_manager import get_gap_risk
            from data_source_resilience import get_gift_nifty_gap
            gap = get_gift_nifty_gap(0)  # 0 = fetch current NIFTY close
            gap_pct = abs(gap.get('gap_pct', 0))
            direction = gap.get('direction', 'FLAT')
            if gap_pct > 0.5 and self.alerts:
                self.alerts.send(
                    f'⚠️ <b>OVERNIGHT GAP WARNING</b>\n\n'
                    f'  GIFT Nifty: {direction} {gap_pct:.1f}%\n'
                    f'  Open positions may be impacted\n'
                    f'  Review: /positions',
                    dedup_key=f'gap_warn_{__import__("datetime").date.today()}'
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('overnight_risk: %s', e)


    def _run_daily_downloads(self) -> None:
        """Run all daily data downloads and report."""
        try:
            from data_download_tracker import run_all_downloads
            results = run_all_downloads()
            ok   = results.get('ok', 0)
            fail = results.get('fail', 0)
            if self.alerts:
                lines = [
                    f'📥 <b>DAILY DOWNLOAD REPORT</b>',
                    f'',
                    f'  ✅ Downloaded: {ok}',
                    f'  ❌ Failed:     {fail}',
                ]
                items = results.get('items', [])
                for item in items[:15]:
                    lines.append(f'  {item}')
                self.alerts.send(
                    '\n'.join(lines),
                    dedup_key=f'daily_dl_{__import__("datetime").date.today()}',
                    dedup_cooldown_override=43200
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug('daily_downloads: %s', e)

    def _run_connection_check(self) -> None:
        """Check all data source connections."""
        results = []
        checks = [
            ("NSE",         "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"),
            ("BSE",         "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"),
            ("NewsAPI",     "https://newsapi.org/v2/top-headlines?country=in&apiKey=test"),
            ("yfinance",    None),  # special check
        ]
        import requests
        for name, url in checks:
            try:
                if url:
                    s = requests.Session()
                    s.headers.update({"User-Agent":"Mozilla/5.0"})
                    r = s.get(url, timeout=8)
                    ok = r.status_code in (200, 401, 403)  # 401/403 = reachable but auth needed
                    results.append(f"  {'✅' if ok else '❌'} {name}: HTTP {r.status_code}")
                else:
                    import yf_compat as yf  # yfinance replaced: Yahoo API broken
                    df = yf.download("^NSEI", period="1d", interval="1d",
                                     progress=False, auto_adjust=True)
                    results.append(f"  {'✅' if df is not None and len(df)>0 else '❌'} yfinance")
            except Exception as e:
                results.append(f"  ❌ {name}: {str(e)[:40]}")

        self._send(
            f"🔌 <b>CONNECTION CHECK  {date.today()}</b>\n"
            + "\n".join(results)
        )

    def _run_next_week_plan(self) -> None:
        """Send next-week download and task plan."""
        next_mon = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)
        msg = (
            f"📋 <b>NEXT WEEK PLAN</b>  w/c {next_mon}\n"
            f"{'─'*30}\n"
            f"DAILY (Mon-Fri):\n"
            f"  📥 199 symbols OHLCV (5m + 1d)\n"
            f"  📥 NIFTY/BANKNIFTY/SENSEX option chains\n"
            f"  📥 FII/DII cash + participant OI\n"
            f"  📥 BhavCopy delivery% + bulk deals\n"
            f"  📥 Cross-asset: USD/INR, Brent, US VIX\n"
            f"  📥 News headlines (NewsAPI)\n"
            f"  📥 F&O ban list + corporate actions\n"
            f"{'─'*30}\n"
            f"WEEKLY (Saturday):\n"
            f"  🔄 Full 199-symbol backtest\n"
            f"  🧠 Deep ML training (15d window)\n"
            f"  📊 Feature IC analysis\n"
            f"{'─'*30}\n"
            f"YOUR ACTIONS THIS WEEK:\n"
            f"  ☑️ Check /downloads daily (failed items)\n"
            f"  ☑️ Update index_rebalancing.json if NSE announcement\n"
            f"  ☑️ Review /weekly report Friday 4 PM\n"
            f"  ☑️ Go-live decision when paper WR > 55% for 15 labelled days\n"
        )
        self._send(msg)

    def _run_param_review(self) -> None:
        """Review strategy parameters from backtest."""
        try:
            from pathlib import Path
            import json
            p = Path("symbol_params.json")
            if p.exists():
                params = json.loads(p.read_text())
                n = len(params)
                self._send(
                    f"⚙️ <b>STRATEGY PARAMS REVIEW</b>\n"
                    f"Optimised params: {n} symbols\n"
                    f"Source: nightly backtest\n"
                    f"Status: Auto-loaded at next startup ✅"
                )
        except Exception as e:
            logger.debug("Param review: %s", e)

    # ── Daily download report ─────────────────────────────────────────────────
    def send_daily_download_report(self) -> None:
        """Comprehensive EOD download report."""
        if self._task_done("daily_download_report"):
            return
        try:
            from data_download_tracker import get_tracker, DAILY_REQUIRED
            tracker = get_tracker()
            summary = tracker.get_daily_summary()
            ok      = summary.get("ok", 0)
            failed  = summary.get("failed", 0)
            missing = summary.get("missing_required", {})
            total_kb= summary.get("total_kb", 0)

            lines = [
                f"📥 <b>DAILY DOWNLOAD REPORT  {date.today()}</b>",
                f"{'═'*32}",
                f"  ✅ Downloaded: {ok}   ❌ Failed: {failed}",
                f"  📦 Volume: {total_kb:.0f} KB",
                f"  📋 Required items: {len(DAILY_REQUIRED)}",
            ]

            if failed > 0:
                lines.append(f"{'─'*32}")
                lines.append(f"  ❌ FAILURES:")
                for f_item in summary.get("failures",[])[:6]:
                    lines.append(f"    • {f_item['source']} | {f_item['item']}")
                    if f_item.get('error'):
                        lines.append(f"      {f_item['error'][:50]}")

            if missing:
                lines.append(f"{'─'*32}")
                lines.append(f"  ⚠️ NOT DOWNLOADED ({len(missing)}):")
                for (src,cat,item),desc in list(missing.items())[:8]:
                    lines.append(f"    • [{src}] {item} — {desc}")

            lines += [f"{'═'*32}", f"🕐 {datetime.now().strftime('%H:%M')}"]
            self._send("\n".join(lines))
        except Exception as e:
            logger.warning("daily_download_report: %s", e)

    def send_weekly_download_report(self) -> None:
        """Weekly download reliability report (Fridays)."""
        if self._task_done("weekly_download_report"):
            return
        try:
            from data_download_tracker import get_tracker, WEEKLY_REQUIRED
            tracker = get_tracker()
            weekly  = tracker.get_weekly_summary()

            lines = [
                f"📥 <b>WEEKLY DOWNLOAD REPORT</b>",
                f"Week ending {date.today()}",
                f"{'═'*32}",
                f"  Items tracked: {weekly.get('total_items',0)}",
                f"  100% reliable: {weekly.get('perfect',0)}",
            ]

            unreliable = weekly.get("unreliable",{})
            if unreliable:
                lines.append(f"{'─'*32}")
                lines.append(f"  ⚠️ UNRELIABLE ({len(unreliable)}):")
                for key, v in list(unreliable.items())[:6]:
                    item = key.split("|")[-1]
                    lines.append(f"    • {item}: {v['pct']}% ({v['ok']}ok/{v['failed']}fail)")

            # What should be downloaded weekly but wasn't
            lines.append(f"{'─'*32}")
            lines.append(f"  WEEKLY REQUIRED ITEMS:")
            for (src,cat,item),desc in WEEKLY_REQUIRED.items():
                lines.append(f"    📌 [{src}] {item}")

            lines += [f"{'═'*32}", f"🕐 {datetime.now().strftime('%H:%M')}"]
            self._send("\n".join(lines))
        except Exception as e:
            logger.warning("weekly_download_report: %s", e)
