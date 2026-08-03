# Reproducibility Requirements

## Pinned environment

- OS/architecture: Windows x86-64, recorded in manifest
- Python: `3.14.0`
- numpy: `2.5.1`
- scikit-learn: `1.9.0`
- joblib: `1.5.3`
- pandas: `3.0.5`
- tzdata: `2026.3`
- random seed: `20260801`
- model thread count: `1`
- Set `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`, and `PYTHONHASHSEED=20260801` before Python starts.

## Determinism gates

1. Verify input and feature-schema hashes before fitting.
2. Use the exact cutoff and feature order in `training_contract.md`.
3. Run two clean builds in separate empty staging directories.
4. Compare semantic manifests after excluding only the wall-clock `trainedAt` field. All other fields must match exactly.
5. Compare Top5, Top10, Top15, full 1-39 ranking, weights, probabilities, and validation events.
6. Integer rankings/hits and weights rounded to eight decimals must match exactly.
7. Raw probability tolerance: absolute `1e-12`, relative `1e-10`. Any rank change is a failure regardless of numeric tolerance.
8. Artifact SHA-256 should match between the two builds. If joblib container metadata prevents byte identity while semantic outputs are exact, record both hashes and require successful deserialization plus exact estimator parameters, learned-array shapes/dtypes, and prediction equality within the stated tolerance.
9. Freeze the accepted run's complete environment export and artifact hashes.

`trainedAt` must be recorded for provenance, but it must not be used as a determinism comparison field. A canonical manifest hash must be calculated after removing `trainedAt` and sorting JSON keys.

## Abort conditions

- Any input/hash/schema/version mismatch
- Any fallback or live-network data access
- More than one compute thread
- Missing timezone database or package
- Training produces warnings indicating convergence failure, numerical instability, or changed feature dimensions
- Two-run ranking, weight, or metric mismatch beyond contract tolerance

