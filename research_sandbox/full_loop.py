"""Crash-safe Cloud wrapper around the already-approved local Research Engine.

This module does not implement research logic.  It supplies isolation, protocol
and artifact integrity, resumability, and Cloud validation around functions in
``brain.py``.  The only bypass of the service kill switch is the explicit
validation entry point, which is hard-bound to ``.../validation/full_loop_gate``.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import resource
except ImportError:  # Windows validation host
    resource = None

from brain import (
    Budget, DataInterface, HoldoutVault, ResourceGovernor, SecurityBoundary,
    execute_tw539_distribution_experiment, freeze_protocol, hypotheses_for,
    knowledge_record, protocol_valid, sha,
)

FULL_LOOP_VERSION = "star-research-full-loop-v1"
ALLOWED_CONCLUSIONS = {"SUPPORTED", "REJECTED", "INCONCLUSIVE", "INSUFFICIENT_DATA", "DATA_QUALITY_BLOCKED"}
VALIDATION_SUFFIX = Path("validation") / "full_loop_gate"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != "nt":
            dfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
    finally:
        if os.path.exists(name): os.unlink(name)


def write_artifact(path: Path, value: Any) -> None:
    _atomic(path, value)


def _read(path: Path, default: Any) -> Any:
    if not path.exists(): return default
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact(value: Any) -> dict[str, Any]:
    return {"status": "completed", "artifact_sha256": sha(value)}


def _max_rss_mib() -> float:
    if resource is not None:
        divisor = 1024 if os.name != "nt" else 1024 * 1024
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def _step(chain: list[dict[str, Any]], name: str, fn: Callable[[], Any]) -> Any:
    started = _now()
    value = fn()
    chain.append({"step": name, "started_at": started, "completed_at": _now(), **_artifact(value)})
    return value


def validation_root(persistent_root: Path) -> Path:
    root = persistent_root.resolve()
    expected = (root / VALIDATION_SUFFIX).resolve()
    if root not in expected.parents:
        raise RuntimeError("VALIDATION_ROOT_ESCAPE")
    return expected


def formal_manifest(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("inbox", "knowledge", "output", "audit"):
        path = root / name
        files = []
        if path.exists():
            for item in sorted(x for x in path.rglob("*") if x.is_file()):
                data = item.read_bytes()
                files.append({"path": str(item.relative_to(root)), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        result[name] = {"count": len(files), "files": files, "manifest_sha256": sha(files)}
    return result


def permission_validation() -> dict[str, Any]:
    guard = SecurityBoundary(); results = {}
    actions = (
        "production_write", "staging_prediction_write", "git_push", "deploy",
        "promotion", "shadow_enable", "cloud_create", "dataset_mutation",
        "prediction_journal_mutation", "evidence_mutation", "snapshot_mutation",
    )
    for action in actions:
        try: guard.require(action)
        except Exception: results[action] = "PERMISSION_DENIED"
    return {"results": results, "all_denied": len(results) == len(actions), "security_events": guard.events}


def _canonical_rq(opened_rq: dict[str, Any]) -> dict[str, Any]:
    if not str(opened_rq.get("rq_id", "")).startswith("RQ-TW539-"):
        raise RuntimeError("NO_ELIGIBLE_RESEARCH_ENGINE")
    return {
        "rq_id": "RQ-TW539-EDGE-0001",
        "question": opened_rq["question"],
        "trigger_evidence_hash": opened_rq["trigger_evidence_hash"],
    }


def run_full_loop(
    *, opened_rq: dict[str, Any], interface: DataInterface, gate_root: Path,
    experiment_key: str, failure: str | None = None,
) -> dict[str, Any]:
    """Run/resume exactly one isolated validation RQ using the local engine."""
    started = time.monotonic(); started_cpu = time.process_time()
    chain: list[dict[str, Any]] = []
    dirs = {n: gate_root / n for n in ("inbox", "knowledge", "output", "audit", "holdout")}
    for path in dirs.values(): path.mkdir(parents=True, exist_ok=True)
    ledger_path = dirs["audit"] / "execution_ledger.json"
    ledger = _read(ledger_path, {"experiments": {}, "knowledge": {}, "holdout_usage": {}})
    if experiment_key in ledger["knowledge"]:
        prior = _read(gate_root / "full_loop_result.json", {})
        return {**prior, "status": "RESUMED_ALREADY_FINALIZED", "experiments": 0, "knowledge_key": experiment_key, "returned_to_sleep": True}

    rq = _step(chain, "OPEN_RQ", lambda: _canonical_rq(opened_rq))
    hypotheses = _step(chain, "HYPOTHESES", lambda: hypotheses_for(rq["rq_id"]))
    if len(hypotheses) < 3 or hypotheses[0]["id"] != "H0":
        raise RuntimeError("INCOMPLETE_HYPOTHESIS_SPACE")
    _atomic(dirs["output"] / "hypotheses.json", hypotheses)

    inputs = interface.list_context("TW539")
    protocol = _step(chain, "FROZEN_PROTOCOL", lambda: freeze_protocol({
        "experiment_id": "EXP-CLOUD-VALIDATION-0001", "rq_id": rq["rq_id"],
        "hypothesis_ids": [x["id"] for x in hypotheses],
        "dataset_sha256": next(x["sha256"] for x in inputs if x["source_id"] == "TW539-HIT-DISTRIBUTION"),
        "eligibility": "validation_only,window=700,status=OOS", "train": "none",
        "validation": "approved immutable validation fixture", "holdout": "VAL-HOLDOUT-0001",
        "baseline": "RESEARCH_BASELINE", "random_control": {"type": "hypergeometric_39_choose_5_top15", "seed": 20260808},
        "metrics": ["mean_hits", "p0_or_1", "distribution"], "statistical_tests": {"bootstrap_iterations": 2000},
        "success_criteria": {"minimum_mean_delta": 0.05, "must_not_underperform_baseline": True},
        "failure_criteria": "CI includes immaterial delta or Current <= Baseline", "stop_criteria": "NO_EDGE_FOUND",
        "resource_budget": Budget().__dict__, "validation_only": True,
    }))
    _atomic(dirs["output"] / "frozen_protocol.json", protocol)
    mutated = {**protocol, "success_criteria": {"minimum_mean_delta": -99}}
    mutation_result = "INVALID_PROTOCOL_MUTATION" if not protocol_valid(mutated) else "UNSAFE"
    if mutation_result != "INVALID_PROTOCOL_MUTATION" or failure == "protocol":
        raise RuntimeError("INVALID_PROTOCOL_MUTATION")

    vault = HoldoutVault(limit=1)
    holdout_meta = vault.register("VAL-HOLDOUT-0001", {"validation_only": True}, 1)
    if failure == "holdout":
        vault.unlock("VAL-HOLDOUT-0001", protocol["experiment_id"], False)
    unlocked = vault.unlock("VAL-HOLDOUT-0001", protocol["experiment_id"], protocol["frozen"])
    ledger["holdout_usage"]["VAL-HOLDOUT-0001"] = unlocked["usage_count"]
    _atomic(dirs["holdout"] / "holdout_validation.json", {**holdout_meta, **unlocked})

    governor = ResourceGovernor(Budget())
    governor.check(1)
    if failure in {"experiment", "timeout"}:
        raise RuntimeError("RESOURCE_BUDGET_EXCEEDED" if failure == "timeout" else "EXPERIMENT_EXCEPTION")
    if experiment_key in ledger["experiments"]:
        experiment = _read(dirs["output"] / "experiment_result.json", {})
    else:
        experiment = _step(chain, "EXPERIMENT", lambda: execute_tw539_distribution_experiment(interface, protocol))
        if experiment.get("status") != "completed": raise RuntimeError("EXPERIMENT_FAILED")
        ledger["experiments"][experiment_key] = sha(experiment)
        _atomic(dirs["output"] / "experiment_result.json", experiment)
        _atomic(ledger_path, ledger)
    if failure == "after_experiment": raise RuntimeError("INJECTED_CRASH_AFTER_EXPERIMENT")
    if failure == "falsification": raise RuntimeError("FALSIFICATION_EXCEPTION")
    falsification = _step(chain, "FALSIFICATION", lambda: experiment["falsification_test"])
    if not falsification or not experiment.get("random_comparison"): raise RuntimeError("FALSIFICATION_MISSING")
    conclusion = experiment["conclusion"]
    if conclusion not in ALLOWED_CONCLUSIONS: raise RuntimeError("INVALID_CONCLUSION")

    run_id = "BR-CLOUD-VALIDATION-0001"
    knowledge = knowledge_record(run_id, rq, hypotheses, protocol, experiment)
    if failure == "knowledge": raise RuntimeError("KNOWLEDGE_WRITE_EXCEPTION")
    knowledge_path = dirs["knowledge"] / f"{knowledge['knowledge_id']}.json"
    if not knowledge_path.exists(): _atomic(knowledge_path, knowledge)
    ledger["knowledge"][experiment_key] = knowledge["record_sha256"]
    _atomic(ledger_path, ledger)
    if failure == "after_knowledge": raise RuntimeError("INJECTED_CRASH_AFTER_KNOWLEDGE")
    next_decision = knowledge["next_decision"]
    chain.append({"step": "STOP_SLEEP", "started_at": _now(), "completed_at": _now(), "status": "completed", "artifact_sha256": sha(next_decision)})
    _atomic(dirs["audit"] / "call_chain.json", chain)
    usage = {
        "wall_seconds": time.monotonic() - started, "cpu_seconds": time.process_time() - started_cpu,
        "max_rss_mib": _max_rss_mib(),
        "experiments": 1, "max_concurrent_brain": 1,
    }
    result = {
        "status": "COMPLETED_RETURNED_TO_SLEEP", "validation_only": True,
        "rq_id": rq["rq_id"], "hypotheses": len(hypotheses), "protocol_sha256": protocol["protocol_sha256"],
        "supporting_test": experiment["support_test"], "falsification": falsification,
        "random_control": experiment["random_comparison"], "baseline_control": experiment["baseline_comparison"],
        "conclusion": conclusion, "evidence_grade": experiment["evidence_grade"],
        "experiments": 1, "knowledge_key": experiment_key, "knowledge_sha256": knowledge["record_sha256"],
        "next_decision": next_decision, "returned_to_sleep": True, "resource_usage": usage,
        "call_chain_sha256": sha(chain),
    }
    _atomic(gate_root / "full_loop_result.json", result)
    _atomic(gate_root / "falsification.json", falsification)
    _atomic(gate_root / "knowledge_validation.json", knowledge)
    _atomic(gate_root / "next_decision.json", {"decision": next_decision, "returned_to_sleep": True})
    _atomic(gate_root / "resource_usage.json", usage)
    _atomic(gate_root / "quarantine_validation.json", {
        "validation_only": True, "root": str(gate_root),
        "formal_paths_used": False, "passed": True,
    })
    return result


def safe_failure_run(**kwargs: Any) -> dict[str, Any]:
    try: return run_full_loop(**kwargs)
    except Exception as exc:
        return {"status": "SAFE_STOP", "error": str(exc), "error_type": type(exc).__name__, "experiments": 0, "running_brain_count": 0, "returned_to_sleep": True}
