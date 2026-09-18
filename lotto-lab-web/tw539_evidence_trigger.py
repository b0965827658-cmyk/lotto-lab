"""Authenticated fixed trigger for one Current-only TW539 Evidence auto cycle."""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tw539_evidence_provenance import run_tw539_daily_evidence_auto


TRIGGER_HEADER = "X-Evidence-Trigger-Secret"
TRIGGER_SECRET_ENV = "EVIDENCE_TRIGGER_SECRET"
TRIGGER_ENABLED_ENV = "EVIDENCE_TRIGGER_ENABLED"
RUNTIME_ENV_ENV = "EVIDENCE_RUNTIME_ENV"
PRODUCTION_ARMED_ENV = "EVIDENCE_PRODUCTION_ARMED"
_INVOCATION_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _root() -> Path:
    raw = os.environ.get("LOTTO_PERSISTENT_DATA_DIR", "")
    root = Path(raw)
    if not raw or not root.is_absolute():
        raise RuntimeError("persistent evidence root unavailable")
    return root.resolve()


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


class AuditFileLock:
    """Blocking cross-process lock for the read/append/replace audit transaction."""

    def __init__(self, path: Path):
        self.path = path
        self.stream: Any = None

    def __enter__(self) -> "AuditFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        if self.stream.read(1) == b"":
            self.stream.seek(0)
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        if os.name == "nt":  # pragma: no cover - Render uses Linux
            import msvcrt

            msvcrt.locking(self.stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_: Any) -> None:
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


def _record_invocation(record: dict[str, Any]) -> None:
    path = _root() / "evidence" / "invocations" / "tw539_invocations.json"
    with AuditFileLock(path.with_suffix(".lock")):
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(existing, list):
            raise ValueError("invocation audit schema mismatch")
        existing.append(record)
        _atomic_write(path, existing[-1000:])


def activation_error() -> str | None:
    enabled = os.environ.get(TRIGGER_ENABLED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    runtime_env = os.environ.get(RUNTIME_ENV_ENV, "").strip().lower()
    if not enabled:
        return "trigger_disabled"
    if runtime_env not in {"staging", "production"}:
        return "runtime_environment"
    production_armed = os.environ.get(PRODUCTION_ARMED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    if runtime_env == "production" and not production_armed:
        return "production_not_armed"
    return None


def authenticate(supplied: str) -> bool:
    expected = os.environ.get(TRIGGER_SECRET_ENV, "")
    return bool(expected and supplied and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")))


def invoke_current_evidence_cycle(
    *,
    supplied_secret: str,
    payload: dict[str, Any] | None,
    runner: Callable[[], dict[str, Any]] = run_tw539_daily_evidence_auto,
    audit_writer: Callable[[dict[str, Any]], None] = _record_invocation,
) -> tuple[int, dict[str, Any]]:
    """Run one fixed cycle. Payload must be empty and cannot control runtime data."""
    invocation_id = str(uuid.uuid4())
    started_at = _now()
    started = time.monotonic()
    blocked = activation_error()
    if blocked:
        return 503, {"status": "PERMANENT_FAILURE", "error_category": blocked, "invocation_id": invocation_id}
    if not authenticate(supplied_secret):
        return 403, {"status": "PERMANENT_FAILURE", "error_category": "authentication", "invocation_id": invocation_id}
    if payload not in (None, {}):
        return 400, {"status": "PERMANENT_FAILURE", "error_category": "payload_forbidden", "invocation_id": invocation_id}
    if not _INVOCATION_LOCK.acquire(blocking=False):
        return 202, {"status": "SAFE_NOOP", "error_category": "lock_contention", "invocation_id": invocation_id}
    try:
        try:
            result = runner()
            status = str(result.get("status", "PERMANENT_FAILURE"))
            if status not in {"SUCCESS", "SAFE_NOOP"}:
                status = "PERMANENT_FAILURE"
            error_category = None if status in {"SUCCESS", "SAFE_NOOP"} else "runtime_exception"
            response_code = 200 if status in {"SUCCESS", "SAFE_NOOP"} else 500
        except Exception:
            result = {}
            status = "PERMANENT_FAILURE"
            error_category = "runtime_exception"
            response_code = 500
        completed_at = _now()
        record = {
            "invocation_id": invocation_id,
            "scheduled_at": None,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": status,
            "draw_id_or_none": result.get("draw_id"),
            "records_added": int(result.get("records_added", 0) or 0),
            "records_skipped": int(result.get("records_skipped", 0) or 0),
            "retry_count": 0,
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
            "error_category": error_category,
        }
        try:
            audit_writer(record)
        except Exception:
            return 500, {"status": "RETRYABLE_FAILURE", "error_category": "disk_error", "invocation_id": invocation_id}
        return response_code, {
            "status": status,
            "invocation_id": invocation_id,
            "records_added": record["records_added"],
            "records_skipped": record["records_skipped"],
            "error_category": error_category,
        }
    finally:
        _INVOCATION_LOCK.release()
