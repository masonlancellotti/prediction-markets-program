from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayMetrics:
    convergence_rate: float | None = None
    time_to_convergence: float | None = None
    false_positive_rate: float | None = None

