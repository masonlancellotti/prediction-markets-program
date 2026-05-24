# Data Reality

V1 fixtures are synthetic and hand-authored. They are useful for exercising scanner logic, not for measuring real markets.

Saved schema-v1 normalized snapshots can now be read from disk in prototype mode. They are treated as already-collected files and do not trigger live ingestion.

## What Fixtures Can Prove

- Models validate probability ranges and timezone-aware timestamps.
- Loader handles fixture snapshots.
- Relationship registry validates market references.
- Consistency checks find known synthetic inconsistencies.
- Reports preserve structure and action guardrails.

## What Fixtures Cannot Prove

- Real venue data quality.
- Current market state.
- Fillability, fees, depth, or economic value.
- Correctness of qualitative resolution wording.
- Whether a relationship should be trusted in production.

## What Saved Snapshots Can Prove

- The project can parse schema-v1 normalized snapshot files without importing sibling code.
- Market rows can be converted into internal `MarketNode` records for inspection.
- Reports can be produced when no relationships are loaded.

## What Saved Snapshots Cannot Prove

- Semantic relationships between markets.
- Current venue state unless the files are fresh and documented.
- Cross-venue consistency without curated edges or exclusion sets.
- Any execution feasibility or account state.

## Qualitative-Market Risks

AI leadership, AGI, valuation, and revenue markets often differ by source, deadline, moderator discretion, and cancellation rules. Similar titles are not enough to enforce a hard relationship.

## Stale Data Rules

Each `MarketNode` carries `as_of` and `source_snapshot_id`. Future real snapshots should reject or down-rank comparisons when timestamps differ beyond a documented freshness window.

## Semantic Ambiguity

Use `AMBIGUOUS` when wording seems related but not safe enough for a hard constraint. Ambiguous edges produce wording-review findings only.

## LLM Limits

LLMs may later help classify wording, but they must not provide direct prices, probabilities, or direct recommendations. Human review remains required before promoting important relationships.
