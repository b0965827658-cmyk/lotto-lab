import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server
import shadow_integration
import shadow_runners


def rows(count=80):
    return [
        {"period": str(12000-index), "date": f"2026-07-{(index % 28)+1:02d}",
         "numbers": sorted({((index*5+offset*7) % 39)+1 for offset in range(5)})}
        for index in range(count)
    ]


def current(period="12001"):
    history = rows()
    return {
        "latest": {"period": period, "date": "2026-08-06", "numbers": [1, 2, 3, 4, 5]},
        "history": history,
        "analysis": {"modelVersion": "fixture", "modelWeights": server._formal_default_weights("tw539")},
        "dataStatus": {"validated": True},
    }


class ShadowRunnerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = shadow_integration.load_candidate_config()

    def test_registry_is_ordered_and_tw539_only(self):
        runners = shadow_runners.get_shadow_runners("tw539", True)
        self.assertEqual(runners, (shadow_runners.run_candidate_a, shadow_runners.run_baseline))
        self.assertEqual(shadow_runners.get_shadow_runners("ca-fantasy5", True), ())
        self.assertEqual(shadow_runners.get_shadow_runners("tw539", False), ())

    def test_candidate_and_baseline_really_execute(self):
        payload = current()
        context = shadow_runners.build_analysis_context("tw539", payload)
        candidate = shadow_runners.run_candidate_a(context, payload, self.config)
        baseline = shadow_runners.run_baseline(context, payload)
        self.assertEqual(candidate.status, "completed")
        self.assertEqual(len(candidate.top15), 15)
        self.assertEqual(baseline.top15, tuple(range(1, 16)))

    def test_frozen_config_and_hash(self):
        self.assertEqual(self.config["definition_sha256"], shadow_integration.EXPECTED_CONFIG_HASH)
        with self.assertRaises(TypeError):
            self.config["version"] = "changed"
        with self.assertRaises(TypeError):
            self.config["removed_features"][0] = "changed"

    def test_shared_context_draw_and_sha(self):
        payload = current()
        context = shadow_runners.build_analysis_context("tw539", payload)
        candidate = shadow_runners.run_candidate_a(context, payload, self.config)
        baseline = shadow_runners.run_baseline(context, payload)
        self.assertEqual(candidate.draw_id, baseline.draw_id)
        expected = hashlib.sha256(json.dumps(payload["history"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(context.dataset_sha256, expected)

    def test_candidate_does_not_mutate_current(self):
        payload = current()
        before = json.dumps(payload, sort_keys=True)
        context = shadow_runners.build_analysis_context("tw539", payload)
        shadow_runners.run_candidate_a(context, payload, self.config)
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_baseline_does_not_mutate_current(self):
        payload = current()
        before = deepcopy(payload)
        context = shadow_runners.build_analysis_context("tw539", payload)
        shadow_runners.run_baseline(context, payload)
        self.assertEqual(payload, before)

    def test_runners_create_no_threads_or_processes(self):
        payload = current()
        context = shadow_runners.build_analysis_context("tw539", payload)
        before = {thread.ident for thread in threading.enumerate()}
        with mock.patch("subprocess.Popen") as popen:
            shadow_runners.run_candidate_a(context, payload, self.config)
            shadow_runners.run_baseline(context, payload)
        self.assertEqual(before, {thread.ident for thread in threading.enumerate()})
        popen.assert_not_called()

    def test_real_tail_is_sequential_and_atomic(self):
        payload = current()
        order = []
        def candidate(context, result, config):
            order.append("candidate")
            return shadow_runners.run_candidate_a(context, result, config)
        def baseline(context, result):
            order.append("baseline")
            return shadow_runners.run_baseline(context, result)
        with tempfile.TemporaryDirectory() as directory:
            outcome = shadow_integration.run_shadow_tail(
                game="tw539", current_result=payload, current_completed_at="2026-08-06T00:00:00+00:00",
                candidate_runner=candidate, baseline_runner=baseline,
                environ={"LOTTO_PERSISTENT_DATA_DIR": directory},
            )
            self.assertEqual(order, ["candidate", "baseline"])
            self.assertTrue(Path(outcome["path"]).is_file())

    def test_candidate_failure_continues_baseline(self):
        payload = current("fail-candidate")
        ran = []
        with tempfile.TemporaryDirectory() as directory:
            outcome = shadow_integration.run_shadow_tail(
                game="tw539", current_result=payload, current_completed_at="now",
                candidate_runner=lambda *_: (_ for _ in ()).throw(RuntimeError("candidate")),
                baseline_runner=lambda context, result: (ran.append(True), shadow_runners.run_baseline(context, result))[1],
                environ={"LOTTO_PERSISTENT_DATA_DIR": directory},
            )
        self.assertTrue(ran)
        self.assertEqual(outcome["record"]["candidate_status"], "failed")
        self.assertEqual(outcome["record"]["baseline_status"], "completed")

    def test_baseline_failure_preserves_candidate(self):
        payload = current("fail-baseline")
        with tempfile.TemporaryDirectory() as directory:
            outcome = shadow_integration.run_shadow_tail(
                game="tw539", current_result=payload, current_completed_at="now",
                candidate_runner=shadow_runners.run_candidate_a,
                baseline_runner=lambda *_: (_ for _ in ()).throw(RuntimeError("baseline")),
                environ={"LOTTO_PERSISTENT_DATA_DIR": directory},
            )
        self.assertEqual(outcome["record"]["candidate_status"], "completed")
        self.assertEqual(outcome["record"]["baseline_status"], "failed")

    def test_restart_dedup(self):
        payload = current("dedup")
        with tempfile.TemporaryDirectory() as directory:
            args = dict(game="tw539", current_result=payload, current_completed_at="now",
                        candidate_runner=shadow_runners.run_candidate_a, baseline_runner=shadow_runners.run_baseline,
                        environ={"LOTTO_PERSISTENT_DATA_DIR": directory})
            first = shadow_integration.run_shadow_tail(**args)
            second = shadow_integration.run_shadow_tail(**args)
        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])

    def test_tw539_only(self):
        payload = current()
        context = shadow_runners.build_analysis_context("ca-fantasy5", payload)
        with self.assertRaises(ValueError):
            shadow_runners.run_candidate_a(context, payload, self.config)
        with self.assertRaises(ValueError):
            shadow_runners.run_baseline(context, payload)

    def test_twenty_bounded_jobs_do_not_accumulate_global_results(self):
        payload = current()
        context = shadow_runners.build_analysis_context("tw539", payload)
        for _ in range(20):
            self.assertEqual(shadow_runners.run_candidate_a(context, payload, self.config).status, "completed")
            self.assertEqual(shadow_runners.run_baseline(context, payload).status, "completed")


if __name__ == "__main__":
    unittest.main()
