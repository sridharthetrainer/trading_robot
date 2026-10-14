# Telegram option visuals — 14 October 2026

The dedicated option bot now has portrait signal cards, a four-contract visual
board, and a session dashboard that distinguishes recorded trades from
simulated signal outcomes. The images use the project's shared chart theme.

## What the user sees

- `/signals`: a visual board plus four compact text setups. Contracts retain
  their separate expiries; watch and blocked entries are labelled explicitly.
- `/report`: the signal board intraday, and the performance dashboard after
  15:35 IST. The text table remains available if a chart cannot be produced.
- Selection and generated-flow notifications: text with Dashboard, Signals,
  OI chart and Positions buttons, followed by a portrait card.
- Target and stop notifications: updated levels and a visual status card.
- Existing OPTION_SCALP notifications retain their underlying evidence chart.

Cards show entry, stop, targets, directional exposure, evidence status and
timestamp. The chart reads same-session stored premiums for the exact contract
and expiry from recognized market-data sources. Replay data and later quotes
are excluded. Missing history gets an explicit empty state; missing risk levels
remain unknown. Native setup scores are not converted into probabilities or
flow scores. BUY PE is displayed as bearish option exposure, rather than
being confused with a short put.

Multi-leg cards list the actual legs and quoted premiums. Credit, debit and
payoff-loss fields are labelled per unit and before fees, rather than as an
account P&L. Research, paper setups, recorded trades and closed signals have
separate status labels.

The session dashboard puts recorded net P&L, trade counts and charges above
stored index captures, decision counts, recorded trade results, and a distinct
simulated signal P&L panel. Times use IST; large P&L axes use readable lakh
labels instead of scientific notation.

## Configuration and delivery

`OPTION_VISUAL_CARDS=true` is the default. Set it to `false` to retain text
alerts without automatic cards. Watch images are capped at one per contract
per 30 minutes. Existing text deduplication and photo retry storage remain
in use. Visual generation failures do not invalidate a successful text alert.

Generated card files live in `reports/telegram_visuals`. The feature uses local
Matplotlib rendering and existing Telegram handlers; no new service or external
image-generation account is required. Loading the code into the running bot
requires the project's normal deployment/restart procedure.

## Preview and validation

Saved previews are in `reports/telegram_ui_preview`. The signal and board
examples are explicitly marked DEMO / SYNTHETIC. The session preview for
13 October uses stored project data. No preview was sent to Telegram.

144 focused tests passed for visual normalization, status and price semantics,
expiry-isolated read-only history, caption escaping and limits, PNG rendering,
message fallback, watch-image cooldown, navigation, photo retries, reports,
and surrounding option strategy behavior. Changed Python files compile and
their tracked diffs pass whitespace checks. No deployment or bot restart was
performed.
