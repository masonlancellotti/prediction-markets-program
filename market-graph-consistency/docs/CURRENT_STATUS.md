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
- The scan writes `reports/graph_consistency_summary.json` and `reports/graph_consistency_summary.md`.
- The fixture report contains known findings for implication, subset, rewording, exclusion-set sum, and ambiguous wording.
- Saved snapshot prototype mode intentionally loads no relationships and produced 0 findings in this run.
- Saved snapshot Markdown scope uses saved snapshot notes instead of fixture wording.
- JSON reports include snapshot notes.
- Highest report action is `MANUAL_REVIEW`.
- Markdown report highest action is computed from actual violations.
- LLM-source confidence cap and `AMBIGUOUS_WORDING` action cap are enforced.
- Report guardrail tests assert prohibited uppercase action tokens are absent from generated reports.
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

## Blockers

None for the requested v1 acceptance criteria.

## Next Exact Command

```powershell
python -m pytest -q
```
