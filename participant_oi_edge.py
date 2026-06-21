"""
participant_oi_edge.py — does FII index-futures positioning predict NIFTY?

HYPOTHESIS (the classic "smart money" claim)
  FII net index-futures positioning, and especially its day-to-day CHANGE,
  leads forward NIFTY returns. We test it honestly and report whether it does.

DATA
  - FII positioning: participant_oi.db (built by participant_oi_backfill.py)
  - NIFTY 50 daily close: NSE index-close archive (ind_close_all_DDMMYYYY.csv),
    fetched once into a `nifty_daily` table in the same DB.

ANTI-FOOL-YOURSELF DISCIPLINE
  - Predict the NEXT day's return only (no lookahead): signal known at EOD t,
    return measured t -> t+1.
  - Locked split: the SIGN of the edge (follow vs fade FII) is chosen on the
    in-sample window only; performance is reported on a never-touched OOS tail.
  - Round-trip transaction costs applied on every position change.
  - Minimum-trade guard: too few trades => result is declared noise.
  - Buy-&-hold over the same OOS window is shown as the benchmark to beat.

This is a MEASUREMENT tool. It places no orders and is isolated from live code.
A positive in-sample number means nothing; only the OOS line, net of costs and
above buy-&-hold, would justify wiring this into the signal engine.

USAGE
  python participant_oi_edge.py                 # default: signal=net change
  python participant_oi_edge.py --signal level  # use net level instead of change
  python participant_oi_edge.py --cost-bps 3 --oos-frac 0.3 --z-window 60
"""
from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("participant_oi_edge")

_DB_PATH = Path("participant_oi.db")
_HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
_IDX_PATH = "/content/indices/ind_close_all_{}.csv"
_REQUEST_GAP_SEC = 0.4


# ── NIFTY close backfill (into the same DB, idempotent) ─────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                      "Referer": "https://www.nseindia.com/", "Accept": "*/*"})
    try:
        from nse_proxy import apply as _apply; _apply(s)
    except Exception:
        pass
    try:
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s


def _fetch_nifty_close(d: date, session: requests.Session) -> Optional[float]:
    ds = d.strftime("%d%m%Y")
    for host in _HOSTS:
        try:
            r = session.get(host + _IDX_PATH.format(ds), timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                df = pd.read_csv(io.BytesIO(r.content))
                df.columns = [c.strip() for c in df.columns]
                row = df[df["Index Name"].astype(str).str.strip().str.upper() == "NIFTY 50"]
                if len(row):
                    return float(row["Closing Index Value"].iloc[0])
        except Exception as e:
            logger.debug("nifty %s %s: %s", d, host, e)
    return None


def ensure_nifty(conn: sqlite3.Connection, dates: List[str]) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS nifty_daily (date TEXT PRIMARY KEY, close REAL)")
    conn.commit()
    have = {r[0] for r in conn.execute("SELECT date FROM nifty_daily")}
    todo = [d for d in dates if d not in have]
    if not todo:
        return 0
    session = _session()
    n = 0
    for ds in todo:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        close = _fetch_nifty_close(d, session)
        if close is not None:
            conn.execute("INSERT OR REPLACE INTO nifty_daily (date, close) VALUES (?,?)",
                         (ds, close))
            n += 1
        time.sleep(_REQUEST_GAP_SEC)
        if n and n % 50 == 0:
            conn.commit(); logger.info("nifty fetched %d/%d", n, len(todo))
    conn.commit()
    return n


# ── data assembly ───────────────────────────────────────────────────────────--

def load_aligned(conn: sqlite3.Connection) -> pd.DataFrame:
    """date, fii_net (index futures), and NIFTY close + next-day return."""
    fii = pd.read_sql_query(
        "SELECT date, future_index_long - future_index_short AS fii_net "
        "FROM participant_oi WHERE client_type='FII' ORDER BY date", conn)
    # Client (retail) index-option positioning for the contrarian hypothesis.
    cli = pd.read_sql_query(
        "SELECT date, "
        "(opt_index_call_long - opt_index_call_short) "
        "- (opt_index_put_long - opt_index_put_short) AS client_skew "
        "FROM participant_oi WHERE client_type='Client' ORDER BY date", conn)
    nif = pd.read_sql_query("SELECT date, close FROM nifty_daily ORDER BY date", conn)
    df = (fii.merge(cli, on="date", how="inner")
             .merge(nif, on="date", how="inner")
             .sort_values("date").reset_index(drop=True))
    df["fwd_ret"] = df["close"].pct_change().shift(-1)      # return from t to t+1
    df["fii_net_chg"] = df["fii_net"].diff()
    df["client_skew_chg"] = df["client_skew"].diff()
    return df


def _zscore(s: pd.Series, window: int) -> pd.Series:
    m = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std()
    return (s - m) / sd.replace(0, np.nan)


def _metrics(daily_ret: np.ndarray) -> dict:
    daily_ret = daily_ret[~np.isnan(daily_ret)]
    if len(daily_ret) == 0:
        return {"n": 0}
    eq = np.cumprod(1 + daily_ret)
    peak = np.maximum.accumulate(eq)
    mdd = float(((eq - peak) / peak).min())
    ann = 252.0
    sharpe = float(np.mean(daily_ret) / np.std(daily_ret) * np.sqrt(ann)) if np.std(daily_ret) > 0 else 0.0
    return {"n": int(len(daily_ret)), "total_ret": float(eq[-1] - 1.0),
            "cagr": float(eq[-1] ** (ann / len(daily_ret)) - 1.0),
            "sharpe": round(sharpe, 2), "max_dd": round(mdd, 4),
            "hit_rate": round(float(np.mean(daily_ret > 0)), 3)}


# ── the test ────────────────────────────────────────────────────────────────--

def run_edge_test(signal_kind: str = "change", z_window: int = 60,
                  cost_bps: float = 2.0, oos_frac: float = 0.3,
                  min_trades: int = 30, entry_lag: int = 1) -> dict:
    conn = sqlite3.connect(str(_DB_PATH))
    poi_dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM participant_oi ORDER BY date")]
    if len(poi_dates) < 50:
        conn.close()
        return {"error": f"only {len(poi_dates)} participant-OI days — backfill first"}
    ensure_nifty(conn, poi_dates)
    df = load_aligned(conn)
    conn.close()

    # Participant-OI for day t is published ~8 PM, AFTER the close. So the
    # earliest tradeable entry is a later bar. entry_lag=1 => enter at close[t+1]
    # and hold to close[t+2] (lookahead-free). entry_lag=0 is the unrealistic
    # "trade at the same close" case, kept only for comparison.
    df["fwd_ret"] = df["close"].pct_change().shift(-(1 + entry_lag))

    _RAW = {"change": "fii_net_chg", "level": "fii_net",
            "client_skew": "client_skew", "client_skew_chg": "client_skew_chg"}
    raw = df[_RAW[signal_kind]]
    df["signal"] = _zscore(raw.astype(float), z_window)
    df = df.dropna(subset=["signal", "fwd_ret"]).reset_index(drop=True)
    if len(df) < 50:
        return {"error": f"only {len(df)} aligned rows after warmup"}

    split = int(len(df) * (1 - oos_frac))
    ins, oos = df.iloc[:split], df.iloc[split:]

    # Choose direction (follow vs fade) on IN-SAMPLE ONLY.
    ins_corr = float(np.corrcoef(ins["signal"], ins["fwd_ret"])[0, 1])
    side = 1.0 if ins_corr >= 0 else -1.0          # +1 follow FII, -1 fade FII

    cost = cost_bps / 10000.0
    def strat(frame: pd.DataFrame) -> Tuple[np.ndarray, int]:
        pos = side * np.sign(frame["signal"].to_numpy())       # -1/0/+1 each day
        ret = pos * frame["fwd_ret"].to_numpy()
        turn = np.abs(np.diff(np.concatenate([[0.0], pos])))   # position changes
        ret = ret - turn * cost
        trades = int((turn > 0).sum())
        return ret, trades

    oos_ret, oos_trades = strat(oos)
    ins_ret, _ = strat(ins)
    bh_oos = oos["fwd_ret"].to_numpy()

    spearman_full = float(df[["signal", "fwd_ret"]].corr(method="spearman").iloc[0, 1])

    result = {
        "signal_kind": signal_kind, "z_window": z_window, "cost_bps": cost_bps,
        "entry_lag": entry_lag,
        "rows": len(df), "in_sample": len(ins), "oos": len(oos),
        "in_sample_corr": round(ins_corr, 4),
        "chosen_side": "FOLLOW FII" if side > 0 else "FADE FII",
        "spearman_signal_vs_fwdret_full": round(spearman_full, 4),
        "oos_trades": oos_trades,
        "oos_strategy": _metrics(oos_ret),
        "oos_buy_hold": _metrics(bh_oos),
        "in_sample_strategy": _metrics(ins_ret),
    }
    s = result["oos_strategy"]; bh = result["oos_buy_hold"]
    verdict = []
    if oos_trades < min_trades:
        verdict.append(f"INSUFFICIENT TRADES ({oos_trades}<{min_trades}) — result is noise")
    if s.get("sharpe", 0) <= 0:
        verdict.append("OOS Sharpe <= 0 — NO edge")
    elif s.get("total_ret", 0) <= bh.get("total_ret", 0):
        verdict.append("OOS does not beat buy-&-hold — NO usable edge")
    else:
        verdict.append("OOS beats buy-&-hold net of costs — WORTH a closer look (not yet proven)")
    result["verdict"] = " | ".join(verdict)
    return result


def format_report(r: dict) -> str:
    if "error" in r:
        return f"participant_oi_edge: {r['error']}"
    L = [
        "── FII index-futures → next-day NIFTY ──────────────────────────────",
        f"signal={r['signal_kind']} (z{r['z_window']}), cost={r['cost_bps']}bps/side, entry_lag={r['entry_lag']}",
        f"rows={r['rows']}  in-sample={r['in_sample']}  oos={r['oos']}",
        f"in-sample corr(signal, fwd_ret)={r['in_sample_corr']}  → {r['chosen_side']}",
        f"Spearman(signal, fwd_ret) full sample = {r['spearman_signal_vs_fwdret_full']}",
        f"OOS trades: {r['oos_trades']}",
        f"  OOS strategy : {r['oos_strategy']}",
        f"  OOS buy&hold : {r['oos_buy_hold']}",
        f"  in-sample str: {r['in_sample_strategy']}",
        f"VERDICT: {r['verdict']}",
    ]
    return "\n".join(L)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Test FII futures positioning vs NIFTY.")
    p.add_argument("--signal", choices=["change", "level", "client_skew",
                                        "client_skew_chg"], default="change")
    p.add_argument("--z-window", type=int, default=60)
    p.add_argument("--cost-bps", type=float, default=2.0)
    p.add_argument("--oos-frac", type=float, default=0.3)
    p.add_argument("--min-trades", type=int, default=30)
    p.add_argument("--entry-lag", type=int, default=1,
                   help="bars between signal and entry (1=realistic, OI is post-close)")
    a = p.parse_args(argv)
    r = run_edge_test(a.signal, a.z_window, a.cost_bps, a.oos_frac,
                      a.min_trades, a.entry_lag)
    print(format_report(r))


if __name__ == "__main__":
    main()
