"""Persistent, policy-limited Web Push delivery journal.

This module has no dependency on prediction or research runtime code.  It only
stores browser subscriptions, queues approved notification events, and records
provider/client receipts under the staging persistent root.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ALLOWED_EVENT_TYPES = {
    "SHADOW_RECOMMENDED",
    "EMERGENCY_KILL",
    "INTEGRITY_FAILURE",
    "PERSISTENT_STORAGE_RISK",
    "REPEATED_AUTONOMY_FAILURE",
    "TEST_NOTIFICATION",
}
FORMAL_EVENT_TYPES = ALLOWED_EVENT_TYPES - {"TEST_NOTIFICATION"}
FINAL_STATES = {"PROVIDER_ACCEPTED", "FAILED", "EXPIRED"}
_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class NotificationDelivery:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.subscriptions_path = self.root / "push_subscriptions.json"
        self.queue_path = self.root / "notification_queue.json"
        self.receipts_path = self.root / "notification_receipts.json"

    @staticmethod
    def subscription_id(subscription: dict[str, Any]) -> str:
        return hashlib.sha256(str(subscription.get("endpoint", "")).encode()).hexdigest()

    def subscriptions(self) -> list[dict[str, Any]]:
        rows = _load(self.subscriptions_path, [])
        return rows if isinstance(rows, list) else []

    def upsert(self, subscription: dict[str, Any], user_agent: str = "", game: str = "all") -> int:
        now = utc_now()
        sid = self.subscription_id(subscription)
        with _LOCK:
            rows = self.subscriptions()
            old = next((row for row in rows if row.get("id") == sid), {})
            record = {
                "id": sid,
                "subscription": subscription,
                "game": game,
                "created_at": old.get("created_at", now),
                "updated_at": now,
                "user_agent": user_agent[:300],
                "enabled": True,
                "failure_count": int(old.get("failure_count", 0)),
            }
            rows = [row for row in rows if row.get("id") != sid] + [record]
            _atomic_write(self.subscriptions_path, rows)
            return len([row for row in rows if row.get("enabled", True)])

    def remove(self, subscription: dict[str, Any]) -> int:
        sid = self.subscription_id(subscription)
        with _LOCK:
            rows = [row for row in self.subscriptions() if row.get("id") != sid]
            _atomic_write(self.subscriptions_path, rows)
            return len([row for row in rows if row.get("enabled", True)])

    def enqueue(self, event_id: str, event_type: str, severity: str, summary: str,
                destination: str = "/#system", validation_only: bool = False) -> int:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("notification event type is not allowed")
        if event_type == "TEST_NOTIFICATION" and not validation_only:
            raise ValueError("test notification must be validation_only")
        if event_type in FORMAL_EVENT_TYPES and validation_only:
            raise ValueError("formal notification cannot be validation_only")
        now = utc_now()
        with _LOCK:
            queue = _load(self.queue_path, [])
            subscriptions = [r for r in self.subscriptions() if r.get("enabled", True)]
            existing = {(r.get("event_id"), r.get("subscription_id")) for r in queue}
            added = 0
            for sub in subscriptions:
                key = (event_id, sub["id"])
                if key in existing:
                    continue
                notification_id = hashlib.sha256(f"{event_id}|{sub['id']}".encode()).hexdigest()
                queue.append({
                    "notification_id": notification_id,
                    "event_id": event_id,
                    "event_type": event_type,
                    "severity": severity,
                    "created_at": now,
                    "status": "QUEUED",
                    "subscription_id": sub["id"],
                    "queued_at": now,
                    "attempt_count": 0,
                    "sent_at": None,
                    "provider_status": None,
                    "delivered_or_accepted_at": None,
                    "failure_reason": None,
                    "validation_only": validation_only,
                    "payload": {
                        "notification_id": notification_id,
                        "event_type": event_type,
                        "severity": severity,
                        "summary": summary[:240],
                        "created_at": now,
                        "url": destination if destination.startswith("/") else "/#system",
                    },
                })
                added += 1
            _atomic_write(self.queue_path, queue)
            return added

    def dispatch(self, sender: Callable[[dict[str, Any], dict[str, Any]], None], max_attempts: int = 3) -> dict[str, int]:
        with _LOCK:
            queue = _load(self.queue_path, [])
            subscriptions = {r.get("id"): r for r in self.subscriptions()}
            accepted = failed = 0
            for row in queue:
                if row.get("status") in FINAL_STATES or int(row.get("attempt_count", 0)) >= max_attempts:
                    continue
                sub = subscriptions.get(row.get("subscription_id"))
                if not sub or not sub.get("enabled", True):
                    row.update(status="EXPIRED", failure_reason="subscription_unavailable")
                    continue
                row["status"] = "SENDING"
                row["attempt_count"] = int(row.get("attempt_count", 0)) + 1
                try:
                    sender(sub["subscription"], row["payload"])
                    now = utc_now()
                    row.update(status="PROVIDER_ACCEPTED", sent_at=now, provider_status="accepted",
                               delivered_or_accepted_at=now, failure_reason=None)
                    accepted += 1
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    row.update(status="FAILED" if row["attempt_count"] >= max_attempts else "QUEUED",
                               provider_status=str(status or "error"), failure_reason=type(exc).__name__)
                    sub["failure_count"] = int(sub.get("failure_count", 0)) + 1
                    if status in (404, 410):
                        sub["enabled"] = False
                        row["status"] = "EXPIRED"
                    failed += 1
            _atomic_write(self.queue_path, queue)
            _atomic_write(self.subscriptions_path, list(subscriptions.values()))
            return {"provider_accepted": accepted, "failed": failed}

    def receipt(self, notification_id: str, state: str) -> None:
        if state not in {"CLIENT_RECEIVED", "USER_OPENED"}:
            raise ValueError("invalid receipt state")
        with _LOCK:
            rows = _load(self.receipts_path, [])
            key = (notification_id, state)
            if key not in {(r.get("notification_id"), r.get("state")) for r in rows}:
                rows.append({"notification_id": notification_id, "state": state, "at": utc_now()})
                _atomic_write(self.receipts_path, rows)

    def status(self) -> dict[str, Any]:
        queue = _load(self.queue_path, [])
        receipts = _load(self.receipts_path, [])
        return {
            "subscriber_count": len([r for r in self.subscriptions() if r.get("enabled", True)]),
            "queue_count": len(queue),
            "provider_accepted": sum(r.get("status") == "PROVIDER_ACCEPTED" for r in queue),
            "client_received": sum(r.get("state") == "CLIENT_RECEIVED" for r in receipts),
            "user_opened": sum(r.get("state") == "USER_OPENED" for r in receipts),
        }
