"""Display latest traded prices for several NSE stocks."""

import os
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException


STOCKS = [
    ("Reliance Industries", "RELIANCE"),
    ("Tata Consultancy Services", "TCS"),
    ("Infosys", "INFY"),
    ("HDFC Bank", "HDFCBANK"),
    ("ICICI Bank", "ICICIBANK"),
]


def print_price_table(prices):
    """Print the requested stocks and their prices in aligned columns."""
    rows = []
    for company_name, symbol in STOCKS:
        quote = prices.get(f"NSE:{symbol}") if isinstance(prices, dict) else None
        price = quote.get("last_price") if isinstance(quote, dict) else None
        price_text = f"INR {price}" if price is not None else "Not available"
        rows.append((company_name, symbol, price_text))

    name_width = max(len("Company"), *(len(row[0]) for row in rows))
    symbol_width = max(len("Symbol"), *(len(row[1]) for row in rows))
    price_width = max(len("Last traded price"), *(len(row[2]) for row in rows))

    print(f"{'Company':<{name_width}}  {'Symbol':<{symbol_width}}  {'Last traded price':>{price_width}}")
    print(f"{'-' * name_width}  {'-' * symbol_width}  {'-' * price_width}")
    for company_name, symbol, price_text in rows:
        print(f"{company_name:<{name_width}}  {symbol:<{symbol_width}}  {price_text:>{price_width}}")


def main():
    # Load the credentials from this project's local .env file.
    env_file = Path(__file__).with_name(".env")
    load_dotenv(dotenv_path=env_file)
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")

    if not api_key or not access_token:
        print("Missing KITE_API_KEY or KITE_ACCESS_TOKEN in .env. Please check the file.")
        return

    instruments = [f"NSE:{symbol}" for _, symbol in STOCKS]

    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        # Request all five prices together in one API call.
        prices = kite.ltp(instruments)
    except TokenException:
        print("Your access token is expired or invalid. Please log in again.")
        return
    except Exception:
        # Do not display exception details, which could contain private data.
        print("Could not connect to Zerodha Kite Connect. Please check your connection and try again.")
        return

    print_price_table(prices)


if __name__ == "__main__":
    main()
