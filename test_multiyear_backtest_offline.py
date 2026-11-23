"""Regression tests for the chronological split and calendar-year accounting."""

import unittest
from datetime import date

import pandas as pd

import backtest_sma_6_30_multiyear as multiyear
import backtest_with_costs as backtest


def candles(dates, closes, opens=None):
    return pd.DataFrame({
        "date": dates,
        "open": closes if opens is None else opens,
        "close": closes,
    })


class MultiyearBacktestTests(unittest.TestCase):
    def test_calendar_split_uses_one_year_of_holdout_for_all_lookbacks(self):
        end = date(2026, 11, 22)
        for years in (3, 4, 5):
            with self.subTest(years=years):
                start, holdout = multiyear.evaluation_dates(end, years)
                self.assertEqual(start, date(2026 - years, 11, 23))
                self.assertEqual(holdout, date(2025, 11, 23))

    def test_holdout_prices_cannot_change_development_reports(self):
        dates = pd.bdate_range("2024-10-01", periods=180)
        closes = [100, 104, 125, 109, 95, 82] * 30
        prices = candles(dates, closes)
        cutoff = dates[120].date()
        summary, yearly = multiyear.evaluate({"NSE_TEST": prices}, cutoff)

        changed = prices.copy()
        changed.loc[120:, ["open", "close"]] *= 3
        changed_summary, changed_yearly = multiyear.evaluate({"NSE_TEST": changed}, cutoff)
        for original, updated in ((summary, changed_summary), (yearly, changed_yearly)):
            pd.testing.assert_frame_equal(
                original.loc[original["period"] == "development"],
                updated.loc[updated["period"] == "development"],
            )
        self.assertNotEqual(summary.iloc[1]["buy_hold_shares"], changed_summary.iloc[1]["buy_hold_shares"])

    def test_previous_close_signal_can_fill_first_holdout_open_without_carrying_a_trade(self):
        dates = pd.bdate_range("2025-09-01", periods=45)
        closes = [100] * 35 + [120] + [115] * 9
        opens = [100] * 35 + [500] + [80] + [115] * 8
        cutoff = dates[36].date()
        summary, yearly = multiyear.evaluate({"NSE_TEST": candles(dates, closes, opens)}, cutoff)
        development, holdout = summary.iloc[0], summary.iloc[1]
        self.assertEqual(development["sma_open_shares"], 0)
        self.assertEqual(development["sma_total_estimated_cost_inr"], 0)
        self.assertEqual(holdout["sma_prior_warmup_sessions"], 31)
        self.assertEqual(holdout["first_session"], cutoff)
        self.assertEqual(holdout["sessions"], 9)
        self.assertEqual(holdout["sma_open_shares"], 124)
        self.assertAlmostEqual(
            holdout["sma_net_pnl_inr"], 124 * (115 - 80) - holdout["sma_total_estimated_cost_inr"]
        )
        self.assertEqual(yearly.iloc[-1]["sma_starting_equity_inr"], backtest.STARTING_CAPITAL)

    def test_calendar_years_carry_equity_and_assign_trade_costs_when_paid(self):
        dates = pd.bdate_range("2024-10-01", periods=95)
        closes = [100] * 35 + [120] * 34 + [75] * 26
        summary, yearly = multiyear.run_period("NSE_TEST", "development", candles(dates, closes))
        first, second = yearly
        self.assertEqual((first["calendar_year"], second["calendar_year"]), (2024, 2025))
        self.assertEqual(first["sma_completed_trades"], 0)
        self.assertEqual(second["sma_completed_trades"], 1)
        self.assertGreater(first["sma_estimated_cost_inr"], 0)
        self.assertGreater(second["sma_estimated_cost_inr"], 0)
        self.assertEqual(second["buy_hold_estimated_cost_inr"], 0)
        for name in ("sma", "buy_hold"):
            self.assertEqual(first[f"{name}_ending_equity_inr"], second[f"{name}_starting_equity_inr"])
            compounded = (1 + first[f"{name}_return_pct"] / 100) * (1 + second[f"{name}_return_pct"] / 100)
            self.assertAlmostEqual(100 * (compounded - 1), summary[f"{name}_return_pct"])
        self.assertAlmostEqual(
            sum(row["sma_estimated_cost_inr"] for row in yearly),
            summary["sma_total_estimated_cost_inr"],
        )


if __name__ == "__main__":
    unittest.main()
