from __future__ import annotations

import json

import pytest

from inbox_adapter import ResearchEvidenceEventAdapter
from natural_evidence import (
    CANONICALIZATION_VERSION,
    HASH_ALGORITHM,
    INTEGRITY_CONTRACT_VERSION,
    SOURCE_HASH_TYPE,
    NaturalEvidenceReadClient,
    _sha,
)


def source(payload: dict, *, source_type: str = "TW539_VALID_LIVE_EVIDENCE") -> dict:
    digest = _sha(payload)
    return {
        "lottery_context": "TW539" if source_type.startswith("TW539") else "FANTASY5",
        "event_type": {
            "TW539_VALID_LIVE_EVIDENCE": "VALID_LIVE_EVIDENCE",
            "TW539_TELEMETRY_MILESTONE": "TELEMETRY_MILESTONE",
            "FANTASY5_FORWARD_SNAPSHOT": "NATURAL_FORWARD_SNAPSHOT",
            "FANTASY5_FORWARD_SETTLEMENT": "FORWARD_SETTLEMENT",
        }[source_type],
        "source_id": f"{source_type}:{payload.get('draw_id', payload.get('milestone_id', digest[:16]))}",
        "source_version": "v1",
        "source_hash": digest,
        "computed_source_hash": digest,
        "source_quality": "NATURAL_IMMUTABLE",
        "evidence_grade": "E2",
        "created_at": "2026-08-09T00:00:00Z",
        "provenance": "authenticated_read_only_export",
        "timing_valid": True,
        "materiality_inputs": {"sample_size": 1, "milestone": int(payload.get("threshold", 0))},
        "affected_knowledge_ids": [],
        "integrity_contract_version": INTEGRITY_CONTRACT_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "source_hash_type": SOURCE_HASH_TYPE,
    }


@pytest.mark.parametrize("source_type,payload", [
    ("TW539_VALID_LIVE_EVIDENCE", {"draw_id": "V-TW", "numbers": [1, 2, 3, 4, 5]}),
    ("TW539_TELEMETRY_MILESTONE", {"milestone_id": "TW539-LIVE-5", "threshold": 5}),
    ("FANTASY5_FORWARD_SNAPSHOT", {"draw_id": "V-F5-S", "locked": True}),
    ("FANTASY5_FORWARD_SETTLEMENT", {"draw_id": "V-F5-X", "actual": [6, 7, 8, 9, 10]}),
])
def test_four_sources_bind_event_identity_to_record_hash(tmp_path, source_type, payload):
    adapter = ResearchEvidenceEventAdapter(tmp_path / "events.json")
    row = source(payload, source_type=source_type)
    result = adapter.adapt(row)
    event = json.loads((tmp_path / "events.json").read_text())["events"][0]
    assert result["status"] == "ENQUEUED"
    assert event["source_hash"] == _sha(payload)
    assert event["source_hash_type"] == "RECORD_HASH"
    assert event["integrity_contract_version"] == INTEGRITY_CONTRACT_VERSION


def test_transport_key_order_does_not_change_record_hash():
    assert _sha({"a": 1, "b": [2, 3]}) == _sha({"b": [2, 3], "a": 1})
    assert _sha({"a": 1}) != _sha({"a": 2})


@pytest.mark.parametrize("field,value", [("draw_id", "tampered"), ("timestamp", "later"), ("number", 39), ("score", 1.5), ("context", "FANTASY5")])
def test_semantic_tamper_changes_record_hash(field, value):
    original = {"draw_id": "V", "timestamp": "2026-08-09T00:00:00Z", "number": 1, "score": 1.0, "context": "TW539"}
    changed = {**original, field: value}
    assert _sha(original) != _sha(changed)


def test_client_resolver_revalidates_exported_record(monkeypatch):
    payload = {"draw_id": "V", "numbers": [1, 2, 3, 4, 5]}
    digest = _sha(payload)
    client = NaturalEvidenceReadClient("http://lotto-lab-candidate-a-staging:10000", "secret")
    monkeypatch.setattr(client, "fetch", lambda source_type, cursor=0: {
        "records": [{"record_hash": digest, "immutable_payload": payload}],
        "pagination": {"next_cursor": None},
    })
    assert client.resolve_event_source_hash({"source_id": "TW539_VALID_LIVE_EVIDENCE:V", "source_hash": digest}) == digest
    assert client.resolve_event_source_hash({"source_id": "TW539_VALID_LIVE_EVIDENCE:V", "source_hash": "0" * 64}) == "SOURCE_RECORD_NOT_FOUND"


def test_repaired_event_supersedes_legacy_without_rewrite(tmp_path):
    path = tmp_path / "events.json"
    adapter = ResearchEvidenceEventAdapter(path)
    row = source({"draw_id": "V", "numbers": [1, 2, 3, 4, 5]})
    legacy = adapter.adapt({**row, "integrity_contract_version": "legacy", "canonicalization_version": "legacy"})
    journal = json.loads(path.read_text())
    journal["events"][0]["adapter_version"] = "star-research-event-adapter-v1"
    journal["events"][0]["unique_key"] = journal["events"][0]["unique_key"].replace("star-research-event-adapter-v2", "star-research-event-adapter-v1")
    path.write_text(json.dumps(journal))
    repaired = adapter.adapt(row)
    after = json.loads(path.read_text())
    assert repaired["status"] == "ENQUEUED"
    assert len(after["events"]) == 2
    assert after["events"][1]["supersedes_event_id"] == legacy["event_id"]
    assert after["events"][1]["supersede_reason"] == "SOURCE_INTEGRITY_CONTRACT_REPAIRED"
