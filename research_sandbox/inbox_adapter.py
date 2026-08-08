"""Local-only, deterministic Research Evidence Event Adapter."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observation import observe_inbox

ADAPTER_VERSION = "star-research-event-adapter-v1"
EVENT_STATUSES = {"NEW", "REJECTED_AT_ADAPTER", "IGNORED_NO_MATERIAL_CHANGE", "OBSERVED", "RESEARCH_OPENED", "CONSUMED"}
TW_TYPES = {"VALID_LIVE_EVIDENCE", "TELEMETRY_MILESTONE", "FULL_RANKING_SETTLEMENT", "SETTLED_MISS_PATTERN", "CANDIDATE_RESEARCH_RESULT", "DATA_QUALITY_CHANGE"}
F5_TYPES = {"NATURAL_FORWARD_SNAPSHOT", "FORWARD_SETTLEMENT", "FORWARD_MILESTONE", "VERIFIED_DATASET_QUALITY_CHANGE", "VALID_RESEARCH_RESULT"}
TW_MILESTONES = {5, 10, 30, 60}
F5_MILESTONES = {10, 30, 60, 100, 180, 365}
_LOCK = threading.RLock()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class FileLock:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0); self.handle.write(b"0"); self.handle.flush(); self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _safe_read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"adapter_version": ADAPTER_VERSION, "events": [], "transitions": [], "rejections": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not all(isinstance(value.get(k), list) for k in ("events", "transitions", "rejections")):
            raise ValueError("invalid journal shape")
        return value
    except Exception as exc:
        isolated = path.with_name(path.name + ".corrupt." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        os.replace(path, isolated)
        raise RuntimeError("INBOX_CORRUPTION_DETECTED") from exc


def _atomic_write(path: Path, value: dict[str, Any], *, fail_before_replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        if fail_before_replace:
            raise RuntimeError("INJECTED_CRASH_BEFORE_REPLACE")
        os.replace(name, path)
        if os.name != "nt":
            dfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)


def _payload_safe(value: Any, key: str = "") -> bool:
    forbidden_keys = {"path", "filesystem_path", "command", "python_code", "url", "git_command", "deploy_instruction"}
    if key.lower() in forbidden_keys:
        return False
    if isinstance(value, dict):
        return all(_payload_safe(v, str(k)) for k, v in value.items())
    if isinstance(value, list):
        return all(_payload_safe(v, key) for v in value)
    if isinstance(value, str):
        lower = value.lower().strip()
        if lower.startswith(("http://", "https://", "file://", "python ", "git ", "deploy ")):
            return False
        if value.startswith(("/", "\\")) or (len(value) > 2 and value[1:3] in {":\\", ":/"}):
            return False
    return True


class ResearchEvidenceEventAdapter:
    def __init__(self, inbox_path: Path):
        self.inbox_path = inbox_path
        self.lock_path = inbox_path.with_suffix(inbox_path.suffix + ".lock")

    def _reject(self, source: dict[str, Any], reason: str) -> dict[str, Any]:
        rejection = {"event_id": "REJ-" + _sha({"source": source.get("source_id"), "hash": source.get("source_hash"), "reason": reason})[:16], "status": "REJECTED_AT_ADAPTER", "reason": reason, "observed_at": _utc_now()}
        with _LOCK, FileLock(self.lock_path):
            journal = _safe_read(self.inbox_path)
            if not any(x["event_id"] == rejection["event_id"] for x in journal["rejections"]):
                journal["rejections"].append(rejection); journal["journal_sha256"] = _sha({"events": journal["events"], "transitions": journal["transitions"], "rejections": journal["rejections"]})
                _atomic_write(self.inbox_path, journal)
        return rejection

    def adapt(self, source: dict[str, Any], *, fail_before_replace: bool = False) -> dict[str, Any]:
        context, event_type = source.get("lottery_context"), source.get("event_type")
        allowed = TW_TYPES if context == "TW539" else F5_TYPES if context == "FANTASY5" else set()
        if not allowed or event_type not in allowed:
            return self._reject(source, "REJECTED_CONTEXT_MISMATCH")
        if source.get("provenance") in {None, "missing", "unverified"}:
            return self._reject(source, "INVALID_PROVENANCE")
        if source.get("computed_source_hash") != source.get("source_hash"):
            return self._reject(source, "EVENT_SOURCE_INTEGRITY_FAILURE")
        if source.get("timing_valid") is not True:
            return self._reject(source, "INVALID_TIMING")
        if not _payload_safe(source.get("materiality_inputs", {})):
            return self._reject(source, "UNSAFE_PAYLOAD")
        if event_type == "TELEMETRY_MILESTONE" and int(source.get("materiality_inputs", {}).get("milestone", -1)) not in TW_MILESTONES:
            return self._reject(source, "INVALID_MILESTONE")
        if event_type == "FORWARD_MILESTONE" and int(source.get("materiality_inputs", {}).get("milestone", -1)) not in F5_MILESTONES:
            return self._reject(source, "INVALID_MILESTONE")
        unique_key = "|".join((context, event_type, source["source_hash"], ADAPTER_VERSION))
        event = {
            "event_id": "EVT-" + _sha(unique_key)[:20], "unique_key": unique_key, "event_type": event_type,
            "lottery_context": context, "source_id": source["source_id"], "source_version": source["source_version"],
            "source_hash": source["source_hash"], "source_quality": source["source_quality"],
            "evidence_grade": source["evidence_grade"], "created_at": source["created_at"], "observed_at": _utc_now(),
            "payload_reference": {"source_id": source["source_id"], "source_hash": source["source_hash"]},
            "materiality_inputs": source.get("materiality_inputs", {}),
            "affected_knowledge_ids": source.get("affected_knowledge_ids", []),
            "adapter_version": ADAPTER_VERSION, "status": "NEW",
        }
        event["event_sha256"] = _sha(event)
        with _LOCK, FileLock(self.lock_path):
            journal = _safe_read(self.inbox_path)
            if any(x["unique_key"] == unique_key for x in journal["events"]):
                return {"status": "DUPLICATE_DEDUPED", "event_id": event["event_id"], "records_added": 0}
            # Milestones are once-only even if an upstream container hash changes.
            if event_type.endswith("MILESTONE") and any(x["lottery_context"] == context and x["event_type"] == event_type and x["materiality_inputs"].get("milestone") == event["materiality_inputs"].get("milestone") for x in journal["events"]):
                return {"status": "MILESTONE_ALREADY_EMITTED", "event_id": event["event_id"], "records_added": 0}
            journal["events"].append(event)
            journal["journal_sha256"] = _sha({"events": journal["events"], "transitions": journal["transitions"], "rejections": journal["rejections"]})
            _atomic_write(self.inbox_path, journal, fail_before_replace=fail_before_replace)
        return {"status": "ENQUEUED", "event_id": event["event_id"], "records_added": 1}

    def current_status(self, journal: dict[str, Any], event_id: str) -> str:
        states = [x["to"] for x in journal["transitions"] if x["event_id"] == event_id]
        if states: return states[-1]
        event = next(x for x in journal["events"] if x["event_id"] == event_id)
        return event["status"]

    @staticmethod
    def verify_source(event: dict[str, Any], current_source_hash: str) -> dict[str, Any]:
        valid = event.get("source_hash") == current_source_hash
        return {"valid": valid, "status": "SOURCE_INTEGRITY_VALID" if valid else "EVENT_SOURCE_INTEGRITY_FAILURE"}

    def transition(self, event_id: str, to: str, decision: str) -> None:
        if to not in EVENT_STATUSES or to == "NEW":
            raise ValueError("INVALID_TRANSITION")
        with _LOCK, FileLock(self.lock_path):
            journal = _safe_read(self.inbox_path)
            if self.current_status(journal, event_id) != "NEW":
                return
            journal["transitions"].append({"event_id": event_id, "from": "NEW", "to": to, "decision": decision, "at": _utc_now()})
            journal["journal_sha256"] = _sha({"events": journal["events"], "transitions": journal["transitions"], "rejections": journal["rejections"]})
            _atomic_write(self.inbox_path, journal)

    def process_new(self, context: str, *, prior_by_source: dict[str, dict[str, Any]], knowledge: list[dict[str, Any]], kill_switch: bool = False, commit_transitions: bool = True) -> dict[str, Any]:
        journal = _safe_read(self.inbox_path)
        events = [x for x in journal["events"] if x["lottery_context"] == context and self.current_status(journal, x["event_id"]) == "NEW"]
        if kill_switch:
            return {"state": "SLEEPING", "reason": "KILL_SWITCH_ACTIVE", "events_preserved_new": len(events), "rq_opened": 0}
        inbox = []
        by_hash = {}
        for event in events:
            evidence = {**event["materiality_inputs"], "source_id": event["source_id"], "context": context, "quality": event["source_quality"], "provenance": "adapter_validated", "source_hash": event["source_hash"]}
            inbox.append(evidence); by_hash[_sha({k:v for k,v in evidence.items() if k not in {"received_at","observed_at"}})] = event
        decision = observe_inbox(context, inbox, seen_hashes=set(), prior_by_source=prior_by_source, knowledge=knowledge, enabled=True, kill_switch=False)
        opened_hashes = {x["trigger_evidence_hash"] for x in decision["opened_rqs"]}
        event_decisions = []
        for row in decision["evaluated"]:
            event = by_hash.get(row["evidence_hash"])
            if not event: continue
            if row["evidence_hash"] in opened_hashes:
                target, label = "RESEARCH_OPENED", "OPEN_RQ"
            elif row["decision"] in {"KNOWLEDGE_DO_NOT_REPEAT", "DUPLICATE_IGNORED"}:
                target, label = "IGNORED_NO_MATERIAL_CHANGE", "NO_MATERIAL_CHANGE"
            else:
                target, label = "OBSERVED", "OBSERVE_ONLY"
            event_decisions.append({"event_id": event["event_id"], "to": target, "decision": label})
            if commit_transitions:
                self.transition(event["event_id"], target, label)
        return {**decision, "events_processed": len(events), "rq_opened": len(decision["opened_rqs"]), "event_decisions": event_decisions}


def process_new_evidence_events(adapter: ResearchEvidenceEventAdapter, context: str, **kwargs: Any) -> dict[str, Any]:
    return adapter.process_new(context, **kwargs)


def detect_file_event(
    path: Path,
    *,
    source_id: str,
    source_version: str,
    lottery_context: str,
    event_type: str,
    source_quality: str,
    evidence_grade: str,
    provenance: str,
    timing_valid: bool,
    materiality_inputs: dict[str, Any],
    affected_knowledge_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Trusted producer adapter: read a source once and emit only its reference/hash."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return {
        "lottery_context": lottery_context,
        "event_type": event_type,
        "source_id": source_id,
        "source_version": source_version,
        "source_hash": digest,
        "computed_source_hash": digest,
        "source_quality": source_quality,
        "evidence_grade": evidence_grade,
        "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "provenance": provenance,
        "timing_valid": timing_valid,
        "materiality_inputs": materiality_inputs,
        "affected_knowledge_ids": affected_knowledge_ids or [],
    }
