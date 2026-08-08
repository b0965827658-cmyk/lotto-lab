"""Sleeping-by-default observation mode for Star Research Brain v1."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from brain import ALLOWED_CONTEXTS, MAX_EXPERIMENTS, SecurityBoundary

MATERIAL_SAMPLE_DELTA = 30
QUALITY_RANK = {
    "UNKNOWN": 0,
    "PROVISIONAL": 1,
    "E1_EXPLORATORY": 2,
    "OOS_RESEARCH": 3,
    "FORWARD_VERIFIED": 4,
    "LIVE_SHADOW": 5,
}


def fingerprint(evidence: dict[str, Any]) -> str:
    stable = {k: v for k, v in evidence.items() if k not in {"received_at", "observed_at"}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Materiality:
    material: bool
    score: float
    reason: str


def assess_materiality(evidence: dict[str, Any], prior: dict[str, Any] | None, knowledge: list[dict[str, Any]]) -> Materiality:
    if evidence.get("provenance") in {None, "missing", "unverified"}:
        return Materiality(False, 0.0, "MISSING_PROVENANCE")
    if evidence.get("context") == "FANTASY5" and int(evidence.get("forward_verified_count", 0)) == 0:
        return Materiality(False, 0.1, "DATA_QUALITY_BLOCKED")
    quality = QUALITY_RANK.get(str(evidence.get("quality", "UNKNOWN")), 0)
    prior_quality = QUALITY_RANK.get(str((prior or {}).get("quality", "UNKNOWN")), 0)
    sample_delta = int(evidence.get("sample_size", 0)) - int((prior or {}).get("sample_size", 0))
    provenance_upgrade = quality > prior_quality
    independent_holdout = bool(evidence.get("independent_holdout"))
    forward_milestone = int(evidence.get("forward_verified_count", 0)) in {10, 30, 60, 100, 180, 365}
    material = independent_holdout or provenance_upgrade or sample_delta >= MATERIAL_SAMPLE_DELTA or forward_milestone
    reason = "MATERIAL_NEW_EVIDENCE" if material else "LOW_MATERIALITY_OBSERVE_ONLY"
    # A rejected question requires genuinely new eligible evidence, not a renamed metric.
    if evidence.get("rq_id"):
        rejected = any(k.get("rq_id") == evidence["rq_id"] and k.get("result") == "REJECTED" for k in knowledge)
        if rejected and not (independent_holdout or provenance_upgrade or sample_delta >= MATERIAL_SAMPLE_DELTA):
            return Materiality(False, 0.2, "KNOWLEDGE_DO_NOT_REPEAT")
    score = min(1.0, 0.4 * provenance_upgrade + 0.4 * independent_holdout + 0.3 * min(1, max(0, sample_delta) / MATERIAL_SAMPLE_DELTA) + 0.2 * forward_milestone)
    return Materiality(material, score, reason)


def proposed_rq(evidence: dict[str, Any]) -> dict[str, Any]:
    context = evidence["context"]
    if context == "TW539":
        return {"rq_id": "RQ-TW539-OBS-" + fingerprint(evidence)[:8], "question": "Does the newly arrived material OOS evidence change the prior NO_EDGE conclusion under the same frozen Random and Baseline controls?", "max_experiments": MAX_EXPERIMENTS}
    return {"rq_id": "RQ-F5-OBS-" + fingerprint(evidence)[:8], "question": "Does newly accumulated forward-verified evidence resolve the prior Fantasy 5 data-quality block?", "max_experiments": MAX_EXPERIMENTS}


def observe_inbox(
    context: str,
    inbox: list[dict[str, Any]],
    *,
    seen_hashes: set[str],
    prior_by_source: dict[str, dict[str, Any]],
    knowledge: list[dict[str, Any]],
    enabled: bool | None = None,
    kill_switch: bool | None = None,
) -> dict[str, Any]:
    enabled = os.environ.get("RESEARCH_BRAIN_ENABLED", "false").lower() == "true" if enabled is None else enabled
    kill_switch = os.environ.get("RESEARCH_BRAIN_KILL_SWITCH", "false").lower() == "true" if kill_switch is None else kill_switch
    if not enabled:
        return {"state": "SLEEPING", "reason": "DISABLED_BY_DEFAULT", "opened_rqs": [], "experiments_started": 0}
    if kill_switch:
        return {"state": "SLEEPING", "reason": "KILL_SWITCH_ACTIVE", "opened_rqs": [], "experiments_started": 0}
    if context not in ALLOWED_CONTEXTS:
        raise ValueError("INVALID_CONTEXT")
    evaluated, material_candidates = [], []
    for evidence in inbox:
        if evidence.get("context") != context:
            continue
        digest = fingerprint(evidence)
        if digest in seen_hashes:
            evaluated.append({"evidence_hash": digest, "decision": "DUPLICATE_IGNORED"})
            continue
        result = assess_materiality(evidence, prior_by_source.get(str(evidence.get("source_id"))), knowledge)
        row = {"evidence_hash": digest, "source_id": evidence.get("source_id"), "material": result.material, "materiality_score": result.score, "decision": result.reason}
        evaluated.append(row)
        if result.material:
            material_candidates.append((result.score, digest, evidence))
    opened = []
    if material_candidates:
        _, digest, evidence = sorted(material_candidates, key=lambda x: (-x[0], x[1]))[0]
        opened.append({**proposed_rq(evidence), "trigger_evidence_hash": digest})
    return {
        "state": "SLEEPING",
        "wake_occurred": bool(inbox),
        "material_evidence_found": bool(material_candidates),
        "evaluated": evaluated,
        "opened_rqs": opened,
        "max_rq_per_wake": 1,
        "experiments_started": 0,
        "returned_to_sleep": True,
        "knowledge_read": [k.get("knowledge_id") for k in knowledge],
    }


def assert_permissions() -> dict[str, str]:
    guard = SecurityBoundary()
    results = {}
    for action in ("production_write", "deploy", "git_push", "promotion", "shadow_enable", "cloud_create"):
        try:
            guard.require(action)
        except Exception:
            results[action] = "PERMISSION_DENIED"
    return results
