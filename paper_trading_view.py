"""Streamlit controls for the local PAPER TRADING account."""

import sqlite3
import time
from datetime import datetime

import pandas as pd
import streamlit as st
from kiteconnect.exceptions import TokenException

import multi_stock_scanner as scanner
import paper_trading as paper


MONITOR_SECONDS = 30


def render_paper_trading(names, fetch_quotes):
    st.divider()
    st.header("PAPER TRADING")
    st.info("Simulation only. Zerodha supplies prices. Fills, cash and stops are virtual.")
    monitor = st.toggle("Monitor PAPER stops while this dashboard is open", key="paper_monitor")
    st.caption(
        "Stops are checked on paper price refreshes and trade submissions. Optional monitoring "
        "checks held stocks every 30 seconds during the 09:15–15:30 IST simulation window. "
        "A stop fills at the observed price with slippage, so gaps can increase the loss."
    )
    panel = st.fragment(
        _render_account, run_every=MONITOR_SECONDS if monitor else None, key="paper_account"
    )
    panel(names, fetch_quotes, monitor)


def _render_account(names, fetch_quotes, monitor):
    account = paper.PaperAccount()
    try:
        portfolio = account.snapshot()
        settings_changed = _account_settings(account, portfolio.settings)

        names = {**names, **{symbol: symbol for symbol in portfolio.positions if symbol not in names}}
        symbol = st.selectbox(
            "PAPER stock",
            options=list(names),
            format_func=lambda symbol: f"{symbol} | {names[symbol]}" if names[symbol] != symbol else symbol,
            key="paper_symbol",
        )
        with st.form("paper_trade", enter_to_submit=False):
            side_col, qty_col = st.columns(2)
            side = side_col.radio("PAPER side", ("BUY", "SELL"), horizontal=True, key="paper_side")
            quantity = qty_col.number_input(
                "PAPER shares", min_value=1, max_value=paper.MAX_QUANTITY, step=1, key="paper_quantity"
            )
            submitted = st.form_submit_button(
                "Simulate PAPER trade", type="primary", disabled=symbol is None
            )
        refresh = st.button(
            "Refresh PAPER prices and check stops", key="paper_refresh",
            disabled=symbol is None and not portfolio.positions,
        )

        now = paper.now_ist()
        if submitted and not paper.in_session(now):
            st.warning("PAPER trades require a fresh last trade within 09:15–15:30 IST.")
            submitted = False
        monitor_due = (
            monitor
            and bool(portfolio.positions)
            and paper.in_session(now)
            and time.monotonic() - st.session_state.get("paper_last_check", -MONITOR_SECONDS)
            >= MONITOR_SECONDS
        )
        check_prices = refresh or submitted or monitor_due
        if check_prices:
            symbols = list(portfolio.positions)
            if (refresh or submitted) and symbol and symbol not in symbols:
                symbols.append(symbol)
            st.session_state["paper_last_check"] = time.monotonic()
            _process_prices(account, fetch_quotes, symbols, symbol, side, quantity, submitted)

        last_quote = st.session_state.get("paper_selected_quote")
        if last_quote and last_quote[0] == symbol:
            quote = last_quote[1]
            label = "fresh" if quote.is_fresh(paper.now_ist()) else "last observed"
            st.caption(f"PAPER reference price for {symbol} · {_inr(quote.price)} · {quote.at:%d %b %Y %H:%M:%S} IST · {label}")
        _portfolio_tables(account.snapshot() if settings_changed or check_prices else portfolio)
    except (sqlite3.Error, OSError):
        st.error("The local PAPER TRADING journal is unavailable. Check that the data folder is writable.")


def _account_settings(account, saved):
    changed = False
    with st.expander("PAPER capital and risk limits"):
        with st.form("paper_settings", enter_to_submit=False):
            left, right = st.columns(2)
            capital = left.number_input(
                "Virtual capital (INR)", min_value=0.01, max_value=float(paper.MAX_CAPITAL),
                value=saved.capital / 100, step=1000.0, format="%.2f", key="paper_capital",
            )
            position_pct = left.number_input(
                "Maximum capital per stock (%)", min_value=1, max_value=100,
                value=saved.position_pct, step=1, key="paper_position_pct",
            )
            max_positions = right.number_input(
                "Maximum open positions", min_value=1, max_value=50,
                value=saved.max_positions, step=1, key="paper_max_positions",
            )
            stop_pct = right.number_input(
                "Stop-loss distance for new buys (%)", min_value=1, max_value=50,
                value=saved.stop_pct, step=1, key="paper_stop_pct",
            )
            if st.form_submit_button("Save PAPER settings"):
                try:
                    account.configure(paper.Settings(paper.to_paise(capital), position_pct, max_positions, stop_pct))
                    changed = True
                    st.success("PAPER settings saved.")
                except paper.PaperTradeError as error:
                    st.error(str(error))
        st.caption(
            "Changing virtual capital adds or removes paper cash. Cash and exposure limits apply "
            "to new buys. Adding shares can tighten an existing stop."
        )
    return changed


def _process_prices(account, fetch_quotes, symbols, selected, side, quantity, submitted):
    try:
        response = fetch_quotes(symbols)
    except scanner.MissingCredentialsError:
        st.error("PAPER price request needs Kite credentials in .env.")
        return
    except TokenException:
        st.error("The Kite access token expired or is invalid. Sign in again to refresh PAPER prices.")
        return
    except Exception:
        st.error("PAPER prices are unavailable. Retry the price request.")
        return

    now = paper.now_ist()
    quotes = {}
    for symbol in symbols:
        packet = response.get(f"NSE:{symbol}") if isinstance(response, dict) else None
        if not isinstance(packet, dict):
            continue
        timestamp = scanner._india_datetime(packet.get("last_trade_time"))
        try:
            price = paper.to_paise(packet.get("last_price"))
        except paper.PaperTradeError:
            continue
        if timestamp and timestamp <= now and price > 0:
            quotes[symbol] = paper.Quote(price, timestamp)
    if selected in quotes:
        quote = quotes[selected]
        previous = st.session_state.get("paper_selected_quote")
        if previous is None or previous[0] != selected or previous[1].at <= quote.at:
            st.session_state["paper_selected_quote"] = (selected, quote)
    stopped = account.apply_quotes(quotes, now)
    if stopped:
        st.warning(f"PAPER stop-loss executed for {', '.join(stopped)}. See the journal for fills.")
    if len(quotes) < len(symbols):
        missing = ", ".join(symbol for symbol in symbols if symbol not in quotes)
        st.warning(f"No valid PAPER price received for {missing}. Any previous marks stay in use.")
    if submitted and selected in stopped:
        st.info("The PAPER stop closed this position. Submit a new trade if you want to re-enter.")
    elif submitted:
        try:
            fill = account.trade(selected, side, quantity, quotes.get(selected), now)
            st.success(f"PAPER {side} · {quantity} × {selected} at {_inr(fill)}.")
        except paper.PaperTradeError as error:
            st.error(str(error))


def _inr(paise):
    return f"₹{paise / 100:,.2f}"


def _portfolio_tables(portfolio):
    with st.container(horizontal=True):
        for label, value in (
            ("PAPER cash", portfolio.cash),
            ("PAPER equity", portfolio.cash + portfolio.market_value),
            ("PAPER realized P/L", portfolio.realized),
            ("PAPER unrealized P/L", portfolio.unrealized),
            ("PAPER estimated costs", portfolio.costs),
        ):
            st.metric(label, _inr(value), border=True)
    st.caption(
        f"Funded capital {_inr(portfolio.settings.capital)} · "
        f"{portfolio.settings.position_pct}% per stock · {portfolio.settings.max_positions} open positions. "
        "P/L includes incurred fees and slippage. Estimates use NSE delivery fees, "
        f"one DP fee per stock per day and {paper.SLIPPAGE_RATE:.2%} adverse slippage."
    )

    st.subheader("PAPER positions")
    if not portfolio.positions:
        st.caption("No open paper positions.")
    else:
        now = paper.now_ist()
        rows = []
        for symbol, holding in portfolio.positions.items():
            quote = paper.Quote(holding["last_price"], datetime.fromisoformat(holding["quote_time"]))
            rows.append(
                {
                    "Symbol": symbol,
                    "Shares": holding["quantity"],
                    "Avg. cost (INR)": round(holding["cost"] / holding["quantity"] / 100, 2),
                    "Last price (INR)": quote.price / 100,
                    "Last trade (IST)": quote.at.strftime("%Y-%m-%d %H:%M:%S"),
                    "Price status": "Fresh" if quote.is_fresh(now) else "Last observed",
                    "Stop price (INR)": holding["stop"] / 100,
                    "Unrealized P/L (INR)": (quote.price * holding["quantity"] - holding["cost"]) / 100,
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, alt="PAPER positions, marks, stops and unrealized profit or loss")

    st.subheader("PAPER trade journal")
    if not portfolio.journal:
        st.caption("Simulated fills will appear here and stay available across restarts.")
    else:
        journal = _journal_dataframe(portfolio.journal)
        st.dataframe(journal, hide_index=True, alt="Permanent PAPER TRADING journal")
        st.download_button(
            "Download PAPER journal CSV",
            data=journal.to_csv(index=False).encode("utf-8"),
            file_name="paper_trading_journal.csv",
            mime="text/csv",
            on_click="ignore",
        )


def _journal_dataframe(trades):
    journal = pd.DataFrame([dict(trade) for trade in trades])
    money_columns = ["quote_price", "fill_price", "fees", "slippage", "realized_pnl", "cash_after"]
    journal[money_columns] = journal[money_columns].astype(float) / 100
    return journal.rename(
        columns={
            "id": "Paper ID", "executed_at": "Filled at (IST)", "symbol": "Symbol", "side": "Side",
            "quantity": "Shares", "quote_price": "Quote (INR)", "quote_time": "Quote time (IST)",
            "fill_price": "Fill (INR)", "fees": "Estimated fees (INR)", "slippage": "Slippage (INR)",
            "realized_pnl": "Realized P/L (INR)", "cash_after": "Cash after (INR)", "reason": "Reason",
        }
    )
