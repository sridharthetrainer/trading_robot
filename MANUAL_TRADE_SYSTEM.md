# Manual Trade Management System — what we built (for AI review)

This document describes a manual-trade detection + protection + dynamic-exit +
decision + learning system added to a live NSE algo-trading bot (Angel One
broker, Python). It is meant for an independent AI/engineer to review. It states
honestly what is implemented vs. what is a future step.

---

## 1. Problem it solves
A discretionary trader places trades manually on the Angel One app. The system
must, without the trader watching the broker app:
1. **Detect** the manual trade (even one placed while the tool was offline).
2. **Protect** it with broker-side SL + target that survive a software crash.
3. **Dynamically manage** SL/target (trail profits, give-back guard).
4. **Decide** at end of day: carry overnight / close / tighten.
5. **Show** status as a Telegram image card (no need to open the broker app).
6. **Learn** from outcomes so management adapts to the trader over time.

---

## 2. Architecture (two processes, shared SQLite)
- **`manual_trade_tracker.py`** (detector + manager): polls Angel order book AND
  reconciles open positions; places/trails GTT orders; renders cards; records
  outcomes. Has its own Angel session with anti-storm reconnect.
- **`trade_guardian_bot.py`** (interactive Telegram bot, separate bot token):
  `/manual`, `/status`, `/help` etc.; reads the tracker's DB and renders cards.
- Shared `manual_trades.db`; tracker writes, Guardian reads. No fragile RPC.

---

## 3. Detection & sync
- **Order-book polling** (30s): new COMPLETE/TRADED fills not tagged by the algo.
- **Position reconciliation** (every cycle, incl. off-hours): adopts any open
  broker position not already tracked — catches fills missed while the tool was
  down. Side from `netqty`, entry from `totalbuy/sellavgprice`.
- Excludes the algo bot's own positions (via `trades.db`).
- Restart-safe: open trades reloaded from DB; broker GTTs adopted, never
  duplicated.

## 4. Protection — broker-side floor (survives crash)
- **Angel GTT** (Good-Till-Triggered, persists at broker for 365 days) SL + target.
- Sizing: options **30% SL / 50% target** of premium (configurable); equities 2%/4%.
- Hard guards: never place a trigger on the wrong side of price (no insta-trigger);
  place-once; adopt-existing guard (queries broker GTTs before placing → no
  duplicates); cancel-on-exit so no orphan leg.
- Instrument classification avoids false positives (e.g. RELIANCE ≠ option).

## 5. Dynamic SL/target engine (`dynamic_exit.py`)
Recomputes ideal levels every 120s from the **option's own 5-min candles** and
modifies the GTT. The SL only ever **tightens**; the target only **extends**.

Blended methods (each a candidate; most-protective wins):
| Method | Source / rationale |
|--------|--------------------|
| **Chandelier Exit** `HH(n) − k·ATR` | Chuck LeBeau — volatility trailing stop |
| **ATR / volatility stop** | J. Welles Wilder, *New Concepts in Technical Trading Systems* |
| **Supertrend** (direction-aligned) | Olivier Seban — trend-following stop |
| **Swing structure** (last swing low/high) | classic price-structure stop |
| **ADX regime-adaptive `k`** | Wilder ADX — trend→wider (let it run), range→tighter |
| **Profit ratchet** (lock a fraction of peak) | Turtle/Elder trade management |
| **Break-even floor** (after ≥20% run, SL ≥ entry) | give-back guard |

Safety: trails **only in profit** (never tightens a losing trade — that just
locks the loss); keeps a `max(0.5·ATR, 1%)` buffer from price so noise can't
trigger; broker GTT remains the static floor.

## 6. End-of-day decision (`exit_decision.py`)
Near 3:10–3:25 PM, per open trade → **HOLD / CLOSE / TIGHTEN** with reasoning:
- Options with **DTE ≤ 1** → CLOSE (theta decay + overnight gap risk).
- Losing + trend turned against (Supertrend) → CLOSE (cut).
- In profit near close → TIGHTEN / book (don't let it round-trip).
- Small loss, time left, trend not against → HOLD.
Recommendation-only by default; opt-in `MANUAL_AUTO_CLOSE_EOD` square-off.

## 7. Self-evolving learner (`manual_learning.py`)
- Records every closed trade: context (regime, VIX, indicator alignment,
  option/equity, DTE, overnight, entry hour) + outcome (win, **R-multiple**,
  **expectancy** — Van Tharp).
- `get_bias()` returns an empirical nudge once **≥10 samples** (e.g. "option
  overnight trades win only 33%, n=12") → feeds the EOD decision.
- Honest: this is **conditional win-rate / expectancy**, not a trained ML model.
  A learned exit-classifier is a future step once the sample is large. Kept
  separate from the algo strategy calibrator on purpose.

## 8. Visualisation & interaction
- **Image status card** (`trade_card.py`, matplotlib): symbol/side/qty, big P&L,
  to-scale SL · LTP · Entry · Target track. Sent on detect / every update / exit
  / EOD, to both the main channel and the Guardian bot.
- **Interactive**: `/manual` (Guardian bot) renders live cards on demand.

## 9. Indicators used (from `indicators.py`)
ATR, Supertrend, ADX, swing high/low detection (active in the exit engine);
library also has EMA, RSI, VWAP, Bollinger, Stoch-RSI, DEMA/TEMA, NATR,
Connors-RSI, RSI-divergence (used by the algo's ~57-strategy signal engine).

## 10. Techniques / literature grounded in
- Wilder — ATR, ADX, volatility stops, Parabolic SAR concepts.
- LeBeau — Chandelier Exit.
- Seban — Supertrend.
- Van Tharp (*Trade Your Way to Financial Freedom*) — R-multiples, expectancy,
  position management.
- Elder / Curtis Faith (*Way of the Turtle*) — ATR-based stops, let-profits-run,
  ratcheting trailing stops.
- Options (Natenberg, Sinclair) — theta decay & expiry-day risk → close-near-expiry.

## 11. Config knobs (env)
`MANUAL_AUTO_PROTECT`, `MANUAL_OPTION_SL_PCT` (0.30), `MANUAL_OPTION_TARGET_PCT`
(0.50), `MANUAL_EQUITY_SL_PCT`/`_TARGET_PCT`, `MANUAL_DYN_RECOMPUTE_SECS` (120),
`MANUAL_EOD_CHECK` (on), `MANUAL_AUTO_CLOSE_EOD` (off), `GUARDIAN_BOT_TOKEN`/`_CHAT_ID`.

## 12. Honest limitations
- **Edge unvalidated**: there is no saved out-of-sample backtest proving any
  algo strategy is profitable; the manual system is *management*, not alpha.
- **Learner data-starved**: needs real closed trades before `get_bias` activates.
- **No trained ML yet** — empirical stats only (by design, until data exists).
- **NSE-direct feeds (option-chain/FII)** are IP-blocked → `opt_bias=0`; India VIX
  is sourced from Angel instead. Needs a proxy for the rest.
- System runs **dual paper+live**: real orders fire when balance ≥ ₹5,000.
