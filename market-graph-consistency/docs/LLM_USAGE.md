# LLM Usage

V1 includes only `graph_engine/relationships/llm_extractor.py`, a deterministic offline interface stub.

## Allowed Future Uses

- Semantic classification.
- Relationship suggestion.
- Wording comparison.
- Entity and theme normalization.

## Disallowed Uses

- Direct prices or probabilities.
- Direct recommendations.
- Account or venue operations.
- Private or undocumented API access.
- Any output above `MANUAL_REVIEW`.

## v1 Enforcement

- Unreviewed relationships with `source="llm"` are capped at confidence `0.6` during `RelationshipEdge` construction.
- Reviewed LLM-sourced relationships may retain higher confidence only when `reviewed_by` is set.
- `AMBIGUOUS_WORDING` findings are metadata-only wording review signals and cannot exceed `WATCH`.

## Future Call Requirements

Future real LLM calls must:

- Use structured JSON output.
- Use temperature `0`.
- Include an abstain option.
- Write audit logs with prompt, model, timestamp, input ids, output, and parser result.
- Never include account data.
- Require human promotion before an LLM relationship becomes high-confidence manual graph state.

## Current Stub

`DeterministicLLMExtractor` returns injected fixture suggestions only. It has no model client, network dependency, environment-variable dependency, or price logic.
