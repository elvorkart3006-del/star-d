# Local workflows

## Set up Python

Run these commands from the project directory in PowerShell. The project was audited with Python 3.13.7. The existing dependency file uses unpinned versions.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`plot_historical.py`, an early standalone plotting script, also needs Matplotlib and the locally saved INFY CSV.

## Run the offline tests

The six modules below cover the scanner and chart, paper ledgers, research rules, reports, and scheduler generation. Run this command in the existing workspace to include the legacy scanner CSV compatibility test.

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_dc_daily_report_offline test_dc_forward_offline test_dc_research_offline test_multiyear_backtest_offline test_paper_trading_offline test_scanner_offline
```

In a clean checkout, run the five self-contained modules and eight scanner checks. The scanner filter skips `test_existing_legacy_csv_loads_without_missing_columns`, which needs the private `data/stock_scanner_results.csv` export.

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_dc_daily_report_offline test_dc_forward_offline test_dc_research_offline test_multiyear_backtest_offline test_paper_trading_offline
.\.venv\Scripts\python.exe -B -m unittest test_scanner_offline -k '*.ScannerOfflineTests.test_[!e]*'
```

`test_api_connection.py` is a live connectivity diagnostic. Use the explicit offline module list above.

## Open the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run trading_dashboard.py
```

Saved reports and journals appear when their local data is available. Use simulated inputs or authorized data for paper trading. The included dashboard code can read Kite quotes for paper workflows, so confirm the permitted use before connecting that workflow to a real feed. [Kite Connect terms](https://kite.trade/terms/#api-usage)

For separately authorized Kite access, create a local `.env` from `.env.example` if you do not already have one. Fill in your own API key and secret. Configure the registered redirect URI for the local callback at `http://127.0.0.1:8000/callback` and run `.\.venv\Scripts\python.exe kite_callback.py`. Complete login in the browser to let the callback write the new access token to your local `.env`.

## Archive DC reports

Use the local ledger and completed candles already available on the machine.

```powershell
.\.venv\Scripts\python.exe -B dc_daily_report.py --catch-up
```

Reports are written under `data/dc_daily_reports/`. Use `--date YYYY-MM-DD` to select a specific date. A report can record missing data, an incomplete session or a closure. Known 2026 holidays are built in. The local `data/dc_nse_calendar.csv` permits calendar overrides.

For an optional Windows schedule, export the XML using the same environment and import it in Task Scheduler on the same Windows account. The `--local-only` option uses saved data.

```powershell
.\.venv\Scripts\python.exe -B dc_report_schedule.py --local-only
```

The task definition runs at 18:00 IST and also at sign-in, with catch-up enabled. If the computer was off, the next run can report from whatever data is present. Sessions without data remain incomplete. For a separately authorized historical refresh, the CLI reports an expired token and can still write a report from available local data. Renew through the browser callback before retrying the refresh.
