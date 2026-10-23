import copy
import json
import unittest
from dataclasses import asdict, replace
from datetime import date

import numpy as np
import pandas as pd

from main import build_payload
from portfolio import ClientPolicy, align_prices, allocation_grid, portfolio_path, run_validation
from reporting import build_figures, deterministic_report, render_html


TODAY = date(2026, 10, 23)


def fixture():
    dates = pd.bdate_range(end="2026-10-22", periods=300)
    rows = []
    for ticker, growth in [("BIL", 0.0001), ("SHY", 0.00012), ("IEF", 0.00015)]:
        rows.extend({"date": d.date().isoformat(), "ticker": ticker, "adjusted_close": 100 * (1 + growth) ** i}
                    for i, d in enumerate(dates))
    facts = {"schema_version": 1, "funds": {}}
    for ticker, duration, sec_yield in [("BIL", 0.1, 3.0), ("SHY", 1.8, 4.0), ("IEF", 7.0, 5.0)]:
        facts["funds"][ticker] = {
            "name": ticker, "source_url": "https://example.com/issuer", "identity_as_of": "2026-10-22",
            "quote_currency": "USD", "asset_class": "us_treasury_etf", "duration_years": duration,
            "duration_as_of": "2026-10-22", "sec_yield_pct": sec_yield, "yield_as_of": "2026-10-22",
            "spread_pct": 0.01, "spread_as_of": "2026-10-22", "expense_ratio_pct": 1.0,
        }
    metadata = {"tickers": ["BIL", "SHY", "IEF"], "start": "2019-01-01", "requested_end": None,
                "fetched_at_utc": "2026-10-23T10:00:00+00:00"}
    return pd.DataFrame(rows), metadata, facts


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.prices, self.metadata, self.facts = fixture()
        # Existing risk/evidence regressions use the former holding rules.
        # The diversification defaults are covered separately below.
        self.policy = ClientPolicy(grid_step_pct=25, minimum_holdings=0, maximum_holding_pct=100)

    def run_case(self, policy=None, facts=None, as_of=TODAY):
        return run_validation(self.prices, self.metadata, policy or self.policy,
                              self.facts if facts is None else facts, as_of)

    def test_grid_is_complete_nonnegative_and_sums_to_one(self):
        grid = allocation_grid(4, 5)
        self.assertEqual(len(grid), 1771)
        np.testing.assert_allclose(grid.sum(axis=1), 1)
        self.assertTrue((grid >= 0).all())
        with self.assertRaisesRegex(ValueError, "25,000"):
            allocation_grid(6, 1)

    def test_every_selected_check_passes_and_income_has_no_second_fee_deduction(self):
        result = self.run_case()
        selected = result["selected"]
        self.assertEqual(result["status"], "feasible")
        self.assertTrue(all(c["status"] == "pass" for c in selected["checks"]))
        self.assertAlmostEqual(sum(selected["amounts_usd"].values()), 100000)
        expected = sum(amount * self.facts["funds"][ticker]["sec_yield_pct"] / 100
                       for ticker, amount in selected["amounts_usd"].items() if ticker != "CASH")
        self.assertEqual(selected["metrics"]["income_run_rate_usd"], expected)
        passing_incomes = [c["income_run_rate_usd"] for c in result["candidate_audit"] if c["status"] == "pass"]
        self.assertEqual(selected["metrics"]["income_run_rate_usd"], max(passing_incomes))

    def test_cash_is_allowed_to_be_the_entire_allocation(self):
        result = self.run_case(replace(self.policy, maximum_rate_loss_usd=0))
        self.assertEqual(result["selected"]["amounts_usd"]["CASH"], 100000)
        self.assertEqual(result["selected"]["metrics"]["rate_up_loss_usd"], 0)

    def test_larger_reserve_changes_the_allocation(self):
        initial = self.run_case()
        updated = self.run_case(replace(self.policy, minimum_cash_usd=50000))
        self.assertLess(initial["selected"]["amounts_usd"]["CASH"], updated["selected"]["amounts_usd"]["CASH"])
        self.assertGreaterEqual(updated["selected"]["amounts_usd"]["CASH"], 50000)

    def test_same_day_claim_cannot_be_funded_with_etf_shares(self):
        result = self.run_case(replace(self.policy, stressed_claim_usd=50000, claim_due_days=0))
        selected = result["selected"]
        self.assertGreaterEqual(selected["amounts_usd"]["CASH"], 50000)
        self.assertEqual(selected["metrics"]["stressed_available_usd"], selected["amounts_usd"]["CASH"])

    def test_impossible_claim_and_reserve_are_infeasible_without_relaxation(self):
        for policy in [replace(self.policy, stressed_claim_usd=100001), replace(self.policy, minimum_cash_usd=100001)]:
            result = self.run_case(policy)
            self.assertEqual(result["status"], "infeasible")
            self.assertIsNone(result["selected"])
            self.assertIsNone(result["provisional"])

    def test_missing_duration_never_becomes_zero_or_passes_for_an_exposed_portfolio(self):
        facts = copy.deepcopy(self.facts)
        facts["funds"]["SHY"].pop("duration_years")
        result = self.run_case(facts=facts)
        exposed = [c for c in result["candidate_audit"] if c["SHY"] > 0]
        self.assertTrue(all(c["rate_up_loss_usd"] is None for c in exposed))
        self.assertTrue(all(c["status"] != "pass" for c in exposed))
        shy = next(e for e in result["fund_evidence"] if e["ticker"] == "SHY")
        self.assertEqual(shy["status"], "unverified")

    def test_stale_or_future_facts_are_unverified(self):
        for fact_date in ("2020-01-01", "2027-01-01"):
            facts = copy.deepcopy(self.facts)
            facts["funds"]["BIL"]["yield_as_of"] = fact_date
            result = self.run_case(facts=facts)
            self.assertTrue(all(c["status"] != "pass" for c in result["candidate_audit"] if c["BIL"] > 0))
            self.assertEqual(result["baseline"]["status"], "unverified")

    def test_currency_mismatch_fails_and_missing_currency_is_unverified(self):
        for currency, expected in [("GBP", "fail"), (None, "unverified")]:
            facts = copy.deepcopy(self.facts)
            facts["funds"]["BIL"]["quote_currency"] = currency
            result = self.run_case(facts=facts)
            fund = next(e for e in result["fund_evidence"] if e["ticker"] == "BIL")
            item = next(c for c in fund["checks"] if c["name"] == "Currency match")
            self.assertEqual(item["status"], expected)

    def test_stale_common_history_blocks_validated_selection(self):
        result = self.run_case(as_of=date(2026, 12, 1))
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["selected"])
        self.assertIsNotNone(result["provisional"])

    def test_cent_exact_allocation(self):
        policy = replace(self.policy, budget_usd=100000.03, minimum_cash_usd=25000.01)
        result = self.run_case(policy)
        selected = result["selected"]
        self.assertEqual(round(sum(selected["amounts_usd"].values()), 2), policy.budget_usd)
        self.assertAlmostEqual(sum(selected["weights"].values()), 1)

    def test_portfolio_drawdown_is_from_the_combined_path(self):
        dates = pd.date_range("2026-01-01", periods=3)
        table = pd.DataFrame({"A": [100, 110, 100], "B": [100, 90, 100]}, index=dates)
        path = portfolio_path(table, {"CASH": 0, "A": 0.5, "B": 0.5}, self.policy)
        np.testing.assert_allclose(path, [1, 1, 1])
        self.assertEqual(float((1 - path / path.cummax()).max()), 0)
        self.assertGreater(float(((1 - table / table.cummax()).max() * 0.5).sum()), 0)

    def test_common_window_alignment_does_not_forward_fill_internal_gaps(self):
        self.prices.loc[5, "adjusted_close"] = np.nan
        with self.assertRaisesRegex(ValueError, "gaps"):
            align_prices(self.prices, self.metadata["tickers"])

    def test_invalid_policies_are_rejected(self):
        for kwargs in ({"budget_usd": -1}, {"budget_usd": "100000"}, {"budget_usd": float("nan")},
                       {"grid_step_pct": 3}, {"currency": "GBP"}, {"rate_shock_bps": 500}, {"minimum_history_sessions": 1},
                       {"minimum_holdings": 2.5}, {"minimum_holdings": True}, {"minimum_holdings": -1},
                       {"minimum_holding_pct": 0}, {"minimum_holding_pct": 40, "maximum_holding_pct": 35},
                       {"maximum_holding_pct": 101}, {"maximum_holding_pct": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ClientPolicy(**kwargs)

    def test_ai_payload_contains_validated_result_but_excludes_the_full_search(self):
        result = self.run_case()
        payload = build_payload({"instruments": []}, validation=result)
        self.assertEqual(payload["client"]["budget_usd"], 100000)
        self.assertEqual(payload["validation_result"]["selected"]["amounts_usd"], result["selected"]["amounts_usd"])
        self.assertNotIn("candidate_audit", payload["validation_result"])
        json.dumps(payload, allow_nan=False)

    def test_report_and_charts_cover_feasible_unverified_and_infeasible_results(self):
        cases = [self.run_case(), self.run_case(as_of=date(2026, 12, 1)),
                 self.run_case(replace(self.policy, minimum_cash_usd=200000))]
        for result in cases:
            with self.subTest(status=result["status"]):
                figures, history = build_figures(result, self.prices)
                output = render_html(result, figures)
                self.assertIn("plotly.js", output)
                self.assertNotIn('<script src="https://cdn.plot.ly', output)
                self.assertIn("prototype", deterministic_report(result).lower())
                self.assertIn("date", history.index.names)


class DiversificationTests(unittest.TestCase):
    def setUp(self):
        self.prices, self.metadata, self.facts = fixture()

    def run_case(self, policy=None, facts=None, metadata=None, prices=None):
        return run_validation(self.prices if prices is None else prices,
                              self.metadata if metadata is None else metadata,
                              ClientPolicy() if policy is None else policy,
                              self.facts if facts is None else facts, TODAY)

    def test_default_selected_and_alternatives_obey_all_rules(self):
        result = self.run_case()
        self.assertEqual(result["status"], "feasible")
        for candidate in [result["selected"], *result["alternatives"]]:
            investments = [weight * 100 for ticker, weight in candidate["weights"].items() if ticker != "CASH" and weight > 0]
            self.assertGreaterEqual(len(investments), 3)
            self.assertTrue(all(10 - 1e-8 <= weight <= 35 + 1e-8 for weight in investments))
            self.assertTrue(all(item["status"] == "pass" for item in candidate["checks"]))
        self.assertEqual(result["selected"]["metrics"]["investment_holdings"], 3)

    def test_old_75_percent_shy_portfolio_fails_and_cash_is_not_a_holding(self):
        result = self.run_case()
        old = next(c for c in result["candidate_audit"] if c["CASH"] == 25000 and c["SHY"] == 75000)
        self.assertEqual(old["status"], "fail")
        self.assertIn("Maximum single holding (%)", old["failed_checks"])
        self.assertIn("Minimum investment holdings", old["failed_checks"])
        cash_only = next(c for c in result["candidate_audit"] if c["CASH"] == 100000)
        self.assertEqual(cash_only["investment_holdings"], 0)
        self.assertIn("Minimum investment holdings", cash_only["failed_checks"])

    def test_token_holding_fails_even_if_the_count_is_three(self):
        result = self.run_case()
        candidate = next(c for c in result["candidate_audit"] if c["CASH"] == 25000 and c["BIL"] == 35000 and c["SHY"] == 35000 and c["IEF"] == 5000)
        self.assertEqual(candidate["investment_holdings"], 3)
        self.assertIn("Minimum holding size (%)", candidate["failed_checks"])
        self.assertNotIn("Maximum single holding (%)", candidate["failed_checks"])

    def test_existing_risk_limits_can_make_diversification_infeasible(self):
        for policy in (ClientPolicy(maximum_rate_loss_usd=0), ClientPolicy(minimum_cash_usd=75000),
                       ClientPolicy(minimum_holdings=4), ClientPolicy(grid_step_pct=25)):
            with self.subTest(policy=policy):
                result = self.run_case(policy)
                self.assertEqual(result["status"], "infeasible")
                self.assertIsNone(result["selected"])
                self.assertIsNone(result["provisional"])

    def test_two_symbol_universe_does_not_silently_reduce_required_count(self):
        metadata = {**self.metadata, "tickers": ["BIL", "SHY"]}
        prices = self.prices.loc[self.prices["ticker"].isin(metadata["tickers"])]
        result = self.run_case(metadata=metadata, prices=prices)
        self.assertEqual(result["status"], "infeasible")
        self.assertEqual(result["policy"]["minimum_holdings"], 3)

    def test_missing_required_evidence_cannot_be_avoided_with_fewer_holdings(self):
        facts = copy.deepcopy(self.facts)
        facts["funds"]["IEF"].pop("duration_years")
        result = self.run_case(facts=facts)
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["selected"])
        self.assertIsNotNone(result["provisional"])
        self.assertGreaterEqual(result["provisional"]["amounts_usd"]["IEF"], 10000)

    def test_reference_measures_income_cost_without_becoming_fallback(self):
        result = self.run_case()
        reference = result["diversification"]["comparison_without_holding_limits"]
        unconstrained = self.run_case(ClientPolicy(minimum_holdings=0, minimum_holding_pct=5, maximum_holding_pct=100))
        self.assertEqual(reference["amounts_usd"], unconstrained["selected"]["amounts_usd"])
        self.assertEqual(reference["metrics"]["income_run_rate_usd"], unconstrained["selected"]["metrics"]["income_run_rate_usd"])
        self.assertEqual(reference["status"], "fail")
        self.assertLessEqual(result["diversification"]["income_change_vs_without_holding_limits_usd"], 0)
        self.assertEqual(result["baseline"]["status"], "fail")
        report = deterministic_report(result)
        self.assertIn("not a fallback", report)
        self.assertIn("US government debt", report)
        self.assertIn("Spreading the investment", render_html(result, build_figures(result, self.prices)[0]))

    def test_cent_rounding_cannot_bypass_holding_limits(self):
        policy = ClientPolicy(budget_usd=100000.03, minimum_cash_usd=25000.01)
        result = self.run_case(policy)
        selected = result["selected"]
        self.assertIsNotNone(selected)
        self.assertEqual(round(sum(selected["amounts_usd"].values()), 2), policy.budget_usd)
        for ticker, weight in selected["weights"].items():
            if ticker != "CASH" and weight:
                self.assertGreaterEqual(weight * 100 + 1e-8, policy.minimum_holding_pct)
                self.assertLessEqual(weight * 100, policy.maximum_holding_pct + 1e-8)

    def test_cash_above_holding_cap_is_allowed_when_reserve_requires_it(self):
        result = self.run_case(ClientPolicy(minimum_cash_usd=50000))
        self.assertEqual(result["status"], "feasible")
        self.assertGreaterEqual(result["selected"]["amounts_usd"]["CASH"], 50000)
        self.assertEqual(result["selected"]["metrics"]["investment_holdings"], 3)


if __name__ == "__main__":
    unittest.main()
