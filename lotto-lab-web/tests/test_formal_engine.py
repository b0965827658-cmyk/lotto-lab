import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"
HISTORY_FILE = ROOT / "public" / "taiwan_539_history.json"

spec = importlib.util.spec_from_file_location("lotto_lab_server", SERVER)
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
assert spec.loader is not None
spec.loader.exec_module(server)


def synthetic_california_rows(count=420):
    rows = []
    for index in range(count):
        month = (index // 28) % 12 + 1
        day = index % 28 + 1
        numbers = [((index * 7 + step * 5) % 39) + 1 for step in range(5)]
        rows.append(
            {
                "game": "ca-fantasy5",
                "period": str(100000 + index),
                "date": f"2024-{month:02d}-{day:02d}",
                "numbers": numbers,
            }
        )
    return rows


class FormalEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tw_rows = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))[:1000]
        cls.ca_rows = synthetic_california_rows()

    def test_games_have_independent_models_and_timezones(self):
        tw_models, _, _ = server._formal_scores("tw539", self.tw_rows)
        ca_models, _, ca_meta = server._formal_scores("ca-fantasy5", self.ca_rows)
        self.assertEqual(set(tw_models), set(server.FORMAL_MODEL_NAMES["tw539"]))
        self.assertEqual(set(ca_models), set(server.FORMAL_MODEL_NAMES["ca-fantasy5"]))
        self.assertNotEqual(set(tw_models), set(ca_models))
        self.assertEqual(server._formal_timezone("tw539"), "Asia/Taipei")
        self.assertEqual(server._formal_timezone("ca-fantasy5"), "America/Los_Angeles")
        self.assertIn(ca_meta["caFeatures"]["currentWeekday"], {str(index) for index in range(7)})

    def test_walkforward_has_baselines_and_no_future_target(self):
        result = server._formal_walkforward(self.tw_rows, "tw539", target_limit=12)
        self.assertEqual(result["testedCount"], 12)
        self.assertEqual(result["trainWindow"], 300)
        self.assertIn("random-expected", result["baselineModels"])
        self.assertIn("baselineComparison", result)
        self.assertTrue(all(len(row["candidate15"]) == 15 for row in result["recentRows"]))

    def test_quality_report_rejects_bad_rows_without_guessing(self):
        bad_rows = self.tw_rows[:2] + [{"game": "tw539", "period": "bad", "date": "2024-01-01", "numbers": [1, 1, 2, 3, 4]}]
        report = server._formal_quality_report("tw539", bad_rows)
        self.assertEqual(report["invalidCount"], 1)
        self.assertTrue(report["anomalies"])

    def test_formal_output_has_auditable_tiers(self):
        result = server._formal_analysis("tw539", self.tw_rows, 39, 5)
        self.assertEqual(len(result["candidateTiers"]["top5"]), 5)
        self.assertEqual(len(result["candidateTiers"]["full15"]), 15)
        self.assertEqual(len(result["frequency"]), 39)
        self.assertEqual(result["statistics"]["hot"], [row["number"] for row in result["hot"]])
        self.assertEqual(result["statistics"]["cold"], [row["number"] for row in result["cold"]])
        self.assertEqual(result["statisticsWindow"], 300)
        self.assertEqual(set(result["modelWeights"]), set(server.FORMAL_MODEL_NAMES["tw539"]))
        self.assertIn("calibration", result)
        self.assertIn("uncertainty", result)
        self.assertIn("ablation", result)
        self.assertFalse(result["appIntegration"]["enabled"])

    def test_insufficient_california_data_never_falls_back_to_legacy_picks(self):
        result = server._formal_analysis("ca-fantasy5", self.ca_rows[:24], 39, 5)
        self.assertTrue(result["dataInsufficient"])
        self.assertEqual(result["recommendation"], [])
        self.assertEqual(result["candidateTiers"]["full15"], [])
        self.assertEqual(result["backtest"]["cacheStatus"], "insufficient")
        self.assertEqual(result["modelWeights"], {})


if __name__ == "__main__":
    unittest.main()
