from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.canonical_convention_registry import (
    REGISTRY_VERSION,
    load_canonical_convention_registry,
    match_canonical_registry_entry,
)


SCHEMA_VERSION = 1
REPORT_SOURCE = "canonical_registry_coverage_v1"
FAMILY_GRADUATION_SOURCE = "family_graduation_plan_v1"
SETTLEMENT_BURDEN_SOURCE = "settlement_evidence_burden_v1"


def build_canonical_registry_coverage_report(
    *,
    input_dir: Path,
    registry_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    _require_tz_aware(generated, "generated_at")
    warnings: list[dict[str, Any]] = []

    burden_payload, warning = _load_json(input_dir / "settlement_evidence_burden.json")
    if warning is not None:
        warnings.append(warning)
    burden_rows = _burden_rows(burden_payload)

    graduation_reports = []
    for filename in ("family_graduation_crypto.json", "family_graduation_fed.json"):
        payload, warning = _load_json(input_dir / filename)
        if warning is not None:
            warnings.append(warning)
            continue
        if isinstance(payload, dict) and payload.get("source") == FAMILY_GRADUATION_SOURCE:
            graduation_reports.append(payload)

    registry_result = load_canonical_convention_registry(registry_path) if registry_path is not None else None
    registry_entries = registry_result.valid_entries if registry_result is not None else []
    if registry_result is not None:
        warnings.extend(registry_result.warnings)

    scopes = _scope_rows(graduation_reports=graduation_reports, registry_entries=registry_entries)
    next_manual_review = _next_manual_review(scopes)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": REPORT_SOURCE,
        "generated_at": generated.isoformat(),
        "input_dir": str(input_dir),
        "registry_path": str(registry_path) if registry_path is not None else None,
        "registry_entry_count": len(registry_entries),
        "settlement_evidence_burden_row_count": len(burden_rows),
        "summary": _summary(scopes, warnings),
        "scopes": scopes,
        "next_manual_review": next_manual_review,
        "warnings": warnings,
        "safety": {
            "saved_files_only": True,
            "live_fetch_attempted": False,
            "execution_or_order_logic_added": False,
            "account_or_auth_logic_added": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
            "registry_proposal_is_trust": False,
            "registry_proposals_treated_as_evidence": False,
            "reviewed_scope_requires_actual_registry_match": True,
        },
    }


def write_canonical_registry_coverage_files(
    *,
    input_dir: Path,
    registry_path: Path | None,
    json_output: Path,
    markdown_output: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    report = build_canonical_registry_coverage_report(
        input_dir=input_dir,
        registry_path=registry_path,
        generated_at=generated_at,
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_canonical_registry_coverage_markdown(report), encoding="utf-8")
    return report


def render_canonical_registry_coverage_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Canonical Registry Coverage",
        "",
        "Saved-file-only registry coverage and reviewer-flow report. Registry proposals are not trust; only actual matching registry entries count as reviewed.",
        "",
        "## Summary",
        "",
        f"- scopes_total: `{summary.get('scopes_total', 0)}`",
        f"- scopes_reviewed: `{summary.get('scopes_reviewed', 0)}`",
        f"- scopes_unreviewed: `{summary.get('scopes_unreviewed', 0)}`",
        f"- rows_covered_by_reviewed_scopes: `{summary.get('rows_covered_by_reviewed_scopes', 0)}`",
        f"- rows_uncovered: `{summary.get('rows_uncovered', 0)}`",
        "",
        "## Scopes",
        "",
        "| Scope | Family | Rows | Registry matches | Typed complete | Eligible if reviewed | Entry | Reviewer | Source hint |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in report.get("scopes") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(row.get("scope_key")),
                    _md(row.get("family")),
                    _md(row.get("row_count")),
                    _md(row.get("registry_match_count")),
                    _md(row.get("rows_with_typed_keys_complete")),
                    _md(row.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed")),
                    _md(row.get("registry_entry_id_if_matched")),
                    _md(row.get("reviewer")),
                    _md(row.get("source_url_candidate_status")),
                ]
            )
            + " |"
        )
    if not report.get("scopes"):
        lines.append("| (none) | (none) | 0 | 0 | 0 | 0 | (none) | (none) | (none) |")
    lines.extend(
        [
            "",
            "## Next Manual Review",
            "",
        ]
    )
    suggestions = (report.get("next_manual_review") or {}).get("top_unreviewed_scopes") or []
    if suggestions:
        for item in suggestions:
            lines.append(f"### `{_md(item.get('scope_key'))}`")
            lines.append("")
            lines.append(f"- row_count: `{item.get('row_count')}`")
            lines.append(f"- rows_eligible_to_upgrade_to_exact_review_if_reviewed: `{item.get('rows_eligible_to_upgrade_to_exact_review_if_reviewed')}`")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(item.get("registry_entry_skeleton"), indent=2, sort_keys=True))
            lines.append("```")
            lines.append("")
    else:
        lines.append("(none)")
    lines.extend(
        [
            "## Safety",
            "",
            "- saved_files_only: `true`",
            "- live_fetch_attempted: `false`",
            "- paper_candidate_emitted: `false`",
            "- affects_evaluator_gates: `false`",
            "- registry_proposal_is_trust: `false`",
        ]
    )
    return "\n".join(lines) + "\n"


def _scope_rows(*, graduation_reports: list[dict[str, Any]], registry_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for report in graduation_reports:
        group_by_key = {
            str(group.get("scope_key")): group
            for group in report.get("registry_proposal_groups") or []
            if isinstance(group, dict) and group.get("scope_key")
        }
        for row in report.get("rows") or []:
            if not isinstance(row, dict):
                continue
            proposal = row.get("registry_proposal") if isinstance(row.get("registry_proposal"), dict) else {}
            recommended = proposal.get("recommended_registry_scope") if isinstance(proposal.get("recommended_registry_scope"), dict) else {}
            scope_key = str(recommended.get("scope_key") or proposal.get("proposal_id") or "")
            if not scope_key:
                continue
            group = group_by_key.get(scope_key, {})
            bucket = buckets.setdefault(
                scope_key,
                {
                    "scope_key": scope_key,
                    "scope_kind": recommended.get("scope_kind") or group.get("scope_kind"),
                    "scope_fields": recommended.get("scope_fields") or group.get("scope_fields") or {},
                    "family": row.get("family") or report.get("family"),
                    "source_kind": proposal.get("source_kind") or group.get("source_kind"),
                    "source_url_candidate": proposal.get("source_url_candidate") or group.get("source_url_candidate"),
                    "source_url_candidate_status": proposal.get("source_url_candidate_status")
                    or group.get("source_url_candidate_status")
                    or "unknown",
                    "official_source_description": proposal.get("official_source_description") or group.get("official_source_description"),
                    "rows": [],
                },
            )
            bucket["rows"].append(row)

    rows: list[dict[str, Any]] = []
    for scope_key, bucket in buckets.items():
        source_rows = bucket.pop("rows")
        matches = [_registry_match_for_row(row, registry_entries) for row in source_rows]
        matches = [match for match in matches if match is not None]
        entry_ids = sorted({str(match.get("registry_entry_id") or match.get("entry_id")) for match in matches if match.get("registry_entry_id") or match.get("entry_id")})
        reviewers = sorted({str(match.get("reviewer")) for match in matches if match.get("reviewer")})
        reviewed_at = sorted({str(match.get("reviewed_at")) for match in matches if match.get("reviewed_at")})
        row_count = len(source_rows)
        registry_match_count = len(matches)
        rows.append(
            {
                **bucket,
                "scope_key": scope_key,
                "row_count": row_count,
                "registry_match_count": registry_match_count,
                "rows_with_typed_keys_complete": sum(1 for row in source_rows if not row.get("missing_typed_keys")),
                "rows_eligible_to_upgrade_to_exact_review_if_reviewed": sum(
                    1
                    for row in source_rows
                    if ((row.get("projection") or {}).get("can_upgrade_to_exact_review_if_reviewed") is True)
                    or ((row.get("registry_proposal") or {}).get("can_upgrade_to_exact_review_if_reviewed") is True)
                ),
                "registry_entry_id_if_matched": entry_ids[0] if entry_ids else None,
                "registry_entry_ids_if_matched": entry_ids,
                "reviewer": reviewers[0] if reviewers else None,
                "reviewers": reviewers,
                "reviewed_at": reviewed_at[0] if reviewed_at else None,
                "reviewed_at_values": reviewed_at,
                "review_status": "reviewed" if registry_match_count > 0 else "unreviewed",
                "registry_proposal_is_trust": False,
            }
        )
    return sorted(rows, key=_scope_sort_key)


def _registry_match_for_row(row: dict[str, Any], registry_entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not registry_entries:
        return None
    typed_keys = row.get("typed_keys") if isinstance(row.get("typed_keys"), dict) else {}
    typed = {
        "required": list(row.get("required_typed_keys") or []),
        "present": list(row.get("present_typed_keys") or []),
        "missing": list(row.get("missing_typed_keys") or []),
        "evidence": {
            key: {"value": value, "source": "canonical_registry_coverage:family_graduation"}
            for key, value in typed_keys.items()
            if value is not None
        },
    }
    return match_canonical_registry_entry(
        registry_entries,
        venue=str(row.get("venue") or ""),
        family=str(row.get("family") or ""),
        event_ticker=_string_or_none(row.get("event_ticker")),
        ticker=_string_or_none(row.get("ticker")),
        event_slug=_string_or_none(row.get("event_slug")),
        typed_keys=typed,
    )


def _summary(scopes: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [scope for scope in scopes if scope.get("registry_match_count", 0) > 0]
    uncovered_rows = sum(max(0, _int(scope.get("row_count")) - _int(scope.get("registry_match_count"))) for scope in scopes)
    leverage = [_leverage_scope(scope) for scope in scopes]
    leverage.sort(key=_leverage_sort_key)
    return {
        "scopes_total": len(scopes),
        "scopes_reviewed": len(reviewed),
        "scopes_unreviewed": len(scopes) - len(reviewed),
        "rows_covered_by_reviewed_scopes": sum(_int(scope.get("registry_match_count")) for scope in scopes),
        "rows_uncovered": uncovered_rows,
        "scopes_sorted_by_review_leverage": leverage,
        "top_leverage_scope": leverage[0]["scope_key"] if leverage else None,
        "warning_count": len(warnings),
        "paper_candidate_count": 0,
    }


def _next_manual_review(scopes: list[dict[str, Any]]) -> dict[str, Any]:
    unreviewed = [scope for scope in scopes if _int(scope.get("registry_match_count")) == 0]
    leverage = sorted(unreviewed, key=_scope_sort_key)[:3]
    return {
        "top_unreviewed_scopes": [
            {
                "scope_key": scope.get("scope_key"),
                "family": scope.get("family"),
                "row_count": scope.get("row_count"),
                "rows_eligible_to_upgrade_to_exact_review_if_reviewed": scope.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed"),
                "source_url_candidate": scope.get("source_url_candidate"),
                "source_url_candidate_status": scope.get("source_url_candidate_status"),
                "registry_entry_skeleton": _registry_entry_skeleton(scope),
            }
            for scope in leverage
        ],
        "registry_proposal_is_trust": False,
        "human_review_required": True,
    }


def _registry_entry_skeleton(scope: dict[str, Any]) -> dict[str, Any]:
    scope_fields = scope.get("scope_fields") if isinstance(scope.get("scope_fields"), dict) else {}
    applies_to_scope = {"venue": scope_fields.get("venue") or "<TODO: venue>"}
    if scope_fields.get("event_ticker_prefix"):
        applies_to_scope["event_ticker_prefix"] = scope_fields["event_ticker_prefix"]
    if scope_fields.get("event_slug_prefix"):
        applies_to_scope["event_slug_prefix"] = scope_fields["event_slug_prefix"]
    if scope_fields.get("ticker_prefix"):
        applies_to_scope["ticker_prefix"] = scope_fields["ticker_prefix"]
    typed_match = {
        key: value
        for key, value in scope_fields.items()
        if key not in {"family", "venue", "event_ticker_prefix", "event_slug_prefix", "ticker_prefix"} and value not in (None, "")
    }
    return {
        "registry_version": REGISTRY_VERSION,
        "entry_id": "<TODO: stable_entry_id>",
        "family": scope.get("family"),
        "reviewer": "<TODO: reviewer>",
        "reviewed_at": "<TODO: reviewed_at>",
        "applies_to_scope": applies_to_scope,
        "typed_key_requirements": {
            "required": sorted(typed_match),
            "match": typed_match,
        },
        "canonical_source_kind": scope.get("source_kind") or "<TODO: canonical_source_kind>",
        "canonical_source_url": "<TODO: canonical_source_url>",
        "official_source_description": scope.get("official_source_description") or "<TODO: official_source_description>",
        "evidence_quote_or_excerpt": "<TODO: evidence_quote_or_excerpt>",
        "limitations": "<TODO: limitations>",
        "review_until": "<TODO: review_until>",
        "confidence": "<TODO: confidence>",
    }


def _leverage_scope(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope_key": scope.get("scope_key"),
        "family": scope.get("family"),
        "row_count": scope.get("row_count"),
        "registry_match_count": scope.get("registry_match_count"),
        "rows_eligible_to_upgrade_to_exact_review_if_reviewed": scope.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed"),
        "review_status": scope.get("review_status"),
    }


def _scope_sort_key(scope: dict[str, Any]) -> tuple[int, int, int, str, str]:
    return (
        -_int(scope.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed")),
        -_int(scope.get("rows_with_typed_keys_complete")),
        -_int(scope.get("row_count")),
        str(scope.get("family") or ""),
        str(scope.get("scope_key") or ""),
    )


def _leverage_sort_key(scope: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        -_int(scope.get("rows_eligible_to_upgrade_to_exact_review_if_reviewed")),
        -_int(scope.get("row_count")),
        str(scope.get("family") or ""),
        str(scope.get("scope_key") or ""),
    )


def _burden_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("source") != SETTLEMENT_BURDEN_SOURCE:
        return []
    rows = payload.get("markets")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _load_json(path: Path) -> tuple[Any, dict[str, Any] | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, {"source_file": str(path), "reason_code": "json_file_missing", "blocker": "saved_json_file_missing"}
    except json.JSONDecodeError:
        return None, {"source_file": str(path), "reason_code": "invalid_json", "blocker": "saved_json_invalid"}
    except OSError as exc:
        return None, {"source_file": str(path), "reason_code": "json_read_error", "blocker": f"saved_json_read_error:{type(exc).__name__}"}


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
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _require_tz_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")
