import json
from pathlib import Path

import cloud_service
import oversight


def seed(root: Path):
    for name in ("inbox", "knowledge", "output", "audit"):
        (root / name).mkdir(parents=True)
    (root / "inbox" / "events.json").write_text(json.dumps({"events": [{
        "event_id": "EVT-1", "lottery_context": "FANTASY5", "source_id": "forward-snapshot"
    }]}))
    (root / "knowledge" / "automation_state.json").write_text(json.dumps({
        "runs": [{"run_id": "WAKE-1", "started_at": "2026-08-09T00:00:00+00:00",
                  "completed_at": "2026-08-09T00:00:01+00:00", "returned_to_sleep": True,
                  "rq_opened": 0, "experiments_started": 0, "contexts": {"FANTASY5": {
                      "event_decisions": [{"event_id": "EVT-1", "decision": "LOW_MATERIALITY_OBSERVE_ONLY"}]}}}],
        "daily_budget": {}, "knowledge_keys": [], "experiment_keys": []
    }))


def test_oversight_is_read_only_and_reports_sleeping(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setenv("RESEARCH_BRAIN_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_BRAIN_KILL_SWITCH", "false")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = oversight.snapshot(tmp_path)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert view["brain_status"]["current_state"] == "SLEEPING"
    assert view["wake_timeline"]["wakes"][0]["materiality_decisions"] == ["LOW_MATERIALITY_OBSERVE_ONLY"]
    assert view["human_boundary"]["model_modify"] is False


def test_shadow_requires_e3_and_recommendation(tmp_path):
    seed(tmp_path)
    (tmp_path / "output" / "e2.json").write_text(json.dumps({"rq_id": "RQ-1", "evidence_grade": "E2", "next_decision": "SHADOW_RECOMMENDED"}))
    (tmp_path / "output" / "e3.json").write_text(json.dumps({"rq_id": "RQ-2", "lottery_context": "TW539", "evidence_grade": "E3", "next_decision": "SHADOW_RECOMMENDED"}))
    queue = oversight.shadow_queue(tmp_path)
    assert queue["count"] == 1
    assert queue["recommendations"][0]["deploy_authority"] is False


def test_oversight_endpoint_get_only(tmp_path, monkeypatch):
    seed(tmp_path)
    monkeypatch.setattr(cloud_service, "_root", lambda: tmp_path)
    server = cloud_service.start_health_server(0)
    try:
        import urllib.error, urllib.request
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/oversight") as response:
            assert response.status == 200
            assert json.load(response)["human_boundary"]["knowledge_modify"] is False
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/oversight", method="POST"))
            assert False
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown(); server.server_close()


def test_generated_artifacts_complete(tmp_path):
    root, output = tmp_path / "brain", tmp_path / "oversight"
    seed(root)
    result = oversight.write_artifacts(root, output)
    assert result["files"] == 15
    assert {p.name for p in output.iterdir()} == {
        "oversight_report.md", "brain_status.json", "wake_timeline.json", "research_timeline.json",
        "knowledge_ledger.json", "shadow_recommendation_queue.json", "research_health.json",
        "learning_velocity.json", "lottery_tw539.json", "lottery_fantasy5.json",
        "notification_policy.json", "daily_digest.json", "emergency_kill_contract.json",
        "human_review_contract.json", "oversight_result.json",
    }
