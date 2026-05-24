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
- Sports champion markets can imply conference or league championship markets in one direction only; the reverse is not valid.
- BTC above a higher threshold implies BTC above a lower threshold only when observable, settlement source, and window are all proven the same.
- Two differently worded `OpenAI over $1T valuation before 2028` markets can be modeled as `SAME_EVENT_REWORDED`.
- `OpenAI first to AGI`, `Anthropic first to AGI`, and `Microsoft first to AGI` belong in an exclusion set, because at most one can be first under the same rules.

## Authoring Rules

- Use globally unique ids like `venue:native_id`.
- Put mutual exclusion in `exclusion_sets`, not pairwise edges.
- Include confidence, source, rationale, evidence snippets, and creation time.
- Use `AMBIGUOUS` when wording is not safe enough for a hard check.
- Do not treat subset/superset edges as exact same-payoff.

## Logic Changes

Current v1 logic enforces only implication/subset/same-event/exclusion constraints and creates manual-review wording findings for ambiguous edges.

## Typed Formula Notes

- A future LLM may propose `MarketFormula` JSON for text or text/number markets, but it must not be trusted directly.
- LLM output is limited to proposed JSON; there is no direct promotion from text similarity or model output into graph relationships.
- The deterministic validator must verify required fields such as family, subject or asset, source, date or meeting date, comparator, thresholds or ranges, units, side, parse quality, and blockers.
- The deterministic validator is mandatory before proposed formulas can feed graph-local formula diagnostics.
- Formula comparison output remains graph-local diagnostics capped to `WATCH` or `MANUAL_REVIEW`.
- Similar titles are not enough for trusted equality. Typed formula matches remain review-only in this project.
- Any relative-value evaluator must independently prove exact same-payoff before producing candidate labels in its own repository.
