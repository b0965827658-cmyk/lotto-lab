import json
import os
from pathlib import Path

import fantasy5_lifecycle_telemetry as t


def analysis():
    return {
        "ranking": [{"number": n, "rank": n, "score": 40-n} for n in range(1, 40)],
        "candidateTiers": {"top5": list(range(1,6)), "top10": list(range(1,11)), "full15": list(range(1,16))},
        "statistics": {"windowFrequencies": {"30": {}}},
    }


def enable(monkeypatch):
    monkeypatch.setenv(t.FLAG, "true")
    t._PENDING_TARGETS.clear(); t._SEEN_ACTUALS.clear(); t.clear_job_context()


def test_flag_false_has_zero_side_effect(monkeypatch, tmp_path):
    monkeypatch.delenv(t.FLAG, raising=False); p=tmp_path/"e.jsonl"
    assert t.emit("RANKING_STARTED", path=p) is None and not p.exists()


def test_event_schema_and_clocks(monkeypatch, tmp_path):
    enable(monkeypatch);p=tmp_path/"e.jsonl";e=t.emit("RANKING_STARTED",draw_id=1,path=p,utc_clock=lambda:"u",monotonic_clock=lambda:2.0)
    for key in ("event_id","draw_id","timestamp_utc","monotonic_timestamp","instance_id","pid","process_start_time","job_id","event_type","dataset_sha256","runtime_commit","event_sha256"): assert key in e
    assert e["timestamp_utc"]=="u" and e["monotonic_timestamp"]==2.0


def test_finalized_availability(monkeypatch,tmp_path):
    enable(monkeypatch);e=t.prediction_finalized(12000,"d",analysis(),path=tmp_path/"e")
    assert e["details"]["full_ranking_available"] and e["details"]["final_scores_available"]
    assert e["details"]["window_state_availability"]["30"]=="AVAILABLE_IN_MEMORY"
    assert e["details"]["window_state_availability"]["5"]=="RECOMPUTE_REQUIRED"
    assert e["details"]["regime_availability"]=="NOT_AVAILABLE"


def test_uncertain_regime_is_observable(monkeypatch):
    a=analysis();a["stateDetection"]={"regime_id":"UNCERTAIN","regime_confidence":0,"change_probability":0,"change_point_state":"none"}
    assert t.availability(a)["regime_availability"]=="AVAILABLE_IN_MEMORY"


def test_partial_feature(monkeypatch):
    a=analysis();a["featureImportance"]={"partial":True}
    assert t.availability(a)["per_number_feature_availability"]=="PARTIAL"


def test_prediction_first_actual_later(monkeypatch,tmp_path):
    enable(monkeypatch);p=tmp_path/"e";t.prediction_finalized(12001,"d",analysis(),path=p)
    assert len(t.observe_actuals([{"period":"12001","numbers":[1,2,3,4,5]}],source="fixture",path=p))==1
    assert t.capture_windows(t.read_events(p))[0]["ordering"]=="PREDICTION_FIRST"


def test_actual_before_prediction_is_not_backfilled(monkeypatch,tmp_path):
    enable(monkeypatch);p=tmp_path/"e";assert len(t.observe_actuals([{"period":"12002","numbers":[1,2,3,4,5]}],source="fixture",path=p))==1
    t.prediction_finalized(12002,"d",analysis(),path=p)
    assert t.capture_windows(t.read_events(p))[0]["ordering"]=="UNPROVEN"


def test_near_simultaneous_uses_monotonic(monkeypatch,tmp_path):
    enable(monkeypatch);p=tmp_path/"e";seq=iter([1.0,1.000001]);t.emit("RANKING_FINALIZED",draw_id=3,path=p,monotonic_clock=lambda:next(seq));t.emit("ACTUAL_FIRST_OBSERVED",draw_id=3,path=p,monotonic_clock=lambda:next(seq))
    assert t.capture_windows(t.read_events(p))[0]["capture_window_ms"]>0


def test_dedup_actual_first_observed(monkeypatch,tmp_path):
    enable(monkeypatch);p=tmp_path/"e";t.prediction_finalized(4,"d",analysis(),path=p);row={"period":"4","numbers":[1,2,3,4,5]}
    assert len(t.observe_actuals([row],source="x",path=p))==1 and t.observe_actuals([row],source="x",path=p)==[]


def test_concurrent_actual_dedup(monkeypatch,tmp_path):
    import concurrent.futures
    enable(monkeypatch);p=tmp_path/"e";t.prediction_finalized(5,"d",analysis(),path=p);row={"period":"5","numbers":[1,2,3,4,5]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: out=list(ex.map(lambda _:t.observe_actuals([row],source="x",path=p),range(5)))
    assert sum(map(len,out))==1


def test_exception_isolated(monkeypatch,tmp_path):
    enable(monkeypatch);bad=tmp_path/"file";bad.write_text("x")
    assert t.emit("RANKING_STARTED",path=bad/"child") is None


def test_corrupt_line_isolated(tmp_path):
    p=tmp_path/"e";p.write_text('{bad}\n{"event_type":"x"}\n',encoding="utf8")
    assert t.read_events(p)==[{"event_type":"x"}]


def test_job_context(monkeypatch,tmp_path):
    enable(monkeypatch);t.set_job_context("j");assert t.emit("ANALYSIS_STARTED",path=tmp_path/"e")["job_id"]=="j"


def test_event_hash_stable_shape(monkeypatch,tmp_path):
    enable(monkeypatch);e=t.emit("WORKER_RELEASED",path=tmp_path/"e");assert len(e["event_sha256"])==64


def test_unknown_event_rejected(monkeypatch,tmp_path):
    enable(monkeypatch);assert t.emit("UNKNOWN",path=tmp_path/"e") is None


def test_tw539_not_supported_by_module_api():
    assert "TW539" not in " ".join(sorted(t.EVENT_TYPES))


def test_ranking_incomplete_detected():
    a=analysis();a["ranking"]=a["ranking"][:-1];assert not t.availability(a)["full_ranking_available"]


def test_scores_missing_detected():
    a=analysis();a["ranking"][0].pop("score");assert not t.availability(a)["final_scores_available"]


def test_capture_window_cross_process_unproven():
    events=[{"event_type":"RANKING_FINALIZED","draw_id":"1","pid":1,"process_start_time":"a","monotonic_timestamp":1},{"event_type":"ACTUAL_FIRST_OBSERVED","draw_id":"1","pid":2,"process_start_time":"b","monotonic_timestamp":2}]
    assert t.capture_windows(events)[0]["ordering"]=="UNPROVEN"


def test_restart_context_clear():
    t.set_job_context("x");t.clear_job_context();assert getattr(t._LOCAL,"job_id",None) is None
