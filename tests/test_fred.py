import copy
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from fred_source import SERIES, _observations, attach_rate_analysis, prepare_rate_data, summarize_rates
from main import build_payload, run_pipeline
from portfolio import ClientPolicy, run_validation
from reporting import build_figures, deterministic_report, render_html
from test_portfolio import TODAY, fixture


def rate_fixture():
    # Both rises and falls, with a genuinely missing daily observation.
    rows = [{"date": "2025-10-22", "value": 4.0},
            {"date": "2026-09-22", "value": 4.5},
            {"date": "2026-10-21", "value": None},
            {"date": "2026-10-22", "value": 4.25}]
    return {"schema_version": 1,
            "request": {"start": "2019-01-01", "requested_end": None, "series_ids": list(SERIES)},
            "observation_end": "2026-10-22", "fetched_at_utc": "2026-10-23T10:00:00+00:00",
            "series": {sid: {"observations": copy.deepcopy(rows)} for sid in SERIES}}


class FredSourceTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key_is_actionable(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, "FRED_API_KEY"):
            prepare_rate_data(data_dir=folder)

    @patch("fred_source.urlopen")
    def test_download_cache_dates_units_and_credential_redaction(self, download):
        rows = [{"date": "2026-10-21", "value": "."}, {"date": "2026-10-22", "value": "-0.25"}]
        def response(*args, **kwargs):
            return io.StringIO(json.dumps({"count": len(rows), "observations": rows}))
        download.side_effect = response
        with tempfile.TemporaryDirectory() as folder:
            kwargs = dict(start="2026-10-01", end="2026-10-23", data_dir=folder)
            original = prepare_rate_data(**kwargs, api_key="test-secret-only")
            cached = prepare_rate_data(**kwargs, api_key="")
            self.assertEqual(original, cached)
            self.assertEqual(download.call_count, 4)
            query = parse_qs(urlparse(download.call_args.args[0]).query)
            self.assertEqual(query["observation_end"], ["2026-10-22"])
            self.assertEqual(query["file_type"], ["json"])
            values = cached["series"]["DFF"]["observations"]
            self.assertIsNone(values[0]["value"])
            self.assertEqual(values[1]["value"], -0.25)
            self.assertNotIn("test-secret-only", (Path(folder) / "fred_rates.json").read_text())
            with self.assertRaisesRegex(ValueError, "request differs"):
                prepare_rate_data(**{**kwargs, "start": "2025-01-01"})

    def test_invalid_observations_are_rejected(self):
        for rows in ([], [{"date": "2026-01-01", "value": "."}],
                     [{"date": "2026-01-01", "value": "NaN"}],
                     [{"date": "2026-01-01", "value": True}],
                     [{"date": "2027-01-01", "value": 1}],
                     [{"date": "2026-01-01", "value": 1}] * 2):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                _observations(rows, date(2026, 1, 1), date(2026, 12, 31))

    @patch("fred_source.urlopen")
    def test_failed_refresh_retains_cache_and_hides_request_credentials(self, download):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "fred_rates.json"
            cache.write_text(json.dumps(rate_fixture()))
            original = cache.read_bytes()
            for failure in (HTTPError("https://test/?api_key=secret", 403, "secret", {}, None),
                            URLError("https://test/?api_key=secret"),
                            TimeoutError("secret")):
                download.side_effect = failure
                with self.subTest(failure=type(failure).__name__), self.assertRaises(ValueError) as caught:
                    prepare_rate_data(data_dir=folder, api_key="secret", refresh=True)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(cache.read_bytes(), original)

    @patch("fred_source.urlopen")
    def test_partial_response_cannot_replace_good_cache(self, download):
        download.side_effect = lambda *a, **kw: io.StringIO(json.dumps({
            "count": 2, "observations": [{"date": "2026-10-22", "value": "4"}]}))
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "fred_rates.json"
            cache.write_text(json.dumps(rate_fixture()))
            original = cache.read_bytes()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                prepare_rate_data(data_dir=folder, refresh=True, api_key="secret")
            self.assertEqual(cache.read_bytes(), original)

    @patch("fred_source.urlopen")
    def test_failure_on_later_series_does_not_save_partial_cache(self, download):
        first = io.StringIO(json.dumps({"count": 1, "observations": [{"date": "2026-10-22", "value": "4"}]}))
        download.side_effect = [first, URLError("failure")]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                prepare_rate_data(start="2026-10-01", end="2026-10-23", data_dir=folder, api_key="secret")
            self.assertFalse((Path(folder) / "fred_rates.json").exists())


class FredAnalysisTests(unittest.TestCase):
    def test_changes_are_basis_points_with_dated_endpoints(self):
        result = summarize_rates(rate_fixture(), TODAY)["series"]["DGS2"]
        self.assertEqual(result["latest_rate_pct"], 4.25)
        self.assertEqual(result["latest_date"], "2026-10-22")
        self.assertEqual(result["changes"]["30d"]["change_bps"], -25)
        self.assertEqual(result["changes"]["365d"]["change_bps"], 25)
        self.assertEqual(result["changes"]["30d"]["start"], "2026-09-22")

    def test_stale_latest_rates_remain_visible_but_changes_are_unverified(self):
        series = summarize_rates(rate_fixture(), date(2026, 12, 1))["series"]["DFF"]
        self.assertEqual(series["status"], "unverified")
        self.assertEqual(series["latest_rate_pct"], 4.25)
        self.assertIsNone(series["changes"]["30d"]["change_bps"])

    def test_no_future_observations_or_interpolation(self):
        snapshot = rate_fixture()
        snapshot["series"]["DFF"]["observations"] += [{"date": "2026-10-24", "value": 99}]
        series = summarize_rates(snapshot, TODAY)["series"]["DFF"]
        self.assertEqual(series["latest_rate_pct"], 4.25)
        # The closest earlier date is too far from the target; do not fill it.
        snapshot["series"]["DFF"]["observations"][1]["date"] = "2026-09-01"
        self.assertIsNone(summarize_rates(snapshot, TODAY)["series"]["DFF"]["changes"]["30d"]["change_bps"])

    def test_weekend_target_uses_previous_observation_within_tolerance(self):
        snapshot = rate_fixture()
        snapshot["series"]["DFF"]["observations"][1]["date"] = "2026-09-18"
        change = summarize_rates(snapshot, TODAY)["series"]["DFF"]["changes"]["30d"]
        self.assertEqual(change["start"], "2026-09-18")
        self.assertEqual(change["change_bps"], -25)

    def test_prices_income_checks_and_selection_are_not_modified(self):
        prices, metadata, facts = fixture()
        result = run_validation(prices, metadata, ClientPolicy(), facts, TODAY)
        before = copy.deepcopy(result)
        attach_rate_analysis(result, rate_fixture())
        for name in ("status", "search", "policy", "candidate_audit"):
            self.assertEqual(result[name], before[name])
        for name in ("amounts_usd", "metrics", "checks"):
            self.assertEqual(result["selected"][name], before["selected"][name])
        scenario = result["baseline"]["observed_rate_scenarios"]
        # 75k BIL x duration 0.1 x 25 bps = $18.75; fall gains, rise loses.
        self.assertEqual(scenario[0]["estimated_value_change_usd"], 18.75)
        self.assertEqual(scenario[1]["estimated_value_change_usd"], -18.75)
        self.assertEqual(scenario[0]["holdings"][0]["proxy_series"], "DGS3MO")
        payload = build_payload({"source": metadata}, validation=result)
        self.assertIn("interest_rates", payload["validation_result"])
        self.assertTrue(all("observations" not in series for series in
                            payload["validation_result"]["interest_rates"]["series"].values()))
        self.assertIn("DGS2", deterministic_report(result))
        figures, _ = build_figures(result, prices)
        self.assertIn("Interest rates (FRED)", render_html(result, figures))

    def test_unknown_proxies_missing_duration_and_large_moves_are_unverified(self):
        for ticker, duration, value in (("OTHER", 1, 4.25), ("SHY", None, 4.25), ("SHY", 1.8, 10)):
            candidate = {"amounts_usd": {"CASH": 25000, ticker: 75000}}
            result = {"analysis_date": TODAY.isoformat(), "policy": {"maximum_price_age_days": 7},
                      "fund_evidence": [{"ticker": ticker, "duration_years": duration}], "selected": candidate}
            snapshot = rate_fixture()
            snapshot["series"]["DGS2"]["observations"][-1]["value"] = value
            attach_rate_analysis(result, snapshot)
            self.assertIsNone(candidate["observed_rate_scenarios"][0]["estimated_value_change_usd"])
            self.assertIsNotNone(candidate["observed_rate_scenarios"][0]["holdings"][0]["reason"])

    def test_missing_unused_series_does_not_block_other_proxies(self):
        snapshot = rate_fixture()
        snapshot["series"].pop("DGS10")
        result = {"analysis_date": TODAY.isoformat(), "policy": {"maximum_price_age_days": 7},
                  "fund_evidence": [{"ticker": "SHY", "duration_years": 1.8}],
                  "selected": {"amounts_usd": {"CASH": 25000, "SHY": 75000}}}
        attach_rate_analysis(result, snapshot)
        self.assertEqual(result["selected"]["observed_rate_scenarios"][0]["estimated_value_change_usd"], 337.5)
        self.assertEqual(result["interest_rates"]["series"]["DGS10"]["status"], "unverified")

    def test_cash_only_has_zero_bond_price_effect_even_without_rates(self):
        result = {"analysis_date": TODAY.isoformat(), "policy": {"maximum_price_age_days": 7},
                  "fund_evidence": [], "selected": {"amounts_usd": {"CASH": 100000}}}
        attach_rate_analysis(result, {"series": {}, "fetched_at_utc": "2026-10-23T10:00:00+00:00"})
        self.assertEqual(result["selected"]["observed_rate_scenarios"][0]["estimated_value_change_usd"], 0)

    @patch("main.prepare_market_data")
    @patch("main.prepare_rate_data", return_value=rate_fixture())
    def test_pipeline_exports_rate_evidence_without_ai(self, rates, market):
        prices, metadata, facts = fixture()
        market.return_value = {"source": metadata, "instruments": []}
        with tempfile.TemporaryDirectory() as folder:
            prices.to_csv(Path(folder) / "prices.csv", index=False)
            result, payload = run_pipeline(metadata["tickers"], "2019-01-01", None, folder, False,
                                           ClientPolicy(grid_step_pct=25), facts, fred=True, refresh_fred=True)
            rates.assert_called_once_with(start="2019-01-01", end=None, data_dir=folder, refresh=True)
            saved = json.loads((Path(folder) / "validation.json").read_text())
            self.assertEqual(saved["interest_rates"], result["interest_rates"])
            self.assertEqual(payload["validation_result"]["interest_rates"], saved["interest_rates"])

    @patch("main.run_pipeline")
    def test_refresh_fred_flag_reaches_pipeline_without_requiring_fred_flag(self, pipeline):
        import main
        prices, metadata, facts = fixture()
        validation = run_validation(prices, metadata, ClientPolicy(grid_step_pct=25), facts, TODAY)
        pipeline.return_value = (validation, {})
        with patch("sys.argv", ["main.py", "--data-only", "--refresh-fred"]), patch("builtins.print"):
            main.main()
        self.assertTrue(pipeline.call_args.kwargs["refresh_fred"])
        self.assertFalse(pipeline.call_args.kwargs["fred"])


if __name__ == "__main__":
    unittest.main()
