"""Deterministic, append-only TW539 Evidence Runtime.

This module evaluates immutable pre-draw predictions against a trusted settled
draw.  It never executes a model and never reads research Markdown as live
evidence.  Production-compatible storage is allowed only below the absolute
``LOTTO_PERSISTENT_DATA_DIR`` path; tests must pass an explicit temporary path.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RUNTIME_VERSION = "tw539-evidence-runtime-v1.0.0"
SCHEMA_VERSION = "tw539-evidence-v1.0.0"
JOURNAL_VERSION = "tw539-evidence-journal-v1.0.0"
LIVE_CANDIDATE_STATES = frozenset({"SHADOW_RUNTIME", "OBSERVATION"})
SUBJECT_TYPES = frozenset({"current", "baseline", "candidate"})
_PROCESS_LOCK = threading.RLock()


class EvidenceError(RuntimeError):
    """Base class for deterministic evidence failures."""


class PersistentPathError(EvidenceError):
    """Raised when production-compatible persistent storage is unavailable."""


class JournalCorruptionError(EvidenceError):
    """Raised after a corrupt journal has been isolated."""


class LockUnavailableError(EvidenceError):
    """Raised when another process owns the evidence journal lock."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_time(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"missing {field}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise EvidenceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _numbers(values: Any, size: int, field: str) -> list[int]:
    if not isinstance(values, list) or len(values) != size:
        raise EvidenceError(f"{field} must contain exactly {size} numbers")
    numbers = [int(value) for value in values]
    if len(set(numbers)) != size or any(number < 1 or number > 39 for number in numbers):
        raise EvidenceError(f"invalid {field}")
    return numbers


def prediction_core(prediction: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable prediction fields protected by prediction_hash."""
    return {
        "lottery": str(prediction.get("lottery", "")),
        "draw_id": str(prediction.get("draw_id", "")),
        "subject_type": str(prediction.get("subject_type", "")),
        "subject_version": str(prediction.get("subject_version", "")),
        "prediction_created_at": prediction.get("prediction_created_at"),
        "locked_at": prediction.get("locked_at"),
        "top5": prediction.get("top5"),
        "top10": prediction.get("top10"),
        "top15": prediction.get("top15"),
        "dataset_version": prediction.get("dataset_version"),
        "dataset_sha256": prediction.get("dataset_sha256"),
    }


def make_prediction_hash(prediction: dict[str, Any]) -> str:
    return sha256_value(prediction_core(prediction))


def evidence_key(record: dict[str, Any]) -> str:
    return "|".join(
        (
            str(record["lottery"]),
            str(record["draw_id"]),
            str(record["subject_version"]),
            str(record["schema_version"]),
        )
    )


def resolve_evidence_dir(*, test_directory: Path | None = None) -> Path:
    """Resolve storage without a repository, cwd, temp, or user-folder fallback."""
    if test_directory is not None:
        path = Path(test_directory).resolve()
        if not path.is_absolute():  # pragma: no cover - resolve is absolute
            raise PersistentPathError("test directory must be absolute")
        return path
    raw = os.environ.get("LOTTO_PERSISTENT_DATA_DIR")
    if not raw:
        raise PersistentPathError("LOTTO_PERSISTENT_DATA_DIR is required")
    root = Path(raw)
    if not root.is_absolute():
        raise PersistentPathError("LOTTO_PERSISTENT_DATA_DIR must be absolute")
    return root.resolve() / "evidence"


class CrossProcessFileLock:
    """Non-blocking cross-process lock backed by one byte in a lock file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.handle = self.path.open("a+b")
            self.handle.seek(0)
            if self.handle.read(1) == b"":
                self.handle.seek(0)
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised on Linux deployment
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            if self.handle is not None:
                self.handle.close()
            self.handle = None
            raise LockUnavailableError("evidence journal lock is held") from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "CrossProcessFileLock":
        self.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return  # os.replace is atomic on the same NTFS volume; directory handles are not fsync-able.
    descriptor = os.open(path, os.O_RDONLY)
    try:  # pragma: no cover - Linux deployment path
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, data: bytes, *, fail_at: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if fail_at == "after_file_fsync":
            raise OSError("injected failure after file fsync")
        os.replace(temporary, path)
        if fail_at == "after_replace":
            raise OSError("injected failure after replace")
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, value: Any, *, fail_at: str | None = None) -> None:
    _atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"), fail_at=fail_at)


def _empty_journal() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    return {"journal_version": JOURNAL_VERSION, "records": records, "journal_sha256": sha256_value(records)}


def _recover_temporary_files(directory: Path) -> list[str]:
    """Remove same-filesystem remnants left before atomic replace."""
    recovered = []
    for temporary in directory.glob(".*.tmp"):
        if temporary.is_file():
            recovered.append(temporary.name)
            temporary.unlink()
    return sorted(recovered)


def _load_journal(path: Path, *, isolate_corrupt: bool = True) -> dict[str, Any]:
    if not path.exists():
        return _empty_journal()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("journal_version") != JOURNAL_VERSION or not isinstance(value.get("records"), list):
            raise ValueError("journal schema mismatch")
        if value.get("journal_sha256") != sha256_value(value["records"]):
            raise ValueError("journal hash mismatch")
        keys = [evidence_key(record) for record in value["records"]]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate evidence key")
        for record in value["records"]:
            expected = sha256_value({key: item for key, item in record.items() if key != "record_sha256"})
            if record.get("record_sha256") != expected:
                raise ValueError("record hash mismatch")
        return value
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        if isolate_corrupt:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            quarantine = path.with_name(f"{path.name}.corrupt.{digest}")
            if not quarantine.exists():
                os.replace(path, quarantine)
        raise JournalCorruptionError(str(exc)) from exc


def _record_for(prediction: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    subject_type = str(prediction.get("subject_type"))
    if subject_type not in SUBJECT_TYPES:
        raise EvidenceError("unsupported subject_type")
    if prediction.get("lottery") != "tw539" or actual.get("lottery") != "tw539":
        raise EvidenceError("runtime supports TW539 only")
    if str(prediction.get("draw_id")) != str(actual.get("draw_id")):
        raise EvidenceError("draw_id mismatch")
    top5 = _numbers(prediction.get("top5"), 5, "top5")
    top10 = _numbers(prediction.get("top10"), 10, "top10")
    top15 = _numbers(prediction.get("top15"), 15, "top15")
    actual_numbers = _numbers(actual.get("actual"), 5, "actual")
    created = _parse_time(str(prediction.get("prediction_created_at", "")), "prediction_created_at")
    locked = _parse_time(str(prediction.get("locked_at", "")), "locked_at")
    available = _parse_time(str(actual.get("actual_available_at", "")), "actual_available_at")
    settled = _parse_time(str(actual.get("settled_at", "")), "settled_at")
    expected_hash = make_prediction_hash(prediction)
    supplied_hash = prediction.get("prediction_hash")
    validity = "valid"
    invalid_reason = None
    if supplied_hash != expected_hash:
        validity, invalid_reason = "integrity_error", "invalid_prediction_hash"
    elif locked < created:
        validity, invalid_reason = "invalid", "lock_before_prediction"
    elif not (created < available and locked < available):
        validity, invalid_reason = "invalid", "invalid_late_prediction"
    actual_set = set(actual_numbers)
    hits = {
        "top5": len(set(top5) & actual_set) if validity == "valid" else None,
        "top10": len(set(top10) & actual_set) if validity == "valid" else None,
        "top15": len(set(top15) & actual_set) if validity == "valid" else None,
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "lottery": "tw539",
        "draw_id": str(prediction["draw_id"]),
        "subject_type": subject_type,
        "subject_version": str(prediction.get("subject_version", "")),
        "prediction_hash": supplied_hash,
        "prediction_created_at": prediction.get("prediction_created_at"),
        "locked_at": prediction.get("locked_at"),
        "actual_available_at": actual.get("actual_available_at"),
        "actual": actual_numbers,
        "top5": top5,
        "top10": top10,
        "top15": top15,
        "hits_top5": hits["top5"],
        "hits_top10": hits["top10"],
        "hits_top15": hits["top15"],
        "win_tie_lose": None,
        "validity_status": validity,
        "invalid_reason": invalid_reason,
        "settled_at": settled.isoformat(),
        "runtime_version": RUNTIME_VERSION,
        "dataset_version": prediction.get("dataset_version") or actual.get("dataset_version"),
        "dataset_sha256": prediction.get("dataset_sha256") or actual.get("dataset_sha256"),
    }
    return record


def _candidate_allowed(prediction: dict[str, Any], registry: dict[str, Any]) -> bool:
    version = str(prediction.get("subject_version", ""))
    entry = registry.get("subjects", {}).get(version, {}) if isinstance(registry, dict) else {}
    return prediction.get("subject_type") != "candidate" or entry.get("status") in LIVE_CANDIDATE_STATES


def _apply_outcomes(records: list[dict[str, Any]]) -> None:
    by_draw: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        if record["validity_status"] == "valid":
            by_draw.setdefault(record["draw_id"], {})[record["subject_type"]] = record
    for subjects in by_draw.values():
        current = subjects.get("current")
        if not current:
            continue
        for subject_type in ("baseline", "candidate"):
            record = subjects.get(subject_type)
            if not record:
                continue
            left, right = record["hits_top15"], current["hits_top15"]
            record["win_tie_lose"] = "win" if left > right else "loss" if left < right else "tie"


def _seal_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    sealed = []
    for source in records:
        record = dict(source)
        record["record_sha256"] = sha256_value(record)
        sealed.append(record)
    return sorted(sealed, key=evidence_key)


def _aggregate(records: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    live = [record for record in records if record.get("validity_status") == "valid"]
    subjects: dict[str, Any] = {}
    for record in live:
        version = record["subject_version"]
        target = subjects.setdefault(version, {"subject_type": record["subject_type"], "valid_draws": 0, "hits_top5": 0, "hits_top10": 0, "hits_top15": 0, "wins": 0, "ties": 0, "losses": 0})
        target["valid_draws"] += 1
        target["hits_top5"] += record["hits_top5"]
        target["hits_top10"] += record["hits_top10"]
        target["hits_top15"] += record["hits_top15"]
        outcome = record.get("win_tie_lose")
        if outcome:
            target[f"{outcome}s"] += 1
    for target in subjects.values():
        count = target["valid_draws"]
        for tier in (5, 10, 15):
            target[f"mean_top{tier}"] = round(target[f"hits_top{tier}"] / count, 12) if count else None
    candidate_status = "Prototype / Awaiting Shadow"
    for version, entry in registry.get("subjects", {}).items():
        if entry.get("subject_type") == "candidate" and entry.get("status") in LIVE_CANDIDATE_STATES:
            candidate_status = entry["status"]
    return {"runtime_version": RUNTIME_VERSION, "valid_live_only": True, "subjects": subjects, "candidate_status": candidate_status}


def _eps(aggregation: dict[str, Any]) -> dict[str, Any]:
    candidates = [value for value in aggregation["subjects"].values() if value["subject_type"] == "candidate"]
    if not candidates:
        return {"score": None, "valid": False, "status": "Research Only", "reason": "no valid Live Candidate evidence"}
    candidate = max(candidates, key=lambda value: value["valid_draws"])
    if candidate["valid_draws"] < 100:
        return {"score": None, "valid": False, "status": "Observation", "reason": "fewer than 100 valid Live draws"}
    # Accuracy-only evidence is intentionally insufficient without runtime and regression inputs.
    return {"score": None, "valid": False, "status": "Observation", "reason": "RSS, latency, regression, stability and confidence gates required"}


def _write_read_models(directory: Path, journal: dict[str, Any], registry: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    aggregation = _aggregate(journal["records"], registry)
    eps = _eps(aggregation)
    _atomic_write_json(directory / "evidence_registry_stats.json", aggregation)
    _atomic_write_json(directory / "evidence_eps.json", eps)
    dashboard = {"source": "verified Evidence Journal aggregation only", "aggregation": aggregation, "eps": eps, "journal_sha256": journal["journal_sha256"]}
    _atomic_write_json(directory / "evidence_dashboard.json", dashboard)
    timeline_path = directory / "evidence_timeline.csv"
    fields = ["schema_version", "lottery", "draw_id", "subject_type", "subject_version", "hits_top5", "hits_top10", "hits_top15", "win_tie_lose", "validity_status", "record_sha256"]
    rows = [[record.get(field) for field in fields] for record in journal["records"]]
    output = []
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(fields)
    writer.writerows(rows)
    _atomic_write_bytes(timeline_path, buffer.getvalue().encode("utf-8"))
    return aggregation, eps


def run_tw539_daily_evidence(
    manifest: dict[str, Any] | Path,
    *,
    test_directory: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Settle the newest available TW539 draw and update deterministic read models."""
    payload = json.loads(Path(manifest).read_text(encoding="utf-8")) if isinstance(manifest, Path) else json.loads(canonical_json(manifest))
    directory = resolve_evidence_dir(test_directory=test_directory)
    current_time = _parse_time(now or datetime.now(timezone.utc).isoformat(), "now")
    actuals = []
    for actual in payload.get("actuals", []):
        try:
            available = _parse_time(str(actual.get("actual_available_at", "")), "actual_available_at")
            if actual.get("lottery") == "tw539" and available <= current_time:
                actuals.append((available, str(actual.get("draw_id")), actual))
        except EvidenceError:
            continue
    if not actuals:
        return {"status": "no_settled_draw", "records_added": 0, "evidence_directory": str(directory)}
    _, draw_id, actual = max(actuals, key=lambda item: (item[0], item[1]))
    registry = payload.get("registry", {})
    predictions = [prediction for prediction in payload.get("predictions", []) if prediction.get("lottery") == "tw539" and str(prediction.get("draw_id")) == draw_id]
    candidate_isolated = [prediction for prediction in predictions if prediction.get("subject_type") == "candidate" and not _candidate_allowed(prediction, registry)]
    eligible = [prediction for prediction in predictions if _candidate_allowed(prediction, registry)]
    new_records = []
    for prediction in eligible:
        try:
            new_records.append(_record_for(prediction, actual))
        except EvidenceError as exc:
            # Structurally incomplete predictions are rejected without inventing evidence.
            continue
    _apply_outcomes(new_records)
    new_records = _seal_records(new_records)
    directory.mkdir(parents=True, exist_ok=True)
    journal_path = directory / "tw539_evidence_journal.json"
    with _PROCESS_LOCK, CrossProcessFileLock(directory / "tw539_evidence_journal.lock"):
        recovered_temporary_files = _recover_temporary_files(directory)
        journal = _load_journal(journal_path)
        existing = {evidence_key(record): record for record in journal["records"]}
        added = 0
        for record in new_records:
            key = evidence_key(record)
            if key in existing:
                if existing[key]["record_sha256"] != record["record_sha256"]:
                    raise EvidenceError(f"immutable evidence conflict for {key}")
                continue
            existing[key] = record
            added += 1
        records = sorted(existing.values(), key=evidence_key)
        updated = {"journal_version": JOURNAL_VERSION, "records": records, "journal_sha256": sha256_value(records)}
        if added or not journal_path.exists():
            _atomic_write_json(journal_path, updated)
        aggregation, eps = _write_read_models(directory, updated, registry)
    return {
        "status": "completed",
        "draw_id": draw_id,
        "records_added": added,
        "record_count": len(updated["records"]),
        "journal_sha256": updated["journal_sha256"],
        "candidate_predictions_isolated": len(candidate_isolated),
        "candidate_status": aggregation["candidate_status"],
        "recovered_temporary_files": recovered_temporary_files,
        "eps": eps,
        "evidence_directory": str(directory),
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic TW539 daily evidence settlement")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", type=Path, help="TEST/RESEARCH ONLY: JSON manifest containing predictions, actuals and registry state")
    mode.add_argument("--auto", action="store_true", help="Production-compatible fixed-source no-argument provenance mode")
    parser.add_argument("--now", help="timezone-aware evidence cutoff; defaults to current UTC")
    parser.add_argument("--local-test-directory", type=Path, help="explicit test-only output directory")
    arguments = parser.parse_args()
    try:
        if arguments.auto:
            if arguments.local_test_directory is not None or arguments.now is not None:
                raise EvidenceError("--auto does not accept test directory or time overrides")
            from tw539_evidence_provenance import run_tw539_daily_evidence_auto

            result = run_tw539_daily_evidence_auto()
        else:
            result = run_tw539_daily_evidence(arguments.manifest, test_directory=arguments.local_test_directory, now=arguments.now)
    except EvidenceError as exc:
        print(canonical_json({"status": "safe_error", "error": type(exc).__name__, "message": str(exc)}))
        return 2
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
