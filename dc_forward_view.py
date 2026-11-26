"""Streamlit panel for the separate DC 5% forward paper account."""

import sqlite3

import pandas as pd
import streamlit as st

import dc_forward as forward
from dc_report_view import render_daily_report
import dc_research as research
import multi_stock_scanner as scanner
import paper_trading as paper
from market_data_retention import strip_raw_market_columns


@st.cache_data(ttl=600, show_spinner=False)
def load_seed():
    return research.load_histories()


def fetch_market(starts, now, request_lock):
    """Fail closed; DC will resume only with an approved feed adapter."""
    raise forward.MarketDataPaused(forward.MARKET_DATA_PAUSE_REASON)


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


def render_dc_forward(request_lock=None):
    st.divider()
    st.header("DC 5% - FORWARD PAPER TRADING")
    st.caption(
        "Fixed 5% reversal rule for INFY, TCS, WIPRO, HDFCBANK, ICICIBANK, SBIN and ITC. "
        "The existing INR 70,000 paper account and all saved records are preserved."
    )
    st.warning(
        "Kite-powered DC market-data refreshes and paper fills are paused. "
        "This panel displays saved data only."
    )
    st.caption(
        "Forward testing can resume with a separately licensed feed that permits paper use, "
        "or after written provider confirmation of permitted use. The DC 5% rules remain unchanged."
    )
    ledger = forward.ForwardLedger()
    try:
        seed = load_seed()
        snapshot = ledger.snapshot()
        histories = forward.histories_with_closes(seed, snapshot.closes if snapshot else ())
        st.dataframe(
            forward.latest_signals(histories), hide_index=True,
            alt="DC 5% signals from saved completed daily candles",
        )
        if snapshot is None:
            st.caption("The forward paper journal has not started.")
        else:
            render_ledger(snapshot)
    except (OSError, ValueError, sqlite3.Error):
        st.error("The saved DC history or forward paper journal could not be read.")
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
    journal_export = strip_raw_market_columns(journal).reindex(
        columns=["Symbol", "Signal session", "DC signal", "Observed at (IST)", "Status"]
    )
    st.download_button(
        "Download DC forward paper journal", journal_export.to_csv(index=False),
        file_name="dc_forward_paper_journal.csv", mime="text/csv", on_click="ignore",
    )
