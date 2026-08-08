import pytest

import observation


KNOWLEDGE = [{"knowledge_id":"K-TW539-0001","rq_id":"RQ-TW539-EDGE-0001","result":"REJECTED"}]
PRIOR = {"source_id":"s","context":"TW539","quality":"OOS_RESEARCH","sample_size":700,"provenance":"strict OOS"}


def run(inbox, **kw):
    return observation.observe_inbox("TW539", inbox, seen_hashes=kw.get("seen", set()), prior_by_source={"s":PRIOR}, knowledge=KNOWLEDGE, enabled=True, kill_switch=False)


def test_default_sleeping(monkeypatch):
    monkeypatch.delenv("RESEARCH_BRAIN_ENABLED", raising=False)
    assert observation.observe_inbox("TW539", [], seen_hashes=set(), prior_by_source={}, knowledge=[])["state"] == "SLEEPING"


def test_no_evidence_no_rq():
    r=run([]); assert r["opened_rqs"] == [] and r["experiments_started"] == 0


def test_duplicate_ignored():
    e={**PRIOR}; h=observation.fingerprint(e)
    assert run([e],seen={h})["evaluated"][0]["decision"] == "DUPLICATE_IGNORED"


def test_low_materiality_observe_only():
    e={**PRIOR,"sample_size":710}
    r=run([e]); assert not r["material_evidence_found"] and not r["opened_rqs"]


def test_material_sample_opens_one_rq():
    e1={**PRIOR,"sample_size":730}
    e2={**PRIOR,"source_id":"s2","sample_size":900}
    r=run([e1,e2]); assert r["material_evidence_found"] and len(r["opened_rqs"]) == 1


def test_quality_upgrade_material():
    e={**PRIOR,"quality":"FORWARD_VERIFIED"}
    assert run([e])["material_evidence_found"]


def test_knowledge_blocks_same_research_without_new_evidence():
    e={**PRIOR,"rq_id":"RQ-TW539-EDGE-0001","sample_size":701}
    assert run([e])["evaluated"][0]["decision"] == "KNOWLEDGE_DO_NOT_REPEAT"


def test_knowledge_is_read_first():
    assert run([])["knowledge_read"] == ["K-TW539-0001"]


def test_fantasy_zero_forward_blocked():
    e={"source_id":"f","context":"FANTASY5","quality":"PROVISIONAL","sample_size":370,"forward_verified_count":0,"provenance":"canonical"}
    r=observation.observe_inbox("FANTASY5",[e],seen_hashes=set(),prior_by_source={},knowledge=[],enabled=True,kill_switch=False)
    assert r["evaluated"][0]["decision"] == "DATA_QUALITY_BLOCKED" and not r["opened_rqs"]


def test_one_context_only():
    e={"source_id":"f","context":"FANTASY5","quality":"FORWARD_VERIFIED","sample_size":30,"forward_verified_count":30,"provenance":"live"}
    assert run([e])["evaluated"] == []


def test_max_one_rq_per_wake():
    es=[{**PRIOR,"source_id":f"s{i}","sample_size":800+i} for i in range(4)]
    assert len(run(es)["opened_rqs"]) == 1


def test_rq_max_three_experiments():
    e={**PRIOR,"sample_size":730}
    assert run([e])["opened_rqs"][0]["max_experiments"] == 3


def test_returns_to_sleep():
    e={**PRIOR,"sample_size":730}
    r=run([e]); assert r["state"]=="SLEEPING" and r["returned_to_sleep"]


def test_kill_switch():
    r=observation.observe_inbox("TW539",[{**PRIOR,"sample_size":730}],seen_hashes=set(),prior_by_source={},knowledge=[],enabled=True,kill_switch=True)
    assert r["reason"] == "KILL_SWITCH_ACTIVE" and not r["opened_rqs"]


def test_permissions_all_denied():
    assert set(observation.assert_permissions().values()) == {"PERMISSION_DENIED"}


def test_independent_holdout_material():
    e={**PRIOR,"independent_holdout":True}
    assert run([e])["material_evidence_found"]


def test_missing_provenance_not_material():
    e={**PRIOR,"provenance":"missing","sample_size":1000}
    assert run([e])["evaluated"][0]["decision"] == "MISSING_PROVENANCE"


def test_fingerprint_ignores_receive_time():
    a={**PRIOR,"received_at":"a"}; b={**PRIOR,"received_at":"b"}
    assert observation.fingerprint(a)==observation.fingerprint(b)
