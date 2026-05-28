"""Manual discovery backlog generator.

Reads the manual relationship evidence inventory and produces a
prioritized, sectioned backlog of manual tasks Mason should perform.

Backlog items are diagnostic-only review tasks. They never authorise
trading, never create paper candidates, and never assert exact
equivalence. The backlog's ``expected_payoff`` field documents which of
the four outcome categories a task contributes to:

- ``enables_graph_relationship`` — adds a new graph edge or upgrades a
  ``NO_CURRENT_PEER`` row.
- ``enables_rv_source_review`` — gives RV enough evidence to start a
  strict settlement-source review.
- ``enables_exact_review_candidate`` — completes the typed-key set so RV
  can decide whether an exact pair exists.
- ``only_improves_basis_risk_map`` — the task helps clustering but does
  not change the gates.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_engine.relationships.rv_edge_taxonomy import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    ALLOWED_EDGE_ACTIONS,
)
from graph_engine.reporting.manual_relationship_evidence import (
    DEFAULT_DIFFICULTY,
    DEFAULT_REPEAT_CADENCE,
    DEFAULT_URGENCY,
    EVIDENCE_VERSION,
    MANUAL_RELATIONSHIP_TYPES,
    NEAR_EXACT_TYPES,
    _redact_payload,
)
from graph_engine.reporting.safety import find_prohibited_rendered_text
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


REPORT_BANNER = (
    "Saved-file-only graph manual discovery backlog. Diagnostic only. The backlog "
    "ranks manual review tasks that unblock graph relationships or relative-value "
    "review. It never authorises trading and never creates evaluator inputs."
)

BACKLOG_VERSION = "graph-manual-discovery-backlog-v1"

REUSABLE_SCOPES = ("one_time", "per_family", "per_market", "per_event_date", "per_venue_rules_version")
PAYOFF_OUTCOMES = (
    "enables_graph_relationship",
    "enables_rv_source_review",
    "enables_exact_review_candidate",
    "only_improves_basis_risk_map",
)
URGENCY_BUCKETS = ("HIGH", "MEDIUM", "LOW")
DIFFICULTY_BUCKETS = ("EASY", "MEDIUM", "HARD")
SECTION_NAMES = (
    "top_10_overall",
    "top_crypto",
    "top_economics",
    "top_sports",
    "unblocks_most_graph_edges",
    "unblocks_relative_value_review",
    "ignore_for_now",
)


def write_graph_manual_discovery_backlog_report(
    *,
    relationships_path: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
) -> dict[str, Any]:
    report = build_graph_manual_discovery_backlog_report(relationships_path=Path(relationships_path))
    markdown = render_graph_manual_discovery_backlog_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "graph manual discovery backlog Markdown contains prohibited vocabulary: "
            + ", ".join(findings)
        )
    json_path = Path(json_output)
    md_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report


def build_graph_manual_discovery_backlog_report(*, relationships_path: Path) -> dict[str, Any]:
    relationships_path = Path(relationships_path)
    if not relationships_path.exists():
        return _empty_backlog(relationships_path, missing=True)
    try:
        payload = json.loads(relationships_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report = _empty_backlog(relationships_path, missing=False)
        report["inputs"]["parse_error"] = str(exc)
        return report
    if not isinstance(payload, dict):
        report = _empty_backlog(relationships_path, missing=False)
        report["inputs"]["parse_error"] = "relationships report must be a JSON object"
        return report

    records = [record for record in payload.get("records", []) if isinstance(record, dict)]
    grouped = _group_for_backlog(records)
    backlog_items = [
        _backlog_item(task_key, group)
        for task_key, group in sorted(grouped.items(), key=lambda item: item[0])
    ]
    backlog_items = [item for item in backlog_items if item is not None]
    backlog_items = _redact_payload(backlog_items)
    backlog_items.sort(
        key=lambda item: (
            _urgency_rank(item["urgency"]),
            -item["relationships_unlocked_count"],
            item["difficulty_rank"],
            item["task_id"],
        )
    )
    summary = _summary(backlog_items)
    sections = _build_sections(backlog_items)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_edge_actions": list(ALLOWED_EDGE_ACTIONS),
        "banner": REPORT_BANNER,
        "backlog_version": BACKLOG_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "relationships_path": str(relationships_path),
        "inputs": {
            "relationships_path": str(relationships_path),
            "total_relationships_read": len(records),
            "missing_input_report": False,
        },
        "summary": summary,
        "sections": sections,
        "items": backlog_items,
    }
    validate_graph_manual_discovery_backlog_report(report)
    return report


def render_graph_manual_discovery_backlog_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Graph Manual Discovery Backlog",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed edge actions: `{', '.join(report['allowed_edge_actions'])}`",
        f"- Backlog version: `{report['backlog_version']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Relationships report: `{report['relationships_path']}`",
        "",
        "## Summary",
        "",
        f"- Total backlog items: {summary['total_items']}",
        f"- HIGH urgency: {summary['by_urgency'].get('HIGH', 0)}",
        f"- MEDIUM urgency: {summary['by_urgency'].get('MEDIUM', 0)}",
        f"- LOW urgency: {summary['by_urgency'].get('LOW', 0)}",
        "",
        "### By vertical",
        "",
        "| Vertical | Count |",
        "| --- | --- |",
    ]
    for entry in summary["by_vertical"]:
        lines.append(f"| `{entry['vertical']}` | {entry['count']} |")
    lines.extend(["", "### By expected_payoff", "", "| Outcome | Count |", "| --- | --- |"])
    for entry in summary["by_expected_payoff"]:
        lines.append(f"| `{entry['expected_payoff']}` | {entry['count']} |")
    for section_name in SECTION_NAMES:
        rows = report["sections"].get(section_name, [])
        title = section_name.replace("_", " ").title()
        lines.extend([
            "",
            f"## {title}",
            "",
            "| Task | Vertical | Manual action | Source / page | Blocker cleared | Relationships unlocked | Urgency | Expected payoff |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ])
        if not rows:
            lines.append("| none |  |  |  |  |  |  |  |")
        for row in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{row['task_id']}`",
                        f"`{row['vertical']}`",
                        _md(row["manual_action"]),
                        _md(row.get("source_page", "")),
                        _md(", ".join(row.get("blocker_cleared", []))),
                        str(row.get("relationships_unlocked_count", 0)),
                        f"`{row['urgency']}`",
                        f"`{row['expected_payoff']}`",
                    ]
                )
                + " |"
            )
    return "\n".join(lines) + "\n"


def validate_graph_manual_discovery_backlog_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("backlog report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("backlog report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("backlog allowed_actions must be WATCH/MANUAL_REVIEW only")
    items = report.get("items")
    if not isinstance(items, list):
        raise SchemaValidationError("backlog items must be a list")
    for index, item in enumerate(items):
        _validate_item(item, f"items[{index}]")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _empty_backlog(relationships_path: Path, *, missing: bool) -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_edge_actions": list(ALLOWED_EDGE_ACTIONS),
        "banner": REPORT_BANNER,
        "backlog_version": BACKLOG_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "relationships_path": str(relationships_path),
        "inputs": {
            "relationships_path": str(relationships_path),
            "total_relationships_read": 0,
            "missing_input_report": missing,
        },
        "summary": {
            "total_items": 0,
            "by_vertical": [],
            "by_expected_payoff": [],
            "by_urgency": {},
        },
        "sections": {name: [] for name in SECTION_NAMES},
        "items": [],
    }


def _group_for_backlog(records: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Group evidence records into backlog tasks.

    Tasks are keyed by ``(vertical, family, primary blocker / manual evidence)``
    so that one task covers all relationships that the same manual evidence
    would unblock. This keeps the backlog short and actionable instead of
    one task per row.
    """

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict):
            continue
        vertical = str(record.get("vertical") or "structural")
        family = str(record.get("family") or "structural")
        primary_blocker = _primary_blocker(record)
        grouped[(vertical, family, primary_blocker)].append(record)
    return grouped


def _primary_blocker(record: dict[str, Any]) -> str:
    blockers = [b for b in (record.get("blockers") or []) if isinstance(b, str)]
    actionable = [
        b
        for b in blockers
        if b
        not in {
            "not_evaluator_input",
            "requires_independent_payoff_verification",
            "settlement_source_not_verified",
            "settlement_time_not_verified",
            "fee_model_not_verified",
            "quote_freshness_not_verified",
        }
    ]
    if actionable:
        return actionable[0]
    needs = record.get("manual_evidence_needed") or []
    if isinstance(needs, list) and needs:
        return f"missing:{needs[0]}"
    return "missing:manual_evidence"


def _backlog_item(
    task_key: tuple[str, str, str],
    group: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not group:
        return None
    vertical, family, blocker = task_key
    sample = group[0]
    relationship_ids = [str(record.get("relationship_id")) for record in group if record.get("relationship_id")]
    blocker_cleared = sorted(
        {
            b
            for record in group
            for b in (record.get("blockers") or [])
            if isinstance(b, str)
        }
    )
    manual_evidence = sorted(
        {
            b
            for record in group
            for b in (record.get("manual_evidence_needed") or [])
            if isinstance(b, str)
        }
    )
    venues = sorted({v for record in group for v in (record.get("venues") or []) if isinstance(v, str)})
    relationship_types = sorted({record.get("relationship_type") for record in group if record.get("relationship_type")})
    urgency_value = _record_urgency(group)
    difficulty_value = DEFAULT_DIFFICULTY.get(family, "MEDIUM")
    expected_payoff_value = _expected_payoff(group, family)
    reusable_scope = _reusable_scope(family)
    manual_action = _manual_action(family, vertical, manual_evidence)
    source_page = _source_page(vertical, family, sample)
    evidence_to_capture = _evidence_to_capture(family, manual_evidence)
    fake_edge_risk = _fake_edge_risk(family, blocker)
    task_id = _task_id(vertical, family, blocker)
    return {
        "task_id": task_id,
        "vertical": vertical,
        "family": family,
        "specific_market_if_known": str(sample.get("left_market_or_source", "")),
        "manual_action": manual_action,
        "source_page": source_page,
        "evidence_to_capture": evidence_to_capture,
        "blocker_cleared": blocker_cleared,
        "primary_blocker": blocker,
        "relationships_unlocked": relationship_ids[:50],
        "relationships_unlocked_count": len(relationship_ids),
        "relationship_types_unlocked": [rt for rt in relationship_types if isinstance(rt, str)],
        "venues": venues,
        "reusable_scope": reusable_scope,
        "expected_payoff": expected_payoff_value,
        "urgency": urgency_value,
        "difficulty": difficulty_value,
        "difficulty_rank": DIFFICULTY_BUCKETS.index(difficulty_value) if difficulty_value in DIFFICULTY_BUCKETS else 1,
        "fake_edge_risk_if_skipped": fake_edge_risk,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
    }


def _record_urgency(group: list[dict[str, Any]]) -> str:
    if any(record.get("can_go_to_relative_value_now") for record in group):
        return "HIGH"
    families = {record.get("family") for record in group}
    for family in families:
        if isinstance(family, str) and DEFAULT_URGENCY.get(family) == "HIGH":
            return "HIGH"
    if any(record.get("relationship_type") in NEAR_EXACT_TYPES for record in group):
        return "HIGH"
    medium = any(DEFAULT_URGENCY.get(record.get("family") or "structural", "LOW") == "MEDIUM" for record in group)
    return "MEDIUM" if medium else "LOW"


def _expected_payoff(group: list[dict[str, Any]], family: str) -> str:
    if any(record.get("relationship_type") in NEAR_EXACT_TYPES for record in group):
        return "enables_exact_review_candidate"
    if any(record.get("can_go_to_relative_value_now") for record in group):
        return "enables_rv_source_review"
    if family in {"payoff_calendar", "settlement_source", "rate_definition", "event_winner", "structural"}:
        return "enables_graph_relationship"
    return "only_improves_basis_risk_map"


def _reusable_scope(family: str) -> str:
    cadence = DEFAULT_REPEAT_CADENCE.get(family, "per_market")
    mapping = {
        "one_time": "one_time",
        "per_market": "per_market",
        "per_event_date": "per_event_date",
        "per_venue_rules_version": "per_venue_rules_version",
        "per_meeting": "per_event_date",
        "per_release": "per_event_date",
        "per_indicator": "per_family",
        "per_season": "per_family",
    }
    scope = mapping.get(cadence, "per_market")
    if scope not in REUSABLE_SCOPES:
        scope = "per_market"
    return scope


def _manual_action(family: str, vertical: str, manual_evidence: list[str]) -> str:
    if vertical == "crypto" and family == "payoff_calendar":
        return "Capture the venue rules text for the payoff shape (touch vs deadline vs daily close vs PIT) and record the observation window in UTC."
    if vertical == "crypto" and family == "settlement_source":
        return "Open the venue rules page, capture the settlement-source URL and index name (e.g. CF Benchmarks BRTI/ERTI vs Binance) for both venues."
    if vertical == "crypto" and family == "observation_time":
        return "Record the observation timestamp in UTC for both venues so the typed-key audit can match windows."
    if vertical == "economics" and family == "rate_definition":
        return "Open the FOMC market rules for both venues; capture whether the strike is midpoint, upper-bound, or effective rate."
    if vertical == "economics" and family == "release_revisions":
        return "Capture the official release schedule and revision rule (first-print vs revised) for both venues."
    if vertical == "economics" and family in {"indicator_source", "indicator_release_time"}:
        return "Confirm the official indicator source URL and release time; capture both venues' settlement source language."
    if vertical == "sports" and family == "event_winner":
        return "Capture the championship definition, team list, and void rule from each venue's rules page (Kalshi rule URL + Polymarket market description)."
    if vertical == "sports" and family == "reference_anchor":
        return "Confirm reference source (sportsbook / oddsfeed) URL and license; never treat as executable."
    if vertical == "sports" and family == "season_void_rules":
        return "Capture each venue's season-void rule text for the season in question."
    if family == "structural":
        return "Confirm exhaustive-group completeness or threshold-ladder coverage from the venue manifest before treating as structural evidence."
    if family == "reference_anchor":
        return "Capture reference source URL and freshness; document license status."
    if family == "weak_signal":
        return "Document why the title overlap is not structural evidence and either close the row or attach a real peer."
    if family == "near_exact_review":
        return "Verify settlement source pair, fee model, orderbook depth, and quote freshness on both legs."
    if manual_evidence:
        return f"Capture {manual_evidence[0]} from venue rules."
    return "Manual evidence capture per family playbook."


def _source_page(vertical: str, family: str, sample: dict[str, Any]) -> str:
    venues = sample.get("venues") or []
    venue_a = venues[0] if isinstance(venues, list) and venues else ""
    venue_b = venues[1] if isinstance(venues, list) and len(venues) > 1 else ""
    if vertical == "crypto":
        return f"{venue_a}/{venue_b} market rules page + CF Benchmarks index reference"
    if vertical == "economics" and family == "rate_definition":
        return f"{venue_a}/{venue_b} FOMC market rules pages + federalreserve.gov/monetarypolicy"
    if vertical == "economics":
        return f"{venue_a}/{venue_b} indicator market rules pages + official release page"
    if vertical == "sports":
        return f"{venue_a}/{venue_b} championship rules page"
    if family == "reference_anchor":
        return "Reference source homepage and license page"
    return f"{venue_a}/{venue_b} venue rules pages"


def _evidence_to_capture(family: str, manual_evidence: list[str]) -> list[str]:
    return manual_evidence or [
        "settlement_source_url",
        "settlement_close_time_utc",
        "payoff_shape_text_from_rules",
    ]


def _fake_edge_risk(family: str, blocker: str) -> str:
    if family == "payoff_calendar":
        return "Touch-vs-point-in-time markets get treated as equivalent and a false near-exact pair is created."
    if family == "rate_definition":
        return "Midpoint-vs-upper-bound differences are ignored and a basis-risk pair is mistakenly called same-payoff."
    if family == "settlement_source":
        return "Different settlement sources resolve differently — same-asset same-date is treated as exact even though indices disagree."
    if family == "event_winner":
        return "Different void / postponement rules cause one venue to resolve YES while the other voids."
    if family == "reference_anchor":
        return "Reference-only sources get treated as executable counterparts."
    if family == "weak_signal":
        return "Title similarity becomes structural evidence and a false relationship is published."
    if family == "structural":
        return "Non-exhaustive groups are treated as exhaustive; sum-over-one violations are missed."
    return "Without manual evidence the graph cannot say *why* the relationship is non-exact and may overstate strength."


def _task_id(vertical: str, family: str, blocker: str) -> str:
    safe_blocker = "".join(c for c in blocker if c.isalnum() or c in "._-:") or "manual"
    return f"manual-task:{vertical}:{family}:{safe_blocker}"


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_vertical: Counter = Counter()
    by_payoff: Counter = Counter()
    by_urgency: Counter = Counter()
    by_difficulty: Counter = Counter()
    for item in items:
        by_vertical[item["vertical"]] += 1
        by_payoff[item["expected_payoff"]] += 1
        by_urgency[item["urgency"]] += 1
        by_difficulty[item["difficulty"]] += 1
    return {
        "total_items": len(items),
        "by_vertical": [
            {"vertical": name, "count": count}
            for name, count in sorted(by_vertical.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "by_expected_payoff": [
            {"expected_payoff": name, "count": count}
            for name, count in sorted(by_payoff.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "by_urgency": {k: by_urgency[k] for k in by_urgency},
        "by_difficulty": {k: by_difficulty[k] for k in by_difficulty},
    }


def _build_sections(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    high = [item for item in items if item["urgency"] == "HIGH"]
    sections = {
        "top_10_overall": items[:10],
        "top_crypto": [item for item in items if item["vertical"] == "crypto"][:10],
        "top_economics": [item for item in items if item["vertical"] == "economics"][:10],
        "top_sports": [item for item in items if item["vertical"] == "sports"][:10],
        "unblocks_most_graph_edges": sorted(
            items,
            key=lambda item: (-item["relationships_unlocked_count"], item["task_id"]),
        )[:10],
        "unblocks_relative_value_review": [
            item
            for item in items
            if item["expected_payoff"] in {"enables_rv_source_review", "enables_exact_review_candidate"}
        ][:10],
        "ignore_for_now": [
            item
            for item in items
            if item["expected_payoff"] == "only_improves_basis_risk_map"
            and item["urgency"] == "LOW"
        ][:10],
    }
    return sections


def _urgency_rank(urgency: str) -> int:
    return URGENCY_BUCKETS.index(urgency) if urgency in URGENCY_BUCKETS else 9


def _validate_item(item: dict[str, Any], path: str) -> None:
    if not isinstance(item, dict):
        raise SchemaValidationError(f"{path} must be an object")
    for key in (
        "task_id",
        "vertical",
        "family",
        "manual_action",
        "source_page",
        "evidence_to_capture",
        "blocker_cleared",
        "relationships_unlocked",
        "reusable_scope",
        "expected_payoff",
        "urgency",
        "difficulty",
        "fake_edge_risk_if_skipped",
        "diagnostic_only",
        "affects_evaluator_gates",
    ):
        if key not in item:
            raise SchemaValidationError(f"{path}.{key} is required")
    if item["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if item["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if item["reusable_scope"] not in REUSABLE_SCOPES:
        raise SchemaValidationError(f"{path}.reusable_scope not allowed")
    if item["expected_payoff"] not in PAYOFF_OUTCOMES:
        raise SchemaValidationError(f"{path}.expected_payoff not allowed")
    if item["urgency"] not in URGENCY_BUCKETS:
        raise SchemaValidationError(f"{path}.urgency not allowed")
    if item["difficulty"] not in DIFFICULTY_BUCKETS:
        raise SchemaValidationError(f"{path}.difficulty not allowed")


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "BACKLOG_VERSION",
    "DIFFICULTY_BUCKETS",
    "PAYOFF_OUTCOMES",
    "REPORT_BANNER",
    "REUSABLE_SCOPES",
    "SECTION_NAMES",
    "URGENCY_BUCKETS",
    "build_graph_manual_discovery_backlog_report",
    "render_graph_manual_discovery_backlog_markdown",
    "validate_graph_manual_discovery_backlog_report",
    "write_graph_manual_discovery_backlog_report",
]
