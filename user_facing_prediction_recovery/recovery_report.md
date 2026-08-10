# User-facing Prediction Contract Recovery

## Outcome

Verdict A. The existing locked TW539 Current prediction is again the sole source of the user-facing recommendation. No prediction was executed or regenerated.

## Data loss location

The runtime, immutable prediction journal, and read API retained the formal prediction. The loss occurred in the product frontend adapter/workspace: the page rendered `/api/latest` actual draw data and did not bind the matching open journal prediction.

## Ranking integrity

The former 1–39 display was natural numeric order produced by a sorting helper and was not a provable model ranking. It has been removed. The UI now displays only the immutable rank 1–15 sequence from the same formal prediction snapshot.

## Metric semantics

The contradictory `0 samples + 91.67%/100%` presentation mixed research controls with product model groups and used numeric fallbacks for missing fields. Zero-sample groups now show `尚無可用樣本` and `—`; baselines are placed in a separate advanced section.

## Verification

- Playwright + Google Chrome desktop: PASS
- Playwright + Google Chrome mobile 390×844: PASS
- Core five recommendation appears at y=638.47, inside the initial mobile viewport
- Browser overflow: 0
- Web suite: 223 passed, 2 subtests passed
- Research sandbox isolation suite: 152 passed
- Legacy product contract: 26/26 retained
- Prediction algorithm: unchanged
- Research Brain: unchanged
- Commit, push, deploy: not performed by this gate
