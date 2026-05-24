# Market Graph Consistency

Lean, read-only scanner for semantic consistency across related prediction-market contracts.

This project represents markets as graph nodes and hand-curated semantic relationships as graph edges. It scans offline fixture snapshots for possible probability inconsistencies and produces review reports. It can also inspect saved schema-v1 normalized snapshot JSON files in read-only prototype mode.

## What This Is

- Fixture-based semantic market graph scanner.
- Read-only probability consistency checker.
- Read-only saved snapshot inspection prototype.
- Documentation-first prototype for future snapshot replay and relationship extraction.
- Manual review aid for related market clusters.

## What This Is Not

- Not a live trading system.
- Not an executor or account client.
- Not a source of direct recommendations.
- Not proof of guaranteed opportunity.
- Not coupled to sibling repositories.

## Quick Start

```powershell
python scan.py
python scan.py --snapshots-dir "../relative-value-scanner/reports"
python -m pytest -q
```

Default mode uses bundled fixtures and manual relationships. `--snapshots-dir` reads saved schema-v1 normalized snapshot JSON files from disk, does not import sibling code, and does not create relationships automatically.

The scan writes:

- `reports/graph_consistency_summary.json`
- `reports/graph_consistency_summary.md`

## Action Ladder

The highest action in this repository is `MANUAL_REVIEW`.

1. `IGNORE`: tiny, weak, or low-confidence signal.
2. `WATCH`: worth tracking in later snapshots or with better relationship evidence.
3. `MANUAL_REVIEW`: human should inspect wording, data freshness, and relationship assumptions.

Anything stronger belongs outside this repository.
