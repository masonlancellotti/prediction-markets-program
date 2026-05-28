"""Saved-file-only graph manual relationship evidence inventory.

This module is the manual-evidence map of the market graph. It reads
saved relative-value-scanner reports — never imports RV code, never
calls a live API — and turns each non-exact relationship into a
structured *evidence record* that tells Mason exactly what manual work
is needed to convert the row into either an RV source-review or a graph
edge that the daily worklist can promote.

The output covers three verticals:

- **crypto** — payoff calendar / touch vs PIT / range vs threshold.
- **economics** — Fed/FOMC meetings, macro indicators, midpoint vs
  upper-bound, effective rate vs upper bound.
- **sports** — event winner boards (MLB / NBA / NHL), sportsbook
  reference anchors, futures settlement source mismatch.

The evidence inventory is review-only. It never claims exact equality,
never emits PAPER_CANDIDATE / executable=true, and never creates a
relative-value evaluator input.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from graph_engine.relationships.rv_edge_taxonomy import (
    ACTION_BASIS_RISK_REVIEW,
    ACTION_IGNORE_LOW_CONFIDENCE,
    ACTION_MANUAL_REVIEW,
    ACTION_SOURCE_REVIEW,
    ACTION_WATCH,
    ALLOWED_EDGE_ACTIONS,
    REQUIRED_RV_EDGE_BLOCKERS,
    RELATIONSHIP_VERSION,
    RV_RELATIONSHIP_TYPES,
)
from graph_engine.reporting.safety import (
    PROHIBITED_REPORT_PHRASES,
    PROHIBITED_REPORT_TOKENS,
    find_prohibited_rendered_text,
)
from graph_engine.reporting.schema_validation import (
    DIAGNOSTIC_HINT_ACTIONS,
    SchemaValidationError,
    _reject_prohibited_tokens,
)


REPORT_BANNER = (
    "Saved-file-only graph manual relationship evidence inventory. Diagnostic only. "
    "Graph never authorises trading, never creates evaluator inputs, and never claims "
    "exact-payoff equality. Each record describes a non-exact relationship and the "
    "manual evidence needed to advance it."
)

EVIDENCE_VERSION = "manual-relationship-evidence-v1"

VERTICALS = ("crypto", "economics", "sports", "structural")

# Manual-evidence relationship vocabulary — extends the RV edge taxonomy
# with economics + sports specific types and reuses the structural types
# already defined there. Keeping the inventory's vocabulary local means
# we can grow the manual-evidence map without rewriting rv_edge_taxonomy.
MANUAL_RELATIONSHIP_TYPES: dict[str, dict[str, str]] = {
    # Crypto
    "INTRADAY_TOUCH_VS_POINT_IN_TIME": {"vertical": "crypto", "family": "payoff_calendar"},
    "DEADLINE_TOUCH_VS_DAILY_CLOSE": {"vertical": "crypto", "family": "payoff_calendar"},
    "DEADLINE_TOUCH_VS_POINT_IN_TIME": {"vertical": "crypto", "family": "payoff_calendar"},
    "INTRADAY_TOUCH_VS_DAILY_CLOSE": {"vertical": "crypto", "family": "payoff_calendar"},
    "DAILY_DIRECTION_VS_PRICE_THRESHOLD": {"vertical": "crypto", "family": "direction_vs_threshold"},
    "HOURLY_POINT_IN_TIME_VS_DAILY_5PM": {"vertical": "crypto", "family": "payoff_calendar"},
    "WEEKLY_FRIDAY_CLOSE_VS_DEADLINE_TOUCH": {"vertical": "crypto", "family": "payoff_calendar"},
    "RANGE_BUCKET_VS_THRESHOLD": {"vertical": "crypto", "family": "range_bucket"},
    "ALL_TIME_HIGH_BY_DATE_VS_POINT_IN_TIME": {"vertical": "crypto", "family": "ath_by_date"},
    "SAME_ASSET_DIFFERENT_SOURCE": {"vertical": "crypto", "family": "settlement_source"},
    "SAME_ASSET_DIFFERENT_OBSERVATION_TIME": {"vertical": "crypto", "family": "observation_time"},
    "SAME_THRESHOLD_DIFFERENT_WINDOW": {"vertical": "crypto", "family": "observation_window"},
    "SAME_DATE_DIFFERENT_TIMEZONE": {"vertical": "crypto", "family": "timezone"},
    "SAME_ASSET_DIFFERENT_INDEX_SOURCE": {"vertical": "crypto", "family": "settlement_source"},
    "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE": {"vertical": "crypto", "family": "settlement_source"},
    "BASIS_RISK_SAME_ASSET_DIFFERENT_OBSERVATION_TIME": {"vertical": "crypto", "family": "observation_time"},
    "SAME_PAYOFF_CANDIDATE_REVIEW": {"vertical": "crypto", "family": "near_exact_review"},
    "SAME_EVENT_DIFFERENT_SOURCE_REVIEW": {"vertical": "crypto", "family": "near_exact_review"},
    # Economics
    "MIDPOINT_VS_UPPER_BOUND": {"vertical": "economics", "family": "rate_definition"},
    "UPPER_BOUND_VS_EFFECTIVE_RATE": {"vertical": "economics", "family": "rate_definition"},
    "SAME_MEETING_DIFFERENT_RATE_DEFINITION": {"vertical": "economics", "family": "rate_definition"},
    "SAME_RELEASE_DIFFERENT_REVISION_RULES": {"vertical": "economics", "family": "release_revisions"},
    "SAME_INDICATOR_DIFFERENT_SOURCE": {"vertical": "economics", "family": "indicator_source"},
    "SAME_INDICATOR_DIFFERENT_RELEASE_TIME": {"vertical": "economics", "family": "indicator_release_time"},
    "SAME_EVENT_SAME_THRESHOLD_REVIEW": {"vertical": "economics", "family": "near_exact_review"},
    # Sports
    "SAME_EVENT_WINNER": {"vertical": "sports", "family": "event_winner"},
    "SAME_TEAM_DIFFERENT_EVENT_SCOPE": {"vertical": "sports", "family": "event_scope"},
    "SAME_SEASON_DIFFERENT_VOID_RULES": {"vertical": "sports", "family": "season_void_rules"},
    "SPORTSBOOK_REFERENCE_ONLY": {"vertical": "sports", "family": "reference_anchor"},
    "FUTURES_SETTLEMENT_SOURCE_MISMATCH": {"vertical": "sports", "family": "settlement_source"},
    "EVENT_WINNER_SAME_FIELD_REVIEW": {"vertical": "sports", "family": "event_winner"},
    # Structural (cross-vertical)
    "SUBSET_SUPERSET": {"vertical": "structural", "family": "structural"},
    "COMPLEMENT": {"vertical": "structural", "family": "structural"},
    "MUTUALLY_EXCLUSIVE": {"vertical": "structural", "family": "structural"},
    "EXHAUSTIVE_GROUP_MEMBER": {"vertical": "structural", "family": "structural"},
    "THRESHOLD_LADDER_NEIGHBOR": {"vertical": "structural", "family": "threshold_ladder"},
    "THRESHOLD_LADDER_INVERSION_WATCH": {"vertical": "structural", "family": "threshold_ladder"},
    "RANGE_BUCKET_PARTITION": {"vertical": "structural", "family": "range_partition"},
    "EVENT_WINNER_SAME_FIELD": {"vertical": "structural", "family": "event_winner"},
    # Reference / weak
    "FAIR_VALUE_REFERENCE_ONLY": {"vertical": "structural", "family": "reference_anchor"},
    "TRUTH_FEED_ANCHOR_ONLY": {"vertical": "structural", "family": "reference_anchor"},
    "TITLE_SIMILARITY_ONLY": {"vertical": "structural", "family": "weak_signal"},
    "SAME_TOPIC_WEAK_SIGNAL": {"vertical": "structural", "family": "weak_signal"},
    "AMBIGUOUS_RELATIONSHIP": {"vertical": "structural", "family": "weak_signal"},
    "NO_CURRENT_PEER": {"vertical": "structural", "family": "weak_signal"},
}

NEAR_EXACT_TYPES = {
    "SAME_PAYOFF_CANDIDATE_REVIEW",
    "SAME_EVENT_DIFFERENT_SOURCE_REVIEW",
    "SAME_EVENT_SAME_THRESHOLD_REVIEW",
    "EVENT_WINNER_SAME_FIELD_REVIEW",
}

# Default repeat cadence per family.  These are the "how often does this
# manual work decay" hints — sportsbook URLs decay per event date, Fed
# meeting rules per meeting, payoff-calendar conventions per venue rules
# version.
DEFAULT_REPEAT_CADENCE: dict[str, str] = {
    "near_exact_review": "per_market",
    "payoff_calendar": "per_venue_rules_version",
    "direction_vs_threshold": "per_event_date",
    "settlement_source": "per_venue_rules_version",
    "observation_time": "per_market",
    "observation_window": "per_market",
    "timezone": "one_time",
    "range_bucket": "per_event_date",
    "ath_by_date": "per_event_date",
    "rate_definition": "per_meeting",
    "release_revisions": "per_release",
    "indicator_source": "per_indicator",
    "indicator_release_time": "per_indicator",
    "event_winner": "per_event_date",
    "event_scope": "per_event_date",
    "season_void_rules": "per_season",
    "reference_anchor": "per_venue_rules_version",
    "structural": "per_market",
    "threshold_ladder": "per_event_date",
    "range_partition": "per_event_date",
    "weak_signal": "one_time",
}

# Default urgency: how soon manual review is worth doing.  HIGH means
# the result unblocks ready-now RV review; MEDIUM means it improves the
# graph but doesn't immediately enable RV; LOW means it only helps the
# basis-risk map.
DEFAULT_URGENCY: dict[str, str] = {
    "near_exact_review": "HIGH",
    "payoff_calendar": "MEDIUM",
    "direction_vs_threshold": "MEDIUM",
    "settlement_source": "HIGH",
    "observation_time": "MEDIUM",
    "observation_window": "MEDIUM",
    "rate_definition": "HIGH",
    "release_revisions": "MEDIUM",
    "indicator_source": "MEDIUM",
    "indicator_release_time": "MEDIUM",
    "event_winner": "HIGH",
    "event_scope": "MEDIUM",
    "season_void_rules": "MEDIUM",
    "reference_anchor": "LOW",
    "structural": "MEDIUM",
    "threshold_ladder": "MEDIUM",
    "range_partition": "MEDIUM",
    "weak_signal": "LOW",
}

DEFAULT_DIFFICULTY: dict[str, str] = {
    "near_exact_review": "MEDIUM",
    "payoff_calendar": "EASY",
    "direction_vs_threshold": "EASY",
    "settlement_source": "EASY",
    "observation_time": "EASY",
    "observation_window": "EASY",
    "rate_definition": "MEDIUM",
    "release_revisions": "MEDIUM",
    "indicator_source": "MEDIUM",
    "indicator_release_time": "MEDIUM",
    "event_winner": "EASY",
    "event_scope": "EASY",
    "season_void_rules": "MEDIUM",
    "reference_anchor": "EASY",
    "structural": "MEDIUM",
    "threshold_ladder": "MEDIUM",
    "range_partition": "MEDIUM",
    "weak_signal": "EASY",
}


def write_graph_manual_relationship_evidence_report(
    *,
    rv_reports_dir: Path | str,
    edges_report_path: Path | str | None = None,
    json_output: Path | str,
    markdown_output: Path | str,
) -> dict[str, Any]:
    """Build and write the manual relationship evidence report."""

    report = build_graph_manual_relationship_evidence_report(
        rv_reports_dir=Path(rv_reports_dir),
        edges_report_path=Path(edges_report_path) if edges_report_path else None,
    )
    markdown = render_graph_manual_relationship_evidence_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "graph manual evidence Markdown contains prohibited vocabulary: " + ", ".join(findings)
        )
    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def build_graph_manual_relationship_evidence_report(
    *,
    rv_reports_dir: Path,
    edges_report_path: Path | None = None,
) -> dict[str, Any]:
    """Build the manual evidence inventory from saved RV reports."""

    rv_reports_dir = Path(rv_reports_dir)
    parsed_inputs: dict[str, dict[str, Any]] = {}
    available: list[str] = []
    missing: list[str] = []
    parse_errors: list[dict[str, Any]] = []
    for name in _supported_files():
        path = rv_reports_dir / name
        if not path.exists():
            missing.append(name)
            continue
        payload, err = _load_json(path)
        if err is not None:
            parse_errors.append({"report": name, "path": str(path), "error": err})
            continue
        if isinstance(payload, dict):
            parsed_inputs[name] = payload
            available.append(name)

    edges_payload: dict[str, Any] | None = None
    if edges_report_path is not None and edges_report_path.exists():
        try:
            candidate = json.loads(edges_report_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                edges_payload = candidate
        except (OSError, ValueError):
            edges_payload = None

    records: list[dict[str, Any]] = []
    # Seed from existing graph edges
    if isinstance(edges_payload, dict):
        for edge in edges_payload.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            rec = _record_from_existing_edge(edge)
            if rec is not None:
                records.append(rec)
    # Add records from RV reports that the rv_edge_taxonomy ingester did
    # not cover (Fed manifest, sports same-payoff boards, near-miss
    # diagnostics, crypto payoff-calendar audit).
    records.extend(_records_from_crypto_payoff_calendar(parsed_inputs))
    records.extend(_records_from_crypto_manual_workbench(parsed_inputs))
    records.extend(_records_from_fed_manifest(parsed_inputs))
    records.extend(_records_from_fed_structural_basket(parsed_inputs))
    records.extend(_records_from_fed_family_graduation(parsed_inputs))
    records.extend(_records_from_sports_same_payoff_boards(parsed_inputs))
    records.extend(_records_from_non_sports_near_miss(parsed_inputs))
    records.extend(_records_from_sx_bet(parsed_inputs))

    # Deduplicate by relationship_id and sanitize text fields.
    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        sanitized = _redact_payload(record)
        rid = sanitized.get("relationship_id")
        if not isinstance(rid, str) or not rid:
            continue
        if rid in deduped:
            deduped[rid] = _merge_records(deduped[rid], sanitized)
        else:
            deduped[rid] = sanitized
    final_records = list(deduped.values())

    summary = _summarize_records(final_records)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_edge_actions": list(ALLOWED_EDGE_ACTIONS),
        "banner": REPORT_BANNER,
        "evidence_version": EVIDENCE_VERSION,
        "relationship_version": RELATIONSHIP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rv_reports_dir": str(rv_reports_dir),
        "edges_report_path": str(edges_report_path) if edges_report_path else None,
        "inputs": {
            "available": sorted(available),
            "missing": sorted(missing),
            "parse_errors": parse_errors,
            "edges_seeded": edges_payload is not None,
        },
        "summary": summary,
        "records": final_records,
        "safety_summary": _safety_summary(),
    }
    validate_graph_manual_relationship_evidence_report(report)
    return report


def validate_graph_manual_relationship_evidence_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("manual evidence report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("manual evidence report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("manual evidence allowed_actions must be WATCH/MANUAL_REVIEW only")
    if report.get("allowed_edge_actions") != list(ALLOWED_EDGE_ACTIONS):
        raise SchemaValidationError("manual evidence allowed_edge_actions must match taxonomy")
    if not isinstance(report.get("records"), list):
        raise SchemaValidationError("manual evidence records must be a list")
    for index, record in enumerate(report["records"]):
        _validate_record(record, f"records[{index}]")


def render_graph_manual_relationship_evidence_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Graph Manual Relationship Evidence",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed edge actions: `{', '.join(report['allowed_edge_actions'])}`",
        f"- Evidence version: `{report['evidence_version']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- RV reports dir: `{report['rv_reports_dir']}`",
        "",
        "## Summary",
        "",
        f"- Total records: {summary['total_records']}",
        f"- Records ready for RV source-review now: {summary['ready_for_rv_now']}",
        f"- Records blocked on manual evidence: {summary['blocked_on_manual_evidence']}",
        "",
        "### By vertical",
        "",
        "| Vertical | Records |",
        "| --- | --- |",
    ]
    for entry in summary["records_by_vertical"]:
        lines.append(f"| `{entry['vertical']}` | {entry['count']} |")
    lines.extend(["", "### By relationship_type", "", "| Type | Count |", "| --- | --- |"])
    for entry in summary["records_by_relationship_type"][:25]:
        lines.append(f"| `{entry['relationship_type']}` | {entry['count']} |")
    lines.extend(["", "### Top blockers", "", "| Blocker | Count |", "| --- | --- |"])
    for entry in summary["top_blockers"]:
        lines.append(f"| `{entry['blocker']}` | {entry['count']} |")
    lines.extend(["", "### Top manual evidence needed", "", "| Manual evidence | Count |", "| --- | --- |"])
    for entry in summary["top_manual_evidence_needed"]:
        lines.append(f"| `{entry['manual_evidence']}` | {entry['count']} |")
    lines.extend(["", "## Records (first 50)", ""])
    lines.extend([
        "| Vertical | Relationship | Action | Ready for RV | Why not exact |",
        "| --- | --- | --- | --- | --- |",
    ])
    for record in report["records"][:50]:
        ready = "yes" if record["can_go_to_relative_value_now"] else "no"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{record['vertical']}`",
                    f"`{record['relationship_type']}`",
                    f"`{record['current_action']}`",
                    ready,
                    _md(record.get("why_not_exact", "")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Per-source ingesters
# ---------------------------------------------------------------------------


def _supported_files() -> list[str]:
    return [
        "crypto_payoff_calendar_audit.json",
        "crypto_manual_discovery_workbench.json",
        "manifest_candidate_scout_fed.json",
        "structural_basket_review_fed.json",
        "family_graduation_fed.json",
        "mlb_world_series_same_payoff_board.json",
        "nba_kxnba_same_payoff_board.json",
        "nhl_stanley_cup_same_payoff_board.json",
        "non_sports_near_miss_diagnostics.json",
        "sx_bet_sports_overlap.json",
        "sx_bet_sports_overlap_game_level.json",
        "default_sports_48h_sweep_summary.json",
    ]


def _record_from_existing_edge(edge: dict[str, Any]) -> dict[str, Any] | None:
    relationship_type = edge.get("relationship_type")
    if not isinstance(relationship_type, str):
        return None
    if relationship_type not in MANUAL_RELATIONSHIP_TYPES:
        # Fall through to "structural" bucket if the type isn't in our
        # extended vocabulary. This keeps the inventory honest about
        # what types we currently understand.
        meta = {"vertical": "structural", "family": "structural"}
    else:
        meta = MANUAL_RELATIONSHIP_TYPES[relationship_type]
    evidence_fields = edge.get("evidence_fields") if isinstance(edge.get("evidence_fields"), dict) else {}
    blockers = [b for b in (edge.get("blockers") or []) if isinstance(b, str)]
    manual_evidence_needed = _manual_evidence_for(relationship_type, blockers, evidence_fields)
    why_related, why_not_exact = _why_pair(relationship_type, evidence_fields)
    ready_now, rv_must_verify, missing_info = _readiness(relationship_type, blockers, edge, evidence_fields)
    current_action = _current_action_for(edge, relationship_type)
    return {
        "relationship_id": str(edge.get("edge_id") or ""),
        "vertical": meta["vertical"],
        "family": meta["family"],
        "left_market_or_source": edge.get("left_market_id") or "",
        "right_market_or_source": edge.get("right_market_id") or edge.get("right_reference_id") or "",
        "venues": [edge.get("left_venue") or "unknown", edge.get("right_venue") or "unknown"],
        "relationship_type": relationship_type,
        "payoff_shape_left": evidence_fields.get("kalshi_market_shape")
        or evidence_fields.get("cdna_market_shape")
        or evidence_fields.get("polymarket_market_shape"),
        "payoff_shape_right": evidence_fields.get("polymarket_market_shape")
        or evidence_fields.get("cdna_market_shape")
        or evidence_fields.get("kalshi_market_shape"),
        "why_related": why_related,
        "why_not_exact": why_not_exact,
        "blockers": blockers,
        "manual_evidence_needed": manual_evidence_needed,
        "evidence_priority": DEFAULT_URGENCY.get(meta["family"], "MEDIUM"),
        "repeat_cadence": DEFAULT_REPEAT_CADENCE.get(meta["family"], "per_market"),
        "current_action": current_action,
        "can_go_to_relative_value_now": ready_now,
        "rv_must_verify": rv_must_verify if ready_now else [],
        "manual_info_missing": missing_info if not ready_now else [],
        "source_reports": list(edge.get("source_report_paths") or []),
        "evidence_fields": evidence_fields,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }


def _records_from_crypto_payoff_calendar(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("crypto_payoff_calendar_audit.json")
    if not payload:
        return []
    rows = payload.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        best_peer = row.get("best_peer") if isinstance(row.get("best_peer"), dict) else {}
        if not best_peer:
            continue
        left_shape = str(row.get("payoff_shape") or "").lower()
        right_shape = str(best_peer.get("payoff_shape") or "").lower()
        relationship_type = _crypto_payoff_relationship(left_shape, right_shape)
        record_id = f"manual-evidence:crypto_payoff:{row.get('asset')}:{row.get('market_id') or row.get('condition_id') or row.get('question')}:{best_peer.get('row_id')}"
        blockers = [b for b in (row.get("blockers") or []) if isinstance(b, str)]
        record = _new_record(
            relationship_id=record_id,
            relationship_type=relationship_type,
            left_market=str(row.get("market_id") or row.get("event_ticker") or row.get("condition_id") or "kalshi:unknown"),
            right_market=str(best_peer.get("row_id") or ""),
            venues=["kalshi", str(best_peer.get("venue") or "polymarket")],
            payoff_shape_left=row.get("payoff_shape"),
            payoff_shape_right=best_peer.get("payoff_shape"),
            blockers=blockers,
            evidence_fields={
                "asset": row.get("asset"),
                "comparator": row.get("comparator"),
                "threshold": row.get("threshold"),
                "observation_time": row.get("observation_time"),
                "observation_timezone": row.get("observation_timezone"),
                "best_peer": best_peer,
                "comparability_class": row.get("comparability_class"),
            },
            source_reports=["crypto_payoff_calendar_audit.json"],
        )
        if record:
            out.append(record)
    return out


def _records_from_crypto_manual_workbench(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("crypto_manual_discovery_workbench.json")
    if not payload:
        return []
    groups = payload.get("groups") or []
    out: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        targets = group.get("targets") or []
        venue = group.get("venue") or "kalshi"
        for target in targets:
            if not isinstance(target, dict):
                continue
            blockers = [b for b in (target.get("blockers") or []) if isinstance(b, str)]
            record_id = f"manual-evidence:crypto_workbench:{venue}:{target.get('audit_row_id') or target.get('market_id') or target.get('asset')}"
            shape = (target.get("payoff_shape") or group.get("payoff_shapes") or [""])[0]
            relationship_type = _crypto_payoff_relationship(shape, shape)
            record = _new_record(
                relationship_id=record_id,
                relationship_type=relationship_type,
                left_market=str(target.get("audit_row_id") or target.get("market_id") or "unknown"),
                right_market="manual_discovery_required",
                venues=[venue, "manual_discovery"],
                payoff_shape_left=shape,
                payoff_shape_right=shape,
                blockers=blockers + ["manual_discovery_required"],
                evidence_fields={
                    "discovery_action_text": target.get("discovery_action_text"),
                    "evidence_checklist": group.get("evidence_checklist"),
                    "comparability_class": target.get("comparability_class"),
                    "asset": target.get("asset"),
                },
                source_reports=["crypto_manual_discovery_workbench.json"],
            )
            if record:
                out.append(record)
    return out


def _records_from_fed_manifest(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("manifest_candidate_scout_fed.json")
    if not payload:
        return []
    rows = payload.get("rows") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_ticker = row.get("event_ticker") or row.get("venue_native_event_id")
        if not event_ticker:
            continue
        # Each scout row is a multi-market FOMC group. We emit one
        # SAME_MEETING_DIFFERENT_RATE_DEFINITION record for the manifest
        # plus an EXHAUSTIVE_GROUP_MEMBER record for the multi-leg structure.
        blockers = [b for b in (row.get("missing_metadata_blockers") or []) if isinstance(b, str)]
        relationship_type = "SAME_MEETING_DIFFERENT_RATE_DEFINITION"
        record_id = f"manual-evidence:fed_manifest:{event_ticker}"
        rec = _new_record(
            relationship_id=record_id,
            relationship_type=relationship_type,
            left_market=str(event_ticker),
            right_market="federalreserve:upcoming_meeting",
            venues=[str(row.get("venue") or "kalshi"), "federalreserve"],
            payoff_shape_left="binary_yes_no",
            payoff_shape_right="committee_decision",
            blockers=blockers,
            evidence_fields={
                "candidate_type": row.get("candidate_type"),
                "market_count": row.get("market_count"),
                "apparent_outcome_count": row.get("apparent_outcome_count"),
                "has_shared_rules": row.get("has_shared_rules"),
                "depth_summary": row.get("depth_summary"),
            },
            source_reports=["manifest_candidate_scout_fed.json"],
        )
        if rec:
            out.append(rec)
        # Multi-leg structural relationship
        struct_rec = _new_record(
            relationship_id=f"manual-evidence:fed_manifest_structural:{event_ticker}",
            relationship_type="EXHAUSTIVE_GROUP_MEMBER",
            left_market=str(event_ticker),
            right_market=f"kalshi:{event_ticker}_outcome_set",
            venues=["kalshi", "kalshi"],
            payoff_shape_left="binary_yes_no",
            payoff_shape_right="exhaustive_group",
            blockers=blockers,
            evidence_fields={
                "market_count": row.get("market_count"),
                "apparent_outcome_count": row.get("apparent_outcome_count"),
                "not_exhaustive_evidence": row.get("not_exhaustive_evidence"),
            },
            source_reports=["manifest_candidate_scout_fed.json"],
        )
        if struct_rec:
            out.append(struct_rec)
    return out


def _records_from_fed_structural_basket(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("structural_basket_review_fed.json")
    if not payload:
        return []
    rows = payload.get("rows") or []
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        record_id = f"manual-evidence:fed_basket:{row.get('event_ticker') or row.get('group_id') or index}"
        blockers = [b for b in (row.get("blockers") or row.get("review_blockers") or []) if isinstance(b, str)]
        relationship_type = "MIDPOINT_VS_UPPER_BOUND"
        rec = _new_record(
            relationship_id=record_id,
            relationship_type=relationship_type,
            left_market=str(row.get("event_ticker") or "kalshi:fed_basket"),
            right_market=str(row.get("group_id") or "polymarket:fed_basket"),
            venues=["kalshi", "polymarket"],
            payoff_shape_left="upper_bound_threshold",
            payoff_shape_right="midpoint_range",
            blockers=blockers,
            evidence_fields={
                "structural_basket": True,
                "row_summary": {k: v for k, v in row.items() if isinstance(v, (str, int, float, bool, type(None)))},
            },
            source_reports=["structural_basket_review_fed.json"],
        )
        if rec:
            out.append(rec)
    return out


def _records_from_fed_family_graduation(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("family_graduation_fed.json")
    if not payload:
        return []
    proposals = payload.get("registry_proposals") or []
    out: list[dict[str, Any]] = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            continue
        blockers = [b for b in (proposal.get("blockers") or []) if isinstance(b, str)]
        record_id = f"manual-evidence:fed_family_graduation:{proposal.get('proposal_id') or index}"
        rec = _new_record(
            relationship_id=record_id,
            relationship_type="SAME_MEETING_DIFFERENT_RATE_DEFINITION",
            left_market=str(proposal.get("proposal_id") or proposal.get("series_ticker") or "kalshi:fed_family"),
            right_market="federalreserve:fomc_meeting",
            venues=["kalshi", "federalreserve"],
            payoff_shape_left="upper_bound_threshold",
            payoff_shape_right="committee_decision",
            blockers=blockers,
            evidence_fields={"proposal_kind": proposal.get("kind"), "proposal_summary": proposal.get("description")},
            source_reports=["family_graduation_fed.json"],
        )
        if rec:
            out.append(rec)
    return out


def _records_from_sports_same_payoff_boards(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    boards = {
        "mlb_world_series_same_payoff_board.json": ("mlb_world_series", "kalshi", "polymarket"),
        "nba_kxnba_same_payoff_board.json": ("nba_finals", "kalshi", "polymarket"),
        "nhl_stanley_cup_same_payoff_board.json": ("nhl_stanley_cup", "kalshi", "polymarket"),
    }
    for filename, (label, left_venue, right_venue) in boards.items():
        payload = parsed.get(filename)
        if not payload:
            continue
        rows = payload.get("rows") or []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
            polymarket = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
            blockers = sorted(
                set(
                    [b for b in (row.get("blockers") or []) if isinstance(b, str)]
                    + [b for b in (row.get("info_blockers") or []) if isinstance(b, str)]
                    + [b for b in (row.get("strict_blockers") or []) if isinstance(b, str)]
                )
            )
            if row.get("same_payoff") is True:
                relationship_type = "EVENT_WINNER_SAME_FIELD_REVIEW"
            else:
                relationship_type = "SAME_EVENT_WINNER"
            record_id = f"manual-evidence:sports:{label}:{kalshi.get('ticker') or polymarket.get('market_id') or index}"
            rec = _new_record(
                relationship_id=record_id,
                relationship_type=relationship_type,
                left_market=str(kalshi.get("ticker") or f"{left_venue}:{label}"),
                right_market=str(polymarket.get("market_id") or f"{right_venue}:{label}"),
                venues=[left_venue, right_venue],
                payoff_shape_left="event_winner_binary",
                payoff_shape_right="event_winner_binary",
                blockers=blockers,
                evidence_fields={
                    "kalshi_question": kalshi.get("question"),
                    "polymarket_question": polymarket.get("question"),
                    "recommended_next_action": row.get("recommended_next_action"),
                    "same_payoff_label": row.get("same_payoff"),
                    "similarity_score": row.get("similarity_score"),
                    "strict_pass_count": row.get("strict_pass_count"),
                    "strict_blockers": row.get("strict_blockers"),
                },
                source_reports=[filename],
            )
            if rec:
                out.append(rec)
    return out


def _records_from_non_sports_near_miss(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    payload = parsed.get("non_sports_near_miss_diagnostics.json")
    if not payload:
        return []
    near_misses = payload.get("near_misses") or []
    out: list[dict[str, Any]] = []
    for index, row in enumerate(near_misses):
        if not isinstance(row, dict):
            continue
        category = (row.get("category") or "other").lower()
        if category in {"companies", "company_metric", "regulatory"}:
            relationship_type = "SAME_TOPIC_WEAK_SIGNAL"
        elif category in {"crypto", "macro", "fed"}:
            relationship_type = "SAME_INDICATOR_DIFFERENT_SOURCE"
        else:
            relationship_type = "AMBIGUOUS_RELATIONSHIP"
        kalshi = row.get("kalshi") if isinstance(row.get("kalshi"), dict) else {}
        polymarket = row.get("polymarket") if isinstance(row.get("polymarket"), dict) else {}
        record_id = f"manual-evidence:near_miss:{category}:{kalshi.get('ticker') or polymarket.get('market_id') or index}"
        rec = _new_record(
            relationship_id=record_id,
            relationship_type=relationship_type,
            left_market=str(kalshi.get("ticker") or "kalshi:near_miss"),
            right_market=str(polymarket.get("market_id") or "polymarket:near_miss"),
            venues=["kalshi", "polymarket"],
            payoff_shape_left="binary_yes_no",
            payoff_shape_right="binary_yes_no",
            blockers=[
                b for b in (row.get("blocker_labels") or []) if isinstance(b, str)
            ] + ["title_similarity_not_structural_evidence"],
            evidence_fields={
                "category": category,
                "matched_fields": row.get("matched_fields"),
                "recommended_next_step": row.get("recommended_next_step"),
                "kalshi_title": kalshi.get("title_or_question"),
                "polymarket_title": polymarket.get("title_or_question"),
            },
            source_reports=["non_sports_near_miss_diagnostics.json"],
        )
        if rec:
            out.append(rec)
    return out


def _records_from_sx_bet(parsed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in ("sx_bet_sports_overlap.json", "sx_bet_sports_overlap_game_level.json"):
        payload = parsed.get(name)
        if not payload:
            continue
        rows = payload.get("rows") or []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            record_id = f"manual-evidence:sx_bet:{row.get('sx_bet_market_id') or index}"
            blockers = [
                b for b in (row.get("blockers") or row.get("reasons") or []) if isinstance(b, str)
            ]
            rec = _new_record(
                relationship_id=record_id,
                relationship_type="FUTURES_SETTLEMENT_SOURCE_MISMATCH",
                left_market=str(row.get("sx_bet_market_id") or "sx_bet:unknown"),
                right_market=str(row.get("kalshi_ticker") or row.get("polymarket_market_id") or "kalshi:unknown"),
                venues=["sx_bet", str(row.get("counterpart_venue") or "kalshi")],
                payoff_shape_left=row.get("market_shape") or "binary_yes_no",
                payoff_shape_right="binary_yes_no",
                blockers=blockers + ["sx_bet_queued_access_blocked"],
                evidence_fields={"sx_bet_typed_keys": row.get("sx_bet_typed_keys")},
                source_reports=[name],
            )
            if rec:
                out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_record(
    *,
    relationship_id: str,
    relationship_type: str,
    left_market: str,
    right_market: str,
    venues: list[str],
    payoff_shape_left: Any,
    payoff_shape_right: Any,
    blockers: Iterable[str],
    evidence_fields: dict[str, Any],
    source_reports: list[str],
) -> dict[str, Any] | None:
    if relationship_type not in MANUAL_RELATIONSHIP_TYPES:
        meta = {"vertical": "structural", "family": "structural"}
    else:
        meta = MANUAL_RELATIONSHIP_TYPES[relationship_type]
    blocker_list = sorted({b for b in blockers if isinstance(b, str) and b})
    manual_evidence = _manual_evidence_for(relationship_type, blocker_list, evidence_fields)
    why_related, why_not_exact = _why_pair(relationship_type, evidence_fields)
    ready_now, rv_must_verify, missing_info = _readiness(
        relationship_type,
        blocker_list,
        edge={"relationship_type": relationship_type, "confidence_bucket": "low"},
        evidence_fields=evidence_fields,
    )
    current_action = _action_for_record(relationship_type, blocker_list, ready_now)
    record = {
        "relationship_id": relationship_id,
        "vertical": meta["vertical"],
        "family": meta["family"],
        "left_market_or_source": left_market,
        "right_market_or_source": right_market,
        "venues": list(venues),
        "relationship_type": relationship_type,
        "payoff_shape_left": payoff_shape_left,
        "payoff_shape_right": payoff_shape_right,
        "why_related": why_related,
        "why_not_exact": why_not_exact,
        "blockers": blocker_list,
        "manual_evidence_needed": manual_evidence,
        "evidence_priority": DEFAULT_URGENCY.get(meta["family"], "MEDIUM"),
        "repeat_cadence": DEFAULT_REPEAT_CADENCE.get(meta["family"], "per_market"),
        "current_action": current_action,
        "can_go_to_relative_value_now": ready_now,
        "rv_must_verify": rv_must_verify if ready_now else [],
        "manual_info_missing": missing_info if not ready_now else [],
        "source_reports": list(source_reports),
        "evidence_fields": evidence_fields,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }
    return record


def _why_pair(relationship_type: str, evidence_fields: dict[str, Any]) -> tuple[str, str]:
    family = MANUAL_RELATIONSHIP_TYPES.get(relationship_type, {}).get("family", "structural")
    if family == "payoff_calendar":
        return (
            "Markets share asset and date but have different observation windows (touch / deadline / point-in-time / 5pm close).",
            "Touch-vs-point-in-time or deadline-vs-close payoff shapes never produce identical payoff vectors; they only converge in special cases that require manual evidence.",
        )
    if family == "rate_definition":
        return (
            "Both markets resolve on the same FOMC meeting outcome.",
            "Markets use different rate definitions (midpoint vs upper-bound vs effective rate); resolving with the same direction does not imply the same threshold.",
        )
    if family == "settlement_source":
        return (
            "Same underlying asset and time window across venues.",
            "Markets use different settlement sources / index references (e.g. CF Benchmarks vs Binance); cannot be treated as equal without source review.",
        )
    if family == "observation_time":
        return (
            "Same asset and date but different observation timestamps.",
            "Hourly point-in-time vs daily 5pm vs weekly close create payoff drift even when the threshold matches.",
        )
    if family == "range_bucket":
        return (
            "Both markets cover the same asset and time window.",
            "Range bucket pays the bucket containing the close; threshold pays on >= or <= comparator. Different payoffs at boundaries.",
        )
    if family == "ath_by_date":
        return (
            "Same asset and end date.",
            "All-time-high-by-date pays if the threshold is touched any time before the deadline; point-in-time pays only on the close. Touch vs close is not the same payoff.",
        )
    if family == "event_winner":
        return (
            "Same event (championship / season) with the same field of teams.",
            "Settlement source, void rules, and tiebreak rules may differ across venues; same-winner labels do not imply identical payoff vectors.",
        )
    if family == "reference_anchor":
        return (
            "Reference / fair-value anchor (sportsbook, central bank dot plot, oracle feed) used for context.",
            "Reference-only sources are not executable counterparts.",
        )
    if family == "indicator_source":
        return (
            "Both markets reference the same macro indicator.",
            "Different indicator sources or release feeds (BLS vs official statistic vs derived index) can disagree on the final value.",
        )
    if family == "indicator_release_time":
        return (
            "Same macro indicator across venues.",
            "Release times and revision windows differ; markets can settle on different observation instants.",
        )
    if family == "weak_signal":
        return (
            "Title-level / topical similarity between two markets.",
            "Title text alone is not structural evidence; markets may resolve on entirely different criteria.",
        )
    if family == "structural":
        return (
            "Logical structural relationship (subset / complement / threshold ladder) between markets in the same group.",
            "Structural pricing inequality is diagnostic only and never asserts equality of payoff vectors.",
        )
    if family == "release_revisions":
        return (
            "Same macro release across venues.",
            "Revision rules differ (first-print vs revised); markets may resolve on different print values.",
        )
    if family == "season_void_rules":
        return (
            "Same sports season across venues.",
            "Season-void / cancellation rules differ across venues; settlement may differ on cancelled events.",
        )
    if family == "near_exact_review":
        return (
            "Markets look like they share asset / date / threshold / comparator from typed-key evidence.",
            "Graph never proves exact same-payoff equality on its own; strict RV review (settlement source, fee, depth, freshness) is required.",
        )
    return (
        "Related under the graph relationship taxonomy.",
        "Graph relationships are never executable inputs; manual review of settlement source, time, and comparator is required.",
    )


def _manual_evidence_for(
    relationship_type: str,
    blockers: list[str],
    evidence_fields: dict[str, Any] | None,
) -> list[str]:
    needed: list[str] = []
    family = MANUAL_RELATIONSHIP_TYPES.get(relationship_type, {}).get("family", "structural")
    if family in {"payoff_calendar", "observation_time", "observation_window", "ath_by_date"}:
        needed.extend([
            "settlement_close_time_utc",
            "observation_window_start_end",
            "payoff_shape_text_from_rules",
            "venue_rules_url",
        ])
    if family in {"settlement_source", "indicator_source"}:
        needed.extend([
            "settlement_source_url",
            "index_reference_or_oracle_name",
            "settlement_source_screenshot",
        ])
    if family in {"rate_definition", "release_revisions"}:
        needed.extend([
            "venue_rate_definition_text",
            "official_release_or_meeting_url",
            "revision_window_text",
        ])
    if family == "range_bucket":
        needed.extend([
            "bucket_partition_text",
            "tie_break_rule",
        ])
    if family == "event_winner":
        needed.extend([
            "venue_settlement_rule_for_void_or_postpone",
            "team_list_and_tie_break_rule",
            "championship_definition_text",
        ])
    if family == "reference_anchor":
        needed.extend([
            "reference_source_url",
            "data_license_status",
        ])
    if family == "season_void_rules":
        needed.extend([
            "venue_void_rule_text",
            "season_cancellation_handling_text",
        ])
    if family == "indicator_release_time":
        needed.extend([
            "official_release_time_utc",
            "revision_schedule",
        ])
    if family == "structural":
        needed.extend([
            "exhaustive_group_completeness_evidence",
            "structural_payoff_matrix",
        ])
    if family == "near_exact_review":
        needed.extend([
            "settlement_source_pair_match",
            "fee_model_text",
            "orderbook_depth_freshness_capture",
            "quote_freshness_capture",
        ])
    if "manual_discovery_required" in blockers:
        needed.append("peer_market_discovery_query")
    if "stale_quote" in blockers or "stale_or_missing_quote" in blockers or "missing_quote" in blockers:
        needed.append("fresh_orderbook_capture")
    if "fee_model_missing" in blockers or "fee_model_not_verified" in blockers:
        needed.append("fee_model_text")
    if "kalshi_orderbook_not_enriched" in blockers or "polymarket_orderbook_not_enriched" in blockers:
        needed.append("fresh_orderbook_capture")
    if "settlement_source_mismatch" in blockers or "settlement_source_unverified" in blockers:
        needed.append("settlement_source_pair_match")
    # de-dup but keep stable order
    seen: set[str] = set()
    dedup: list[str] = []
    for item in needed:
        if item not in seen:
            seen.add(item)
            dedup.append(item)
    return dedup


def _readiness(
    relationship_type: str,
    blockers: list[str],
    edge: dict[str, Any],
    evidence_fields: dict[str, Any] | None,
) -> tuple[bool, list[str], list[str]]:
    """Decide if RV can inspect this row now, and what's missing if not."""

    family = MANUAL_RELATIONSHIP_TYPES.get(relationship_type, {}).get("family", "structural")
    # Reference-only, weak-signal, no-current-peer never ready for RV review.
    if relationship_type in {"NO_CURRENT_PEER", "TITLE_SIMILARITY_ONLY", "FAIR_VALUE_REFERENCE_ONLY", "SPORTSBOOK_REFERENCE_ONLY", "TRUTH_FEED_ANCHOR_ONLY", "SAME_TOPIC_WEAK_SIGNAL", "AMBIGUOUS_RELATIONSHIP"}:
        return False, [], ["peer_market_discovery_query", "structural_evidence_or_explicit_basis_risk_classification"]
    if relationship_type in NEAR_EXACT_TYPES:
        # Ready if and only if we have evidence on both sides.
        has_typed = bool(evidence_fields)
        if has_typed:
            return True, [
                "independent_settlement_source_verification",
                "fee_model_and_depth_freshness_capture",
                "orderbook_freshness_check",
            ], []
        return False, [], [
            "left_market_typed_evidence",
            "right_market_typed_evidence",
        ]
    if family in {"payoff_calendar", "observation_time", "observation_window", "ath_by_date"}:
        if "manual_discovery_required" in blockers:
            return False, [], ["peer_market_discovery_query", "payoff_shape_text_from_rules"]
        return False, [], _manual_evidence_for(relationship_type, blockers, evidence_fields)
    if family == "rate_definition":
        return False, [], _manual_evidence_for(relationship_type, blockers, evidence_fields)
    if family == "event_winner":
        # If both sides are present we can route to RV for source review,
        # though graph never claims same-payoff.
        if evidence_fields:
            return True, [
                "venue_settlement_rule_for_void_or_postpone",
                "team_list_and_tie_break_rule",
            ], []
        return False, [], _manual_evidence_for(relationship_type, blockers, evidence_fields)
    return False, [], _manual_evidence_for(relationship_type, blockers, evidence_fields)


def _current_action_for(edge: dict[str, Any], relationship_type: str) -> str:
    raw = edge.get("action")
    if isinstance(raw, str) and raw in ALLOWED_EDGE_ACTIONS:
        return raw
    if relationship_type in NEAR_EXACT_TYPES:
        return ACTION_MANUAL_REVIEW
    family = MANUAL_RELATIONSHIP_TYPES.get(relationship_type, {}).get("family", "structural")
    if family == "weak_signal":
        return ACTION_IGNORE_LOW_CONFIDENCE
    if family in {"reference_anchor"}:
        return ACTION_SOURCE_REVIEW
    if family in {"payoff_calendar", "settlement_source", "observation_time", "observation_window", "range_bucket", "ath_by_date", "indicator_source", "indicator_release_time", "release_revisions", "season_void_rules", "rate_definition"}:
        return ACTION_BASIS_RISK_REVIEW
    return ACTION_MANUAL_REVIEW


def _action_for_record(relationship_type: str, blockers: list[str], ready_now: bool) -> str:
    if relationship_type == "TITLE_SIMILARITY_ONLY":
        return ACTION_IGNORE_LOW_CONFIDENCE
    if relationship_type == "NO_CURRENT_PEER":
        return ACTION_IGNORE_LOW_CONFIDENCE
    if relationship_type in {"FAIR_VALUE_REFERENCE_ONLY", "SPORTSBOOK_REFERENCE_ONLY", "TRUTH_FEED_ANCHOR_ONLY"}:
        return ACTION_SOURCE_REVIEW
    if relationship_type in NEAR_EXACT_TYPES:
        return ACTION_MANUAL_REVIEW if ready_now else ACTION_SOURCE_REVIEW
    family = MANUAL_RELATIONSHIP_TYPES.get(relationship_type, {}).get("family", "structural")
    if family == "weak_signal":
        return ACTION_IGNORE_LOW_CONFIDENCE
    return ACTION_BASIS_RISK_REVIEW


def _crypto_payoff_relationship(left_shape: str, right_shape: str) -> str:
    left = left_shape.lower()
    right = right_shape.lower()
    if "intraday" in left and "point_in_time" in right:
        return "INTRADAY_TOUCH_VS_POINT_IN_TIME"
    if "intraday" in left and "daily" in right:
        return "INTRADAY_TOUCH_VS_DAILY_CLOSE"
    if "deadline" in left and "point_in_time" in right:
        return "DEADLINE_TOUCH_VS_POINT_IN_TIME"
    if "deadline" in left and "daily" in right:
        return "DEADLINE_TOUCH_VS_DAILY_CLOSE"
    if "all_time_high" in left or "all_time_high" in right:
        return "ALL_TIME_HIGH_BY_DATE_VS_POINT_IN_TIME"
    if ("range" in left or "year_end_range" in left) and ("threshold" in right or "point_in_time" in right):
        return "RANGE_BUCKET_VS_THRESHOLD"
    if "hourly" in left and "daily" in right:
        return "HOURLY_POINT_IN_TIME_VS_DAILY_5PM"
    if "weekly" in left and "deadline" in right:
        return "WEEKLY_FRIDAY_CLOSE_VS_DEADLINE_TOUCH"
    if "daily_direction" in left and "threshold" in right:
        return "DAILY_DIRECTION_VS_PRICE_THRESHOLD"
    if left and right and left == right:
        return "SAME_ASSET_DIFFERENT_SOURCE"
    return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    vertical = Counter()
    rt = Counter()
    blockers = Counter()
    evidence = Counter()
    ready_now = 0
    blocked = 0
    family = Counter()
    for record in records:
        vertical[record["vertical"]] += 1
        rt[record["relationship_type"]] += 1
        family[record["family"]] += 1
        for blocker in record.get("blockers", []):
            blockers[blocker] += 1
        for need in record.get("manual_evidence_needed", []):
            evidence[need] += 1
        if record.get("can_go_to_relative_value_now"):
            ready_now += 1
        else:
            blocked += 1
    return {
        "total_records": len(records),
        "ready_for_rv_now": ready_now,
        "blocked_on_manual_evidence": blocked,
        "records_by_vertical": _counter_rows(vertical, "vertical"),
        "records_by_relationship_type": _counter_rows(rt, "relationship_type"),
        "records_by_family": _counter_rows(family, "family"),
        "top_blockers": _counter_rows(blockers, "blocker")[:15],
        "top_manual_evidence_needed": _counter_rows(evidence, "manual_evidence")[:15],
    }


def _counter_rows(counter: Counter, key: str) -> list[dict[str, Any]]:
    return [
        {key: name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _merge_records(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    merged = dict(a)
    merged["blockers"] = sorted(set((a.get("blockers") or []) + (b.get("blockers") or [])))
    merged["manual_evidence_needed"] = list(
        dict.fromkeys((a.get("manual_evidence_needed") or []) + (b.get("manual_evidence_needed") or []))
    )
    merged["source_reports"] = sorted(set((a.get("source_reports") or []) + (b.get("source_reports") or [])))
    merged["evidence_fields"] = {**(a.get("evidence_fields") or {}), **(b.get("evidence_fields") or {})}
    return merged


def _validate_record(record: dict[str, Any], path: str) -> None:
    if not isinstance(record, dict):
        raise SchemaValidationError(f"{path} must be an object")
    for key in (
        "relationship_id",
        "vertical",
        "family",
        "left_market_or_source",
        "right_market_or_source",
        "venues",
        "relationship_type",
        "why_related",
        "why_not_exact",
        "blockers",
        "manual_evidence_needed",
        "evidence_priority",
        "repeat_cadence",
        "current_action",
        "can_go_to_relative_value_now",
        "rv_must_verify",
        "manual_info_missing",
        "source_reports",
        "diagnostic_only",
        "affects_evaluator_gates",
    ):
        if key not in record:
            raise SchemaValidationError(f"{path}.{key} is required")
    if record["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if record["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if record["vertical"] not in VERTICALS:
        raise SchemaValidationError(f"{path}.vertical not allowed")
    if record["current_action"] not in ALLOWED_EDGE_ACTIONS:
        raise SchemaValidationError(f"{path}.current_action not allowed")
    if not isinstance(record["why_not_exact"], str) or not record["why_not_exact"]:
        raise SchemaValidationError(f"{path}.why_not_exact must be a non-empty string")
    if not isinstance(record["why_related"], str) or not record["why_related"]:
        raise SchemaValidationError(f"{path}.why_related must be a non-empty string")
    if not isinstance(record["blockers"], list):
        raise SchemaValidationError(f"{path}.blockers must be a list")
    if not isinstance(record["venues"], list) or len(record["venues"]) < 2:
        raise SchemaValidationError(f"{path}.venues must list two venues")


def _safety_summary() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "graph_emits_evaluator_input": False,
        "graph_can_create_candidate_pair": False,
        "graph_can_claim_exact_payoff": False,
        "manual_evidence_layer_diagnostic_only": True,
        "llm_advisory_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
    }


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_payload(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(value: str) -> str:
    redacted = value
    for token in PROHIBITED_REPORT_TOKENS:
        redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[redacted]", redacted)
    for phrase in PROHIBITED_REPORT_PHRASES:
        redacted = redacted.replace(phrase, "[redacted]")
        redacted = redacted.replace(phrase.replace("_", "-"), "[redacted]")
        redacted = redacted.replace(phrase.upper(), "[REDACTED]")
    return redacted


def _load_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"io_error:{exc}"
    except ValueError as exc:
        return None, f"json_error:{exc}"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


__all__ = [
    "DEFAULT_DIFFICULTY",
    "DEFAULT_REPEAT_CADENCE",
    "DEFAULT_URGENCY",
    "EVIDENCE_VERSION",
    "MANUAL_RELATIONSHIP_TYPES",
    "NEAR_EXACT_TYPES",
    "REPORT_BANNER",
    "VERTICALS",
    "build_graph_manual_relationship_evidence_report",
    "render_graph_manual_relationship_evidence_markdown",
    "validate_graph_manual_relationship_evidence_report",
    "write_graph_manual_relationship_evidence_report",
]
