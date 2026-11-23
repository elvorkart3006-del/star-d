"""Measure completed trades from an SMA 5/10 Infosys backtest."""

from pathlib import Path

import pandas as pd


STARTING_CAPITAL = 10_000.00


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

    # Calculate each moving average using closing prices.
    prices["SMA 5"] = prices["close"].rolling(window=5).mean()
    prices["SMA 10"] = prices["close"].rolling(window=10).mean()

    cash = STARTING_CAPITAL
    shares = 0
    entry_date = None
    entry_price = None
    completed_trades = []

    # Signals use closing data. Simulated trades happen at the next day's open.
    for signal_day in range(1, len(prices) - 1):
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
        if not bullish_cross and not bearish_cross:
            continue

        execution_day = prices.iloc[signal_day + 1]
        execution_date = execution_day["date"].strftime("%Y-%m-%d")
        execution_price = float(execution_day["open"])

        if bullish_cross and shares == 0 and execution_price > 0:
            shares_to_buy = int(cash // execution_price)
            if shares_to_buy > 0:
                shares = shares_to_buy
                entry_date = execution_date
                entry_price = execution_price
                cash -= shares * entry_price

        elif bearish_cross and shares > 0:
            sale_price = execution_price
            trade_profit_loss = (sale_price - entry_price) * shares
            cash += shares * sale_price
            completed_trades.append(
                {
                    "buy_date": entry_date,
                    "buy_price": entry_price,
                    "sell_date": execution_date,
                    "sell_price": sale_price,
                    "shares": shares,
                    "profit_loss": trade_profit_loss,
                }
            )
            shares = 0
            entry_date = None
            entry_price = None

    print(f"Starting capital: INR {STARTING_CAPITAL:.2f}")
    if completed_trades:
        print("Completed buy/sell pairs:")
        for number, trade in enumerate(completed_trades, start=1):
            result = trade["profit_loss"]
            print(
                f"{number}. Bought {trade['shares']} share(s) on {trade['buy_date']} "
                f"at INR {trade['buy_price']:.2f}; sold on {trade['sell_date']} "
                f"at INR {trade['sell_price']:.2f}; P/L: INR {result:+.2f}"
            )
    else:
        print("No completed buy/sell pairs during this date range.")

    trade_results = [trade["profit_loss"] for trade in completed_trades]
    winning_trades = sum(result > 0 for result in trade_results)
    losing_trades = sum(result < 0 for result in trade_results)
    total_trades = len(completed_trades)

    if total_trades:
        win_rate = winning_trades / total_trades * 100
        average_profit = sum(trade_results) / total_trades
        biggest_win = max(trade_results) if winning_trades else None
        biggest_loss = min(trade_results) if losing_trades else None
    else:
        win_rate = 0.0
        average_profit = None
        biggest_win = None
        biggest_loss = None

    latest_close = float(prices.iloc[-1]["close"])
    ending_value = cash + shares * latest_close
    overall_return = (ending_value - STARTING_CAPITAL) / STARTING_CAPITAL * 100

    print("\nPerformance summary")
    print(f"Total completed trades: {total_trades}")
    print(f"Winning trades: {winning_trades}")
    print(f"Losing trades: {losing_trades}")
    print(f"Win rate: {win_rate:.2f}%")
    if average_profit is None:
        print("Average profit per trade: N/A")
    else:
        print(f"Average profit per trade: INR {average_profit:+.2f}")
    if biggest_win is None:
        print("Biggest winning trade: N/A")
    else:
        print(f"Biggest winning trade: INR {biggest_win:+.2f}")
    if biggest_loss is None:
        print("Biggest losing trade: N/A")
    else:
        print(f"Biggest losing trade: INR {biggest_loss:+.2f}")
    print(f"Ending portfolio value: INR {ending_value:.2f}")
    print(f"Overall return: {overall_return:+.2f}%")
    if shares > 0:
        print(f"Open position at end: {shares} share(s), valued at latest close INR {latest_close:.2f}")
    print("Results are preliminary and exclude brokerage, taxes, and slippage.")


if __name__ == "__main__":
    main()
