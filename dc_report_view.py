"""Read-only display of the newest archived DC paper report."""

import logging
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

import dc_daily_report as daily
import paper_trading as paper


LOGGER = logging.getLogger(__name__)


def render_daily_report():
    st.divider()
    st.subheader("Daily quant performance · DC PAPER")
    if st.button("Generate today's DC report from saved data", key="dc_generate_daily_report"):
        try:
            now = paper.now_ist()
            daily.generate_reports(now.date(), now, catch_up=True)
            st.success("Dated DC paper report saved.")
        except (OSError, ValueError, sqlite3.Error) as exc:
            LOGGER.exception("Could not generate the DC paper report.")
            if isinstance(exc, daily.ReportInputError):
                st.error(str(exc))
            elif isinstance(exc, sqlite3.Error):
                code = getattr(exc, "sqlite_errorname", type(exc).__name__)
                st.error(f"Could not read the DC journal (data/dc_forward.sqlite3). SQLite reported {code}.")
            elif isinstance(exc, PermissionError):
                st.error("Check write permissions for data/dc_daily_reports and close files opened exclusively.")
            elif isinstance(exc, OSError):
                st.error("Could not write the DC report archive. Check data/dc_daily_reports and free disk space.")
            else:
                st.error("Saved DC data could not be used to build the report. See the Streamlit log for details.")

    try:
        archived, skipped = daily.load_latest_report()
    except OSError:
        LOGGER.exception("Could not list the DC report archive.")
        st.error("Could not open data/dc_daily_reports. Check its folder permissions.")
        return

    if archived is None:
        if skipped:
            st.error("No readable DC report revision was found. Generate a new report from saved data.")
        else:
            st.caption("Generate a report from the ledger or enable the optional Windows schedule.")
        return
    if skipped:
        st.warning("Showing an earlier DC report because a newer revision could not be read.")
    row = archived.summary
    st.caption(f"{row['date']} · {row['stage']} · Generated {row['generated_at_ist']}")
    st.download_button("Download latest DC report HTML", archived.html,
                       file_name=f"{row['date']}_dc_report.html", mime="text/html", on_click="ignore")
    st.download_button("Download latest DC summary CSV", archived.summary_csv,
                       file_name=f"{row['date']}_dc_summary.csv", mime="text/csv", on_click="ignore")
    performance, reliability = st.columns(2)
    with performance:
        st.markdown("**Trading performance**")
        fields = {
            "equity_inr": "Closing equity", "daily_net_pnl_inr": "Daily net P/L",
            "cumulative_net_pnl_inr": "Cumulative net P/L", "daily_return_pct": "Daily return",
            "cumulative_return_pct": "Cumulative return", "buy_hold_return_pct": "Buy-and-hold return",
            "drawdown_pct": "Closing drawdown", "max_drawdown_pct": "Worst closing drawdown",
            "daily_fees_inr": "Daily fees", "daily_slippage_inr": "Daily slippage",
            "closed_trades": "Closed round trips", "win_rate_pct": "Win rate",
            "average_win_inr": "Average win", "average_loss_inr": "Average loss",
            "expectancy_inr": "Net expectancy per closed trade",
        }
        _metric_table(row, fields, "Daily and cumulative DC paper performance")
    with reliability:
        st.markdown("**System reliability**")
        fields = {key: key.replace("_", " ").capitalize() for key in daily.RELIABILITY_FIELDS}
        _metric_table(row, fields, "DC market data and execution reliability")
        st.caption("Unavailable values and unrecorded attempts are shown separately from observed zero counts.")
        if "scanner export" in str(row.get("close_source")).lower():
            st.caption("Saved scanner closes supply valuations. Signals, fills and monitoring counts use the DC journal.")
    _trends(archived.history)
    for file, title in (
        ("readiness", "Forward-testing readiness progress"), ("signals", "New DC signals"),
        ("positions", "Open DC positions"), ("fills", "Simulated DC fills"),
        ("missed", "Missed eligible fills"), ("observations", "Recorded refresh attempts"),
    ):
        with st.expander(title, expanded=file == "readiness"):
            table = archived.details[file]
            if table.empty:
                st.caption("No records in this section.")
            else:
                st.dataframe(table, hide_index=True, alt=title)


def _metric_table(row, fields, description):
    rows = []
    for key, label in fields.items():
        value = row.get(key)
        if pd.isna(value):
            display = "Unavailable"
        elif key.endswith("_inr"):
            display = f"₹{value:,.2f}"
        elif key.endswith("_pct"):
            display = f"{value:.2f}%"
        else:
            display = str(value)
        rows.append({"Metric": label, "Value": display})
    st.dataframe(pd.DataFrame(rows), hide_index=True, alt=description)


def _trends(history):
    st.markdown("**Historical trends**")
    for columns, label in (
        ({"equity_inr": "DC paper", "buy_hold_equity_inr": "Buy-and-hold"}, "Closing equity (INR)"),
        ({"drawdown_pct": "DC paper"}, "Closing drawdown (%)"),
    ):
        if not history[list(columns)].notna().any().any():
            st.caption(f"{label} will appear once closing values are recorded.")
            continue
        source = history[["date", *columns]].rename(columns=columns).melt("date", var_name="Series", value_name="Value")
        source["date"] = source["date"].dt.strftime("%Y-%m-%dT00:00:00Z")
        chart = alt.Chart(source).mark_line(point=True, invalid="break-paths-filter-domains").encode(
            x=alt.X("date:T", title="Report date", scale=alt.Scale(type="utc")),
            y=alt.Y("Value:Q", title=label, scale=alt.Scale(zero=False)),
            color="Series:N",
        )
        st.altair_chart(chart, alt=f"Historical {label.lower()}")
