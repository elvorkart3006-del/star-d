"""Daily ledger accounting, gaps, archive, scheduler refresh and dashboard tests."""

import shutil
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from xml.etree import ElementTree

import pandas as pd

import dc_daily_report as daily
import dc_forward as forward
import dc_report_metrics as metrics
import dc_report_schedule as schedule
import multi_stock_scanner as scanner
import paper_trading as paper
from test_dc_forward_offline import MONDAY, TUESDAY, WEDNESDAY, bars, clock, quote, seed_prices


THURSDAY = WEDNESDAY + timedelta(days=1)
FRIDAY = THURSDAY + timedelta(days=1)
SATURDAY = FRIDAY + timedelta(days=1)
PROJECT = Path(__file__).resolve().parent


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory()))
        self.ledger = forward.ForwardLedger(self.folder / "forward.sqlite3")
        self.output = self.folder / "reports"
        self.seed = seed_prices()

    def closes(self, day, infy):
        candles = {symbol: bars([day], [95]) for symbol in forward.SYMBOLS}
        candles["INFY"] = bars([day], [infy])
        self.ledger.refresh(self.seed, candles, {}, clock(day, 17), source="Dashboard")

    def test_daily_pnl_cost_basis_trade_metrics_and_benchmark(self):
        self.closes(MONDAY, 100)
        self.ledger.refresh(
            self.seed, {}, {"INFY": quote(100, clock(TUESDAY, 10))}, clock(TUESDAY, 10),
            source="Dashboard", quotes_requested=True,
        )
        self.closes(TUESDAY, 106)  # Actual candles take precedence over the holiday calendar.
        self.closes(WEDNESDAY, 99)
        self.ledger.refresh(
            self.seed, {}, {"INFY": quote(98, clock(THURSDAY, 10), previous=99)}, clock(THURSDAY, 10),
            source="Dashboard", quotes_requested=True,
        )
        self.closes(THURSDAY, 98)
        snapshot = self.ledger.snapshot()
        sell, buy = snapshot.fills
        reports = metrics.build_reports(snapshot, THURSDAY, clock(THURSDAY, 18))

        first = reports[MONDAY].summary
        self.assertEqual(first["equity_inr"], 70000)
        self.assertEqual(first["daily_net_pnl_inr"], 0)
        self.assertIsNone(first["buy_hold_return_pct"])
        self.assertEqual(first["new_signals"], 1)

        bought = reports[TUESDAY]
        self.assertEqual(bought.summary["market"], "Session")
        self.assertEqual(bought.summary["daily_closed_trades"], 0)
        self.assertEqual(bought.summary["daily_fees_inr"], buy["fees"] / 100)
        self.assertEqual(bought.summary["missing_quotes_recorded"], 6)
        self.assertEqual(bought.summary["buy_hold_entry_session"], str(TUESDAY))
        self.assertLess(bought.summary["buy_hold_return_pct"], 0)  # Entry costs.
        self.assertAlmostEqual(bought.summary["daily_net_pnl_inr"], bought.positions[0]["unrealized_pnl_inr"])
        self.assertIsNone(bought.summary["win_rate_pct"])

        sold = reports[THURSDAY]
        self.assertEqual(sold.positions, [])
        self.assertEqual(sold.summary["daily_closed_trades"], 1)
        self.assertEqual(sold.summary["win_rate_pct"], 0)
        self.assertAlmostEqual(sold.summary["average_loss_inr"], sell["realized_pnl"] / 100)
        self.assertAlmostEqual(sold.summary["expectancy_inr"], sell["realized_pnl"] / 100)
        self.assertAlmostEqual(sold.summary["cumulative_net_pnl_inr"], sell["realized_pnl"] / 100)
        self.assertGreater(sold.summary["max_drawdown_pct"], 0)
        self.assertEqual(sold.summary["cumulative_fees_inr"], (buy["fees"] + sell["fees"]) / 100)

    def test_missed_fills_holiday_gaps_and_unknown_quote_counts(self):
        self.closes(MONDAY, 100)
        self.closes(WEDNESDAY, 100)
        self.ledger.refresh(
            self.seed, {}, {"INFY": quote(100, clock(FRIDAY, 9, 50))}, clock(FRIDAY, 10),
            source="Dashboard", quotes_requested=True,
        )
        self.closes(FRIDAY, 103)
        reports = metrics.build_reports(self.ledger.snapshot(), SATURDAY, clock(SATURDAY, 18))
        self.assertEqual(reports[TUESDAY].summary["market"], "Scheduled closure")
        self.assertEqual(reports[TUESDAY].summary["mark_session"], str(MONDAY))
        self.assertEqual(reports[TUESDAY].summary["daily_net_pnl_inr"], 0)
        self.assertEqual(reports[WEDNESDAY].summary["missed_trades"], 1)
        self.assertEqual(reports[WEDNESDAY].missed[0]["symbol"], "INFY")
        self.assertEqual(reports[THURSDAY].summary["data_status"], "No completed bars verified")
        self.assertIsNone(reports[THURSDAY].summary["equity_inr"])
        self.assertIsNone(reports[THURSDAY].summary["api_errors_recorded"])
        self.assertIsNone(reports[FRIDAY].summary["daily_net_pnl_inr"])
        self.assertIsNone(reports[FRIDAY].summary["missed_trades"])
        self.assertEqual(reports[FRIDAY].summary["stale_quotes_recorded"], 1)
        self.assertEqual(reports[SATURDAY].summary["market"], "Scheduled closure")

    def test_catchup_saves_immutable_html_csv_and_idempotent_revisions(self):
        self.closes(MONDAY, 100)
        first = daily.generate_reports(
            WEDNESDAY, clock(WEDNESDAY, 12), ledger=self.ledger, output=self.output, catch_up=True,
        )
        old = (first / "report.html").read_bytes()
        row = pd.read_csv(first / "summary.csv").iloc[0]
        self.assertEqual(row["stage"], "Interim")
        self.assertTrue(pd.isna(row["daily_net_pnl_inr"]))
        self.assertIsNotNone(daily.latest_report(self.output, TUESDAY))

        self.closes(WEDNESDAY, 100)
        final = daily.generate_reports(
            WEDNESDAY, clock(THURSDAY, 10), ledger=self.ledger, output=self.output, catch_up=True,
        )
        self.assertNotEqual(first, final)
        self.assertEqual((first / "report.html").read_bytes(), old)
        self.assertEqual(daily.latest_report(self.output), final)
        self.assertEqual(pd.read_csv(final / "history.csv")["date"].tolist(), list(map(str, (MONDAY, TUESDAY, WEDNESDAY))))
        text = (final / "report.html").read_text(encoding="utf-8")
        self.assertIn("<svg", text)
        self.assertIn("Trading performance", text)
        self.assertIn("System reliability", text)
        self.assertNotIn("<script", text)
        self.assertEqual(daily.generate_reports(
            WEDNESDAY, clock(THURSDAY, 11), ledger=self.ledger, output=self.output, catch_up=True,
        ), final)

    def test_dc_refresh_is_paused_and_saved_report_still_works(self):
        self.closes(MONDAY, 100)
        original = self.ledger.path.read_bytes()
        now = clock(WEDNESDAY, 18)
        with patch.object(
            scanner, "create_kite_client", side_effect=AssertionError("Kite call while DC feed is paused")
        ) as create_client:
            status = daily.refresh_closes(
                self.ledger, WEDNESDAY, now, {}, source="Windows schedule"
            )
        self.assertTrue(status.startswith("Paused:"))
        create_client.assert_not_called()
        self.assertEqual(self.ledger.path.read_bytes(), original)
        path = daily.generate_reports(WEDNESDAY, clock(WEDNESDAY, 20), ledger=self.ledger, output=self.output)
        row = pd.read_csv(path / "summary.csv").iloc[0]
        self.assertTrue(pd.isna(row["equity_inr"]))
        self.assertFalse(self.ledger.snapshot().fills)
        self.assertEqual(self.ledger.path.read_bytes(), original)

    def test_report_uses_dated_scanner_closes_without_writing_dc_journal(self):
        self.ledger.refresh(self.seed, {}, {}, clock(MONDAY, 11))
        original = self.ledger.path.read_bytes()
        file = self.output / str(MONDAY) / daily.SCANNER_CLOSES_FILE
        file.parent.mkdir(parents=True)
        pd.DataFrame([
            {"Symbol": symbol, "Latest Completed Session": str(MONDAY),
             "Latest Close (INR)": 95, "Current LTP (INR)": 1000}
            for symbol in forward.SYMBOLS
        ]).to_csv(file, index=False)
        closes = daily.load_scanner_closes(file, MONDAY)
        self.assertEqual(closes, dict.fromkeys(forward.SYMBOLS, 9500))
        self.assertEqual(daily.load_scanner_closes(file, TUESDAY), {})
        with self.ledger._database() as db:
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                db.execute("CREATE TABLE report_should_not_write (value)")

        saved = daily.generate_reports(MONDAY, clock(MONDAY, 18), ledger=self.ledger, output=self.output)
        report = daily.load_archived_report(saved)
        self.assertEqual(report.summary["stage"], "After close")
        self.assertEqual(report.summary["data_status"], "Complete")
        self.assertEqual(report.summary["close_source"], "Saved scanner export")
        self.assertEqual(report.summary["candles_received"], 7)
        self.assertEqual(report.summary["dc_candles_received"], 0)
        self.assertEqual(report.summary["simulated_fills"], 0)
        self.assertTrue(pd.isna(report.summary["missed_trades"]))
        self.assertEqual(report.details["readiness"].iloc[0]["progress"], "0 / 60")
        self.assertEqual(self.ledger.path.read_bytes(), original)

    def test_bad_calendar_message_preserves_readable_archive(self):
        from streamlit.testing.v1 import AppTest

        self.closes(MONDAY, 100)
        daily.generate_reports(MONDAY, clock(MONDAY, 18), ledger=self.ledger, output=self.output)
        calendar = self.folder / "dc_nse_calendar.csv"
        self.assertEqual(daily.load_calendar(calendar), {})
        calendar.write_text("date,status\n2026-11-23, OPEN \n", encoding="utf-8")
        self.assertEqual(daily.load_calendar(calendar), {MONDAY: "open"})
        calendar.write_text("date,status\n2026-11-99,open\n", encoding="utf-8")
        with (
            patch.object(daily, "REPORT_DIR", self.output),
            patch.object(daily, "CALENDAR_PATH", calendar),
            patch.object(forward, "JOURNAL_PATH", self.ledger.path),
            patch.object(paper, "now_ist", return_value=clock(MONDAY, 19)),
        ):
            app = AppTest.from_string("from dc_report_view import render_daily_report\nrender_daily_report()").run()
            with self.assertLogs("dc_report_view", level="ERROR") as logs:
                app.button(key="dc_generate_daily_report").click().run()
        self.assertIn("YYYY-MM-DD", app.error[0].value)
        self.assertIn("ValueError", "\n".join(logs.output))
        self.assertTrue(app.get("download_button"))
        self.assertFalse(app.exception)

    def test_missing_archive_file_falls_back_and_generate_repairs_revision(self):
        from streamlit.testing.v1 import AppTest

        self.closes(MONDAY, 100)
        good = daily.generate_reports(MONDAY, clock(MONDAY, 18), ledger=self.ledger, output=self.output)
        partial = good.parent / "revision-20261123T180500000000-damaged"
        shutil.copytree(good, partial)
        (partial / "history.csv").unlink()
        with (
            patch.object(daily, "REPORT_DIR", self.output),
            patch.object(forward, "JOURNAL_PATH", self.ledger.path),
            patch.object(paper, "now_ist", return_value=clock(MONDAY, 19)),
            self.assertLogs("dc_daily_report", level="WARNING") as logs,
        ):
            app = AppTest.from_string("from dc_report_view import render_daily_report\nrender_daily_report()").run()
            self.assertIn("earlier DC report", app.warning[0].value)
            self.assertTrue(any("18:00:00" in node.value for node in app.caption))
            app.button(key="dc_generate_daily_report").click().run()
            latest = daily.latest_report()
        self.assertIn("FileNotFoundError", "\n".join(logs.output))
        self.assertNotIn(latest, (None, good, partial))
        self.assertTrue((good / "history.csv").is_file())
        self.assertTrue(partial.is_dir())
        self.assertFalse(app.error)
        self.assertFalse(app.warning)
        self.assertFalse(app.exception)
        self.assertTrue(app.success)

    def test_publish_permission_error_preserves_existing_revision(self):
        self.closes(MONDAY, 100)
        interim = daily.generate_reports(MONDAY, clock(MONDAY, 12), ledger=self.ledger, output=self.output)
        with patch.object(Path, "rename", side_effect=PermissionError("Archive write denied")):
            with self.assertRaisesRegex(PermissionError, "Archive write denied"):
                daily.generate_reports(MONDAY, clock(MONDAY, 18), ledger=self.ledger, output=self.output)
        self.assertEqual(daily.latest_report(self.output), interim)
        self.assertEqual(list(interim.parent.iterdir()), [interim])

    def test_scheduled_refresh_is_paused_without_kite_or_journal_changes(self):
        self.closes(MONDAY, 100)
        original = self.ledger.path.read_bytes()
        now = clock(WEDNESDAY, 18)
        with self.assertRaises(forward.MarketDataPaused):
            forward.fetch_completed_candles(None, None, {}, WEDNESDAY)
        with patch.object(
            scanner, "create_kite_client", side_effect=AssertionError("Kite call while DC feed is paused")
        ) as create_client:
            status = daily.refresh_closes(
                self.ledger, WEDNESDAY, now, {}, source="Windows schedule"
            )
        self.assertTrue(status.startswith("Paused:"))
        create_client.assert_not_called()
        snapshot = self.ledger.snapshot()
        self.assertEqual(len(snapshot.fills), 0)
        self.assertEqual(self.ledger.path.read_bytes(), original)

    def test_dashboard_displays_archived_report_and_existing_panels(self):
        from streamlit.testing.v1 import AppTest

        self.closes(MONDAY, 100)
        daily.generate_reports(MONDAY, clock(MONDAY, 18), ledger=self.ledger, output=self.output)
        with (
            patch.object(daily, "REPORT_DIR", self.output),
            patch.object(forward, "JOURNAL_PATH", self.ledger.path),
            patch.object(paper, "JOURNAL_PATH", self.folder / "manual.sqlite3"),
            patch("dc_forward_view.load_seed", return_value=self.seed),
            patch.object(scanner, "create_kite_client", side_effect=AssertionError("Network on page load")),
        ):
            app = AppTest.from_file(str(PROJECT / "trading_dashboard.py"), default_timeout=10).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Daily quant performance" in node.value for node in app.subheader))
        self.assertTrue(any(node.value == "PAPER TRADING" for node in app.header))
        self.assertTrue(app.get("vega_lite_chart"))


class WindowsScheduleTests(unittest.TestCase):
    def test_exports_least_privilege_task_with_catchup_and_local_option(self):
        user_sid = "S-1-5-21-100-200-300-1001"
        xml = schedule.make_task(user_sid, "python.exe", PROJECT, MONDAY)
        root = ElementTree.fromstring(xml)
        ns = {"t": schedule.NAMESPACE}

        def value(path):
            return root.findtext(path, namespaces=ns)

        self.assertEqual(value("t:Triggers/t:CalendarTrigger/t:StartBoundary"), "2026-11-23T18:00:00+05:30")
        self.assertEqual(value("t:Triggers/t:LogonTrigger/t:UserId"), user_sid)
        self.assertEqual(value("t:Principals/t:Principal/t:RunLevel"), "LeastPrivilege")
        self.assertEqual(value("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")
        self.assertEqual(value("t:Settings/t:StartWhenAvailable"), "true")
        self.assertEqual(value("t:Settings/t:RunOnlyIfNetworkAvailable"), "false")
        self.assertIn("dc_daily_report.py", value("t:Actions/t:Exec/t:Arguments"))
        self.assertIn("--catch-up --scheduled --refresh", value("t:Actions/t:Exec/t:Arguments"))
        self.assertNotIn("--refresh", schedule.make_task(
            user_sid, "python.exe", PROJECT, MONDAY, refresh=False,
        ).decode("utf-16"))


if __name__ == "__main__":
    unittest.main()
