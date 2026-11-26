"""Paper ledger and dashboard tests using only temporary databases and fake quotes."""

import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import multi_stock_scanner as scanner
import paper_trading as paper


NOW = datetime(2026, 11, 23, 10, tzinfo=paper.IST)
PROJECT = Path(__file__).resolve().parent


def quote(price, at=NOW):
    return paper.Quote(paper.to_paise(price), at)


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "paper.sqlite3"
        self.account = paper.PaperAccount(self.path)

    def test_partial_exits_reconcile_cost_basis_cash_and_journal_across_reopen(self):
        first = self.account.snapshot()
        self.assertFalse(self.path.exists())
        self.assertEqual(first.cash, first.settings.capital)

        buy_fill = self.account.trade("INFY", "BUY", 10, quote(100), NOW)
        self.assertEqual(buy_fill, 10005)
        opened = self.account.snapshot()
        self.assertEqual(opened.unrealized, -opened.costs)
        self.assertEqual(opened.positions["INFY"]["stop"], 9504)

        added_at = NOW + timedelta(minutes=1)
        self.account.trade("INFY", "BUY", 10, quote(110, added_at), added_at)
        added = self.account.snapshot()
        self.assertGreater(added.positions["INFY"]["stop"], opened.positions["INFY"]["stop"])

        sold_at = added_at + timedelta(minutes=1)
        self.account.trade("INFY", "SELL", 10, quote(120, sold_at), sold_at)
        partial = self.account.snapshot()
        self.assertEqual(partial.positions["INFY"]["quantity"], 10)
        self.assertEqual(partial.positions["INFY"]["stop"], added.positions["INFY"]["stop"])
        self.assertEqual(
            partial.realized + partial.unrealized,
            partial.cash + partial.market_value - partial.settings.capital,
        )
        self.account.trade("INFY", "SELL", 10, quote(120, sold_at), sold_at)
        reopened = paper.PaperAccount(self.path).snapshot()
        self.assertEqual(reopened.positions, {})
        self.assertEqual(len(reopened.journal), 4)
        self.assertEqual(reopened.realized, reopened.cash - reopened.settings.capital)
        self.assertEqual(reopened.realized, 30_000 - reopened.costs)
        self.assertEqual(reopened.journal[1]["fees"] - reopened.journal[0]["fees"], 1534)

        self.account.configure(paper.Settings(capital=20_000_000, stop_pct=10))
        funded = paper.PaperAccount(self.path).snapshot()
        self.assertEqual(funded.cash, reopened.cash + 10_000_000)
        self.assertEqual(funded.realized, reopened.realized)
        self.assertEqual(funded.settings.stop_pct, 10)
        self.assertEqual(len(funded.journal), 4)

    def test_buy_limits_and_no_overselling(self):
        self.account.configure(paper.Settings(capital=100_000, position_pct=25, max_positions=1))
        self.account.trade("INFY", "BUY", 2, quote(100), NOW)
        for side, symbol, quantity, message in (
            ("BUY", "INFY", 1, "position limit"),
            ("BUY", "TCS", 1, "open position limit"),
            ("BUY", "INFY", 0, "positive whole number"),
            ("SELL", "TCS", 1, "only shares"),
            ("SELL", "INFY", 3, "only shares"),
        ):
            with self.subTest(side=side, symbol=symbol, quantity=quantity):
                with self.assertRaisesRegex(paper.PaperTradeError, message):
                    self.account.trade(symbol, side, quantity, quote(100), NOW)
        self.account.configure(paper.Settings(capital=100_000, position_pct=100))
        with self.assertRaisesRegex(paper.PaperTradeError, "Insufficient paper cash"):
            self.account.trade("TCS", "BUY", 8, quote(100), NOW)
        with self.assertRaisesRegex(paper.PaperTradeError, "available paper cash"):
            self.account.configure(paper.Settings(capital=10_000))
        self.assertEqual(len(self.account.snapshot().journal), 1)

    def test_stops_ignore_stale_missing_and_future_quotes_and_fill_at_observed_gap(self):
        self.account.trade("INFY", "BUY", 10, quote(100), NOW)
        later = NOW + timedelta(minutes=10)
        self.assertEqual(self.account.apply_quotes({}, later), [])
        self.assertEqual(self.account.apply_quotes({"INFY": quote(80, NOW)}, later), [])
        self.assertEqual(self.account.apply_quotes({"INFY": quote(80, later + timedelta(seconds=1))}, later), [])
        self.assertEqual(self.account.snapshot().positions["INFY"]["quantity"], 10)
        with self.assertRaisesRegex(paper.PaperTradeError, "fresh Zerodha"):
            self.account.trade("INFY", "SELL", 10, quote(80, NOW), later)
        with self.assertRaisesRegex(paper.PaperTradeError, "fresh Zerodha"):
            closing_time = NOW.replace(hour=15, minute=30)
            self.account.trade("INFY", "SELL", 10, quote(80, closing_time), closing_time)

        marks = {"INFY": quote(80, later)}
        self.assertEqual(self.account.apply_quotes(marks, later), ["INFY"])
        self.assertEqual(self.account.apply_quotes(marks, later), [])
        closed = self.account.snapshot()
        self.assertFalse(closed.positions)
        self.assertEqual(len(closed.journal), 2)
        stop = closed.journal[0]
        self.assertEqual((stop["side"], stop["reason"], stop["fill_price"]), ("SELL", "Stop-loss", 7996))
        self.assertEqual(closed.realized, closed.cash - closed.settings.capital)

    def test_stale_or_out_of_order_mark_does_not_replace_a_newer_mark(self):
        self.account.trade("INFY", "BUY", 1, quote(100), NOW)
        later = NOW + timedelta(minutes=2)
        self.account.apply_quotes({"INFY": quote(110, later)}, later)
        self.account.apply_quotes({"INFY": quote(80, NOW)}, later)
        self.assertEqual(self.account.snapshot().positions["INFY"]["last_price"], 11000)
        with self.assertRaisesRegex(paper.PaperTradeError, "newer price"):
            self.account.trade("INFY", "SELL", 1, quote(80), later)

    def test_failed_journal_insert_rolls_back_and_concurrent_sells_cannot_oversell(self):
        self.account.trade("INFY", "BUY", 10, quote(100), NOW)
        before = self.account.snapshot()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """CREATE TRIGGER reject_trade BEFORE INSERT ON trades
                   BEGIN SELECT RAISE(ABORT, 'offline rollback test'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.account.trade("INFY", "SELL", 10, quote(110), NOW)
        after = self.account.snapshot()
        self.assertEqual(after.cash, before.cash)
        self.assertEqual(after.positions["INFY"]["quantity"], 10)
        self.assertEqual(len(after.journal), 1)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP TRIGGER reject_trade")

        def sell():
            try:
                paper.PaperAccount(self.path).trade("INFY", "SELL", 10, quote(110), NOW)
                return "sold"
            except paper.PaperTradeError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(sell), executor.submit(sell)]
            self.assertCountEqual([future.result() for future in futures], ["sold", "rejected"])
        self.assertFalse(self.account.snapshot().positions)
        self.assertEqual(len(self.account.snapshot().journal), 2)

    def test_dashboard_disables_kite_paper_trading_and_preserves_account(self):
        from streamlit.testing.v1 import AppTest

        self.account.configure(paper.Settings(5_000_000, 40, 4, 8))
        before = self.account.snapshot()
        with (
            patch.object(paper, "JOURNAL_PATH", self.path),
            patch.object(scanner, "create_kite_client", side_effect=AssertionError("Kite called")) as client,
        ):
            app = AppTest.from_file(str(PROJECT / "trading_dashboard.py"), default_timeout=10).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(item.value == "PAPER TRADING" for item in app.header))
            self.assertTrue(any(button.label == "Refresh Scanner" for button in app.button))
            self.assertTrue(any("configuration and journal remain untouched" in item.value for item in app.caption))
            labels = {button.label for button in app.button}
            self.assertNotIn("Simulate PAPER trade", labels)
            self.assertNotIn("Refresh PAPER prices and check stops", labels)
            self.assertNotIn("Save PAPER settings", labels)
            client.assert_not_called()

        after = self.account.snapshot()
        self.assertEqual(after.settings, before.settings)
        self.assertEqual(after.cash, before.cash)
        self.assertEqual(after.journal, before.journal)


if __name__ == "__main__":
    unittest.main()
