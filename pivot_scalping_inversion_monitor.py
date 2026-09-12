"""
pivot_scalping_inversion_monitor.py -- forward-tracks a suspected direction
inversion in the pivot_scalping strategy.

2026-09-12 finding: across 266 training-eligible pivot_scalping signals
(2026-06-29 to 2026-07-29), flipping side (BUY<->SELL) turns a 7.1% win
rate / -0.21 avg net-R into 92.9% / +0.213 -- and the pattern holds
independently in both halves of that period (7.7%/92.3% vs 6.6%/93.4%).
No code defect was found after auditing the strategy's own logic
(_pivot_bias, _ema_cross), the CPR/floor/Camarilla formulas in
pivot_boss.py, and the generic strategy-dispatch mechanism in
signal_engine.py (_invoke_strategy) -- so this is an unexplained,
purely empirical pattern, discovered by scanning 49 strategies for the
most extreme outlier. That is exactly the kind of result the deflated-
Sharpe / multiple-testing discipline elsewhere in this repo exists to
catch before trusting it. This script does NOT change live signal
generation or direction in any way -- it only measures, on signals
logged AFTER the discovery date, whether the inversion continues to
hold on genuinely new data before anyone considers acting on it.

Run manually any time: python3 pivot_scalping_inversion_monitor.py
"""
from __future__ import annotations

import sqlite3
from datetime import date

DISCOVERY_DATE = "2026-09-12"   # signals on/after this date are out-of-sample
MIN_N_FOR_VERDICT = 40          # don't verdict on a tiny forward sample

DB_PATH = "signal_log.db"


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN tb_r_multiple_net > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*),
               AVG(tb_r_multiple_net),
               SUM(CASE WHEN -tb_r_multiple_net > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*),
               AVG(-tb_r_multiple_net)
        FROM signal_log
        WHERE training_eligible=1 AND tb_r_multiple_net IS NOT NULL
          AND strategy='pivot_scalping' AND signal_date >= ?
        """,
        (DISCOVERY_DATE,),
    )
    n, wr, avg_r, flip_wr, flip_avg_r = cur.fetchone()
    n = n or 0

    print(f"pivot_scalping forward check (signals >= {DISCOVERY_DATE}, out-of-sample from discovery)")
    print(f"  n = {n}")
    if n == 0:
        print("  No new pivot_scalping signals logged yet since discovery. Nothing to report.")
        return

    print(f"  AS-LOGGED : win_rate={wr:.3f}  avg_net_R={avg_r:.4f}")
    print(f"  FLIPPED   : win_rate={flip_wr:.3f}  avg_net_R={flip_avg_r:.4f}")

    if n < MIN_N_FOR_VERDICT:
        print(f"  n < {MIN_N_FOR_VERDICT} -- not enough forward data yet for a verdict. "
              f"Keep watching, do not act on this.")
        return

    if flip_avg_r > 0 and flip_avg_r > avg_r:
        print("  Forward pattern is CONSISTENT with the original discovery. "
              "Still not sufficient alone to trade on -- this needs the same "
              "purged walk-forward validation as any other strategy before "
              "any live use, but it is now worth that formal validation pass.")
    else:
        print("  Forward pattern does NOT confirm the original discovery. "
              "Treat the original finding as most likely an artifact of "
              "scanning many strategies for the most extreme outlier.")


if __name__ == "__main__":
    main()
