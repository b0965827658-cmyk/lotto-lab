"""Trusted, fixed-source provenance adapter for TW539 live Evidence.

The public auto entry accepts no data-bearing arguments.  It reads only files
below LOTTO_PERSISTENT_DATA_DIR and never runs a prediction model.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import tw539_evidence_runtime as evidence_runtime


CURRENT_JOURNAL = "prediction_journal_v3_tw539.json"
SHADOW_JOURNAL = "shadow/v2_shadow_journal.json"
ACTUAL_DATABASE = "tw539_database.json"
REGISTRY = "evidence/evidence_subject_registry.json"
LIVE_CANDIDATE_STATES = frozenset({"SHADOW_RUNTIME", "OBSERVATION"})


class ProvenanceError(RuntimeError):
    """Trusted source is malformed or fails immutable provenance checks."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _persistent_root() -> Path:
    raw = os.environ.get("LOTTO_PERSISTENT_DATA_DIR", "")
    root = Path(raw)
    if not raw or not root.is_absolute():
        raise evidence_runtime.PersistentPathError("LOTTO_PERSISTENT_DATA_DIR must be an absolute persistent path")
    return root.resolve()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ProvenanceError(f"invalid trusted source: {path.name}") from exc


def _verify_current_record(record: dict[str, Any]) -> None:
    prediction = record.get("prediction")
    expected = record.get("predictionHash")
    if not isinstance(prediction, dict) or not expected or _sha(prediction) != expected:
        raise ProvenanceError("Current Prediction Journal hash mismatch")
    if not record.get("locked") or not record.get("predictionCapturedAt"):
        raise ProvenanceError("Current Prediction Journal lacks immutable lock metadata")


def _actual_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("game") != "tw539" or not row.get("verified"):
            continue
        numbers = row.get("numbers")
        if isinstance(numbers, list) and len(numbers) == 5 and row.get("period"):
            result[str(row["period"])] = row
    return result


def _closed_current_records(root: Path) -> list[dict[str, Any]]:
    records = _read_json(root / CURRENT_JOURNAL, [])
    return [
        record for record in records
        if isinstance(record, dict)
        and record.get("recordType") == "live-pre-draw"
        and record.get("game") == "tw539"
        and record.get("status") == "closed"
        and isinstance(record.get("settlement"), dict)
    ]


def _registry(root: Path) -> dict[str, Any]:
    value = _read_json(root / REGISTRY, {"subjects": {}})
    return value if isinstance(value, dict) and isinstance(value.get("subjects", {}), dict) else {"subjects": {}}


def _current_prediction(record: dict[str, Any], actual_row: dict[str, Any]) -> dict[str, Any]:
    _verify_current_record(record)
    settlement = record["settlement"]
    draw_id = str(settlement.get("drawId", ""))
    if not draw_id or draw_id != str(actual_row.get("period", "")):
        raise ProvenanceError("Current settlement draw does not match canonical Actual")
    if sorted(int(value) for value in settlement.get("winningNumbers", [])) != sorted(int(value) for value in actual_row.get("numbers", [])):
        raise ProvenanceError("Current settlement numbers do not match canonical Actual")
    prediction = record["prediction"]
    created_at = str(record["predictionCapturedAt"])
    version = str(record.get("modelVersion") or prediction.get("modelVersion") or "unknown-current")
    transformed = {
        "lottery": "tw539",
        "draw_id": draw_id,
        "subject_type": "current",
        "subject_version": version,
        "prediction_created_at": created_at,
        "locked_at": created_at,
        "top5": prediction.get("top5"),
        "top10": prediction.get("top10"),
        "top15": prediction.get("top15"),
        "dataset_version": str(prediction.get("repositoryVersion") or "unknown"),
        "dataset_sha256": str(prediction.get("datasetHash") or record.get("sourceDataHash") or ""),
    }
    transformed["prediction_hash"] = evidence_runtime.make_prediction_hash(transformed)
    return transformed


def _actual(record: dict[str, Any], actual_row: dict[str, Any]) -> dict[str, Any]:
    settlement = record["settlement"]
    available = settlement.get("settledAt") or record.get("closedAt")
    if not available:
        raise ProvenanceError("Actual lacks trusted first-observed settlement timestamp")
    source_core = {
        "period": str(actual_row["period"]),
        "date": actual_row.get("date"),
        "numbers": actual_row["numbers"],
        "source": actual_row.get("source"),
        "sourceUrl": actual_row.get("sourceUrl"),
        "verified": actual_row.get("verified"),
    }
    return {
        "lottery": "tw539",
        "draw_id": str(actual_row["period"]),
        "actual_available_at": str(available),
        "settled_at": str(available),
        "actual": sorted(int(value) for value in actual_row["numbers"]),
        "dataset_version": str(actual_row.get("dataVersion") or "source-row-v1"),
        "dataset_sha256": _sha(source_core),
        "source": str(actual_row.get("source") or "verified-canonical-database"),
        "source_hash": _sha(source_core),
    }


def _build_tw539_evidence_manifest_from_root(root: Path) -> dict[str, Any]:
    """Private testable adapter; callers cannot choose individual sources or draws."""
    root = Path(root).resolve()
    actuals = _actual_index(_read_json(root / ACTUAL_DATABASE, []))
    candidates = []
    for record in _closed_current_records(root):
        draw_id = str(record.get("settlement", {}).get("drawId", ""))
        if draw_id in actuals:
            candidates.append((str(record.get("settlement", {}).get("settledAt", "")), draw_id, record, actuals[draw_id]))
    if not candidates:
        return {"predictions": [], "actuals": [], "registry": _registry(root), "provenance_status": "SAFE_NOOP_NO_ELIGIBLE_CURRENT"}
    _, _, record, actual_row = max(candidates, key=lambda item: (item[0], item[1]))
    current = _current_prediction(record, actual_row)
    actual = _actual(record, actual_row)
    # Baseline and Candidate are deliberately excluded until their own persisted
    # immutable prediction hash, pre-Actual timestamp, draw_id and Registry state
    # are all independently verifiable. Research/Walk-Forward is never adapted.
    return {
        "predictions": [current],
        "actuals": [actual],
        "registry": _registry(root),
        "provenance_status": "TRUSTED_CURRENT_ONLY",
        "excluded_subjects": {
            "baseline": "NO_VALID_LIVE_PROVENANCE",
            "candidate": "NO_ELIGIBLE_PERSISTED_LIVE_PROVENANCE",
            "candidate_c": "Prototype / Awaiting Shadow",
        },
    }


def build_tw539_evidence_manifest() -> dict[str, Any]:
    """Build an in-memory manifest from fixed persistent sources; no arguments."""
    return _build_tw539_evidence_manifest_from_root(_persistent_root())


def _run_tw539_daily_evidence_auto_from_root(root: Path, evidence_directory: Path) -> dict[str, Any]:
    """Private isolated harness used to prove the exact auto lifecycle."""
    manifest = _build_tw539_evidence_manifest_from_root(root)
    if not manifest["predictions"] or not manifest["actuals"]:
        return {"status": "SAFE_NOOP", "reason": manifest["provenance_status"], "records_added": 0}
    result = evidence_runtime.run_tw539_daily_evidence(
        manifest,
        test_directory=evidence_directory,
        now=manifest["actuals"][0]["settled_at"],
    )
    if result.get("records_added", 0) == 0:
        return {**result, "status": "SAFE_NOOP", "reason": "already_settled_or_deduplicated"}
    return {**result, "status": "SUCCESS"}


def run_tw539_daily_evidence_auto() -> dict[str, Any]:
    """Fixed no-argument Cloud entry. Never accepts draw, path, or payload."""
    manifest = build_tw539_evidence_manifest()
    if not manifest["predictions"] or not manifest["actuals"]:
        return {"status": "SAFE_NOOP", "reason": manifest["provenance_status"], "records_added": 0}
    result = evidence_runtime.run_tw539_daily_evidence(manifest)
    if result.get("records_added", 0) == 0:
        return {**result, "status": "SAFE_NOOP", "reason": "already_settled_or_deduplicated"}
    return {**result, "status": "SUCCESS"}
