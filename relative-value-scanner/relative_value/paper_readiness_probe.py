from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REPORT_SOURCE = "paper_readiness_probe_v1"

TIER_SETTLEMENT_SOURCE_REVIEW_READY = "SETTLEMENT_SOURCE_REVIEW_READY"
TIER_EXACT_PAYOFF_REVIEW_READY = "EXACT_PAYOFF_REVIEW_READY"
TIER_EXECUTION_EVALUATION_READY = "EXECUTION_EVALUATION_READY"
TIERS_TO_CONSIDER = {
    TIER_SETTLEMENT_SOURCE_REVIEW_READY,
    TIER_EXACT_PAYOFF_REVIEW_READY,
    TIER_EXECUTION_EVALUATION_READY,
}
TIER_RANK = {
    TIER_SETTLEMENT_SOURCE_REVIEW_READY: 1,
    TIER_EXACT_PAYOFF_REVIEW_READY: 2,
    TIER_EXECUTION_EVALUATION_READY: 3,
}


def build_paper_readiness_probe_report(
    *,
    input_dir: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    warnings: list[dict[str, Any]] = []

    burden_payload, warning = _load_json(input_dir / "settlement_evidence_burden.json")
    if warning is not None:
        warnings.append(warning)
    coverage_payload, warning = _load_json(input_dir / "canonical_registry_coverage.json")
    if warning is not None:
        warnings.append(warning)
    normalized_payload, warning = _load_json(input_dir / "normalized_markets_v0.json")
    if warning is not None:
        warnings.append(warning)

    graduation_reports: list[dict[str, Any]] = []
    for filename in ("family_graduation_crypto.json", "family_graduation_fed.json"):
        payload, warning = _load_json(input_dir / filename)
        if warning is not None:
            warnings.append(warning)
        elif isinstance(payload, dict):
            graduation_reports.append(payload)

    reviewed_scopes = _reviewed_scope_rows(coverage_payload)
    scope_by_key = _graduation_scope_index(graduation_reports)
    normalized_by_key = _normalized_index(_list_value(normalized_payload, "normalized_markets"))

    rows = []
    for row in _list_value(burden_payload, "markets"):
        tier = str(row.get("review_readiness_tier") or "")
        if tier not in TIERS_TO_CONSIDER:
            continue
        graduation = _lookup_by_identity(scope_by_key, row)
        scope_key = _scope_key_from_graduation(graduation) or _string_or_none(row.get("canonical_scope_key"))
        if not scope_key or scope_key not in reviewed_scopes:
            continue
        normalized = _lookup_by_identity(normalized_by_key, row)
        rows.append(_probe_row(row, graduation=graduation, normalized=normalized, scope=reviewed_scopes[scope_key]))

    summary = _summary(rows, reviewed_scopes, warnings)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "summary": summary,
        "rows": rows,
        "closest_rows_to_execution": _closest_rows(rows),
        "next_operator_actions": _next_operator_actions(summary),
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "paper_ready_asserted": False,
            "affects_evaluator_gates": False,
        },
    }


def write_paper_readiness_probe_files(
    *,
    input_dir: Path,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_paper_readiness_probe_report(input_dir=input_dir, generated_at=generated_at)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_paper_readiness_probe_markdown(report), encoding="utf-8")
    return report


def render_paper_readiness_probe_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Paper Readiness Probe",
        "",
        "Saved-file-only diagnostic. This report explains what is still missing before execution readiness; it never asserts paper readiness.",
        "",
        "## Summary",
        "",
        f"- total_rows_considered: `{summary.get('total_rows_considered', 0)}`",
        f"- reviewed_scope_count: `{summary.get('reviewed_scope_count', 0)}`",
        f"- rows_blocked_by_stale_quote: `{summary.get('rows_blocked_by_stale_quote', 0)}`",
        f"- rows_blocked_by_missing_quote: `{summary.get('rows_blocked_by_missing_quote', 0)}`",
        f"- rows_blocked_by_fee: `{summary.get('rows_blocked_by_fee', 0)}`",
        f"- rows_blocked_by_pair_review: `{summary.get('rows_blocked_by_pair_review', 0)}`",
        "",
        "## Closest Rows To Execution",
        "",
    ]
    closest = report.get("closest_rows_to_execution") or []
    if closest:
        lines.extend(["| Tier | Family | Venue | Market | Scope | Required fields |", "|---|---|---|---|---|---|"])
        for row in closest[:20]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(row.get("current_tier")),
                        _md(row.get("family")),
                        _md(row.get("venue")),
                        _md(row.get("market_id") or row.get("ticker")),
                        _md(row.get("canonical_scope")),
                        _md(", ".join(row.get("required_fields_to_advance_one_tier") or [])),
                    ]
                )
                + " |"
            )
    else:
        lines.append("(none)")
    lines.extend(["", "## Next Operator Actions", ""])
    for action in report.get("next_operator_actions") or []:
        lines.append(f"- `{action.get('action')}`: {action.get('reason')}")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- paper_candidate_emitted: `false`",
            "- paper_ready_asserted: `false`",
            "- affects_evaluator_gates: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _probe_row(
    row: dict[str, Any],
    *,
    graduation: dict[str, Any] | None,
    normalized: dict[str, Any] | None,
    scope: dict[str, Any],
) -> dict[str, Any]:
    readiness = normalized.get("readiness") if isinstance(normalized, dict) and isinstance(normalized.get("readiness"), dict) else {}
    quote_freshness = row.get("quote_freshness_status") if isinstance(row.get("quote_freshness_status"), dict) else {}
    quote_blocker = _string_or_none(quote_freshness.get("blocker"))
    quote_depth_ready = readiness.get("quote_depth_ready") is True
    fee_metadata_ready = readiness.get("fee_metadata_ready") is True
    blockers = {str(blocker) for blocker in row.get("blockers") or []}
    projection = graduation.get("projection") if isinstance(graduation, dict) and isinstance(graduation.get("projection"), dict) else {}
    projected_blockers = {str(blocker) for blocker in projection.get("projected_blockers_if_registry_or_source_added") or []}

    missing_quote_depth = not quote_depth_ready or "missing_quote_depth_for_execution" in blockers
    missing_fee_metadata = not fee_metadata_ready or "missing_fee_metadata_for_execution" in blockers
    missing_pair_review = _missing_pair_review(row, graduation)
    required = _required_fields(
        quote_blocker=quote_blocker,
        missing_quote_depth=missing_quote_depth,
        missing_fee_metadata=missing_fee_metadata,
        missing_pair_review=missing_pair_review,
        projected_blockers=projected_blockers,
    )
    return {
        "current_tier": row.get("review_readiness_tier"),
        "family": row.get("family"),
        "venue": row.get("venue"),
        "event_id": row.get("event_id"),
        "event_ticker": row.get("event_ticker"),
        "market_id": row.get("market_id"),
        "ticker": row.get("ticker"),
        "canonical_scope": scope.get("scope_key"),
        "canonical_registry_entry_id": scope.get("registry_entry_id_if_matched"),
        "quote_freshness_status": quote_freshness,
        "quote_freshness_blocker": quote_blocker,
        "missing_quote_depth_for_execution": missing_quote_depth,
        "missing_fee_metadata_for_execution": missing_fee_metadata,
        "missing_relationship_or_pair_review": missing_pair_review,
        "required_fields_to_advance_one_tier": required,
        "blocker_count": len(required),
        "diagnostic_only": True,
        "affects_evaluator_gates": False,
        "paper_ready": False,
    }


def _required_fields(
    *,
    quote_blocker: str | None,
    missing_quote_depth: bool,
    missing_fee_metadata: bool,
    missing_pair_review: bool,
    projected_blockers: set[str],
) -> list[str]:
    required: list[str] = []
    if quote_blocker == "stale_quote":
        required.append("fresh_quote_captured_at_under_staleness_policy")
    elif quote_blocker == "missing_quote_captured_at":
        required.append("quote_depth.captured_at")
    elif quote_blocker:
        required.append(f"quote_freshness_fix:{quote_blocker}")
    if missing_quote_depth:
        required.append("saved_orderbook_depth")
    if missing_fee_metadata:
        required.append("reviewed_fee_metadata")
    if missing_pair_review or "pair_review_not_performed" in projected_blockers:
        required.append("strict_relationship_or_pair_review")
    return sorted(dict.fromkeys(required))


def _missing_pair_review(row: dict[str, Any], graduation: dict[str, Any] | None) -> bool:
    # Pair review is currently NEVER auto-performed by saved-file reports; it
    # always requires a manual reviewer. Until a saved row explicitly carries
    # ``relationship_or_pair_review=True`` or ``trusted_relationships_attached=
    # True``, treat pair review as missing. The graduation projection is read
    # only to keep the blocker visible in downstream diagnostics; it never
    # downgrades the missing flag.
    if row.get("relationship_or_pair_review") is True or row.get("trusted_relationships_attached") is True:
        return False
    return True


def _summary(rows: list[dict[str, Any]], reviewed_scopes: dict[str, dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    blocker_counts = Counter()
    for row in rows:
        if row.get("quote_freshness_blocker"):
            blocker_counts[str(row["quote_freshness_blocker"])] += 1
        if row.get("missing_quote_depth_for_execution"):
            blocker_counts["missing_quote_depth_for_execution"] += 1
        if row.get("missing_fee_metadata_for_execution"):
            blocker_counts["missing_fee_metadata_for_execution"] += 1
        if row.get("missing_relationship_or_pair_review"):
            blocker_counts["missing_relationship_or_pair_review"] += 1
    return {
        "total_rows_considered": len(rows),
        "reviewed_scope_count": len(reviewed_scopes),
        "rows_blocked_by_stale_quote": sum(1 for row in rows if row.get("quote_freshness_blocker") == "stale_quote"),
        "rows_blocked_by_missing_quote": sum(
            1
            for row in rows
            if row.get("quote_freshness_blocker") == "missing_quote_captured_at"
            or row.get("missing_quote_depth_for_execution")
        ),
        "rows_blocked_by_fee": sum(1 for row in rows if row.get("missing_fee_metadata_for_execution")),
        "rows_blocked_by_pair_review": sum(1 for row in rows if row.get("missing_relationship_or_pair_review")),
        "top_blockers": [{"blocker": key, "count": count} for key, count in blocker_counts.most_common(10)],
        "paper_ready_count": 0,
        "warning_count": len(warnings),
    }


def _closest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _int(row.get("blocker_count")),
            -TIER_RANK.get(str(row.get("current_tier")), 0),
            str(row.get("family") or ""),
            str(row.get("venue") or ""),
            str(row.get("market_id") or row.get("ticker") or ""),
        ),
    )[:25]


def _next_operator_actions(summary: dict[str, Any]) -> list[dict[str, Any]]:
    if summary.get("reviewed_scope_count", 0) <= 0:
        return [
            {
                "action": "REVIEW_CANONICAL_REGISTRY_COVERAGE",
                "reason": "No reviewed canonical scopes were available for the probe.",
            }
        ]
    if summary.get("total_rows_considered", 0) <= 0:
        return [
            {
                "action": "REGENERATE_BURDEN_WITH_REVIEWED_REGISTRY_OR_SOURCE_INPUTS",
                "reason": "Reviewed scopes exist, but no saved burden rows at source-review or higher mapped to those scopes.",
            }
        ]
    actions = []
    if summary.get("rows_blocked_by_stale_quote", 0) > 0:
        actions.append(
            {
                "action": "ADD_FRESH_SAVED_QUOTE_CAPTURES",
                "reason": "Rows have reviewed scope evidence but stale quote timestamps.",
            }
        )
    if summary.get("rows_blocked_by_missing_quote", 0) > 0:
        actions.append(
            {
                "action": "ADD_SAVED_ORDERBOOK_DEPTH_AND_CAPTURE_TIMES",
                "reason": "Rows still lack saved quote depth or captured_at evidence.",
            }
        )
    if summary.get("rows_blocked_by_fee", 0) > 0:
        actions.append(
            {
                "action": "ADD_REVIEWED_FEE_METADATA",
                "reason": "Rows still lack reviewed fee metadata for execution readiness.",
            }
        )
    if summary.get("rows_blocked_by_pair_review", 0) > 0:
        actions.append(
            {
                "action": "RUN_STRICT_RELATIONSHIP_OR_PAIR_REVIEW",
                "reason": "Rows still need strict pair review before any evaluator path.",
            }
        )
    return actions or [
        {
            "action": "MANUAL_REVIEW_REMAINING_EXECUTION_GATES",
            "reason": "No automated missing field was detected, but this probe does not assert paper readiness.",
        }
    ]


def _reviewed_scope_rows(payload: Any) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict) or payload.get("source") != "canonical_registry_coverage_v1":
        return output
    for row in payload.get("scopes") or []:
        if not isinstance(row, dict):
            continue
        if _int(row.get("registry_match_count")) <= 0:
            continue
        scope_key = _string_or_none(row.get("scope_key"))
        if scope_key:
            output[scope_key] = row
    return output


def _graduation_scope_index(reports: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for report in reports:
        if report.get("source") != "family_graduation_plan_v1":
            continue
        for row in report.get("rows") or []:
            if not isinstance(row, dict):
                continue
            for key in _identity_keys(row):
                index.setdefault(key, row)
    return index


def _normalized_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        for key in _identity_keys(row):
            index.setdefault(key, row)
    return index


def _lookup_by_identity(index: dict[tuple[str, str], dict[str, Any]], row: dict[str, Any]) -> dict[str, Any] | None:
    for key in _identity_keys(row):
        if key in index:
            return index[key]
    return None


def _identity_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    venue = str(row.get("venue") or "").strip().lower()
    if not venue:
        return []
    values = [
        row.get("ticker"),
        row.get("market_id"),
        row.get("token_id"),
    ]
    keys = []
    for value in values:
        text = _string_or_none(value)
        if text:
            keys.append((venue, text))
    return keys


def _scope_key_from_graduation(row: dict[str, Any] | None) -> str | None:
    if not isinstance(row, dict):
        return None
    proposal = row.get("registry_proposal") if isinstance(row.get("registry_proposal"), dict) else {}
    scope = proposal.get("recommended_registry_scope") if isinstance(proposal.get("recommended_registry_scope"), dict) else {}
    return _string_or_none(scope.get("scope_key"))


def _list_value(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return [row for row in payload[key] if isinstance(row, dict)]
    return []


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_report_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_report_invalid_json"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_report_read_error:{type(exc).__name__}"}


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware")
