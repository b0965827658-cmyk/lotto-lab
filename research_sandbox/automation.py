"""One-shot, event-driven automation for the local Research Sandbox."""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from inbox_adapter import FileLock, ResearchEvidenceEventAdapter, _atomic_write, _safe_read

AUTOMATION_VERSION = "star-research-automation-v1"
MAX_RETRIES = 2
DAILY_GLOBAL_EXPERIMENTS = 3
DAILY_CONTEXT_EXPERIMENTS = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class ResearchWakeLock:
    def __init__(self, path: Path, timeout_seconds: int = 7200):
        self.path, self.timeout_seconds, self.guard = path, timeout_seconds, None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.guard = FileLock(self.path.with_suffix(".guard"))
        self.guard.__enter__()
        try:
            if self.path.exists():
                try:
                    meta = json.loads(self.path.read_text())
                    age = time.time() - float(meta["created_epoch"])
                    if age <= self.timeout_seconds:
                        return False
                except Exception:
                    pass
            self.path.write_text(json.dumps({"pid": os.getpid(), "created_epoch": time.time(), "created_at": _utc_now()}))
            return True
        finally:
            self.guard.__exit__(None, None, None); self.guard = None

    def release(self) -> None:
        guard = FileLock(self.path.with_suffix(".guard"))
        with guard:
            if self.path.exists():
                try:
                    if json.loads(self.path.read_text()).get("pid") == os.getpid():
                        self.path.unlink()
                except Exception:
                    pass


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": AUTOMATION_VERSION, "runs": [], "daily_budget": {}, "knowledge_keys": [], "experiment_keys": [], "retry_counts": {}}
    value = json.loads(path.read_text())
    value.setdefault("retry_counts", {})
    return value


def _save_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write(path, state)


def _budget(state: dict[str, Any], context: str) -> tuple[bool, dict[str, int]]:
    bucket = state["daily_budget"].setdefault(_day(), {"global": 0, "TW539": 0, "FANTASY5": 0})
    allowed = bucket["global"] < DAILY_GLOBAL_EXPERIMENTS and bucket[context] < DAILY_CONTEXT_EXPERIMENTS
    return allowed, bucket


def _new_events(adapter: ResearchEvidenceEventAdapter, context: str) -> list[dict[str, Any]]:
    journal = _safe_read(adapter.inbox_path)
    return [x for x in journal["events"] if x["lottery_context"] == context and adapter.current_status(journal, x["event_id"]) == "NEW"]


def process_research_inbox_once(
    adapter: ResearchEvidenceEventAdapter,
    *,
    state_path: Path,
    wake_lock_path: Path,
    prior_by_context: dict[str, dict[str, dict[str, Any]]],
    knowledge_by_context: dict[str, list[dict[str, Any]]],
    source_hash_resolver: Callable[[dict[str, Any]], str],
    sandbox_executor: Callable[[str, dict[str, Any], str], dict[str, Any]],
    enabled: bool | None = None,
    kill_switch: bool | None = None,
    inject_failure: str | None = None,
) -> dict[str, Any]:
    enabled = os.environ.get("RESEARCH_BRAIN_ENABLED", "false").lower() == "true" if enabled is None else enabled
    kill_switch = os.environ.get("RESEARCH_BRAIN_KILL_SWITCH", "false").lower() == "true" if kill_switch is None else kill_switch
    if not enabled:
        return {"status": "SAFE_NOOP_SLEEPING", "experiments_started": 0}
    if kill_switch:
        return {"status": "SAFE_NOOP_KILLED", "experiments_started": 0}
    wake = ResearchWakeLock(wake_lock_path)
    if not wake.try_acquire():
        return {"status": "SAFE_NOOP_LOCKED", "experiments_started": 0}
    run_id = "AUTO-" + _utc_now().replace(":", "").replace("-", "")
    try:
        state = _load_state(state_path)
        run = {"run_id": run_id, "started_at": _utc_now(), "events_seen": 0, "contexts": {}, "rq_opened": 0, "experiments_started": 0, "retries": 0, "failures": []}
        # Verify every NEW event against its immutable source before any Brain decision.
        candidates: dict[str, list[dict[str, Any]]] = {}
        for context in ("TW539", "FANTASY5"):
            verified = []
            for event in _new_events(adapter, context):
                run["events_seen"] += 1
                if source_hash_resolver(event) != event["source_hash"]:
                    adapter.transition(event["event_id"], "IGNORED_NO_MATERIAL_CHANGE", "EVENT_SOURCE_INTEGRITY_FAILURE")
                    run["failures"].append({"event_id": event["event_id"], "error": "EVENT_SOURCE_INTEGRITY_FAILURE"})
                else:
                    verified.append(event)
            candidates[context] = verified
        if inject_failure == "after_read":
            raise RuntimeError("INJECTED_AFTER_READ")

        # Conservative global policy: first context that can open one RQ wins; the other remains NEW.
        for context in ("TW539", "FANTASY5"):
            if not candidates[context] or run["rq_opened"]:
                continue
            decision = adapter.process_new(context, prior_by_source=prior_by_context.get(context, {}), knowledge=knowledge_by_context.get(context, []), kill_switch=False, commit_transitions=False)
            run["contexts"][context] = decision
            if decision.get("rq_opened", 0) == 0:
                for item in decision["event_decisions"]:
                    adapter.transition(item["event_id"], item["to"], item["decision"])
                continue
            run["rq_opened"] = 1
            rq = decision["opened_rqs"][0]
            allowed, bucket = _budget(state, context)
            if not allowed:
                run["failures"].append({"rq_id": rq["rq_id"], "error": "DAILY_EXPERIMENT_BUDGET_EXCEEDED"})
                break
            experiment_key = rq["rq_id"] + "|" + rq["trigger_evidence_hash"]
            if experiment_key in state["experiment_keys"]:
                run["failures"].append({"rq_id": rq["rq_id"], "error": "DUPLICATE_EXPERIMENT_PREVENTED"})
                break
            result = None
            prior_retries = int(state["retry_counts"].get(experiment_key, 0))
            attempts_available = max(0, (MAX_RETRIES - prior_retries) + 1)
            for attempt in range(attempts_available):
                try:
                    if inject_failure == "executor":
                        raise RuntimeError("INJECTED_EXECUTOR_FAILURE")
                    result = sandbox_executor(context, rq, experiment_key)
                    break
                except Exception as exc:
                    state["retry_counts"][experiment_key] = int(state["retry_counts"].get(experiment_key, 0)) + 1
                    run["retries"] += 1
                    run["failures"].append({"rq_id": rq["rq_id"], "attempt": attempt + 1, "error": "BRAIN_AUTOMATION_FAILURE", "type": type(exc).__name__})
            if result is None:
                if int(state["retry_counts"].get(experiment_key, 0)) >= MAX_RETRIES:
                    for item in decision["event_decisions"]:
                        adapter.transition(item["event_id"], "OBSERVED", "AUTOMATION_RETRY_EXHAUSTED")
                break
            for item in decision["event_decisions"]:
                adapter.transition(item["event_id"], item["to"], item["decision"])
            state["experiment_keys"].append(experiment_key)
            knowledge_key = str(result.get("knowledge_key") or experiment_key)
            if knowledge_key not in state["knowledge_keys"]:
                state["knowledge_keys"].append(knowledge_key)
            bucket["global"] += int(result.get("experiments", 1))
            bucket[context] += int(result.get("experiments", 1))
            run["experiments_started"] = int(result.get("experiments", 1))
            run["result"] = result
            break
        run["completed_at"] = _utc_now()
        run["returned_to_sleep"] = True
        state["runs"].append(run)
        _save_state(state_path, state)
        return {"status": "PROCESSED_RETURNED_TO_SLEEP", **run}
    except Exception as exc:
        # Inbox events remain NEW unless a durable decision transition already exists.
        return {"status": "BRAIN_AUTOMATION_FAILURE", "error_type": type(exc).__name__, "retryable": True, "experiments_started": 0}
    finally:
        wake.release()
