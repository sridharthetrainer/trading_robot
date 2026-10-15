# External strategy research shortlist — 15 October 2026

Seven strategy families are catalogued with source URLs, existing implementation overlap, remaining work and explicit research-only status. No candidate was promoted, no runtime settings changed, no service restarted and no broker order submitted. This pass performed source and data qualification, not performance backtests. It did not review every strategy from every vendor.

Machine-readable catalog: [shortlist.json](reports/external_strategy_shortlist_2026-10-15/shortlist.json). Local implementation fingerprints make this review reproducible against the current source versions.

## Shortlist

Priority means order of investigation, not expected profit.

| Priority | Family and source | Existing overlap | Decision |
|---|---|---|---|
| 1 | [Tradetron ORB](https://tradetron.tech/community/t/30133/build-a-list-based-orb-opening-range-breakout-strategy), [Angel One ORB](https://www.angelone.in/knowledge-center/online-share-trading/opening-range-breakout-strategy) | Catalog A1 and existing ORB experiments | One explicit NSE 30-minute adaptation frozen in JSON; reconcile earlier ORB work before another experiment. |
| 2 | [Zerodha debit spread](https://zerodha.com/varsity/chapter/bull-call-spread/), [Groww debit spread](https://groww.in/p/bull-call-spread) | Catalog B1 | Structure already exists. Needs a predictive timing rule and simultaneous leg quotes. |
| 2 | [Zerodha credit spread](https://zerodha.com/varsity/chapter/bull-put-spread/), [Groww option structures](https://groww.in/blog/best-option-trading-strategies) | Catalog B2–B4 and selective-VRP backtest | Reconcile prior volatility-filter evidence; do not count an equivalent vendor description as a new independent strategy. |
| 3 | [Chartink NR7 scanner](https://chartink.com/screener/nr7-scanner) | NR7 modifier in signal_engine.py | Indexed listing found, full scanner page inaccessible. Exact formula and complete trade rules are unverified. |
| 3 | [Quantified Strategies RSI research](https://quantifiedstrategies.substack.com/p/rsi-trading-strategies) | Local RSI2 strategy | Exact published rules are paywalled. Local RSI2 is not an exact reproduction; US ETF results do not establish Indian option performance. |
| 3 | [Investing.com Bollinger education](https://www.investing.com/education/webinars/applying-bollinger-bands-to-your-charts-trading-13344) | Existing Bollinger option momentum and reversal backtests | Educational listing lacks a complete testable rule set. Treat breakout and reversal as separate hypotheses. |
| 3 | [Zerodha option structures](https://zerodha.com/varsity/modules/), [Groww structures](https://groww.in/blog/best-option-trading-strategies) | Catalog C1–C4 and D1–D4 | Straddles, strangles and condors already exist as research structures. Need explicit event/volatility triggers and all-leg execution evidence. |

The ORB adaptation is this project's research design. The Tradetron example uses US stock timing; its times and equity sizing are not copied into NSE options. The proposed version fixes a 09:15–09:45 IST opening range, completed-bar crossings, a signal cutoff before 11:30, one trade per day, exact ATM option selection, underlying-based stop/target and 15:10 exit. Its full rule set, quote requirements and ₹5,000 full-premium research cap are recorded in the JSON. It is not installed in the robot. No parameter sweep or income claim is attached.

## Data qualification

Read-only SQLite queries produced [local_data_inventory.json](reports/external_strategy_shortlist_2026-10-15/local_data_inventory.json).

- `options_nifty.db`: 3,272,753 end-of-day records, 2020-01-01 through 2026-10-13, with 386 distinct expiries. Schema contains daily close/settle/OI, not intraday bid/ask paths.
- `historical_options.db`: empty at inspection.
- NIFTY underlying candles: 26,249 five-minute bars and 36,816 one-minute bars. Underlying coverage alone does not supply option execution prices.
- Existing `single_leg_intraday_option_backtest.py` uses Black–Scholes pricing anchored to EOD settlement and a constant lot size across history. Its docstring identifies the historical lot-size limitation. Such output is modeled research, not observed intraday option fills.

The separate chain archive does contain actual stored L1 fields. The [quote-quality audit](reports/external_strategy_shortlist_2026-10-15/nifty_quote_quality.json) found:

| NIFTY observation | Result |
|---|---:|
| Snapshots marked live and successful | 4,111 |
| Distinct snapshot dates | 51 |
| Date range | 2026-06-29 to 2026-10-14 |
| Repeated rows_json payloads | 860 |
| Median gap within the same date | 221.5 seconds |
| 95th-percentile gap within the same date | 457 seconds |
| Option-side observations with positive uncrossed bid/ask and quantities | 137,500 |
| Missing/nonpositive L1 option-side observations | 1,906 |

Repeated payloads are observations, not proof of a feed fault: unchanged markets can repeat data. Counts are option-side observations across snapshots, not independent trades. The scanned row and summary keys do not include exchange feed timestamps. A live/source flag and a receipt timestamp do not prove the freshness of each underlying quote. No fill probability was inferred.

This sampling cannot establish the ordering of entry, stop and target events between snapshots. Sparse snapshots may support bounded exploratory studies with missing paths reported explicitly; they cannot support precise scalping P&L or HFT execution claims. Do not interpolate an option stop fill and report it as observed.

## Data-provider leads

[Dhan documents expired-options data](https://dhan.co/support/platforms/dhanhq-api/do-dhan-provides-expired-options-data-via-the-api-s/), [TrueData describes live and historical market-data features](https://www.truedata.in/downloads/TrueData_1MarketView_WhitePaper.pdf), and [FYERS documents a history API](https://fyers.in/products/api). Exact contract coverage, timestamp semantics, bid/ask history, licensing and entitlements still need qualification. Historical OHLC alone does not establish historical order-book availability. This task made no connection, subscription or purchase.

## Admission requirements and income probability

Each independent hypothesis needs explicit contract selection, dated lot/tick rules, next-observable entry, conservative executable prices, all charges, missing-data accounting and chronological validation. Count every parameter trial and family selection; remove vendor aliases from the count. Previously explored historical periods cannot later be called untouched holdouts. New observations begin after this research specification is fixed; sufficient complete calendar months and independent outcomes are still needed.

Report net expectancy, win rate with uncertainty, average win/loss, drawdown and adverse execution stress. Estimate the chance of achieving ₹30,000 from ₹500,000 only from a defensible net daily/monthly return process with serial dependence and sufficient history. At present that probability is **not estimable** from the qualified evidence. Payoff probability under a pricing model is not an empirically calibrated probability of earning the monthly target.

The immediate prerequisite for the ORB options experiment is an audit of synchronized, exchange-timestamped quote coverage and contract metadata. Do not enable live trading or tune parameters to force the desired income figure. The Telegram confirmation requirement remains unchanged.

## Verification

The shortlist's IDs are unique; all seven entries have sources and explicit non-live status; all nine referenced local implementation/evidence files exist and their SHA-256 fingerprints are recorded. All catalog probability fields are null. The quote audit ran successfully with read-only database connections and no broker imports. No production code was changed, so a production regression run was unnecessary for these research artifacts.
