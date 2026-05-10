"""
backtest_mr_validate.py

Validation runner for the enhanced mean-reversion strategy.

What this version improves
--------------------------
- Loads best parameters automatically from best_params_mr.json
- Removes manual copy-paste of parameters
- Uses a clean train/validation split from fetched data
- Saves validation summary to JSON
- Produces a comparable score for validation quality
- Fails clearly if files or data are missing
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

from angel import AngelOne
from data_fetcher import DataFetcher
from backtest_mr_enhanced import backtest_mr

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BEST_PARAMS_JSON = "best_params_mr.json"
VALIDATION_RESULT_JSON = "validation_result_mr.json"


def load_best_params(path: str = BEST_PARAMS_JSON) -> Tuple[str, Dict]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run backtest_mr_grid.py first to generate best parameters."
        )

    with open(file_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if "params" not in payload:
        raise ValueError(f"{path} is invalid: missing 'params' key.")

    symbol = payload.get("symbol", "NIFTY")
    params = payload["params"]

    if not isinstance(params, dict) or not params:
        raise ValueError(f"{path} contains empty or invalid params.")

    return symbol, params


def fetch_validation_source_data(symbol: str, days: int = 60, interval: str = "5m") -> pd.DataFrame:
    dummy_angel = AngelOne("", "", "", "", paper_trade=True)
    fetcher = DataFetcher(dummy_angel, paper_trade=True)

    logger.info("Fetching validation source data | symbol=%s interval=%s days=%d", symbol, interval, days)
    data = fetcher.get_market_data(symbol, interval=interval, days=days)

    if data is None or len(data) == 0:
        raise RuntimeError("Failed to fetch market data for validation.")

    if not isinstance(data, pd.DataFrame):
        raise TypeError("Fetched validation data is not a DataFrame.")

    logger.info("Fetched %d candles for validation source data.", len(data))
    return data


def split_data_for_validation(data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Assume fetched data is chronological (oldest -> newest).

    We use:
    - first half  = validation period
    - second half = reference/recent period

    Reason:
    If grid search used the most recent block, this validation tests the same
    parameter set on an older block without manual date handling.
    """
    if len(data) < 200:
        raise ValueError("Not enough candles to perform validation split safely.")

    split_idx = len(data) // 2
    validation_data = data.iloc[:split_idx].copy().reset_index(drop=True)
    reference_data = data.iloc[split_idx:].copy().reset_index(drop=True)

    if len(validation_data) < 100:
        raise ValueError("Validation split too small after split.")
    if len(reference_data) < 100:
        raise ValueError("Reference split too small after split.")

    return validation_data, reference_data


def compute_validation_score(result: Dict) -> float:
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


def summarize_period(name: str, data: pd.DataFrame) -> Dict:
    summary = {
        "name": name,
        "rows": int(len(data)),
    }

    if isinstance(data.index, pd.DatetimeIndex) and len(data) > 0:
        summary["start"] = str(data.index.min())
        summary["end"] = str(data.index.max())
    else:
        summary["start"] = None
        summary["end"] = None

    return summary


def save_validation_result(
    symbol: str,
    params: Dict,
    validation_result: Dict,
    validation_period: Dict,
    reference_period: Dict,
) -> None:
    payload = {
        "symbol": symbol,
        "strategy": "mean_reversion_enhanced",
        "validation_period": validation_period,
        "reference_period": reference_period,
        "params": params,
        "metrics": {
            "total_pnl": validation_result["total_pnl"],
            "num_trades": validation_result["num_trades"],
            "win_rate": validation_result["win_rate"],
            "sharpe": validation_result["sharpe"],
            "max_drawdown": validation_result["max_drawdown"],
            "final_capital": validation_result["final_capital"],
            "buy_signals": validation_result["buy_signals"],
            "sell_signals": validation_result["sell_signals"],
            "total_signals": validation_result["total_signals"],
            "skipped_due_to_filters": validation_result["skipped_due_to_filters"],
            "score": validation_result["score"],
            "csv_file": validation_result["csv_file"],
        },
    }

    with open(VALIDATION_RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info("Validation summary saved to %s", VALIDATION_RESULT_JSON)


def main() -> None:
    try:
        symbol, best_params = load_best_params(BEST_PARAMS_JSON)
    except Exception as exc:
        logger.error("Failed to load best parameters: %s", exc)
        raise SystemExit(1)  # safe in threads

    logger.info("Loaded best parameters for %s from %s", symbol, BEST_PARAMS_JSON)

    try:
        source_data = fetch_validation_source_data(symbol=symbol, days=60, interval="5m")
        validation_data, reference_data = split_data_for_validation(source_data)
    except Exception as exc:
        logger.error("Failed to prepare validation data: %s", exc)
        raise SystemExit(1)  # safe in threads

    validation_period = summarize_period("validation", validation_data)
    reference_period = summarize_period("reference_recent", reference_data)

    logger.info(
        "Validation candles=%d | Reference candles=%d",
        len(validation_data),
        len(reference_data),
    )

    # Force validation runner settings as needed
    run_params = dict(best_params)
    run_params["verbose"] = True

    logger.info("Running validation backtest using saved best parameters...")
    try:
        result = backtest_mr(
            symbol=symbol,
            data=validation_data,
            **run_params,
        )
    except Exception as exc:
        logger.error("Validation backtest failed: %s", exc)
        raise SystemExit(1)  # safe in threads

    result["score"] = compute_validation_score(result)

    try:
        save_validation_result(
            symbol=symbol,
            params=run_params,
            validation_result=result,
            validation_period=validation_period,
            reference_period=reference_period,
        )
    except Exception as exc:
        logger.warning("Validation completed, but saving summary failed: %s", exc)

    print("\n" + "=" * 70)
    print("MEAN-REVERSION VALIDATION RESULTS")
    print("=" * 70)
    print(f"Symbol              : {symbol}")
    print(f"Validation Candles  : {len(validation_data)}")
    print(f"Score               : {result['score']:.2f}")
    print(f"Total P&L           : ₹{result['total_pnl']:.2f}")
    print(f"Trades              : {result['num_trades']}")
    print(f"Win Rate            : {result['win_rate'] * 100:.2f}%")
    print(f"Sharpe              : {result['sharpe']:.2f}")
    print(f"Max Drawdown        : ₹{result['max_drawdown']:.2f}")
    print(f"Final Capital       : ₹{result['final_capital']:.2f}")
    print(f"Buy Signals         : {result['buy_signals']}")
    print(f"Sell Signals        : {result['sell_signals']}")
    print(f"Total Signals       : {result['total_signals']}")
    print(f"Skipped By Filters  : {result['skipped_due_to_filters']}")
    print(f"Trades CSV          : {result['csv_file']}")
    print(f"Summary JSON        : {VALIDATION_RESULT_JSON}")

    print("\nParameters Used:")
    for key, value in run_params.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
