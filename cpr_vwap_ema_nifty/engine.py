"""
Indicator frame, day features and trade simulation for CPR_VWAP_EMA_Nifty.

Bar convention: bars are labelled by their START time (09:15 = 09:15-09:20).
A signal is evaluated on a bar's CLOSE and filled at the NEXT bar's OPEN.

No lookahead:
  * 5m EMAs at bar t use closes up to and including bar t.
  * 1m 200 EMA at 5m bar t = value after the 1m bar starting t+4 (closes t+5).
  * 30m 200 EMA at 5m bar t = EMA of the last 30m bar that ENDED at or before
    t+5 (30m bars start 09:15, 09:45, ..., 15:15).
  * VWAP at bar t = cumulative (typical price x volume) of the futures bars
    09:15..t of that session.
  * Levels come from levels.build_day_levels (prior periods only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import levels as lv

FIVE = pd.Timedelta(minutes=5)


def resample(spot_1m: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = spot_1m.resample(rule, origin="start_day", offset="15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return out.dropna()


def build_frame(spot_1m: pd.DataFrame, fut_5m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """5m spot bars + every indicator needed by the rules, aligned per bar."""
    ind = cfg["indicators"]
    m5 = resample(spot_1m, "5min")
    for n in ind["ema_5m"]:
        m5[f"ema{n}_5m"] = m5["close"].ewm(span=n, adjust=False).mean()
    e1 = {n: spot_1m["close"].ewm(span=n, adjust=False).mean() for n in ind["ema_1m"]}
    probe = m5.index + pd.Timedelta(minutes=4)
    for n, s in e1.items():
        m5[f"ema{n}_1m"] = s.reindex(s.index.union(probe)).ffill().reindex(probe).to_numpy()
    m30 = resample(spot_1m, "30min")
    m30_end = m30.index + pd.Timedelta(minutes=30)
    m30_end = m30_end.where(m30_end.time != pd.Timestamp("15:45").time(),
                            m30_end - pd.Timedelta(minutes=15))  # 15:15 bar ends 15:30
    for n in ind["ema_30m"]:
        e = m30["close"].ewm(span=n, adjust=False).mean()
        ser = pd.Series(e.to_numpy(), index=m30_end).sort_index()
        ser = ser[~ser.index.duplicated(keep="last")]
        close_t = m5.index + FIVE
        m5[f"ema{n}_30m"] = ser.reindex(ser.index.union(close_t)).ffill().reindex(close_t).to_numpy()
        cser = pd.Series(m30["close"].to_numpy(), index=m30_end)
        cser = cser[~cser.index.duplicated(keep="last")].sort_index()
        m5[f"close_30m"] = cser.reindex(cser.index.union(close_t)).ffill().reindex(close_t).to_numpy()

    # Futures VWAP per session; spot TWAP as the labelled fallback.
    m5["date"] = m5.index.normalize()
    tp_spot = (m5["high"] + m5["low"] + m5["close"]) / 3
    m5["twap"] = tp_spot.groupby(m5["date"]).cumsum() / m5.groupby("date").cumcount().add(1)
    if fut_5m is not None and not fut_5m.empty:
        f = fut_5m.copy()
        f["date"] = f.index.normalize()
        tpv = (f["high"] + f["low"] + f["close"]) / 3 * f["volume"]
        f["vwap"] = tpv.groupby(f["date"]).cumsum() / f["volume"].groupby(f["date"]).cumsum()
        m5["fut_close"] = f["close"].reindex(m5.index)
        m5["fut_vwap"] = f["vwap"].reindex(m5.index)
    else:
        m5["fut_close"] = np.nan
        m5["fut_vwap"] = np.nan
    return m5


def futures_day_ok(fut_5m: pd.DataFrame, min_bars: int) -> Dict[pd.Timestamp, bool]:
    if fut_5m is None or fut_5m.empty:
        return {}
    g = fut_5m.groupby(fut_5m.index.normalize())
    n = g.size()
    v = g["volume"].sum()
    return {d: bool(n[d] >= min_bars and v[d] > 0) for d in n.index}


def day_type(day_bars: pd.DataFrame, ratio: float) -> str:
    rng = day_bars["high"].max() - day_bars["low"].min()
    body = abs(day_bars["close"].iloc[-1] - day_bars["open"].iloc[0])
    return "trending" if rng > 0 and body >= ratio * rng else "range"


SETUP_KEYS = ("cpr_width", "cpr_vs_prior_day", "cpr_vs_weekly", "cpr_vs_monthly",
              "open_vs_cpr", "open_vs_daily_h3", "open_vs_weekly_h3")


def matches_setup(level_row: pd.Series, setup: Dict[str, str]) -> bool:
    return all(str(level_row.get(k)) == str(v) for k, v in setup.items() if k in SETUP_KEYS)


@dataclass
class Trade:
    date: str
    side: str
    entry_time: str
    entry_px: float
    exit_time: str
    exit_px: float
    exit_reason: str
    points: float
    mfe: float
    mae: float
    hold_min: float
    vwap_source: str
    extra: Dict[str, float] = field(default_factory=dict)


def _ctx_ok(bar: pd.Series, context: Dict[str, bool], sign: int, vwap_px: float, vwap: float) -> bool:
    checks = {
        "above_vwap": vwap_px > vwap,
        "above_ema20_5m": bar.close > bar.get("ema20_5m", np.nan),
        "above_ema50_5m": bar.close > bar.get("ema50_5m", np.nan),
        "above_ema200_5m": bar.close > bar.get("ema200_5m", np.nan),
        "above_ema200_1m": bar.close > bar.get("ema200_1m", np.nan),
    }
    for key, want in context.items():
        if key not in checks:
            continue
        ok = checks[key] if sign > 0 else not checks[key]
        if bool(want) != bool(ok):
            return False
    return True


def simulate_day(day: pd.DataFrame, lev: pd.Series, cond: dict, filters: List[str], cfg: dict,
                 vwap_mode: str) -> Optional[Trade]:
    """One trade max per session. vwap_mode: 'futures' or 'twap' (fallback)."""
    sess = cfg["session"]
    sign = 1 if str(cond.get("bias", "LONG")).upper() == "LONG" else -1
    ema_exit = f"ema{int(cond['exit'].get('period', 20))}_{cond['exit'].get('timeframe', '5m')}"
    t_first = pd.Timestamp(sess["first_signal_bar"]).time()
    t_last = pd.Timestamp(sess["last_signal_bar"]).time()
    t_force = pd.Timestamp(sess["force_exit_bar"]).time()
    idx = list(day.index)
    entry_i = None
    for i, t in enumerate(idx[:-1]):
        if not (t_first <= t.time() <= t_last):
            continue
        bar = day.iloc[i]
        if vwap_mode == "futures":
            px, vw = bar.fut_close, bar.fut_vwap
        else:
            px, vw = bar.close, bar.twap
        if pd.isna(px) or pd.isna(vw):
            continue
        if not ((px > vw) if sign > 0 else (px < vw)):
            continue
        if not _ctx_ok(bar, cond.get("context", {}), sign, px, vw):
            continue
        if "ema200_30m" in filters and not (sign * (bar.close_30m - bar.ema200_30m) > 0):
            continue
        if "r1_pdh_break" in filters:
            lvl = max(lev.R1, lev.PDH) if sign > 0 else min(lev.S1, lev.PDL)
            if not (sign * (bar.close - lvl) > 0):
                continue
        if "pwh_break" in filters:
            lvl = lev.PWH if sign > 0 else lev.PWL
            if not (sign * (bar.close - lvl) > 0):
                continue
        entry_i = i + 1
        break
    if entry_i is None or idx[entry_i].time() >= t_force:
        return None

    entry_px = float(day.iloc[entry_i].open)
    target = None
    if "target_r4_mh3" in filters:
        cands = [lev.R4, lev.MH3] if sign > 0 else [lev.S4, lev.ML3]
        cands = [c for c in cands if sign * (c - entry_px) > 0]
        target = (min(cands) if sign > 0 else max(cands)) if cands else None
    mfe = mae = 0.0
    exit_px, exit_t, reason = None, None, None
    for j in range(entry_i, len(idx)):
        bar = day.iloc[j]
        if idx[j].time() >= t_force:
            exit_px, exit_t, reason = float(bar.open), idx[j], "force_exit"
            break
        fav = (bar.high - entry_px) if sign > 0 else (entry_px - bar.low)
        adv = (entry_px - bar.low) if sign > 0 else (bar.high - entry_px)
        if target is not None and sign * ((bar.high if sign > 0 else bar.low) - target) >= 0:
            fill = max(target, bar.open) if sign > 0 else min(target, bar.open)
            mfe, mae = max(mfe, fav), max(mae, adv)
            exit_px, exit_t, reason = float(fill), idx[j], "target_r4_mh3"
            break
        mfe, mae = max(mfe, fav), max(mae, adv)
        e = bar.get(ema_exit, np.nan)
        if not pd.isna(e) and sign * (bar.close - e) < 0:
            if j + 1 < len(idx):
                exit_px, exit_t, reason = float(day.iloc[j + 1].open), idx[j + 1], "close_vs_ema"
            else:
                exit_px, exit_t, reason = float(bar.close), idx[j] + FIVE, "close_vs_ema_eod"
            break
    if exit_px is None:
        exit_px, exit_t, reason = float(day.iloc[-1].close), idx[-1] + FIVE, "data_end"
    return Trade(
        date=idx[0].strftime("%Y-%m-%d"), side="LONG" if sign > 0 else "SHORT",
        entry_time=idx[entry_i].strftime("%Y-%m-%d %H:%M"), entry_px=entry_px,
        exit_time=exit_t.strftime("%Y-%m-%d %H:%M"), exit_px=exit_px, exit_reason=reason,
        points=round(sign * (exit_px - entry_px), 2), mfe=round(mfe, 2), mae=round(mae, 2),
        hold_min=(exit_t - idx[entry_i]).total_seconds() / 60.0,
        vwap_source="futures_vwap" if vwap_mode == "futures" else "spot_twap_fallback",
    )


def day_levels(spot_1m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    daily = lv.session_ohlc(spot_1m)
    w = cfg["cpr_width_pct"]
    return lv.build_day_levels(daily, w["narrow_below"], w["wide_above"])
