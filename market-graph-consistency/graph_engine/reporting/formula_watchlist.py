from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from graph_engine.formula import MarketFormula, build_formula_diagnostics_report, build_formula_diagnostics_report_from_formulas
from graph_engine.models import GraphSnapshot
from graph_engine.reporting.multi_leg import build_multi_leg_constraints_report
from graph_engine.reporting.schema_validation import DIAGNOSTIC_HINT_ACTIONS, SchemaValidationError, _reject_prohibited_tokens
from graph_engine.reporting.safety import PROHIBITED_REPORT_TOKENS, find_prohibited_report_tokens


BANNER = "Diagnostic-only formula watchlist. Exact payoff must be verified outside this graph report."
REQUEST_BANNER = "Diagnostic-only rel-value investigation request. Does not affect evaluator gates."
PROHIBITED_REQUEST_FIELDS = PROHIBITED_REPORT_TOKENS | {"evaluator_ready"}


def build_formula_watchlist_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    formulas = [build_formula_diagnostics_report(snapshot)["formulas"]][0]
    diagnostics = build_formula_diagnostics_report(snapshot)["formula_diagnostics"]
    report = build_formula_watchlist_report_from_rows(formulas, diagnostics)
    validate_formula_watchlist_report(report)
    return report


def build_formula_watchlist_report_from_formulas(formulas: list[MarketFormula]) -> dict[str, Any]:
    formula_report = build_formula_diagnostics_report_from_formulas(formulas)
    report = build_formula_watchlist_report_from_rows(formula_report["formulas"], formula_report["formula_diagnostics"])
    validate_formula_watchlist_report(report)
    return report


def build_formula_watchlist_report_from_rows(formulas: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    formula_by_id = {formula["market_id"]: formula for formula in formulas}
    rows = [_watchlist_row(diagnostic, formula_by_id) for diagnostic in diagnostics]
    rows = sorted(rows, key=lambda row: (_priority_order(row["max_action_cap"]), row["watchlist_type"], row["source_market_ids"]))
    relation_counts = Counter(row["watchlist_type"] for row in rows)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "watchlist_count": len(rows),
        "counts_by_watchlist_type": dict(sorted(relation_counts.items())),
        "watchlist": rows,
    }
    validate_formula_watchlist_report(report)
    return report


def build_investigation_requests_report(snapshot: GraphSnapshot) -> dict[str, Any]:
    formula_watchlist = build_formula_watchlist_report(snapshot)
    multi_leg = build_multi_leg_constraints_report(snapshot)
    requests = [
        *[_request_from_watchlist(row) for row in formula_watchlist["watchlist"]],
        *[_request_from_multi_leg(row) for row in multi_leg["multi_leg_constraints"]],
    ]
    requests = sorted(requests, key=lambda row: (_priority_order(row["max_action_cap"]), row["request_type"], row["source_market_ids"]))
    counts = Counter(row["request_type"] for row in requests)
    report = {
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "banner": REQUEST_BANNER,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "request_count": len(requests),
        "counts_by_request_type": dict(sorted(counts.items())),
        "investigation_requests": requests,
    }
    validate_investigation_requests_report(report)
    return report


def write_formula_watchlist_reports(
    snapshot: GraphSnapshot,
    watchlist_json_path: Path | str,
    watchlist_md_path: Path | str,
    requests_json_path: Path | str,
    requests_md_path: Path | str,
) -> None:
    watchlist = build_formula_watchlist_report(snapshot)
    requests = build_investigation_requests_report(snapshot)
    validate_formula_watchlist_report(watchlist)
    validate_investigation_requests_report(requests)

    watchlist_json = Path(watchlist_json_path)
    watchlist_md = Path(watchlist_md_path)
    requests_json = Path(requests_json_path)
    requests_md = Path(requests_md_path)
    for path in [watchlist_json, watchlist_md, requests_json, requests_md]:
        path.parent.mkdir(parents=True, exist_ok=True)

    watchlist_json.write_text(json.dumps(watchlist, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    watchlist_md.write_text(render_formula_watchlist_markdown(watchlist), encoding="utf-8")
    requests_json.write_text(json.dumps(requests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    requests_md.write_text(render_investigation_requests_markdown(requests), encoding="utf-8")


def validate_formula_watchlist_report(report: dict[str, Any]) -> None:
    _validate_report_header(report)
    for index, row in enumerate(report.get("watchlist", [])):
        _validate_item(row, f"watchlist[{index}]")
        if "watchlist_type" not in row:
            raise SchemaValidationError(f"watchlist[{index}].watchlist_type is required")
        if "group_key" not in row:
            raise SchemaValidationError(f"watchlist[{index}].group_key is required")


def validate_investigation_requests_report(report: dict[str, Any]) -> None:
    _validate_report_header(report)
    for index, row in enumerate(report.get("investigation_requests", [])):
        _validate_item(row, f"investigation_requests[{index}]")
        if "request_type" not in row:
            raise SchemaValidationError(f"investigation_requests[{index}].request_type is required")
        keys = row.get("requested_exact_keys_to_verify")
        if not isinstance(keys, list) or not keys or not all(isinstance(item, str) and item for item in keys):
            raise SchemaValidationError(f"investigation_requests[{index}].requested_exact_keys_to_verify must contain strings")


def render_formula_watchlist_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Market Graph Formula Watchlist",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Watchlist rows: {report['watchlist_count']}",
        "",
        "| Type | Cap | Markets | Group Key | Reason | Blockers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["watchlist"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["watchlist_type"]),
                    _md(row["max_action_cap"]),
                    _md(", ".join(row["source_market_ids"])),
                    _md(row["group_key"]),
                    _md(row["reason_for_review"]),
                    _md(", ".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_investigation_requests_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Rel-Value Investigation Requests",
        "",
        report["banner"],
        "",
        f"- Diagnostic only: `{str(report['diagnostic_only']).lower()}`",
        f"- Affects evaluator gates: `{str(report['affects_evaluator_gates']).lower()}`",
        f"- Allowed actions: `{', '.join(report['allowed_actions'])}`",
        f"- Requests: {report['request_count']}",
        "",
        "| Type | Cap | Markets | Keys To Verify | Reason | Blockers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["investigation_requests"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row["request_type"]),
                    _md(row["max_action_cap"]),
                    _md(", ".join(row["source_market_ids"])),
                    _md(", ".join(row["requested_exact_keys_to_verify"])),
                    _md(row["reason_for_review"]),
                    _md(", ".join(row["blockers"]) or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _watchlist_row(diagnostic: dict[str, Any], formula_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    market_ids = list(diagnostic["market_ids"])
    formulas = [formula_by_id[market_id] for market_id in market_ids if market_id in formula_by_id]
    return {
        "watchlist_id": f"watchlist:{diagnostic['comparison_id']}",
        "watchlist_type": _watchlist_type(diagnostic["formula_relation"]),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": diagnostic["max_action_cap"],
        "source_market_ids": market_ids,
        "group_key": _group_key(formulas),
        "formula_relation": diagnostic["formula_relation"],
        "requested_exact_keys_to_verify": _keys_for_formula_relation(diagnostic["formula_relation"], formulas),
        "blockers": list(diagnostic["blockers"]),
        "reason_for_review": diagnostic["review_reason"],
    }


def _request_from_watchlist(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": f"request:{row['watchlist_id']}",
        "request_type": row["watchlist_type"],
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": row["max_action_cap"],
        "source_market_ids": list(row["source_market_ids"]),
        "requested_exact_keys_to_verify": list(row["requested_exact_keys_to_verify"]),
        "blockers": list(row["blockers"]),
        "reason_for_review": row["reason_for_review"],
    }


def _request_from_multi_leg(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": f"request:{row['constraint_id']}",
        "request_type": "complex_multi_leg_group",
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "allowed_actions": DIAGNOSTIC_HINT_ACTIONS,
        "max_action_cap": row["max_action_cap"],
        "source_market_ids": list(row["market_ids"]),
        "requested_exact_keys_to_verify": [
            "event_scope",
            "settlement_source",
            "settlement_window",
            "outcome_partition_rules",
            "cancellation_rules",
        ],
        "blockers": list(row["blockers"]),
        "reason_for_review": row["review_reason"],
    }


def _watchlist_type(relation: str) -> str:
    return {
        "typed_formula_match_review_only": "possible_exact_typed_formula_match_review_only",
        "threshold_ladder": "threshold_ladder_family",
        "overlap_not_identical": "overlapping_fed_ranges",
        "ambiguous_not_exact": "ambiguous_source_or_date_mismatch",
        "parse_blocked": "parse_blocked_high_similarity",
        "disjoint_ranges": "range_family_review",
    }.get(relation, "formula_review")


def _keys_for_formula_relation(relation: str, formulas: list[dict[str, Any]]) -> list[str]:
    base = ["family", "source"]
    if any(formula.get("asset") for formula in formulas):
        base.extend(["asset", "date", "settlement_time", "comparator", "threshold", "units"])
    elif any(formula.get("subject") == "FED_FUNDS" for formula in formulas):
        base.extend(["subject", "meeting_date", "settlement_time", "lower_bound", "upper_bound", "units"])
    else:
        base.extend(["subject", "team", "location", "date"])
    if relation in {"ambiguous_not_exact", "parse_blocked"}:
        base.extend(["resolution_criteria", "venue_rules"])
    return sorted(set(base))


def _group_key(formulas: list[dict[str, Any]]) -> str:
    if not formulas:
        return "unknown"
    first = formulas[0]
    parts = [
        first.get("family"),
        first.get("subject") or first.get("asset") or first.get("team") or first.get("location"),
        first.get("source"),
        first.get("date") or first.get("settlement_time") or first.get("meeting_date"),
        first.get("comparator"),
        _range_or_threshold(first),
        _venues(formulas),
    ]
    return "|".join(str(part) for part in parts if part not in {None, ""})


def _range_or_threshold(formula: dict[str, Any]) -> str | None:
    if formula.get("threshold") is not None:
        return f"threshold={formula['threshold']}"
    if formula.get("lower_bound") is not None or formula.get("upper_bound") is not None:
        return f"range={formula.get('lower_bound')}:{formula.get('upper_bound')}"
    return None


def _venues(formulas: list[dict[str, Any]]) -> str:
    venues = sorted({str(formula["market_id"]).split(":", 1)[0] for formula in formulas})
    return "venues=" + ",".join(venues)


def _priority_order(action: str) -> int:
    return {"MANUAL_REVIEW": 0, "WATCH": 1}.get(action, 2)


def _validate_report_header(report: dict[str, Any]) -> None:
    _reject_prohibited_tokens(report)
    _reject_extra_prohibited_terms(report)
    if report.get("diagnostic_only") is not True:
        raise SchemaValidationError("report must be diagnostic_only")
    if report.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError("report must not affect evaluator gates")
    if report.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError("allowed_actions must be WATCH and MANUAL_REVIEW only")


def _validate_item(row: dict[str, Any], path: str) -> None:
    for key in row:
        if key in PROHIBITED_REQUEST_FIELDS:
            raise SchemaValidationError(f"{path}.{key} is prohibited")
    if row.get("diagnostic_only") is not True:
        raise SchemaValidationError(f"{path}.diagnostic_only must be true")
    if row.get("affects_evaluator_gates") is not False:
        raise SchemaValidationError(f"{path}.affects_evaluator_gates must be false")
    if row.get("allowed_actions") != DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.allowed_actions must be WATCH and MANUAL_REVIEW only")
    if row.get("max_action_cap") not in DIAGNOSTIC_HINT_ACTIONS:
        raise SchemaValidationError(f"{path}.max_action_cap must be WATCH or MANUAL_REVIEW")
    if not isinstance(row.get("source_market_ids"), list) or not row["source_market_ids"]:
        raise SchemaValidationError(f"{path}.source_market_ids must contain market ids")
    if not isinstance(row.get("blockers"), list):
        raise SchemaValidationError(f"{path}.blockers must be a list")
    if not isinstance(row.get("reason_for_review"), str) or not row["reason_for_review"]:
        raise SchemaValidationError(f"{path}.reason_for_review must be a non-empty string")
    _reject_extra_prohibited_terms(row)


def _reject_extra_prohibited_terms(payload: Any) -> None:
    findings = find_prohibited_report_tokens(payload)
    if findings:
        raise SchemaValidationError(f"prohibited investigation term present: {sorted(set(findings))}")


def _contains_extra_token(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(token in normalized.split("_") for token in PROHIBITED_REQUEST_FIELDS)


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
