"""Observer-only Fantasy 5 prediction lifecycle telemetry.

The observer is default-off and fail-open.  It never computes rankings,
features, regimes, settlements, or recommendations; it only records facts
already present at runtime lifecycle boundaries.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

FLAG = "FANTASY5_LIFECYCLE_TELEMETRY_ENABLED"
EVENT_TYPES = {
    "DATA_UPDATE_STARTED", "DATA_UPDATE_COMPLETED", "ANALYSIS_QUEUED",
    "ANALYSIS_STARTED", "RANKING_STARTED", "RANKING_FINALIZED",
    "PREDICTION_OBJECT_CREATED", "PREDICTION_LOCK_READY",
    "CACHE_WRITE_STARTED", "CACHE_WRITE_COMPLETED",
    "PREDICTION_JOURNAL_WRITE_STARTED", "PREDICTION_JOURNAL_WRITE_COMPLETED",
    "ACTUAL_CHECK_STARTED", "ACTUAL_FIRST_OBSERVED", "SETTLEMENT_STARTED",
    "SETTLEMENT_COMPLETED", "WORKER_RELEASED",
}
_PROCESS_STARTED_UTC = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
_PROCESS_STARTED_MONOTONIC = time.monotonic()
_LOCK = threading.RLock()
_LOCAL = threading.local()
_SEEN_ACTUALS: set[str] = set()
_PENDING_TARGETS: set[str] = set()


def enabled() -> bool:
    return os.environ.get(FLAG, "false").strip().lower() in {"1", "true", "yes", "on"}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _runtime_commit() -> str:
    return os.environ.get("RENDER_GIT_COMMIT", os.environ.get("GIT_COMMIT", "unknown"))


def _default_path() -> Path:
    root = Path(os.environ.get("LOTTO_PERSISTENT_DATA_DIR", Path(__file__).parent / "data"))
    return root / "fantasy5_lifecycle" / "events.jsonl"


def set_job_context(job_id: str | None) -> None:
    _LOCAL.job_id = job_id


def clear_job_context() -> None:
    _LOCAL.job_id = None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def emit(
    event_type: str,
    *,
    draw_id: Any = None,
    dataset_sha256: str | None = None,
    details: dict[str, Any] | None = None,
    path: Path | None = None,
    utc_clock: Callable[[], str] = _now_utc,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """Append one event.  All failures are isolated from the caller."""
    if not enabled() or event_type not in EVENT_TYPES:
        return None
    try:
        event = {
            "event_id": uuid.uuid4().hex,
            "draw_id": str(draw_id) if draw_id not in (None, "") else None,
            "timestamp_utc": utc_clock(),
            "monotonic_timestamp": monotonic_clock(),
            "instance_id": os.environ.get("RENDER_INSTANCE_ID", socket.gethostname()),
            "pid": os.getpid(),
            "process_start_time": _PROCESS_STARTED_UTC,
            "process_start_monotonic": _PROCESS_STARTED_MONOTONIC,
            "job_id": getattr(_LOCAL, "job_id", None),
            "event_type": event_type,
            "dataset_sha256": dataset_sha256,
            "runtime_commit": _runtime_commit(),
            "details": details or {},
        }
        event["event_sha256"] = hashlib.sha256(_canonical(event)).hexdigest()
        target = path or _default_path()
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return event
    except Exception:
        return None


def availability(analysis: dict[str, Any]) -> dict[str, Any]:
    ranking = analysis.get("ranking")
    ranking_ok = isinstance(ranking, list) and len(ranking) == 39 and {int(x.get("number", 0)) for x in ranking if isinstance(x, dict)} == set(range(1, 40))
    scores_ok = ranking_ok and all(isinstance(x.get("score"), (int, float)) for x in ranking)
    tiers = analysis.get("candidateTiers") or {}
    stats = analysis.get("statistics") or {}
    window_values = stats.get("windowFrequencies") or {}
    window_status = {str(w): ("AVAILABLE_IN_MEMORY" if str(w) in window_values or w in window_values else "RECOMPUTE_REQUIRED") for w in (5, 10, 20, 30, 45, 60, 90)}
    state = analysis.get("stateDetection") if isinstance(analysis.get("stateDetection"), dict) else None
    regime_status = "AVAILABLE_IN_MEMORY" if state and all(k in state for k in ("regime_id", "regime_confidence", "change_probability", "change_point_state")) else "NOT_AVAILABLE"
    feature_value = analysis.get("featureImportance")
    feature_status = "PARTIAL" if isinstance(feature_value, dict) and feature_value else "NOT_AVAILABLE"
    return {
        "full_ranking_available": ranking_ok,
        "final_scores_available": scores_ok,
        "top5_available": len(tiers.get("top5") or analysis.get("recommendation") or []) == 5,
        "top10_available": len(tiers.get("top10") or []) == 10,
        "top15_available": len(tiers.get("full15") or []) == 15,
        "window_state_availability": window_status,
        "regime_availability": regime_status,
        "per_number_feature_availability": feature_status,
    }


def prediction_finalized(draw_id: Any, dataset_sha256: str | None, analysis: dict[str, Any], *, path: Path | None = None) -> dict[str, Any] | None:
    with _LOCK:
        _PENDING_TARGETS.add(str(draw_id))
    return emit("RANKING_FINALIZED", draw_id=draw_id, dataset_sha256=dataset_sha256, details=availability(analysis), path=path)


def capture_if_before_actual(draw_id: Any, callback: Callable[[], Any], *, path: Path | None = None) -> tuple[str, Any]:
    """Serialize capture against actual observation and inspect persisted evidence."""
    key = str(draw_id)
    with _LOCK:
        prior_actual = key in _SEEN_ACTUALS or any(
            event.get("event_type") == "ACTUAL_FIRST_OBSERVED" and str(event.get("draw_id")) == key
            for event in read_events(path or _default_path())
        )
        if prior_actual:
            return "TOO_LATE_FOR_FORWARD_CAPTURE", None
        return "FORWARD_CAPTURED_PENDING_ACTUAL", callback()


def observe_actuals(rows: list[dict[str, Any]], *, source: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Record the first in-process observation of the newest Actual watermark."""
    if not enabled():
        return []
    found = []
    emit("ACTUAL_CHECK_STARTED", details={"actual_source": source, "rows_observed": len(rows)}, path=path)
    with _LOCK:
        valid_rows = [row for row in rows if str(row.get("period") or row.get("draw_id") or "")]
        newest = max(valid_rows, key=lambda row: int(str(row.get("period") or row.get("draw_id"))) if str(row.get("period") or row.get("draw_id")).isdigit() else -1, default=None)
        for row in ([newest] if newest is not None else []):
            draw_id = str(row.get("period") or row.get("draw_id") or "")
            if not draw_id or draw_id in _SEEN_ACTUALS:
                continue
            numbers = row.get("numbers")
            if not isinstance(numbers, list) or len(numbers) != 5:
                continue
            _SEEN_ACTUALS.add(draw_id)
            details = {
                "actual_source": source,
                "actual_draw_id": draw_id,
                "actual_numbers_hash": hashlib.sha256(_canonical(sorted(map(int, numbers)))).hexdigest(),
                "actual_observed_at": _now_utc(),
                "prediction_pending_at_observation": draw_id in _PENDING_TARGETS,
            }
            event = emit("ACTUAL_FIRST_OBSERVED", draw_id=draw_id, details=details, path=path)
            if event:
                found.append(event)
    return found


def read_events(path: Path) -> list[dict[str, Any]]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except (TypeError, ValueError):
            continue
    return records


def capture_windows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = {e.get("draw_id"): e for e in events if e.get("event_type") == "RANKING_FINALIZED"}
    results = []
    for event in events:
        if event.get("event_type") != "ACTUAL_FIRST_OBSERVED" or event.get("draw_id") not in finalized:
            continue
        start = finalized[event["draw_id"]]
        same_process = start.get("pid") == event.get("pid") and start.get("process_start_time") == event.get("process_start_time")
        results.append({
            "draw_id": event["draw_id"],
            "same_process": same_process,
            "capture_window_ms": (event["monotonic_timestamp"] - start["monotonic_timestamp"]) * 1000 if same_process else None,
            "ordering": "PREDICTION_FIRST" if same_process and event["monotonic_timestamp"] > start["monotonic_timestamp"] else "UNPROVEN",
        })
    return results
