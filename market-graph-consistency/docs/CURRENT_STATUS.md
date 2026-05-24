# Current Status

Last updated: 2026-05-20.

## Completed Work

- Added core dataclass models and validation.
- Added offline fixture loader for `venues/fixtures/*.json`.
- Added manual relationship registry for JSON-compatible YAML files.
- Added v1 checks for implication, subset/superset, same-event rewording, mutual-exclusion hyperedges, and ambiguous wording.
- Added deterministic LLM extractor stub.
- Added JSON and Markdown report writers.
- Added synthetic AI/private-company fixture cluster.
- Added tests covering the requested v1 acceptance surface.
- Enforced the unreviewed LLM-source confidence cap and `AMBIGUOUS_WORDING` action cap.
- Added strict Markdown golden report coverage.
- Added read-only schema-v1 saved snapshot loader prototype.
- Added `scan.py --snapshots-dir` and `--snapshot-file` CLI options.
- Added local schema-v1 snapshot loader tests.
- Markdown reports now derive scope/notes from `GraphSnapshot.notes`.
- JSON reports serialize `GraphSnapshot.notes`.
- Snapshot-mode no-usable-snapshots fallback is covered by tests.
- Generated reports in `reports/`.
- Hardened graph diagnostics with reference-only node handling, SAME_EVENT_REWORDED settlement-source proof gates, threshold-chain same-basis checks, stale-node blockers, LLM-source action caps, probability-only magnitude metadata, and report summaries for edge source/review/stale/reference-only status.
- Added graph-local relative-value hint exports at `reports/market_graph_relative_value_hints.json` and `reports/market_graph_relative_value_hints.md`; they are research-only hints, not evaluator inputs or permission for orders.
- Added the formal hint export schema at `schemas/relative_value_hint.schema.json`; future consumers should reject hint files that do not validate against it.
- Added structural fixture coverage for sports champion-to-conference implications, BTC threshold monotonicity with same source/window proof, complete and incomplete mutually exclusive groups, and downgrade cases.

## Commands Run

```powershell
python scan.py
python scan.py --snapshots-dir "../relative-value-scanner/reports"
python -m pytest -q
git checkout -- reports/graph_consistency_summary.json
Select-String -Path reports\graph_consistency_summary.* -Pattern 'TRADE|PAPER|POSSIBLE_ARB' -CaseSensitive
git status --short
git diff --stat
```

## Tests Run

```text
34 passed
```

## What Works

- `python scan.py` loads 7 fixture markets, 5 edges, and 1 exclusion set.
- `python scan.py --snapshots-dir "../relative-value-scanner/reports"` loaded 59 saved schema-v1 snapshot markets in read-only inspection mode during this run.
- The scan writes `reports/graph_consistency_summary.json`, `reports/graph_consistency_summary.md`, `reports/market_graph_consistency_diagnostics.json`, `reports/market_graph_consistency_diagnostics.md`, `reports/market_graph_relative_value_hints.json`, and `reports/market_graph_relative_value_hints.md`.
- `reports/market_graph_relative_value_hints.json` validates against `schemas/relative_value_hint.schema.json`; schema validation is a contract guard only and does not make graph hints executable.
- The fixture report contains known findings for implication, subset, rewording, exclusion-set sum, and ambiguous wording.
- Saved snapshot prototype mode intentionally loads no relationships and produced 0 findings in this run.
- Saved snapshot Markdown scope uses saved snapshot notes instead of fixture wording.
- JSON reports include snapshot notes.
- Highest report action is `MANUAL_REVIEW`.
- Markdown report highest action is computed from actual violations.
- LLM-source confidence cap and `AMBIGUOUS_WORDING` action cap are enforced.
- Report guardrail tests assert prohibited uppercase action tokens are absent from generated reports.
- Violation JSON is guarded against PnL/profit/dollar/fill/size/edge-bps/execution/promoted-action fields and reports magnitude in probability units only.
- Subset/superset findings are probability-bound diagnostics and are not exact same-payoff claims.
- Direct report guardrail command returned no matches.

## Stubbed Or Mocked

- LLM extraction is a deterministic interface stub.
- Backtest replay and metrics are placeholders.
- Semantic helpers are lightweight utilities only.
- Venue adapters are absent; bundled fixtures and saved snapshot files are the only sources.
- Saved snapshot mode converts normalized rows to internal `MarketNode` records only; no relationship extraction is performed.

## Known Limitations

- No live data, scheduler, DB, UI, snapshot history, transitive closure, embedding clustering, or real LLM calls.
- Positive/negative correlation and proxy relationships are documented but do not produce hard v1 price checks.
- Fixture prices are synthetic and cannot establish real market state.
- Saved schema-v1 snapshots are consumed as files only and do not prove semantic consistency without curated relationships.
- Any future evaluator integration must stay separate, explicit, schema-validated, and fail-closed on missing settlement/source/window proof.

## Blockers

None for the requested v1 acceptance criteria.

## Next Exact Command

```powershell
python -m pytest -q
```
