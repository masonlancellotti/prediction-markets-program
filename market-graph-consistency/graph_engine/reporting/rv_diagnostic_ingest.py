"""Saved-file-only ingester for relative-value diagnostic reports.

This module reads relative-value-scanner output files from a sibling
``reports/`` directory and converts them into a relationship graph of
non-exact-but-useful edges.  It never imports relative-value Python code
and never calls a live API; defensive parsing tolerates missing files
and unexpected shapes.

The resulting JSON + Markdown reports surface:

- a list of graph nodes (one per RV market it could place),
- a list of relationship edges using ``rv_edge_taxonomy``,
- counts by relationship_type / action / blocker,
- the top relationship clusters,
- a core-trio crypto graph section,
- a queued IBKR/ForecastEx section,
- manual-discovery priorities,
- graph-to-RV handoff candidates.

All outputs are diagnostic-only and capped at WATCH /
MANUAL_REVIEW / BASIS_RISK_REVIEW / SOURCE_REVIEW /
IGNORE_LOW_CONFIDENCE.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
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
    RELATIONSHIP_VERSION,
    RV_RELATIONSHIP_TYPES,
    default_action_for,
    make_rv_edge,
    validate_rv_edge,
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
    "Saved-file-only RV diagnostic relationship graph. Non-exact relationship memory; "
    "graph never claims exact equality, never creates evaluator inputs, and is capped "
    "at WATCH / MANUAL_REVIEW / BASIS_RISK_REVIEW / SOURCE_REVIEW / IGNORE_LOW_CONFIDENCE."
)

# Subset of relative-value report filenames the importer understands.
SUPPORTED_REPORTS: dict[str, str] = {
    "kalshi_crypto_typed_key_audit.json": "kalshi_crypto_typed_key_audit",
    "polymarket_point_in_time_typed_key_audit.json": "polymarket_point_in_time_typed_key_audit",
    "polymarket_taxonomy_shape_scout.json": "polymarket_taxonomy_shape_scout",
    "polymarket_taxonomy_shape_scout_enriched.json": "polymarket_taxonomy_shape_scout_enriched",
    "polymarket_clob_taxonomy_refresh.json": "polymarket_clob_taxonomy_refresh",
    "cdna_crypto_basis_risk_scout.json": "cdna_crypto_basis_risk_scout",
    "core_trio_peer_coverage_audit.json": "core_trio_peer_coverage_audit",
    "cross_venue_opportunity_scout.json": "cross_venue_opportunity_scout",
    "crypto_peer_acquisition_plan.json": "crypto_peer_acquisition_plan",
    "ibkr_forecastex_quote_diagnostics.json": "ibkr_forecastex_quote_diagnostics",
    "ibkr_forecastex_manual_ui_memo_validation.json": "ibkr_forecastex_manual_ui_memo_validation",
    "relative_value_ops_status.json": "relative_value_ops_status",
}


def write_rv_diagnostic_relationship_edges_report(
    *,
    rv_reports_dir: Path | str,
    json_output: Path | str,
    markdown_output: Path | str,
) -> dict[str, Any]:
    """Build and persist the RV diagnostic relationship-edges report."""

    rv_reports_dir = Path(rv_reports_dir)
    report = build_rv_diagnostic_relationship_edges_report(rv_reports_dir)
    markdown = render_rv_diagnostic_relationship_edges_markdown(report)

    findings = find_prohibited_rendered_text(markdown)
    if findings:
        raise SchemaValidationError(
            "rv diagnostic relationship Markdown contains prohibited vocabulary: "
            + ", ".join(findings)
        )

    json_path = Path(json_output)
    markdown_path = Path(markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def build_rv_diagnostic_relationship_edges_report(rv_reports_dir: Path) -> dict[str, Any]:
    """Top-level deterministic builder; no live API calls."""

    available, missing = _scan_supported_reports(rv_reports_dir)

    parsed: dict[str, dict[str, Any]] = {}
    parse_errors: list[dict[str, Any]] = []
    for slug, path in available.items():
        payload, err = _load_json(path)
        if err is not None:
            parse_errors.append({"report": slug, "path": str(path), "error": err})
            continue
        if isinstance(payload, dict):
            parsed[slug] = payload

    nodes_by_id: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    manual_discovery_priorities: list[dict[str, Any]] = []

    _ingest_kalshi_crypto(parsed, nodes_by_id, edges)
    _ingest_polymarket_pit(parsed, nodes_by_id, edges)
    _ingest_polymarket_taxonomy(parsed, nodes_by_id, edges)
    _ingest_polymarket_clob_refresh(parsed, nodes_by_id, edges)
    _ingest_cdna_basis_risk(parsed, nodes_by_id, edges)
    _ingest_cross_venue_scout(parsed, nodes_by_id, edges)
    _ingest_core_trio_coverage(parsed, nodes_by_id, edges, manual_discovery_priorities)
    _ingest_crypto_peer_acquisition(parsed, nodes_by_id, edges, manual_discovery_priorities)
    _ingest_ibkr_diagnostics(parsed, nodes_by_id, edges)

    # Sanitize external title / evidence text so the safety sweep does
    # not reject the report on prohibited vocabulary that lives in RV
    # market titles. This must run before per-edge validation so the
    # validator sees the same text that gets written.
    for node_id, node in list(nodes_by_id.items()):
        nodes_by_id[node_id] = _redact_payload(node)
    for index, edge in enumerate(edges):
        edges[index] = _redact_payload(edge)
    for edge in edges:
        validate_rv_edge(edge)

    summary = _summary(nodes_by_id, edges)
    relationship_clusters = _top_relationship_clusters(edges, limit=20)
    crypto_section = _core_trio_crypto_section(nodes_by_id, edges)
    ibkr_section = _queued_ibkr_section(nodes_by_id, edges, parsed)
    handoff_candidates = _handoff_candidates(edges, limit=20)

    report: dict[str, Any] = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": list(DIAGNOSTIC_HINT_ACTIONS),
        "allowed_edge_actions": list(ALLOWED_EDGE_ACTIONS),
        "banner": REPORT_BANNER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "relationship_version": RELATIONSHIP_VERSION,
        "rv_reports_dir": str(rv_reports_dir),
        "inputs": {
            "available": sorted(available),
            "missing": sorted(missing),
            "parse_errors": parse_errors,
        },
        "summary": summary,
        "nodes": [nodes_by_id[node_id] for node_id in sorted(nodes_by_id)],
        "edges": edges,
        "top_relationship_clusters": relationship_clusters,
        "core_trio_crypto_section": crypto_section,
        "queued_ibkr_section": ibkr_section,
        "manual_discovery_priorities": manual_discovery_priorities,
        "graph_to_rv_handoff_candidates": handoff_candidates,
    }
    validate_rv_diagnostic_relationship_edges_report(report)
    return report


def validate_rv_diagnostic_relationship_edges_report(report: dict[str, Any]) -> None:
    """Cross-cutting validator for the RV-edges report."""

    _reject_prohibited_tokens(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("rv-edges report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("rv-edges report must not affect evaluator gates")
    if report.get("allowed_actions") != list(DIAGNOSTIC_HINT_ACTIONS):
        raise SchemaValidationError("rv-edges report allowed_actions must be WATCH and MANUAL_REVIEW only")
    if report.get("allowed_edge_actions") != list(ALLOWED_EDGE_ACTIONS):
        raise SchemaValidationError("rv-edges report allowed_edge_actions must match the taxonomy")
    if report.get("relationship_version") != RELATIONSHIP_VERSION:
        raise SchemaValidationError("rv-edges report relationship_version mismatch")
    for index, edge in enumerate(report.get("edges", [])):
        try:
            validate_rv_edge(edge)
        except Exception as exc:  # noqa: BLE001
            raise SchemaValidationError(f"edges[{index}] invalid: {exc}") from exc
    nodes = report.get("nodes")
    if not isinstance(nodes, list):
        raise SchemaValidationError("rv-edges report nodes must be a list")
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise SchemaValidationError("each node must be an object")
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise SchemaValidationError("node.node_id must be a non-empty string")
        if node_id in seen:
            raise SchemaValidationError(f"duplicate node_id {node_id!r}")
        seen.add(node_id)
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise SchemaValidationError("rv-edges report summary must be an object")
    for key in (
        "total_nodes",
        "total_edges",
        "edges_by_relationship_type",
        "edges_by_action",
        "edges_by_family",
        "edges_by_left_venue",
        "edges_by_right_venue",
        "top_blockers",
    ):
        if key not in summary:
            raise SchemaValidationError(f"summary.{key} is required")


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def render_rv_diagnostic_relationship_edges_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RV Diagnostic Relationship Edges",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed edge actions: `{', '.join(report['allowed_edge_actions'])}`",
        f"- Relationship version: `{report['relationship_version']}`",
        f"- RV reports dir: `{report['rv_reports_dir']}`",
        f"- Generated at: `{report['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Total nodes: {summary['total_nodes']}",
        f"- Total edges: {summary['total_edges']}",
        "",
        "### Edges by relationship_type",
        "",
        "| Relationship type | Count |",
        "| --- | --- |",
    ]
    if not summary["edges_by_relationship_type"]:
        lines.append("| none |  |")
    for entry in summary["edges_by_relationship_type"]:
        lines.append(f"| `{entry['relationship_type']}` | {entry['count']} |")
    lines.extend(["", "### Edges by action", "", "| Action | Count |", "| --- | --- |"])
    if not summary["edges_by_action"]:
        lines.append("| none |  |")
    for entry in summary["edges_by_action"]:
        lines.append(f"| `{entry['action']}` | {entry['count']} |")
    lines.extend(["", "### Top blockers", "", "| Blocker | Count |", "| --- | --- |"])
    if not summary["top_blockers"]:
        lines.append("| none |  |")
    for entry in summary["top_blockers"]:
        lines.append(f"| `{entry['blocker']}` | {entry['count']} |")

    lines.extend(["", "## Top 20 Relationship Clusters", "", "| Relationship type | Action | Confidence | Pair count |", "| --- | --- | --- | --- |"])
    if not report["top_relationship_clusters"]:
        lines.append("| none |  |  |  |")
    for cluster in report["top_relationship_clusters"]:
        lines.append(
            f"| `{cluster['relationship_type']}` | `{cluster['action']}` | `{cluster['confidence_bucket']}` | {cluster['pair_count']} |"
        )

    lines.extend(["", "## Core Trio Crypto Section", ""])
    crypto = report["core_trio_crypto_section"]
    lines.append(f"- Core trio crypto edges: {crypto['edge_count']}")
    lines.append(f"- Core trio venues seen: {', '.join(sorted(crypto['venues'])) or 'none'}")
    lines.append("")
    if crypto["edges_by_relationship_type"]:
        lines.extend(["| Relationship type | Count |", "| --- | --- |"])
        for entry in crypto["edges_by_relationship_type"]:
            lines.append(f"| `{entry['relationship_type']}` | {entry['count']} |")
    else:
        lines.append("- No core-trio crypto edges produced from current saved reports.")

    lines.extend(["", "## Queued IBKR / ForecastEx Section", ""])
    ibkr = report["queued_ibkr_section"]
    lines.append(f"- IBKR contracts seen: {ibkr['contract_count']}")
    lines.append(f"- IBKR cross-venue edges seen: {ibkr['cross_venue_edge_count']}")
    lines.append(f"- Quote diagnostics complete rows: {ibkr['quote_diagnostic_complete_rows']}")
    lines.append(f"- Quote diagnostics blocked rows: {ibkr['quote_diagnostic_blocked_rows']}")
    lines.append(f"- Manual UI memo validation present: {str(ibkr['manual_ui_memo_validation_present']).lower()}")

    lines.extend(["", "## Manual Discovery Priorities", ""])
    if not report["manual_discovery_priorities"]:
        lines.append("- none")
    for priority in report["manual_discovery_priorities"]:
        lines.append(
            f"- `{priority['family']}` :: `{priority['priority']}` :: {priority['reason']}"
        )

    lines.extend(["", "## Graph-to-RV Handoff Candidates", ""])
    if not report["graph_to_rv_handoff_candidates"]:
        lines.append("- none")
    lines.extend(["", "| Edge | Type | Action | Confidence | Blockers |", "| --- | --- | --- | --- | --- |"])
    for entry in report["graph_to_rv_handoff_candidates"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{entry['edge_id']}`",
                    f"`{entry['relationship_type']}`",
                    f"`{entry['action']}`",
                    f"`{entry['confidence_bucket']}`",
                    ", ".join(f"`{b}`" for b in entry["blockers"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Per-report ingestion helpers
# ---------------------------------------------------------------------------


def _ingest_kalshi_crypto(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("kalshi_crypto_typed_key_audit")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "kalshi_crypto_typed_key_audit.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = _kalshi_node_from_row(row, source_path)
        if node:
            nodes_by_id.setdefault(node["node_id"], node)
        peer_evidence = row.get("peer_evidence") if isinstance(row.get("peer_evidence"), dict) else {}
        # CDNA peer hints
        for cdna_candidate in peer_evidence.get("cdna_candidates", []) or []:
            if not isinstance(cdna_candidate, dict):
                continue
            cdna_node = _ad_hoc_cdna_node(cdna_candidate, source_path)
            if cdna_node:
                nodes_by_id.setdefault(cdna_node["node_id"], cdna_node)
            relationship_type = _kalshi_cdna_relationship_type(row, cdna_candidate)
            edge = make_rv_edge(
                edge_id=_edge_id("kal", "cdna", node, cdna_node),
                left_market_id=node["node_id"] if node else str(row.get("market_id") or row.get("row_id") or ""),
                right_market_id=cdna_node["node_id"] if cdna_node else None,
                left_venue="kalshi",
                right_venue="cdna",
                relationship_type=relationship_type,
                confidence_bucket=_confidence_from_evidence(row, cdna_candidate),
                evidence_fields=_kalshi_cdna_evidence(row, cdna_candidate),
                blockers=_blockers_from_row(row),
                source_report_paths=[source_path],
            )
            edges.append(edge)
        # Polymarket peer hints
        for poly_candidate in peer_evidence.get("polymarket_candidates", []) or []:
            if not isinstance(poly_candidate, dict):
                continue
            poly_node = _ad_hoc_polymarket_node(poly_candidate, source_path)
            if poly_node:
                nodes_by_id.setdefault(poly_node["node_id"], poly_node)
            relationship_type = _kalshi_polymarket_relationship_type(row, poly_candidate)
            edge = make_rv_edge(
                edge_id=_edge_id("kal", "poly", node, poly_node),
                left_market_id=node["node_id"] if node else str(row.get("market_id") or row.get("row_id") or ""),
                right_market_id=poly_node["node_id"] if poly_node else None,
                left_venue="kalshi",
                right_venue="polymarket",
                relationship_type=relationship_type,
                confidence_bucket=_confidence_from_evidence(row, poly_candidate),
                evidence_fields=_kalshi_polymarket_evidence(row, poly_candidate),
                blockers=_blockers_from_row(row),
                source_report_paths=[source_path],
            )
            edges.append(edge)
        # No-peer markets that still want to be remembered as nodes get a
        # NO_CURRENT_PEER edge to a synthetic reference id so the daily
        # worklist can prioritise discovery.
        if (row.get("peer_hints") or []) == ["no_saved_peer"] and node:
            edge = make_rv_edge(
                edge_id=_edge_id("kal", "nopeer", node, None),
                left_market_id=node["node_id"],
                right_market_id=None,
                right_reference_id="manual_discovery_required",
                left_venue="kalshi",
                right_venue="manual_discovery",
                relationship_type="NO_CURRENT_PEER",
                confidence_bucket="low",
                evidence_fields={
                    "asset": row.get("asset"),
                    "market_shape": row.get("market_shape"),
                    "target_date": row.get("target_date"),
                    "target_time": row.get("target_time"),
                    "threshold": row.get("threshold"),
                },
                blockers=("no_current_peer", "manual_discovery_required") + tuple(_blockers_from_row(row)),
                source_report_paths=[source_path],
            )
            edges.append(edge)


def _ingest_polymarket_pit(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("polymarket_point_in_time_typed_key_audit")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "polymarket_point_in_time_typed_key_audit.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        poly_node = _polymarket_node_from_pit_row(row, source_path)
        if poly_node:
            nodes_by_id.setdefault(poly_node["node_id"], poly_node)
        # Surface SAME_TOPIC_WEAK_SIGNAL/NO_CURRENT_PEER markers based on
        # the peer_lane_hints metadata so the daily worklist can cluster
        # company_metric-style rows that have no real Kalshi/CDNA peer.
        peer_hints = row.get("peer_lane_hints") if isinstance(row.get("peer_lane_hints"), dict) else {}
        if peer_hints.get("likely_no_current_peer"):
            edge = make_rv_edge(
                edge_id=_edge_id("poly", "nopeer", poly_node, None),
                left_market_id=poly_node["node_id"] if poly_node else str(row.get("market_id") or row.get("row_id") or ""),
                right_market_id=None,
                right_reference_id="manual_discovery_required",
                left_venue="polymarket",
                right_venue="manual_discovery",
                relationship_type="NO_CURRENT_PEER",
                confidence_bucket="low",
                evidence_fields={
                    "market_family": row.get("market_family"),
                    "asset_or_family": row.get("asset_or_family"),
                    "market_shape": row.get("market_shape"),
                    "target_date": row.get("target_date"),
                    "threshold": row.get("threshold"),
                    "title": row.get("title"),
                },
                blockers=("no_current_peer", "manual_discovery_required") + tuple(_blockers_from_row(row)),
                source_report_paths=[source_path],
            )
            edges.append(edge)
        # A typed_key_complete row with no peer is the strongest manual
        # discovery prompt available from this report — keep it weak but
        # tagged for clustering.
        if peer_hints.get("likely_kalshi_peer_family") or peer_hints.get("likely_cdna_peer_family"):
            edge = make_rv_edge(
                edge_id=_edge_id("poly", "topic", poly_node, None),
                left_market_id=poly_node["node_id"] if poly_node else str(row.get("market_id") or row.get("row_id") or ""),
                right_market_id=None,
                right_reference_id=f"family:{peer_hints.get('likely_kalshi_peer_family') or peer_hints.get('likely_cdna_peer_family')}",
                left_venue="polymarket",
                right_venue="cross_venue",
                relationship_type="SAME_TOPIC_WEAK_SIGNAL",
                confidence_bucket="low",
                evidence_fields={
                    "peer_lane_hints": peer_hints,
                    "market_family": row.get("market_family"),
                    "market_shape": row.get("market_shape"),
                    "target_date": row.get("target_date"),
                },
                blockers=("weak_signal_topic_only", "manual_discovery_required") + tuple(_blockers_from_row(row)),
                source_report_paths=[source_path],
            )
            edges.append(edge)


def _ingest_polymarket_taxonomy(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("polymarket_taxonomy_shape_scout") or parsed.get(
        "polymarket_taxonomy_shape_scout_enriched"
    )
    if not payload:
        return
    rows = payload.get("rows") or []
    # Only the deadline-touch / range-hit / ATH-by-date shapes are useful as
    # taxonomy edges; the rest fall through to the PIT importer.
    source_path = "polymarket_taxonomy_shape_scout.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        shape = (row.get("market_shape") or "").lower()
        if not row.get("deadline_touch_phrase_detected") and "deadline" not in shape and "range" not in shape:
            continue
        node = _polymarket_node_from_taxonomy_row(row, source_path)
        if not node:
            continue
        nodes_by_id.setdefault(node["node_id"], node)
        # Mark as deadline-touch reminder (no peer attached yet — clustering)
        edge = make_rv_edge(
            edge_id=_edge_id("poly_tax", "deadline", node, None),
            left_market_id=node["node_id"],
            right_market_id=None,
            right_reference_id="payoff_calendar_deadline_touch",
            left_venue="polymarket",
            right_venue="payoff_calendar",
            relationship_type="DEADLINE_TOUCH_VS_POINT_IN_TIME",
            confidence_bucket="low",
            evidence_fields={
                "raw_taxonomy_shape": row.get("raw_taxonomy_shape"),
                "market_shape": row.get("market_shape"),
                "family": row.get("family"),
                "recommended_pair": row.get("recommended_pair"),
            },
            blockers=("deadline_touch_not_point_in_time", "manual_discovery_required")
            + tuple(_blockers_from_row(row)),
            source_report_paths=[source_path],
        )
        edges.append(edge)


def _ingest_polymarket_clob_refresh(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("polymarket_clob_taxonomy_refresh")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "polymarket_clob_taxonomy_refresh.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = _polymarket_node_from_clob_row(row, source_path)
        if node:
            nodes_by_id.setdefault(node["node_id"], node)


def _ingest_cdna_basis_risk(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("cdna_crypto_basis_risk_scout")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "cdna_crypto_basis_risk_scout.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        cdna = row.get("cdna") if isinstance(row.get("cdna"), dict) else {}
        peer = row.get("peer") if isinstance(row.get("peer"), dict) else {}
        cdna_node = _cdna_node_from_scout_row(cdna, source_path)
        if cdna_node:
            nodes_by_id.setdefault(cdna_node["node_id"], cdna_node)
        peer_node = _peer_node_from_scout_row(peer, source_path)
        if peer_node:
            nodes_by_id.setdefault(peer_node["node_id"], peer_node)
        relationship_type = _cdna_relationship_type(cdna, peer)
        edge_blockers = tuple(_blockers_from_row(row))
        action_override = None
        action = (row.get("allowed_next_action") or "").upper() or None
        if action == "BASIS_RISK_REVIEW":
            action_override = ACTION_BASIS_RISK_REVIEW
        elif action == "MANUAL_REVIEW":
            action_override = ACTION_MANUAL_REVIEW
        elif action == "SOURCE_REVIEW":
            action_override = ACTION_SOURCE_REVIEW
        # Near-exact review relationships must stay at MANUAL_REVIEW /
        # SOURCE_REVIEW regardless of the RV row's own action label, so the
        # graph never lets BASIS_RISK_REVIEW act as a stand-in for "ready
        # for strict review" on a candidate-quality pair.
        if RV_RELATIONSHIP_TYPES.get(relationship_type) == "near_exact_review":
            if action_override not in {ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW}:
                action_override = ACTION_MANUAL_REVIEW
        edge = make_rv_edge(
            edge_id=_edge_id("cdna", "scout", cdna_node, peer_node),
            left_market_id=cdna_node["node_id"] if cdna_node else str(row.get("row_id") or "cdna_unknown"),
            right_market_id=peer_node["node_id"] if peer_node else None,
            right_reference_id=None if peer_node else "kalshi_manual_lookup",
            left_venue="cdna",
            right_venue="kalshi",
            relationship_type=relationship_type,
            action=action_override,
            confidence_bucket=_confidence_from_basis_risk(row),
            evidence_fields={
                "cdna_asset": cdna.get("asset"),
                "cdna_threshold": cdna.get("threshold_value"),
                "cdna_comparator": cdna.get("comparator"),
                "cdna_market_shape": cdna.get("market_shape_conservative"),
                "cdna_settlement_source": cdna.get("settlement_source"),
                "peer_ticker": peer.get("ticker_or_event"),
                "peer_settlement_close_time": peer.get("settlement_close_time"),
                "peer_threshold": peer.get("peer_threshold"),
                "basis_risk_priority_score": row.get("basis_risk_priority_score"),
            },
            blockers=edge_blockers,
            source_report_paths=[source_path],
        )
        edges.append(edge)


def _ingest_cross_venue_scout(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("cross_venue_opportunity_scout")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "cross_venue_opportunity_scout.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        left = row.get("left") if isinstance(row.get("left"), dict) else {}
        right = row.get("right") if isinstance(row.get("right"), dict) else {}
        left_node = _generic_node(left, source_path)
        right_node = _generic_node(right, source_path)
        if left_node:
            nodes_by_id.setdefault(left_node["node_id"], left_node)
        if right_node:
            nodes_by_id.setdefault(right_node["node_id"], right_node)
        relationship_type = _cross_venue_relationship_type(row, left, right)
        action_text = (row.get("allowed_next_action") or "").upper() or None
        action_override = None
        if action_text == "BASIS_RISK_REVIEW":
            action_override = ACTION_BASIS_RISK_REVIEW
        elif action_text == "MANUAL_REVIEW":
            action_override = ACTION_MANUAL_REVIEW
        elif action_text == "SOURCE_REVIEW":
            action_override = ACTION_SOURCE_REVIEW
        elif action_text == "WATCH":
            action_override = ACTION_WATCH
        # Make sure near-exact review only gets MANUAL_REVIEW / SOURCE_REVIEW
        family = RV_RELATIONSHIP_TYPES.get(relationship_type)
        if family == "near_exact_review":
            if action_override not in {ACTION_MANUAL_REVIEW, ACTION_SOURCE_REVIEW}:
                action_override = ACTION_MANUAL_REVIEW
        elif family == "weak_signal":
            # Weak signals must stay WATCH / IGNORE — never the RV BASIS_RISK_REVIEW lane.
            if action_override not in {ACTION_WATCH, ACTION_IGNORE_LOW_CONFIDENCE}:
                action_override = ACTION_WATCH
        elif family == "reference_only":
            if action_override not in {ACTION_SOURCE_REVIEW, ACTION_WATCH}:
                action_override = ACTION_SOURCE_REVIEW
        edge = make_rv_edge(
            edge_id=_edge_id("xv", "scout", left_node, right_node, row_id=row.get("row_id")),
            left_market_id=left_node["node_id"] if left_node else str(row.get("row_id") or "xv_unknown"),
            right_market_id=right_node["node_id"] if right_node else None,
            right_reference_id=None if right_node else "cross_venue_review_pending",
            left_venue=str(left.get("exchange_venue") or left.get("venue") or left.get("source_platform") or "unknown").lower(),
            right_venue=str(right.get("exchange_venue") or right.get("venue") or right.get("source_platform") or "unknown").lower(),
            relationship_type=relationship_type,
            action=action_override,
            confidence_bucket=_confidence_from_cross_venue(row),
            evidence_fields={
                "lane": row.get("lane"),
                "comparison": row.get("comparison"),
                "evidence_summary": row.get("evidence_summary"),
                "active_platforms": row.get("active_platforms"),
                "active_platform_status": row.get("active_platform_status"),
                "review_priority_score": row.get("review_priority_score"),
            },
            blockers=tuple(_blockers_from_row(row)),
            source_report_paths=[source_path],
        )
        edges.append(edge)


def _ingest_core_trio_coverage(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    manual_discovery_priorities: list[dict[str, Any]],
) -> None:
    payload = parsed.get("core_trio_peer_coverage_audit")
    if not payload:
        return
    families = payload.get("families") or []
    for family in families:
        if not isinstance(family, dict):
            continue
        manual_discovery_priorities.append(
            {
                "family": str(family.get("family") or "unknown"),
                "priority": "HIGH"
                if int(family.get("kalshi_typed_complete_rows_found") or 0)
                >= int(family.get("polymarket_typed_complete_rows") or 0)
                else "MEDIUM",
                "reason": str(
                    family.get("next_fetch_query_suggestion")
                    or "Manual discovery required to fill peer coverage gap"
                ),
                "kalshi_typed_complete_rows_found": int(family.get("kalshi_typed_complete_rows_found") or 0),
                "polymarket_typed_complete_rows": int(family.get("polymarket_typed_complete_rows") or 0),
                "cdna_point_in_time_rows": int(family.get("cdna_point_in_time_rows") or 0),
                "blockers": [b for b in (family.get("blockers") or []) if isinstance(b, str)],
            }
        )


def _ingest_crypto_peer_acquisition(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    manual_discovery_priorities: list[dict[str, Any]],
) -> None:
    payload = parsed.get("crypto_peer_acquisition_plan")
    if not payload:
        return
    queries = payload.get("polymarket_queries_recommended") or []
    targets = payload.get("targets") or []
    for query in queries:
        if not isinstance(query, str):
            continue
        manual_discovery_priorities.append(
            {
                "family": "polymarket_query",
                "priority": "MEDIUM",
                "reason": f"recommended polymarket peer query: {query}",
                "blockers": ["manual_discovery_required"],
            }
        )
    for target in targets:
        if not isinstance(target, dict):
            continue
        manual_discovery_priorities.append(
            {
                "family": str(target.get("family") or target.get("series_ticker") or "crypto_peer"),
                "priority": "MEDIUM",
                "reason": str(target.get("reason") or "crypto peer acquisition target"),
                "blockers": ["manual_discovery_required"],
            }
        )


def _ingest_ibkr_diagnostics(
    parsed: dict[str, dict[str, Any]],
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    payload = parsed.get("ibkr_forecastex_quote_diagnostics")
    if not payload:
        return
    rows = payload.get("rows") or []
    source_path = "ibkr_forecastex_quote_diagnostics.json"
    for row in rows:
        if not isinstance(row, dict):
            continue
        node_id = _ibkr_node_id(row)
        if not node_id:
            continue
        nodes_by_id.setdefault(
            node_id,
            {
                "node_id": node_id,
                "venue": "ibkr_forecastex",
                "asset_or_family": row.get("symbol"),
                "market_shape": "binary_yes_no",
                "title": row.get("title"),
                "target_date": row.get("maturity_date"),
                "settlement_source": None,
                "diagnostic_only": True,
                "queued_inactive": True,
                "source_report_paths": [source_path],
            },
        )


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _kalshi_node_from_row(row: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    market_id = row.get("market_id")
    if not market_id:
        return None
    return {
        "node_id": f"kalshi:{market_id}",
        "venue": "kalshi",
        "market_id": str(market_id),
        "event_ticker": row.get("event_ticker"),
        "asset_or_family": row.get("asset"),
        "market_shape": row.get("market_shape"),
        "comparator": row.get("comparator"),
        "threshold": row.get("threshold"),
        "target_date": row.get("target_date"),
        "target_time": row.get("target_time"),
        "timezone": row.get("timezone"),
        "settlement_source": row.get("settlement_source"),
        "settlement_close_time": row.get("settlement_close_time"),
        "settlement_resolution_time": row.get("settlement_resolution_time"),
        "title": row.get("title"),
        "yes_no_side": row.get("yes_no_side"),
        "quote_present": _safe_get(row, "quote", "present"),
        "typed_completeness_score": row.get("typed_completeness_score"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _polymarket_node_from_pit_row(row: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    market_id = row.get("market_id") or row.get("condition_id")
    if not market_id:
        return None
    return {
        "node_id": f"polymarket:{market_id}",
        "venue": "polymarket",
        "market_id": str(market_id),
        "condition_id": row.get("condition_id"),
        "asset_or_family": row.get("asset_or_family") or row.get("market_family"),
        "market_family": row.get("market_family"),
        "market_shape": row.get("market_shape"),
        "comparator": row.get("comparator"),
        "threshold": row.get("threshold"),
        "target_date": row.get("target_date"),
        "target_time": row.get("target_time"),
        "timezone": row.get("timezone"),
        "title": row.get("title") or row.get("question"),
        "settlement_source_present": row.get("settlement_source_present"),
        "typed_key_completeness_score": row.get("typed_key_completeness_score"),
        "clob_book_attached": row.get("clob_book_attached"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _polymarket_node_from_taxonomy_row(row: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    market_id = row.get("market_id") or row.get("condition_id")
    if not market_id:
        return None
    return {
        "node_id": f"polymarket:{market_id}",
        "venue": "polymarket",
        "market_id": str(market_id),
        "condition_id": row.get("condition_id"),
        "asset_or_family": row.get("family"),
        "market_family": row.get("family"),
        "market_shape": row.get("market_shape"),
        "raw_taxonomy_shape": row.get("raw_taxonomy_shape"),
        "title": row.get("title") or row.get("question"),
        "settlement_source_present": row.get("settlement_source_present"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _polymarket_node_from_clob_row(row: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    market_id = row.get("market_id") or row.get("condition_id")
    if not market_id:
        return None
    return {
        "node_id": f"polymarket:{market_id}",
        "venue": "polymarket",
        "market_id": str(market_id),
        "condition_id": row.get("condition_id"),
        "asset_or_family": row.get("family"),
        "market_shape": row.get("market_shape"),
        "title": row.get("title") or row.get("question"),
        "clob_book_attached_now": row.get("clob_book_attached_now"),
        "review_priority_score": row.get("review_priority_score"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _cdna_node_from_scout_row(cdna: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    title = cdna.get("title")
    if not title and not cdna.get("source_url"):
        return None
    node_key = cdna.get("source_url") or title or "unknown"
    node_id = f"cdna:{node_key}"
    return {
        "node_id": node_id,
        "venue": "cdna",
        "asset_or_family": cdna.get("asset"),
        "market_shape": cdna.get("market_shape_conservative") or cdna.get("market_type"),
        "comparator": cdna.get("comparator"),
        "threshold": cdna.get("threshold_value"),
        "target_date": cdna.get("target_date"),
        "settlement_source": cdna.get("settlement_source"),
        "settlement_source_url": cdna.get("settlement_source_url"),
        "title": title,
        "price_source_index": cdna.get("price_source_index"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _peer_node_from_scout_row(peer: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    if not peer:
        return None
    ticker = peer.get("ticker_or_event")
    venue = (peer.get("venue") or "kalshi").lower()
    if not ticker:
        return None
    return {
        "node_id": f"{venue}:{ticker}",
        "venue": venue,
        "market_id": ticker,
        "title": peer.get("title"),
        "settlement_close_time": peer.get("settlement_close_time"),
        "peer_threshold": peer.get("peer_threshold"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _generic_node(payload: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    if not payload:
        return None
    venue = (
        payload.get("exchange_venue")
        or payload.get("venue")
        or payload.get("source_platform")
        or "unknown"
    )
    venue = str(venue).lower()
    market_id = (
        payload.get("market_id_or_conid")
        or payload.get("market_id")
        or payload.get("condition_id")
        or payload.get("ticker")
        or payload.get("contract_conid")
    )
    if not market_id:
        return None
    node_id = f"{venue}:{market_id}"
    return {
        "node_id": node_id,
        "venue": venue,
        "market_id": str(market_id),
        "asset_or_family": payload.get("event_family"),
        "market_shape": payload.get("market_shape"),
        "comparator": payload.get("comparator"),
        "threshold": payload.get("threshold"),
        "target_date": payload.get("settlement_event_date") or payload.get("fomc_meeting_date") or payload.get("target_date"),
        "settlement_source": payload.get("settlement_source"),
        "settlement_source_url": payload.get("settlement_source_url"),
        "title": payload.get("title"),
        "quote_present": isinstance(payload.get("quote"), dict),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _ad_hoc_cdna_node(payload: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    if not payload:
        return None
    title = payload.get("title")
    source_url = payload.get("source_url") or payload.get("event_url")
    if not title and not source_url:
        return None
    node_key = source_url or title
    return {
        "node_id": f"cdna:{node_key}",
        "venue": "cdna",
        "title": title,
        "source_url": source_url,
        "asset_or_family": payload.get("asset"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _ad_hoc_polymarket_node(payload: dict[str, Any], source_path: str) -> dict[str, Any] | None:
    if not payload:
        return None
    market_id = payload.get("market_id") or payload.get("condition_id") or payload.get("event_slug")
    if not market_id:
        return None
    return {
        "node_id": f"polymarket:{market_id}",
        "venue": "polymarket",
        "market_id": str(market_id),
        "condition_id": payload.get("condition_id"),
        "title": payload.get("title") or payload.get("question"),
        "asset_or_family": payload.get("asset_or_family") or payload.get("family"),
        "market_shape": payload.get("market_shape"),
        "diagnostic_only": True,
        "source_report_paths": [source_path],
    }


def _ibkr_node_id(row: dict[str, Any]) -> str | None:
    conid = row.get("contract_conid")
    if conid is None:
        return None
    return f"ibkr_forecastex:{conid}"


# ---------------------------------------------------------------------------
# Relationship-type classifiers
# ---------------------------------------------------------------------------


def _kalshi_cdna_relationship_type(row: dict[str, Any], cdna_candidate: dict[str, Any]) -> str:
    shape_left = str(row.get("market_shape") or "").lower()
    shape_right = str(cdna_candidate.get("market_shape") or cdna_candidate.get("market_shape_conservative") or "").lower()
    if "all_time_high" in shape_left or "all_time_high" in shape_right:
        return "ALL_TIME_HIGH_BY_DATE_VS_POINT_IN_TIME"
    if "point_in_time" in shape_left and "point_in_time" in shape_right:
        return "SAME_EVENT_DIFFERENT_SOURCE_REVIEW"
    if shape_left == shape_right and shape_left.startswith("point_in_time"):
        return "SAME_EVENT_DIFFERENT_SOURCE_REVIEW"
    return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"


def _kalshi_polymarket_relationship_type(row: dict[str, Any], poly_candidate: dict[str, Any]) -> str:
    shape_left = str(row.get("market_shape") or "").lower()
    shape_right = str(poly_candidate.get("market_shape") or "").lower()
    poly_family = str(poly_candidate.get("market_family") or "").lower()
    if "deadline" in shape_right or "earliest" in shape_right or "range" in shape_right:
        return "DEADLINE_TOUCH_VS_POINT_IN_TIME"
    if "hit" in shape_right or "touch" in shape_right:
        return "INTRADAY_TOUCH_VS_DAILY_CLOSE"
    if poly_family == "company_metric":
        return "SAME_TOPIC_WEAK_SIGNAL"
    if "point_in_time" in shape_left and "point_in_time" in shape_right:
        return "SAME_EVENT_DIFFERENT_SOURCE_REVIEW"
    return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"


def _cdna_relationship_type(cdna: dict[str, Any], peer: dict[str, Any]) -> str:
    cdna_shape = str(cdna.get("market_shape_conservative") or cdna.get("market_type") or "").lower()
    if "all_time_high" in cdna_shape:
        return "ALL_TIME_HIGH_BY_DATE_VS_POINT_IN_TIME"
    if "deadline" in cdna_shape:
        return "DEADLINE_TOUCH_VS_POINT_IN_TIME"
    if "year_end_range" in cdna_shape or "range_bucket" in cdna_shape:
        return "RANGE_BUCKET_VS_THRESHOLD"
    if "earliest_timeframe" in cdna_shape:
        return "WEEKLY_FRIDAY_CLOSE_VS_DEADLINE_TOUCH"
    if cdna_shape == "point_in_time_threshold":
        if peer:
            return "SAME_EVENT_DIFFERENT_SOURCE_REVIEW"
        return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"
    return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"


def _cross_venue_relationship_type(row: dict[str, Any], left: dict[str, Any], right: dict[str, Any]) -> str:
    comparison = row.get("comparison") if isinstance(row.get("comparison"), dict) else {}
    settlement_rel = str(comparison.get("settlement_source_relation") or "").lower()
    if "midpoint_vs_upper" in settlement_rel:
        return "MIDPOINT_VS_UPPER_BOUND"
    if "upper_bound" in settlement_rel and "effective_rate" in settlement_rel:
        return "UPPER_BOUND_VS_EFFECTIVE_RATE"
    if (
        comparison.get("same_family")
        and comparison.get("same_market_shape")
        and comparison.get("same_threshold_after_convention_translation") == "approx_equivalent"
        and comparison.get("same_meeting_date")
    ):
        return "SAME_EVENT_SAME_THRESHOLD_REVIEW"
    if comparison.get("same_family") and comparison.get("same_market_shape"):
        return "SAME_EVENT_DIFFERENT_SOURCE_REVIEW"
    if comparison.get("same_family"):
        return "BASIS_RISK_SAME_ASSET_DIFFERENT_SOURCE"
    return "SAME_TOPIC_WEAK_SIGNAL"


# ---------------------------------------------------------------------------
# Evidence / confidence
# ---------------------------------------------------------------------------


def _kalshi_cdna_evidence(row: dict[str, Any], cdna: dict[str, Any]) -> dict[str, Any]:
    return {
        "kalshi_market_id": row.get("market_id"),
        "kalshi_event_ticker": row.get("event_ticker"),
        "kalshi_asset": row.get("asset"),
        "kalshi_comparator": row.get("comparator"),
        "kalshi_threshold": row.get("threshold"),
        "kalshi_target_date": row.get("target_date"),
        "kalshi_target_time": row.get("target_time"),
        "kalshi_settlement_source": row.get("settlement_source"),
        "kalshi_market_shape": row.get("market_shape"),
        "cdna_title": cdna.get("title"),
        "cdna_source_url": cdna.get("source_url"),
        "cdna_market_shape": cdna.get("market_shape") or cdna.get("market_shape_conservative"),
    }


def _kalshi_polymarket_evidence(row: dict[str, Any], poly: dict[str, Any]) -> dict[str, Any]:
    return {
        "kalshi_market_id": row.get("market_id"),
        "kalshi_event_ticker": row.get("event_ticker"),
        "kalshi_asset": row.get("asset"),
        "kalshi_comparator": row.get("comparator"),
        "kalshi_threshold": row.get("threshold"),
        "kalshi_target_date": row.get("target_date"),
        "kalshi_market_shape": row.get("market_shape"),
        "polymarket_market_id": poly.get("market_id") or poly.get("condition_id"),
        "polymarket_market_family": poly.get("market_family"),
        "polymarket_market_shape": poly.get("market_shape"),
        "polymarket_title": poly.get("title") or poly.get("question"),
    }


def _confidence_from_evidence(row: dict[str, Any], candidate: dict[str, Any]) -> str:
    completeness = row.get("typed_completeness_score") or row.get("typed_key_completeness_score") or 0
    try:
        completeness = float(completeness)
    except (TypeError, ValueError):
        completeness = 0.0
    if completeness >= 0.85 and candidate:
        return "medium"
    if completeness >= 0.6 and candidate:
        return "low"
    return "low"


def _confidence_from_basis_risk(row: dict[str, Any]) -> str:
    score = row.get("basis_risk_priority_score") or 0
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = 0.0
    if score_value >= 25:
        return "medium"
    return "low"


def _confidence_from_cross_venue(row: dict[str, Any]) -> str:
    score = row.get("review_priority_score") or 0
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = 0.0
    if score_value >= 3:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Summary / clustering
# ---------------------------------------------------------------------------


def _summary(nodes_by_id: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    type_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    left_venue_counter: Counter[str] = Counter()
    right_venue_counter: Counter[str] = Counter()
    blocker_counter: Counter[str] = Counter()
    for edge in edges:
        type_counter[edge["relationship_type"]] += 1
        action_counter[edge["action"]] += 1
        family_counter[edge["relationship_family"]] += 1
        left_venue_counter[edge["left_venue"]] += 1
        right_venue_counter[edge["right_venue"]] += 1
        for blocker in edge.get("blockers", []):
            blocker_counter[blocker] += 1
    return {
        "total_nodes": len(nodes_by_id),
        "total_edges": len(edges),
        "edges_by_relationship_type": _to_count_rows(type_counter, "relationship_type"),
        "edges_by_action": _to_count_rows(action_counter, "action"),
        "edges_by_family": _to_count_rows(family_counter, "relationship_family"),
        "edges_by_left_venue": _to_count_rows(left_venue_counter, "left_venue"),
        "edges_by_right_venue": _to_count_rows(right_venue_counter, "right_venue"),
        "top_blockers": _to_count_rows(blocker_counter, "blocker")[:15],
    }


def _to_count_rows(counter: Counter[str], key_name: str) -> list[dict[str, Any]]:
    return [
        {key_name: name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _top_relationship_clusters(edges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    clusters: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for edge in edges:
        key = (edge["relationship_type"], edge["action"], edge["confidence_bucket"])
        clusters[key].append(edge["edge_id"])
    rows: list[dict[str, Any]] = []
    for (relationship_type, action, confidence), pair_ids in clusters.items():
        rows.append(
            {
                "relationship_type": relationship_type,
                "action": action,
                "confidence_bucket": confidence,
                "pair_count": len(pair_ids),
                "sample_edge_ids": list(pair_ids[:5]),
            }
        )
    rows.sort(key=lambda row: (-row["pair_count"], row["relationship_type"]))
    return rows[:limit]


def _core_trio_crypto_section(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    crypto_assets = {"BTC", "ETH", "SOL", "DOGE"}
    venues_seen: set[str] = set()
    relevant_edges: list[dict[str, Any]] = []
    for edge in edges:
        left_id = edge.get("left_market_id")
        right_id = edge.get("right_market_id")
        left_node = nodes_by_id.get(left_id) if left_id else None
        right_node = nodes_by_id.get(right_id) if right_id else None
        left_asset = (left_node or {}).get("asset_or_family")
        right_asset = (right_node or {}).get("asset_or_family")
        if (
            (isinstance(left_asset, str) and left_asset.upper() in crypto_assets)
            or (isinstance(right_asset, str) and right_asset.upper() in crypto_assets)
            or edge["left_venue"] == "cdna"
            or edge["right_venue"] == "cdna"
        ):
            relevant_edges.append(edge)
            venues_seen.add(edge["left_venue"])
            venues_seen.add(edge["right_venue"])
    counter: Counter[str] = Counter()
    for edge in relevant_edges:
        counter[edge["relationship_type"]] += 1
    return {
        "edge_count": len(relevant_edges),
        "venues": sorted(venues_seen),
        "edges_by_relationship_type": _to_count_rows(counter, "relationship_type"),
    }


def _queued_ibkr_section(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    parsed: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    contracts = sum(
        1
        for node in nodes_by_id.values()
        if isinstance(node, dict) and node.get("venue") == "ibkr_forecastex"
    )
    cross_venue = sum(
        1
        for edge in edges
        if edge["left_venue"] == "ibkr_forecastex" or edge["right_venue"] == "ibkr_forecastex"
    )
    quote_payload = parsed.get("ibkr_forecastex_quote_diagnostics") or {}
    summary = quote_payload.get("summary") if isinstance(quote_payload, dict) else {}
    quote_complete_rows = 0
    quote_blocked_rows = 0
    if isinstance(summary, dict):
        quote_complete_rows = int(summary.get("rows_quote_diagnostic_complete") or 0)
        total = int(summary.get("final_contract_rows") or 0)
        quote_blocked_rows = max(total - quote_complete_rows, 0)
    return {
        "contract_count": contracts,
        "cross_venue_edge_count": cross_venue,
        "quote_diagnostic_complete_rows": quote_complete_rows,
        "quote_diagnostic_blocked_rows": quote_blocked_rows,
        "manual_ui_memo_validation_present": "ibkr_forecastex_manual_ui_memo_validation" in parsed,
    }


def _handoff_candidates(edges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Pick the top edges useful for graph→RV handoff.

    Selection rules mirror the worklist exporter but capped at *limit* and
    rendered inline so the importer's report is self-contained.
    """

    scored: list[tuple[float, dict[str, Any]]] = []
    for edge in edges:
        if edge["action"] == ACTION_IGNORE_LOW_CONFIDENCE:
            continue
        if edge["relationship_type"] in {"NO_CURRENT_PEER", "TITLE_SIMILARITY_ONLY"}:
            continue
        score = _edge_handoff_score(edge)
        scored.append((score, edge))
    scored.sort(key=lambda item: (-item[0], item[1]["edge_id"]))
    output = []
    for score, edge in scored[:limit]:
        output.append(
            {
                "edge_id": edge["edge_id"],
                "relationship_type": edge["relationship_type"],
                "action": edge["action"],
                "confidence_bucket": edge["confidence_bucket"],
                "blockers": list(edge["blockers"]),
                "score": round(score, 3),
            }
        )
    return output


def _edge_handoff_score(edge: dict[str, Any]) -> float:
    score = 0.0
    family = edge["relationship_family"]
    if family == "near_exact_review":
        score += 5.0
    elif family == "basis_risk":
        score += 3.0
    elif family == "structural":
        score += 2.0
    if edge["confidence_bucket"] == "medium":
        score += 1.0
    if edge["confidence_bucket"] == "high":
        score += 2.0
    evidence = edge.get("evidence_fields") or {}
    for key in ("kalshi_target_date", "cdna_threshold", "polymarket_market_shape", "lane", "review_priority_score"):
        if evidence.get(key):
            score += 0.25
    # Soft penalty: too many blockers indicate more work needed.
    score -= 0.05 * max(0, len(edge.get("blockers", [])) - len(_required_count()))
    return score


def _required_count() -> tuple[str, ...]:
    from graph_engine.relationships.rv_edge_taxonomy import REQUIRED_RV_EDGE_BLOCKERS

    return REQUIRED_RV_EDGE_BLOCKERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scan_supported_reports(rv_reports_dir: Path) -> tuple[dict[str, Path], list[str]]:
    available: dict[str, Path] = {}
    missing: list[str] = []
    if not rv_reports_dir.exists():
        missing.extend(SUPPORTED_REPORTS.values())
        return available, missing
    for filename, slug in SUPPORTED_REPORTS.items():
        candidate = rv_reports_dir / filename
        if candidate.exists():
            available[slug] = candidate
        else:
            missing.append(slug)
    return available, missing


def _load_json(path: Path) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"io_error:{exc}"
    except ValueError as exc:
        return None, f"json_error:{exc}"
    return payload, None


def _blockers_from_row(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("blockers") or []
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            continue
        cleaned.append(_normalize_blocker(item))
    return tuple(cleaned)


def _normalize_blocker(value: str) -> str:
    # If an upstream RV blocker contains a graph-prohibited substring we
    # alias it on the way in.  This preserves the audit trail without
    # tripping the safety validator.
    if "paper_candidate" in value:
        return value.replace("paper_candidate", "evaluator_input")
    if "executable" in value:
        return value.replace("executable", "evaluator_ready_blocked")
    return value


def _edge_id(
    left_prefix: str,
    right_prefix: str,
    left_node: dict[str, Any] | None,
    right_node: dict[str, Any] | None,
    *,
    row_id: str | None = None,
) -> str:
    left_part = (left_node or {}).get("node_id") if left_node else "unknown"
    right_part = (right_node or {}).get("node_id") if right_node else (row_id or "_")
    return f"rv-edge:{left_prefix}:{right_prefix}:{left_part}->{right_part}"


def _safe_get(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _redact_string(value: str) -> str:
    """Redact tokens / phrases the graph safety vocabulary forbids in reports.

    External market titles and rationale text from the RV diagnostics may
    legitimately contain words like ``trade`` or ``order`` (e.g. a NFL
    market titled "Trade for a player"). The graph's prohibited-token
    sweep rejects any rendered Markdown / JSON that contains those words,
    so the ingester redacts them on the way in. The redaction preserves
    the surrounding context so a human reviewer can still understand the
    market label.
    """

    import re

    redacted = value
    for token in PROHIBITED_REPORT_TOKENS:
        redacted = re.sub(rf"(?i)\b{re.escape(token)}\b", "[redacted]", redacted)
    for phrase in PROHIBITED_REPORT_PHRASES:
        redacted = redacted.replace(phrase, "[redacted]")
        redacted = redacted.replace(phrase.replace("_", "-"), "[redacted]")
        redacted = redacted.replace(phrase.upper(), "[REDACTED]")
    return redacted


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_payload(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


__all__ = [
    "REPORT_BANNER",
    "SUPPORTED_REPORTS",
    "build_rv_diagnostic_relationship_edges_report",
    "render_rv_diagnostic_relationship_edges_markdown",
    "validate_rv_diagnostic_relationship_edges_report",
    "write_rv_diagnostic_relationship_edges_report",
]
