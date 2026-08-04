import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server


class WarmCacheTests(unittest.TestCase):
    def setUp(self):
        self.original_file = server.WARM_CACHE_FILE
        self.directory = tempfile.TemporaryDirectory()
        server.WARM_CACHE_FILE = Path(self.directory.name) / "warm.json"
        with server.warm_cache_lock:
            server.warm_cache_jobs.clear()

    def tearDown(self):
        server.WARM_CACHE_FILE = self.original_file
        self.directory.cleanup()

    @staticmethod
    def payload(number):
        return {
            "latest": {"date": "2026-08-04", "period": "p1", "numbers": [1, 2, 3, 4, 5]},
            "history": [],
            "analysis": {"drawCount": 5000, "candidateTiers": {"top5": [number] * 5}},
        }

    def test_completed_warm_cache_is_returned_without_recalculation(self):
        calls = 0

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            return self.payload(7)

        server.build_warm_cache("tw539", 90, "tw539:5000:2026-08-04:p1", loader)
        entry = server.get_warm_analysis("tw539", 90)
        self.assertEqual([7] * 5, entry["result"]["analysis"]["candidateTiers"]["top5"])
        self.assertEqual(1, calls)
        response, status = server.start_analysis_job("tw539", 90)
        self.assertEqual(200, status)
        self.assertEqual("completed", response["status"])
        self.assertTrue(response["cached"])
        self.assertEqual([7] * 5, response["result"]["analysis"]["candidateTiers"]["top5"])

    def test_failed_rebuild_keeps_previous_good_cache(self):
        server.build_warm_cache("tw539", 90, "old", lambda *args, **kwargs: self.payload(8))

        def failing(*args, **kwargs):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            server.build_warm_cache("tw539", 90, "new", failing)
        entry = server.get_warm_analysis("tw539", 90)
        self.assertEqual("old", entry["repositorySignature"])
        self.assertEqual([8] * 5, entry["result"]["analysis"]["candidateTiers"]["top5"])

    def test_same_repository_version_starts_only_one_background_build(self):
        release = threading.Event()
        calls = 0

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            release.wait(1)
            return self.payload(9)

        first = server.start_warm_cache("ca-fantasy5", "sig", (90,), loader)
        second = server.start_warm_cache("ca-fantasy5", "sig", (90,), loader)
        release.set()
        deadline = time.time() + 2
        while server.warm_cache_jobs and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, calls)

    def test_one_canonical_analysis_populates_smaller_app_variant(self):
        calls = 0

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            payload = self.payload(4)
            payload["history"] = list(range(limit))
            payload["analysis"]["metadata"] = {"analysisLimit": limit}
            return payload

        server._run_warm_cache("tw539", "sig", (90, 10), loader)
        small = server.get_warm_analysis("tw539", 10)
        self.assertEqual(1, calls)
        self.assertEqual(10, len(small["result"]["history"]))
        self.assertEqual(10, small["result"]["analysis"]["metadata"]["analysisLimit"])

    def test_tw539_finishes_before_fantasy5_starts(self):
        calls = []
        active = 0
        peak_active = 0
        active_lock = threading.Lock()

        def loader(game, limit, optimize=False):
            nonlocal active, peak_active
            with active_lock:
                active += 1
                peak_active = max(peak_active, active)
            calls.append(game)
            time.sleep(0.02)
            with active_lock:
                active -= 1
            return self.payload(6)

        self.assertTrue(server.start_warm_cache("tw539", "tw-sig", (90,), loader))
        self.assertTrue(server.start_warm_cache("ca-fantasy5", "ca-sig", (90,), loader))
        deadline = time.time() + 2
        while server.warm_cache_jobs and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(["tw539", "ca-fantasy5"], calls)
        self.assertEqual(1, peak_active)

    def test_cache_file_is_valid_json(self):
        server.build_warm_cache("ca-fantasy5", 10, "sig", lambda *args, **kwargs: self.payload(3))
        document = json.loads(server.WARM_CACHE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(server.WARM_CACHE_SCHEMA_VERSION, document["schemaVersion"])


if __name__ == "__main__":
    unittest.main()
