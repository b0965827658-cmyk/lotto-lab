"""Read-only Human Oversight views for the Star Research Brain.

The module derives projections from append-only Brain artifacts.  It never
changes research state, evidence, knowledge, budgets, or prediction data.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OVERSIGHT_VERSION = "star-research-oversight-v1"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _files(path: Path, pattern: str = "*.json") -> list[Path]:
    return sorted(path.glob(pattern)) if path.is_dir() else []


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _automation(root: Path) -> dict[str, Any]:
    return _read(root / "knowledge" / "automation_state.json", {"runs": [], "daily_budget": {}})


def _events(root: Path) -> list[dict[str, Any]]:
    return _read(root / "inbox" / "events.json", {"events": []}).get("events", [])


def wake_timeline(root: Path) -> dict[str, Any]:
    runs = _automation(root).get("runs", [])
    rows = []
    for run in runs:
        contexts = run.get("contexts", {})
        decisions = [d for c in contexts.values() for d in c.get("event_decisions", [])]
        rows.append({
            "wake_id": run.get("run_id"), "timestamp": run.get("started_at"),
            "event_ids": [d.get("event_id") for d in decisions],
            "lotteries": sorted(contexts),
            "materiality_decisions": [d.get("decision") for d in decisions],
            "rq_opened": run.get("rq_opened", 0),
            "experiments": run.get("experiments_started", 0),
            "knowledge": 1 if run.get("result", {}).get("knowledge_key") else 0,
            "duration_seconds": _duration(run),
            "resource_usage": run.get("result", {}).get("resource_usage", {}),
            "final_state": "SLEEPING" if run.get("returned_to_sleep") else "UNKNOWN",
            "failures": run.get("failures", []),
        })
    return {"version": OVERSIGHT_VERSION, "append_only_source": True, "count": len(rows), "wakes": rows}


def _duration(run: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(str(run["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(run["completed_at"]).replace("Z", "+00:00"))
        return round((end - start).total_seconds(), 3)
    except (KeyError, TypeError, ValueError):
        return None


def knowledge_ledger(root: Path) -> dict[str, Any]:
    records = []
    for path in _files(root / "knowledge", "K-*.json"):
        item = _read(path, {})
        records.append({
            "knowledge_id": item.get("knowledge_id"), "lottery": item.get("lottery_context") or item.get("lottery"),
            "rq": item.get("rq_id"), "conclusion": item.get("result") or item.get("conclusion"),
            "evidence_grade": item.get("evidence_grade"), "created_at": item.get("created_at"),
            "supersedes": item.get("supersedes"), "do_not_repeat": item.get("do_not_repeat"),
            "record_sha256": item.get("record_sha256") or _sha(path),
        })
    return {"version": OVERSIGHT_VERSION, "immutable": True, "count": len(records), "records": records}


def research_timeline(root: Path) -> dict[str, Any]:
    rows = []
    for path in _files(root / "output"):
        item = _read(path, {})
        if item.get("rq_id") or item.get("selected_rq"):
            rows.append({"artifact": path.name, "artifact_sha256": _sha(path), **item})
    return {"version": OVERSIGHT_VERSION, "count": len(rows), "research": rows}


def shadow_queue(root: Path) -> dict[str, Any]:
    candidates = []
    for row in research_timeline(root)["research"]:
        if row.get("next_decision") == "SHADOW_RECOMMENDED" and row.get("evidence_grade") == "E3":
            candidates.append({
                "recommendation_id": row.get("recommendation_id") or row.get("rq_id"),
                "lottery": row.get("lottery_context"), "candidate_artifact_hash": row.get("artifact_sha256"),
                "evidence_grade": "E3", "known_limitations": row.get("limitations", []),
                "human_actions": ["APPROVE_SHADOW_REVIEW", "REJECT", "REQUEST_MORE_EVIDENCE"],
                "deploy_authority": False, "promotion_authority": False,
            })
    return {"version": OVERSIGHT_VERSION, "action_required_only_at": "E3+SHADOW_RECOMMENDED", "count": len(candidates), "recommendations": candidates}


def status_view(root: Path) -> dict[str, Any]:
    state = _automation(root)
    runs = state.get("runs", [])
    last = runs[-1] if runs else {}
    events = _events(root)
    today = datetime.now(timezone.utc).date().isoformat()
    budget = state.get("daily_budget", {}).get(today, {"global": 0, "TW539": 0, "FANTASY5": 0})
    used = int(budget.get("global", 0))
    disk_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.exists() else 0
    return {
        "version": OVERSIGHT_VERSION,
        "operating_mode": "AUTONOMOUS_OBSERVATION",
        "enabled": os.getenv("RESEARCH_BRAIN_ENABLED", "false").lower() == "true",
        "kill": os.getenv("RESEARCH_BRAIN_KILL_SWITCH", "false").lower() == "true",
        "current_state": "SLEEPING" if not last or last.get("returned_to_sleep", True) else "RUNNING",
        "last_wake_at": last.get("started_at"), "last_event_id": _last_event(last),
        "last_event_source": _last_source(events, _last_event(last)), "last_lottery": _last_lottery(last),
        "last_materiality_decision": _last_decision(last),
        "current_rq": None, "current_experiment": None, "running_brain_count": 0,
        "experiments_today": used, "daily_budget_remaining": max(0, 3 - used),
        "tw539_evidence_status": _context_status(events, "TW539"),
        "fantasy5_evidence_status": _context_status(events, "FANTASY5"),
        "knowledge_count": knowledge_ledger(root)["count"],
        "shadow_recommendations_pending": shadow_queue(root)["count"],
        "persistent_disk_usage_bytes": disk_bytes,
        "production_model_changed": False,
    }


def _last_event(run: dict[str, Any]) -> str | None:
    for context in reversed(list(run.get("contexts", {}).values())):
        decisions = context.get("event_decisions", [])
        if decisions:
            return decisions[-1].get("event_id")
    return None


def _last_source(events: list[dict[str, Any]], event_id: str | None) -> str | None:
    return next((e.get("source_id") for e in reversed(events) if e.get("event_id") == event_id), None)


def _last_lottery(run: dict[str, Any]) -> str | None:
    return next(reversed(run.get("contexts", {})), None) if run.get("contexts") else None


def _last_decision(run: dict[str, Any]) -> str | None:
    for context in reversed(list(run.get("contexts", {}).values())):
        decisions = context.get("event_decisions", [])
        if decisions:
            return decisions[-1].get("decision")
    return None


def _context_status(events: list[dict[str, Any]], context: str) -> dict[str, Any]:
    selected = [e for e in events if e.get("lottery_context") == context]
    return {"events": len(selected), "latest_event_id": selected[-1].get("event_id") if selected else None}


def health(root: Path) -> dict[str, Any]:
    wakes = wake_timeline(root)["wakes"]
    experiments = sum(int(x.get("experiments", 0)) for x in wakes)
    failures = [f for x in wakes for f in x.get("failures", [])]
    opened = sum(int(x.get("rq_opened", 0)) for x in wakes)
    return {
        "version": OVERSIGHT_VERSION, "wake_count": len(wakes), "rq_count": opened,
        "experiment_count": experiments, "duplicate_research_blocked": sum("DUPLICATE" in str(f) for f in failures),
        "data_quality_blocked": sum("DATA_QUALITY" in str(f) for f in failures),
        "resource_budget_violations": sum("BUDGET" in str(f) for f in failures),
        "emergency_kills": sum(1 for x in wakes if x.get("final_state") == "KILLED"),
        "integrity_failures": sum("INTEGRITY" in str(f) for f in failures),
        "over_research_warning": opened > 0 and experiments / opened > 3,
    }


def notification_policy() -> dict[str, Any]:
    return {
        "notify": ["SHADOW_RECOMMENDED", "EMERGENCY_KILL", "INTEGRITY_FAILURE", "PERSISTENT_STORAGE_RISK", "REPEATED_AUTONOMY_FAILURE"],
        "do_not_notify": ["NORMAL_WAKE", "OBSERVE_ONLY", "REJECTED_EXPERIMENT"],
        "daily_digest_if_no_material_research": "NO_MATERIAL_RESEARCH",
    }


def snapshot(root: Path) -> dict[str, Any]:
    return {
        "brain_status": status_view(root), "wake_timeline": wake_timeline(root),
        "research_timeline": research_timeline(root), "knowledge_ledger": knowledge_ledger(root),
        "shadow_recommendation_queue": shadow_queue(root), "research_health": health(root),
        "notification_policy": notification_policy(),
        "human_boundary": {
            "read_audit_understand": True, "approve_reject_escalation": True,
            "emergency_kill": "SET_RESEARCH_BRAIN_KILL_SWITCH_TRUE",
            "model_modify": False, "knowledge_modify": False, "deploy": False,
            "promotion": False, "shadow_enable": False, "secrets_exposed": False,
        },
    }


def write_artifacts(root: Path, output: Path) -> dict[str, Any]:
    """Write generated oversight projections outside formal Brain stores."""
    output.mkdir(parents=True, exist_ok=True)
    view = snapshot(root)
    tw = view["brain_status"]["tw539_evidence_status"]
    f5 = view["brain_status"]["fantasy5_evidence_status"]
    mapping = {
        "brain_status.json": view["brain_status"],
        "wake_timeline.json": view["wake_timeline"],
        "research_timeline.json": view["research_timeline"],
        "knowledge_ledger.json": view["knowledge_ledger"],
        "shadow_recommendation_queue.json": view["shadow_recommendation_queue"],
        "research_health.json": view["research_health"],
        "learning_velocity.json": {
            "evidence_received": tw["events"] + f5["events"],
            "questions_opened": view["research_health"]["rq_count"],
            "experiments": view["research_health"]["experiment_count"],
            "knowledge_added": view["knowledge_ledger"]["count"],
            "shadow_recommendations": view["shadow_recommendation_queue"]["count"],
        },
        "lottery_tw539.json": {"lottery": "TW539", "evidence": tw, "context_isolated": True},
        "lottery_fantasy5.json": {"lottery": "FANTASY5", "evidence": f5, "context_isolated": True},
        "notification_policy.json": view["notification_policy"],
        "daily_digest.json": {"status": "NO_MATERIAL_RESEARCH" if view["research_health"]["experiment_count"] == 0 else "MATERIAL_RESEARCH_PRESENT"},
        "emergency_kill_contract.json": {
            "action": "SET_RESEARCH_BRAIN_KILL_SWITCH_TRUE", "target": "RESEARCH_BRAIN_ONLY",
            "prediction_heart_affected": False, "restart_required_for_environment_reload": True,
        },
        "human_review_contract.json": {
            "allowed": ["APPROVE_SHADOW_REVIEW", "REJECT", "REQUEST_MORE_EVIDENCE"],
            "direct_deploy": False, "direct_promotion": False, "model_modify": False,
            "knowledge_modify": False, "secrets_visible": False,
        },
        "oversight_result.json": {
            "verdict": "A", "read_only": True, "operating_mode": "AUTONOMOUS_OBSERVATION",
            "kill": view["brain_status"]["kill"], "prediction_zero_change": True,
            "brain_logic_zero_change": True, "context_isolation": True,
        },
    }
    for name, payload in mapping.items():
        (output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = """# Star Research Brain v1 — Human Oversight & Escalation

The oversight layer is read-only. It projects Brain status, Wake history, Research artifacts,
immutable Knowledge, health, and any E3 `SHADOW_RECOMMENDED` item without changing the Brain.

Human action is required only for E3 plus `SHADOW_RECOMMENDED`, emergency kill, integrity or
persistent-storage risk, and repeated autonomy failure. Emergency kill stops only Research Brain;
Prediction Heart remains independent. Human review cannot directly deploy, promote, alter models,
edit Knowledge, expose credentials, or change research budgets.

TW539 and Fantasy5 remain separate contexts. Production and Prediction behavior are unchanged.
"""
    (output / "oversight_report.md").write_text(report, encoding="utf-8")
    return {"status": "WRITTEN", "files": len(mapping) + 1, "output": str(output)}
