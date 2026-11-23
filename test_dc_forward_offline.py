"""Offline DC forward ledger, dashboard and cost stress checks."""

import hashlib
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import dc_forward as forward
import dc_research as research
import dc_robustness as robustness
import paper_trading as paper


MONDAY = date(2026, 11, 23)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = TUESDAY + timedelta(days=1)
PROJECT = Path(__file__).resolve().parent


def clock(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=paper.IST)


def bars(days, closes):
    return pd.DataFrame({"date": pd.to_datetime(days), "close": closes, "open": closes})


def seed_prices():
    return {
        symbol: bars(["2026-11-19", "2026-11-20"], [100, 95])
        for symbol in forward.SYMBOLS
    }


def quote(price, at, previous=100):
    return forward.MarketQuote(paper.Quote(price * 100, at), previous * 100)


class ForwardLedgerTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.directory = Path(directory)
        self.ledger = forward.ForwardLedger(self.directory / "dc.sqlite3")
        self.seed = seed_prices()

    def confirm_bullish(self):
        incoming = {"INFY": bars([MONDAY, TUESDAY], [100, 400])}
        self.assertEqual(self.ledger.refresh(self.seed, incoming, {}, clock(MONDAY, 17)), 0)
        state = self.ledger.snapshot()
        signal, = state.signals
        self.assertEqual((signal["session"], signal["direction"], signal["status"]), (str(MONDAY), "up", "Pending"))
        self.assertEqual(len(state.closes), 1)

    def test_completed_signal_and_fresh_price_gate_are_persistent(self):
        self.assertIsNone(self.ledger.snapshot())
        self.confirm_bullish()
        now = clock(TUESDAY, 10)
        for moment, market in (
            (now, quote(100, now - timedelta(minutes=6))),
            (now + timedelta(seconds=1), quote(100, now, previous=95)),
            (now + timedelta(seconds=2), quote(100, clock(MONDAY, 15, 20))),
            (now + timedelta(seconds=3), quote(100, now + timedelta(minutes=1))),
            (now + timedelta(seconds=4), quote(100, now - timedelta(minutes=1))),
        ):
            self.assertEqual(self.ledger.refresh(self.seed, {}, {"INFY": market}, moment), 0)
            self.assertEqual(self.ledger.snapshot().signals[0]["status"], "Pending")
        fresh = {"INFY": quote(100, now + timedelta(seconds=5))}
        self.assertEqual(self.ledger.refresh(self.seed, {}, fresh, now + timedelta(seconds=5)), 1)
        reopened = forward.ForwardLedger(self.ledger.path)
        self.assertEqual(reopened.refresh(self.seed, {}, fresh, now + timedelta(seconds=6)), 0)
        (fill,) = reopened.snapshot().fills
        self.assertEqual(fill["shares"], 99)
        self.assertGreater(fill["fees"], 0)
        self.assertGreater(fill["slippage"], 0)

        self.assertEqual(
            self.ledger.refresh(self.seed, {"INFY": bars([TUESDAY], [93])}, {}, clock(TUESDAY, 17)), 0,
        )
        closing_quote = {"INFY": quote(90, clock(WEDNESDAY, 10), previous=93)}
        self.assertEqual(self.ledger.refresh(self.seed, {}, closing_quote, clock(WEDNESDAY, 10)), 1)
        state = reopened.snapshot()
        sell, buy = state.fills
        infy = next(row for row in state.sleeves if row["symbol"] == "INFY")
        self.assertEqual((sell["direction"], buy["direction"], infy["shares"]), ("down", "up", 0))
        self.assertEqual(sell["realized_pnl"], infy["cash"] - forward.SLEEVE_CAPITAL)
        self.assertGreater(state.run["max_drawdown"], 0)

    def test_older_pending_signal_expires_when_a_new_session_closes(self):
        self.confirm_bullish()
        now = clock(WEDNESDAY, 10)
        quotes = {"INFY": quote(111, now, previous=100)}
        # Even if INFY data lags and its quote repeats the signal close, another
        # stock's completed Tuesday candle proves the fill session was missed.
        self.assertEqual(
            self.ledger.refresh(self.seed, {"TCS": bars([TUESDAY], [97])}, quotes, now), 0,
        )
        (signal,) = self.ledger.snapshot().signals
        self.assertEqual(signal["status"], "Expired")

    def test_start_does_not_backfill_old_trades_or_use_intraday_candles(self):
        self.assertEqual(
            self.ledger.refresh(
                self.seed, {"INFY": bars([MONDAY, TUESDAY], [100, 81])},
                {"INFY": quote(111, clock(TUESDAY, 10))}, clock(TUESDAY, 10),
            ), 0,
        )
        state = self.ledger.snapshot()
        self.assertEqual([row["session"] for row in state.closes], [str(MONDAY)])
        self.assertEqual(state.signals, [])

    def test_dashboard_dc_refresh_preserves_manual_paper_journal(self):
        from streamlit.testing.v1 import AppTest

        manual = self.directory / "manual.sqlite3"
        paper.PaperAccount(manual).configure(paper.Settings())
        digest = hashlib.sha256(manual.read_bytes()).hexdigest()
        now = [clock(MONDAY, 17)]
        with (
            patch.object(forward, "JOURNAL_PATH", self.ledger.path),
            patch.object(paper, "JOURNAL_PATH", manual),
            patch.object(paper, "now_ist", side_effect=lambda: now[0]),
            patch("dc_forward_view.load_seed", return_value=self.seed),
            patch("dc_forward_view.fetch_market") as market,
        ):
            app = AppTest.from_file(str(PROJECT / "trading_dashboard.py"), default_timeout=10).run()
            self.assertFalse(app.exception)
            self.assertFalse(self.ledger.path.exists())
            market.assert_not_called()
            self.assertTrue(any(node.value.startswith("DC 5%") for node in app.header))
            self.assertTrue(any(node.value == "PAPER TRADING" for node in app.header))

            market.return_value = ({"INFY": bars([MONDAY], [100])}, {})
            app.button(key="dc_forward_refresh").click().run()
            self.assertFalse(app.exception)
            table = next(item.value for item in app.dataframe if "Daily DC signal" in item.value)
            self.assertEqual(table.set_index("Symbol").loc["INFY", "Daily DC signal"], "Bullish")
            now[0] = clock(TUESDAY, 10)
            market.return_value = ({}, {"INFY": quote(101, now[0])})
            app.button(key="dc_forward_refresh").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(metric.label == "DC max drawdown" for metric in app.metric))
            self.assertEqual(len(self.ledger.snapshot().fills), 1)
        self.assertEqual(hashlib.sha256(manual.read_bytes()).hexdigest(), digest)


class RobustnessTests(unittest.TestCase):
    def test_cost_stress_uses_same_threshold_and_reduces_returns_on_fixed_sample(self):
        dates = pd.bdate_range("2025-09-01", periods=120)
        prices = bars(dates, [100, 105, 115, 110, 104, 94, 96, 110] * 15)
        summary, periods = robustness.stress_test({"TEST": prices}, dates[70].date())
        self.assertEqual(set(summary["dc_threshold_pct"]), {5})
        self.assertEqual(set(summary["fee_multiplier"]), {1, 2, 3})
        self.assertEqual(set(periods["cost_scenario"]), {"base", "higher", "severe"})
        for _, rows in summary.groupby("period"):
            self.assertTrue(rows["dc_total_estimated_cost_inr"].is_monotonic_increasing)
            self.assertTrue(rows["dc_return_pct"].is_monotonic_decreasing)
        base, _ = research.run_dc(prices.iloc[:70])
        actual = summary.query("period == 'development' and cost_scenario == 'base'").iloc[0]
        self.assertAlmostEqual(base["dc_net_pnl_inr"], actual["dc_net_pnl_inr"])


if __name__ == "__main__":
    unittest.main()
