"""
idle_engine.py — Meaningful Idle-Time Tasks for the Trading System

Runs in the background during non-market hours, turning 16 hours of
idle compute into trading intelligence:

SCHEDULE (automatic):
  4:28 PM   Nightly backtest — 198 symbols × 5m × 90d
  5:30 PM   ML model retrain — signal_log 60d
  6:30 PM   Walk-forward validation — rolling 30d windows
  7:00 PM   Deep parameter optimization — score thresholds per symbol
  8:00 PM   Correlation matrix + sector rotation analysis
  9:00 PM   Next-day calendar: earnings, events, high-risk symbols
  9:30 PM   Alternative data: FII historical, delivery %, pledges
  10:00 PM  Multi-tf backtest on 1h bars (swing setups)
  5:30 AM   Pre-market data download (FII/DII, participant OI)
  6:00 AM   Swing trade shortlist for morning
  7:00 AM   News scan + sentiment for high-impact stocks
  8:00 AM   Cross-asset + GIFT Nifty analysis
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_STATE_FILE = Path("idle_engine_state.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _yf_download(ticker: str, period: str = "3mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Safe yfinance download — handles MultiIndex API change."""
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        logger.debug("yf_download %s: %s", ticker, e)
        return None


def _nifty200_symbols() -> List[str]:
    for p in ["nifty200.csv"]:
        try:
            df  = pd.read_csv(p)
            col = [c for c in df.columns if c.lower() in ("symbol","ticker","scrip")][0]
            return [str(s).upper().strip() for s in df[col].dropna()][:200]
        except Exception:
            pass
    return []


def _yf_ticker(symbol: str) -> str:
    """Map NSE symbol to yfinance ticker."""
    special = {
        "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
        "SENSEX": "^BSESN", "FINNIFTY": "^CNXFIN",
    }
    return special.get(symbol.upper(), f"{symbol.upper()}.NS")


# ── Task 1: Walk-Forward Validation ──────────────────────────────────────────

def run_walk_forward_validation(alerts=None) -> dict:
    """
    Roll a 30-day backtest window across the last 90 days.
    Identifies which strategies are STABLE vs OVERFITTED.

    Window 1: days 1-30
    Window 2: days 31-60
    Window 3: days 61-90
    Stable strategy: win rate consistent across all 3 windows.
    """
    logger.info("Walk-forward validation starting...")
    results = {}

    try:
        from autonomous_backtest import _fetch, _fetch_htf, _run_single
        from signal_engine import STRATEGIES

        wf_limit = int(os.getenv("IDLE_WALK_FORWARD_MAX_SYMBOLS", os.getenv("FULL_UNIVERSE_SCAN_MAX_SYMBOLS", "220")))
        symbols = ["NIFTY", "BANKNIFTY"] + _nifty200_symbols()[:max(0, wf_limit - 2)]

        strategy_stability: Dict[str, List[float]] = {}

        for symbol in symbols[:wf_limit]:
            try:
                df     = _fetch(symbol, days=90)
                df_htf = _fetch_htf(symbol, days=90)
                if df is None or len(df) < 200:
                    continue

                # Split into 3 windows
                n = len(df)
                window_size = n // 3
                wfr = []

                for w in range(3):
                    start = w * window_size
                    end   = start + window_size
                    df_w  = df.iloc[start:end].reset_index(drop=True)
                    if len(df_w) < 50:
                        continue
                    r = _run_single(df_w, df_htf, symbol, 1.5, 2.5)
                    wr = r.get("win_rate", 0) if r else 0
                    wfr.append(wr)

                if len(wfr) >= 2:
                    avg_wr  = float(np.mean(wfr))
                    std_wr  = float(np.std(wfr))
                    stable  = std_wr < 0.15  # stable if std < 15%
                    results[symbol] = {
                        "windows": [round(w, 3) for w in wfr],
                        "avg_wr":  round(avg_wr, 3),
                        "std_wr":  round(std_wr, 3),
                        "stable":  stable,
                    }
            except Exception as e:
                logger.debug("WFV %s: %s", symbol, e)

        # Report stable vs unstable
        stable   = [s for s, d in results.items() if d.get("stable")]
        unstable = [s for s, d in results.items() if not d.get("stable")]

        if alerts:
            lines = [
                "📊 <b>WALK-FORWARD VALIDATION</b>",
                f"  Tested: {len(results)} symbols",
                f"  ✅ Stable:   {len(stable)} ({', '.join(stable[:5])})",
                f"  ⚠️ Unstable: {len(unstable)} (inconsistent win rates)",
                f"🕐 {datetime.now().strftime('%H:%M')}",
            ]
            alerts.send("\n".join(lines),
                        dedup_key=f"wfv:{date.today()}",
                        dedup_cooldown_override=3600)

        logger.info("Walk-forward: %d stable, %d unstable", len(stable), len(unstable))
        return {"stable": stable, "unstable": unstable, "details": results}

    except Exception as e:
        logger.warning("walk_forward_validation: %s", e)
        return {}


def _run_triple_barrier_labelling(alerts=None) -> dict:
    """
    Post-market: fetch EOD OHLCV for all symbols with unlabelled signals,
    then apply Triple Barrier labels (+1 win / -1 loss / 0 timeout).
    Must run BEFORE ml_train so the training data is fresh.
    Scheduled at 16:45 — 15 min after market close, before ML retrain at 17:30.
    """
    try:
        from signal_log import get_signal_logger
        from data_fetcher import DataFetcher
        sl = get_signal_logger()

        # Find symbols with pending labels
        import sqlite3
        conn = sqlite3.connect(str(sl.db_path), timeout=10)
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM signal_log WHERE tb_label = -99 "
            "AND signal_date <= date('now','localtime')"
        ).fetchall()
        conn.close()
        symbols = [r[0] for r in rows]

        if not symbols:
            logger.info("Triple-barrier labelling: no pending signals to label")
            return {"labelled": 0, "symbols": []}

        # Fetch EOD data for each symbol.
        # BUG FIX 2026-06-12: DataFetcher was constructed WITHOUT an Angel
        # client, so every fetch failed ("no market data fetched for 190
        # symbols" nightly) and no signal was ever labelled. AngelOne is a
        # singleton, so this reuses the bot's existing session.
        try:
            _ang = None
            try:
                from angel import AngelOne
                import os as _os_tb
                _ang = AngelOne(
                    api_key=_os_tb.getenv("API_KEY", ""),
                    client_id=_os_tb.getenv("CLIENT_ID", ""),
                    password=_os_tb.getenv("PASSWORD", ""),
                    totp_secret=_os_tb.getenv("TOTP_SECRET", ""))
            except Exception as _ae:
                logger.warning("Triple-barrier: Angel unavailable: %s", _ae)
            fetcher = DataFetcher(angel=_ang, paper_trade=False)
        except Exception as e:
            logger.warning("Triple-barrier: DataFetcher unavailable: %s", e)
            return {"labelled": 0, "error": str(e)}

        df_map = {}
        # days=10 so signals up to ~7 trading days old can still find their
        # entry bar; cap 60 symbols/cycle clears a full backlog in ~3 evenings
        for sym in symbols[:60]:
            try:
                df = fetcher.get_market_data(sym, interval="5m", days=10)
                if df is not None and len(df) > 50:
                    df_map[sym] = df
            except Exception as e:
                logger.debug("Triple-barrier fetch %s: %s", sym, e)

        if not df_map:
            logger.warning("Triple-barrier: no market data fetched for %d symbols", len(symbols))
            return {"labelled": 0, "symbols": symbols}

        count = sl.apply_triple_barrier_labels(df_map)
        logger.info("Triple-barrier labelling: %d signals labelled for %d symbols",
                    count, len(df_map))
        if alerts and count > 0:
            try:
                alerts.send(f"🏷️ Triple-barrier: {count} signals labelled → ML training ready")
            except Exception: pass
        return {"labelled": count, "symbols": list(df_map.keys())}
    except Exception as e:
        logger.warning("_run_triple_barrier_labelling: %s", e)
        return {"labelled": 0, "error": str(e)}


def _run_edge_report(alerts=None) -> dict:
    """Nightly measured-edge analytics over labelled signals (edge_report.py).
    Runs after labelling + weight update so the numbers include today."""
    try:
        from edge_report import build_report, render
        report = build_report(days=30, min_n=30)
        if report.get("total_labelled", 0) < 30:
            logger.info("Edge report: only %d labelled signals — skipping",
                        report.get("total_labelled", 0))
            return report
        import json as _j
        from pathlib import Path as _P
        _P("edge_report.json").write_text(_j.dumps(report, indent=1))
        logger.info("Edge report: %d labelled, overall WR %.1f%%",
                    report["total_labelled"], 100 * report["overall_win_rate"])
        if alerts:
            try:
                alerts.send(
                    "📈 <b>EDGE REPORT (30d)</b>\n"
                    f"Labelled: {report['total_labelled']} | "
                    f"WR {report['overall_win_rate']:.1%} "
                    f"(floor {report['overall_wr_wilson_low']:.1%})\n"
                    f"{report['wins']}W / {report['losses']}L / "
                    f"{report['timeouts']}T\n"
                    "Full breakdown: edge_report.json")
            except Exception: pass
        return report
    except Exception as e:
        logger.warning("_run_edge_report: %s", e)
        return {}


def _run_calibrator_retrain(alerts=None) -> dict:
    """Nightly retrain of the logistic regression signal calibrator."""
    try:
        # ML-training window guard (07:00–21:00, config) — heavy job, not overnight.
        from trading_calendar import in_ml_training_window
        _win_ok, _win = in_ml_training_window()
        if not _win_ok:
            logger.info("Signal calibrator: outside ML training window %s — skipped", _win)
            return {"trained": False, "reason": f"outside_training_window:{_win}"}
        from signal_calibrator import retrain_calibrator
        result = retrain_calibrator()
        if result.get("trained"):
            logger.info(
                "Signal calibrator retrained: n=%d val_acc=%.2f brier=%.4f",
                result.get("n_train", 0), result.get("val_acc", 0), result.get("val_brier", 0),
            )
        else:
            logger.info("Signal calibrator: %s", result.get("reason", "not trained"))
        return result
    except Exception as e:
        logger.warning("_run_calibrator_retrain: %s", e)
        return {}


def _run_eod_weight_update(alerts=None) -> dict:
    """Nightly update of strategy and indicator weights from P&L/labels."""
    try:
        from eod_weight_engine import run_eod_weight_update
        return run_eod_weight_update(alerts=alerts)
    except Exception as e:
        logger.warning("_run_eod_weight_update: %s", e)
        return {"error": str(e)}


def _run_track_record(alerts=None) -> dict:
    """Build and save signal_track_record.json nightly after walk-forward completes."""
    try:
        from signal_track_record import save_track_record
        record = save_track_record()
        n = record.get("summary", {}).get("strategies_tracked", 0)
        wr = record.get("summary", {}).get("overall_win_rate", 0)
        logger.info("Track record updated: %d strategies, win_rate=%.1f%%", n, wr * 100)
        if alerts:
            try:
                alerts.send(f"📊 Track record updated: {n} strategies | System win rate: {wr*100:.1f}%")
            except Exception: pass
        return record.get("summary", {})
    except Exception as e:
        logger.warning("_run_track_record: %s", e)
        return {}


def _run_autonomous_learning_cycle(alerts=None) -> dict:
    """Nightly autonomous learning + safety gate refresh."""
    try:
        from autonomous_learning_cycle import render_summary, run_autonomous_learning_cycle

        report = run_autonomous_learning_cycle()
        logger.info("Autonomous learning cycle: ok=%s", report.get("ok"))
        if alerts:
            try:
                alerts.send(
                    "<b>AUTONOMOUS LEARNING CYCLE</b>\n"
                    + render_summary(report),
                    dedup_key=f"autonomous_learning:{date.today()}",
                    dedup_cooldown_override=3600,
                )
            except Exception:
                pass
        return report
    except Exception as e:
        logger.warning("_run_autonomous_learning_cycle: %s", e)
        return {"ok": False, "error": str(e)}


def _run_nse_data_hub(alerts=None) -> dict:
    """Refresh all NSE data hub datasets into cache."""
    try:
        from nse_data_hub import collect_all_nse_data, summarize_nse_data

        result = collect_all_nse_data(force=True)
        logger.info(
            "NSE data hub refresh: ok=%s datasets=%s/%s",
            result.get("ok"),
            result.get("ok_count"),
            result.get("dataset_count"),
        )
        if alerts:
            try:
                alerts.send(
                    "<b>NSE DATA HUB REFRESH</b>\n"
                    + summarize_nse_data(result),
                    dedup_key=f"nse_data_hub:{date.today()}:{datetime.now().hour}",
                    dedup_cooldown_override=3600,
                )
            except Exception:
                pass
        return {
            "ok": bool(result.get("ok")),
            "ok_count": result.get("ok_count", 0),
            "dataset_count": result.get("dataset_count", 0),
        }
    except Exception as e:
        logger.warning("_run_nse_data_hub: %s", e)
        return {"ok": False, "error": str(e)}


def _run_data_pipeline_audit(alerts=None) -> dict:
    """Run data pipeline audit and save score/report for autonomous monitoring."""
    try:
        from data_pipeline_audit import run_audit, render_markdown, REPORT_JSON, REPORT_MD

        report = run_audit(fetch_sample=3, internet=True)
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        Path(REPORT_MD).write_text(render_markdown(report), encoding="utf-8")
        score = report.get("score", {}) or {}
        logger.info(
            "Data pipeline audit: ok=%s score=%s grade=%s",
            report.get("ok"),
            score.get("total"),
            score.get("grade"),
        )
        if alerts:
            try:
                alerts.send(
                    "<b>DATA PIPELINE AUDIT</b>\n"
                    f"Score: {score.get('total', 0)}/{score.get('max', 100)} "
                    f"grade {score.get('grade', '?')}\n"
                    f"Readiness: {score.get('readiness', '?')}\n"
                    f"Status: {'PASS' if report.get('ok') else 'WARN'}",
                    dedup_key=f"data_pipeline_audit:{date.today()}",
                    dedup_cooldown_override=3600,
                )
            except Exception:
                pass
        return {
            "ok": bool(report.get("ok")),
            "score": score.get("total", 0),
            "grade": score.get("grade", "?"),
            "readiness": score.get("readiness", "?"),
        }
    except Exception as e:
        logger.warning("_run_data_pipeline_audit: %s", e)
        return {"ok": False, "error": str(e)}


def _run_option_bot_audit(alerts=None) -> dict:
    """Run option bot audit and save score/report for autonomous monitoring."""
    try:
        from option_bot_audit import build_audit

        report = build_audit()
        Path("option_bot_audit_report.json").write_text(
            json.dumps(report, indent=2, default=str),
            encoding="utf-8",
        )
        score = report.get("score", {}) or {}
        auto = score.get("autonomous_score", {}) or {}
        logger.info(
            "Option bot audit: strict=%s auto=%s readiness=%s",
            score.get("total"),
            auto.get("total"),
            score.get("readiness"),
        )
        if alerts:
            try:
                alerts.send(
                    "<b>OPTION BOT AUDIT</b>\n"
                    f"Strict score: {score.get('total', 0)}/{score.get('max', 100)} "
                    f"grade {score.get('grade', '?')}\n"
                    f"Autonomous score: {auto.get('total', 0)}/{auto.get('max', 100)} "
                    f"grade {auto.get('grade', '?')}\n"
                    f"Readiness: {score.get('readiness', '?')}",
                    dedup_key=f"option_bot_audit:{date.today()}",
                    dedup_cooldown_override=3600,
                )
            except Exception:
                pass
        return {
            "ok": True,
            "strict_score": score.get("total", 0),
            "autonomous_score": auto.get("total", 0),
            "readiness": score.get("readiness", "?"),
        }
    except Exception as e:
        logger.warning("_run_option_bot_audit: %s", e)
        return {"ok": False, "error": str(e)}


def _run_intraday_candle_recording(alerts=None) -> dict:
    """Store 1m/5m/15m candles locally before EOD learning begins."""
    try:
        from intraday_candle_recorder import record_intraday_candles

        report = record_intraday_candles()
        logger.info(
            "Intraday candle recorder: %d/%d fetches ok",
            report.get("ok_count", 0),
            report.get("requested", 0),
        )
        if alerts:
            try:
                alerts.send(
                    "<b>INTRADAY CANDLE RECORDER</b>\n"
                    f"ok={report.get('ok_count', 0)}/{report.get('requested', 0)}",
                    dedup_key=f"intraday_candles:{date.today()}",
                    dedup_cooldown_override=3600,
                )
            except Exception:
                pass
        return report
    except Exception as e:
        logger.warning("_run_intraday_candle_recording: %s", e)
        return {"error": str(e)}


# ── Task 2: Correlation Matrix ────────────────────────────────────────────────

def run_correlation_update(alerts=None) -> dict:
    """
    Compute daily correlation matrix for top 50 Nifty200 symbols.
    Saves to correlation_matrix.json for use by position sizer.
    High correlation (>0.8) → don't hold both positions simultaneously.
    """
    logger.info("Correlation matrix update starting...")
    try:
        symbols = _nifty200_symbols()[:50]
        prices  = {}

        for sym in symbols:
            ticker = _yf_ticker(sym)
            df     = _yf_download(ticker, period="3mo", interval="1d")
            if df is not None and "close" in df.columns and len(df) >= 30:
                prices[sym] = df["close"].values[-30:]  # last 30 days

        if len(prices) < 5:
            return {}

        # Build correlation matrix
        syms    = list(prices.keys())
        matrix  = np.zeros((len(syms), len(syms)))
        min_len = min(len(v) for v in prices.values())

        for i, s1 in enumerate(syms):
            for j, s2 in enumerate(syms):
                if i == j:
                    matrix[i][j] = 1.0
                elif i < j:
                    p1 = prices[s1][-min_len:]
                    p2 = prices[s2][-min_len:]
                    if len(p1) > 1 and len(p2) > 1:
                        corr = float(np.corrcoef(p1, p2)[0, 1])
                        matrix[i][j] = matrix[j][i] = round(corr, 3)

        # Find highly correlated pairs (>0.8)
        high_corr = []
        for i in range(len(syms)):
            for j in range(i+1, len(syms)):
                if matrix[i][j] > 0.80:
                    high_corr.append((syms[i], syms[j], round(matrix[i][j], 3)))

        # Save to file
        result = {
            "symbols":     syms,
            "matrix":      matrix.tolist(),
            "high_corr":   high_corr[:20],
            "updated":     datetime.now().isoformat(),
        }
        Path("correlation_matrix.json").write_text(json.dumps(result, indent=2))

        if alerts:
            top_pairs = " · ".join(f"{a}/{b}={c:.2f}" for a,b,c in high_corr[:3])
            alerts.send(
                f"🔗 <b>CORRELATION UPDATE</b>\n"
                f"  {len(syms)} symbols analysed\n"
                f"  {len(high_corr)} high-correlation pairs (>0.80)\n"
                f"  Top: {top_pairs or 'None'}\n"
                f"  ✅ position_sizer updated\n"
                f"🕐 {datetime.now().strftime('%H:%M')}",
                dedup_key=f"corr:{date.today()}",
                dedup_cooldown_override=3600,
            )

        logger.info("Correlation: %d pairs >0.8 found", len(high_corr))
        return result

    except Exception as e:
        logger.warning("correlation_update: %s", e)
        return {}


# ── Task 3: Next-Day Event Calendar + High-Risk Symbols ──────────────────────

def run_event_calendar_scan(alerts=None) -> dict:
    """
    Fetch tomorrow's:
    - NSE earnings calendar (avoid trading these stocks)
    - RBI/SEBI events (flag for reduced sizing)
    - Expiry details (NIFTY Thursday, BANKNIFTY monthly etc)
    - FnO ban list preview
    """
    logger.info("Event calendar scan starting...")
    try:
        import requests
        from datetime import timedelta

        tomorrow    = date.today() + timedelta(days=1)
        # Skip weekends
        if tomorrow.weekday() >= 5:
            tomorrow = tomorrow + timedelta(days=7 - tomorrow.weekday())

        events        = []
        high_risk_syms = []

        # 1. NSE Corporate Actions (dividends, splits, results)
        try:
            s = requests.Session()
            s.headers.update({"User-Agent": "Mozilla/5.0",
                               "Referer": "https://www.nseindia.com/"})
            s.get("https://www.nseindia.com/", timeout=6)
            r = s.get(
                f"https://www.nseindia.com/api/event-calendar?index=equities"
                f"&from_date={tomorrow.strftime('%d-%m-%Y')}"
                f"&to_date={tomorrow.strftime('%d-%m-%Y')}",
                timeout=10
            )
            if r.status_code == 200:
                for ev in r.json().get("data", [])[:20]:
                    sym  = str(ev.get("symbol", "?")).upper()
                    purp = str(ev.get("purpose", "?"))
                    events.append(f"{sym}: {purp[:40]}")
                    if any(x in purp.upper() for x in ["RESULT","EARNINGS","DIVIDEND","SPLIT","BONUS"]):
                        high_risk_syms.append(sym)
        except Exception as e:
            logger.debug("Event calendar NSE: %s", e)

        # 2. Check FnO ban list
        try:
            r2 = s.get("https://www.nseindia.com/api/fo-sec-ban-list", timeout=10)
            if r2.status_code == 200:
                banned = r2.json().get("data", [])
                for b in banned[:10]:
                    sym = str(b.get("symbol","?")).upper()
                    if sym not in high_risk_syms:
                        high_risk_syms.append(sym)
                        events.append(f"{sym}: F&O BAN")
        except Exception:
            pass

        # Save for morning use
        result = {
            "date":           tomorrow.isoformat(),
            "events":         events[:20],
            "high_risk_syms": list(set(high_risk_syms))[:15],
            "updated":        datetime.now().isoformat(),
        }
        Path("next_day_events.json").write_text(json.dumps(result, indent=2))

        if alerts and (events or high_risk_syms):
            lines = [
                f"📅 <b>TOMORROW'S CALENDAR</b>  {tomorrow.strftime('%d %b')}",
                f"  Events: {len(events)}",
                f"  ⚠️ Avoid: {', '.join(high_risk_syms[:6]) or 'None'}",
            ]
            if events:
                for ev in events[:3]:
                    lines.append(f"    • {ev}")
            lines.append(f"🕐 {datetime.now().strftime('%H:%M')}")
            alerts.send("\n".join(lines),
                        dedup_key=f"events:{tomorrow}",
                        dedup_cooldown_override=3600)

        logger.info("Events: %d events, %d high-risk symbols", len(events), len(high_risk_syms))
        return result

    except Exception as e:
        logger.warning("event_calendar_scan: %s", e)
        return {}


# ── Task 4: Multi-Timeframe Backtest (1h bars — swing setups) ─────────────────

def run_mtf_backtest(alerts=None) -> dict:
    """
    Run 1-hour bar backtest for swing setups.
    Identifies stocks with strong daily/weekly trends for 1-3 day holds.
    Runs at 5:30 AM — results ready for pre-market brief.
    """
    logger.info("MTF backtest (1h) starting...")
    try:
        from signal_engine import generate_signal
        mtf_limit = int(os.getenv("IDLE_MTF_BACKTEST_MAX_SYMBOLS", os.getenv("FULL_UNIVERSE_SCAN_MAX_SYMBOLS", "220")))
        symbols  = ["NIFTY", "BANKNIFTY"] + _nifty200_symbols()[:max(0, mtf_limit - 2)]
        watchlist = []

        for symbol in symbols[:mtf_limit]:
            try:
                ticker = _yf_ticker(symbol)
                df_1h  = _yf_download(ticker, period="60d", interval="1h")
                df_d   = _yf_download(ticker, period="6mo", interval="1d")

                if df_1h is None or len(df_1h) < 50:
                    continue

                sig = generate_signal(df=df_1h, df_htf=df_d, symbol=symbol)
                if sig and float(sig.get("score", 0)) >= 5.5 and sig.get("direction"):
                    watchlist.append({
                        "symbol":    symbol,
                        "direction": sig.get("direction", "?"),
                        "score":     round(float(sig.get("score", 0)), 2),
                        "strategy":  sig.get("strategy", "?"),
                    })
            except Exception as e:
                logger.debug("MTF backtest %s: %s", symbol, e)

        # Deduplicate by symbol (keep highest score)
        seen = {}
        for w in watchlist:
            sym = w["symbol"]
            if sym not in seen or w["score"] > seen[sym]["score"]:
                seen[sym] = w
        watchlist = sorted(seen.values(), key=lambda x: -x["score"])

        # Cap scores at realistic max (10.0) — duplicate strategies inflate scores
        for w in watchlist:
            w["score"] = min(w["score"], 10.0)

        # At pre-market only show BUY setups — can't short before open
        from datetime import time as _dtime
        _now_t = datetime.now().time()
        _pre_market = _dtime(4, 0) <= _now_t <= _dtime(9, 14)
        if _pre_market:
            buy_setups  = [w for w in watchlist if w.get("direction") == "BUY"]
            sell_setups = [w for w in watchlist if w.get("direction") == "SELL"]
            display_list = buy_setups[:8]   # show buys pre-market
            sell_note = f"  + {len(sell_setups)} SELL setups (post-open only)" if sell_setups else ""
        else:
            display_list = watchlist[:8]
            sell_note = ""

        Path("swing_watchlist.json").write_text(json.dumps({
            "date":      date.today().isoformat(),
            "watchlist": [dict(w, score=min(w["score"],10.0)) for w in watchlist[:12]],
        }, indent=2))

        if alerts and display_list:
            lines = [
                f"📈 <b>SWING WATCHLIST</b>  {date.today().strftime('%d %b')}",
                f"  {len(watchlist)} setups | showing {'BUY only (pre-market)' if _pre_market else 'all'}",
            ]
            for w in display_list[:5]:
                si = "🟢" if w["direction"] == "BUY" else "🔴"
                lines.append(f"  {si} {w['symbol']:<12} score {w['score']:.1f}  [{w['strategy']}]")
            if sell_note:
                lines.append(sell_note)
            lines.append(f"  📱 /morning for full list + context")
            lines.append(f"🕐 {datetime.now().strftime('%H:%M')}")
            alerts.send("\n".join(lines),
                        dedup_key=f"swing:{date.today()}",
                        dedup_cooldown_override=3600)

        logger.info("MTF backtest: %d swing setups found", len(watchlist))
        return {"watchlist": watchlist}

    except Exception as e:
        logger.warning("mtf_backtest: %s", e)
        return {}


# ── Task 5: Alternative Data Download ────────────────────────────────────────

def run_alternative_data_download(alerts=None) -> dict:
    """
    Download data that's not available during market hours:
    - FII/DII last 30 days history
    - Delivery % for Nifty200 (BhavCopy)
    - Promoter pledge changes
    - Bulk/block deal history
    """
    logger.info("Alternative data download starting...")
    results = {}

    # FII/DII historical
    try:
        from participant_oi import get_participant_data
        pd_data = get_participant_data(force=True)
        if pd_data:
            results["fii_dii"] = "✅ updated"
    except Exception as e:
        results["fii_dii"] = f"⚠️ {str(e)[:30]}"

    # BhavCopy
    try:
        from bhav_copy import get_bhav_data as fetch_bhav_copy
        bhav = fetch_bhav_copy()
        if bhav:
            results["bhav_copy"] = f"✅ {len(bhav)} records"
    except Exception as e:
        results["bhav_copy"] = f"⚠️ {str(e)[:30]}"
        # Save FII data to history tracker after successful fetch
        try:
            from fii_tracker import record_today as _rec_fii
            from participant_oi import get_participant_data as _gpd
            _pd = _gpd(force=True)
            if _pd and _pd.get("FII",{}).get("net_cash",0):
                _rec_fii(
                    fii_cash_net=_pd["FII"].get("net_cash",0),
                    dii_cash_net=_pd.get("DII",{}).get("net_cash",0),
                    fii_fut_long=_pd["FII"].get("fut_long",0),
                    fii_fut_short=_pd["FII"].get("fut_short",0),
                    fii_call_long=_pd["FII"].get("call_long",0),
                    fii_put_long=_pd["FII"].get("put_long",0),
                )
                results["fii_history"] = "✅ FII/DII saved"
        except Exception as _fe: results["fii_history"] = f"⚠️ {_fe}"

    # Bulk deals
    try:
        from bulk_deals import get_bulk_deals as fetch_bulk_deals
        deals = fetch_bulk_deals()
        if deals:
            results["bulk_deals"] = f"✅ {len(deals)} deals"
    except Exception as e:
        results["bulk_deals"] = f"⚠️ {str(e)[:30]}"

    # Corporate actions
    try:
        from corporate_actions import get_corporate_actions as _get_ca_fn
        _get_ca_fn()
        results["corporate_actions"] = "✅ updated"
    except Exception as e:
        results["corporate_actions"] = f"⚠️ {str(e)[:30]}"

    if alerts:
        ok_count = sum(1 for v in results.values() if v.startswith("✅"))
        alerts.send(
            f"📥 <b>ALT DATA DOWNLOAD</b>\n"
            + "\n".join(f"  {k}: {v}" for k, v in results.items())
            + f"\n  {ok_count}/{len(results)} succeeded"
            + f"\n🕐 {datetime.now().strftime('%H:%M')}",
            dedup_key=f"altdata:{date.today()}",
            dedup_cooldown_override=3600,
        )

    logger.info("Alt data: %s", results)
    return results


def _run_edge_monitor(alerts=None) -> dict:
    """Nightly: re-test pairs stat-arb + strategy keep/prune; alert ONLY when a
    pair crosses the validated bar (cointegrated + OOS edge after costs)."""
    try:
        import nightly_edge_monitor
        return nightly_edge_monitor.run()
    except Exception as exc:
        logger.debug("edge monitor: %s", exc)
        return {"error": str(exc)[:80]}


# ── Master Idle Engine ────────────────────────────────────────────────────────

class IdleEngine:
    """
    Orchestrates all idle-time tasks on a schedule.
    Runs as a background thread inside main_autonomous.
    """

    SCHEDULE = [
        # (hour, min, key, fn, desc[, max_late_min])
        # "backtest" 16:28 and "ml_train" 17:30 were fn=None placeholders that
        # never ran (removed 2026-06-12): walk_forward 18:30 and calibrator
        # 20:30 are the real implementations of those intents.
        ( 8, 50, "nse_hub_am",   _run_nse_data_hub,             "NSE data hub pre-market refresh", 180),
        (15, 25, "intraday_candles", _run_intraday_candle_recording, "Intraday candle cache recording", 180),
        (15, 45, "nse_hub_pm",   _run_nse_data_hub,             "NSE data hub post-market refresh", 360),
        (16, 10, "pipeline_audit", _run_data_pipeline_audit,    "Data pipeline audit", 360),
        (16, 20, "option_bot_audit", _run_option_bot_audit,     "Option bot audit", 360),
        (16, 45, "tb_labels",    _run_triple_barrier_labelling, "Triple-barrier signal labelling", 360),
        (18, 30, "walk_forward", run_walk_forward_validation,   "Walk-forward validation", 480),
        (19, 30, "track_record", _run_track_record,             "Signal track record update", 480),
        (20, 30, "calibrator",   _run_calibrator_retrain,       "LR calibrator retrain", 480),
        (20, 45, "eod_weights",  _run_eod_weight_update,        "EOD strategy/indicator weights", 480),
        (21, 15, "edge_report",  _run_edge_report,              "Measured-edge analytics report", 480),
        (21, 30, "autolearn",    _run_autonomous_learning_cycle,"Autonomous learning cycle", 600),
        (19, 0,  "alt_data",     run_alternative_data_download, "Alternative data download", 480),
        (20, 0,  "correlation",  run_correlation_update,        "Correlation matrix", 480),
        (21, 0,  "events",       run_event_calendar_scan,       "Event calendar scan", 480),
        ( 5, 30, "mtf_backtest", run_mtf_backtest,              "Multi-TF backtest (1h)", 240),
        (21, 50, "edge_monitor", _run_edge_monitor,            "Edge monitor: pairs + keep/prune (alert on validated edge)", 600),
    ]

    def __init__(self, alerts=None) -> None:
        self.alerts   = alerts
        self._ran     = {}
        self._thread  = None
        self._stop    = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="IdleEngine"
        )
        self._thread.start()
        logger.info("IdleEngine started")

    def stop(self) -> None:
        self._stop.set()

    # 2026-07-10 (operator: "I may switch the system off at night — ensure
    # skipped processes are carried out while the system is on, note which
    # happened and which didn't, and re-execute on the next run"): a task's
    # most recent slot (today's, or yesterday's if today's hasn't come yet)
    # that never completed is CAUGHT UP within this window instead of being
    # skipped for the day when the machine was off past max_late_min.
    CATCHUP_WINDOW_H = float(os.getenv("IDLE_CATCHUP_WINDOW_HOURS", "26"))

    @staticmethod
    def _due_slot(item, now, ran) -> tuple:
        """Decide what (if anything) is due for one schedule item.

        Returns (task_key, mode) where mode is "on_time" (inside the task's
        own max_late window), "catch_up" (missed slot within
        CATCHUP_WINDOW_H — machine was off or the process was down), or
        ("", "") when nothing is due. Checks today's slot first, then
        yesterday's, so a multi-day gap drains newest-first across passes.
        """
        h, m, key, fn, desc = item[:5]
        max_late_min = int(item[5]) if len(item) > 5 else 5
        slot_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        slots = ([slot_today] if now >= slot_today else []) + [slot_today - timedelta(days=1)]
        for slot in slots:
            task_key = f"{key}:{slot.date()}"
            if ran.get(task_key):
                continue
            late = (now - slot).total_seconds()
            if late < 0:
                continue
            if late <= max_late_min * 60:
                return task_key, "on_time"
            if late <= IdleEngine.CATCHUP_WINDOW_H * 3600:
                return task_key, "catch_up"
        return "", ""

    @staticmethod
    def _market_open_now(now) -> bool:
        return now.weekday() < 5 and dtime(9, 10) <= now.time() <= dtime(15, 35)

    def _record_run(self, key: str, task_key: str, scheduled: str, mode: str) -> None:
        """Append to job_catchup_report.json — the operator-visible ledger of
        which scheduled jobs ran on time, which were caught up, and when."""
        try:
            path = Path("job_catchup_report.json")
            rep = json.loads(path.read_text()) if path.exists() else {}
            day = task_key.rsplit(":", 1)[-1]
            rep.setdefault(day, {})[key] = {
                "scheduled": scheduled,
                "ran_at": datetime.now().isoformat(timespec="seconds"),
                "mode": mode,
            }
            # keep last 14 days
            for old in sorted(rep)[:-14]:
                rep.pop(old, None)
            path.write_text(json.dumps(rep, indent=2))
        except Exception as e:
            logger.debug("catchup report: %s", e)

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            for item in self.SCHEDULE:
                h, m, key, fn, desc = item[:5]
                if fn is None:
                    continue  # handled by main_autonomous
                task_key, mode = self._due_slot(item, now, self._ran)
                if not task_key:
                    continue
                # Catch-up runs are heavy learning/report jobs — never let a
                # backlog drain compete with live scanning. On-time runs keep
                # their designed slots (which were already market-aware).
                if mode == "catch_up" and self._market_open_now(now):
                    continue
                # Persist "started" (truthy, so this process never double-
                # fires) and only mark True once the task completes: a
                # restart mid-task drops the marker in _load_state so the
                # catch-up window re-fires it. (2026-07-06: a 21:38 restart
                # killed the 21:30 autolearn mid-run, and the pre-marked
                # True skipped it for the rest of the day.)
                self._ran[task_key] = "started"
                self._save_state()
                tag = "CATCH-UP (missed while off)" if mode == "catch_up" else "starting"
                logger.info("IdleEngine: %s %s [%s]", tag, desc, task_key)
                self.alerts.send(
                    f"⚙️ <b>IDLE TASK: {desc.upper()}</b>\n"
                    f"  {'♻️ Catch-up — was missed while the system was off' if mode == 'catch_up' else 'Starting scheduled background task'}\n"
                    f"🕐 {now.strftime('%H:%M')}",
                    dedup_key=f"idle_start:{task_key}",
                    dedup_cooldown_override=3600,
                ) if self.alerts else None
                try:
                    fn(self.alerts)
                except Exception as e:
                    logger.warning("IdleEngine task %s: %s", key, e)
                self._ran[task_key] = True
                self._save_state()
                self._record_run(key, task_key, f"{h:02d}:{m:02d}", mode)
            # External (cron) jobs missed while the machine was off —
            # post-market ML and the condor forward test. Cheap file checks.
            try:
                from job_catchup import check_and_run_external
                check_and_run_external(now)
            except Exception as e:
                logger.debug("external catchup: %s", e)
            self._stop.wait(60)  # check every minute

    def get_todays_schedule(self) -> str:
        """Format schedule for /schedule — chronological, full 07:00–20:00 window.

        Merges the idle-engine tasks (done-tracked) with the main-loop milestones
        that run in main_autonomous (market open, pipelines, the 07:00 prep and
        19:00 final EOD capture) so /schedule reflects the real operating window
        instead of only the idle list in declaration order."""
        now = datetime.now()
        # idle tasks (tracked) — mark with their _ran status
        items = []
        for item in self.SCHEDULE:
            h, m, key, fn, desc = item[:5]
            items.append((h, m, key, desc, True))
        # main-loop milestones (display-only — they run in main_autonomous)
        for h, m, key, desc in [
            (7, 0,   "early_prep",  "Pre-session data prep (early start)"),
            (8, 28,  "premkt_brief", "Pre-market brief"),
            (9, 15,  "market_open", "Market opens — scan + signals + OI/IV log"),
            (15, 30, "eod_pnl",     "EOD square-off + daily P&L"),
            (16, 0,  "ml_pipeline", "Post-market ML pipeline"),
            (19, 0,  "eod_final",   "Final EOD capture (fresh FII/DII + VIX)"),
        ]:
            items.append((h, m, key, desc, False))
        items.sort(key=lambda x: (x[0], x[1]))
        try:
            from trading_calendar import in_ml_training_window
            _w_ok, _w = in_ml_training_window()
            _ml_line = f"  🧠 ML training window: {_w} ({'open' if _w_ok else 'closed'})"
        except Exception:
            _ml_line = "  🧠 ML training window: 07:00-21:00"
        lines = ["📅 <b>TODAY'S SCHEDULE</b>  (07:00–20:00 active)",
                 f"  🕐 Now: {now.strftime('%H:%M')}",
                 _ml_line,
                 "  ─────────────────────────"]
        for h, m, key, desc, tracked in items:
            t = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if tracked and self._ran.get(f"{key}:{date.today()}", False):
                icon = "✅"
            elif t > now:
                icon = "🔜"
            else:
                icon = "✅" if not tracked else "⏰"   # past untracked milestone = assume ran
            lines.append(f"  {icon} {h:02d}:{m:02d}  {desc}")
        return "\n".join(lines)

    def _save_state(self) -> None:
        try:
            _STATE_FILE.write_text(json.dumps(self._ran))
        except Exception:
            pass

    def _load_state(self) -> None:
        try:
            if _STATE_FILE.exists():
                loaded = json.loads(_STATE_FILE.read_text())
                # Keep only COMPLETED tasks (True): "started" markers belong to
                # a process that died mid-task, and the catch-up window should
                # re-fire those. Also drop entries older than 2 days so the
                # state file stops growing forever (task keys embed the date).
                cutoff = str(date.today() - timedelta(days=2))
                self._ran = {
                    k: v for k, v in loaded.items()
                    if v is True and k.split(":", 1)[-1] >= cutoff
                }
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────
_engine: Optional[IdleEngine] = None

def get_idle_engine(alerts=None) -> IdleEngine:
    global _engine
    if _engine is None:
        _engine = IdleEngine(alerts=alerts)
        _engine._load_state()
    return _engine
