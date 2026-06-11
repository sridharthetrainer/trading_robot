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

        symbols = ["NIFTY", "BANKNIFTY"] + _nifty200_symbols()[:10]  # quick sample

        strategy_stability: Dict[str, List[float]] = {}

        for symbol in symbols[:15]:  # test 15 symbols for now
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
            "AND signal_date <= date('now','-1 day')"
        ).fetchall()
        conn.close()
        symbols = [r[0] for r in rows]

        if not symbols:
            logger.info("Triple-barrier labelling: no pending signals to label")
            return {"labelled": 0, "symbols": []}

        # Fetch EOD data for each symbol
        try:
            fetcher = DataFetcher(paper_trade=False)
        except Exception as e:
            logger.warning("Triple-barrier: DataFetcher unavailable: %s", e)
            return {"labelled": 0, "error": str(e)}

        df_map = {}
        for sym in symbols[:20]:  # cap at 20 symbols per cycle
            try:
                df = fetcher.get_market_data(sym, interval="5m", days=5)
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


def _run_calibrator_retrain(alerts=None) -> dict:
    """Nightly retrain of the logistic regression signal calibrator."""
    try:
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
        symbols  = ["NIFTY", "BANKNIFTY"] + _nifty200_symbols()[:20]
        watchlist = []

        for symbol in symbols[:25]:
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


# ── Master Idle Engine ────────────────────────────────────────────────────────

class IdleEngine:
    """
    Orchestrates all idle-time tasks on a schedule.
    Runs as a background thread inside main_autonomous.
    """

    SCHEDULE = [
        # (hour, min, key, fn, desc)
        (16, 28, "backtest",     None,                          "Nightly backtest"),
        (16, 45, "tb_labels",    _run_triple_barrier_labelling, "Triple-barrier signal labelling"),
        (17, 30, "ml_train",     None,                          "ML training"),
        (18, 30, "walk_forward", run_walk_forward_validation,  "Walk-forward validation"),
        (19, 30, "track_record",  _run_track_record,            "Signal track record update"),
        (20, 30, "calibrator",   _run_calibrator_retrain,      "LR calibrator retrain"),
        (20, 45, "eod_weights",  _run_eod_weight_update,       "EOD strategy/indicator weights"),
        (19, 0,  "alt_data",     run_alternative_data_download,"Alternative data download"),
        (20, 0,  "correlation",  run_correlation_update,       "Correlation matrix"),
        (21, 0,  "events",       run_event_calendar_scan,      "Event calendar scan"),
        ( 5, 30, "mtf_backtest", run_mtf_backtest,             "Multi-TF backtest (1h)"),
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

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            for h, m, key, fn, desc in self.SCHEDULE:
                if fn is None:
                    continue  # handled by main_autonomous
                task_key = f"{key}:{date.today()}"
                if self._ran.get(task_key):
                    continue
                # Fire within a 5-min window
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff   = abs((now - target).total_seconds())
                if diff <= 300:   # within 5 min of scheduled time
                    self._ran[task_key] = True
                    self._save_state()
                    logger.info("IdleEngine: starting %s", desc)
                    self.alerts.send(
                        f"⚙️ <b>IDLE TASK: {desc.upper()}</b>\n"
                        f"  Starting scheduled background task\n"
                        f"🕐 {now.strftime('%H:%M')}",
                        dedup_key=f"idle_start:{task_key}",
                        dedup_cooldown_override=3600,
                    ) if self.alerts else None
                    try:
                        fn(self.alerts)
                    except Exception as e:
                        logger.warning("IdleEngine task %s: %s", key, e)
            self._stop.wait(60)  # check every minute

    def get_todays_schedule(self) -> str:
        """Format schedule for /schedule Telegram command."""
        now  = datetime.now()
        lines = ["📅 <b>TODAY'S SCHEDULE</b>"]
        for h, m, key, fn, desc in self.SCHEDULE:
            task_key = f"{key}:{date.today()}"
            done     = self._ran.get(task_key, False)
            t        = datetime.now().replace(hour=h, minute=m, second=0)
            future   = t > now
            icon     = "✅" if done else "🔜" if future else "⏰"
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
                self._ran = json.loads(_STATE_FILE.read_text())
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
