"""Fixed Render Cron client for the Staging TW539 Evidence trigger."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def invoke() -> dict[str, object]:
    url = os.environ.get("TW539_EVIDENCE_TRIGGER_URL", "")
    secret = os.environ.get("EVIDENCE_TRIGGER_SECRET", "")
    if not url.startswith("https://") or not secret:
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
    except (OSError, urllib.error.URLError, ValueError, TypeError):
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
