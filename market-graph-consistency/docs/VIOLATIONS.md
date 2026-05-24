# Violations

Violations are review findings, not direct recommendations.

## `IMPLICATION_VIOLATION`

Math: `P(A) <= P(B) + tolerance + spread_buffer`.

Triggers when source market `A` is modeled as implying destination market `B`, but `P(A)` is higher after buffers.

Example: `OpenAI first to AGI` priced above `Any company announces AGI`.

## `SUBSET_OVER_SUPERSET`

Math: `P(narrower) <= P(broader) + tolerance + spread_buffer`.

Triggers when a narrower event has a higher probability than its broader event after buffers.

Example: `$1T valuation` priced above `$500B valuation`.

## `SUM_OVER_ONE`

Math: `sum(P_i) <= 1 + tolerance + spread_buffer`.

Triggers on an exclusion set when mutually exclusive yes outcomes sum above one after buffers.

Example: named first-to-AGI company markets sum above one.

## `REWORD_MISMATCH`

Math: `abs(P(A) - P(B)) <= tolerance + spread_buffer`.

Triggers when markets modeled as the same event differ beyond buffers.

Example: two `OpenAI over $1T before 2028` wordings diverge.

## `AMBIGUOUS_WORDING`

No price math. This is metadata for wording review only and is emitted when an edge is marked `AMBIGUOUS`.

This is not a price violation and cannot exceed `WATCH`, regardless of confidence.

## Stubbed or Limited

- `STALE_DIVERGENCE`: needs multiple snapshots.
- `NEGCORR_COMOVEMENT`: needs multiple snapshots.
- Positive/negative correlation and proxy relationships have no hard v1 price constraint.

## Action Mapping

`graph_engine/consistency/tolerances.py` maps confidence and magnitude to `IGNORE`, `WATCH`, or `MANUAL_REVIEW`. `AMBIGUOUS_WORDING` is capped at `WATCH`.
