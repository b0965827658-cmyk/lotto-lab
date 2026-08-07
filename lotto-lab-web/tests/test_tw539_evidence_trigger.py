from __future__ import annotations

import threading

import tw539_evidence_trigger as trigger


def test_authentication_and_empty_payload_only(monkeypatch):
    monkeypatch.setenv(trigger.TRIGGER_SECRET_ENV, "secret-value")
    code, response = trigger.invoke_current_evidence_cycle(supplied_secret="wrong", payload={}, runner=lambda: {"status":"SAFE_NOOP"}, audit_writer=lambda _: None)
    assert code == 403 and response["error_category"] == "authentication"
    code, response = trigger.invoke_current_evidence_cycle(supplied_secret="secret-value", payload={"draw_id":"x"}, runner=lambda: {"status":"SAFE_NOOP"}, audit_writer=lambda _: None)
    assert code == 400 and response["error_category"] == "payload_forbidden"


def test_safe_noop_and_redacted_response(monkeypatch):
    monkeypatch.setenv(trigger.TRIGGER_SECRET_ENV, "secret-value")
    audits = []
    code, response = trigger.invoke_current_evidence_cycle(
        supplied_secret="secret-value", payload={}, runner=lambda: {"status":"SAFE_NOOP", "records_added":0}, audit_writer=audits.append
    )
    assert code == 200 and response["status"] == "SAFE_NOOP"
    assert "secret" not in str(response).lower()
    assert audits[0]["records_added"] == 0


def test_current_only_success(monkeypatch):
    monkeypatch.setenv(trigger.TRIGGER_SECRET_ENV, "secret-value")
    code, response = trigger.invoke_current_evidence_cycle(
        supplied_secret="secret-value", payload=None,
        runner=lambda: {"status":"SUCCESS", "draw_id":"115000200", "records_added":1, "record_count":1},
        audit_writer=lambda _: None,
    )
    assert code == 200 and response["records_added"] == 1


def test_reentry_is_safe_noop(monkeypatch):
    monkeypatch.setenv(trigger.TRIGGER_SECRET_ENV, "secret-value")
    ready = threading.Event()
    release = threading.Event()
    first = {}
    def slow():
        ready.set(); release.wait(2); return {"status":"SAFE_NOOP", "records_added":0}
    thread = threading.Thread(target=lambda: first.update(result=trigger.invoke_current_evidence_cycle(supplied_secret="secret-value", payload={}, runner=slow, audit_writer=lambda _: None)))
    thread.start(); ready.wait(2)
    code, response = trigger.invoke_current_evidence_cycle(supplied_secret="secret-value", payload={}, runner=lambda: {"status":"SUCCESS"}, audit_writer=lambda _: None)
    release.set(); thread.join(2)
    assert code == 202 and response["error_category"] == "lock_contention"


def test_runtime_and_disk_failures_are_redacted(monkeypatch):
    monkeypatch.setenv(trigger.TRIGGER_SECRET_ENV, "secret-value")
    def fail(): raise RuntimeError("sensitive")
    code, response = trigger.invoke_current_evidence_cycle(supplied_secret="secret-value", payload={}, runner=fail, audit_writer=lambda _: None)
    assert code == 500 and response["error_category"] == "runtime_exception" and "sensitive" not in str(response)
    code, response = trigger.invoke_current_evidence_cycle(supplied_secret="secret-value", payload={}, runner=lambda: {"status":"SAFE_NOOP"}, audit_writer=lambda _: (_ for _ in ()).throw(OSError("disk")))
    assert code == 500 and response["status"] == "RETRYABLE_FAILURE"
