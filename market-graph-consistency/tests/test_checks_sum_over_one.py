from __future__ import annotations

import pytest

from graph_engine.consistency.checks import check_exclusion_set
from graph_engine.models import ExclusionSet, GraphSnapshot, ViolationKind
from tests.conftest import make_node


def test_mutual_exclusion_sum_over_one_violation_on_hyperedge() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:a": make_node("test:a", 0.42),
            "test:b": make_node("test:b", 0.41),
            "test:c": make_node("test:c", 0.35),
        },
    )
    exclusion = ExclusionSet(
        set_id="exclusive_winner",
        member_market_ids=["test:a", "test:b", "test:c"],
        completeness="subset",
        tolerance=0.03,
    )

    violation = check_exclusion_set(snapshot, exclusion)

    assert violation is not None
    assert violation.kind == ViolationKind.SUM_OVER_ONE
    assert violation.raw_gap == pytest.approx(0.18)
