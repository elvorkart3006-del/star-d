# Local workflows
## Set up Python
Run these commands from the project directory in PowerShell. The project was audited with Python 3.13.7. The existing dependency file uses unpinned versions.
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```
`plot_historical.py`, an early standalone plotting script, also needs Matplotlib and the locally saved INFY CSV.
## Run the offline tests
Run the full offline suite with synthetic inputs. The scanner compatibility test creates and removes its own synthetic derived CSV.
```powershell
.\.venv\Scripts\python.exe -B -m unittest test_dc_daily_report_offline test_dc_forward_offline test_dc_research_offline test_multiyear_backtest_offline test_paper_trading_offline test_scanner_offline test_market_data_retention_offline test_dc_shadow_execution_offline
```
All tests run without private market-data files. Their scanner fixture is synthetic and temporary.
`test_api_connection.py` and `test_kite.py` are not offline tests. Do not run them as part of this suite; they may access a broker account. Kite live quotes must not be used in simulated portfolios. Historical candles must be processed temporarily and not saved or redistributed.
## Open the dashboard
```powershell
.\.venv\Scripts\python.exe -m streamlit run trading_dashboard.py
```
Saved reports and journals appear when local files are available. The dashboard paper-trading screen is disabled. Do not use Kite live quotes in paper or simulated portfolios.
For separately authorized Kite access, create a local `.env` from `.env.example` if you do not already have one. Fill in your own API key and secret. Configure the registered redirect URI for the local callback at `http://127.0.0.1:8000/callback` and run `.\.venv\Scripts\python.exe kite_callback.py`. Complete login in the browser to let the callback write the new access token to your local `.env`.
## Archive DC reports
Use the local ledger and completed candles already available on the machine.
```powershell
.\.venv\Scripts\python.exe -B dc_daily_report.py --catch-up
```
Reports are written under `data/dc_daily_reports/`. Use `--date YYYY-MM-DD` to select a specific date. Reports use available saved/derived inputs; the DC external market-data refresh is paused. Do not permanently store or redistribute raw Kite candles.
For an optional Windows schedule, export the XML using the same environment and import it in Task Scheduler on the same Windows account. The `--local-only` option uses saved data.
```powershell
.\.venv\Scripts\python.exe -B dc_report_schedule.py --local-only
```
The task definition runs at 18:00 IST and also at sign-in. It uses saved derived data and does not fetch Kite market data.
