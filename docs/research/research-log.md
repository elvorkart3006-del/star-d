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

The source CSV directories are `data/sma_6_30_kite/`, `data/sma_6_30_multiyear/` and `data/dc_research/`. The DC research directory also contains the cost and market stress outputs. The aggregates above are proposed for review. The files that contain candles, fills, daily values and per-stock outputs stay local. [Data and repository audit](../security/repository-audit.md)
