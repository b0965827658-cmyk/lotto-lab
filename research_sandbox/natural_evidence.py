"""Fixed-endpoint read client and reconciliation for natural research evidence."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from inbox_adapter import ResearchEvidenceEventAdapter

SCHEMA_VERSION = "star-research-natural-export-v1"
ENDPOINTS = {
    "TW539_VALID_LIVE_EVIDENCE": "/api/internal/research-evidence/tw539/evidence",
    "TW539_TELEMETRY_MILESTONE": "/api/internal/research-evidence/tw539/milestones",
    "FANTASY5_FORWARD_SNAPSHOT": "/api/internal/research-evidence/fantasy5/snapshots",
    "FANTASY5_FORWARD_SETTLEMENT": "/api/internal/research-evidence/fantasy5/settlements",
}
EVENT_TYPES = {
    "TW539_VALID_LIVE_EVIDENCE": "VALID_LIVE_EVIDENCE",
    "TW539_TELEMETRY_MILESTONE": "TELEMETRY_MILESTONE",
    "FANTASY5_FORWARD_SNAPSHOT": "NATURAL_FORWARD_SNAPSHOT",
    "FANTASY5_FORWARD_SETTLEMENT": "FORWARD_SETTLEMENT",
}


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class NaturalEvidenceReadClient:
    def __init__(self, base_url: str, secret: str, *, timeout: float = 10):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname != "lotto-lab-candidate-a-staging" or parsed.path not in ("", "/"):
            raise ValueError("PRIVATE_APPROVED_HOST_REQUIRED")
        self.base_url, self.secret, self.timeout = base_url.rstrip("/"), secret, timeout

    def fetch(self, source_type: str, cursor: int = 0) -> dict[str, Any]:
        if source_type not in ENDPOINTS:
            raise ValueError("UNSUPPORTED_SOURCE")
        request = urllib.request.Request(
            f"{self.base_url}{ENDPOINTS[source_type]}?cursor={cursor}&limit=25",
            headers={"X-Research-Evidence-Read-Secret": self.secret, "Accept": "application/json"}, method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            value = json.loads(response.read())
        export_hash = value.pop("export_sha256", None)
        if value.get("schema_version") != SCHEMA_VERSION or value.get("source_type") != source_type or export_hash != _sha(value):
            raise ValueError("REJECTED_SOURCE_INTEGRITY")
        expected_context = "TW539" if source_type.startswith("TW539") else "FANTASY5"
        if value.get("lottery_context") != expected_context:
            raise ValueError("REJECTED_SOURCE_INTEGRITY")
        for item in value.get("records", []):
            if item.get("record_hash") != _sha(item.get("immutable_payload")):
                raise ValueError("REJECTED_SOURCE_INTEGRITY")
        return {**value, "export_sha256": export_hash}


def reconcile_natural_research_events_once(root: Path, client: NaturalEvidenceReadClient) -> dict[str, Any]:
    adapter = ResearchEvidenceEventAdapter(root / "inbox" / "events.json")
    added = duplicates = 0
    for source_type in ENDPOINTS:
        cursor = 0
        while cursor is not None:
            page = client.fetch(source_type, cursor)
            for item in page["records"]:
                payload, digest = item["immutable_payload"], item["record_hash"]
                milestone = int(payload.get("threshold", 0))
                if source_type == "TW539_VALID_LIVE_EVIDENCE":
                    timing_valid = payload.get("validity_status") == "valid"
                elif source_type == "FANTASY5_FORWARD_SNAPSHOT":
                    timing_valid = bool(payload.get("locked")) and payload.get("timing_classification") != "TOO_LATE_FOR_FORWARD_CAPTURE"
                else:
                    timing_valid = True
                source = {
                    "lottery_context": page["lottery_context"], "event_type": EVENT_TYPES[source_type],
                    "source_id": f"{source_type}:{payload.get('draw_id') or payload.get('milestone_id') or digest[:16]}",
                    "source_version": page["source_version"], "source_hash": digest, "computed_source_hash": digest,
                    "source_quality": page["source_quality"], "evidence_grade": "E2", "created_at": page["created_at"],
                    "provenance": "authenticated_read_only_export", "timing_valid": timing_valid,
                    "materiality_inputs": {"sample_size": payload.get("qualified_sample_count", milestone), "milestone": milestone, "forward_verified_count": 1 if source_type.startswith("FANTASY5") else 0},
                    "affected_knowledge_ids": [],
                }
                result = adapter.adapt(source)
                added += int(result.get("records_added", 0)); duplicates += int(result.get("status") in {"DUPLICATE_DEDUPED", "MILESTONE_ALREADY_EMITTED"})
            cursor = page["pagination"]["next_cursor"]
    return {"status": "RECONCILED", "records_added": added, "duplicates": duplicates, "research_started": 0, "knowledge_written": 0}


def configured_client() -> NaturalEvidenceReadClient | None:
    url, secret = os.environ.get("RESEARCH_EVIDENCE_BASE_URL", ""), os.environ.get("RESEARCH_EVIDENCE_READ_SECRET", "")
    return NaturalEvidenceReadClient(url, secret) if url and secret else None
