"""Deterministic, local-only autonomous research sandbox."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "star-research-sandbox-v1"
ALLOWED_CONTEXTS = {"TW539", "FANTASY5"}
DENIED_ACTIONS = {
    "production_write", "staging_prediction_write", "deploy", "git_push",
    "promotion", "shadow_enable", "cloud_create", "formal_journal_write",
    "formal_dataset_write", "dataset_mutation", "prediction_journal_mutation",
    "evidence_mutation", "snapshot_mutation",
}
MAX_EXPERIMENTS = 3


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PermissionDenied(RuntimeError):
    pass


class SecurityBoundary:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def require(self, action: str) -> None:
        if action in DENIED_ACTIONS:
            event = {"at": utc_now(), "action": action, "status": "PERMISSION_DENIED"}
            self.events.append(event)
            raise PermissionDenied(action)


class DataInterface:
    def __init__(self, repo_root: Path, manifest: Path):
        self.repo_root = repo_root.resolve()
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        self.sources = {x["source_id"]: x for x in raw["sources"]}

    def list_context(self, context: str) -> list[dict[str, Any]]:
        if context not in ALLOWED_CONTEXTS:
            raise ValueError("INVALID_CONTEXT")
        result = []
        for item in self.sources.values():
            if item["lottery"] != context:
                continue
            path = (self.repo_root / item["path"]).resolve()
            if self.repo_root not in path.parents or not path.is_file():
                continue
            result.append({**item, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "timestamp": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})
        return result

    def read(self, source_id: str, context: str) -> tuple[dict[str, Any], bytes]:
        item = self.sources.get(source_id)
        if not item:
            raise PermissionDenied("UNAPPROVED_INPUT")
        if item["lottery"] != context:
            raise PermissionDenied("CONTEXT_ISOLATION")
        path = (self.repo_root / item["path"]).resolve()
        if self.repo_root not in path.parents or not path.is_file():
            raise PermissionDenied("SOURCE_PATH_INVALID")
        data = path.read_bytes()
        envelope = {**item, "sha256": hashlib.sha256(data).hexdigest(), "timestamp": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}
        return envelope, data


class HoldoutVault:
    def __init__(self, limit: int = 1):
        self.limit = limit
        self.items: dict[str, dict[str, Any]] = {}

    def register(self, holdout_id: str, outcomes: Any, sample_count: int) -> dict[str, Any]:
        self.items[holdout_id] = {"outcomes": outcomes, "sample_count": sample_count, "usage_count": 0, "last_used_at": None, "experiments_used": []}
        return {"holdout_id": holdout_id, "sample_count": sample_count, "locked": True}

    def unlock(self, holdout_id: str, experiment_id: str, protocol_frozen: bool) -> dict[str, Any]:
        if not protocol_frozen:
            raise PermissionDenied("HOLDOUT_LOCKED")
        item = self.items[holdout_id]
        item["usage_count"] += 1
        item["last_used_at"] = utc_now()
        item["experiments_used"].append(experiment_id)
        return {"outcomes": item["outcomes"], "usage_count": item["usage_count"], "contamination_risk": item["usage_count"] > self.limit}


@dataclass(frozen=True)
class Budget:
    max_wall_seconds: float = 10.0
    max_output_bytes: int = 1_000_000
    max_experiments: int = MAX_EXPERIMENTS
    max_rss_mib: int = 512
    max_cpu_seconds: float = 10.0


class ResourceGovernor:
    def __init__(self, budget: Budget):
        self.budget = budget
        self.started = time.monotonic()

    def check(self, experiment_count: int, output: Any | None = None) -> None:
        if experiment_count > self.budget.max_experiments:
            raise RuntimeError("RESOURCE_BUDGET_EXCEEDED")
        if time.monotonic() - self.started > self.budget.max_wall_seconds:
            raise RuntimeError("RESOURCE_BUDGET_EXCEEDED")
        if output is not None and len(canonical(output)) > self.budget.max_output_bytes:
            raise RuntimeError("RESOURCE_BUDGET_EXCEEDED")


def freeze_protocol(protocol: dict[str, Any]) -> dict[str, Any]:
    frozen = {**protocol, "frozen": True}
    frozen["protocol_sha256"] = sha(frozen)
    return frozen


def protocol_valid(protocol: dict[str, Any]) -> bool:
    digest = protocol.get("protocol_sha256")
    return bool(digest) and sha({k: v for k, v in protocol.items() if k != "protocol_sha256"}) == digest


def observe_tw539(interface: DataInterface) -> list[dict[str, Any]]:
    _, concentration_bytes = interface.read("TW539-CONCENTRATION", "TW539")
    concentration = json.loads(concentration_bytes)
    _, random_bytes = interface.read("TW539-RANDOM-THEORY", "TW539")
    random_control = json.loads(random_bytes)
    return [
        {"observation_id": "OBS-TW539-EDGE-0001", "metric": "Current_vs_random_distribution", "severity": 0.95, "confidence": "OOS_700", "information_value": 0.95, "data_quality": "OOS_RESEARCH", "expected": random_control.get("mean"), "observed": "requires deterministic distribution comparison"},
        {"observation_id": "OBS-TW539-DIVERSITY-0001", "metric": "ranking_concentration", "severity": 0.75, "confidence": "OOS_700", "information_value": 0.65, "data_quality": "OOS_RESEARCH", "expected": {"coverage60_min": 30}, "observed": {"coverage60": concentration["rolling_coverage"]["60"], "jaccard": concentration["mean_consecutive_jaccard"]}},
    ]


def questions_from_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions = []
    for obs in observations:
        if obs["metric"] == "Current_vs_random_distribution":
            questions.append({"rq_id": "RQ-TW539-EDGE-0001", "question": "Does Current Top15 have a stable OOS distributional edge over the frozen Random control and research Baseline?", "priority": obs["information_value"] * obs["severity"], "answerable": True})
        else:
            questions.append({"rq_id": "RQ-TW539-DIVERSITY-0001", "question": "Does low Top15 diversity causally increase failure concentration?", "priority": obs["information_value"] * obs["severity"], "answerable": False, "blocker": "per-draw diversity-to-outcome provenance unavailable"})
    return sorted(questions, key=lambda x: (-x["priority"], x["rq_id"]))


def hypotheses_for(rq_id: str) -> list[dict[str, str]]:
    if rq_id != "RQ-TW539-EDGE-0001":
        return []
    return [
        {"id": "H0", "statement": "Current hit distribution is not materially different from Random or Baseline."},
        {"id": "H1", "statement": "Current improves mean hits and failure distribution versus Random and Baseline."},
        {"id": "H2", "statement": "Current differs from Random but underperforms the research Baseline, indicating scoring complexity without edge."},
    ]


def _distribution_rows(data: bytes, window: str = "700") -> dict[str, dict[int, int]]:
    rows = csv.DictReader(data.decode("utf-8-sig").splitlines())
    out: dict[str, dict[int, int]] = {}
    for row in rows:
        if row["window"] == window:
            out.setdefault(row["subject"], {})[int(row["hits"])] = int(row["count"])
    return out


def execute_tw539_distribution_experiment(interface: DataInterface, protocol: dict[str, Any]) -> dict[str, Any]:
    if not protocol_valid(protocol):
        return {"status": "INVALID_PROTOCOL_MUTATION"}
    _, data = interface.read("TW539-HIT-DISTRIBUTION", "TW539")
    _, random_bytes = interface.read("TW539-RANDOM-THEORY", "TW539")
    rows = _distribution_rows(data)
    theory = json.loads(random_bytes)
    current, baseline = rows["current"], rows["baseline"]
    n = sum(current.values())
    mean = lambda d: sum(k * v for k, v in d.items()) / sum(d.values())
    low = lambda d: (d.get(0, 0) + d.get(1, 0)) / sum(d.values())
    random_mean = float(theory["mean"])
    random_low = float(theory["p_0_or_1"])
    current_mean, baseline_mean = mean(current), mean(baseline)
    current_low, baseline_low = low(current), low(baseline)
    # Fixed-seed bootstrap of the aggregate empirical distribution.
    population = [h for h, count in current.items() for _ in range(count)]
    rng = random.Random(protocol["random_control"]["seed"])
    deltas = []
    for _ in range(protocol["statistical_tests"]["bootstrap_iterations"]):
        sample = [population[rng.randrange(n)] for _ in range(n)]
        deltas.append(sum(sample) / n - random_mean)
    deltas.sort()
    ci = [deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas)) - 1]]
    falsified_h1 = ci[0] <= protocol["success_criteria"]["minimum_mean_delta"] or current_mean <= baseline_mean
    conclusion = "REJECTED" if falsified_h1 else "SUPPORTED"
    return {
        "status": "completed", "experiment_id": protocol["experiment_id"], "n": n,
        "current": {"mean": current_mean, "p0_or_1": current_low},
        "random": {"mean": random_mean, "p0_or_1": random_low},
        "baseline": {"mean": baseline_mean, "p0_or_1": baseline_low, "provenance": "RESEARCH_BASELINE"},
        "effect_size_mean_vs_random": current_mean - random_mean,
        "effect_size_mean_vs_baseline": current_mean - baseline_mean,
        "bootstrap_ci95_mean_delta_vs_random": ci,
        "support_test": {"passed": not falsified_h1},
        "falsification_test": {"H1_falsified": falsified_h1, "what_would_prove_me_wrong": "A frozen CI entirely above the material delta and no Baseline loss."},
        "random_comparison": True, "baseline_comparison": True, "multiple_testing_risk": "ONE_PRE_FROZEN_EXPERIMENT",
        "conclusion": conclusion, "evidence_grade": "E2", "edge_status": "NO_EDGE_FOUND" if conclusion == "REJECTED" else "EDGE_CANDIDATE"
    }


def knowledge_record(run_id: str, rq: dict[str, Any], hypotheses: list[dict[str, str]], protocol: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    record = {
        "knowledge_id": "K-TW539-0001", "brain_run_id": run_id, "rq_id": rq["rq_id"],
        "question": rq["question"], "hypotheses": hypotheses, "experiment_id": protocol["experiment_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "experiments": [{"experiment_id": result["experiment_id"], "result_sha256": sha(result)}],
        "falsification": result["falsification_test"],
        "result": result["conclusion"], "evidence_grade": result["evidence_grade"],
        "what_worked": "Deterministic OOS distribution, Random, Baseline, and fixed-seed bootstrap comparison.",
        "what_failed": "Current did not establish a stable material edge.",
        "limitations": ["Aggregated OOS evidence cannot identify diversity causality.", "No independent untouched holdout."],
        "do_not_repeat": "Do not rerun the same distribution comparison without new OOS evidence or a genuinely independent holdout.",
        "next_questions": ["Revisit diversity causality only after per-draw full-ranking provenance exists."],
        "next_decision": "STOP" if result["conclusion"] == "REJECTED" else "ESCALATE",
    }
    record["record_sha256"] = sha(record)
    return record


def similar_rejected(new_protocol: dict[str, Any], knowledge: list[dict[str, Any]], new_evidence_hashes: list[str]) -> bool:
    for item in knowledge:
        if item.get("result") == "REJECTED" and item.get("rq_id") == new_protocol.get("rq_id") and not new_evidence_hashes:
            return True
    return False


def run_brain(context: str, interface: DataInterface, output_root: Path, *, enabled: bool | None = None, kill_switch: bool | None = None) -> dict[str, Any]:
    enabled = (os.environ.get("RESEARCH_BRAIN_ENABLED", "false").lower() == "true") if enabled is None else enabled
    kill_switch = (os.environ.get("RESEARCH_BRAIN_KILL_SWITCH", "false").lower() == "true") if kill_switch is None else kill_switch
    if not enabled:
        return {"status": "DISABLED", "experiments": 0}
    if kill_switch:
        return {"status": "KILL_SWITCH_ACTIVE", "experiments": 0}
    if context not in ALLOWED_CONTEXTS:
        raise ValueError("INVALID_CONTEXT")
    run_id = f"BR-{context}-0001"
    started = utc_now()
    inputs = interface.list_context(context)
    if context == "FANTASY5":
        result = {
            "brain_run_id": run_id, "lottery_context": context, "status": "DATA_QUALITY_BLOCKED",
            "reason": "Verified=95 and Forward Verified=0; regime evidence is data-confidence-sensitive.",
            "evidence_grade": "E1", "experiments": 0, "next_decision": "STOP",
            "retrain": "FORBIDDEN", "inputs": inputs,
        }
    else:
        observations = observe_tw539(interface)
        queue = questions_from_observations(observations)
        rq = next(x for x in queue if x["answerable"])
        hypotheses = hypotheses_for(rq["rq_id"])
        if len(hypotheses) < 3:
            return {"status": "INCOMPLETE_HYPOTHESIS_SPACE", "experiments": 0}
        protocol = freeze_protocol({
            "experiment_id": "EXP-TW539-AUTO-0001", "rq_id": rq["rq_id"], "hypothesis_ids": [x["id"] for x in hypotheses],
            "dataset_sha256": next(x["sha256"] for x in inputs if x["source_id"] == "TW539-HIT-DISTRIBUTION"),
            "eligibility": "window=700,status=OOS", "train": "none", "validation": "700 OOS aggregate", "holdout": "not_used_no_independent_holdout",
            "baseline": "RESEARCH_BASELINE", "random_control": {"type": "hypergeometric_39_choose_5_top15", "seed": 20260808},
            "metrics": ["mean_hits", "p0_or_1", "distribution"], "statistical_tests": {"bootstrap_iterations": 2000},
            "success_criteria": {"minimum_mean_delta": 0.05, "must_not_underperform_baseline": True},
            "failure_criteria": "CI includes immaterial delta or Current <= Baseline", "stop_criteria": "NO_EDGE_FOUND",
            "resource_budget": Budget().__dict__,
        })
        governor = ResourceGovernor(Budget())
        governor.check(1)
        experiment = execute_tw539_distribution_experiment(interface, protocol)
        governor.check(1, experiment)
        knowledge = knowledge_record(run_id, rq, hypotheses, protocol, experiment)
        next_decision = "STOP" if experiment["edge_status"] == "NO_EDGE_FOUND" else "ESCALATE"
        result = {
            "brain_run_id": run_id, "lottery_context": context, "status": "completed", "inputs": inputs,
            "observations": observations, "research_queue": queue, "selected_rq": rq, "hypotheses": hypotheses,
            "protocol": protocol, "experiments": [experiment], "knowledge_writes": [knowledge],
            "next_decision": next_decision, "resource_usage": {"experiments": 1, "within_budget": True},
            "security_events": [], "started_at": started, "completed_at": utc_now(),
        }
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / f"{context.lower()}_run.json"
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    result["audit_path"] = str(path)
    result["audit_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result
