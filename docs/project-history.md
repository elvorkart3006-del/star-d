# Project history

## 22 November 2026

The workspace began with read-only Kite experiments and daily INFY price files, including 21 sessions from 23 October to 20 November 2026 and 247 sessions from 24 November 2025 to 20 November 2026. Early scripts explored historical plots, moving averages, basic backtests and a multi-stock scanner. The local history files and source are available in the working project.

## 23 November 2026

| Milestone | Evidence |
| --- | --- |
| Kite login and scanner | The loopback login uses `kite.login_url()` and saves the access token locally. The project record reports working scanner prices and SMA signals. The offline chart tests exercise daily dates, close and SMA traces, and both crossover directions. |
| Manual paper trading | The local SQLite journal contains one simulated BUY and one simulated SELL dated 23 November. The manual simulator and the DC forward tracker use separate databases. |
| Historical SMA and DC research | Saved CSVs cover a one-year SMA study, a five-year development and holdout comparison on seven stocks, yearly results, and DC transaction cost stress tests. [Research log](research/research-log.md) |
| DC 5% forward tracking | The separate DC ledger records enrollment on 23 November. It held zero simulated fills at the audit. The system records confirmation dates and waits for eligible new-session quotes before a paper fill. |
| Daily quant reporting | A dated interim revision exists for 23 November. It records the initial ₹70,000 virtual capital and pending close data. Local CSV and HTML output and scheduler XML generation are verified. Scheduled execution on the user's computer remains unverified. |
| TradingView | The archived project record shows the revised Pine chart displaying DC confirmations and simulated fills on NSE INFY 1D after the feed-prefix fix. The record reports offline parity on matching candles. [Verification scope](research/research-log.md#tradingview-comparison) |

The repository preparation reran all 35 existing offline tests successfully in the local workspace. The evidence above comes from saved, private artifacts and the offline suite. The journals, exports and screenshots that support it remain local.
