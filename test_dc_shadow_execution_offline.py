"""Focused tests for the synthetic, in-memory DC shadow prototype.

Run with:
    py -3 -B -m unittest -v test_dc_shadow_execution_offline

These tests use no credentials, network, broker, or persistent journal.
"""

import ast
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import dc_shadow_execution as shadow


IST = shadow.IST
SESSIONS = [
    date(2026, 11, 18),
    date(2026, 11, 19),
    date(2026, 11, 20),
    date(2026, 11, 23),
    date(2026, 11, 24),
    date(2026, 11, 25),
]


class ShadowExecutionOfflineTests(unittest.TestCase):
    def build_bullish_pending(self):
        wrapper = shadow.ShadowExecutionWrapper()
        signal = None
        for session, close in zip(SESSIONS[:3], (100.0, 94.0, 99.0), strict=True):
            signal = wrapper.observe_completed_close(
                shadow.CompletedClose("TCS", session, close)
            ) or signal
        self.assertIsNotNone(signal)
        self.assertEqual(signal.confirmation.direction, "up")
        return wrapper, signal

    def test_stale_quote_stays_pending_then_fresh_synthetic_quote_fills_in_memory(self):
        wrapper, signal = self.build_bullish_pending()
        now = datetime(2026, 11, 23, 10, 0, tzinfo=IST)
        stale = shadow.QuoteSnapshot(
            "TCS", 99.25, now - timedelta(seconds=301), 99.0
        )
        stale_result = wrapper.observe_quote(stale, now)
        self.assertFalse(stale_result.filled)
        self.assertIn("more than five minutes", stale_result.reason)
        self.assertEqual(signal.status, "Pending")
        self.assertEqual(wrapper.ledger.fills, [])

        fresh = shadow.QuoteSnapshot(
            "TCS", 99.25, now - timedelta(seconds=30), 99.0
        )
        fresh_result = wrapper.observe_quote(fresh, now)
        self.assertTrue(fresh_result.filled)
        self.assertEqual(fresh_result.intent.mode, "SHADOW_ONLY")
        self.assertEqual(fresh_result.fill.mode, "VIRTUAL")
        self.assertEqual(fresh_result.fill.side, "BUY")
        self.assertGreater(fresh_result.fill.quantity, 0)
        self.assertEqual(signal.status, "Shadow filled")

    def test_bearish_confirmation_closes_only_the_virtual_position(self):
        wrapper, _ = self.build_bullish_pending()
        buy_now = datetime(2026, 11, 23, 10, 1, tzinfo=IST)
        wrapper.observe_quote(
            shadow.QuoteSnapshot(
                "TCS", 99.25, buy_now - timedelta(seconds=30), 99.0
            ),
            buy_now,
        )
        wrapper.observe_completed_close(shadow.CompletedClose("TCS", SESSIONS[3], 104.0))
        sell_signal = wrapper.observe_completed_close(
            shadow.CompletedClose("TCS", SESSIONS[4], 98.0)
        )
        self.assertIsNotNone(sell_signal)
        self.assertEqual(sell_signal.confirmation.direction, "down")

        sell_now = datetime(2026, 11, 25, 10, 2, tzinfo=IST)
        result = wrapper.observe_quote(
            shadow.QuoteSnapshot(
                "TCS", 98.5, sell_now - timedelta(seconds=20), 98.0
            ),
            sell_now,
        )
        self.assertTrue(result.filled)
        self.assertEqual(result.fill.side, "SELL")
        self.assertEqual(wrapper.ledger.sleeves["TCS"].shares, 0)
        self.assertEqual([fill.side for fill in wrapper.ledger.fills], ["BUY", "SELL"])

    def test_account_is_seven_fixed_virtual_sleeves_totaling_70000(self):
        ledger = shadow.InMemoryVirtualLedger()
        self.assertEqual(set(ledger.sleeves), set(shadow.SYMBOLS))
        self.assertEqual(len(ledger.sleeves), 7)
        self.assertEqual(ledger.starting_capital_inr, 70_000)
        self.assertEqual(ledger.cash_inr, 70_000)
        self.assertEqual(ledger.portfolio_value_inr, 70_000)
        self.assertTrue(all(sleeve.shares == 0 for sleeve in ledger.sleeves.values()))

    def test_only_synthetic_source_is_accepted(self):
        wrapper = shadow.ShadowExecutionWrapper()
        with self.assertRaises(shadow.ShadowInputError):
            wrapper.observe_completed_close(
                shadow.CompletedClose("TCS", SESSIONS[0], 100, source="KITE")
            )
        self.assertEqual(wrapper.ledger.fills, [])

    def test_prototype_has_no_broker_or_network_order_interface(self):
        wrapper = shadow.ShadowExecutionWrapper()
        self.assertFalse(hasattr(wrapper, "kite"))
        self.assertFalse(hasattr(wrapper, "broker"))
        self.assertFalse(hasattr(wrapper, "place_order"))

        source_path = Path(shadow.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(imported.isdisjoint({"kiteconnect", "requests", "dotenv", "socket"}))


if __name__ == "__main__":
    unittest.main()
