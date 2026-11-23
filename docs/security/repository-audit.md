# Proposed private repository and file audit

Audit date is 23 November 2026. This is a local proposal for approval.

## Proposed files to include

| Area | Paths |
| --- | --- |
| Top-level project files | `.gitignore`, `.env.example`, `requirements.txt`, `README.md`, `CHANGELOG.md`, `ROADMAP.md` |
| Documentation | `docs/project-history.md`, `docs/research/research-log.md`, `docs/setup/local-workflows.md`, `docs/security/repository-audit.md` |
| Dashboard, scanner and authentication | `trading_dashboard.py`, `multi_stock_scanner.py`, `kite_callback.py` |
| Paper simulation and reporting | `paper_trading.py`, `paper_trading_view.py`, `dc_forward.py`, `dc_forward_view.py`, `dc_daily_report.py`, `dc_report_metrics.py`, `dc_report_schedule.py`, `dc_report_view.py` |
| Backtests and DC research | `backtest_sma.py`, `backtest_sma_1year.py`, `backtest_with_costs.py`, `backtest_sma_6_30_kite.py`, `backtest_sma_6_30_multiyear.py`, `analyze_sma_performance.py`, `dc_research.py`, `dc_robustness.py` |
| Historical tools and diagnostics | `historical_data.py`, `historical_data_1year.py`, `moving_averages.py`, `plot_historical.py`, `stock_price.py`, `multiple_stock_prices.py`, `test_api_connection.py`, `test_kite.py` |
| Offline tests | `test_dc_daily_report_offline.py`, `test_dc_forward_offline.py`, `test_dc_research_offline.py`, `test_multiyear_backtest_offline.py`, `test_paper_trading_offline.py`, `test_scanner_offline.py` |
| TradingView | `dc_5_percent_strategy.pine` |

## Excluded local files

| Material | Finding |
| --- | --- |
| `.env`, other credential files and keys | The local environment has values for the Kite API key, API secret and access token. The tracked template would contain blank assignments. |
| `kite_login.py` | The legacy helper prints the broker login URL with the API key in its query string. The proposed source list includes the browser callback flow. |
| `data/` and dataset exports | The folder holds raw Kite/NSE candles, scanner exports, stock-level backtest data, fills and reports. All CSV, spreadsheet and common binary dataset formats used by the workspace are excluded. |
| Paper journals | `data/paper_trading.sqlite3` and `data/dc_forward.sqlite3` record private simulation activity. Databases, their sidecars and the archived copies stay local. |
| Scheduler XML, backups and logs | The generated task definition contains a Windows account SID and absolute Python and project paths. Source backups and logs are local artifacts. |
| Research PDFs, Word progress report and chart screenshots | Redistribution permission for the third-party files has not been established. The internal report contains author metadata and embedded screenshots. The repository uses a citation for the thesis and a sanitized milestone summary. |
| `Trading Trailer.py` and caches | The prototype is empty. Generated Python caches, local editors and virtual environments are excluded. |

## Security and rights findings

- The local audit checked for copies of the current Kite credential values outside `.env`, including common encodings and the text contents of the Word report archive. It found none. A Python source and backup review found no candidate literal credential assignments and no direct Kite order or GTT calls. This review covers the files on disk and the current values. Earlier credentials, image contents, external accounts and remote history were outside the scan.
- Kite Connect's permitted-access terms restrict using the APIs or live market data for virtual or mock trading. The paper workflow can use synthetic inputs or a feed with explicit simulation permission. Any claimed exception for Kite use should be confirmed in writing with the provider. [Kite Connect terms](https://kite.trade/terms/#api-usage)
- NSE's policy covers historical and live data and ties the right to redistribute it to the applicable agreement. Account-specific agreements were unavailable for this audit. Keep the local data and detailed derived outputs out of GitHub. Confirm that the proposed aggregate research summary is permitted for the intended recipients before upload. [NSE Data Sharing and Usage Policy](https://www.nseindia.com/static/market-data/nse-data-policy)
- The proposed GitHub visibility is **Private**, with access limited to the approved collaborators. Provider permissions still apply. [GitHub repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
- Review the first staged file list against the inclusion table after approval. Git's ignore rules apply to untracked files and can be overridden. [Git ignore documentation](https://git-scm.com/docs/gitignore)

## Verification

The ignore rules expose exactly the 44 paths in the inclusion table and passed 21 targeted checks for sensitive paths. The existing workspace passed all 35 offline tests. A copy of just the 44 proposed files, with the same installed dependencies, passed the 34 tests independent of private files. The remaining scanner compatibility test needs the local scanner CSV. [Offline test commands](../setup/local-workflows.md#run-the-offline-tests)
