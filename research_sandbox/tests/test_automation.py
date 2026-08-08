import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import automation
import inbox_adapter as ia


def source(context="TW539", digest="a"*64, sample=730):
    event_type="VALID_LIVE_EVIDENCE" if context=="TW539" else "NATURAL_FORWARD_SNAPSHOT"
    return {"lottery_context":context,"event_type":event_type,"source_id":"s","source_version":"v1","source_hash":digest,"computed_source_hash":digest,"source_quality":"OOS_RESEARCH" if context=="TW539" else "FORWARD_VERIFIED","evidence_grade":"E2","created_at":"2026-08-08T00:00:00Z","provenance":"valid","timing_valid":True,"materiality_inputs":{"sample_size":sample,"forward_verified_count":sample if context=="FANTASY5" else 0},"affected_knowledge_ids":[]}


@pytest.fixture
def setup(tmp_path):
    adapter=ia.ResearchEvidenceEventAdapter(tmp_path/"inbox.json")
    args={"adapter":adapter,"state_path":tmp_path/"state.json","wake_lock_path":tmp_path/"wake.lock","prior_by_context":{"TW539":{"s":{"sample_size":700,"quality":"OOS_RESEARCH"}},"FANTASY5":{}},"knowledge_by_context":{"TW539":[],"FANTASY5":[]},"source_hash_resolver":lambda e:e["source_hash"],"sandbox_executor":lambda c,r,k:{"status":"completed","experiments":1,"knowledge_key":k},"enabled":True,"kill_switch":False}
    return adapter,args,tmp_path


def test_sleep_no_events(setup):
    _,args,_=setup
    r=automation.process_research_inbox_once(**args)
    assert r["status"]=="PROCESSED_RETURNED_TO_SLEEP" and r["experiments_started"]==0


def test_default_disabled(setup,monkeypatch):
    _,args,_=setup; args.pop("enabled"); monkeypatch.delenv("RESEARCH_BRAIN_ENABLED",raising=False)
    assert automation.process_research_inbox_once(**args)["status"]=="SAFE_NOOP_SLEEPING"


def test_killed_preserves_new(setup):
    a,args,_=setup; a.adapt(source()); args["kill_switch"]=True
    assert automation.process_research_inbox_once(**args)["status"]=="SAFE_NOOP_KILLED"
    assert a.current_status(json.loads(a.inbox_path.read_text()),json.loads(a.inbox_path.read_text())["events"][0]["event_id"])=="NEW"


def test_lock_busy_safe_noop(setup):
    _,args,_=setup
    lock=automation.ResearchWakeLock(args["wake_lock_path"]); assert lock.try_acquire()
    try: assert automation.process_research_inbox_once(**args)["status"]=="SAFE_NOOP_LOCKED"
    finally: lock.release()


def test_stale_lock_recovered(setup):
    _,args,_=setup; args["wake_lock_path"].write_text(json.dumps({"pid":999,"created_epoch":0}))
    assert automation.process_research_inbox_once(**args)["status"]=="PROCESSED_RETURNED_TO_SLEEP"


def test_material_event_runs_one_experiment(setup):
    a,args,_=setup; a.adapt(source())
    r=automation.process_research_inbox_once(**args)
    assert r["rq_opened"]==1 and r["experiments_started"]==1 and r["returned_to_sleep"]


def test_low_material_no_experiment(setup):
    a,args,_=setup; a.adapt(source(sample=710))
    r=automation.process_research_inbox_once(**args)
    assert r["experiments_started"]==0


def test_source_hash_failure_no_experiment(setup):
    a,args,_=setup; a.adapt(source()); args["source_hash_resolver"]=lambda e:"bad"
    r=automation.process_research_inbox_once(**args)
    assert r["experiments_started"]==0 and r["failures"][0]["error"]=="EVENT_SOURCE_INTEGRITY_FAILURE"


def test_batch_global_max_one_rq(setup):
    a,args,_=setup; a.adapt(source("TW539","a"*64)); a.adapt(source("FANTASY5","b"*64,30))
    r=automation.process_research_inbox_once(**args)
    assert r["rq_opened"]<=1 and r["experiments_started"]<=1


def test_other_context_remains_new_when_one_opens(setup):
    a,args,_=setup; a.adapt(source("TW539","a"*64)); a.adapt(source("FANTASY5","b"*64,30))
    automation.process_research_inbox_once(**args)
    j=json.loads(a.inbox_path.read_text()); f=next(x for x in j["events"] if x["lottery_context"]=="FANTASY5")
    assert a.current_status(j,f["event_id"])=="NEW"


def test_experiment_idempotency(setup):
    a,args,_=setup; a.adapt(source())
    first=automation.process_research_inbox_once(**args)
    second=automation.process_research_inbox_once(**args)
    assert first["experiments_started"]==1 and second["experiments_started"]==0


def test_knowledge_idempotency(setup):
    a,args,_=setup; a.adapt(source()); automation.process_research_inbox_once(**args)
    state=json.loads(args["state_path"].read_text())
    assert len(state["knowledge_keys"])==1


def test_daily_global_budget(setup):
    a,args,_=setup
    state={"version":"v","runs":[],"daily_budget":{automation._day():{"global":3,"TW539":0,"FANTASY5":0}},"knowledge_keys":[],"experiment_keys":[],"retry_counts":{}}
    args["state_path"].write_text(json.dumps(state)); a.adapt(source())
    assert automation.process_research_inbox_once(**args)["experiments_started"]==0


def test_daily_context_budget(setup):
    a,args,_=setup
    state={"version":"v","runs":[],"daily_budget":{automation._day():{"global":2,"TW539":2,"FANTASY5":0}},"knowledge_keys":[],"experiment_keys":[],"retry_counts":{}}
    args["state_path"].write_text(json.dumps(state)); a.adapt(source())
    assert automation.process_research_inbox_once(**args)["experiments_started"]==0


def test_failure_after_read_keeps_new(setup):
    a,args,_=setup; a.adapt(source()); args["inject_failure"]="after_read"
    assert automation.process_research_inbox_once(**args)["status"]=="BRAIN_AUTOMATION_FAILURE"
    j=json.loads(a.inbox_path.read_text()); assert a.current_status(j,j["events"][0]["event_id"])=="NEW"


def test_executor_retry_succeeds(setup):
    a,args,_=setup; a.adapt(source()); calls={"n":0}
    def flaky(c,r,k):
        calls["n"]+=1
        if calls["n"]<3: raise RuntimeError()
        return {"experiments":1,"knowledge_key":k}
    args["sandbox_executor"]=flaky
    r=automation.process_research_inbox_once(**args)
    assert calls["n"]==3 and r["experiments_started"]==1 and r["retries"]==2


def test_retry_exhaustion_no_duplicate_experiment(setup):
    a,args,_=setup; a.adapt(source()); args["inject_failure"]="executor"
    r=automation.process_research_inbox_once(**args)
    assert r["experiments_started"]==0
    state=json.loads(args["state_path"].read_text()); assert state["experiment_keys"]==[]


def test_prediction_marker_unchanged_on_failure(setup):
    a,args,tmp=setup; marker=tmp/"prediction"; marker.write_text("same"); a.adapt(source()); args["inject_failure"]="after_read"
    automation.process_research_inbox_once(**args)
    assert marker.read_text()=="same"


def test_lock_released_after_failure(setup):
    a,args,_=setup; a.adapt(source()); args["inject_failure"]="after_read"; automation.process_research_inbox_once(**args)
    lock=automation.ResearchWakeLock(args["wake_lock_path"]); assert lock.try_acquire(); lock.release()


def test_state_audit_written(setup):
    a,args,_=setup; a.adapt(source()); automation.process_research_inbox_once(**args)
    assert json.loads(args["state_path"].read_text())["runs"][0]["returned_to_sleep"]


def test_no_polling_loop_api():
    assert hasattr(automation,"process_research_inbox_once") and not hasattr(automation,"run_forever")
