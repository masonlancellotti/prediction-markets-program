# Relationships

Relationships connect market nodes. In v1, relationships are manual and loaded from `relationships/*.yaml`.

## Types

- `IMPLICATION`: if source resolves yes, destination should also resolve yes.
- `SUBSET`: source is a narrower event than destination.
- `SUPERSET`: source is a broader event than destination.
- `MUTUAL_EXCLUSION`: represented only as an `ExclusionSet` hyperedge.
- `SAME_EVENT_REWORDED`: two contracts appear to ask the same event with different wording.
- `POSITIVE_CORRELATION`: related directionally; no hard single-snapshot constraint in v1.
- `NEGATIVE_CORRELATION`: inverse directionally; no hard single-snapshot constraint in v1.
- `PROXY`: weak semantic proxy; no hard constraint.
- `AMBIGUOUS`: related wording that needs manual review before any hard constraint.

## Worked Examples

- `OpenAI first to AGI by 2027` implies `AGI announced by any company by 2027`.
- `OpenAI valuation at least $1T by 2027` is a subset of `OpenAI valuation at least $500B by 2027`.
- Two differently worded `OpenAI over $1T valuation before 2028` markets can be modeled as `SAME_EVENT_REWORDED`.
- `OpenAI first to AGI`, `Anthropic first to AGI`, and `Microsoft first to AGI` belong in an exclusion set, because at most one can be first under the same rules.

## Authoring Rules

- Use globally unique ids like `venue:native_id`.
- Put mutual exclusion in `exclusion_sets`, not pairwise edges.
- Include confidence, source, rationale, evidence snippets, and creation time.
- Use `AMBIGUOUS` when wording is not safe enough for a hard check.

## Logic Changes

Current v1 logic enforces only implication/subset/same-event/exclusion constraints and creates manual-review wording findings for ambiguous edges.

