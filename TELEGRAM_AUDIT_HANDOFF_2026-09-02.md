# AUDIT REQUEST — Telegram UI, images, and reports (NSE algo trading bot)

## CONTEXT
Follow-up to `AUDIT_HANDOFF_2026-09-02.md` (the systemd deployment audit) on the
same repo: `/home/owner/Desktop/trading_robot`, NSE algorithmic trading system,
now running in PAPER mode as `trading-bot.service` on a freshly migrated host.
This document inventories everything the system sends to Telegram — commands,
images, text reports — compiled by grepping and reading the actual source
(file:line cited throughout), not from memory or naming conventions. Items the
investigation could not fully verify are flagged explicitly as such; treat
those as open questions for the audit, not settled facts.

## ARCHITECTURE (context for everything below)
Three separate Telegram bot/channel pairs, each its own token/chat-id pair in
`.env.template`: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (main bot),
`OPTION_BOT_TOKEN`/`OPTION_CHAT_ID` (option dashboard bot),
`GUARDIAN_BOT_TOKEN`/`GUARDIAN_CHAT_ID` (manual-trade guardian channel).
None use the installed `python-telegram-bot` (v22.8) or `pyTelegramBotAPI`
(v4.36.1) library APIs — despite both being in `requirements.txt`, grepping for
`CommandHandler(` returns zero real matches (only a same-named custom class in
test files). Everything is a **hand-rolled long-poll loop** hitting
`https://api.telegram.org/bot<token>/getUpdates` directly. Core dispatcher:
`telegram_commands.py:257` (`class TelegramCommandHandler`), poll loop at
`telegram_commands.py:571` (`_poll_loop`), dispatch at `telegram_commands.py:
621-694` (`_handle_update`) — strips the leading `/`, looks the token up in a
`self._handlers` dict (`telegram_commands.py:664-666`).

---

## 1. TELEGRAM COMMANDS

Registration: `telegram_commands.py:877-1151` (`_register_defaults`) calls
`self.register(...)` **258 times**, mapping to roughly **150 unique
`_cmd_*` handler methods** (many names alias the same handler — e.g.
`positions`/`open`/`trade` all → `_cmd_positions`). `/start`'s own onboarding
text claims "101 commands" (`telegram_commands.py:3153`) — that number is
stale/inaccurate against the actual 258 registered aliases.

A **second, independent** command handler is instantiated in-process for the
option-bot channel: `main_autonomous.py:2149` (`self._tg_cmd_option =
_TGCmd(...)`), registering its own smaller set — `signals`, `all`, `status`,
`edge`, `positions`, `optlots`, `help`, `report` (`main_autonomous.py:
2198-2405`).

**Verified by reading the handler body** (not inferred from the name):

| Command | File:line | Confirmed behavior |
|---|---|---|
| `/help` | `telegram_commands.py:1246` | Static curated command list (not the full 258). |
| `/start` | `telegram_commands.py:3130` | Static onboarding text; disclaimer "Educational signals only. Not SEBI registered advice." |
| `/status` | `telegram_commands.py:1264` | Reads `trade_manager` (daily P&L, open-trade count, mode) in a background thread, 3s timeout to avoid deadlock. |
| `/pnl [days]` | `telegram_commands.py:1306` | `pnl_reporting.format_today_pnl()` + `performance_analytics.format_telegram_report(days)` (default 30). |
| `/positions`, `/open`, `/trade` | `telegram_commands.py:1339` | Tries `WebSocketTracker` for live P&L first, falls back to `trade_manager.get_open_positions()`. |
| `/pause` | `telegram_commands.py:1435` | `config._PAUSED = True` — blocks new entries only; open positions continue. |
| `/resume` | `telegram_commands.py:1443` | `config._PAUSED = False`. |
| `/arm` | `telegram_commands.py:1451` | `dual_mode_engine.arm_live_trading()` — **arms real order placement for the day**, auto-disarms next day. |
| `/disarm` | `telegram_commands.py:1461` | `dual_mode_engine.disarm_live_trading()` — forces paper-only. |
| `/kill` | `telegram_commands.py:1469` | **Two-step confirm** (added 2026-08-19 per inline comment): bare `/kill` shows a warning + open-count and arms a 60s window; only `/kill CONFIRM` within that window calls `close_all_trades(reason="telegram_kill")`. |
| `/exit`, `/close` | `telegram_commands.py:4779` | Both literally call `_cmd_kill(args)` — same close-**all**-with-confirmation flow, **not** a single-position exit. Worth knowing if you ever want to close one leg only. |
| `/buy` | `telegram_commands.py:4773` | **No-op / informational only** — returns a static string pointing to `/signals`. Does **not** place a manual order. |
| `/sell` | `telegram_commands.py:4776` | **No-op / informational only** — returns a static string pointing to `/kill`/`/pause`. Does **not** place a manual order. |
| `/in`, `/out`, `/sl`, `/target`, `/protect`, `/hold`, `/gtrades` | `telegram_commands.py:1000-1006` | Routed via `_guardian_call` (`telegram_commands.py:1331`) into an in-process `TradeGuardian` instance, lazily started by `_ensure_guardian` (`telegram_commands.py:1316`) — explicitly built this way (per inline comment `:1317-1319`) to avoid a second `getUpdates` poller conflicting with the standalone `trade_guardian_bot.py`, since `trade_guardian.service` is currently disabled. |

**Not individually verified** (~140 remaining handlers — `/oichart`, `/oisr`,
`/strikeflow`, `/backtest`, `/train`, `/broker`, `/config`, etc.): only
skimmed at signature level this pass, flagged rather than guessed at.

---

## 2. IMAGES SENT TO TELEGRAM

All image sends funnel through one of: `AlertManager.send_photo`
(`alerts.py:806`), `TelegramCommandHandler.send_photo`
(`telegram_commands.py:362`), or a bare `requests.post(.../sendPhoto)`
(`manual_trade_tracker.py:888`, `trade_guardian_bot.py:186`).

| Trigger | File:line | What it is |
|---|---|---|
| Post-market option dashboard | `option_telegram_report.py:451`, fed by `daily_pipeline.py:130-136` and `post_market_ml.py:743-744` | 4-panel PNG (`generate_option_report()`) — strike outcomes, autotune weights, audit-gate score. `reports/option_post_market_YYYY-MM-DD.png`. |
| `/report` (option bot, on-demand) | `main_autonomous.py:2362-2364` | Same dashboard, generated live on request. |
| Executive EOD report | `executive_reporting.py:90`, via `daily_pipeline.py:117-123` | 4-subplot chart: cumulative signal outcomes, strategy expectancy leaderboard, top rejection reasons, text panel. |
| Signal broadcast card | `signal_broadcaster.py:352-353` | Per-signal card image, sent on background thread, file deleted after send. |
| Morning brief video (with fallback) | `off_hours_engine.py:383-429` → `voice_video_generator.py:291-336` | Builds market chart PNG + TTS MP3 + MP4; sends **video** if MP4 build succeeded, else falls back to the **chart PNG** (`voice_video_generator.py:317-329`). |
| OI flip alert | `oi_tracker.py:490-502` | Picture card of an OI conviction flip (`option_oi_chart.generate_oi_flip_alert_image`), falls back to text on failure. |
| `/oisr`, `/oichart` | `telegram_commands.py:2204-2287` | Strike-wise OI support/resistance profile / intraday OI line chart, on-demand. |
| `/direction NIFTY\|BANKNIFTY\|FINNIFTY` | `telegram_commands.py:1222-1234` | Technical/OI/PCR direction card, on-demand. |
| Manual trade card | `manual_trade_tracker.py:895-912` | Entry/LTP/SL/target status card, sent to manual channel + Guardian bot. Belongs to disabled `manual-tracker.service`. |
| Guardian position snapshot | `trade_guardian_bot.py:773` | Belongs to disabled `trade_guardian.service`. |
| Option decision journal chart | `option_decision_journal.py:537` | Chart/summary tied to the decision journal. |
| Weekly equity curve | `off_hours_engine.py:799-845` (`_run_weekly_equity_chart`) | **Flagged as possibly dead code** — repo-wide grep for callers found none; not present in either internal schedule table (`main_autonomous.py:1404-1429` or `:4552-4780`, nor `off_hours_engine.py:283-317`). Not asserted dead with certainty — worth a direct check. |

**Live evidence, checked against file timestamps on this host (2026-09-02):**
`daily_videos/` contains only `market_brief_20260902.mp3` +
`market_chart_20260902.png` for today — **no `.mp4`**. Consistent with the
MP4 encode step silently failing every day and the photo-fallback branch
firing instead, which "works" well enough that the underlying failure may be
going unnoticed. `reports/option_post_market_*.png` stops at **2026-08-21**
(the last date before the host move) — consistent with §4 below.

---

## 3. TEXT REPORTS SENT

| Report | Trigger | Content | Format |
|---|---|---|---|
| Daily performance report | `daily_performance_report.py:167`, sent via `:310-327`; invoked by `daily_pipeline.py --telegram` | Per-strategy rolling win rate, trend, pause/promote candidates | HTML |
| Executive EOD chart caption | `executive_reporting.py:90` | Caption only ("Executive EOD • {date}") — substance is in the attached image | Plain text |
| Option post-market evidence digest | `option_telegram_report.py` (`build_evidence_digest`), sent at `:454-457` paired with the dashboard photo | Strike-outcome win rates, autotune weights, audit-gate score | HTML |
| 3:35 PM daily P&L/journal report | `main_autonomous.py:1323-1354` — **runs inside `main_autonomous.py`'s own scheduler**, fires ~15:34-15:40 daily | Journal daily summary, rejection-reason stats, Drive-sync trigger | HTML |
| Weekly report | `main_autonomous.py:1311-1320`, Friday 16:00-16:10 | Weekly performance rollup | Not read in detail this pass |
| Morning intelligence brief | `off_hours_engine.py:354-360`, fired ~08:28+ daily via `main_autonomous.py:1408` | Pre-market VIX/global/sector/sentiment snapshot | Not read in detail this pass |
| Overnight gap warning | `off_hours_engine.py:361-381`, same ~08:30 trigger | Gap risk on open positions vs. stop-loss | HTML |
| Sector rotation refresh | `off_hours_engine.py:431-442` — reached only via `main_autonomous.py:4602-4610`, i.e. **weekend/holiday path only**, not the regular trading-day schedule | Top-sector rankings | HTML |
| Backtest/ML-training banners | `main_autonomous.py:4682-4730` (holiday path), `off_hours_engine.py:217-244` (weekend path) | Start/complete of nightly backtest / ML retrain | HTML, deduped |
| Nightly sync/Drive backup report | `main_autonomous.py:4749-4767`, holiday path only | Backup confirmation, daily download stats | HTML |

**Scheduling appears to be split across (at least) three mechanisms** inside
`main_autonomous.py`: a trading-day schedule (`:1375-1429`), a holiday/weekend
path (`:4544-4780`, `_run_holiday_off_hours_tasks`), and a third table-driven
version in `off_hours_engine.py:283-317` (`run_weekend_tasks`/
`run_holiday_tasks`), called from `main_autonomous.py:1475-1479`. Whether
these three overlap or conflict was **not fully reconciled** in this pass —
flagged as worth a dedicated look, not asserted as either safe or broken.

---

## 4. ENABLED vs. NOT CURRENTLY RUNNING (confirmed via `systemctl is-enabled`)

**Enabled and running:** `trading-bot.service`, `trading-bot-watchdog.service`
(both fixed for this host in the prior deployment session).

**Not installed on this host** (`systemctl is-enabled` → `not-found` for all):
`daily-pipeline.service`/`.timer`, `manual-tracker.service`,
`option-chain-recorder.service`, `post-market-ml.service`/`.timer`,
`trade_guardian.service`. Their `ExecStart=` lines **still reference the old
host path** `/home/sridhar/Projcts/trading_robot/...` — same class of bug
already fixed for the two core services, not yet applied to these six.

**Concrete consequence, confirmed via file timestamps:** the option dashboard
image+digest, the daily performance report, and the executive EOD chart — all
driven exclusively by the disabled `daily-pipeline`/`post-market-ml` services
— stopped firing on **2026-08-21** (the host-move date) and have not run
since, except on manual/on-demand invocation.

**Still active despite those services being disabled**, because they live
inside `main_autonomous.py`'s own process (under the enabled
`trading-bot.service`):
- 3:35 PM daily P&L/journal report.
- Morning schedule: gap warning (~07:45), morning video (~08:00 — confirmed
  live today via matching file timestamps), morning brief (~08:30), FNO ban
  check (~09:04+).
- The second, in-process option-bot command handler and its `/report` image.
- Manual-trade `/in /out /sl /target /protect /hold /gtrades` — deliberately
  reimplemented in-process specifically so they keep working with
  `trade_guardian.service` disabled (inline comment confirms this intent).

**Currently dark** (code exists, no live trigger given the disabled
services): `manual_trade_tracker.py`/`trade_guardian_bot.py` image+text alerts
(each needs its own `getUpdates` poller, i.e. its own disabled service). OI
charts/flip-alerts may also depend on `option-chain-recorder.service` for
fresh data — **not verified** whether there's an in-process fallback; flagged
rather than assumed dark.

---

## PLEASE AUDIT

1. **`/buy` and `/sell` are silent no-ops** that return informational text
   instead of placing (or even attempting) a manual order. Is that
   discoverable enough, or could a user reasonably believe they just placed a
   trade? Worth an explicit "this command does nothing, use X instead" reply
   rather than a generic pointer string?
2. **The six disabled service files still point at the old host path.** Should
   they be fixed now (same treatment as `trading-bot.service`) so they're
   ready to enable later, or intentionally left broken as a guard against
   someone reflexively running `systemctl enable --now` on all eight without
   thinking?
3. **Silent MP4-encode failure, masked by a working fallback.** The morning
   video pipeline appears to fail every day on this host (no `.mp4` ever
   produced, PNG fallback firing instead) with no visible alert about the
   failure itself. Is that failure logged/monitored anywhere, or would it run
   silently forever?
4. **Three overlapping scheduling mechanisms** for off-hours tasks in
   `main_autonomous.py` — worth reconciling to rule out double-sends or
   conflicting state, since some `off_hours_engine` methods look reachable
   from more than one path.
5. **Sector rotation refresh only fires on the weekend/holiday path**, not the
   regular trading-day schedule — intentional (weekly cadence by design) or a
   gap?
6. **`_run_weekly_equity_chart` has no found callers** — confirm whether it's
   genuinely dead code, and either wire it in or remove it.
7. **`/start`'s command count is stale** ("101 commands" vs. 258 actually
   registered) — cosmetic, but worth fixing so onboarding text isn't
   misleading.
8. Given `/exit` and `/close` both alias to the same close-**all** flow as
   `/kill`, is there any single-position close command? If not, is that an
   intentional simplification (fewer footguns) or a missing feature?
