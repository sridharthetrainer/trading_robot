# Trading Robot Competitive Audit

Date: 2026-06-17

Scope: local codebase audit plus comparison with related open-source trading
engines and Indian algo-trading products. This is an engineering/product audit,
not financial advice.

## Executive Summary

This project is far more capable than a typical retail trading script. It has a
large NSE/India-specific feature set: Angel One integration, options selection,
multi-timeframe signals, option-chain intelligence, learning loops, Telegram
ops, risk managers, validation harnesses, and many strategy modules. Locally it
is closer to a custom quant platform than to a single strategy bot.

Its competitive weakness is not breadth. The weakness is productization and
institutional proof:

1. Backtest-to-live parity is weaker than engines such as QuantConnect LEAN and
   NautilusTrader.
2. The codebase has many strategies, but only a small subset has measured edge;
   several reports still mark the live edge as unvalidated or marginal.
3. Compliance metadata for India's 2025-2026 retail algo framework is not a
   first-class concept.
4. Operational UX is Telegram/file/log centered, while competitors provide
   dashboards, managed deployment, audit trails, broker onboarding, and
   monitoring.
5. Test coverage exists, but the most important live-risk invariants need
   explicit automated regression tests.

Recommended near-term posture: keep the system in paper or tightly capped live
mode until the validation gate, compliance envelope, and execution audit trail
are upgraded.

## Local System Snapshot

- Code size: roughly 462 tracked files and 135k+ lines of Python.
- Primary domain: Indian equities, indices, and options.
- Primary broker/data path: Angel One SmartAPI, with NSE/direct and fallback
  data utilities.
- Main live orchestrator: `live_signal_engine.py`.
- Strategy core: `signal_engine.py` and many specialized strategy/backtest
  modules.
- Ops surfaces: Telegram alerts, systemd services, local JSON/SQLite state,
  diagnostics scripts.
- Validation surfaces: `walk_forward_backtest.py`, `validation_harness.py`,
  `strategy_validation_report.json`, `edge_report.json`, `validation_results.json`.

## Strengths Versus Competitors

### India-Specific Alpha Surface

The project contains India/NSE details that generic platforms do not give out
of the box: FII/DII context, F&O ban handling, option-chain fallbacks, India VIX
gates, participant OI, sector rotation, index events, manual-trade protection,
and Angel-specific token/fill handling.

Compared with QuantConnect, Freqtrade, Backtrader, vectorbt, or NautilusTrader,
this project is much more tailored to NSE options workflows. That is a real
advantage if the target is Indian retail/semi-professional trading rather than
global multi-asset research.

### Risk Layer Has Become Real

The current source shows that older roadmap gaps have been partially addressed:

- `LiveSignalEngine` now initializes `DailyLossLimitManager` and resets day
  state.
- Scan cycles check the daily loss lock before work.
- India VIX is read once per cycle and used to block new option buys.
- Broker order fills are polled and rejected orders are surfaced.
- A circuit breaker pauses entries after repeated execution failures.

These are not cosmetic. They move the project closer to a deployable trading
system.

### Learning/Measurement Loop Exists

The project logs candidates, tracks rejections, labels outcomes, stores model
artifacts, and generates edge reports. This is more advanced than most no-code
retail products, which often expose only user-facing backtests.

## Weaknesses Versus Competitors

### 1. Backtest-to-Live Parity

QuantConnect and NautilusTrader are designed around one engine path for
research, backtest, and live trading. QuantConnect advertises fee/slippage/spread
adjusted backtests and live deployment through many integrations. NautilusTrader
emphasizes identical core systems between backtest and live plus deterministic
replay.

This project has many separate backtest scripts and live modules. That makes
innovation fast, but it raises the risk that a strategy behaves differently in
research and production.

Priority fix:

- Create a canonical strategy interface consumed by both backtest and live.
- Make slippage, brokerage, STT, spread, margin, lot-size, and order rejection
  models shared libraries rather than per-backtest assumptions.
- Add a replay harness: feed a recorded market-data session into the live
  signal path and assert the same decisions are reproduced.

### 2. Validation Still Does Not Justify Broad Live Automation

Current repo reports show mixed/weak evidence:

- `validation_results.json` has multiple strategy verdicts as `FAIL`.
- `edge_report.json` labels only `mean_reversion` as confirmed, while many
  strategies are marginal, negative, or insufficient data.
- `modifier_edge_report.json` / `edge_report.json` explicitly caution that win
  rate alone is not profit and live use is unvalidated.

Priority fix:

- Promote only strategies with enough sample size, cost-adjusted expectancy,
  stable parameters, and out-of-sample pass status.
- Force paper-only for negative or insufficient-data strategies.
- Add a "live eligibility manifest" generated from validation output. The live
  engine should read this file and block non-eligible strategies by default.

### 3. Compliance Is Not Yet a Product Feature

NSE's current retail algo framework has moved toward exchange/provider
empanelment, static IP/API controls, algo tagging, audit trails, and broker-side
risk controls. If this system is only for personal use below broker/API limits,
the burden may be smaller; if it becomes a signal service, marketplace product,
or third-party managed algo, compliance becomes central.

Priority fix:

- Add `algo_id`, `strategy_version`, `code_hash`, `config_hash`, `signal_id`,
  `user_approval_mode`, `execution_mode`, `broker_api_key_id`, `source_ip`, and
  `order_trace_id` to every signal/order/trade record.
- Build immutable daily audit exports.
- Add explicit "personal automation" vs "provider product" mode in config.
- Confirm broker requirements for static IP, tagging, session expiry, and order
  rate limits before any live scale-up.

### 4. Operational UX Trails Competitors

Tradetron, AlgoTest, Streak, DhanHQ, and QuantConnect compete with dashboards,
managed hosting, broker onboarding, visual strategy builders, reporting, and
easy monitoring. This project is powerful but operator-heavy.

Priority fix:

- Promote `remote_dashboard.py` or create a small dashboard showing live mode,
  broker status, open risk, daily P&L, strategy eligibility, rejection reasons,
  last data timestamp, open orders, and kill switch status.
- Add one-click paper/live lock controls with confirmation and audit logging.
- Add a daily "can trade today?" preflight card.

### 5. Broker Redundancy Is Thin

The requirements include `kiteconnect`, optional Upstox/Dhan comments, and
broker abstractions exist, but the project is operationally Angel-first.
Competitors win on broker breadth and managed broker integrations.

Priority fix:

- Finish one alternate broker adapter, preferably Zerodha Kite Connect or DhanHQ.
- Use it initially for data/fill reconciliation and then failover.
- Add broker health scoring and automatic live-order disable when broker state
  diverges from local state.

## Competitor Comparison

| Category | Local project | Strong competitors | Gap |
|---|---|---|---|
| India/NSE options intelligence | Strong | AlgoTest, Tradetron, DhanHQ, Sensibull-style tools | Local system is deep but less polished |
| No-code strategy creation | Weak | Streak, Tradetron, AlgoTest | Needs UI or config-driven builder |
| Backtesting credibility | Medium | QuantConnect, NautilusTrader, vectorbt, AlgoTest | Needs unified engine and reproducible reports |
| Live execution infra | Medium | QuantConnect, DhanHQ, broker APIs, Tradetron | Needs hosted reliability, audit, broker redundancy |
| ML/research depth | Medium-high | QuantConnect, vectorbt, custom quant stacks | Needs clearer experiment tracking and feature store |
| Compliance readiness | Low-medium | NSE-empanelled providers, broker platforms | Needs algo IDs, audit trails, provider-mode controls |
| Monitoring/dashboard | Low | QuantConnect, Freqtrade WebUI, DhanHQ/AlgoTest/Tradetron | Needs real-time dashboard |
| Community/ecosystem | Low | Freqtrade, QuantConnect, Backtrader, vectorbt | Private codebase; docs and onboarding limited |
| Personal customization | High | Open-source frameworks | This is a major advantage |

## Product Positioning

Best-fit positioning:

"A private, India-first systematic trading workstation for NSE equities and
options, with strong local intelligence and guarded automation."

Poor positioning:

"A ready-to-sell automated algo marketplace product."

To sell or operate for others, the project needs compliance hardening, provider
registration planning, audit logs, user-facing reporting, capital segregation,
and support processes.

## Related Project Benchmarks

### QuantConnect / LEAN

Benchmark strength: unified research, backtesting, optimization, and live
deployment; large datasets; fee/slippage/spread adjusted modeling; many broker
integrations.

What to copy:

- One canonical engine path from research to production.
- Dataset/version metadata in every backtest.
- Cloud/local reproducible job records.

### Freqtrade

Benchmark strength: open-source Python bot with dry-run/live modes, Telegram,
WebUI, backtesting, plotting, hyperoptimization, and ML optimization.

What to copy:

- Clear configuration conventions.
- WebUI for control/monitoring.
- Strong dry-run default and explicit live transition workflow.

### NautilusTrader

Benchmark strength: production-grade event engine, backtest/live parity,
deterministic replay, durable event logging, fast execution core.

What to copy:

- Event log as the source of truth.
- Replayable sessions.
- Separate strategy logic from venue/execution plumbing.

### Backtrader / vectorbt

Benchmark strength: fast research and backtesting ergonomics. vectorbt is
especially strong for large parameter sweeps.

What to copy:

- Faster parameter-sweep workflows.
- Standard portfolio metrics and plots.
- Lightweight strategy notebooks or reports.

### Indian Platforms: Tradetron, AlgoTest, Streak, DhanHQ, AlgoBulls

Benchmark strength: onboarding, no-code/low-code workflows, strategy
marketplaces, paper/live deployment, broker integrations, reporting, and
regulatory positioning.

What to copy:

- Strategy health cards.
- Preflight checks.
- Broker connection UX.
- Portfolio-level options backtesting reports.
- Compliance-ready audit trail.

## Priority Roadmap

### P0: Do Before Wider Live Use

1. Generate a live eligibility manifest from validation reports.
2. Block all negative/insufficient strategies in live mode by default.
3. Add regression tests for daily loss lock, VIX option-buy blocking, rejected
   order handling, circuit breaker pause, and paper/live order routing.
4. Add immutable order/signal audit fields: strategy version, code hash, config
   hash, signal ID, broker order ID, fill status, rejection reason.
5. Run a complete paper session replay and compare local state to broker/order
   book snapshots.

### P1: Become Competitive With Serious Retail Platforms

1. Build a real dashboard for risk, signals, orders, broker status, and
   validation eligibility.
2. Finish alternate broker support for DhanHQ or Zerodha.
3. Consolidate strategy interfaces so live and backtest share the same signal
   function.
4. Add cost-adjusted expectancy and confidence intervals to every strategy card.
5. Create daily compliance/audit exports.

### P2: Become Competitive With Quant Frameworks

1. Add deterministic replay from recorded candles/ticks/order events.
2. Add experiment tracking for ML features, labels, models, and validation runs.
3. Add portfolio-level optimization and correlation exposure limits in the live
   engine.
4. Build vectorized research sweeps for fast parameter exploration.
5. Version all market data inputs and cache snapshots.

## Build vs Buy Conclusion

Keep building if the goal is a private edge engine for Indian markets. The
project has enough local domain knowledge that replacing it with a generic
platform would lose useful nuance.

Buy or integrate if the goal is polished commercial deployment. Competitors
already solve hosting, onboarding, broker breadth, compliance workflow, and UX.

The best path is hybrid:

- Keep this project as the alpha/risk brain.
- Borrow product patterns from Freqtrade and AlgoTest.
- Borrow engine discipline from NautilusTrader and QuantConnect.
- Add broker/compliance hardening before any serious live scaling.

## Sources Checked

- QuantConnect: https://www.quantconnect.com/
- Freqtrade docs: https://www.freqtrade.io/en/stable/
- NautilusTrader: https://nautilustrader.io/
- Backtrader: https://www.backtrader.com/
- vectorbt: https://vectorbt.dev/
- Zerodha Kite Connect: https://zerodha.com/products/api/
- DhanHQ: https://dhanhq.co/
- Tradetron: https://tradetron.tech/
- AlgoTest docs: https://docs.algotest.in/
- Streak: https://www.streak.tech/
- AlgoBulls: https://algobulls.com/
- NSE empanelled algo providers page: https://www.nseindia.com/static/trade/empanelled-algo-providers-exchange
