# Rollback Plan

## Isolation

Build only under a versioned staging root outside Production paths, for example `staging/v3.0.1-rebuilt/run-<id>/`. Snapshot the pre-promotion Git commit, file inventory, SHA-256 values, and absence/presence of every destination artifact.

## Promotion prerequisite

Promotion is a separate, explicitly authorized step after validation. Stop the server, verify hashes again, and copy the complete accepted set atomically as one unit:

- both game model directories
- both weight files
- both manifests and baseline manifests
- validation reports and environment lock

Never promote a partial game or a mixture of runs.

## Rollback package

Before promotion, create a read-only backup of any files at the destination and a rollback manifest containing original paths, sizes, timestamps, and SHA-256. Because the current expected V2 artifacts are absent, the initial rollback state is principally "artifacts absent"; preserve that fact explicitly.

## Rollback triggers

- Artifact load failure
- Hash/schema mismatch
- Prediction/ranking mismatch versus accepted staging output
- Production error or unexpected fallback
- Any evidence of changed Repository, Prediction, Battle, API, or UI behavior outside artifact loading

## Rollback action

Stop service, remove only the precisely enumerated promoted `v3.0.1-rebuilt` files, restore the pre-promotion backup if one existed, verify hashes, and leave service stopped pending review. Do not retrain during rollback.

