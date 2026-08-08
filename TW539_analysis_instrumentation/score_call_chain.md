# Score call chain

| Stage | Location | Classification |
|---|---|---|
| Raw history | `server.py::taiwan_history` / `_mm_rows` | STATEFUL |
| Aggregate statistics | `server.py::_mm_stats` | CAPTURABLE_NOW |
| Raw and normalized features | `server.py::_mm_feature_rows` | CAPTURABLE_NOW; some raw values were previously NOT_EXPOSED |
| Normalization | `server.py::_mm_norm`, `_mm_clamp` | TRANSFORM_ONLY |
| Logistic coefficients | `server.py::_mm_fit_logistic` | STATEFUL calculation, observer does not mutate |
| Weighted model terms | `server.py::_mm_model_scores` | CAPTURABLE_NOW |
| Sigmoid/clamp | `server.py::_mm_model_scores` | TRANSFORM_ONLY |
| Dynamic model weights | `server.py::_formal_weights_from_profiles` | STATEFUL, read-only capture |
| Ensemble final score | `server.py::_formal_analysis` | CAPTURABLE_NOW |
| Ranking | `server.py::_formal_analysis` | CAPTURABLE_NOW |
| Public payload | `server.py::build_payload` | SIDE_EFFECT_RISK; deliberately untouched |
| Prediction lock | `prediction_journal_v3.record_live_prediction` | SIDE_EFFECT_RISK; deliberately untouched |

Instrumentation is implemented by `tw539_score_trace.py` and calls from the two scoring locations only.
