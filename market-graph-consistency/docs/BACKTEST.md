# Backtest Methodology

Backtesting is documented but not implemented in v1.

## Phase A: Synthetic Backtest

Replay handcrafted snapshots with known relationship and pricing states. Validate that each violation type triggers only when expected.

## Phase B: Real Snapshot Collection

Store read-only public snapshot files from venues or reports. No account credentials or execution features. Keep raw payloads for audit and freshness checks.

## Phase C: Historical Replay

Replay timestamped snapshots through the same graph checks. Compare finding persistence, convergence, and false positives by violation type.

## Metrics

- Convergence rate.
- Time-to-convergence.
- False-positive rate by violation type.
- Finding persistence across snapshots.
- Manual-review acceptance rate.

## Current Code

- `graph_engine/backtest/replay.py` defines a `ReplayPlan` and an intentional `NotImplementedError`.
- `graph_engine/backtest/metrics.py` defines a placeholder metrics dataclass.

