from __future__ import annotations

import json
from pathlib import Path

import scan
from relative_value.ibkr_forecastex_manual_memo import (
    REQUIRED_MEMO_FIELDS,
    validate_ibkr_forecastex_manual_memo,
    validate_ibkr_forecastex_manual_memo_file,
)
from relative_value.source_registry import ImplementationStatus, SOURCE_REGISTRY


def _filled_memo() -> dict[str, object]:
    memo: dict[str, object] = {field: f"{field} evidence" for field in REQUIRED_MEMO_FIELDS}
    memo["threshold_semantics"] = "upper_bound"
    memo["comparator_semantics"] = "above"
    memo["settlement_source_url"] = "https://example.com/settlement"
    memo["sample_strikes"] = ["4.375", "4.500"]
    memo["ibkr_ui_capture_status"] = "captured"
    memo["applies_to_other_months"] = "yes_verified"
    memo["contract_symbol_or_id_reviewed"] = "FF JUN26 4.375"
    memo["ibkr_forecastx_month_reviewed"] = "JUN26"
    memo["api_month_currently_fetched"] = "JUN26"
    return memo


def test_manual_memo_template_shape_validates_when_required_fields_are_present() -> None:
    report = validate_ibkr_forecastex_manual_memo(_filled_memo())

    assert report["validation_passed"] is True
    assert report["summary"]["diagnostic_only"] is True
    assert report["summary"]["affects_evaluator_gates"] is False
    assert report["summary"]["can_create_candidate_pair"] is False
    assert report["summary"]["paper_candidate_emitted"] is False
    assert report["summary"]["memo_credibility_for_downstream_merge"] is True


def test_manual_memo_missing_threshold_settlement_and_fee_observations_block() -> None:
    memo = _filled_memo()
    memo["threshold_semantics"] = ""
    memo["settlement_source_name"] = ""
    memo["settlement_source_url"] = ""
    memo["commission_schedule_observed"] = ""
    memo["order_preview_fee_observation"] = ""

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "missing_threshold_semantics" in report["blockers"]
    assert "missing_settlement_source" in report["blockers"]
    assert "missing_fee_observation" in report["blockers"]
    assert report["safety"]["merge_into_normalized_rows"] is False
    assert report["safety"]["settlement_rules_review_cleared"] is False


def test_manual_memo_unknown_semantics_are_still_blocking() -> None:
    memo = _filled_memo()
    memo["threshold_semantics"] = "unknown"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "unknown_threshold_semantics" in report["blockers"]


def test_memo_with_ibkr_ui_not_captured_is_blocked() -> None:
    memo = _filled_memo()
    memo["ibkr_ui_capture_status"] = "not_captured"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "memo_ibkr_ui_not_captured" in report["blockers"]
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_memo_with_ibkr_ui_partially_captured_is_blocked_or_warned() -> None:
    memo = _filled_memo()
    memo["ibkr_ui_capture_status"] = "partially_captured"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "memo_ibkr_ui_partially_captured" in report["blockers"]
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_memo_with_unknown_other_months_is_blocked() -> None:
    memo = _filled_memo()
    memo["applies_to_other_months"] = "unknown_without_separate_review"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "memo_unknown_other_months" in report["blockers"]
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_memo_with_no_other_months_is_blocked_for_cross_month_use() -> None:
    memo = _filled_memo()
    memo["applies_to_other_months"] = "no"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "memo_does_not_apply_to_other_months" in report["blockers"]
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_memo_month_must_match_api_month() -> None:
    memo = _filled_memo()
    memo["ibkr_forecastx_month_reviewed"] = "DEC26"
    memo["api_month_currently_fetched"] = "JUN26"

    report = validate_ibkr_forecastex_manual_memo(memo)

    assert report["validation_passed"] is False
    assert "memo_month_mismatch_with_api_month" in report["blockers"]
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_memo_credibility_for_downstream_merge_requires_all_caveats_cleared() -> None:
    credible = validate_ibkr_forecastex_manual_memo(_filled_memo())
    assert credible["validation_passed"] is True
    assert credible["summary"]["memo_credibility_for_downstream_merge"] is True

    caveated = _filled_memo()
    caveated["comparator_semantics"] = "unknown"
    caveated_report = validate_ibkr_forecastex_manual_memo(caveated)
    assert caveated_report["validation_passed"] is False
    assert caveated_report["summary"]["memo_credibility_for_downstream_merge"] is False


def test_filled_current_memo_now_fails_validation_with_caveat_blockers() -> None:
    memo_path = Path("reports/manual_snapshots/ibkr_forecastex/ff_manual_ui_memo.json")
    assert memo_path.exists()

    report = validate_ibkr_forecastex_manual_memo_file(memo_json=memo_path)

    assert report["validation_passed"] is False
    assert report["summary"]["memo_credibility_for_downstream_merge"] is False
    assert "memo_ibkr_ui_not_captured" in report["blockers"]
    assert "memo_unknown_other_months" in report["blockers"]
    assert "memo_month_mismatch_with_api_month" in report["blockers"]


def test_manual_memo_cli_writes_diagnostic_report_without_registry_or_paper_effects(tmp_path: Path, capsys) -> None:
    memo_path = tmp_path / "memo.json"
    output_path = tmp_path / "validation.json"
    memo_path.write_text(json.dumps(_filled_memo()), encoding="utf-8")

    result = scan.main(
        [
            "validate-ibkr-forecastex-manual-memo",
            "--memo-json",
            str(memo_path),
            "--json-output",
            str(output_path),
        ]
    )
    stdout = capsys.readouterr().out
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert result == 0
    assert "diagnostic_only=true" in stdout
    assert "memo_credibility_for_downstream_merge=true" in stdout
    assert "paper_candidate_emitted=false" in stdout
    assert payload["summary"]["source_registry_unchanged"] is True
    assert "PAPER_CANDIDATE" not in json.dumps(payload)
    assert SOURCE_REGISTRY["forecastex_ibkr"].implementation_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED


def test_validator_still_never_changes_source_registry_or_paper_flags() -> None:
    before_status = SOURCE_REGISTRY["forecastex_ibkr"].implementation_status

    report = validate_ibkr_forecastex_manual_memo(_filled_memo())

    assert SOURCE_REGISTRY["forecastex_ibkr"].implementation_status == before_status
    assert before_status == ImplementationStatus.PLANNED_NOT_IMPLEMENTED
    assert report["summary"]["source_registry_unchanged"] is True
    assert report["summary"]["can_create_candidate_pair"] is False
    assert report["summary"]["paper_candidate_emitted"] is False
    assert report["safety"]["merge_into_normalized_rows"] is False
    assert report["safety"]["settlement_rules_review_cleared"] is False
    assert "PAPER_CANDIDATE" not in json.dumps(report)


def test_ibkr_pipeline_authenticated_runs_fetch_and_ops_with_safe_outputs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls: list[str] = []

    def fake_doctor(**kwargs):
        calls.append("doctor")
        return {"schema_kind": "ibkr_forecastex_access_doctor_v1", "status": "OK", "reachable": True, "authenticated": True}

    def fake_fetch(**kwargs):
        calls.append("fetch")
        return {
            "status": "OK",
            "summary": {
                "final_tradable_rows": 28,
                "ibkr_quote_rows_quote_diagnostic_complete": 8,
                "ibkr_quote_rows_execution_ready": 0,
            },
        }

    def fake_ops(**kwargs):
        calls.append("ops")
        return {"highest_priority_next_action": {"action": "FORECASTX_QUOTE_PARTIAL_REVIEW_PERMISSIONS_OR_DEPTH"}}

    monkeypatch.setattr(scan, "build_ibkr_forecastex_access_doctor", fake_doctor)
    monkeypatch.setattr(scan, "write_ibkr_forecastex_readonly_snapshot_file", fake_fetch)
    monkeypatch.setattr(scan, "write_relative_value_ops_status_files", fake_ops)

    result = scan.main(
        [
            "ibkr-forecastex-readonly-pipeline",
            "--forecastx-months",
            "JUN26",
            "--json-output",
            str(tmp_path / "reports" / "ibkr_forecastex_normalized_draft.json"),
            "--ops-json-output",
            str(tmp_path / "reports" / "relative_value_ops_status.json"),
            "--ops-markdown-output",
            str(tmp_path / "reports" / "relative_value_ops_status.md"),
        ]
    )
    stdout = capsys.readouterr().out

    assert result == 0
    assert calls == ["doctor", "fetch", "ops"]
    assert "ibkr_forecastex_readonly_pipeline_status=OK" in stdout
    assert "execution_ready_rows=0" in stdout
    assert "session" not in stdout.lower()


def test_ibkr_pipeline_unauthenticated_no_wait_exits_safely(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        scan,
        "build_ibkr_forecastex_access_doctor",
        lambda **kwargs: {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
            "reachable": True,
            "authenticated": False,
        },
    )

    result = scan.main(
        [
            "ibkr-forecastex-readonly-pipeline",
            "--forecastx-months",
            "JUN26",
            "--wait-for-auth-seconds",
            "0",
            "--ops-json-output",
            str(tmp_path / "reports" / "relative_value_ops_status.json"),
        ]
    )

    assert result == 1
    assert "manual_action=open_https_localhost_5000_and_log_in" in capsys.readouterr().out


def test_ibkr_pipeline_wait_then_authenticated_runs(tmp_path: Path, monkeypatch, capsys) -> None:
    reports = [
        {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
            "reachable": True,
            "authenticated": False,
        },
        {"schema_kind": "ibkr_forecastex_access_doctor_v1", "status": "OK", "reachable": True, "authenticated": True},
    ]
    calls: list[str] = []

    def fake_doctor(**kwargs):
        return reports.pop(0)

    monkeypatch.setattr(scan, "build_ibkr_forecastex_access_doctor", fake_doctor)
    monkeypatch.setattr(scan, "write_ibkr_forecastex_readonly_snapshot_file", lambda **kwargs: calls.append("fetch") or {"status": "OK", "summary": {}})
    monkeypatch.setattr(scan, "write_relative_value_ops_status_files", lambda **kwargs: calls.append("ops") or {"highest_priority_next_action": {"action": "OK"}})

    result = scan.main(
        [
            "ibkr-forecastex-readonly-pipeline",
            "--forecastx-months",
            "JUN26",
            "--wait-for-auth-seconds",
            "1",
            "--poll-seconds",
            "0",
            "--ops-json-output",
            str(tmp_path / "reports" / "relative_value_ops_status.json"),
        ]
    )

    assert result == 0
    assert calls == ["fetch", "ops"]
    assert "pipeline_status=OK" in capsys.readouterr().out


def test_ibkr_pipeline_wait_timeout_exits_safely(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        scan,
        "build_ibkr_forecastex_access_doctor",
        lambda **kwargs: {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "LOCAL_GATEWAY_REACHABLE_SESSION_NOT_AUTHENTICATED",
            "reachable": True,
            "authenticated": False,
        },
    )

    result = scan.main(
        [
            "ibkr-forecastex-readonly-pipeline",
            "--forecastx-months",
            "JUN26",
            "--wait-for-auth-seconds",
            "1",
            "--poll-seconds",
            "0",
            "--ops-json-output",
            str(tmp_path / "reports" / "relative_value_ops_status.json"),
        ]
    )

    assert result == 1
    stdout = capsys.readouterr().out
    assert "AUTH_REQUIRED" in stdout
    assert "session_id" not in stdout.lower()


def test_ibkr_pipeline_unreachable_gateway_instructs_manual_start(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        scan,
        "build_ibkr_forecastex_access_doctor",
        lambda **kwargs: {
            "schema_kind": "ibkr_forecastex_access_doctor_v1",
            "status": "LOCAL_GATEWAY_UNREACHABLE",
            "reachable": False,
            "authenticated": False,
        },
    )

    result = scan.main(
        [
            "ibkr-forecastex-readonly-pipeline",
            "--forecastx-months",
            "JUN26",
            "--ops-json-output",
            str(tmp_path / "reports" / "relative_value_ops_status.json"),
        ]
    )

    assert result == 1
    assert "manual_action=start_IBKR_Client_Portal_Gateway" in capsys.readouterr().out
