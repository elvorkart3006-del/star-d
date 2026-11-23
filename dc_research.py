"""Offline, long-only Directional Changes baseline for the saved NSE sample.

The confirmation rule follows Chapter 4 of Amer Bakhach's 2018 thesis:
https://repository.essex.ac.uk/23552/1/PhDThesis_AmerBakhach_Final.pdf
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

import backtest_sma_6_30_kite as kite_batch
import backtest_sma_6_30_multiyear as multiyear
import backtest_with_costs as backtest


THRESHOLD = 0.05  # Fixed for all stocks and both periods.
END = date(2026, 11, 22)
START, HOLDOUT_START = multiyear.evaluation_dates(END, years=5)
RUN_NAME = f"{START}_{END}"
HISTORY_DIR = multiyear.OUTPUT_ROOT / RUN_NAME
OUTPUT_DIR = backtest.DATA_DIR / "dc_research" / RUN_NAME


@dataclass(frozen=True)
class TradingCosts:
    fee_multiplier: float = 1.0
    slippage_rate: float = backtest.SLIPPAGE_RATE


BASE_COSTS = TradingCosts()


@dataclass(frozen=True)
class Confirmation:
    direction: str
    day: pd.Timestamp
    close: float
    extreme_day: pd.Timestamp
    extreme_close: float


def directional_changes(prices, threshold=THRESHOLD):
    """Yield confirmations from consecutive, completed closes in date order."""
    if not 0 < threshold < 1:
        raise ValueError("The DC threshold must be a fraction between zero and one.")
    change = Decimal(str(threshold))
    up_factor = 1 + change
    down_factor = 1 - change
    high = None
    low = None
    trend = None

    for session in prices.itertuples(index=False):
        close = Decimal(str(session.close))
        current = (session.date, close)
        if high is None:
            high = low = current
        elif trend != "up" and close >= low[1] * up_factor:
            yield Confirmation("up", session.date, float(close), low[0], float(low[1]))
            trend, high = "up", current
        elif trend != "down" and close <= high[1] * down_factor:
            yield Confirmation("down", session.date, float(close), high[0], float(high[1]))
            trend, low = "down", current
        else:
            if trend != "down" and close > high[1]:
                high = current
            if trend != "up" and close < low[1]:
                low = current


def run_dc(prices, warmup=None, *, threshold=THRESHOLD, costs=BASE_COSTS, track_daily=False):
    """Build DC state using prior closes, then simulate from fresh cash."""
    if prices.empty:
        raise ValueError("At least one DC evaluation session is required.")
    inputs = prices if warmup is None else pd.concat([warmup, prices], ignore_index=True)
    if not inputs["date"].is_monotonic_increasing or not inputs["date"].is_unique:
        raise ValueError("DC warm-up and evaluation dates must be unique and in ascending order.")
    events = {event.day: event for event in directional_changes(inputs, threshold)}
    previous_dates = inputs["date"].shift(1).iloc[-len(prices):]
    cash = backtest.STARTING_CAPITAL
    shares = 0
    entry_cost = 0.0
    fills = []
    daily = []
    for session, previous_day in zip(prices.itertuples(index=False), previous_dates, strict=True):
        event = events.get(previous_day)
        costs_today = 0.0
        if event is not None:
            fill = next_open_fill(session, event, cash, shares, costs=costs)
            if fill is not None:
                costs_today = fill["charges_inr"] + fill["slippage_inr"]
                turnover = fill["fill_price_inr"] * fill["shares"]
                if fill["side"] == "BUY":
                    entry_cost = turnover + fill["charges_inr"]
                    cash -= entry_cost
                    shares = fill["shares"]
                else:
                    proceeds = turnover - fill["charges_inr"]
                    fill["realized_pnl_inr"] = proceeds - entry_cost
                    cash += proceeds
                    shares = 0
                    entry_cost = 0.0
                fills.append(fill)
        daily.append((session.date, cash + shares * session.close, costs_today))

    daily = pd.DataFrame(daily, columns=["date", "equity", "estimated_cost"])
    equity = multiyear.yearly_equity_metrics(
        daily, backtest.STARTING_CAPITAL
    )
    closed = [fill["realized_pnl_inr"] for fill in fills if fill["side"] == "SELL"]
    wins = sum(pnl > 0 for pnl in closed)
    metrics = {
        "dc_threshold_pct": threshold * 100,
        "dc_completed_trades": len(closed),
        "dc_winning_trades": wins,
        "dc_win_rate_pct": 100 * wins / len(closed) if closed else None,
        "dc_net_pnl_inr": equity["net_pnl_inr"],
        "dc_realized_pnl_inr": sum(closed),
        "dc_open_pnl_inr": equity["net_pnl_inr"] - sum(closed),
        "dc_open_shares": shares,
        "dc_trading_charges_inr": sum(fill["charges_inr"] for fill in fills),
        "dc_slippage_inr": sum(fill["slippage_inr"] for fill in fills),
        "dc_total_estimated_cost_inr": equity["estimated_cost_inr"],
        "dc_return_pct": equity["return_pct"],
        "dc_max_drawdown_pct": equity["max_drawdown_pct"],
        "dc_prior_state_sessions": 0 if warmup is None else len(warmup),
    }
    if track_daily:
        metrics["daily"] = daily
    return metrics, fills


def next_open_fill(session, event, cash, shares, *, costs=BASE_COSTS):
    """Turn a prior confirmation into a cash-funded simulated fill."""
    raw_price = float(session.open)
    if event.direction == "up" and shares == 0:
        side = "BUY"
        price = raw_price * (1 + costs.slippage_rate)
        quantity = int(cash // price)
        while quantity > 0:
            _, charges = backtest.calculate_charges(quantity * price, "buy")
            charges *= costs.fee_multiplier
            if quantity * price + charges <= cash:
                break
            quantity -= 1
        if quantity <= 0:
            return None
    elif event.direction == "down" and shares > 0:
        side = "SELL"
        price = raw_price * (1 - costs.slippage_rate)
        quantity = shares
        _, charges = backtest.calculate_charges(quantity * price, "sell")
        charges *= costs.fee_multiplier
    else:
        return None
    return {
        "side": side,
        "extreme_date": event.extreme_day.date(),
        "extreme_close_inr": event.extreme_close,
        "confirmation_date": event.day.date(),
        "confirmation_close_inr": event.close,
        "fill_date": session.date.date(),
        "shares": quantity,
        "open_inr": raw_price,
        "fill_price_inr": price,
        "charges_inr": charges,
        "slippage_inr": quantity * abs(price - raw_price),
        "realized_pnl_inr": None,
    }


def evaluate(histories, holdout_start):
    """Score the fixed rule on the same phase boundaries as the SMA study."""
    reports = []
    fills = []
    for period in ("development", "out_of_sample"):
        for symbol, prices in histories.items():
            early = prices["date"].dt.date < holdout_start
            development = prices.loc[early]
            if period == "development":
                metrics, stock_fills = run_dc(development)
            else:
                metrics, stock_fills = run_dc(prices.loc[~early], warmup=development)
            reports.append({"period": period, "stock": symbol, **metrics})
            fills.extend({"period": period, "stock": symbol, **fill} for fill in stock_fills)

    baseline, _ = multiyear.evaluate(histories, holdout_start)
    comparison = baseline.merge(pd.DataFrame(reports), on=["period", "stock"], validate="one_to_one")
    comparison["dc_minus_sma_pct_points"] = comparison["dc_return_pct"] - comparison["sma_return_pct"]
    comparison["dc_minus_buy_hold_pct_points"] = comparison["dc_return_pct"] - comparison["buy_hold_return_pct"]
    return comparison, pd.DataFrame(fills)


def period_summary(comparison):
    """Summarize independent stock runs as simple stock averages."""
    rows = []
    for period, stocks in comparison.groupby("period", sort=False):
        for strategy in ("dc", "sma", "buy_hold"):
            prefix = f"{strategy}_"
            trades = stocks[prefix + "completed_trades"].sum() if strategy != "buy_hold" else 0
            wins = stocks[prefix + "winning_trades"].sum() if trades else 0
            cost_column = "estimated_cost_inr" if strategy == "buy_hold" else "total_estimated_cost_inr"
            rows.append({
                "period": period,
                "strategy": strategy,
                "stocks": len(stocks),
                "mean_return_pct": stocks[prefix + "return_pct"].mean(),
                "mean_net_pnl_inr": stocks[prefix + "net_pnl_inr"].mean(),
                "mean_max_drawdown_pct": stocks[prefix + "max_drawdown_pct"].mean(),
                "mean_estimated_cost_inr": stocks[prefix + cost_column].mean(),
                "total_closed_trades": trades,
                "winning_closed_trades": wins,
                "win_rate_pct": 100 * wins / trades if trades else None,
            })
    return pd.DataFrame(rows)


def load_histories():
    histories = {}
    for symbol in kite_batch.SYMBOLS:
        path = HISTORY_DIR / f"{symbol}_daily.csv"
        prices = backtest.load_stock_history([path], END + timedelta(days=1))
        prices = prices.loc[prices["date"] >= START].reset_index(drop=True)
        if prices.empty:
            raise ValueError(f"No saved daily bars for {symbol}.")
        prices["date"] = pd.to_datetime(prices["date"])
        histories[symbol] = prices
    return histories


def main():
    comparison, fills = evaluate(load_histories(), HOLDOUT_START)
    summary = period_summary(comparison)
    for name, frame in (
        ("dc_comparison.csv", comparison),
        ("dc_period_summary.csv", summary),
        ("dc_fills.csv", fills),
    ):
        kite_batch.save_csv_once(frame, OUTPUT_DIR / name, float_format="%.4f")
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"DC research reports saved in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
