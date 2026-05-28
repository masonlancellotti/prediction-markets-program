from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "existing_paper_candidate_audit_v1"

CURRENT_NEEDS_REVIEW = "CURRENT_NEEDS_REVIEW"
STALE_SOURCE_FILE = "STALE_SOURCE_FILE"
MISSING_SOURCE_CONTEXT = "MISSING_SOURCE_CONTEXT"
FAILS_CURRENT_NORMALIZED_GATES = "FAILS_CURRENT_NORMALIZED_GATES"
SNAPSHOT_MISMATCH_RISK = "SNAPSHOT_MISMATCH_RISK"
POSSIBLE_FAKE_EDGE = "POSSIBLE_FAKE_EDGE"
DUPLICATE_ROW = "DUPLICATE_ROW"

REVIEW_EXISTING_EVALUATOR_OUTPUT = "REVIEW_EXISTING_EVALUATOR_OUTPUT"
ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS = "ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS"
NO_EXISTING_ROWS_FOUND = "NO_EXISTING_ROWS_FOUND"

EVALUATOR_ACTION = "PAPER" + "_CANDIDATE"
STALE_AFTER = timedelta(hours=24)

SKIPPED_SOURCES = {
    REPORT_SOURCE,
    "relative_value_ops_status_v1",
    "cross_platform_opportunity_triage_v1",
    "standardized_family_candidates_v1",
    "venue_metadata_coverage_audit_v1",
    "settlement_evidence_burden_v1",
    "normalized_market_contract_v0",
    "normalized_market_contract_v0_coverage",
    "mlb_world_series_revival_status_v1",
    "stale_report_archive_plan_v1",
}


def build_existing_paper_candidate_audit_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    current = _load_current_context(input_dir)
    findings, summary_refs, warnings = _scan_existing_rows(input_dir=input_dir, generated_at=generated, current=current)
    summary = _summary(findings, summary_refs, warnings)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "summary": summary,
        "candidates": findings,
        "summary_counter_references": summary_refs,
        "current_report_context": {
            "execution_evaluation_ready_count": current["execution_evaluation_ready_count"],
            "normalized_report_present": current["normalized_report_present"],
            "settlement_evidence_burden_present": current["settlement_evidence_burden_present"],
            "venue_metadata_coverage_present": current["venue_metadata_coverage_present"],
            "standardized_family_candidates_present": current["standardized_family_candidates_present"],
            "cross_platform_opportunity_triage_present": current["cross_platform_opportunity_triage_present"],
        },
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_rows_created": False,
            "evaluator_logic_modified": False,
            "affects_evaluator_gates": False,
        },
    }


def write_existing_paper_candidate_audit_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_existing_paper_candidate_audit_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_existing_paper_candidate_audit_markdown(report), encoding="utf-8")
    return report


def render_existing_paper_candidate_audit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Existing Evaluator Candidate Forensic Audit",
        "",
        "Saved-file-only forensic audit. It reads existing evaluator output and does not create candidates.",
        "",
        "## Summary",
        "",
        f"- total_row_level_hits: `{summary.get('total_paper_candidate_rows_found', 0)}`",
        f"- unique_candidate_count: `{summary.get('unique_candidate_count', 0)}`",
        f"- current_needs_review_count: `{summary.get('current_needs_review_count', 0)}`",
        f"- stale_count: `{summary.get('stale_count', 0)}`",
        f"- likely_fake_or_blocked_count: `{summary.get('likely_fake_or_blocked_count', 0)}`",
        f"- summary_counter_reference_count: `{summary.get('summary_counter_reference_count', 0)}`",
        f"- recommended_next_action: `{summary.get('recommended_next_action')}`",
        "",
        "## Source Files",
        "",
    ]
    source_files = summary.get("top_source_files") or []
    if source_files:
        lines.extend(["| Source file | Row count |", "|---|---:|"])
        for row in source_files:
            lines.append(f"| {_md(row.get('source_file'))} | {_md(row.get('count'))} |")
    else:
        lines.append("(none)")
    lines.extend(["", "## Candidate Rows", "", "| Candidate | Source | Classification | Markets | Blockers |", "|---|---|---|---|---|"])
    for row in report.get("candidates") or []:
        markets = ", ".join(
            f"{market.get('venue')}:{market.get('market_id') or market.get('ticker')}"
            for market in row.get("markets_involved") or []
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("candidate_id")),
                    _md(row.get("source_file")),
                    _md(row.get("primary_classification")),
                    _md(markets),
                    _md("; ".join(row.get("blockers") or [])),
                ]
            )
            + " |"
        )
    if not report.get("candidates"):
        lines.append("| (none) | (none) | (none) | (none) | (none) |")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- execution_or_order_logic_added: `false`",
            "- paper_candidate_rows_created: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_current_context(input_dir: Path) -> dict[str, Any]:
    normalized = _load_optional_json(input_dir / "normalized_markets_v0.json")
    burden = _load_optional_json(input_dir / "settlement_evidence_burden.json")
    venue_metadata = _load_optional_json(input_dir / "venue_metadata_coverage.json")
    standardized = _load_optional_json(input_dir / "standardized_family_candidates.json")
    triage = _load_optional_json(input_dir / "cross_platform_opportunity_triage.json")
    normalized_rows = _list_value(normalized, "normalized_markets")
    burden_rows = _list_value(burden, "markets")
    return {
        "normalized_report_present": isinstance(normalized, dict),
        "settlement_evidence_burden_present": isinstance(burden, dict),
        "venue_metadata_coverage_present": isinstance(venue_metadata, dict),
        "standardized_family_candidates_present": isinstance(standardized, dict),
        "cross_platform_opportunity_triage_present": isinstance(triage, dict),
        "execution_evaluation_ready_count": _execution_ready_count(burden),
        "normalized_by_key": _normalized_by_key(normalized_rows),
        "burden_by_key": _burden_by_key(burden_rows),
        "standardized": standardized if isinstance(standardized, dict) else {},
        "triage": triage if isinstance(triage, dict) else {},
    }


def _scan_existing_rows(
    *,
    input_dir: Path,
    generated_at: datetime,
    current: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    summary_refs: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    if not input_dir.exists():
        warnings.append({"source_file": str(input_dir), "reason_code": "input_dir_missing", "blocker": "saved_input_directory_missing"})
        return findings, summary_refs, warnings

    for path in sorted(input_dir.rglob("*.json")):
        payload, warning = _load_json(path)
        if warning is not None:
            warnings.append(warning)
            continue
        if isinstance(payload, dict) and payload.get("source") in SKIPPED_SOURCES:
            continue
        mtime = _file_mtime(path)
        file_generated_at = _parse_dt((payload or {}).get("generated_at") if isinstance(payload, dict) else None)
        root_context = {
            "source": payload.get("source") if isinstance(payload, dict) else None,
            "generated_at": file_generated_at.isoformat() if file_generated_at else None,
            "schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
        }
        for ref in _positive_summary_references(payload, source_file=path, root_context=root_context):
            summary_refs.append(ref)
        for row_path, row in _walk_dict_rows(payload):
            if row.get("action") != EVALUATOR_ACTION:
                continue
            finding = _candidate_finding(
                row,
                row_path=row_path,
                source_file=path,
                source_mtime=mtime,
                source_generated_at=file_generated_at,
                root_context=root_context,
                generated_at=generated_at,
                current=current,
            )
            duplicate_key = finding["dedupe_key"]
            if duplicate_key in seen:
                finding["classifications"].append(DUPLICATE_ROW)
                finding["blockers"].append("duplicate_existing_evaluator_row")
                finding["duplicate_of_index"] = seen[duplicate_key]
            else:
                seen[duplicate_key] = len(findings)
            finding["primary_classification"] = _primary_classification(finding["classifications"])
            findings.append(finding)
    return findings, summary_refs, warnings


def _candidate_finding(
    row: dict[str, Any],
    *,
    row_path: str,
    source_file: Path,
    source_mtime: datetime | None,
    source_generated_at: datetime | None,
    root_context: dict[str, Any],
    generated_at: datetime,
    current: dict[str, Any],
) -> dict[str, Any]:
    markets = _markets_involved(row)
    current_gate = _current_gate_status(markets, current)
    relationship_source = _relationship_evidence_source(row)
    same_payoff_source = _same_payoff_evidence_source(row)
    settlement_status = _settlement_evidence_status(row, current_gate)
    quote_status = _quote_depth_freshness_status(row, current_gate)
    fee_status = _fee_model_status(row, current_gate)
    blockers: list[str] = []
    classifications: list[str] = []

    effective_time = source_generated_at or source_mtime
    stale = bool(effective_time and generated_at - effective_time > STALE_AFTER)
    if stale:
        classifications.append(STALE_SOURCE_FILE)
        blockers.append("source_file_or_report_generated_at_older_than_24h")
    if not relationship_source:
        classifications.append(MISSING_SOURCE_CONTEXT)
        blockers.append("missing_relationship_evidence_source")
    if not same_payoff_source:
        classifications.append(MISSING_SOURCE_CONTEXT)
        blockers.append("missing_same_payoff_evidence_source")
    if settlement_status["status"] != "current_source_or_registry_ready":
        classifications.append(MISSING_SOURCE_CONTEXT)
        blockers.extend(settlement_status["blockers"])
    if current_gate["fails_current_gates"]:
        classifications.append(FAILS_CURRENT_NORMALIZED_GATES)
        blockers.extend(current_gate["blockers"])
    if quote_status["status"] != "fresh_quote_depth_present":
        classifications.append(SNAPSHOT_MISMATCH_RISK)
        blockers.extend(quote_status["blockers"])
    if _possible_fake_edge(row, relationship_source, same_payoff_source):
        classifications.append(POSSIBLE_FAKE_EDGE)
        blockers.append("possible_fake_edge_or_old_same_payoff_artifact")
    if not classifications:
        classifications.append(CURRENT_NEEDS_REVIEW)

    gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
    candidate_id = _string_or_none(row.get("candidate_id")) or _dedupe_key(markets, row)
    return {
        "source_file": str(source_file),
        "source_file_modified_time": source_mtime.isoformat() if source_mtime else None,
        "row_path": row_path,
        "candidate_id": candidate_id,
        "dedupe_key": _string_or_none(row.get("candidate_id")) or _dedupe_key(markets, row),
        "detected_existing_evaluator_positive_action": True,
        "markets_involved": markets,
        "venues_involved": sorted({str(market.get("venue")) for market in markets if market.get("venue")}),
        "family_or_universe": _family_or_universe(row, markets),
        "relationship_evidence_source": relationship_source,
        "same_payoff_evidence_source": same_payoff_source,
        "settlement_evidence_status": settlement_status,
        "quote_depth_freshness_status": quote_status,
        "fee_model_status": fee_status,
        "estimated_net_edge": _number_or_none(gap.get("estimated_net_gap")),
        "gross_edge": _number_or_none(gap.get("gross_gap")),
        "evaluator_source": root_context.get("source"),
        "evaluator_generated_at": root_context.get("generated_at"),
        "evaluator_schema_version": root_context.get("schema_version"),
        "classifications": _unique_strings(classifications),
        "primary_classification": CURRENT_NEEDS_REVIEW,
        "blockers": _unique_strings(blockers),
        "warnings": _row_warnings(row),
        "diagnostic_only": True,
        "creates_new_candidate": False,
    }


def _current_gate_status(markets: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    market_statuses: list[dict[str, Any]] = []
    execution_ready_count = int(current.get("execution_evaluation_ready_count") or 0)
    if execution_ready_count <= 0:
        blockers.append("current_settlement_evidence_burden_execution_evaluation_ready_count_zero")
    for market in markets:
        venue = str(market.get("venue") or "")
        ids = [market.get("market_id"), market.get("ticker"), market.get("token_id")]
        normalized = _lookup_current(current["normalized_by_key"], venue, ids)
        burden = _lookup_current(current["burden_by_key"], venue, ids)
        readiness = normalized.get("readiness") if isinstance(normalized.get("readiness"), dict) else {}
        status = {
            "venue": venue,
            "market_id": market.get("market_id"),
            "ticker": market.get("ticker"),
            "normalized_found": bool(normalized),
            "burden_found": bool(burden),
            "normalized_evaluator_metadata_ready": bool(readiness.get("evaluator_metadata_ready")),
            "normalized_quote_depth_ready": bool(readiness.get("quote_depth_ready")),
            "settlement_review_tier": burden.get("review_readiness_tier"),
            "burden_blockers": list(burden.get("blockers") or []),
        }
        if not normalized:
            blockers.append(f"current_normalized_market_missing:{venue}")
        elif not readiness.get("evaluator_metadata_ready"):
            blockers.append(f"current_normalized_evaluator_metadata_not_ready:{venue}")
        if burden and burden.get("review_readiness_tier") != "EXECUTION_EVALUATION_READY":
            blockers.append(f"current_settlement_burden_not_execution_ready:{venue}")
        elif not burden:
            blockers.append(f"current_settlement_burden_market_missing:{venue}")
        market_statuses.append(status)
    return {
        "fails_current_gates": bool(blockers),
        "blockers": _unique_strings(blockers),
        "market_statuses": market_statuses,
    }


def _settlement_evidence_status(row: dict[str, Any], current_gate: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    relationship = row.get("contract_relationship") if isinstance(row.get("contract_relationship"), dict) else {}
    if relationship.get("same_payoff") is not True:
        blockers.append("same_payoff_not_true_in_existing_row")
    if not _relationship_evidence_source(row):
        blockers.append("relationship_source_missing")
    if any(
        status.get("settlement_review_tier") in {"EXACT_PAYOFF_REVIEW_READY", "EXECUTION_EVALUATION_READY"}
        for status in current_gate.get("market_statuses") or []
    ):
        return {"status": "current_source_or_registry_ready", "blockers": blockers}
    blockers.append("current_reports_do_not_show_exact_or_execution_settlement_readiness_for_involved_markets")
    return {"status": "missing_or_not_current", "blockers": _unique_strings(blockers)}


def _quote_depth_freshness_status(row: dict[str, Any], current_gate: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    row_ready = True
    for market in _markets_involved(row):
        if _number_or_none(market.get("best_bid")) is None or _number_or_none(market.get("best_ask")) is None:
            row_ready = False
            blockers.append(f"existing_row_missing_top_of_book:{market.get('venue')}")
        if not market.get("quote_captured_at"):
            row_ready = False
            blockers.append(f"existing_row_missing_quote_timestamp:{market.get('venue')}")
    current_ready = all(status.get("normalized_quote_depth_ready") for status in current_gate.get("market_statuses") or [])
    if not current_ready:
        blockers.append("current_normalized_quote_depth_not_ready_for_all_legs")
    return {
        "status": "fresh_quote_depth_present" if row_ready and current_ready else "missing_or_stale_in_current_reports",
        "existing_row_quote_depth_present": row_ready,
        "current_normalized_quote_depth_ready": current_ready,
        "blockers": _unique_strings(blockers),
    }


def _fee_model_status(row: dict[str, Any], current_gate: dict[str, Any]) -> dict[str, Any]:
    gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
    fees_present = _number_or_none(gap.get("kalshi_fee")) is not None and _number_or_none(gap.get("polymarket_fee")) is not None
    return {
        "status": "existing_row_fee_fields_present" if fees_present else "missing_fee_fields",
        "existing_row_fee_fields_present": fees_present,
        "current_gate_blockers": [blocker for blocker in current_gate.get("blockers") or [] if "fee" in blocker],
    }


def _markets_involved(row: dict[str, Any]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for venue_key, default_venue in (("polymarket", "polymarket"), ("kalshi", "kalshi")):
        market = row.get(venue_key)
        if isinstance(market, dict):
            markets.append(
                {
                    "venue": market.get("venue") or default_venue,
                    "market_id": market.get("market_id"),
                    "ticker": market.get("ticker") or market.get("market_ticker"),
                    "token_id": market.get("yes_token_id") or market.get("token_id"),
                    "question": market.get("question") or market.get("title"),
                    "best_bid": market.get("best_bid"),
                    "best_ask": market.get("best_ask"),
                    "depth_at_best_bid": market.get("depth_at_best_bid"),
                    "depth_at_best_ask": market.get("depth_at_best_ask"),
                    "quote_captured_at": market.get("quote_captured_at"),
                    "would_enter_side": market.get("would_enter_side"),
                    "would_enter_price": market.get("would_enter_price"),
                    "would_enter_size": market.get("would_enter_size"),
                }
            )
    if markets:
        return markets
    for leg in row.get("legs") or []:
        if isinstance(leg, dict):
            markets.append(
                {
                    "venue": leg.get("venue"),
                    "market_id": leg.get("market_id") or leg.get("id"),
                    "ticker": leg.get("ticker"),
                    "token_id": leg.get("token_id"),
                    "question": leg.get("question") or leg.get("title"),
                }
            )
    return markets


def _relationship_evidence_source(row: dict[str, Any]) -> str | None:
    relationship = row.get("contract_relationship") if isinstance(row.get("contract_relationship"), dict) else {}
    return _string_or_none(relationship.get("source") or row.get("relationship_evidence_source"))


def _same_payoff_evidence_source(row: dict[str, Any]) -> str | None:
    relationship = row.get("contract_relationship") if isinstance(row.get("contract_relationship"), dict) else {}
    board_evidence = relationship.get("same_payoff_board_evidence") if isinstance(relationship.get("same_payoff_board_evidence"), dict) else {}
    return _string_or_none(
        relationship.get("source")
        or board_evidence.get("classifier_version")
        or row.get("same_payoff_evidence_source")
    )


def _possible_fake_edge(row: dict[str, Any], relationship_source: str | None, same_payoff_source: str | None) -> bool:
    gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
    relationship = row.get("contract_relationship") if isinstance(row.get("contract_relationship"), dict) else {}
    if not relationship_source or not same_payoff_source:
        return True
    if relationship and relationship.get("same_payoff") is not True:
        return True
    if _number_or_none(gap.get("settlement_delta_seconds")) and float(gap.get("settlement_delta_seconds")) > 3600:
        return True
    if gap.get("size_unit_warning"):
        return True
    return False


def _family_or_universe(row: dict[str, Any], markets: list[dict[str, Any]]) -> str | None:
    for key in ("family", "universe", "opportunity_class"):
        value = _string_or_none(row.get(key))
        if value:
            return value
    joined = " ".join(str(market.get("ticker") or market.get("market_id") or "") for market in markets).upper()
    if "KXMLB" in joined or "MLB" in joined:
        return "MLB_CHAMPIONSHIP"
    if "KXNBA" in joined or "NBA" in joined:
        return "NBA_CHAMPIONSHIP"
    return None


def _row_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    gap = row.get("gap") if isinstance(row.get("gap"), dict) else {}
    if gap.get("size_unit_warning"):
        warnings.append(str(gap["size_unit_warning"]))
    markouts = row.get("markouts") if isinstance(row.get("markouts"), dict) else {}
    if markouts and all(not item.get("observed_at") for item in markouts.values() if isinstance(item, dict)):
        warnings.append("markout_observations_missing")
    return warnings


def _primary_classification(classifications: list[str]) -> str:
    priority = [
        DUPLICATE_ROW,
        FAILS_CURRENT_NORMALIZED_GATES,
        POSSIBLE_FAKE_EDGE,
        STALE_SOURCE_FILE,
        SNAPSHOT_MISMATCH_RISK,
        MISSING_SOURCE_CONTEXT,
        CURRENT_NEEDS_REVIEW,
    ]
    for item in priority:
        if item in classifications:
            return item
    return CURRENT_NEEDS_REVIEW


def _summary(findings: list[dict[str, Any]], summary_refs: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    unique_keys = {row.get("dedupe_key") for row in findings if row.get("dedupe_key")}
    class_counts = Counter(classification for row in findings for classification in row.get("classifications") or [])
    source_counts = Counter(row.get("source_file") for row in findings)
    likely_fake_or_blocked = sum(
        1
        for row in findings
        if row.get("primary_classification") != CURRENT_NEEDS_REVIEW
        or POSSIBLE_FAKE_EDGE in (row.get("classifications") or [])
        or FAILS_CURRENT_NORMALIZED_GATES in (row.get("classifications") or [])
    )
    recommended = CURRENT_NEEDS_REVIEW if class_counts.get(CURRENT_NEEDS_REVIEW, 0) else ARCHIVE_OR_REGENERATE_FROM_CURRENT_REPORTS
    if not findings:
        recommended = NO_EXISTING_ROWS_FOUND
    return {
        "total_paper_candidate_rows_found": len(findings),
        "unique_candidate_count": len(unique_keys),
        "current_needs_review_count": class_counts.get(CURRENT_NEEDS_REVIEW, 0),
        "stale_count": class_counts.get(STALE_SOURCE_FILE, 0),
        "likely_fake_or_blocked_count": likely_fake_or_blocked,
        "duplicate_row_count": class_counts.get(DUPLICATE_ROW, 0),
        "summary_counter_reference_count": len(summary_refs),
        "summary_counter_reference_total": sum(_int(ref.get("positive_count")) for ref in summary_refs),
        "classification_counts": dict(sorted(class_counts.items())),
        "top_source_files": [{"source_file": str(path), "count": count} for path, count in source_counts.most_common(10)],
        "recommended_next_action": recommended,
        "warning_count": len(warnings),
    }


def _positive_summary_references(payload: Any, *, source_file: Path, root_context: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for path, mapping in _walk_mappings(payload):
        if not isinstance(mapping, dict) or EVALUATOR_ACTION not in mapping:
            continue
        count = _int(mapping.get(EVALUATOR_ACTION))
        if count <= 0:
            continue
        refs.append(
            {
                "source_file": str(source_file),
                "path": path.replace(EVALUATOR_ACTION, "positive_action"),
                "positive_count": count,
                "source": root_context.get("source"),
                "generated_at": root_context.get("generated_at"),
            }
        )
    return refs


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


def _walk_mappings(payload: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    if isinstance(payload, dict):
        rows = [(path, payload)]
        for key, value in payload.items():
            rows.extend(_walk_mappings(value, f"{path}.{key}"))
        return rows
    if isinstance(payload, list):
        rows: list[tuple[str, dict[str, Any]]] = []
        for index, value in enumerate(payload):
            rows.extend(_walk_mappings(value, f"{path}[{index}]"))
        return rows
    return []


def _execution_ready_count(burden: Any) -> int:
    if isinstance(burden, dict):
        tiers = ((burden.get("summary") or {}).get("by_review_readiness_tier") or {})
        if isinstance(tiers, dict):
            return _int(tiers.get("EXECUTION_EVALUATION_READY"))
    return sum(1 for row in _list_value(burden, "markets") if row.get("review_readiness_tier") == "EXECUTION_EVALUATION_READY")


def _normalized_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "")
        for key in ("market_id", "ticker", "token_id", "event_id", "event_ticker", "event_slug"):
            value = row.get(key)
            if value:
                output[(venue, str(value))] = row
    return output


def _burden_by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "")
        for key in ("ticker", "event_id", "event_ticker", "event_slug"):
            value = row.get(key)
            if value:
                output[(venue, str(value))] = row
    return output


def _lookup_current(index: dict[tuple[str, str], dict[str, Any]], venue: str, ids: list[Any]) -> dict[str, Any]:
    for value in ids:
        if value and (venue, str(value)) in index:
            return index[(venue, str(value))]
    return {}


def _load_optional_json(path: Path) -> Any:
    payload, warning = _load_json(path)
    return None if warning else payload


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


def _list_value(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return [row for row in payload[key] if isinstance(row, dict)]
    return []


def _file_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dedupe_key(markets: list[dict[str, Any]], row: dict[str, Any]) -> str:
    market_bits = [
        f"{market.get('venue')}:{market.get('market_id') or market.get('ticker') or market.get('token_id')}"
        for market in markets
    ]
    if market_bits:
        return "|".join(sorted(market_bits))
    return json.dumps(row, sort_keys=True, default=str)[:240]


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_strings(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _string_or_none(value)
        if text is None or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")
