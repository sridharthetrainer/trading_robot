import json
import sqlite3
from pathlib import Path

import pandas as pd


def test_runtime_telemetry_schema_and_scan_lifecycle(tmp_path, monkeypatch):
    import runtime_telemetry as rt
    monkeypatch.setattr(rt, "DB_PATH", tmp_path / "runtime.db")
    rt.ensure_schema()
    cycle = rt.begin_scan(2)
    rt.scan_progress(cycle, "NIFTY", duration_ms=12, strategy="trend", result="SIGNAL")
    rt.log_signal("qualified", cycle, {"symbol":"NIFTY","strategy":"trend","side":"BUY","score":7})
    rt.finish_scan(cycle, signals=1, qualified=1, rejected=1, started_at=__import__("time").time()-0.1)
    snap = rt.snapshot()
    con = sqlite3.connect(rt.DB_PATH)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    required = {"scan_cycles","raw_signals","qualified_signals","rejected_signals",
                "trade_journal","option_snapshot","market_context","daily_summary",
                "cumulative_summary","strategy_statistics","crash_history"}
    assert required <= tables
    assert snap["last_scan"]["signals"] == 1


def test_option_metrics_cache_marks_stale_and_computes_max_pain(tmp_path, monkeypatch):
    import option_metrics_cache as cache
    monkeypatch.setattr(cache, "PATH", tmp_path / "metrics.json")
    df = pd.DataFrame({"strikePrice":[90,100,110],"CE_openInterest":[100,200,50],"PE_openInterest":[50,200,100]})
    assert cache.compute_max_pain(df) == 100
    cache.update("NIFTY", {"pcr_oi":1.1,"rows":3,"spot":100}, source="nse_live")
    item = cache.get("NIFTY")
    assert item["pcr"] == 1.1 and item["stale"] is False


def test_telegram_inline_menu_has_navigation_and_confirmations():
    from telegram_commands import TelegramCommandHandler
    home = TelegramCommandHandler._menu_keyboard("home")
    control = TelegramCommandHandler._menu_keyboard("control")
    home_data = {b["callback_data"] for row in home["inline_keyboard"] for b in row}
    control_data = {b["callback_data"] for row in control["inline_keyboard"] for b in row}
    assert "menu:options" in home_data and "menu:direction" in home_data
    assert "confirm:kill" in control_data and "menu:home" in control_data


def test_all_requested_telegram_routes_are_registered():
    from telegram_commands import TelegramCommandHandler
    handler = TelegramCommandHandler("token", "1")
    expected = {"signals","positions","status","dashboard","pnl","health","strategies",
                "performance","journal","history","stats","charges","trade","open","closed",
                "menu","settings","optionhealth","optionedge","strikeflow","direction",
                "tradeview","view","nexttrade","controlroom","readiness","profitgate","go"}
    assert expected <= set(handler._handlers)


def test_visual_dashboards_render(tmp_path):
    import numpy as np
    from visual_analytics import option_dashboard, technical_dashboard
    n=60; base=np.linspace(100,110,n)
    candles=pd.DataFrame({"open":base-.2,"high":base+.8,"low":base-1,"close":base,
                          "volume":np.linspace(1000,2000,n)})
    chain=pd.DataFrame({"strikePrice":[90,100,110],"CE_openInterest":[100,200,50],
                        "PE_openInterest":[50,200,100],"CE_impliedVolatility":[18,16,19],
                        "PE_impliedVolatility":[20,17,18]})
    assert Path(technical_dashboard(candles,output_dir=str(tmp_path))).exists()
    assert Path(option_dashboard(chain,100,output_dir=str(tmp_path))).exists()


def test_auto_deploy_defaults_to_disabled(monkeypatch):
    import auto_deploy_watcher as watcher
    called = []
    monkeypatch.setattr(watcher, "load_env", lambda: None)
    monkeypatch.setattr(watcher, "deploy", lambda: called.append(True))
    monkeypatch.setattr(__import__("sys"), "argv", ["auto_deploy_watcher.py"])
    watcher.main()
    assert called == []
