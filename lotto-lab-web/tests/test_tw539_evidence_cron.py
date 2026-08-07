import json

import tw539_evidence_cron as cron


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self): return json.dumps(self.payload).encode()


def test_fixed_empty_post_and_redacted_result(monkeypatch):
    monkeypatch.setenv("TW539_EVIDENCE_TRIGGER_URL", "https://staging.example/internal")
    monkeypatch.setenv("EVIDENCE_TRIGGER_SECRET", "secret-value")
    captured = {}
    def open_request(request, timeout):
        captured["request"] = request; captured["timeout"] = timeout
        return Response({"status":"SAFE_NOOP", "invocation_id":"id", "records_added":0, "records_skipped":0})
    monkeypatch.setattr(cron.urllib.request, "urlopen", open_request)
    result = cron.invoke()
    assert captured["request"].method == "POST"
    assert captured["request"].data == b"{}"
    assert result["status"] == "SAFE_NOOP"
    assert "secret-value" not in str(result)


def test_configuration_and_network_failures_are_safe(monkeypatch):
    monkeypatch.delenv("TW539_EVIDENCE_TRIGGER_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_TRIGGER_SECRET", raising=False)
    assert cron.invoke()["error_category"] == "configuration"
    monkeypatch.setenv("TW539_EVIDENCE_TRIGGER_URL", "https://staging.example/internal")
    monkeypatch.setenv("EVIDENCE_TRIGGER_SECRET", "secret-value")
    monkeypatch.setattr(cron.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("secret-value")))
    assert cron.invoke() == {"status":"RETRYABLE_FAILURE", "error_category":"network_or_response"}
