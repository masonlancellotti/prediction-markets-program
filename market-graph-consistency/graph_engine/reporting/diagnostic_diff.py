from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.reporting.json_report import _assert_safe_violation_schema
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
    validate_formula_diagnostics_contract,
    validate_multi_leg_constraints_contract,
)


BANNER = "Diagnostic-only saved-file graph diagnostic diff. It does not affect evaluator gates."
COMPARED_FIELDS = ["bound_gap", "diagnostic_priority", "blockers", "formula_relation"]


def load_validated_diagnostic_report(path: Path | str) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    _assert_saved_file_report(report)
    return report


def build_diagnostic_diff_report(old_report: dict[str, Any], new_report: dict[str, Any]) -> dict[str, Any]:
    _assert_saved_file_report(old_report)
    _assert_saved_file_report(new_report)
    old_items = _items_by_id(old_report)
    new_items = _items_by_id(new_report)
    old_ids = set(old_items)
    new_ids = set(new_items)

    added = [_summary(new_items[item_id]) for item_id in sorted(new_ids - old_ids)]
    removed = [_summary(old_items[item_id]) for item_id in sorted(old_ids - new_ids)]
    changed = []
    field_changes = []
    for item_id in sorted(old_ids & new_ids):
        changes = _field_changes(item_id, old_items[item_id], new_items[item_id])
        if not changes:
            continue
        field_changes.extend(changes)
        row = _summary(new_items[item_id])
        row["field_changes"] = changes
        changed.append(row)

    counts = Counter(change["field"] for change in field_changes)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "old_snapshot_id": old_report.get("snapshot_id", "unknown"),
        "new_snapshot_id": new_report.get("snapshot_id", "unknown"),
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
            "unchanged_count": len(old_ids & new_ids) - len(changed),
            "changes_by_field": dict(sorted(counts.items())),
        },
        "added_constraints": added,
        "removed_constraints": removed,
        "changed_constraints": changed,
        "field_changes": field_changes,
    }
    validate_diagnostic_diff_contract(report)
    return report


def write_diagnostic_diff_report(
    old_path: Path | str,
    new_path: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
) -> dict[str, Any]:
    old_report = load_validated_diagnostic_report(old_path)
    new_report = load_validated_diagnostic_report(new_path)
    report = build_diagnostic_diff_report(old_report, new_report)
    validate_diagnostic_diff_contract(report)

    json_path = Path(json_output)
    md_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_diagnostic_diff_markdown(report), encoding="utf-8")
    return report


def render_diagnostic_diff_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Market Graph Diagnostic Diff",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Old snapshot: `{report['old_snapshot_id']}`",
        f"- New snapshot: `{report['new_snapshot_id']}`",
        f"- Added constraints: {summary['added_count']}",
        f"- Removed constraints: {summary['removed_count']}",
        f"- Changed constraints: {summary['changed_count']}",
        f"- Unchanged constraints: {summary['unchanged_count']}",
        "",
    ]
    lines.extend(_table("Added Constraints", report["added_constraints"]))
    lines.extend(_table("Removed Constraints", report["removed_constraints"]))
    lines.extend(_table("Changed Constraints", report["changed_constraints"]))
    lines.extend(["## Field Changes", "", "| Constraint | Field | Old | New |", "| --- | --- | --- | --- |"])
    if not report["field_changes"]:
        lines.append("| none |  |  |  |")
    for change in report["field_changes"]:
        lines.append(
            f"| {_md(change['constraint_id'])} | {_md(change['field'])} | {_md(_value(change['old_value']))} | {_md(_value(change['new_value']))} |"
        )
    lines.extend(["", "This diff compares saved diagnostic files only.", ""])
    return "\n".join(lines)


def validate_diagnostic_diff_contract(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    _assert_safe_violation_schema(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("diagnostic diff must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("diagnostic diff must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("diagnostic diff actions must be WATCH and MANUAL_REVIEW only")
    for section in ["added_constraints", "removed_constraints", "changed_constraints"]:
        rows = report.get(section)
        if not isinstance(rows, list):
            raise SchemaValidationError(f"{section} must be a list")
        for index, row in enumerate(rows):
            _validate_summary_row(row, f"{section}[{index}]")
    for index, change in enumerate(report.get("field_changes", [])):
        path = f"field_changes[{index}]"
        if change.get("field") not in COMPARED_FIELDS:
            raise SchemaValidationError(f"{path}.field is not supported")
        if not isinstance(change.get("constraint_id"), str) or not change["constraint_id"]:
            raise SchemaValidationError(f"{path}.constraint_id must be a non-empty string")


def _assert_saved_file_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    _assert_safe_violation_schema(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("diagnostic report must be diagnostic_only")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("diagnostic report actions must be WATCH and MANUAL_REVIEW only")
    if "multi_leg_constraints" in report:
        validate_multi_leg_constraints_contract(report["multi_leg_constraints"])
    if "formula_diagnostics" in report:
        validate_formula_diagnostics_contract(report["formula_diagnostics"])


def _items_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for row in report.get("multi_leg_constraints", {}).get("multi_leg_constraints", []):
        key = f"multi_leg:{row['constraint_id']}"
        items[key] = _normalized_item(key, "multi_leg_constraint", row)
    for row in report.get("formula_diagnostics", {}).get("formula_diagnostics", []):
        key = f"formula_relation:{row['comparison_id']}"
        items[key] = _normalized_item(key, "formula_diagnostic", row)
    for row in report.get("formula_diagnostics", {}).get("formula_cluster_constraints", []):
        key = f"formula_cluster:{row['constraint_id']}"
        items[key] = _normalized_item(key, "formula_cluster_constraint", row)
    return items


def _normalized_item(item_id: str, item_type: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "constraint_id": item_id,
        "constraint_type": item_type,
        "diagnostic_only": True,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "source_market_ids": _market_ids(row),
        "bound_gap": row.get("bound_gap"),
        "diagnostic_priority": row.get("diagnostic_priority") or row.get("max_action_cap"),
        "blockers": list(row.get("blockers", [])),
        "formula_relation": row.get("formula_relation"),
        "reason_for_review": row.get("review_reason") or row.get("reason_for_review", ""),
    }


def _market_ids(row: dict[str, Any]) -> list[str]:
    return list(row.get("market_ids") or row.get("source_market_ids") or [])


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "constraint_id": item["constraint_id"],
        "constraint_type": item["constraint_type"],
        "diagnostic_only": True,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "source_market_ids": list(item["source_market_ids"]),
        "bound_gap": item["bound_gap"],
        "diagnostic_priority": item["diagnostic_priority"],
        "blockers": list(item["blockers"]),
        "formula_relation": item["formula_relation"],
        "reason_for_review": item["reason_for_review"],
    }


def _field_changes(item_id: str, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for field in COMPARED_FIELDS:
        old_value = _normalized_value(old.get(field))
        new_value = _normalized_value(new.get(field))
        if old_value == new_value:
            continue
        changes.append(
            {
                "constraint_id": item_id,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


def _validate_summary_row(row: dict[str, Any], path: str) -> None:
    if row.get("diagnostic_only") is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(row.get("constraint_id"), str) or not row["constraint_id"]:
        raise SchemaValidationError(f"{path}.constraint_id must be a non-empty string")
    if row.get("diagnostic_priority") not in {None, *DIAGNOSTIC_HINT_ACTIONS}:
        raise SchemaValidationError(f"{path}.diagnostic_priority must be WATCH or MANUAL_REVIEW")
    if not isinstance(row.get("source_market_ids"), list):
        raise SchemaValidationError(f"{path}.source_market_ids must be a list")
    if not isinstance(row.get("blockers"), list):
        raise SchemaValidationError(f"{path}.blockers must be a list")


def _normalized_value(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(value)
    if isinstance(value, float):
        return round(value, 6)
    return value


def _table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"## {title}", "", "| Constraint | Type | Priority | Markets | Blockers |", "| --- | --- | --- | --- | --- |"]
    if not rows:
        lines.append("| none |  |  |  |  |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["constraint_id"]),
                    _md(row["constraint_type"]),
                    _md(row.get("diagnostic_priority") or ""),
                    _md(", ".join(row["source_market_ids"])),
                    _md(", ".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    if value is None:
        return ""
    return str(value)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
