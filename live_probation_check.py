#!/usr/bin/env python3
"""
live_probation_check.py

Run:
    python live_probation_check.py
"""

from __future__ import annotations

import argparse
import json
from typing import Iterable

from live_probation import get_probation_status, probation_preflight


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-option-premium", type=float, default=20.0)
    parser.add_argument("--sample-equity-price", type=float, default=500.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    preflight = probation_preflight(
        sample_option_premium=args.sample_option_premium,
        sample_equity_price=args.sample_equity_price,
    )
    status = get_probation_status()
    payload = {"preflight": preflight, "status": status}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("LIVE PROBATION CHECK")
        print("=" * 48)
        print(f"enabled={preflight['enabled']} real={preflight['enable_real_trading']} paper={preflight['paper_trading']}")
        print(f"max_capital=₹{preflight['max_capital']:.0f} max_lots={preflight['max_lots']}")
        print(f"sample option lot cost=₹{preflight['sample_option_min_cost']:.0f}")
        print(f"today trades={status['trades_today']}/{status['max_trades_per_day']} pnl={status['daily_pnl']} locked={status['loss_locked']}")
        if preflight["warnings"]:
            print("WARN " + ", ".join(preflight["warnings"]))
        else:
            print("PASS probation preflight ok")
    return 0 if preflight.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
