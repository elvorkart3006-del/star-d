"""Interactive, read-only Zerodha NSE stock scanner dashboard.

Run from the Trading project folder with:
    streamlit run trading_dashboard.py

Kite market data is requested by scanner and paper trading actions.
"""

import io
import threading
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
from kiteconnect.exceptions import TokenException

import multi_stock_scanner as scanner
from dc_forward_view import render_dc_forward
from paper_trading_view import render_paper_trading


PROJECT_FOLDER = Path(__file__).resolve().parent
RESULTS_FILE = PROJECT_FOLDER / "data" / "stock_scanner_results.csv"
NIFTY100_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"
NIFTY100_REFERER = "https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-100"
IST = ZoneInfo("Asia/Kolkata")
API_ACTION_LOCK = threading.Lock()

DISPLAY_COLUMNS = [
    "Company",
    "Symbol",
    "Confirmed Daily Signal",
    "Confirmed Signal Date",
    "Confirmed Sessions Ago",
    "Latest Close (INR)",
    "Provisional Intraday Signal",
    "Provisional Signal Date",
    "Provisional Sessions Ago",
    "Current LTP (INR)",
    "Quote Time (IST)",
    "SMA Short Period",
    "SMA Long Period",
    "Status",
]


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def download_nifty100_symbols():
    """Download and validate the official NSE Indices NIFTY 100 constituent CSV."""
    request = Request(
        NIFTY100_CSV_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TradingScanner/1.0",
            "Referer": NIFTY100_REFERER,
            "Accept": "text/csv,application/octet-stream,*/*",
        },
    )
    with urlopen(request, timeout=20) as response:
        content = response.read()

    constituents = pd.read_csv(io.BytesIO(content))
    symbol_column = next(
        (column for column in constituents.columns if str(column).strip().lower() == "symbol"),
        None,
    )
    if symbol_column is None:
        raise ValueError("The official NIFTY 100 file did not contain a Symbol column.")

    symbols = (
        constituents[symbol_column]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
        .unique()
        .tolist()
    )
    if len(symbols) != 100:
        raise ValueError(
            f"The official constituent file contained {len(symbols)} unique symbols, not 100."
        )
    return symbols


def load_results_csv():
    """Load current or older scanner exports and add newer display columns."""
    results = pd.read_csv(RESULTS_FILE)
    if "Symbol" not in results.columns:
        raise ValueError("The scanner CSV does not contain a Symbol column.")

    defaults = {
        "Company": results["Symbol"],
        "Latest Crossover": "N/A",
        "Crossover Date": None,
        "Sessions Ago": None,
        "Latest Close (INR)": None,
        "Status": "",
        "Confirmed Daily Signal": results.get("Latest Crossover", "N/A"),
        "Confirmed Signal Date": results.get("Crossover Date"),
        "Confirmed Sessions Ago": results.get("Sessions Ago"),
        "Provisional Intraday Signal": "N/A",
        "Provisional Signal Date": None,
        "Provisional Sessions Ago": None,
        "Current LTP (INR)": None,
        "Quote Time (IST)": None,
        "Latest Completed Session": None,
        "Most Recent Signal Date": results.get("Crossover Date"),
        "SMA Short Period": scanner.DEFAULT_SMA_SHORT,
        "SMA Long Period": scanner.DEFAULT_SMA_LONG,
    }
    for column, default in defaults.items():
        if column not in results.columns:
            results[column] = default

    for column in [
        "Crossover Date",
        "Confirmed Signal Date",
        "Provisional Signal Date",
        "Latest Completed Session",
        "Most Recent Signal Date",
    ]:
        results[column] = pd.to_datetime(results[column], errors="coerce")
    for column in [
        "Sessions Ago",
        "Confirmed Sessions Ago",
        "Provisional Sessions Ago",
        "Latest Close (INR)",
        "Current LTP (INR)",
    ]:
        results[column] = pd.to_numeric(results[column], errors="coerce")
    return results


def get_results_modified_text():
    """Return the results CSV modification time in India time."""
    modified = datetime.fromtimestamp(RESULTS_FILE.stat().st_mtime, IST)
    return modified.strftime("%d %b %Y, %I:%M:%S %p IST")


def format_instrument(symbol, names):
    """Show an NSE symbol alongside its company name in selection widgets."""
    return f"{symbol}  |  {names.get(symbol, symbol)}"


def fetch_instrument_catalog():
    """Load NSE equity symbols using the user's existing Kite authentication."""
    if not API_ACTION_LOCK.acquire(blocking=False):
        raise RuntimeError("Another Kite data request is already running. Please wait.")
    try:
        kite = scanner.create_kite_client()
        return scanner.get_nse_equity_universe(kite)
    finally:
        API_ACTION_LOCK.release()


def fetch_paper_quotes(symbols):
    """Use the scanner's shared lock and quote pacing for paper market data."""
    if not API_ACTION_LOCK.acquire(blocking=False):
        raise RuntimeError("Another Kite data request is already running.")
    try:
        kite = scanner.create_kite_client()
        instruments = [f"NSE:{symbol}" for symbol in symbols]
        return scanner._call_with_rate_limit(lambda: kite.quote(instruments), "quote")
    finally:
        API_ACTION_LOCK.release()


def run_manual_scan(symbols, short_period, long_period):
    """Run a user-requested scan, reusing this session's catalog and candles."""
    if not API_ACTION_LOCK.acquire(blocking=False):
        raise RuntimeError("A scanner refresh is already running. Please wait for it to finish.")
    try:
        kite = scanner.create_kite_client()
        catalog = st.session_state.get("nse_catalog")
        if not catalog:
            catalog = scanner.get_nse_equity_universe(kite)

        history_cache = st.session_state.setdefault("historical_candle_cache", {})
        results, charts, history_cache = scanner.scan_stocks(
            kite,
            catalog,
            symbols,
            short_period=short_period,
            long_period=long_period,
            history_cache=history_cache,
        )
        scanner.save_results_csv(results, RESULTS_FILE)
        return catalog, history_cache, results, charts
    finally:
        API_ACTION_LOCK.release()


def add_signal_names(results):
    """Ensure signal labels are strings for safe filtering and display."""
    results = results.copy()
    results["Confirmed Daily Signal"] = (
        results["Confirmed Daily Signal"].fillna("N/A").astype(str)
    )
    results["Provisional Intraday Signal"] = (
        results["Provisional Intraday Signal"].fillna("N/A").astype(str)
    )
    results["Status"] = results["Status"].fillna("").astype(str)
    return results


def scan_periods_from_results(results):
    """Return the SMA periods recorded by a scan or legacy CSV defaults."""
    short_period = scanner.DEFAULT_SMA_SHORT
    long_period = scanner.DEFAULT_SMA_LONG
    if "SMA Short Period" in results.columns:
        values = pd.to_numeric(results["SMA Short Period"], errors="coerce").dropna()
        if not values.empty:
            short_period = int(values.iloc[0])
    if "SMA Long Period" in results.columns:
        values = pd.to_numeric(results["SMA Long Period"], errors="coerce").dropna()
        if not values.empty:
            long_period = int(values.iloc[0])
    return short_period, long_period


def add_symbol_to_selection(selected, candidate, available):
    """Add one valid catalog symbol once, preserving selection order."""
    available = set(available)
    selection = [symbol for symbol in selected if symbol in available]
    if candidate in available and candidate not in selection:
        selection.append(candidate)
    return selection


def keep_valid_selection(selected, available):
    """Drop symbols no longer present in the current catalog."""
    available = set(available)
    return list(dict.fromkeys(symbol for symbol in selected if symbol in available))


def chart_dates(dates):
    """Read chart timestamps as India time, including already cached dates."""
    india_times = dates.map(scanner._india_datetime, na_action="ignore")
    return pd.to_datetime(india_times, errors="coerce", utc=True).dt.tz_convert(IST)


def build_sma_chart(chart_entry, company, short_period, long_period):
    """Plot completed closing prices, their SMAs, and the scanner's signal markers."""
    series_labels = {
        "close": "Completed close",
        "SMA Short": f"SMA {short_period}",
        "SMA Long": f"SMA {long_period}",
    }
    history = chart_entry["history"].rename(columns=series_labels)
    dates = chart_dates(history["date"]).dt.normalize()
    # Encode the IST wall clock on a UTC axis so browser time zones cannot shift it.
    axis_datetime_format = "%Y-%m-%dT%H:%M:%SZ"
    history["date"] = dates.dt.strftime(axis_datetime_format)
    history["Session"] = dates.dt.strftime("%Y-%m-%d")
    line_data = history.melt(
        id_vars=["date", "Session"],
        value_vars=list(series_labels.values()),
        var_name="Series",
        value_name="Price",
    )
    line_data["Price"] = pd.to_numeric(line_data["Price"], errors="coerce")
    line_data = line_data.loc[
        line_data["date"].notna() & np.isfinite(line_data["Price"])
    ].sort_values("date")
    if line_data.empty:
        return None

    trading_date = alt.X(
        "date:T", title="Trading date (IST)", scale=alt.Scale(type="utc", padding=10)
    )
    price_scale = alt.Scale(zero=False, padding=8)
    has_single_point_series = bool(line_data.groupby("Series").size().eq(1).any())
    lines = (
        alt.Chart(line_data)
        .mark_line(clip=True, point=has_single_point_series)
        .encode(
            x=trading_date,
            y=alt.Y("Price:Q", title="Price (INR)", scale=price_scale),
            color=alt.Color("Series:N", sort=list(series_labels.values())),
            tooltip=[
                alt.Tooltip("Session:N", title="Session (IST)"),
                alt.Tooltip("Series:N"),
                alt.Tooltip("Price:Q", format=",.2f"),
            ],
        )
    )

    marker_rows = list(chart_entry["events"])
    if chart_entry.get("provisional"):
        marker_rows.append(chart_entry["provisional"])
    layers = [lines]
    if marker_rows:
        markers = pd.DataFrame(marker_rows)
        dates = chart_dates(markers["date"])
        confirmed = markers["signal"].isin(["Bullish", "Bearish"])
        markers["date"] = dates.where(~confirmed, dates.dt.normalize()).dt.strftime(
            axis_datetime_format
        )
        markers["Time"] = dates.dt.strftime("%Y-%m-%d %H:%M:%S").where(
            ~confirmed, dates.dt.strftime("%Y-%m-%d")
        )
        markers["price"] = pd.to_numeric(markers["price"], errors="coerce")
        markers = markers.loc[markers["date"].notna() & np.isfinite(markers["price"])]
        if not markers.empty:
            signals = [
                "Bullish",
                "Bearish",
                "Provisional Bullish",
                "Provisional Bearish",
                "Live LTP (provisional)",
            ]
            layers.append(
                alt.Chart(markers)
                .mark_point(filled=True, size=115, stroke="white", strokeWidth=0.7, clip=True)
                .encode(
                    x=trading_date,
                    y=alt.Y("price:Q", title="Price (INR)", scale=price_scale),
                    color=alt.Color(
                        "signal:N",
                        title="Signal marker",
                        scale=alt.Scale(
                            domain=signals,
                            range=["#22c55e", "#ef4444", "#86efac", "#fca5a5", "#facc15"],
                        ),
                    ),
                    shape=alt.Shape(
                        "signal:N",
                        legend=None,
                        scale=alt.Scale(
                            domain=signals,
                            range=[
                                "triangle-up",
                                "triangle-down",
                                "triangle-up",
                                "triangle-down",
                                "diamond",
                            ],
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("Time:N", title="Date / quote time (IST)"),
                        alt.Tooltip("signal:N", title="Marker"),
                        alt.Tooltip("price:Q", title="Price", format=",.2f"),
                    ],
                )
            )

    return (
        alt.layer(*layers)
        .resolve_scale(color="independent")
        .interactive()
        .properties(height=420, title=f"{company} - SMA {short_period}/{long_period}")
    )


st.set_page_config(
    page_title="Trading Scanner Dashboard",
    page_icon=":material/query_stats:",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp { background: #0b0f14; color: #e5e7eb; }
    [data-testid="stHeader"] { background: rgba(11, 15, 20, 0.95); }
    [data-testid="stSidebar"] { background: #111820; }
    [data-testid="stMetric"] {
        background: #131b24;
        border: 1px solid #263442;
        border-radius: 12px;
        padding: 14px 16px;
    }
    [data-testid="stMetricLabel"] { color: #aab7c4; }
    [data-testid="stMetricValue"] { color: #f3f6f9; }
    div[data-testid="stDataFrame"] {
        border: 1px solid #263442;
        border-radius: 10px;
        overflow: hidden;
    }
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("NSE stock scanner")
st.caption("SMA crossover analysis with confirmed daily signals and separate provisional market signals.")

if "scan_results" not in st.session_state:
    st.session_state["scan_results"] = None
    st.session_state["chart_data"] = {}
    st.session_state["nse_catalog"] = []
    st.session_state["historical_candle_cache"] = {}
    st.session_state["load_error"] = False
    if RESULTS_FILE.is_file():
        try:
            st.session_state["scan_results"] = load_results_csv()
        except Exception:
            st.session_state["load_error"] = True

if st.session_state.get("scan_results") is not None:
    stored_results = st.session_state["scan_results"]
    if not isinstance(stored_results, pd.DataFrame):
        stored_results = pd.DataFrame(stored_results)
        st.session_state["scan_results"] = stored_results
    if "latest_scan_periods" not in st.session_state:
        st.session_state["latest_scan_periods"] = scan_periods_from_results(stored_results)
    if "latest_scan_symbols" not in st.session_state:
        st.session_state["latest_scan_symbols"] = (
            stored_results["Symbol"].astype(str).tolist()
            if "Symbol" in stored_results.columns
            else []
        )
    st.session_state.setdefault("chart_data", {})
else:
    st.session_state.setdefault("latest_scan_periods", None)
    st.session_state.setdefault("chart_data", {})


with st.sidebar:
    st.header("Stock universe")

    if st.button(
        "Load NSE instrument list",
        icon=":material/download:",
        width="stretch",
    ):
        try:
            with st.spinner("Loading tradable NSE equities from Kite..."):
                st.session_state["nse_catalog"] = fetch_instrument_catalog()
            available_symbols = {item["symbol"] for item in st.session_state["nse_catalog"]}
            if "selected_stock_symbols" not in st.session_state:
                st.session_state["selected_stock_symbols"] = [
                    symbol for symbol in scanner.DEFAULT_STOCKS if symbol in available_symbols
                ]
            else:
                st.session_state["selected_stock_symbols"] = keep_valid_selection(
                    st.session_state["selected_stock_symbols"], available_symbols
                )
            st.success(f"Loaded {len(st.session_state['nse_catalog'])} NSE equity instruments.")
        except scanner.MissingCredentialsError:
            st.error("KITE_API_KEY or KITE_ACCESS_TOKEN is missing from .env.")
        except TokenException:
            st.error("Your Kite access token is expired or invalid. Log in again, then retry.")
        except Exception:
            st.error("Could not load the NSE instrument list. Check your connection and try again.")

    catalog = st.session_state.get("nse_catalog", [])
    if catalog:
        symbols_by_name = {item["symbol"]: item["company"] for item in catalog}
        all_symbols = list(symbols_by_name)

        search_text = st.text_input(
            "Search NSE stocks",
            type="search",
            placeholder="Company name or symbol",
            key="nse_stock_search",
        )
        search_lower = search_text.strip().lower()
        matching_symbols = [
            symbol
            for symbol, company in symbols_by_name.items()
            if not search_lower
            or search_lower in symbol.lower()
            or search_lower in company.lower()
        ]

        if matching_symbols:
            candidate = st.selectbox(
                "Stock to add",
                options=matching_symbols,
                format_func=lambda symbol: format_instrument(symbol, symbols_by_name),
                key="nse_stock_candidate",
            )
            if st.button("Add stock", icon=":material/add:", width="stretch"):
                st.session_state["selected_stock_symbols"] = add_symbol_to_selection(
                    st.session_state.get("selected_stock_symbols", []),
                    candidate,
                    all_symbols,
                )
        else:
            st.caption("No instruments match that search.")

        if st.button("Use NIFTY 100 preset", icon=":material/format_list_bulleted:", width="stretch"):
            try:
                official_symbols = download_nifty100_symbols()
                available = set(all_symbols)
                matched = [symbol for symbol in official_symbols if symbol in available]
                missing = [symbol for symbol in official_symbols if symbol not in available]
                if len(matched) != 100:
                    st.warning(
                        f"The official file lists 100 constituents; {len(missing)} are not "
                        "currently present as NSE equities in the Kite instrument list."
                    )
                st.session_state["selected_stock_symbols"] = matched
                st.success(f"Selected {len(matched)} official NIFTY 100 constituents.")
            except Exception:
                st.error("Could not load a valid NIFTY 100 constituent file from NSE Indices.")

        st.multiselect(
            "Stocks to scan (remove with x)",
            options=all_symbols,
            format_func=lambda symbol: format_instrument(symbol, symbols_by_name),
            key="selected_stock_symbols",
            help="Search and add stocks above. Remove a selected stock with the x beside its name.",
        )
        st.caption("NIFTY 100 uses the official NSE Indices constituent CSV, not the full NSE instrument list.")
    else:
        st.info("Load the NSE instrument list to search and choose stocks.")

    st.divider()
    st.header("Strategy settings")
    short_period = st.number_input(
        "Short SMA period",
        min_value=1,
        max_value=199,
        value=scanner.DEFAULT_SMA_SHORT,
        step=1,
        key="short_sma_period",
        help="Default: 6 completed daily closes.",
    )
    long_period = st.number_input(
        "Long SMA period",
        min_value=2,
        max_value=200,
        value=scanner.DEFAULT_SMA_LONG,
        step=1,
        key="long_sma_period",
        help="Default: 30 completed daily closes.",
    )
    st.caption("A bullish crossover occurs when the short SMA crosses above the long SMA; bearish is the reverse.")


selected_symbols = st.session_state.get("selected_stock_symbols", [])
control_left, control_right = st.columns([1, 3], vertical_alignment="center")
with control_left:
    refresh_clicked = st.button(
        "Refresh Scanner",
        type="primary",
        icon=":material/refresh:",
        width="stretch",
        disabled=not selected_symbols,
    )
with control_right:
    st.caption(
        "Refresh is manual. It fetches daily history and one separate quote snapshot; "
        "it never places orders or GTTs."
    )

if refresh_clicked:
    if short_period >= long_period:
        st.error("The short SMA period must be smaller than the long SMA period.")
    else:
        try:
            with st.spinner(
                "Fetching selected stocks. Daily history is paced to respect Kite's API limits..."
            ):
                catalog, candle_cache, result_rows, chart_data = run_manual_scan(
                    selected_symbols,
                    int(short_period),
                    int(long_period),
                )
            st.session_state["nse_catalog"] = catalog
            st.session_state["historical_candle_cache"] = candle_cache
            st.session_state["scan_results"] = pd.DataFrame(result_rows)
            st.session_state["chart_data"] = chart_data
            st.session_state["latest_scan_periods"] = (int(short_period), int(long_period))
            st.session_state["latest_scan_symbols"] = list(selected_symbols)
            st.session_state["load_error"] = False
            st.success("Scanner refreshed and CSV export updated.")
        except scanner.MissingCredentialsError:
            st.error("KITE_API_KEY or KITE_ACCESS_TOKEN is missing from .env.")
        except TokenException:
            st.error("Your Kite access token is expired or invalid. Log in again, then retry.")
        except ValueError as error:
            st.error(str(error))
        except Exception:
            st.error("The scanner could not complete. Existing results have been kept.")


results = st.session_state.get("scan_results")
if results is None:
    if st.session_state.get("load_error"):
        st.error("The existing scanner CSV could not be read. Refresh the scanner to create a new export.")
    else:
        st.info("No scanner results yet. Load the stock list, choose stocks, then click Refresh Scanner.")
else:
    results = add_signal_names(results)
    scan_periods = st.session_state.get("latest_scan_periods")
    if not scan_periods:
        scan_periods = scan_periods_from_results(results)
    scan_short_period, scan_long_period = scan_periods
    st.info(
        f"Latest completed scan: SMA {scan_short_period}/{scan_long_period}, "
        f"{len(st.session_state.get('latest_scan_symbols', []))} stocks. "
        "Sidebar stock and SMA changes apply after the next manual refresh."
    )
    if RESULTS_FILE.is_file():
        st.caption(f"CSV last modified: **{get_results_modified_text()}**")
    st.download_button(
        "Download scanner results CSV",
        data=results.to_csv(index=False).encode("utf-8"),
        file_name="stock_scanner_results.csv",
        mime="text/csv",
        icon=":material/download:",
    )

    confirmed = results["Confirmed Daily Signal"]
    provisional = results["Provisional Intraday Signal"]
    error_status = results["Status"].str.contains(
        r"error|missing|no completed|insufficient|unavailable",
        case=False,
        regex=True,
    )

    with st.container(horizontal=True):
        st.metric("Stocks scanned", len(results), border=True)
        st.metric("Confirmed bullish", int((confirmed == "Bullish").sum()), border=True)
        st.metric("Confirmed bearish", int((confirmed == "Bearish").sum()), border=True)
        provisional_count = int(provisional.isin(["Bullish", "Bearish"]).sum())
        st.metric("Provisional signals", provisional_count, border=True)
        st.metric("Errors / unavailable", int(error_status.sum()), border=True)

    st.info(
        "Confirmed daily signals use completed candles only. Provisional intraday signals "
        "use a fresh current-session quote and may change before market close."
    )

    with st.container(border=True):
        st.subheader("Signal filters")
        filter_left, filter_right = st.columns(2)
        with filter_left:
            confirmed_filter = st.multiselect(
                "Confirmed daily directions",
                options=["Bullish", "Bearish", "None", "N/A"],
                default=["Bullish", "Bearish", "None", "N/A"],
                key="confirmed_signal_filter",
            )
        with filter_right:
            provisional_filter = st.multiselect(
                "Provisional intraday directions",
                options=["Bullish", "Bearish", "No crossover", "N/A"],
                default=["Bullish", "Bearish", "No crossover", "N/A"],
                key="provisional_signal_filter",
            )

    confirmed_rows = results[
        results["Confirmed Daily Signal"].isin(confirmed_filter)
        & results["Confirmed Daily Signal"].isin(["Bullish", "Bearish"])
    ].copy()
    confirmed_rows = confirmed_rows.sort_values(
        "Confirmed Signal Date", ascending=False, na_position="last"
    )
    st.subheader("Confirmed daily signals")
    if confirmed_rows.empty:
        st.caption("No confirmed bullish or bearish crossovers match the current filters.")
    else:
        st.dataframe(
            confirmed_rows[DISPLAY_COLUMNS],
            hide_index=True,
            width="stretch",
            alt="Confirmed daily crossover signals and the latest completed closing prices",
        )

    provisional_rows = results[
        results["Provisional Intraday Signal"].isin(provisional_filter)
        & results["Provisional Intraday Signal"].isin(["Bullish", "Bearish"])
    ].copy()
    provisional_rows = provisional_rows.sort_values(
        "Provisional Signal Date", ascending=False, na_position="last"
    )
    st.subheader("Provisional intraday signals")
    if provisional_rows.empty:
        st.caption("No fresh current-session provisional crossovers match the current filters.")
    else:
        st.dataframe(
            provisional_rows[DISPLAY_COLUMNS],
            hide_index=True,
            width="stretch",
            alt="Provisional intraday crossover signals based on fresh market quotes",
        )

    st.subheader("Latest prices and scan status")
    table_sort_date = pd.to_datetime(results["Most Recent Signal Date"], errors="coerce")
    all_rows = results.assign(_sort_date=table_sort_date).sort_values(
        "_sort_date", ascending=False, na_position="last"
    )
    st.dataframe(
        all_rows[DISPLAY_COLUMNS],
        hide_index=True,
        width="stretch",
        alt="Selected NSE stocks, confirmed and provisional signals, latest prices, and status",
    )

    st.subheader("Interactive SMA chart")
    available_chart_symbols = list(st.session_state.get("chart_data", {}).keys())
    if not available_chart_symbols:
        st.caption("Refresh the scanner to load candle history for interactive charts.")
    else:
        chart_names = {item["symbol"]: item["company"] for item in st.session_state.get("nse_catalog", [])}
        chart_symbol = st.selectbox(
            "Choose a stock",
            options=available_chart_symbols,
            format_func=lambda symbol: format_instrument(symbol, chart_names),
            key="chart_stock_symbol",
        )
        chart = build_sma_chart(
            st.session_state["chart_data"][chart_symbol],
            chart_names.get(chart_symbol, chart_symbol),
            scan_short_period,
            scan_long_period,
        )
        if chart is None:
            st.caption("No valid completed prices are available for this chart.")
        else:
            st.altair_chart(
                chart,
                width="stretch",
                alt=f"{chart_symbol} completed daily closes with SMA {scan_short_period} and SMA {scan_long_period}, plus crossover markers",
            )
            st.caption(
                "Daily markers show confirmed crossovers. An intraday marker shows the live "
                "price or a provisional crossover when a fresh quote is available."
            )

st.caption("Read-only analysis: no orders, GTTs, or automatic trading are used.")

paper_names = dict.fromkeys(scanner.DEFAULT_STOCKS)
if results is not None:
    paper_names.update(zip(results["Symbol"], results["Company"]))
paper_names.update(
    (item["symbol"], item["company"]) for item in st.session_state.get("nse_catalog", [])
)
render_paper_trading({symbol: name or symbol for symbol, name in paper_names.items()}, fetch_paper_quotes)
render_dc_forward(API_ACTION_LOCK)
