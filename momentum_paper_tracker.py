"""momentum_paper_tracker.py -- forward PAPER tracking of the momentum strategy
that passed pre-registered Test 7 (reports/research_2026-10-14/).

No orders, no broker calls. Once per month, after the first trading day of a
month is in the bhavcopy:
  1. refresh bhavcopy, NSE corporate actions and the adjusted-return DB;
  2. record the new top-30 selection for the month-end just completed, using
     run_test7_momentum.select() -- the exact rule that was tested;
  3. settle any earlier selection whose holding month has finished, against
     the equal-weight universe and NIFTYBEES, with the same 0.30%/side cost;
  4. post a short Telegram summary.
The ledger (reports/momentum_paper/ledger.json) is append-only: recorded
selections and results are never rewritten. Forward evidence starts with the
October-2026 month-end; everything earlier is backtest, not forward record.

One deliberate difference from the recorded backtest: the ETF filter also
excludes SILV/MON100/MAFANG/HNGSNG/NASDAQ/SP500/N100 (the backtest's filter
let TATSILV, a silver ETF, through; the robustness re-run without it passed).

Run by the momentum-paper.timer user unit (1st-7th of each month, evening);
idempotent, so extra runs are harmless.
    .venv/bin/python momentum_paper_tracker.py [--dry-run] [--no-refresh]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "reports" / "research_2026-10-14"
sys.path.insert(0, str(RESEARCH))

import run_test7_momentum as t  # noqa: E402
import run_test17_volscaled_momentum as v17  # noqa: E402

LEDGER = ROOT / "reports" / "momentum_paper" / "ledger.json"
FORWARD_START = "2026-10-01"     # first month-end on or after this (Oct-2026: Fri 30th)
CAPITAL = 500_000
t.ETF = re.compile(t.ETF.pattern + "|SILV|MON100|MAFANG|HNGSNG|NASDAQ|SP500|N100")
logger = logging.getLogger("momentum_paper")


def refresh_data() -> None:
    import bhavcopy_backfill
    import build_adjusted_returns
    import fetch_corporate_actions
    import sqlite3
    con = sqlite3.connect(str(ROOT / "nse_cache.db"))
    last = con.execute("SELECT MAX(date) FROM ohlcv").fetchone()[0]
    con.close()
    start = date.fromordinal(date.fromisoformat(last).toordinal() + 1)
    if start <= date.today():
        logger.info("bhavcopy backfill %s -> %s", start, date.today())
        bhavcopy_backfill.backfill_years(start=start, end=date.today(), delay_sec=1.0)
    fetch_corporate_actions.main()
    build_adjusted_returns.main()


def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"strategy": "Test 7: 12-1 momentum, top 30 of point-in-time top-200 liquid, monthly",
            "forward_start": FORWARD_START, "capital": CAPITAL, "entries": []}


def volscaled_weight(R, C, TV, p: int) -> float:
    """Test 17's exposure for the month starting after month-end row p, from the
    strategy's daily returns up to and including the entry day (rows < p + 2)."""
    df = t.simulate(R, C, TV, 252, t.COST)
    return v17.weight_before(v17.daily_series(R, df), p + 2)


def holding(period: np.ndarray, cols) -> float:
    return float(np.mean([t.holding_return(period[:, j]) for j in cols]))


def benchmark_return(symbol: str, row_dates: list[str]) -> float | None:
    """Compounded adjusted return of symbol over exactly these trading dates."""
    import sqlite3
    con = sqlite3.connect(f"file:{RESEARCH / 'adjusted_returns.db'}?mode=ro", uri=True)
    rets = [r[0] for r in con.execute(
        "SELECT ret FROM daily WHERE symbol=? AND date IN (%s) ORDER BY date"
        % ",".join("?" * len(row_dates)), (symbol, *row_dates))]
    con.close()
    if len(rets) != len(row_dates) or any(r is None for r in rets):
        return None
    return float(np.prod(1 + np.array(rets)) - 1)


def update(ledger: dict) -> list[str]:
    R, C, TV = t.load()
    Rv, Cv, TVv = R.to_numpy(), C.to_numpy(), TV.to_numpy()
    dates = [str(d) for d in R.index]
    cols = list(R.columns)
    ends = t.month_end_rows(R.index)
    by_month_end = {e["month_end"]: e for e in ledger["entries"]}
    notes = []

    # settle finished months
    for k in range(len(ends) - 1):
        p, q = ends[k], ends[k + 1]
        entry = by_month_end.get(dates[p])
        if entry is None or entry.get("result") is not None or q + 1 >= len(dates):
            continue
        rows = slice(p + 2, q + 2)
        period = Rv[rows]
        mom = [cols.index(s) for s in entry["names"] if s in cols]
        uni = [cols.index(s) for s in entry["universe"] if s in cols]
        earlier = [e for e in ledger["entries"] if e["month_end"] < entry["month_end"]]
        prev = max(earlier, key=lambda e: e["month_end"]) if earlier else None
        turn = 1.0 if prev is None else len(set(entry["names"]) - set(prev["names"])) / t.TOP_N
        gross = holding(period, mom)
        net = gross - 2 * t.COST * turn + (t.COST if prev is None else 0)
        w = entry.get("volscaled_weight")
        w_prev = prev.get("volscaled_weight") if prev else None
        entry["result"] = {
            "from": dates[p + 1], "to": dates[q + 1], "settled_on": date.today().isoformat(),
            "momentum": round(net, 5),
            "volscaled": None if w is None else round(
                w * net + (1 - w) * v17.CASH / 12 - (abs(w - w_prev) * t.COST if w_prev is not None else 0.0), 5),
            "equal_weight": round(holding(period, uni), 5),
            "niftybees": benchmark_return("NIFTYBEES", dates[p + 2:q + 2]),
            "turnover": round(turn, 3)}
        notes.append(f"settled {entry['month_end']}")

    # record the latest completed month-end
    p = ends[-1]
    if dates[p] >= FORWARD_START and dates[p] not in by_month_end and p + 1 < len(dates):
        uni, mom = t.select(Rv, Cv, TVv, p)
        per_stock = CAPITAL / t.TOP_N
        entry = {"month_end": dates[p], "recorded_at": datetime.now().isoformat(timespec="seconds"),
                 "enter_at_close_of": dates[p + 1],
                 "names": [cols[j] for j in mom], "universe": [cols[j] for j in uni],
                 "paper_qty_at_month_end_close": {cols[j]: int(per_stock // Cv[p, j]) for j in mom},
                 # Test 17 (passed): exposure scaled to a 12% vol target, rest in cash
                 "volscaled_weight": round(volscaled_weight(R, C, TV, p), 4),
                 "result": None}
        ledger["entries"].append(entry)
        ledger["entries"].sort(key=lambda e: e["month_end"])
        notes.append(f"recorded {dates[p]}")
    return notes


def summary(ledger: dict) -> str:
    done = [e for e in ledger["entries"] if e.get("result")]
    lines = ["📊 <b>MOMENTUM PAPER TRACKER</b> (no orders)"]
    if done:
        last = done[-1]["result"]
        pct = lambda v: f"{v*100:+.2f}%" if v is not None else "n/a"
        lines.append(f"Last month {last['from']}→{last['to']}: momentum {pct(last['momentum'])} | "
                     f"vol-scaled {pct(last.get('volscaled'))} | equal-wt {pct(last['equal_weight'])} | "
                     f"NIFTYBEES {pct(last['niftybees'])}")
        cum = lambda k: (np.prod([1 + e["result"][k] for e in done if e["result"].get(k) is not None]) - 1) * 100
        lines.append(f"Since {done[0]['result']['from']} ({len(done)} mo): momentum {cum('momentum'):+.2f}% | "
                     f"vol-scaled {cum('volscaled'):+.2f}% | equal-wt {cum('equal_weight'):+.2f}% | "
                     f"NIFTYBEES {cum('niftybees'):+.2f}%")
    pending = [e for e in ledger["entries"] if not e.get("result")]
    if pending:
        e = pending[-1]
        lines.append(f"Holding from close of {e['enter_at_close_of']} (₹{CAPITAL // t.TOP_N:,}/stock):")
        lines.append(", ".join(e["names"]))
        if e.get("volscaled_weight") is not None:
            lines.append(f"Vol-scaled version: {e['volscaled_weight']*100:.0f}% invested, rest in cash")
    return "\n".join(lines)


def notify(text: str) -> None:
    import config
    from alerts import AlertManager
    am = AlertManager(bot_token=config.TELEGRAM_BOT_TOKEN or "", chat_id=config.TELEGRAM_CHAT_ID or "",
                      name="momentum_paper")
    am.send(text, dedup_key=f"momentum_paper_{date.today():%Y%m%d}", dedup_cooldown_override=3600)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="no ledger write, no Telegram")
    ap.add_argument("--no-refresh", action="store_true", help="skip data refresh")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
    if not a.no_refresh:
        refresh_data()
    ledger = load_ledger()
    notes = update(ledger)
    text = summary(ledger)
    print(text)
    if a.dry_run or not notes:
        logger.info("nothing new%s", " (dry run)" if a.dry_run else "")
        return 0
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=1))
    notify(text)
    logger.info("ledger updated: %s", notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
