"""Score confirmed DC BUY signals with price-free, close-to-close outcome labels."""

import argparse
from datetime import date, datetime
import json
from pathlib import Path

import pandas as pd
from kiteconnect.exceptions import KiteException, TokenException
from requests.exceptions import RequestException

import dc_daily_signal_monitor as signal_monitor
import multi_stock_scanner as scanner
from backtest_sma_6_30_kite import SYMBOLS
from backtest_with_costs import DATA_DIR


HORIZONS = (1, 3, 5)
LABELS = ("HIGHER", "LOWER", "UNCHANGED", "PENDING", "UNAVAILABLE")
SEED_REPORT = signal_monitor.OUTPUT_DIR / "2026-11-26.csv"
OUTPUT_DIR = DATA_DIR / "star_d_outcomes"
# NSE capital-market calendar, circular CMTR/71775, for the seed signals.
SEED_SESSIONS = tuple(map(date.fromisoformat, (
    "2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03",
)))


def confirmed_buys(path):
    report = pd.read_csv(path, dtype=str)
    buys = report.loc[
        report["signal"].eq("BUY") & report["data_quality"].eq("OK"),
        ["symbol", "signal_date", "requested_session", "completed_session"],
    ]
    if not (
        buys["symbol"].isin(SYMBOLS).all()
        and buys["signal_date"].eq(buys["requested_session"]).all()
        and buys["signal_date"].eq(buys["completed_session"]).all()
    ):
        raise ValueError("BUY records must identify a monitored stock and its confirmed session.")
    return [(symbol, date.fromisoformat(day)) for symbol, day in
            buys[["symbol", "signal_date"]].drop_duplicates().itertuples(index=False, name=None)]


def score(buys, sessions, now, load_closes):
    """Use the supplied exchange session sequence, even when candles are missing."""
    if list(sessions) != sorted(set(sessions)):
        raise ValueError("Trading sessions must be unique and in ascending order.")
    rows = []
    for symbol, signal_day in buys:
        following = [day for day in sessions if day > signal_day][:max(HORIZONS)]
        if len(following) < max(HORIZONS):
            raise ValueError(f"The calendar needs five scheduled sessions after {signal_day}.")
        completed = [day for day in following if signal_monitor.session_complete(day, now)]
        closes, issue = {}, None
        if completed:
            closes, issue = load_closes(symbol, signal_day, completed[-1])
            if set(closes) - {signal_day, *completed}:
                issue = "Kite candle dates differ from the supplied session calendar"
        for horizon in HORIZONS:
            target = following[horizon - 1]
            detail = issue
            if target not in completed:
                label, detail = "PENDING", "Target session has yet to complete"
            elif issue:
                label = "UNAVAILABLE"
            else:
                missing = {signal_day, *following[:horizon]} - closes.keys()
                if missing:
                    label = "UNAVAILABLE"
                    detail = "Missing candle for " + ", ".join(map(str, sorted(missing)))
                else:
                    start, end = closes[signal_day], closes[target]
                    label = "HIGHER" if end > start else "LOWER" if end < start else "UNCHANGED"
            rows.append({
                "symbol": symbol, "signal_date": str(signal_day), "horizon_sessions": horizon,
                "target_session": str(target), "outcome": label, "status_detail": detail,
            })
    return rows


def aggregate(rows):
    return [
        {"horizon_sessions": horizon, **{
            label: sum(row["horizon_sessions"] == horizon and row["outcome"] == label for row in rows)
            for label in LABELS
        }}
        for horizon in HORIZONS
    ]


def kite_close_loader():
    kite = None
    tokens = {}
    auth_issue = None

    def load(symbol, start, end):
        nonlocal kite, tokens, auth_issue
        if auth_issue:
            return {}, auth_issue
        try:
            if kite is None:
                client = scanner.create_kite_client()
                tokens = {
                    item["symbol"]: item["instrument_token"]
                    for item in scanner.get_nse_equity_universe(client)
                    if item["symbol"] in SYMBOLS
                }
                kite = client
            if symbol not in tokens:
                return {}, "Missing NSE instrument"
            prices = signal_monitor.fetch_closes(kite, tokens[symbol], start, end)
            return dict(zip(prices["date"].dt.date, prices["close"], strict=True)), None
        except (TokenException, scanner.MissingCredentialsError):
            auth_issue = "Kite authentication unavailable"
            return {}, auth_issue
        except (KiteException, RequestException) as error:
            return {}, f"Kite read failed ({type(error).__name__})"
        except ValueError:
            return {}, "Kite candle validation failed"

    return load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signals", type=Path, default=SEED_REPORT)
    parser.add_argument("--session", type=date.fromisoformat, action="append",
                        help="Expected NSE session, in calendar order. Required with a different report.")
    args = parser.parse_args()
    if args.signals.resolve() != SEED_REPORT.resolve() and not args.session:
        parser.error("Supply the expected trading sessions for a different report.")
    now = datetime.now(scanner.IST)
    rows = score(confirmed_buys(args.signals), args.session or SEED_SESSIONS, now, kite_close_loader())
    counts = aggregate(rows)
    payload = {"evaluated_at_ist": now.isoformat(timespec="seconds"), "outcomes": rows, "counts": counts}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / now.strftime("%Y-%m-%dT%H%M%S%f-IST.json")
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
    if rows:
        print(pd.DataFrame(rows).fillna("-").to_string(index=False))
    print(pd.DataFrame(counts).to_string(index=False))
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
