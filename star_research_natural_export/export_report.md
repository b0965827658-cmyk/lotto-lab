# Star Research Brain v1 — Read-only Natural Evidence Export

Implementation provides four fixed, authenticated, bounded GET exports on the Staging web service and a fixed-host read client plus reconciliation on the dedicated Research service. The exporter performs read/validate/serialize only and never calls prediction, analysis, settlement, or journal writers.

Network contract is restricted to Render private hostname `lotto-lab-candidate-a-staging:10000`. Requests using the public hostname are rejected before source access. Authentication uses the dedicated `RESEARCH_EVIDENCE_READ_SECRET` header value.

Brain Kill remains enabled. Reconciliation may enqueue verified immutable events, while the research processor remains `SAFE_NOOP_KILLED`.

Local verification: Web 189 passed plus 2 subtests; Research 136 passed; Python compile and Git diff check passed.
