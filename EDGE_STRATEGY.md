# EDGE_STRATEGY.md — where profit can actually come from

The 4 real sources of trading edge, mapped to THIS system (retail NIFTY options, Python,
home machine). The hard truth up front: **indicators/strategies/code are not edge** — they
are free and public. Edge comes only from the four sources below. Two are impossible for
retail and must NOT be chased; one is started; one is the real path.

> Rule for everything here: nothing gets wired to live trading until it clears
> `validation_harness.py` (Deflated Sharpe ≥ 0.95 + positive locked holdout). Measured > believed.

---

## 1. INFORMATION others don't have — PARTIAL (the only retail-accessible slice)
- **Not accessible retail:** true institutional order flow, dealer books, news before it's priced.
- **What we CAN do** — systematically capture + validate the data we *can* get:
  - ✅ `intraday_oi_logger.py` — accruing intraday OI/IV snapshots (the one genuinely-new-info experiment; free).
  - ✅ `macro_global_profit_engine.py`, FII/sector history — context, report-only.
  - ✅ `condor_forward_test` — paper selling track record.
- **Prior:** low (vol-surface, EOD-OI, GEX, participant-OI all already measured null) — but information is the only lever we actually have. Validate before any wiring.

## 2. SPEED (HFT) — IMPOSSIBLE retail → DO NOT PURSUE
- Microsecond edge needs colocation + exchange infra you cannot buy. This system runs ~30s
  cycles on a home machine. Per CLAUDE.md #3, **latency is not the constraint** — adding a
  streaming/low-latency scan would burn effort for zero gain. **Abandoned by design.**

## 3. STRUCTURE / SCALE — IMPOSSIBLE retail (one thin exception, measured negative)
- Market-maker rebates, IPO/allocation access, capital scale: not available to you.
- **The only structural premium a retail trader can harvest = DEFINED-RISK option SELLING**
  (you get paid the "insurance premium" for taking capped tail risk). BUT measured: NIFTY
  weekly iron condor = **no edge after costs** (−0.036R real data). Now running as a PAPER
  forward-test (`condor_forward_test`) to confirm over real expiries. **Do not expect it to pay**;
  promote to live only if the paper record proves OOS *and* the account is funded.

## 4. DISCIPLINE + TIME — THE REAL CODEABLE EDGE (focus here)
Not magic — it's what lets a real edge survive variance, plus the only thing that compounds.
- **(a) Risk discipline — already strong, KEEP:** VaR, daily-loss limit, kill-switch, adaptive
  sizing, portfolio caps, and PAPER-as-config-guarantee. This is genuinely above retail norm.
- **(b) Cost minimization — STOP the bleed:** directional option *buying* has a structural
  **−67%/yr** theta drag. Every day you hold, you lose. The single biggest "edge" available is
  to **stop doing the negative-edge thing.**
- **(c) Long-horizon, diversified TREND on a BASKET — the one untested codeable edge with a real
  prior:** trend/momentum premia across many instruments over weeks–months is the best-documented
  retail-accessible edge (Carver, Kaufman). NOT yet properly tested here — cross-sectional
  momentum failed (4.5yr / one regime), and trend-on-a-basket is the *documented* proper
  follow-up that was never built. This is the highest-value *new* thing to research.

---

## The realistic retail edge stack (priority order)
1. **Don't lose** — risk discipline + stop negative-edge option buying. *(done / ongoing)*
2. **New-information accrual + honest validation** — OI logger running; validate at ~20 days.
3. **Long-horizon diversified trend on a basket** — propose as a research track (DSR-gated). *(not built)*
4. **Defined-risk selling** — only if the paper forward-test proves OOS edge + account funded.

## What to STOP doing
- Adding indicators / strategies / modifiers — 0 edge (measured), and they *dilute* signal.
- Chasing speed or scale — impossible retail.
- Trusting any backtest that doesn't pass DSR + locked holdout.
- Funding hope: plan finances assuming **₹0** profit until something clears the gate.
