# Architecture

This repository is a lean, offline semantic consistency scanner.

## Flow

1. `scan.py` loads fixture markets from `venues/fixtures/`.
2. `graph_engine.loader` builds a `GraphSnapshot`.
3. `graph_engine.relationships.registry` loads manual relationship files from `relationships/`.
4. `graph_engine.consistency.runner` applies v1 checks.
5. `graph_engine.reporting` writes JSON and Markdown reports to `reports/`.
6. `graph_engine.reporting.hints` writes a graph-local relative-value hints artifact for later research review only.

Saved snapshot prototype mode uses `scan.py --snapshots-dir` or `--snapshot-file` to load schema-v1 normalized snapshot JSON files through `graph_engine.snapshot_loader`. It does not load manual fixture relationships or infer new relationships.

## Module Responsibilities

- `graph_engine/models.py`: dataclasses, enums, validation, serialization.
- `graph_engine/loader.py`: fixture-only market loading and snapshot construction.
- `graph_engine/snapshot_loader.py`: read-only schema-v1 saved snapshot loading.
- `graph_engine/relationships/types.py`: relationship model exports.
- `graph_engine/relationships/registry.py`: relationship file loading and market-id validation.
- `graph_engine/relationships/confidence.py`: simple confidence math.
- `graph_engine/relationships/llm_extractor.py`: deterministic offline interface stub.
- `graph_engine/consistency/checks.py`: implication, subset, rewording, exclusion, and ambiguous-wording checks.
- `graph_engine/consistency/tolerances.py`: v1 tolerances and action ladder.
- `graph_engine/reporting/json_report.py`: structured report writer.
- `graph_engine/reporting/md_report.py`: human-readable grouped report writer.
- `graph_engine/reporting/hints.py`: research-only graph hint export; not an evaluator integration.
- `graph_engine/semantics/`: minimal future helper functions.
- `graph_engine/backtest/`: documented placeholders for replay work.

## Invariants

- Offline by default and currently offline only.
- Fixtures are the only market source.
- Relationship files are hand-authored.
- Highest action is `MANUAL_REVIEW`.
- No account, execution, private API, scheduler, DB, or cross-repo imports.
- Graph contradictions and hints are not permission for orders or promoted candidate labels.

## Known Limits

V1 does not infer relationships, handle transitive closure, fetch live prices, or prove economic opportunity. It only flags fixture inconsistencies for human inspection.
