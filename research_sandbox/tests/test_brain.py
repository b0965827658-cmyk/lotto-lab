import json
from pathlib import Path

import pytest

import brain


@pytest.fixture
def interface():
    root = Path(__file__).resolve().parents[2]
    return brain.DataInterface(root, root / "research_sandbox" / "approved_inputs.json")


def test_approved_input_only(interface):
    with pytest.raises(brain.PermissionDenied): interface.read("UNKNOWN", "TW539")


def test_context_isolation(interface):
    with pytest.raises(brain.PermissionDenied): interface.read("F5-REGIME-RESULT", "TW539")


def test_autonomous_observation(interface):
    assert len(brain.observe_tw539(interface)) >= 2


def test_autonomous_rq(interface):
    queue = brain.questions_from_observations(brain.observe_tw539(interface))
    assert queue[0]["rq_id"] == "RQ-TW539-EDGE-0001"


def test_hypothesis_space():
    hs = brain.hypotheses_for("RQ-TW539-EDGE-0001")
    assert [x["id"] for x in hs] == ["H0", "H1", "H2"]


def test_protocol_freezes():
    p = brain.freeze_protocol({"x": 1})
    assert p["frozen"] and brain.protocol_valid(p)


def test_protocol_mutation_invalidates():
    p = brain.freeze_protocol({"x": 1}); p["x"] = 2
    assert not brain.protocol_valid(p)


def test_holdout_hidden_until_frozen():
    v = brain.HoldoutVault(); v.register("h", [1], 1)
    with pytest.raises(brain.PermissionDenied): v.unlock("h", "e", False)


def test_holdout_usage_and_contamination():
    v = brain.HoldoutVault(limit=1); v.register("h", [1], 1)
    assert not v.unlock("h", "e1", True)["contamination_risk"]
    assert v.unlock("h", "e2", True)["contamination_risk"]


def test_random_control_fixed(interface):
    p = brain.freeze_protocol({"experiment_id":"e","random_control":{"seed":20260808},"statistical_tests":{"bootstrap_iterations":50},"success_criteria":{"minimum_mean_delta":0.05}})
    a = brain.execute_tw539_distribution_experiment(interface, p)
    b = brain.execute_tw539_distribution_experiment(interface, p)
    assert a == b


def test_falsification_present(interface, tmp_path):
    result = brain.run_brain("TW539", interface, tmp_path, enabled=True)
    assert "falsification_test" in result["experiments"][0]


def test_rejected_writes_knowledge(interface, tmp_path):
    result = brain.run_brain("TW539", interface, tmp_path, enabled=True)
    assert result["knowledge_writes"][0]["result"] == "REJECTED"


def test_duplicate_guard():
    assert brain.similar_rejected({"rq_id":"q"}, [{"rq_id":"q","result":"REJECTED"}], [])
    assert not brain.similar_rejected({"rq_id":"q"}, [{"rq_id":"q","result":"REJECTED"}], ["new"])


def test_max_three_experiments():
    g = brain.ResourceGovernor(brain.Budget())
    with pytest.raises(RuntimeError, match="RESOURCE_BUDGET_EXCEEDED"): g.check(4)


def test_brain_can_stop(interface, tmp_path):
    assert brain.run_brain("TW539", interface, tmp_path, enabled=True)["next_decision"] == "STOP"


def test_no_edge_legal(interface, tmp_path):
    r = brain.run_brain("TW539", interface, tmp_path, enabled=True)
    assert r["experiments"][0]["edge_status"] == "NO_EDGE_FOUND"


def test_data_quality_blocked_legal(interface, tmp_path):
    r = brain.run_brain("FANTASY5", interface, tmp_path, enabled=True)
    assert r["status"] == "DATA_QUALITY_BLOCKED" and r["experiments"] == 0


@pytest.mark.parametrize("action", sorted(brain.DENIED_ACTIONS))
def test_permission_denials(action):
    guard = brain.SecurityBoundary()
    with pytest.raises(brain.PermissionDenied): guard.require(action)
    assert guard.events[-1]["status"] == "PERMISSION_DENIED"


def test_kill_switch(interface, tmp_path):
    assert brain.run_brain("TW539", interface, tmp_path, enabled=True, kill_switch=True)["status"] == "KILL_SWITCH_ACTIVE"


def test_default_disabled(interface, tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_BRAIN_ENABLED", raising=False)
    assert brain.run_brain("TW539", interface, tmp_path)["status"] == "DISABLED"


def test_crash_does_not_touch_formal_runtime(interface, tmp_path):
    marker = tmp_path / "formal"; marker.write_text("same")
    with pytest.raises(ValueError): brain.run_brain("BAD", interface, tmp_path / "output", enabled=True)
    assert marker.read_text() == "same"


def test_context_output_separate(interface, tmp_path):
    brain.run_brain("TW539", interface, tmp_path, enabled=True)
    brain.run_brain("FANTASY5", interface, tmp_path, enabled=True)
    assert (tmp_path/"tw539_run.json").exists() and (tmp_path/"fantasy5_run.json").exists()


def test_audit_trail_complete(interface, tmp_path):
    r = brain.run_brain("TW539", interface, tmp_path, enabled=True)
    for key in ["brain_run_id","lottery_context","started_at","completed_at","inputs","observations","selected_rq","hypotheses","protocol","experiments","knowledge_writes","next_decision","resource_usage","security_events"]:
        assert key in r


def test_deterministic_experiment_replay(interface, tmp_path):
    a = brain.run_brain("TW539", interface, tmp_path/"a", enabled=True)
    b = brain.run_brain("TW539", interface, tmp_path/"b", enabled=True)
    assert a["experiments"] == b["experiments"] and a["protocol"] == b["protocol"]


def test_output_is_only_requested_root(interface, tmp_path):
    out = tmp_path / "research_sandbox" / "output"
    brain.run_brain("TW539", interface, out, enabled=True)
    assert [p.name for p in out.iterdir()] == ["tw539_run.json"]


def test_protocol_mutation_result(interface):
    p = brain.freeze_protocol({"experiment_id":"e","random_control":{"seed":1},"statistical_tests":{"bootstrap_iterations":10},"success_criteria":{"minimum_mean_delta":0}})
    p["experiment_id"] = "changed"
    assert brain.execute_tw539_distribution_experiment(interface, p)["status"] == "INVALID_PROTOCOL_MUTATION"
