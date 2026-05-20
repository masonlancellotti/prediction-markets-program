# Codex Handoff

Future agents should read these first:

1. `README.md`
2. `docs/CURRENT_STATUS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/RELATIONSHIPS.md`
5. `docs/VIOLATIONS.md`
6. `docs/DATA_REALITY.md`
7. `docs/NOT_YET.md`

## Hard Rules

- Work only inside `market-graph-consistency`.
- Keep this project read-only and fixture-based unless the docs and tests are intentionally changed.
- Do not import sibling repository internals.
- Do not add account credentials, execution, live adapters, private APIs, or real API calls in tests.
- Highest action remains `MANUAL_REVIEW`.
- LLM behavior must remain optional, structured, auditable, and never a source of direct prices or direct recommendations.

## Invariants

- Every market probability must be in `[0, 1]`.
- Every market timestamp must be timezone-aware.
- Every relationship must validate referenced market ids.
- Mutual exclusion is a hyperedge (`ExclusionSet`), not pairwise.
- Reports must keep action labels inside the `IGNORE` / `WATCH` / `MANUAL_REVIEW` ladder.

## Commands Run

```powershell
python scan.py
python scan.py --snapshots-dir "../relative-value-scanner/reports"
python -m pytest -q
git checkout -- reports/graph_consistency_summary.json
Select-String -Path reports\graph_consistency_summary.* -Pattern 'TRADE|PAPER|POSSIBLE_ARB' -CaseSensitive
```

## Test Status

```text
34 passed
```

## What Works

- Offline fixture scan.
- Manual relationship loading.
- V1 consistency checks.
- JSON and Markdown reports.
- Report guardrail tests.
- LLM-source confidence cap and AMBIGUOUS_WORDING action cap enforced.
- Read-only schema-v1 saved snapshot loader prototype.
- Snapshot reports use `GraphSnapshot.notes`; JSON reports serialize notes.
- No-usable-snapshots fallback path is tested.

## Stubbed Or Mocked

- Deterministic LLM extractor.
- Backtest replay and metrics.
- Live venue adapters.
- Saved snapshot mode maps rows to `MarketNode` records only and does not create relationships.

## Current Limitations

- Synthetic fixtures only.
- No historical replay yet.
- No real semantic extraction.
- No hard checks for correlation/proxy relationships.
- Saved snapshot mode has no automatic relationship discovery.

## Current Next Task

Use saved schema-v1 snapshots only for read-only inspection until curated relationships exist for those market ids.

## Next Exact Command

```powershell
python -m pytest -q
```
