"""Safely check that the Kite Connect client can be initialized.

This does not log in to a Zerodha account and does not place orders.
"""

import os

from dotenv import load_dotenv
from kiteconnect import KiteConnect


def main():
    # Read credentials from the local .env file.
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")

    # Check that both settings exist without displaying their values.
    if not api_key or not api_secret:
        print("Credentials are missing. Add KITE_API_KEY and KITE_API_SECRET to your .env file.")
        return

    # The API key is enough to create the client object.
    kite = KiteConnect(api_key=api_key)

    if kite:
        print("Kite Connect client initialized successfully.")
        print("Initialization alone does not authenticate your Zerodha account.")
        print("This script does not place any trades or orders.")


if __name__ == "__main__":
    main()
