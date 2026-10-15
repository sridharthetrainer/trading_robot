# Scalping inside the existing options Telegram bot

The interface is part of the existing options bot. No separate dashboard was published.

## Commands and buttons

- `/menu` → **⚡ Scalping** opens the scalping section.
- `/scalp` shows scanner/bot status and the latest three recorded scalp setups.
- `/scalpstatus` shows scanner configuration, automatic-alert setting, paper/live mode, market phase, entry-lock state and the recorded bot-status time. Old status snapshots are labelled stale.
- `/scalpsignals` shows the latest recorded OPTION_SCALP setups. Add an underlying, for example `/scalpsignals NIFTY` or `/scalpsignals BANKNIFTY`.
- `/optscalp` exposes the existing option-scalp performance report. This command was registered previously but excluded by the options bot's command whitelist. The `scalppnl` alias is also retained.

The scalping section provides Status, Setups, NIFTY, BANKNIFTY, Performance and Back buttons. Existing option report cards also link to this section. The options bot's help and Telegram command menu include scalping.

## What the signal view means

Each recorded setup shows its timestamp, bullish/bearish underlying direction, ATM option reference, underlying trigger, stop, targets and setup score. Old signals are marked **HISTORICAL / EXPIRED**; future/invalid timestamps are not claimed fresh. OPTION_SCALP does not verify expiry or executable option premium, so underlying price levels are explicitly labelled and are not shown as option-premium order levels. The score is not presented as a win probability; probability remains uncalibrated.

Views read at most the recent 8 MB of the existing append-only journal. This is declared in the view when the journal is larger; absence from this window does not mean no historical signal ever existed. Duplicated source IDs are displayed once. HTML is escaped, malformed records are ignored and missing data stays unavailable.

These commands do not scan for new signals, write the journal, place orders or change configuration. Existing automatic-alert and scanner settings are preserved. In the captured local preview the scanner was enabled, automatic scalp alerts were disabled and the bot was in PAPER mode.

## Validation and activation

Focused view, navigation, alert-gate, visual-card and command checks passed: **41 tests**. These cover missing journals, stale/future signals, symbol filtering, bounded reads, HTML escaping, duplicate records, command registration and actual options-bootstrap whitelisting. Dependency coverage and changed-file static checks passed.

The complete isolated regression also passed: **1,160 tests**, with 12 warnings, in approximately 86 seconds.

The source changes require the running trading-bot process to reload through its normal deployment/restart workflow before the new menu and commands are available. No trading service was restarted and no live Telegram message was sent during this work.

Recorded preview and test logs: [reports/telegram_scalping_2026-10-15](reports/telegram_scalping_2026-10-15/).
