"""Frozen Candidate A tail adapter.

This module is imported only after Current is published and only when the
explicit Shadow flag is enabled. It owns no worker, queue, process or thread.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

EXPECTED_CONFIG_HASH = "b49be8a60a7ed45a014ed4f2e4f5f00216b5966865501b2b07a7c10973182240"
CANDIDATE_VERSION = "v2-shadow-candidate-a-1.0.0"
CONFIG_PATH = Path(__file__).resolve().parent / "frozen_shadow_test" / "candidate_a.json"
JOURNAL_NAME = "v2_shadow_journal.json"
_journal_lock = threading.Lock()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class _FrozenList(tuple):
    def __eq__(self, other: Any) -> bool:
        return tuple(self) == tuple(other) if isinstance(other, (list, tuple)) else False


def _readonly(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_readonly(item) for item in value)
    return value


def load_candidate_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    payload = {key: item for key, item in value.items() if key != "definition_sha256"}
    if value.get("definition_sha256") != EXPECTED_CONFIG_HASH or canonical_hash(payload) != EXPECTED_CONFIG_HASH:
        raise ValueError("Candidate A definition hash mismatch")
    if value.get("version") != CANDIDATE_VERSION or value.get("immutable") is not True:
        raise ValueError("Candidate A definition is not frozen")
    expected_removed = ["recent30", "oddBalance", "sizeBalance", "previousRepeat", "primeBalance"]
    if value.get("removed_features") != expected_removed:
        raise ValueError("Candidate A removed-feature contract mismatch")
    return _readonly(value)


def persistent_journal_path(environ: dict[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    raw = source.get("LOTTO_PERSISTENT_DATA_DIR", "").strip()
    if not raw:
        raise RuntimeError("Shadow skipped: LOTTO_PERSISTENT_DATA_DIR is missing")
    root = Path(raw)
    if not root.is_absolute():
        raise RuntimeError("Shadow skipped: persistent path must be absolute")
    return root / "shadow" / JOURNAL_NAME


def _tiers(ranking: list[int]) -> dict[str, list[int]]:
    if len(ranking) < 15 or len(set(ranking)) != len(ranking):
        raise ValueError("Shadow ranking must contain at least 15 unique numbers")
    return {"top5": ranking[:5], "top10": ranking[:10], "top15": ranking[:15]}


class ShadowJournal:
    """Atomic and deduplicated journal, fully separate from Production data."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": "v2-shadow-journal-v1", "records": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Shadow Journal isolated as unreadable: {exc}") from exc
        if value.get("schema") != "v2-shadow-journal-v1" or not isinstance(value.get("records"), dict):
            raise RuntimeError("Shadow Journal isolated due to invalid schema")
        return value

    def _save(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with temporary.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        # Render/Linux must durably sync the directory entry. Windows does not
        # permit opening a directory through os.open, so its test path stops
        # after the durable file fsync + atomic os.replace contract.
        if os.name != "nt":
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    @staticmethod
    def key(lottery: str, draw_id: str, candidate_version: str = CANDIDATE_VERSION) -> str:
        return f"{lottery}|{draw_id}|{candidate_version}"

    def record(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = self.key(record["lottery"], record["draw_id"], record["candidate_version"])
        with _journal_lock:
            data = self._load()
            if key in data["records"]:
                return deepcopy(data["records"][key]), False
            data["records"][key] = deepcopy(record)
            self._save(data)
            return deepcopy(record), True

    def settle(self, key: str, actual: list[int], settled_at: str) -> dict[str, Any]:
        with _journal_lock:
            data = self._load()
            record = data["records"][key]
            if record.get("status") == "invalid":
                raise ValueError("invalid Shadow record cannot be settled")
            if canonical_hash(record["prediction"]) != record.get("prediction_hash"):
                raise RuntimeError("Shadow prediction integrity error")
            if record.get("actual") is not None:
                if record["actual"] != actual:
                    raise ValueError("Shadow settlement is immutable")
                return deepcopy(record)
            actual_set = set(actual)
            record["actual"] = list(actual)
            record["candidate_hits"] = {tier: len(set(values) & actual_set) for tier, values in record["prediction"].items()}
            record["baseline_hits"] = {tier: len(set(values) & actual_set) for tier, values in record["baseline_prediction"].items()}
            record["settled_at"] = settled_at
            record["status"] = "settled"
            self._save(data)
            return deepcopy(record)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _runner_payload(value: Any, *, game: str, draw_id: str, version: str) -> tuple[dict[str, list[int]], dict[str, Any]]:
    """Normalize real runner contracts while retaining test-injection compatibility."""
    if hasattr(value, "prediction") and hasattr(value, "to_dict"):
        return _tiers(list(value.prediction)), value.to_dict()
    tiers = _tiers(list(value))
    return tiers, {
        "lottery": game, "draw_id": draw_id, "version": version,
        "prediction": list(value), "top5": tiers["top5"], "top10": tiers["top10"],
        "top15": tiers["top15"], "status": "completed", "latency_ms": None,
    }


def run_shadow_tail(
    *,
    game: str,
    current_result: dict[str, Any],
    current_completed_at: str,
    candidate_runner: Callable[..., Any],
    baseline_runner: Callable[..., Any],
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run Candidate then baseline sequentially in the caller's worker/flock."""
    config = load_candidate_config()
    journal_path = persistent_journal_path(environ)
    from shadow_runners import build_analysis_context
    context = build_analysis_context(game, current_result)
    latest = current_result.get("latest") or {}
    draw_id = str(latest.get("period") or latest.get("drawId") or latest.get("date") or "")
    if not draw_id:
        raise ValueError("Shadow draw_id is missing")
    candidate_result = baseline_result = None
    candidate_error = baseline_error = None
    try:
        candidate_result = candidate_runner(context, current_result, config)
    except Exception as exc:
        candidate_error = {"type": type(exc).__name__, "message": str(exc)}
    try:
        baseline_result = baseline_runner(context, current_result)
    except Exception as exc:
        baseline_error = {"type": type(exc).__name__, "message": str(exc)}
    if candidate_result is None and baseline_result is None:
        raise RuntimeError("all Shadow runners failed")
    candidate, candidate_data = _runner_payload(candidate_result, game=game, draw_id=draw_id, version=config["version"]) if candidate_result is not None else (None, None)
    baseline, baseline_data = _runner_payload(baseline_result, game=game, draw_id=draw_id, version="uniform") if baseline_result is not None else (None, None)
    created_at = _iso_now()
    data_complete = bool((current_result.get("dataStatus") or {}).get("validated", True))
    official_draw_time = current_result.get("_shadow_official_draw_time")
    late = bool(official_draw_time and created_at >= str(official_draw_time))
    status = "invalid" if late or not data_complete else "locked"
    invalid_reason = "late_prediction" if late else "incomplete_source_data" if not data_complete else None
    prediction_hash = canonical_hash(candidate) if candidate is not None else None
    record = {
        "lottery": game,
        "draw_id": draw_id,
        "candidate_version": config["version"],
        "candidate_config_sha256": config["definition_sha256"],
        "prediction": candidate,
        "prediction_hash": prediction_hash,
        "baseline_prediction": baseline,
        "candidate_result": candidate_data,
        "baseline_result": baseline_data,
        "candidate_status": candidate_data.get("status") if candidate_data else "failed",
        "baseline_status": baseline_data.get("status") if baseline_data else "failed",
        "candidate_latency_ms": candidate_data.get("latency_ms") if candidate_data else None,
        "baseline_latency_ms": baseline_data.get("latency_ms") if baseline_data else None,
        "candidate_error": candidate_error,
        "baseline_error": baseline_error,
        "runtime_version": context.runtime_version,
        "context_dataset_sha256": context.dataset_sha256,
        "created_at": created_at,
        "current_completed_at": current_completed_at,
        "shadow_started_at": created_at,
        "shadow_completed_at": _iso_now(),
        "status": status,
        "invalid_reason": invalid_reason,
        "actual": None,
        "candidate_hits": None,
        "baseline_hits": None,
        "settled_at": None,
    }
    saved, inserted = ShadowJournal(journal_path).record(record)
    return {
        "record": saved, "inserted": inserted, "path": str(journal_path),
        "candidate_error": candidate_error, "baseline_error": baseline_error,
    }
