import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from test_portfolio import fixture


class DashboardTests(unittest.TestCase):
    def test_recalculating_reserve_and_impossible_claim_updates_results(self):
        prices, metadata, facts = fixture()
        today = datetime.now(timezone.utc).date()
        dates = pd.bdate_range(end=pd.Timestamp(today) - pd.Timedelta(days=1), periods=300)
        date_map = dict(zip(sorted(prices["date"].unique()), dates.strftime("%Y-%m-%d")))
        prices["date"] = prices["date"].map(date_map)
        metadata["fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
        for fact in facts["funds"].values():
            for field in ("identity_as_of", "duration_as_of", "yield_as_of", "spread_as_of"):
                fact[field] = today.isoformat()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prices.to_csv(root / "prices.csv", index=False)
            (root / "metadata.json").write_text(json.dumps(metadata))
            facts_path = root / "facts.json"
            facts_path.write_text(json.dumps(facts))
            with patch.dict(os.environ, {"MSHACK_DATA_DIR": folder, "MSHACK_FACTS_PATH": str(facts_path), "AZURE_OPENAI_API_KEY": ""}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=45)
                self.assertEqual(list(app.exception), [])
                self.assertEqual(list(app.error), [])
                self.assertEqual(app.metric[0].value, "$25,000")
                self.assertTrue(next(b for b in app.button if b.label == "Generate AI explanation").disabled)
                app.text_input(key="azure_api_key").set_value("session-ui-test-only").run(timeout=45)
                self.assertEqual(list(app.exception), [])
                self.assertFalse(next(b for b in app.button if b.label == "Generate AI explanation").disabled)
                self.assertNotIn("session-ui-test-only", (root / "model_input.json").read_text())
                explanation = "Cash meets the **$25,000** minimum. **$98,653** remains for a \\$40,000 claim."
                with patch("main.analyze", return_value=explanation):
                    next(b for b in app.button if b.label == "Generate AI explanation").click().run(timeout=45)
                self.assertEqual(list(app.exception), [])
                self.assertTrue(any(
                    item.value == "Cash meets the **\\$25,000** minimum. **\\$98,653** remains for a \\$40,000 claim."
                    for item in app.markdown
                ))
                self.assertEqual((root / "analysis.md").read_text(), explanation)
                initial = json.loads((root / "validation.json").read_text())
                self.assertEqual(initial["selected"]["metrics"]["investment_holdings"], 3)
                self.assertLessEqual(initial["selected"]["metrics"]["largest_holding_pct"], 35 + 1e-8)
                self.assertLessEqual(initial["selected"]["amounts_usd"]["SHY"], 35000)
                app.number_input(key="reserve").set_value(40000)
                next(b for b in app.button if b.label == "Recalculate allocation").click().run(timeout=45)
                self.assertEqual(list(app.exception), [])
                self.assertEqual(app.metric[0].value, "$40,000")
                self.assertFalse(any("Cash meets the" in item.value for item in app.markdown))
                saved = json.loads((root / "validation.json").read_text())
                self.assertEqual(saved["policy"]["minimum_cash_usd"], 40000)
                self.assertEqual(saved["selected"]["amounts_usd"]["CASH"], 40000)
                app.number_input(key="claim").set_value(150000)
                next(b for b in app.button if b.label == "Recalculate allocation").click().run(timeout=45)
                self.assertEqual(list(app.exception), [])
                self.assertTrue(any("No feasible allocation" in e.value for e in app.error))
                saved = json.loads((root / "validation.json").read_text())
                self.assertEqual(saved["status"], "infeasible")
                self.assertIsNone(saved["selected"])
                self.assertEqual((root / "analysis.md").read_text(), explanation)


if __name__ == "__main__":
    unittest.main()
