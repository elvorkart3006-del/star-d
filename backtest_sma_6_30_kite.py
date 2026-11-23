"""Download NSE daily candles from Kite and compare SMA 6/30 with buy-and-hold."""

import math
from datetime import datetime, time, timedelta

import pandas as pd
from kiteconnect.exceptions import KiteException, PermissionException, TokenException
from requests.exceptions import RequestException

import backtest_with_costs as backtest
import multi_stock_scanner as scanner


SYMBOLS = ("INFY", "TCS", "WIPRO", "HDFCBANK", "ICICIBANK", "SBIN", "ITC")
SHORT_PERIOD = 6
LONG_PERIOD = 30
OUTPUT_ROOT = backtest.DATA_DIR / "sma_6_30_kite"


def buy_and_hold(prices, *, track_daily=False):
    """Buy at the first open and value the holding at each daily close."""
    raw_price = float(prices["open"].iloc[0])
    fill = raw_price * (1 + backtest.SLIPPAGE_RATE)
    capital = backtest.STARTING_CAPITAL
    shares = int(capital // fill)
    fees = 0.0
    while shares:
        _, fees = backtest.calculate_charges(shares * fill, "buy")
        if shares * fill + fees <= capital:
            break
        shares -= 1
    if shares == 0:
        fees = 0.0

    cash = capital - shares * fill - fees
    equity = cash + shares * prices["close"]
    peak = equity.cummax().clip(lower=capital)
    cost = fees + shares * (fill - raw_price)
    result = {
        "buy_hold_shares": shares,
        "buy_hold_estimated_cost_inr": cost,
        "buy_hold_net_pnl_inr": float(equity.iloc[-1]) - capital,
        "buy_hold_max_drawdown_pct": float(((peak - equity) / peak).max()) * 100,
    }
    if track_daily:
        daily = pd.DataFrame({"date": prices["date"], "equity": equity, "estimated_cost": 0.0})
        daily.loc[daily.index[0], "estimated_cost"] = cost
        result["daily"] = daily.reset_index(drop=True)
    return result


def compare(symbol, prices):
    sma = backtest.run_backtest(prices, True, short_period=SHORT_PERIOD, long_period=LONG_PERIOD)
    return comparison_row(symbol, prices, sma, buy_and_hold(prices))


def comparison_row(symbol, prices, sma, benchmark):
    trades = sma["completed_trades"]
    wins = sum(trade["net_profit"] > 0 for trade in trades)
    realized = sum(trade["net_profit"] for trade in trades)
    capital = backtest.STARTING_CAPITAL
    sma_return = 100 * sma["net_profit"] / capital
    benchmark_return = 100 * benchmark["buy_hold_net_pnl_inr"] / capital
    slippage = sma["cost_breakdown"]["slippage"]
    return {
        "stock": symbol,
        "first_session": prices["date"].iloc[0].date(),
        "last_session": prices["date"].iloc[-1].date(),
        "sessions": len(prices),
        "short_sma": SHORT_PERIOD,
        "long_sma": LONG_PERIOD,
        "starting_capital_inr": capital,
        "slippage_per_side_pct": backtest.SLIPPAGE_RATE * 100,
        "sma_completed_trades": len(trades),
        "sma_winning_trades": wins,
        "sma_win_rate_pct": 100 * wins / len(trades) if trades else None,
        "sma_net_pnl_inr": sma["net_profit"],
        "sma_realized_pnl_inr": realized,
        "sma_open_pnl_inr": sma["net_profit"] - realized,
        "sma_open_shares": sma["shares"],
        "sma_trading_charges_inr": sma["total_costs"] - slippage,
        "sma_slippage_inr": slippage,
        "sma_total_estimated_cost_inr": sma["total_costs"],
        "sma_return_pct": sma_return,
        "sma_max_drawdown_pct": sma["max_drawdown_pct"],
        **{key: value for key, value in benchmark.items() if key != "daily"},
        "buy_hold_return_pct": benchmark_return,
        "sma_minus_buy_hold_pct_points": sma_return - benchmark_return,
    }


def save_csv_once(frame, path, **options):
    """Keep existing reports and downloads intact on a repeated run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_csv(path, index=False, mode="x", **options)
    except FileExistsError:
        pass


def iter_histories(start, end, destination, create_client=scanner.create_kite_client):
    """Yield each stock's cached or newly downloaded daily history."""
    kite = None
    tokens = {}
    for symbol in SYMBOLS:
        history_path = destination / f"{symbol}_daily.csv"
        if history_path.is_file():
            prices = pd.read_csv(history_path, parse_dates=["date"])
        else:
            if kite is None:
                kite = create_client()
                tokens = {
                    item["symbol"]: item["instrument_token"]
                    for item in scanner.get_nse_equity_universe(kite)
                }
            if symbol not in tokens:
                print(f"{symbol}: missing from the Kite NSE equity catalog.", flush=True)
                yield symbol, None
                continue
            try:
                candles = scanner._call_with_rate_limit(
                    lambda: kite.historical_data(
                        instrument_token=tokens[symbol],
                        from_date=datetime.combine(start, time.min),
                        to_date=datetime.combine(end, time(23, 59, 59)),
                        interval="day",
                    ),
                    "historical",
                )
            except (TokenException, PermissionException, RequestException):
                raise
            except KiteException as error:
                status = getattr(error, "code", None)
                print(f"{symbol}: historical download failed (Kite status {status}).", flush=True)
                yield symbol, None
                continue
            prices = scanner._prepare_candles(candles, start, end)

        if len(prices) < LONG_PERIOD + 2 or not (
            (prices[["open", "close"]] > 0) & (prices[["open", "close"]] < math.inf)
        ).all().all():
            print(f"{symbol}: insufficient or invalid daily candles.", flush=True)
            yield symbol, None
            continue

        save_csv_once(prices, history_path)
        yield symbol, prices


def run_batch(start, end, destination, create_client=scanner.create_kite_client):
    """Resume same-window downloads and write independent stock comparisons."""
    rows = []
    failed = []
    for symbol, prices in iter_histories(start, end, destination, create_client):
        if prices is None:
            failed.append(symbol)
            continue
        row = compare(symbol, prices)
        save_csv_once(pd.DataFrame([row]), destination / f"{symbol}_results.csv", float_format="%.2f")
        rows.append(row)
        print(f"{symbol}: {len(prices)} sessions analyzed.", flush=True)

    summary = pd.DataFrame(rows)
    if rows and not failed:
        save_csv_once(summary, destination / "summary.csv", float_format="%.2f")
    return summary, failed


def main():
    end = datetime.now(scanner.IST).date() - timedelta(days=1)
    start = end - timedelta(days=364)
    destination = OUTPUT_ROOT / f"{start}_{end}"
    try:
        summary, failed = run_batch(start, end, destination)
    except scanner.MissingCredentialsError:
        print("Kite credentials or access token are missing from .env.")
        return 1
    except TokenException:
        print("Kite rejected the session token. Sign in to Kite Connect to refresh it.")
        return 1
    except PermissionException:
        print("Kite denied access to the instrument catalog or historical candles.")
        return 1
    except RequestException as error:
        print(f"Kite Connect network request failed ({type(error).__name__}).")
        return 1
    except KiteException as error:
        print(f"Kite Connect request failed (Kite status {error.code}).")
        return 1
    except (OSError, ValueError) as error:
        print(f"Could not complete the local backtest ({type(error).__name__}).")
        return 1

    if not summary.empty:
        columns = [
            "stock", "sma_completed_trades", "sma_win_rate_pct", "sma_return_pct",
            "sma_max_drawdown_pct", "buy_hold_return_pct", "buy_hold_max_drawdown_pct",
        ]
        print(summary[columns].to_string(index=False, float_format=lambda value: f"{value:.2f}"))
        print(f"Results saved in {destination}")
    if failed:
        print(f"Results unavailable for {', '.join(failed)}.")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
