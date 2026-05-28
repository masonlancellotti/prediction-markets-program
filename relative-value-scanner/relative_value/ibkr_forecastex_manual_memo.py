from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


IBKR_FORECASTEX_MANUAL_MEMO_VALIDATION_SCHEMA_KIND = "ibkr_forecastex_ff_manual_ui_memo_validation_v1"
IBKR_FORECASTEX_MANUAL_MEMO_SOURCE = "ibkr_forecastex_manual_ui_memo_validation_v1"

REQUIRED_MEMO_FIELDS = (
    "market_rules_full_text",
    "about_this_market_full_text",
    "exchange_rules_full_text",
    "expiration_and_last_trading_time",
    "settlement_source_name",
    "settlement_source_url",
    "settlement_source_field",
    "threshold_semantics",
    "comparator_semantics",
    "settlement_event_date",
    "fomc_meeting_date",
    "sample_strikes",
    "commission_schedule_observed",
    "order_preview_fee_observation",
    "marketdata_permission_status_observed",
    "realtime_or_delayed_observed",
    "void_cancellation_rules_text",
    "reviewer_name_or_initials",
    "reviewed_at",
    "source_ui_surface",
    "ibkr_ui_capture_status",
    "applies_to_other_months",
    "contract_symbol_or_id_reviewed",
    "ibkr_forecastx_month_reviewed",
    "api_month_currently_fetched",
)

ALLOWED_THRESHOLD_SEMANTICS = {"upper_bound", "lower_bound", "midpoint", "effective_rate", "unknown"}
ALLOWED_COMPARATOR_SEMANTICS = {"above", "at_or_above", "greater_than", "unknown"}
ALLOWED_IBKR_UI_CAPTURE_STATUS = {"captured", "partially_captured", "not_captured"}
ALLOWED_APPLIES_TO_OTHER_MONTHS = {"yes_verified", "no", "unknown_without_separate_review"}


def validate_ibkr_forecastex_manual_memo(
    memo: dict[str, Any],
    *,
    memo_path: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    missing_fields = [field for field in REQUIRED_MEMO_FIELDS if _is_blank(memo.get(field))]
    blockers: list[str] = [f"missing_{field}" for field in missing_fields]

    threshold = str(memo.get("threshold_semantics") or "").strip()
    comparator = str(memo.get("comparator_semantics") or "").strip()
    ibkr_ui_capture_status = str(memo.get("ibkr_ui_capture_status") or "").strip()
    applies_to_other_months = str(memo.get("applies_to_other_months") or "").strip()
    reviewed_month = str(memo.get("ibkr_forecastx_month_reviewed") or "").strip().upper()
    api_month = str(memo.get("api_month_currently_fetched") or "").strip().upper()
    if threshold and threshold not in ALLOWED_THRESHOLD_SEMANTICS:
        blockers.append("invalid_threshold_semantics")
    if threshold == "unknown":
        blockers.append("unknown_threshold_semantics")
    if comparator and comparator not in ALLOWED_COMPARATOR_SEMANTICS:
        blockers.append("invalid_comparator_semantics")
    if comparator == "unknown":
        blockers.append("unknown_comparator_semantics")
    if ibkr_ui_capture_status and ibkr_ui_capture_status not in ALLOWED_IBKR_UI_CAPTURE_STATUS:
        blockers.append("invalid_ibkr_ui_capture_status")
    if ibkr_ui_capture_status == "not_captured":
        blockers.append("memo_ibkr_ui_not_captured")
    if ibkr_ui_capture_status == "partially_captured":
        blockers.append("memo_ibkr_ui_partially_captured")
    if applies_to_other_months and applies_to_other_months not in ALLOWED_APPLIES_TO_OTHER_MONTHS:
        blockers.append("invalid_applies_to_other_months")
    if applies_to_other_months == "unknown_without_separate_review":
        blockers.append("memo_unknown_other_months")
    if applies_to_other_months == "no":
        blockers.append("memo_does_not_apply_to_other_months")
    if reviewed_month and api_month and reviewed_month != api_month:
        blockers.append("memo_month_mismatch_with_api_month")
    if _is_blank(memo.get("settlement_source_name")) or _is_blank(memo.get("settlement_source_url")):
        blockers.append("missing_settlement_source")
    if _is_blank(memo.get("commission_schedule_observed")) or _is_blank(memo.get("order_preview_fee_observation")):
        blockers.append("missing_fee_observation")

    blockers = list(dict.fromkeys(blockers))
    memo_credibility_for_downstream_merge = _memo_credibility_for_downstream_merge(
        blockers=blockers,
        threshold=threshold,
        comparator=comparator,
        ibkr_ui_capture_status=ibkr_ui_capture_status,
        applies_to_other_months=applies_to_other_months,
        reviewed_month=reviewed_month,
        api_month=api_month,
        memo=memo,
    )
    return {
        "schema_kind": IBKR_FORECASTEX_MANUAL_MEMO_VALIDATION_SCHEMA_KIND,
        "source": IBKR_FORECASTEX_MANUAL_MEMO_SOURCE,
        "generated_at": generated.isoformat(),
        "memo_path": str(memo_path) if memo_path else None,
        "diagnostic_only": True,
        "validation_passed": not blockers,
        "missing_fields": missing_fields,
        "blockers": blockers,
        "summary": {
            "required_fields": len(REQUIRED_MEMO_FIELDS),
            "missing_required_fields": len(missing_fields),
            "validation_blocker_count": len(blockers),
            "memo_credibility_for_downstream_merge": memo_credibility_for_downstream_merge,
            "diagnostic_only": True,
            "settlement_rules_review_cleared": False,
            "affects_evaluator_gates": False,
            "can_create_candidate_pair": False,
            "paper_candidate_emitted": False,
            "source_registry_unchanged": True,
        },
        "safety": {
            "diagnostic_only": True,
            "merge_into_normalized_rows": False,
            "settlement_rules_review_cleared": False,
            "is_executable": False,
            "memo_credibility_for_downstream_merge": memo_credibility_for_downstream_merge,
            "can_create_candidate_pair": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
        },
    }


def validate_ibkr_forecastex_manual_memo_file(
    *,
    memo_json: Path,
    json_output: Path | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    try:
        memo = json.loads(memo_json.read_text(encoding="utf-8"))
    except OSError as exc:
        report = _failure_report(
            memo_json,
            f"memo_json_unreadable:{type(exc).__name__}:{_safe_message(exc)}",
            generated_at=generated_at,
        )
    except json.JSONDecodeError as exc:
        report = _failure_report(
            memo_json,
            f"memo_json_invalid:{type(exc).__name__}:{_safe_message(exc)}",
            generated_at=generated_at,
        )
    else:
        if not isinstance(memo, dict):
            report = _failure_report(memo_json, "memo_json_must_be_object", generated_at=generated_at)
        else:
            report = validate_ibkr_forecastex_manual_memo(
                memo,
                memo_path=memo_json,
                generated_at=generated_at,
            )
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _failure_report(path: Path, blocker: str, *, generated_at: datetime | None) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    return {
        "schema_kind": IBKR_FORECASTEX_MANUAL_MEMO_VALIDATION_SCHEMA_KIND,
        "source": IBKR_FORECASTEX_MANUAL_MEMO_SOURCE,
        "generated_at": generated.isoformat(),
        "memo_path": str(path),
        "diagnostic_only": True,
        "validation_passed": False,
        "missing_fields": list(REQUIRED_MEMO_FIELDS),
        "blockers": [blocker],
        "summary": {
            "required_fields": len(REQUIRED_MEMO_FIELDS),
            "missing_required_fields": len(REQUIRED_MEMO_FIELDS),
            "validation_blocker_count": 1,
            "memo_credibility_for_downstream_merge": False,
            "diagnostic_only": True,
            "settlement_rules_review_cleared": False,
            "affects_evaluator_gates": False,
            "can_create_candidate_pair": False,
            "paper_candidate_emitted": False,
            "source_registry_unchanged": True,
        },
        "safety": {
            "diagnostic_only": True,
            "merge_into_normalized_rows": False,
            "settlement_rules_review_cleared": False,
            "is_executable": False,
            "memo_credibility_for_downstream_merge": False,
            "can_create_candidate_pair": False,
            "paper_candidate_emitted": False,
            "affects_evaluator_gates": False,
        },
    }


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _memo_credibility_for_downstream_merge(
    *,
    blockers: list[str],
    threshold: str,
    comparator: str,
    ibkr_ui_capture_status: str,
    applies_to_other_months: str,
    reviewed_month: str,
    api_month: str,
    memo: dict[str, Any],
) -> bool:
    if blockers:
        return False
    if ibkr_ui_capture_status != "captured" and memo.get("reviewed_alternative_explicitly_accepted") is not True:
        return False
    month_covered = bool(reviewed_month and api_month and reviewed_month == api_month) or applies_to_other_months == "yes_verified"
    if not month_covered:
        return False
    if threshold in {"", "unknown"} or comparator in {"", "unknown"}:
        return False
    if _is_blank(memo.get("settlement_source_name")) or _is_blank(memo.get("settlement_source_url")):
        return False
    if _is_blank(memo.get("settlement_source_field")):
        return False
    if _is_blank(memo.get("commission_schedule_observed")) or _is_blank(memo.get("order_preview_fee_observation")):
        return False
    return True


def _safe_message(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:200]
