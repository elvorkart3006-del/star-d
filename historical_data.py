"""Temporarily process recent Infosys candles without saving raw prices."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException


def main():
    # Load credentials from this project's local .env file.
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

        # Use yesterday in India as the cutoff so today's unfinished candle
        # cannot be included. The API skips weekends and market holidays.
        end_date = datetime.now(ZoneInfo("Asia/Kolkata")).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=29)

        instruments = kite.instruments("NSE")
        infy = next(
            (
                instrument
                for instrument in instruments
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
        # Do not display exception details, which may contain private data.
        print("Could not retrieve historical prices. Please check your connection and try again.")
        return

    columns = ["date", "open", "high", "low", "close", "volume"]
    data = pd.DataFrame(candles, columns=columns)
    if data.empty:
        print("No historical prices were returned for the selected date range.")
        return

    sessions = pd.to_datetime(data["date"])
    print(f"Infosys (INFY) candles processed in memory: {len(data)} trading sessions")
    print(f"Earliest session: {sessions.min():%Y-%m-%d}")
    print(f"Latest session: {sessions.max():%Y-%m-%d}")
    print("Raw candles were not saved to disk.")


if __name__ == "__main__":
    main()
