"""Stress the fixed DC 5% rule on the saved NSE sample."""

import pandas as pd

import backtest_sma_6_30_kite as kite_batch
import backtest_sma_6_30_multiyear as multiyear
import backtest_with_costs as backtest
import dc_research as dc


SCENARIOS = {
    "base": dc.BASE_COSTS,
    "higher": dc.TradingCosts(2, 0.001),
    "severe": dc.TradingCosts(3, 0.0025),
}


def stress_test(histories, cutoff=dc.HOLDOUT_START):
    """Keep positions continuous inside each split when measuring calendar returns."""
    _, benchmark_yearly = multiyear.evaluate(histories, cutoff)
    yearly_reference = benchmark_yearly.groupby(["period", "calendar_year"])[
        ["sma_return_pct", "buy_hold_return_pct"]
    ].mean()
    phases = []
    calendar = []
    for scenario, costs in SCENARIOS.items():
        assumptions = {
            "cost_scenario": scenario,
            "dc_threshold_pct": dc.THRESHOLD * 100,
            "fee_multiplier": costs.fee_multiplier,
            "slippage_per_side_pct": costs.slippage_rate * 100,
        }
        for period in ("development", "out_of_sample"):
            for symbol, prices in histories.items():
                early = prices["date"].dt.date < cutoff
                development = prices.loc[early]
                active = development if period == "development" else prices.loc[~early]
                warmup = development if period == "out_of_sample" else None
                metrics, fills = dc.run_dc(active, warmup=warmup, costs=costs, track_daily=True)
                daily = metrics.pop("daily")
                phases.append({"stock": symbol, "period": period, **assumptions, **metrics})
                starting_equity = backtest.STARTING_CAPITAL
                sells = [fill for fill in fills if fill["side"] == "SELL"]
                for year, group in daily.groupby(daily["date"].dt.year):
                    row = multiyear.yearly_equity_metrics(group, starting_equity)
                    starting_equity = row["ending_equity_inr"]
                    closed = [fill for fill in sells if fill["fill_date"].year == year]
                    calendar.append({
                        "stock": symbol,
                        "period": period,
                        "calendar_year": year,
                        "first_session": group["date"].iloc[0].date(),
                        "last_session": group["date"].iloc[-1].date(),
                        **assumptions,
                        **row,
                        "closed_trades": len(closed),
                        "wins": sum(fill["realized_pnl_inr"] > 0 for fill in closed),
                    })

    by_period = pd.DataFrame(phases)
    by_market = pd.DataFrame(calendar).groupby(
        ["cost_scenario", "fee_multiplier", "slippage_per_side_pct", "period", "calendar_year"],
        sort=False, as_index=False,
    ).agg(
        first_session=("first_session", "min"),
        last_session=("last_session", "max"),
        stocks=("stock", "size"),
        mean_dc_return_pct=("return_pct", "mean"),
        mean_dc_max_drawdown_pct=("max_drawdown_pct", "mean"),
        mean_dc_cost_inr=("estimated_cost_inr", "mean"),
        closed_trades=("closed_trades", "sum"),
        winning_trades=("wins", "sum"),
    ).join(yearly_reference, on=["period", "calendar_year"])
    reference_return = by_market["buy_hold_return_pct"]
    by_market["market_condition"] = "Range-bound"
    by_market.loc[reference_return >= 5, "market_condition"] = "Rising"
    by_market.loc[reference_return <= -5, "market_condition"] = "Falling"
    by_market["dc_win_rate_pct"] = 100 * by_market["winning_trades"] / by_market["closed_trades"].where(
        by_market["closed_trades"] > 0
    )
    by_market = by_market.rename(columns={
        "sma_return_pct": "mean_sma_return_at_base_cost_pct",
        "buy_hold_return_pct": "mean_buy_hold_return_at_base_cost_pct",
    })
    return by_period, by_market


def main():
    by_stock, by_market = stress_test(dc.load_histories())
    for name, frame in (
        ("dc_cost_stress.csv", by_stock), ("dc_market_conditions.csv", by_market),
    ):
        kite_batch.save_csv_once(frame, dc.OUTPUT_DIR / name, float_format="%.4f")
    means = by_stock.groupby(["cost_scenario", "period"], sort=False)[
        ["dc_return_pct", "dc_max_drawdown_pct", "dc_total_estimated_cost_inr"]
    ].mean()
    print(means.to_string(float_format=lambda value: f"{value:.2f}"))
    print(f"Reports saved in {dc.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
