# TW539 Analysis Instrumentation Gate

## Verdict

**B — Instrumentation captures the scoring path and reconstructs the final ensemble exactly, but nonlinear transforms remain explicitly unattributed. Explainability is PARTIAL_ATTRIBUTION.**

The implementation is observer-only and guarded by `TW539_SCORE_TRACE_ENABLED`, default false. It uses context-local state, does not add fields to `analysis`, and never writes a Journal. Collector failures are isolated from Current.

## Captured at calculation time

- 39 number containers
- normalized values for all 19 active Features
- available raw source statistics
- model Feature weights and weighted terms
- Bayesian, Boosted and Markov pre-clamp scores
- Logistic intercept, per-Feature logit terms, bounded input and sigmoid output
- clamp/sigmoid transform input, output and delta
- dynamic ensemble model weights
- final score and final rank
- exact ensemble reconstruction error

## Attribution boundary

Logistic Feature contributions are valid in logit space, not final probability space. The sigmoid delta is retained as a transform component and is not distributed across Features. Clamp deltas are likewise explicit. Some composite window terms remain model-level subtotals in models that consume `windows_score`. Therefore this trace must not be presented as complete additive Feature attribution in final-score space.

## Zero behavior change

ON and OFF runs on the same 1,000-row TW539 fixture produced identical model score dictionaries, ensemble scores, Top15 and full 39-number ranking. Trace data is not inserted into API payload, Warm Cache or Prediction Journal.

## Local cost preflight

Twenty isolated scoring iterations per mode:

- OFF mean latency: 30.603 ms
- ON mean latency: 33.121 ms
- Increment: 2.518 ms (8.23%)
- Observed peak RSS delta: 802,816 bytes (0.766 MiB)
- Compact JSON trace: 153,049 bytes per draw
- 365-draw projection: 55,862,885 bytes (53.28 MiB)
- Three-year projection: 167,588,655 bytes (159.82 MiB)

This is a local preflight, not Render RSS evidence.

## Safety

- Flag defaults false and creates no trace container.
- Fantasy 5 cannot activate the collector.
- No worker, process, thread pool or parallel scoring was added.
- No live Journal integration or historical backfill was implemented.
- No Commit, Push, Deploy, Render change or manual E4 trigger occurred.
- Candidate C remains Prototype / Awaiting Shadow.
- Production is unchanged and S5 remains locked.
