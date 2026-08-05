import hashlib
import importlib
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
import shadow_integration as shadow


def payload(game="tw539", period="draw-1", validated=True):
    return {
        "latest": {"period": period, "date": "2026-08-06", "numbers": [1, 2, 3, 4, 5]},
        "history": [],
        "analysis": {"ranking": [{"number": number} for number in range(1, 40)]},
        "dataStatus": {"validated": validated},
    }


class GateS3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = dict(os.environ)
        self.old_candidate = server.shadow_candidate_runner
        self.old_baseline = server.shadow_baseline_runner
        os.environ["LOTTO_PERSISTENT_DATA_DIR"] = self.temp.name
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "false"
        with server.analysis_job_lock:
            server.analysis_jobs.clear()
            server.analysis_job_keys.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        server.shadow_candidate_runner = self.old_candidate
        server.shadow_baseline_runner = self.old_baseline
        self.temp.cleanup()

    def make_job(self, game="tw539", loader=None):
        loader = loader or (lambda *_args, **_kwargs: payload(game))
        job, _ = server.start_analysis_job(game, 10, loader=loader)
        return job["job_id"]

    def wait_completed(self, job_id, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = server.get_analysis_job(job_id)
            if job and job["status"] == "completed":
                return job
            time.sleep(0.005)
        self.fail("Current did not complete")

    def test_flag_false_is_zero_shadow_behavior(self):
        before_modules = set(sys.modules)
        with mock.patch("builtins.__import__", wraps=__import__) as importer:
            job_id = self.make_job()
            job = self.wait_completed(job_id)
            server.analysis_work_queue.join()
        self.assertEqual(job["result"]["latest"]["period"], "draw-1")
        self.assertFalse((Path(self.temp.name) / "shadow").exists())
        self.assertFalse(any(call.args and call.args[0] == "shadow_integration" for call in importer.mock_calls))
        self.assertEqual(before_modules, set(sys.modules))

    def test_current_is_published_before_sleeping_shadow_finishes(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        started = threading.Event()
        release = threading.Event()
        server.shadow_candidate_runner = lambda *_: (started.set(), release.wait(2), list(range(1, 40)))[2]
        server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
        job_id = self.make_job()
        self.assertTrue(started.wait(1))
        published_at = time.time()
        current = self.wait_completed(job_id)
        self.assertIsNotNone(current["result"])
        self.assertLess(time.time() - published_at, 0.2)
        with server.analysis_job_lock:
            self.assertIsNone(server.analysis_jobs[job_id].get("shadow_completed_at"))
        release.set()
        server.analysis_work_queue.join()
        with server.analysis_job_lock:
            final = dict(server.analysis_jobs[job_id])
        self.assertLess(final["current_completed_at"], final["shadow_completed_at"])
        self.assertEqual(final["status"], "completed")

    def test_tw539_and_fantasy_timelines_publish_early(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        for game in ("tw539", "ca-fantasy5"):
            release = threading.Event()
            server.shadow_candidate_runner = lambda *_args, e=release: (e.wait(2), list(range(1, 40)))[1]
            server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
            job_id = self.make_job(game, lambda *_args, g=game, **_kwargs: payload(g, f"{g}-draw"))
            current = self.wait_completed(job_id)
            api_response_at = time.time()
            self.assertEqual(current["status"], "completed")
            release.set()
            server.analysis_work_queue.join()
            with server.analysis_job_lock:
                final = dict(server.analysis_jobs[job_id])
            self.assertLess(datetime_epoch(final["current_completed_at"]), api_response_at)
            self.assertLess(api_response_at, datetime_epoch(final["shadow_completed_at"]) + 0.05)

    def test_shadow_failures_never_change_current(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        failures = [
            RuntimeError("candidate"),
            ValueError("candidate"),
        ]
        for index, failure in enumerate(failures):
            server.shadow_candidate_runner = lambda *_args, e=failure: (_ for _ in ()).throw(e)
            server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
            job_id = self.make_job(loader=lambda *_args, i=index, **_kwargs: payload(period=f"failure-{i}"))
            server.analysis_work_queue.join()
            with server.analysis_job_lock:
                job = dict(server.analysis_jobs[job_id])
            self.assertEqual(job["status"], "completed")
            self.assertIsNotNone(job["result"])
            self.assertIsNotNone(job.get("shadow_failed_at"))

    def test_baseline_and_journal_failures_are_isolated(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        server.shadow_candidate_runner = lambda *_: list(range(1, 40))
        server.shadow_baseline_runner = lambda *_: (_ for _ in ()).throw(RuntimeError("baseline"))
        first = self.make_job(loader=lambda *_args, **_kwargs: payload(period="baseline-fail"))
        server.analysis_work_queue.join()
        with server.analysis_job_lock:
            self.assertEqual(server.analysis_jobs[first]["status"], "completed")
        server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
        with mock.patch.object(shadow.ShadowJournal, "record", side_effect=OSError("write")):
            second = self.make_job(loader=lambda *_args, **_kwargs: payload(period="journal-fail"))
            server.analysis_work_queue.join()
        with server.analysis_job_lock:
            self.assertEqual(server.analysis_jobs[second]["status"], "completed")

    def test_hash_mismatch_and_missing_path_are_isolated(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        server.shadow_candidate_runner = lambda *_: list(range(1, 40))
        server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
        with mock.patch.object(shadow, "load_candidate_config", side_effect=ValueError("hash")):
            first = self.make_job(loader=lambda *_args, **_kwargs: payload(period="hash"))
            server.analysis_work_queue.join()
        os.environ.pop("LOTTO_PERSISTENT_DATA_DIR")
        second = self.make_job(loader=lambda *_args, **_kwargs: payload(period="path"))
        server.analysis_work_queue.join()
        with server.analysis_job_lock:
            self.assertEqual(server.analysis_jobs[first]["status"], "completed")
            self.assertEqual(server.analysis_jobs[second]["status"], "completed")

    def test_same_job_key_deduplicates_and_worker_stays_single(self):
        gate = threading.Event()
        loader = lambda *_args, **_kwargs: (gate.wait(1), payload())[1]
        first, status1 = server.start_analysis_job("tw539", 10, loader=loader)
        second, status2 = server.start_analysis_job("tw539", 10, loader=loader)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual((status1, status2), (202, 202))
        gate.set()
        server.analysis_work_queue.join()
        self.assertTrue(server.analysis_worker_started)

    def test_new_job_waits_for_shadow_tail(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        release = threading.Event()
        second_started = threading.Event()
        server.shadow_candidate_runner = lambda *_: (release.wait(2), list(range(1, 40)))[1]
        server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
        first = self.make_job(loader=lambda *_args, **_kwargs: payload(period="first"))
        self.wait_completed(first)
        second = self.make_job("ca-fantasy5", lambda *_args, **_kwargs: (second_started.set(), payload("ca-fantasy5", "second"))[1])
        time.sleep(0.05)
        self.assertFalse(second_started.is_set())
        release.set()
        server.analysis_work_queue.join()
        self.assertTrue(second_started.is_set())
        with server.analysis_job_lock:
            self.assertEqual(server.analysis_jobs[second]["status"], "completed")

    def test_journal_dedup_restart_invalid_and_integrity(self):
        path = Path(self.temp.name) / "shadow" / shadow.JOURNAL_NAME
        journal = shadow.ShadowJournal(path)
        record = base_record()
        one, inserted1 = journal.record(record)
        two, inserted2 = shadow.ShadowJournal(path).record(record)
        self.assertTrue(inserted1)
        self.assertFalse(inserted2)
        self.assertEqual(one, two)
        invalid = base_record("draw-invalid")
        invalid["status"] = "invalid"
        journal.record(invalid)
        with self.assertRaises(ValueError):
            journal.settle(shadow.ShadowJournal.key("tw539", "draw-invalid"), [1, 2, 3, 4, 5], "now")
        data = json.loads(path.read_text(encoding="utf-8"))
        key = shadow.ShadowJournal.key("tw539", "draw-1")
        data["records"][key]["prediction"]["top5"] = [9, 8, 7, 6, 5]
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            journal.settle(key, [1, 2, 3, 4, 5], "now")

    def test_corrupt_journal_isolated(self):
        path = Path(self.temp.name) / "shadow" / shadow.JOURNAL_NAME
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            shadow.ShadowJournal(path).record(base_record())
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_config_hash_and_removed_features_are_frozen(self):
        config = shadow.load_candidate_config()
        self.assertEqual(config["definition_sha256"], shadow.EXPECTED_CONFIG_HASH)
        self.assertEqual(config["removed_features"], ["recent30", "oddBalance", "sizeBalance", "previousRepeat", "primeBalance"])

    def test_current_fixture_schema_warm_cache_and_production_journal_are_unchanged(self):
        current = payload()
        fixture_before = stable_hash(current)
        warm = Path(self.temp.name) / "analysis_warm_cache.json"
        production_journal = Path(self.temp.name) / "prediction_journal_v3_tw539.json"
        warm.write_bytes(b'{"current":"warm"}')
        production_journal.write_bytes(b'{"current":"journal"}')
        sources_before = (hashlib.sha256(warm.read_bytes()).hexdigest(), hashlib.sha256(production_journal.read_bytes()).hexdigest())
        job_id = self.make_job(loader=lambda *_args, **_kwargs: deepcopy(current))
        server.analysis_work_queue.join()
        with server.analysis_job_lock:
            internal = dict(server.analysis_jobs[job_id])
        delivered = server.analysis_get_response(server._analysis_job_response(internal))
        fixture_after = stable_hash({key: delivered[key] for key in ("latest", "history", "analysis", "dataStatus")})
        expected_schema = {"ok", "game", "updatedAt", "latest", "history", "analysis", "dataStatus", "status", "completed", "cached", "stale", "job_id", "retry_after_seconds", "error"}
        self.assertEqual(fixture_before, fixture_after)
        self.assertEqual(set(delivered), expected_schema)
        self.assertEqual(sources_before, (hashlib.sha256(warm.read_bytes()).hexdigest(), hashlib.sha256(production_journal.read_bytes()).hexdigest()))

    def test_flag_true_writes_only_separate_shadow_journal(self):
        os.environ["SHADOW_CANDIDATE_A_ENABLED"] = "true"
        server.shadow_candidate_runner = lambda *_: list(range(1, 40))
        server.shadow_baseline_runner = lambda *_: list(range(39, 0, -1))
        production_journal = Path(self.temp.name) / "prediction_journal_v3_tw539.json"
        production_journal.write_bytes(b"production")
        before = hashlib.sha256(production_journal.read_bytes()).hexdigest()
        job_id = self.make_job(loader=lambda *_args, **_kwargs: payload(period="separate"))
        server.analysis_work_queue.join()
        self.assertEqual(before, hashlib.sha256(production_journal.read_bytes()).hexdigest())
        self.assertTrue((Path(self.temp.name) / "shadow" / shadow.JOURNAL_NAME).is_file())
        with server.analysis_job_lock:
            self.assertEqual(server.analysis_jobs[job_id]["status"], "completed")


def datetime_epoch(value):
    from datetime import datetime
    return datetime.fromisoformat(value).timestamp()


def base_record(draw_id="draw-1"):
    prediction = {"top5": [1, 2, 3, 4, 5], "top10": list(range(1, 11)), "top15": list(range(1, 16))}
    return {
        "lottery": "tw539", "draw_id": draw_id, "candidate_version": shadow.CANDIDATE_VERSION,
        "candidate_config_sha256": shadow.EXPECTED_CONFIG_HASH, "prediction": prediction,
        "prediction_hash": shadow.canonical_hash(prediction), "baseline_prediction": prediction,
        "created_at": "2026-08-06T00:00:00+00:00", "current_completed_at": "2026-08-06T00:00:00+00:00",
        "shadow_started_at": "2026-08-06T00:00:00+00:00", "shadow_completed_at": "2026-08-06T00:00:01+00:00",
        "status": "locked", "invalid_reason": None, "actual": None, "candidate_hits": None,
        "baseline_hits": None, "settled_at": None,
    }


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


if __name__ == "__main__":
    unittest.main()
