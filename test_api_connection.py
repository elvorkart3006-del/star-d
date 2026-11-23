"""Check whether the saved Kite Connect access token is valid."""

import os
from pathlib import Path

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

        # The returned profile is intentionally not displayed or stored.
        kite.profile()
    except TokenException:
        print("Your access token is expired or invalid. Please log in again.")
    except Exception:
        # Keep error details private; they can sometimes contain sensitive data.
        print("Could not connect to Zerodha Kite Connect. Please check your setup and try again.")
    else:
        print("SUCCESS: Connected to Zerodha Kite Connect API!")


if __name__ == "__main__":
    main()
