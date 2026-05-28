"""Graph-to-relative-value worklist exporter.

Reads the RV diagnostic relationship-edges report and ranks the edges
that are useful for the relative-value scanner to inspect next.  Output
is diagnostic-only; the worklist never authorises trading and never
hands a row to the RV evaluator.  Allowed next actions are limited to
RV_SOURCE_REVIEW / RV_BASIS_RISK_REVIEW / RV_MANUAL_DISCOVERY /
RV_IGNORE_FOR_NOW.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_engine.relationships.rv_edge_taxonomy import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    RELATIONSHIP_VERSION,
    RV_RELATIONSHIP_TYPES,
    validate_rv_edge,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


REPORT_BANNER = (
    "Saved-file-only RV review worklist. Ranks graph diagnostic relationship edges by "
    "their usefulness for relative-value follow-up. Worklist is review-only; it never "
    "creates evaluator inputs and never authorises trading."
)

ALLOWED_WORKLIST_ACTIONS: tuple[str, ...] = (
    "RV_SOURCE_REVIEW",
    "RV_BASIS_RISK_REVIEW",
    "RV_MANUAL_DISCOVERY",
    "RV_IGNORE_FOR_NOW",
)

# Venue priority — core trio first, queued IBKR last.  When two edges
# have an identical score the worklist breaks the tie by left/right venue
# priority so the daily list trends to the active venues.
VENUE_PRIORITY: dict[str, int] = {
    "kalshi": 0,
    "polymarket": 1,
    "cdna": 2,
    "ibkr_forecastex": 9,
    "ibkr": 9,
    "unknown": 5,
}


def write_rv_review_worklist_report(
    *,
    edges_report_path: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
    include_queued_ibkr: bool = False,
) -> dict[str, Any]:
    """Build and persist the graph-to-RV review worklist."""

    report = build_rv_review_worklist_report(
        edges_report_path=edges_report_path,
        include_queued_ibkr=include_queued_ibkr,
    )
    markdown = render_rv_review_worklist_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "rv review worklist Markdown contains prohibited vocabulary: " + ", ".join(findings)
        )
    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def build_rv_review_worklist_report(
    *,
    edges_report_path: Path | str,
    include_queued_ibkr: bool = False,
) -> dict[str, Any]:
    edges_path = Path(edges_report_path)
    if not edges_path.exists():
        return _empty_report(edges_path, missing=True, include_queued_ibkr=include_queued_ibkr)
    try:
        payload = json.loads(edges_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report = _empty_report(edges_path, missing=False, include_queued_ibkr=include_queued_ibkr)
        report["inputs"]["parse_error"] = str(exc)
        return report
    if not isinstance(payload, dict):
        report = _empty_report(edges_path, missing=False, include_queued_ibkr=include_queued_ibkr)
        report["inputs"]["parse_error"] = "edges report must be a JSON object"
        return report

    edges = [edge for edge in payload.get("edges", []) if isinstance(edge, dict)]
    # Sanity-check every edge before scoring.
    for edge in edges:
        validate_rv_edge(edge)

    rows = _rank_edges(edges, include_queued_ibkr=include_queued_ibkr)
    summary = _summary(rows)
    report: dict[str, Any] = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_worklist_actions": list(ALLOWED_WORKLIST_ACTIONS),
        "banner": REPORT_BANNER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "edges_report_path": str(edges_path),
        "relationship_version": RELATIONSHIP_VERSION,
        "inputs": {
            "edges_report_path": str(edges_path),
            "include_queued_ibkr": bool(include_queued_ibkr),
            "total_edges_read": len(edges),
        },
        "summary": summary,
        "rows": rows,
    }
    validate_rv_review_worklist_report(report)
    return report


def validate_rv_review_worklist_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("worklist report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("worklist report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("worklist allowed_actions must be WATCH and MANUAL_REVIEW only")
    if report.get("allowed_worklist_actions") != list(ALLOWED_WORKLIST_ACTIONS):
        raise SchemaValidationError("worklist allowed_worklist_actions must match the contract")
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise SchemaValidationError("worklist rows must be a list")
    for index, row in enumerate(rows):
        _validate_row(row, f"rows[{index}]")


def render_rv_review_worklist_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RV Review Worklist",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed worklist actions: `{', '.join(report['allowed_worklist_actions'])}`",
        f"- Relationship version: `{report['relationship_version']}`",
        f"- Generated at: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Total worklist rows: {summary['total_rows']}",
        f"- Excluded edges: {summary['excluded_count']}",
        "",
        "### Rows by allowed_next_action",
        "",
        "| Action | Count |",
        "| --- | --- |",
    ]
    if not summary["rows_by_action"]:
        lines.append("| none |  |")
    for entry in summary["rows_by_action"]:
        lines.append(f"| `{entry['allowed_next_action']}` | {entry['count']} |")
    lines.extend(["", "### Rows by relationship_type", "", "| Relationship type | Count |", "| --- | --- |"])
    if not summary["rows_by_relationship_type"]:
        lines.append("| none |  |")
    for entry in summary["rows_by_relationship_type"]:
        lines.append(f"| `{entry['relationship_type']}` | {entry['count']} |")
    lines.extend([
        "",
        "## Worklist",
        "",
        "| Rank | Edge | Type | Action | Confidence | Score | Next step | RV can inspect now |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    if not report["rows"]:
        lines.append("| none |  |  |  |  |  |  |  |")
    for index, row in enumerate(report["rows"], start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{row['edge_id']}`",
                    f"`{row['relationship_type']}`",
                    f"`{row['allowed_next_action']}`",
                    f"`{row['confidence_bucket']}`",
                    f"{row['score']:.2f}",
                    row["next_code_or_data_action"],
                    "yes" if row["rv_can_inspect_now"] else "no",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _empty_report(edges_path: Path, *, missing: bool, include_queued_ibkr: bool) -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_worklist_actions": list(ALLOWED_WORKLIST_ACTIONS),
        "banner": REPORT_BANNER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "edges_report_path": str(edges_path),
        "relationship_version": RELATIONSHIP_VERSION,
        "inputs": {
            "edges_report_path": str(edges_path),
            "include_queued_ibkr": bool(include_queued_ibkr),
            "total_edges_read": 0,
            "missing_input_report": missing,
        },
        "summary": {
            "total_rows": 0,
            "excluded_count": 0,
            "rows_by_action": [],
            "rows_by_relationship_type": [],
        },
        "rows": [],
    }


def _rank_edges(edges: list[dict[str, Any]], *, include_queued_ibkr: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for edge in edges:
        if _should_exclude(edge, include_queued_ibkr=include_queued_ibkr):
            continue
        rows.append(_worklist_row(edge))
    rows.sort(
        key=lambda row: (
            -row["score"],
            VENUE_PRIORITY.get(row["left_venue"], 7),
            VENUE_PRIORITY.get(row["right_venue"], 7),
            row["edge_id"],
        )
    )
    return rows


def _should_exclude(edge: dict[str, Any], *, include_queued_ibkr: bool) -> bool:
    if edge["action"] == ACTION_IGNORE_LOW_CONFIDENCE:
        return True
    if edge["relationship_type"] == "TITLE_SIMILARITY_ONLY":
        return True
    if edge["relationship_type"] == "NO_CURRENT_PEER":
        return True
    family = RV_RELATIONSHIP_TYPES.get(edge["relationship_type"])
    if family == "reference_only" and edge["right_venue"] != "kalshi":
        # Reference-only without an executable counterpart cannot be RV
        # inspected; drop unless it points to an active venue.
        return True
    if not include_queued_ibkr and (
        edge["left_venue"] == "ibkr_forecastex" or edge["right_venue"] == "ibkr_forecastex"
    ):
        return True
    return False


def _worklist_row(edge: dict[str, Any]) -> dict[str, Any]:
    score, why, completeness = _score_edge(edge)
    next_action = _allowed_next_action(edge)
    next_step = _next_step(edge, next_action)
    rv_can_inspect_now = _rv_can_inspect_now(edge, next_action)
    return {
        "edge_id": edge["edge_id"],
        "relationship_type": edge["relationship_type"],
        "relationship_family": edge["relationship_family"],
        "left_market_id": edge["left_market_id"],
        "right_market_id": edge.get("right_market_id"),
        "right_reference_id": edge.get("right_reference_id"),
        "left_venue": edge["left_venue"],
        "right_venue": edge["right_venue"],
        "confidence_bucket": edge["confidence_bucket"],
        "allowed_next_action": next_action,
        "why_it_matters": why,
        "exact_blockers": list(edge.get("blockers", [])),
        "next_manual_evidence_needed": list(edge.get("required_review_fields", [])),
        "next_code_or_data_action": next_step,
        "rv_can_inspect_now": rv_can_inspect_now,
        "score": round(score, 3),
        "typed_completeness_proxy": round(completeness, 3),
        "source_report_paths": list(edge.get("source_report_paths", [])),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }


def _allowed_next_action(edge: dict[str, Any]) -> str:
    family = edge["relationship_family"]
    if family == "near_exact_review":
        return "RV_SOURCE_REVIEW"
    if family == "basis_risk":
        return "RV_BASIS_RISK_REVIEW"
    if family == "structural":
        return "RV_BASIS_RISK_REVIEW"
    if family == "reference_only":
        return "RV_SOURCE_REVIEW"
    if family == "weak_signal":
        if edge["relationship_type"] == "SAME_TOPIC_WEAK_SIGNAL":
            return "RV_MANUAL_DISCOVERY"
        return "RV_IGNORE_FOR_NOW"
    return "RV_MANUAL_DISCOVERY"


def _next_step(edge: dict[str, Any], next_action: str) -> str:
    if next_action == "RV_SOURCE_REVIEW":
        return (
            "Open the typed-key audit for the source venue and verify settlement source, "
            "settlement time, and threshold convention before any exact comparison."
        )
    if next_action == "RV_BASIS_RISK_REVIEW":
        return (
            "Compare payoff calendar shapes (deadline-touch vs point-in-time vs range bucket) "
            "and confirm asset/index source mismatch before any pair creation review."
        )
    if next_action == "RV_MANUAL_DISCOVERY":
        return (
            "Manual peer discovery required — query Polymarket/CDNA/Kalshi for a matching "
            "point-in-time market on the same asset, date, and threshold."
        )
    return "Suppress for now; revisit after fresher RV diagnostics become available."


def _rv_can_inspect_now(edge: dict[str, Any], next_action: str) -> bool:
    if next_action == "RV_IGNORE_FOR_NOW":
        return False
    if next_action == "RV_MANUAL_DISCOVERY":
        return False
    evidence = edge.get("evidence_fields") or {}
    return bool(
        evidence.get("lane")
        or evidence.get("kalshi_market_id")
        or evidence.get("cdna_threshold")
        or evidence.get("polymarket_market_id")
    )


def _score_edge(edge: dict[str, Any]) -> tuple[float, str, float]:
    family = edge["relationship_family"]
    base = {"near_exact_review": 6.0, "basis_risk": 4.0, "structural": 3.0, "reference_only": 2.0, "weak_signal": 1.0}.get(family, 0.5)
    confidence_bonus = {"low": 0.0, "medium": 1.0, "high": 2.0}.get(edge["confidence_bucket"], 0.0)
    evidence = edge.get("evidence_fields") or {}
    completeness = 0.0
    typed_fields_present = 0
    typed_fields_required = 0
    for key in (
        "kalshi_market_id",
        "kalshi_target_date",
        "kalshi_threshold",
        "kalshi_comparator",
        "kalshi_settlement_source",
        "kalshi_market_shape",
        "cdna_threshold",
        "cdna_settlement_source",
        "cdna_market_shape",
        "polymarket_market_id",
        "polymarket_market_shape",
        "lane",
        "peer_threshold",
    ):
        typed_fields_required += 1
        if evidence.get(key):
            typed_fields_present += 1
    if typed_fields_required:
        completeness = typed_fields_present / typed_fields_required
    typed_bonus = completeness * 2.0
    blocker_penalty = 0.05 * max(0, len(edge.get("blockers", [])) - 6)
    score = base + confidence_bonus + typed_bonus - blocker_penalty
    why = _why_it_matters(edge)
    return score, why, completeness


def _why_it_matters(edge: dict[str, Any]) -> str:
    family = edge["relationship_family"]
    if family == "near_exact_review":
        return (
            "Same event/threshold candidate from RV diagnostics — worth a strict "
            "settlement-source and quote-freshness review even though the graph does "
            "not promote it to exact on its own."
        )
    if family == "basis_risk":
        return (
            "Basis-risk relationship: markets share asset/event family but differ in "
            "observation window or source. Confirming the mismatch keeps the peer "
            "link useful without claiming equality."
        )
    if family == "structural":
        return (
            "Structural relationship: subset/superset, complement, or partition rule "
            "between markets. Worth review because structural inconsistency in either "
            "side hints at mispricing context."
        )
    if family == "reference_only":
        return "Reference-only anchor that needs a separate executable counterpart before RV can act."
    return "Weak topic-only signal kept for clustering; not a structural or exact match."


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    for row in rows:
        action_counter[row["allowed_next_action"]] += 1
        type_counter[row["relationship_type"]] += 1
    return {
        "total_rows": len(rows),
        "excluded_count": 0,
        "rows_by_action": [
            {"allowed_next_action": name, "count": count}
            for name, count in sorted(action_counter.items(), key=lambda item: (-item[1], item[0]))
        ],
        "rows_by_relationship_type": [
            {"relationship_type": name, "count": count}
            for name, count in sorted(type_counter.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _validate_row(row: dict[str, Any], path: str) -> None:
    if not isinstance(row, dict):
        raise SchemaValidationError(f"{path} must be an object")
    for key in (
        "edge_id",
        "relationship_type",
        "relationship_family",
        "left_market_id",
        "left_venue",
        "right_venue",
        "confidence_bucket",
        "allowed_next_action",
        "why_it_matters",
        "exact_blockers",
        "next_manual_evidence_needed",
        "next_code_or_data_action",
        "rv_can_inspect_now",
        "score",
        "typed_completeness_proxy",
        "source_report_paths",
        "diagnostic_only",
        "affects_evaluator_gates",
    ):
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_next_action"] not in ALLOWED_WORKLIST_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_next_action is not allowed")
    if not isinstance(row["exact_blockers"], list):
        raise SchemaValidationError(f"{path}.exact_blockers must be a list")
    if not isinstance(row["next_manual_evidence_needed"], list):
        raise SchemaValidationError(f"{path}.next_manual_evidence_needed must be a list")
    if not isinstance(row["source_report_paths"], list):
        raise SchemaValidationError(f"{path}.source_report_paths must be a list")


__all__ = [
    "ALLOWED_WORKLIST_ACTIONS",
    "REPORT_BANNER",
    "build_rv_review_worklist_report",
    "render_rv_review_worklist_markdown",
    "validate_rv_review_worklist_report",
    "write_rv_review_worklist_report",
]
