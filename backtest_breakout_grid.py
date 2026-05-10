"""
backtest_breakout_grid.py

Robust grid search for backtest_breakout.backtest_breakout()

Improvements
------------
- Fully aligned with backtest_breakout() signature
- Saves best params automatically to JSON
- Saves all results to CSV
- Logs failed/invalid combinations
- Uses a composite score instead of raw P&L only
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import sys
from typing import Dict, List, Tuple

import pandas as pd
from tqdm import tqdm

from angel import AngelOne
from data_fetcher import DataFetcher
from backtest_breakout import backtest_breakout

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

RESULTS_CSV = "grid_search_results_breakout.csv"
BEST_PARAMS_JSON = "best_params_breakout.json"
FAILED_PARAMS_CSV = "grid_search_failed_breakout.csv"


def build_param_grid() -> Dict:
    return {
        "channel_period": [10, 20, 30, 40],
        "adx_threshold": [None, 18, 20, 25],
        "min_atr_ratio": [0.0, 0.001],
        "max_atr_ratio": [0.015, 0.02, 1.0],
        "stop_atr_mult": [1.5, 2.0, 2.5],
        "trail_atr_mult": [1.0, 1.5, 2.0],
        "trail_activate": [0.0, 0.5, 1.0],
        "profit_target_atr_mult": [None, 3.0, 4.0],
        "exit_on_opposite_breakout": [True, False],
    }


def build_fixed_params() -> Dict:
    return {
        "lot_multiplier": 1,
        "initial_capital": 100000.0,
        "brokerage_per_order": 20.0,
        "close_at_end": True,
        "verbose": False,
    }


def validate_param_combo(params: Dict) -> Tuple[bool, str]:
    if params["channel_period"] <= 1:
        return False, "channel_period_must_be_greater_than_1"

    if params["min_atr_ratio"] > params["max_atr_ratio"]:
        return False, "min_atr_ratio_greater_than_max_atr_ratio"

    if params["stop_atr_mult"] <= 0:
        return False, "stop_atr_mult_must_be_positive"

    if params["trail_atr_mult"] < 0:
        return False, "trail_atr_mult_must_be_non_negative"

    if params["trail_activate"] < 0:
        return False, "trail_activate_must_be_non_negative"

    return True, ""


def compute_score(result: Dict) -> float:
    pnl = float(result["total_pnl"])
    sharpe = float(result["sharpe"])
    win_rate = float(result["win_rate"])
    max_dd = float(result["max_drawdown"])
    num_trades = int(result["num_trades"])

    if num_trades == 0:
        return -1e15
    if num_trades < 5:
        trade_penalty = 50000.0
    elif num_trades < 10:
        trade_penalty = 15000.0
    else:
        trade_penalty = 0.0

    score = (
        pnl
        + (sharpe * 5000.0)
        + (win_rate * 3000.0)
        - (max_dd * 0.8)
        - trade_penalty
    )
    return float(score)


def save_best_params(best_params: Dict, best_result: Dict, symbol: str) -> None:
    payload = {
        "symbol": symbol,
        "strategy": "breakout",
        "params": best_params,
        "metrics": {
            "total_pnl": best_result["total_pnl"],
            "num_trades": best_result["num_trades"],
            "win_rate": best_result["win_rate"],
            "sharpe": best_result["sharpe"],
            "max_drawdown": best_result["max_drawdown"],
            "final_capital": best_result["final_capital"],
            "score": best_result["score"],
        },
    }

    with open(BEST_PARAMS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("Best breakout parameters saved to %s", BEST_PARAMS_JSON)


def fetch_backtest_data(symbol: str, days: int = 30, interval: str = "5m") -> pd.DataFrame:
    dummy_angel = AngelOne("", "", "", "", paper_trade=True)
    fetcher = DataFetcher(dummy_angel, paper_trade=True)

    logger.info("Fetching %s data | interval=%s | days=%d", symbol, interval, days)
    data = fetcher.get_market_data(symbol, interval=interval, days=days)

    if data is None or len(data) == 0:
        raise RuntimeError("Failed to fetch market data.")
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Fetched data is not a DataFrame.")

    logger.info("Fetched %d candles for %s", len(data), symbol)
    return data


def main() -> None:
    symbol = "NIFTY"

    try:
        data = fetch_backtest_data(symbol=symbol, days=30, interval="5m")
    except Exception as exc:
        logger.error("Data fetch failed: %s", exc)
        raise SystemExit(1)  # converted from sys.exit — safe in threads

    param_grid = build_param_grid()
    fixed_params = build_fixed_params()

    keys = list(param_grid.keys())
    all_combinations = list(itertools.product(*param_grid.values()))
    logger.info("Starting breakout grid search | combinations=%d", len(all_combinations))

    results: List[Dict] = []
    failed_rows: List[Dict] = []

    best_score = -math.inf
    best_params = None
    best_result = None

    for combo in tqdm(all_combinations, desc="Breakout Grid Search"):
        params = dict(zip(keys, combo))
        is_valid, reason = validate_param_combo(params)

        if not is_valid:
            failed_rows.append({
                **params,
                "failure_type": "validation",
                "failure_reason": reason,
            })
            continue

        try:
            result = backtest_breakout(
                symbol=symbol,
                data=data,
                **fixed_params,
                **params,
            )

            score = compute_score(result)

            row = {
                **params,
                "score": score,
                "pnl": result["total_pnl"],
                "num_trades": result["num_trades"],
                "win_rate": result["win_rate"],
                "sharpe": result["sharpe"],
                "max_drawdown": result["max_drawdown"],
                "final_capital": result["final_capital"],
                "buy_signals": result["buy_signals"],
                "sell_signals": result["sell_signals"],
                "total_signals": result["total_signals"],
                "skipped_due_to_filters": result["skipped_due_to_filters"],
            }
            results.append(row)

            if score > best_score:
                best_score = score
                best_params = params.copy()
                best_result = {
                    **result,
                    "score": score,
                }

        except Exception as exc:
            logger.exception("Breakout grid-search failure for params=%s", params)
            failed_rows.append({
                **params,
                "failure_type": "runtime",
                "failure_reason": str(exc),
            })

    if not results:
        logger.error("No valid breakout grid-search results generated.")
        if failed_rows:
            pd.DataFrame(failed_rows).to_csv(FAILED_PARAMS_CSV, index=False)
            logger.info("Saved failures to %s", FAILED_PARAMS_CSV)
        raise SystemExit(1)  # converted from sys.exit — safe in threads

    df = pd.DataFrame(results).sort_values(
        by=["score", "pnl", "sharpe"],
        ascending=[False, False, False],
    )
    df.to_csv(RESULTS_CSV, index=False)
    logger.info("Saved breakout grid results to %s", RESULTS_CSV)

    if failed_rows:
        pd.DataFrame(failed_rows).to_csv(FAILED_PARAMS_CSV, index=False)
        logger.info("Saved failed breakout combinations to %s", FAILED_PARAMS_CSV)

    if best_params is None or best_result is None:
        logger.error("Breakout grid search completed but no best result was found.")
        raise SystemExit(1)  # converted from sys.exit — safe in threads

    save_best_params(best_params, best_result, symbol)

    print("\n" + "=" * 70)
    print("BEST BREAKOUT GRID RESULT")
    print("=" * 70)
    print(f"Symbol          : {symbol}")
    print(f"Score           : {best_result['score']:.2f}")
    print(f"Total P&L       : ₹{best_result['total_pnl']:.2f}")
    print(f"Trades          : {best_result['num_trades']}")
    print(f"Win Rate        : {best_result['win_rate'] * 100:.2f}%")
    print(f"Sharpe          : {best_result['sharpe']:.2f}")
    print(f"Max Drawdown    : ₹{best_result['max_drawdown']:.2f}")
    print(f"Final Capital   : ₹{best_result['final_capital']:.2f}")

    print("\nBest Parameters:")
    for key, value in best_params.items():
        print(f"  {key}: {value}")

    print("\nTop 10 Results:")
    print(df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
