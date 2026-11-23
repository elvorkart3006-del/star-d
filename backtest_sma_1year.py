"""Run a one-year, long-only SMA crossover simulation for Infosys."""

from pathlib import Path

import pandas as pd


STARTING_CASH = 10000.00


def main():
    print("PRELIMINARY BACKTEST - brokerage, taxes, and slippage are excluded")
    print("This is a simulation only; no real orders are placed.\n")

    csv_file = Path(__file__).with_name("data") / "INFY_1year.csv"
    if not csv_file.exists():
        print("One-year data file not found. Run historical_data_1year.py first.")
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

    # Calculate both averages using each day's closing price.
    prices["SMA 5"] = prices["close"].rolling(window=5).mean()
    prices["SMA 10"] = prices["close"].rolling(window=10).mean()

    cash = STARTING_CASH
    shares = 0
    bullish_signals = 0
    bearish_signals = 0
    executed_trades = 0  # Counts each simulated buy or sell transaction.
    skipped_buys_insufficient_cash = 0

    print(f"Starting capital: INR {STARTING_CASH:.2f}")

    # A crossover is known after a day's close. Simulate it at the next day's open.
    # The final day's signal is counted, but cannot be traded without another day.
    for signal_day in range(1, len(prices)):
        previous = prices.iloc[signal_day - 1]
        current = prices.iloc[signal_day]

        if pd.isna(previous["SMA 5"]) or pd.isna(previous["SMA 10"]):
            continue

        bullish_cross = (
            previous["SMA 5"] <= previous["SMA 10"]
            and current["SMA 5"] > current["SMA 10"]
        )
        bearish_cross = (
            previous["SMA 5"] >= previous["SMA 10"]
            and current["SMA 5"] < current["SMA 10"]
        )

        if bullish_cross:
            bullish_signals += 1
        if bearish_cross:
            bearish_signals += 1

        if not bullish_cross and not bearish_cross:
            continue

        # There is no next trading day's open for a signal on the final row.
        if signal_day + 1 >= len(prices):
            continue

        execution_day = prices.iloc[signal_day + 1]
        execution_price = float(execution_day["open"])
        execution_date = execution_day["date"].strftime("%Y-%m-%d")

        if bullish_cross and shares == 0 and execution_price > 0:
            shares_to_buy = int(cash // execution_price)
            if shares_to_buy > 0:
                shares = shares_to_buy
                cash -= shares * execution_price
                executed_trades += 1
                print(
                    f"BUY  {execution_date}: {shares} share(s) at "
                    f"INR {execution_price:.2f} each"
                )
            else:
                skipped_buys_insufficient_cash += 1
                print(
                    f"SKIP BUY {execution_date}: not enough cash for one share "
                    f"at INR {execution_price:.2f}"
                )

        elif bearish_cross and shares > 0:
            print(
                f"SELL {execution_date}: {shares} share(s) at "
                f"INR {execution_price:.2f} each"
            )
            cash += shares * execution_price
            shares = 0
            executed_trades += 1

    latest_close = float(prices.iloc[-1]["close"])
    remaining_share_value = shares * latest_close
    final_portfolio_value = cash + remaining_share_value
    profit_or_loss = final_portfolio_value - STARTING_CASH

    print("\nBacktest summary")
    print(f"Bullish crossover signals: {bullish_signals}")
    print(f"Bearish crossover signals: {bearish_signals}")
    print(f"Executed trades (buy/sell transactions): {executed_trades}")
    print(f"Buy signals skipped for insufficient cash: {skipped_buys_insufficient_cash}")
    print(f"Ending cash: INR {cash:.2f}")
    print(f"Remaining shares: {shares}")
    print(f"Final portfolio value: INR {final_portfolio_value:.2f}")
    print(f"Profit/Loss: INR {profit_or_loss:+.2f}")
    print("Preliminary results exclude brokerage, taxes, and slippage.")


if __name__ == "__main__":
    main()
