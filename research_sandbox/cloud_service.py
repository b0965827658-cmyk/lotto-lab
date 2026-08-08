"""Dedicated cloud runner for the Star Research Brain.

This process owns no prediction credentials and never runs a polling research loop.
It wakes on a fixed four-hour cadence, calls the deterministic one-shot processor,
then returns to an interruptible sleep.  Brain execution remains disabled unless an
explicit later activation gate sets RESEARCH_BRAIN_ENABLED=true.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from automation import process_research_inbox_once
from inbox_adapter import ResearchEvidenceEventAdapter

RUNTIME_VERSION = "star-research-cloud-v1"
ROOT_ENV = "STAR_RESEARCH_PERSISTENT_ROOT"
DEFAULT_ROOT = "/var/data/star-research"
INTERVAL_ENV = "RESEARCH_BRAIN_WAKE_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 4 * 60 * 60

_stopping = False


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


def process_once(root: Path | None = None) -> dict[str, object]:
    root = _root() if root is None else root.resolve()
    paths = ensure_quarantine(root)
    adapter = ResearchEvidenceEventAdapter(Path(paths["inbox"]) / "events.json")
    result = process_research_inbox_once(
        adapter,
        state_path=Path(paths["knowledge"]) / "automation_state.json",
        wake_lock_path=Path(paths["audit"]) / "wake.lock",
        prior_by_context={},
        knowledge_by_context={},
        source_hash_resolver=lambda event: event["source_hash"],
        sandbox_executor=lambda context, rq, key: {
            "status": "SANDBOX_PROPOSAL_RECORDED",
            "context": context,
            "rq_id": rq["rq_id"],
            "experiments": 0,
            "knowledge_key": key,
        },
    )
    if result.get("status") == "SAFE_NOOP_SLEEPING":
        result = {**result, "status": "SAFE_NOOP_DISABLED"}
    return {"runtime_version": RUNTIME_VERSION, **result}


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
    print(json.dumps({"event": "RESEARCH_SERVICE_STARTED", **status(root)}, sort_keys=True), flush=True)
    # First invocation proves Disabled/Kill behavior without waiting four hours.
    print(json.dumps({"event": "RESEARCH_WAKE_RESULT", **process_once(root)}, sort_keys=True), flush=True)
    interval = max(60, int(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL_SECONDS)))
    while not _stopping:
        deadline = time.monotonic() + interval
        while not _stopping and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
        if not _stopping:
            print(json.dumps({"event": "RESEARCH_WAKE_RESULT", **process_once(root)}, sort_keys=True), flush=True)
    print(json.dumps({"event": "RESEARCH_SERVICE_STOPPED"}), flush=True)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(json.dumps(status(), ensure_ascii=False, sort_keys=True))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "--process-once":
        print(json.dumps(process_once(), ensure_ascii=False, sort_keys=True))
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
