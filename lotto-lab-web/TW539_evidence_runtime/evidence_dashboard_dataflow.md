# Evidence Dashboard deterministic dataflow

```text
Immutable pre-draw Prediction
        +
Trusted Draw Settlement (actual_available_at)
        ↓
Prediction hash + created/locked time validation
        ↓
Append-only Evidence Journal
        ↓
Valid-Live-only Aggregation
        ↓
EPS gate evaluation
        ↓
Evidence Dashboard read model
```

The Dashboard reads `evidence_dashboard.json`, which is rebuilt from the verified Evidence Journal while holding the same process and cross-process lock. It never reads Markdown, Candidate prototype results or Walk-Forward artifacts as Live evidence.

Candidate C remains **Prototype / Awaiting Shadow** until the Registry explicitly records `SHADOW_RUNTIME` or `OBSERVATION` and the target draw has an immutable prediction locked before `actual_available_at`.

Invalid, late, corrupted, missing-actual and unverified records are excluded from aggregation and EPS. Dashboard output is derived data and may be rebuilt; the Evidence Journal is the append-only source of truth.
