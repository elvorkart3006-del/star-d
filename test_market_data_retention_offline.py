"""Offline checks for the Kite data-retention and paper-trading guards."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import backtest_sma_6_30_kite as kite_batch
import dc_daily_report as daily_report
import dc_research
import multi_stock_scanner as scanner
import paper_trading_view
from market_data_retention import strip_raw_market_columns


class RetentionOfflineTests(unittest.TestCase):
    def test_market_price_and_quote_fields_are_removed(self):
        source = pd.DataFrame(
            {
                "Symbol": ["INFY"],
                "Confirmed Daily Signal": ["Bullish"],
                "Latest Close (INR)": [1500.0],
                "Current LTP (INR)": [1501.0],
                "Quote Time (IST)": ["2026-11-23 10:00:00"],
            }
        )

        result = strip_raw_market_columns(source)

        self.assertEqual(list(result.columns), ["Symbol", "Confirmed Daily Signal"])
        self.assertEqual(source["Latest Close (INR)"].iloc[0], 1500.0)

    def test_scanner_csv_saves_derived_fields_only(self):
        rows = [
            {
                "Symbol": "INFY",
                "Confirmed Daily Signal": "Bullish",
                "Latest Close (INR)": 1500.0,
                "Current LTP (INR)": 1501.0,
                "Quote Time (IST)": "2026-11-23 10:00:00",
            }
        ]
        with TemporaryDirectory() as temp:
            destination = Path(temp) / "derived.csv"
            saved = scanner.save_results_csv(rows, destination)
            loaded = pd.read_csv(saved)

        self.assertIn("Confirmed Daily Signal", loaded.columns)
        self.assertNotIn("Latest Close (INR)", loaded.columns)
        self.assertNotIn("Current LTP (INR)", loaded.columns)
        self.assertNotIn("Quote Time (IST)", loaded.columns)

    def test_historical_backtest_download_is_not_cached_to_disk(self):
        class FakeKite:
            def historical_data(self, **kwargs):
                return candles

        days = pd.bdate_range("2026-01-05", periods=kite_batch.LONG_PERIOD + 5)
        candles = [
            {
                "date": day.to_pydatetime(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000,
            }
            for day in days
        ]
        kite = FakeKite()
        catalog = [
            {
                "symbol": "INFY",
                "instrument_token": 1,
            }
        ]
        with TemporaryDirectory() as temp:
            destination = Path(temp) / "new-window"
            with (
                patch.object(kite_batch, "SYMBOLS", ("INFY",)),
                patch.object(scanner, "get_nse_equity_universe", return_value=catalog),
                patch.object(scanner, "_call_with_rate_limit", side_effect=lambda call, endpoint: call()),
            ):
                symbol, prices = next(
                    kite_batch.iter_histories(
                        days[0].date(), days[-1].date(), destination,
                        create_client=lambda: kite,
                    )
                )

            self.assertEqual(symbol, "INFY")
            self.assertEqual(len(prices), len(candles))
            self.assertFalse(destination.exists())

    def test_backtest_csv_writer_filters_raw_market_fields(self):
        frame = pd.DataFrame(
            {
                "stock": ["INFY"],
                "sma_return_pct": [2.0],
                "close": [1500.0],
                "volume": [1000],
            }
        )
        with TemporaryDirectory() as temp:
            path = Path(temp) / "summary.csv"
            kite_batch.save_csv_once(frame, path)
            saved = pd.read_csv(path)

        self.assertEqual(list(saved.columns), ["stock", "sma_return_pct"])

    def test_dc_fill_export_keeps_events_without_prices_or_trade_values(self):
        fills = pd.DataFrame(
            [
                {
                    "period": "development",
                    "stock": "INFY",
                    "side": "BUY",
                    "extreme_date": "2026-01-02",
                    "extreme_close_inr": 100.0,
                    "confirmation_date": "2026-01-05",
                    "confirmation_close_inr": 105.0,
                    "fill_date": "2026-01-06",
                    "shares": 10,
                    "fill_price_inr": 106.0,
                    "realized_pnl_inr": None,
                }
            ]
        )

        result = dc_research.derived_fill_events(fills)

        self.assertEqual(
            list(result.columns),
            ["period", "stock", "side", "extreme_date", "confirmation_date", "fill_date"],
        )
        self.assertEqual(result.iloc[0]["stock"], "INFY")

    def test_dc_report_detail_exports_drop_price_and_reconstructive_values(self):
        legacy = pd.DataFrame(
            [
                {
                    "symbol": "INFY",
                    "session": "2026-01-05",
                    "direction": "up",
                    "confirmation_close_inr": 105.0,
                    "shares": 10,
                    "fill_price_inr": 106.0,
                    "realized_pnl_inr": 20.0,
                    "status": "Filled",
                }
            ]
        )

        result = daily_report.safe_detail_frame("fills", legacy)

        self.assertEqual(list(result.columns), ["symbol", "session", "direction"])

    def test_kite_paper_quote_fetch_fails_closed(self):
        with patch.object(scanner, "create_kite_client", side_effect=AssertionError("Kite called")) as client:
            with self.assertRaises(paper_trading_view.PaperTradingDisabledError):
                paper_trading_view.fetch_paper_quotes(["INFY"])
        client.assert_not_called()

    def test_paper_screen_never_calls_the_quote_callback(self):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(
            """
from paper_trading_view import render_paper_trading
def forbidden_quotes(_symbols):
    raise AssertionError('quote callback was called')
render_paper_trading({}, forbidden_quotes)
"""
        ).run()

        self.assertFalse(app.exception)
        self.assertTrue(any("disabled" in item.value.lower() for item in app.caption))


if __name__ == "__main__":
    unittest.main()
