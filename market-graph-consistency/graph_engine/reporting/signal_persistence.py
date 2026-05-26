from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


BANNER = (
    "Saved-file-only market graph signal persistence report. It compares diagnostic rows across "
    "saved reports and does not affect evaluator gates."
)
WHY_REVIEW_ONLY = (
    "Persistence is a review-priority diagnostic only; relationship evidence, payoff checks, "
    "freshness, fees, and depth must be independently reviewed outside this report."
)
PERSISTENCE_STATUSES = {
    "NEW_SIGNAL",
    "PERSISTENT_SIGNAL",
    "WORSENED_SIGNAL",
    "IMPROVED_SIGNAL",
    "RESOLVED_SIGNAL",
    "MISSING_PREVIOUS_BASELINE",
}
HIGH_CONFIDENCE_VALUES = {"HIGH", "high"}
SEVERITY_EPSILON = 1e-9


def build_signal_persistence_report(
    current_reports: list[dict[str, Any]],
    previous_reports: list[dict[str, Any]] | None = None,
    previous_persistence_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_items = _items_by_key(_normalized_items_from_reports(current_reports))
    history_items = _items_by_key(_items_from_previous_persistence(previous_persistence_report))
    previous_items = _items_by_key(_normalized_items_from_reports(previous_reports or []))
    if not previous_items:
        previous_items = history_items

    rows: list[dict[str, Any]] = []
    has_previous_baseline = bool(previous_items)
    all_keys = set(current_items) | set(previous_items)
    for key in sorted(all_keys):
        current = current_items.get(key)
        previous = previous_items.get(key)
        previous_history_count = 0
        if key in history_items:
            previous_history_count = int(history_items[key].get("persistence_count") or 0)
        if current is None and previous is not None:
            rows.append(_resolved_row(previous, previous_history_count))
            continue
        if current is None:
            continue
        if not has_previous_baseline:
            rows.append(_current_row(current, None, "MISSING_PREVIOUS_BASELINE", 1))
            continue
        if previous is None:
            rows.append(_current_row(current, None, "NEW_SIGNAL", 1))
            continue
        status = _status_from_severity(current["severity"], previous["severity"])
        rows.append(_current_row(current, previous, status, max(1, previous_history_count) + 1))

    rows = sorted(rows, key=lambda row: (_status_priority(row["persistence_status"]), -abs(row["severity_delta"]), row["signal_key"]))
    for index, row in enumerate(rows, start=1):
        row["diagnostic_rank"] = index

    summary = _summary(rows, len(current_items))
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "summary": summary,
        "signal_persistence_rows": rows,
        "current_signal_snapshots": [
            _snapshot_item(item, rows_by_key={row["signal_key"]: row for row in rows})
            for item in sorted(current_items.values(), key=lambda item: item["signal_key"])
        ],
    }
    validate_signal_persistence_report(report)
    return report


def write_signal_persistence_report(
    current_paths: list[Path | str],
    previous_paths: list[Path | str] | None,
    json_output: Path | str,
    markdown_output: Path | str,
    *,
    previous_persistence_path: Path | str | None = None,
) -> dict[str, Any]:
    previous_persistence_report = _load_optional_json(previous_persistence_path)
    current_reports = _load_required_reports(current_paths)
    previous_reports = _load_optional_reports(previous_paths or [])
    report = build_signal_persistence_report(
        current_reports,
        previous_reports,
        previous_persistence_report=previous_persistence_report,
    )
    validate_signal_persistence_report(report)
    markdown = render_signal_persistence_markdown(report)
    hits = find_prohibited_rendered_text(markdown)
    if hits:
        raise SchemaValidationError("signal persistence Markdown contains prohibited vocabulary: " + ", ".join(hits))

    json_path = Path(json_output)
    md_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report


def render_signal_persistence_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Market Graph Signal Persistence",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Total current: {summary['total_current']}",
        f"- New: {summary['new_count']}",
        f"- Persistent: {summary['persistent_count']}",
        f"- Worsened: {summary['worsened_count']}",
        f"- Improved: {summary['improved_count']}",
        f"- Resolved: {summary['resolved_count']}",
        f"- Missing previous baseline: {summary['missing_previous_baseline_count']}",
        "",
    ]
    lines.extend(_markdown_table("Top Worsening Signals", summary["top_worsening_signals"]))
    lines.extend(_markdown_table("Top Persistent High-Confidence Signals", summary["top_persistent_high_confidence_signals"]))
    lines.extend(
        [
            "## Persistence Rows",
            "",
            "| Rank | Status | Type | Current Severity | Previous Severity | Delta | Confidence | Count | Markets |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["signal_persistence_rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["diagnostic_rank"]),
                    _md(row["persistence_status"]),
                    _md(row["signal_type"]),
                    _md(row["current_severity"]),
                    _md(row["previous_severity"]),
                    _md(row["severity_delta"]),
                    _md(row["current_confidence"]),
                    _md(row["persistence_count"]),
                    _md(", ".join(row["markets_involved"])),
                ]
            )
            + " |"
        )
    if not report["signal_persistence_rows"]:
        lines.append("| none |  |  |  |  |  |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def validate_signal_persistence_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("signal persistence report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("signal persistence report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("signal persistence actions must be WATCH and MANUAL_REVIEW only")
    rows = report.get("signal_persistence_rows")
    if not isinstance(rows, list):
        raise SchemaValidationError("signal_persistence_rows must be a list")
    snapshots = report.get("current_signal_snapshots")
    if not isinstance(snapshots, list):
        raise SchemaValidationError("current_signal_snapshots must be a list")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("summary must be an object")
    for key in [
        "total_current",
        "new_count",
        "persistent_count",
        "worsened_count",
        "improved_count",
        "resolved_count",
        "missing_previous_baseline_count",
    ]:
        if not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool):
            raise SchemaValidationError(f"summary.{key} must be an integer")
    if summary["total_current"] != len(snapshots):
        raise SchemaValidationError("summary.total_current must match current_signal_snapshots")
    for section in ["top_worsening_signals", "top_persistent_high_confidence_signals"]:
        if not isinstance(summary.get(section), list):
            raise SchemaValidationError(f"summary.{section} must be a list")
        for index, row in enumerate(summary[section]):
            _validate_top_row(row, f"summary.{section}[{index}]")
    for index, row in enumerate(rows):
        _validate_persistence_row(row, f"signal_persistence_rows[{index}]")
    for index, row in enumerate(snapshots):
        _validate_snapshot_row(row, f"current_signal_snapshots[{index}]")


def _load_required_reports(paths: list[Path | str]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SchemaValidationError(f"{path} must contain a JSON object")
        reports.append(payload)
    if not reports:
        raise SchemaValidationError("at least one current report is required")
    return reports


def _load_optional_reports(paths: list[Path | str]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for path in paths:
        payload = _load_optional_json(path)
        if isinstance(payload, dict):
            reports.append(payload)
    return reports


def _load_optional_json(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _normalized_items_from_reports(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for report in reports:
        if isinstance(report.get("signals"), list):
            items.extend(_items_from_indicator_report(report))
        if isinstance(report.get("probability_constraints"), list):
            items.extend(_items_from_probability_constraints(report))
        if isinstance(report.get("payoff_state_feasibility_bridge"), list):
            items.extend(_items_from_payoff_bridge(report))
    return items


def _items_from_indicator_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in report.get("signals", []):
        if not isinstance(row, dict):
            continue
        items.append(
            _normalized_item(
                source_kind="indicator_report",
                item_id=str(row.get("signal_id") or ""),
                signal_type=str(row.get("signal_type") or "UNKNOWN_SIGNAL"),
                markets=row.get("markets_involved") or [],
                evidence=str(row.get("relationship_evidence_type") or ""),
                severity=_number(row.get("severity_score")),
                confidence=row.get("confidence_tier"),
                gap=None,
                blockers=row.get("review_blockers") or [],
            )
        )
    return items


def _items_from_probability_constraints(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in report.get("probability_constraints", []):
        if not isinstance(row, dict):
            continue
        items.append(
            _normalized_item(
                source_kind="probability_constraint_report",
                item_id=str(row.get("constraint_id") or ""),
                signal_type=str(row.get("constraint_type") or "UNKNOWN_CONSTRAINT"),
                markets=row.get("markets_involved") or [],
                evidence=str(row.get("inequality_checked") or row.get("evidence_basis") or ""),
                severity=_number(row.get("severity_score")),
                confidence=row.get("confidence_tier"),
                gap=_optional_number(row.get("observed_gap")),
                blockers=row.get("review_blockers") or [],
            )
        )
    return items


def _items_from_payoff_bridge(report: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in report.get("payoff_state_feasibility_bridge", []):
        if not isinstance(row, dict):
            continue
        constraint_types = ",".join(sorted(str(value) for value in row.get("constraint_types_represented") or []))
        status = str(row.get("feasibility_status") or "UNKNOWN_FEASIBILITY")
        items.append(
            _normalized_item(
                source_kind="payoff_state_feasibility_bridge",
                item_id=str(row.get("state_family_id") or row.get("bridge_id") or ""),
                signal_type=constraint_types or status,
                markets=row.get("markets_involved") or [],
                evidence=status,
                severity=round(_number(row.get("infeasibility_gap")) * 100.0, 6),
                confidence=_confidence_from_basis(row.get("confidence_basis")),
                gap=_optional_number(row.get("infeasibility_gap")),
                blockers=row.get("review_blockers") or [],
            )
        )
    return items


def _items_from_previous_persistence(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    snapshots = report.get("current_signal_snapshots")
    if not isinstance(snapshots, list):
        return []
    items: list[dict[str, Any]] = []
    for row in snapshots:
        if not isinstance(row, dict):
            continue
        item = {
            "signal_key": row.get("signal_key"),
            "source_kind": row.get("source_kind"),
            "item_id": row.get("item_id"),
            "signal_type": row.get("signal_type"),
            "markets_involved": row.get("markets_involved") or [],
            "relationship_evidence_type": row.get("relationship_evidence_type") or "",
            "severity": _number(row.get("current_severity")),
            "confidence": row.get("current_confidence"),
            "gap": _optional_number(row.get("current_gap")),
            "review_blockers": row.get("review_blockers") or [],
            "persistence_count": int(row.get("persistence_count") or 1),
        }
        if isinstance(item["signal_key"], str) and item["signal_key"]:
            items.append(item)
    return items


def _normalized_item(
    *,
    source_kind: str,
    item_id: str,
    signal_type: str,
    markets: list[Any],
    evidence: str,
    severity: float,
    confidence: Any,
    gap: float | None,
    blockers: list[Any],
) -> dict[str, Any]:
    market_ids = sorted(str(market_id) for market_id in markets)
    key = _signal_key(
        source_kind=source_kind,
        signal_type=signal_type,
        markets=market_ids,
        evidence=evidence,
        item_id=item_id,
    )
    return {
        "signal_key": key,
        "source_kind": source_kind,
        "item_id": item_id,
        "signal_type": signal_type,
        "markets_involved": market_ids,
        "relationship_evidence_type": evidence,
        "severity": round(severity, 6),
        "confidence": str(confidence) if confidence is not None else None,
        "gap": gap,
        "review_blockers": sorted(str(blocker) for blocker in blockers),
    }


def _signal_key(
    *,
    source_kind: str,
    signal_type: str,
    markets: list[str],
    evidence: str,
    item_id: str,
) -> str:
    return "|".join(
        [
            source_kind,
            signal_type,
            item_id,
            evidence,
            ",".join(sorted(markets)),
        ]
    )


def _items_by_key(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["signal_key"]: item for item in items if isinstance(item.get("signal_key"), str) and item["signal_key"]}


def _status_from_severity(current: float, previous: float) -> str:
    delta = current - previous
    if delta > SEVERITY_EPSILON:
        return "WORSENED_SIGNAL"
    if delta < -SEVERITY_EPSILON:
        return "IMPROVED_SIGNAL"
    return "PERSISTENT_SIGNAL"


def _current_row(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    status: str,
    persistence_count: int,
) -> dict[str, Any]:
    previous_severity = previous["severity"] if previous is not None else None
    current_gap = current["gap"]
    previous_gap = previous["gap"] if previous is not None else None
    row = {
        "signal_key": current["signal_key"],
        "persistence_status": status,
        "source_kind": current["source_kind"],
        "item_id": current["item_id"],
        "signal_type": current["signal_type"],
        "markets_involved": list(current["markets_involved"]),
        "relationship_evidence_type": current["relationship_evidence_type"],
        "current_severity": current["severity"],
        "previous_severity": previous_severity,
        "severity_delta": round(current["severity"] - (previous_severity or 0.0), 6) if previous is not None else 0.0,
        "current_confidence": current["confidence"],
        "previous_confidence": previous["confidence"] if previous is not None else None,
        "current_gap": current_gap,
        "previous_gap": previous_gap,
        "persistence_count": persistence_count,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "review_blockers": list(current["review_blockers"]),
        "why_review_only_yet": WHY_REVIEW_ONLY,
    }
    _validate_persistence_row(row, "signal_persistence_rows[]")
    return row


def _resolved_row(previous: dict[str, Any], previous_history_count: int) -> dict[str, Any]:
    row = {
        "signal_key": previous["signal_key"],
        "persistence_status": "RESOLVED_SIGNAL",
        "source_kind": previous["source_kind"],
        "item_id": previous["item_id"],
        "signal_type": previous["signal_type"],
        "markets_involved": list(previous["markets_involved"]),
        "relationship_evidence_type": previous["relationship_evidence_type"],
        "current_severity": 0.0,
        "previous_severity": previous["severity"],
        "severity_delta": round(0.0 - previous["severity"], 6),
        "current_confidence": None,
        "previous_confidence": previous["confidence"],
        "current_gap": None,
        "previous_gap": previous["gap"],
        "persistence_count": max(1, previous_history_count),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "review_blockers": list(previous["review_blockers"]),
        "why_review_only_yet": WHY_REVIEW_ONLY,
    }
    _validate_persistence_row(row, "signal_persistence_rows[]")
    return row


def _snapshot_item(item: dict[str, Any], *, rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row = rows_by_key.get(item["signal_key"], {})
    return {
        "signal_key": item["signal_key"],
        "source_kind": item["source_kind"],
        "item_id": item["item_id"],
        "signal_type": item["signal_type"],
        "markets_involved": list(item["markets_involved"]),
        "relationship_evidence_type": item["relationship_evidence_type"],
        "current_severity": item["severity"],
        "current_confidence": item["confidence"],
        "current_gap": item["gap"],
        "persistence_count": row.get("persistence_count", 1),
        "review_blockers": list(item["review_blockers"]),
    }


def _summary(rows: list[dict[str, Any]], total_current: int) -> dict[str, Any]:
    return {
        "total_current": total_current,
        "new_count": _count_status(rows, "NEW_SIGNAL"),
        "persistent_count": _count_status(rows, "PERSISTENT_SIGNAL"),
        "worsened_count": _count_status(rows, "WORSENED_SIGNAL"),
        "improved_count": _count_status(rows, "IMPROVED_SIGNAL"),
        "resolved_count": _count_status(rows, "RESOLVED_SIGNAL"),
        "missing_previous_baseline_count": _count_status(rows, "MISSING_PREVIOUS_BASELINE"),
        "top_worsening_signals": _top_worsening(rows),
        "top_persistent_high_confidence_signals": _top_persistent_high_confidence(rows),
    }


def _count_status(rows: list[dict[str, Any]], status: str) -> int:
    return sum(1 for row in rows if row["persistence_status"] == status)


def _status_priority(status: str) -> int:
    return {
        "WORSENED_SIGNAL": 0,
        "NEW_SIGNAL": 1,
        "PERSISTENT_SIGNAL": 2,
        "IMPROVED_SIGNAL": 3,
        "RESOLVED_SIGNAL": 4,
        "MISSING_PREVIOUS_BASELINE": 5,
    }.get(status, 9)


def _top_worsening(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["persistence_status"] == "WORSENED_SIGNAL"]
    ranked = sorted(candidates, key=lambda row: (-row["severity_delta"], row["signal_key"]))
    return [_top_summary(row) for row in ranked[:limit]]


def _top_persistent_high_confidence(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if row["persistence_status"] in {"PERSISTENT_SIGNAL", "WORSENED_SIGNAL"}
        and row.get("current_confidence") in HIGH_CONFIDENCE_VALUES
    ]
    ranked = sorted(candidates, key=lambda row: (-row["current_severity"], row["signal_key"]))
    return [_top_summary(row) for row in ranked[:limit]]


def _top_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_key": row["signal_key"],
        "persistence_status": row["persistence_status"],
        "signal_type": row["signal_type"],
        "markets_involved": list(row["markets_involved"]),
        "current_severity": row["current_severity"],
        "previous_severity": row["previous_severity"],
        "severity_delta": row["severity_delta"],
        "current_confidence": row["current_confidence"],
        "diagnostic_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
    }


def _confidence_from_basis(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    score = _optional_number(value.get("score"))
    if score is None:
        return None
    if score >= 0.75:
        return "HIGH"
    if score >= 0.4:
        return "MEDIUM"
    return "LOW"


def _number(value: Any) -> float:
    numeric = _optional_number(value)
    return numeric if numeric is not None else 0.0


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return None


def _validate_persistence_row(row: dict[str, Any], path: str) -> None:
    required = [
        "signal_key",
        "persistence_status",
        "source_kind",
        "item_id",
        "signal_type",
        "markets_involved",
        "relationship_evidence_type",
        "current_severity",
        "previous_severity",
        "severity_delta",
        "current_confidence",
        "previous_confidence",
        "current_gap",
        "previous_gap",
        "persistence_count",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
        "review_blockers",
        "why_review_only_yet",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["persistence_status"] not in PERSISTENCE_STATUSES:
        raise SchemaValidationError(f"{path}.persistence_status is not supported")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(row["markets_involved"], list):
        raise SchemaValidationError(f"{path}.markets_involved must be a list")
    if not isinstance(row["review_blockers"], list):
        raise SchemaValidationError(f"{path}.review_blockers must be a list")
    if not isinstance(row["persistence_count"], int) or isinstance(row["persistence_count"], bool) or row["persistence_count"] < 1:
        raise SchemaValidationError(f"{path}.persistence_count must be a positive integer")
    for key in ["current_severity", "severity_delta"]:
        if not isinstance(row[key], (int, float)) or isinstance(row[key], bool):
            raise SchemaValidationError(f"{path}.{key} must be numeric")
    for key in ["previous_severity", "current_gap", "previous_gap"]:
        if row[key] is not None and (not isinstance(row[key], (int, float)) or isinstance(row[key], bool)):
            raise SchemaValidationError(f"{path}.{key} must be numeric or null")
    _reject_prohibited_tokens(row)


def _validate_snapshot_row(row: dict[str, Any], path: str) -> None:
    required = [
        "signal_key",
        "source_kind",
        "item_id",
        "signal_type",
        "markets_involved",
        "relationship_evidence_type",
        "current_severity",
        "current_confidence",
        "current_gap",
        "persistence_count",
        "review_blockers",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if not isinstance(row["signal_key"], str) or not row["signal_key"]:
        raise SchemaValidationError(f"{path}.signal_key must be a non-empty string")
    if not isinstance(row["markets_involved"], list):
        raise SchemaValidationError(f"{path}.markets_involved must be a list")
    if not isinstance(row["review_blockers"], list):
        raise SchemaValidationError(f"{path}.review_blockers must be a list")
    if not isinstance(row["persistence_count"], int) or isinstance(row["persistence_count"], bool) or row["persistence_count"] < 1:
        raise SchemaValidationError(f"{path}.persistence_count must be a positive integer")
    _reject_prohibited_tokens(row)


def _validate_top_row(row: dict[str, Any], path: str) -> None:
    required = [
        "signal_key",
        "persistence_status",
        "signal_type",
        "markets_involved",
        "current_severity",
        "previous_severity",
        "severity_delta",
        "current_confidence",
        "diagnostic_only",
        "allowed_actions",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    _reject_prohibited_tokens(row)


def _markdown_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Status | Type | Current | Previous | Delta | Confidence | Markets |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["persistence_status"]),
                    _md(row["signal_type"]),
                    _md(row["current_severity"]),
                    _md(row["previous_severity"]),
                    _md(row["severity_delta"]),
                    _md(row["current_confidence"]),
                    _md(", ".join(row["markets_involved"])),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "BANNER",
    "PERSISTENCE_STATUSES",
    "build_signal_persistence_report",
    "render_signal_persistence_markdown",
    "validate_signal_persistence_report",
    "write_signal_persistence_report",
]
