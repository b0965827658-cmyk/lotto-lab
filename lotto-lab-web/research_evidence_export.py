"""Bounded, observer-only exports of four approved natural evidence sources."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "star-research-natural-export-v1"
SECRET_ENV = "RESEARCH_EVIDENCE_READ_SECRET"
PRIVATE_HOST_ENV = "RESEARCH_EVIDENCE_PRIVATE_HOST"
HEADER = "X-Research-Evidence-Read-Secret"
MAX_LIMIT = 50
ROUTES = {
    "/api/internal/research-evidence/tw539/evidence": "TW539_VALID_LIVE_EVIDENCE",
    "/api/internal/research-evidence/tw539/milestones": "TW539_TELEMETRY_MILESTONE",
    "/api/internal/research-evidence/fantasy5/snapshots": "FANTASY5_FORWARD_SNAPSHOT",
    "/api/internal/research-evidence/fantasy5/settlements": "FANTASY5_FORWARD_SETTLEMENT",
}


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def authorized(path: str, supplied: str | None, host: str | None) -> bool:
    if path not in ROUTES:
        return False
    secret = os.environ.get(SECRET_ENV, "")
    expected_host = os.environ.get(PRIVATE_HOST_ENV, "lotto-lab-candidate-a-staging")
    actual_host = (host or "").split(":", 1)[0]
    return bool(secret) and actual_host == expected_host and hmac.compare_digest(supplied or "", secret)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"records": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("INVALID_SOURCE_JOURNAL")
    if value.get("journal_sha256") and value["journal_sha256"] != _sha(value["records"]):
        raise ValueError("SOURCE_JOURNAL_INTEGRITY_FAILURE")
    return value


def _telemetry_milestones(root: Path) -> tuple[list[dict[str, Any]], str]:
    job_hashes, qualified = [], 0
    jobs = root / "job_telemetry" / "jobs"
    for path in sorted(jobs.glob("*.json")) if jobs.is_dir() else []:
        value = json.loads(path.read_text(encoding="utf-8"))
        content = {"events": value.get("events"), "heartbeat_latest": value.get("heartbeat_latest", {})}
        if value.get("journal_sha256") != _sha(content):
            raise ValueError("TELEMETRY_INTEGRITY_FAILURE")
        job_hashes.append(value["journal_sha256"])
        events = {x.get("event_type"): x for x in value["events"]}
        completed = events.get("WORKER_RELEASED") or {}
        required = ("started_at", "current_completed_at", "worker_released_at", "owner_instance_id", "deploy_commit")
        if completed.get("status") == "completed" and all(completed.get(k) not in (None, "", "UNKNOWN") for k in required):
            qualified += 1
    journal_hash = _sha(job_hashes)
    records = []
    for threshold in (5, 10, 30, 60):
        if qualified >= threshold:
            records.append({"milestone_id": f"TW539-LIVE-{threshold}", "threshold": threshold, "qualified_sample_count": qualified, "source_hashes": job_hashes, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return records, journal_hash


def _source(root: Path, source_type: str) -> tuple[str, list[dict[str, Any]], str]:
    if source_type == "TW539_VALID_LIVE_EVIDENCE":
        value = _read(root / "evidence" / "tw539_evidence_journal.json")
        records = [x for x in value["records"] if x.get("validity_status") == "valid"]
        return "TW539", records, str(value.get("journal_sha256") or _sha(value["records"]))
    if source_type == "TW539_TELEMETRY_MILESTONE":
        records, digest = _telemetry_milestones(root)
        return "TW539", records, digest
    directory = root / "fantasy5_forward_partial"
    filename = "partial_snapshot_journal.json" if source_type == "FANTASY5_FORWARD_SNAPSHOT" else "partial_settlement_journal.json"
    value = _read(directory / filename)
    if source_type == "FANTASY5_FORWARD_SNAPSHOT":
        records = [x for x in value["records"] if x.get("snapshot_type") == "PARTIAL_SNAPSHOT" and not x.get("validation_only") and not x.get("historical_backfill")]
    else:
        records = [x for x in value["records"] if x.get("snapshot_sha256") and x.get("settlement_sha256") and not x.get("validation_only")]
    return "FANTASY5", records, str(value.get("journal_sha256") or _sha(value["records"]))


def export_page(path: str, persistent_root: Path, *, cursor: int = 0, limit: int = 25) -> dict[str, Any]:
    if path not in ROUTES or cursor < 0 or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("UNSUPPORTED_EXPORT_REQUEST")
    source_type = ROUTES[path]
    context, records, journal_hash = _source(persistent_root, source_type)
    page = records[cursor:cursor + limit]
    items = [{"record_hash": _sha(record), "immutable_payload": record} for record in page]
    core = {
        "schema_version": SCHEMA_VERSION, "source_type": source_type, "lottery_context": context,
        "source_id": source_type, "source_version": "v1", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_quality": "NATURAL_IMMUTABLE", "journal_hash": journal_hash, "records": items,
        "pagination": {"cursor": cursor, "limit": limit, "next_cursor": cursor + len(page) if cursor + len(page) < len(records) else None, "total": len(records)},
    }
    return {**core, "export_sha256": _sha(core)}
