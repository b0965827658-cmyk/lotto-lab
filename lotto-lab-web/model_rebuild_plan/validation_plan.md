# Validation Plan

## Walk-forward design

- Strict chronological walk-forward; each target uses only earlier rows.
- Training window: 300 rows.
- Report separate windows for the most recent 100, most recent 300, and all eligible targets.
- TW539 eligible targets: 700; report 100, 300, and 700.
- Fantasy5 eligible targets: 65; report all 65 and label 100/300 as unavailable rather than padding or reusing targets.
- Validate Random, long-term frequency, rolling frequency, gap, Logistic Regression, Random Forest, HistGradientBoosting, and Ensemble. Uniform/repeat/co-occurrence may be retained as supplementary diagnostics.

## Required metrics per model and ensemble

- Top5 average hits
- Top10 average hits
- Top15 average hits
- Brier Score
- Log Loss
- 95% bootstrap confidence intervals for Top5/10/15
- Delta versus random and the best deterministic simple baseline
- Paired effect size on per-target Top15 hits: mean paired delta divided by the sample standard deviation of paired deltas (Cohen's dz); report raw mean delta as well
- One-sided paired sign-permutation p-value, fixed seed `20260801`, at least 10,000 permutations for final acceptance
- Holm correction across the three supervised model promotion tests per game

## Promotion rule

A supervised model may receive non-zero production Ensemble weight only when all conditions hold on all eligible walk-forward targets:

1. Top15 mean is greater than random and no worse than the best frequency/gap baseline.
2. Paired effect size versus the best simple baseline is positive.
3. Holm-adjusted p-value is at most `0.05`.
4. Brier Score and Log Loss are not both worse than the best simple baseline; at least one must improve without material degradation (`>1%`) in the other.
5. Recent-window results do not reverse direction: TW539 recent100 and recent300 deltas must be non-negative; Fantasy5 uses all 65 and is marked sample-limited.
6. Both deterministic rebuild runs agree under the reproducibility contract.

If a supervised model fails, set its weight to zero and renormalize accepted models. If all supervised models fail, publish a `baseline-only` decision; do not force supervised models into the Ensemble.

## Acceptance outputs

- Machine-readable validation JSON and human-readable report per game
- Per-target events sufficient to recompute every metric
- Final Top5/Top10/Top15 and complete ranking for the fixed cutoff
- Final weights and explicit inclusion/exclusion reason per model
- Artifact load test in a clean process
- Schema validation of `manifest.json`
- SHA-256 verification for every artifact and weight file

