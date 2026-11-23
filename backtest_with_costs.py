"""Backtest long-only SMA crossovers with estimated NSE delivery costs."""

import argparse
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


# Starting simulated capital. Change these constants to explore other assumptions.
STARTING_CAPITAL = 10_000.00
DATA_DIR = Path(__file__).with_name("data")
SMA_6_30_RESULTS = "sma_6_30_backtest_results.csv"

# Defaults below model NSE equity delivery using Zerodha's published schedule.
# Brokerage is zero for Zerodha equity delivery. Rates are decimals: 0.1% = 0.001.
# Check the broker/exchange schedule before relying on these estimates.
BROKERAGE_RATE = 0.0
BROKERAGE_PER_ORDER = 0.0
STT_BUY_RATE = 0.001                 # 0.1% on delivery buys
STT_SELL_RATE = 0.001                # 0.1% on delivery sells
NSE_TRANSACTION_RATE = 0.0000307     # 0.00307% on both sides
SEBI_TURNOVER_RATE = 0.000001        # INR 10 per crore on both sides
GST_RATE = 0.18                      # On brokerage + NSE charges + SEBI fee
STAMP_DUTY_BUY_RATE = 0.00015        # 0.015% on buys only
SLIPPAGE_RATE = 0.0005               # Illustrative 0.05% adverse price movement per side
DP_CHARGE_PER_SELL = 15.34           # Zerodha example: one INFY delivery sale event

# Current Zerodha charge references (rates can change):
# https://www.zerodha.com/charges
# https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated
# DP charges are included because they apply when delivery shares are sold.


def calculate_charges(turnover, side):
    """Estimate the regulatory and broker charges for one buy or sell order."""
    brokerage = turnover * BROKERAGE_RATE + BROKERAGE_PER_ORDER
    stt_rate = STT_BUY_RATE if side == "buy" else STT_SELL_RATE

    # STT is estimated to the nearest rupee for each order.
    stt = math.floor(turnover * stt_rate + 0.5)
    nse_transaction = turnover * NSE_TRANSACTION_RATE
    sebi_fee = turnover * SEBI_TURNOVER_RATE
    gst = (brokerage + nse_transaction + sebi_fee) * GST_RATE
    stamp_duty = turnover * STAMP_DUTY_BUY_RATE if side == "buy" else 0.0
    dp_charge = DP_CHARGE_PER_SELL if side == "sell" else 0.0

    pieces = {
        "brokerage": brokerage,
        "STT": stt,
        "NSE transaction charges": nse_transaction,
        "SEBI charges": sebi_fee,
        "GST": gst,
        "stamp duty": stamp_duty,
        "DP charge": dp_charge,
    }
    return pieces, sum(pieces.values())


def run_backtest(
    prices, include_costs, *, short_period=5, long_period=10, warmup_sessions=0, track_daily=False
):
    """Run the SMA strategy from completed closes, with next-open fills."""
    if not 0 < short_period < long_period:
        raise ValueError("The short SMA period must be positive and smaller than the long period.")
    if not 0 <= warmup_sessions < len(prices):
        raise ValueError("At least one session after warm-up is required.")
    prices = prices.sort_values("date").reset_index(drop=True)
    short_sma = prices["close"].rolling(window=short_period).mean()
    long_sma = prices["close"].rolling(window=long_period).mean()
    previous_short = short_sma.shift(1)
    previous_long = long_sma.shift(1)
    bullish = ((previous_short <= previous_long) & (short_sma > long_sma)).shift(1, fill_value=False)
    bearish = ((previous_short >= previous_long) & (short_sma < long_sma)).shift(1, fill_value=False)

    cash = STARTING_CAPITAL
    shares = 0
    peak_equity = STARTING_CAPITAL
    max_drawdown = 0.0
    entry_date = None
    entry_raw_price = None
    entry_costs = 0.0
    completed_trades = []
    daily = []
    total_costs = {
        "brokerage": 0.0,
        "STT": 0.0,
        "NSE transaction charges": 0.0,
        "SEBI charges": 0.0,
        "GST": 0.0,
        "stamp duty": 0.0,
        "DP charge": 0.0,
        "slippage": 0.0,
    }

    # Warm-up prices build the averages before the account starts trading.
    active = slice(warmup_sessions, None)
    for session, bullish_at_open, bearish_at_open in zip(
        prices.iloc[active].itertuples(index=False), bullish.iloc[active], bearish.iloc[active],
        strict=True,
    ):
        daily_cost = 0.0
        raw_price = float(session.open)
        if bullish_at_open and shares == 0 and raw_price > 0:
            if include_costs:
                fill_price = raw_price * (1 + SLIPPAGE_RATE)

                # Find the largest whole-share quantity affordable after fees.
                quantity = int(cash // fill_price)
                while quantity > 0:
                    buy_turnover = quantity * fill_price
                    buy_pieces, buy_charges = calculate_charges(buy_turnover, "buy")
                    if buy_turnover + buy_charges <= cash:
                        break
                    quantity -= 1
            else:
                fill_price = raw_price
                quantity = int(cash // fill_price)
                buy_pieces = {}
                buy_charges = 0.0

            if quantity > 0:
                shares = quantity
                entry_date = session.date.strftime("%Y-%m-%d")
                entry_raw_price = raw_price
                buy_slippage = quantity * (fill_price - raw_price) if include_costs else 0.0
                entry_costs = buy_charges + buy_slippage
                daily_cost = entry_costs
                cash -= quantity * fill_price + buy_charges

                if include_costs:
                    for name, amount in buy_pieces.items():
                        total_costs[name] += amount
                    total_costs["slippage"] += buy_slippage

        elif bearish_at_open and shares > 0 and raw_price > 0:
            if include_costs:
                fill_price = raw_price * (1 - SLIPPAGE_RATE)
                sell_turnover = shares * fill_price
                sell_pieces, sell_charges = calculate_charges(sell_turnover, "sell")
                sell_slippage = shares * (raw_price - fill_price)
                total_costs["slippage"] += sell_slippage
                for name, amount in sell_pieces.items():
                    total_costs[name] += amount
            else:
                fill_price = raw_price
                sell_charges = 0.0
                sell_slippage = 0.0

            gross_trade_profit = (raw_price - entry_raw_price) * shares
            daily_cost = sell_charges + sell_slippage
            trade_costs = entry_costs + daily_cost
            completed_trades.append(
                {
                    "buy_date": entry_date,
                    "sell_date": session.date.strftime("%Y-%m-%d"),
                    "shares": shares,
                    "gross_profit": gross_trade_profit,
                    "estimated_costs": trade_costs,
                    "net_profit": gross_trade_profit - trade_costs,
                }
            )
            cash += shares * fill_price - sell_charges
            shares = 0
            entry_date = None
            entry_raw_price = None
            entry_costs = 0.0

        equity = cash + shares * float(session.close)
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity)
        if track_daily:
            daily.append((session.date, equity, daily_cost))

    latest_close = float(prices.iloc[-1]["close"])
    open_position_gross = (latest_close - entry_raw_price) * shares if shares else 0.0
    completed_gross_profit = sum(trade["gross_profit"] for trade in completed_trades)
    gross_profit = completed_gross_profit + open_position_gross
    total_cost_amount = sum(total_costs.values())
    ending_value = cash + shares * latest_close
    net_profit = ending_value - STARTING_CAPITAL

    # The gross P/L less all costs must agree with the portfolio cash flows.
    assert abs((gross_profit - total_cost_amount) - net_profit) <= 0.01, (
        "Gross profit minus estimated costs does not reconcile with net profit."
    )
    assert abs(net_profit - (ending_value - STARTING_CAPITAL)) <= 0.01, (
        "Net profit does not equal ending portfolio value minus starting capital."
    )

    result = {
        "cash": cash,
        "shares": shares,
        "completed_trades": completed_trades,
        "gross_profit": gross_profit,
        "cost_breakdown": total_costs,
        "total_costs": total_cost_amount,
        "net_profit": net_profit,
        "ending_value": ending_value,
        "latest_close": latest_close,
        "max_drawdown_pct": max_drawdown * 100,
    }
    if track_daily:
        result["daily"] = pd.DataFrame(daily, columns=["date", "equity", "estimated_cost"])
    return result


def nse_session_date(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("Asia/Kolkata")
    return timestamp.date()


def load_stock_history(paths, completed_before):
    """Merge daily candles by NSE session date and check overlapping prices."""
    required = ["date", "open", "close"]
    histories = []
    for path in paths:
        history = pd.read_csv(path)
        if not set(required).issubset(history.columns):
            raise ValueError(f"{path.name} must contain date, open and close columns.")
        history = history[required].copy()
        history["date"] = history["date"].map(nse_session_date)
        for column in ("open", "close"):
            history[column] = pd.to_numeric(history[column], errors="coerce")
        if history["date"].isna().any() or not (
            (history[["open", "close"]] > 0) & (history[["open", "close"]] < math.inf)
        ).all().all():
            raise ValueError(f"{path.name} contains an invalid date or price.")
        histories.append(history)

    stocks = pd.concat(histories, ignore_index=True)
    prices = stocks.loc[stocks["date"] < completed_before].drop_duplicates()
    if prices["date"].duplicated().any():
        raise ValueError(f"Conflicting daily prices in {', '.join(path.name for path in paths)}.")
    return prices.sort_values("date").reset_index(drop=True)


def save_sma_6_30_results(data_dir=DATA_DIR):
    sources = {}
    for suffix in ("_1year.csv", "_historical.csv"):
        for path in sorted(data_dir.glob(f"*{suffix}")):
            symbol = path.name.removesuffix(suffix).upper()
            sources.setdefault(symbol, []).append(path)
    if not sources:
        raise ValueError("No saved NSE historical price CSVs were found.")

    completed_before = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    rows = []
    for symbol, paths in sorted(sources.items()):
        prices = load_stock_history(paths, completed_before)
        if prices.empty:
            raise ValueError(f"{symbol} has no usable completed daily candles.")
        result = run_backtest(prices, include_costs=True, short_period=6, long_period=30)
        trades = result["completed_trades"]
        wins = sum(trade["net_profit"] > 0 for trade in trades)
        realized = sum(trade["net_profit"] for trade in trades)
        slippage = result["cost_breakdown"]["slippage"]
        rows.append({
            "stock": symbol,
            "first_session": prices["date"].iloc[0],
            "last_session": prices["date"].iloc[-1],
            "sessions": len(prices),
            "short_sma": 6,
            "long_sma": 30,
            "starting_capital_inr": STARTING_CAPITAL,
            "total_trades": len(trades),
            "executed_transactions": 2 * len(trades) + bool(result["shares"]),
            "winning_trades": wins,
            "win_rate_pct": 100 * wins / len(trades) if trades else None,
            "net_pnl_inr": result["net_profit"],
            "realized_pnl_inr": realized,
            "unrealized_pnl_inr": result["net_profit"] - realized,
            "net_return_pct": 100 * result["net_profit"] / STARTING_CAPITAL,
            "max_drawdown_pct": result["max_drawdown_pct"],
            "trading_charges_inr": result["total_costs"] - slippage,
            "slippage_inr": slippage,
            "slippage_per_side_pct": SLIPPAGE_RATE * 100,
            "estimated_total_cost_inr": result["total_costs"],
            "ending_equity_inr": result["ending_value"],
            "open_shares": result["shares"],
            "source_files": ", ".join(path.name for path in paths),
        })

    summary = pd.DataFrame(rows)
    destination = data_dir / SMA_6_30_RESULTS
    summary.to_csv(destination, index=False, float_format="%.2f")
    print(summary[[
        "stock", "sessions", "total_trades", "win_rate_pct", "net_pnl_inr", "max_drawdown_pct",
    ]].to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    print(f"Saved results to {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sma-6-30", action="store_true", help="Backtest local daily data by stock and save CSV results."
    )
    if parser.parse_args().sma_6_30:
        try:
            save_sma_6_30_results()
        except (OSError, ValueError) as error:
            parser.exit(1, f"{error}\n")
        return

    print("PRELIMINARY BACKTEST - estimated costs; no real orders are placed")
    print("Costs are estimates and exclude any charges not listed in the assumptions.\n")

    csv_file = DATA_DIR / "INFY_1year.csv"
    if not csv_file.exists():
        print("One-year data file not found. Run historical_data_1year.py first.")
        return

    try:
        prices = pd.read_csv(csv_file, parse_dates=["date"])
    except Exception:
        print("Could not read the historical CSV file.")
        return

    required_columns = {"date", "open", "close"}
    if not required_columns.issubset(prices.columns):
        print("The CSV must contain date, open, and close columns.")
        return

    prices = prices.dropna(subset=["date", "open", "close"])
    prices = prices.sort_values("date").reset_index(drop=True)
    if prices.empty:
        print("The CSV does not contain usable price data.")
        return

    # The cost-free run uses the same capital and signals for a fair comparison.
    baseline = run_backtest(prices, include_costs=False)
    result = run_backtest(prices, include_costs=True)

    print(f"Starting simulated capital: INR {STARTING_CAPITAL:.2f}")
    print("\nCompleted buy/sell pairs (cost-aware run):")
    if result["completed_trades"]:
        for number, trade in enumerate(result["completed_trades"], start=1):
            print(
                f"{number}. Buy {trade['buy_date']}, sell {trade['sell_date']}, "
                f"{trade['shares']} share(s): gross P/L INR {trade['gross_profit']:+.2f}, "
                f"estimated costs INR {trade['estimated_costs']:.2f}, "
                f"net P/L INR {trade['net_profit']:+.2f}"
            )
    else:
        print("No completed buy/sell pairs.")

    print("\nCost-aware results")
    print(f"Completed trades: {len(result['completed_trades'])}")
    print(f"Gross P/L: INR {result['gross_profit']:+.2f}")
    print(f"Total estimated trading costs: INR {result['total_costs']:.2f}")
    for name, amount in result["cost_breakdown"].items():
        print(f"  {name}: INR {amount:.2f}")
    print(f"Net P/L: INR {result['net_profit']:+.2f}")
    net_return = result["net_profit"] / STARTING_CAPITAL * 100
    print(f"Net return: {net_return:+.2f}%")
    print(f"Ending portfolio value: INR {result['ending_value']:.2f}")

    print("\nComparison with the existing cost-free strategy (same INR 10,000 capital)")
    print(f"Cost-free ending value: INR {baseline['ending_value']:.2f}")
    print(f"Cost-free gross P/L: INR {baseline['gross_profit']:+.2f}")
    print(
        "Ending-value difference (cost-aware minus cost-free): "
        f"INR {result['ending_value'] - baseline['ending_value']:+.2f}"
    )
    if result["shares"]:
        print(
            f"Open shares at the end: {result['shares']} share(s), "
            f"valued at latest close INR {result['latest_close']:.2f}"
        )


if __name__ == "__main__":
    main()
