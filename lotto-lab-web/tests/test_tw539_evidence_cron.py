from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import tw539_evidence_cron as cron


def configure(monkeypatch, *, runtime_env="staging", host="staging.example.test"):
    monkeypatch.setenv("EVIDENCE_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("EVIDENCE_RUNTIME_ENV", runtime_env)
    monkeypatch.setenv("EVIDENCE_ALLOWED_TRIGGER_HOST", host)
    monkeypatch.setenv("TW539_EVIDENCE_TRIGGER_URL", f"https://{host}/api/internal/tw539-evidence-cycle")
    monkeypatch.setenv("EVIDENCE_TRIGGER_SECRET", "secret-value")


def test_scheduler_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EVIDENCE_SCHEDULER_ENABLED", raising=False)
    assert cron.invoke() == {"status": "PERMANENT_FAILURE", "error_category": "configuration"}


def test_staging_host_must_match_allowlist(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("TW539_EVIDENCE_TRIGGER_URL", "https://production.example.test/api/internal/tw539-evidence-cycle")
    assert cron.invoke()["error_category"] == "configuration"


def test_production_requires_independent_arm(monkeypatch):
    configure(monkeypatch, runtime_env="production", host="production.example.test")
    monkeypatch.delenv("EVIDENCE_PRODUCTION_ARMED", raising=False)
    assert cron.invoke()["error_category"] == "configuration"


def test_success(monkeypatch):
    configure(monkeypatch)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"status":"SAFE_NOOP","records_added":0,"records_skipped":1}'

    monkeypatch.setattr(cron.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    result = cron.invoke()
    assert result["status"] == "SAFE_NOOP"
    assert result["records_skipped"] == 1


def test_http_errors_are_classified(monkeypatch):
    configure(monkeypatch)

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://staging.example.test", 403, "forbidden", {}, io.BytesIO())

    monkeypatch.setattr(cron.urllib.request, "urlopen", fail)
    result = cron.invoke()
    assert result["status"] == "PERMANENT_FAILURE"
    assert result["error_category"] == "authentication"


def test_runtime_dockerfile_packages_all_evidence_modules():
    dockerfile = Path(cron.__file__).with_name("Dockerfile").read_text(encoding="utf-8")
    for name in (
        "tw539_evidence_runtime.py",
        "tw539_evidence_provenance.py",
        "tw539_evidence_trigger.py",
        "tw539_evidence_cron.py",
    ):
        assert name in dockerfile
