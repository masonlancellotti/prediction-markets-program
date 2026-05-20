# Graph Consistency Summary

- Snapshot: `fixture-snapshot-20260519T181000Z`
- Markets: 7
- Relationships: 5
- Exclusion sets: 1
- Findings: 6
- Highest action: `MANUAL_REVIEW`
- Scope: Offline fixture snapshot only; no live venue calls were made.

## AMBIGUOUS_WORDING

### `AMBIGUOUS_WORDING:edge_microsoft_leader_ambiguous_openai_first_agi`

- Action: `WATCH`
- Confidence: 0.550
- Raw gap: 0.000
- Spread-adjusted gap: 0.000
- Magnitude: 0.000
- Involved markets:
- `kalshi:microsoft_first_agi_2027`: Microsoft first to AGI by the end of 2027 | yes=0.310 | as_of=2026-05-19T18:05:00+00:00
- `manifold:openai_first_agi_2027`: OpenAI is first company to announce AGI by 2027 | yes=0.460 | as_of=2026-05-19T18:10:00+00:00
- Explanation: The relationship is intentionally marked ambiguous and needs human wording review before any hard constraint is used.
- Review questions:
  - What exact relationship, if any, should be promoted from this ambiguous edge?
  - Which resolution words create the ambiguity?
  - Should this remain documentation-only until more snapshots are available?

## IMPLICATION_VIOLATION

### `IMPLICATION_VIOLATION:edge_openai_first_agi_implies_agi_by_2027`

- Action: `MANUAL_REVIEW`
- Confidence: 0.883
- Raw gap: 0.120
- Spread-adjusted gap: 0.050
- Magnitude: 0.050
- Involved markets:
- `manifold:openai_first_agi_2027`: OpenAI is first company to announce AGI by 2027 | yes=0.460 | as_of=2026-05-19T18:10:00+00:00
- `manifold:agi_by_2027`: Will AGI be announced by any company before 2028? | yes=0.340 | as_of=2026-05-19T18:10:00+00:00
- Explanation: manifold:openai_first_agi_2027 is modeled as implying manifold:agi_by_2027, but its probability is higher after tolerance and spread buffer.
- Review questions:
  - Does the source market truly imply the destination market under resolution wording?
  - Are both snapshots fresh enough to compare?
  - Could fees, wide spreads, or venue-specific wording explain the gap?

### `IMPLICATION_VIOLATION:edge_anthropic_first_agi_implies_agi_by_2027_low_conf`

- Action: `IGNORE`
- Confidence: 0.190
- Raw gap: 0.100
- Spread-adjusted gap: 0.030
- Magnitude: 0.030
- Involved markets:
- `polymarket:anthropic_first_agi_2027`: Anthropic is first company to announce AGI by 2027 | yes=0.440 | as_of=2026-05-19T18:00:00+00:00
- `manifold:agi_by_2027`: Will AGI be announced by any company before 2028? | yes=0.340 | as_of=2026-05-19T18:10:00+00:00
- Explanation: polymarket:anthropic_first_agi_2027 is modeled as implying manifold:agi_by_2027, but its probability is higher after tolerance and spread buffer.
- Review questions:
  - Does the source market truly imply the destination market under resolution wording?
  - Are both snapshots fresh enough to compare?
  - Could fees, wide spreads, or venue-specific wording explain the gap?

## REWORD_MISMATCH

### `REWORD_MISMATCH:edge_openai_1t_same_event_cross_venue`

- Action: `MANUAL_REVIEW`
- Confidence: 0.874
- Raw gap: 0.120
- Spread-adjusted gap: 0.040
- Magnitude: 0.040
- Involved markets:
- `polymarket:openai_valuation_1t_2027`: OpenAI exceeds $1T valuation by end of 2027 | yes=0.620 | as_of=2026-05-19T18:00:00+00:00
- `kalshi:openai_value_above_1t_2027`: Will OpenAI be valued over one trillion dollars before 2028? | yes=0.500 | as_of=2026-05-19T18:05:00+00:00
- Explanation: polymarket:openai_valuation_1t_2027 and kalshi:openai_value_above_1t_2027 are modeled as rewordings of the same event, but their probabilities differ beyond configured buffers.
- Review questions:
  - Do both contracts resolve from the same source and date window?
  - Is one market using a materially different threshold or definition?
  - Could stale or low-liquidity quotes explain the mismatch?

## SUBSET_OVER_SUPERSET

### `SUBSET_OVER_SUPERSET:edge_openai_1t_subset_openai_500b`

- Action: `MANUAL_REVIEW`
- Confidence: 0.912
- Raw gap: 0.100
- Spread-adjusted gap: 0.030
- Magnitude: 0.030
- Involved markets:
- `polymarket:openai_valuation_1t_2027`: OpenAI exceeds $1T valuation by end of 2027 | yes=0.620 | as_of=2026-05-19T18:00:00+00:00
- `polymarket:openai_valuation_500b_2027`: OpenAI valuation above $500B by end of 2027 | yes=0.520 | as_of=2026-05-19T18:00:00+00:00
- Explanation: polymarket:openai_valuation_1t_2027 is modeled as narrower than polymarket:openai_valuation_500b_2027, but the narrower outcome has a higher probability after buffers.
- Review questions:
  - Is the subset relationship valid across venues and resolution sources?
  - Does the broader market include all cases covered by the narrower market?
  - Are there stale prices or sparse liquidity behind either quote?

## SUM_OVER_ONE

### `SUM_OVER_ONE:first_company_to_agi_2027_named_companies`

- Action: `MANUAL_REVIEW`
- Confidence: 0.712
- Raw gap: 0.210
- Spread-adjusted gap: 0.120
- Magnitude: 0.120
- Involved markets:
- `manifold:openai_first_agi_2027`: OpenAI is first company to announce AGI by 2027 | yes=0.460 | as_of=2026-05-19T18:10:00+00:00
- `polymarket:anthropic_first_agi_2027`: Anthropic is first company to announce AGI by 2027 | yes=0.440 | as_of=2026-05-19T18:00:00+00:00
- `kalshi:microsoft_first_agi_2027`: Microsoft first to AGI by the end of 2027 | yes=0.310 | as_of=2026-05-19T18:05:00+00:00
- Explanation: Exclusion set first_company_to_agi_2027_named_companies sums to 1.210, above 1.0 after configured tolerance and spread buffer.
- Review questions:
  - Are all listed outcomes truly mutually exclusive?
  - Is the set complete or only a subset of possible winners?
  - Do venue rules allow ties, cancellations, or different resolution windows?
