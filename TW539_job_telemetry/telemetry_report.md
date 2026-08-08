# TW539 Job Telemetry-only Instrumentation

Telemetry-only instrumentation records TW539 queue, start, heartbeat, Current completion, worker release, and failure events. It is isolated from ranking, scoring, API response decisions, Warm Cache, Prediction Journal, Shadow Journal, Evidence Journal, and Score Trace.

The feature flag is `TW539_JOB_TELEMETRY_ENABLED`, default `false`. Automatic recovery, stale transition, orphan cleanup, and analysis re-execution are absent from this delivery.

Storage uses one atomic file per job under `${LOTTO_PERSISTENT_DATA_DIR}/job_telemetry/jobs/`. Lifecycle events are append-only within that job file. Heartbeats are compacted to the latest observation plus count and first/last timestamps.
