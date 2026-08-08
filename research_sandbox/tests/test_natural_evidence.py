import json

import natural_evidence as natural


class FakeClient:
    def __init__(self, payload): self.payload = payload
    def fetch(self, source_type, cursor=0):
        records = [self.payload] if source_type == "TW539_VALID_LIVE_EVIDENCE" else []
        return {"lottery_context": "TW539" if source_type.startswith("TW539") else "FANTASY5", "source_version": "v1", "source_quality": "NATURAL_IMMUTABLE", "created_at": "2026-08-09T00:00:00Z", "records": records, "pagination": {"next_cursor": None}}


def test_reconciliation_ten_runs_dedup_without_research(tmp_path):
    payload = {"draw_id": "natural-1", "validity_status": "valid"}
    item = {"immutable_payload": payload, "record_hash": natural._sha(payload)}
    results = [natural.reconcile_natural_research_events_once(tmp_path, FakeClient(item)) for _ in range(10)]
    journal = json.loads((tmp_path / "inbox" / "events.json").read_text())
    assert len(journal["events"]) == 1
    assert sum(x["records_added"] for x in results) == 1
    assert all(x["research_started"] == 0 and x["knowledge_written"] == 0 for x in results)


def test_client_rejects_non_private_host():
    for url in ("https://public.example", "http://evil", "file:///tmp/x"):
        try:
            natural.NaturalEvidenceReadClient(url, "secret")
            raise AssertionError("unsafe host accepted")
        except ValueError:
            pass
