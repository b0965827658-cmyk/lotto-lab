import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import inbox_adapter as ia


def source(context="TW539", event_type="VALID_LIVE_EVIDENCE", digest="a"*64, **inputs):
    return {
        "lottery_context":context, "event_type":event_type, "source_id":"src", "source_version":"v1",
        "source_hash":digest, "computed_source_hash":digest, "source_quality":"OOS_RESEARCH",
        "evidence_grade":"E2", "created_at":"2026-08-08T00:00:00Z", "provenance":"valid_live",
        "timing_valid":True, "materiality_inputs":{"sample_size":730, **inputs},
        "affected_knowledge_ids":["K-TW539-0001"],
    }


@pytest.fixture
def adapter(tmp_path):
    return ia.ResearchEvidenceEventAdapter(tmp_path/"inbox.json")


def read(adapter):
    return json.loads(adapter.inbox_path.read_text())


def test_tw539_evidence_to_event(adapter):
    assert adapter.adapt(source())["status"]=="ENQUEUED"


def test_fantasy_snapshot_to_event(adapter):
    s=source("FANTASY5","NATURAL_FORWARD_SNAPSHOT",quality="FORWARD_VERIFIED",forward_verified_count=1)
    assert adapter.adapt(s)["status"]=="ENQUEUED"


def test_fantasy_settlement_to_event(adapter):
    assert adapter.adapt(source("FANTASY5","FORWARD_SETTLEMENT"))["status"]=="ENQUEUED"


def test_telemetry_milestone_to_event(adapter):
    assert adapter.adapt(source(event_type="TELEMETRY_MILESTONE",milestone=5))["status"]=="ENQUEUED"


def test_duplicate_ten_times_one_record(adapter):
    s=source()
    for _ in range(10): adapter.adapt(s)
    assert len(read(adapter)["events"])==1


def test_source_hash_rejected(adapter):
    s=source(); s["computed_source_hash"]="b"*64
    assert adapter.adapt(s)["reason"]=="EVENT_SOURCE_INTEGRITY_FAILURE"


def test_source_hash_change_after_event(adapter):
    adapter.adapt(source())
    event=read(adapter)["events"][0]
    assert adapter.verify_source(event,"b"*64)["status"]=="EVENT_SOURCE_INTEGRITY_FAILURE"


def test_context_mismatch_rejected(adapter):
    assert adapter.adapt(source("TW539","NATURAL_FORWARD_SNAPSHOT"))["reason"]=="REJECTED_CONTEXT_MISMATCH"


def test_invalid_provenance_rejected(adapter):
    s=source(); s["provenance"]="unverified"
    assert adapter.adapt(s)["reason"]=="INVALID_PROVENANCE"


def test_milestone_only_once(adapter):
    adapter.adapt(source(event_type="TELEMETRY_MILESTONE",digest="a"*64,milestone=5))
    r=adapter.adapt(source(event_type="TELEMETRY_MILESTONE",digest="b"*64,milestone=5))
    assert r["status"]=="MILESTONE_ALREADY_EMITTED"


def test_materiality_by_brain(adapter):
    adapter.adapt(source(sample_size=710))
    r=adapter.process_new("TW539",prior_by_source={"src":{"sample_size":700,"quality":"OOS_RESEARCH"}},knowledge=[])
    assert r["rq_opened"]==0 and read(adapter)["transitions"][0]["decision"]=="OBSERVE_ONLY"


def test_duplicate_knowledge_no_research(adapter):
    s=source(sample_size=701); s["materiality_inputs"]["rq_id"]="RQ-TW539-EDGE-0001"; adapter.adapt(s)
    k=[{"knowledge_id":"K-TW539-0001","rq_id":"RQ-TW539-EDGE-0001","result":"REJECTED"}]
    r=adapter.process_new("TW539",prior_by_source={"src":{"sample_size":700,"quality":"OOS_RESEARCH"}},knowledge=k)
    assert r["rq_opened"]==0 and read(adapter)["transitions"][0]["to"]=="IGNORED_NO_MATERIAL_CHANGE"


def test_material_opens_one_rq(adapter):
    adapter.adapt(source(sample_size=730))
    r=adapter.process_new("TW539",prior_by_source={"src":{"sample_size":700,"quality":"OOS_RESEARCH"}},knowledge=[])
    assert r["rq_opened"]==1


def test_event_batching_one_rq(adapter):
    for i in range(3): adapter.adapt(source(digest=str(i)*64,source_id=f"s{i}",sample_size=800+i))
    r=adapter.process_new("TW539",prior_by_source={},knowledge=[])
    assert r["rq_opened"]==1


def test_concurrent_dedup(adapter):
    with ThreadPoolExecutor(max_workers=5) as pool: list(pool.map(lambda _:adapter.adapt(source()),range(10)))
    assert len(read(adapter)["events"])==1


def test_crash_before_replace_keeps_old(adapter):
    adapter.adapt(source())
    before=adapter.inbox_path.read_bytes()
    with pytest.raises(RuntimeError): adapter.adapt(source(digest="b"*64),fail_before_replace=True)
    assert adapter.inbox_path.read_bytes()==before


def test_inbox_written_brain_not_read_keeps_new(adapter):
    adapter.adapt(source())
    assert adapter.current_status(read(adapter),read(adapter)["events"][0]["event_id"])=="NEW"


def test_brain_decision_transition_prevents_replay(adapter):
    adapter.adapt(source(sample_size=730))
    adapter.process_new("TW539",prior_by_source={},knowledge=[])
    second=adapter.process_new("TW539",prior_by_source={},knowledge=[])
    assert second["events_processed"]==0


def test_consumed_not_replay(adapter):
    adapter.adapt(source()); eid=read(adapter)["events"][0]["event_id"]
    adapter.transition(eid,"CONSUMED","RESEARCH_COMPLETE")
    assert adapter.process_new("TW539",prior_by_source={},knowledge=[])["events_processed"]==0


def test_kill_switch_preserves_new(adapter):
    adapter.adapt(source())
    r=adapter.process_new("TW539",prior_by_source={},knowledge=[],kill_switch=True)
    assert r["events_preserved_new"]==1 and adapter.current_status(read(adapter),read(adapter)["events"][0]["event_id"])=="NEW"


@pytest.mark.parametrize("key,value",[
    ("path","/tmp/x"),("command","echo x"),("python_code","print(1)"),("url","https://x"),("git_command","git push"),("deploy_instruction","deploy now")
])
def test_payload_cannot_execute(adapter,key,value):
    s=source(); s["materiality_inputs"][key]=value
    assert adapter.adapt(s)["reason"]=="UNSAFE_PAYLOAD"


def test_invalid_timing_rejected(adapter):
    s=source(); s["timing_valid"]=False
    assert adapter.adapt(s)["reason"]=="INVALID_TIMING"


def test_invalid_milestone_rejected(adapter):
    assert adapter.adapt(source(event_type="TELEMETRY_MILESTONE",milestone=6))["reason"]=="INVALID_MILESTONE"


def test_corruption_isolated(adapter):
    adapter.inbox_path.write_text("{bad")
    with pytest.raises(RuntimeError,match="INBOX_CORRUPTION_DETECTED"): adapter.adapt(source())
    assert list(adapter.inbox_path.parent.glob("inbox.json.corrupt.*"))


def test_adapter_does_not_copy_evidence(adapter):
    s=source(); s["secret_evidence_body"]={"numbers":[1,2,3]}
    adapter.adapt(s); event=read(adapter)["events"][0]
    assert "secret_evidence_body" not in event and set(event["payload_reference"])=={"source_id","source_hash"}


def test_prediction_decoupled(adapter,tmp_path):
    marker=tmp_path/"prediction"; marker.write_text("same")
    adapter.adapt(source())
    assert marker.read_text()=="same"


def test_event_sha_valid(adapter):
    adapter.adapt(source()); event=read(adapter)["events"][0]
    digest=event.pop("event_sha256")
    assert digest==ia._sha(event)


@pytest.mark.parametrize("context,event_type",[
    ("TW539","VALID_LIVE_EVIDENCE"),
    ("TW539","TELEMETRY_MILESTONE"),
    ("FANTASY5","NATURAL_FORWARD_SNAPSHOT"),
    ("FANTASY5","FORWARD_SETTLEMENT"),
])
def test_trusted_file_producer_auto_conversion(adapter,tmp_path,context,event_type):
    p=tmp_path/"source.json"; p.write_text('{"records":[]}')
    milestone={"milestone":5} if event_type=="TELEMETRY_MILESTONE" else {}
    s=ia.detect_file_event(p,source_id="source",source_version="v1",lottery_context=context,event_type=event_type,source_quality="FORWARD_VERIFIED",evidence_grade="E2",provenance="approved_writer",timing_valid=True,materiality_inputs=milestone)
    assert adapter.adapt(s)["status"]=="ENQUEUED"
