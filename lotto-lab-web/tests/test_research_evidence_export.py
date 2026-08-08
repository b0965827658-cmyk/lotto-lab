import json
from pathlib import Path

import research_evidence_export as export


def write_journal(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records, "journal_sha256": export._sha(records)}), encoding="utf-8")


def test_fixed_private_authenticated_read_only_export(tmp_path, monkeypatch):
    monkeypatch.setenv(export.SECRET_ENV, "separate-secret")
    monkeypatch.setenv(export.PRIVATE_HOST_ENV, "lotto-lab-candidate-a-staging")
    record = {"draw_id": "1", "validity_status": "valid", "record_sha256": "upstream"}
    journal = tmp_path / "evidence" / "tw539_evidence_journal.json"
    write_journal(journal, [record])
    before = journal.read_bytes()
    assert export.authorized(next(iter(export.ROUTES)), "separate-secret", "lotto-lab-candidate-a-staging:10000")
    assert not export.authorized(next(iter(export.ROUTES)), "bad", "lotto-lab-candidate-a-staging:10000")
    assert not export.authorized(next(iter(export.ROUTES)), "separate-secret", "public.example")
    result = export.export_page("/api/internal/research-evidence/tw539/evidence", tmp_path)
    assert result["records"][0]["immutable_payload"] == record
    assert result["records"][0]["record_hash"] == export._sha(record)
    assert journal.read_bytes() == before


def test_export_rejects_path_and_tampered_journal(tmp_path):
    try:
        export.export_page("/api/internal/research-evidence/../../etc/passwd", tmp_path)
        raise AssertionError("path accepted")
    except ValueError:
        pass
    path = tmp_path / "fantasy5_forward_partial" / "partial_snapshot_journal.json"
    path.parent.mkdir(parents=True); path.write_text(json.dumps({"records": [{}], "journal_sha256": "bad"}))
    try:
        export.export_page("/api/internal/research-evidence/fantasy5/snapshots", tmp_path)
        raise AssertionError("tamper accepted")
    except ValueError as exc:
        assert "INTEGRITY" in str(exc)


def test_snapshot_filters_non_natural(tmp_path):
    valid = {"draw_id": "2", "snapshot_type": "PARTIAL_SNAPSHOT", "locked": True, "snapshot_sha256": "s"}
    write_journal(tmp_path / "fantasy5_forward_partial" / "partial_snapshot_journal.json", [valid, {**valid, "draw_id": "3", "validation_only": True}])
    result = export.export_page("/api/internal/research-evidence/fantasy5/snapshots", tmp_path)
    assert result["pagination"]["total"] == 1
