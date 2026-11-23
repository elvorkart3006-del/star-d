"""Offline tests for scanner calculations and dashboard behavior.

Run with:
    py -3 -m unittest test_scanner_offline.py

All market data in these tests is synthetic. No Kite client or credentials are used.
"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa

import multi_stock_scanner as scanner


IST = ZoneInfo("Asia/Kolkata")
PROJECT_FOLDER = Path(__file__).resolve().parent


def make_prices(closes):
    """Build completed-session rows for direct, synthetic SMA tests."""
    dates = pd.bdate_range(end="2026-11-20", periods=len(closes))
    return pd.DataFrame(
        {
            "date": dates,
            "close": closes,
        }
    )


def chart_output(app):
    """Read the actual Vega-Lite spec and Arrow datasets sent to the browser."""
    (element,) = app.get("vega_lite_chart")
    datasets = {
        dataset.name: pa.ipc.open_stream(dataset.data.data).read_all().to_pylist()
        for dataset in element.proto.datasets
    }
    return json.loads(element.proto.spec), datasets


class FakeKite:
    """Tiny in-memory stand-in for just the two read-only calls being tested."""

    def __init__(self, candles, quotes=None):
        self.candles = candles
        self.quotes = quotes or {}
        self.historical_calls = 0
        self.quote_calls = 0

    def quote(self, symbols):
        self.quote_calls += 1
        return self.quotes

    def historical_data(self, **kwargs):
        self.historical_calls += 1
        return self.candles


class ScannerOfflineTests(unittest.TestCase):
    def test_detects_bullish_and_bearish_crossovers(self):
        prices = make_prices([10, 9, 8, 9, 10, 11, 10, 9, 8])
        _, events = scanner._add_sma_columns(prices, 2, 3)
        signals = [event["signal"] for event in events]
        self.assertIn("Bullish", signals)
        self.assertIn("Bearish", signals)

    def test_provisional_signal_requires_fresh_current_session_quote(self):
        prices, _ = scanner._add_sma_columns(make_prices([10, 9, 8]), 2, 3)
        now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)

        signal, _, _ = scanner._provisional_signal(
            prices, 11, now - timedelta(seconds=60), now, 2, 3
        )
        self.assertEqual(signal, "Bullish")

        stale_signal = scanner._provisional_signal(
            prices, 11, now - timedelta(seconds=301), now, 2, 3
        )
        future_signal = scanner._provisional_signal(
            prices, 11, now + timedelta(seconds=1), now, 2, 3
        )
        self.assertEqual(stale_signal, (None, None, None))
        self.assertEqual(future_signal, (None, None, None))

    def test_200_period_uses_wide_window_and_upgrades_short_cache(self):
        self.assertGreaterEqual(scanner.history_calendar_days(200), 630)
        self.assertGreaterEqual(scanner.history_calendar_days(30), 90)

        candles = []
        for index, day in enumerate(pd.bdate_range(end="2026-11-20", periods=220)):
            close = 100 + index / 10
            candles.append(
                {
                    "date": day.to_pydatetime(),
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000,
                }
            )
        kite = FakeKite(candles)
        instrument = [{
            "symbol": "INFY",
            "company": "Infosys",
            "exchange": "NSE",
            "instrument_type": "EQ",
            "instrument_token": 1,
        }]
        cache = {}
        scan_now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
        with patch.object(scanner, "_call_with_rate_limit", side_effect=lambda call, endpoint: call()):
            scanner.scan_stocks(kite, instrument, ["INFY"], 6, 30, cache, scan_now)
            scanner.scan_stocks(kite, instrument, ["INFY"], 6, 200, cache, scan_now)
        self.assertEqual(kite.historical_calls, 2)

    def test_insufficient_completed_history_is_reported(self):
        candles = []
        for index, day in enumerate(pd.bdate_range(end="2026-11-20", periods=20)):
            candles.append(
                {
                    "date": day.to_pydatetime(),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100 + index,
                    "volume": 1000,
                }
            )
        kite = FakeKite(candles)
        instrument = [{
            "symbol": "INFY",
            "company": "Infosys",
            "exchange": "NSE",
            "instrument_type": "EQ",
            "instrument_token": 1,
        }]
        with patch.object(scanner, "_call_with_rate_limit", side_effect=lambda call, endpoint: call()):
            results, _, _ = scanner.scan_stocks(
                kite,
                instrument,
                ["INFY"],
                6,
                30,
                now=datetime(2026, 11, 23, 10, 0, tzinfo=IST),
            )
        self.assertIn("Insufficient", results[0]["Status"])

    def test_dashboard_stock_add_remove_and_select_state_offline(self):
        # AppTest runs Streamlit locally and only stock-selection buttons are
        # clicked; the buttons that load Kite data and run a scan are untouched.
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PROJECT_FOLDER / "trading_dashboard.py"))
        app.session_state["scan_results"] = None
        app.session_state["chart_data"] = {}
        app.session_state["nse_catalog"] = [
            {"symbol": "INFY", "company": "Infosys"},
            {"symbol": "TCS", "company": "Tata Consultancy Services"},
            {"symbol": "WIPRO", "company": "Wipro"},
        ]
        app.session_state["historical_candle_cache"] = {}
        app.session_state["selected_stock_symbols"] = ["INFY"]
        app.run()
        self.assertFalse(app.exception)

        app.selectbox(key="nse_stock_candidate").set_value("TCS").run()
        next(button for button in app.button if button.label == "Add stock").click().run()
        self.assertEqual(app.session_state["selected_stock_symbols"], ["INFY", "TCS"])

        app.multiselect(key="selected_stock_symbols").set_value(["TCS"]).run()
        self.assertEqual(app.session_state["selected_stock_symbols"], ["TCS"])

    def test_dashboard_labels_stay_with_latest_completed_scan(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PROJECT_FOLDER / "trading_dashboard.py"))
        result = {
            "Company": "Infosys",
            "Symbol": "INFY",
            "Latest Crossover": "Bullish",
            "Crossover Date": "2026-11-19",
            "Sessions Ago": 1,
            "Latest Close (INR)": 1500.0,
            "Status": "OK",
            "Confirmed Daily Signal": "Bullish",
            "Confirmed Signal Date": "2026-11-19",
            "Confirmed Sessions Ago": 1,
            "Provisional Intraday Signal": "N/A",
            "Provisional Signal Date": None,
            "Provisional Sessions Ago": None,
            "Current LTP (INR)": None,
            "Quote Time (IST)": None,
            "Latest Completed Session": "2026-11-20",
            "Most Recent Signal Date": "2026-11-19",
            "SMA Short Period": 6,
            "SMA Long Period": 30,
        }
        app.session_state["scan_results"] = pd.DataFrame([result])
        app.session_state["latest_scan_periods"] = (6, 30)
        app.session_state["latest_scan_symbols"] = ["INFY"]
        app.session_state["chart_data"] = {
            "INFY": {
                "history": pd.DataFrame(
                    {
                        "date": pd.to_datetime(["2026-11-18", "2026-11-19", "2026-11-20"]),
                        "close": [1490.0, 1500.0, 1505.0],
                        "SMA Short": [1488.0, 1492.0, 1498.0],
                        "SMA Long": [1490.0, 1491.0, 1493.0],
                    }
                ),
                "events": [],
                "provisional": None,
            }
        }
        app.session_state["nse_catalog"] = [
            {"symbol": "INFY", "company": "Infosys"},
            {"symbol": "TCS", "company": "Tata Consultancy Services"},
        ]
        # A changed selection is for the next scan and must not erase the
        # previously completed INFY chart or relabel its moving averages.
        app.session_state["selected_stock_symbols"] = ["TCS"]
        app.session_state["historical_candle_cache"] = {}
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(
            any(option.startswith("INFY ") for option in app.selectbox(key="chart_stock_symbol").options)
        )
        self.assertTrue(any("SMA 6/30" in item.value for item in app.info))

        app.number_input(key="short_sma_period").set_value(12).run()
        app.number_input(key="long_sma_period").set_value(50).run()
        self.assertTrue(any("SMA 6/30" in item.value for item in app.info))

    def test_manual_refresh_sends_close_smas_and_crossovers_to_chart(self):
        from streamlit.testing.v1 import AppTest

        closes = [100] * 30 + list(range(101, 109)) + list(range(95, 88, -1))
        dates = pd.bdate_range(end="2026-11-20", periods=len(closes), tz=IST)
        candles = [
            {
                "date": day.isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
            }
            for day, close in zip(dates, closes)
        ]
        kite = FakeKite(
            list(reversed(candles)),
            {"NSE:INFY": {"last_price": 200, "last_trade_time": "2026-11-23T04:29:00Z"}},
        )
        app = AppTest.from_file(str(PROJECT_FOLDER / "trading_dashboard.py"))
        app.context = {"timezone": "America/Los_Angeles"}
        app.session_state["scan_results"] = None
        app.session_state["nse_catalog"] = [
            {
                "symbol": "INFY",
                "company": "Infosys",
                "exchange": "NSE",
                "instrument_type": "EQ",
                "instrument_token": 1,
            }
        ]
        app.session_state["selected_stock_symbols"] = ["INFY"]
        app.run()
        scan_now = datetime(2026, 11, 23, 10, tzinfo=IST)
        scan_stocks = scanner.scan_stocks
        with (
            patch.object(scanner, "create_kite_client", return_value=kite),
            patch.object(scanner, "save_results_csv") as save_results,
            patch.object(
                scanner, "scan_stocks",
                side_effect=lambda *args, **kwargs: scan_stocks(*args, **kwargs, now=scan_now),
            ),
            patch.object(scanner, "_call_with_rate_limit", side_effect=lambda call, endpoint: call()),
        ):
            next(button for button in app.button if button.label == "Refresh Scanner").click().run()

        self.assertFalse(app.exception)
        save_results.assert_called_once()
        self.assertEqual((kite.historical_calls, kite.quote_calls), (1, 1))
        spec, datasets = chart_output(app)
        lines, markers = spec["layer"]
        self.assertEqual(spec["resolve"]["scale"]["color"], "independent")
        self.assertEqual(lines["encoding"]["x"]["scale"]["type"], "utc")
        self.assertEqual(markers["encoding"]["x"], lines["encoding"]["x"])
        self.assertTrue(any(param.get("bind") == "scales" for param in spec["params"]))

        line_rows = datasets[lines["data"]["name"]]
        completed = [row for row in line_rows if row["Series"] == "Completed close"]
        short = [row for row in line_rows if row["Series"] == "SMA 6"]
        long = [row for row in line_rows if row["Series"] == "SMA 30"]
        self.assertEqual([row["Price"] for row in completed], closes)
        self.assertEqual(completed[0]["date"], "2026-09-21T00:00:00Z")
        self.assertEqual(completed[-1]["date"], "2026-11-20T00:00:00Z")
        self.assertEqual(short[0]["date"], completed[5]["date"])
        self.assertEqual(long[0]["date"], completed[29]["date"])
        self.assertAlmostEqual(short[-1]["Price"], sum(closes[-6:]) / 6)
        self.assertAlmostEqual(long[-1]["Price"], sum(closes[-30:]) / 30)

        marker_rows = datasets[markers["data"]["name"]]
        self.assertEqual([row["signal"] for row in marker_rows], ["Bullish", "Bearish", "Provisional Bullish"])
        closes_by_date = {row["date"]: row["Price"] for row in completed}
        for marker in marker_rows[:2]:
            self.assertEqual(marker["price"], closes_by_date[marker["date"]])
        self.assertEqual(marker_rows[-1]["date"], "2026-11-23T09:59:00Z")
        self.assertEqual(marker_rows[-1]["Time"], "2026-11-23 09:59:00")
        self.assertEqual(marker_rows[-1]["price"], 200)

    def test_chart_handles_a_single_close_and_invalid_history(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file(str(PROJECT_FOLDER / "trading_dashboard.py"))
        app.session_state["scan_results"] = pd.DataFrame(
            [scanner._new_result_row("Infosys", "INFY", 6, 30)]
        )
        history = pd.DataFrame(
            {
                "date": ["2026-11-19T18:30:00Z", "invalid", "2026-11-19"],
                "close": ["1500.5", 1499, float("inf")],
                "SMA Short": [None, None, None],
                "SMA Long": [None, None, None],
            }
        )
        app.session_state["chart_data"] = {"INFY": {"history": history, "events": []}}
        app.run()
        self.assertFalse(app.exception)
        spec, datasets = chart_output(app)
        (lines,) = spec["layer"]
        self.assertTrue(lines["mark"]["point"])
        self.assertEqual(
            datasets[spec["data"]["name"]],
            [
                {
                    "date": "2026-11-20T00:00:00Z",
                    "Session": "2026-11-20",
                    "Series": "Completed close",
                    "Price": 1500.5,
                }
            ],
        )

        app.session_state["chart_data"] = {"INFY": {"history": history.iloc[1:], "events": []}}
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.get("vega_lite_chart"))
        self.assertTrue(any("No valid completed prices" in item.value for item in app.caption))

    def test_existing_legacy_csv_loads_without_missing_columns(self):
        from streamlit.testing.v1 import AppTest

        # The project's current export has the original seven-column schema.
        app = AppTest.from_file(str(PROJECT_FOLDER / "trading_dashboard.py"))
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("SMA 6/30" in item.value for item in app.info))


if __name__ == "__main__":
    unittest.main()
