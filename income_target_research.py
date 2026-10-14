"""Read-only, exploratory strategy selection for a monthly income target.

This report never authorizes execution or edits an existing frozen experiment.
Signal R values are underlying proxies, not executable option P&L. Already-seen
historical comparisons are descriptive, not a new untouched holdout.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def target_requirements(capital=500000.0, monthly_target=30000.0):
    if not all(finite(value) is not None and value > 0 for value in (capital, monthly_target)):
        raise ValueError("capital and target must be finite positive numbers")
    rows = []
    for risk_fraction in (.0025, .005):
        risk = capital * risk_fraction
        for count in (10, 20, 40, 60):
            rows.append({"risk_fraction": risk_fraction, "planned_risk_rupees": risk,
                         "monthly_trades": count,
                         "required_average_net_r": monthly_target / (risk * count)})
    return {"capital": capital, "monthly_target": monthly_target,
            "required_monthly_return": monthly_target / capital,
            "before_personal_income_tax": True, "scenarios": rows,
            "warning": "Risk-unit arithmetic, not a return forecast; stop orders do not cap gap losses."}


def read_signal_snapshot(db_path, as_of):
    path = Path(db_path)
    if not path.exists():
        return [], {"reason": "signal_db_missing"}
    cutoff = date.fromisoformat(as_of)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        raw = conn.execute(
            """SELECT id,log_time,signal_date,signal_time,lower(strategy) strategy,
                      symbol,upper(side) side,entry_price,stop_loss,target,rr,
                      tb_label,tb_r_multiple_net,outcome_time
                 FROM signal_log WHERE training_eligible=1 AND signal_date<=?
                 ORDER BY log_time,id""", (as_of,),
        ).fetchall()
    earliest = {}
    days = set()
    invalid = 0
    for raw_row in raw:
        row = dict(raw_row)
        try:
            day = date.fromisoformat(row["signal_date"])
        except (TypeError, ValueError):
            invalid += 1
            continue
        prices = [finite(row.get(key)) for key in ("entry_price", "stop_loss", "target", "rr")]
        if any(value is None or value <= 0 for value in prices) or row["side"] not in {"BUY", "SELL"}:
            invalid += 1
            continue
        entry, stop, target, _rr = prices
        if not ((row["side"] == "BUY" and stop < entry < target) or
                (row["side"] == "SELL" and target < entry < stop)):
            invalid += 1
            continue
        days.add(day.isoformat())
        key = (row["strategy"], row["symbol"], row["side"], row["signal_date"])
        # Choose the first structural setup before inspecting its outcome.
        # A pending first setup cannot be replaced with a later winner.
        earliest.setdefault(key, row)
    completed, pending = [], 0
    for row in earliest.values():
        value = finite(row["tb_r_multiple_net"])
        if row["tb_label"] not in (-1, 0, 1) or value is None:
            pending += 1
            continue
        outcome = row.get("outcome_time")
        if outcome:
            try:
                dt = datetime.fromisoformat(str(outcome).replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(IST)
                if dt.date() > cutoff:
                    pending += 1
                    continue
            except ValueError:
                pass
        row["net_r"] = value  # Include zero returns and stop-label 0.
        completed.append(row)
    fingerprint = hashlib.sha256(json.dumps([dict(row) for row in raw], sort_keys=True,
                                             default=str).encode()).hexdigest()
    return completed, {"raw_training_rows": len(raw), "invalid_structural_rows": invalid,
                       "distinct_setups": len(earliest), "completed": len(completed),
                       "pending_or_invalid_outcomes": pending, "observed_dates": sorted(days),
                       "data_snapshot_sha256": fingerprint}


def summarize(values):
    if not values:
        return {"n": 0, "mean_net_r": None, "profit_factor": None, "positive_rate": None}
    positive = sum(value for value in values if value > 0)
    negative = -sum(value for value in values if value < 0)
    return {"n": len(values), "mean_net_r": statistics.mean(values),
            "profit_factor": positive / negative if negative else None,
            "positive_rate": sum(value > 0 for value in values) / len(values)}


def rank_strategies(rows, observed_dates, *, as_of, recent_days=10, limit=3):
    recent = set(observed_dates[-recent_days:])
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["strategy"]].append(row)
    ranked = []
    for name, items in grouped.items():
        all_stats = summarize([row["net_r"] for row in items])
        recent_rows = [row for row in items if row["signal_date"] in recent]
        recent_stats = summarize([row["net_r"] for row in recent_rows])
        active = len({row["signal_date"] for row in items})
        last_day = max(row["signal_date"] for row in items)
        fresh = (date.fromisoformat(as_of) - date.fromisoformat(last_day)).days <= 14
        average = all_stats["mean_net_r"]
        latest = recent_stats["mean_net_r"]
        by_month = defaultdict(list)
        by_day = defaultdict(list)
        for row in items:
            by_month[row["signal_date"][:7]].append(row["net_r"])
            by_day[row["signal_date"]].append(row["net_r"])
        daily = [statistics.mean(by_day[day]) for day in sorted(by_day)]
        equal_day_mean = statistics.mean(daily)
        without_best_day = statistics.mean(sorted(daily)[:-1]) if len(daily) > 1 else None
        pf = all_stats["profit_factor"]
        # A near-zero trade mean, one crowded winning session, or a recent
        # reversal is insufficient even for this exploratory paper shortlist.
        if (all_stats["n"] >= 30 and active >= 8 and average >= .05
                and (pf is None or pf >= 1.2) and equal_day_mean > 0
                and without_best_day is not None and without_best_day > 0
                and recent_stats["n"] >= 10 and latest is not None and latest > 0 and fresh):
            status = "EXPLORATORY_PAPER_CANDIDATE"
        elif average <= 0 and all_stats["n"] >= 20:
            status = "NEGATIVE_AFTER_COST"
        elif average > 0:
            status = "REVIEW_ONLY_RECENT_OR_SAMPLE_GATE_FAILED"
        else:
            status = "INSUFFICIENT_EVIDENCE"
        # Day-resampled interval is descriptive: it does not correct the
        # search across strategies or establish a validated positive edge.
        interval = None
        if len(daily) >= 2:
            import numpy as np
            rng = np.random.default_rng(20261014)
            samples = rng.choice(daily, size=(2000, len(daily)), replace=True).mean(axis=1)
            interval = [float(value) for value in np.quantile(samples, [.025, .975])]
        ranked.append({"strategy": name, "status": status, **all_stats,
                       "active_days": active, "last_day": last_day,
                       "recent": recent_stats, "recent_system_days": sorted(recent),
                       "equal_active_day_mean_net_r": equal_day_mean,
                       "equal_day_mean_excluding_best_day": without_best_day,
                       "day_bootstrap_ci95_descriptive": interval,
                       "monthly_proxy_net_r": {month: sum(values) for month, values in sorted(by_month.items())},
                       "live_approved": False, "income_target_supported": False})
    ranked.sort(key=lambda row: (row["status"] == "EXPLORATORY_PAPER_CANDIDATE",
                                 row["mean_net_r"], row["n"]), reverse=True)
    shortlist = [row for row in ranked if row["status"] == "EXPLORATORY_PAPER_CANDIDATE"][:limit]
    return ranked, shortlist


def funded_model_replay(trades, capital=500000, monthly_target=30000):
    """Sequential cash check on a long-option pricing MODEL, never a live claim."""
    equity, peak, max_dd = capital, capital, 0.0
    funded, skipped = [], 0
    months = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0})
    for trade in sorted(trades, key=lambda row: (row["entry_date"], row["entry_time"])):
        debit = trade["entry_premium"] * trade["qty"]
        if not all(finite(value) is not None for value in (debit, trade["cost"], trade["pnl"])) or debit <= 0 or trade["qty"] <= 0 or trade["cost"] < 0:
            skipped += 1
            continue
        # Round-trip costs reserved as an intentionally conservative buffer.
        if equity <= 0 or debit + trade["cost"] > equity:
            skipped += 1
            continue
        equity += trade["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        bucket = months[trade["entry_date"][:7]]
        bucket["trades"] += 1
        bucket["net_pnl"] += trade["pnl"]
        funded.append(trade)
    return {"price_evidence": "EOD_anchored_Black_Scholes_model_NOT_intraday_quotes",
            "funded_model_trades": len(funded), "skipped_unfunded_or_invalid": skipped,
            "initial_capital": capital, "final_model_equity": round(equity, 2),
            "total_model_net_pnl": round(equity - capital, 2),
            "max_model_closed_trade_drawdown": round(max_dd, 2),
            "monthly": {month: {**bucket, "net_pnl": round(bucket["net_pnl"], 2),
                                "target_met": bucket["net_pnl"] >= monthly_target}
                        for month, bucket in sorted(months.items())},
            "income_target_supported": False,
            "limits": ["Retrospective exploratory sizing and exit rules", "Partial months and missing pricing days",
                       "Uniform 65-unit lot assumption, not a historical lot schedule",
                       "No executable bid/ask validation", "Closed-trade drawdown omits intratrade mark-to-market"]}


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def build_report(*, db_path="signal_log.db", as_of=None, capital=500000, monthly_target=30000,
                 research_dir="reports/income_target_research"):
    as_of = as_of or datetime.now(IST).date().isoformat()
    target = target_requirements(capital, monthly_target)
    rows, coverage = read_signal_snapshot(db_path, as_of)
    ranked, shortlist = rank_strategies(rows, coverage.get("observed_dates", []), as_of=as_of)
    manifest = load_json("live_eligibility.json")
    validation = load_json("validation_results.json").get("results", {})
    model_runs = {}
    for tag in ("exploratory", "stress"):
        path = Path(research_dir) / f"bollinger_1lot_{tag}.json"
        model = load_json(path)
        if model:
            model_runs[tag] = {"source_file": str(path),
                               "summary": {key: value for key, value in model.items() if key != "trades"},
                               "capital_replay": funded_model_replay(model.get("trades", []), capital, monthly_target)}
    return {"as_of": as_of, "mode": "RESEARCH_ONLY", "target": target, "coverage": coverage,
            "historical_search_is_exploratory": True, "historical_data_is_not_an_untouched_holdout": True,
            "scope": {"signal_strategies_with_clean_outcomes": len(ranked),
                      "live_manifest_strategies": manifest.get("total_strategies"),
                      "live_ready_count": manifest.get("live_ready_count", 0)},
            "ranked_signal_strategies": ranked, "paper_shortlist": shortlist,
            "option_bollinger_model_runs": model_runs,
            "rigorous_validation": {name: {key: row.get(key) for key in (
                "verdict", "dev_windows", "holdout_pnl", "holdout_trades", "stability_ok", "deflated_sharpe")}
                for name, row in validation.items() if isinstance(row, dict)},
            "other_tracks": {"pairs": load_json("pairs_validation_report.json").get("passed"),
                             "option_flow_candidates": len(load_json("option_cohort_edge_report.json").get("candidates", [])),
                             "frozen_runtime_cohort": load_json("frozen_strategy_cohort.json").get("strategies", [])},
            "live_approved": False, "income_target_supported": False,
            "next_action": "Freeze this small research shortlist for future executable evidence; do not fund it from this report."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="signal_log.db")
    parser.add_argument("--as-of")
    parser.add_argument("--capital", type=float, default=500000)
    parser.add_argument("--monthly-target", type=float, default=30000)
    parser.add_argument("--output-dir", default="reports/income_target_research")
    args = parser.parse_args()
    report = build_report(db_path=args.db, as_of=args.as_of, capital=args.capital,
                          monthly_target=args.monthly_target, research_dir=args.output_dir)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps({"scope": report["scope"], "paper_shortlist": [row["strategy"] for row in report["paper_shortlist"]],
                      "income_target_supported": False, "report": str(output / "summary.json")}, indent=2))


if __name__ == "__main__":
    main()
