from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

from graph_engine.formula import (
    build_formula_diagnostics_report,
    build_formula_diagnostics_report_from_formulas,
    import_proposed_market_formulas,
    load_proposed_market_formulas,
    parse_fixture_market_formula,
)
from graph_engine.models import GraphSnapshot
from graph_engine.reporting.json_report import PROHIBITED_VIOLATION_FIELDS, build_json_report
from graph_engine.reporting.schema_validation import SchemaValidationError, validate_formula_diagnostics_contract
from tests.conftest import make_node


PROPOSED_FORMULAS_FIXTURE = Path(__file__).parent / "fixtures" / "proposed_market_formulas.json"
PROHIBITED_TOKENS = sorted(
    PROHIBITED_VIOLATION_FIELDS
    | {
        "PAPER_CANDIDATE",
        "POSSIBLE_ARB",
        "executable-arb",
        "fill-size",
        "trade-permission",
    }
)


def _diagnostics_by_relation(report: dict, relation: str) -> list[dict]:
    return [item for item in report["formula_diagnostics"] if item["formula_relation"] == relation]


def _proposal_payload() -> dict:
    return json.loads(PROPOSED_FORMULAS_FIXTURE.read_text(encoding="utf-8"))


def _assert_proposal_invalid(payload: dict) -> None:
    try:
        import_proposed_market_formulas(payload)
    except SchemaValidationError:
        return
    raise AssertionError("invalid proposed formula payload should fail closed")


def test_btc_formula_parser_extracts_typed_threshold(fixture_snapshot) -> None:
    formula = parse_fixture_market_formula(fixture_snapshot.nodes["fixture:btc_over_120k_june"])

    assert formula.family == "BTC_THRESHOLD"
    assert formula.asset == "BTC"
    assert formula.source == "fixture_btc_index"
    assert formula.date == "2026-06-30"
    assert formula.comparator == ">"
    assert formula.threshold == 120000
    assert formula.units == "USD"
    assert formula.side == "YES"
    assert formula.blockers == []


def test_formula_diagnostics_include_btc_threshold_ladder_and_ambiguous_date_mismatch(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    ladder = _diagnostics_by_relation(report, "threshold_ladder")
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")

    assert any({"fixture:btc_over_140k_june", "fixture:btc_over_120k_june"} == set(item["market_ids"]) for item in ladder)
    assert any({"fixture:btc_over_120k_june", "fixture:btc_over_100k_other_window"} == set(item["market_ids"]) for item in ambiguous)
    assert all(item["diagnostic_only"] is True for item in report["formula_diagnostics"])
    assert all(item["affects_evaluator_gates"] is False for item in report["formula_diagnostics"])


def test_title_similarity_alone_does_not_create_trusted_equality(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    ambiguous = _diagnostics_by_relation(report, "ambiguous_not_exact")

    assert any({"fixture:fed_june_425_450_a", "fixture:fed_july_425_450"} == set(item["market_ids"]) for item in ambiguous)
    assert all(item["formula_relation"] != "trusted_equality" for item in report["formula_diagnostics"])


def test_exact_typed_formula_match_is_still_diagnostic_only(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    matches = _diagnostics_by_relation(report, "typed_formula_match_review_only")
    target = next(item for item in matches if {"fixture:fed_june_425_450_a", "fixture:fed_june_425_450_b"} == set(item["market_ids"]))

    assert target["diagnostic_only"] is True
    assert target["affects_evaluator_gates"] is False
    assert target["max_action_cap"] == "MANUAL_REVIEW"


def test_fed_range_overlap_is_diagnostic_only(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    overlaps = _diagnostics_by_relation(report, "overlap_not_identical")

    assert any({"fixture:fed_june_425_450_a", "fixture:fed_june_400_450"} == set(item["market_ids"]) for item in overlaps)
    assert all(item["max_action_cap"] in {"WATCH", "MANUAL_REVIEW"} for item in overlaps)


def test_missing_formula_source_creates_blocker(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    blocked = _diagnostics_by_relation(report, "parse_blocked")

    assert any("fixture:fed_missing_source" in item["market_ids"] for item in blocked)
    assert any("missing_source" in item["blockers"] for item in blocked)


def test_formula_diagnostics_are_in_json_report(fixture_snapshot) -> None:
    report = build_json_report(fixture_snapshot, [])

    assert report["formula_diagnostics"]["diagnostic_only"] is True
    assert report["formula_diagnostics"]["affects_evaluator_gates"] is False
    assert report["summary"]["formula_diagnostic_count"] == report["formula_diagnostics"]["comparison_count"]


def test_formula_contract_rejects_prohibited_value(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    report["formula_diagnostics"][0]["review_reason"] = "POSSIBLE" + "_" + "ARB"

    try:
        validate_formula_diagnostics_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited formula diagnostic value should fail")


def test_formula_contract_rejects_prohibited_field(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    report["formula_diagnostics"][0]["profit_usd"] = 1

    try:
        validate_formula_diagnostics_contract(report)
    except SchemaValidationError:
        return
    raise AssertionError("prohibited formula diagnostic field should fail")


def test_unknown_or_missing_formula_fields_fail_closed() -> None:
    snapshot = GraphSnapshot(
        snapshot_id="test",
        as_of="2026-05-19T18:00:00+00:00",
        nodes={
            "test:unknown": make_node(
                "test:unknown",
                0.5,
                title="Similar looking BTC market",
                canonical_text="Bitcoin market with no threshold date or source.",
                settlement_source=None,
            )
        },
    )

    report = build_formula_diagnostics_report(snapshot)

    assert report["formulas"][0]["blockers"]
    assert report["formula_diagnostics"] == []


def test_formula_diagnostics_contain_no_prohibited_tokens(fixture_snapshot) -> None:
    report = build_formula_diagnostics_report(fixture_snapshot)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None


def test_valid_proposed_formula_fixture_imports() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)

    assert len(formulas) == 5
    assert formulas[0].provenance["validator_required"] is True
    assert {formula.family for formula in formulas} == {"BTC_THRESHOLD", "FED_MEETING_RANGE"}


def test_proposed_formula_diagnostics_remain_review_only() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)
    report = build_formula_diagnostics_report_from_formulas(formulas)
    matches = _diagnostics_by_relation(report, "typed_formula_match_review_only")

    assert any({"proposal:btc_over_100k_june_a", "proposal:btc_over_100k_june_b"} == set(item["market_ids"]) for item in matches)
    assert all(item["diagnostic_only"] is True for item in matches)
    assert all(item["affects_evaluator_gates"] is False for item in matches)
    assert all(item["max_action_cap"] == "MANUAL_REVIEW" for item in matches)


def test_proposed_formula_title_similarity_still_fails_closed() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"] = [
        {
            "market_id": "proposal:btc_similar_missing_date",
            "family": "BTC_THRESHOLD",
            "subject": "BTC",
            "asset": "BTC",
            "source": "fixture_btc_index",
            "comparator": ">",
            "threshold": 100000,
            "units": "USD",
            "side": "YES",
            "parse_quality": 0.9,
            "blockers": [],
            "provenance": {"proposed_by": "fixture_json"},
        }
    ]
    payload["formula_count"] = 1

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_missing_family_specific_keys() -> None:
    payload = _proposal_payload()
    del payload["proposed_formulas"][0]["source"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_unsupported_comparator() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["comparator"] = "approximately"

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_threshold_without_units() -> None:
    payload = _proposal_payload()
    del payload["proposed_formulas"][0]["units"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_fed_missing_meeting_or_range() -> None:
    payload = _proposal_payload()
    fed = payload["proposed_formulas"][3]
    del fed["meeting_date"]

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_low_parse_quality() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["parse_quality"] = 0.2

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_exact_same_payoff_claim() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["exact_same_payoff"] = True

    _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_evaluator_trading_or_paper_fields() -> None:
    for field_name in ["affects_evaluator_gates", "trade_permission", "profit_usd", "PAPER_CANDIDATE"]:
        payload = _proposal_payload()
        payload["proposed_formulas"][0][field_name] = True

        _assert_proposal_invalid(payload)


def test_proposed_formula_rejects_prohibited_value() -> None:
    payload = _proposal_payload()
    payload["proposed_formulas"][0]["provenance"] = {"note": "POSSIBLE" + "_" + "ARB"}

    _assert_proposal_invalid(payload)


def test_validated_proposals_contain_no_prohibited_tokens() -> None:
    formulas = load_proposed_market_formulas(PROPOSED_FORMULAS_FIXTURE)
    report = build_formula_diagnostics_report_from_formulas(formulas)
    serialized = json.dumps(report).lower()

    for token in PROHIBITED_TOKENS:
        assert re.search(rf"\b{re.escape(token)}\b", serialized, flags=re.IGNORECASE) is None
