"""Fail-closed scheduler client for the TW539 Evidence trigger."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def invoke() -> dict[str, object]:
    url = os.environ.get("TW539_EVIDENCE_TRIGGER_URL", "")
    secret = os.environ.get("EVIDENCE_TRIGGER_SECRET", "")
    runtime_env = os.environ.get("EVIDENCE_RUNTIME_ENV", "").strip().lower()
    allowed_host = os.environ.get("EVIDENCE_ALLOWED_TRIGGER_HOST", "").strip().lower()
    parsed = urlparse(url)
    configured = (
        _enabled("EVIDENCE_SCHEDULER_ENABLED")
        and runtime_env in {"staging", "production"}
        and bool(secret)
        and parsed.scheme == "https"
        and parsed.hostname == allowed_host
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and parsed.path == "/api/internal/tw539-evidence-cycle"
    )
    if runtime_env == "production" and not _enabled("EVIDENCE_PRODUCTION_ARMED"):
        configured = False
    if not configured:
        return {"status": "PERMANENT_FAILURE", "error_category": "configuration"}
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Evidence-Trigger-Secret": secret,
            "User-Agent": "TW539-Evidence-Cron/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            category = "authentication"
            status = "PERMANENT_FAILURE"
        elif exc.code == 404:
            category = "endpoint"
            status = "PERMANENT_FAILURE"
        elif exc.code == 429:
            category = "rate_limit"
            status = "RETRYABLE_FAILURE"
        elif 500 <= exc.code < 600:
            category = "upstream"
            status = "RETRYABLE_FAILURE"
        else:
            category = "http_response"
            status = "PERMANENT_FAILURE"
        return {"status": status, "error_category": category, "http_status": exc.code}
    except (OSError, urllib.error.URLError, ValueError, TypeError):
        return {"status": "RETRYABLE_FAILURE", "error_category": "network_or_response"}
    if not isinstance(payload, dict):
        return {"status": "RETRYABLE_FAILURE", "error_category": "network_or_response"}
    return {
        "status": payload.get("status", "PERMANENT_FAILURE"),
        "invocation_id": payload.get("invocation_id"),
        "records_added": int(payload.get("records_added", 0) or 0),
        "records_skipped": int(payload.get("records_skipped", 0) or 0),
        "error_category": payload.get("error_category"),
    }


def main() -> int:
    result = invoke()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {"SUCCESS", "SAFE_NOOP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
