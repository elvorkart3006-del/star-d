"""Streamlit panel for the separate DC 5% forward paper account."""

import sqlite3
from datetime import timedelta

import pandas as pd
import streamlit as st
from kiteconnect.exceptions import KiteException, TokenException
from requests.exceptions import RequestException

import dc_forward as forward
from dc_report_view import render_daily_report
import dc_research as research
import multi_stock_scanner as scanner
import paper_trading as paper


@st.cache_data(show_spinner=False)
def load_seed():
    return research.load_histories()


def fetch_market(starts, now, request_lock):
    """Reuse the dashboard's request lock and Kite pacing for read-only data."""
    if not request_lock.acquire(blocking=False):
        raise RuntimeError("Another Kite data request is running.")
    try:
        kite = scanner.create_kite_client()
        cutoff = forward.completed_cutoff(now)
        candles = {}
        due = {symbol: start for symbol, start in starts.items() if start <= cutoff}
        if due:
            catalog = st.session_state.get("nse_catalog") or scanner.get_nse_equity_universe(kite)
            candles = forward.fetch_completed_candles(kite, catalog, due, cutoff)

        packets = {}
        if paper.in_session(paper.now_ist()):
            symbols = [f"NSE:{symbol}" for symbol in forward.SYMBOLS]
            packets = scanner._call_with_rate_limit(lambda: kite.quote(symbols), "quote")
        return candles, parse_quotes(packets)
    finally:
        request_lock.release()


def parse_quotes(packets):
    quotes = {}
    for symbol in forward.SYMBOLS:
        packet = packets.get(f"NSE:{symbol}", {})
        at = scanner._india_datetime(packet.get("last_trade_time"))
        try:
            price = paper.to_paise(packet.get("last_price"))
        except paper.PaperTradeError:
            continue
        if at and price > 0:
            previous = packet.get("ohlc") or {}
            try:
                previous_close = paper.to_paise(previous.get("close"))
            except paper.PaperTradeError:
                previous_close = 0
            quotes[symbol] = forward.MarketQuote(paper.Quote(price, at), previous_close)
    return quotes


def render_dc_forward(request_lock):
    st.divider()
    st.header("DC 5% · FORWARD PAPER TRADING")
    st.caption(
        "Fixed 5% reversal rule for INFY, TCS, WIPRO, HDFCBANK, ICICIBANK, SBIN and ITC. "
        "Each has ₹10,000 in virtual capital. The first successful refresh starts the forward test."
    )
    st.caption(
        "Refresh after 16:00 IST for completed daily signals and during 09:15–15:30 IST on the next trading session "
        "for simulated fills. A fill uses a matching, current-session Kite trade at most five "
        "minutes old, with estimated fees and slippage. Drawdown is measured at refreshes."
    )
    ledger = forward.ForwardLedger()
    try:
        seed = load_seed()
        snapshot = ledger.snapshot()
        histories = forward.histories_with_closes(seed, snapshot.closes if snapshot else ())
        if st.button("Refresh DC signals and paper fills", key="dc_forward_refresh"):
            try:
                starts = {
                    symbol: prices["date"].iloc[-1].date() + timedelta(days=1)
                    for symbol, prices in histories.items()
                }
                with st.spinner("Checking completed DC candles and current quotes…"):
                    now = paper.now_ist()
                    candles, quotes = fetch_market(starts, now, request_lock)
                    count = ledger.refresh(
                        seed, candles, quotes, paper.now_ist(),
                        source="Dashboard", quotes_requested=paper.in_session(now),
                    )
                st.success(f"DC updated. Simulated fills recorded: {count}.")
                snapshot = ledger.snapshot()
                histories = forward.histories_with_closes(seed, snapshot.closes)
            except scanner.MissingCredentialsError:
                ledger.record_observation(paper.now_ist(), "Dashboard", "Missing credentials")
                st.error("DC refresh needs Kite credentials and an access token in .env.")
            except TokenException:
                ledger.record_observation(paper.now_ist(), "Dashboard", "Expired or invalid Kite token")
                st.error("The Kite access token is expired or invalid. Sign in again to refresh DC.")
            except RequestException:
                ledger.record_observation(paper.now_ist(), "Dashboard", "Network error")
                st.error("DC could not reach Kite. Try again when connected.")
            except KiteException:
                ledger.record_observation(paper.now_ist(), "Dashboard", "Kite API error")
                st.error("Kite could not complete the DC data request.")
            except Exception:
                ledger.record_observation(paper.now_ist(), "Dashboard", "Refresh error")
                st.error("DC refresh could not complete. Try again to update signals and quotes.")
        st.dataframe(
            forward.latest_signals(histories), hide_index=True, alt="DC 5% signals from completed daily candles"
        )
        if snapshot is None:
            st.caption("Showing saved historical signals. The forward paper journal has yet to start.")
        else:
            render_ledger(snapshot)
    except (OSError, ValueError, sqlite3.Error):
        st.error("The DC history or forward paper journal could not be read.")
    render_daily_report()


def render_ledger(snapshot):
    sleeves = snapshot.sleeves
    fills = snapshot.fills
    equity = sum(row["cash"] + row["shares"] * row["mark"] for row in sleeves)
    realized = sum(row["realized_pnl"] or 0 for row in fills)
    unrealized = sum(row["shares"] * row["mark"] - row["basis"] for row in sleeves)
    fees = sum(row["fees"] for row in fills)
    slippage = sum(row["slippage"] for row in fills)
    st.caption(
        f"Forward start {snapshot.run['started_at'][:10]} · "
        f"Last refresh {snapshot.run['updated_at'][:19].replace('T', ' ')} IST"
    )
    with st.container(horizontal=True):
        st.metric("DC equity", f"₹{equity / 100:,.2f}", border=True)
        st.metric("DC net P/L", f"₹{(realized + unrealized) / 100:,.2f}", border=True)
        st.metric("DC realized P/L", f"₹{realized / 100:,.2f}", border=True)
        st.metric("DC unrealized P/L", f"₹{unrealized / 100:,.2f}", border=True)
        st.metric("DC max drawdown", f"{snapshot.run['max_drawdown']:.2%}", border=True)
        st.metric("DC fees", f"₹{fees / 100:,.2f}", border=True)
    st.caption(f"Estimated slippage ₹{slippage / 100:,.2f} · {len(fills)} simulated fills")

    positions = [
        {
            "Symbol": row["symbol"], "Shares": row["shares"], "Last mark (INR)": row["mark"] / 100,
            "Mark time (IST)": row["mark_at"],
            "Unrealized P/L (INR)": (row["shares"] * row["mark"] - row["basis"]) / 100,
        }
        for row in sleeves if row["shares"]
    ]
    if positions:
        st.dataframe(pd.DataFrame(positions), hide_index=True, alt="DC forward paper positions and price marks")

    journal = pd.DataFrame([dict(row) for row in snapshot.signals])
    if journal.empty:
        st.caption("No forward DC confirmations recorded yet.")
        return
    for column in ("confirmation_close", "quote_price", "fill_price", "fees", "slippage", "realized_pnl"):
        journal[column] = pd.to_numeric(journal[column]) / 100
    journal["direction"] = journal["direction"].map({"up": "Bullish", "down": "Bearish"})
    journal = journal.rename(columns={
        "symbol": "Symbol", "session": "Signal session", "direction": "DC signal",
        "confirmation_close": "Signal close (INR)", "observed_at": "Observed at (IST)",
        "status": "Status", "filled_at": "Filled at (IST)", "quote_at": "Last trade at (IST)",
        "quote_price": "Quote (INR)", "fill_price": "Fill (INR)", "shares": "Shares",
        "fees": "Fees (INR)", "slippage": "Slippage (INR)", "realized_pnl": "Realized P/L (INR)",
    })
    st.subheader("DC forward paper journal")
    st.dataframe(journal, hide_index=True, alt="DC forward signals and simulated fills")
    st.download_button(
        "Download DC forward paper journal", journal.to_csv(index=False),
        file_name="dc_forward_paper_journal.csv", mime="text/csv", on_click="ignore",
    )
