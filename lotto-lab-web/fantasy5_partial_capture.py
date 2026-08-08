"""Default-off, observer-only Fantasy 5 partial pre-draw capture."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PARTIAL_FLAG = "FANTASY5_FORWARD_PARTIAL_CAPTURE_ENABLED"
WINDOW_FLAG = "FANTASY5_WINDOW_STATE_OBSERVER_ENABLED"
REGIME_FLAG = "FANTASY5_REGIME_OBSERVER_ENABLED"
FEATURE_FLAG = "FANTASY5_FEATURE_STATE_OBSERVER_ENABLED"
SNAPSHOT_VERSION = "fantasy5-partial-v1"
WINDOWS = (5, 10, 20, 30, 45, 60, 90)
_LOCK = threading.RLock()


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _persistent_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
    else:
        value = os.environ.get("LOTTO_PERSISTENT_DATA_DIR", "")
        if not value or not Path(value).is_absolute():
            raise RuntimeError("absolute LOTTO_PERSISTENT_DATA_DIR is required")
        root = Path(value)
    return root / "fantasy5_forward_partial"


class CrossProcessLock:
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

    def __exit__(self, *_args):
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0); msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _read_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SNAPSHOT_VERSION, "records": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise ValueError("invalid journal shape")
        return value
    except Exception as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        isolated = path.with_name(f"{path.name}.corrupt.{stamp}")
        os.replace(path, isolated)
        raise RuntimeError(f"corrupted snapshot journal isolated: {isolated.name}") from exc


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
    finally:
        if os.path.exists(name): os.unlink(name)


def _ranking(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    ranking = analysis.get("ranking")
    if not isinstance(ranking, list) or len(ranking) != 39:
        raise ValueError("complete 1-39 ranking is required")
    normalized = [{"number": int(x["number"]), "rank": int(x["rank"]), "score": float(x["score"])} for x in ranking]
    if {x["number"] for x in normalized} != set(range(1, 40)) or {x["rank"] for x in normalized} != set(range(1, 40)):
        raise ValueError("ranking universe or ranks are incomplete")
    return sorted(normalized, key=lambda x: x["rank"])


def build_partial_snapshot(draw_id: Any, analysis: dict[str, Any], *, dataset_sha256: str, data_cutoff_draw_id: Any, source_quality: dict[str, Any] | None = None, captured_at: str | None = None, window_state: dict[str, Any] | None = None) -> dict[str, Any]:
    if int(str(draw_id)) <= int(str(data_cutoff_draw_id)):
        raise ValueError("HISTORICAL_BACKFILL_FORBIDDEN")
    ranking = _ranking(analysis)
    tiers, quality = analysis.get("candidateTiers") or {}, source_quality or {}
    top5 = list(tiers.get("top5") or analysis.get("recommendation") or [])
    top10, top15 = list(tiers.get("top10") or []), list(tiers.get("full15") or [])
    if (len(top5), len(top10), len(top15)) != (5, 10, 15): raise ValueError("complete Top5/10/15 is required")
    now = captured_at or _utc_now()
    core = {
        "lottery": "fantasy5", "draw_id": str(draw_id), "captured_at": now, "locked_at": now,
        "locked": True, "snapshot_version": SNAPSHOT_VERSION,
        "dataset_version": str(analysis.get("metadata", {}).get("datasetVersion") or analysis.get("modelVersion") or "unknown"),
        "dataset_sha256": dataset_sha256,
        "runtime_version": os.environ.get("RENDER_GIT_COMMIT", os.environ.get("GIT_COMMIT", "unknown")),
        "model_version": str(analysis.get("modelVersion", "unknown")),
        "full_ranking_1_to_39": ranking,
        "final_score_by_number": {str(x["number"]): x["score"] for x in ranking},
        "top5": top5, "top10": top10, "top15": top15, "source_quality": quality,
        "verified_history_count": int(quality.get("verifiedCount", 95)),
        "provisional_history_count": int(quality.get("provisionalCount", 275)),
        "snapshot_type": "PARTIAL_SNAPSHOT", "attribution_status": "PARTIAL_SNAPSHOT",
        "window_state": window_state or {"availability": "NOT_MATERIALIZED"},
        "regime_state": "NOT_MATERIALIZED", "feature_state": "NOT_CAPTURED",
        "timing_classification": "FORWARD_CAPTURED_PENDING_ACTUAL",
        "instance_id": os.environ.get("RENDER_INSTANCE_ID", socket.gethostname()), "pid": os.getpid(),
    }
    core["snapshot_sha256"] = _sha(core)
    return core


def append_snapshot(snapshot: dict[str, Any], *, test_directory: Path | None = None) -> dict[str, Any]:
    root = _persistent_root(test_directory); path = root / "partial_snapshot_journal.json"
    key = f"fantasy5|{snapshot['draw_id']}|{snapshot['snapshot_version']}"
    with _LOCK, CrossProcessLock(root / "partial_snapshot_journal.lock"):
        journal = _read_journal(path)
        for record in journal["records"]:
            if record.get("unique_key") == key:
                return {"status": "duplicate", "record": record, "records_added": 0, "journal_path": str(path)}
        record = {"unique_key": key, **snapshot}; journal["records"].append(record)
        journal["journal_sha256"] = _sha(journal["records"]); _atomic_write(path, journal)
        return {"status": "captured", "record": record, "records_added": 1, "journal_path": str(path)}


def capture_finalized(draw_id: Any, analysis: dict[str, Any], *, dataset_sha256: str, data_cutoff_draw_id: Any, source_quality: dict[str, Any] | None = None, history: list[dict[str, Any]] | None = None, lifecycle_path: Path | None = None, test_directory: Path | None = None) -> dict[str, Any]:
    if not _enabled(PARTIAL_FLAG): return {"status": "disabled", "records_added": 0}
    try:
        import fantasy5_lifecycle_telemetry as lifecycle
        if not lifecycle.enabled():
            return {"status": "TIMING_OBSERVER_UNAVAILABLE", "records_added": 0}
        def commit():
            window_state = materialize_window_state(history or [], dataset_sha256=dataset_sha256)
            return append_snapshot(build_partial_snapshot(draw_id, analysis, dataset_sha256=dataset_sha256, data_cutoff_draw_id=data_cutoff_draw_id, source_quality=source_quality, window_state=window_state), test_directory=test_directory)
        timing, result = lifecycle.capture_if_before_actual(draw_id, commit, path=lifecycle_path)
        return {"status": timing, "records_added": 0} if result is None else {**result, "timing_classification": timing}
    except Exception as exc:
        return {"status": "observer_error", "records_added": 0, "error_type": type(exc).__name__}


def materialize_window_state(history: list[dict[str, Any]], *, dataset_sha256: str, captured_at: str | None = None) -> dict[str, Any]:
    if not _enabled(WINDOW_FLAG): return {"availability": "NOT_MATERIALIZED"}
    ordered = sorted(history, key=lambda r: (str(r.get("date", "")), int(str(r.get("period", 0)))))
    states = {}
    for window in WINDOWS:
        sample, frequency, overlaps = ordered[-window:], {str(n): 0 for n in range(1, 40)}, []
        for i, row in enumerate(sample):
            values = {int(n) for n in row.get("numbers", [])}
            for n in values:
                if 1 <= n <= 39: frequency[str(n)] += 1
            if i: overlaps.append(len(values & {int(n) for n in sample[i - 1].get("numbers", [])}))
        state = {"draw_count": len(sample), "frequency": frequency, "repeat_rate": sum(overlaps) / max(1, len(overlaps) * 5), "number_turnover": 1 - sum(overlaps) / max(1, len(overlaps) * 5)}
        state["state_sha256"] = _sha(state); states[str(window)] = state
    bundle = {"availability": "MATERIALIZED_PRE_ACTUAL", "captured_at": captured_at or _utc_now(), "algorithm_version": "fantasy5-window-observer-v1", "config_hash": _sha({"windows": WINDOWS, "formula": "frequency-repeat-turnover"}), "input_dataset_sha": dataset_sha256, "windows": states}
    bundle["state_sha256"] = _sha(bundle); return bundle


def regime_observer_design() -> dict[str, Any]:
    return {"availability": "NOT_MATERIALIZED", "reason": "Exploratory regime algorithm is not frozen; implementation is forbidden.", "required_output": ["regime_id", "regime_confidence", "change_probability", "change_point_state", "algorithm_version", "config_hash"]}


def feature_state_audit() -> dict[str, Any]:
    return {"availability": "NOT_MATERIALIZED", "available_during_scoring_then_discarded": ["freq_5", "freq_7", "freq_14", "freq_30", "freq_100", "freq_300", "gap", "repeat_prev", "repeat_2", "pair", "tail", "odd", "size", "zone", "sum", "span", "ac", "same_tail", "consecutive", "weekday_pacific", "dst_pacific", "month_pacific"], "capture_status": "DEFERRED_REQUIRES_NONINVASIVE_SCORING_CALLBACK"}


def classify_settlement_timing(snapshot: dict[str, Any], actual_event: dict[str, Any]) -> str:
    same_process = snapshot.get("instance_id") == actual_event.get("instance_id") and snapshot.get("pid") == actual_event.get("pid")
    if not same_process: return "FORWARD_CAPTURED_TIME_ORDER_UNPROVEN"
    captured = datetime.fromisoformat(str(snapshot["captured_at"]).replace("Z", "+00:00"))
    observed = datetime.fromisoformat(str(actual_event["timestamp_utc"]).replace("Z", "+00:00"))
    return "FORWARD_VERIFIED_TIMING" if captured < observed else "TOO_LATE_FOR_FORWARD_CAPTURE"


def append_settlement(snapshot: dict[str, Any], actual_numbers: list[int], actual_event: dict[str, Any], *, test_directory: Path | None = None) -> dict[str, Any]:
    root = _persistent_root(test_directory); path = root / "partial_settlement_journal.json"
    key = f"fantasy5|{snapshot['draw_id']}|{snapshot['snapshot_sha256']}"
    ranks = {int(x["number"]): {"pre_draw_rank": int(x["rank"]), "pre_draw_score": float(x["score"])} for x in snapshot["full_ranking_1_to_39"]}
    record = {
        "unique_key": key, "draw_id": snapshot["draw_id"], "snapshot_sha256": snapshot["snapshot_sha256"],
        "actual_numbers": sorted(int(n) for n in actual_numbers),
        "actual_available_at": actual_event.get("timestamp_utc"), "settled_at": _utc_now(),
        "timing_classification": classify_settlement_timing(snapshot, actual_event),
        "actual_number_ranks": {str(n): ranks[n] for n in sorted(int(x) for x in actual_numbers)},
    }
    record["settlement_sha256"] = _sha(record)
    with _LOCK, CrossProcessLock(root / "partial_settlement_journal.lock"):
        journal = _read_journal(path)
        for existing in journal["records"]:
            if existing.get("unique_key") == key:
                return {"status": "duplicate", "record": existing, "records_added": 0}
        journal["records"].append(record); journal["journal_sha256"] = _sha(journal["records"]); _atomic_write(path, journal)
    return {"status": "settled", "record": record, "records_added": 1}


def settle_observed_actuals(rows: list[dict[str, Any]], events: list[dict[str, Any]], *, test_directory: Path | None = None) -> list[dict[str, Any]]:
    """Append settlements only for already-captured snapshots; never backfill."""
    if not _enabled(PARTIAL_FLAG) or not events: return []
    try:
        root = _persistent_root(test_directory); snapshots = _read_journal(root / "partial_snapshot_journal.json")["records"]
        by_draw = {str(x["draw_id"]): x for x in snapshots}
        row_by_draw = {str(r.get("period") or r.get("draw_id")): r for r in rows}
        results = []
        for event in events:
            draw = str(event.get("draw_id")); snapshot, row = by_draw.get(draw), row_by_draw.get(draw)
            if snapshot and row and len(row.get("numbers") or []) == 5:
                results.append(append_settlement(snapshot, row["numbers"], event, test_directory=test_directory))
        return results
    except Exception:
        return []
