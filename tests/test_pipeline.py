import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

import main
from yfinance_source import normalize_prices, prepare_market_data, summarize_prices


def download_fixture(values=(100.0, 80.0, 90.0), ticker="TEST"):
    return pd.DataFrame(
        {("Close", ticker): values},
        index=pd.bdate_range("2024-01-01", periods=len(values)),
    )


class MarketDataTests(unittest.TestCase):
    def test_drawdown_includes_first_observation_and_short_windows_are_null(self):
        prices = normalize_prices(download_fixture(), ["TEST"])
        result = summarize_prices(prices, {"tickers": ["TEST"]})["instruments"][0]
        self.assertAlmostEqual(result["max_drawdown_full_history_pct"], -20.0)
        self.assertIsNone(result["return_63_sessions_pct"])
        self.assertIsNone(result["annualized_volatility_last_252_sessions_pct"])

    def test_known_compound_returns_and_volatility(self):
        values = [100 * (1.001 ** i) for i in range(253)]
        prices = normalize_prices(download_fixture(values), ["TEST"])
        result = summarize_prices(prices, {"tickers": ["TEST"]})["instruments"][0]
        self.assertAlmostEqual(result["return_252_sessions_pct"], (1.001 ** 252 - 1) * 100, places=3)
        self.assertAlmostEqual(result["return_63_sessions_pct"], (1.001 ** 63 - 1) * 100, places=3)
        self.assertEqual(result["annualized_volatility_last_252_sessions_pct"], 0.0)
        self.assertEqual(result["max_drawdown_full_history_pct"], 0.0)

    def test_volatility_uses_daily_returns_and_percentage_units(self):
        values = [100.0]
        for daily_return in [0.01, -0.01] * 126:
            values.append(values[-1] * (1 + daily_return))
        prices = normalize_prices(download_fixture(values), ["TEST"])
        result = summarize_prices(prices, {"tickers": ["TEST"]})["instruments"][0]
        expected = math.sqrt(252 * 0.01 ** 2 / 251) * math.sqrt(252) * 100
        self.assertAlmostEqual(result["annualized_volatility_last_252_sessions_pct"], expected, places=3)

    def test_single_ticker_flat_columns(self):
        flat = pd.DataFrame({"Close": [100, 101]}, index=pd.bdate_range("2024-01-01", periods=2))
        self.assertEqual(normalize_prices(flat, ["TEST"])["adjusted_close"].tolist(), [100, 101])

    def test_empty_partial_missing_and_invalid_data_are_rejected(self):
        bad_downloads = [pd.DataFrame(), download_fixture(), download_fixture([100, float("nan"), 101]), download_fixture([100, -1, 101])]
        requests = [["TEST"], ["TEST", "MISSING"], ["TEST"], ["TEST"]]
        for frame, tickers in zip(bad_downloads, requests):
            with self.subTest(tickers=tickers):
                with self.assertRaises(ValueError):
                    normalize_prices(frame, tickers)

    @patch("yfinance_source.yf.download", return_value=download_fixture())
    @patch("yfinance_source.yf.set_tz_cache_location")
    def test_cache_roundtrip_and_request_mismatch(self, cache_location, download):
        with tempfile.TemporaryDirectory() as folder:
            kwargs = {"tickers": ["test"], "start": "2024-01-01", "end": "2024-02-01", "data_dir": folder}
            original = prepare_market_data(**kwargs)
            cached = prepare_market_data(**kwargs)
            self.assertEqual(original, cached)
            download.assert_called_once()
            self.assertTrue((Path(folder) / "prices.csv").exists())
            self.assertEqual(json.loads((Path(folder) / "summary.json").read_text()), cached)
            with self.assertRaisesRegex(ValueError, "Cached symbols or dates differ"):
                prepare_market_data(**{**kwargs, "start": "2023-01-01"})
            download.return_value = pd.DataFrame()
            with self.assertRaises(ValueError):
                prepare_market_data(**kwargs, refresh=True)
            self.assertEqual(prepare_market_data(**kwargs), original)


class ModelConnectionTests(unittest.TestCase):
    @patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "unit-test-only"})
    @patch("main.AzureOpenAI")
    def test_evidence_and_usd_budget_reach_model(self, azure):
        client = MagicMock()
        azure.return_value.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Research comparison"))
        ])
        payload = main.build_payload({"instruments": [{"ticker": "TEST"}]})
        self.assertEqual(main.analyze(payload), "Research comparison")
        call = client.chat.completions.create.call_args.kwargs
        sent = json.loads(call["messages"][1]["content"])
        self.assertEqual(sent["client"]["budget"], 100_000)
        self.assertEqual(sent["client"]["currency"], "USD")
        self.assertIsNone(sent["client"]["risk_limits"])
        self.assertEqual(sent["market_evidence"]["instruments"][0]["ticker"], "TEST")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credential_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "AZURE_OPENAI_API_KEY"):
            main.analyze({})

    @patch.dict(os.environ, {}, clear=True)
    @patch("main.AzureOpenAI")
    def test_session_key_can_be_used_without_an_environment_key(self, azure):
        client = azure.return_value.__enter__.return_value
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Session explanation"))
        ])
        payload = main.build_payload({"instruments": []})
        self.assertEqual(main.analyze(payload, api_key=" session-test-only "), "Session explanation")
        self.assertEqual(azure.call_args.kwargs["api_key"], "session-test-only")
        self.assertNotIn("session-test-only", json.dumps(payload))

    @patch("main.analyze")
    @patch("main.prepare_market_data")
    def test_data_only_saves_input_without_ai(self, prepare, analyze):
        from test_portfolio import fixture
        prices, metadata, _ = fixture()
        prepare.return_value = {"source": metadata, "instruments": []}
        with tempfile.TemporaryDirectory() as folder:
            prices.to_csv(Path(folder) / "prices.csv", index=False)
            with patch("sys.argv", ["main.py", "--data-only", "--data-dir", folder]), patch("builtins.print"):
                main.main()
            analyze.assert_not_called()
            saved = json.loads((Path(folder) / "model_input.json").read_text())
            self.assertEqual(saved["client"]["budget_usd"], 100_000)
            self.assertIn("selected", saved["validation_result"])
            self.assertTrue((Path(folder) / "report.html").exists())
            self.assertTrue((Path(folder) / "candidate_allocations.csv").exists())


if __name__ == "__main__":
    unittest.main()
