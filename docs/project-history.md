# Project history
## 22 November 2026
The workspace began with read-only Kite experiments and local INFY history used for charts, moving averages and early backtests. Raw price files are excluded from this repository and were removed from local working data during retention cleanup.
## 23 November 2026
| Milestone | Evidence |
| --- | --- |
| Kite login and scanner | The loopback login uses `kite.login_url()` and saves the access token locally. The project record reports working scanner prices and SMA signals. The offline chart tests exercise daily dates, close and SMA traces, and both crossover directions. |
| Paper-trading safeguards | Kite-powered paper-trading and DC external-data refresh are disabled. Offline simulations use synthetic inputs; account settings and journals remain local. |
| Historical SMA and DC research | Research code and permitted aggregate results remain. Raw candles, price series and detailed journals are excluded. [Research log](research/research-log.md) |
| DC 5% forward tracking | DC rules and offline shadow execution remain. Kite live data is not used for paper fills; simulation requires synthetic or explicitly authorized data. |
| Daily quant reporting | Local reporting remains, while DC external market-data refresh is paused. Raw-price reports and account records are excluded from the repository. |
| TradingView | The archived project record shows the revised Pine chart displaying DC confirmations and simulated fills on NSE INFY 1D after the feed-prefix fix. The record reports offline parity on matching candles. [Verification scope](research/research-log.md#tradingview-comparison) |
Offline tests use synthetic fixtures. Private journals, raw exports and screenshots are excluded. Historical performance figures are research results, not claims about future or live returns.
