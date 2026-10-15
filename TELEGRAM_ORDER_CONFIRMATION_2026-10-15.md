# Confirmation-based options support

Implemented locally; running services have not been restarted. No broker orders were sent during development or verification. Existing live/paper configuration was preserved.

## Use

In the options Telegram bot, open Scalping → Prepare Order, or send:

`/prepare EXACT_OPTION_SYMBOL LOTS LIMIT_PRICE`

Use an exact, unexpired NFO option symbol, 1–2 lots, and a valid tick-aligned limit price. Review the displayed mode, contract, quantity, limit, current ask/quote age and full premium exposure. Choose **Confirm PAPER BUY** or **Confirm LIVE BUY**, or cancel the draft. Confirmation expires after 60 seconds. `/orderstatus` lists the latest five reviews and their submission states.

The first version supports single-leg BUY LIMIT INTRADAY entries. It does not attach a stop order. Broker acknowledgement is explicitly distinct from a fill. PAPER confirmation records a simulation acknowledgement, without sending an order or claiming a simulated fill/position. Existing positions and spread exits should be managed in the broker terminal. Existing automatic protective exits were not changed.

## Authorization and durability

Orders require an exact Telegram sender user ID. A positive private-chat ID is the fallback owner. Group/channel deployments must set `OPTION_ORDER_OWNER_ID` to the owner's positive Telegram user ID. Group membership, an empty owner setting, and a matching group ID do not authorize orders.

Drafts are stored in `telegram_order_drafts.db`, scoped to bot identity and owner. Confirmation atomically claims the draft before checking and submitting. Repeated taps and process restarts cannot reuse a claimed draft. An unresolved SUBMITTING or UNCERTAIN record blocks further confirmations across this database. These states require operator reconciliation against the broker order book, using the TGCF order tag and stored review ID; there is deliberately no Telegram retry/unlock button. Do not delete the database to bypass reconciliation.

Legacy immediate `exec:` buttons now explain how to prepare a review. The legacy quick-execution function has no order side effects. Immediate spread-close callbacks are disabled; there is no fallback from a spread to an unreviewed naked long.

## Checks

Preparation and confirmation validate runtime mode, the trading-manager lock, trading session, local contract metadata, lot/freeze/tick rules, exchange-feed timestamp and actual sell depth. Missing depth is never synthesized. Quotes must be at most 10 seconds old; the limit must cover the ask and remain within 1% of it. The exact contract/token, side, quantity, product, exchange, price and mode remain bound to the reviewed draft. No automatic resizing/repricing occurs.

The initial review cap is ₹25,000 full premium per entry (5% of the stated ₹500,000 capital), with a ₹5,000 daily realized-loss lock. Missing manual risk database/schema blocks preparation. LIVE additionally requires the existing evidence-based readiness gate, enabled native broker flags, freshly queried cash with a 2% reserve, no broker positions and no outstanding broker orders. These are conservative initial constraints, not profit optimization. Existing readiness evidence does not qualify the system for live trading.

Submission uses the existing AngelOne order wrapper with a fixed limit and quantity, retaining its paper flags and pacing. Returned fallback fill prices are ignored. A timeout or missing/ambiguous acknowledgement is persisted as UNCERTAIN and is never automatically retried. External broker actions can race local checks; broker fills/protection still require monitoring. This is a manual assistance flow, not an HFT OMS or a guarantee of ₹30,000 monthly income.

SmartAPI FULL quote fields were checked against the [official documentation](https://smartapi.angelone.in/docs). Verification uses fake gateways/quotes and an isolated checkout with network access blocked; no production broker integration was exercised.

## Validation

See `reports/option_order_confirmation_2026-10-15/focused_tests.txt` and `regression_tests.txt`. Focused coverage includes owner/group spoofing, exact quantity/price binding, concurrency, replay, expiry during recheck, cancellation, crash persistence, uncertain acknowledgements, stale/future/missing quotes, daily loss/cash/exposure gates, mode changes and honest paper/fill messages.

Results: full isolated regression **1,196 passed, 12 warnings**; final focused checks **53 passed**, including three additional tests added after the full suite started. Source compilation and whitespace checks passed for the changed implementation files. Existing unrelated runtime CSV whitespace was left untouched.

Subsequent hardening: account-bound reviews, broker-safe tags, ACKNOWLEDGED-state locking, exact terminal reconciliation, combined manual/ALGO loss checks and `/probability` are described in [the ordered fixes report](ORDERED_FIXES_2026-10-15.md). Service activation still requires a controlled restart.
