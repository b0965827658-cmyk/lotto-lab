import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import job_telemetry as telemetry
import server
import tw539_score_trace


class JobTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.journal = telemetry.TelemetryJournal(self.root, enabled=True)
        self.job = {
            "job_id": "j1", "request_key": "tw539:90:0:sig", "game": "tw539",
            "draw_id": "d1", "status": "processing", "created_at_utc": telemetry.utc_now(),
            "queued_at": telemetry.utc_now(), "attempt": 1,
        }

    def tearDown(self):
        self.directory.cleanup()

    def test_feature_flag_defaults_false(self):
        self.assertFalse(server.TW539_JOB_TELEMETRY_ENABLED)

    def test_off_mode_writes_nothing(self):
        journal = telemetry.TelemetryJournal(self.root / "off", enabled=False)
        self.assertIsNone(journal.emit("QUEUED", self.job))
        self.assertFalse((self.root / "off").exists())

    def test_queued_event_schema(self):
        event = self.journal.emit("QUEUED", self.job)
        self.assertEqual(telemetry.SCHEMA_VERSION, event["schema_version"])
        self.assertEqual("UTC", event["timezone"])
        self.assertTrue(event["event_id"])
        self.assertTrue(event["record_sha256"])

    def test_started_at_is_captured(self):
        self.job["current_started_at"] = telemetry.utc_now()
        event = self.journal.emit("STARTED", self.job, running_count=1, lock_state="HELD")
        self.assertEqual(self.job["current_started_at"], event["started_at"])

    def test_current_completed_at_is_captured(self):
        self.job.update(current_started_at=telemetry.utc_now(), current_completed_at=telemetry.utc_now())
        event = self.journal.emit("CURRENT_COMPLETED", self.job, running_count=1, lock_state="HELD")
        self.assertEqual(self.job["current_completed_at"], event["current_completed_at"])

    def test_worker_released_at_is_captured(self):
        self.job.update(status="completed", current_started_at=telemetry.utc_now(), current_completed_at=telemetry.utc_now(), worker_released_at=telemetry.utc_now())
        event = self.journal.emit("WORKER_RELEASED", self.job, running_count=0, lock_state="HELD")
        self.assertEqual(self.job["worker_released_at"], event["worker_released_at"])

    def test_heartbeat_contract_is_ten_seconds(self):
        self.assertEqual(10, telemetry.HEARTBEAT_INTERVAL_SECONDS)
        event = self.journal.emit("HEARTBEAT", self.job, running_count=1, lock_state="HELD")
        self.assertEqual(event["timestamp_utc"], event["last_heartbeat_at"])

    def test_heartbeat_is_compacted_per_job(self):
        first = self.journal.emit("HEARTBEAT", self.job)
        second = self.journal.emit("HEARTBEAT", self.job)
        payload = json.loads(self.journal.path_for("j1").read_text(encoding="utf-8"))
        self.assertEqual(0, len(payload["events"]))
        self.assertEqual(1, len(payload["heartbeat_latest"]))
        self.assertEqual(2, second["heartbeat_count"])
        self.assertEqual(first["timestamp_utc"], second["first_heartbeat_at"])

    def test_owner_identity_is_tuple(self):
        event = self.journal.emit("QUEUED", self.job)
        self.assertIsNotNone(event["owner_instance_id"])
        self.assertEqual(os.getpid(), event["owner_pid"])
        self.assertTrue(event["owner_process_start_time"])

    def test_unknown_instance_is_explicit(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RENDER_INSTANCE_ID", None)
            self.assertEqual("UNKNOWN", telemetry.runtime_owner()["owner_instance_id"])

    def test_pid_alone_never_claims_cross_instance_identity(self):
        owner = telemetry.runtime_owner()
        self.assertEqual({"owner_instance_id", "owner_pid", "owner_process_start_time"}, set(owner))

    def test_lock_state_unknown_is_legal(self):
        event = self.journal.emit("QUEUED", self.job, lock_state="UNKNOWN")
        self.assertEqual("UNKNOWN", event["lock_state"])
        self.assertIsNone(event["lock_owner_pid"])
        self.assertEqual("FLOCK_OWNER_NOT_AUTHORITATIVE", event["flock_owner_status"])

    def test_invalid_lock_state_becomes_unknown(self):
        event = self.journal.emit("QUEUED", self.job, lock_state="file_exists")
        self.assertEqual("UNKNOWN", event["lock_state"])

    def test_running_count_is_recorded(self):
        event = self.journal.emit("STARTED", self.job, running_count=1, lock_state="HELD")
        self.assertEqual(1, event["running_count"])

    def test_duplicate_event_is_deduplicated(self):
        stamp = telemetry.utc_now()
        self.journal.emit("QUEUED", self.job, timestamp=stamp)
        self.journal.emit("QUEUED", self.job, timestamp=stamp)
        self.assertEqual(1, len(self.journal.events()))

    def test_concurrent_duplicate_is_deduplicated(self):
        stamp = telemetry.utc_now()
        journals = [telemetry.TelemetryJournal(self.root, enabled=True) for _ in range(5)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda item: item.emit("QUEUED", self.job, timestamp=stamp), journals))
        self.assertEqual(1, len(self.journal.events()))

    def test_corruption_is_quarantined(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.journal.path_for("j1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        self.journal.emit("QUEUED", self.job)
        self.assertEqual(1, len(self.journal.events()))
        self.assertEqual(1, len(list(self.root.rglob("*.corrupt-*"))))

    def test_restart_reader_preserves_events(self):
        self.journal.emit("QUEUED", self.job)
        reopened = telemetry.TelemetryJournal(self.root, enabled=True)
        self.assertEqual(1, len(reopened.events()))

    def test_journal_hash_validates(self):
        self.journal.emit("QUEUED", self.job)
        payload = json.loads(self.journal.path_for("j1").read_text(encoding="utf-8"))
        content = {"events": payload["events"], "heartbeat_latest": payload["heartbeat_latest"]}
        self.assertEqual(telemetry.stable_hash(content), payload["journal_sha256"])

    def test_live_sample_qualification(self):
        self.job.update(status="completed", current_started_at=telemetry.utc_now(), current_completed_at=telemetry.utc_now(), worker_released_at=telemetry.utc_now())
        with patch.dict(os.environ, {"RENDER_INSTANCE_ID": "instance", "RENDER_GIT_COMMIT": "commit"}):
            event = self.journal.emit("WORKER_RELEASED", self.job)
        self.assertEqual("LIVE_LATENCY_SAMPLE", telemetry.sample_qualification([event])["qualification"])

    def test_incomplete_sample_is_excluded(self):
        event = self.journal.emit("QUEUED", self.job)
        self.assertEqual("EXCLUDED_FROM_TIMEOUT_CALIBRATION", telemetry.sample_qualification([event])["qualification"])

    def test_fantasy5_is_not_recorded(self):
        other = dict(self.job, game="ca-fantasy5")
        self.assertIsNone(self.journal.emit("QUEUED", other))
        self.assertEqual([], self.journal.events())

    def test_recovery_is_observation_only(self):
        event = self.journal.emit("HEARTBEAT", self.job, recovery_candidate=True)
        self.assertTrue(event["recovery_candidate"])
        self.assertEqual("processing", self.job["status"])
        self.assertFalse(hasattr(telemetry.TelemetryJournal, "recover"))

    def test_api_schema_is_unchanged(self):
        response = server._analysis_job_response({"job_id": "j", "game": "tw539", "status": "processing", "cached": False, "error": None, "result": None})
        self.assertEqual({"status", "completed", "job_id", "game", "retry_after_seconds", "cached", "stale", "error"}, set(response))

    def test_trace_remains_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TW539_SCORE_TRACE_ENABLED", None)
            self.assertFalse(tw539_score_trace.enabled())

    def test_telemetry_exception_is_isolated(self):
        job = dict(self.job)
        with patch.object(server.job_telemetry, "emit", side_effect=OSError("disk")):
            server._telemetry_emit("QUEUED", job)
        self.assertEqual("processing", job["status"])

    def test_telemetry_does_not_mutate_current_payload(self):
        payload = {
            "top5": [1, 2, 3, 4, 5], "top10": list(range(1, 11)),
            "top15": list(range(1, 16)), "ranking": list(range(1, 40)),
            "scores": {str(number): number / 100 for number in range(1, 40)},
        }
        before = telemetry.stable_hash(payload)
        self.journal.emit("QUEUED", self.job, current_payload=payload)
        self.assertEqual(before, telemetry.stable_hash(payload))

    def test_on_off_same_fixture_hash(self):
        fixture = {"ranking": list(range(39, 0, -1)), "scores": {str(n): n * 0.01 for n in range(1, 40)}}
        before = telemetry.stable_hash(fixture)
        telemetry.TelemetryJournal(self.root / "disabled", enabled=False).emit("QUEUED", self.job, fixture=fixture)
        self.journal.emit("QUEUED", self.job, fixture=fixture)
        self.assertEqual(before, telemetry.stable_hash(fixture))

    def test_server_integration_max_running_one(self):
        original_journal = server.job_telemetry
        original_enabled = server.TW539_JOB_TELEMETRY_ENABLED
        original_lock = server.ANALYSIS_EXECUTION_LOCK_FILE
        try:
            server.job_telemetry = telemetry.TelemetryJournal(self.root / "server", enabled=True)
            server.TW539_JOB_TELEMETRY_ENABLED = True
            server.ANALYSIS_EXECUTION_LOCK_FILE = self.root / "analysis.lock"
            with server.analysis_job_lock:
                server.analysis_jobs.clear(); server.analysis_job_keys.clear()
            with server.telemetry_running_lock:
                server.telemetry_running_count = 0; server.telemetry_max_running_observed = 0

            def loader(game, limit, optimize=False):
                time.sleep(0.02)
                return {"analysis": {"top5": [1, 2, 3, 4, 5]}, "history": [], "latest": {}}

            jobs = [server.start_analysis_job("tw539", limit, loader=loader)[0] for limit in (11, 12)]
            deadline = time.time() + 3
            while time.time() < deadline:
                if all(server.get_analysis_job(item["job_id"])["completed"] for item in jobs): break
                time.sleep(0.01)
            server.analysis_work_queue.join()
            self.assertEqual(1, server.telemetry_max_running_observed)
            types = [event["event_type"] for event in server.job_telemetry.events()]
            self.assertEqual(2, types.count("STARTED"))
            self.assertEqual(2, types.count("CURRENT_COMPLETED"))
            self.assertEqual(2, types.count("WORKER_RELEASED"))
        finally:
            server.analysis_work_queue.join()
            server.job_telemetry = original_journal
            server.TW539_JOB_TELEMETRY_ENABLED = original_enabled
            server.ANALYSIS_EXECUTION_LOCK_FILE = original_lock


if __name__ == "__main__":
    unittest.main()
