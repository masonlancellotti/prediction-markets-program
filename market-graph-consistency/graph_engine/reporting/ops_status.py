from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.reporting.safety import (
    PROHIBITED_REPORT_PHRASES,
    PROHIBITED_REPORT_TOKENS,
    find_prohibited_rendered_text,
)
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens

# Mirror the stale/lag freshness bucket vocabulary so the daily radar passes the
# bucket counts through verbatim without re-implementing the labels here.
FRESHNESS_BUCKETS = (
    "fresh",
    "maybe_stale",
    "stale",
    "missing_timestamp",
    "uniform_timestamps_suspicious",
)
PACKET_KINDS = (
    "STRUCTURAL_VIOLATION",
    "LLM_ONLY",
    "SIMILARITY_RESEARCH",
    "BTC_BASIS_RISK_REVIEW",
    "FAIR_VALUE_REFERENCE_ONLY",
)
TOP_BLOCKERS_LIMIT = 10

# These blockers are diagnostic invariants the graph stamps onto every row by
# design — they document that the row is not executable and not an evaluator
# input. Including them in the "what to fix first" ladder would crowd out the
# actionable bottlenecks, so the radar suppresses them from the frequency rank.
BOILERPLATE_BLOCKERS = frozenset(
    {
        "graph_packet_review_only",
        "no_execution_permission",
        "not_evaluator_input",
        "requires_fee_depth_freshness_review",
        "requires_fee_model_review",
        "requires_independent_payoff_verification",
        "requires_independent_settlement_review",
        "requires_native_payoff_vector_review",
        "requires_orderbook_depth_freshness",
        "requires_payoff_relationship_proof",
        "requires_settlement_source_proof",
        "requires_settlement_source_review",
        "requires_typed_key_verification",
    }
)


BANNER = (
    "Saved-file-only market graph ops status. This daily radar summarizes diagnostic reports "
    "and does not affect evaluator gates."
)
NEXT_RECOMMENDED_ACTIONS = [
    "REVIEW_PERSISTENT_HIGH_CONFIDENCE",
    "REVIEW_WORSENING_DIAGNOSTICS",
    "REVIEW_TOP_PROBABILITY_CONSTRAINTS",
    "REVIEW_RV_HANDOFF_PACKETS",
    "REVIEW_PLATFORM_EXPANSION_GAPS",
    "REVIEW_ONTOLOGY_COVERAGE",
    "REVIEW_TOP_INFEASIBILITY",
    "REBUILD_MISSING_INPUT_REPORTS",
]
INPUT_REPORTS = {
    "trade_indicators": "market_graph_trade_indicators.json",
    "probability_constraints": "market_graph_probability_constraints.json",
    "payoff_state_feasibility_bridge": "market_graph_payoff_state_feasibility_bridge.json",
    "signal_persistence": "market_graph_signal_persistence.json",
    "rv_investigation_packets": "graph_to_relative_value_investigation_packets.json",
    "stale_lag_watchlist": "market_graph_stale_lag_watchlist.json",
}
OPTIONAL_INPUT_REPORTS = {
    "platform_expansion_radar": "market_graph_platform_expansion_radar.json",
    "event_entity_ontology": "market_graph_event_entity_ontology.json",
    "rv_diagnostic_relationship_edges": "rv_diagnostic_relationship_edges.json",
    "rv_review_worklist": "rv_review_worklist.json",
    "llm_graph_relationship_review_prompt": "llm_graph_relationship_review_prompt.md",
    "graph_manual_relationship_evidence": "graph_manual_relationship_evidence.json",
    "graph_manual_discovery_backlog": "graph_manual_discovery_backlog.json",
    "llm_graph_manual_evidence_prompt": "llm_graph_manual_evidence_prompt.md",
}
UNIFORM_TIMESTAMP_STALE_BLOCKER = "uniform_fixture_or_snapshot_timestamps_no_skew_detectable"


def build_market_graph_ops_status_report(
    *,
    snapshot_id: str | None = None,
    as_of: str | None = None,
    trade_indicator_report: dict[str, Any] | None = None,
    probability_constraints_report: dict[str, Any] | None = None,
    payoff_state_feasibility_bridge_report: dict[str, Any] | None = None,
    signal_persistence_report: dict[str, Any] | None = None,
    rv_investigation_packets_report: dict[str, Any] | None = None,
    stale_lag_watchlist_report: dict[str, Any] | None = None,
    platform_expansion_radar_report: dict[str, Any] | None = None,
    event_entity_ontology_report: dict[str, Any] | None = None,
    rv_diagnostic_relationship_edges_report: dict[str, Any] | None = None,
    rv_review_worklist_report: dict[str, Any] | None = None,
    llm_graph_relationship_review_status: dict[str, Any] | None = None,
    graph_manual_relationship_evidence_report: dict[str, Any] | None = None,
    graph_manual_discovery_backlog_report: dict[str, Any] | None = None,
    llm_graph_manual_evidence_review_status: dict[str, Any] | None = None,
    input_blockers: list[str] | None = None,
) -> dict[str, Any]:
    signals = _list_from_report(trade_indicator_report, "signals")
    probability_constraints = _list_from_report(probability_constraints_report, "probability_constraints")
    bridge_rows = _list_from_report(payoff_state_feasibility_bridge_report, "payoff_state_feasibility_bridge")
    persistence_rows = _list_from_report(signal_persistence_report, "signal_persistence_rows")
    rv_packets = _list_from_report(rv_investigation_packets_report, "investigation_packets")
    stale_lag_rows = _list_from_report(stale_lag_watchlist_report, "stale_lag_watchlist")
    platform_gap_rows = _list_from_report(platform_expansion_radar_report, "platform_gap_rows")
    platform_recommendations = _list_from_report(platform_expansion_radar_report, "recommended_platform_fetches")
    ontology_rows = _list_from_report(event_entity_ontology_report, "ontology_rows")
    ontology_summary = (
        event_entity_ontology_report.get("summary", {}) if isinstance(event_entity_ontology_report, dict) else {}
    )
    persistence_summary = signal_persistence_report.get("summary", {}) if isinstance(signal_persistence_report, dict) else {}

    top_persistent = _top_rows(
        persistence_summary.get("top_persistent_high_confidence_signals"),
        limit=10,
        source="signal_persistence",
    )
    top_worsening = _top_rows(
        persistence_summary.get("top_worsening_signals"),
        limit=10,
        source="signal_persistence",
    )
    top_constraints = _top_probability_constraints(probability_constraints, limit=10)
    top_packets = _top_rv_packets(rv_packets, limit=5)
    top_platform_recs = _top_platform_recommendations(platform_recommendations, limit=5)
    top_infeasibility = _top_infeasibility_diagnostic(bridge_rows, limit=5)
    blockers = sorted(
        set(input_blockers or [])
        | set(_content_blockers(signals, probability_constraints, bridge_rows, rv_packets, stale_lag_rows))
    )
    top_blockers_by_frequency = _top_blockers_by_frequency(
        signals=signals,
        constraints=probability_constraints,
        packets=rv_packets,
        stale_lag_rows=stale_lag_rows,
        bridge_rows=bridge_rows,
        limit=TOP_BLOCKERS_LIMIT,
    )
    freshness_buckets = _freshness_bucket_counts(stale_lag_watchlist_report, stale_lag_rows)
    packet_kind_counts = _packet_kind_counts(rv_investigation_packets_report, rv_packets)
    bridge_status_counts = _bridge_status_counts(bridge_rows)
    summary = {
        "total_signals": len(signals),
        "high_confidence_signals": sum(1 for row in signals if row.get("confidence_tier") == "HIGH"),
        "new_signals_since_last_run": _int_summary(persistence_summary, "new_count"),
        "worsened_signals": _int_summary(persistence_summary, "worsened_count"),
        "persistent_high_confidence_signals": len(top_persistent),
        "midpoint_blocked_signals": _midpoint_blocked_count(signals, probability_constraints, rv_packets),
        "yes_price_equal_to_midpoint_rows": _yes_price_equal_to_midpoint_count(
            signals, probability_constraints
        ),
        "stale_lag_watch_count": _stale_lag_watch_count(stale_lag_watchlist_report, stale_lag_rows),
        "stale_lag_blocked_count": _stale_lag_blocked_count(stale_lag_watchlist_report, stale_lag_rows),
        "uniform_timestamp_stale_blocked_count": _uniform_timestamp_stale_blocked_count(
            stale_lag_watchlist_report, stale_lag_rows
        ),
        "stale_blocked_signal_constraint_packet_rows": _stale_blocked_signal_constraint_packet_count(
            signals, probability_constraints, rv_packets
        ),
        "rv_handoff_packets_ready": len(top_packets),
        "platform_expansion_gap_rows": len(platform_gap_rows),
        "platform_expansion_high_value_rows": sum(
            1 for row in platform_gap_rows if row.get("expected_value_of_fetch") == "HIGH"
        ),
        "ontology_entity_count": _int_or_default(_report_value(event_entity_ontology_report, "entity_count"), len(ontology_rows)),
        "ontology_low_confidence_count": len(_string_list(ontology_summary.get("low_confidence_entities"))),
        "ontology_cross_venue_candidate_count": len(_string_list(ontology_summary.get("cross_venue_entity_candidates"))),
        "bridge_row_count": len(bridge_rows),
        "bridge_feasible_count": bridge_status_counts["FEASIBLE"],
        "bridge_infeasible_diagnostic_count": bridge_status_counts["INFEASIBLE_DIAGNOSTIC"],
        "bridge_blocked_missing_payoff_matrix_count": bridge_status_counts["BLOCKED_MISSING_PAYOFF_MATRIX"],
        "bridge_blocked_missing_probability_inputs_count": bridge_status_counts["BLOCKED_MISSING_PROBABILITY_INPUTS"],
        "bridge_blocked_missing_state_family_count": bridge_status_counts["BLOCKED_MISSING_STATE_FAMILY"],
        "bridge_blocked_unsupported_constraint_type_count": bridge_status_counts["BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE"],
        "freshness_buckets": freshness_buckets,
        "packet_kind_counts": packet_kind_counts,
    }
    rv_relationship_summary = _rv_relationship_summary(
        rv_diagnostic_relationship_edges_report,
        rv_review_worklist_report,
        llm_graph_relationship_review_status,
    )
    manual_evidence_summary = _manual_evidence_summary(
        graph_manual_relationship_evidence_report,
        graph_manual_discovery_backlog_report,
        llm_graph_manual_evidence_review_status,
    )
    summary.update(
        {
            "manual_evidence_records_total": manual_evidence_summary["total_records"],
            "manual_evidence_ready_for_rv_now": manual_evidence_summary["ready_for_rv_now"],
            "manual_evidence_blocked_count": manual_evidence_summary["blocked_count"],
            "manual_evidence_backlog_total": manual_evidence_summary["backlog_total"],
            "manual_evidence_backlog_high_urgency": manual_evidence_summary["backlog_high_urgency"],
            "llm_graph_manual_evidence_prompt_present": manual_evidence_summary["llm_prompt_present"],
            "llm_graph_manual_evidence_schema_present": manual_evidence_summary["llm_schema_present"],
            "rv_diagnostic_edges_total": rv_relationship_summary["edges_total"],
            "rv_diagnostic_edges_basis_risk": rv_relationship_summary["edges_basis_risk"],
            "rv_diagnostic_edges_near_exact_review": rv_relationship_summary["edges_near_exact_review"],
            "rv_diagnostic_edges_structural": rv_relationship_summary["edges_structural"],
            "rv_diagnostic_edges_weak_signal": rv_relationship_summary["edges_weak_signal"],
            "rv_diagnostic_edges_reference_only": rv_relationship_summary["edges_reference_only"],
            "rv_diagnostic_crypto_payoff_calendar_edges": rv_relationship_summary["crypto_payoff_calendar_edges"],
            "rv_review_worklist_row_count": rv_relationship_summary["worklist_row_count"],
            "rv_review_worklist_rv_can_inspect_now_count": rv_relationship_summary["worklist_rv_can_inspect_now_count"],
            "llm_graph_relationship_review_prompt_present": rv_relationship_summary["llm_prompt_present"],
            "llm_graph_relationship_review_schema_present": rv_relationship_summary["llm_schema_present"],
        }
    )
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "banner": BANNER,
        "snapshot_id": snapshot_id or _first_string(
            [
                _report_value(trade_indicator_report, "snapshot_id"),
                _report_value(probability_constraints_report, "snapshot_id"),
                _report_value(payoff_state_feasibility_bridge_report, "snapshot_id"),
            ]
        ),
        "as_of": as_of,
        "summary": summary,
        "top_persistent_high_confidence": top_persistent,
        "top_worsening": top_worsening,
        "top_probability_constraints": top_constraints,
        "top_rv_handoff_packets": top_packets,
        "top_platform_expansion_recommendations": top_platform_recs,
        "top_infeasibility_diagnostic": top_infeasibility,
        "top_blockers_by_frequency": top_blockers_by_frequency,
        "blockers": blockers,
        "next_recommended_actions": _next_actions(
            blockers, summary, top_constraints, top_packets, platform_gap_rows, ontology_summary
        ),
        "rv_relationship_summary": rv_relationship_summary,
        "manual_evidence_summary": manual_evidence_summary,
        "safety_summary": _safety_summary(),
    }
    validate_market_graph_ops_status_report(report)
    return report


def write_market_graph_ops_status_report(
    *,
    json_output: Path | str,
    markdown_output: Path | str,
    snapshot_id: str | None = None,
    as_of: str | None = None,
    trade_indicators_path: Path | str | None = None,
    probability_constraints_path: Path | str | None = None,
    payoff_state_feasibility_bridge_path: Path | str | None = None,
    signal_persistence_path: Path | str | None = None,
    rv_investigation_packets_path: Path | str | None = None,
    stale_lag_watchlist_path: Path | str | None = None,
    platform_expansion_radar_path: Path | str | None = None,
    event_entity_ontology_path: Path | str | None = None,
    rv_diagnostic_relationship_edges_path: Path | str | None = None,
    rv_review_worklist_path: Path | str | None = None,
    llm_graph_relationship_review_prompt_path: Path | str | None = None,
    llm_graph_relationship_review_schema_path: Path | str | None = None,
    graph_manual_relationship_evidence_path: Path | str | None = None,
    graph_manual_discovery_backlog_path: Path | str | None = None,
    llm_graph_manual_evidence_prompt_path: Path | str | None = None,
    llm_graph_manual_evidence_schema_path: Path | str | None = None,
) -> dict[str, Any]:
    inputs = {
        "trade_indicators": _load_optional_report(trade_indicators_path),
        "probability_constraints": _load_optional_report(probability_constraints_path),
        "payoff_state_feasibility_bridge": _load_optional_report(payoff_state_feasibility_bridge_path),
        "signal_persistence": _load_optional_report(signal_persistence_path),
        "rv_investigation_packets": _load_optional_report(rv_investigation_packets_path),
        "stale_lag_watchlist": _load_optional_report(stale_lag_watchlist_path),
    }
    optional_inputs = {
        "platform_expansion_radar": _load_optional_report(platform_expansion_radar_path),
        "event_entity_ontology": _load_optional_report(event_entity_ontology_path),
        "rv_diagnostic_relationship_edges": _load_optional_report(rv_diagnostic_relationship_edges_path),
        "rv_review_worklist": _load_optional_report(rv_review_worklist_path),
        "graph_manual_relationship_evidence": _load_optional_report(graph_manual_relationship_evidence_path),
        "graph_manual_discovery_backlog": _load_optional_report(graph_manual_discovery_backlog_path),
    }
    llm_status = {
        "prompt_present": bool(llm_graph_relationship_review_prompt_path)
        and Path(llm_graph_relationship_review_prompt_path).exists(),
        "schema_present": bool(llm_graph_relationship_review_schema_path)
        and Path(llm_graph_relationship_review_schema_path).exists(),
    }
    llm_manual_status = {
        "prompt_present": bool(llm_graph_manual_evidence_prompt_path)
        and Path(llm_graph_manual_evidence_prompt_path).exists(),
        "schema_present": bool(llm_graph_manual_evidence_schema_path)
        and Path(llm_graph_manual_evidence_schema_path).exists(),
        "prompt_path": str(llm_graph_manual_evidence_prompt_path) if llm_graph_manual_evidence_prompt_path else None,
        "schema_path": str(llm_graph_manual_evidence_schema_path) if llm_graph_manual_evidence_schema_path else None,
    }
    blockers = [
        f"missing_input_report:{INPUT_REPORTS[key]}"
        for key, payload in inputs.items()
        if payload is None
    ]
    # Only flag missing optional inputs when the caller explicitly supplied a
    # path. Default-mode operators may run the ops status before the optional
    # platform/ontology reports exist and that must not flood the blocker list.
    if platform_expansion_radar_path is not None and optional_inputs["platform_expansion_radar"] is None:
        blockers.append(f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['platform_expansion_radar']}")
    if event_entity_ontology_path is not None and optional_inputs["event_entity_ontology"] is None:
        blockers.append(f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['event_entity_ontology']}")
    if rv_diagnostic_relationship_edges_path is not None and optional_inputs["rv_diagnostic_relationship_edges"] is None:
        blockers.append(
            f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['rv_diagnostic_relationship_edges']}"
        )
    if rv_review_worklist_path is not None and optional_inputs["rv_review_worklist"] is None:
        blockers.append(
            f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['rv_review_worklist']}"
        )
    if graph_manual_relationship_evidence_path is not None and optional_inputs["graph_manual_relationship_evidence"] is None:
        blockers.append(
            f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['graph_manual_relationship_evidence']}"
        )
    if graph_manual_discovery_backlog_path is not None and optional_inputs["graph_manual_discovery_backlog"] is None:
        blockers.append(
            f"missing_optional_input_report:{OPTIONAL_INPUT_REPORTS['graph_manual_discovery_backlog']}"
        )
    report = build_market_graph_ops_status_report(
        snapshot_id=snapshot_id,
        as_of=as_of,
        trade_indicator_report=inputs["trade_indicators"],
        probability_constraints_report=inputs["probability_constraints"],
        payoff_state_feasibility_bridge_report=inputs["payoff_state_feasibility_bridge"],
        signal_persistence_report=inputs["signal_persistence"],
        rv_investigation_packets_report=inputs["rv_investigation_packets"],
        stale_lag_watchlist_report=inputs["stale_lag_watchlist"],
        platform_expansion_radar_report=optional_inputs["platform_expansion_radar"],
        event_entity_ontology_report=optional_inputs["event_entity_ontology"],
        rv_diagnostic_relationship_edges_report=optional_inputs["rv_diagnostic_relationship_edges"],
        rv_review_worklist_report=optional_inputs["rv_review_worklist"],
        llm_graph_relationship_review_status=llm_status,
        graph_manual_relationship_evidence_report=optional_inputs["graph_manual_relationship_evidence"],
        graph_manual_discovery_backlog_report=optional_inputs["graph_manual_discovery_backlog"],
        llm_graph_manual_evidence_review_status=llm_manual_status,
        input_blockers=blockers,
    )
    markdown = render_market_graph_ops_status_markdown(report)
    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError("ops status Markdown contains prohibited vocabulary: " + ", ".join(findings))

    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def validate_market_graph_ops_status_report(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("ops status report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("ops status report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("ops status actions must be WATCH and MANUAL_REVIEW only")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("summary must be an object")
    for key in [
        "total_signals",
        "high_confidence_signals",
        "new_signals_since_last_run",
        "worsened_signals",
        "persistent_high_confidence_signals",
        "midpoint_blocked_signals",
        "yes_price_equal_to_midpoint_rows",
        "stale_lag_watch_count",
        "stale_lag_blocked_count",
        "uniform_timestamp_stale_blocked_count",
        "stale_blocked_signal_constraint_packet_rows",
        "rv_handoff_packets_ready",
        "platform_expansion_gap_rows",
        "platform_expansion_high_value_rows",
        "ontology_entity_count",
        "ontology_low_confidence_count",
        "ontology_cross_venue_candidate_count",
        "bridge_row_count",
        "bridge_feasible_count",
        "bridge_infeasible_diagnostic_count",
        "bridge_blocked_missing_payoff_matrix_count",
        "bridge_blocked_missing_probability_inputs_count",
        "bridge_blocked_missing_state_family_count",
        "bridge_blocked_unsupported_constraint_type_count",
        "rv_diagnostic_edges_total",
        "rv_diagnostic_edges_basis_risk",
        "rv_diagnostic_edges_near_exact_review",
        "rv_diagnostic_edges_structural",
        "rv_diagnostic_edges_weak_signal",
        "rv_diagnostic_edges_reference_only",
        "rv_diagnostic_crypto_payoff_calendar_edges",
        "rv_review_worklist_row_count",
        "rv_review_worklist_rv_can_inspect_now_count",
        "manual_evidence_records_total",
        "manual_evidence_ready_for_rv_now",
        "manual_evidence_blocked_count",
        "manual_evidence_backlog_total",
        "manual_evidence_backlog_high_urgency",
    ]:
        if not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool):
            raise SchemaValidationError(f"summary.{key} must be an integer")
    for key in (
        "llm_graph_relationship_review_prompt_present",
        "llm_graph_relationship_review_schema_present",
        "llm_graph_manual_evidence_prompt_present",
        "llm_graph_manual_evidence_schema_present",
    ):
        if not isinstance(summary.get(key), bool):
            raise SchemaValidationError(f"summary.{key} must be a boolean")
    freshness_buckets = summary.get("freshness_buckets")
    if not isinstance(freshness_buckets, dict):
        raise SchemaValidationError("summary.freshness_buckets must be an object")
    if set(freshness_buckets.keys()) != set(FRESHNESS_BUCKETS):
        raise SchemaValidationError("summary.freshness_buckets must list every supported bucket exactly once")
    for bucket, count in freshness_buckets.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise SchemaValidationError(f"summary.freshness_buckets[{bucket!r}] must be a non-negative integer")
    packet_kind_counts = summary.get("packet_kind_counts")
    if not isinstance(packet_kind_counts, dict):
        raise SchemaValidationError("summary.packet_kind_counts must be an object")
    if set(packet_kind_counts.keys()) != set(PACKET_KINDS):
        raise SchemaValidationError("summary.packet_kind_counts must list every supported kind exactly once")
    for kind, count in packet_kind_counts.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise SchemaValidationError(f"summary.packet_kind_counts[{kind!r}] must be a non-negative integer")
    for section in [
        "top_persistent_high_confidence",
        "top_worsening",
        "top_probability_constraints",
        "top_rv_handoff_packets",
        "top_platform_expansion_recommendations",
        "top_infeasibility_diagnostic",
    ]:
        rows = report.get(section)
        if not isinstance(rows, list):
            raise SchemaValidationError(f"{section} must be a list")
        for index, row in enumerate(rows):
            _validate_top_row(row, f"{section}[{index}]")
            if section == "top_infeasibility_diagnostic":
                _validate_infeasibility_top_row(row, f"{section}[{index}]")
    top_blockers = report.get("top_blockers_by_frequency")
    if not isinstance(top_blockers, list):
        raise SchemaValidationError("top_blockers_by_frequency must be a list")
    for index, row in enumerate(top_blockers):
        _validate_top_blocker_row(row, f"top_blockers_by_frequency[{index}]")
    if not isinstance(report.get("blockers"), list):
        raise SchemaValidationError("blockers must be a list")
    if not isinstance(report.get("next_recommended_actions"), list):
        raise SchemaValidationError("next_recommended_actions must be a list")
    for action in report["next_recommended_actions"]:
        if action not in NEXT_RECOMMENDED_ACTIONS:
            raise SchemaValidationError(f"unsupported next_recommended_action {action!r}")


def render_market_graph_ops_status_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Market Graph Ops Status",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Snapshot: `{report.get('snapshot_id') or 'unknown'}`",
        f"- As of: `{report.get('as_of') or 'unknown'}`",
        "",
        "## Summary",
        "",
        f"- Total signals: {summary['total_signals']}",
        f"- High confidence signals: {summary['high_confidence_signals']}",
        f"- New since last run: {summary['new_signals_since_last_run']}",
        f"- Worsened: {summary['worsened_signals']}",
        f"- Persistent high confidence: {summary['persistent_high_confidence_signals']}",
        f"- Midpoint blocked: {summary['midpoint_blocked_signals']}",
        f"- Yes-price equals midpoint (synthetic) rows: {summary['yes_price_equal_to_midpoint_rows']}",
        f"- Stale/lag watch rows: {summary['stale_lag_watch_count']}",
        f"- Stale/lag blocked rows: {summary['stale_lag_blocked_count']}",
        f"- Uniform-timestamp stale/lag blocked rows: {summary['uniform_timestamp_stale_blocked_count']}",
        f"- Stale blocked signal/constraint/packet rows: {summary['stale_blocked_signal_constraint_packet_rows']}",
        f"- RV handoff packets ready: {summary['rv_handoff_packets_ready']}",
        f"- Platform expansion gap rows: {summary['platform_expansion_gap_rows']}",
        f"  - HIGH-value rows: {summary['platform_expansion_high_value_rows']}",
        f"- Ontology entities: {summary['ontology_entity_count']}",
        f"  - LOW-confidence entities: {summary['ontology_low_confidence_count']}",
        f"  - Cross-venue entity candidates: {summary['ontology_cross_venue_candidate_count']}",
        f"- Bridge rows: {summary['bridge_row_count']}",
        f"  - Feasible: {summary['bridge_feasible_count']}",
        f"  - Infeasible diagnostic: {summary['bridge_infeasible_diagnostic_count']}",
        f"  - Blocked missing payoff matrix: {summary['bridge_blocked_missing_payoff_matrix_count']}",
        f"  - Blocked missing probability inputs: {summary['bridge_blocked_missing_probability_inputs_count']}",
        f"  - Blocked missing state family: {summary['bridge_blocked_missing_state_family_count']}",
        f"  - Blocked unsupported constraint type: {summary['bridge_blocked_unsupported_constraint_type_count']}",
        "",
        "## Freshness Buckets",
        "",
    ]
    for bucket in FRESHNESS_BUCKETS:
        lines.append(f"- `{bucket}`: {summary['freshness_buckets'].get(bucket, 0)}")
    lines.extend(["", "## RV Packet Kinds", ""])
    for kind in PACKET_KINDS:
        lines.append(f"- `{kind}`: {summary['packet_kind_counts'].get(kind, 0)}")
    rv_relationship_summary = report.get("rv_relationship_summary") or {}
    lines.extend([
        "",
        "## RV Relationship Layer",
        "",
        f"- Diagnostic only: `{str(rv_relationship_summary.get('diagnostic_only', True)).lower()}`",
        f"- Total RV-ingested edges: {rv_relationship_summary.get('edges_total', 0)}",
        f"- Basis-risk edges: {rv_relationship_summary.get('edges_basis_risk', 0)}",
        f"- Near-exact review edges: {rv_relationship_summary.get('edges_near_exact_review', 0)}",
        f"- Structural edges: {rv_relationship_summary.get('edges_structural', 0)}",
        f"- Weak-signal edges: {rv_relationship_summary.get('edges_weak_signal', 0)}",
        f"- Reference-only edges: {rv_relationship_summary.get('edges_reference_only', 0)}",
        f"- Crypto payoff-calendar edges: {rv_relationship_summary.get('crypto_payoff_calendar_edges', 0)}",
        f"- Graph-to-RV worklist rows: {rv_relationship_summary.get('worklist_row_count', 0)}",
        f"- Worklist rows RV can inspect now: {rv_relationship_summary.get('worklist_rv_can_inspect_now_count', 0)}",
        f"- LLM prompt present: {str(rv_relationship_summary.get('llm_prompt_present', False)).lower()}",
        f"- LLM schema present: {str(rv_relationship_summary.get('llm_schema_present', False)).lower()}",
    ])
    top_manual = rv_relationship_summary.get("top_manual_discovery_priority")
    if isinstance(top_manual, dict) and top_manual.get("family"):
        lines.append(
            f"- Top manual discovery target: `{top_manual.get('family')}` — {top_manual.get('reason', '')}"
        )
    if rv_relationship_summary.get("top_blockers"):
        lines.extend(["", "### RV relationship top blockers", "", "| Blocker | Count |", "| --- | --- |"])
        for entry in rv_relationship_summary["top_blockers"]:
            lines.append(f"| `{entry.get('blocker')}` | {entry.get('count')} |")
    manual_summary = report.get("manual_evidence_summary") or {}
    lines.extend([
        "",
        "## Manual Evidence Layer",
        "",
        f"- Diagnostic only: `{str(manual_summary.get('diagnostic_only', True)).lower()}`",
        f"- Manual evidence records: {manual_summary.get('total_records', 0)}",
        f"- Records ready for RV source-review: {manual_summary.get('ready_for_rv_now', 0)}",
        f"- Records blocked on manual evidence: {manual_summary.get('blocked_count', 0)}",
        f"- Manual discovery backlog items: {manual_summary.get('backlog_total', 0)}",
        f"- HIGH-urgency backlog items: {manual_summary.get('backlog_high_urgency', 0)}",
        f"- Top vertical by manual work: `{manual_summary.get('top_vertical_by_manual_work') or 'none'}`",
        f"- LLM manual-evidence prompt present: {str(manual_summary.get('llm_prompt_present', False)).lower()}",
        f"- LLM manual-evidence schema present: {str(manual_summary.get('llm_schema_present', False)).lower()}",
    ])
    if manual_summary.get("top_blockers"):
        lines.extend(["", "### Manual evidence top blockers", "", "| Blocker | Count |", "| --- | --- |"])
        for entry in manual_summary["top_blockers"]:
            lines.append(f"| `{entry.get('blocker')}` | {entry.get('count')} |")
    if manual_summary.get("records_by_vertical"):
        lines.extend(["", "### Manual evidence records by vertical", "", "| Vertical | Count |", "| --- | --- |"])
        for entry in manual_summary["records_by_vertical"]:
            lines.append(f"| `{entry.get('vertical')}` | {entry.get('count')} |")
    safety = report.get("safety_summary") or {}
    if safety:
        lines.extend([
            "",
            "## Safety Summary",
            "",
            f"- Diagnostic only: `{str(safety.get('diagnostic_only', True)).lower()}`",
            f"- Affects evaluator gates: `{str(safety.get('affects_evaluator_gates', False)).lower()}`",
            f"- Graph emits evaluator input: `{str(safety.get('graph_emits_evaluator_input', False)).lower()}`",
            f"- Graph can create candidate pair: `{str(safety.get('graph_can_create_candidate_pair', False)).lower()}`",
            f"- Graph can claim exact payoff: `{str(safety.get('graph_can_claim_exact_payoff', False)).lower()}`",
            f"- LLM advisory only: `{str(safety.get('llm_advisory_only', True)).lower()}`",
            f"- RV relationship layer diagnostic only: `{str(safety.get('rv_relationship_layer_diagnostic_only', True)).lower()}`",
        ])
    lines.extend([
        "",
        "## Next Recommended Actions",
        "",
    ])
    if report["next_recommended_actions"]:
        lines.extend(f"- `{action}`" for action in report["next_recommended_actions"])
    else:
        lines.append("- none")
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- `{blocker}`" for blocker in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(_markdown_top_blockers_table(report["top_blockers_by_frequency"]))
    lines.extend(_markdown_table("Top Persistent High Confidence", report["top_persistent_high_confidence"], "current_severity"))
    lines.extend(_markdown_table("Top Worsening", report["top_worsening"], "severity_delta"))
    lines.extend(_markdown_table("Top Probability Constraints", report["top_probability_constraints"], "severity_score"))
    lines.extend(_markdown_table("Top RV Handoff Packets", report["top_rv_handoff_packets"], "priority_score"))
    lines.extend(
        _markdown_table(
            "Top Platform Expansion Recommendations",
            report["top_platform_expansion_recommendations"],
            "score",
        )
    )
    lines.extend(_markdown_infeasibility_table(report["top_infeasibility_diagnostic"]))
    return "\n".join(lines)


def _top_probability_constraints(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if isinstance(row, dict)
        and (
            row.get("violated") is True
            or _number(row.get("severity_score")) > 0
            or _number(row.get("observed_gap")) > 0
        )
    ]
    ranked = sorted(
        candidates,
        key=lambda row: (-_number(row.get("severity_score")), -_number(row.get("observed_gap")), str(row.get("constraint_id"))),
    )
    return [
        _top_row(
            row_id=str(row.get("constraint_id") or ""),
            row_type=str(row.get("constraint_type") or ""),
            markets=_string_list(row.get("markets_involved")),
            score=_number(row.get("severity_score")),
            confidence=_optional_string(row.get("confidence_tier")),
            source="probability_constraints",
            extra={"observed_gap": _optional_number(row.get("observed_gap"))},
        )
        for row in ranked[:limit]
    ]


def _top_rv_packets(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ranked = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: (-_number(row.get("priority_score")), str(row.get("packet_id"))),
    )
    return [
        _top_row(
            row_id=str(row.get("packet_id") or ""),
            row_type=",".join(_string_list(row.get("signal_types")) or _string_list(row.get("probability_constraint_types"))),
            markets=_string_list(row.get("markets_involved")),
            score=_number(row.get("priority_score")),
            confidence=_optional_string(row.get("confidence_tier")),
            source="rv_handoff_packets",
            extra={"allowed_next_action": _optional_string(row.get("allowed_next_action"))},
        )
        for row in ranked[:limit]
    ]


def _top_platform_recommendations(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Surface the top platform-radar recommendations alongside structural signals.

    Recommendations carry no numeric ``severity_score`` (they encode an expected
    fetch value tier), so we project the tier onto a 0-100 score so the row
    matches the existing top-row schema and lets reviewers sort the daily radar
    table.
    """

    tier_score = {"HIGH": 75.0, "MEDIUM": 50.0, "LOW": 25.0}
    ranked = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: (
            -tier_score.get(str(row.get("expected_value_of_fetch")), 0.0),
            -_int(row.get("ontology_priority_score")),
            str(row.get("missing_platform_or_venue")),
        ),
    )
    return [
        _top_row(
            row_id=f"platform:{row.get('missing_platform_or_venue', '')}:{row.get('family', '')}",
            row_type=f"{row.get('family', '')}:{row.get('expected_value_of_fetch', '')}",
            markets=[],
            score=tier_score.get(str(row.get("expected_value_of_fetch")), 0.0),
            confidence=str(row.get("expected_value_of_fetch")) if row.get("expected_value_of_fetch") in tier_score else None,
            source="platform_expansion_radar",
            extra={
                "missing_platform_or_venue": str(row.get("missing_platform_or_venue") or ""),
                "allowed_next_action": _optional_string(row.get("allowed_next_action")),
                "ontology_priority_score": _int(row.get("ontology_priority_score")),
            },
        )
        for row in ranked[:limit]
    ]


def _top_infeasibility_diagnostic(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("feasibility_status") == "INFEASIBLE_DIAGNOSTIC"
        and _number(row.get("infeasibility_gap")) > 0
    ]
    ranked = sorted(
        candidates,
        key=lambda row: (-_number(row.get("infeasibility_gap")), str(row.get("bridge_id"))),
    )
    return [
        _top_row(
            row_id=str(row.get("bridge_id") or ""),
            row_type=",".join(_string_list(row.get("constraint_types_represented"))) or str(row.get("feasibility_status") or ""),
            markets=_string_list(row.get("markets_involved")),
            score=round(_number(row.get("infeasibility_gap")) * 100, 6),
            confidence=None,
            source="payoff_state_feasibility_bridge",
            extra={
                "state_family_id": _optional_string(row.get("state_family_id")),
                "feasibility_status": str(row.get("feasibility_status") or ""),
                "blockers": _safe_blockers(row.get("review_blockers")),
                "why_review_only_yet": _optional_string(row.get("why_review_only_yet")) or "",
                "infeasibility_gap": _number(row.get("infeasibility_gap")),
                **_worst_contract_repair(row.get("per_contract_repair")),
            },
        )
        for row in ranked[:limit]
    ]


def _top_rows(rows: Any, *, limit: int, source: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    output: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        output.append(
            _top_row(
                row_id=str(row.get("signal_key") or row.get("item_id") or ""),
                row_type=str(row.get("signal_type") or ""),
                markets=_string_list(row.get("markets_involved")),
                score=_number(row.get("current_severity")),
                confidence=_optional_string(row.get("current_confidence")),
                source=source,
                extra={
                    "persistence_status": _optional_string(row.get("persistence_status")),
                    "severity_delta": _optional_number(row.get("severity_delta")),
                },
            )
        )
    return output


def _top_row(
    *,
    row_id: str,
    row_type: str,
    markets: list[str],
    score: float,
    confidence: str | None,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "row_id": row_id,
        "row_type": row_type,
        "markets_involved": list(markets),
        "score": round(score, 6),
        "confidence_tier": confidence,
        "source_report": source,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
    }
    row.update(extra or {})
    return row


def _content_blockers(
    signals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    bridge_rows: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    stale_lag_rows: list[dict[str, Any]],
) -> list[str]:
    blockers: set[str] = set()
    if not signals:
        blockers.add("no_signal_rows_available")
    if not constraints:
        blockers.add("no_probability_constraint_rows_available")
    if not bridge_rows:
        blockers.add("no_payoff_state_bridge_rows_available")
    if not packets:
        blockers.add("no_rv_handoff_packets_available")
    if not stale_lag_rows:
        blockers.add("no_stale_lag_rows_available")
    return sorted(blockers)


def _next_actions(
    blockers: list[str],
    summary: dict[str, int],
    top_constraints: list[dict[str, Any]],
    top_packets: list[dict[str, Any]],
    platform_gap_rows: list[dict[str, Any]] | None = None,
    ontology_summary: dict[str, Any] | None = None,
) -> list[str]:
    actions: list[str] = []
    if any(blocker.startswith("missing_input_report:") for blocker in blockers):
        actions.append("REBUILD_MISSING_INPUT_REPORTS")
    if summary["persistent_high_confidence_signals"] > 0:
        actions.append("REVIEW_PERSISTENT_HIGH_CONFIDENCE")
    if summary["worsened_signals"] > 0:
        actions.append("REVIEW_WORSENING_DIAGNOSTICS")
    if top_constraints:
        actions.append("REVIEW_TOP_PROBABILITY_CONSTRAINTS")
    if top_packets:
        actions.append("REVIEW_RV_HANDOFF_PACKETS")
    high_value_gap_rows = [
        row
        for row in platform_gap_rows or []
        if isinstance(row, dict) and row.get("expected_value_of_fetch") == "HIGH"
    ]
    if high_value_gap_rows:
        actions.append("REVIEW_PLATFORM_EXPANSION_GAPS")
    if summary["bridge_infeasible_diagnostic_count"] > 0:
        actions.append("REVIEW_TOP_INFEASIBILITY")
    low_confidence = (
        len(_string_list(ontology_summary.get("low_confidence_entities")))
        if isinstance(ontology_summary, dict)
        else 0
    )
    missing_coverage = (
        len(_string_list(ontology_summary.get("families_with_missing_entity_coverage")))
        if isinstance(ontology_summary, dict)
        else 0
    )
    if low_confidence > 0 or missing_coverage > 0:
        actions.append("REVIEW_ONTOLOGY_COVERAGE")
    return actions


def _bridge_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    statuses = {
        "FEASIBLE",
        "INFEASIBLE_DIAGNOSTIC",
        "BLOCKED_MISSING_PAYOFF_MATRIX",
        "BLOCKED_MISSING_PROBABILITY_INPUTS",
        "BLOCKED_MISSING_STATE_FAMILY",
        "BLOCKED_UNSUPPORTED_CONSTRAINT_TYPE",
    }
    return {status: sum(1 for row in rows if row.get("feasibility_status") == status) for status in statuses}


def _top_blockers_by_frequency(
    *,
    signals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    packets: list[dict[str, Any]],
    stale_lag_rows: list[dict[str, Any]],
    bridge_rows: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Rank diagnostic blockers by how many rows they gate across all reports.

    The point is to give the operator a single ladder of *what to fix first*:
    if the same blocker appears across signal/constraint/packet rows, fixing it
    can unblock many downstream packets at once. The blockers themselves are
    sorted by total frequency, then by source-report diversity, then
    alphabetically for deterministic ordering.
    """

    sources: dict[str, str] = {
        "signals": "trade_indicators",
        "constraints": "probability_constraints",
        "packets": "rv_handoff_packets",
        "stale_lag": "stale_lag_watchlist",
        "bridge": "payoff_state_feasibility_bridge",
    }
    by_blocker: dict[str, dict[str, int]] = {}

    def _add(blocker: str, source: str) -> None:
        if not isinstance(blocker, str) or not blocker:
            return
        if blocker in BOILERPLATE_BLOCKERS:
            return
        by_blocker.setdefault(blocker, {label: 0 for label in sources.values()})
        by_blocker[blocker][sources[source]] += 1

    for row in signals:
        for blocker in _string_list(row.get("review_blockers")):
            _add(blocker, "signals")
    for row in constraints:
        for blocker in _string_list(row.get("review_blockers")):
            _add(blocker, "constraints")
    for row in packets:
        for blocker in _string_list(row.get("packet_blockers")):
            _add(blocker, "packets")
    for row in stale_lag_rows:
        for blocker in _string_list(row.get("blockers")):
            _add(blocker, "stale_lag")
    for row in bridge_rows:
        for blocker in _string_list(row.get("review_blockers")):
            _add(blocker, "bridge")

    ranked: list[dict[str, Any]] = []
    for blocker, counts in by_blocker.items():
        total = sum(counts.values())
        source_reports = sorted(label for label, count in counts.items() if count > 0)
        ranked.append(
            {
                "blocker": blocker,
                "total_rows_blocked": total,
                "by_source_report": {label: count for label, count in counts.items() if count > 0},
                "source_reports": source_reports,
                "diagnostic_only": True,
                "affects_evaluator_gates": False,
                "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
            }
        )
    ranked.sort(
        key=lambda row: (
            -int(row["total_rows_blocked"]),
            -len(row["source_reports"]),
            row["blocker"],
        )
    )
    return ranked[:limit]


def _freshness_bucket_counts(
    report: dict[str, Any] | None,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Mirror the stale/lag freshness bucket counts at the daily-radar layer."""

    counts = {bucket: 0 for bucket in FRESHNESS_BUCKETS}
    if isinstance(report, dict):
        passthrough = report.get("freshness_buckets")
        if isinstance(passthrough, dict):
            for bucket in FRESHNESS_BUCKETS:
                value = passthrough.get(bucket)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    counts[bucket] = value
            return counts
    for row in rows:
        bucket = row.get("freshness_bucket")
        if isinstance(bucket, str) and bucket in counts:
            counts[bucket] += 1
    return counts


def _packet_kind_counts(
    report: dict[str, Any] | None,
    packets: list[dict[str, Any]],
) -> dict[str, int]:
    """Surface RV packet kind counts so operators can see review focus.

    The radar reads the upstream report's ``summary.by_packet_kind`` when it is
    present (the packet builder already provides the canonical roll-up), and
    falls back to re-counting from the packet list when only the rows are
    available.
    """

    counts = {kind: 0 for kind in PACKET_KINDS}
    if isinstance(report, dict):
        passthrough = (
            report.get("summary", {}).get("by_packet_kind") if isinstance(report.get("summary"), dict) else None
        )
        if isinstance(passthrough, dict):
            for kind in PACKET_KINDS:
                value = passthrough.get(kind)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    counts[kind] = value
            return counts
    for packet in packets:
        kind = packet.get("packet_kind")
        if isinstance(kind, str) and kind in counts:
            counts[kind] += 1
    return counts


def _midpoint_blocked_count(
    signals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    packets: list[dict[str, Any]],
) -> int:
    return (
        sum(1 for row in signals if _has_blocker(row, "diagnostic_midpoint_not_actionable"))
        + sum(
            1
            for row in constraints
            if row.get("midpoint_only") is True
            or _has_blocker(row, "diagnostic_midpoint_not_actionable")
            or row.get("uses_yes_price_equal_to_midpoint") is True
        )
        + sum(1 for row in packets if "midpoint_only_gap" in _string_list(row.get("packet_blockers")))
    )


def _yes_price_equal_to_midpoint_count(
    signals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> int:
    signal_count = sum(
        1
        for row in signals
        if isinstance(row.get("probability_inputs_used"), list)
        and any(
            isinstance(item, dict) and item.get("yes_price_equals_midpoint") is True
            for item in row["probability_inputs_used"]
        )
    )
    constraint_count = sum(
        1
        for row in constraints
        if row.get("uses_yes_price_equal_to_midpoint") is True
        or (
            isinstance(row.get("probability_inputs"), list)
            and any(
                isinstance(item, dict) and item.get("yes_price_equals_midpoint") is True
                for item in row["probability_inputs"]
            )
        )
    )
    return signal_count + constraint_count


def _stale_blocked_signal_constraint_packet_count(
    signals: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    packets: list[dict[str, Any]],
) -> int:
    """Count signal/constraint/packet rows blocked by stale or missing quote inputs.

    The dedicated stale/lag watchlist is counted separately via
    ``stale_lag_blocked_count``; including those rows here would double-count
    the same evidence and make the daily radar look noisier than it is.
    """

    stale_terms = {"stale_quote", "missing_quote_timestamp", "stale_or_missing_quote"}
    return (
        sum(1 for row in signals if _has_any_blocker(row, stale_terms))
        + sum(
            1
            for row in constraints
            if row.get("has_stale_or_missing_quote") is True or _has_any_blocker(row, stale_terms)
        )
        + sum(1 for row in packets if stale_terms & set(_string_list(row.get("packet_blockers"))))
    )


def _stale_lag_watch_count(report: dict[str, Any] | None, rows: list[dict[str, Any]]) -> int:
    if isinstance(report, dict) and isinstance(report.get("stale_lag_watch_count"), int):
        return report["stale_lag_watch_count"]
    return sum(1 for row in rows if row.get("deterministic_lag_evidence") is True)


def _stale_lag_blocked_count(report: dict[str, Any] | None, rows: list[dict[str, Any]]) -> int:
    if isinstance(report, dict) and isinstance(report.get("stale_lag_blocked_count"), int):
        return report["stale_lag_blocked_count"]
    return sum(1 for row in rows if row.get("deterministic_lag_evidence") is not True)


def _uniform_timestamp_stale_blocked_count(report: dict[str, Any] | None, rows: list[dict[str, Any]]) -> int:
    if isinstance(report, dict):
        count = report.get("uniform_timestamps_blocked_count")
        if isinstance(count, int) and not isinstance(count, bool):
            return count
    return sum(1 for row in rows if UNIFORM_TIMESTAMP_STALE_BLOCKER in _string_list(row.get("blockers")))


def _has_blocker(row: dict[str, Any], blocker: str) -> bool:
    return blocker in _string_list(row.get("review_blockers"))


def _has_any_blocker(row: dict[str, Any], blockers: set[str]) -> bool:
    return bool(blockers & set(_string_list(row.get("review_blockers"))))


def _list_from_report(report: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    rows = report.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _load_optional_report(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SchemaValidationError(f"{path} must contain a JSON object")
    return payload


def _report_value(report: dict[str, Any] | None, key: str) -> Any:
    return report.get(key) if isinstance(report, dict) else None


def _first_string(values: list[Any]) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _int_summary(summary: Any, key: str) -> int:
    if isinstance(summary, dict) and isinstance(summary.get(key), int) and not isinstance(summary.get(key), bool):
        return summary[key]
    return 0


def _int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _number(value: Any) -> float:
    numeric = _optional_number(value)
    return numeric if numeric is not None else 0.0


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _safe_blockers(value: Any) -> list[str]:
    return [
        blocker
        for blocker in _string_list(value)
        if not _contains_central_prohibited_vocab(blocker)
    ]


def _contains_central_prohibited_vocab(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(_token_pattern(token).search(normalized) for token in PROHIBITED_REPORT_TOKENS) or any(
        phrase in normalized for phrase in PROHIBITED_REPORT_PHRASES
    )


def _token_pattern(token: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(token)}\b")


def _worst_contract_repair(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {"worst_contract_id": None, "worst_contract_repair_gap": 0.0}
    candidates = [
        (str(contract_id), float(gap))
        for contract_id, gap in value.items()
        if isinstance(contract_id, str)
        and isinstance(gap, (int, float))
        and not isinstance(gap, bool)
        and gap >= 0
    ]
    if not candidates:
        return {"worst_contract_id": None, "worst_contract_repair_gap": 0.0}
    contract_id, gap = sorted(candidates, key=lambda item: (-item[1], item[0]))[0]
    return {"worst_contract_id": contract_id, "worst_contract_repair_gap": round(gap, 6)}


def _validate_top_blocker_row(row: dict[str, Any], path: str) -> None:
    if not isinstance(row, dict):
        raise SchemaValidationError(f"{path} must be an object")
    for key in [
        "blocker",
        "total_rows_blocked",
        "by_source_report",
        "source_reports",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
    ]:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if not isinstance(row["blocker"], str) or not row["blocker"]:
        raise SchemaValidationError(f"{path}.blocker must be a non-empty string")
    if (
        not isinstance(row["total_rows_blocked"], int)
        or isinstance(row["total_rows_blocked"], bool)
        or row["total_rows_blocked"] <= 0
    ):
        raise SchemaValidationError(f"{path}.total_rows_blocked must be a positive integer")
    if not isinstance(row["by_source_report"], dict):
        raise SchemaValidationError(f"{path}.by_source_report must be an object")
    for source, count in row["by_source_report"].items():
        if not isinstance(source, str):
            raise SchemaValidationError(f"{path}.by_source_report keys must be strings")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise SchemaValidationError(f"{path}.by_source_report[{source!r}] must be a positive integer")
    if not isinstance(row["source_reports"], list) or not all(isinstance(item, str) for item in row["source_reports"]):
        raise SchemaValidationError(f"{path}.source_reports must be a list of strings")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    _reject_prohibited_tokens(row)


def _validate_top_row(row: dict[str, Any], path: str) -> None:
    required = [
        "row_id",
        "row_type",
        "markets_involved",
        "score",
        "confidence_tier",
        "source_report",
        "diagnostic_only",
        "affects_evaluator_gates",
        "allowed_actions",
    ]
    for key in required:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["diagnostic_only"] is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row["affects_evaluator_gates"] is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row["allowed_actions"] != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if not isinstance(row["markets_involved"], list):
        raise SchemaValidationError(f"{path}.markets_involved must be a list")
    if not isinstance(row["score"], (int, float)) or isinstance(row["score"], bool):
        raise SchemaValidationError(f"{path}.score must be numeric")
    _reject_prohibited_tokens(row)


def _validate_infeasibility_top_row(row: dict[str, Any], path: str) -> None:
    for key in [
        "state_family_id",
        "feasibility_status",
        "blockers",
        "why_review_only_yet",
        "infeasibility_gap",
        "worst_contract_id",
        "worst_contract_repair_gap",
    ]:
        if key not in row:
            raise SchemaValidationError(f"{path}.{key} is required")
    if row["state_family_id"] is not None and not isinstance(row["state_family_id"], str):
        raise SchemaValidationError(f"{path}.state_family_id must be a string or null")
    if row["feasibility_status"] != "INFEASIBLE_DIAGNOSTIC":
        raise SchemaValidationError(f"{path}.feasibility_status must be INFEASIBLE_DIAGNOSTIC")
    if not isinstance(row["blockers"], list) or not all(isinstance(item, str) for item in row["blockers"]):
        raise SchemaValidationError(f"{path}.blockers must be a list of strings")
    if not isinstance(row["why_review_only_yet"], str) or not row["why_review_only_yet"]:
        raise SchemaValidationError(f"{path}.why_review_only_yet must be a non-empty string")
    if not isinstance(row["infeasibility_gap"], (int, float)) or isinstance(row["infeasibility_gap"], bool):
        raise SchemaValidationError(f"{path}.infeasibility_gap must be numeric")
    if row["infeasibility_gap"] <= 0:
        raise SchemaValidationError(f"{path}.infeasibility_gap must be positive")
    if row["worst_contract_id"] is not None and not isinstance(row["worst_contract_id"], str):
        raise SchemaValidationError(f"{path}.worst_contract_id must be a string or null")
    if not isinstance(row["worst_contract_repair_gap"], (int, float)) or isinstance(
        row["worst_contract_repair_gap"], bool
    ):
        raise SchemaValidationError(f"{path}.worst_contract_repair_gap must be numeric")
    if row["worst_contract_repair_gap"] < 0:
        raise SchemaValidationError(f"{path}.worst_contract_repair_gap must be non-negative")
    _reject_prohibited_tokens(row)


def _markdown_top_blockers_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Top Blockers (by row count)",
        "",
        "| Blocker | Rows blocked | Source reports |",
        "| --- | --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |  |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("blocker")),
                    _md(row.get("total_rows_blocked")),
                    _md(", ".join(row.get("source_reports") or [])),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _markdown_table(title: str, rows: list[dict[str, Any]], score_key: str) -> list[str]:
    lines = [
        "",
        f"## {title}",
        "",
        "| Source | Type | Score | Confidence | Markets |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |  |  |  |")
    for row in rows:
        score = row.get(score_key, row.get("score"))
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["source_report"]),
                    _md(row["row_type"]),
                    _md(score),
                    _md(row.get("confidence_tier")),
                    _md(", ".join(row["markets_involved"])),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _markdown_infeasibility_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Top Infeasibility (Diagnostic)",
        "",
        "| Source | Type | Score | State family | Status | Worst contract | Worst gap | Blockers | Markets | Why |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not rows:
        lines.append("| none |  |  |  |  |  |  |  |  |  |")
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["source_report"]),
                    _md(row["row_type"]),
                    _md(row["score"]),
                    _md(row.get("state_family_id")),
                    _md(row.get("feasibility_status")),
                    _md(row.get("worst_contract_id")),
                    _md(row.get("worst_contract_repair_gap")),
                    _md(", ".join(row.get("blockers") or [])),
                    _md(", ".join(row["markets_involved"])),
                    _md(row.get("why_review_only_yet")),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _rv_relationship_summary(
    rv_edges_report: dict[str, Any] | None,
    rv_worklist_report: dict[str, Any] | None,
    llm_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Roll up the RV-diagnostic relationship surfaces for the ops radar.

    Returns a self-contained block so the daily operator surface can show
    relationship counts, crypto payoff-calendar edge counts, worklist
    rows, and LLM prompt readiness without re-reading the source files.
    """

    summary = {
        "edges_total": 0,
        "edges_basis_risk": 0,
        "edges_near_exact_review": 0,
        "edges_structural": 0,
        "edges_weak_signal": 0,
        "edges_reference_only": 0,
        "crypto_payoff_calendar_edges": 0,
        "worklist_row_count": 0,
        "worklist_rv_can_inspect_now_count": 0,
        "top_blockers": [],
        "top_manual_discovery_priority": None,
        "llm_prompt_present": bool(llm_status.get("prompt_present")) if isinstance(llm_status, dict) else False,
        "llm_schema_present": bool(llm_status.get("schema_present")) if isinstance(llm_status, dict) else False,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }
    if isinstance(rv_edges_report, dict):
        edge_summary = rv_edges_report.get("summary") if isinstance(rv_edges_report.get("summary"), dict) else {}
        summary["edges_total"] = int(edge_summary.get("total_edges") or 0)
        for entry in edge_summary.get("edges_by_family") or []:
            if not isinstance(entry, dict):
                continue
            family = entry.get("relationship_family")
            count = entry.get("count")
            if not isinstance(count, int):
                continue
            if family == "basis_risk":
                summary["edges_basis_risk"] = count
            elif family == "near_exact_review":
                summary["edges_near_exact_review"] = count
            elif family == "structural":
                summary["edges_structural"] = count
            elif family == "weak_signal":
                summary["edges_weak_signal"] = count
            elif family == "reference_only":
                summary["edges_reference_only"] = count
        crypto_section = rv_edges_report.get("core_trio_crypto_section")
        if isinstance(crypto_section, dict) and isinstance(crypto_section.get("edge_count"), int):
            summary["crypto_payoff_calendar_edges"] = crypto_section["edge_count"]
        top_blockers = edge_summary.get("top_blockers") if isinstance(edge_summary.get("top_blockers"), list) else []
        summary["top_blockers"] = [
            {"blocker": entry.get("blocker"), "count": entry.get("count")}
            for entry in top_blockers[:5]
            if isinstance(entry, dict) and isinstance(entry.get("blocker"), str)
        ]
        priorities = rv_edges_report.get("manual_discovery_priorities")
        if isinstance(priorities, list):
            for priority in priorities:
                if isinstance(priority, dict) and priority.get("priority") == "HIGH":
                    summary["top_manual_discovery_priority"] = {
                        "family": priority.get("family"),
                        "reason": priority.get("reason"),
                    }
                    break
            if summary["top_manual_discovery_priority"] is None and priorities:
                first = priorities[0]
                if isinstance(first, dict):
                    summary["top_manual_discovery_priority"] = {
                        "family": first.get("family"),
                        "reason": first.get("reason"),
                    }
    if isinstance(rv_worklist_report, dict):
        worklist_summary = rv_worklist_report.get("summary") if isinstance(rv_worklist_report.get("summary"), dict) else {}
        summary["worklist_row_count"] = int(worklist_summary.get("total_rows") or 0)
        rows = rv_worklist_report.get("rows")
        if isinstance(rows, list):
            summary["worklist_rv_can_inspect_now_count"] = sum(
                1 for row in rows if isinstance(row, dict) and row.get("rv_can_inspect_now")
            )
    return summary


def _safety_summary() -> dict[str, Any]:
    return {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "graph_emits_evaluator_input": False,
        "graph_can_create_candidate_pair": False,
        "graph_can_claim_exact_payoff": False,
        "llm_advisory_only": True,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "rv_relationship_layer_diagnostic_only": True,
        "manual_evidence_layer_diagnostic_only": True,
    }


def _manual_evidence_summary(
    evidence_report: dict[str, Any] | None,
    backlog_report: dict[str, Any] | None,
    llm_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Roll up the manual-evidence inventory + backlog for the daily radar."""

    summary: dict[str, Any] = {
        "total_records": 0,
        "ready_for_rv_now": 0,
        "blocked_count": 0,
        "records_by_vertical": [],
        "top_blockers": [],
        "top_vertical_by_manual_work": None,
        "backlog_total": 0,
        "backlog_high_urgency": 0,
        "backlog_by_vertical": [],
        "llm_prompt_present": bool(llm_status.get("prompt_present")) if isinstance(llm_status, dict) else False,
        "llm_schema_present": bool(llm_status.get("schema_present")) if isinstance(llm_status, dict) else False,
        "llm_prompt_path": llm_status.get("prompt_path") if isinstance(llm_status, dict) else None,
        "llm_schema_path": llm_status.get("schema_path") if isinstance(llm_status, dict) else None,
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
    }
    if isinstance(evidence_report, dict):
        evidence_summary = evidence_report.get("summary") if isinstance(evidence_report.get("summary"), dict) else {}
        summary["total_records"] = int(evidence_summary.get("total_records") or 0)
        summary["ready_for_rv_now"] = int(evidence_summary.get("ready_for_rv_now") or 0)
        summary["blocked_count"] = int(evidence_summary.get("blocked_on_manual_evidence") or 0)
        records_by_vertical = evidence_summary.get("records_by_vertical")
        if isinstance(records_by_vertical, list):
            summary["records_by_vertical"] = [
                {"vertical": entry.get("vertical"), "count": entry.get("count")}
                for entry in records_by_vertical
                if isinstance(entry, dict)
            ]
            if summary["records_by_vertical"]:
                summary["top_vertical_by_manual_work"] = summary["records_by_vertical"][0]["vertical"]
        top_blockers = evidence_summary.get("top_blockers")
        if isinstance(top_blockers, list):
            summary["top_blockers"] = [
                {"blocker": entry.get("blocker"), "count": entry.get("count")}
                for entry in top_blockers[:5]
                if isinstance(entry, dict)
            ]
    if isinstance(backlog_report, dict):
        backlog_summary = backlog_report.get("summary") if isinstance(backlog_report.get("summary"), dict) else {}
        summary["backlog_total"] = int(backlog_summary.get("total_items") or 0)
        urgency = backlog_summary.get("by_urgency") if isinstance(backlog_summary.get("by_urgency"), dict) else {}
        summary["backlog_high_urgency"] = int(urgency.get("HIGH") or 0)
        backlog_vertical = backlog_summary.get("by_vertical")
        if isinstance(backlog_vertical, list):
            summary["backlog_by_vertical"] = [
                {"vertical": entry.get("vertical"), "count": entry.get("count")}
                for entry in backlog_vertical
                if isinstance(entry, dict)
            ]
    return summary


__all__ = [
    "NEXT_RECOMMENDED_ACTIONS",
    "build_market_graph_ops_status_report",
    "render_market_graph_ops_status_markdown",
    "validate_market_graph_ops_status_report",
    "write_market_graph_ops_status_report",
]
