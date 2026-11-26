# Changelog
Dates are in India Standard Time. This development record uses calendar dates because no Git versions have been assigned.
## 2026-11-23
- Added browser-based Kite authentication with a loopback callback and local token storage.
- Fixed the interactive SMA chart's date handling, price and moving-average traces, and crossover markers. Added offline scanner checks.
- Added manual paper trading with virtual capital, positions, estimated costs, simulated stop losses, risk controls and a local SQLite journal.
- Extended SMA 6/30 research to seven NSE stocks and a five-year evaluation with next-session-open fills, development and holdout slices, yearly summaries and a buy-and-hold benchmark.
- Added fixed DC 5% research, market-period and transaction cost stress tests, and a separate forward-paper ledger and dashboard view.
- Added dated DC reports, report history and readiness tracking, plus an optional Windows task definition exporter.
- Added the Pine v6 strategy and broadened the chart validation to accept supported NSE feed prefixes while checking currency, equity type, daily timeframe and standard candles.
- Prepared the repository documentation, placeholder environment file and ignore rules for review.
## 2026-11-22
- Established read-only Kite market data experiments, saved INFY daily history, and developed early SMA experiments and a multi-stock scanner.

## Compliance safeguards

- Disabled Kite-powered paper trading and DC external refresh.
- Prevented raw market-price fields from being retained in scanner and research exports.
- Replaced the scanner compatibility test dependency on private data with a synthetic fixture.
