# Trading research and paper trading

A Streamlit dashboard and research workspace for seven NSE equities. The project includes an SMA scanner and chart, a manual paper-trading ledger, historical SMA 6/30 and Directional Change (DC) backtests, and separate forward paper tracking for fixed DC 5%. Trades and fills in this project are simulated.

This is the proposed contents of a private GitHub repository. The source and sanitized documentation can be reviewed locally. Credentials, market datasets, journals, generated reports, backups and reference documents are excluded.

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
| `trading_dashboard.py` | Streamlit scanner, SMA chart, manual paper trading, DC forward tracking and report views |
| `kite_callback.py` | Local browser login and token refresh for authorized Kite use |
| `backtest_sma_6_30_multiyear.py` | Historical SMA 6/30 evaluation with development, holdout and yearly output |
| `dc_research.py` and `dc_robustness.py` | Fixed-threshold DC research and transaction cost stress tests |
| `dc_daily_report.py` and `dc_report_schedule.py` | Local reporting and Windows Task Scheduler XML export |
| `dc_5_percent_strategy.pine` | TradingView Pine v6 implementation with on-chart paper results |

The offline tests use simulated inputs, with one legacy compatibility test that uses a local scanner export. Kite's published terms restrict using its APIs or live quotes for virtual or mock trading. Use synthetic data or a feed authorized for simulation when evaluating paper workflows. [Kite Connect terms](https://kite.trade/terms/#api-usage)
