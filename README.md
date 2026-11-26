# Trading research and paper trading

A Streamlit research dashboard for NSE equities, with SMA and Directional Change (DC) analysis. Kite-powered paper-trading workflows are disabled. Historical candles may be processed temporarily for research, but raw Kite market data must not be permanently stored or redistributed. Scanner exports retain derived signals only; local journals and old data are excluded from this repository.

This repository contains source code and sanitized documentation. Credentials, market datasets, journals, generated reports, backups and reference documents are excluded. Do not use Kite live quotes in paper trading or simulated portfolios. Use synthetic inputs or a market-data source explicitly authorized for simulation.

## Start here

- [Local setup and offline tests](docs/setup/local-workflows.md)
- [Project history for 22–23 November 2026](docs/project-history.md)
- [Research methods, results and limitations](docs/research/research-log.md)
- [Changelog](CHANGELOG.md)
- [Roadmap](ROADMAP.md)
- [Repository audit and file-by-file inclusion proposal](docs/security/repository-audit.md)

## Main entry points

| File | Purpose |
| --- | --- |
| `trading_dashboard.py` | Streamlit scanner, SMA chart, disabled paper-trading panel, DC forward tracking and report views |
| `kite_callback.py` | Local browser login and token refresh for authorized Kite use |
| `backtest_sma_6_30_multiyear.py` | Historical SMA 6/30 evaluation with development, holdout and yearly output |
| `dc_research.py` and `dc_robustness.py` | Fixed-threshold DC research and transaction cost stress tests |
| `dc_daily_report.py` and `dc_report_schedule.py` | Local reporting and Windows Task Scheduler XML export |
| `dc_5_percent_strategy.pine` | TradingView Pine v6 implementation with on-chart paper results |

Offline tests use synthetic inputs and do not contact Kite. Scanner exports omit exact closes, LTPs and quote timestamps. Paper-trading screens and DC external-data refreshes are paused. Kite historical data can be used temporarily for analysis only; it must not be permanently stored or redistributed. Derived research outputs may be retained only when they cannot be used to reconstruct raw prices. [Kite Connect terms](https://kite.trade/terms/#api-usage)
