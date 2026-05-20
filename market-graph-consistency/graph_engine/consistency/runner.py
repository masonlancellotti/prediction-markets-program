from __future__ import annotations

from graph_engine.consistency.checks import (
    check_ambiguous_wording,
    check_exclusion_set,
    check_implication,
    check_same_event_reworded,
    check_subset,
)
from graph_engine.models import ConsistencyViolation, GraphSnapshot


def run_consistency_checks(snapshot: GraphSnapshot) -> list[ConsistencyViolation]:
    violations: list[ConsistencyViolation] = []

    for edge in snapshot.edges:
        for check in (
            check_implication,
            check_subset,
            check_same_event_reworded,
            check_ambiguous_wording,
        ):
            violation = check(snapshot, edge)
            if violation is not None:
                violations.append(violation)

    for exclusion in snapshot.exclusion_sets:
        violation = check_exclusion_set(snapshot, exclusion)
        if violation is not None:
            violations.append(violation)

    return sorted(
        violations,
        key=lambda item: (item.kind.value, -item.rank_score, item.violation_id),
    )

