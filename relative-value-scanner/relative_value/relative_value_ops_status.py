from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "relative_value_ops_status_v1"

TIER_DISCOVERY_READY = "DISCOVERY_READY"
TIER_FAMILY_TYPED_REVIEW_READY = "FAMILY_TYPED_REVIEW_READY"
TIER_SETTLEMENT_SOURCE_REVIEW_READY = "SETTLEMENT_SOURCE_REVIEW_READY"
TIER_EXACT_PAYOFF_REVIEW_READY = "EXACT_PAYOFF_REVIEW_READY"
TIER_EXECUTION_EVALUATION_READY = "EXECUTION_EVALUATION_READY"

TIER_ORDER = (
    TIER_DISCOVERY_READY,
    TIER_FAMILY_TYPED_REVIEW_READY,
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_EXECUTION_EVALUATION_READY,
)

CORE_REPORTS = {
    "venue_metadata_coverage": "venue_metadata_coverage.json",
    "normalized_markets_v0": "normalized_markets_v0.json",
    "normalized_markets_v0_coverage": "normalized_markets_v0_coverage.json",
    "settlement_evidence_burden": "settlement_evidence_burden.json",
    "cross_platform_opportunity_triage": "cross_platform_opportunity_triage.json",
}

OPTIONAL_REPORTS = {
    "standardized_family_candidates": "standardized_family_candidates.json",
    "structural_basket_review": "structural_basket_review.json",
    "structural_basket_hunt": "structural_basket_hunt.json",
    "structural_basket_dry_run_summary": "structural_basket_dry_run_summary.json",
    "existing_paper_candidate_audit": "existing_paper_candidate_audit.json",
    "stale_report_archive_plan": "stale_report_archive_plan.json",
    "stale_report_archive_applied": "stale_report_archive_applied.json",
    "family_graduation_crypto": "family_graduation_crypto.json",
    "family_graduation_fed": "family_graduation_fed.json",
    "canonical_registry_coverage": "canonical_registry_coverage.json",
    "canonical_registry_expiry_audit": "canonical_registry_expiry_audit.json",
    "paper_readiness_probe": "paper_readiness_probe.json",
    "crypto_com_predict_cdna_research_snapshot": "crypto_com_predict_cdna_research_snapshot.json",
    "pending_registry_entries_plan": "pending_registry_entries_plan.json",
    "the_odds_api_fv_residuals": "the_odds_api_fv_residuals.json",
    "ibkr_forecastex_access_doctor": "ibkr_forecastex_access_doctor.json",
    "ibkr_forecastex_discovery_candidates": "ibkr_forecastex_discovery_candidates.json",
    "ibkr_forecastex_quote_diagnostics": "ibkr_forecastex_quote_diagnostics.json",
    "ibkr_forecastex_raw_shape_summary": "ibkr_forecastex_raw_shape_summary.json",
    "kalshi_kxmlb26_event_evidence_summary": "kalshi_kxmlb26_event_evidence_summary.json",
    "cross_venue_opportunity_scout": "cross_venue_opportunity_scout.json",
    "cdna_crypto_basis_risk_scout": "cdna_crypto_basis_risk_scout.json",
    "polymarket_taxonomy_shape_scout": "polymarket_taxonomy_shape_scout.json",
    "polymarket_clob_taxonomy_refresh": "polymarket_clob_taxonomy_refresh.json",
    "polymarket_point_in_time_typed_key_audit": "polymarket_point_in_time_typed_key_audit.json",
    "core_trio_peer_coverage_audit": "core_trio_peer_coverage_audit.json",
    "kalshi_crypto_typed_key_audit": "kalshi_crypto_typed_key_audit.json",
    "crypto_peer_acquisition_plan": "crypto_peer_acquisition_plan.json",
    "crypto_payoff_calendar_audit": "crypto_payoff_calendar_audit.json",
    "crypto_manual_discovery_workbench": "crypto_manual_discovery_workbench.json",
    "manual_evidence_requirements": "manual_evidence_requirements.json",
}

EVALUATOR_ACTION = "PAPER" + "_CANDIDATE"
FAMILY_GRADUATION_REPORTS = ("family_graduation_crypto", "family_graduation_fed")


def build_relative_value_ops_status_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    reports, blockers = _load_expected_reports(input_dir)

    burden = reports.get("settlement_evidence_burden")
    normalized = reports.get("normalized_markets_v0")
    normalized_coverage = reports.get("normalized_markets_v0_coverage")
    venue_metadata = reports.get("venue_metadata_coverage")
    triage = reports.get("cross_platform_opportunity_triage")
    standardized = reports.get("standardized_family_candidates")
    archive_plan = reports.get("stale_report_archive_plan")
    archive_applied = reports.get("stale_report_archive_applied")
    structural_reports = [
        reports.get("structural_basket_review"),
        reports.get("structural_basket_hunt"),
        reports.get("structural_basket_dry_run_summary"),
    ]

    burden_rows = _list_value(burden, "markets")
    normalized_rows = _list_value(normalized, "normalized_markets")
    normalized_by_key = _normalized_by_key(normalized_rows)

    tier_counts = _tier_counts(burden)
    families = _family_counts(burden_rows, burden)
    venues = _venue_names(burden, normalized_coverage, venue_metadata)
    unique_market_count = _unique_market_count(burden, venue_metadata, normalized_rows)
    highlight = _highlight_counts(burden_rows, normalized_by_key)
    cross_platform = _cross_platform_counts(triage, standardized)
    structural = _structural_counts(structural_reports)
    paper = _existing_evaluator_paper_counts(
        input_dir,
        audit=reports.get("existing_paper_candidate_audit"),
        archive_plan=archive_plan,
        archive_applied=archive_applied,
    )
    family_graduation_status = _family_graduation_status(reports)
    canonical_registry_coverage = _canonical_registry_coverage_status(reports.get("canonical_registry_coverage"))
    canonical_registry_expiry = _canonical_registry_expiry_status(reports.get("canonical_registry_expiry_audit"))
    paper_readiness_probe = _paper_readiness_probe_status(reports.get("paper_readiness_probe"))
    cdna_snapshot = _cdna_research_snapshot_status(reports.get("crypto_com_predict_cdna_research_snapshot"))
    pending_registry_entries = _pending_registry_entries_status(reports.get("pending_registry_entries_plan"))
    reference_odds_fv = _reference_odds_fv_status(reports.get("the_odds_api_fv_residuals"))
    ibkr_access_doctor = _ibkr_forecastex_access_doctor_status(reports.get("ibkr_forecastex_access_doctor"))
    ibkr_discovery_candidates = _ibkr_forecastex_discovery_candidates_status(
        reports.get("ibkr_forecastex_discovery_candidates")
    )
    ibkr_raw_shape_summary = _ibkr_forecastex_raw_shape_summary_status(
        reports.get("ibkr_forecastex_raw_shape_summary")
    )
    ibkr_quote_diagnostics = _ibkr_forecastex_quote_diagnostics_status(
        reports.get("ibkr_forecastex_quote_diagnostics")
    )
    kxmlb_event_evidence = _kalshi_kxmlb_event_evidence_status(
        reports.get("kalshi_kxmlb26_event_evidence_summary")
    )
    cross_venue_scout = _cross_venue_opportunity_scout_status(
        reports.get("cross_venue_opportunity_scout")
    )
    cdna_basis_scout = _cdna_crypto_basis_risk_scout_status(
        reports.get("cdna_crypto_basis_risk_scout")
    )
    cdna_parser_health = _cdna_parser_health_status(
        reports.get("crypto_com_predict_cdna_research_snapshot")
    )
    polymarket_shape_scout = _polymarket_taxonomy_shape_scout_status(
        reports.get("polymarket_taxonomy_shape_scout")
    )
    polymarket_clob_refresh = _polymarket_clob_taxonomy_refresh_status(
        reports.get("polymarket_clob_taxonomy_refresh")
    )
    polymarket_point_in_time_audit = _polymarket_point_in_time_typed_key_audit_status(
        reports.get("polymarket_point_in_time_typed_key_audit")
    )
    core_trio_peer_coverage = _core_trio_peer_coverage_audit_status(
        reports.get("core_trio_peer_coverage_audit")
    )
    kalshi_crypto_typed_key_audit = _kalshi_crypto_typed_key_audit_status(
        reports.get("kalshi_crypto_typed_key_audit")
    )
    crypto_peer_acquisition_plan = _crypto_peer_acquisition_plan_status(
        reports.get("crypto_peer_acquisition_plan")
    )
    crypto_payoff_calendar_audit = _crypto_payoff_calendar_audit_status(
        reports.get("crypto_payoff_calendar_audit")
    )
    crypto_manual_discovery_workbench = _crypto_manual_discovery_workbench_status(
        reports.get("crypto_manual_discovery_workbench")
    )
    manual_evidence_requirements = _manual_evidence_requirements_status(
        reports.get("manual_evidence_requirements")
    )

    top_blockers = _top_blockers(
        missing_report_blockers=blockers,
        reports=reports,
        burden_rows=burden_rows,
    )
    not_ready = _not_ready_for_paper_reasons(
        missing_report_blockers=blockers,
        tier_counts=tier_counts,
        paper=paper,
        top_blockers=top_blockers,
        canonical_registry_coverage=canonical_registry_coverage,
    )
    next_action = _highest_priority_next_action(
        missing_report_blockers=blockers,
        tier_counts=tier_counts,
        highlight=highlight,
        cross_platform=cross_platform,
        paper=paper,
        family_graduation=family_graduation_status,
        canonical_registry_coverage=canonical_registry_coverage,
        paper_readiness_probe=paper_readiness_probe,
        ibkr_access_doctor=ibkr_access_doctor,
        ibkr_discovery_candidates=ibkr_discovery_candidates,
        ibkr_raw_shape_summary=ibkr_raw_shape_summary,
        ibkr_quote_diagnostics=ibkr_quote_diagnostics,
    )
    ibkr_consistency_warnings = _ibkr_forecastex_consistency_warnings(
        discovery=ibkr_discovery_candidates,
        quote_diagnostics=ibkr_quote_diagnostics,
    )

    summary = {
        "unique_market_count": unique_market_count,
        "venue_count": len(venues),
        "venues": venues,
        "family_count": len(families),
        "families": families,
        "review_readiness_tier_counts": tier_counts,
        "discovery_ready_count": tier_counts[TIER_DISCOVERY_READY],
        "family_typed_review_ready_count": tier_counts[TIER_FAMILY_TYPED_REVIEW_READY],
        "settlement_source_review_ready_count": tier_counts[TIER_SETTLEMENT_SOURCE_REVIEW_READY],
        "exact_payoff_review_ready_count": tier_counts[TIER_EXACT_PAYOFF_REVIEW_READY],
        "execution_evaluation_ready_count": tier_counts[TIER_EXECUTION_EVALUATION_READY],
        "cross_platform_candidate_counts": cross_platform,
        "structural_status": structural,
        "fed_fomc_typed_ready_count": highlight["fed_fomc_typed_ready_count"],
        "crypto_typed_ready_count": highlight["crypto_typed_ready_count"],
        "sports_source_ready_count": highlight["sports_source_ready_count"],
        "existing_evaluator_paper_candidate_count": paper["count"],
        "stale_report_archive_plan_present": paper["archive_plan_present"],
        "stale_report_archive_plan_archive_candidate_count": paper["archive_plan_archive_candidate_count"],
        "stale_report_archive_applied_present": paper["archive_applied_present"],
        "stale_report_archive_applied_move_count": paper["archive_applied_move_count"],
        "missing_report_count": sum(1 for blocker in blockers if blocker["blocker"] == "saved_report_missing"),
        "highest_priority_next_action": next_action,
        "family_graduation_status": family_graduation_status,
        "canonical_registry_coverage": canonical_registry_coverage,
        "canonical_registry_expiry_audit": canonical_registry_expiry,
        "paper_readiness_probe": paper_readiness_probe,
        "crypto_com_predict_cdna_research_snapshot": cdna_snapshot,
        "pending_registry_entries": pending_registry_entries,
        "reference_odds_fv": reference_odds_fv,
        "ibkr_forecastex_access_doctor": ibkr_access_doctor,
        "ibkr_forecastex_discovery_candidates": ibkr_discovery_candidates,
        "ibkr_forecastex_quote_diagnostics": ibkr_quote_diagnostics,
        "ibkr_forecastex_consistency_warnings": ibkr_consistency_warnings,
        "ibkr_forecastex_raw_shape_summary": ibkr_raw_shape_summary,
        "ibkr_unified_ui_exchange_venue_warning": (
            "IBKR unified UI can show Kalshi/CME/ForecastEx; exchange_venue, not tab/source platform alone, determines independence."
        ),
        "kalshi_kxmlb26_event_evidence_summary": kxmlb_event_evidence,
        "cross_venue_opportunity_scout": cross_venue_scout,
        "cdna_crypto_basis_risk_scout": cdna_basis_scout,
        "cdna_parser_health": cdna_parser_health,
        "polymarket_taxonomy_shape_scout": polymarket_shape_scout,
        "polymarket_clob_taxonomy_refresh": polymarket_clob_refresh,
        "polymarket_point_in_time_typed_key_audit": polymarket_point_in_time_audit,
        "core_trio_peer_coverage": core_trio_peer_coverage,
        "kalshi_crypto_typed_key_audit": kalshi_crypto_typed_key_audit,
        "crypto_peer_acquisition_plan": crypto_peer_acquisition_plan,
        "crypto_payoff_calendar_audit": crypto_payoff_calendar_audit,
        "crypto_manual_discovery_workbench": crypto_manual_discovery_workbench,
        "manual_evidence_requirements": manual_evidence_requirements,
    }
    if paper.get("current_needs_review_count", 0) > 0:
        summary["existing_evaluator_positive_action"] = EVALUATOR_ACTION

    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "reports": _report_inventory(input_dir, reports, blockers),
        "summary": summary,
        "highlights": highlight,
        "top_blockers": top_blockers,
        "not_ready_for_paper_because": not_ready,
        "highest_priority_next_action": next_action,
        "paper_status": paper,
        "stale_report_archive_plan": _archive_plan_status(archive_plan),
        "safety": _safety_block(),
    }


def write_relative_value_ops_status_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_relative_value_ops_status_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_relative_value_ops_status_markdown(report), encoding="utf-8")
    return report


def render_relative_value_ops_status_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    tiers = summary.get("review_readiness_tier_counts") or {}
    cross_platform = summary.get("cross_platform_candidate_counts") or {}
    next_action = report.get("highest_priority_next_action") or {}
    cross_venue = summary.get("cross_venue_opportunity_scout") or {}
    top_poly_targets = list(cross_venue.get("top_enriched_polymarket_review_targets") or [])[:10]
    point_audit = summary.get("polymarket_point_in_time_typed_key_audit") or {}
    top_point_candidates = list(point_audit.get("top_targeted_clob_refresh_candidates") or [])[:10]
    core_trio = summary.get("core_trio_peer_coverage") or {}
    top_core_targets = list(core_trio.get("top_10_next_fetch_targets") or [])[:10]
    top_core_overlaps = list(core_trio.get("top_10_closest_existing_overlaps") or [])[:10]
    top_poly_lines = []
    for index, target in enumerate(top_poly_targets, start=1):
        top_poly_lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"{target.get('review_priority_score', 0):.1f}",
                    _md(target.get("lane")),
                    _md(target.get("allowed_next_action")),
                    _md(target.get("ticker_or_symbol") or target.get("market_id_or_conid")),
                    _md(
                        f"bid={target.get('bid')} ask={target.get('ask')} "
                        f"bid_size={target.get('bid_size')} ask_size={target.get('ask_size')}"
                    ),
                    _md(", ".join((target.get("top_blockers") or [])[:3]) or "none"),
                ]
            )
            + " |"
        )
    if not top_poly_lines:
        top_poly_lines.append("| _none_ |  |  |  |  |  |  |")
    top_point_lines = []
    for index, target in enumerate(top_point_candidates, start=1):
        top_point_lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"{target.get('typed_key_completeness_score', 0):.1f}",
                    _md(target.get("market_family")),
                    _md(target.get("market_slug") or target.get("question")),
                    _md(", ".join((target.get("blockers") or [])[:3]) or "none"),
                ]
            )
            + " |"
        )
    if not top_point_lines:
        top_point_lines.append("| _none_ |  |  |  |  |")
    top_core_target_lines = []
    for index, target in enumerate(top_core_targets, start=1):
        top_core_target_lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _md(target.get("family")),
                    f"{target.get('priority_score', 0):.1f}",
                    _md(target.get("reason")),
                    _md(", ".join((target.get("blockers") or [])[:3]) or "none"),
                ]
            )
            + " |"
        )
    if not top_core_target_lines:
        top_core_target_lines.append("| _none_ |  |  |  |  |")
    top_core_overlap_lines = []
    for index, overlap in enumerate(top_core_overlaps, start=1):
        top_core_overlap_lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _md(overlap.get("lane")),
                    _md(overlap.get("family")),
                    f"{overlap.get('overlap_score', 0):.1f}",
                    _md(overlap.get("typed_key")),
                    _md(", ".join((overlap.get("blockers") or [])[:3]) or "none"),
                ]
            )
            + " |"
        )
    if not top_core_overlap_lines:
        top_core_overlap_lines.append("| _none_ |  |  |  |  |")
    lines = [
        "# Relative-Value Ops Status",
        "",
        "Saved-file-only operator status. This report summarizes existing diagnostics and does not create paper actions.",
        "",
        "## IBKR Venue Warning",
        "",
        f"- `{summary.get('ibkr_unified_ui_exchange_venue_warning')}`",
        "",
        "## Summary",
        "",
        f"- unique_markets: `{summary.get('unique_market_count', 0)}`",
        f"- venues: `{summary.get('venue_count', 0)}` ({', '.join(summary.get('venues') or []) or 'none'})",
        f"- families: `{summary.get('family_count', 0)}`",
        f"- discovery_ready: `{tiers.get(TIER_DISCOVERY_READY, 0)}`",
        f"- family_typed_review_ready: `{tiers.get(TIER_FAMILY_TYPED_REVIEW_READY, 0)}`",
        f"- settlement_source_review_ready: `{tiers.get(TIER_SETTLEMENT_SOURCE_REVIEW_READY, 0)}`",
        f"- exact_payoff_review_ready: `{tiers.get(TIER_EXACT_PAYOFF_REVIEW_READY, 0)}`",
        f"- execution_evaluation_ready: `{tiers.get(TIER_EXECUTION_EVALUATION_READY, 0)}`",
        f"- cross_platform_rows: `{cross_platform.get('triage_row_count', 0)}`",
        f"- stale_archive_plan_present: `{str(bool(summary.get('stale_report_archive_plan_present'))).lower()}`",
        f"- stale_archive_candidates: `{summary.get('stale_report_archive_plan_archive_candidate_count', 0)}`",
        f"- stale_archive_applied_present: `{str(bool(summary.get('stale_report_archive_applied_present'))).lower()}`",
        f"- stale_archive_applied_moves: `{summary.get('stale_report_archive_applied_move_count', 0)}`",
        "",
        "### canonical_registry_coverage",
        "",
        f"- scopes_total: `{(summary.get('canonical_registry_coverage') or {}).get('scopes_total', 0)}`",
        f"- scopes_reviewed: `{(summary.get('canonical_registry_coverage') or {}).get('scopes_reviewed', 0)}`",
        f"- scopes_unreviewed: `{(summary.get('canonical_registry_coverage') or {}).get('scopes_unreviewed', 0)}`",
        f"- rows_covered_by_reviewed_scopes: `{(summary.get('canonical_registry_coverage') or {}).get('rows_covered_by_reviewed_scopes', 0)}`",
        f"- rows_uncovered: `{(summary.get('canonical_registry_coverage') or {}).get('rows_uncovered', 0)}`",
        f"- top_leverage_scope: `{(summary.get('canonical_registry_coverage') or {}).get('top_leverage_scope')}`",
        "",
        "### canonical_registry_expiry_audit",
        "",
        f"- registry_entries_total: `{(summary.get('canonical_registry_expiry_audit') or {}).get('registry_entries_total', 0)}`",
        f"- registry_entries_expired: `{(summary.get('canonical_registry_expiry_audit') or {}).get('registry_entries_expired', 0)}`",
        f"- registry_entries_expiring_soon: `{(summary.get('canonical_registry_expiry_audit') or {}).get('registry_entries_expiring_soon', 0)}`",
        f"- registry_entries_valid_current_review: `{(summary.get('canonical_registry_expiry_audit') or {}).get('registry_entries_valid_current_review', 0)}`",
        "",
        "### paper_readiness_probe",
        "",
        f"- present: `{str(bool((summary.get('paper_readiness_probe') or {}).get('present'))).lower()}`",
        f"- total_rows_considered: `{(summary.get('paper_readiness_probe') or {}).get('total_rows_considered', 0)}`",
        f"- rows_blocked_by_stale_quote: `{(summary.get('paper_readiness_probe') or {}).get('rows_blocked_by_stale_quote', 0)}`",
        f"- rows_blocked_by_missing_quote: `{(summary.get('paper_readiness_probe') or {}).get('rows_blocked_by_missing_quote', 0)}`",
        f"- rows_blocked_by_fee: `{(summary.get('paper_readiness_probe') or {}).get('rows_blocked_by_fee', 0)}`",
        f"- rows_blocked_by_pair_review: `{(summary.get('paper_readiness_probe') or {}).get('rows_blocked_by_pair_review', 0)}`",
        "",
        "### crypto_com_predict_cdna_research_snapshot",
        "",
        f"- present: `{str(bool((summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('present'))).lower()}`",
        f"- parsed_rows: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('parsed_rows', 0)}`",
        f"- btc_rows: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('btc_rows', 0)}`",
        f"- eth_rows: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('eth_rows', 0)}`",
        f"- point_in_time_rows: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('deadline_or_range_hit_rows', 0)}`",
        f"- basis_risk_compatible_with_kalshi: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('basis_risk_compatible_with_kalshi', 0)}`",
        f"- exact_payoff_compatible_with_kalshi: `{(summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('exact_payoff_compatible_with_kalshi', 0)}`",
        f"- top_blockers: `{', '.join((summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('top_blockers') or []) or 'none'}`",
        f"- research_only: `{str(bool((summary.get('crypto_com_predict_cdna_research_snapshot') or {}).get('research_only'))).lower()}`",
        "",
        "### pending_registry_entries",
        "",
        f"- present: `{str(bool((summary.get('pending_registry_entries') or {}).get('present'))).lower()}`",
        f"- pending_files_written: `{(summary.get('pending_registry_entries') or {}).get('pending_files_written', 0)}`",
        f"- skipped_reviewed_scopes: `{(summary.get('pending_registry_entries') or {}).get('skipped_reviewed_scopes', 0)}`",
        f"- reviewer_must_validate: `true`",
        f"- registry_proposal_is_trust: `false`",
        "",
        "### reference_odds_fv",
        "",
        f"- present: `{str(bool((summary.get('reference_odds_fv') or {}).get('present'))).lower()}`",
        f"- odds_events_read: `{(summary.get('reference_odds_fv') or {}).get('odds_events_read', 0)}`",
        f"- reference_markets_read: `{(summary.get('reference_odds_fv') or {}).get('reference_markets_read', 0)}`",
        f"- matched_rows: `{(summary.get('reference_odds_fv') or {}).get('matched_rows', 0)}`",
        f"- residual_rows: `{(summary.get('reference_odds_fv') or {}).get('residual_rows', 0)}`",
        f"- unmatched_reference_rows: `{(summary.get('reference_odds_fv') or {}).get('unmatched_reference_rows', 0)}`",
        f"- reference_only_source: `true`",
        "",
        "### ibkr_forecastex",
        "",
        "- warning: `IBKR unified UI can show Kalshi/CME/ForecastEx; exchange_venue, not tab/source platform alone, determines independence.`",
        f"- access_doctor_present: `{str(bool((summary.get('ibkr_forecastex_access_doctor') or {}).get('present'))).lower()}`",
        f"- gateway_reachable: `{str(bool((summary.get('ibkr_forecastex_access_doctor') or {}).get('reachable'))).lower()}`",
        f"- gateway_authenticated: `{str(bool((summary.get('ibkr_forecastex_access_doctor') or {}).get('authenticated'))).lower()}`",
        f"- discovery_present: `{str(bool((summary.get('ibkr_forecastex_discovery_candidates') or {}).get('present'))).lower()}`",
        f"- discovery_status: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('discovery_status')}`",
        f"- documented_seed_ff_attempted: `{str(bool((summary.get('ibkr_forecastex_discovery_candidates') or {}).get('documented_seed_ff_attempted'))).lower()}`",
        f"- ff_underlier_found: `{str(bool((summary.get('ibkr_forecastex_discovery_candidates') or {}).get('ff_underlier_found'))).lower()}`",
        f"- candidate_count: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('candidate_count', 0)}`",
        f"- forecastx_candidate_count: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_candidate_count', 0)}`",
        f"- forecastx_underlier_candidates: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_underlier_candidates', 0)}`",
        f"- forecastx_tradable_contract_candidates: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_tradable_contract_candidates', 0)}`",
        f"- forecastx_marketdata_rows: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_marketdata_rows', 0)}`",
        f"- final_tradable_rows: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('final_tradable_rows', 0)}`",
        f"- forecastx_option_months_attempted: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_option_months_attempted', 0)}`",
        f"- forecastx_strikes_found: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_strikes_found', 0)}`",
        f"- forecastx_info_requests: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_info_requests', 0)}`",
        f"- forecastx_yes_rows: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_yes_rows', 0)}`",
        f"- forecastx_no_rows: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('forecastx_no_rows', 0)}`",
        f"- quote_diagnostics_present: `{str(bool((summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('present'))).lower()}`",
        f"- quote_final_contract_rows: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('final_contract_rows', 0)}`",
        f"- quote_marketdata_rows: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('marketdata_rows', 0)}`",
        f"- quote_rows_mapped_to_contracts: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('quote_rows_mapped_to_contracts', 0)}`",
        f"- quote_rows_with_bid: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_with_bid', 0)}`",
        f"- quote_rows_with_ask: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_with_ask', 0)}`",
        f"- quote_rows_with_bid_ask: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_with_bid_ask', 0)}`",
        f"- quote_rows_with_bid_ask_size: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_with_bid_ask_size', 0)}`",
        f"- quote_rows_with_timestamp: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_with_timestamp', 0)}`",
        f"- quote_rows_diagnostic_complete: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_quote_diagnostic_complete', 0)}`",
        f"- quote_rows_execution_ready: `{(summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('rows_execution_ready', 0)}`",
        f"- quote_top_blockers: `{_format_top_blockers((summary.get('ibkr_forecastex_quote_diagnostics') or {}).get('top_quote_blockers') or [])}`",
        f"- consistency_warnings: `{', '.join(summary.get('ibkr_forecastex_consistency_warnings') or []) or 'none'}`",
        f"- normalized_possible_count: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('normalized_possible_count', 0)}`",
        f"- seed_conids_count: `{(summary.get('ibkr_forecastex_discovery_candidates') or {}).get('seed_conids_count', 0)}`",
        f"- raw_shape_summary_present: `{str(bool((summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('present'))).lower()}`",
        f"- raw_shape_files_read: `{(summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('raw_files_read', 0)}`",
        f"- raw_shape_forecastx_identifier_files: `{(summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('forecastx_identifier_files', 0)}`",
        f"- raw_shape_final_tradable_contract_field_files: `{(summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('final_tradable_contract_field_files', 0)}`",
        f"- raw_shape_call_put_right_files: `{(summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('call_put_right_files', 0)}`",
        f"- raw_shape_expiry_or_month_files: `{(summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('expiry_or_month_files', 0)}`",
        f"- raw_shape_final_tradable_contract_fields_present: `{str(bool((summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('final_tradable_contract_fields_present'))).lower()}`",
        f"- raw_shape_read_only_secdef_paths_exhausted: `{str(bool((summary.get('ibkr_forecastex_raw_shape_summary') or {}).get('read_only_secdef_paths_exhausted'))).lower()}`",
        "",
        "### kalshi_kxmlb26_event_evidence",
        "",
        f"- present: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('present'))).lower()}`",
        f"- ready_for_human_manifest_review: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('ready_for_human_manifest_review'))).lower()}`",
        f"- market_count: `{(summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('market_count', 0)}`",
        f"- explicit_outcome_list_exists: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('explicit_outcome_list_exists'))).lower()}`",
        f"- explicit_completeness_evidence_exists: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('explicit_completeness_evidence_exists'))).lower()}`",
        f"- settlement_rules_source_evidence_exists: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('settlement_rules_source_evidence_exists'))).lower()}`",
        f"- fresh_orderbook_depth_exists: `{str(bool((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('fresh_orderbook_depth_exists'))).lower()}`",
        f"- top_blockers: `{', '.join((summary.get('kalshi_kxmlb26_event_evidence_summary') or {}).get('top_blockers') or []) or 'none'}`",
        "",
        "### cross_venue_opportunity_scout",
        "",
        f"- present: `{str(bool((summary.get('cross_venue_opportunity_scout') or {}).get('present'))).lower()}`",
        f"- scout_row_count: `{(summary.get('cross_venue_opportunity_scout') or {}).get('scout_row_count', 0)}`",
        f"- exact_ready_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('paper_candidate_rows', 0)}`",
        f"- execution_ready_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('execution_ready_rows', 0)}`",
        f"- top_lane: `{(summary.get('cross_venue_opportunity_scout') or {}).get('top_lane')}`",
        f"- all_platform_top_lane: `{(summary.get('cross_venue_opportunity_scout') or {}).get('all_platform_top_lane')}`",
        f"- active_platforms: `{','.join((summary.get('cross_venue_opportunity_scout') or {}).get('active_platforms') or []) or 'all'}`",
        f"- active_ranked_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('active_ranked_rows', 0)}`",
        f"- inactive_platform_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('inactive_platform_rows', 0)}`",
        f"- core_trio_top_lane: `{(summary.get('cross_venue_opportunity_scout') or {}).get('core_trio_top_lane')}`",
        f"- polymarket_enriched_rows_loaded: `{(summary.get('cross_venue_opportunity_scout') or {}).get('polymarket_enriched_rows_loaded', 0)}`",
        f"- quoted_polymarket_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('polymarket_rows_with_bid_ask_size', 0)}`",
        f"- polymarket_rows_with_timestamp: `{(summary.get('cross_venue_opportunity_scout') or {}).get('polymarket_rows_with_timestamp', 0)}`",
        f"- polymarket_overlap_rows: `{(summary.get('cross_venue_opportunity_scout') or {}).get('polymarket_overlap_rows', 0)}`",
        f"- scout_report_path: `{(summary.get('cross_venue_opportunity_scout') or {}).get('scout_report_path')}`",
        f"- polymarket_enriched_report_path: `{(summary.get('cross_venue_opportunity_scout') or {}).get('polymarket_enriched_report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('cross_venue_opportunity_scout') or {}).get('top_blockers') or [])}`",
        "",
        "#### top_enriched_polymarket_review_targets",
        "",
        "| Rank | Score | Lane | Action | Target | Quote | Top Blockers |",
        "|---:|---:|---|---|---|---|---|",
        *top_poly_lines,
        "",
        "### cdna_parser_health",
        "",
        f"- present: `{str(bool((summary.get('cdna_parser_health') or {}).get('present'))).lower()}`",
        f"- rows: `{(summary.get('cdna_parser_health') or {}).get('rows', 0)}`",
        f"- btc_rows: `{(summary.get('cdna_parser_health') or {}).get('btc_rows', 0)}`",
        f"- eth_rows: `{(summary.get('cdna_parser_health') or {}).get('eth_rows', 0)}`",
        f"- point_in_time_rows: `{(summary.get('cdna_parser_health') or {}).get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{(summary.get('cdna_parser_health') or {}).get('deadline_or_range_hit_rows', 0)}`",
        f"- ambiguous_rows: `{(summary.get('cdna_parser_health') or {}).get('ambiguous_rows', 0)}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('cdna_parser_health') or {}).get('top_blockers') or [])}`",
        "",
        "### polymarket_taxonomy_shape_scout",
        "",
        f"- present: `{str(bool((summary.get('polymarket_taxonomy_shape_scout') or {}).get('present'))).lower()}`",
        f"- total_rows: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('total_rows', 0)}`",
        f"- point_in_time_candidates: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('point_in_time_candidates', 0)}`",
        f"- deadline_or_range_hit_blocked: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('deadline_or_range_hit_blocked', 0)}`",
        f"- clob_book_attached: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('clob_book_attached', 0)}`",
        f"- typed_key_complete: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('typed_key_complete', 0)}`",
        f"- exact_ready_rows: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('paper_candidate_rows', 0)}`",
        f"- scout_report_path: `{(summary.get('polymarket_taxonomy_shape_scout') or {}).get('scout_report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('polymarket_taxonomy_shape_scout') or {}).get('top_blockers') or [])}`",
        "",
        "### polymarket_clob_taxonomy_refresh",
        "",
        f"- present: `{str(bool((summary.get('polymarket_clob_taxonomy_refresh') or {}).get('present'))).lower()}`",
        f"- shape_filter: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('shape_filter')}`",
        f"- min_score: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('min_score')}`",
        f"- candidates_selected: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('candidates_selected', 0)}`",
        f"- books_requested: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('books_requested', 0)}`",
        f"- books_saved: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('books_saved', 0)}`",
        f"- rows_enriched: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_enriched', 0)}`",
        f"- rows_with_bid: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_with_bid', 0)}`",
        f"- rows_with_ask: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_with_ask', 0)}`",
        f"- rows_with_bid_ask: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_with_bid_ask', 0)}`",
        f"- rows_with_bid_ask_size: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_with_bid_ask_size', 0)}`",
        f"- rows_with_timestamp: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('rows_with_timestamp', 0)}`",
        f"- still_missing_clob: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('still_missing_clob', 0)}`",
        f"- still_stale_or_missing_quote: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('still_stale_or_missing_quote', 0)}`",
        f"- enriched_report_path: `{(summary.get('polymarket_clob_taxonomy_refresh') or {}).get('enriched_report_path')}`",
        f"- top_remaining_blockers: `{_format_top_blockers((summary.get('polymarket_clob_taxonomy_refresh') or {}).get('top_remaining_blockers') or [])}`",
        "",
        "### polymarket_point_in_time_typed_key_audit",
        "",
        f"- present: `{str(bool((summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('present'))).lower()}`",
        f"- point_in_time_rows_audited: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('point_in_time_rows_audited', 0)}`",
        f"- excluded_fake_point_in_time_rows: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('excluded_fake_point_in_time_rows', 0)}`",
        f"- typed_complete_rows: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('typed_complete_rows', 0)}`",
        f"- targeted_clob_refresh_candidate_rows: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('targeted_clob_refresh_candidate_rows', 0)}`",
        f"- rows_with_clob_attached: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('rows_with_clob_attached', 0)}`",
        f"- rows_with_bid_ask_size: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('rows_with_bid_ask_size', 0)}`",
        f"- exact_ready_rows: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('polymarket_point_in_time_typed_key_audit') or {}).get('top_blockers') or [])}`",
        "",
        "#### top_point_in_time_targeted_clob_refresh_candidates",
        "",
        "| Rank | Score | Family | Target | Top Blockers |",
        "|---:|---:|---|---|---|",
        *top_point_lines,
        "",
        "### core_trio_peer_coverage",
        "",
        f"- present: `{str(bool((summary.get('core_trio_peer_coverage') or {}).get('present'))).lower()}`",
        f"- total_core_trio_rows: `{(summary.get('core_trio_peer_coverage') or {}).get('total_core_trio_rows', 0)}`",
        f"- peer_coverage_families: `{(summary.get('core_trio_peer_coverage') or {}).get('peer_coverage_families', 0)}`",
        f"- strongest_overlap_family: `{(summary.get('core_trio_peer_coverage') or {}).get('strongest_overlap_family')}`",
        f"- next_recommended_lane: `{(summary.get('core_trio_peer_coverage') or {}).get('next_recommended_lane')}`",
        f"- families_with_kalshi_peer_rows: `{(summary.get('core_trio_peer_coverage') or {}).get('families_with_kalshi_peer_rows', 0)}`",
        f"- families_without_kalshi_peer_rows: `{(summary.get('core_trio_peer_coverage') or {}).get('families_without_kalshi_peer_rows', 0)}`",
        f"- exact_ready_rows: `{(summary.get('core_trio_peer_coverage') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('core_trio_peer_coverage') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('core_trio_peer_coverage') or {}).get('report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('core_trio_peer_coverage') or {}).get('top_blockers') or [])}`",
        "",
        "#### top_core_trio_next_fetch_targets",
        "",
        "| Rank | Family | Priority | Reason | Top Blockers |",
        "|---:|---|---:|---|---|",
        *top_core_target_lines,
        "",
        "#### top_core_trio_closest_existing_overlaps",
        "",
        "| Rank | Lane | Family | Score | Typed Key | Top Blockers |",
        "|---:|---|---|---:|---|---|",
        *top_core_overlap_lines,
        "",
        "### kalshi_crypto_typed_key_audit",
        "",
        f"- present: `{str(bool((summary.get('kalshi_crypto_typed_key_audit') or {}).get('present'))).lower()}`",
        f"- kalshi_crypto_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('kalshi_crypto_rows', 0)}`",
        f"- typed_complete_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('typed_complete_rows', 0)}`",
        f"- point_in_time_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('deadline_or_range_hit_rows', 0)}`",
        f"- ambiguous_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('ambiguous_rows', 0)}`",
        f"- rows_with_threshold: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_threshold', 0)}`",
        f"- rows_with_target_date: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_target_date', 0)}`",
        f"- rows_with_target_time: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_target_time', 0)}`",
        f"- rows_with_settlement_source: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_settlement_source', 0)}`",
        f"- rows_with_quote: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_quote', 0)}`",
        f"- fresh_crypto_snapshot_present: `{str(bool((summary.get('kalshi_crypto_typed_key_audit') or {}).get('fresh_crypto_snapshot_present'))).lower()}`",
        f"- fresh_crypto_snapshot_rows_loaded: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('fresh_crypto_snapshot_rows_loaded', 0)}`",
        f"- enriched_files_read: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('enriched_files_read', 0)}`",
        f"- rows_with_existing_top_of_book: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_existing_top_of_book', 0)}`",
        f"- rows_with_fresh_orderbook: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_fresh_orderbook', 0)}`",
        f"- rows_with_stale_top_of_book: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_stale_top_of_book', 0)}`",
        f"- rows_with_full_orderbook_missing: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_full_orderbook_missing', 0)}`",
        f"- rows_with_bid_ask_size_timestamp: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('rows_with_bid_ask_size_timestamp', 0)}`",
        f"- kalshi_live_orderbook_fetch_supported: `{str(bool((summary.get('kalshi_crypto_typed_key_audit') or {}).get('kalshi_live_orderbook_fetch_supported'))).lower()}`",
        f"- kalshi_live_orderbook_fetch_not_enabled_or_missing_count: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('kalshi_live_orderbook_fetch_not_enabled_or_missing_count', 0)}`",
        f"- possible_cdna_peer_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('possible_cdna_peer_rows', 0)}`",
        f"- possible_polymarket_peer_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('possible_polymarket_peer_rows', 0)}`",
        f"- date_threshold_comparator_overlap_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('date_threshold_comparator_overlap_rows', 0)}`",
        f"- exact_ready_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('paper_candidate_rows', 0)}`",
        f"- next_action: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('next_action')}`",
        f"- next_action_reason: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('next_action_reason')}`",
        f"- report_path: `{(summary.get('kalshi_crypto_typed_key_audit') or {}).get('report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('kalshi_crypto_typed_key_audit') or {}).get('top_blockers') or [])}`",
        "",
        "### crypto_peer_acquisition_plan",
        "",
        f"- present: `{str(bool((summary.get('crypto_peer_acquisition_plan') or {}).get('present'))).lower()}`",
        f"- kalshi_typed_complete_grid_rows: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_typed_complete_grid_rows', 0)}`",
        f"- unique_assets: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('unique_assets', 0)}`",
        f"- unique_dates: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('unique_dates', 0)}`",
        f"- unique_thresholds: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('unique_thresholds', 0)}`",
        f"- top_target_asset: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('top_target_asset')}`",
        f"- top_target_date: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('top_target_date')}`",
        f"- polymarket_queries_recommended: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('polymarket_queries_recommended', 0)}`",
        f"- polymarket_clob_refresh_recommended: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('polymarket_clob_refresh_recommended', 0)}`",
        f"- cdna_targets_recommended: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('cdna_targets_recommended', 0)}`",
        f"- kalshi_orderbook_targets_recommended: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_orderbook_targets_recommended', 0)}`",
        f"- kalshi_fresh_crypto_snapshot_recommended: `{str(bool((summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_fresh_crypto_snapshot_recommended'))).lower()}`",
        f"- kalshi_orderbook_targets_requiring_snapshot_age_override: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_orderbook_targets_requiring_snapshot_age_override', 0)}`",
        f"- safe_commands_referenced: `{','.join((summary.get('crypto_peer_acquisition_plan') or {}).get('safe_commands_referenced') or []) or 'none'}`",
        f"- safe_commands_missing: `{','.join((summary.get('crypto_peer_acquisition_plan') or {}).get('safe_commands_missing') or []) or 'none'}`",
        f"- command_validation_error_count: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('command_validation_error_count', 0)}`",
        f"- polymarket_targeted_query_command_missing: `{str(bool((summary.get('crypto_peer_acquisition_plan') or {}).get('polymarket_targeted_query_command_missing'))).lower()}`",
        f"- kalshi_orderbook_input_snapshot_missing_for_crypto_grid: `{str(bool((summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_orderbook_input_snapshot_missing_for_crypto_grid'))).lower()}`",
        f"- kalshi_orderbook_input_snapshot_paths: `{','.join((summary.get('crypto_peer_acquisition_plan') or {}).get('kalshi_orderbook_input_snapshot_paths') or []) or 'none'}`",
        f"- recommended_next_command: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('recommended_next_command')}`",
        f"- recommended_next_action: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('recommended_next_action')}`",
        f"- exact_ready_rows: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('crypto_peer_acquisition_plan') or {}).get('report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('crypto_peer_acquisition_plan') or {}).get('top_blockers') or [])}`",
        "",
        "### crypto_payoff_calendar_audit",
        "",
        f"- present: `{str(bool((summary.get('crypto_payoff_calendar_audit') or {}).get('present'))).lower()}`",
        f"- total_crypto_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('total_crypto_rows', 0)}`",
        f"- venues: `{','.join((summary.get('crypto_payoff_calendar_audit') or {}).get('venues') or []) or 'none'}`",
        f"- exact_shape_possible_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('exact_shape_possible_rows', 0)}`",
        f"- basis_risk_only_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('basis_risk_only_rows', 0)}`",
        f"- manual_rules_needed_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('manual_rules_needed_rows', 0)}`",
        f"- reference_only_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('reference_only_rows', 0)}`",
        f"- no_current_peer_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('no_current_peer_rows', 0)}`",
        f"- exact_ready_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('crypto_payoff_calendar_audit') or {}).get('report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('crypto_payoff_calendar_audit') or {}).get('top_blockers') or [])}`",
        "",
        "### crypto_manual_discovery_workbench",
        "",
        f"- present: `{str(bool((summary.get('crypto_manual_discovery_workbench') or {}).get('present'))).lower()}`",
        f"- group_count: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('group_count', 0)}`",
        f"- targets_emitted: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('targets_emitted', 0)}`",
        f"- total_eligible_audit_rows: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('total_eligible_audit_rows', 0)}`",
        f"- top_target_group: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_group')}`",
        f"- top_target_venue: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_venue')}`",
        f"- top_target_asset: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_asset')}`",
        f"- top_target_date: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_date')}`",
        f"- top_target_payoff_shape: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_payoff_shape')}`",
        f"- top_target_comparability_class: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('top_target_comparability_class')}`",
        f"- exact_ready_rows: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('crypto_manual_discovery_workbench') or {}).get('report_path')}`",
        "",
        "### manual_evidence_requirements",
        "",
        f"- present: `{str(bool((summary.get('manual_evidence_requirements') or {}).get('present'))).lower()}`",
        f"- total_items: `{(summary.get('manual_evidence_requirements') or {}).get('total_items', 0)}`",
        f"- verticals: `{','.join((summary.get('manual_evidence_requirements') or {}).get('verticals') or []) or 'none'}`",
        f"- P0: `{((summary.get('manual_evidence_requirements') or {}).get('priority_counts') or {}).get('P0', 0)}`",
        f"- P1: `{((summary.get('manual_evidence_requirements') or {}).get('priority_counts') or {}).get('P1', 0)}`",
        f"- P2: `{((summary.get('manual_evidence_requirements') or {}).get('priority_counts') or {}).get('P2', 0)}`",
        f"- P3: `{((summary.get('manual_evidence_requirements') or {}).get('priority_counts') or {}).get('P3', 0)}`",
        f"- P4: `{((summary.get('manual_evidence_requirements') or {}).get('priority_counts') or {}).get('P4', 0)}`",
        f"- platform_status_active: `{((summary.get('manual_evidence_requirements') or {}).get('platform_status_counts') or {}).get('active', 0)}`",
        f"- platform_status_queued: `{((summary.get('manual_evidence_requirements') or {}).get('platform_status_counts') or {}).get('queued', 0)}`",
        f"- platform_status_reference: `{((summary.get('manual_evidence_requirements') or {}).get('platform_status_counts') or {}).get('reference', 0)}`",
        f"- platform_status_official_source: `{((summary.get('manual_evidence_requirements') or {}).get('platform_status_counts') or {}).get('official_source', 0)}`",
        f"- closest_to_source_review_vertical: `{(summary.get('manual_evidence_requirements') or {}).get('closest_to_source_review_vertical')}`",
        f"- closest_to_exact_review_vertical: `{(summary.get('manual_evidence_requirements') or {}).get('closest_to_exact_review_vertical')}`",
        f"- distraction_vertical: `{(summary.get('manual_evidence_requirements') or {}).get('distraction_vertical')}`",
        f"- queued_platforms_remain_queued: `{str(bool((summary.get('manual_evidence_requirements') or {}).get('queued_platforms_remain_queued'))).lower()}`",
        f"- reference_only_platforms_never_become_pair_side: `{str(bool((summary.get('manual_evidence_requirements') or {}).get('reference_only_platforms_never_become_pair_side'))).lower()}`",
        f"- exact_ready_rows: `{(summary.get('manual_evidence_requirements') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('manual_evidence_requirements') or {}).get('paper_candidate_rows', 0)}`",
        f"- report_path: `{(summary.get('manual_evidence_requirements') or {}).get('report_path')}`",
        "",
        "### cdna_crypto_basis_risk_scout",
        "",
        f"- present: `{str(bool((summary.get('cdna_crypto_basis_risk_scout') or {}).get('present'))).lower()}`",
        f"- scout_row_count: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('scout_row_count', 0)}`",
        f"- cdna_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('cdna_rows', 0)}`",
        f"- cdna_btc_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('cdna_btc_rows', 0)}`",
        f"- cdna_eth_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('cdna_eth_rows', 0)}`",
        f"- point_in_time_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('point_in_time_rows', 0)}`",
        f"- deadline_or_range_hit_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('deadline_or_range_hit_rows', 0)}`",
        f"- ambiguous_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('ambiguous_rows', 0)}`",
        f"- exact_ready_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('exact_ready_rows', 0)}`",
        f"- paper_candidate_rows: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('paper_candidate_rows', 0)}`",
        f"- scout_report_path: `{(summary.get('cdna_crypto_basis_risk_scout') or {}).get('scout_report_path')}`",
        f"- top_blockers: `{_format_top_blockers((summary.get('cdna_crypto_basis_risk_scout') or {}).get('top_blockers') or [])}`",
        "",
        "## Highlights",
        "",
        f"- Fed/FOMC typed-ready: `{summary.get('fed_fomc_typed_ready_count', 0)}`",
        f"- Crypto typed-ready: `{summary.get('crypto_typed_ready_count', 0)}`",
        f"- Sports source-ready: `{summary.get('sports_source_ready_count', 0)}`",
        "",
        "### Families With Source Evidence But Missing Quote/Depth",
        "",
    ]
    source_missing = (report.get("highlights") or {}).get("families_with_source_url_but_missing_quote_depth") or []
    lines.extend(_markdown_count_table(source_missing, "family"))
    lines.extend(
        [
            "",
            "### Families With Quote/Depth But Missing Source Or Registry",
            "",
        ]
    )
    quote_missing_source = (report.get("highlights") or {}).get("families_with_quote_depth_but_missing_registry_or_source") or []
    lines.extend(_markdown_count_table(quote_missing_source, "family"))
    lines.extend(
        [
            "",
            "## Cross-Platform Candidates",
            "",
        f"- triage_row_count: `{cross_platform.get('triage_row_count', 0)}`",
        f"- standardized_cross_venue_group_count: `{cross_platform.get('standardized_cross_venue_group_count', 0)}`",
        f"- standardized_cross_venue_pair_count: `{cross_platform.get('standardized_cross_venue_pair_count', 0)}`",
        f"- standardized_btc_basis_risk_review_count: `{cross_platform.get('standardized_btc_basis_risk_review_count', 0)}`",
        f"- standardized_btc_basis_risk_discovery_count: `{cross_platform.get('standardized_btc_basis_risk_discovery_count', 0)}`",
        f"- standardized_crypto_related_fv_watch_rows: `{cross_platform.get('standardized_crypto_related_fv_watch_rows', 0)}`",
        f"- standardized_crypto_deadline_range_hit_fv_watch_rows: `{cross_platform.get('standardized_crypto_deadline_range_hit_fv_watch_rows', 0)}`",
        f"- standardized_basis_risk_known_reputable_source_pair_count: `{cross_platform.get('standardized_basis_risk_known_reputable_source_pair_count', 0)}`",
        "",
        "## Top Blockers",
            "",
        ]
    )
    blockers = report.get("top_blockers") or []
    if blockers:
        lines.extend(["| Blocker | Count | Sources |", "|---|---:|---|"])
        for blocker in blockers[:10]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(blocker.get("blocker")),
                        _md(blocker.get("count")),
                        _md(", ".join(blocker.get("sources") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")
    lines.extend(
        [
            "",
            "## Not Ready For Paper Because",
            "",
        ]
    )
    reasons = report.get("not_ready_for_paper_because") or []
    if reasons:
        for reason in reasons:
            lines.append(f"- `{reason.get('reason_code')}`: {reason.get('message')}")
    else:
        lines.append("- No blocking reason was detected in the saved status reports.")
    archive_plan = report.get("stale_report_archive_plan") or {}
    lines.extend(
        [
            "",
            "## Stale Archive Plan",
            "",
            f"- present: `{str(bool(archive_plan.get('present'))).lower()}`",
            f"- archive_candidate_count: `{archive_plan.get('archive_candidate_count', 0)}`",
            f"- path: `{archive_plan.get('path')}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Recommended Next Action",
            "",
            f"- action: `{next_action.get('action')}`",
            f"- reason: {next_action.get('reason')}",
            f"- report_only: `{str(bool(next_action.get('report_only'))).lower()}`",
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- execution_or_order_logic_added: `false`",
            "- account_or_auth_logic_added: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    paper_status = report.get("paper_status") or {}
    if paper_status.get("current_needs_review_count", 0) > 0:
        lines.append(f"- existing_evaluator_positive_action: `{EVALUATOR_ACTION}`")
    elif paper_status.get("count", 0) > 0 and paper_status.get("audit_present"):
        lines.append(
            f"- existing_evaluator_paper_action_rows: `{paper_status.get('count', 0)}` "
            "(audit classified as stale or likely fake/blocked; no current positive action)"
        )
    return "\n".join(lines) + "\n"


def _format_top_blockers(items: list[Any]) -> str:
    formatted: list[str] = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        blocker = str(item.get("blocker") or "").strip()
        if not blocker:
            continue
        count = _int(item.get("count"))
        formatted.append(f"{blocker}:{count}")
    return ", ".join(formatted) or "none"


def _load_expected_reports(input_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reports: dict[str, Any] = {}
    blockers: list[dict[str, Any]] = []
    for name, filename in {**CORE_REPORTS, **OPTIONAL_REPORTS}.items():
        path = input_dir / filename
        payload, warning = _load_json(path)
        if warning is not None:
            severity = "core" if name in CORE_REPORTS else "optional"
            blockers.append(
                {
                    "report": name,
                    "path": str(path),
                    "severity": severity,
                    "blocker": warning["blocker"],
                    "reason_code": warning["reason_code"],
                }
            )
            reports[name] = None
        else:
            reports[name] = payload
    return reports, blockers


def _report_inventory(input_dir: Path, reports: dict[str, Any], blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocker_by_report = {blocker["report"]: blocker for blocker in blockers}
    inventory = []
    for name, filename in {**CORE_REPORTS, **OPTIONAL_REPORTS}.items():
        payload = reports.get(name)
        blocker = blocker_by_report.get(name)
        inventory.append(
            {
                "report": name,
                "path": str(input_dir / filename),
                "present": payload is not None,
                "source": payload.get("source") if isinstance(payload, dict) else None,
                "blocker": blocker.get("blocker") if blocker else None,
                "severity": "core" if name in CORE_REPORTS else "optional",
            }
        )
    return inventory


def _archive_plan_status(archive_plan: Any) -> dict[str, Any]:
    if not isinstance(archive_plan, dict):
        return {
            "present": False,
            "archive_candidate_count": 0,
            "suggested_command_count": 0,
            "path": None,
        }
    summary = archive_plan.get("summary") if isinstance(archive_plan.get("summary"), dict) else {}
    return {
        "present": True,
        "archive_candidate_count": _int(summary.get("archive_candidate_count")),
        "suggested_command_count": _int(summary.get("suggested_command_count")),
        "path": archive_plan.get("archive_dir"),
        "generated_at": archive_plan.get("generated_at"),
        "source": archive_plan.get("source"),
    }


def _archive_applied_status(archive_applied: Any) -> dict[str, Any]:
    if not isinstance(archive_applied, dict) or archive_applied.get("source") != "stale_report_archive_applied_v1":
        return {
            "present": False,
            "applied_move_count": 0,
            "noop_move_count": 0,
            "refused_move_count": 0,
            "covers_stale_archive_plan": False,
        }
    summary = archive_applied.get("summary") if isinstance(archive_applied.get("summary"), dict) else {}
    return {
        "present": True,
        "applied_move_count": _int(summary.get("applied_move_count")),
        "noop_move_count": _int(summary.get("noop_move_count")),
        "refused_move_count": _int(summary.get("refused_move_count")),
        "covers_stale_archive_plan": bool(summary.get("covers_stale_archive_plan")) and _int(summary.get("refused_move_count")) == 0,
        "generated_at": archive_applied.get("generated_at"),
        "status": archive_applied.get("status"),
    }


def _tier_counts(burden: Any) -> dict[str, int]:
    counts = {tier: 0 for tier in TIER_ORDER}
    summary_counts = ((burden or {}).get("summary") or {}).get("by_review_readiness_tier") if isinstance(burden, dict) else None
    if isinstance(summary_counts, dict):
        for tier in TIER_ORDER:
            counts[tier] = _int(summary_counts.get(tier))
        return counts
    for row in _list_value(burden, "markets"):
        tier = row.get("review_readiness_tier")
        if tier in counts:
            counts[tier] += 1
    return counts


def _family_counts(rows: list[dict[str, Any]], burden: Any) -> dict[str, int]:
    family_summary = ((burden or {}).get("summary") or {}).get("by_family") if isinstance(burden, dict) else None
    if isinstance(family_summary, dict):
        return {
            str(family): _int((data or {}).get("market_count"))
            for family, data in sorted(family_summary.items())
            if isinstance(data, dict)
        }
    counter = Counter(str(row.get("family") or "UNKNOWN") for row in rows)
    return dict(sorted(counter.items()))


def _venue_names(burden: Any, normalized_coverage: Any, venue_metadata: Any) -> list[str]:
    names: set[str] = set()
    for row in _list_value(burden, "venues"):
        if row.get("venue"):
            names.add(str(row["venue"]))
    for row in _list_value(normalized_coverage, "venues"):
        if row.get("venue"):
            names.add(str(row["venue"]))
    for row in _list_value(venue_metadata, "venues"):
        if row.get("venue"):
            names.add(str(row["venue"]))
    return sorted(names)


def _unique_market_count(burden: Any, venue_metadata: Any, normalized_rows: list[dict[str, Any]]) -> int:
    for payload in (burden, venue_metadata):
        value = (((payload or {}).get("summary") or {}).get("unique_market_count")) if isinstance(payload, dict) else None
        if value is not None:
            return _int(value)
    unique_keys = {
        (
            str(row.get("venue") or "unknown"),
            str(row.get("market_id") or row.get("ticker") or row.get("token_id") or row.get("event_id") or ""),
        )
        for row in normalized_rows
        if row.get("market_id") or row.get("ticker") or row.get("token_id") or row.get("event_id")
    }
    return len(unique_keys)


def _highlight_counts(rows: list[dict[str, Any]], normalized_by_key: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    fed_typed = 0
    crypto_typed = 0
    sports_source = 0
    source_missing_quote: Counter[str] = Counter()
    quote_missing_source: Counter[str] = Counter()
    for row in rows:
        family = str(row.get("family") or "UNKNOWN")
        tier = str(row.get("review_readiness_tier") or "")
        source_present = bool(row.get("settlement_source_url_present") or row.get("registry_match"))
        quote_ready = _quote_depth_ready_for_burden_row(row, normalized_by_key)
        if family == "FED_FOMC" and _tier_at_least_typed(tier):
            fed_typed += 1
        if family == "CRYPTO_PRICE_THRESHOLD" and _tier_at_least_typed(tier):
            crypto_typed += 1
        if family in {"SPORTS_GAME_RESULT", "SPORTS_FUTURES_CHAMPIONSHIP"} and _tier_at_least_source(tier):
            sports_source += 1
        if source_present and quote_ready is False:
            source_missing_quote[family] += 1
        if quote_ready is True and not source_present:
            quote_missing_source[family] += 1
    return {
        "fed_fomc_typed_ready_count": fed_typed,
        "crypto_typed_ready_count": crypto_typed,
        "sports_source_ready_count": sports_source,
        "families_with_source_url_but_missing_quote_depth": _counter_rows(source_missing_quote),
        "families_with_quote_depth_but_missing_registry_or_source": _counter_rows(quote_missing_source),
    }


def _quote_depth_ready_for_burden_row(row: dict[str, Any], normalized_by_key: dict[tuple[str, str], dict[str, Any]]) -> bool | None:
    venue = str(row.get("venue") or "")
    candidates = [
        row.get("ticker"),
        row.get("event_id"),
        row.get("event_ticker"),
        row.get("event_slug"),
    ]
    for value in candidates:
        if not value:
            continue
        normalized = normalized_by_key.get((venue, str(value)))
        if normalized is None:
            continue
        readiness = normalized.get("readiness") if isinstance(normalized.get("readiness"), dict) else {}
        if "quote_depth_ready" in readiness:
            return bool(readiness.get("quote_depth_ready"))
    blockers = set(row.get("blockers") or [])
    if "missing_quote_depth_for_execution" in blockers:
        return False
    return None


def _normalized_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "")
        for field in ("market_id", "ticker", "token_id", "event_id", "event_ticker", "event_slug"):
            value = row.get(field)
            if value:
                output[(venue, str(value))] = row
    return output


def _cross_platform_counts(triage: Any, standardized: Any) -> dict[str, Any]:
    triage_summary = (triage or {}).get("summary") if isinstance(triage, dict) else {}
    standardized_summary = (standardized or {}).get("summary") if isinstance(standardized, dict) else {}
    return {
        "triage_row_count": _int((triage_summary or {}).get("row_count")),
        "triage_relationship_class_counts": dict((triage_summary or {}).get("relationship_class_counts") or {}),
        "triage_paper_candidate_count": _int((triage_summary or {}).get("paper_candidate_count")),
        "standardized_candidate_group_count": _int((standardized_summary or {}).get("candidate_group_count")),
        "standardized_candidate_pair_count": _int((standardized_summary or {}).get("candidate_pair_count")),
        "standardized_cross_venue_group_count": _int((standardized_summary or {}).get("cross_venue_candidate_group_count")),
        "standardized_cross_venue_pair_count": _int((standardized_summary or {}).get("cross_venue_candidate_pair_count")),
        "standardized_btc_basis_risk_review_count": _int((standardized_summary or {}).get("btc_basis_risk_review_count")),
        "standardized_btc_basis_risk_discovery_count": _int((standardized_summary or {}).get("btc_basis_risk_discovery_count")),
        "standardized_crypto_related_fv_watch_rows": _int(
            (standardized_summary or {}).get("crypto_related_fv_watch_rows")
        ),
        "standardized_crypto_related_fv_watch_by_asset": dict(
            (standardized_summary or {}).get("crypto_related_fv_watch_by_asset") or {}
        ),
        "standardized_crypto_deadline_range_hit_fv_watch_rows": _int(
            (standardized_summary or {}).get("crypto_deadline_range_hit_fv_watch_rows")
        ),
        "standardized_crypto_deadline_range_hit_fv_watch_by_asset": dict(
            (standardized_summary or {}).get("crypto_deadline_range_hit_fv_watch_by_asset") or {}
        ),
        "standardized_basis_risk_relationship_class_counts": dict(
            (standardized_summary or {}).get("basis_risk_relationship_class_counts") or {}
        ),
        "standardized_basis_risk_severity_hint_counts": dict(
            (standardized_summary or {}).get("basis_risk_severity_hint_counts") or {}
        ),
        "standardized_basis_risk_known_reputable_source_pair_count": _int(
            (standardized_summary or {}).get("basis_risk_known_reputable_source_pair_count")
        ),
        "standardized_candidate_counts_by_family": dict((standardized_summary or {}).get("candidate_counts_by_family") or {}),
    }


def _structural_counts(reports: list[Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "reports_present": 0,
        "structural_groups_evaluated": 0,
        "stop_for_review_count": 0,
        "paper_candidate_count": 0,
        "top_blockers": [],
    }
    blockers: Counter[str] = Counter()
    for report in reports:
        if not isinstance(report, dict):
            continue
        summary["reports_present"] += 1
        report_summary = report.get("summary") or {}
        summary["structural_groups_evaluated"] += _int(
            report_summary.get("structural_groups_evaluated") or report_summary.get("evaluated_group_count")
        )
        summary["stop_for_review_count"] += _int(report_summary.get("stop_for_review_count"))
        summary["paper_candidate_count"] += _int(report_summary.get("paper_candidate_count"))
        for item in report.get("top_blockers") or report_summary.get("top_blockers") or []:
            blocker = item.get("blocker") or item.get("category")
            if blocker:
                blockers[str(blocker)] += _int(item.get("count"), default=1)
    summary["top_blockers"] = [{"blocker": key, "count": value} for key, value in blockers.most_common(10)]
    return summary


def _top_blockers(
    *,
    missing_report_blockers: list[dict[str, Any]],
    reports: dict[str, Any],
    burden_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    sources: dict[str, set[str]] = defaultdict(set)
    for blocker in missing_report_blockers:
        label = blocker["blocker"]
        counts[label] += 1
        sources[label].add(f"missing:{blocker['report']}")
    for row in burden_rows:
        for blocker in row.get("blockers") or []:
            counts[str(blocker)] += 1
            sources[str(blocker)].add("settlement_evidence_burden")
    for name, payload in reports.items():
        if not isinstance(payload, dict):
            continue
        for blocker, count in _summary_blockers(payload):
            counts[blocker] += count
            sources[blocker].add(name)
    return [
        {"blocker": blocker, "count": count, "sources": sorted(sources[blocker])}
        for blocker, count in counts.most_common(12)
    ]


def _summary_blockers(payload: dict[str, Any]) -> list[tuple[str, int]]:
    summary = payload.get("summary") or {}
    items = []
    for item in summary.get("top_blockers") or []:
        if isinstance(item, dict) and item.get("blocker"):
            items.append((str(item["blocker"]), _int(item.get("count"), default=1)))
    for item in payload.get("top_blockers") or []:
        if isinstance(item, dict):
            blocker = item.get("blocker") or item.get("category")
            if blocker:
                items.append((str(blocker), _int(item.get("count"), default=1)))
    return items


def _not_ready_for_paper_reasons(
    *,
    missing_report_blockers: list[dict[str, Any]],
    tier_counts: dict[str, int],
    paper: dict[str, Any],
    top_blockers: list[dict[str, Any]],
    canonical_registry_coverage: dict[str, Any],
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    core_missing = [b["report"] for b in missing_report_blockers if b.get("severity") == "core"]
    if core_missing:
        reasons.append(
            {
                "reason_code": "missing_core_status_reports",
                "message": "Missing core saved reports: " + ", ".join(sorted(core_missing)) + ".",
            }
        )
    if tier_counts[TIER_EXECUTION_EVALUATION_READY] <= 0:
        reasons.append(
            {
                "reason_code": "no_execution_evaluation_ready_markets",
                "message": "No market has reached execution-evaluation readiness in the saved burden report.",
            }
        )
    if paper.get("current_needs_review_count", 0) <= 0:
        if paper.get("count", 0) > 0 and paper.get("audit_present"):
            reasons.append(
                {
                    "reason_code": "existing_evaluator_paper_rows_classified_stale_or_blocked",
                    "message": (
                        "Saved evaluator files contain "
                        f"{paper.get('count', 0)} PAPER-action rows, but the existing_paper_candidate_audit "
                        f"classified {paper.get('audit_stale_count', 0)} as stale and "
                        f"{paper.get('audit_likely_fake_or_blocked_count', 0)} as likely fake/blocked. "
                        "Archive the stale files or regenerate from current snapshots."
                    ),
                }
            )
            if paper.get("archive_plan_present"):
                reasons.append(
                    {
                        "reason_code": "stale_report_archive_plan_available",
                        "message": (
                            "A stale report archive plan is present with "
                            f"{paper.get('archive_plan_archive_candidate_count', 0)} suggested archive candidates. "
                            "Review the plan before treating old evaluator artifacts as current evidence."
                        ),
                    }
                )
        else:
            reasons.append(
                {
                    "reason_code": "no_existing_evaluator_positive_paper_report",
                    "message": "No existing evaluator report in the input directory reports a positive paper action.",
                }
            )
    if (
        canonical_registry_coverage.get("present")
        and _int(canonical_registry_coverage.get("scopes_total")) > 0
        and _int(canonical_registry_coverage.get("scopes_reviewed")) == 0
    ):
        reasons.append(
            {
                "reason_code": "no_canonical_scopes_reviewed_yet",
                "message": (
                    "Canonical registry coverage exists, but none of the proposed canonical scopes "
                    "have a matching reviewed registry entry."
                ),
            }
        )
    if top_blockers:
        top = top_blockers[0]
        reasons.append(
            {
                "reason_code": "top_blocker_present",
                "message": f"Top blocker is {top.get('blocker')} across {top.get('count')} saved-report observations.",
            }
        )
    return reasons


def _family_graduation_status(reports: dict[str, Any]) -> dict[str, Any]:
    """Summarize saved family_graduation_*.json reports without trusting them.

    Registry proposals here are NOT evidence; this status only tells the
    operator whether a family-graduation review is the highest-leverage
    next manual action.
    """
    families: list[dict[str, Any]] = []
    total_rows = 0
    total_typed_ready = 0
    total_ready_for_review = 0
    total_groups = 0
    for name in FAMILY_GRADUATION_REPORTS:
        payload = reports.get(name)
        if not isinstance(payload, dict):
            continue
        if payload.get("source") != "family_graduation_plan_v1":
            continue
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        family = payload.get("family")
        candidate_rows = _int(summary.get("candidate_row_count"))
        typed_ready = _int(summary.get("family_typed_ready_count"))
        ready_for_review = _int(summary.get("ready_for_human_registry_review_count"))
        proposal_count = _int(summary.get("registry_proposal_count"))
        group_count = _int(summary.get("registry_proposal_group_count"))
        existing_match = _int(summary.get("existing_reviewed_registry_match_count"))
        projected_exact = _int(summary.get("projected_exact_review_if_registry_reviewed_count"))
        families.append(
            {
                "report": name,
                "family": family,
                "candidate_row_count": candidate_rows,
                "family_typed_ready_count": typed_ready,
                "ready_for_human_registry_review_count": ready_for_review,
                "registry_proposal_count": proposal_count,
                "registry_proposal_group_count": group_count,
                "existing_reviewed_registry_match_count": existing_match,
                "projected_exact_review_if_registry_reviewed_count": projected_exact,
            }
        )
        total_rows += candidate_rows
        total_typed_ready += typed_ready
        total_ready_for_review += ready_for_review
        total_groups += group_count
    families.sort(key=lambda item: (-item.get("ready_for_human_registry_review_count", 0), item.get("family") or ""))
    return {
        "family_graduation_reports_present": len(families),
        "total_candidate_row_count": total_rows,
        "total_family_typed_ready_count": total_typed_ready,
        "total_ready_for_human_registry_review_count": total_ready_for_review,
        "total_registry_proposal_group_count": total_groups,
        "best_family_for_human_review": families[0]["family"] if families else None,
        "families": families,
        "registry_proposal_is_trust": False,
    }


def _canonical_registry_coverage_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "canonical_registry_coverage_v1":
        return {
            "present": False,
            "scopes_total": 0,
            "scopes_reviewed": 0,
            "scopes_unreviewed": 0,
            "top_leverage_scope": None,
            "rows_covered_by_reviewed_scopes": 0,
            "rows_uncovered": 0,
            "registry_proposal_is_trust": False,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "scopes_total": _int(summary.get("scopes_total")),
        "scopes_reviewed": _int(summary.get("scopes_reviewed")),
        "scopes_unreviewed": _int(summary.get("scopes_unreviewed")),
        "top_leverage_scope": summary.get("top_leverage_scope"),
        "rows_covered_by_reviewed_scopes": _int(summary.get("rows_covered_by_reviewed_scopes")),
        "rows_uncovered": _int(summary.get("rows_uncovered")),
        "registry_proposal_is_trust": False,
    }


def _canonical_registry_expiry_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "canonical_registry_expiry_audit_v1":
        return {
            "present": False,
            "registry_entries_total": 0,
            "registry_entries_valid_current_review": 0,
            "registry_entries_expired": 0,
            "registry_entries_expiring_soon": 0,
            "registry_entries_missing_review_until": 0,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "registry_entries_total": _int(summary.get("registry_entries_total")),
        "registry_entries_valid_current_review": _int(summary.get("registry_entries_valid_current_review")),
        "registry_entries_expired": _int(summary.get("registry_entries_expired")),
        "registry_entries_expiring_soon": _int(summary.get("registry_entries_expiring_soon")),
        "registry_entries_missing_review_until": _int(summary.get("registry_entries_missing_review_until")),
    }


def _cdna_research_snapshot_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "crypto_com_predict_cdna_research_snapshot_v1":
        return {
            "present": False,
            "parsed_rows": 0,
            "btc_rows": 0,
            "eth_rows": 0,
            "point_in_time_rows": 0,
            "deadline_or_range_hit_rows": 0,
            "basis_risk_compatible_with_kalshi": 0,
            "exact_payoff_compatible_with_kalshi": 0,
            "top_blockers": [],
            "research_only": True,
            "can_create_candidate_pair": False,
            "can_create_paper_candidate": False,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "parsed_rows": _int(summary.get("parsed_rows")),
        "btc_rows": _int(summary.get("btc_rows")),
        "eth_rows": _int(summary.get("eth_rows")),
        "point_in_time_rows": _int(summary.get("point_in_time_rows")),
        "deadline_or_range_hit_rows": _int(summary.get("deadline_or_range_hit_rows")),
        "basis_risk_compatible_with_kalshi": _int(summary.get("basis_risk_compatible_with_kalshi")),
        "exact_payoff_compatible_with_kalshi": _int(summary.get("exact_payoff_compatible_with_kalshi")),
        "top_blockers": [
            str(item.get("blocker"))
            for item in (summary.get("top_blockers") or [])[:5]
            if isinstance(item, dict) and item.get("blocker")
        ],
        "research_only": True,
        "can_create_candidate_pair": False,
        "can_create_paper_candidate": False,
        "generated_at": payload.get("generated_at"),
    }


def _pending_registry_entries_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "pending_registry_entries_plan_v1":
        return {
            "present": False,
            "pending_files_planned": 0,
            "pending_files_written": 0,
            "skipped_reviewed_scopes": 0,
            "top_scopes": [],
            "reviewer_must_validate": True,
            "registry_proposal_is_trust": False,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "pending_files_planned": _int(summary.get("pending_files_planned")),
        "pending_files_written": _int(summary.get("pending_files_written")),
        "skipped_reviewed_scopes": _int(summary.get("skipped_reviewed_scopes")),
        "top_scopes": list(summary.get("top_scopes") or []),
        "reviewer_must_validate": True,
        "registry_proposal_is_trust": False,
        "generated_at": payload.get("generated_at"),
    }


def _reference_odds_fv_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "reference_odds_fv_residuals_v1":
        return {
            "present": False,
            "odds_events_read": 0,
            "reference_markets_read": 0,
            "matched_rows": 0,
            "unmatched_reference_rows": 0,
            "residual_rows": 0,
            "reference_only_source": True,
            "executable_leg": False,
            "affects_evaluator_gates": False,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "odds_events_read": _int(summary.get("odds_events_read")),
        "reference_markets_read": _int(summary.get("reference_markets_read")),
        "matched_rows": _int(summary.get("matched_rows")),
        "unmatched_reference_rows": _int(summary.get("unmatched_reference_rows")),
        "residual_rows": _int(summary.get("residual_rows")),
        "reference_only_source": True,
        "executable_leg": False,
        "affects_evaluator_gates": False,
        "generated_at": payload.get("generated_at"),
    }


def _ibkr_forecastex_access_doctor_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_kind") != "ibkr_forecastex_access_doctor_v1":
        return {
            "present": False,
            "reachable": False,
            "authenticated": False,
            "status": None,
            "blockers": [],
        }
    return {
        "present": True,
        "reachable": bool(payload.get("reachable")),
        "authenticated": bool(payload.get("authenticated")),
        "status": payload.get("status"),
        "blockers": list(payload.get("blockers") or []),
        "generated_at": payload.get("generated_at"),
    }


def _ibkr_forecastex_discovery_candidates_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "ibkr_forecastex_discovery_candidates_v1":
        return {
            "present": False,
            "discovery_status": None,
            "discovery_statuses": [],
            "documented_seed_ff_attempted": False,
            "ff_underlier_found": False,
            "forecastx_underlier_candidates": 0,
            "forecastx_tradable_contract_candidates": 0,
            "forecastx_marketdata_rows": 0,
            "final_tradable_rows": 0,
            "forecastx_option_months_attempted": 0,
            "forecastx_strikes_found": 0,
            "forecastx_info_requests": 0,
            "forecastx_yes_rows": 0,
            "forecastx_no_rows": 0,
            "candidate_count": 0,
            "forecastx_candidate_count": 0,
            "normalized_possible_count": 0,
            "seed_candidate_count": 0,
            "seed_conids_count": 0,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "discovery_status": summary.get("discovery_status"),
        "discovery_statuses": list(summary.get("discovery_statuses") or []),
        "documented_seed_ff_attempted": bool(summary.get("documented_seed_ff_attempted")),
        "ff_underlier_found": bool(summary.get("ff_underlier_found")),
        "forecastx_underlier_candidates": _int(summary.get("forecastx_underlier_candidates")),
        "forecastx_tradable_contract_candidates": _int(summary.get("forecastx_tradable_contract_candidates")),
        "forecastx_marketdata_rows": _int(summary.get("forecastx_marketdata_rows")),
        "final_tradable_rows": _int(summary.get("final_tradable_rows")),
        "forecastx_option_months_attempted": _int(summary.get("forecastx_option_months_attempted")),
        "forecastx_strikes_found": _int(summary.get("forecastx_strikes_found")),
        "forecastx_info_requests": _int(summary.get("forecastx_info_requests")),
        "forecastx_yes_rows": _int(summary.get("forecastx_yes_rows")),
        "forecastx_no_rows": _int(summary.get("forecastx_no_rows")),
        "candidate_count": _int(summary.get("candidate_count")),
        "forecastx_candidate_count": _int(summary.get("forecastx_candidate_count")),
        "normalized_possible_count": _int(summary.get("normalized_possible_count")),
        "seed_candidate_count": _int(summary.get("seed_candidate_count")),
        "seed_conids_count": _int(payload.get("seed_conids_count")),
        "generated_at": payload.get("generated_at"),
    }


def _ibkr_forecastex_quote_diagnostics_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "ibkr_forecastex_quote_diagnostics_v1":
        return {
            "present": False,
            "final_contract_rows": 0,
            "marketdata_rows": 0,
            "quote_rows_mapped_to_contracts": 0,
            "rows_with_bid": 0,
            "rows_with_ask": 0,
            "rows_with_bid_ask": 0,
            "rows_with_bid_ask_size": 0,
            "rows_with_timestamp": 0,
            "rows_quote_diagnostic_complete": 0,
            "rows_execution_ready": 0,
            "top_quote_blockers": [],
            "blockers_by_count": {},
            "legacy_aliases": [],
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_quote_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in summary.get("top_quote_blockers", [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ]
    return {
        "present": True,
        "final_contract_rows": _int(summary.get("final_contract_rows")),
        "marketdata_rows": _int(summary.get("marketdata_rows")),
        "quote_rows_mapped_to_contracts": _int(summary.get("quote_rows_mapped_to_contracts")),
        "rows_with_bid": _int(summary.get("rows_with_bid")),
        "rows_with_ask": _int(summary.get("rows_with_ask")),
        "rows_with_bid_ask": _int(summary.get("rows_with_bid_ask")),
        "rows_with_bid_ask_size": _int(summary.get("rows_with_bid_ask_size")),
        "rows_with_timestamp": _int(summary.get("rows_with_timestamp")),
        "rows_quote_diagnostic_complete": _int(summary.get("rows_quote_diagnostic_complete")),
        "rows_execution_ready": 0,
        "top_quote_blockers": top_quote_blockers,
        "blockers_by_count": dict(summary.get("blockers_by_count") or {}),
        "legacy_aliases": list(summary.get("legacy_aliases") or payload.get("legacy_aliases") or []),
        "generated_at": payload.get("generated_at"),
    }


def _ibkr_forecastex_consistency_warnings(
    *,
    discovery: dict[str, Any],
    quote_diagnostics: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if not discovery.get("present") or not quote_diagnostics.get("present"):
        return warnings
    discovery_final = _int(discovery.get("final_tradable_rows"))
    quote_final = _int(quote_diagnostics.get("final_contract_rows"))
    if discovery_final and quote_final and discovery_final != quote_final:
        warnings.append(
            "ibkr_final_tradable_rows_mismatch:"
            f"discovery={discovery_final}:quote_diagnostics={quote_final}"
        )
    mapped = _int(quote_diagnostics.get("quote_rows_mapped_to_contracts"))
    marketdata_rows = _int(quote_diagnostics.get("marketdata_rows"))
    if mapped and marketdata_rows and mapped != marketdata_rows:
        warnings.append(
            "ibkr_quote_rows_mapped_marketdata_rows_mismatch:"
            f"mapped={mapped}:marketdata_rows={marketdata_rows}"
        )
    return warnings


def _ibkr_forecastex_raw_shape_summary_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_kind") != "ibkr_forecastex_raw_shape_summary_v1":
        return {
            "present": False,
            "raw_files_read": 0,
            "forecastx_identifier_files": 0,
            "final_tradable_contract_field_files": 0,
            "call_put_right_files": 0,
            "expiry_or_month_files": 0,
            "strike_field_files": 0,
            "event_contract_field_files": 0,
            "binary_yes_no_files": 0,
            "final_tradable_contract_fields_present": False,
            "final_tradable_contract_blockers": [],
            "endpoint_counts": {},
            "read_only_secdef_paths_exhausted": False,
            "snapshot_dir": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    raw_files_read = _int(summary.get("raw_files_read"))
    forecastx_files = _int(summary.get("forecastx_identifier_files"))
    final_tradable_files = _int(summary.get("final_tradable_contract_field_files"))
    call_put_files = _int(summary.get("call_put_right_files"))
    # Conservative exhaustion gate: at least 8 saved raw files from the read-only
    # secdef paths, all confirming ForecastEx identifier present, none containing
    # any C/P right field, and no final tradable contract evidence.
    exhausted = (
        raw_files_read >= 8
        and forecastx_files >= 1
        and final_tradable_files == 0
        and call_put_files == 0
    )
    return {
        "present": True,
        "snapshot_dir": payload.get("snapshot_dir"),
        "raw_files_read": raw_files_read,
        "forecastx_identifier_files": forecastx_files,
        "final_tradable_contract_field_files": final_tradable_files,
        "call_put_right_files": call_put_files,
        "expiry_or_month_files": _int(summary.get("expiry_or_month_files")),
        "strike_field_files": _int(summary.get("strike_field_files")),
        "event_contract_field_files": _int(summary.get("event_contract_field_files")),
        "binary_yes_no_files": _int(summary.get("binary_yes_no_files")),
        "final_tradable_contract_fields_present": bool(summary.get("final_tradable_contract_fields_present")),
        "final_tradable_contract_blockers": list(summary.get("final_tradable_contract_blockers") or []),
        "endpoint_counts": dict(summary.get("endpoint_counts") or {}),
        "read_only_secdef_paths_exhausted": exhausted,
        "generated_at": payload.get("generated_at"),
    }


def _kalshi_kxmlb_event_evidence_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "kalshi_event_evidence_summary_v1":
        return {
            "present": False,
            "ready_for_human_manifest_review": False,
            "market_count": 0,
            "explicit_outcome_list_exists": False,
            "explicit_completeness_evidence_exists": False,
            "settlement_rules_source_evidence_exists": False,
            "fresh_orderbook_depth_exists": False,
            "local_manifest_v1_would_pass_if_reviewer_fields_added": False,
            "top_blockers": [],
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        str(row.get("blocker"))
        for row in summary.get("top_blockers", [])
        if isinstance(row, dict) and row.get("blocker")
    ][:5]
    return {
        "present": True,
        "ready_for_human_manifest_review": bool(summary.get("ready_for_human_manifest_review")),
        "market_count": _int(summary.get("market_count")),
        "explicit_outcome_list_exists": bool(summary.get("explicit_outcome_list_exists")),
        "explicit_completeness_evidence_exists": bool(summary.get("explicit_completeness_evidence_exists")),
        "settlement_rules_source_evidence_exists": bool(summary.get("settlement_rules_source_evidence_exists")),
        "fresh_orderbook_depth_exists": bool(summary.get("fresh_orderbook_depth_exists")),
        "local_manifest_v1_would_pass_if_reviewer_fields_added": bool(
            summary.get("local_manifest_v1_would_pass_if_reviewer_fields_added")
        ),
        "top_blockers": top_blockers,
        "generated_at": payload.get("generated_at"),
    }


def _polymarket_taxonomy_shape_scout_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "polymarket_taxonomy_shape_scout_v1":
        return {
            "present": False,
            "total_rows": 0,
            "point_in_time_candidates": 0,
            "deadline_or_range_hit_blocked": 0,
            "clob_book_attached": 0,
            "typed_key_complete": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "shape_counts": {},
            "top_blockers": [],
            "scout_report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "total_rows": _int(summary.get("total_rows")),
        "point_in_time_candidates": _int(summary.get("point_in_time_candidates")),
        "deadline_or_range_hit_blocked": _int(summary.get("deadline_or_range_hit_blocked")),
        "clob_book_attached": _int(summary.get("clob_book_attached")),
        "typed_key_complete": _int(summary.get("typed_key_complete")),
        "exact_ready_rows": _int(summary.get("exact_ready_rows")),
        "paper_candidate_rows": _int(summary.get("paper_candidate_rows")),
        "shape_counts": dict(summary.get("shape_counts") or {}),
        "category_counts": dict(summary.get("category_counts") or {}),
        "action_counts": dict(summary.get("action_counts") or {}),
        "top_blockers": top_blockers,
        "scout_report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _polymarket_clob_taxonomy_refresh_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "polymarket_clob_taxonomy_refresh_v1":
        return {
            "present": False,
            "shape_filter": None,
            "min_score": None,
            "candidates_selected": 0,
            "books_requested": 0,
            "books_saved": 0,
            "rows_enriched": 0,
            "rows_with_bid": 0,
            "rows_with_ask": 0,
            "rows_with_bid_ask": 0,
            "rows_with_bid_ask_size": 0,
            "rows_with_timestamp": 0,
            "still_missing_clob": 0,
            "still_stale_or_missing_quote": 0,
            "top_remaining_blockers": [],
            "enriched_report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_remaining = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_remaining_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "shape_filter": payload.get("shape_filter"),
        "min_score": payload.get("min_score"),
        "max_candidates": _int(payload.get("max_candidates")),
        "include_deadline_range": bool(payload.get("include_deadline_range")),
        "candidates_selected": _int(summary.get("candidates_selected")),
        "books_requested": _int(summary.get("books_requested")),
        "books_saved": _int(summary.get("books_saved")),
        "rows_enriched": _int(summary.get("rows_enriched")),
        "rows_with_bid": _int(summary.get("rows_with_bid")),
        "rows_with_ask": _int(summary.get("rows_with_ask")),
        "rows_with_bid_ask": _int(summary.get("rows_with_bid_ask")),
        "rows_with_bid_ask_size": _int(summary.get("rows_with_bid_ask_size")),
        "rows_with_timestamp": _int(summary.get("rows_with_timestamp")),
        "still_missing_clob": _int(summary.get("still_missing_clob")),
        "still_stale_or_missing_quote": _int(summary.get("still_stale_or_missing_quote")),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_remaining_blockers": top_remaining,
        "snapshot_dir": payload.get("snapshot_dir"),
        "enriched_report_path": payload.get("taxonomy_json"),
        "generated_at": payload.get("generated_at"),
    }


def _polymarket_point_in_time_typed_key_audit_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "polymarket_point_in_time_typed_key_audit_v1":
        return {
            "present": False,
            "point_in_time_rows_audited": 0,
            "excluded_fake_point_in_time_rows": 0,
            "typed_complete_rows": 0,
            "targeted_clob_refresh_candidate_rows": 0,
            "rows_with_clob_attached": 0,
            "rows_with_bid_ask_size": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_blockers": [],
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "point_in_time_rows_seen": _int(summary.get("point_in_time_rows_seen")),
        "point_in_time_rows_audited": _int(summary.get("point_in_time_rows_audited")),
        "excluded_fake_point_in_time_rows": _int(summary.get("excluded_fake_point_in_time_rows")),
        "typed_complete_rows": _int(summary.get("typed_complete_rows")),
        "targeted_clob_refresh_candidate_rows": _int(summary.get("targeted_clob_refresh_candidate_rows")),
        "rows_with_clob_attached": _int(summary.get("rows_with_clob_attached")),
        "rows_with_bid_ask_size": _int(summary.get("rows_with_bid_ask_size")),
        "rows_missing_target_date_or_source": _int(summary.get("rows_missing_target_date_or_source")),
        "exact_ready_rows": _int(summary.get("exact_ready_rows")),
        "paper_candidate_rows": _int(summary.get("paper_candidate_rows")),
        "execution_ready_rows": _int(summary.get("execution_ready_rows")),
        "market_family_counts": dict(summary.get("market_family_counts") or {}),
        "peer_lane_hint_counts": dict(summary.get("peer_lane_hint_counts") or {}),
        "top_blockers": top_blockers,
        "top_20_candidates": list(summary.get("top_20_candidates") or [])[:20],
        "top_targeted_clob_refresh_candidates": list(summary.get("top_targeted_clob_refresh_candidates") or [])[:20],
        "report_path": payload.get("report_path") or payload.get("taxonomy_json"),
        "generated_at": payload.get("generated_at"),
    }


def _cdna_crypto_basis_risk_scout_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "cdna_crypto_basis_risk_scout_v1":
        return {
            "present": False,
            "cdna_rows": 0,
            "cdna_btc_rows": 0,
            "cdna_eth_rows": 0,
            "point_in_time_rows": 0,
            "deadline_or_range_hit_rows": 0,
            "ambiguous_rows": 0,
            "scout_row_count": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_blockers": [],
            "action_counts": {},
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "cdna_rows": _int(summary.get("cdna_rows")),
        "cdna_btc_rows": _int(summary.get("cdna_btc_rows")),
        "cdna_eth_rows": _int(summary.get("cdna_eth_rows")),
        "point_in_time_rows": _int(summary.get("point_in_time_rows")),
        "deadline_or_range_hit_rows": _int(summary.get("deadline_or_range_hit_rows")),
        "ambiguous_rows": _int(summary.get("ambiguous_rows")),
        "scout_row_count": _int(summary.get("scout_row_count")),
        "exact_ready_rows": _int(summary.get("exact_ready_rows")),
        "paper_candidate_rows": _int(summary.get("paper_candidate_rows")),
        "top_blockers": top_blockers,
        "action_counts": dict(summary.get("action_counts") or {}),
        "scout_report_path": payload.get("input_fixture"),
        "generated_at": payload.get("generated_at"),
    }


def _cdna_parser_health_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "crypto_com_predict_cdna_research_snapshot_v1":
        return {
            "present": False,
            "rows": 0,
            "btc_rows": 0,
            "eth_rows": 0,
            "point_in_time_rows": 0,
            "deadline_or_range_hit_rows": 0,
            "all_time_high_rows": 0,
            "ambiguous_rows": 0,
            "top_blockers": [],
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows_by_shape = summary.get("rows_by_market_shape") or {}
    raw_top_blockers = summary.get("top_blockers") or []
    top_blockers: list[dict[str, Any]] = []
    if isinstance(raw_top_blockers, list):
        for item in raw_top_blockers[:10]:
            if isinstance(item, dict) and item.get("blocker"):
                top_blockers.append({"blocker": str(item.get("blocker")), "count": _int(item.get("count"))})
            elif isinstance(item, str):
                top_blockers.append({"blocker": item, "count": 0})
    elif isinstance(raw_top_blockers, dict):
        for b, c in sorted(raw_top_blockers.items(), key=lambda kv: -int(kv[1] or 0))[:10]:
            top_blockers.append({"blocker": b, "count": _int(c)})
    ambiguous_rows = int(rows_by_shape.get("ambiguous", 0)) + int(rows_by_shape.get("unknown", 0))
    return {
        "present": True,
        "rows": _int(summary.get("rows") or summary.get("parsed_rows")),
        "btc_rows": _int(summary.get("btc_rows")),
        "eth_rows": _int(summary.get("eth_rows")),
        "point_in_time_rows": _int(summary.get("point_in_time_rows")),
        "deadline_or_range_hit_rows": _int(summary.get("deadline_or_range_hit_rows")),
        "all_time_high_rows": _int(summary.get("all_time_high_rows")),
        "ambiguous_rows": ambiguous_rows,
        "rows_by_market_shape": dict(rows_by_shape),
        "top_blockers": top_blockers,
        "generated_at": payload.get("generated_at"),
    }


def _cross_venue_opportunity_scout_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "cross_venue_opportunity_scout_v1":
        return {
            "present": False,
            "scout_row_count": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "execution_ready_rows": 0,
            "top_lane": None,
            "all_platform_top_lane": None,
            "active_platforms": [],
            "active_ranked_rows": 0,
            "inactive_platform_rows": 0,
            "core_trio_top_lane": None,
            "top_blockers": [],
            "lane_counts": {},
            "action_counts": {},
            "polymarket_rows_loaded": 0,
            "polymarket_enriched_rows_loaded": 0,
            "polymarket_rows_with_bid_ask_size": 0,
            "polymarket_rows_with_timestamp": 0,
            "polymarket_overlap_rows": 0,
            "top_enriched_polymarket_review_targets": [],
            "scout_report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "scout_row_count": _int(summary.get("scout_row_count")),
        "exact_ready_rows": _int(summary.get("exact_ready_rows")),
        "paper_candidate_rows": _int(summary.get("paper_candidate_rows")),
        "execution_ready_rows": _int(summary.get("execution_ready_rows")),
        "top_lane": summary.get("top_lane"),
        "all_platform_top_lane": summary.get("all_platform_top_lane"),
        "active_platforms": list(summary.get("active_platforms") or []),
        "active_ranked_rows": _int(summary.get("active_ranked_rows")),
        "inactive_platform_rows": _int(summary.get("inactive_platform_rows")),
        "core_trio_top_lane": summary.get("core_trio_top_lane"),
        "core_trio_top_lane_summary": list(summary.get("core_trio_top_lane_summary") or []),
        "top_blockers": top_blockers,
        "lane_counts": dict(summary.get("lane_counts") or {}),
        "action_counts": dict(summary.get("action_counts") or {}),
        "polymarket_rows_loaded": _int(summary.get("polymarket_rows_loaded")),
        "polymarket_enriched_rows_loaded": _int(summary.get("polymarket_enriched_rows_loaded")),
        "polymarket_rows_with_bid_ask_size": _int(summary.get("polymarket_rows_with_bid_ask_size")),
        "polymarket_rows_with_timestamp": _int(summary.get("polymarket_rows_with_timestamp")),
        "polymarket_overlap_rows": _int(summary.get("polymarket_overlap_rows")),
        "top_enriched_polymarket_review_targets": list(summary.get("top_enriched_polymarket_review_targets") or [])[:10],
        "scout_report_path": payload.get("input_dir"),
        "polymarket_enriched_report_path": summary.get("polymarket_enriched_report_path"),
        "generated_at": payload.get("generated_at"),
    }


def _core_trio_peer_coverage_audit_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "core_trio_peer_coverage_audit_v1":
        return {
            "present": False,
            "total_core_trio_rows": 0,
            "peer_coverage_families": 0,
            "families_with_kalshi_peer_rows": 0,
            "families_without_kalshi_peer_rows": 0,
            "strongest_overlap_family": None,
            "next_recommended_lane": None,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_blockers": [],
            "top_10_next_fetch_targets": [],
            "top_10_closest_existing_overlaps": [],
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    top_targets = list(summary.get("top_10_next_fetch_targets") or [])[:10]
    return {
        "present": True,
        "total_core_trio_rows": _int(summary.get("total_core_trio_rows")),
        "peer_coverage_families": _int(summary.get("peer_coverage_families")),
        "families_with_kalshi_peer_rows": _int(summary.get("families_with_kalshi_peer_rows")),
        "families_without_kalshi_peer_rows": _int(summary.get("families_without_kalshi_peer_rows")),
        "families_with_kalshi_peer_row_names": list(summary.get("families_with_kalshi_peer_row_names") or []),
        "families_without_kalshi_peer_row_names": list(summary.get("families_without_kalshi_peer_row_names") or []),
        "strongest_overlap_family": summary.get("strongest_overlap_family"),
        "next_recommended_lane": (top_targets[0].get("family") if top_targets and isinstance(top_targets[0], dict) else None),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "top_blockers": top_blockers,
        "top_10_next_fetch_targets": top_targets,
        "top_10_closest_existing_overlaps": list(summary.get("top_10_closest_existing_overlaps") or [])[:10],
        "report_path": payload.get("report_path") or payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _kalshi_crypto_typed_key_audit_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "kalshi_crypto_typed_key_audit_v1":
        return {
            "present": False,
            "kalshi_crypto_rows": 0,
            "typed_complete_rows": 0,
            "point_in_time_rows": 0,
            "deadline_or_range_hit_rows": 0,
            "ambiguous_rows": 0,
            "rows_with_asset": 0,
            "rows_with_threshold": 0,
            "rows_with_comparator": 0,
            "rows_with_target_date": 0,
            "rows_with_target_time": 0,
            "rows_with_timezone": 0,
            "rows_with_settlement_source": 0,
            "rows_with_settlement_source_url": 0,
            "rows_with_quote": 0,
            "fresh_crypto_snapshot_present": False,
            "fresh_crypto_snapshot_rows_loaded": 0,
            "enriched_files_read": 0,
            "rows_with_existing_top_of_book": 0,
            "rows_with_fresh_orderbook": 0,
            "rows_with_stale_top_of_book": 0,
            "rows_with_full_orderbook_missing": 0,
            "rows_with_bid_ask_size_timestamp": 0,
            "kalshi_live_orderbook_fetch_supported": False,
            "kalshi_live_orderbook_fetch_not_enabled_or_missing_count": 0,
            "possible_cdna_peer_rows": 0,
            "possible_polymarket_peer_rows": 0,
            "no_saved_peer_rows": 0,
            "date_threshold_comparator_overlap_rows": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "next_action": None,
            "next_action_reason": None,
            "top_blockers": [],
            "asset_counts": {},
            "shape_counts": {},
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "kalshi_crypto_rows": _int(summary.get("kalshi_crypto_rows")),
        "typed_complete_rows": _int(summary.get("typed_complete_rows")),
        "point_in_time_rows": _int(summary.get("point_in_time_rows")),
        "deadline_or_range_hit_rows": _int(summary.get("deadline_or_range_hit_rows")),
        "ambiguous_rows": _int(summary.get("ambiguous_rows")),
        "rows_with_asset": _int(summary.get("rows_with_asset")),
        "rows_with_threshold": _int(summary.get("rows_with_threshold")),
        "rows_with_comparator": _int(summary.get("rows_with_comparator")),
        "rows_with_target_date": _int(summary.get("rows_with_target_date")),
        "rows_with_target_time": _int(summary.get("rows_with_target_time")),
        "rows_with_timezone": _int(summary.get("rows_with_timezone")),
        "rows_with_settlement_source": _int(summary.get("rows_with_settlement_source")),
        "rows_with_settlement_source_url": _int(summary.get("rows_with_settlement_source_url")),
        "rows_with_quote": _int(summary.get("rows_with_quote")),
        "fresh_crypto_snapshot_present": bool(summary.get("fresh_crypto_snapshot_present")),
        "fresh_crypto_snapshot_rows_loaded": _int(summary.get("fresh_crypto_snapshot_rows_loaded")),
        "enriched_files_read": _int(summary.get("enriched_files_read")),
        "rows_with_existing_top_of_book": _int(summary.get("rows_with_existing_top_of_book")),
        "rows_with_fresh_orderbook": _int(summary.get("rows_with_fresh_orderbook")),
        "rows_with_stale_top_of_book": _int(summary.get("rows_with_stale_top_of_book")),
        "rows_with_full_orderbook_missing": _int(summary.get("rows_with_full_orderbook_missing")),
        "rows_with_bid_ask_size_timestamp": _int(summary.get("rows_with_bid_ask_size_timestamp")),
        "kalshi_live_orderbook_fetch_supported": bool(summary.get("kalshi_live_orderbook_fetch_supported")),
        "kalshi_live_orderbook_fetch_not_enabled_or_missing_count": _int(
            summary.get("kalshi_live_orderbook_fetch_not_enabled_or_missing_count")
        ),
        "possible_cdna_peer_rows": _int(summary.get("possible_cdna_peer_rows")),
        "possible_polymarket_peer_rows": _int(summary.get("possible_polymarket_peer_rows")),
        "no_saved_peer_rows": _int(summary.get("no_saved_peer_rows")),
        "date_threshold_comparator_overlap_rows": _int(summary.get("date_threshold_comparator_overlap_rows")),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "next_action": summary.get("next_action"),
        "next_action_reason": summary.get("next_action_reason"),
        "top_blockers": top_blockers,
        "asset_counts": dict(summary.get("asset_counts") or {}),
        "shape_counts": dict(summary.get("shape_counts") or {}),
        "report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _crypto_payoff_calendar_audit_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "crypto_payoff_calendar_audit_v1":
        return {
            "present": False,
            "total_crypto_rows": 0,
            "venues": [],
            "exact_shape_possible_rows": 0,
            "basis_risk_only_rows": 0,
            "manual_rules_needed_rows": 0,
            "reference_only_rows": 0,
            "no_current_peer_rows": 0,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "counts_by_shape_and_venue": {},
            "counts_by_class_and_venue": {},
            "top_blockers": [],
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    return {
        "present": True,
        "total_crypto_rows": _int(summary.get("total_crypto_rows")),
        "venues": list(summary.get("venues") or []),
        "exact_shape_possible_rows": _int(summary.get("exact_shape_possible_rows")),
        "basis_risk_only_rows": _int(summary.get("basis_risk_only_rows")),
        "manual_rules_needed_rows": _int(summary.get("manual_rules_needed_rows")),
        "reference_only_rows": _int(summary.get("reference_only_rows")),
        "no_current_peer_rows": _int(summary.get("no_current_peer_rows")),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "counts_by_shape_and_venue": dict(summary.get("counts_by_shape_and_venue") or {}),
        "counts_by_class_and_venue": dict(summary.get("counts_by_class_and_venue") or {}),
        "top_blockers": top_blockers,
        "report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _crypto_manual_discovery_workbench_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "crypto_manual_discovery_workbench_v1":
        return {
            "present": False,
            "group_count": 0,
            "targets_emitted": 0,
            "total_eligible_audit_rows": 0,
            "top_target_group": None,
            "top_target_venue": None,
            "top_target_asset": None,
            "top_target_date": None,
            "top_target_payoff_shape": None,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []
    group_breakdown = [
        {
            "group_name": g.get("group_name"),
            "venue": g.get("venue"),
            "targets_emitted": _int(g.get("targets_emitted")),
            "total_eligible_rows": _int(g.get("total_eligible_rows")),
        }
        for g in groups
        if isinstance(g, dict)
    ]
    return {
        "present": True,
        "group_count": _int(summary.get("group_count")),
        "targets_emitted": _int(summary.get("targets_emitted")),
        "total_eligible_audit_rows": _int(summary.get("total_eligible_audit_rows")),
        "top_target_group": summary.get("top_target_group"),
        "top_target_venue": summary.get("top_target_venue"),
        "top_target_asset": summary.get("top_target_asset"),
        "top_target_date": summary.get("top_target_date"),
        "top_target_payoff_shape": summary.get("top_target_payoff_shape"),
        "top_target_comparability_class": summary.get("top_target_comparability_class"),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "group_breakdown": group_breakdown,
        "report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _manual_evidence_requirements_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "manual_evidence_requirements_v1":
        return {
            "present": False,
            "total_items": 0,
            "verticals": [],
            "priority_counts": {},
            "status_counts": {},
            "platform_status_counts": {},
            "closest_to_source_review_vertical": None,
            "closest_to_exact_review_vertical": None,
            "distraction_vertical": None,
            "top_10_this_week": [],
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "total_items": _int(summary.get("total_items")),
        "verticals": list(summary.get("verticals") or []),
        "priority_counts": dict(summary.get("priority_counts") or {}),
        "status_counts": dict(summary.get("status_counts") or {}),
        "platform_status_counts": dict(summary.get("platform_status_counts") or {}),
        "closest_to_source_review_vertical": summary.get("closest_to_source_review_vertical"),
        "closest_to_exact_review_vertical": summary.get("closest_to_exact_review_vertical"),
        "distraction_vertical": summary.get("distraction_vertical"),
        "top_10_this_week": list(summary.get("top_10_this_week") or [])[:10],
        "queued_platforms_remain_queued": bool(summary.get("queued_platforms_remain_queued")),
        "reference_only_platforms_never_become_pair_side": bool(
            summary.get("reference_only_platforms_never_become_pair_side")
        ),
        "no_manual_evidence_clears_evaluator_gates_on_its_own": bool(
            summary.get("no_manual_evidence_clears_evaluator_gates_on_its_own")
        ),
        "no_manual_evidence_creates_paper_candidate": bool(
            summary.get("no_manual_evidence_creates_paper_candidate")
        ),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _crypto_peer_acquisition_plan_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "crypto_peer_acquisition_plan_v1":
        return {
            "present": False,
            "kalshi_typed_complete_grid_rows": 0,
            "unique_assets": 0,
            "unique_dates": 0,
            "unique_thresholds": 0,
            "top_target_asset": None,
            "top_target_date": None,
            "polymarket_queries_recommended": 0,
            "polymarket_clob_refresh_recommended": 0,
            "cdna_targets_recommended": 0,
            "kalshi_orderbook_targets_recommended": 0,
            "kalshi_fresh_crypto_snapshot_recommended": False,
            "kalshi_orderbook_targets_requiring_snapshot_age_override": 0,
            "safe_commands_referenced": [],
            "safe_commands_missing": [],
            "command_validation_error_count": 0,
            "polymarket_targeted_query_command_missing": False,
            "kalshi_orderbook_input_snapshot_missing_for_crypto_grid": False,
            "kalshi_orderbook_input_snapshot_paths": [],
            "recommended_next_command": None,
            "recommended_next_action": None,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "top_blockers": [],
            "top_target_assets": [],
            "top_target_dates": [],
            "report_path": None,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    top_blockers = [
        {"blocker": str(row.get("blocker")), "count": _int(row.get("count"))}
        for row in (summary.get("top_blockers") or [])
        if isinstance(row, dict) and str(row.get("blocker") or "").strip()
    ][:10]
    top_assets = list(summary.get("top_target_assets") or [])[:5]
    top_dates = list(summary.get("top_target_dates") or [])[:5]
    top_targets = list(summary.get("top_20_targets") or [])
    recommended_next_command: str | None = None
    recommended_next_action: str | None = None
    if top_targets:
        recommended_next_command = top_targets[0].get("safe_command")
        recommended_next_action = top_targets[0].get("recommended_next_action")
    return {
        "present": True,
        "kalshi_typed_complete_grid_rows": _int(summary.get("kalshi_typed_complete_grid_rows")),
        "unique_assets": _int(summary.get("unique_assets")),
        "unique_dates": _int(summary.get("unique_dates")),
        "unique_thresholds": _int(summary.get("unique_thresholds")),
        "top_target_asset": (top_assets[0].get("asset") if top_assets and isinstance(top_assets[0], dict) else None),
        "top_target_date": (top_dates[0].get("target_date") if top_dates and isinstance(top_dates[0], dict) else None),
        "polymarket_queries_recommended": _int(summary.get("polymarket_queries_recommended")),
        "polymarket_clob_refresh_recommended": _int(summary.get("polymarket_clob_refresh_recommended")),
        "cdna_targets_recommended": _int(summary.get("cdna_targets_recommended")),
        "kalshi_orderbook_targets_recommended": _int(summary.get("kalshi_orderbook_targets_recommended")),
        "kalshi_fresh_crypto_snapshot_recommended": bool(summary.get("kalshi_fresh_crypto_snapshot_recommended")),
        "kalshi_orderbook_targets_requiring_snapshot_age_override": _int(
            summary.get("kalshi_orderbook_targets_requiring_snapshot_age_override")
        ),
        "safe_commands_referenced": list(summary.get("safe_commands_referenced") or []),
        "safe_commands_missing": list(summary.get("safe_commands_missing") or []),
        "command_validation_error_count": _int(summary.get("command_validation_error_count")),
        "polymarket_targeted_query_command_missing": bool(summary.get("polymarket_targeted_query_command_missing")),
        "kalshi_orderbook_input_snapshot_missing_for_crypto_grid": bool(
            summary.get("kalshi_orderbook_input_snapshot_missing_for_crypto_grid")
        ),
        "kalshi_orderbook_input_snapshot_paths": list(summary.get("kalshi_orderbook_input_snapshot_paths") or []),
        "kalshi_crypto_grid_source_files": list(summary.get("kalshi_crypto_grid_source_files") or []),
        "recommended_next_command": recommended_next_command,
        "recommended_next_action": recommended_next_action,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "execution_ready_rows": 0,
        "top_blockers": top_blockers,
        "top_target_assets": top_assets,
        "top_target_dates": top_dates,
        "top_20_targets": top_targets,
        "report_path": payload.get("input_dir"),
        "generated_at": payload.get("generated_at"),
    }


def _paper_readiness_probe_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("source") != "paper_readiness_probe_v1":
        return {
            "present": False,
            "total_rows_considered": 0,
            "rows_blocked_by_stale_quote": 0,
            "rows_blocked_by_missing_quote": 0,
            "rows_blocked_by_fee": 0,
            "rows_blocked_by_pair_review": 0,
            "paper_ready_count": 0,
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "present": True,
        "total_rows_considered": _int(summary.get("total_rows_considered")),
        "rows_blocked_by_stale_quote": _int(summary.get("rows_blocked_by_stale_quote")),
        "rows_blocked_by_missing_quote": _int(summary.get("rows_blocked_by_missing_quote")),
        "rows_blocked_by_fee": _int(summary.get("rows_blocked_by_fee")),
        "rows_blocked_by_pair_review": _int(summary.get("rows_blocked_by_pair_review")),
        "paper_ready_count": _int(summary.get("paper_ready_count")),
    }


def _highest_priority_next_action(
    *,
    missing_report_blockers: list[dict[str, Any]],
    tier_counts: dict[str, int],
    highlight: dict[str, Any],
    cross_platform: dict[str, Any],
    paper: dict[str, Any],
    family_graduation: dict[str, Any] | None = None,
    canonical_registry_coverage: dict[str, Any] | None = None,
    paper_readiness_probe: dict[str, Any] | None = None,
    ibkr_access_doctor: dict[str, Any] | None = None,
    ibkr_discovery_candidates: dict[str, Any] | None = None,
    ibkr_raw_shape_summary: dict[str, Any] | None = None,
    ibkr_quote_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    core_missing = sorted(b["report"] for b in missing_report_blockers if b.get("severity") == "core")
    if core_missing:
        return {
            "action": "GENERATE_MISSING_CORE_REPORTS",
            "reason": "Core status inputs are missing: " + ", ".join(core_missing) + ".",
            "report_only": True,
        }
    if paper.get("current_needs_review_count", 0) > 0:
        return {
            "action": "REVIEW_EXISTING_EVALUATOR_OUTPUT",
            "reason": "Forensic audit reports current-needs-review evaluator rows; review them manually before any downstream step.",
            "report_only": True,
        }
    if paper.get("count", 0) > 0 and paper.get("audit_present"):
        if paper.get("archive_plan_present") and paper.get("archive_plan_archive_candidate_count", 0) > 0:
            return {
                "action": "REVIEW_STALE_REPORT_ARCHIVE_PLAN",
                "reason": (
                    "Stale evaluator artifacts have already been classified and a non-mutating archive plan exists. "
                    "Review the suggested moves before regenerating current reports."
                ),
                "report_only": True,
            }
        return {
            "action": "ARCHIVE_OR_REGENERATE_STALE_PAPER_CANDIDATES",
            "reason": (
                "Saved evaluator files contain PAPER-action rows, but the existing_paper_candidate_audit "
                "classified them as stale or blocked. Archive the old files or regenerate from current snapshots."
            ),
            "report_only": True,
        }
    if paper.get("count", 0) > 0:
        return {
            "action": "RUN_EXISTING_PAPER_CANDIDATE_AUDIT",
            "reason": (
                "Saved evaluator files contain PAPER-action rows. Run audit-existing-paper-candidates to classify "
                "them before recommending review or archive."
            ),
            "report_only": True,
        }
    ibkr_doctor = ibkr_access_doctor or {}
    ibkr_discovery = ibkr_discovery_candidates or {}
    ibkr_quote = ibkr_quote_diagnostics or {}
    if ibkr_doctor.get("present") and ibkr_doctor.get("reachable") and not ibkr_doctor.get("authenticated"):
        return {
            "action": "LOCAL_GATEWAY_REAUTH_REQUIRED",
            "reason": (
                "IBKR Client Portal Gateway is reachable but the local session is not authenticated. "
                "Complete manual login in Client Portal Gateway, then rerun ibkr-forecastex-access-doctor."
            ),
            "report_only": True,
        }
    if (
        ibkr_doctor.get("present")
        and ibkr_doctor.get("authenticated")
        and ibkr_discovery.get("present")
        and _int(ibkr_discovery.get("forecastx_tradable_contract_candidates")) > 0
        and _int(ibkr_discovery.get("forecastx_marketdata_rows")) == 0
    ):
        return {
            "action": "FORECASTX_CONTRACT_INFO_FOUND_NEEDS_MARKETDATA_PERMISSION",
            "reason": (
                "Read-only ForecastEx secdef info found tradable C/P contract rows, but no market-data "
                "snapshot rows were captured. Review market-data permissions before considering downstream use."
            ),
            "report_only": True,
            "forecastx_tradable_contract_candidates": _int(ibkr_discovery.get("forecastx_tradable_contract_candidates")),
        }
    ibkr_auth_or_latest_ok = (
        bool(ibkr_doctor.get("authenticated"))
        or ibkr_doctor.get("status") == "OK"
        or ibkr_discovery.get("discovery_status") == "FORECASTX_CANDIDATES_FOUND"
    )
    quote_final_rows = _int(ibkr_quote.get("final_contract_rows"))
    quote_complete_rows = _int(ibkr_quote.get("rows_quote_diagnostic_complete"))
    quote_incomplete_rows = max(0, quote_final_rows - quote_complete_rows)
    if (
        ibkr_auth_or_latest_ok
        and ibkr_quote.get("present")
        and quote_final_rows > 0
        and (_int(ibkr_quote.get("marketdata_rows")) > 0 or _int(ibkr_quote.get("quote_rows_mapped_to_contracts")) > 0)
        and 0 < quote_complete_rows < quote_final_rows
        and _int(ibkr_quote.get("rows_execution_ready")) == 0
    ):
        top_quote_blockers = list(ibkr_quote.get("top_quote_blockers") or [])[:3]
        return {
            "action": "FORECASTX_QUOTE_PARTIAL_REVIEW_PERMISSIONS_OR_DEPTH",
            "reason": (
                f"IBKR ForecastEx quote diagnostics have {quote_complete_rows}/{quote_final_rows} complete rows "
                f"({quote_incomplete_rows} incomplete). Top quote blockers: {_format_top_blockers(top_quote_blockers)}. "
                "Review ForecastEx market data permissions and partial top-of-book depth before exact-review."
            ),
            "report_only": True,
            "final_contract_rows": quote_final_rows,
            "rows_quote_diagnostic_complete": quote_complete_rows,
            "incomplete_quote_rows": quote_incomplete_rows,
            "top_quote_blockers": top_quote_blockers,
            "rows_execution_ready": 0,
        }
    ibkr_raw_shape = ibkr_raw_shape_summary or {}
    if (
        ibkr_doctor.get("present")
        and ibkr_doctor.get("authenticated")
        and ibkr_discovery.get("present")
        and ibkr_discovery.get("ff_underlier_found")
        and _int(ibkr_discovery.get("forecastx_tradable_contract_candidates")) == 0
        and ibkr_raw_shape.get("present")
        and ibkr_raw_shape.get("read_only_secdef_paths_exhausted")
    ):
        return {
            "action": "FORECASTX_READ_ONLY_SECDEF_FINAL_CONTRACT_FIELDS_EXHAUSTED",
            "reason": (
                "The FF ForecastEx underlier was found, but the saved raw-shape inventory across "
                f"{_int(ibkr_raw_shape.get('raw_files_read'))} read-only secdef responses contains zero call/put "
                "right fields and zero final tradable contract evidence. Provide a manually verified seed-conid "
                "file with --seed-conids before further secdef variants will help."
            ),
            "report_only": True,
            "ff_underlier_found": True,
            "raw_files_read": _int(ibkr_raw_shape.get("raw_files_read")),
            "forecastx_identifier_files": _int(ibkr_raw_shape.get("forecastx_identifier_files")),
            "call_put_right_files": _int(ibkr_raw_shape.get("call_put_right_files")),
        }
    if (
        ibkr_doctor.get("present")
        and ibkr_doctor.get("authenticated")
        and ibkr_discovery.get("present")
        and ibkr_discovery.get("ff_underlier_found")
        and _int(ibkr_discovery.get("forecastx_tradable_contract_candidates")) == 0
    ):
        return {
            "action": "FORECASTX_UNDERLIER_FOUND_NEEDS_STRIKES_OR_CONTRACT_INFO",
            "reason": (
                "The documented FF ForecastEx underlier was found, but no tradable C/P ForecastEx contract rows "
                "were discovered. Continue with read-only strikes/secdef info discovery or provide final conids."
            ),
            "report_only": True,
            "ff_underlier_found": True,
        }
    if (
        ibkr_doctor.get("present")
        and ibkr_doctor.get("authenticated")
        and ibkr_discovery.get("present")
        and not ibkr_discovery.get("ff_underlier_found")
        and _int(ibkr_discovery.get("forecastx_candidate_count")) == 0
    ):
        return {
            "action": "PROVIDE_IBKR_FORECASTEX_CONID_OR_REFINE_DISCOVERY",
            "reason": (
                "IBKR Client Portal Gateway is reachable and authenticated, but the latest ForecastEx discovery "
                "report found zero FORECASTX candidates. Provide a seed conid file with --seed-conids or refine "
                "--search-terms before expecting normalized ForecastEx rows."
            ),
            "report_only": True,
            "forecastx_candidate_count": 0,
        }
    if (
        (canonical_registry_coverage or {}).get("scopes_reviewed", 0) > 0
        and tier_counts[TIER_EXECUTION_EVALUATION_READY] == 0
    ):
        probe = paper_readiness_probe or {}
        probe_present = bool(probe.get("present"))
        rows_considered = _int(probe.get("total_rows_considered"))
        if probe_present and rows_considered > 0:
            blocked_quote = _int(probe.get("rows_blocked_by_missing_quote")) + _int(probe.get("rows_blocked_by_stale_quote"))
            blocked_fee = _int(probe.get("rows_blocked_by_fee"))
            blocked_pair = _int(probe.get("rows_blocked_by_pair_review"))
            return {
                "action": "ADVANCE_PROBE_BLOCKERS_FOR_REVIEWED_SCOPES",
                "reason": (
                    f"audit-paper-readiness-probe considered {rows_considered} reviewed-scope rows: "
                    f"{blocked_quote} blocked by quote, {blocked_fee} by fee metadata, {blocked_pair} by pair review. "
                    "Resolve the highest-count blocker before any market can reach EXECUTION_EVALUATION_READY."
                ),
                "report_only": True,
                "probe_blocker_breakdown": {
                    "rows_considered": rows_considered,
                    "rows_blocked_by_quote": blocked_quote,
                    "rows_blocked_by_fee": blocked_fee,
                    "rows_blocked_by_pair_review": blocked_pair,
                },
            }
        return {
            "action": "RUN_PAPER_READINESS_PROBE",
            "reason": (
                "Canonical reviewed scopes exist but no market is execution-evaluation ready. "
                + (
                    "Refresh audit-paper-readiness-probe to inspect the current saved blockers."
                    if probe_present
                    else "Run audit-paper-readiness-probe to inspect quote, fee, and pair-review blockers."
                )
            ),
            "report_only": True,
        }
    if tier_counts[TIER_EXACT_PAYOFF_REVIEW_READY] > 0 and tier_counts[TIER_EXECUTION_EVALUATION_READY] == 0:
        return {
            "action": "ADD_SAVED_QUOTE_DEPTH_FRESHNESS_AND_FEE_EVIDENCE",
            "reason": "Exact-payoff review rows exist but none are execution-evaluation ready.",
            "report_only": True,
        }
    if family_graduation and (family_graduation.get("total_ready_for_human_registry_review_count") or 0) > 0:
        best_family = family_graduation.get("best_family_for_human_review")
        groups = family_graduation.get("total_registry_proposal_group_count") or 0
        ready_count = family_graduation.get("total_ready_for_human_registry_review_count") or 0
        return {
            "action": "REVIEW_FAMILY_GRADUATION_PROPOSALS",
            "reason": (
                f"plan-family-graduation reports name {ready_count} typed-ready rows across "
                f"{groups} coarse canonical registry scopes (best_family={best_family}). "
                "A human reviewer must validate each scope's source URL and evidence before "
                "any row can advance toward exact-payoff review. Registry proposals are not trust."
            ),
            "report_only": True,
            "registry_proposal_is_trust": False,
        }
    if tier_counts[TIER_FAMILY_TYPED_REVIEW_READY] > 0:
        return {
            "action": "REVIEW_CANONICAL_REGISTRY_OR_SOURCE_URLS",
            "reason": "Family typed-key rows exist and need explicit source or registry evidence before exact-payoff review.",
            "report_only": True,
        }
    if cross_platform.get("triage_row_count", 0) > 0:
        return {
            "action": "REVIEW_CROSS_PLATFORM_TRIAGE_BLOCKERS",
            "reason": "Cross-platform diagnostic rows exist but are still blocked from paper review.",
            "report_only": True,
        }
    if highlight.get("sports_source_ready_count", 0) > 0:
        return {
            "action": "ADD_TYPED_KEYS_FOR_SOURCE_READY_SPORTS",
            "reason": "Sports rows have source evidence but still need typed exact-key and evaluator metadata work.",
            "report_only": True,
        }
    return {
        "action": "REGENERATE_SAVED_DIAGNOSTIC_REPORTS",
        "reason": "No higher-confidence review lane is available from the saved status inputs.",
        "report_only": True,
    }


def _existing_evaluator_paper_counts(
    input_dir: Path,
    *,
    audit: Any = None,
    archive_plan: Any = None,
    archive_applied: Any = None,
) -> dict[str, Any]:
    count = 0
    unique_keys: set[str] = set()
    evidence_files: list[str] = []
    applied_status = _archive_applied_status(archive_applied)
    if input_dir.exists():
        for path in sorted(input_dir.rglob("*.json")):
            if _is_under_archive(path, input_dir):
                continue
            payload, warning = _load_json(path)
            if warning is not None:
                continue
            found_keys = _paper_row_keys_from_payload(payload)
            found = len(found_keys)
            if found > 0:
                count += found
                unique_keys.update(found_keys)
                evidence_files.append(str(path))
    audit_summary = audit.get("summary") if isinstance(audit, dict) else None
    current_needs_review = (
        _int(audit_summary.get("current_needs_review_count"))
        if isinstance(audit_summary, dict)
        else count
    )
    audit_recommendation = audit_summary.get("recommended_next_action") if isinstance(audit_summary, dict) else None
    result = {
        "count": count,
        "unique_count": len(unique_keys),
        "evidence_files": evidence_files,
        "current_needs_review_count": current_needs_review,
        "audit_recommended_next_action": audit_recommendation,
        "audit_present": isinstance(audit, dict),
        "archive_plan_present": isinstance(archive_plan, dict),
        "archive_plan_archive_candidate_count": _archive_plan_status(archive_plan)["archive_candidate_count"],
        "archive_applied_present": applied_status["present"],
        "archive_applied_move_count": applied_status["applied_move_count"],
        "archive_applied_noop_count": applied_status["noop_move_count"],
        "archive_applied_covers_plan": applied_status["covers_stale_archive_plan"],
        # Only assert a positive evaluator report when the forensic audit confirms
        # at least one current-needs-review row. Raw walk counts can be polluted
        # by stale or fake-edge artifacts that the audit has already cleared.
        "positive_evaluator_report_present": current_needs_review > 0,
    }
    if isinstance(audit_summary, dict):
        result["audit_stale_count"] = _int(audit_summary.get("stale_count"))
        result["audit_total_paper_candidate_rows_found"] = _int(audit_summary.get("total_paper_candidate_rows_found"))
        result["audit_likely_fake_or_blocked_count"] = _int(audit_summary.get("likely_fake_or_blocked_count"))
    if current_needs_review > 0:
        result["positive_action"] = EVALUATOR_ACTION
    return result


def _paper_row_keys_from_payload(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    source = str(payload.get("source") or "").lower()
    if source in {
        REPORT_SOURCE,
        "cross_platform_opportunity_triage_v1",
        "standardized_family_candidates_v1",
        "stale_report_archive_plan_v1",
        "stale_report_archive_applied_v1",
    }:
        return set()
    output: set[str] = set()
    for _, row in _walk_dict_rows(payload):
        if row.get("action") != EVALUATOR_ACTION:
            continue
        candidate_id = row.get("candidate_id")
        if candidate_id:
            output.add(str(candidate_id))
        else:
            output.add(json.dumps(row, sort_keys=True, default=str)[:240])
    return output


def _walk_dict_rows(payload: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(payload, dict):
        rows.append((path, payload))
        for key, value in payload.items():
            rows.extend(_walk_dict_rows(value, f"{path}.{key}"))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            rows.extend(_walk_dict_rows(value, f"{path}[{index}]"))
    return rows


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_report_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_report_invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_report_read_error:{type(exc).__name__}"}


def _is_under_archive(path: Path, input_dir: Path) -> bool:
    try:
        relative = path.resolve().relative_to(input_dir.resolve())
    except (OSError, ValueError):
        return False
    return len(relative.parts) > 0 and relative.parts[0] == "archive"


def _list_value(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return [row for row in payload[key] if isinstance(row, dict)]
    return []


def _tier_at_least_typed(tier: str) -> bool:
    return tier in {
        TIER_FAMILY_TYPED_REVIEW_READY,
        TIER_SETTLEMENT_SOURCE_REVIEW_READY,
        TIER_EXACT_PAYOFF_REVIEW_READY,
        TIER_EXECUTION_EVALUATION_READY,
    }


def _tier_at_least_source(tier: str) -> bool:
    return tier in {
        TIER_SETTLEMENT_SOURCE_REVIEW_READY,
        TIER_EXACT_PAYOFF_REVIEW_READY,
        TIER_EXECUTION_EVALUATION_READY,
    }


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"family": key, "count": value} for key, value in counter.most_common()]


def _markdown_count_table(rows: list[dict[str, Any]], label: str) -> list[str]:
    if not rows:
        return ["(none)"]
    output = [f"| {label.title()} | Count |", "|---|---:|"]
    for row in rows:
        output.append(f"| {_md(row.get(label))} | {_md(row.get('count'))} |")
    return output


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safety_block() -> dict[str, bool]:
    return {
        "saved_files_only": True,
        "live_fetch_attempted": False,
        "execution_or_order_logic_added": False,
        "account_or_auth_logic_added": False,
        "tradability_claimed": False,
        "paper_action_created": False,
        "affects_evaluator_gates": False,
    }


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")
