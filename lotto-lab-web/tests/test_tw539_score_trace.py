import json
from pathlib import Path

import server
import tw539_score_trace as trace


def rows():
    path = Path(__file__).parents[1] / "data" / "taiwan_539_history.json"
    return json.loads(path.read_text(encoding="utf-8"))[:1000]


def score_once(monkeypatch, enabled):
    monkeypatch.setenv("TW539_SCORE_TRACE_ENABLED", "true" if enabled else "false")
    trace.clear_latest()
    source = server._mm_rows(rows(), max_number=39, limit=1000)
    token = trace.begin("tw539")
    scores, features, _meta = server._formal_scores("tw539", source, 39)
    weights = server._formal_default_weights("tw539")
    ensemble = {number: sum(weights[name] * scores[name][number] for name in server.FORMAL_MODEL_NAMES["tw539"]) for number in range(1, 40)}
    ranked = sorted(ensemble, key=lambda number: (-ensemble[number], number))
    trace.finalize(weights, ensemble, ranked)
    trace.finish(token)
    return scores, features, ensemble, ranked, trace.latest_trace()


def test_flag_false_is_zero_side_effect(monkeypatch):
    _scores, _features, _ensemble, _ranked, result = score_once(monkeypatch, False)
    assert result is None


def test_on_off_scores_and_ranking_are_identical(monkeypatch):
    off = score_once(monkeypatch, False)
    on = score_once(monkeypatch, True)
    assert off[:4] == on[:4]


def test_all_numbers_features_models_and_transforms_are_captured(monkeypatch):
    _scores, features, _ensemble, ranked, result = score_once(monkeypatch, True)
    assert result is not None
    assert set(result["numbers"]) == {str(i) for i in range(1, 40)}
    assert len(ranked) == 39
    for number in range(1, 40):
        row = result["numbers"][str(number)]
        assert row["normalized_features"] == features[number]
        assert set(row["models"]) == set(server.FORMAL_MODEL_NAMES["tw539"])
        assert row["models"]["tw-logistic"]["transform"]["type"] == "bounded_sigmoid_then_clamp"
        assert all("weighted_terms" in model for model in row["models"].values())
        assert "final_rank" in row and "final_score" in row
        assert row["final_score"] == round(row["ensemble_score_raw"] * 100, 2)
    assert result["score_reconstruction"]["max_error"] == 0.0


def test_trace_data_is_defensively_copied(monkeypatch):
    *_unused, result = score_once(monkeypatch, True)
    changed = trace.latest_trace()
    changed["numbers"]["1"]["final_rank"] = 999
    assert trace.latest_trace()["numbers"]["1"]["final_rank"] == result["numbers"]["1"]["final_rank"]


def test_fantasy5_never_captures(monkeypatch):
    monkeypatch.setenv("TW539_SCORE_TRACE_ENABLED", "true")
    trace.clear_latest()
    token = trace.begin("ca-fantasy5")
    trace.finish(token)
    assert trace.latest_trace() is None


def test_collector_exception_does_not_change_current(monkeypatch):
    baseline = score_once(monkeypatch, False)
    monkeypatch.setenv("TW539_SCORE_TRACE_ENABLED", "true")
    monkeypatch.setattr(trace.copy, "deepcopy", lambda _value: (_ for _ in ()).throw(RuntimeError("trace failure")))
    source = server._mm_rows(rows(), max_number=39, limit=1000)
    token = trace.begin("tw539")
    scores, _features, _meta = server._formal_scores("tw539", source, 39)
    weights = server._formal_default_weights("tw539")
    ensemble = {number: sum(weights[name] * scores[name][number] for name in server.FORMAL_MODEL_NAMES["tw539"]) for number in range(1, 40)}
    ranked = sorted(ensemble, key=lambda number: (-ensemble[number], number))
    trace.finalize(weights, ensemble, ranked)
    trace.finish(token)
    assert scores == baseline[0]
    assert ensemble == baseline[2]
    assert ranked == baseline[3]
