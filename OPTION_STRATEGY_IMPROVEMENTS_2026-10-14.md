# Option strategy improvements — 14 October 2026

Reviewed all 42 entries in the option strategy catalog. Three entries have real
signal evaluators (C1, C3, D1); 39 are registered placeholders. Registration,
CORE status, and a parameter file do not establish that an algorithm exists.
No strategy is promoted to live execution by these changes.

## Changes affecting the option bot

- Shared option quality checks require a known timestamp, preserve stale exchange
  time even after a fresh fetch, reject future/nonfinite clocks and missing or
  crossed quotes, and accept both nested and flat liquidity fields.
- Buying quality and catalog strike selection filter declared expiries, including
  expiry tags inside individual legs. Legacy completely untagged rows remain
  supported for shadow research; their expiry consistency is not verified.
- Expected-move consumption uses the latest session range, rather than the range
  across all historical candles supplied to the scanner.
- Gap detection uses the current session open against the preceding session close
  when no daily frame is supplied. Dated daily frames exclude today's candle.
- Classification caching invalidates on changed candles, VIX, or trading date.
  Event, expiry, gap and time overlays refresh independently of the cache.
- Catalog entry windows use Indian market time and accept an explicit replay clock.
  Invalid windows fail closed, weekend entry is blocked, and day dedup state is
  bounded. The existing global risk checks now run before shadow evaluation.
- Global risk inputs reject nonfinite values and invalid position counts. A defined
  risk entry does not incorrectly consume the naked-position allowance.
- All catalog results explicitly say shadow mode and live_ready=false. Disabled,
  skipped and dangerous entries cannot become active just by acquiring a callable.
- Strike-flow momentum compares only nearby same-session snapshots of the same
  expiry. Expiry rolls, overnight boundaries and gaps over 15 minutes warm up anew
  (OPTION_FLOW_MAX_SNAPSHOT_GAP_SEC can configure the gap). Entry plans use the ask;
  a zero candidate cap permits no actionable flow signals.

## Implemented strategy changes

| ID | Strategy | Changes |
| --- | --- | --- |
| C1 | Short straddle | Known freshness and expiry, event and configured ADX checks, finite VIX, unknown-regime rejection, invalid quote rejection, expiry-day Greeks using remaining hours. |
| C3 | Iron condor | Known freshness and expiry, event checks, configured IVP band enforced, insufficient IV history rejected, positive delta required, unusable wings rejected, next configured wing width tried when the first is unavailable. |
| D1 | Pre-event long strangle | Known freshness and expiry, finite IVP, positive delta required, both legs require valid quotes and volume, spread check, debit based on asks rather than last trades. |

The same-expiry requirement and wing-width/credit payoff calculation are consistent
with the [Options Industry Council's iron-condor description](https://www.optionseducation.org/strategies/all-strategies/short-condor).

## Catalog readiness

Every entry still requires independent after-cost forward validation and an
execution path before live promotion. Placeholders additionally need entry,
exit, strike-selection and, where relevant, position-state logic implemented.

| ID | Strategy | Activation | Actual logic | Risk |
| --- | --- | --- | --- | --- |
| A1 | ORB Directional Option Buy | CORE | PLACEHOLDER | defined_risk |
| A2 | Supertrend + ADX Momentum | CORE | PLACEHOLDER | defined_risk |
| A3 | High-Frequency Gamma Scalping | DISABLED | PLACEHOLDER | defined_risk |
| A4 | VWAP + CPR Breakout | DISABLED | PLACEHOLDER | defined_risk |
| A5 | Triple-Confluence Momentum | ADVANCED | PLACEHOLDER | defined_risk |
| A6 | 15-Minute Candle Sweep (IMPS/CISD) | DISABLED | PLACEHOLDER | defined_risk |
| A7 | Bollinger Band + Stochastic Directional | DISABLED | PLACEHOLDER | defined_risk |
| A8 | Multi-Timeframe Trend Alignment | CORE | PLACEHOLDER | defined_risk |
| A9 | EMA Pullback Continuation | DISABLED | PLACEHOLDER | defined_risk |
| A10 | Gamma Expiry Scalping (0-DTE) | DANGEROUS | PLACEHOLDER | defined_risk |
| A11 | CPR Breakout Directional (PDH/PDL) | DISABLED | PLACEHOLDER | defined_risk |
| A12 | Option Buying Momentum (OI+Vol) | ADVANCED | PLACEHOLDER | defined_risk |
| A13 | Multi-Leg SuperTrend Breakout Spread | CORE | PLACEHOLDER | defined_risk |
| A14 | Synthetic Long/Short | DISABLED | PLACEHOLDER | undefined_risk |
| B1 | Debit Vertical (Bull Call / Bear Put) | CORE | PLACEHOLDER | defined_risk |
| B2 | Credit Vertical (Bull Put / Bear Call) | CORE | PLACEHOLDER | defined_risk |
| B3 | Bull Put Credit Spread | ADVANCED | PLACEHOLDER | defined_risk |
| B4 | Bear Call Credit Spread | ADVANCED | PLACEHOLDER | defined_risk |
| B5 | Ratio Spread (1:2) | DANGEROUS | PLACEHOLDER | undefined_risk |
| C1 | 09:20 Short Straddle | CORE | IMPLEMENTED | undefined_risk |
| C2 | Short Strangle (OTM) | ADVANCED | PLACEHOLDER | undefined_risk |
| C3 | Iron Condor | CORE | IMPLEMENTED | defined_risk |
| C4 | Iron Butterfly | CORE | PLACEHOLDER | defined_risk |
| C5 | Calendar Spread | ADVANCED | PLACEHOLDER | defined_risk |
| C6 | Expiry-Day OTM Decay Harvest | DANGEROUS | PLACEHOLDER | defined_risk |
| C7 | CPR Mean Reversion (Fading the Extremes) | ADVANCED | PLACEHOLDER | defined_risk |
| C8 | VWAP Mean Reversion (Extreme Stretch Fade) | CORE | PLACEHOLDER | defined_risk |
| C9 | Short Straddle -> Strangle Conversion | ADVANCED | PLACEHOLDER | undefined_risk |
| D1 | Pre-Event Long Strangle | CORE | IMPLEMENTED | defined_risk |
| D2 | Post-Event Volatility Crush Short Strangle | ADVANCED | PLACEHOLDER | undefined_risk |
| D3 | Long Straddle (Generic Pre-Event) | ADVANCED | PLACEHOLDER | defined_risk |
| D4 | Long Strangle (Volatility Breakout/Compression) | ADVANCED | PLACEHOLDER | defined_risk |
| D5 | Gamma Scalping (Long Straddle + Futures Hedge) | SKIPPED | PLACEHOLDER | not_implemented |
| D6 | Reverse Iron Condor / Reverse Iron Butterfly | ADVANCED | PLACEHOLDER | defined_risk |
| D7 | Diagonal Spread | ADVANCED | PLACEHOLDER | defined_risk |
| E1 | Time-Based Adaptive Strangle | ADVANCED | PLACEHOLDER | undefined_risk |
| E2 | Straddle -> Strangle Auto-Conversion (State Machine) | CORE | PLACEHOLDER | undefined_risk |
| E3 | Delta-Neutral Straddle Machine | ADVANCED | PLACEHOLDER | undefined_risk |
| E4 | Rolling Short Straddle (Intraday Re-Entry) | ADVANCED | PLACEHOLDER | undefined_risk |
| E5 | Short Strangle with Delta Rebalancing | ADVANCED | PLACEHOLDER | undefined_risk |
| E6 | Trend-Adjusting Iron Condor | ADVANCED | PLACEHOLDER | defined_risk |
| E7 | Ratio Spread with Tail Hedge | DANGEROUS | PLACEHOLDER | undefined_risk |

## Practical limits

These are code correctness and signal-quality improvements. Their effect on
realized profit has not been measured. Parameters retain their unvalidated
status. Completely untagged legacy chains cannot prove expiry identity; a
provider without usable clocks or quotes will now be blocked instead of
assumed valid. The IV-history check verifies available observations, not an
independent daily sampling contract. Defined-risk maximum-loss fields remain
payoff estimates before fees, not guaranteed fills or account risk budgets.
Dynamic, calendar, diagonal, and other placeholder strategies remain inert;
they were not substituted with invented entry rules.

No deployment, service restart, live-mode change, parameter optimization or
broker order was performed. Existing worktree edits were preserved.

## Validation

245 focused tests passed across strategy evaluators, all 42 readiness entries,
quote quality, expiry isolation, snapshot/session boundaries, regime caching,
catalog risk checks, recorder wiring, hero-zero selection, cohort policies,
decision journaling, trade economics, execution routing and risk shadow checks.
The catalog execution test forces actual C1 and C3 selection and verifies zero
order attempts even when evaluator exceptions are caught. An integration test
checks that the live adapter does not manufacture quote freshness.

Changed Python files compile, and their tracked diffs pass whitespace checks.
Two existing SmartApi TLS-option deprecation warnings were reported.
