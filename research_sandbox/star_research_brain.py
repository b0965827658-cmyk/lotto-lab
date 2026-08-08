"""CLI for one-shot Research Inbox processing. No polling loop."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from automation import process_research_inbox_once
from inbox_adapter import ResearchEvidenceEventAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process-inbox-once", action="store_true")
    parser.add_argument("--sandbox-root", type=Path, required=True)
    args = parser.parse_args()
    if not args.process_inbox_once:
        parser.error("--process-inbox-once is required")
    root = args.sandbox_root.resolve()
    adapter = ResearchEvidenceEventAdapter(root / "inbox.json")
    # Production CLI has no arbitrary executor or filesystem resolver from payload.
    resolver = lambda event: event["source_hash"]
    executor = lambda context, rq, key: {"status": "SANDBOX_PROPOSAL_RECORDED", "context": context, "rq_id": rq["rq_id"], "experiments": 0, "knowledge_key": key}
    result = process_research_inbox_once(
        adapter, state_path=root/"automation_state.json", wake_lock_path=root/"wake.lock",
        prior_by_context={}, knowledge_by_context={}, source_hash_resolver=resolver,
        sandbox_executor=executor,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
