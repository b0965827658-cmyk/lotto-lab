import json
import importlib.util
import multiprocessing
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import server


def _hold_file_lock(path, ready, release):
    lock = server.AnalysisExecutionFileLock(Path(path))
    acquired = lock.acquire()
    ready.put(acquired)
    release.wait(5)
    lock.release()


def _try_file_lock(path, result):
    lock = server.AnalysisExecutionFileLock(Path(path))
    acquired = lock.acquire()
    result.put(acquired)
    lock.release()


class ProductionAnalysisConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_warm_file = server.WARM_CACHE_FILE
        self.original_lock_file = server.ANALYSIS_EXECUTION_LOCK_FILE
        server.WARM_CACHE_FILE = Path(self.directory.name) / "warm.json"
        server.ANALYSIS_EXECUTION_LOCK_FILE = Path(self.directory.name) / "analysis.lock"
        with server.analysis_job_lock:
            server.analysis_jobs.clear()
            server.analysis_job_keys.clear()
        with server.warm_cache_lock:
            server.warm_cache_jobs.clear()
        self.history = [
            {"game": "test", "period": "p1", "date": "2026-08-04", "numbers": [1, 2, 3, 4, 5]}
        ]

    def tearDown(self):
        server.analysis_work_queue.join()
        server.WARM_CACHE_FILE = self.original_warm_file
        server.ANALYSIS_EXECUTION_LOCK_FILE = self.original_lock_file
        self.directory.cleanup()

    def _wait(self, job_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = server.get_analysis_job(job_id)
            if job and job["status"] != "processing":
                return job
            time.sleep(0.01)
        self.fail(f"job did not finish: {job_id}")

    def _serve(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, f"http://127.0.0.1:{httpd.server_port}"

    @staticmethod
    def _get(url):
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.load(response)

    def test_two_and_ten_tw539_gets_share_one_job(self):
        for count in (2, 10):
            with self.subTest(count=count):
                calls = 0
                release = threading.Event()

                def loader(game, limit, optimize=False):
                    nonlocal calls
                    calls += 1
                    release.wait(2)
                    return {"latest": self.history[0], "history": [], "analysis": {"top5": [1, 2, 3, 4, 5]}}

                with server.analysis_job_lock:
                    server.analysis_jobs.clear()
                    server.analysis_job_keys.clear()
                httpd, base = self._serve()
                try:
                    with patch.object(server, "taiwan_history", return_value=self.history), patch.object(
                        server, "build_payload", side_effect=loader
                    ):
                        with ThreadPoolExecutor(max_workers=count) as pool:
                            futures = [pool.submit(self._get, f"{base}/api/analyze/tw539?limit=180") for _ in range(count)]
                            time.sleep(0.05)
                            release.set()
                            responses = [future.result() for future in futures]
                    ids = {payload["job_id"] for _, payload in responses}
                    self.assertEqual(1, len(ids))
                    self.assertTrue(all(status in (200, 202) for status, _ in responses))
                    self._wait(next(iter(ids)))
                    self.assertEqual(1, calls)
                finally:
                    httpd.shutdown()

    def test_ten_mixed_gets_run_one_core_at_a_time(self):
        active = 0
        peak = 0
        calls = []
        lock = threading.Lock()

        def loader(game, limit, optimize=False):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(game)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {"latest": self.history[0], "history": [], "analysis": {"top5": [1, 2, 3, 4, 5]}}

        httpd, base = self._serve()
        try:
            with patch.object(server, "taiwan_history", return_value=self.history), patch.object(
                server, "california_history", return_value=self.history
            ), patch.object(server, "build_payload", side_effect=loader):
                urls = [
                    f"{base}/api/analyze/{'tw539' if index % 2 == 0 else 'ca-fantasy5'}?limit=180"
                    for index in range(10)
                ]
                with ThreadPoolExecutor(max_workers=10) as pool:
                    responses = list(pool.map(self._get, urls))
                ids = {payload["job_id"] for _, payload in responses}
                for job_id in ids:
                    self._wait(job_id)
            self.assertEqual(2, len(ids))
            self.assertEqual(2, len(calls))
            self.assertEqual(1, peak)
        finally:
            httpd.shutdown()

    def test_cache_hit_get_is_legacy_compatible_and_does_not_run_core(self):
        signature = server._repository_signature("tw539", self.history)
        result = {
            "ok": True,
            "game": "tw539",
            "latest": self.history[0],
            "history": [],
            "analysis": {"top5": [1, 2, 3, 4, 5]},
        }
        server.store_warm_result("tw539", 180, signature, result)
        httpd, base = self._serve()
        try:
            with patch.object(server, "taiwan_history", return_value=self.history), patch.object(
                server, "build_payload", side_effect=AssertionError("cache hit ran analysis")
            ):
                status, payload = self._get(f"{base}/api/analyze/tw539?limit=180")
            self.assertEqual(200, status)
            self.assertEqual("completed", payload["status"])
            self.assertTrue(payload["completed"])
            self.assertTrue(payload["cached"])
            self.assertIn("latest", payload)
            self.assertIn("analysis", payload)
        finally:
            httpd.shutdown()

    def test_client_retry_while_queued_and_running_reuses_job(self):
        release = threading.Event()
        calls = 0

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            release.wait(2)
            return {"analysis": {"top5": [1, 2, 3, 4, 5]}}

        first, first_status = server.start_analysis_job("tw539", 180, loader=loader)
        retries = [server.start_analysis_job("tw539", 180, loader=loader) for _ in range(10)]
        release.set()
        self._wait(first["job_id"])
        self.assertEqual(202, first_status)
        self.assertEqual({first["job_id"]}, {item[0]["job_id"] for item in retries})
        self.assertEqual(1, calls)

    def test_cross_process_flock_is_nonblocking_and_crash_releases(self):
        context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
        ready = context.Queue()
        release = context.Event()
        holder = context.Process(target=_hold_file_lock, args=(str(server.ANALYSIS_EXECUTION_LOCK_FILE), ready, release))
        holder.start()
        self.assertTrue(ready.get(timeout=5))
        result = context.Queue()
        contender = context.Process(target=_try_file_lock, args=(str(server.ANALYSIS_EXECUTION_LOCK_FILE), result))
        contender.start()
        contender.join(5)
        self.assertFalse(result.get(timeout=2))
        release.set()
        holder.join(5)
        after = context.Queue()
        recovered = context.Process(target=_try_file_lock, args=(str(server.ANALYSIS_EXECUTION_LOCK_FILE), after))
        recovered.start()
        recovered.join(5)
        self.assertTrue(after.get(timeout=2))

    def test_duplicate_worker_initialization_still_has_one_thread(self):
        for _ in range(20):
            server.enqueue_analysis_work(lambda: None)
        server.analysis_work_queue.join()
        workers = [thread for thread in threading.enumerate() if thread.name == "lotto-analysis-queue"]
        self.assertEqual(1, len(workers))

    def test_lock_contention_fails_job_without_calling_loader_or_api_crash(self):
        lock = server.AnalysisExecutionFileLock()
        self.assertTrue(lock.acquire())
        calls = 0

        def loader(*_args, **_kwargs):
            nonlocal calls
            calls += 1

        try:
            job, status = server.start_analysis_job("tw539", 180, loader=loader)
            failed = self._wait(job["job_id"])
        finally:
            lock.release()
        self.assertEqual(202, status)
        self.assertEqual("failed", failed["status"])
        self.assertEqual("AnalysisBusy", failed["error"]["type"])
        self.assertEqual(0, calls)

    def test_analysis_core_rejects_direct_request_thread_call(self):
        with self.assertRaisesRegex(RuntimeError, "Production analysis queue worker"):
            server.build_payload("tw539", 180)

    def test_legacy_public_server_delegates_to_canonical_server(self):
        legacy_path = Path(server.__file__).parent / "public" / "server.py"
        spec = importlib.util.spec_from_file_location("lotto_legacy_server_launcher", legacy_path)
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        with patch.object(server, "main") as canonical_main:
            legacy.main()
        canonical_main.assert_called_once_with()

    def test_journal_is_unique_for_repeated_job_result(self):
        analysis = {
            "modelVersion": "queue-test-v1",
            "drawCount": 1,
            "candidateTiers": {
                "top5": [1, 2, 3, 4, 5],
                "top10": list(range(1, 11)),
                "full15": list(range(1, 16)),
            },
            "ranking": [
                {"number": number, "rank": number, "score": 40 - number}
                for number in range(1, 40)
            ],
            "modelScores": {},
            "modelWeights": {},
        }
        latest = {"period": "p1", "date": "2026-08-04", "numbers": [1, 2, 3, 4, 5]}
        path = Path(self.directory.name) / "journal.json"
        first = server.prediction_journal_v3.record_live_prediction(
            "tw539", analysis, latest, [latest], path=path, captured_at="2026-08-04T01:00:00+00:00"
        )
        second = server.prediction_journal_v3.record_live_prediction(
            "tw539", analysis, latest, [latest], path=path, captured_at="2026-08-04T01:01:00+00:00"
        )
        records = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("recorded", first["status"])
        self.assertEqual("deduplicated", second["status"])
        self.assertEqual(1, len(records))


if __name__ == "__main__":
    unittest.main()
