"""Diff two saved finite-state payoff diagnostic reports.

The diff is diagnostic-only.  It reports added/removed/changed payoff-state
families plus per-field changes for ``bound_gap``, ``feasibility_status``,
``blockers``, ``confidence_basis``, ``violated_constraints``, and
``max_action_cap``.  Outputs are capped at ``WATCH`` and ``MANUAL_REVIEW``
and may NOT be used as evaluator gate input.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.reporting.payoff_state_report import (
    validate_payoff_state_diagnostics_report,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


BANNER = (
    "Diagnostic-only saved-file diff for finite-state payoff diagnostic reports. "
    "It does not affect evaluator gates and contains no equality-of-payoff claims."
)
COMPARED_FIELDS = [
    "bound_gap",
    "feasibility_status",
    "blockers",
    "violated_constraints",
    "max_action_cap",
    "confidence_basis",
]


def load_validated_payoff_state_report(path: Path | str) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_payoff_state_diagnostics_report(report)
    return report


def build_payoff_state_diff_report(
    old_report: dict[str, Any],
    new_report: dict[str, Any],
) -> dict[str, Any]:
    validate_payoff_state_diagnostics_report(old_report)
    validate_payoff_state_diagnostics_report(new_report)

    old_items = _items_by_id(old_report)
    new_items = _items_by_id(new_report)
    old_ids = set(old_items)
    new_ids = set(new_items)

    added = [_summary(new_items[item_id]) for item_id in sorted(new_ids - old_ids)]
    removed = [_summary(old_items[item_id]) for item_id in sorted(old_ids - new_ids)]
    changed: list[dict[str, Any]] = []
    field_changes: list[dict[str, Any]] = []
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
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "old_snapshot_id": old_report.get("snapshot_id", "unknown"),
        "new_snapshot_id": new_report.get("snapshot_id", "unknown"),
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
            "unchanged_count": len(old_ids & new_ids) - len(changed),
            "changes_by_field": dict(sorted(counts.items())),
        },
        "added_families": added,
        "removed_families": removed,
        "changed_families": changed,
        "field_changes": field_changes,
    }
    validate_payoff_state_diff_contract(report)
    return report


def write_payoff_state_diff_report(
    old_path: Path | str,
    new_path: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
) -> dict[str, Any]:
    old_report = load_validated_payoff_state_report(old_path)
    new_report = load_validated_payoff_state_report(new_path)
    report = build_payoff_state_diff_report(old_report, new_report)
    validate_payoff_state_diff_contract(report)
    markdown = render_payoff_state_diff_markdown(report)
    hits = find_prohibited_rendered_text(markdown)
    if hits:
        raise SchemaValidationError(
            "payoff-state diff Markdown contains prohibited vocabulary: " + ", ".join(hits)
        )

    json_path = Path(json_output)
    md_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report


def render_payoff_state_diff_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Payoff-State Diagnostic Diff",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Old snapshot: `{report['old_snapshot_id']}`",
        f"- New snapshot: `{report['new_snapshot_id']}`",
        f"- Added families: {summary['added_count']}",
        f"- Removed families: {summary['removed_count']}",
        f"- Changed families: {summary['changed_count']}",
        f"- Unchanged families: {summary['unchanged_count']}",
        "",
        "## Added families",
        "",
    ]
    lines.extend(_render_table(report["added_families"]))
    lines.extend(["", "## Removed families", ""])
    lines.extend(_render_table(report["removed_families"]))
    lines.extend(["", "## Changed families", ""])
    lines.extend(_render_table(report["changed_families"]))
    lines.extend(["", "## Field changes", "", "| Family | Field | Old | New |", "| --- | --- | --- | --- |"])
    if not report["field_changes"]:
        lines.append("| none |  |  |  |")
    else:
        for change in report["field_changes"]:
            lines.append(
                f"| {_md(change['family_id'])} | {_md(change['field'])} "
                f"| {_md(_format_value(change['old_value']))} "
                f"| {_md(_format_value(change['new_value']))} |"
            )
    lines.append("")
    return "\n".join(lines)


def validate_payoff_state_diff_contract(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("payoff-state diff must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("payoff-state diff must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("payoff-state diff actions must be WATCH and MANUAL_REVIEW only")
    for section in ("added_families", "removed_families", "changed_families"):
        rows = report.get(section)
        if not isinstance(rows, list):
            raise SchemaValidationError(f"{section} must be a list")
        for index, row in enumerate(rows):
            _validate_summary_row(row, f"{section}[{index}]")
    for index, change in enumerate(report.get("field_changes", [])):
        path = f"field_changes[{index}]"
        if change.get("field") not in COMPARED_FIELDS:
            raise SchemaValidationError(f"{path}.field is not supported")
        if not isinstance(change.get("family_id"), str) or not change["family_id"]:
            raise SchemaValidationError(f"{path}.family_id must be a non-empty string")


def _items_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["family_id"]: row for row in report.get("payoff_state_diagnostics", [])}


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "family_id": row["family_id"],
        "family_type": row["family_type"],
        "diagnostic_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "max_action_cap": row.get("max_action_cap"),
        "diagnostic_priority": row.get("diagnostic_priority"),
        "feasibility_status": row.get("feasibility_status"),
        "bound_gap": row.get("bound_gap"),
        "normalized_bound_gap": row.get("normalized_bound_gap"),
        "blockers": list(row.get("blockers", [])),
        "violated_constraints": list(row.get("violated_constraints", [])),
        "review_reason": row.get("review_reason", ""),
    }


def _field_changes(item_id: str, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in COMPARED_FIELDS:
        old_value = _normalized_value(old.get(field))
        new_value = _normalized_value(new.get(field))
        if old_value == new_value:
            continue
        changes.append(
            {
                "family_id": item_id,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


def _validate_summary_row(row: dict[str, Any], path: str) -> None:
    if row.get("diagnostic_only") is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(row.get("family_id"), str) or not row["family_id"]:
        raise SchemaValidationError(f"{path}.family_id must be a non-empty string")
    cap = row.get("max_action_cap")
    if cap is not None and cap not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")


def _normalized_value(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalized_value(value[key]) for key in sorted(value)}
    return value


def _render_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["| Family | Type | Status | Cap | Bound Gap | Blockers | Violated |", "| --- | --- | --- | --- | --- | --- | --- |"]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |")
        return lines
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["family_id"]),
                    _md(row["family_type"]),
                    _md(row["feasibility_status"]),
                    _md(row["max_action_cap"]),
                    _md(row["bound_gap"]),
                    _md(", ".join(row["blockers"]) or "none"),
                    _md(", ".join(row["violated_constraints"]) or "none"),
                ]
            )
            + " |"
        )
    return lines


def _format_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "BANNER",
    "COMPARED_FIELDS",
    "build_payoff_state_diff_report",
    "load_validated_payoff_state_report",
    "render_payoff_state_diff_markdown",
    "validate_payoff_state_diff_contract",
    "write_payoff_state_diff_report",
]
