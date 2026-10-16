"""
CLI for CPR_VWAP_EMA_Nifty (research-only strategy).

  python -m cpr_vwap_ema_nifty fetch [--force]        download spot 1m + futures 5m into own cache
  python -m cpr_vwap_ema_nifty backtest [--no-options]
  python -m cpr_vwap_ema_nifty add-condition FILE.json   validate, duplicate-check, append, re-run backtest
  python -m cpr_vwap_ema_nifty check-condition FILE.json duplicate/conflict check only (no write)
  python -m cpr_vwap_ema_nifty status                 config flags + data coverage
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import backtest as bt
from . import data as dm
from . import rules as rl


def _short(summary: dict) -> str:
    out = [f"Run {summary['generated_at']} | sessions {summary['data']['sessions_in_window']} | "
           f"missing futures volume {summary['data']['sessions_missing_futures_volume']}"]
    for cid, c in summary["conditions"].items():
        base = c["variants"].get("base|futures", {})
        out.append(f"{cid}: setup-matched sessions {c['setup_matched_sessions']}")
        for s in ("matched", "non_matched"):
            out.append(f"  {s:12s} {bt._fmt(base.get(s, {}))}")
    out.append("Full report: cpr_vwap_ema_nifty/results/latest/summary.md")
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cpr_vwap_ema_nifty")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--force", action="store_true")
    b = sub.add_parser("backtest")
    b.add_argument("--no-options", action="store_true")
    a = sub.add_parser("add-condition")
    a.add_argument("file")
    c = sub.add_parser("check-condition")
    c.add_argument("file")
    sub.add_parser("status")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("smartConnect", "angel", "urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = bt.load_config()

    if args.cmd == "fetch":
        w = cfg["window"]
        end = date.today()
        for series in ("fut_5m", "spot_1m"):
            print(series, dm.fetch(series, date.fromisoformat(w["warmup_start"]), end, cfg, force=args.force))
        return 0
    if args.cmd == "status":
        print(json.dumps({"enabled_live": cfg["enabled_live"], "research_only": cfg["research_only"],
                          "live_active": bt.is_enabled_live(cfg),
                          "conditions": [x["id"] for x in rl.load()["conditions"]],
                          "coverage": dm.coverage(cfg["window"]["warmup_start"], cfg["window"]["end"])}, indent=2))
        return 0
    if args.cmd == "check-condition":
        cond = json.loads(open(args.file).read())
        print(json.dumps(rl.check_new(cond, rl.load()["conditions"]), indent=2))
        return 0
    if args.cmd == "add-condition":
        cond = json.loads(open(args.file).read())
        res = rl.add_condition(cond)
        print(json.dumps(res, indent=2))
        if res["status"] == "duplicate":
            print(f"SKIPPED: duplicate of {res['of']} (setup, context and outcome all match).")
            return 0
        if res["status"] in {"invalid", "id_taken"}:
            return 2
        if res["status"] == "conflict":
            print(f"FLAGGED CONFLICT with {res['with']}: same setup, different outcome. Added with conflict_with set.")
        print(_short(bt.run_and_save(cfg)))
        print("Comparison vs previous run: see 'Change vs previous run' in summary.md")
        return 0
    if args.cmd == "backtest":
        print(_short(bt.run_and_save(cfg, with_options=not args.no_options)))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
