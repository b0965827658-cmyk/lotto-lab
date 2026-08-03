import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import server


class AnalysisJobTests(unittest.TestCase):
    def setUp(self):
        with server.analysis_job_lock:
            server.analysis_jobs.clear()
            server.analysis_job_keys.clear()

    def wait_for_job(self, job_id, timeout=2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = server.get_analysis_job(job_id)
            if job["status"] != "processing":
                return job
            time.sleep(0.005)
        self.fail("analysis job did not finish")

    def test_five_concurrent_requests_start_one_job(self):
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def loader(game, limit, optimize=False):
            nonlocal calls
            with calls_lock:
                calls += 1
            release.wait(1)
            return {"analysis": {"top5": [1, 2, 3, 4, 5]}}

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(server.start_analysis_job, "tw539", 180, False, loader) for _ in range(5)]
            time.sleep(0.05)
            release.set()
            responses = [future.result() for future in futures]
        self.assertEqual(1, calls)
        self.assertEqual(1, len({response[0]["job_id"] for response in responses}))
        self.assertTrue(all(status == 202 for _, status in responses))

    def test_different_games_can_run_separately(self):
        release = threading.Event()
        calls = []

        def loader(game, limit, optimize=False):
            calls.append(game)
            release.wait(1)
            return {"gameResult": game}

        tw, tw_status = server.start_analysis_job("tw539", 180, loader=loader)
        ca, ca_status = server.start_analysis_job("ca-fantasy5", 180, loader=loader)
        release.set()
        self.wait_for_job(tw["job_id"])
        self.wait_for_job(ca["job_id"])
        self.assertEqual(202, tw_status)
        self.assertEqual(202, ca_status)
        self.assertNotEqual(tw["job_id"], ca["job_id"])
        self.assertCountEqual(["tw539", "ca-fantasy5"], calls)

    def test_completed_result_is_stable_and_hot_request_is_cached(self):
        calls = 0
        expected = {"analysis": {"top5": [1, 2, 3, 4, 5], "ranking": list(range(1, 40))}}

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            return expected

        first, status = server.start_analysis_job("tw539", 90, loader=loader)
        self.assertEqual(202, status)
        completed = self.wait_for_job(first["job_id"])
        polled_again = server.get_analysis_job(first["job_id"])
        hot, hot_status = server.start_analysis_job("tw539", 90, loader=loader)
        self.assertEqual(expected["analysis"], completed["result"]["analysis"])
        self.assertEqual(completed["result"], polled_again["result"])
        self.assertEqual(completed["result"], hot["result"])
        self.assertEqual(200, hot_status)
        self.assertTrue(hot["cached"])
        self.assertEqual(1, calls)

    def test_failure_is_reported_without_starting_duplicate(self):
        calls = 0

        def loader(game, limit, optimize=False):
            nonlocal calls
            calls += 1
            raise RuntimeError("test failure")

        first, _ = server.start_analysis_job("ca-fantasy5", 50, loader=loader)
        failed = self.wait_for_job(first["job_id"])
        repeated, status = server.start_analysis_job("ca-fantasy5", 50, loader=loader)
        self.assertEqual("failed", failed["status"])
        self.assertEqual("RuntimeError", failed["error"]["type"])
        self.assertIn("test failure", failed["error"]["traceback"])
        self.assertEqual(first["job_id"], repeated["job_id"])
        self.assertEqual(200, status)
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
