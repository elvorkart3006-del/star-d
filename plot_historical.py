"""Plot Infosys closing prices from the saved historical CSV file."""

from pathlib import Path

import pandas as pd

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError:
    print("Matplotlib is not installed. Install it with: python -m pip install matplotlib")
else:
    # Find the CSV beside this script, inside the data folder.
    csv_file = Path(__file__).with_name("data") / "INFY_historical.csv"

    if not csv_file.exists():
        print("Historical data file not found. Run historical_data.py first to create it.")
    else:
        # Load the CSV and convert its date column into real dates for plotting.
        historical_data = pd.read_csv(csv_file, parse_dates=["date"])

        # Draw the closing price over time.
        plt.plot(historical_data["date"], historical_data["close"])
        plt.title("Infosys (INFY) Closing Prices")
        plt.xlabel("Date")
        plt.ylabel("Closing Price (INR)")
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.show()
