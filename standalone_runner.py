"""standalone_runner.py — Runs when main bot is manually stopped."""
from __future__ import annotations
import json, logging, sys, time
from datetime import datetime, date
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_HB_FILE = Path("heartbeat.json")
_RAN_FILE = Path("standalone_ran.json")
_MAX_AGE  = 600  # 10 min

def _bot_is_running() -> bool:
    try:
        if not _HB_FILE.exists(): return False
        hb  = json.loads(_HB_FILE.read_text())
        age = time.time() - float(hb.get("ts", 0))
        return age < _MAX_AGE
    except Exception: return False

def _already_ran_today(task: str) -> bool:
    try:
        if not _RAN_FILE.exists(): return False
        ran = json.loads(_RAN_FILE.read_text())
        return ran.get(f"{date.today()}:{task}", False)
    except Exception: return False

def _mark_ran(task: str) -> None:
    try:
        ran = {}
        if _RAN_FILE.exists(): ran = json.loads(_RAN_FILE.read_text())
        ran[f"{date.today()}:{task}"] = True
        _RAN_FILE.write_text(json.dumps(ran))
    except Exception: pass

def _send_alert(msg: str) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import config as cfg, requests
        url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": cfg.TELEGRAM_CHAT_ID,
                                  "text": msg, "parse_mode":"HTML"}, timeout=10)
    except Exception as e:
        logger.debug("Alert: %s", e)

def run_standalone_tasks():
    now = datetime.now(); h = now.hour
    if h < 4 or h > 23: return
    ts  = now.strftime("%H:%M")
    _send_alert(f"\U0001f319 <b>STANDALONE MODE</b>\nBot offline — running maintenance\n\U0001f550 {ts}")
    tasks_run = []
    if 16 <= h <= 18 and not _already_ran_today("backtest"):
        _mark_ran("backtest"); tasks_run.append("Nightly backtest")
        _send_alert("\U0001f4d0 Starting nightly backtest (199 symbols)...")
        try:
            from autonomous_backtest import get_backtest
            get_backtest().run(); _send_alert("\u2705 Backtest complete.")
        except Exception as e:
            _send_alert(f"\u274c Backtest failed: {e}")
    if 18 <= h <= 20 and not _already_ran_today("ml_training"):
        _mark_ran("ml_training"); tasks_run.append("ML training")
        _send_alert("\U0001f9e0 Starting ML training...")
        try:
            from self_learning_engine import SelfLearningEngine
            SelfLearningEngine().run(); _send_alert("\u2705 ML training complete.")
        except Exception as e:
            _send_alert(f"\u274c ML training failed: {e}")
    if now.weekday() == 5 and not _already_ran_today("evolution"):
        _mark_ran("evolution"); tasks_run.append("Strategy evolution")
        _send_alert("\U0001f9ec Starting weekly strategy evolution...")
        try:
            from strategy_evolution import get_evolution
            get_evolution().evolve()
        except Exception as e:
            _send_alert(f"\u274c Evolution failed: {e}")
    if tasks_run:
        done_list = "\n".join(f"  \u2022 {t}" for t in tasks_run)
        _send_alert(f"\u2705 <b>STANDALONE TASKS DONE</b>\n{done_list}\n\U0001f550 {datetime.now().strftime(chr(37)+'H:%M')}")

if __name__ == "__main__":
    if _bot_is_running():
        logger.info("Main bot is running — standalone not needed"); sys.exit(0)
    logger.info("Main bot offline — running standalone tasks")
    run_standalone_tasks()
