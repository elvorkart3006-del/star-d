"""Calculate and plot Infosys closing prices with simple moving averages."""

from pathlib import Path

import pandas as pd

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError:
    print("Matplotlib is not installed. Install it with: python -m pip install matplotlib")
else:
    # Look for the historical prices file inside this project's data folder.
    csv_file = Path(__file__).with_name("data") / "INFY_historical.csv"

    if not csv_file.exists():
        print("Historical data file not found. Run historical_data.py first to create it.")
    else:
        # Read the CSV and parse its date column as dates.
        stock_data = pd.read_csv(csv_file, parse_dates=["date"])

        if "close" not in stock_data.columns or stock_data.empty:
            print("The CSV does not contain closing-price data.")
        else:
            # A simple moving average is the average close over the given window.
            stock_data["SMA 5"] = stock_data["close"].rolling(window=5).mean()
            stock_data["SMA 10"] = stock_data["close"].rolling(window=10).mean()

            latest = stock_data.iloc[-1]
            print(f"Latest closing price: INR {latest['close']:.2f}")

            if pd.isna(latest["SMA 5"]):
                print("Latest 5-day SMA: Not enough price data")
            else:
                print(f"Latest 5-day SMA: INR {latest['SMA 5']:.2f}")

            if pd.isna(latest["SMA 10"]):
                print("Latest 10-day SMA: Not enough price data")
            else:
                print(f"Latest 10-day SMA: INR {latest['SMA 10']:.2f}")

            # Draw the closing price and both averages on the same chart.
            plt.plot(stock_data["date"], stock_data["close"], label="Closing price")
            plt.plot(stock_data["date"], stock_data["SMA 5"], label="SMA 5")
            plt.plot(stock_data["date"], stock_data["SMA 10"], label="SMA 10")
            plt.title("Infosys Closing Price and Simple Moving Averages")
            plt.xlabel("Date")
            plt.ylabel("Price (INR)")
            plt.legend()
            plt.grid(True)
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.show()
