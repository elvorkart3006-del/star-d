"""Evaluate fixed SMA 6/30 on three to five years of NSE daily candles."""

import argparse
from datetime import date, datetime, timedelta

import pandas as pd
from kiteconnect.exceptions import KiteException, PermissionException, TokenException
from requests.exceptions import RequestException

import backtest_sma_6_30_kite as kite_batch
import backtest_with_costs as backtest
import multi_stock_scanner as scanner


OUTPUT_ROOT = backtest.DATA_DIR / "sma_6_30_multiyear"
CAPITAL = backtest.STARTING_CAPITAL
SHORT_PERIOD = kite_batch.SHORT_PERIOD
LONG_PERIOD = kite_batch.LONG_PERIOD


def evaluation_dates(end, years):
    """Build adjacent calendar windows ending with a one-year holdout."""
    after_end = pd.Timestamp(end + timedelta(days=1))
    start = (after_end - pd.DateOffset(years=years)).date()
    holdout_start = (after_end - pd.DateOffset(years=1)).date()
    return start, holdout_start


def run_period(symbol, phase, prices, warmup=None):
    """Start from cash, using earlier prices only to seed the holdout SMAs."""
    warmup_sessions = 0 if warmup is None else len(warmup)
    inputs = prices if warmup is None else pd.concat([warmup, prices], ignore_index=True)
    strategy = backtest.run_backtest(
        inputs, True, short_period=SHORT_PERIOD, long_period=LONG_PERIOD,
        warmup_sessions=warmup_sessions, track_daily=True,
    )
    benchmark = kite_batch.buy_and_hold(prices, track_daily=True)
    summary = {
        "period": phase,
        **kite_batch.comparison_row(symbol, prices, strategy, benchmark),
        "sma_prior_warmup_sessions": warmup_sessions,
    }
    return summary, yearly_rows(symbol, phase, strategy, benchmark)


def yearly_rows(symbol, phase, strategy, benchmark):
    """Break continuous equity into calendar-year results within one period."""
    sma_daily = strategy["daily"]
    hold_daily = benchmark["daily"]
    trades = strategy["completed_trades"]
    starting_sma, starting_hold = CAPITAL, CAPITAL
    rows = []
    for year, sma in sma_daily.groupby(sma_daily["date"].dt.year):
        hold = hold_daily.loc[sma.index]
        closed_trades = [trade for trade in trades if trade["sell_date"][:4] == str(year)]
        wins = sum(trade["net_profit"] > 0 for trade in closed_trades)
        sma_metrics = yearly_equity_metrics(sma, starting_sma)
        hold_metrics = yearly_equity_metrics(hold, starting_hold)
        rows.append({
            "period": phase,
            "calendar_year": year,
            "stock": symbol,
            "first_session": sma["date"].iloc[0].date(),
            "last_session": sma["date"].iloc[-1].date(),
            "sessions": len(sma),
            "sma_completed_trades": len(closed_trades),
            "sma_winning_trades": wins,
            "sma_win_rate_pct": 100 * wins / len(closed_trades) if closed_trades else None,
            **{f"sma_{key}": value for key, value in sma_metrics.items()},
            **{f"buy_hold_{key}": value for key, value in hold_metrics.items()},
            "sma_minus_buy_hold_pct_points": sma_metrics["return_pct"] - hold_metrics["return_pct"],
        })
        starting_sma = sma_metrics["ending_equity_inr"]
        starting_hold = hold_metrics["ending_equity_inr"]
    return rows


def yearly_equity_metrics(daily, starting_equity):
    equity = daily["equity"]
    ending_equity = float(equity.iloc[-1])
    pnl = ending_equity - starting_equity
    peak = equity.cummax().clip(lower=starting_equity)
    return {
        "starting_equity_inr": starting_equity,
        "ending_equity_inr": ending_equity,
        "net_pnl_inr": pnl,
        "return_pct": 100 * pnl / starting_equity,
        "max_drawdown_pct": 100 * float(((peak - equity) / peak).max()),
        "estimated_cost_inr": float(daily["estimated_cost"].sum()),
    }


def evaluate(histories, holdout_start):
    """Evaluate all development windows before scoring any holdout window."""
    periods = {}
    for symbol, prices in histories.items():
        early = prices["date"].dt.date < holdout_start
        development = prices.loc[early].reset_index(drop=True)
        holdout = prices.loc[~early].reset_index(drop=True)
        if len(development) < LONG_PERIOD + 2 or holdout.empty:
            raise ValueError(f"{symbol} has insufficient development or holdout candles.")
        periods[symbol] = (development, holdout)

    summary = []
    yearly = []
    for phase in ("development", "out_of_sample"):
        for symbol, (development, holdout) in periods.items():
            if phase == "development":
                row, years = run_period(symbol, phase, development)
            else:
                row, years = run_period(symbol, phase, holdout, development.tail(LONG_PERIOD + 1))
            summary.append(row)
            yearly.extend(years)
    return pd.DataFrame(summary), pd.DataFrame(yearly)


def compare_market_periods(yearly):
    """Compare calendar periods using the seven stocks' average return."""
    return yearly.assign(outperformed=yearly["sma_minus_buy_hold_pct_points"] > 0).groupby(
        ["calendar_year", "period"], as_index=False
    ).agg(
        first_session=("first_session", "min"),
        last_session=("last_session", "max"),
        stocks=("stock", "size"),
        mean_sma_return_pct=("sma_return_pct", "mean"),
        mean_buy_hold_return_pct=("buy_hold_return_pct", "mean"),
        mean_sma_minus_buy_hold_pct_points=("sma_minus_buy_hold_pct_points", "mean"),
        stocks_sma_beat_buy_hold=("outperformed", "sum"),
        mean_sma_max_drawdown_pct=("sma_max_drawdown_pct", "mean"),
        mean_buy_hold_max_drawdown_pct=("buy_hold_max_drawdown_pct", "mean"),
    )


def main(argv=None):
    yesterday = datetime.now(scanner.IST).date() - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, choices=(3, 4, 5), default=5)
    parser.add_argument("--end-date", type=date.fromisoformat, default=yesterday)
    args = parser.parse_args(argv)
    if args.end_date > yesterday:
        parser.error("Choose an end date no later than yesterday in India.")

    start, holdout_start = evaluation_dates(args.end_date, args.years)
    destination = OUTPUT_ROOT / f"{start}_{args.end_date}"
    try:
        histories = {}
        for symbol, prices in kite_batch.iter_histories(start, args.end_date, destination):
            if prices is not None:
                histories[symbol] = prices
                print(f"{symbol}: {len(prices)} completed sessions available.", flush=True)
        missing = [symbol for symbol in kite_batch.SYMBOLS if symbol not in histories]
        if missing:
            print(f"Missing usable histories for {', '.join(missing)}.")
            return 1
        summary, yearly = evaluate(histories, holdout_start)
        market = compare_market_periods(yearly)
        for name, frame in (
            ("phase_summary.csv", summary),
            ("yearly_performance.csv", yearly),
            ("market_periods.csv", market),
        ):
            kite_batch.save_csv_once(frame, destination / name, float_format="%.2f")
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

    print(f"Holdout starts on {holdout_start}. Reports saved in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
