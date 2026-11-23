"""Download about one year of completed daily Infosys prices."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException


def main():
    # Read the Kite credentials from this project's local .env file.
    env_file = Path(__file__).with_name(".env")
    load_dotenv(dotenv_path=env_file)
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")

    if not api_key or not access_token:
        print("Missing KITE_API_KEY or KITE_ACCESS_TOKEN in .env. Please check the file.")
        return

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)

        # Yesterday in India is a safe cutoff: it excludes today's unfinished candle.
        # The exchange has no candle for weekends and market holidays.
        end_date = datetime.now(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=364)

        nse_instruments = kite.instruments("NSE")
        infy = next(
            (
                instrument
                for instrument in nse_instruments
                if instrument.get("tradingsymbol") == "INFY"
            ),
            None,
        )
        if infy is None:
            print("Could not find Infosys (INFY) in the NSE instrument list.")
            return

        candles = kite.historical_data(
            instrument_token=infy["instrument_token"],
            from_date=start_date,
            to_date=end_date,
            interval="day",
        )
    except TokenException:
        print("Your access token is expired or invalid. Please log in again.")
        return
    except Exception:
        # Keep error details private; they could contain credentials or other sensitive data.
        print("Could not download historical prices. Please check your connection and try again.")
        return

    columns = ["date", "open", "high", "low", "close", "volume"]
    data = pd.DataFrame(candles, columns=columns)
    if data.empty:
        print("No historical prices were returned for the selected date range.")
        return

    data_folder = Path(__file__).with_name("data")
    data_folder.mkdir(parents=True, exist_ok=True)
    csv_file = data_folder / "INFY_1year.csv"
    data.to_csv(csv_file, index=False)

    earliest_date = pd.to_datetime(data["date"]).min().strftime("%Y-%m-%d")
    latest_date = pd.to_datetime(data["date"]).max().strftime("%Y-%m-%d")
    print(f"Trading sessions downloaded: {len(data)}")
    print(f"Earliest date: {earliest_date}")
    print(f"Latest date: {latest_date}")
    print("Daily OHLCV data saved to data/INFY_1year.csv")


if __name__ == "__main__":
    main()
