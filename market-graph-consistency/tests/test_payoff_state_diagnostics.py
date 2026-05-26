from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from graph_engine.loader import load_fixture_markets
from graph_engine.payoff_state import (
    SUPPORTED_FAMILY_TYPES,
    PayoffMatrix,
    compile_payoff_families,
)
from graph_engine.payoff_state_feasibility import (
    FEASIBILITY_STATUSES,
    check_no_arb_consistency,
)
from graph_engine.reporting.payoff_state_report import (
    BANNER,
    build_payoff_state_diagnostics_report,
    render_payoff_state_diagnostics_markdown,
    validate_payoff_state_diagnostics_report,
    write_payoff_state_diagnostics_report,
)
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS
from graph_engine.reporting.schema_validation import SchemaValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "venues" / "fixtures"
PROHIBITED_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill-size",
        "trade-permission",
        "evaluator_ready",
    }
)


def _fixture_report() -> dict:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    return build_payoff_state_diagnostics_report(snapshot)


def _by_family(report: dict) -> dict[str, dict]:
    return {item["family_id"]: item for item in report["payoff_state_diagnostics"]}


def _families() -> dict[str, PayoffMatrix]:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    return {matrix.family_id: matrix for matrix in compile_payoff_families(snapshot)}


# ---------------------------------------------------------------------------
# Compiler structural tests
# ---------------------------------------------------------------------------


def test_compiler_emits_finite_state_matrices_for_each_supported_family_type() -> None:
    families = _families()
    family_types = {matrix.family_type for matrix in families.values()}

    assert {
        "exhaustive_group",
        "mutually_exclusive_group",
        "threshold_ladder",
        "range_bucket_partition",
        "child_parent_chain",
        "formula_cluster_exact",
        "complement_pair",
    } <= family_types
    assert family_types <= (SUPPORTED_FAMILY_TYPES | {"unknown"})


def test_complement_pair_inconsistency_is_manual_review() -> None:
    row = _by_family(_fixture_report())["payoff_complement_inconsistency"]

    assert row["family_type"] == "complement_pair"
    assert row["feasibility_status"] == "infeasible"
    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert any("complement_pair" in violation for violation in row["violated_constraints"])
    assert row["bound_gap"] > 0


def test_compiler_produces_dense_state_payoff_matrix_for_feasible_family() -> None:
    matrix = _families()["payoff_feasible_exhaustive"]
    payoffs = matrix.state_payoff_matrix()

    assert matrix.state_count == 3
    assert matrix.contract_count == 3
    assert sorted(payoffs.keys()) == [
        "fixture_payoff:feasible_exh_blue",
        "fixture_payoff:feasible_exh_green",
        "fixture_payoff:feasible_exh_red",
    ]
    for vector in payoffs.values():
        assert len(vector) == matrix.state_count
        assert sum(value or 0.0 for value in vector) == 1.0


def test_compiler_captures_blockers_for_missing_states_and_payoffs() -> None:
    matrix = _families()["payoff_ambiguous_missing_states"]

    assert matrix.is_ready_for_feasibility is False
    assert "missing_state_definitions" in matrix.blockers
    assert any(blocker.startswith("missing_payoff_vector:") for blocker in matrix.blockers)


def test_compiler_detects_range_bucket_overlap_and_gap() -> None:
    families = _families()

    assert "range_bucket_overlap" in families["payoff_range_partition_overlap"].blockers
    assert "range_bucket_gap" in families["payoff_range_partition_gap"].blockers


# ---------------------------------------------------------------------------
# Feasibility engine tests
# ---------------------------------------------------------------------------


def test_feasible_exhaustive_does_not_create_high_priority_false_positive() -> None:
    row = _by_family(_fixture_report())["payoff_feasible_exhaustive"]

    assert row["diagnostic_only"] is True
    assert row["affects_evaluator_gates"] is False
    assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert row["feasibility_status"] == "feasible"
    assert row["max_action_cap"] == "WATCH"
    assert row["diagnostic_priority"] == "WATCH"
    assert row["violated_constraints"] == []
    assert row["bound_gap"] == 0.0
    assert row["normalized_bound_gap"] == 0.0
    assert row["state_count"] == 3
    assert row["contract_count"] == 3


@pytest.mark.parametrize(
    ("family_id", "expected_violation"),
    [
        ("payoff_infeasible_exhaustive", "exhaustive_sum_bound"),
        ("payoff_mutex_oversum", "mutually_exclusive_sum_bound"),
        ("payoff_child_parent_violation", "child_parent_bound"),
        ("payoff_threshold_ladder_violation", "threshold_ladder_monotonicity"),
    ],
)
def test_infeasible_cases_become_manual_review_only(family_id: str, expected_violation: str) -> None:
    row = _by_family(_fixture_report())[family_id]

    assert row["feasibility_status"] == "infeasible"
    assert row["max_action_cap"] == "MANUAL_REVIEW"
    assert row["diagnostic_priority"] == "MANUAL_REVIEW"
    assert "finite_state_feasibility" in row["violated_constraints"]
    assert expected_violation in row["violated_constraints"]
    assert row["bound_gap"] > 0
    assert row["normalized_bound_gap"] > 0
    assert row["confidence_basis"]["score"] >= 0.6


def test_missing_or_ambiguous_state_definitions_fail_closed() -> None:
    row = _by_family(_fixture_report())["payoff_ambiguous_missing_states"]

    assert row["feasibility_status"] == "blocked"
    assert row["max_action_cap"] == "WATCH"
    assert row["violated_constraints"] == []
    assert row["bound_gap"] == 0.0
    assert row["confidence_basis"]["score"] <= 0.25
    assert "missing_state_definitions" in row["blockers"]


@pytest.mark.parametrize("family_id", ["payoff_range_partition_overlap", "payoff_range_partition_gap"])
def test_range_bucket_partitions_with_gap_or_overlap_block_feasibility(family_id: str) -> None:
    row = _by_family(_fixture_report())[family_id]

    assert row["feasibility_status"] == "blocked"
    assert row["max_action_cap"] == "WATCH"
    assert row["confidence_basis"]["score"] <= 0.25


def test_formula_cluster_exact_remains_diagnostic_only_review() -> None:
    row = _by_family(_fixture_report())["payoff_formula_cluster_btc_120k_2026_06_30"]

    assert row["family_type"] == "formula_cluster_exact"
    assert row["feasibility_status"] == "feasible"
    assert row["max_action_cap"] == "WATCH"
    assert row["diagnostic_only"] is True
    assert row["graph_artifact_not_equality_evidence"] is True
    assert row["review_artifact_not_candidate"] is True
    evidence = row["contracts"][0]["required_evidence_fields"]
    assert "source" in evidence
    assert "date" in evidence
    assert "threshold" in evidence


def test_feasibility_status_set_is_exactly_three_values() -> None:
    assert FEASIBILITY_STATUSES == {"feasible", "infeasible", "blocked"}


def test_engine_returns_blocked_when_observed_probability_missing() -> None:
    families = _families()
    matrix = families["payoff_feasible_exhaustive"]
    contracts = [
        type(matrix.contracts[0])(
            contract_id=contract.contract_id,
            family_id=contract.family_id,
            payoff_by_state=dict(contract.payoff_by_state),
            required_evidence_fields=list(contract.required_evidence_fields),
            blockers=list(contract.blockers),
            observed_probability=None if index == 0 else contract.observed_probability,
            structural_role=contract.structural_role,
        )
        for index, contract in enumerate(matrix.contracts)
    ]
    mutated = PayoffMatrix(
        family_id=matrix.family_id,
        family_type=matrix.family_type,
        family_description=matrix.family_description,
        states=matrix.states,
        contracts=contracts,
        structural_metadata=matrix.structural_metadata,
        blockers=[],
        confidence_basis=matrix.confidence_basis,
    )
    result = check_no_arb_consistency(mutated)

    assert result.feasibility_status == "blocked"
    assert result.bound_gap == 0.0
    assert "missing_observed_probability" in result.blockers


# ---------------------------------------------------------------------------
# Report-shape / safety contract tests
# ---------------------------------------------------------------------------


def test_report_has_banner_and_diagnostic_only_envelope() -> None:
    report = _fixture_report()

    assert report["banner"] == BANNER
    assert report["diagnostic_only"] is True
    assert report["affects_evaluator_gates"] is False
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    assert report["family_count"] == len(report["payoff_state_diagnostics"])
    assert report["counts_by_feasibility_status"]["feasible"] >= 1
    assert report["counts_by_feasibility_status"]["infeasible"] >= 4
    assert report["counts_by_feasibility_status"]["blocked"] >= 3


def test_diagnostic_ranking_is_stable_by_priority_gap_and_id() -> None:
    diagnostics = _fixture_report()["payoff_state_diagnostics"]
    expected = sorted(
        diagnostics,
        key=lambda item: (
            0 if item["max_action_cap"] == "MANUAL_REVIEW" else 1,
            -item["normalized_bound_gap"],
            item["family_id"],
        ),
    )

    assert diagnostics == expected
    assert [item["diagnostic_rank"] for item in diagnostics] == list(range(1, len(diagnostics) + 1))


def test_every_row_includes_required_fields_and_review_questions() -> None:
    for row in _fixture_report()["payoff_state_diagnostics"]:
        assert row["diagnostic_only"] is True
        assert row["affects_evaluator_gates"] is False
        assert row["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
        assert row["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}
        assert row["diagnostic_priority"] in {"WATCH", "MANUAL_REVIEW"}
        assert row["graph_artifact_not_equality_evidence"] is True
        assert row["review_artifact_not_candidate"] is True
        assert row["family_type"] in (SUPPORTED_FAMILY_TYPES | {"unknown"})
        assert row["family_id"]
        assert row["state_count"] == len(row["states"])
        assert row["contract_count"] == len(row["contracts"])
        assert row["required_review_questions"]
        assert row["confidence_basis"]["description"]
        assert 0 <= row["confidence_basis"]["score"] <= 1


def test_state_payoff_matrix_matches_contract_count_and_state_count() -> None:
    for row in _fixture_report()["payoff_state_diagnostics"]:
        matrix = row["state_payoff_matrix"]
        assert len(matrix) == row["contract_count"]
        for vector in matrix.values():
            assert len(vector) == row["state_count"]


def test_validate_report_rejects_missing_required_fields() -> None:
    report = _fixture_report()
    mutated = deepcopy(report)
    mutated["payoff_state_diagnostics"][0].pop("required_review_questions")

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(mutated)


def test_validate_report_rejects_unsupported_feasibility_status() -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["feasibility_status"] = "looks_ok"

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_validate_report_rejects_attempt_to_drop_equality_evidence_disclaimer() -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["graph_artifact_not_equality_evidence"] = False

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_validate_report_rejects_attempt_to_drop_review_artifact_disclaimer() -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["review_artifact_not_candidate"] = False

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_validate_report_rejects_affects_evaluator_gates_true() -> None:
    report = _fixture_report()
    report["affects_evaluator_gates"] = True

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_validate_report_rejects_disallowed_action_cap() -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["max_action_cap"] = "PAPER_CANDIDATE"

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_validate_report_rejects_prohibited_field_keys() -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["profit_usd"] = 1

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


@pytest.mark.parametrize("token", ["trade", "fill", "size", "paper"])
def test_validate_report_rejects_bare_prohibited_value(token: str) -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["review_reason"] = token

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_report_validates_before_writing(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    json_output = tmp_path / "market_graph_payoff_state_diagnostics.json"
    md_output = tmp_path / "market_graph_payoff_state_diagnostics.md"

    report = write_payoff_state_diagnostics_report(snapshot, json_output, md_output)

    assert json_output.exists()
    assert md_output.exists()
    assert json.loads(json_output.read_text(encoding="utf-8")) == report
    validate_payoff_state_diagnostics_report(report)


def test_report_serialised_output_contains_no_prohibited_tokens() -> None:
    report = _fixture_report()
    serialised = json.dumps(report).lower()

    assert report["diagnostic_only"] is True
    assert report["allowed_actions"] == ["WATCH", "MANUAL_REVIEW"]
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialised, flags=re.IGNORECASE) is None


def test_markdown_report_includes_section_headers_and_review_questions() -> None:
    report = _fixture_report()
    markdown = render_payoff_state_diagnostics_markdown(report)

    assert "# Finite-State Payoff Diagnostics" in markdown
    assert "## Diagnostics" in markdown
    assert "Required review questions:" in markdown
    assert "NOT same-payoff equality evidence" in markdown
    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", markdown, flags=re.IGNORECASE) is None


def test_no_diagnostic_can_promote_above_manual_review() -> None:
    """Even an infeasible row with maximum confidence stays capped at MANUAL_REVIEW."""

    for row in _fixture_report()["payoff_state_diagnostics"]:
        assert row["max_action_cap"] != "PAPER_CANDIDATE"
        assert row["max_action_cap"] != "POSSIBLE_ARB"
        assert row["max_action_cap"] != "EXECUTABLE"
        assert row["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"}


def test_no_row_can_produce_evaluator_ready_field() -> None:
    """An attempted attempt to declare evaluator gate readiness must fail closed."""

    for row in _fixture_report()["payoff_state_diagnostics"]:
        assert "evaluator_ready" not in row
        assert "trade_permission" not in row
        assert "profit_usd" not in row
        assert "fill_size" not in row


# ---------------------------------------------------------------------------
# Safety hardening: substring detection of embedded compound phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "embedded_key",
    [
        "graph_hint_is_paper_candidate",
        "is_exact_same_payoff_evidence",
        "executable_arb_marker",
        "trade_permission_flag",
        "graph_evaluator_ready_marker",
        "trusted_relationship_claim",
        "possible_arb_hint",
    ],
)
def test_validator_rejects_embedded_compound_phrase_key(embedded_key: str) -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0][embedded_key] = "fixture"

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


@pytest.mark.parametrize(
    "embedded_value",
    [
        "see possible_arb later",
        "see PAPER_CANDIDATE",
        "exact_same_payoff guaranteed",
        "ready for executable_arb pipeline",
        "trade_permission required",
        "tagged paper-candidate",
    ],
)
def test_validator_rejects_embedded_compound_phrase_value(embedded_value: str) -> None:
    report = _fixture_report()
    report["payoff_state_diagnostics"][0]["review_reason"] = embedded_value

    with pytest.raises(SchemaValidationError):
        validate_payoff_state_diagnostics_report(report)


def test_markdown_pre_write_validation_rejects_prohibited_vocabulary(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    json_output = tmp_path / "market_graph_payoff_state_diagnostics.json"
    md_output = tmp_path / "market_graph_payoff_state_diagnostics.md"

    from graph_engine.reporting import payoff_state_report

    original = payoff_state_report.render_payoff_state_diagnostics_markdown

    def poisoned_renderer(report: dict) -> str:
        return original(report) + "\n\nThis is a paper_candidate.\n"

    payoff_state_report.render_payoff_state_diagnostics_markdown = poisoned_renderer
    try:
        with pytest.raises(SchemaValidationError):
            write_payoff_state_diagnostics_report(snapshot, json_output, md_output)
    finally:
        payoff_state_report.render_payoff_state_diagnostics_markdown = original


def test_safety_module_detects_embedded_paper_candidate_in_identifier() -> None:
    from graph_engine.reporting.safety import contains_prohibited_report_token

    assert contains_prohibited_report_token("graph_hint_is_paper_candidate") is True
    assert contains_prohibited_report_token("is-paper-candidate-v2") is True
    assert contains_prohibited_report_token("is_exact_same_payoff_v2") is True
    assert contains_prohibited_report_token("recorder_state_id") is False  # word boundary keeps "order" safe inside "recorder"
    assert contains_prohibited_report_token("threshold_sequence") is False


def test_generated_payoff_state_reports_pass_broad_safety_scan(tmp_path) -> None:
    snapshot, _ = load_fixture_markets(FIXTURES_DIR)
    json_output = tmp_path / "market_graph_payoff_state_diagnostics.json"
    md_output = tmp_path / "market_graph_payoff_state_diagnostics.md"

    write_payoff_state_diagnostics_report(snapshot, json_output, md_output)

    broad_pattern = re.compile(
        r"PAPER_CANDIDATE|POSSIBLE_ARB|EXACT_SAME_PAYOFF|exact_same_payoff|paper_candidate|"
        r"executable|PnL|pnl|profit|fill|size|trade|order|trade_permission|place_order|cancel_order",
        re.IGNORECASE,
    )
    for path in (json_output, md_output):
        text = path.read_text(encoding="utf-8")
        assert broad_pattern.search(text) is None, f"unsafe token in {path.name}"
