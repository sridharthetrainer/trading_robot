# Diagnostic Findings — Paper Trading Profitability Fix
Session: 2026-07-03 (Fable 5). Created this session — no prior content.

## Status
- [ ] 1. Silent data corruption (option-chain provenance + trade-log integrity)
- [ ] 2. Sign-error anomaly (scores >=18 worst outcomes)
- [ ] 3. Statistical reality check (signal_log shadow labels, net of cost)
- [ ] 4. Risk/execution leaks (only if 1-3 insufficient)

## Log
### Task 1 — Silent data corruption: NO feed corruption; REAL execution finding
Evidence (queries this session):
- Option-chain provenance in-hours: 2026-06-29→07-03 all served by source=angel ok=1
  (36-177 rows/day). 9 cache rows on 07-02 correctly recorded ok=0 (the
  _is_live_source gate works). Pre-06-29 rows have empty source (predate the
  provenance column). => feed NOT serving silent stale data.
- trades.db (all 12 rows): 0 duplicates, 0 missing exits, 0 impossible fills. BUT:
  * 12/12 exits are JANITORIAL: startup_eod_emergency(4), after_hours_stale(5),
    time_exit(2), rest_poll_stop(1). ZERO exits via target or stop-loss.
  * 4 trades exited at exit_price == entry_price (no quote available after-hours)
    → gross 0, net = -charges. Per-trade pnl uniformly -45..-68 ≈ round-trip cost.
  * Holds 91-815 min incl. positions held past session close → closed by
    emergency paths on restart. These trades date from the June restart-churn era
    (memory-cap kill loop / startup hang — both since fixed; EOD-close-without-
    quote also since fixed: close_positions_at_eod now defers without a quote).
CONCLUSION: the -Rs625 flat result = 12 trades never managed to target/stop
(bot down at the wrong times), each paying ~Rs52 costs on ~0 gross. Not feed
corruption; infrastructure-era execution failure, root causes already fixed,
NOT yet re-validated by a clean live day.
### Task 2 — Sign-error anomaly: RESOLVED (confounder) + 1 REAL bug fixed
- The >=18-score bucket (WR .205 vs .398 for 7-12) is NOT a modifier sign error.
  Decomposition (this session): >=18 is 95.7% SELL (7-12 is 77% BUY). Within-side,
  decided-WR decays mildly for BOTH sides (BUY .441->.376, SELL .418->.363):
  composition + timeouts-in-denominator explain the dramatic raw gap. SELLs lost
  because NIFTY recovered 23500 (06-21) -> 24167 (07-03). Conclusion: score has NO
  positive rank power (mild inverse, consistent with modifier_edge_report
  helps:0/hurts:0/dead:10/noise:2) — but no sign flip. No fix; do NOT re-weight.
- REAL BUG FIXED: trades.db held a legacy-corrupt r_multiple (RELIANCE
  entry==exit yet stored R=+30.47; current writer formula is correct — old-era
  row). It made edge_report print "+2.425R/trade positive expectancy" over a
  12/12-losing sample. Fix: (a) edge_report.fetch_r_distribution now RECOMPUTES
  R from entry/exit/stop (trade_manager's canonical formula), stored value only
  as fallback; (b) one-time repair of deviant rows (1 repaired: 30.47->0.0).
  EVIDENCE: re-run edge_report => n=12 mean=-0.11R expectancy=-0.114R/trade.
### Task 3 — Statistical reality check (signal_log shadow labels, net of cost)
All numbers computed this session from signal_log.db (tb_stop>0, net R):
- USABLE sample: n=6128 across only 5 DISTINCT DAYS (the 12-day headline includes
  pre-barrier rows). PBO: ok:False (needs >=8 days). Meta-labeler: not ready.
- Overall: avg gross R = -0.001 (zero), avg NET R = -0.184, Sharpe/signal -0.25,
  Sortino -0.30, cumulative maxDD -1125R. Chronological split: fit(<=07-01)
  -0.175 vs validate(>) -0.221 -> NO EDGE, in-sample and out-of-sample. VERDICT:
  no edge (stated plainly). The constraint is distinct trading days (5 usable).
- Positive pocket: elder_triple_screen net +0.145 (n=112) — 5 correlated days,
  PBO cannot run -> NEEDS MORE DATA, not actionable. td_sequential +0.048 n=24 noise.
- Kill candidates: expiry_scalp net -1.105 (n=20, small), ichimoku -0.707 (n=33).
- The 5 named modifiers (gex/skew/oi/weinstein/sector): endorsed n=0 — they NEVER
  fire in this window. No sign error possible; they are DEAD weight (producers
  output ~0 in this regime). Consistent with modifier_edge_report dead:10.
### Task 4 — Risk/execution leaks (brief; 1-3 explained the result)
- Stops: the material finding is Task 1's — 12/12 exits janitorial (bot-downtime
  era), 0 via stop/target. Root causes (memory-cap kill loop, startup hang,
  EOD-close-without-quote) already fixed; needs a clean live day to re-validate.
- Re-entry churn: 0 same-symbol re-entries <30min. Max simultaneous positions: 2
  (computed from entry/exit intervals) — no correlation-stacking leak in this
  sample. MANUAL_STRUCT_STOP env flag verified present (STRUCT_STOP_ENABLED,
  manual_trade_tracker.py:106).
- Slippage/costs: shadow net-R already embeds the corrected 2026 cost model +
  slippage (capital_compounder/triple_barrier) — no separate leak found.

## FINAL RANKED LIST (by estimated profit impact)
1. FIXED THIS SESSION — corrupt r_multiple made edge_report claim +2.425R/trade
   positive expectancy on an all-losing sample (dangerous misinformation that
   could have justified scaling). edge_report now recomputes R from prices;
   1 DB row repaired. EVIDENCE: re-run => expectancy -0.114R/trade, all buckets
   consistent with 12/12 losses.
2. NEEDS MORE DATA (was: "why flat?") — the -Rs625 is now fully explained:
   12 downtime-era trades exited janitorially at ~0 gross paying ~Rs52 costs
   each. Uptime root causes fixed earlier (watchdog memory cap, scan-stall
   auto-repair, startup hang, EOD-quote guard); what resolves it: 5-10 CLEAN
   uninterrupted live days. No code change left to make here.
3. NEEDS MORE DATA — elder_triple_screen is the only net-positive pocket
   (+0.145R, n=112) but rests on 5 correlated days; PBO needs >=8 distinct
   days, meta-labeler >=10. Resolves automatically as days accrue; do NOT act on
   it before the gates can run.
4. STRUCTURAL, NEEDS A DECISION — the confluence score has no positive rank
   power (higher score = mildly worse in BOTH sides; >=18 bucket is 95.7% SELL
   composition). Decision: keep gates as capital-preservation (current stance)
   vs prune the scoring stack down to measured survivors once >=8-10 usable
   days exist. Do NOT re-weight now (would be curve-fitting 5 days).
5. KILL CANDIDATES (small n, review at >=8 days): expiry_scalp (net -1.105R,
   n=20), ichimoku (net -0.707R, n=33). Recommend retiring if they persist
   negative once PBO/day thresholds are met.
6. NO ACTION — the 5 named modifiers never fire (endorsed n=0): dead weight,
   not sign errors. Candidates for pruning in the same >=8-day review.

## Session summary
Feed corruption: none (provenance verified). One real reporting/data bug found
and fixed with evidence. No edge net of costs (fit AND validate negative) — the
honest binding constraint is 5 usable days of data, not code. Next session:
re-run Task 3 + PBO once >=8 distinct barriered days exist; re-validate exits on
the first clean live day.
