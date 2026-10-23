import copy
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from fred_source import FUND_RATE_PROXIES, attach_rate_analysis
from main import build_payload
from portfolio import ClientPolicy, run_validation
from rate_relationships import attach_price_rate_relationships, price_rate_relationship
from reporting import build_figures, deterministic_report, save_outputs


def matched_fixture(intervals=40):
    days = pd.date_range(end="2026-10-22", periods=intervals + 1, freq="7D")
    rate, price = 4.0, 100.0
    prices, rates = [], []
    for index, day in enumerate(days):
        if index:
            move = [10, -20, 15, -5][(index - 1) % 4]
            rate += move / 100
            # Known regression: intercept +0.05%, slope -1.8% per +100 bps.
            price *= 1 + (0.05 - 0.018 * move) / 100
        prices.append({"date": day.date().isoformat(), "ticker": "SHY", "adjusted_close": price})
        rates.append({"date": day.date().isoformat(), "value": rate})
    return pd.DataFrame(prices), rates


class RelationshipTests(unittest.TestCase):
    def run_case(self, prices=None, rates=None, today="2026-10-23"):
        default_prices, default_rates = matched_fixture()
        return price_rate_relationship(default_prices if prices is None else prices,
                                       default_rates if rates is None else rates,
                                       "SHY", "DGS2", today)

    def test_known_inverse_relationship_and_correct_units(self):
        result, pairs = self.run_case()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["intervals"], 40)
        self.assertAlmostEqual(result["correlation"], -1)
        self.assertAlmostEqual(result["r_squared"], 1)
        self.assertAlmostEqual(result["slope_return_pct_per_100bps"], -1.8)
        self.assertAlmostEqual(result["intercept_return_pct"], 0.05)
        self.assertLess(result["mean_return_when_yields_rise_pct"], 0)
        self.assertGreater(result["mean_return_when_yields_fall_pct"], 0)
        self.assertEqual(result["rising_intervals"], 20)
        self.assertEqual(result["falling_intervals"], 20)
        self.assertAlmostEqual(pairs[0]["yield_change_bps"], 10)
        self.assertAlmostEqual(pairs[0]["adjusted_return_pct"], -0.13)

    def test_missing_week_is_never_forward_filled_or_treated_as_one_week(self):
        prices, rates = matched_fixture()
        omitted = rates[20]["date"]
        rates[20]["value"] = None
        result, pairs = self.run_case(prices, rates)
        self.assertEqual(result["intervals"], 38)
        self.assertTrue(all(omitted not in (row["start"], row["end"]) for row in pairs))
        self.assertTrue(all((pd.Timestamp(row["end"]) - pd.Timestamp(row["start"])).days == 7 for row in pairs))
        self.assertAlmostEqual(result["slope_return_pct_per_100bps"], -1.8)

    def test_future_data_cannot_change_the_relationship(self):
        prices, rates = matched_fixture()
        original, _ = self.run_case(prices, rates)
        prices.loc[len(prices)] = ["2027-01-01", "SHY", 1000]
        rates.append({"date": "2027-01-01", "value": 50})
        future, pairs = self.run_case(prices, rates)
        self.assertEqual(original, future)
        self.assertTrue(all(row["end"] <= "2026-10-23" for row in pairs))

    def test_stale_and_insufficient_samples_are_unverified(self):
        prices, rates = matched_fixture(25)
        result, _ = self.run_case(prices, rates)
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["correlation"])
        self.assertIn("26", result["reason"])
        result, _ = self.run_case(today="2026-11-23")
        self.assertEqual(result["reason"], "No recent matched interval")

    def test_constant_rates_or_constant_returns_do_not_produce_nan_statistics(self):
        prices, rates = matched_fixture()
        for row in rates:
            row["value"] = 4
        result, _ = self.run_case(prices, rates)
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["slope_return_pct_per_100bps"])
        prices, rates = matched_fixture()
        prices["adjusted_close"] = 100
        result, _ = self.run_case(prices, rates)
        self.assertEqual(result["status"], "unverified")
        json.dumps(result, allow_nan=False)

    def test_no_common_dates_gives_no_relationship(self):
        prices, rates = matched_fixture()
        for row in rates:
            row["date"] = (pd.Timestamp(row["date"]) - pd.Timedelta(days=1)).date().isoformat()
        result, pairs = self.run_case(prices, rates)
        self.assertEqual(result["intervals"], 0)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(pairs, [])

    def test_bad_prices_and_duplicate_rate_dates_are_rejected(self):
        prices, rates = matched_fixture()
        with self.assertRaises(ValueError):
            self.run_case(prices, [*rates, rates[-1]])
        prices.loc[0, "adjusted_close"] = -1
        with self.assertRaises(ValueError):
            self.run_case(prices, rates)

    def test_unknown_ticker_is_not_assigned_a_bond_proxy(self):
        result = {"market_source": {"tickers": ["OTHER"]}, "policy": {"maximum_price_age_days": 7},
                  "analysis_date": "2026-10-23"}
        attach_price_rate_relationships(result, pd.DataFrame(), {"series": {}}, FUND_RATE_PROXIES)
        self.assertEqual(result["rate_price_relationships"]["instruments"][0]["status"], "unverified")
        self.assertEqual(result["rate_price_observations"], [])

    def test_reports_charts_payload_and_pair_export(self):
        prices, rates = matched_fixture()
        metadata = {"tickers": ["SHY"]}
        result = run_validation(prices, metadata, ClientPolicy(grid_step_pct=25), {"funds": {}} ,
                                as_of=pd.Timestamp("2026-10-23").date())
        original = copy.deepcopy(result)
        snapshot = {"series": {"DGS2": {"observations": rates}}, "fetched_at_utc": "2026-10-23T10:00:00+00:00"}
        attach_rate_analysis(result, snapshot, prices)
        self.assertEqual(result["candidate_audit"], original["candidate_audit"])
        self.assertEqual(result["rate_price_relationships"]["instruments"][0]["status"], "available")
        payload = build_payload({}, validation=result)
        self.assertIn("rate_price_relationships", payload["validation_result"])
        self.assertNotIn("rate_price_observations", payload["validation_result"])
        self.assertIn("opposite directions", deterministic_report(result))
        self.assertIn("R-squared", deterministic_report(result))
        figures, _ = build_figures(result, prices)
        self.assertIn("rate_relationships", figures)
        with tempfile.TemporaryDirectory() as folder:
            save_outputs(result, prices, folder)
            exported = pd.read_csv(Path(folder) / "rate_price_observations.csv")
            self.assertEqual(len(exported), 40)
            self.assertIn("Observed relationship", (Path(folder) / "report.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
