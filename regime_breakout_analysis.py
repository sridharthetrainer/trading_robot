"""
regime_breakout_analysis.py

Diagnostic follow-up to the breakout strategy's walk-forward FAIL verdict in
extended_validation_report.json. Every breakout window (NIFTY + BANKNIFTY,
both source segments) is profitable in Sharpe terms, but parameter_stability_cv
sits at 0.68-0.78 (needs <0.5) -- the optimal channel_period/stop_atr_mult
etc. swing around a lot window to window. That's the signature of a strategy
whose edge is real but regime-dependent, not absent.

This script re-labels each already-computed walk-forward window with the
market regime that prevailed going INTO it (classify_regime() from
market_regime.py, fed only trailing daily bars ending at test_start -- no
lookahead into the test window itself), then checks whether restricting to
one regime tightens parameter_stability_cv below the 0.5 bar.

VIX is only available from 2026-06-24 onward (vix_history.csv); windows
before that fall back to classify_regime's own default (15.0, i.e. "normal").
That weakens the HIGH_VOL / LOW_VOL_CHOP calls for older windows specifically
-- flagged in the output, not hidden. The ADX/R^2/ATR-expansion legs of the
classifier (STRONG_TREND / BREAKOUT / MEAN_REVERT / WEAK_TREND) don't depend
on VIX at all and are unaffected.

This is exploratory grouping, not a new formal validation pass -- it does not
recompute deflated_sharpe_ratio per regime subset (the per-regime trial counts
are too small for that correction to mean anything), so no verdict here can
promote a strategy to live. It can only tell us whether regime-conditioning
is worth building into validation_harness properly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from market_regime import classify_regime
from run_extended_validation import load_labeled_history, split_into_segments
from validation_harness import parameter_stability

REPORT_PATH = "extended_validation_report.json"
VIX_PATH = "vix_history.csv"
REGIME_LOOKBACK_DAYS = 60  # trailing daily bars fed to classify_regime


def _load_vix_lookup() -> dict:
    if not Path(VIX_PATH).exists():
        return {}
    df = pd.read_csv(VIX_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return dict(zip(df["date"], df["vix"]))


def _segment_daily_bars(symbol: str, segments: list, source: str, start: str, end: str):
    # match on source + overlapping date range (segment_start/end in the
    # report are str(Timestamp); allow a day of slack for formatting drift)
    start_ts = pd.Timestamp(start)
    # Match on source + start only: the live_broker segment keeps growing as
    # more candles accrue since the report was generated (its end date drifts
    # forward), but a segment's start is fixed once split.
    for seg in segments:
        if seg["source"] != source:
            continue
        if abs((pd.Timestamp(seg["start"]) - start_ts).total_seconds()) < 3600 * 24:
            daily = seg["data"].resample("1D").agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            ).dropna()
            return daily
    return None


def main():
    report = json.load(open(REPORT_PATH))
    vix_lookup = _load_vix_lookup()

    segments_by_symbol = {}
    for symbol in ("NIFTY", "BANKNIFTY"):
        df = load_labeled_history(symbol)
        segments_by_symbol[symbol] = split_into_segments(df) if not df.empty else []

    rows = []
    vix_coverage_missing = 0
    for key, result in report["results"].items():
        if "__breakout__" not in key:
            continue
        symbol = result["symbol"]
        source = result.get("segment_source")
        seg_start = result.get("segment_start")
        seg_end = result.get("segment_end")
        if not source or not result.get("dev_windows_detail"):
            continue

        daily = _segment_daily_bars(symbol, segments_by_symbol.get(symbol, []), source, seg_start, seg_end)
        if daily is None or daily.empty:
            print(f"SKIP {key}: could not locate matching segment daily bars")
            continue

        for w in result["dev_windows_detail"]:
            test_start = pd.Timestamp(w["test_start"])
            trailing = daily[daily.index < test_start].tail(REGIME_LOOKBACK_DAYS)
            if len(trailing) < 30:
                continue
            vix_val = vix_lookup.get(test_start.date())
            if vix_val is None:
                vix_coverage_missing += 1
                vix_val = 15.0
            regime, conf, _ = classify_regime(trailing, vix=vix_val)
            rows.append({
                "symbol": symbol, "source": source, "regime": regime,
                "confidence": conf, "sharpe": w["sharpe"], "pnl": w["pnl"],
                "win_rate": w["win_rate"], "best_params": w["best_params"],
                "vix_was_real": vix_val != 15.0 or (test_start.date() in vix_lookup),
            })

    if not rows:
        print("No windows could be regime-labelled -- aborting.")
        return

    df = pd.DataFrame(rows)
    print(f"Labelled {len(df)} breakout windows across {df['symbol'].nunique()} symbol(s).")
    print(f"Windows with real VIX (>= 2026-06-24): {(df['vix_was_real']).sum()} / {len(df)}")
    print()

    print(f"{'Regime':<14}{'N':>4}{'AvgSharpe':>11}{'PctProfit':>11}{'ParamCV':>10}")
    print("-" * 50)
    for regime, g in df.groupby("regime"):
        avg_sharpe = g["sharpe"].mean()
        pct_profit = (g["pnl"] > 0).mean()
        cv = parameter_stability(list(g["best_params"]))
        flag = "  <-- stable + profitable" if cv < 0.5 and avg_sharpe > 0 else ""
        print(f"{regime:<14}{len(g):>4}{avg_sharpe:>11.3f}{pct_profit:>11.1%}{cv:>10.3f}{flag}")

    print()
    print("Unconditioned (all regimes pooled), for comparison:")
    cv_all = parameter_stability(list(df["best_params"]))
    print(f"{'ALL':<14}{len(df):>4}{df['sharpe'].mean():>11.3f}{(df['pnl']>0).mean():>11.1%}{cv_all:>10.3f}")

    out = {
        "generated_from": REPORT_PATH,
        "n_windows_labelled": len(rows),
        "vix_real_coverage": int((df["vix_was_real"]).sum()),
        "by_regime": [],
    }
    for regime, g in df.groupby("regime"):
        out["by_regime"].append({
            "regime": regime, "n_windows": len(g),
            "avg_sharpe": round(float(g["sharpe"].mean()), 4),
            "pct_profitable": round(float((g["pnl"] > 0).mean()), 4),
            "parameter_stability_cv": round(float(parameter_stability(list(g["best_params"]))), 4),
            "stability_ok": bool(parameter_stability(list(g["best_params"])) < 0.5),
        })
    Path("regime_breakout_analysis_report.json").write_text(json.dumps(out, indent=2))
    print("\nSaved regime_breakout_analysis_report.json")


if __name__ == "__main__":
    main()
