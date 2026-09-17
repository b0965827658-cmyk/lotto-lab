# TW539 Deterministic Evidence Runtime v1

## Result

The deterministic local Runtime is complete. It does not execute Current, Baseline or Candidate models. It settles only immutable predictions created and locked before a trusted `actual_available_at`, then writes a hash-verified append-only Evidence Journal and deterministic read models.

## Runtime entry

- Python: `tw539_evidence_runtime.run_tw539_daily_evidence()`
- CLI: `python -m tw539_evidence_runtime --manifest <manifest.json>`
- Local tests may add `--local-test-directory <absolute-temp-path>`.
- Production-compatible storage requires an absolute `LOTTO_PERSISTENT_DATA_DIR` and resolves to `${LOTTO_PERSISTENT_DATA_DIR}/evidence/`. There is no repository, cwd, `/tmp` or user-folder fallback.

## Settlement safety

The Prediction hash covers lottery, Draw ID, subject type/version, created/locked times, Top5/10/15 and dataset identity. Both `prediction_created_at` and `locked_at` must be strictly earlier than the trusted `actual_available_at`. A late or altered prediction is stored as invalid with no hits and is excluded from aggregation, Win/Tie/Lose, EPS and Promotion.

This is fail-closed: existing records without a trustworthy `actual_available_at` cannot become valid Evidence merely because they contain an outcome.

## Journal and recovery

- Unique key: `lottery|draw_id|subject_version|tw539-evidence-v1.0.0`
- Per-record SHA-256 and whole-journal SHA-256
- Same-directory temporary file, file fsync, atomic `os.replace`, POSIX directory fsync
- Process mutex plus nonblocking cross-process file lock
- Immutable conflict rejection
- Content-addressed corrupt-file quarantine followed by safe stop
- Stale pre-replace temporary-file recovery
- Restart deduplication

Ten identical runs produced three records—Current, Baseline and one authorized Live Candidate—only on the first run. Runs 2–10 added zero records, and all runs produced journal SHA-256 `430115f2e37ff33ebed3ebe41c16b8eea70f8449a64ffad1b34480389eb48ef6`.

## Candidate isolation

Candidate C's current Registry state is `PROTOTYPE`, so the Runtime displays **Prototype / Awaiting Shadow** and writes no Candidate Live Evidence. A Candidate is accepted only when the Registry says `SHADOW_RUNTIME` or `OBSERVATION` and a valid pre-draw prediction exists. Candidate A archives and prototype Walk-Forward results are never treated as Live Evidence.

## EPS and Dashboard

Aggregation consumes only `validity_status=valid` Live records. EPS remains score-null until mandatory Live volume and operational gates exist. The Dashboard is a read model generated from the Evidence Journal aggregation; it cannot read Markdown or Prototype reports as Live data.

## Tests

- Evidence Runtime specialization: 19 passed, 0 failed
- Full pytest: 60 passed plus 2 subtests, 0 failed
- Python compile: passed
- `git diff --check`: passed
- Failure injection: crash, corruption, duplicate, invalid hash, late prediction, missing actual, Prototype isolation, lock contention, stale temp, fsync and replace paths passed in temporary directories

## Cloud plan

Recommended after a separate Staging-only Gate: run the deterministic Runtime from the existing Render Web Service's controlled scheduler lifecycle. Render Cron Jobs cannot access Persistent Disks; a separate background worker can have its own disk but cannot share the Web Service disk. No cloud resource was created here. The current Codex heartbeat remains unchanged and is labelled `LOCAL_DEVELOPMENT_ONLY`.

## Gate state

Evidence Runtime completion does not complete S4. New-draw staged RSS and Current early-return evidence are still missing. **S5 remains LOCKED.** No Commit, Push, Deploy, Production operation, retraining or model/Candidate modification occurred.
