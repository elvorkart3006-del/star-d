"""Calendar-day accounting for the recorded DC paper ledger."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time, timedelta

import pandas as pd

import backtest_sma_6_30_kite as kite_batch
import dc_forward as forward


REPORT_AFTER = time(17, 15)
SYMBOLS = forward.SYMBOLS
CAPITAL = forward.TOTAL_CAPITAL
API_ERROR_STATUSES = {"Expired or invalid Kite token", "Network error", "Kite API error"}
# NSE capital market circular CMTR71775. Recorded candles override the calendar.
HOLIDAYS_2026 = frozenset(map(date.fromisoformat, (
    "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
    "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25",
)))


@dataclass
class DayReport:
    summary: dict
    signals: list
    positions: list
    fills: list
    missed: list
    observations: list
    readiness: list


def days_between(first, last):
    for offset in range((last - first).days + 1):
        yield first + timedelta(days=offset)


def day_of(timestamp):
    return date.fromisoformat(timestamp[:10])


def scheduled_closure(day, overrides):
    if day in overrides:
        return overrides[day] == "closed"
    return day.weekday() >= 5 or day in HOLIDAYS_2026


def trade_metrics(closed):
    wins = [pnl for pnl in closed if pnl > 0]
    losses = [pnl for pnl in closed if pnl < 0]
    return {
        "closed_trades": len(closed),
        "win_rate_pct": 100 * len(wins) / len(closed) if closed else None,
        "average_win_inr": sum(wins) / len(wins) / 100 if wins else None,
        "average_loss_inr": sum(losses) / len(losses) / 100 if losses else None,
        "expectancy_inr": sum(closed) / len(closed) / 100 if closed else None,
    }


def readiness_progress(rows, trading):
    expected = [row for row in rows if row["stage"] == "After close" and row["market"] != "Scheduled closure"]
    complete = sum(row["market"] == "Session" and row["dc_candles_received"] == len(SYMBOLS) for row in expected)
    recent = expected[-20:]
    healthy = sum(row["dc_candles_received"] == len(SYMBOLS) and row["all_quotes_fresh_observed"] for row in recent)
    missed = sum(row["missed_trades"] or 0 for row in recent)
    expectancy = trading["expectancy_inr"]
    drawdowns = [row["max_drawdown_pct"] for row in rows if row["max_drawdown_pct"] is not None]
    drawdown = drawdowns[-1] if drawdowns else None
    tests = [
        ("Complete trading sessions", "At least 60", f"{complete} / 60", complete >= 60),
        ("Closed round trips", "At least 30", f"{trading['closed_trades']} / 30", trading["closed_trades"] >= 30),
        ("Net expectancy", "Above ₹0 per closed trade", f"₹{expectancy:.2f}" if expectancy is not None else "Unavailable",
         expectancy is not None and expectancy > 0),
        ("Worst observed closing drawdown", "At most 20%", f"{drawdown:.2f}%" if drawdown is not None else "Unavailable",
         drawdown is not None and drawdown <= 20),
        ("Recent data and quote monitoring", "At least 19 of last 20 expected sessions",
         f"{healthy} / {len(recent)} observed", len(recent) == 20 and healthy >= 19),
        ("Recent missed fills", "Zero over last 20 observable expected sessions",
         f"{missed} observed in {len(recent)} sessions",
         len(recent) == 20 and all(row["missed_trades"] is not None for row in recent) and missed == 0),
    ]
    return [
        {"criterion": name, "target": target, "progress": value, "met": met}
        for name, target, value, met in tests
    ]


def _as_rupees(amount):
    return amount / 100 if amount is not None else None


def _benchmark(entry, marks):
    if not entry or not marks:
        return None
    pnl = 0.0
    for symbol in SYMBOLS:
        # The existing benchmark sizes whole shares and includes fees and slippage.
        price = pd.DataFrame({"open": [entry[symbol] / 100], "close": [marks[symbol] / 100]})
        pnl += kite_batch.buy_and_hold(price)["buy_hold_net_pnl_inr"]
    return pnl


def build_reports(snapshot, through, now, *, overrides=None, observations=(), report_closes=None):
    """Rebuild each date solely from its recorded paper fills and completed prices."""
    overrides = overrides or {}
    report_closes = report_closes or {}
    first = day_of(snapshot.run["started_at"]) if snapshot else through
    if first > through:
        first = through
        snapshot = None

    signals_by_day, fills_by_day, logs = defaultdict(list), defaultdict(list), defaultdict(list)
    market = defaultdict(dict)
    for row in observations if not snapshot else snapshot.observations:
        logs[day_of(row["observed_at"])].append(dict(row))
    if snapshot:
        for row in snapshot.closes:
            market[day_of(row["session"])][row["symbol"]] = row["price"]
        for row in snapshot.signals:
            signals_by_day[day_of(row["session"])].append(dict(row))
        for row in snapshot.fills:
            fills_by_day[day_of(row["filled_at"])].append(dict(row))

    cash = CAPITAL
    held = {}
    closed = []
    paid = slip = 0
    peak = CAPITAL
    worst = 0.0
    previous_equity = CAPITAL
    previous_marks = {}
    previous_mark_day = None
    bridge_known = True
    benchmark_day = None
    benchmark_entry = None
    previous_benchmark_equity = None
    previous_session = None
    session_gap = False
    history = []
    reports = {}
    for day in days_between(first, through):
        enrolled = snapshot is not None
        is_final = day < now.date() or day == now.date() and now.time() >= REPORT_AFTER
        stage = "After close" if is_final else "Interim"
        journal_marks = market.get(day, {})
        scanner_marks = report_closes.get(day, {})
        marks_today = {**scanner_marks, **journal_marks}
        uses_scanner = bool(scanner_marks.keys() - journal_marks.keys())
        close_source = "DC journal" if journal_marks else None
        if uses_scanner:
            close_source = "DC journal and saved scanner export" if journal_marks else "Saved scanner export"
        fills = sorted(fills_by_day[day], key=lambda row: row["filled_at"])
        signals = signals_by_day[day]
        has_session = bool(marks_today or fills)
        closure = not has_session and scheduled_closure(day, overrides)
        market_status = "Session" if has_session else "Scheduled closure" if closure else "Unverified"
        complete = all(symbol in marks_today for symbol in SYMBOLS)
        day_fees = day_slip = 0
        day_closed = []
        fill_keys = {(row["symbol"], row["session"]) for row in fills}
        misses = []
        missed_observable = enrolled and bool(journal_marks or fills) and is_final and not session_gap
        if missed_observable and previous_session is not None:
            for signal in signals_by_day[previous_session]:
                active = (signal["direction"] == "up") != (signal["symbol"] in held)
                if active and (signal["symbol"], signal["session"]) not in fill_keys:
                    misses.append({
                        "symbol": signal["symbol"], "signal_session": signal["session"],
                        "direction": signal["direction"], "expected_fill_date": day.isoformat(),
                        "ledger_status_at_generation": signal["status"],
                    })

        for fill in fills:
            symbol = fill["symbol"]
            turnover = fill["shares"] * fill["fill_price"]
            day_fees += fill["fees"]
            day_slip += fill["slippage"]
            if fill["direction"] == "up":
                basis = turnover + fill["fees"]
                held[symbol] = (fill["shares"], basis)
                cash -= basis
            else:
                held.pop(symbol)
                cash += turnover - fill["fees"]
                day_closed.append(fill["realized_pnl"])
        closed.extend(day_closed)
        paid += day_fees
        slip += day_slip

        marks, mark_day = {}, None
        if has_session and is_final:
            marks, mark_day = marks_today, day
        elif closure and is_final and bridge_known:
            marks, mark_day = previous_marks, previous_mark_day
        if not enrolled:
            equity = None
        elif (has_session or closure) and is_final and all(symbol in marks for symbol in held):
            equity = cash + sum(qty * marks[symbol] for symbol, (qty, _) in held.items())
        else:
            equity = None
        # A missing trading valuation breaks the daily comparison, even when the next close is available.
        daily_pnl = equity - previous_equity if equity is not None and previous_equity is not None else None
        daily_return = 100 * daily_pnl / previous_equity if daily_pnl is not None and previous_equity else None
        if equity is not None:
            peak = max(peak, equity)
        drawdown = 100 * (peak - equity) / peak if equity is not None else None
        if drawdown is not None:
            worst = max(worst, drawdown)

        if enrolled and day > first and has_session and is_final and benchmark_day is None:
            benchmark_day = day
            if complete:
                benchmark_entry = marks_today
        benchmark_pnl = _benchmark(benchmark_entry, marks) if all(symbol in marks for symbol in SYMBOLS) else None
        benchmark_equity = CAPITAL / 100 + benchmark_pnl if benchmark_pnl is not None else None
        benchmark_return = benchmark_pnl * 100 / (CAPITAL / 100) if benchmark_pnl is not None else None
        cumulative_return = 100 * (equity / CAPITAL - 1) if equity is not None else None
        today_logs = logs[day]
        quote_logs = [row for row in today_logs if row["fresh_quotes"] is not None]
        trading = trade_metrics(closed) if enrolled else {key: None for key in trade_metrics([])}
        summary = {
            "date": day.isoformat(), "stage": stage, "market": market_status,
            "forward_started": enrolled, "mark_session": mark_day.isoformat() if mark_day else None,
            "candles_received": len(marks_today), "stocks_expected": len(SYMBOLS) if not closure else 0,
            "dc_candles_received": len(journal_marks), "close_source": close_source,
            "data_status": "Complete" if complete else "Scheduled closure" if closure else
                           "Close pending" if not is_final else "Incomplete" if has_session else "No completed bars verified",
            "cash_inr": _as_rupees(cash) if enrolled else None,
            "equity_inr": _as_rupees(equity),
            "daily_net_pnl_inr": _as_rupees(daily_pnl), "daily_return_pct": daily_return,
            "cumulative_net_pnl_inr": _as_rupees(equity - CAPITAL) if equity is not None else None,
            "cumulative_return_pct": cumulative_return,
            "cumulative_realized_pnl_inr": _as_rupees(sum(closed)) if enrolled else None,
            "daily_realized_pnl_inr": _as_rupees(sum(day_closed)) if enrolled else None,
            "daily_closed_trades": len(day_closed) if enrolled else None,
            **trading, "drawdown_pct": drawdown, "max_drawdown_pct": worst if equity is not None else None,
            "daily_fees_inr": _as_rupees(day_fees) if enrolled else None,
            "daily_slippage_inr": _as_rupees(day_slip) if enrolled else None,
            "cumulative_fees_inr": _as_rupees(paid) if enrolled else None,
            "cumulative_slippage_inr": _as_rupees(slip) if enrolled else None,
            "buy_hold_entry_session": benchmark_day.isoformat() if benchmark_day else None,
            "buy_hold_equity_inr": benchmark_equity,
            "buy_hold_daily_return_pct": 100 * (benchmark_equity / previous_benchmark_equity - 1)
                if benchmark_equity is not None and previous_benchmark_equity else None,
            "buy_hold_return_pct": benchmark_return,
            "dc_minus_buy_hold_pct_points": cumulative_return - benchmark_return
                if cumulative_return is not None and benchmark_return is not None else None,
            "new_signals": len(signals) if enrolled else None, "open_positions": len(held) if enrolled else None,
            "simulated_fills": len(fills) if enrolled else None,
            "missed_trades": len(misses) if missed_observable else 0 if closure and enrolled else None,
            "refresh_attempts_recorded": len(today_logs),
            "refresh_errors_recorded": sum(row["status"] != "Updated" for row in today_logs) if today_logs else None,
            "api_errors_recorded": sum(row["status"] in API_ERROR_STATUSES for row in today_logs) if today_logs else None,
            "quote_checks_recorded": len(quote_logs),
            "stale_quotes_recorded": sum(row["stale_quotes"] for row in quote_logs) if quote_logs else None,
            "missing_quotes_recorded": sum(row["missing_quotes"] for row in quote_logs) if quote_logs else None,
            "all_quotes_fresh_observed": any(row["fresh_quotes"] == len(SYMBOLS) for row in quote_logs),
        }
        positions = [
            {"symbol": symbol, "shares": qty, "cost_inr": _as_rupees(basis),
             "mark_inr": _as_rupees(marks.get(symbol)),
             "unrealized_pnl_inr": _as_rupees(qty * marks[symbol] - basis) if symbol in marks else None}
            for symbol, (qty, basis) in sorted(held.items())
        ]
        history.append(summary)
        reports[day] = DayReport(
            summary, signals, positions, fills, misses, today_logs, readiness_progress(history, trading)
            if enrolled else [],
        )
        previous_equity = equity
        previous_benchmark_equity = benchmark_equity
        if has_session and is_final:
            previous_session = day
            session_gap = len(journal_marks) != len(SYMBOLS)
            bridge_known = complete
            previous_marks, previous_mark_day = marks_today, day
        elif not closure and is_final:
            bridge_known = False
            session_gap = True
    return reports
