# Rebuild Inventory

Target release: `v3.0.1-rebuilt`

This release must never be represented as a recovered V3.0.0 artifact set. It is a new, deterministic rebuild from fixed repositories and a pinned environment.

## Required supervised models

Both games require exactly the three supervised models explicitly returned by `analysis_v2._supervised_models()` and expected by `_model_names()`.

| Game | Model | Training entry point | Hyperparameters | Output |
|---|---|---|---|---|
| TW539 | Logistic Regression | `analysis_v2.train_and_save("tw539", frozen_rows)`; internally `_fit_supervised` | `max_iter=150`, `class_weight="balanced"`, `random_state=20260801` | `data/models_v2/tw539/logistic-regression.joblib` |
| TW539 | Random Forest | same | `n_estimators=16`, `max_depth=6`, `min_samples_leaf=3`, `random_state=20260801`, `n_jobs=1`, `class_weight="balanced_subsample"` | `data/models_v2/tw539/random-forest.joblib` |
| TW539 | HistGradientBoosting | same | `max_iter=24`, `max_leaf_nodes=12`, `learning_rate=0.08`, `random_state=20260801` | `data/models_v2/tw539/hist-gradient-boosting.joblib` |
| Fantasy5 | Logistic Regression | `analysis_v2.train_and_save("ca-fantasy5", frozen_rows)`; internally `_fit_supervised` | `max_iter=150`, `class_weight="balanced"`, `random_state=20260801` | `data/models_v2/ca-fantasy5/logistic-regression.joblib` |
| Fantasy5 | Random Forest | same | `n_estimators=16`, `max_depth=6`, `min_samples_leaf=3`, `random_state=20260801`, `n_jobs=1`, `class_weight="balanced_subsample"` | `data/models_v2/ca-fantasy5/random-forest.joblib` |
| Fantasy5 | HistGradientBoosting | same | `max_iter=24`, `max_leaf_nodes=12`, `learning_rate=0.08`, `random_state=20260801` | `data/models_v2/ca-fantasy5/hist-gradient-boosting.joblib` |

No other supervised models are currently expected by Production.

## Required companion artifacts

- `data/models_v2/tw539/manifest.json`
- `data/models_v2/tw539/baseline_models.json`
- `data/weights_v2_tw539.json`
- `data/models_v2/ca-fantasy5/manifest.json`
- `data/models_v2/ca-fantasy5/baseline_models.json`
- `data/weights_v2_ca_fantasy5.json`

## Weight generation

Weights must come only from chronological walk-forward outcomes. For every active model, compute the weighted mean of Top15 hits as `0.50 * recent30 + 0.30 * recent100 + 0.20 * recent300`, apply a `0.05` floor before normalization, cap each model at `0.28`, redistribute excess, and normalize to sum to one. Use the final walk-forward weight row. Random and supervised models are eligible only if the validation contract permits them; uniform/random baselines must not be promoted merely because they are available.

