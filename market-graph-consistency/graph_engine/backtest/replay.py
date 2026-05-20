from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReplayPlan:
    snapshot_dir: Path
    phase: str


def build_replay_plan(snapshot_dir: Path | str, phase: str = "synthetic") -> ReplayPlan:
    return ReplayPlan(snapshot_dir=Path(snapshot_dir), phase=phase)


def run_replay(_: ReplayPlan) -> None:
    raise NotImplementedError("Backtest replay is documented but intentionally not implemented in v1.")

