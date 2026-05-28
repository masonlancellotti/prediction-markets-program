from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens


BANNER = (
    "Saved-file-only real quote overlay status. Rows are diagnostic graph inputs "
    "for WATCH/MANUAL_REVIEW routing only."
)
TOP_LIMIT = 10


def build_saved_quote_overlay_status_report(
    *,
    overlay_metadata: dict[str, Any] | None,
    before_stale_lag_report: dict[str, Any] | None,
    after_stale_lag_report: dict[str, Any] | None,
    rv_packets_report: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = overlay_metadata if isinstance(overlay_metadata, dict) else {}
    packets = _list_from_report(rv_packets_report, "investigation_packets")
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "saved_rv_files_scanned": _string_list(metadata.get("files_scanned")),
        "saved_rv_files_imported": _string_list(metadata.get("files_imported", metadata.get("files_read"))),
        "markets_imported": _int(metadata.get("markets_imported", metadata.get("markets_read"))),
        "quote_rows_imported": _int(metadata.get("quote_rows_imported")),
        "markets_overlayed": _int(metadata.get("markets_overlayed")),
        "markets_added": _int(metadata.get("markets_added")),
        "freshness_buckets": _metadata_freshness_buckets(metadata) or _freshness_buckets(after_stale_lag_report),
        "top_blockers_before_overlay": _top_blockers(before_stale_lag_report),
        "top_blockers_after_overlay": _top_blockers(after_stale_lag_report),
        "packet_kind_counts": _packet_kind_counts(rv_packets_report, packets),
        "top_rel_value_handoff_candidates": _top_candidates(packets),
        "overlay_blockers": _string_list(metadata.get("blockers")),
    }
    validate_saved_quote_overlay_status_report(report)
    return report


def write_saved_quote_overlay_status_report(
    *,
    json_output: Path | str,
    markdown_output: Path | str,
    overlay_metadata: dict[str, Any] | None,
    before_stale_lag_report: dict[str, Any] | None,
    after_stale_lag_report: dict[str, Any] | None,
    rv_packets_report: dict[str, Any] | None,
) -> dict[str, Any]:
    report = build_saved_quote_overlay_status_report(
        overlay_metadata=overlay_metadata,
        before_stale_lag_report=before_stale_lag_report,
        after_stale_lag_report=after_stale_lag_report,
        rv_packets_report=rv_packets_report,
    )
    markdown = render_saved_quote_overlay_status_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError("saved quote overlay status Markdown contains prohibited vocabulary: " + ", ".join(findings))
    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def validate_saved_quote_overlay_status_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("saved quote overlay status must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("saved quote overlay status must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("saved quote overlay status actions must be WATCH and MANUAL_REVIEW only")
    for key in [
        "saved_rv_files_scanned",
        "saved_rv_files_imported",
        "top_blockers_before_overlay",
        "top_blockers_after_overlay",
        "top_rel_value_handoff_candidates",
        "overlay_blockers",
    ]:
        if not isinstance(report.get(key), list):
            raise SchemaValidationError(f"{key} must be a list")
    for key in ["markets_imported", "quote_rows_imported", "markets_overlayed", "markets_added"]:
        if not isinstance(report.get(key), int) or isinstance(report.get(key), bool) or report[key] < 0:
            raise SchemaValidationError(f"{key} must be a non-negative integer")
    if not isinstance(report.get("freshness_buckets"), dict):
        raise SchemaValidationError("freshness_buckets must be an object")
    if not isinstance(report.get("packet_kind_counts"), dict):
        raise SchemaValidationError("packet_kind_counts must be an object")


def render_saved_quote_overlay_status_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Saved Quote Overlay Status",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Saved RV files scanned: {len(report['saved_rv_files_scanned'])}",
        f"- Saved RV files imported: {len(report['saved_rv_files_imported'])}",
        f"- Markets imported: {report['markets_imported']}",
        f"- Quote rows imported: {report['quote_rows_imported']}",
        f"- Markets overlayed: {report['markets_overlayed']}",
        f"- Markets added: {report['markets_added']}",
        "",
        "## Freshness Buckets",
        "",
    ]
    for bucket, count in sorted(report["freshness_buckets"].items()):
        lines.append(f"- `{bucket}`: {count}")
    lines.extend(_blocker_table("Top Blockers Before Overlay", report["top_blockers_before_overlay"]))
    lines.extend(_blocker_table("Top Blockers After Overlay", report["top_blockers_after_overlay"]))
    lines.extend(
        [
            "",
            "## Packet Kinds",
            "",
        ]
    )
    for kind, count in sorted(report["packet_kind_counts"].items()):
        lines.append(f"- `{kind}`: {count}")
    lines.extend(
        [
            "",
            "## Top Handoff Candidates",
            "",
            "| Rank | Packet | Kind | Priority | Confidence | Markets | Blockers |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not report["top_rel_value_handoff_candidates"]:
        lines.append("| none |  |  |  |  |  |  |")
    for row in report["top_rel_value_handoff_candidates"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["rank"]),
                    _md(row["packet_id"]),
                    _md(row["packet_kind"]),
                    _md(row["priority_score"]),
                    _md(row["confidence_tier"]),
                    _md(", ".join(row["markets_involved"])),
                    _md(", ".join(row["packet_blockers"])),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _blocker_table(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        f"## {title}",
        "",
        "| Blocker | Rows |",
        "| --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |")
    for row in rows:
        lines.append(f"| {_md(row['blocker'])} | {_md(row['row_count'])} |")
    return lines


def _top_blockers(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = _list_from_report(report, "stale_lag_watchlist")
    counts: Counter[str] = Counter()
    for row in rows:
        for blocker in _string_list(row.get("blockers")):
            counts[blocker] += 1
    return [
        {"blocker": blocker, "row_count": count}
        for blocker, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_LIMIT]
    ]


def _freshness_buckets(report: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(report, dict) or not isinstance(report.get("freshness_buckets"), dict):
        return {}
    return {
        str(bucket): count
        for bucket, count in report["freshness_buckets"].items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }


def _metadata_freshness_buckets(metadata: dict[str, Any]) -> dict[str, int]:
    buckets = metadata.get("saved_quote_freshness_buckets")
    if not isinstance(buckets, dict):
        return {}
    return {
        str(bucket): count
        for bucket, count in buckets.items()
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0
    }


def _packet_kind_counts(report: dict[str, Any] | None, packets: list[dict[str, Any]]) -> dict[str, int]:
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    counts = summary.get("by_packet_kind") if isinstance(summary, dict) else None
    if isinstance(counts, dict):
        return {str(kind): int(count) for kind, count in counts.items() if isinstance(count, int) and not isinstance(count, bool)}
    return dict(sorted(Counter(str(packet.get("packet_kind") or "UNKNOWN") for packet in packets).items()))


def _top_candidates(packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_packets = sorted(
        packets,
        key=lambda row: (
            -float(row.get("priority_score") or 0.0),
            int(row.get("diagnostic_rank") or 999999),
            str(row.get("packet_id") or ""),
        ),
    )
    candidates: list[dict[str, Any]] = []
    for index, packet in enumerate(sorted_packets[:TOP_LIMIT], start=1):
        candidates.append(
            {
                "rank": index,
                "packet_id": str(packet.get("packet_id") or ""),
                "packet_kind": str(packet.get("packet_kind") or ""),
                "priority_score": _number(packet.get("priority_score")),
                "confidence_tier": str(packet.get("confidence_tier") or "LOW"),
                "markets_involved": _string_list(packet.get("markets_involved")),
                "packet_blockers": _string_list(packet.get("packet_blockers")),
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
            }
        )
    return candidates


def _list_from_report(report: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows = report.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return 0.0


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "build_saved_quote_overlay_status_report",
    "render_saved_quote_overlay_status_markdown",
    "validate_saved_quote_overlay_status_report",
    "write_saved_quote_overlay_status_report",
]
