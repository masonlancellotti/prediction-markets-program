from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.reporting.json_report import _assert_safe_violation_schema
from graph_engine.reporting.schema_validation import validate_json_schema_subset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HINT_SCHEMA_PATH = PROJECT_ROOT / "schemas" / "relative_value_hint.schema.json"
ALLOWED_ACTIONS = ["WATCH", "MANUAL_REVIEW"]
ACTION_LEVEL = {"WATCH": 0, "MANUAL_REVIEW": 1}
COMPARED_FIELDS = [
    "relation_type",
    "hard_bound_type",
    "blockers",
    "max_action_cap",
    "direction",
    "settlement_source_proven",
]
BANNER = "Research-only saved-file hint diff. Not permission for any market action."


def load_validated_hint_report(path: Path | str, schema_path: Path | str = DEFAULT_HINT_SCHEMA_PATH) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    validate_json_schema_subset(report, schema)
    _assert_safe_violation_schema(report)
    return report


def build_hint_diff_report(old_report: dict[str, Any], new_report: dict[str, Any]) -> dict[str, Any]:
    old_hints = _hints_by_id(old_report)
    new_hints = _hints_by_id(new_report)
    old_ids = set(old_hints)
    new_ids = set(new_hints)

    new_items = [_hint_summary(new_hints[hint_id]) for hint_id in sorted(new_ids - old_ids)]
    removed_items = [_hint_summary(old_hints[hint_id]) for hint_id in sorted(old_ids - new_ids)]

    field_changes: list[dict[str, Any]] = []
    upgraded: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []
    for hint_id in sorted(old_ids & new_ids):
        old_hint = old_hints[hint_id]
        new_hint = new_hints[hint_id]
        changes = _field_changes(hint_id, old_hint, new_hint)
        field_changes.extend(changes)
        cap_change = next((change for change in changes if change["field"] == "max_action_cap"), None)
        if cap_change is None:
            continue
        old_level = ACTION_LEVEL[cap_change["old_value"]]
        new_level = ACTION_LEVEL[cap_change["new_value"]]
        summary = {
            "graph_hint_id": hint_id,
            "old_max_action_cap": cap_change["old_value"],
            "new_max_action_cap": cap_change["new_value"],
        }
        if new_level > old_level:
            upgraded.append(summary)
        elif new_level < old_level:
            downgraded.append(summary)

    change_counts = Counter(change["field"] for change in field_changes)
    report = {
        "diagnostic_only": True,
        "banner": BANNER,
        "allowed_actions": ALLOWED_ACTIONS,
        "old_snapshot_id": old_report["snapshot_id"],
        "new_snapshot_id": new_report["snapshot_id"],
        "summary": {
            "new_count": len(new_items),
            "removed_count": len(removed_items),
            "changed_count": len({change["graph_hint_id"] for change in field_changes}),
            "upgraded_count": len(upgraded),
            "downgraded_count": len(downgraded),
            "changes_by_field": dict(sorted(change_counts.items())),
        },
        "new_hints": new_items,
        "removed_hints": removed_items,
        "upgraded_hints": upgraded,
        "downgraded_hints": downgraded,
        "field_changes": field_changes,
    }
    _assert_safe_violation_schema(report)
    return report


def write_hint_diff_report(
    old_path: Path | str,
    new_path: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
    schema_path: Path | str = DEFAULT_HINT_SCHEMA_PATH,
) -> dict[str, Any]:
    old_report = load_validated_hint_report(old_path, schema_path)
    new_report = load_validated_hint_report(new_path, schema_path)
    report = build_hint_diff_report(old_report, new_report)

    json_path = Path(json_output)
    md_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_hint_diff_markdown(report), encoding="utf-8")
    return report


def render_hint_diff_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Market Graph Hint Diff",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Old snapshot: `{report['old_snapshot_id']}`",
        f"- New snapshot: `{report['new_snapshot_id']}`",
        f"- New hints: {summary['new_count']}",
        f"- Removed hints: {summary['removed_count']}",
        f"- Changed hints: {summary['changed_count']}",
        f"- Upgraded caps: {summary['upgraded_count']}",
        f"- Downgraded caps: {summary['downgraded_count']}",
        "",
    ]
    lines.extend(_markdown_hint_table("New Hints", report["new_hints"]))
    lines.extend(_markdown_hint_table("Removed Hints", report["removed_hints"]))
    lines.extend(_markdown_cap_table("Upgraded Caps", report["upgraded_hints"]))
    lines.extend(_markdown_cap_table("Downgraded Caps", report["downgraded_hints"]))
    lines.extend(
        [
            "## Field Changes",
            "",
            "| Hint | Field | Old | New |",
            "| --- | --- | --- | --- |",
        ]
    )
    for change in report["field_changes"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(change["graph_hint_id"]),
                    _md(change["field"]),
                    _md(_value_for_markdown(change["old_value"])),
                    _md(_value_for_markdown(change["new_value"])),
                ]
            )
            + " |"
        )
    if not report["field_changes"]:
        lines.append("| none |  |  |  |")
    lines.extend(["", "This diff compares saved diagnostic files only.", ""])
    return "\n".join(lines)


def _hints_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {hint["graph_hint_id"]: hint for hint in report["hints"]}


def _field_changes(hint_id: str, old_hint: dict[str, Any], new_hint: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for field in COMPARED_FIELDS:
        old_value = _normalized_value(old_hint.get(field))
        new_value = _normalized_value(new_hint.get(field))
        if old_value == new_value:
            continue
        changes.append(
            {
                "graph_hint_id": hint_id,
                "field": field,
                "old_value": old_value,
                "new_value": new_value,
            }
        )
    return changes


def _hint_summary(hint: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_hint_id": hint["graph_hint_id"],
        "source_market_id": hint["source_market_id"],
        "target_market_id": hint["target_market_id"],
        "relation_type": hint["relation_type"],
        "direction": hint["direction"],
        "hard_bound_type": hint["hard_bound_type"],
        "max_action_cap": hint["max_action_cap"],
        "blockers": list(hint["blockers"]),
        "edge_source": hint["edge_source"],
        "diagnostic_only": True,
        "allowed_actions": ALLOWED_ACTIONS,
    }


def _normalized_value(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(value)
    return value


def _markdown_hint_table(title: str, hints: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Hint | Relation | Cap | Direction | Blockers |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not hints:
        lines.append("| none |  |  |  |  |")
    for hint in hints:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(hint["graph_hint_id"]),
                    _md(hint["relation_type"]),
                    _md(hint["max_action_cap"]),
                    _md(hint["direction"]),
                    _md(", ".join(hint["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _markdown_cap_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Hint | Old Cap | New Cap |",
        "| --- | --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |  |")
    for row in rows:
        lines.append(f"| {_md(row['graph_hint_id'])} | {_md(row['old_max_action_cap'])} | {_md(row['new_max_action_cap'])} |")
    lines.append("")
    return lines


def _value_for_markdown(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "none"
    if value is None:
        return ""
    return str(value)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
