from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import tw539_evidence_provenance as provenance
import tw539_evidence_runtime as runtime


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def sources(root: Path, *, late=False, tamper=False, include_actual=True):
    prediction = {
        "predictionTime": "2026-08-07T10:00:00+00:00",
        "drawId": "next-after:115000199",
        "drawDate": "2026-08-08",
        "top5": [1, 2, 3, 4, 5],
        "top10": list(range(1, 11)),
        "top15": list(range(1, 16)),
        "modelVersion": "current-v1",
        "repositoryVersion": "sha256:dataset",
        "datasetHash": "dataset",
    }
    record = {
        "recordType": "live-pre-draw", "game": "tw539", "status": "closed", "locked": True,
        "predictionCapturedAt": "2026-08-07T13:00:01+00:00" if late else "2026-08-07T10:00:00+00:00",
        "modelVersion": "current-v1", "sourceDataHash": "dataset", "prediction": prediction,
        "predictionHash": hashlib.sha256(canonical(prediction).encode()).hexdigest(),
        "settlement": {"drawId": "115000200", "winningNumbers": [1, 6, 11, 16, 21], "settledAt": "2026-08-07T13:00:00+00:00"},
        "closedAt": "2026-08-07T13:00:00+00:00",
    }
    if tamper:
        record["prediction"]["top5"] = [2, 3, 4, 5, 6]
    write_json(root / provenance.CURRENT_JOURNAL, [record])
    write_json(root / provenance.REGISTRY, {"subjects": {"candidate-c": {"subject_type": "candidate", "status": "PROTOTYPE"}}})
    if include_actual:
        write_json(root / provenance.ACTUAL_DATABASE, [{
            "game": "tw539", "period": "115000200", "date": "2026-08-08", "numbers": [1, 6, 11, 16, 21],
            "source": "official", "sourceUrl": "https://example.invalid/official", "verified": True, "dataVersion": "source-row-v1",
        }])


def test_current_provenance_and_prediction_before_actual(tmp_path):
    sources(tmp_path)
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    assert manifest["provenance_status"] == "TRUSTED_CURRENT_ONLY"
    assert len(manifest["predictions"]) == len(manifest["actuals"]) == 1
    assert manifest["predictions"][0]["prediction_created_at"] < manifest["actuals"][0]["actual_available_at"]
    assert manifest["predictions"][0]["prediction_hash"] == runtime.make_prediction_hash(manifest["predictions"][0])


def test_hash_tamper_rejected(tmp_path):
    sources(tmp_path, tamper=True)
    with pytest.raises(provenance.ProvenanceError, match="hash mismatch"):
        provenance._build_tw539_evidence_manifest_from_root(tmp_path)


def test_late_prediction_is_not_valid_evidence(tmp_path):
    sources(tmp_path, late=True)
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    result = runtime.run_tw539_daily_evidence(manifest, test_directory=tmp_path / "evidence", now="2026-08-07T14:00:00+00:00")
    journal = json.loads((tmp_path / "evidence" / "tw539_evidence_journal.json").read_text())
    assert journal["records"][0]["validity_status"] == "invalid"
    assert journal["records"][0]["invalid_reason"] == "invalid_late_prediction"


@pytest.mark.parametrize("include_actual", [False])
def test_missing_actual_safe_noop(tmp_path, include_actual):
    sources(tmp_path, include_actual=include_actual)
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    assert manifest["predictions"] == [] and manifest["actuals"] == []


def test_missing_prediction_safe_noop(tmp_path):
    write_json(tmp_path / provenance.ACTUAL_DATABASE, [])
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    assert manifest["provenance_status"] == "SAFE_NOOP_NO_ELIGIBLE_CURRENT"


def test_subjects_without_live_provenance_are_excluded(tmp_path):
    sources(tmp_path)
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    assert [item["subject_type"] for item in manifest["predictions"]] == ["current"]
    assert manifest["excluded_subjects"]["baseline"] == "NO_VALID_LIVE_PROVENANCE"
    assert manifest["excluded_subjects"]["candidate_c"] == "Prototype / Awaiting Shadow"


def test_ten_runs_and_concurrent_runs_deduplicate(tmp_path, monkeypatch):
    sources(tmp_path)
    evidence = tmp_path / "evidence"
    results = [provenance._run_tw539_daily_evidence_auto_from_root(tmp_path, evidence) for _ in range(10)]
    assert [item["records_added"] for item in results] == [1] + [0] * 9
    with ThreadPoolExecutor(max_workers=5) as pool:
        concurrent = list(pool.map(lambda _: provenance._run_tw539_daily_evidence_auto_from_root(tmp_path, evidence), range(5)))
    assert all(item["records_added"] == 0 for item in concurrent)
    journal = json.loads((evidence / "tw539_evidence_journal.json").read_text())
    assert len(journal["records"]) == 1


def test_public_auto_contract_accepts_no_arguments():
    with pytest.raises(TypeError):
        provenance.run_tw539_daily_evidence_auto({"draw_id": "x"})
    with pytest.raises(TypeError):
        provenance.build_tw539_evidence_manifest("/tmp/evil")


def test_no_historical_backfill_or_candidate_live_data(tmp_path):
    sources(tmp_path)
    manifest = provenance._build_tw539_evidence_manifest_from_root(tmp_path)
    assert all(item["subject_type"] != "candidate" for item in manifest["predictions"])
    assert len(manifest["actuals"]) == 1
