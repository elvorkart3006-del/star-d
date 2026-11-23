"""Read-only NSE stock scanner using Zerodha Kite Connect.

From the Trading folder, run:
    python multi_stock_scanner.py

The default scan uses SMA 6/30 and the existing eight symbols. The dashboard
can import the functions in this file to scan a configurable stock universe.
This module never places, changes, or cancels orders or GTTs.
"""

import argparse
import os
import threading
import time
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException, TokenException


DEFAULT_STOCKS = [
    "INFY",
    "TCS",
    "WIPRO",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "ITC",
    "TATAMOTORS",
]
DEFAULT_SMA_SHORT = 6
DEFAULT_SMA_LONG = 30
HISTORY_CALENDAR_DAYS = 90
MAX_SMA_PERIOD = 200
HISTORY_CACHE_SECONDS = 600
HISTORICAL_REQUEST_INTERVAL = 1 / 3 + 0.03
QUOTE_REQUEST_INTERVAL = 1.05
MAX_RATE_LIMIT_RETRIES = 3
IST = ZoneInfo("Asia/Kolkata")
PROJECT_FOLDER = Path(__file__).resolve().parent
RESULTS_FILE = PROJECT_FOLDER / "data" / "stock_scanner_results.csv"

_RATE_LIMIT_LOCKS = {
    "historical": threading.Lock(),
    "quote": threading.Lock(),
}
_LAST_REQUEST_TIME = {"historical": 0.0, "quote": 0.0}


class MissingCredentialsError(Exception):
    """Raised when the local .env file does not contain the Kite credentials."""


class InsufficientHistoryError(Exception):
    """Raised when there are not enough completed daily candles for the SMAs."""


def history_calendar_days(long_period):
    """Request ample calendar time to obtain at least 200 completed sessions.

    Trading holidays mean calendar days and trading sessions are not the same.
    Three calendar days per SMA session plus a 30-day margin gives room for
    weekends and holidays, while keeping each request to one daily-history call.
    """
    if long_period < 2 or long_period > MAX_SMA_PERIOD:
        raise ValueError(f"SMA periods must be between 1 and {MAX_SMA_PERIOD}.")
    return max(HISTORY_CALENDAR_DAYS, long_period * 3 + 30)


def create_kite_client(env_file=None):
    """Create an authenticated client using the project's local .env file."""
    env_path = Path(env_file) if env_file else PROJECT_FOLDER / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        raise MissingCredentialsError(
            "KITE_API_KEY and KITE_ACCESS_TOKEN must be set in the local .env file."
        )

    client = KiteConnect(api_key=api_key)
    client.set_access_token(access_token)
    return client


def get_nse_equity_universe(kite):
    """Return NSE-listed equity instruments from Kite's instrument master."""
    instruments = kite.instruments("NSE")
    equities = []
    for instrument in instruments:
        if (
            instrument.get("exchange") == "NSE"
            and instrument.get("segment") == "NSE"
            and instrument.get("instrument_type") == "EQ"
            and instrument.get("tradingsymbol")
        ):
            equities.append(
                {
                    "symbol": str(instrument["tradingsymbol"]).upper(),
                    "company": str(instrument.get("name") or instrument["tradingsymbol"]),
                    "instrument_token": int(instrument["instrument_token"]),
                    "exchange": "NSE",
                    "segment": "NSE",
                    "instrument_type": "EQ",
                }
            )

    equities.sort(key=lambda item: (item["symbol"], item["company"]))
    return equities


def completed_session_end_date(now=None):
    """Return a safe daily-candle cutoff using the Asia/Kolkata market clock.

    Before the NSE cash close, today's candle is excluded. At/after the close,
    today's date is requested, but a today candle is used only if Kite returns
    that dated daily bar. Candle dates, rather than weekdays, define sessions.
    """
    india_now = now or datetime.now(IST)
    if india_now.tzinfo is None:
        india_now = india_now.replace(tzinfo=IST)
    else:
        india_now = india_now.astimezone(IST)

    if india_now.time() >= clock_time(15, 30):
        return india_now.date()
    return india_now.date() - timedelta(days=1)


def _wait_for_rate_limit_slot(endpoint):
    """Space requests below Kite's documented per-second limits."""
    interval = (
        HISTORICAL_REQUEST_INTERVAL if endpoint == "historical" else QUOTE_REQUEST_INTERVAL
    )
    with _RATE_LIMIT_LOCKS[endpoint]:
        now = time.monotonic()
        delay = interval - (now - _LAST_REQUEST_TIME[endpoint])
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_TIME[endpoint] = time.monotonic()


def _is_rate_limit_error(error):
    """Recognize Kite's 429 / too-many-requests response without printing it."""
    if getattr(error, "code", None) == 429:
        return True
    message = str(error).lower()
    return "429" in message or "too many requests" in message or "rate limit" in message


def _call_with_rate_limit(call, endpoint):
    """Throttle requests and retry only rate-limit errors with backoff."""
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        _wait_for_rate_limit_slot(endpoint)
        try:
            return call()
        except TokenException:
            raise
        except Exception as error:
            if not _is_rate_limit_error(error) or attempt + 1 >= MAX_RATE_LIMIT_RETRIES:
                raise
            time.sleep(0.75 * (2**attempt))


def _india_datetime(value):
    """Parse a Kite timestamp and return it in India time."""
    if value is None or value == "":
        return None
    try:
        parsed = pd.Timestamp(value).to_pydatetime()
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _prepare_candles(candles, start_date, end_date):
    """Make sorted OHLCV data and drop any candle beyond the allowed cutoff."""
    data = pd.DataFrame(
        candles,
        columns=["date", "open", "high", "low", "close", "volume"],
    )
    if data.empty:
        return data

    data["date"] = pd.to_datetime(data["date"])
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    data = data[(data["date"].dt.date >= start_date) & (data["date"].dt.date <= end_date)]
    data = data.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return data


def _add_sma_columns(prices, short_period, long_period):
    """Calculate both simple moving averages and mark all crossovers."""
    result = prices.copy()
    result["close"] = pd.to_numeric(result["close"], errors="coerce")
    result["SMA Short"] = result["close"].rolling(window=short_period).mean()
    result["SMA Long"] = result["close"].rolling(window=long_period).mean()
    result["Signal"] = ""

    events = []
    for row_number in range(1, len(result)):
        previous = result.iloc[row_number - 1]
        current = result.iloc[row_number]
        if pd.isna(previous["SMA Short"]) or pd.isna(previous["SMA Long"]):
            continue

        if previous["SMA Short"] <= previous["SMA Long"] and current["SMA Short"] > current["SMA Long"]:
            signal = "Bullish"
        elif previous["SMA Short"] >= previous["SMA Long"] and current["SMA Short"] < current["SMA Long"]:
            signal = "Bearish"
        else:
            continue

        result.at[row_number, "Signal"] = signal
        events.append(
            {
                "date": current["date"],
                "signal": signal,
                "price": float(current["close"]),
                "sessions_ago": len(result) - 1 - row_number,
            }
        )
    return result, events


def _latest_confirmed_signal(prices, events):
    """Return the newest completed-candle crossover, if there is one."""
    if events:
        event = events[-1]
        return event["signal"], event["date"], event["sessions_ago"]
    return None, None, None


def _provisional_signal(prices, ltp, quote_time, now, short_period, long_period):
    """Compare today's live LTP with the last completed session, when fresh."""
    quote_datetime = _india_datetime(quote_time)
    india_now = now or datetime.now(IST)
    if india_now.tzinfo is None:
        india_now = india_now.replace(tzinfo=IST)
    else:
        india_now = india_now.astimezone(IST)

    market_open_time = clock_time(9, 15)
    market_close_time = clock_time(15, 30)
    if not (market_open_time <= india_now.time() < market_close_time):
        return None, None, None
    if quote_datetime is None or quote_datetime.date() != india_now.date():
        return None, None, None
    quote_age_seconds = (india_now - quote_datetime).total_seconds()
    if not 0 <= quote_age_seconds <= 300:
        return None, None, None
    if not prices.empty and prices.iloc[-1]["date"].date() == india_now.date():
        # Today's bar has already entered the confirmed set; don't label it provisional.
        return None, None, None
    if ltp is None or pd.isna(ltp) or len(prices) < long_period:
        return None, None, None

    previous = prices.iloc[-1]
    current_short = (prices["close"].tail(short_period - 1).sum() + float(ltp)) / short_period
    current_long = (prices["close"].tail(long_period - 1).sum() + float(ltp)) / long_period
    if pd.isna(previous["SMA Short"]) or pd.isna(previous["SMA Long"]):
        return None, None, None

    if previous["SMA Short"] <= previous["SMA Long"] and current_short > current_long:
        return "Bullish", quote_datetime, float(ltp)
    if previous["SMA Short"] >= previous["SMA Long"] and current_short < current_long:
        return "Bearish", quote_datetime, float(ltp)
    return None, quote_datetime, float(ltp)


def _new_result_row(company, symbol, short_period, long_period):
    """Create a scanner row while keeping the established CSV columns."""
    return {
        "Company": company,
        "Symbol": symbol,
        "Latest Crossover": "N/A",
        "Crossover Date": None,
        "Sessions Ago": None,
        "Latest Close (INR)": None,
        "Status": "",
        "Confirmed Daily Signal": "N/A",
        "Confirmed Signal Date": None,
        "Confirmed Sessions Ago": None,
        "Current LTP (INR)": None,
        "Quote Time (IST)": None,
        "Provisional Intraday Signal": "N/A",
        "Provisional Signal Date": None,
        "Provisional Sessions Ago": None,
        "Latest Completed Session": None,
        "Most Recent Signal Date": None,
        "SMA Short Period": short_period,
        "SMA Long Period": long_period,
    }


def scan_stocks(
    kite,
    instruments,
    symbols,
    short_period=DEFAULT_SMA_SHORT,
    long_period=DEFAULT_SMA_LONG,
    history_cache=None,
    now=None,
):
    """Scan selected NSE stocks. No order or GTT methods are called.

    ``history_cache`` may be kept in Streamlit session state. It reuses daily
    candles for ten minutes when the cutoff date has not changed; live quotes
    are still fetched as one fresh batch on every manual refresh.
    """
    if (
        short_period < 1
        or short_period >= long_period
        or long_period < 2
        or long_period > MAX_SMA_PERIOD
    ):
        raise ValueError(
            f"SMA periods must be positive, short must be smaller, and long cannot exceed {MAX_SMA_PERIOD}."
        )
    if not symbols:
        raise ValueError("Select at least one NSE stock to scan.")

    india_now = now or datetime.now(IST)
    if india_now.tzinfo is None:
        india_now = india_now.replace(tzinfo=IST)
    else:
        india_now = india_now.astimezone(IST)
    end_date = completed_session_end_date(india_now)
    requested_calendar_days = history_calendar_days(long_period)
    start_date = end_date - timedelta(days=requested_calendar_days - 1)
    history_cache = history_cache if history_cache is not None else {}

    instrument_map = {
        item["symbol"]: item
        for item in instruments
        if item.get("exchange") == "NSE" and item.get("instrument_type") == "EQ"
    }

    quote_data = {}
    quote_error = False
    quote_keys = [f"NSE:{symbol}" for symbol in symbols if symbol in instrument_map]
    if quote_keys:
        try:
            quote_data = _call_with_rate_limit(lambda: kite.quote(quote_keys), "quote")
        except TokenException:
            raise
        except Exception:
            # Historical daily results are still useful if the live quote endpoint fails.
            quote_error = True

    results = []
    chart_data = {}
    for symbol in symbols:
        instrument = instrument_map.get(symbol)
        company = instrument["company"] if instrument else symbol
        result = _new_result_row(company, symbol, short_period, long_period)
        if instrument is None:
            result["Status"] = "Instrument missing from NSE equity list"
            results.append(result)
            continue

        cached = history_cache.get(symbol)
        use_cached = False
        if cached and cached.get("end_date") == end_date:
            age_seconds = time.time() - cached.get("saved_at", 0)
            cached_start = cached.get("start_date")
            if cached_start is None:
                # Older in-session cache entries had no requested start date.
                cached_dates = [
                    _india_datetime(candle.get("date")).date()
                    for candle in cached.get("candles", [])
                    if isinstance(candle, dict) and candle.get("date") is not None
                ]
                cached_start = min(cached_dates) if cached_dates else None
            elif not isinstance(cached_start, date):
                cached_start = pd.Timestamp(cached_start).date()
            covers_requested_history = (
                cached_start is not None and cached_start <= start_date
            )
            use_cached = age_seconds < HISTORY_CACHE_SECONDS and covers_requested_history

        if use_cached:
            candles = cached.get("candles", [])
        else:
            try:
                candles = _call_with_rate_limit(
                    lambda token=instrument["instrument_token"]: kite.historical_data(
                        instrument_token=token,
                        from_date=start_date,
                        to_date=end_date,
                        interval="day",
                    ),
                    "historical",
                )
            except TokenException:
                raise
            except Exception:
                result["Status"] = "Historical API error"
                results.append(result)
                continue

            history_cache[symbol] = {
                "end_date": end_date,
                "start_date": start_date,
                "saved_at": time.time(),
                "candles": candles,
            }

        prices = _prepare_candles(candles, start_date, end_date)
        if prices.empty:
            result["Status"] = "No completed daily candles returned"
            results.append(result)
            continue

        prices, confirmed_events = _add_sma_columns(prices, short_period, long_period)
        result["Latest Close (INR)"] = float(prices.iloc[-1]["close"])
        result["Latest Completed Session"] = prices.iloc[-1]["date"].date()
        confirmed_type, confirmed_date, confirmed_age = _latest_confirmed_signal(
            prices, confirmed_events
        )
        if len(prices) < long_period + 1:
            result["Status"] = "Insufficient completed sessions for crossover"
        else:
            result["Status"] = "OK"

        if confirmed_type:
            result["Latest Crossover"] = confirmed_type
            result["Crossover Date"] = confirmed_date.date()
            result["Sessions Ago"] = confirmed_age
            result["Confirmed Daily Signal"] = confirmed_type
            result["Confirmed Signal Date"] = confirmed_date.date()
            result["Confirmed Sessions Ago"] = confirmed_age
            result["Most Recent Signal Date"] = confirmed_date.date()
        elif len(prices) >= long_period + 1:
            result["Latest Crossover"] = "None"
            result["Confirmed Daily Signal"] = "None"

        quote_key = f"NSE:{symbol}"
        quote = quote_data.get(quote_key) if isinstance(quote_data, dict) else None
        ltp = quote.get("last_price") if isinstance(quote, dict) else None
        last_trade_time = (
            _india_datetime(quote.get("last_trade_time"))
            if isinstance(quote, dict)
            else None
        )
        quote_time = (
            last_trade_time
            or _india_datetime(quote.get("timestamp"))
            if isinstance(quote, dict)
            else None
        )
        if ltp is not None:
            result["Current LTP (INR)"] = float(ltp)
        if quote_time is not None:
            result["Quote Time (IST)"] = quote_time.strftime("%Y-%m-%d %H:%M:%S")

        provisional_type, provisional_time, provisional_price = _provisional_signal(
            prices,
            ltp,
            last_trade_time,
            india_now,
            short_period,
            long_period,
        )
        provisional_marker = None
        if provisional_time is not None:
            result["Provisional Signal Date"] = provisional_time.date()
            result["Provisional Sessions Ago"] = 0
            provisional_marker = {
                "date": provisional_time.replace(tzinfo=None),
                "signal": f"Provisional {provisional_type}" if provisional_type else "Live LTP (provisional)",
                "price": provisional_price,
            }
            if provisional_type:
                result["Provisional Intraday Signal"] = provisional_type
                result["Most Recent Signal Date"] = provisional_time.date()
            else:
                result["Provisional Intraday Signal"] = "No crossover"

        if quote_error or quote is None:
            if result["Status"] == "OK":
                result["Status"] = "Live quote unavailable; daily scan complete"

        # Keep the confirmed bars separate from the live quote point for charts.
        chart_history = prices[["date", "close", "SMA Short", "SMA Long"]].copy()
        chart_history["date"] = chart_history["date"].map(
            lambda value: _india_datetime(value).replace(tzinfo=None)
        )
        chart_events = [
            {
                "date": _india_datetime(event["date"]).replace(tzinfo=None),
                "signal": event["signal"],
                "price": event["price"],
            }
            for event in confirmed_events
        ]
        chart_data[symbol] = {
            "history": chart_history,
            "events": chart_events,
            "provisional": provisional_marker,
        }
        results.append(result)

    oldest_date = date.min
    results.sort(key=lambda row: row["Most Recent Signal Date"] or oldest_date, reverse=True)
    return results, chart_data, history_cache


def save_results_csv(results, destination=None):
    """Export the summary rows to the established scanner CSV path."""
    output_path = Path(destination) if destination else RESULTS_FILE
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(output_path, index=False)
    return output_path


def print_results_table(results):
    """Print confirmed and provisional scanner signals in aligned columns."""
    headings = [
        "Company",
        "Symbol",
        "Confirmed daily",
        "Confirmed date",
        "Sessions ago",
        "Provisional intraday",
        "Latest close INR",
        "Current LTP INR",
        "Status",
    ]
    rows = []
    for result in results:
        confirmed_date = result.get("Crossover Date")
        provisional_date = result.get("Provisional Signal Date")
        rows.append(
            [
                str(result.get("Company", "")),
                str(result.get("Symbol", "")),
                str(result.get("Confirmed Daily Signal", "N/A")),
                confirmed_date.strftime("%Y-%m-%d") if confirmed_date else "-",
                str(result.get("Confirmed Sessions Ago") if result.get("Confirmed Sessions Ago") is not None else "-"),
                str(result.get("Provisional Intraday Signal", "N/A")),
                f"{result['Latest Close (INR)']:,.2f}" if result.get("Latest Close (INR)") is not None else "-",
                f"{result['Current LTP (INR)']:,.2f}" if result.get("Current LTP (INR)") is not None else "-",
                str(result.get("Status", "")),
            ]
        )

    widths = [max(len(head), *(len(row[i]) for row in rows)) for i, head in enumerate(headings)]
    print("  ".join(head.ljust(widths[i]) for i, head in enumerate(headings)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def main(argv=None):
    """Run the scanner from a terminal; API calls happen only when run directly."""
    parser = argparse.ArgumentParser(description="Read-only NSE SMA crossover scanner")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_STOCKS)
    parser.add_argument("--sma-short", type=int, default=DEFAULT_SMA_SHORT)
    parser.add_argument("--sma-long", type=int, default=DEFAULT_SMA_LONG)
    args = parser.parse_args(argv)

    try:
        kite = create_kite_client()
        instruments = get_nse_equity_universe(kite)
        results, _, _ = scan_stocks(
            kite,
            instruments,
            [symbol.upper() for symbol in args.symbols],
            args.sma_short,
            args.sma_long,
        )
    except MissingCredentialsError:
        print("Login setup is incomplete. Check KITE_API_KEY and KITE_ACCESS_TOKEN in .env.")
        return 1
    except TokenException:
        print("Your access token is expired or invalid. Please log in again.")
        return 1
    except ValueError as error:
        print(f"Scanner settings error: {error}")
        return 1
    except Exception:
        print("Could not complete the scan. Check your connection and try again.")
        return 1

    print("Read-only NSE SMA crossover scan")
    print(f"Confirmed daily SMA: {args.sma_short}/{args.sma_long}")
    print("Provisional signals use a fresh current-session quote and are not confirmed.")
    print_results_table(results)
    save_results_csv(results)
    print("\nResults saved to data/stock_scanner_results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
