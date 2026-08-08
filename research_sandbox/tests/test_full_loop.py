import json
from pathlib import Path

import pytest

import full_loop as fl
from brain import DataInterface


@pytest.fixture
def setup(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    interface = DataInterface(repo, repo / "research_sandbox" / "approved_inputs.json")
    rq = {"rq_id":"RQ-TW539-OBS-validation","question":"validation question","trigger_evidence_hash":"vhash"}
    return interface, rq, tmp_path / "validation" / "full_loop_gate"


def run(setup, failure=None, key="KEY"):
    interface, rq, root = setup
    return fl.safe_failure_run(opened_rq=rq, interface=interface, gate_root=root, experiment_key=key, failure=failure)


def test_full_loop_complete(setup):
    r=run(setup); assert r["status"]=="COMPLETED_RETURNED_TO_SLEEP"
    assert r["hypotheses"]>=3 and r["falsification"] and r["random_control"] and r["baseline_control"]
    assert r["conclusion"] in fl.ALLOWED_CONCLUSIONS and r["returned_to_sleep"]


def test_protocol_frozen_and_mutation_rejected(setup):
    run(setup); p=json.loads((setup[2]/"output"/"frozen_protocol.json").read_text())
    assert p["frozen"] and p["protocol_sha256"]
    assert run(setup, "protocol", "OTHER")["status"]=="SAFE_STOP"


def test_holdout_once(setup):
    run(setup); h=json.loads((setup[2]/"holdout"/"holdout_validation.json").read_text())
    assert h["locked"] and h["usage_count"]==1 and not h["contamination_risk"]


@pytest.mark.parametrize("failure",["experiment","falsification","knowledge","holdout","protocol","timeout"])
def test_failure_injection_safe_stop(setup,failure):
    r=run(setup,failure,f"F-{failure}"); assert r["status"]=="SAFE_STOP" and r["running_brain_count"]==0 and r["returned_to_sleep"]


def test_crash_after_experiment_resumes_without_duplicate(setup):
    first=run(setup,"after_experiment","CRASH-EXP"); assert first["status"]=="SAFE_STOP"
    second=run(setup,None,"CRASH-EXP"); assert second["status"]=="COMPLETED_RETURNED_TO_SLEEP"
    ledger=json.loads((setup[2]/"audit"/"execution_ledger.json").read_text())
    assert len(ledger["experiments"])==1 and len(ledger["knowledge"])==1 and ledger["holdout_usage"]["VAL-HOLDOUT-0001"]==1


def test_crash_after_knowledge_finalizes_idempotently(setup):
    assert run(setup,"after_knowledge","CRASH-KNOW")["status"]=="SAFE_STOP"
    r=run(setup,None,"CRASH-KNOW"); assert r["status"]=="RESUMED_ALREADY_FINALIZED"
    assert len(list((setup[2]/"knowledge").glob("*.json")))==1


def test_permissions_all_denied():
    p=fl.permission_validation(); assert p["all_denied"] and len(p["results"])==11


def test_validation_root_is_quarantined(tmp_path):
    assert fl.validation_root(tmp_path)==(tmp_path/"validation"/"full_loop_gate").resolve()


def test_deterministic_replay(tmp_path):
    repo=Path(__file__).resolve().parents[2]; interface=DataInterface(repo,repo/"research_sandbox"/"approved_inputs.json")
    rq={"rq_id":"RQ-TW539-OBS-validation","question":"validation question","trigger_evidence_hash":"vhash"}
    a=fl.run_full_loop(opened_rq=rq,interface=interface,gate_root=tmp_path/"a",experiment_key="A")
    b=fl.run_full_loop(opened_rq=rq,interface=interface,gate_root=tmp_path/"b",experiment_key="B")
    for k in ("conclusion","evidence_grade","supporting_test","falsification","random_control","baseline_control","protocol_sha256"):
        assert a[k]==b[k]
