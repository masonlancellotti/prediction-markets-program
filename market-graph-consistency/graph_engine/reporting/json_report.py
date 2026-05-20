from __future__ import annotations

import json
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

from graph_engine.models import ConsistencyViolation, GraphSnapshot, utc_now


def build_json_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    fixture_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    kind_counts = Counter(violation.kind.value for violation in violations)
    action_counts = Counter(violation.action.value for violation in violations)
    return {
        "generated_at": utc_now().astimezone(timezone.utc).isoformat(),
        "snapshot_id": snapshot.snapshot_id,
        "notes": list(snapshot.notes),
        "summary": {
            "market_count": len(snapshot.nodes),
            "edge_count": len(snapshot.edges),
            "exclusion_set_count": len(snapshot.exclusion_sets),
            "violation_count": len(violations),
            "counts_by_kind": dict(sorted(kind_counts.items())),
            "counts_by_action": dict(sorted(action_counts.items())),
            "highest_action": "MANUAL_REVIEW" if any(v.action.value == "MANUAL_REVIEW" for v in violations) else "WATCH" if violations else "IGNORE",
        },
        "violations": [violation.to_dict() for violation in violations],
        "source_fixture_metadata": fixture_metadata or [],
    }


def write_json_report(
    snapshot: GraphSnapshot,
    violations: list[ConsistencyViolation],
    path: Path | str,
    fixture_metadata: list[dict[str, Any]] | None = None,
) -> None:
    report = build_json_report(snapshot, violations, fixture_metadata)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
