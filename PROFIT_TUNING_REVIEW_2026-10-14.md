# Profit tuning review — 14 October 2026

The available evidence does not establish profitability. The 13 October
live eligibility manifest reports 0 eligible strategies out of 112.
The prospective execution report has 11 outcomes across 5 active days,
0 executable quote coverage, and a negative equal-day mean net R
(-0.373363). Its evidence gates fail. Do not optimize strategy parameters
against this small sample or treat it as a live promotion.

## Implemented improvement

The autonomous entry economics gate now accounts for adverse entry and
exit slippage as well as existing transaction fees. Options use the existing
SHADOW_OPTION_SLIPPAGE_PCT assumption (default 0.005, or 0.5% per leg).
Cash uses SLIPPAGE_PCT converted from percentage points to a fraction
(default 0.05%, or 0.0005 per leg).

Target and stop net proceeds, breakeven probability, expected net profit,
and the cost hurdle include execution drag. The hurdle uses the maximum
cost across target, stop, and flat exits. Invalid numeric inputs,
nonfinite values, fractional quantities, invalid sides, out-of-range
probabilities, and negative cost settings reject admission. A target with
nonpositive net proceeds is rejected even when the cost hurdle is zero.

Example: BUY at 200, target 210, stop 195, quantity 50, assumed win
probability 0.55. Fee-only expected net profit is 91.28; at 0.5% adverse
slippage per leg it is -9.46. The revised gate rejects this trade.

## Validation and limits

54 focused tests passed, covering economics, adverse BUY/SELL fills,
invalid inputs, risk shadow checks, strategy quarantine, execution routing,
risk barriers, and live eligibility. Changed Python files compile and the
tracked code diff passes whitespace checks.

This is an admission and loss-prevention improvement, not a measured
increase in realized profitability. The pure economics helper defaults to
zero slippage for caller compatibility; the autonomous execution caller
explicitly supplies slippage. Existing signal confidence is still the
probability input; a valid 0–1 value is not evidence of calibration.
Fixed slippage assumptions also do not establish executable liquidity.
No service restart, deployment, live-mode change, or parameter search
was performed.

## Evidence needed for subsequent tuning

Collect prospective executable quotes and completed outcomes for the
frozen strategy cohort. Reconcile fills and fees, assess equal-day returns
and uncertainty, and calibrate probability estimates on independent data.
Choose parameter candidates on development data and evaluate them once
on untouched data with trading costs and multiple-testing controls.
Live promotion remains subject to the existing eligibility gates.
