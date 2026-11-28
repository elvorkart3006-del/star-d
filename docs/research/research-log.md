# Research log
## 22 November 2026 · Initial data and SMA experiments
The earliest saved inputs include short and one-year INFY daily histories. The exploratory scripts cover charts, moving averages and an early cost-aware SMA simulator.
## 23 November 2026 · SMA 6/30
The one-year INFY export covers 247 sessions. From ₹10,000 virtual starting capital, the saved SMA report records five closed trades, no winners, a net loss of ₹3,267.50 and 33.30% maximum drawdown. The seven-stock extension used the same 247 sessions per stock. SMA beat buy-and-hold on four of seven stocks and had a smaller maximum drawdown on six.
The five-year comparison covers INFY, TCS, WIPRO, HDFCBANK, ICICIBANK, SBIN and ITC. Each stock has 1,241 completed daily candles. The development slice has 994 sessions from 23 November 2021 to 21 November 2025. The final slice has 247 sessions from 24 November 2025 to 20 November 2026. The SMA settings stayed fixed for the comparison. The one-year study had already examined the final dates, so this is a chronological holdout with prior exposure to the period.
## 23 November 2026 · Directional Change 5%
`dc_research.py` starts neutral at the first daily close. A 5% rise from the running trough confirms a bullish reversal. A 5% fall from the running peak confirms a bearish reversal. The backtest carries signal history into the final slice and starts that evaluation with fresh cash. A close-generated signal can fill from the next session's open. The approach was inspired by the Directional Changes framework described by Amer Bakhach. [University of Essex thesis record](https://repository.essex.ac.uk/23552/)
Each stock starts with ₹10,000 in simulated capital. SMA and DC use estimated delivery charges and 0.05% adverse slippage per fill in the base case. Open positions are marked at the final close, and dividends are outside the model. Returns and closing-equity maximum drawdowns below are simple averages across the seven stocks. Win rate pools completed round trips.
| Period | Approach | Mean net return | Mean max drawdown | Closed trades | Win rate |
| --- | --- | ---: | ---: | ---: | ---: |
| Development | DC 5% | +9.76% | 24.49% | 130 | 41.54% |
| Development | SMA 6/30 | +2.17% | 22.13% | 150 | 34.67% |
| Development | Buy and hold | +39.33% | 28.26% | — | — |
| Chronological holdout | DC 5% | +3.28% | 15.03% | 33 | 33.33% |
| Chronological holdout | SMA 6/30 | −10.15% | 21.60% | 32 | 9.38% |
| Chronological holdout | Buy and hold | −9.87% | 31.02% | — | — |
Buy-and-hold takes a single position per stock and marks it at the end of the slice, so the saved comparison has no closed benchmark round trips. DC beat both alternatives on all seven stocks in the final slice and had positive net returns on three. Buy-and-hold earned the highest average return in development. Maximum drawdown is the largest decline from an earlier equity peak.
### Cost and market stress
| DC 5% scenario | Estimated fee multiplier | Slippage per fill | Development return | Holdout return | Holdout drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base | 1× | 0.05% | +9.76% | +3.28% | 15.03% |
| Higher | 2× | 0.10% | +1.04% | +1.07% | 16.24% |
| Severe | 3× | 0.25% | −10.99% | −2.17% | 17.84% |
In calendar 2024, a rising period under the seven-stock benchmark's classification, DC returned +4.54% versus +16.34% for buy-and-hold. In 2026 through 20 November, a falling period, DC returned +3.26% versus −10.46%. These are historical simulations over a small stock universe. Higher simulated costs reduced or erased the average advantage.
### TradingView comparison
The workspace fills export contains 335 historical DC fills. The archived comparison reports 344 matching confirmations and 335 matching fills in an offline replay on identical candles. The chart confirmation on INFY covered the corrected NSE feed validation and visible markers. Full seven-stock comparison on TradingView's own candles and alert delivery remain open. The Pine custom table uses the strategy's paper accounting. The optional native Strategy Tester preview has separate sizing and cost assumptions.
### Forward-test criteria
The dashboard tracks at least 60 complete trading sessions, at least 30 closed round trips, positive net expectancy, maximum observed closing drawdown of 20% or less, fresh-quote and candle monitoring for at least 19 of the last 20 expected sessions, and zero missed fills across 20 observable expected sessions. The saved 23 November interim report records enrollment and pending data. It does not establish any forward performance result.
### Local evidence
Raw candle and detailed fill files used for this research are excluded from the repository and were removed from local working data. Retained figures are aggregate derived outputs; do not publish outputs that permit reconstruction of underlying prices.

Data-use note: Zerodha permits temporary historical-candle analysis but prohibits permanent raw-data retention or redistribution, and live market data must not be used in simulated portfolios. Kite-powered paper fills and external DC refresh are disabled. Use synthetic inputs or an explicitly authorized feed for simulation.

## 28 November 2026 ? STAR-D forward signal outcomes

STAR-D scores confirmed frozen DC 5% BUYs. For each BUY it compares the close of the first, third and fifth completed NSE trading sessions after confirmation against the signal-session close. The scheduled trading-session sequence fixes each target date. A missing candle leaves a completed horizon unavailable and cannot shift the target. Future horizons stay pending until the 16:00 IST completed-candle cutoff. Kite candles are processed in memory. Local outputs retain only labels, dates, status details and aggregate counts. [NSE 2026 capital-market calendar](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf)

| Confirmed BUY on 26 November | One session ? 27 November | Three sessions ? 1 December | Five sessions ? 3 December |
| --- | --- | --- | --- |
| INFY | LOWER | PENDING | PENDING |
| WIPRO | LOWER | PENDING | PENDING |

The pilot covers two signals on one confirmation date. The labels measure close-to-close direction. They provide no estimate of return magnitude, next-open execution, transaction costs, or strategy profitability. The two 27 November execution opportunities remain unverified and outside the confirmed missed-fill count.

Seven-stock same-session completed-close coverage is **2 of 4 expected sessions** since 23 November. The 26 and 27 November checks passed for all seven stocks. The 23 and 25 November sessions lack same-session reports. Retrospective checks remain outside that count. The coverage milestone remains 19 of 20 expected sessions, and execution coverage is unverified.

For offline verification, run `python -B -m unittest -v test_star_d_outcomes_offline`. The five synthetic tests cover direction labels and aggregate counts, weekend session indexing, the cutoff, missing candles, and authentication failure. To run the pilot with valid Kite authentication, use `python -B star_d_outcomes.py` with the validated 26 November signal report held locally. For a different local signal report, use `--signals` and supply the expected NSE trading sessions in ascending order with repeated `--session` arguments. Local signal and outcome files stay outside source control.
