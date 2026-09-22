# California Fantasy 5 staging source validation

Scope: codex/fantasy5-fast-official-feed; Render lotto-lab-candidate-a-staging only.
Production is excluded. Fantasy 5 remains in DELIVERY_BLOCKED_GAMES. No LINE
requests are part of source probing; direct sends now also enforce the block.

The primary endpoint is https://www.calottery.com/api/v1.5/drawgames.
A 200 response is insufficient: JSON media type, strict decoding, game identity,
draw fields, five distinct integers 1..39, dates and history must all pass.
A maintenance HTML response triggers a real fetch of
https://www.calottery.com/draw-games/fantasy-5.
Redirects outside the explicit California Lottery HTTPS host allowlist fail
before requesting the redirect target.

Fallback supports JSON script tags and JSON data attributes, and official URLs
explicitly advertised by data-api-url/data-endpoint/data-draw-url attributes.
It does not parse visible numbers, execute JavaScript, guess game IDs, or use
third-party data. These formats are tested synthetic contracts, NOT a confirmed
representation of the currently inaccessible live page. No faster official XHR
has yet been confirmed. A maintenance page or unknown schema remains unavailable.

Historical anchor: the official Fantasy 5 page displayed draw 12006,
2026-09-20, 1/8/10/19/21 when inspected on 2026-09-22. Matching that period
requires exact numbers; later periods must maintain daily draw-number/date
continuity. This continuity check does not independently corroborate new winning
numbers. The adapter rejects future results and latest dates older than one day.

Each probe returns separate attempts with endpoint/final URL, HTTP status,
Content-Type, bytes, SHA256, fetch timestamp (Unix UTC), latencyMs and rejection
reason. Latency measures request plus body read, not draw-publication delay.
Optional ledger_path writes only an isolated official probe SQLite ledger,
never the existing application draw database or VERIFIED registry. Unique draw
number/date, an immediate transaction and immutable values prevent duplicate or
conflicting writes across processes. Missing live data produces no ledger rows.

Validation: pytest test_california_fantasy5_official.py
tests/test_fantasy5_fallback.py tests/test_line_admin_staging.py
tests/test_line_webhook.py tests/test_line_notifications.py -q
Fixtures exercise 200 maintenance HTML, incorrect media types, malformed JSON,
schema/history failures, structured fallback, official endpoint discovery,
external redirects, repeated/concurrent writes and blocked LINE sends.
No mocked source success is reported as a live official-source success.

## Staging query trial
The user requested an unblock trial. Only `/api/latest?game=ca-fantasy5`
bypasses the broad delivery block on the exact Staging service/runtime. It calls
only the strict official adapter. Upstream unavailability returns HTTP 503 with
validated=false and per-source diagnostics; valid results retain provenance.
The broad block stays in place for legacy history, analysis and all LINE delivery.
The trial performs no database writes or notification sends. Other service IDs
and non-Staging runtimes cannot enable the query trial.
