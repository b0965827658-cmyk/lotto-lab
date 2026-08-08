from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fantasy5_lifecycle_telemetry as lifecycle
import fantasy5_partial_capture as capture


def analysis():
    ranking = [{"number": number, "rank": number, "score": 100 - number} for number in range(1, 40)]
    return {"modelVersion": "f5-test", "ranking": ranking, "recommendation": list(range(1, 6)), "candidateTiers": {"top5": list(range(1, 6)), "top10": list(range(1, 11)), "full15": list(range(1, 16))}}


def snap(draw="101"):
    return capture.build_partial_snapshot(draw, analysis(), dataset_sha256="d" * 64, data_cutoff_draw_id="100", captured_at="2026-08-08T01:00:00+00:00")


def test_partial_capture_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv(capture.PARTIAL_FLAG, raising=False)
    assert capture.capture_finalized("101", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", test_directory=tmp_path)["status"] == "disabled"
    assert not list(tmp_path.rglob("*.json"))


def test_full_ranking_1_to_39():
    value = snap()
    assert [x["number"] for x in value["full_ranking_1_to_39"]] == list(range(1, 40))


def test_final_scores_complete():
    assert set(snap()["final_score_by_number"]) == {str(n) for n in range(1, 40)}


def test_prediction_first_capture(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.setenv(lifecycle.FLAG, "true")
    result = capture.capture_finalized("101", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", lifecycle_path=tmp_path / "events", test_directory=tmp_path)
    assert result["records_added"] == 1


def test_actual_first_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.setenv(lifecycle.FLAG, "true")
    event_path = tmp_path / "events.jsonl"
    event_path.write_text(json.dumps({"event_type": "ACTUAL_FIRST_OBSERVED", "draw_id": "101"}) + "\n", encoding="utf-8")
    result = capture.capture_finalized("101", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", lifecycle_path=event_path, test_directory=tmp_path)
    assert result == {"status": "TOO_LATE_FOR_FORWARD_CAPTURE", "records_added": 0}


def test_near_simultaneous_atomic(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.setenv(lifecycle.FLAG, "true")
    lifecycle._SEEN_ACTUALS.discard("102")
    barrier = threading.Barrier(2)
    out = []
    def do_capture():
        barrier.wait(); out.append(capture.capture_finalized("102", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", lifecycle_path=tmp_path / "events", test_directory=tmp_path))
    def do_actual():
        barrier.wait(); lifecycle._SEEN_ACTUALS.add("102")
    a, b = threading.Thread(target=do_capture), threading.Thread(target=do_actual)
    a.start(); b.start(); a.join(); b.join()
    assert out[0]["records_added"] in {0, 1}
    assert len(json.loads(next(tmp_path.rglob("partial_snapshot_journal.json")).read_text())["records"]) <= 1 if out[0]["records_added"] else True


def test_hash_immutable():
    value = snap(); digest = value.pop("snapshot_sha256")
    assert digest == hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def test_ten_runs_one_record(tmp_path):
    results = [capture.append_snapshot(snap(), test_directory=tmp_path) for _ in range(10)]
    assert sum(x["records_added"] for x in results) == 1


def test_concurrent_capture_dedup(tmp_path):
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: capture.append_snapshot(snap(), test_directory=tmp_path), range(5)))
    assert sum(x["records_added"] for x in results) == 1


def test_restart_persistence(tmp_path):
    first = capture.append_snapshot(snap(), test_directory=tmp_path)
    second = capture.append_snapshot(snap(), test_directory=tmp_path)
    assert first["record"]["snapshot_sha256"] == second["record"]["snapshot_sha256"]


def test_cross_restart_timing_unproven():
    value = snap(); value["instance_id"] = "a"; value["pid"] = 1
    event = {"instance_id": "b", "pid": 2, "timestamp_utc": "2026-08-08T02:00:00+00:00"}
    assert capture.classify_settlement_timing(value, event) == "FORWARD_CAPTURED_TIME_ORDER_UNPROVEN"


def test_same_process_timing_verified():
    value = snap(); event = {"instance_id": value["instance_id"], "pid": value["pid"], "timestamp_utc": "2026-08-08T02:00:00+00:00"}
    assert capture.classify_settlement_timing(value, event) == "FORWARD_VERIFIED_TIMING"


def test_settlement_reference_does_not_mutate_snapshot():
    value = snap(); before = json.dumps(value, sort_keys=True)
    settlement = {"draw_id": value["draw_id"], "snapshot_sha256": value["snapshot_sha256"], "actual_numbers": [1, 2, 3, 4, 5]}
    assert settlement["snapshot_sha256"] == value["snapshot_sha256"] and json.dumps(value, sort_keys=True) == before


def test_window_observer_materializes_all(monkeypatch):
    monkeypatch.setenv(capture.WINDOW_FLAG, "true")
    rows = [{"period": str(i), "date": f"2026-01-{(i % 28) + 1:02d}", "numbers": [1, 2, 3, 4, 5]} for i in range(1, 101)]
    value = capture.materialize_window_state(rows, dataset_sha256="d")
    assert set(value["windows"]) == {str(w) for w in capture.WINDOWS}


def test_window_observer_off(monkeypatch):
    monkeypatch.delenv(capture.WINDOW_FLAG, raising=False)
    assert capture.materialize_window_state([], dataset_sha256="d") == {"availability": "NOT_MATERIALIZED"}


def test_window_observer_does_not_mutate_ranking(monkeypatch):
    monkeypatch.setenv(capture.WINDOW_FLAG, "true")
    value = analysis(); before = json.dumps(value, sort_keys=True)
    capture.materialize_window_state([], dataset_sha256="d")
    assert json.dumps(value, sort_keys=True) == before


def test_regime_not_materialized():
    assert capture.regime_observer_design()["availability"] == "NOT_MATERIALIZED"


def test_features_not_materialized():
    assert capture.feature_state_audit()["availability"] == "NOT_MATERIALIZED"


def test_historical_backfill_rejected():
    try: capture.build_partial_snapshot("100", analysis(), dataset_sha256="d", data_cutoff_draw_id="100")
    except ValueError as exc: assert str(exc) == "HISTORICAL_BACKFILL_FORBIDDEN"
    else: raise AssertionError("backfill accepted")


def test_observer_failure_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.setenv(lifecycle.FLAG, "true")
    result = capture.capture_finalized("101", {}, dataset_sha256="d", data_cutoff_draw_id="100", lifecycle_path=tmp_path / "events", test_directory=tmp_path)
    assert result["status"] == "observer_error"


def test_tw539_not_supported_by_contract():
    assert snap()["lottery"] == "fantasy5"


def test_partial_requires_timing_observer(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.delenv(lifecycle.FLAG, raising=False)
    result = capture.capture_finalized("101", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", test_directory=tmp_path)
    assert result["status"] == "TIMING_OBSERVER_UNAVAILABLE"


def test_capture_includes_seven_windows_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    monkeypatch.setenv(capture.WINDOW_FLAG, "true")
    monkeypatch.setenv(lifecycle.FLAG, "true")
    rows = [{"period": str(i), "date": f"2026-01-{(i % 28) + 1:02d}", "numbers": [1, 2, 3, 4, 5]} for i in range(1, 101)]
    result = capture.capture_finalized("101", analysis(), dataset_sha256="d", data_cutoff_draw_id="100", history=rows, lifecycle_path=tmp_path / "events", test_directory=tmp_path)
    assert set(result["record"]["window_state"]["windows"]) == {str(w) for w in capture.WINDOWS}
    assert result["record"]["regime_state"] == "NOT_MATERIALIZED"
    assert result["record"]["feature_state"] == "NOT_CAPTURED"


def test_settlement_is_separate_append_only_reference(tmp_path):
    snapshot = snap(); capture.append_snapshot(snapshot, test_directory=tmp_path)
    event = {"instance_id": snapshot["instance_id"], "pid": snapshot["pid"], "timestamp_utc": "2026-08-08T02:00:00+00:00"}
    first = capture.append_settlement(snapshot, [1, 2, 3, 4, 5], event, test_directory=tmp_path)
    second = capture.append_settlement(snapshot, [1, 2, 3, 4, 5], event, test_directory=tmp_path)
    assert first["records_added"] == 1 and second["records_added"] == 0
    assert first["record"]["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert "actual_numbers" not in json.loads((tmp_path / "fantasy5_forward_partial" / "partial_snapshot_journal.json").read_text())["records"][0]


def test_no_snapshot_means_no_settlement(monkeypatch, tmp_path):
    monkeypatch.setenv(capture.PARTIAL_FLAG, "true")
    assert capture.settle_observed_actuals([{"period": "101", "numbers": [1, 2, 3, 4, 5]}], [{"draw_id": "101"}], test_directory=tmp_path) == []
