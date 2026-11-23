"""Offline regression checks for DC confirmation, fills, costs and holdout isolation."""

import unittest

import pandas as pd

import backtest_with_costs as backtest
import dc_research as dc


def candles(closes, opens=None):
    return pd.DataFrame({
        "date": pd.bdate_range("2025-01-02", periods=len(closes)),
        "close": closes,
        "open": closes if opens is None else opens,
    })


class DirectionalChangeTests(unittest.TestCase):
    def test_running_extremes_and_exact_thresholds(self):
        prices = candles([100, 98, 95, 90, 94.49, 94.5, 110, 104.51, 104.5])
        dates = prices["date"].tolist()
        events = list(dc.directional_changes(prices))
        self.assertEqual(
            [(event.direction, event.day, event.extreme_day) for event in events],
            [("down", dates[2], dates[0]), ("up", dates[5], dates[3]), ("down", dates[8], dates[6])],
        )
        self.assertEqual([event.extreme_close for event in events], [100, 90, 110])
        self.assertEqual(list(dc.directional_changes(prices.iloc[:-1])), events[:-1])

    def test_next_open_fills_costs_and_drawdown(self):
        prices = candles(
            [100, 95, 90, 94.5, 100, 95, 100],
            [100, 100, 100, 500, 80, 500, 60],
        )
        pending, fills = dc.run_dc(prices.iloc[:-1])
        self.assertEqual([fill["side"] for fill in fills], ["BUY"])
        self.assertEqual(pending["dc_open_shares"], 124)
        self.assertEqual(pending["dc_completed_trades"], 0)
        self.assertIsNone(pending["dc_win_rate_pct"])

        complete, (buy, sell) = dc.run_dc(prices)
        dates = prices["date"].dt.date.tolist()
        self.assertEqual([buy["confirmation_date"], sell["confirmation_date"]], [dates[3], dates[5]])
        self.assertEqual([buy["fill_date"], sell["fill_date"]], [dates[4], dates[6]])
        self.assertEqual([buy["open_inr"], sell["open_inr"]], [80, 60])
        self.assertGreater(buy["fill_price_inr"], 80)
        self.assertLess(sell["fill_price_inr"], 60)
        self.assertEqual(complete["dc_win_rate_pct"], 0)
        costs = complete["dc_total_estimated_cost_inr"]
        self.assertAlmostEqual(complete["dc_net_pnl_inr"], 124 * (60 - 80) - costs)
        self.assertAlmostEqual(complete["dc_net_pnl_inr"], sell["realized_pnl_inr"])
        peak = backtest.STARTING_CAPITAL + 124 * (100 - 80) - buy["charges_inr"] - buy["slippage_inr"]
        finish = backtest.STARTING_CAPITAL + complete["dc_net_pnl_inr"]
        self.assertAlmostEqual(complete["dc_max_drawdown_pct"], 100 * (peak - finish) / peak)

    def test_cross_boundary_signal_uses_prior_close_and_starts_from_cash(self):
        prices = candles([100, 95, 90, 94.5, 100, 101], [100, 100, 100, 900, 80, 100])
        development = prices.iloc[:4]
        holdout = prices.iloc[4:]
        dev_metrics, dev_fills = dc.run_dc(development)
        self.assertEqual(dev_fills, [])
        self.assertEqual(dev_metrics["dc_total_estimated_cost_inr"], 0)
        metrics, (buy,) = dc.run_dc(holdout, warmup=development)
        self.assertEqual(buy["confirmation_date"], development["date"].iloc[-1].date())
        self.assertEqual(buy["fill_date"], holdout["date"].iloc[0].date())
        self.assertEqual(metrics["dc_open_shares"], 124)
        self.assertEqual(metrics["dc_prior_state_sessions"], 4)
        with self.assertRaises(ValueError):
            dc.run_dc(development, warmup=holdout)

    def test_holdout_price_changes_cannot_change_development_results(self):
        prices = candles([100, 104, 125, 109, 95, 82] * 12)
        cutoff = prices["date"].iloc[45].date()
        summary, fills = dc.evaluate({"NSE_TEST": prices}, cutoff)
        changed = prices.copy()
        changed.loc[45:, ["open", "close"]] *= 3
        new_summary, new_fills = dc.evaluate({"NSE_TEST": changed}, cutoff)
        for original, updated in ((summary, new_summary), (fills, new_fills)):
            pd.testing.assert_frame_equal(
                original.loc[original["period"] == "development"],
                updated.loc[updated["period"] == "development"],
            )
        self.assertNotEqual(summary.iloc[1]["buy_hold_shares"], new_summary.iloc[1]["buy_hold_shares"])


if __name__ == "__main__":
    unittest.main()
