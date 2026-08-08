"""Failure-isolated, append-only telemetry for TW539 analysis jobs.

This module observes lifecycle events only.  It never changes a job status,
requeues work, acquires the analysis lock, or performs recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "tw539-job-telemetry-v1"
EVENT_TYPES = ("QUEUED", "STARTED", "HEARTBEAT", "CURRENT_COMPLETED", "WORKER_RELEASED", "FAILED")
LOCK_STATES = ("HELD", "AVAILABLE", "UNKNOWN")
HEARTBEAT_INTERVAL_SECONDS = 10
PROCESS_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def runtime_owner() -> dict[str, Any]:
    instance_id = os.environ.get("RENDER_INSTANCE_ID") or "UNKNOWN"
    return {
        "owner_instance_id": instance_id,
        "owner_pid": os.getpid(),
        "owner_process_start_time": PROCESS_STARTED_AT,
    }


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TelemetryJournal:
    def __init__(self, root: Path, *, enabled: bool = False):
        self.root = Path(root)
        self.jobs_root = self.root / "jobs"
        self.lock_path = self.root / ".tw539_job_telemetry.lock"
        self.enabled = bool(enabled)
        self._mutex = threading.RLock()

    @contextmanager
    def _locked(self):
        with self._mutex:
            self.root.mkdir(parents=True, exist_ok=True)
            stream = self.lock_path.open("a+b")
            try:
                if os.name == "nt":  # pragma: no cover - Render is Linux
                    import msvcrt

                    if stream.seek(0, os.SEEK_END) == 0:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                try:
                    if os.name == "nt":  # pragma: no cover
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()

    def _empty(self) -> dict[str, Any]:
        content = {"events": [], "heartbeat_latest": {}}
        return {"schema_version": SCHEMA_VERSION, **content, "journal_sha256": stable_hash(content)}

    def path_for(self, job_id: str) -> Path:
        safe = "".join(character for character in str(job_id) if character.isalnum() or character in "-_")
        if not safe:
            raise ValueError("invalid telemetry job id")
        return self.jobs_root / f"{safe}.json"

    def _load(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._empty()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            events = payload.get("events")
            heartbeats = payload.get("heartbeat_latest", {})
            content = {"events": events, "heartbeat_latest": heartbeats}
            if not isinstance(events, list) or not isinstance(heartbeats, dict) or payload.get("journal_sha256") != stable_hash(content):
                raise ValueError("telemetry journal integrity mismatch")
            return payload
        except Exception:
            quarantine = path.with_name(f"{path.name}.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}")
            os.replace(path, quarantine)
            return self._empty()

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".tw539-job-telemetry-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            if os.name != "nt":
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def emit(self, event_type: str, job: dict[str, Any], **fields: Any) -> dict[str, Any] | None:
        if not self.enabled or job.get("game") != "tw539":
            return None
        if event_type not in EVENT_TYPES:
            raise ValueError(f"invalid telemetry event: {event_type}")
        timestamp = fields.pop("timestamp", None) or utc_now()
        lock_state = fields.pop("lock_state", "UNKNOWN")
        if lock_state not in LOCK_STATES:
            lock_state = "UNKNOWN"
        owner = runtime_owner()
        base = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job["job_id"],
            "job_key": job.get("request_key"),
            "lottery": job.get("game"),
            "draw_id": job.get("draw_id"),
            "status": job.get("status"),
            "event_type": event_type,
            "timestamp_utc": timestamp,
            "timezone": "UTC",
            "created_at": job.get("created_at_utc"),
            "queued_at": job.get("queued_at"),
            "started_at": job.get("current_started_at"),
            "current_completed_at": job.get("current_completed_at"),
            "worker_released_at": job.get("worker_released_at"),
            "completed_at": job.get("completed_at_utc"),
            "failed_at": job.get("failed_at"),
            "last_heartbeat_at": timestamp if event_type == "HEARTBEAT" else job.get("last_heartbeat_at"),
            **owner,
            "worker_id": fields.pop("worker_id", None),
            "running_count": int(fields.pop("running_count", 0)),
            "lock_state": lock_state,
            "lock_observed_at": timestamp,
            "lock_owner_pid": None,
            "flock_owner_status": "FLOCK_OWNER_NOT_AUTHORITATIVE",
            "attempt": int(job.get("attempt", 1)),
            "deploy_commit": os.environ.get("RENDER_GIT_COMMIT") or "UNKNOWN",
            "runtime_version": SCHEMA_VERSION,
            "recovery_candidate": bool(fields.pop("recovery_candidate", False)),
            "validation": fields.pop("validation", None),
            **fields,
        }
        identity = {
            "job_id": base["job_id"],
            "event_type": event_type,
            "timestamp_utc": timestamp,
            "owner_instance_id": base["owner_instance_id"],
        }
        base["event_id"] = stable_hash(identity)
        base["record_sha256"] = stable_hash({k: v for k, v in base.items() if k != "record_sha256"})
        path = self.path_for(base["job_id"])
        with self._locked():
            payload = self._load(path)
            if event_type == "HEARTBEAT":
                previous = payload["heartbeat_latest"].get(base["job_id"])
                base["heartbeat_count"] = int((previous or {}).get("heartbeat_count", 0)) + 1
                base["first_heartbeat_at"] = (previous or {}).get("first_heartbeat_at") or timestamp
                payload["heartbeat_latest"][base["job_id"]] = base
                content = {"events": payload["events"], "heartbeat_latest": payload["heartbeat_latest"]}
                payload["journal_sha256"] = stable_hash(content)
                self._write(path, payload)
                return base
            if any(item.get("event_id") == base["event_id"] for item in payload["events"]):
                return base
            payload["events"].append(base)
            content = {"events": payload["events"], "heartbeat_latest": payload["heartbeat_latest"]}
            payload["journal_sha256"] = stable_hash(content)
            self._write(path, payload)
        return base

    def events(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._locked():
            result: list[dict[str, Any]] = []
            if not self.jobs_root.exists():
                return result
            for path in sorted(self.jobs_root.glob("*.json")):
                payload = self._load(path)
                result.extend(payload["events"])
                result.extend(payload["heartbeat_latest"].values())
            return result


def sample_qualification(events: list[dict[str, Any]]) -> dict[str, Any]:
    values = {event.get("event_type"): event for event in events}
    completed = values.get("WORKER_RELEASED") or values.get("CURRENT_COMPLETED") or {}
    required = {
        "started_at": completed.get("started_at"),
        "current_completed_at": completed.get("current_completed_at"),
        "worker_released_at": completed.get("worker_released_at"),
        "owner_instance_id": completed.get("owner_instance_id"),
        "deploy_commit": completed.get("deploy_commit"),
    }
    missing = [key for key, value in required.items() if value in (None, "", "UNKNOWN")]
    valid = not missing and completed.get("status") == "completed"
    return {
        "qualification": "LIVE_LATENCY_SAMPLE" if valid else "EXCLUDED_FROM_TIMEOUT_CALIBRATION",
        "missing": missing,
    }
