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

## Third-party integration investigation — 2026-09-27

The user explicitly requested third-party integration. This authorizes a separately
attributed lookup, not promotion of third-party results to official VERIFIED data
or enabling Fantasy 5 LINE notifications. No live third-party adapter has yet
been connected; do not describe the documentation examples as a working feed.

Local and remote branch remain at d991bf73fdc52ba6ee3c2f9178cd72c817c30b87.
Staging health returned 200 at 2026-09-27T09:12:44Z. Its current deployed commit
could not be rechecked because the authenticated Render browser is unavailable;
the last verified deployment was dep-dapvui5g1s2s73djfjcg. Preserve the untracked
board-preview-umybr84j directory. No application code, draw data, recipient or
deduplication ledger was changed, and no deployment or LINE send was performed.

Candidate evidence:

* Lottery Results Feed: independent provider, not an official lottery operator.
  https://www.lotteryresultsfeed.com/lottery/api-integration/20 documents ID 20
  as US/California Fantasy 5, pick 5 from 39. Metadata and results require a
  Bearer token. A documentation response example showed 2026-09-26 and
  1/6/7/11/26; this was NOT an authenticated results API response and is not
  acceptable as a feed fixture. No official draw number or measured publication
  latency was obtained. Prior direct Cloudflare denial was not retried/bypassed.
  https://www.lotteryresultsfeed.com/pricing currently lists the free tier as
  100 calls/month and data typically within 6 hours, unsuitable for the requested
  fast feed. Its terms prohibit scraping outside documented APIs and require
  written consent for redistribution; its pricing page advertises commercial
  app use. Public-display scope needs clarification if this provider is selected.
  https://www.lotteryresultsfeed.com/terms
* Stepzero (Lottery Analytics LLC): independent provider; California Fantasy 5
  coverage remains unconfirmed. Its older developer article describes no-key
  access, but the current https://lotteryanalytics.app/api-docs requires a free
  X-API-Key for most endpoints (500/hour on the free account plan). At
  2026-09-27T09:11:33.114461Z, a direct GET to
  https://lotteryanalytics.app/api/v1/ask?q=What+are+the+latest+California+Fantasy+5+winning+numbers+and+draw+date%3F
  returned HTTP 401, application/json, API_KEY_REQUIRED, 860 ms, 190 bytes.
  No date, numbers or draw number were returned. The documented open discovery
  endpoints /api/v1 and /api/v1/public-stats returned HTTP 200 JSON at
  09:11:51Z / 09:11:52Z (635 / 656 ms); these only describe routes and do not
  establish game coverage. Do not impersonate an exempt crawler to avoid keys.
  https://lotteryanalytics.app/account redirects to an email-link login.
* Down Tack: https://downtack.com/en/us-lottery-api.html explicitly lists
  California Fantasy 5 and offers JSON/XML/widgets for websites. Published
  starting price is USD 50/month; test access requires contacting the vendor.
  No live response, source-specific provenance or publication latency tested.
  No account purchase or vendor contact was made.

The documented providers may share upstream sources; independence has not been
established. HTTP request duration does not measure result publication delay.
The user was asked only for an existing provider name, not a plaintext key.
Next: obtain account access via the provider UI, keep credentials server-side,
confirm California identity using actual metadata, retrieve and validate real
results, then implement and test the attributed lookup with Los Angeles date/DST,
freshness, schema, same-draw deduplication and conflict handling. Deploy only after
these checks pass and the specified Staging deployment can be verified.

## Stepzero account access follow-up — 2026-09-27

The user completed Stepzero login in Chrome. The account showed the free plan,
500 API requests/hour, and a dedicated key named lotto-lab-staging-fantasy5 was
created once. No subscription upgrade or payment was performed.

Browser control then failed while completing the local credential handoff.
The one-use localhost form was repaired and its GET was checked with HTTP 200,
but no encrypted credential file or authenticated catalog response has been
created yet. A user-input request asks the user to paste the key into the local
password form and submit it, never into the conversation. The account and local
setup tabs are retained for that action. Once submitted, the helper encrypts the
key with Windows DPAPI under .git and calls only GET /api/v1/games; it records
non-secret response evidence and exits. Do not create another key or claim the
feed is integrated before actual catalog/results validation.

No application code, lottery records, Staging environment variables, deployment,
notification settings, recipients or deduplication ledger changed in this turn.
The existing staging-line automation was already PAUSED and remains unchanged.

## Third-party adapter completed locally — 2026-09-27 10:12 UTC

This supersedes the earlier credential-handoff blocker. The dedicated Stepzero
key is now saved locally using Windows-account DPAPI encryption under .git;
neither the key nor its plaintext is in tracked files. The localhost handoff
exited after a successful authenticated catalog request.

Actual authenticated evidence (not documentation examples):

- GET https://lotteryanalytics.app/api/v1/games at 10:02:09.428918Z:
  HTTP 200 application/json; 848 ms. Catalog entry CA-FANTASY5 explicitly identifies
  California, Fantasy 5, FANTASY5, pick_balls, metadata_schema_version 1.
- GET https://lotteryanalytics.app/api/v1/games/CA-FANTASY5/latest at
  10:02:36.354385Z: HTTP 200 application/json; 874 ms. draw_date is
  2026-09-26T00:00:00.000Z, result is 01 06 07 11 26, draw_time E,
  provider draw_id 25125087. updated_at is null; publication delay is unknown.
  The provider's draw ID is NOT an official draw number and is never labelled one.
- GET https://lotteryanalytics.app/api/v1/games/CA-FANTASY5/draws?limit=10 at
  10:02:36.356106Z: HTTP 200 application/json; 876 ms. Ten consecutive dates
  September 17–26, matching latest and the September 20 historical anchor
  01/08/10/19/21. Latest/history belong to the same provider; this does not establish
  source independence or official verification. Sanitized actual JSON is retained
  in tests/fixtures/stepzero_ca_fantasy5.json with acquisition provenance.
- Two live adapter fetches at 10:09:42 / 10:09:43 UTC returned the same result;
  complete lookup durations 1201 / 458 ms. The isolated SQLite file's bytes were
  identical after the second fetch. Request duration is not publication delay.

Implementation:

- fantasy5_stepzero.py validates exact California identity, real response schema,
  five unique integers 1–39, consecutive recent history, matching latest/history,
  Pacific date/DST, historical anchor, conflicts and regressions.
- The midnight-Z date is a provider calendar label, not an instant to convert to
  Pacific (that would incorrectly subtract a day). After the 18:30 Pacific draw
  cutoff, the prior draw can appear with an explicit pending-current-draw label
  for 30 minutes; after 19:00, an unupdated prior result is withheld. This is a
  conservative local freshness policy, not a provider publication SLA.
- API key stays server-side in STEPZERO_API_KEY. All calls have bounded time/body,
  reject redirects, sanitize errors and share a 60-second cache under a lock.
- Results are append-only in fantasy5_third_party_staging.sqlite3, separate from
  official records, legacy history/model stores and notification ledgers.
- Only the existing exact Staging runtime/service guard exposes this lookup.
  /api/latest?game=ca-fantasy5 defaults to Stepzero when configured;
  source=stepzero selects it explicitly, source=official preserves official probing.
  Third-party responses have ok=true but validated=false, trust=third_party,
  schemaValidated=true and historyConsistent=true. No official VERIFIED promotion.
- /start.html has the attributed Fantasy 5 card, Pacific date, acquisition time,
  source links, unknown publication-delay note and 60-second polling.
- DELIVERY_BLOCKED_GAMES still blocks Fantasy 5 legacy history, models and LINE.
  Existing 539 delivery settings and deduplication records remain untouched.

Validation: 115 relevant tests passed (27 provider tests, plus 88 existing trial,
LINE, fallback, registry and community tests). Real local browser rendering shows
2026-09-26 / 01 06 07 11 26 with clear third-party attribution. Main/backup source,
wrong jurisdiction, DST, stale/future dates, conflict/rollback, concurrent cache,
no rewrite and no LINE delivery are covered. git diff --check passed.

Deployment remains pending: Staging /api/health returned 200 at
10:12:41.125178Z, but the Render dashboard session is expired. The user has been
asked to sign into the existing Render account. Do not describe local completion
as Live. Before pushing/deploying, verify service srv-d9pomuqd0e5s73en98eg,
branch codex/fantasy5-fast-official-feed and current deployment. Add only
STEPZERO_API_KEY to its private environment, then deploy the new commit once and
verify /start.html plus the live API. Do not modify Production or any other env
settings. No live LINE messages were sent. No deployment was triggered here.
