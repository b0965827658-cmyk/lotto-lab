# Lotto Lab — Production V1.1

Lotto Lab serves TW539 and California Fantasy 5 from canonical repositories. Production uses a persistent Warm Cache and a single analysis queue so the Starter instance never runs two cold analyses concurrently.

## System architecture

```text
Canonical Repository
        │ repository signature changed
        ▼
Single Analysis Queue ── TW539 ──► Fantasy 5
        │
        ▼
Persistent Disk (/api/health)
  ├─ analysis_warm_cache.json
  ├─ prediction_journal_v3_*.json
  ├─ *_prediction_history.json
  └─ operations/
       ├─ health_checks/
       ├─ backups/ (30 days)
       ├─ reports/
       └─ dashboard/health_dashboard.html
```

The V1.1 operations layer does not import or alter model code, weights, features, rankings, recommendations, or response payloads.

## API flow

1. The client requests the existing analysis endpoint.
2. A valid persistent entry returns `HTTP 200`, `completed`, `cached=true`.
3. A missing entry is queued once and returns `HTTP 202`.
4. Status polling reads job state; it does not create another job.
5. Prediction Journal writes remain controlled by the existing analysis pipeline.

No API route or response format is added or changed by V1.1.

## Warm Cache flow

Repository checks enqueue work instead of starting a second thread. TW539 completes before Fantasy 5 starts. A rebuild is published atomically only after success; an older valid cache remains readable during refresh or failure.

## V1.1 operations

The daily daemon starts 60 seconds after Production startup and then runs every 24 hours. It only records anomalies and never repairs them automatically.

```bash
python operations_v11.py health
python operations_v11.py backup
python operations_v11.py report
python operations_v11.py all
```

Health output includes API health, Warm Cache evidence, memory, CPU load, single-queue mode, Journal integrity, last analysis times, and Render instance metadata. Reports cover observed daily health requests; Production API payloads are intentionally not instrumented.

Backups are written beneath `/api/health/operations/backups/YYYY-MM-DD/`, include a SHA-256 manifest, never overwrite the source, and retain the most recent 30 days.

## Render deployment

1. Deploy GitHub `main` without clearing build cache.
2. Confirm `LOTTO_PERSISTENT_DATA_DIR=/api/health` and the disk mount is `/api/health`.
3. Confirm `/api/health` returns HTTP 200.
4. Confirm both games return `completed`, `cached=true` sequentially.
5. Compare Persistent Disk file sizes, SHA-256 values, and timestamps before and after restart.
6. Confirm Journal counts did not increase from cache reads.

## Recovery SOP

1. Stop new deployment activity; do not delete or reformat the disk.
2. Record commit, instance ID, file sizes, SHA-256 values, and timestamps.
3. Inspect `/api/health/operations/backups/` and select the newest valid manifest.
4. Restore only after human approval. V1.1 never restores automatically.
5. Restart once, then verify health and both cache hits sequentially.
6. If hashes differ unexpectedly, stop and preserve both copies for audit.

## Production acceptance

- Health endpoint is HTTP 200.
- Both games are `completed` and `cached=true` after restart.
- Warm Cache evidence is unchanged across restart.
- No cold Walk-Forward is triggered by acceptance reads.
- Prediction Journal has no duplicate `(drawId, predictionHash)` identities.
- No OOM, Exit 137, traceback, or duplicate cold analysis appears.
- The complete automated test suite passes.

## Prediction invariance

V1.1 is an operations-only release. It does not modify models, weights, feature order, Walk-Forward, Repository content, recommendation logic, API payloads, or frontend layout.
