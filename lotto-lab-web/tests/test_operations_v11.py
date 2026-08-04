import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import operations_v11 as ops


class OperationsV11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.original = (ops.DATA_DIR, ops.OPS_DIR, ops.WARM_CACHE, ops.JOURNALS)
        self.original_env = dict(os.environ)
        ops.DATA_DIR = self.data
        ops.OPS_DIR = self.data / "operations"
        ops.WARM_CACHE = self.data / "analysis_warm_cache.json"
        ops.JOURNALS = {
            "tw539": self.data / "prediction_journal_v3_tw539.json",
            "ca-fantasy5": self.data / "prediction_journal_v3_ca_fantasy5.json",
        }
        ops.WARM_CACHE.write_text(json.dumps({"entries": {
            "tw539:90:0": {"completedAt": "2026-08-04T01:00:00Z", "repositorySignature": "tw", "result": {"ok": True}},
            "ca-fantasy5:90:0": {"completedAt": "2026-08-04T02:00:00Z", "repositorySignature": "ca", "result": {"ok": True}},
        }}), encoding="utf-8")
        for path in ops.JOURNALS.values():
            path.write_text(json.dumps({"records": [{"drawId": "1", "predictionHash": path.name}]}), encoding="utf-8")

    def tearDown(self):
        ops.DATA_DIR, ops.OPS_DIR, ops.WARM_CACHE, ops.JOURNALS = self.original
        os.environ.clear()
        os.environ.update(self.original_env)
        self.temp.cleanup()

    def test_health_does_not_call_analysis_api(self):
        with patch.object(ops, "api_health", return_value={"ok": True, "http": 200, "responseMs": 1.0, "error": None}):
            snapshot = ops.collect_health()
        self.assertEqual("normal", snapshot["status"])
        self.assertTrue((ops.OPS_DIR / "dashboard" / "health_dashboard.html").is_file())

    def test_duplicate_journal_is_reported_only(self):
        record = {"drawId": "1", "predictionHash": "same"}
        ops.JOURNALS["tw539"].write_text(json.dumps({"records": [record, record]}), encoding="utf-8")
        status = ops.journal_status()
        self.assertEqual(1, status["tw539"]["duplicates"])
        self.assertEqual(2, len(json.loads(ops.JOURNALS["tw539"].read_text())["records"]))

    def test_backup_preserves_source_and_has_manifest(self):
        before = ops.sha256(ops.WARM_CACHE)
        manifest = ops.backup()
        self.assertEqual(before, ops.sha256(ops.WARM_CACHE))
        self.assertTrue(manifest["files"])
        self.assertTrue(next((ops.OPS_DIR / "backups").glob("*/manifest.json")).is_file())

    def test_persistent_disk_path_and_temporary_round_trip(self):
        os.environ.update(RENDER="true", LOTTO_PERSISTENT_DATA_DIR=str(self.data), LOTTO_RENDER_DISK_MOUNT_PATH=str(self.data))
        target = ops.validate_operations_path(self.data, require_render_disk=True)
        target.mkdir(parents=True)
        temporary = target / "write-test.tmp"
        temporary.write_bytes(b"persistent-round-trip")
        digest = ops.sha256(temporary)
        self.assertEqual(b"persistent-round-trip", temporary.read_bytes())
        self.assertEqual(64, len(digest))
        temporary.unlink()
        self.assertFalse(temporary.exists())

    def test_ephemeral_render_path_is_rejected(self):
        os.environ.update(RENDER="true", LOTTO_PERSISTENT_DATA_DIR=str(self.data), LOTTO_RENDER_DISK_MOUNT_PATH=str(self.data / "actual-disk"))
        with self.assertRaises(RuntimeError):
            ops.validate_operations_path(self.data, require_render_disk=True)

    def test_scheduler_lock_is_nonblocking_singleton(self):
        first = ops.SchedulerLock(ops.OPS_DIR / ops.LOCK_FILE_NAME)
        second = ops.SchedulerLock(ops.OPS_DIR / ops.LOCK_FILE_NAME)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_scheduler_lock_blocks_another_process(self):
        lock = ops.SchedulerLock(ops.OPS_DIR / ops.LOCK_FILE_NAME)
        self.assertTrue(lock.acquire())
        code = "import operations_v11 as o; x=o.acquire_scheduler_lock(); print('acquired' if x else 'blocked'); x and x.release()"
        environment = dict(os.environ, LOTTO_PERSISTENT_DATA_DIR=str(self.data), RENDER="false")
        try:
            result = subprocess.run([sys.executable, "-c", code], cwd=Path(ops.__file__).parent, env=environment, capture_output=True, text=True, timeout=10)
        finally:
            lock.release()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("blocked", result.stdout)

    def test_repeated_import_does_not_start_scheduler(self):
        code = (
            "import importlib,threading,operations_v11 as o;"
            "a=sum(t.name=='lotto-operations-v11' for t in threading.enumerate());"
            "importlib.reload(o);"
            "b=sum(t.name=='lotto-operations-v11' for t in threading.enumerate());"
            "print(a,b)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(ops.__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual("0 0", result.stdout.strip())

    def test_retention_keeps_30_days_and_never_deletes_sources(self):
        fixed = datetime(2026, 8, 4, 12, 0, tzinfo=ops.TAIPEI)
        backups = ops.OPS_DIR / "backups"
        old = backups / "2026-07-05"
        recent = backups / "2026-07-06"
        old.mkdir(parents=True)
        recent.mkdir(parents=True)
        outside = self.data / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        link = backups / "2026-07-01"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            link = None
        sources = {path: ops.sha256(path) for path in [ops.WARM_CACHE, *ops.JOURNALS.values()]}
        with patch.object(ops, "now", return_value=fixed):
            ops.backup()
            first_files = sorted(path.name for path in (backups / "2026-08-04").iterdir())
            ops.backup()
            second_files = sorted(path.name for path in (backups / "2026-08-04").iterdir())
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(first_files, second_files)
        self.assertEqual(sources, {path: ops.sha256(path) for path in sources})
        self.assertTrue(marker.exists())
        if link is not None:
            self.assertTrue(link.is_symlink())

    def test_audit_does_not_import_models_or_modify_journal(self):
        before = {path: ops.sha256(path) for path in ops.JOURNALS.values()}
        self.assertNotIn("analysis_v2", ops.__dict__)
        with patch.object(ops, "api_health", return_value={"ok": True, "http": 200, "responseMs": 1.0, "error": None}):
            ops.collect_health()
        self.assertEqual(before, {path: ops.sha256(path) for path in before})

    def test_dashboard_contains_no_secrets_or_controls(self):
        os.environ["DATABASE_URL"] = "postgres://secret"
        os.environ["API_TOKEN"] = "super-secret-token"
        with patch.object(ops, "api_health", return_value={"ok": True, "http": 200, "responseMs": 1.0, "error": None}):
            ops.collect_health()
        dashboard = (ops.OPS_DIR / "dashboard" / "health_dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn("super-secret-token", dashboard)
        self.assertNotIn("postgres://secret", dashboard)
        self.assertNotIn("<button", dashboard.lower())
        self.assertNotIn("<form", dashboard.lower())

    def test_audit_failure_is_contained(self):
        with patch.object(ops, "run_all", side_effect=RuntimeError("audit failure")):
            self.assertFalse(ops.run_audit_safely())


if __name__ == "__main__":
    unittest.main()
