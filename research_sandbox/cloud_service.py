"""Dedicated cloud runner for the Star Research Brain.

This process owns no prediction credentials and never runs a polling research loop.
It wakes on a fixed four-hour cadence, calls the deterministic one-shot processor,
then returns to an interruptible sleep.  Brain execution remains disabled unless an
explicit later activation gate sets RESEARCH_BRAIN_ENABLED=true.
"""
from __future__ import annotations

import json
import hashlib
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from automation import process_research_inbox_once
from inbox_adapter import ResearchEvidenceEventAdapter
from brain import DataInterface
from full_loop import formal_manifest, permission_validation, run_full_loop, safe_failure_run, validation_root, write_artifact
from natural_evidence import configured_client, reconcile_natural_research_events_once

RUNTIME_VERSION = "star-research-cloud-v1"
ROOT_ENV = "STAR_RESEARCH_PERSISTENT_ROOT"
DEFAULT_ROOT = "/var/data/star-research"
INTERVAL_ENV = "RESEARCH_BRAIN_WAKE_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 4 * 60 * 60
DEFAULT_PORT = 10000

_stopping = False


class HealthHandler(BaseHTTPRequestHandler):
    """Private-network liveness only. No trigger or management surface."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/health":
            self.send_error(404)
            return
        payload = json.dumps({"status": "ok", "brain_enabled": _flag("RESEARCH_BRAIN_ENABLED")}, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - explicitly deny trigger surface
        self.send_error(405)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_health_server(port: int | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port if port is not None else int(os.environ.get("PORT", DEFAULT_PORT))), HealthHandler)
    threading.Thread(target=server.serve_forever, name="research-health", daemon=True).start()
    return server


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() == "true"


def _root() -> Path:
    value = Path(os.environ.get(ROOT_ENV, DEFAULT_ROOT))
    if not value.is_absolute():
        raise RuntimeError("PERSISTENT_ROOT_MUST_BE_ABSOLUTE")
    return value.resolve()


def ensure_quarantine(root: Path) -> dict[str, str]:
    allowed = {}
    for name in ("inbox", "knowledge", "output", "audit"):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        allowed[name] = str(path)
    return allowed


def formal_full_loop_executor(root: Path) -> Callable[[str, dict[str, object], str], dict[str, object]]:
    """Bind formal OPEN_RQ handling to the approved Full Research Loop.

    A natural source export must already exist under the formal inbox.  The
    Research service never reaches into another Render service's disk and never
    manufactures research inputs from an Inbox event.
    """
    source_root = root / "inbox" / "source_exports"
    manifest = source_root / "approved_inputs.json"

    def execute(context: str, rq: dict[str, object], experiment_key: str) -> dict[str, object]:
        if context != "TW539":
            return {"status": "DATA_QUALITY_BLOCKED", "experiments": 0, "knowledge_key": experiment_key}
        if not manifest.is_file():
            raise RuntimeError("NATURAL_EVIDENCE_READ_PATH_UNAVAILABLE")
        return run_full_loop(
            opened_rq=rq,
            interface=DataInterface(source_root, manifest),
            gate_root=root,
            experiment_key=experiment_key,
            result_path=root / "output" / "full_loop_result.json",
        )

    return execute


def process_once(
    root: Path | None = None,
    *,
    enabled: bool | None = None,
    kill_switch: bool | None = None,
    prior_by_context: dict[str, dict[str, dict[str, object]]] | None = None,
    knowledge_by_context: dict[str, list[dict[str, object]]] | None = None,
    source_hash_resolver: Callable[[dict[str, object]], str] | None = None,
    sandbox_executor: Callable[[str, dict[str, object], str], dict[str, object]] | None = None,
) -> dict[str, object]:
    root = _root() if root is None else root.resolve()
    paths = ensure_quarantine(root)
    adapter = ResearchEvidenceEventAdapter(Path(paths["inbox"]) / "events.json")
    client = configured_client()
    if client is None:
        reconciliation = {"status": "SAFE_NOOP_EXPORT_NOT_CONFIGURED", "records_added": 0}
    else:
        try:
            reconciliation = reconcile_natural_research_events_once(root, client)
        except Exception as exc:
            reconciliation = {"status": "NATURAL_EXPORT_RETRYABLE", "records_added": 0, "error_type": type(exc).__name__}
    result = process_research_inbox_once(
        adapter,
        state_path=Path(paths["knowledge"]) / "automation_state.json",
        wake_lock_path=Path(paths["audit"]) / "wake.lock",
        prior_by_context=prior_by_context or {},
        knowledge_by_context=knowledge_by_context or {},
        source_hash_resolver=source_hash_resolver or (lambda _event: "SOURCE_EXPORT_NOT_READABLE"),
        sandbox_executor=sandbox_executor or formal_full_loop_executor(root),
        enabled=enabled,
        kill_switch=kill_switch,
    )
    if result.get("status") == "SAFE_NOOP_SLEEPING":
        result = {**result, "status": "SAFE_NOOP_DISABLED"}
    return {"runtime_version": RUNTIME_VERSION, "reconciliation": reconciliation, **result}


def validation_process_once(root: Path, fixture: Path) -> dict[str, object]:
    """Exercise the real Inbox -> Materiality -> Full Loop chain in quarantine."""
    gate = validation_root(root) / "processor_validation"
    paths = {name: gate / name for name in ("inbox", "knowledge", "output", "audit", "holdout")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    adapter = ResearchEvidenceEventAdapter(paths["inbox"] / "events.json")
    manifest = fixture / "approved_inputs.json"
    if not manifest.exists():
        manifest = fixture / "research_sandbox" / "approved_inputs.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    source = {
        "lottery_context": "TW539", "event_type": "VALID_LIVE_EVIDENCE",
        "source_id": "CLOUD-FULL-LOOP-VALIDATION", "source_version": "v1",
        "source_hash": digest, "computed_source_hash": digest,
        "source_quality": "OOS_RESEARCH", "evidence_grade": "E2",
        "created_at": "2026-08-08T00:00:00Z", "provenance": "validation_fixture",
        "timing_valid": True, "validation_only": True,
        "materiality_inputs": {"sample_size": 730, "validation_only": True},
        "affected_knowledge_ids": ["K-TW539-0001"],
    }
    enqueue = adapter.adapt(source)
    interface = DataInterface(fixture, manifest)
    result = process_research_inbox_once(
        adapter,
        state_path=paths["knowledge"] / "automation_state.json",
        wake_lock_path=paths["audit"] / "wake.lock",
        prior_by_context={"TW539": {source["source_id"]: {"sample_size": 700, "quality": "OOS_RESEARCH"}}},
        knowledge_by_context={"TW539": [{"knowledge_id": "K-TW539-0001", "result": "NO_EDGE_FOUND", "do_not_repeat": "requires new material evidence"}]},
        source_hash_resolver=lambda event: event["source_hash"],
        sandbox_executor=lambda context, rq, key: run_full_loop(
            opened_rq=rq, interface=interface, gate_root=gate,
            experiment_key=key,
        ) if context == "TW539" else {"status": "DATA_QUALITY_BLOCKED", "experiments": 0, "knowledge_key": key},
        enabled=True, kill_switch=False,
    )
    return {"enqueue": enqueue, "processor": result, "validation_root": str(gate)}


def status(root: Path | None = None) -> dict[str, object]:
    root = _root() if root is None else root.resolve()
    paths = ensure_quarantine(root)
    return {
        "runtime_version": RUNTIME_VERSION,
        "persistent_root": str(root),
        "directories": paths,
        "brain_enabled": _flag("RESEARCH_BRAIN_ENABLED"),
        "kill_switch": _flag("RESEARCH_BRAIN_KILL_SWITCH"),
        "wake_interval_seconds": int(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS)),
        "permission_boundary": {
            "production_write": "DENIED_NO_CREDENTIALS_NO_DISK",
            "staging_prediction_write": "DENIED_SEPARATE_SERVICE_DISK",
            "git_push": "DENIED_NO_CREDENTIALS",
            "deploy": "DENIED_NO_CREDENTIALS",
            "promotion": "DENIED_BY_RUNTIME",
            "shadow_enable": "DENIED_BY_RUNTIME",
            "cloud_resource_create": "DENIED_NO_CREDENTIALS",
        },
    }


def _stop(_signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True


def serve() -> int:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    root = _root()
    health_server = start_health_server()
    print(json.dumps({"event": "RESEARCH_SERVICE_STARTED", **status(root)}, sort_keys=True), flush=True)
    # First invocation proves Disabled/Kill behavior without waiting four hours.
    print(json.dumps({"event": "RESEARCH_WAKE_RESULT", **process_once(root)}, sort_keys=True), flush=True)
    interval = max(60, int(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS)))
    try:
        while not _stopping:
            deadline = time.monotonic() + interval
            while not _stopping and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
            if not _stopping:
                print(json.dumps({"event": "RESEARCH_WAKE_RESULT", **process_once(root)}, sort_keys=True), flush=True)
    finally:
        health_server.shutdown()
        health_server.server_close()
    print(json.dumps({"event": "RESEARCH_SERVICE_STOPPED"}), flush=True)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(json.dumps(status(), ensure_ascii=False, sort_keys=True))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--process-once":
        print(json.dumps(process_once(), ensure_ascii=False, sort_keys=True))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--validate-full-loop":
        if not _flag("RESEARCH_BRAIN_ENABLED") or not _flag("RESEARCH_BRAIN_KILL_SWITCH"):
            print(json.dumps({"status": "VALIDATION_REQUIRES_ENABLED_AND_KILLED"}, sort_keys=True)); return 2
        root = _root(); gate = validation_root(root)
        before = formal_manifest(root)
        fixture = Path("/app/research_fixture")
        processor_validation = validation_process_once(root, fixture)
        interface = DataInterface(fixture, fixture / "approved_inputs.json")
        opened = {"rq_id": "RQ-TW539-OBS-validation", "question": "Does the approved validation fixture complete the frozen Research Loop?", "trigger_evidence_hash": "validation-event-sha256"}
        result = run_full_loop(opened_rq=opened, interface=interface, gate_root=gate, experiment_key="VALIDATION|TW539|FULL_LOOP|v1")
        failure_results = {}
        for case in ("experiment", "falsification", "knowledge", "holdout", "protocol", "timeout"):
            failure_results[case] = safe_failure_run(
                opened_rq=opened, interface=interface, gate_root=gate / "failure_cases" / case,
                experiment_key=f"FAILURE|{case}", failure=case,
            )
        crash_root = gate / "crash_cases"
        crash_experiment_first = safe_failure_run(opened_rq=opened, interface=interface, gate_root=crash_root / "experiment", experiment_key="CRASH-EXP", failure="after_experiment")
        crash_experiment_resume = safe_failure_run(opened_rq=opened, interface=interface, gate_root=crash_root / "experiment", experiment_key="CRASH-EXP")
        crash_knowledge_first = safe_failure_run(opened_rq=opened, interface=interface, gate_root=crash_root / "knowledge", experiment_key="CRASH-KNOW", failure="after_knowledge")
        crash_knowledge_resume = safe_failure_run(opened_rq=opened, interface=interface, gate_root=crash_root / "knowledge", experiment_key="CRASH-KNOW")
        parity = run_full_loop(opened_rq=opened, interface=interface, gate_root=gate / "parity", experiment_key="PARITY")
        after = formal_manifest(root)
        formal_unchanged = all(before[k] == after[k] for k in ("inbox", "knowledge", "output"))
        permissions = permission_validation()
        parity_keys = ("conclusion", "evidence_grade", "supporting_test", "falsification", "random_control", "baseline_control", "protocol_sha256")
        parity_result = {"passed": all(result[k] == parity[k] for k in parity_keys), "compared_fields": list(parity_keys)}
        crash_result = {
            "passed": crash_experiment_first["status"] == "SAFE_STOP" and crash_experiment_resume["status"] == "COMPLETED_RETURNED_TO_SLEEP" and crash_knowledge_first["status"] == "SAFE_STOP" and crash_knowledge_resume["status"] == "RESUMED_ALREADY_FINALIZED",
            "after_experiment": [crash_experiment_first, crash_experiment_resume],
            "after_knowledge": [crash_knowledge_first, crash_knowledge_resume],
        }
        formal_diff = {"before": before, "after": after, "unchanged": formal_unchanged}
        write_artifact(gate / "failure_injection.json", failure_results)
        write_artifact(gate / "crash_recovery.json", crash_result)
        write_artifact(gate / "local_cloud_parity.json", parity_result)
        write_artifact(gate / "permission_validation.json", permissions)
        write_artifact(gate / "formal_store_diff.json", formal_diff)
        processor = processor_validation["processor"]
        evidence = {**result, "processor_validation": processor_validation, "processor_full_loop_passed": processor.get("rq_opened") == 1 and processor.get("experiments_started") == 1 and processor.get("returned_to_sleep") is True, "formal_store_unchanged": formal_unchanged, "permission_validation": permissions, "failure_injection_passed": all(x["status"] == "SAFE_STOP" for x in failure_results.values()), "crash_recovery_passed": crash_result["passed"], "local_cloud_parity": parity_result["passed"], "kill_switch": True, "running_brain_count": 0}
        write_artifact(gate / "full_loop_result.json", evidence)
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True)); return 0 if formal_unchanged else 3
    if len(sys.argv) > 1 and sys.argv[1] == "--validation-manifest":
        print(json.dumps(formal_manifest(validation_root(_root())), ensure_ascii=False, sort_keys=True)); return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
