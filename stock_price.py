"""Display the latest traded price for Infosys from Kite Connect."""

import os
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException


def main():
    # Load the API key and access token from this project's local .env file.
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
        response = kite.ltp("NSE:INFY")
    except TokenException:
        print("Your access token is expired or invalid. Please log in again.")
        return
    except Exception:
        # Do not display exception details, which may contain private information.
        print("Could not retrieve the stock price. Please check your connection and try again.")
        return

    quote = response.get("NSE:INFY") if isinstance(response, dict) else None
    if not isinstance(quote, dict) or "last_price" not in quote:
        print("The API response did not include the Infosys last traded price.")
        return

    print("Stock: Infosys")
    print("Exchange: NSE")
    print(f"Last traded price: \u20b9{quote['last_price']}")
    print("LTP is the most recent traded price; it may not update continuously.")


if __name__ == "__main__":
    main()
