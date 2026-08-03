# Final Rebuild Plan

## Identity

Version: `v3.0.1-rebuilt`

Required disclosure: **These artifacts are newly trained from fixed data and a pinned environment. They are not recovered copies of the pre-migration V3.0.0 models.**

## Execution sequence for a future authorized rebuild

1. Keep Production stopped and create two empty, isolated staging runs.
2. Verify code commit, package versions, thread controls, timezones, canonical files, row counts, cutoffs, data hashes, and feature-schema hashes.
3. Train the three declared supervised models independently for TW539 and Fantasy5 using the existing `analysis_v2.train_and_save` pipeline against frozen rows only.
4. Produce complete manifests, baseline manifests, joblib artifacts, walk-forward outputs, and weight candidates in staging.
5. Repeat from an empty staging directory and enforce deterministic comparison.
6. Run the validation plan against random, frequency, gap, each supervised model, and Ensemble.
7. Exclude every supervised model that fails promotion criteria; never force a model into the Ensemble.
8. Label the accepted artifact set `v3.0.1-rebuilt`, calculate all hashes, validate manifest schemas, and test clean-process loading.
9. Produce a signed-off promotion bundle. Do not modify Production until a separate promotion instruction is given.
10. If later promoted, use the atomic promotion and rollback procedure in `rollback_plan.md`.

## Fixed inputs

- TW539: exactly 1,000 rows from `data/tw539_database.json`, cutoff 2026-06-30, canonical SHA-256 `ad2aea59e3d57e0c5e95600ede777f61e964949ad6e8021b0b4b3ed9107d5dde`.
- Fantasy5: exactly 365 rows from `data/ca_fantasy5_database_v2.json`, cutoff 2026-07-31, canonical SHA-256 `74b78bad133b9d25fa2cfe448fa619bda76e2f9b71ef8e3e8d43d570adf49dc6`.

## Deliverables after a future rebuild

- `data/models_v2/tw539/` with three model artifacts, `manifest.json`, and `baseline_models.json`
- `data/models_v2/ca-fantasy5/` with three model artifacts, `manifest.json`, and `baseline_models.json`
- `data/weights_v2_tw539.json`
- `data/weights_v2_ca_fantasy5.json`
- environment lock, two-run reproducibility report, per-game validation report, artifact hash inventory, and rollback manifest

No training or artifact creation is authorized by this plan itself.
