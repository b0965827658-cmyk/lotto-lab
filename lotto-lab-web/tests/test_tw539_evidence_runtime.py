from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

import pytest

import tw539_evidence_runtime as runtime


def prediction(subject_type="current", version="current-v1", *, created="2026-08-07T10:00:00+00:00", locked="2026-08-07T10:00:01+00:00"):
    value = {
        "lottery": "tw539",
        "draw_id": "115000190",
        "subject_type": subject_type,
        "subject_version": version,
        "prediction_created_at": created,
        "locked_at": locked,
        "top5": [1, 2, 3, 4, 5],
        "top10": list(range(1, 11)),
        "top15": list(range(1, 16)),
        "dataset_version": "dataset-v1",
        "dataset_sha256": "d" * 64,
    }
    value["prediction_hash"] = runtime.make_prediction_hash(value)
    return value


def manifest(*, candidate_status="PROTOTYPE", candidate=True):
    predictions = [prediction(), prediction("baseline", "baseline-v1")]
    if candidate:
        item = prediction("candidate", "candidate-c1")
        item["top15"] = list(range(2, 17))
        item["prediction_hash"] = runtime.make_prediction_hash(item)
        predictions.append(item)
    return {
        "predictions": predictions,
        "actuals": [{
            "lottery": "tw539",
            "draw_id": "115000190",
            "actual_available_at": "2026-08-07T12:00:00+00:00",
            "settled_at": "2026-08-07T12:00:05+00:00",
            "actual": [1, 2, 3, 16, 17],
            "dataset_version": "dataset-v1",
            "dataset_sha256": "d" * 64,
        }],
        "registry": {"subjects": {"candidate-c1": {"subject_type": "candidate", "status": candidate_status}}},
    }


def read_journal(directory: Path):
    return json.loads((directory / "tw539_evidence_journal.json").read_text(encoding="utf-8"))


def run(value, directory):
    return runtime.run_tw539_daily_evidence(value, test_directory=directory, now="2026-08-07T13:00:00+00:00")


def test_deterministic_output_and_prediction_immutability(tmp_path):
    value = manifest(candidate_status="OBSERVATION")
    before = copy.deepcopy(value)
    first = run(value, tmp_path)
    journal_before = (tmp_path / "tw539_evidence_journal.json").read_bytes()
    second = run(value, tmp_path)
    assert value == before
    assert first["journal_sha256"] == second["journal_sha256"]
    assert journal_before == (tmp_path / "tw539_evidence_journal.json").read_bytes()


def test_ten_replays_are_idempotent(tmp_path):
    value = manifest(candidate_status="OBSERVATION")
    results = [run(value, tmp_path) for _ in range(10)]
    assert results[0]["record_count"] == 3
    assert all(result["record_count"] == 3 for result in results)
    assert sum(result["records_added"] for result in results) == 3
    assert len({result["journal_sha256"] for result in results}) == 1
    dashboard = (tmp_path / "evidence_dashboard.json").read_bytes()
    run(value, tmp_path)
    assert dashboard == (tmp_path / "evidence_dashboard.json").read_bytes()


def test_candidate_prototype_isolation(tmp_path):
    result = run(manifest(candidate_status="PROTOTYPE"), tmp_path)
    records = read_journal(tmp_path)["records"]
    assert result["candidate_predictions_isolated"] == 1
    assert result["candidate_status"] == "Prototype / Awaiting Shadow"
    assert {record["subject_type"] for record in records} == {"current", "baseline"}


@pytest.mark.parametrize("state", ["SHADOW_RUNTIME", "OBSERVATION"])
def test_candidate_live_shadow_acceptance(state, tmp_path):
    result = run(manifest(candidate_status=state), tmp_path)
    candidates = [record for record in read_journal(tmp_path)["records"] if record["subject_type"] == "candidate"]
    assert len(candidates) == 1
    assert candidates[0]["validity_status"] == "valid"
    assert result["candidate_status"] == state


def test_late_prediction_is_invalid_and_excluded_from_eps(tmp_path):
    value = manifest(candidate=False)
    late = prediction(created="2026-08-07T12:00:01+00:00", locked="2026-08-07T12:00:02+00:00")
    value["predictions"] = [late]
    result = run(value, tmp_path)
    record = read_journal(tmp_path)["records"][0]
    assert record["validity_status"] == "invalid"
    assert record["invalid_reason"] == "invalid_late_prediction"
    assert record["hits_top15"] is None
    assert result["eps"]["valid"] is False
    aggregation = json.loads((tmp_path / "evidence_registry_stats.json").read_text(encoding="utf-8"))
    assert aggregation["subjects"] == {}


def test_invalid_prediction_hash_is_rejected(tmp_path):
    value = manifest(candidate=False)
    value["predictions"][0]["prediction_hash"] = "bad"
    run(value, tmp_path)
    record = next(record for record in read_journal(tmp_path)["records"] if record["subject_type"] == "current")
    assert record["invalid_reason"] == "invalid_prediction_hash"
    assert record["hits_top5"] is None


def test_missing_actual_is_safe_noop(tmp_path):
    value = manifest()
    value["actuals"] = []
    result = run(value, tmp_path)
    assert result["status"] == "no_settled_draw"
    assert not (tmp_path / "tw539_evidence_journal.json").exists()


def test_dashboard_reads_journal_aggregation_only(tmp_path):
    run(manifest(candidate_status="OBSERVATION"), tmp_path)
    dashboard = json.loads((tmp_path / "evidence_dashboard.json").read_text(encoding="utf-8"))
    assert dashboard["source"] == "verified Evidence Journal aggregation only"
    assert "prototype" not in json.dumps(dashboard).lower()
    assert dashboard["journal_sha256"] == read_journal(tmp_path)["journal_sha256"]


def test_eps_uses_valid_live_evidence_only(tmp_path):
    value = manifest(candidate_status="OBSERVATION")
    value["predictions"][0]["prediction_hash"] = "bad"
    result = run(value, tmp_path)
    assert result["eps"]["valid"] is False
    assert result["eps"]["score"] is None


def test_atomic_write_preserves_old_file_on_pre_replace_failure(tmp_path):
    target = tmp_path / "journal.json"
    target.write_text('{"old":true}', encoding="utf-8")
    with pytest.raises(OSError):
        runtime._atomic_write_json(target, {"new": True}, fail_at="after_file_fsync")
    assert target.read_text(encoding="utf-8") == '{"old":true}'
    assert not list(tmp_path.glob(".*.tmp"))


def test_replace_failure_after_replace_is_recoverable(tmp_path):
    target = tmp_path / "journal.json"
    target.write_text('{"old":true}', encoding="utf-8")
    with pytest.raises(OSError):
        runtime._atomic_write_json(target, {"new": True}, fail_at="after_replace")
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob(".*.tmp"))


def test_corrupted_journal_is_isolated_without_new_journal(tmp_path):
    path = tmp_path / "tw539_evidence_journal.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(runtime.JournalCorruptionError):
        run(manifest(), tmp_path)
    assert not path.exists()
    assert len(list(tmp_path.glob("tw539_evidence_journal.json.corrupt.*"))) == 1


def test_restart_deduplication(tmp_path):
    first = run(manifest(candidate_status="OBSERVATION"), tmp_path)
    runtime._PROCESS_LOCK = threading.RLock()
    second = run(manifest(candidate_status="OBSERVATION"), tmp_path)
    assert first["journal_sha256"] == second["journal_sha256"]
    assert second["records_added"] == 0


def test_lock_contention_is_nonblocking(tmp_path):
    lock = runtime.CrossProcessFileLock(tmp_path / "tw539_evidence_journal.lock")
    lock.acquire()
    try:
        with pytest.raises(runtime.LockUnavailableError):
            runtime.CrossProcessFileLock(tmp_path / "tw539_evidence_journal.lock").acquire()
    finally:
        lock.release()


def test_concurrent_invocation_has_no_duplicates(tmp_path):
    outcomes = []
    failures = []

    def invoke():
        try:
            outcomes.append(run(manifest(candidate_status="OBSERVATION"), tmp_path))
        except runtime.LockUnavailableError as exc:
            failures.append(exc)

    threads = [threading.Thread(target=invoke) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not failures
    assert len(read_journal(tmp_path)["records"]) == 3
    assert sum(outcome["records_added"] for outcome in outcomes) == 3


def test_stale_temp_file_recovery(tmp_path):
    stale = tmp_path / ".tw539_evidence_journal.json.abcd.tmp"
    stale.write_text("partial", encoding="utf-8")
    result = run(manifest(), tmp_path)
    assert stale.name in result["recovered_temporary_files"]
    assert not stale.exists()


def test_persistent_path_fail_safe(monkeypatch, tmp_path):
    monkeypatch.delenv("LOTTO_PERSISTENT_DATA_DIR", raising=False)
    with pytest.raises(runtime.PersistentPathError):
        runtime.resolve_evidence_dir()
    monkeypatch.setenv("LOTTO_PERSISTENT_DATA_DIR", "relative/path")
    with pytest.raises(runtime.PersistentPathError):
        runtime.resolve_evidence_dir()
    assert runtime.resolve_evidence_dir(test_directory=tmp_path) == tmp_path.resolve()


def test_record_and_journal_integrity_validation(tmp_path):
    run(manifest(), tmp_path)
    journal = runtime._load_journal(tmp_path / "tw539_evidence_journal.json", isolate_corrupt=False)
    assert journal["journal_sha256"] == runtime.sha256_value(journal["records"])
    for record in journal["records"]:
        core = {key: value for key, value in record.items() if key != "record_sha256"}
        assert record["record_sha256"] == runtime.sha256_value(core)
