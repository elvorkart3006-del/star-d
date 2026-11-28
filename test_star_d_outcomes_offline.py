"""Synthetic tests for STAR-D horizon indexing and data availability."""

from datetime import date, datetime
import unittest
from unittest.mock import Mock

from multi_stock_scanner import IST
from star_d_outcomes import SEED_SESSIONS, aggregate, score


SIGNAL = date(2026, 11, 26)
BUY = [("INFY", SIGNAL)]


class StarDOutcomesTests(unittest.TestCase):
    def evaluate(self, now, loader):
        return score(BUY, SEED_SESSIONS, now, loader)

    def test_completed_horizons_and_aggregate(self):
        closes = dict(zip((SIGNAL, *SEED_SESSIONS), (100, 103, 102, 96, 105, 100)))
        loader = Mock(return_value=(closes, None))
        rows = self.evaluate(datetime(2026, 12, 3, 16, tzinfo=IST), loader)
        self.assertEqual([row["outcome"] for row in rows], ["HIGHER", "LOWER", "UNCHANGED"])
        self.assertEqual([row["target_session"] for row in rows],
                         ["2026-11-27", "2026-12-01", "2026-12-03"])
        self.assertEqual([row[label] for row, label in
                          zip(aggregate(rows), ("HIGHER", "LOWER", "UNCHANGED"))], [1, 1, 1])
        self.assertEqual(set(rows[0]), {
            "symbol", "signal_date", "horizon_sessions", "target_session", "outcome", "status_detail",
        })

    def test_weekend_fetch_stops_at_last_completed_session(self):
        first = SEED_SESSIONS[0]
        loader = Mock(return_value=({SIGNAL: 100, first: 99}, None))
        rows = self.evaluate(datetime(2026, 11, 28, 12, tzinfo=IST), loader)
        loader.assert_called_once_with("INFY", SIGNAL, first)
        self.assertEqual([row["outcome"] for row in rows], ["LOWER", "PENDING", "PENDING"])

    def test_before_cutoff_fetches_nothing(self):
        loader = Mock()
        rows = self.evaluate(datetime(2026, 11, 27, 15, 59, tzinfo=IST), loader)
        loader.assert_not_called()
        self.assertTrue(all(row["outcome"] == "PENDING" for row in rows))

    def test_missing_intermediate_candle_does_not_shift_target(self):
        first, _, third, *_ = SEED_SESSIONS
        loader = Mock(return_value=({SIGNAL: 100, first: 99, third: 105}, None))
        rows = self.evaluate(datetime(2026, 12, 1, 16, tzinfo=IST), loader)
        self.assertEqual(rows[1]["target_session"], str(third))
        self.assertEqual(rows[1]["outcome"], "UNAVAILABLE")
        self.assertIn("2026-11-30", rows[1]["status_detail"])

    def test_auth_failure_keeps_future_horizons_pending(self):
        loader = Mock(return_value=({}, "Kite authentication unavailable"))
        rows = self.evaluate(datetime(2026, 11, 28, 12, tzinfo=IST), loader)
        self.assertEqual([row["outcome"] for row in rows], ["UNAVAILABLE", "PENDING", "PENDING"])


if __name__ == "__main__":
    unittest.main()
