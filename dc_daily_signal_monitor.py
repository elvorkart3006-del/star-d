"""Read completed Kite candles and export DC confirmation metadata only."""

import argparse
import math
from datetime import date, datetime, time, timedelta

import pandas as pd
from kiteconnect.exceptions import KiteException, TokenException
from requests.exceptions import RequestException

import dc_research as dc
import multi_stock_scanner as scanner
from backtest_sma_6_30_kite import SYMBOLS
from backtest_with_costs import DATA_DIR


# Match the frozen forward workflow's completed daily candle cutoff.
DAILY_CUTOFF = time(16)
OUTPUT_DIR = DATA_DIR / "dc_daily_signal_monitor"
SIGNALS = {"up": "BUY", "down": "EXIT"}
# These two reports were verified on their session dates before provenance was added.
VERIFIED_LEGACY_SESSIONS = {date(2026, 11, 26), date(2026, 11, 27)}


def session_complete(target, now):
    ist_now = now.astimezone(scanner.IST)
    cutoff = ist_now.date() - timedelta(days=ist_now.time() < DAILY_CUTOFF)
    return target <= cutoff


def fetch_closes(kite, token, start, end):
    candles = scanner._call_with_rate_limit(
        lambda: kite.historical_data(
            instrument_token=token,
            from_date=datetime.combine(start, time.min),
            to_date=datetime.combine(end, time(23, 59, 59)),
            interval="day",
        ),
        "historical",
    )
    frame = pd.DataFrame(candles, columns=["date", "close"])
    dates = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    frame["date"] = dates.dt.tz_convert(scanner.IST).dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if frame["date"].isna().any() or not frame["close"].map(
        lambda value: math.isfinite(value) and value > 0
    ).all():
        raise ValueError("Invalid candle date or close")
    if frame["date"].duplicated().any():
        raise ValueError("Duplicate daily candle")
    if not frame["date"].dt.date.between(start, end).all():
        raise ValueError("Candle outside requested dates")
    return frame.sort_values("date", ignore_index=True)


def monitor(kite, target, now, expected_sessions=()):
    rows = {
        symbol: {
            "symbol": symbol, "requested_session": target, "completed_session": None,
            "signal": None, "signal_date": None, "last_confirmed_signal": None,
            "last_confirmation_date": None, "data_quality": None,
        }
        for symbol in SYMBOLS
    }
    if not session_complete(target, now):
        for row in rows.values():
            row["data_quality"] = "Requested session not yet complete"
        return pd.DataFrame(rows.values())

    tokens = {
        item["symbol"]: item["instrument_token"]
        for item in scanner.get_nse_equity_universe(kite)
        if item["symbol"] in SYMBOLS
    }
    required = {target, *expected_sessions}
    histories = {}
    for symbol, row in rows.items():
        if symbol not in tokens:
            row["data_quality"] = "Missing NSE instrument"
            continue
        try:
            # Separate the frozen seed from its extension and bound each request.
            end = min(target, dc.END)
            parts = [fetch_closes(kite, tokens[symbol], dc.START, end)]
            start = end + timedelta(days=1)
            while start <= target:
                end = min(start + timedelta(days=364), target)
                parts.append(fetch_closes(kite, tokens[symbol], start, end))
                start = end + timedelta(days=1)
        except TokenException:
            raise
        except (KiteException, RequestException) as error:
            row["data_quality"] = f"Kite read failed ({type(error).__name__})"
            continue
        except ValueError as error:
            row["data_quality"] = str(error)
            continue

        history = pd.concat(parts, ignore_index=True)
        dates = history["date"].dt.date
        if not dates.empty:
            row["completed_session"] = dates.iloc[-1]
        missing = sorted(required.difference(dates))
        if missing:
            row["data_quality"] = "Missing candle for " + ", ".join(map(str, missing))
        elif dates.iloc[0] != dc.START or (
            target >= dc.END and len(parts[0]) != 1241
        ):
            row["data_quality"] = "Incomplete frozen seed history"
        else:
            histories[symbol] = history

    calendars = {tuple(history["date"]) for history in histories.values()}
    for symbol, history in histories.items():
        row = rows[symbol]
        if len(calendars) > 1:
            row["data_quality"] = "Session calendar differs across stocks"
            continue
        last = None
        for last in dc.directional_changes(history, dc.THRESHOLD):
            pass
        row["signal"] = "NO NEW SIGNAL"
        row["data_quality"] = "OK"
        if last:
            row["last_confirmed_signal"] = SIGNALS[last.direction]
            row["last_confirmation_date"] = last.day.date()
            if last.day.date() == target:
                row["signal"] = SIGNALS[last.direction]
                row["signal_date"] = target
    return pd.DataFrame(rows.values())


def valid_report(path, day, kind):
    if not path.is_file():
        return False
    try:
        report = pd.read_csv(path, dtype=str)
        if "observation_type" in report:
            observed = pd.to_datetime(report["observed_at_ist"], utc=True, errors="coerce")
            observed = observed.dt.tz_convert(scanner.IST)
            timing_ok = (
                observed.dt.date.eq(day) & observed.dt.time.ge(DAILY_CUTOFF)
                if kind == "same_session_close" else observed.dt.date.gt(day)
            )
            provenance_ok = report["observation_type"].eq(kind).all() and timing_ok.all()
        else:
            provenance_ok = kind == "same_session_close" and day in VERIFIED_LEGACY_SESSIONS
        return (
            provenance_ok
            and sorted(report["symbol"]) == sorted(SYMBOLS)
            and report["requested_session"].eq(str(day)).all()
            and report["completed_session"].eq(str(day)).all()
            and report["data_quality"].eq("OK").all()
        )
    except (OSError, ValueError, KeyError):
        return False


def print_coverage(expected_sessions, now):
    complete = sorted(day for day in expected_sessions if session_complete(day, now))
    observed = [
        day for day in complete
        if valid_report(OUTPUT_DIR / f"{day}.csv", day, "same_session_close")
    ]
    gaps = sorted(set(complete) - set(observed))
    retrospective = [
        day for day in gaps
        if valid_report(OUTPUT_DIR / "retrospective" / f"{day}.csv", day, "retrospective")
    ]
    print(f"Same-session close coverage: {len(observed)}/{len(complete)} expected completed sessions.")
    print("Missing same-session reports: " + (", ".join(map(str, gaps)) or "none"))
    if retrospective:
        print("Retrospective reports available: " + ", ".join(map(str, retrospective)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(scanner.IST).date())
    parser.add_argument("--expect-session", type=date.fromisoformat, action="append", default=[])
    parser.add_argument("--coverage-only", action="store_true", help="Check saved coverage without Kite.")
    args = parser.parse_args()
    if args.date < dc.START or any(
        not dc.START <= day <= args.date for day in args.expect_session
    ):
        parser.error("The requested dates must be within the replay window.")
    expected = {args.date, *args.expect_session}
    now = datetime.now(scanner.IST)
    if args.coverage_only:
        print_coverage(expected, now)
        return
    if not session_complete(args.date, now):
        print_coverage(expected, now)
        raise SystemExit("Requested session awaits the 16:00 IST cutoff.")
    try:
        report = monitor(scanner.create_kite_client(), args.date, now, args.expect_session)
    except TokenException:
        print_coverage(expected, now)
        raise SystemExit(
            "Kite authentication rejected. Refresh an expired session with python -B kite_callback.py."
        ) from None
    except scanner.MissingCredentialsError:
        print_coverage(expected, now)
        raise SystemExit("Kite API key or access token is missing from .env.") from None
    except (KiteException, RequestException) as error:
        print_coverage(expected, now)
        raise SystemExit(f"Kite read failed ({type(error).__name__}).") from None

    observed_at = datetime.now(scanner.IST)
    same_session = args.date == observed_at.date()
    report["observation_type"] = "same_session_close" if same_session else "retrospective"
    report["observed_at_ist"] = observed_at.isoformat(timespec="seconds")
    print(report.fillna("-").to_string(index=False))
    if not report["data_quality"].eq("OK").all():
        print_coverage(expected, now)
        raise SystemExit("Daily report was not saved because some checks failed.")
    destination = OUTPUT_DIR if same_session else OUTPUT_DIR / "retrospective"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{args.date}.csv"
    try:
        report.to_csv(path, index=False, mode="x")
    except FileExistsError:
        print(f"Preserved existing report: {path}")
    print_coverage(expected, observed_at)


if __name__ == "__main__":
    main()
