"""Run a simple long-only SMA crossover backtest for Infosys."""

from pathlib import Path

import pandas as pd


STARTING_CASH = 500.00


def main():
    print("PRELIMINARY BACKTEST - excludes brokerage, taxes, and slippage")
    print("This is a simulation only; it does not place real orders.\n")

    csv_file = Path(__file__).with_name("data") / "INFY_historical.csv"
    if not csv_file.exists():
        print("Historical data file not found. Run historical_data.py first to create it.")
        return

    try:
        prices = pd.read_csv(csv_file, parse_dates=["date"])
    except Exception:
        print("Could not read the historical CSV file.")
        return

    required_columns = {"date", "open", "close"}
    if not required_columns.issubset(prices.columns):
        print("The CSV must contain date, open, and close columns.")
        return

    prices = prices.dropna(subset=["date", "open", "close"])
    prices = prices.sort_values("date").reset_index(drop=True)
    if prices.empty:
        print("The CSV does not contain usable price data.")
        return

    # Calculate moving averages from closing prices.
    prices["SMA 5"] = prices["close"].rolling(window=5).mean()
    prices["SMA 10"] = prices["close"].rolling(window=10).mean()

    cash = STARTING_CASH
    shares = 0
    print(f"Starting cash: INR {cash:.2f}\n")

    # A signal is known after the day's close, so use the following day's open.
    # The final day cannot be used for execution because there is no next day.
    for signal_day in range(1, len(prices) - 1):
        previous = prices.iloc[signal_day - 1]
        current = prices.iloc[signal_day]
        execution_day = prices.iloc[signal_day + 1]

        if pd.isna(previous["SMA 5"]) or pd.isna(previous["SMA 10"]):
            continue

        crossed_above = (
            previous["SMA 5"] <= previous["SMA 10"]
            and current["SMA 5"] > current["SMA 10"]
        )
        crossed_below = (
            previous["SMA 5"] >= previous["SMA 10"]
            and current["SMA 5"] < current["SMA 10"]
        )
        execution_price = float(execution_day["open"])
        execution_date = execution_day["date"].strftime("%Y-%m-%d")

        if crossed_above and shares == 0 and execution_price > 0:
            shares_to_buy = int(cash // execution_price)
            if shares_to_buy > 0:
                shares = shares_to_buy
                cash -= shares * execution_price
                print(
                    f"BUY  {execution_date}: {shares} share(s) at "
                    f"INR {execution_price:.2f} each; cash left INR {cash:.2f}"
                )

        elif crossed_below and shares > 0:
            sale_value = shares * execution_price
            print(
                f"SELL {execution_date}: {shares} share(s) at "
                f"INR {execution_price:.2f} each; received INR {sale_value:.2f}"
            )
            cash += sale_value
            shares = 0

    latest_close = float(prices.iloc[-1]["close"])
    remaining_value = shares * latest_close
    total_value = cash + remaining_value

    print("\nBacktest summary")
    print(f"Ending cash: INR {cash:.2f}")
    print(f"Remaining shares: {shares}")
    print(f"Remaining shares valued at latest close: INR {remaining_value:.2f}")
    print(f"Total portfolio value: INR {total_value:.2f}")
    print("Results are preliminary and exclude brokerage, taxes, and slippage.")


if __name__ == "__main__":
    main()
